"""Photographic measurement for selection.

Ported *conceptually* from the verified old stack (tools/property_promo.py)
but rewritten against the new module's contracts — the old module is never
imported. No content interpretation happens here: these are measurable
photographic numbers (sharpness, contrast, brightness, colour, clipping).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

#: Quality weights (same shape as the verified old stack).
Q_DEFAULTS: dict[str, float] = {
    "sharp_divisor": 1500.0,
    "contrast_divisor": 65.0,
    "colour_divisor": 46.0,
    "w_sharp": 1.0,
    "w_contrast": 1.2,
    "w_colour": 0.5,
    "bright_centre": 165.0,
    "bright_tolerance": 25.0,
    "clip_penalty": 6.0,
}

#: Below these thresholds a photo is flagged for the user (§5.2).
BLURRY_SHARPNESS = 250.0
DARK_BRIGHTNESS = 70.0
BRIGHT_BLOWOUT = 0.03


def _lap_var(gray: np.ndarray) -> float:
    """Variance of the Laplacian — a focus/sharpness proxy."""
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    h, w = gray.shape
    if h < 3 or w < 3:
        return 0.0
    a = gray[1:-1, 1:-1] * -4.0
    a += gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:]
    a = a * a
    return float(a.mean())


def measure(path: Path) -> dict[str, Any]:
    """Measure one image file. Raises on undecodable images."""
    with Image.open(path) as im:
        im.load()
        w, h = im.size
        rgb = im.convert("RGB")
        small = rgb.resize((min(640, w), max(1, int(min(640, w) * h / w))),
                           Image.LANCZOS)
        arr = np.asarray(small, dtype=np.float32) / 255.0

    gray = arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)

    return {
        "w": w,
        "h": h,
        "aspect": round(w / h, 4) if h else 0.0,
        "sharpness": round(_lap_var(gray * 255.0), 2),
        "contrast": round(float(gray.std() * 255.0), 2),
        "brightness": round(float(gray.mean() * 255.0), 2),
        "colorfulness": round(float(sat.mean() * 255.0), 2),
        "clip_hi": round(float((mx >= 0.995).mean()), 5),
        "clip_lo": round(float((mx <= 0.02).mean()), 5),
    }


def quality(stats: dict[str, Any], q: dict[str, float] | None = None) -> float:
    """Combine measurements into one comparable score."""
    q = {**Q_DEFAULTS, **(q or {})}
    sharp = stats["sharpness"] / q["sharp_divisor"]
    contrast = stats["contrast"] / q["contrast_divisor"]
    colour = stats["colorfulness"] / q["colour_divisor"]
    b = stats["brightness"]
    lo = q["bright_centre"] - q["bright_tolerance"]
    hi = q["bright_centre"] + q["bright_tolerance"]
    bright_pen = 0.0 if lo <= b <= hi else abs(b - q["bright_centre"]) / q["bright_centre"]
    clip_pen = (stats["clip_hi"] + stats["clip_lo"]) * q["clip_penalty"]
    return round(sharp * q["w_sharp"] + contrast * q["w_contrast"]
                 + colour * q["w_colour"] - bright_pen - clip_pen, 4)


def user_flags(stats: dict[str, Any]) -> list[str]:
    """Turn measurements into user-readable flags (§5.2).

    Internal scores are never exposed — only plain-language reasons.
    """
    flags: list[str] = []
    if stats["sharpness"] < BLURRY_SHARPNESS:
        flags.append("模糊")
    if stats["brightness"] < DARK_BRIGHTNESS:
        flags.append("过暗")
    if stats["clip_hi"] > BRIGHT_BLOWOUT:
        flags.append("过曝")
    return flags
