"""Workbench routes (§5, §6, §9).

The upload phase (§3) only needed three routes. The workbench adds the
whole product surface: a task list, a per-listing detail view, manual
selection, global defaults and per-listing overrides, custom music, the
render queue and the finished videos.

Two rules run through every route here:

  * **No path ever crosses the wire.** The client addresses photos, jobs
    and versions by id; the server resolves the file. The interface must
    not be able to ask for an arbitrary file on this machine (§4).
  * **Every failure is a DomainError.** The统一信封 `{code, message,
    listing_id?, rel_path?, hint?}` is produced in one place, and the
    message always says what to do next.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from ..domain import errors
from ..domain.models import ListingState
from ..ingestion import media_paths
from ..jobs.service import RenderService
from ..jobs.store import Store
from ..jobs.worker_pool import WorkerPool
from ..selection.selector import Selector
from ..settings import module_settings
from . import payloads

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------- util
class _Lazy:
    """A late-bound handle to the store / service / pool.

    The routes must see the objects that are live *now*, not the ones that
    existed when the router was built — a test (or a future "reset the
    workspace" button) that swaps the store has to take effect on the very
    next request.
    """

    def __init__(self, target):
        self._target = target

    def _current(self):
        if callable(self._target):
            return self._target()
        return self._target

    def __getattr__(self, item):
        return getattr(self._current(), item)

    def __fspath__(self) -> str:
        """So `Path(handle)` works — the handle can stand in for a path."""
        return str(self._current())

    def __truediv__(self, other):
        return self._current() / other


def _listing_or_404(store: Store, listing_id: str):
    listing = store.get_listing(listing_id)
    if not listing:
        raise errors.listing_not_found(listing_id)
    return listing


def _summary(store: Store, listing, service: RenderService) -> dict:
    return payloads.listing_summary(
        listing, service.effective_settings(listing.listing_id))


def _detail(store: Store, listing, service: RenderService,
            media_dir: Path) -> dict:
    return payloads.listing_detail(
        store, listing, service.effective_settings(listing.listing_id),
        listing.settings, media_dir)


def _states(raw: str | None) -> list[ListingState]:
    states: list[ListingState] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            states.append(ListingState(part))
        except ValueError:
            raise errors.bad_setting("state", f"未知状态：{part}") from None
    return states


# ------------------------------------------------------------------ routes
def build_router(*, store, service, pool,
                 media_dir, renders_dir) -> APIRouter:
    """Build the workbench router.

    `store`, `service`, `pool`, `media_dir` and `renders_dir` are accepted
    either as objects/paths or as zero-argument factories. Everything is
    wrapped in a late-bound handle so the routes always talk to the
    *current* store / service / pool and the *current* data directory.
    """
    store = _Lazy(store)
    service = _Lazy(service)
    pool = _Lazy(pool)
    media_dir = _Lazy(media_dir)
    renders_dir = _Lazy(renders_dir)
    api = router
    selector = Selector(media_dir)

    # ------------------------------------------------------- workbench view
    @api.get("/workbench")
    async def workbench(
        state: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        """One poll target for the whole page (§5.4: DB + 1s 轮询)."""
        listings = store.list_listings_by_state(_states(state), limit=limit)
        # Only queued + rendering: a finished job is history, and its outcome
        # already shows up on the listing itself (成片版本 / 失败原因). Feeding
        # every past job back here made the queue panel grow forever.
        jobs = store.list_live_jobs()
        return {
            "listings": [_summary(store, li, service) for li in listings],
            "counts": store.count_listings_by_state(),
            "settings": service.global_defaults(),
            "queue": {
                "active": pool.active_count(),
                "concurrency": pool.concurrency,
                "jobs": [payloads.job_payload(store, j) for j in jobs],
            },
            "batches": store.list_batches(),
        }

    # ------------------------------------------------------------- listings
    @api.get("/listings")
    async def list_listings(state: str | None = Query(default=None),
                            limit: int = Query(default=200, ge=1, le=500)):
        listings = store.list_listings_by_state(_states(state), limit=limit)
        return [_summary(store, li, service) for li in listings]

    @api.get("/listings/{listing_id}")
    async def get_listing(listing_id: str):
        listing = _listing_or_404(store, listing_id)
        return _detail(store, listing, service, media_dir)

    @api.put("/listings/{listing_id}/name")
    async def rename_listing(listing_id: str, body: dict):
        listing = _listing_or_404(store, listing_id)
        name = str(body.get("name") or "").strip()
        if not name:
            raise errors.DomainError("invalid_name", "房源名称不能为空。")
        store.rename_listing(listing_id, name)
        return {"listing_id": listing_id, "name": name,
                "state": listing.state.value}

    # ---------------------------------------------------------- auto select
    @api.post("/listings/{listing_id}/auto-select")
    async def auto_select(listing_id: str):
        """Run the measured recommendation (§5.2).

        Photos the user already decided on are never touched, so a manual
        choice always wins over the automatic one.
        """
        listing = _listing_or_404(store, listing_id)
        photos = listing.all_photos()
        if not photos:
            raise errors.no_images()
        records = selector.analyze(photos, listing_id)
        changed = store.apply_auto_selection(
            listing_id, {r["photo_id"]: bool(r["selected"]) for r in records},
            {r["photo_id"]: list(r["flags"]) for r in records})
        listing = _listing_or_404(store, listing_id)
        return {
            "listing_id": listing_id,
            "changed": changed,
            "selected": len(listing.selected_photos()),
            "photos": [
                {"photo_id": r["photo_id"], "file_name": r["file_name"],
                 "room": r["room"], "selected": r["selected"],
                 "flags": r["flags"]}
                for r in records
            ],
        }

    # ------------------------------------------------------------ selection
    @api.put("/listings/{listing_id}/selection")
    async def patch_selection(listing_id: str, body: dict):
        listing = _listing_or_404(store, listing_id)
        if listing.state in (ListingState.QUEUED, ListingState.RENDERING):
            raise errors.selection_conflict(listing.name)
        selections = {str(k): bool(v) for k, v in
                      (body.get("selections") or {}).items()}
        orders = {str(k): int(v) for k, v in (body.get("orders") or {}).items()}
        if selections or orders:
            store.set_photo_selection(listing_id, selections, orders)
        name = body.get("name")
        if isinstance(name, str) and name.strip():
            store.rename_listing(listing_id, name.strip())
        listing = _listing_or_404(store, listing_id)
        return _summary(store, listing, service)

    # ---------------------------------------------------------------- rooms
    @api.put("/listings/{listing_id}/rooms")
    async def patch_rooms(listing_id: str, body: dict):
        """Rename and/or reorder rooms (§5.2).

        A room sent without `order_index` keeps its current position: the
        detail panel renames a room without dragging it, and that must not
        quietly move the room to the front of the film.
        """
        _listing_or_404(store, listing_id)
        rooms = body.get("rooms") or []
        for i, item in enumerate(rooms):
            room_dir = str(item.get("room_dir") or "").strip()
            if not room_dir:
                continue
            raw_order = item.get("order_index")
            order = int(raw_order) if raw_order is not None else None
            if order is None and item.get("drag_order") is True:
                order = i
            store.set_room_meta(
                listing_id, room_dir,
                display_name=(item.get("display_name") or "").strip() or None,
                order_index=order)
        listing = _listing_or_404(store, listing_id)
        return _detail(store, listing, service, media_dir)

    # -------------------------------------------------------------- settings
    @api.get("/settings/global")
    async def get_global_settings():
        return service.global_defaults()

    @api.put("/settings/global")
    async def put_global_settings(body: dict):
        current = store.get_settings()
        patch = module_settings.normalize(current)
        for key, value in (body or {}).items():
            if key in ("contact", "music", "motion") and isinstance(value, dict):
                merged = dict(patch.get(key) or {})
                merged.update(value)
                patch[key] = merged
            else:
                patch[key] = value
        normalized = module_settings.normalize(patch)
        store.set_global_settings(normalized)
        # 并发数不是「下次重启才生效」的设置：存下来之后立刻调整渲染池。
        # 调小到一个正在渲染的数量以下时会延后到本轮渲染结束再生效，
        # 绝不会为了改设置而杀掉正在跑的片子（见 WorkerPool.apply_concurrency）。
        try:
            pool.apply_concurrency(int(normalized["render_concurrency"]))
        except Exception:  # noqa: BLE001 — settings must save even if the pool cannot widen
            pass
        return normalized

    @api.put("/listings/{listing_id}/settings")
    async def put_listing_settings(listing_id: str, body: dict):
        """Store a *sparse* override set for one listing (§5.3).

        A key sent as `null` is reset to the global default, which is how
        「恢复全局默认」 works without wiping the other overrides.
        """
        listing = _listing_or_404(store, listing_id)
        overrides = dict(listing.settings)
        for key, value in (body or {}).items():
            if value is None:
                overrides.pop(key, None)
                continue
            if key in ("contact", "music", "motion") and isinstance(value, dict):
                merged = dict(overrides.get(key) or {})
                for ck, cv in value.items():
                    if cv is None:
                        merged.pop(ck, None)
                    else:
                        merged[ck] = cv
                overrides[key] = merged
            else:
                overrides[key] = value
        module_settings.validate(module_settings.apply_overrides(
            service.global_defaults(), overrides))
        store.set_settings(listing_id, overrides)
        return {"listing_id": listing_id, "overrides": overrides,
                "settings": service.effective_settings(listing_id)}

    # ---------------------------------------------------------------- music
    @api.post("/listings/{listing_id}/music")
    async def upload_music(listing_id: str, file: UploadFile):
        """Store a user-owned music file for one listing (§12.2)."""
        _listing_or_404(store, listing_id)
        name = Path(file.filename or "music.mp3").name
        ext = Path(name).suffix.lower()
        if ext not in module_settings.MUSIC_EXTS:
            raise errors.music_unsupported(name)
        payload = await file.read()
        limit = module_settings.MAX_MUSIC_MB * 1024 * 1024
        if len(payload) > limit:
            raise errors.music_too_large(name, module_settings.MAX_MUSIC_MB)
        # Stored *inside* the listing's own media folder, so the storyboard
        # can carry a relative path like the photos do (§4).
        rel = f"_music/{name}"
        dest = media_paths.listing_media_dir(media_dir, listing_id).joinpath(
            *rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        store.set_listing_music(listing_id, rel, name, len(payload))
        return {"listing_id": listing_id, "file_name": name,
                "size_bytes": len(payload)}

    @api.delete("/listings/{listing_id}/music")
    async def delete_music(listing_id: str):
        _listing_or_404(store, listing_id)
        music = store.get_listing_music(listing_id)
        if music:
            path = media_paths.listing_media_dir(
                media_dir, listing_id).joinpath(
                *[str(music["file_path"]).split("/")])
            path.unlink(missing_ok=True)
        store.clear_listing_music(listing_id)
        return {"listing_id": listing_id, "music_file": None}

    # ------------------------------------------------------------ thumbnails
    @api.get("/photos/{photo_id}/thumbnail")
    async def thumbnail(photo_id: str, size: int = Query(default=320,
                                                         ge=64, le=1600)):
        """A photo wall thumbnail, addressed by id only."""
        found = store.find_photo(photo_id)
        if not found:
            raise HTTPException(404, "照片不存在")
        listing_id, photo = found
        source = media_paths.photo_path(media_dir, listing_id, photo.rel_path)
        if not source.is_file():
            raise HTTPException(404, "照片文件缺失，请重新上传该文件")
        cache = Path(media_dir) / "_thumbs" / f"{photo_id}_{size}.jpg"
        if not cache.is_file():
            try:
                from PIL import Image
                with Image.open(source) as im:
                    im = im.convert("RGB")
                    im.thumbnail((size, size), Image.LANCZOS)
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    im.save(cache, "JPEG", quality=82)
            except Exception:  # noqa: BLE001 — fall back to the original
                return FileResponse(source)
        return FileResponse(cache, media_type="image/jpeg")

    # ---------------------------------------------------------- render queue
    @api.post("/renders/batch")
    async def render_batch(body: dict):
        """Queue one render per listing (§6).

        Idempotent: a listing that is already queued or rendering is
        skipped instead of queued twice. A listing that is not confirmed
        is reported as refused — with the reason — instead of failing the
        whole batch, so one unfinished listing never blocks the rest.
        """
        listing_ids = [str(x) for x in (body.get("listing_ids") or [])]
        aspect = body.get("aspect")
        if not listing_ids:
            raise errors.nothing_selected()
        jobs = []
        refused = []
        for lid in listing_ids:
            listing = store.get_listing(lid)
            if not listing:
                refused.append({"listing_id": lid, "name": lid,
                                "reason": "房源不存在或已删除。"})
                continue
            if listing.state in (ListingState.QUEUED, ListingState.RENDERING):
                refused.append({"listing_id": lid, "name": listing.name,
                                "reason": "已经在队列里了，本次没有重复排队。"})
                continue
            if listing.state not in (ListingState.AWAITING_CONFIRM,
                                     ListingState.COMPLETE,
                                     ListingState.FAILED):
                refused.append({
                    "listing_id": lid, "name": listing.name,
                    "reason": f"当前是「{listing.state.label}」，"
                              "请先完成上传并确认照片。"})
                continue
            try:
                created = service.enqueue([lid], aspect=aspect)
                jobs.extend(payloads.job_payload(store, j) for j in created)
            except errors.DomainError as e:
                refused.append({"listing_id": lid, "name": listing.name,
                                "reason": e.message})
        if not jobs and refused:
            raise errors.DomainError(
                "nothing_to_render",
                "没有可入队的房源：" + "；".join(r["reason"] for r in refused[:3]))
        return {"jobs": jobs, "refused": refused}

    @api.post("/listings/{listing_id}/render")
    async def render_one(listing_id: str, body: dict | None = None):
        """「生成本套」— the same path as checking the box and hitting 批量生成."""
        body = body or {}
        listing = _listing_or_404(store, listing_id)
        if listing.state in (ListingState.QUEUED, ListingState.RENDERING):
            raise errors.already_queued(listing.name)
        if listing.state not in (ListingState.AWAITING_CONFIRM,
                                 ListingState.COMPLETE, ListingState.FAILED):
            raise errors.listing_not_confirmed(listing.name)
        jobs = service.enqueue([listing_id], aspect=body.get("aspect"))
        return {"jobs": [payloads.job_payload(store, j) for j in jobs]}

    @api.get("/render-jobs")
    async def render_jobs(limit: int = Query(default=100, ge=1, le=500)):
        return [payloads.job_payload(store, j) for j in store.list_jobs(limit=limit)]

    @api.post("/render-jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        job = service.cancel(job_id)
        pool.cancel(job_id)
        return payloads.job_payload(store, job)

    @api.post("/render-jobs/{job_id}/retry")
    async def retry_job(job_id: str):
        job = service.retry(job_id)
        return payloads.job_payload(store, job)

    # ------------------------------------------------------------- versions
    @api.get("/listings/{listing_id}/video-versions")
    async def list_versions(listing_id: str):
        _listing_or_404(store, listing_id)
        return [
            {"version_id": v.version_id, "job_id": v.job_id, "aspect": v.aspect,
             "duration_s": round(v.duration_s, 2), "size_bytes": v.size_bytes,
             "created_at": v.created_at}
            for v in store.list_versions(listing_id)
        ]

    @api.get("/video-versions/{version_id}/file")
    async def version_file(version_id: str, download: bool = False):
        version = store.get_version(version_id)
        if not version:
            raise errors.version_not_found(version_id)
        path = Path(version.file_path)
        if not path.is_file():
            raise HTTPException(404, "成片文件已不在磁盘上，请重新生成。")
        headers = {"Content-Disposition":
                   f'attachment; filename="{path.name}"'} if download else None
        return FileResponse(path, media_type="video/mp4", headers=headers)

    @api.post("/listings/{listing_id}/open-folder")
    async def open_folder(listing_id: str):
        """Open the output folder of one listing in the file manager."""
        _listing_or_404(store, listing_id)
        target = renders_dir / listing_id
        target.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            raise errors.DomainError(
                "open_folder_unsupported",
                "当前系统不支持自动打开文件夹，请手动访问输出目录。")
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]
        except OSError as e:
            raise errors.DomainError(
                "open_folder_failed",
                f"打开文件夹失败：{e}") from e
        return {"opened": True}

    return api
