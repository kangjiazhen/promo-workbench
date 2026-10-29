"""Render service: confirm → freeze → enqueue → versions (§5.4, §6).

The human-confirm gate is the core of this module: a listing may only
enter the queue from `awaiting_confirm`. Everything else here is
idempotency, freezing, cancellation and retry.

Freezing (§5.3): the settings a render uses are read *at the moment the
user clicks 生成* and written into the Storyboard. Later edits to the
global defaults or the listing do not touch a queued or running job.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..domain import constants, errors
from ..domain.models import JobState, ListingState, RenderJob, VideoVersion
from ..jobs.store import Store
from ..settings import module_settings
from ..storyboard import planner, schema


class RenderService:
    def __init__(self, store: Store, *, media_dir: Path, renders_dir: Path):
        self.store = store
        self.media_dir = media_dir
        self.renders_dir = renders_dir
        self.renders_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------- effective
    def global_defaults(self) -> dict:
        """The global default settings, normalised (§5.3)."""
        return module_settings.normalize(self.store.get_settings())

    def effective_settings(self, listing_id: str) -> dict:
        """Listing overrides over the global defaults (§5.3).

        The listing stores *sparse overrides* only: a key that is absent
        inherits the current global default, so an unconfirmed listing
        always shows the latest defaults (§5.3 "未确认的套显示值在下次进入
        详情时刷新"). A key explicitly set to `None` was reset to the
        global default by the user.
        """
        listing = self.store.get_listing(listing_id)
        if not listing:
            raise errors.listing_not_found(listing_id)
        return module_settings.apply_overrides(self.global_defaults(),
                                               listing.settings)

    def snapshot_defaults(self, listing_id: str) -> None:
        """Record the upload-time defaults for a listing (§5.3).

        The listing keeps *no* override entries, so it inherits the live
        global defaults; the values a *render* uses are frozen again at
        queue time (§6). The snapshot is kept for the audit trail only.
        """
        listing = self.store.get_listing(listing_id)
        if not listing:
            raise errors.listing_not_found(listing_id)
        self.store.set_settings(listing_id, {})

    # ------------------------------------------------------------- enqueue
    def enqueue(self, listing_ids: list[str], *,
                aspect: str | None = None) -> list[RenderJob]:
        """Queue one render job per listing (idempotent, §6).

        A listing that is already queued or rendering is skipped, not
        duplicated. A listing that is not `awaiting_confirm` is refused
        with a message that says what to do.
        """
        if not listing_ids:
            raise errors.nothing_selected()
        jobs: list[RenderJob] = []
        for lid in listing_ids:
            listing = self.store.get_listing(lid)
            if not listing:
                raise errors.listing_not_found(lid)
            if listing.state in (ListingState.QUEUED, ListingState.RENDERING):
                continue  # idempotent: already in flight
            if listing.state not in (ListingState.AWAITING_CONFIRM,
                                     ListingState.COMPLETE,
                                     ListingState.FAILED):
                raise errors.listing_not_confirmed(listing.name)
            # A listing whose photos are not all on disk cannot render.
            # Refuse it here, at queue time, with the count and the fix —
            # not after a worker has burned a minute and failed at 5%.
            missing = self.store.find_missing_media(lid, self.media_dir)
            if missing:
                raise errors.DomainError(
                    "media_missing",
                    f"「{listing.name}」有 {len(missing)} 张照片在磁盘上找不到"
                    f"（例如 {missing[0]}），请重新上传这些文件后再生成。")
            settings = self.effective_settings(lid)
            if aspect:
                if aspect not in constants.ASPECTS:
                    raise errors.bad_setting("aspect", "未知画幅")
                settings["aspect"] = aspect
            music = settings.get("music") or {}
            if music.get("mode") == "custom" and not self.store.get_listing_music(lid):
                # Refusing here is the whole point of the confirm gate: a
                # silent fallback to no music would look like a success and
                # ship a video the user did not ask for.
                raise errors.music_file_missing(listing.name)
            board = planner.build_storyboard(listing, settings)
            problems = schema.validate(board)
            if problems:
                raise errors.DomainError(
                    "storyboard_invalid",
                    "分镜脚本校验未通过：" + "；".join(problems[:3]))
            job_id = self.store.new_id()
            self.store.create_job(job_id, lid, settings["aspect"], board,
                                  batch_id=listing.batch_id)
            self.store.set_state(lid, ListingState.QUEUED)
            jobs.append(self.store.get_job(job_id))
        return jobs

    # ------------------------------------------------------------- cancel
    def cancel(self, job_id: str) -> RenderJob:
        job = self.store.get_job(job_id)
        if not job:
            raise errors.job_not_found(job_id)
        if job.state in (JobState.COMPLETE, JobState.CANCELLED, JobState.FAILED):
            return job
        self.store.cancel_job(job_id)
        listing = self.store.get_listing(job.listing_id)
        if listing and listing.state in (ListingState.QUEUED,
                                         ListingState.RENDERING):
            self.store.set_state(job.listing_id, ListingState.AWAITING_CONFIRM)
        return self.store.get_job(job_id)

    # -------------------------------------------------------------- retry
    def retry(self, job_id: str) -> RenderJob:
        job = self.store.get_job(job_id)
        if not job:
            raise errors.job_not_found(job_id)
        if job.state != JobState.FAILED:
            raise errors.DomainError(
                "not_failed",
                "只有失败的渲染任务可以重试。")
        listing = self.store.get_listing(job.listing_id)
        if not listing:
            raise errors.listing_not_found(job.listing_id)
        missing = self.store.find_missing_media(job.listing_id, self.media_dir)
        if missing:
            raise errors.DomainError(
                "media_missing",
                f"「{listing.name}」有 {len(missing)} 张照片在磁盘上找不到"
                f"（例如 {missing[0]}），重新上传后才能重试。")
        board = job.storyboard or planner.build_storyboard(
            listing, self.effective_settings(job.listing_id))
        new_id = self.store.new_id()
        self.store.create_job(new_id, job.listing_id, job.aspect, board,
                              batch_id=job.batch_id)
        self.store.set_state(job.listing_id, ListingState.QUEUED)
        return self.store.get_job(new_id)

    # ----------------------------------------------------------- versions
    def register_version(self, job: RenderJob, out_path: Path, *,
                         duration_s: float = 0.0) -> VideoVersion:
        """Record a finished MP4 as a new version of the listing (§5.4)."""
        listing = self.store.get_listing(job.listing_id)
        if not listing:
            raise errors.listing_not_found(job.listing_id)
        version = VideoVersion(
            version_id=self.store.new_id(),
            listing_id=job.listing_id,
            job_id=job.job_id,
            file_path=str(out_path),
            aspect=job.aspect,
            duration_s=duration_s,
            size_bytes=out_path.stat().st_size if out_path.is_file() else 0,
            created_at=time.time(),
        )
        self.store.add_version(version)
        keep = int(module_settings.normalize(
            self.store.get_settings()).get("max_versions_per_listing",
                                           constants.MAX_VERSIONS_PER_LISTING))
        for removed in self.store.prune_versions(job.listing_id, keep):
            try:
                Path(removed).unlink(missing_ok=True)
            except OSError:
                pass
        return version

    def listing_output_dir(self, listing_id: str) -> Path:
        d = self.renders_dir / listing_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def job_output_path(self, job: RenderJob) -> Path:
        """One output file per job — the job id keeps versions distinct."""
        return self.listing_output_dir(job.listing_id) / (
            f"{job.listing_id}_{job.aspect.replace(':', 'x')}_{job.job_id}.mp4")

    def storyboard_path(self, job: RenderJob) -> Path:
        d = self.listing_output_dir(job.listing_id) / "storyboards"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{job.job_id}.json"

    def write_storyboard(self, job: RenderJob) -> Path:
        """Persist the frozen storyboard next to the output (§6)."""
        import json
        p = self.storyboard_path(job)
        p.write_text(json.dumps(job.storyboard, ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    def listing_settings_payload(self, listing_id: str) -> dict[str, Any]:
        return self.effective_settings(listing_id)
