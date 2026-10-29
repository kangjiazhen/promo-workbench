"""Render worker pool (§5.4, §12.3).

Contract:

  * N workers poll the queue; a job is claimed atomically so two workers
    never take the same job.
  * A worker owns a **lease** it renews every 30 s. If the server dies,
    the lease expires; the next startup sweep marks the job failed with a
    readable reason and cleans its temp files — it never silently
    re-queues (the user decides).
  * Progress writes are throttled to ≥500 ms so the SQLite writes never
    starve the UI polling.
  * Cancelling a running job terminates the child process and cleans up.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..domain import constants, errors
from ..domain.models import JobState, ListingState, RenderJob, RenderStage
from ..ingestion import media_paths
from ..jobs.store import Store
from ..jobs.service import RenderService
from ..rendering.base import Renderer
from ..rendering.verifier import (VerifyError, check_frames, loudness,
                                  sample_frames, verify)

try:  # the Remotion renderer is optional (its node deps may not be installed)
    from ..rendering.remotion_renderer import RenderCancelled
except Exception:  # noqa: BLE001 — a missing renderer must not break the pool
    class RenderCancelled(RuntimeError):  # type: ignore[no-redef]
        pass


class WorkerPool:
    """A fixed pool of render workers."""

    def __init__(self, store: Store, renderer: Renderer, *,
                 media_dir: Path, renders_dir: Path,
                 service: "RenderService | None" = None,
                 concurrency: int = constants.RENDER_CONCURRENCY_DEFAULT):
        self.store = store
        self.renderer = renderer
        self.media_dir = media_dir
        self.renders_dir = renders_dir
        self.service = service or RenderService(store, media_dir=media_dir,
                                                renders_dir=renders_dir)
        self.concurrency = max(1, int(concurrency))
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._threads_active: list[bool] = []
        self._lock = threading.Lock()
        self._cancelled: set[str] = set()
        self._started = False
        self._pending_concurrency: int | None = None

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        for i in range(self.concurrency):
            t = threading.Thread(target=self._loop, name=f"render-worker-{i}",
                                 daemon=True)
            with self._lock:
                self._threads_active.append(False)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        self._started = False

    def set_concurrency(self, n: int) -> None:
        n = max(1, min(constants.RENDER_CONCURRENCY_MAX, int(n)))
        if n == self.concurrency:
            return
        self.concurrency = n
        if self._started:
            self.stop()
            self.start()

    def apply_concurrency(self, n: int) -> bool:
        """Change the pool width the way a user expects it to behave.

        Growing the pool is safe at any time: new threads simply start
        claiming work. Shrinking it while jobs are in flight would *kill*
        those renders, which is never what 「把并发调回 1」 means — so a
        shrink that arrives mid-render is deferred to the next idle moment.

        Returns True when the new width is already in effect.
        """
        n = max(1, min(constants.RENDER_CONCURRENCY_MAX, int(n)))
        if n == self.concurrency:
            return True
        if not self._started:
            self.concurrency = n
            return True
        if n > self.concurrency:
            self.concurrency = n
            with self._lock:
                for i in range(len(self._threads), n):
                    t = threading.Thread(target=self._loop,
                                         name=f"render-worker-{i}", daemon=True)
                    self._threads_active.append(False)
                    t.start()
                    self._threads.append(t)
            return True
        if self.active_count() == 0:
            self.set_concurrency(n)
            return True
        self._pending_concurrency = n
        return False

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for a in self._threads_active if a)

    def _settle_pending_concurrency(self) -> None:
        """Shrink a deferred pool width once nothing is rendering.

        Called from inside a worker loop, so it must never join threads —
        it only records the new width and lets surplus workers fall out of
        the loop the next time round.
        """
        pending = self._pending_concurrency
        if pending is None or self.active_count() > 0:
            return
        self._pending_concurrency = None
        self.concurrency = pending
        with self._lock:
            surplus = max(0, len(self._threads) - pending)
            if surplus:
                del self._threads[len(self._threads) - surplus:]
                del self._threads_active[len(self._threads_active) - surplus:]

    # ------------------------------------------------------------- worker
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._settle_pending_concurrency()
            claimed = None
            try:
                claimed = self.store.claim_next_job(f"worker-{threading.get_ident()}")
            except Exception:  # noqa: BLE001 — a DB hiccup must not kill the pool
                claimed = None
            if claimed is None:
                time.sleep(0.4)
                continue
            with self._lock:
                try:
                    self._threads_active[self._threads.index(
                        threading.current_thread())] = True
                except ValueError:
                    pass
            try:
                self.run_job(claimed)
            except Exception:  # noqa: BLE001 — job errors are recorded, not raised
                pass
            finally:
                with self._lock:
                    try:
                        self._threads_active[self._threads.index(
                            threading.current_thread())] = False
                    except ValueError:
                        pass

    # --------------------------------------------------------------- job
    def run_job(self, job: RenderJob) -> None:
        """Render one job end to end; record the outcome in the store."""
        job_id = job.job_id
        out_path = self.service.job_output_path(job)
        heartbeat = _Heartbeat(self.store, job_id)
        heartbeat.start()
        self._cancelled.discard(job_id)
        try:
            self.store.update_job(job_id, stage=RenderStage.PREPARING,
                                  progress=0.0)
            self.store.set_state(job.listing_id, ListingState.RENDERING)
            self.store.set_progress(job.listing_id, 0.02, "preparing")

            board = job.storyboard or {}
            # The renderer gets *this listing's* media folder, so a storyboard
            # only ever carries a listing-relative path — never an absolute
            # one (§4).
            listing_media = media_paths.listing_media_dir(self.media_dir,
                                                          job.listing_id)
            result = self.renderer.render(
                board, out_path, media_dir=listing_media,
                on_progress=self._progress_cb(job, heartbeat),
                cancel=lambda: self.is_cancelled(job_id),
                # Each job gets its own name, so two jobs rendering at the
                # same time never mount the same asset folder (§12.3 多开).
                job_token=job_id,
            )

            if self.is_cancelled(job_id):
                raise _Cancelled()

            self.store.update_job(job_id, stage=RenderStage.CHECKING)
            canvas = board.get("canvas") or {}
            try:
                info = verify(out_path,
                              expect_width=int(canvas.get("width", 0)),
                              expect_height=int(canvas.get("height", 0)),
                              min_duration_s=0.5,
                              expect_audio=_expects_audio(
                                  getattr(self.renderer, "name", ""), board))
            except VerifyError as e:
                raise errors.render_failed(str(e)) from e

            # 抽帧核对 (§6.5): a film can pass ffprobe and still be blank.
            qa_dir = out_path.parent / f".{out_path.stem}.qa"
            try:
                frames = sample_frames(out_path, count=5, work_dir=qa_dir,
                                       duration_s=float(info.get("duration_s")
                                                        or 0.0))
                check_frames(frames)
                levels = loudness(out_path) if info.get("has_audio") else {}
                if info.get("has_audio") and levels.get("max_db", -91.0) < -60.0:
                    raise VerifyError("成片有音轨但全程无声（配乐没有真正写入）。")
            except VerifyError as e:
                raise errors.render_failed(str(e)) from e
            finally:
                shutil.rmtree(qa_dir, ignore_errors=True)

            self.store.update_job(job_id, state=JobState.COMPLETE, progress=1.0,
                                  stage=RenderStage.CHECKING, error=None)
            self.store.set_state(job.listing_id, ListingState.COMPLETE)
            self.store.set_progress(job.listing_id, 1.0, "checking")
            self.service.register_version(job, out_path,
                                          duration_s=float(info.get("duration_s", 0.0)))
        except (_Cancelled, RenderCancelled):
            out_path.unlink(missing_ok=True)
            self.store.update_job(job_id, state=JobState.CANCELLED,
                                  error="用户取消", stage=RenderStage.RENDERING)
            listing = self.store.get_listing(job.listing_id)
            if listing and listing.state in (ListingState.QUEUED,
                                             ListingState.RENDERING):
                self.store.set_state(job.listing_id, ListingState.AWAITING_CONFIRM)
        except Exception as e:  # noqa: BLE001 — the reason must be readable
            out_path.unlink(missing_ok=True)
            message = str(e) or type(e).__name__
            self.store.update_job(job_id, state=JobState.FAILED, error=message)
            self.store.set_state_with_error(job.listing_id, ListingState.FAILED,
                                            message)
        finally:
            heartbeat.stop()
            self._cancelled.discard(job_id)
            shutil.rmtree(out_path.parent / f".{out_path.stem}.parts",
                          ignore_errors=True)

    def _progress_cb(self, job: RenderJob, heartbeat: "_Heartbeat"
                     ) -> Callable[[float], None]:
        def cb(p: float) -> None:
            heartbeat.touch()
            self.store.update_job(job.job_id, progress=max(0.0, min(1.0, p)),
                                  stage=RenderStage.RENDERING)
            self.store.set_progress(job.listing_id, 0.05 + 0.9 * p, "rendering")
        return cb

    # ------------------------------------------------------------ cancel
    def cancel(self, job_id: str) -> None:
        """Signal a running job to stop (checked between frames)."""
        self._cancelled.add(job_id)

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancelled

    # ------------------------------------------------------------- sweep
    def sweep_stale_jobs(self) -> list[str]:
        """Startup sweep (§5.4): expired lease → failed + cleanup.

        The job is marked failed with a readable reason and its temp
        files are removed. It is NOT re-queued automatically.
        """
        swept: list[str] = []
        for job in self.store.stale_rendering_jobs():
            listing = self.store.get_listing(job.listing_id)
            reason = ("服务器中断，渲染进程已失联（lease 超时）。"
                      "可以一键重试。")
            self.store.update_job(job.job_id, state=JobState.FAILED,
                                  error=reason)
            # The listing may still read 排队渲染: a crash between "claim the
            # job" and "mark the listing rendering" leaves exactly that
            # window. Both states must fail, or the workbench shows a house
            # sitting in 排队渲染 forever with nothing behind it.
            if listing and listing.state in (ListingState.RENDERING,
                                             ListingState.QUEUED):
                self.store.set_state_with_error(job.listing_id,
                                                ListingState.FAILED, reason)
            tmp = self.renders_dir / job.listing_id
            for leftover in tmp.glob(f"*{job.job_id}*"):
                if leftover.is_file():
                    leftover.unlink(missing_ok=True)
            swept.append(job.job_id)
        return swept


class _Cancelled(RuntimeError):
    pass


def _expects_audio(renderer_name: str, board: dict[str, Any]) -> bool:
    """Does this render have to come out with a soundtrack?

    Only the real renderer writes the music bed; the stub is a placeholder
    and is allowed to be silent, so demanding audio there would fail a
    correct run. With `music.mode == none` silence is the intent.
    """
    if renderer_name != "remotion":
        return False
    music = board.get("music") or {}
    return str(music.get("mode") or "none") in ("synth", "custom")


class _Heartbeat:
    """Lease heartbeat: renew every LEASE_SECONDS while a job runs."""

    def __init__(self, store: Store, job_id: str,
                 seconds: float = constants.LEASE_SECONDS):
        self.store = store
        self.job_id = job_id
        self.seconds = seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def touch(self) -> None:
        self.store.renew_lease(self.job_id, self.seconds)

    def _loop(self) -> None:
        while not self._stop.wait(self.seconds / 3.0):
            try:
                self.touch()
            except Exception:  # noqa: BLE001 — a failed renew is not fatal
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
