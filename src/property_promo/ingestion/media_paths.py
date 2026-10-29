"""One place that knows where a photo actually lives on disk.

The upload service writes a photo to

    <media_dir>/<listing_id>/<safe room>/<safe file>

and every other component (the API that serves a thumbnail, the quality
selector, the renderer) must resolve the *same* path. Before this module
existed the paths were re-derived in four places and had already drifted
twice — once so badly that Chinese room names collapsed onto each other
and photos overwrote each other on disk.

So: `photo_path()` is the only sanctioned way to get a photo's file.
"""
from __future__ import annotations

from pathlib import Path

from . import relative_path as rp


def listing_media_dir(media_dir: Path, listing_id: str) -> Path:
    """The folder that holds one listing's photos."""
    return Path(media_dir) / listing_id


def photo_path(media_dir: Path, listing_id: str, rel_path: str) -> Path:
    """The file of one photo, addressed by its canonical 房间/文件 path."""
    safe = rp.safe_rel(rel_path)
    return listing_media_dir(media_dir, listing_id).joinpath(*safe.split("/"))


def media_rel(rel_path: str) -> str:
    """The path of a photo *relative to its listing's media folder*.

    This is what a frozen storyboard carries, so a render never needs the
    listing id and never sees an absolute path (§4: no local path may
    reach the video or its metadata).
    """
    return rp.safe_rel(rel_path)


__all__ = ["listing_media_dir", "media_rel", "photo_path"]
