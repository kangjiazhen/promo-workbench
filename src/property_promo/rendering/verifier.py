"""ffprobe-based quality gate (§6.5).

`verify()` never reports success without probing the produced file: a
render that "finished" but produced an unplayable or empty MP4 must fail
here, not in the user's player.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


class VerifyError(RuntimeError):
    """The output file failed the quality gate."""


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise VerifyError(f"需要 {name} 才能质检，但系统 PATH 里没有它。")
    return found


def _ffprobe(out: Path, args: list[str]) -> str:
    proc = subprocess.run(
        [_tool("ffprobe"), "-v", "error", *args, str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return (proc.stdout or "").strip()


def probe(out: Path) -> dict[str, Any]:
    """Raw probe facts about a finished MP4."""
    if not out.is_file() or out.stat().st_size == 0:
        raise VerifyError(f"成片文件缺失或为空：{out.name}")
    dur = _ffprobe(out, ["-show_entries", "format=duration", "-of", "csv=p=0"])
    streams = _ffprobe(out, ["-show_entries",
                              "stream=codec_type,width,height,r_frame_rate",
                              "-of", "csv=p=0"])
    info: dict[str, Any] = {
        "size_mb": round(out.stat().st_size / 1048576, 2),
        "duration_s": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0,
        "has_audio": False,
        "streams": streams.splitlines(),
    }
    try:
        info["duration_s"] = float(dur)
    except ValueError:
        pass
    for line in info["streams"]:
        parts = line.split(",")
        if parts and parts[0] == "video" and len(parts) >= 4:
            try:
                info["width"] = int(parts[1])
                info["height"] = int(parts[2])
            except ValueError:
                pass
            m = re.match(r"(\d+)/(\d+)", parts[3])
            if m and int(m.group(2)):
                info["fps"] = int(m.group(1)) // int(m.group(2))
        elif parts and parts[0] == "audio":
            info["has_audio"] = True
    return info


def verify(out: Path, *, expect_width: int = 0, expect_height: int = 0,
           min_duration_s: float = 1.0,
           expect_audio: bool = False) -> dict[str, Any]:
    """Run the quality gate; raise VerifyError with a readable reason."""
    info = probe(out)
    problems: list[str] = []
    if info["duration_s"] < min_duration_s:
        problems.append(
            f"时长只有 {info['duration_s']:.2f} 秒，低于预期的 {min_duration_s:.2f} 秒")
    if expect_width and info["width"] != expect_width:
        problems.append(
            f"画面宽度是 {info['width']}，与画幅要求的 {expect_width} 不一致")
    if expect_height and info["height"] != expect_height:
        problems.append(
            f"画面高度是 {info['height']}，与画幅要求的 {expect_height} 不一致")
    if expect_audio and not info["has_audio"]:
        problems.append("成片没有音轨：配乐没有写进视频")
    if problems:
        raise VerifyError("成片质检未通过：" + "；".join(problems))
    info["ok"] = True
    return info


def loudness(out: Path) -> dict[str, float]:
    """Measured mean/peak level in dBFS (silence shows up as about -91)."""
    proc = subprocess.run(
        [_tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(out),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = (proc.stderr or "")
    facts: dict[str, float] = {"mean_db": -91.0, "max_db": -91.0}
    for key, label in (("mean_db", "mean_volume"), ("max_db", "max_volume")):
        m = re.search(rf"{label}:\s*(-?\d+(?:\.\d+)?)\s*dB", text)
        if m:
            facts[key] = float(m.group(1))
    return facts


def extract_frame(out: Path, at_s: float, dest: Path) -> Path:
    """Grab one frame for visual review (§6.5 抽帧)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [_tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{at_s:.2f}",
         "-i", str(out), "-frames:v", "1", str(dest)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not dest.is_file():
        raise VerifyError(
            f"抽帧失败（{out.name} @ {at_s:.2f}s）："
            f"{(proc.stderr or '')[-400:]}")
    return dest


def _frame_facts(png: Path) -> tuple[float, float]:
    """(mean brightness, distinct-colour count) for one extracted frame."""
    from PIL import Image

    with Image.open(png) as img:
        small = img.convert("RGB").resize((96, 54))
        raw = small.tobytes()
    pixels = [raw[i:i + 3] for i in range(0, len(raw), 3)]
    mean = sum((r + g + b) / 3 for r, g, b in pixels) / len(pixels)
    return mean, float(len(set(pixels)))


def sample_frames(out: Path, *, count: int = 5, work_dir: Path,
                  duration_s: float | None = None) -> list[dict[str, Any]]:
    """Grab `count` frames spread across the film and describe each one.

    A film that renders "successfully" but contains black or blank frames is
    a failed render, not a delivered one — this is the check that catches it.
    """
    length = duration_s if duration_s is not None else probe(out)["duration_s"]
    if length <= 0:
        raise VerifyError("成片时长为 0，无法抽帧核对。")
    work_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for i in range(count):
        at = length * (i + 0.5) / count
        dest = work_dir / f"qa_{i:02d}.png"
        extract_frame(out, at, dest)
        mean, colours = _frame_facts(dest)
        frames.append({"at_s": round(at, 2), "frame": str(dest),
                       "mean": round(mean, 1), "colours": int(colours)})
    return frames


def check_frames(frames: list[dict[str, Any]], *, min_mean: float = 6.0,
                 min_colours: int = 8) -> None:
    """Reject a film that came out blank.

    Judged over the whole film, not per frame: a legitimately minimal frame
    (a title card on near-black, or a flat swatch used in a test fixture)
    can be almost colourless while the film as a whole is fine. What must
    never happen is *every* sampled frame being the same near-colourless
    plate — that is a render that produced nothing.
    """
    if not frames:
        raise VerifyError("没有抽到任何画面，无法核对成片。")
    if all(f["mean"] < min_mean for f in frames):
        raise VerifyError("成片每一帧都是纯黑，渲染内容没有出现。")
    flat = [f for f in frames if f["colours"] < min_colours]
    if len(flat) == len(frames):
        distinct = {(round(f["mean"], 1), f["colours"]) for f in frames}
        if len(distinct) == 1:
            raise VerifyError(
                f"成片全是同一块纯色画面（亮度 {frames[0]['mean']}、"
                f"颜色数 {frames[0]['colours']}），渲染内容没有出现。")
