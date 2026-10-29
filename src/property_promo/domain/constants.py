"""Single source of truth for the input rules (§3.2 / §4).

`shared/constants.json` is the one口径: the frontend imports a generated
TypeScript copy of it, the backend imports this module. Two sides can
therefore never drift apart on supported formats, path depth, aspects or
concurrency limits.

`tests/test_shared_constants.py` fails if the generated TS copy is stale.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[3] / "shared"
CONSTANTS_PATH = SHARED_DIR / "constants.json"


@lru_cache(maxsize=1)
def constants() -> dict:
    return json.loads(CONSTANTS_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------- images
SUPPORTED_IMAGE_EXTS: tuple[str, ...] = tuple(
    constants()["supported_image_exts"])
JUNK_NAMES: tuple[str, ...] = tuple(constants()["junk_names"])
JUNK_PREFIXES: tuple[str, ...] = tuple(constants()["junk_prefixes"])

#: webkitRelativePath 段数：房源根/房间/文件 = 3 段
PATH_DEPTH_LISTING_ROOT: int = int(
    constants()["path_depth"]["listing_root"])

#: 相对路径里房间段之后还有几段就是「嵌套」
ROOM_SEGMENT_INDEX: int = int(constants()["path_depth"]["room_segment"])


def is_junk(name: str) -> bool:
    """系统/资源叉文件：.DS_Store、Thumbs.db、._xxx。"""
    lower = name.lower()
    if lower in JUNK_NAMES:
        return True
    return any(name.startswith(p) for p in JUNK_PREFIXES)


def is_supported_image(name: str) -> bool:
    lower = name.lower()
    dot = lower.rfind(".")
    if dot < 0:
        return False
    return lower[dot:] in SUPPORTED_IMAGE_EXTS


# ---------------------------------------------------------------- rooms
KNOWN_ROOM_ORDER: tuple[str, ...] = tuple(constants()["known_room_order"])


# -------------------------------------------------------------- aspects
ASPECTS: dict[str, dict] = dict(constants()["aspects"])
DEFAULT_ASPECT: str = str(constants()["default_aspect"])


# ---------------------------------------------------------------- music
MUSIC_MAX_MB: int = int(constants()["music"]["max_mb"])
MUSIC_EXTS: tuple[str, ...] = tuple(constants()["music"]["exts"])
MUSIC_MODES: tuple[str, ...] = tuple(constants()["music"]["modes"])


# ---------------------------------------------------------- concurrency
UPLOAD_CONCURRENCY_DEFAULT: int = int(
    constants()["concurrency"]["upload_default"])
UPLOAD_CONCURRENCY_MAX: int = int(constants()["concurrency"]["upload_max"])
RENDER_CONCURRENCY_DEFAULT: int = int(
    constants()["concurrency"]["render_default"])
RENDER_CONCURRENCY_MAX: int = int(constants()["concurrency"]["render_max"])
REMOTION_FRAME_CONCURRENCY: int = int(
    constants()["concurrency"]["remotion_frame_concurrency"])


# --------------------------------------------------------------- limits
MAX_PHOTOS_PER_LISTING: int = int(
    constants()["limits"]["max_photos_per_listing"])
MAX_UPLOAD_MB: int = int(constants()["limits"]["max_upload_mb"])
MAX_CLIENT_SCAN_FILES: int = int(constants()["limits"]["max_client_scan_files"])
MAX_BATCH_LISTINGS: int = int(constants()["limits"]["max_batch_listings"])
MAX_VERSIONS_PER_LISTING: int = int(
    constants()["limits"]["max_versions_per_listing"])


# ------------------------------------------------------------- duration
OPENER_SECONDS: float = float(constants()["duration"]["opener_s"])
SHOT_MIN_SECONDS: float = float(constants()["duration"]["shot_min_s"])
SHOT_MAX_SECONDS: float = float(constants()["duration"]["shot_max_s"])
OUTRO_SECONDS: float = float(constants()["duration"]["outro_s"])


# ---------------------------------------------------------------- lease
LEASE_SECONDS: float = float(constants()["lease_seconds"])
PROGRESS_THROTTLE_MS: int = int(constants()["progress_throttle_ms"])
BUSY_TIMEOUT_MS: int = int(constants()["busy_timeout_ms"])
