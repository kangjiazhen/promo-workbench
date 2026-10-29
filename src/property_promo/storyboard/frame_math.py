"""Frame geometry for the camera moves (§6.5).

One rule drives everything here: **the photo must always cover the frame
completely**. A move that leaves black bars is not a move, it is a bug —
and the old stack's "静态帧" complaints came from exactly that (a pan over
spare pixels reads as a still).

The model:

    cover  = max(W/pw, H/ph)            # the image scaled to cover the canvas
    element = (pw*cover*z, ph*cover*z)  # after the zoom of the move
    surplus = element - canvas          # how much room a pan may use

A pan of `x` (a fraction of the element width) is legal only while

    |x| * element_width  <=  surplus_x / 2

Otherwise the frame would show the background. The planner clamps to that
budget *and records that it clamped* — a move is never silently downgraded,
and the board always states the real on-screen motion.
"""
from __future__ import annotations

from typing import Any


def cover_scale(pw: int, ph: int, W: int, H: int) -> float:
    if pw <= 0 or ph <= 0 or W <= 0 or H <= 0:
        return 1.0
    return max(W / pw, H / ph)


def element_size(pw: int, ph: int, W: int, H: int, z: float) -> tuple[float, float]:
    c = cover_scale(pw, ph, W, H)
    return pw * c * z, ph * c * z


def max_pan_fraction(pw: int, ph: int, W: int, H: int,
                     z: float) -> tuple[float, float]:
    """Largest legal |x| / |y| as a fraction of the (scaled) element."""
    ew, eh = element_size(pw, ph, W, H, z)
    mx = max(0.0, (ew - W) / 2.0) / ew if ew else 0.0
    my = max(0.0, (eh - H) / 2.0) / eh if eh else 0.0
    return mx, my


def clamp_move(move: dict[str, Any], pw: int, ph: int, W: int, H: int
               ) -> tuple[dict[str, Any], bool]:
    """Clamp a move's pan so the frame stays fully covered.

    Returns (move, clamped). The move dict is copied, never mutated in
    place — a frozen storyboard is read-only input.
    """
    out = dict(move)
    z0 = float(out.get("z0", 1.0))
    z1 = float(out.get("z1", 1.0))
    mx0, my0 = max_pan_fraction(pw, ph, W, H, z0)
    mx1, my1 = max_pan_fraction(pw, ph, W, H, z1)
    clamped = False

    for key, (lim0, lim1) in (("x", (mx0, mx1)), ("y", (my0, my1))):
        a = float(out.get(f"{key}0", 0.0))
        b = float(out.get(f"{key}1", 0.0))
        ca = max(-lim0, min(lim0, a))
        cb = max(-lim1, min(lim1, b))
        if abs(ca - a) > 1e-6 or abs(cb - b) > 1e-6:
            clamped = True
        out[f"{key}0"] = round(ca, 4)
        out[f"{key}1"] = round(cb, 4)

    out["max_pan_x"] = round(mx1, 4)
    out["max_pan_y"] = round(my1, 4)
    return out, clamped


def effective_scale(move: dict[str, Any]) -> float:
    """The on-screen scale change of a move (z1/z0)."""
    z0 = float(move.get("z0", 1.0)) or 1.0
    z1 = float(move.get("z1", 1.0)) or 1.0
    return z1 / z0
