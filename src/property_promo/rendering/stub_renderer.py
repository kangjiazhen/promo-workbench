"""Stub renderer — a real MP4 built from the storyboard's own numbers.

Purpose (Stage 1 vertical slice): the job → version → recovery chain must
be exercised end to end *before* the real Remotion film lands, so that a
broken queue or a broken resume is found on a cheap render, not on a
2-minute film.

This is NOT a fake: it produces a playable MP4 with the storyboard's
aspect ratio and duration, the real photos, and the real camera moves
(zoom + drift, clamped so the frame stays covered). What it does not do
is the film-grade typography and the synthesised music bed — that is the
Remotion renderer's job (stage 3).

Nothing here touches a copyrighted asset: the audio track is silence.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..domain import constants
from ..ingestion import media_paths
from . import verifier
from .base import RenderResult


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise RuntimeError(f"需要 {name} 才能渲染，但系统 PATH 里没有它。")
    return found


class StubRenderer:
    """Render a storyboard to MP4 with ffmpeg (no Remotion, no network)."""

    name = "stub"

    def __init__(self, fps: int = 30):
        self.fps = fps

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _shots_of(storyboard: dict[str, Any]) -> list[dict[str, Any]]:
        return list(storyboard.get("shots") or [])

    def _shot_duration(self, shot: dict[str, Any]) -> float:
        return max(0.1, float(shot.get("duration_s", 1.0)))

    def render(self, storyboard: dict[str, Any], out_path: Path, *,
               media_dir: Path,
               on_progress: Callable[[float], None] | None = None,
               cancel: Callable[[], bool] | None = None,
               job_token: str | None = None) -> RenderResult:
        # `job_token` is unused here: ffmpeg builds every shot in a private
        # `.parts` folder named after the output file, which is already
        # unique per job.
        _ = job_token
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas = storyboard.get("canvas") or {}
        W = int(canvas.get("width", 1920))
        H = int(canvas.get("height", 1080))
        fps = int(storyboard.get("fps", self.fps))
        shots = self._shots_of(storyboard)
        if not shots:
            raise RuntimeError("storyboard 没有镜头，无法渲染。")

        work = out_path.parent / f".{out_path.stem}.parts"
        work.mkdir(parents=True, exist_ok=True)
        parts: list[Path] = []
        try:
            for i, shot in enumerate(shots):
                if cancel and cancel():
                    raise RuntimeError("渲染已取消。")
                part = work / f"part_{i:03d}.mp4"
                self._render_shot(shot, part, media_dir, W, H, fps)
                parts.append(part)
                if on_progress:
                    on_progress((i + 1) / len(shots))
            self._concat(parts, out_path, fps)
        finally:
            shutil.rmtree(work, ignore_errors=True)

        info = verifier.probe(out_path)
        return RenderResult(
            out_path=out_path,
            duration_s=float(info.get("duration_s", 0.0)),
            size_bytes=out_path.stat().st_size,
            width=int(info.get("width", W)),
            height=int(info.get("height", H)),
            fps=int(info.get("fps", fps)) or fps,
            has_audio=False,
            notes=["stub 渲染：真实画幅与时长，无音乐床（阶段①垂直切片）"],
        )

    # -------------------------------------------------------------- shots
    def _render_shot(self, shot: dict[str, Any], dest: Path, media_dir: Path,
                     W: int, H: int, fps: int) -> None:
        dur = self._shot_duration(shot)
        kind = shot.get("kind", "shot")
        if kind == "opener":
            self._render_card(shot, dest, W, H, fps, dur,
                              color="0x0b0b0d", text=shot.get("title", ""))
            return
        if kind == "outro":
            self._render_card(shot, dest, W, H, fps, dur,
                              color="0x0b0b0d", text=self._outro_text(shot))
            return

        src = media_dir / str(shot.get("media_path")
                              or media_paths.media_rel(str(shot.get("rel_path", ""))))
        if not src.is_file():
            raise RuntimeError(
                f"照片文件缺失：{shot.get('rel_path')}（房源 {shot.get('photo_id')}）")
        self._render_photo(shot, src, dest, W, H, fps, dur)

    @staticmethod
    def _outro_text(shot: dict[str, Any]) -> str:
        c = shot.get("contact") or {}
        parts = [str(c.get(k, "")).strip() for k in
                 ("name", "phone", "wechat", "agency", "closing")]
        return "  ".join(p for p in parts if p)

    def _render_card(self, shot: dict[str, Any], dest: Path, W: int, H: int,
                     fps: int, dur: float, color: str, text: str) -> None:
        """A static card. Text is drawn by ffmpeg's drawtext when a font
        is available; otherwise the card is simply a colour plate."""
        safe = text.replace("\\", "/").replace(":", "：").replace("'", "")
        safe = safe.replace("%", "%%").replace("\n", " ")
        font = self._font()
        vf = (f"drawtext=fontfile='{self._font_arg(font)}':text='{safe}':"
              f"fontcolor=0xf5f2ec:"
              f"fontsize={max(18, W // 28)}:x=(w-text_w)/2:y=(h-text_h)/2"
              ) if (font and safe) else "null"
        cmd = [
            _tool("ffmpeg"), "-v", "error", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s={W}x{H}:r={fps}",
            "-t", f"{dur:.3f}", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(dest),
        ]
        self._run(cmd)

    @staticmethod
    def _font() -> str | None:
        for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if Path(p).is_file():
                return p
        return None

    @staticmethod
    def _font_arg(font: str) -> str:
        """Escape a font path for use inside an ffmpeg filter.

        ffmpeg's filter parser splits options on `:`, so a Windows path
        like ``C:\\Windows\\Fonts\\msyh.ttc`` has to become
        ``C\\:/Windows/Fonts/msyh.ttc`` — otherwise drawtext fails with
        "No option name near ...". The drive letter's colon is the only
        one that needs escaping; the rest of the path must use forward
        slashes.
        """
        text = str(font).replace("\\", "/")
        if len(text) > 1 and text[1] == ":":
            text = text[0] + "\\:" + text[2:]
        return text

    def _render_photo(self, shot: dict[str, Any], src: Path, dest: Path,
                      W: int, H: int, fps: int, dur: float) -> None:
        """Render one photo with its camera move.

        The move is a zoom + drift over the cover-scaled image. The
        zoom range is driven by `zoompan` so the motion is real, and the
        drift is clamped by `frame_math` so the frame never shows the
        background.
        """
        from ..storyboard import frame_math

        pw = int(shot.get("width") or 0)
        ph = int(shot.get("height") or 0)
        move = shot.get("move") or {}
        if not pw or not ph:
            pw, ph = _image_size(src)

        clamped_move, was_clamped = frame_math.clamp_move(move, pw, ph, W, H)
        z0 = float(clamped_move.get("z0", 1.0))
        z1 = float(clamped_move.get("z1", 1.0))
        x0 = float(clamped_move.get("x0", 0.0))
        x1 = float(clamped_move.get("x1", 0.0))
        y0 = float(clamped_move.get("y0", 0.0))
        y0 = float(clamped_move.get("y0", 0.0))
        y1 = float(clamped_move.get("y1", 0.0))

        frames = max(1, int(round(dur * fps)))
        # zoompan expects the zoom to be expressed relative to the source
        # crop; the cover scale is folded in so the output never letterboxes.
        cover = frame_math.cover_scale(pw, ph, W, H)
        base = max(pw, ph) * cover / max(pw, ph)
        z0_p = max(1.0, z0 * (W / max(1, pw)))
        z1_p = max(1.0, z1 * (W / max(1, pw)))

        # Drift is expressed in canvas fractions; convert to zoompan's
        # x/y expression units (pixels of the scaled source).
        dpx = (x1 - x0) * W
        dpy = (y1 - y0) * H
        sx = f"iw/2-(iw/zoom/2)+({dpx:.3f}*on/{max(1, frames - 1)})"
        sy = f"ih/2-(ih/zoom/2)+({dpy:.3f}*on/{max(1, frames - 1)})"
        zexpr = f"{z0_p:.5f}+({z1_p - z0_p:.5f}*on/{max(1, frames - 1)})"

        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},"
              f"zoompan=z='{zexpr}':x='{sx}':y='{sy}':d={frames}:s={W}x{H}:fps={fps}")
        cmd = [
            _tool("ffmpeg"), "-v", "error", "-y",
            "-loop", "1", "-i", str(src),
            "-t", f"{dur:.3f}", "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(dest),
        ]
        self._run(cmd)
        if was_clamped:
            _ = base  # the cover scale is already folded into z0_p/z1_p

    def _render_photo_static(self, shot: dict[str, Any], src: Path,
                             dest: Path, W: int, H: int, fps: int,
                             dur: float) -> None:
        cmd = [
            _tool("ffmpeg"), "-v", "error", "-y",
            "-loop", "1", "-i", str(src),
            "-t", f"{dur:.3f}",
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                   f"crop={W}:{H}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(dest),
        ]
        self._run(cmd)

    # ------------------------------------------------------------- concat
    def _concat(self, parts: list[Path], out_path: Path, fps: int) -> None:
        if not parts:
            raise RuntimeError("没有可拼接的片段。")
        list_file = out_path.parent / f".{out_path.stem}.concat.txt"
        list_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts),
            encoding="utf-8")
        cmd = [
            _tool("ffmpeg"), "-v", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(fps),
            "-movflags", "+faststart", str(out_path),
        ]
        try:
            self._run(cmd)
        finally:
            list_file.unlink(missing_ok=True)

    @staticmethod
    def _run(cmd: list[str]) -> None:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-1200:]
            raise RuntimeError(f"ffmpeg 失败：{tail}")


def _image_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


__all__ = ["StubRenderer", "constants"]
