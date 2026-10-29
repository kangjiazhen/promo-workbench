"""Image probe: decode, orientation, size, quality flags (blur/dark)."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageStat


def probe(path: Path) -> dict:
    """Return {width, height, flags} for one uploaded image.

    flags are user-facing strings only (模糊/过暗) — no model scores.
    Undecodable files raise ValueError (caller reports them explicitly).
    """
    with Image.open(path) as im:
        im.load()
        width, height = im.size
        fmt = (im.format or "").upper()

        # EXIF orientation: normalize for probe stats
        oriented = im
        try:
            exif = im.getexif()
            o = exif.get(274, 1)  # Orientation
            if o in (5, 6, 7, 8):
                oriented = im.transpose(Image.ROTATE_270)
            elif o in (3, 4):
                oriented = im.transpose(Image.ROTATE_180)
        except Exception:  # noqa: BLE001 — EXIF is best-effort
            pass

        small = oriented.convert("L")
        small.thumbnail((256, 256))
        stat = ImageStat.Stat(small)

        flags: list[str] = []
        if _is_blurry(small):
            flags.append("模糊")
        if stat.mean[0] < 40:
            flags.append("过暗")

        return {"width": width, "height": height, "format": fmt,
                "flags": flags}


def _is_blurry(gray: Image.Image) -> bool:
    """Cheap variance-of-Laplacian proxy on a 256px thumbnail."""
    w, h = gray.size
    if w < 8 or h < 8:
        return False
    px = gray.load()
    vals = []
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            v = (4 * px[x, y]
                 - px[x - 1, y] - px[x + 1, y]
                 - px[x, y - 1] - px[x, y + 1])
            vals.append(v * v)
    mean = sum(vals) / len(vals)
    return mean < 12.0
