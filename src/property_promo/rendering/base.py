"""Renderer contract for the property_promo module.

A renderer turns one frozen Storyboard into one MP4 plus a verification
report. Two implementations ship:

  * `StubRenderer`    — a real MP4 built with ffmpeg from the storyboard's
                        own numbers (used for the Stage-1 vertical slice
                        and for the concurrency spike, so the job/version/
                        recovery chain is exercised without Remotion).
  * `RemotionRenderer` — the real film, rendered by the module's own
                        Remotion composition.

Both must obey the same contract:

  * the storyboard is read-only input — a render never mutates it;
  * the output contains no address and no local path (§4);
  * success is only reported after the file exists and ffprobe confirms it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


@dataclass
class RenderResult:
    out_path: Path
    duration_s: float = 0.0
    size_bytes: int = 0
    width: int = 0
    height: int = 0
    fps: int = 0
    has_audio: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.out_path),
            "duration_s": round(self.duration_s, 2),
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "has_audio": self.has_audio,
            "notes": list(self.notes),
        }


class Renderer(Protocol):
    name: str

    def render(self, storyboard: dict[str, Any], out_path: Path, *,
               media_dir: Path,
               on_progress: Callable[[float], None] | None = None,
               cancel: Callable[[], bool] | None = None,
               job_token: str | None = None) -> RenderResult:
        """Render one frozen board.

        `job_token` identifies the job that owns this render. A renderer that
        needs a per-job private scratch area (the Remotion renderer mounts
        the album under a unique name so two concurrent jobs cannot collide)
        uses it as the folder name; renderers that do not care ignore it.
        """
        ...
