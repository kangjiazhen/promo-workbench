"""Storyboard schema (§6 / §6.5).

A Storyboard is the frozen contract between the workbench and the renderer.
It is written to disk as JSON, versioned, and must never change after a
render starts — a render reads only the frozen copy.

Field contract (v1):

    {
      "version": 1,
      "listing_id": "…",
      "name": "房源A",
      "aspect": "16:9",
      "canvas": {"width": 1920, "height": 1080},
      "fps": 30,
      "created_at": 1730000000.0,
      "contact": {...},                 # frozen contact block (no address)
      "music": {"mode": "synth", "track": "warm"},
      "shots": [
        {
          "index": 0,
          "kind": "shot",               # opener | shot | outro
          "photo_id": "…",
          "room": "客厅",
          "room_label": "客厅",          # 画面上要印的房间名，null = 这一张不印
          "file_name": "01.jpg",
          "rel_path": "客厅/01.jpg",
          "width": 4032, "height": 3024,
          "duration_s": 2.6,
          "move": {"z0":…, "z1":…, "x0":…, "x1":…, "y0":…, "y1":…,
                   "ease": "lin", "kind": "push"}
        }, …
      ],
      "audio": {"narration": [], "bed": "synth:warm"}
    }
"""
from __future__ import annotations

from typing import Any

from ..domain import constants

STORYBOARD_VERSION = 1

#: Opening / closing card lengths, matching the verified old stack (§5.5).
#: Values come from `shared/constants.json` — the one口径 both ends share.
OPENER_SECONDS = constants.OPENER_SECONDS
OUTRO_SECONDS = constants.OUTRO_SECONDS
#: Per-photo shot length range (§5.5).
SHOT_MIN_SECONDS = constants.SHOT_MIN_SECONDS
SHOT_MAX_SECONDS = constants.SHOT_MAX_SECONDS

REQUIRED_SHOT_KEYS = {"index", "kind", "duration_s", "move"}
MOVE_KEYS = {"z0", "z1", "x0", "x1", "y0", "y1", "ease", "kind"}


def validate(storyboard: dict[str, Any]) -> list[str]:
    """Return a list of problems; empty list means the board is valid."""
    problems: list[str] = []
    if not isinstance(storyboard, dict):
        return ["storyboard 必须是对象"]
    if storyboard.get("version") != STORYBOARD_VERSION:
        problems.append(f"storyboard 版本必须是 {STORYBOARD_VERSION}")
    for key in ("listing_id", "aspect", "canvas", "fps", "shots"):
        if key not in storyboard:
            problems.append(f"storyboard 缺少字段 {key}")
    canvas = storyboard.get("canvas") or {}
    if not isinstance(canvas, dict) or not canvas.get("width") or not canvas.get("height"):
        problems.append("storyboard.canvas 必须包含 width/height")
    shots = storyboard.get("shots")
    if not isinstance(shots, list):
        problems.append("storyboard.shots 必须是数组")
        return problems
    if not shots:
        problems.append("storyboard 至少要有一个镜头")
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            problems.append(f"第 {i + 1} 个镜头不是对象")
            continue
        missing = REQUIRED_SHOT_KEYS - set(shot)
        if missing:
            problems.append(f"第 {i + 1} 个镜头缺少 {sorted(missing)}")
            continue
        move = shot.get("move")
        if not isinstance(move, dict) or MOVE_KEYS - set(move):
            problems.append(f"第 {i + 1} 个镜头的 move 不完整")
        dur = shot.get("duration_s")
        if not isinstance(dur, (int, float)) or dur <= 0:
            problems.append(f"第 {i + 1} 个镜头的 duration_s 无效")
    return problems


def total_duration(storyboard: dict[str, Any]) -> float:
    return sum(float(s.get("duration_s", 0)) for s in storyboard.get("shots", []))
