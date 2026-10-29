"""RemotionRenderer — the real film, rendered by the module's own composition.

One render = one `node render-job.mjs` child process, which owns its own
Chromium. That is deliberate: **N workers really are N renders in flight**
(the pool runs them in parallel), cancellation is a real process kill
instead of a flag the renderer might ignore, and a crash or a hang cannot
take the API down with it.

Concurrency safety, which is the whole point of the design:

  * the webpack bundle is built once and shared — rebuilt only when a source
    file changes, guarded by a lock file so two simultaneous jobs cannot
    interleave a half-written bundle;
  * each job mounts its own `staticFile()` folder as a *uniquely named*
    junction inside the bundle, so two listings holding `客厅/01.jpg`
    cannot overwrite each other (the old stack did exactly that);
  * the mount is removed when the job ends, including on cancel/failure.

Nothing here puts an address or a local path on screen (§4): the child
receives a storyboard whose photo paths are listing-relative, and the
output is a plain MP4.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import stat as _stat
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .base import RenderResult
from . import music_bed, verifier

#: Where the renderer project lives (sibling of `src/`).
RENDERER_DIR = Path(__file__).resolve().parents[3] / "renderer"
#: Built once, shared by every job.
BUNDLE_DIR = RENDERER_DIR / ".cache" / "bundle"

_CHROME_REL = (Path(".remotion") / "chrome-headless-shell" / "win64"
               / "chrome-headless-shell-win64" / "chrome-headless-shell.exe")


def local_chrome() -> Path | None:
    """A Chromium already on this machine, so an offline render still works.

    Remotion downloads its own headless shell on first render; this is only a
    shortcut for machines that already have one. Looked for, in order:
    `PROMO_CHROME` (explicit), then `.remotion/` at the module root, then two
    and four levels above the package (the layouts this repo has lived in).
    Returns None when nothing is found — Remotion then fetches its own.
    """
    env = os.environ.get("PROMO_CHROME")
    if env:
        candidate = Path(env).expanduser()
        return candidate if candidate.is_file() else None
    here = Path(__file__).resolve()
    for base in (here.parents[3], here.parents[5] if len(here.parents) > 5 else here.parents[3]):
        candidate = base / _CHROME_REL
        if candidate.is_file():
            return candidate
    return None

_REPARSE_POINT = getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _is_link(path: Path) -> bool:
    """True for symlinks *and* Windows junctions.

    `Path.is_symlink()` answers False for a junction, which is why the
    per-job mounts used to survive every render and pile up inside
    `bundle/public/`; once a listing was deleted they dangled, and the next
    render died on launch (exit 0xC0000409). Nothing here may assume
    `is_symlink()`.
    """
    try:
        info = path.lstat()
    except OSError:
        return False
    if path.is_symlink():
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _drop_link(path: Path) -> bool:
    """Delete the link, never the directory it points at."""
    if not _is_link(path):
        return False
    try:
        os.rmdir(path)          # junction / directory symlink: link only
    except OSError:
        try:
            os.unlink(path)     # file symlink
        except OSError:
            return False
    return True



class RemotionError(RuntimeError):
    """The render child failed; the message is already user-readable."""


def _board_duration(storyboard: dict[str, Any]) -> float:
    """Total film length from the frozen shot list, in seconds."""
    total = 0.0
    for shot in storyboard.get("shots") or []:
        try:
            total += max(0.0, float(shot.get("duration_s") or 0.0))
        except (TypeError, ValueError):
            continue
    return total or 1.0


class RemotionRenderer:
    """Render one frozen storyboard with the module's Remotion composition."""

    name = "remotion"

    def __init__(self, *, renderer_dir: Path | None = None,
                 bundle_dir: Path | None = None,
                 chromium: str | Path | None = None,
                 crf: int = 18,
                 frame_concurrency: int | None = None,
                 bed_cache_dir: Path | None = None):
        self.renderer_dir = Path(renderer_dir or RENDERER_DIR)
        self.bundle_dir = Path(bundle_dir or BUNDLE_DIR)
        self.chromium = str(chromium) if chromium else (
            str(found) if (found := local_chrome()) else None)
        self.crf = crf
        self.frame_concurrency = frame_concurrency
        #: Where synthesised beds are cached (shared across listings).
        self.bed_cache_dir = Path(bed_cache_dir or (RENDERER_DIR / ".cache"))

    # ------------------------------------------------------------- helpers
    def available(self) -> tuple[bool, str]:
        """Can this machine render at all? (used by the API to explain)."""
        if not (self.renderer_dir / "render-job.mjs").is_file():
            return False, f"缺少渲染驱动 {self.renderer_dir / 'render-job.mjs'}"
        if not (self.renderer_dir / "node_modules").is_dir():
            return False, "渲染依赖未安装（在 renderer/ 里跑一次 pnpm install）"
        if not shutil.which("node"):
            return False, "系统 PATH 里没有 node，无法启动渲染"
        return True, ""

    @staticmethod
    def _mount_name(job_token: str) -> str:
        """A stable, filesystem-safe, unique-per-job asset folder name."""
        safe = "".join(c for c in job_token if c.isalnum() or c in "-_")
        return f"m_{safe or 'job'}"

    # -------------------------------------------------------------- render
    def render(self, storyboard: dict[str, Any], out_path: Path, *,
               media_dir: Path,
               on_progress: Callable[[float], None] | None = None,
               cancel: Callable[[], bool] | None = None,
               job_token: str | None = None) -> RenderResult:
        ok, why = self.available()
        if not ok:
            raise RemotionError(why)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        token = job_token or out_path.stem
        mount_name = self._mount_name(token)
        self.prune_mounts()

        asset_roots = [{"name": mount_name,
                        "path": str(Path(media_dir).resolve())}]
        bed_file = None
        music = (storyboard.get("music") or {})
        mode = str(music.get("mode") or "none")
        if mode == "custom":
            music_path = Path(media_dir) / "_music"
            if music_path.is_dir():
                files = sorted(p for p in music_path.iterdir() if p.is_file())
                if files:
                    bed_file = f"{mount_name}/_music/{files[0].name}"
        elif mode == "synth":
            # 本地合成配乐：没有版权素材，也不联网。缓存按 (曲目, 时长)。
            wav = music_bed.synthesize(
                str(music.get("track") or "warm"),
                _board_duration(storyboard),
                base_dir=self.bed_cache_dir)
            music_mount = self._mount_name(f"{token}_bed")
            asset_roots.append({"name": music_mount,
                                "path": str(wav.parent.resolve())})
            bed_file = f"{music_mount}/{wav.name}"

        job = {
            "storyboard": storyboard,
            "out_path": str(out_path.resolve()),
            "asset_roots": asset_roots,
            "media_root": mount_name,
            "bed_file": bed_file,
            "bundle_dir": str(self.bundle_dir.resolve()),
            "src_dir": str((self.renderer_dir / "src").resolve()),
            "chromium": self.chromium,
            "crf": self.crf,
            "concurrency": self.frame_concurrency,
        }
        job_file = out_path.parent / f".{out_path.stem}.job.json"
        job_file.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

        try:
            return self._run(job_file, out_path,
                             [r["name"] for r in asset_roots],
                             on_progress=on_progress, cancel=cancel)
        finally:
            job_file.unlink(missing_ok=True)

    # ------------------------------------------------------------ the child
    def _run(self, job_file: Path, out_path: Path, mount_names: list[str],
             *, on_progress: Callable[[float], None] | None,
             cancel: Callable[[], bool] | None) -> RenderResult:
        env = dict(os.environ)
        env["REMOTION_DISABLE_TELEMETRY"] = "1"
        proc = subprocess.Popen(
            ["node", str(self.renderer_dir / "render-job.mjs"),
             "--job", str(job_file)],
            cwd=str(self.renderer_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env,
        )

        lines: queue.Queue[str | None] = queue.Queue()

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=pump, name="remotion-stdout", daemon=True).start()

        progress = 0.0
        last_error = ""
        finished = False
        deadline = time.time() + 1800  # a stuck child must not hold a worker forever

        try:
            while True:
                if cancel and cancel():
                    self._kill(proc)
                    raise RenderCancelled()

                try:
                    line = lines.get(timeout=0.25)
                except queue.Empty:
                    if proc.poll() is not None:
                        break
                    if time.time() > deadline:
                        self._kill(proc)
                        raise RemotionError("渲染超过 30 分钟仍未结束，已终止。")
                    continue

                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue  # webpack noise is not a protocol message
                kind = msg.get("type")
                if kind == "progress":
                    p = float(msg.get("progress") or 0.0)
                    # Progress must be monotonic: a rebuild reports 0–10%
                    # again and the bar must not jump backwards.
                    progress = max(progress, min(1.0, p))
                    if on_progress:
                        on_progress(progress)
                elif kind == "error":
                    last_error = str(msg.get("message") or "")
                elif kind == "done":
                    finished = True
                    # The child keeps stdin open so it can receive `cancel`
                    # while it renders — which also keeps its event loop
                    # alive after the last frame is written. Closing stdin
                    # is what lets it exit; without this the renderer
                    # finished in 4 minutes and the worker sat for 30.
                    try:
                        if proc.stdin is not None:
                            proc.stdin.close()
                    except OSError:
                        pass
                    break
                elif kind == "log" and msg.get("message"):
                    last_error = last_error or str(msg["message"])

            code = proc.wait(timeout=30)
        finally:
            self._cleanup_mount(mount_names)

        if cancel and cancel():
            raise RenderCancelled()
        if not finished or code != 0:
            stderr = ""
            if proc.stderr is not None:
                try:
                    stderr = proc.stderr.read() or ""
                except (OSError, ValueError):
                    stderr = ""
            detail = last_error or stderr.strip().splitlines()[-1:] or ""
            if isinstance(detail, list):
                detail = " ".join(detail)
            raise RemotionError(
                f"Remotion 渲染失败（退出码 {code}）：{str(detail)[:600]}")

        if not out_path.is_file():
            raise RemotionError("渲染进程报成功，但成片文件不存在。")

        info = verifier.probe(out_path)
        return RenderResult(
            out_path=out_path,
            duration_s=float(info.get("duration_s", 0.0)),
            size_bytes=out_path.stat().st_size,
            width=int(info.get("width", 0)),
            height=int(info.get("height", 0)),
            fps=int(info.get("fps", 0)),
            has_audio=bool(info.get("has_audio")),
            notes=["Remotion 渲染"],
        )

    # ------------------------------------------------------------- cleanup
    def prune_mounts(self) -> list[str]:
        """Drop mounts whose media folder is gone; return their names.

        Called before every render so a crashed/killed job (or a deleted
        listing) cannot poison the next one. Dangling links only: a live
        job's mount always resolves.
        """
        removed: list[str] = []
        for folder in (self.bundle_dir / "public", self.bundle_dir):
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue
            for link in entries:
                if not link.name.startswith("m_") or not _is_link(link):
                    continue
                if link.exists():        # follows the link: target is alive
                    continue
                if _drop_link(link):
                    removed.append(link.name)
        return removed

    def _cleanup_mount(self, mount_names: list[str]) -> None:
        """Drop this job's asset junctions; never touch a real directory.

        The junction lives under `public/` because that is the folder
        Remotion serves `staticFile()` out of. Both places are checked so a
        bundle built by an older driver is cleaned up too.
        """
        for mount_name in mount_names:
            if not mount_name.startswith("m_"):
                continue
            for link in (self.bundle_dir / "public" / mount_name,
                         self.bundle_dir / mount_name):
                _drop_link(link)


    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        """Kill the whole tree — node alone would leave Chromium behind."""
        if proc.poll() is not None:
            return
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:  # noqa: BLE001 — fall back to a plain kill
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


class RenderCancelled(RuntimeError):
    """Raised when the user cancels; the pool maps it to 已取消."""


__all__ = ["RemotionRenderer", "RemotionError", "RenderCancelled",
           "RENDERER_DIR", "BUNDLE_DIR", "local_chrome"]
