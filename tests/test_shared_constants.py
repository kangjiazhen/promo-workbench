"""Guards the one口径 contract (§3.2): `shared/constants.json`.

Three copies of the same numbers used to live in the tree — the JSON, the
Python module and a generated TS file — plus a hand-copied aspect table in
every panel. This test makes drift fatal instead of silent:

  1. the generated `web/src/lib/sharedConstants.ts` must equal `render()`
  2. the backend module must read the same values the JSON carries
  3. the modules that used to hardcode defaults must alias the constants
  4. no panel may carry its own pixel table

Run after touching `shared/constants.json`:
    python scripts/sync_shared_constants.py
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

from property_promo.domain import constants as C

ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = ROOT / "web" / "src"
SHARED_JSON = ROOT / "shared" / "constants.json"
GENERATED_TS = WEB_SRC / "lib" / "sharedConstants.ts"

sys.path.insert(0, str(ROOT / "scripts"))
import sync_shared_constants  # noqa: E402

#: Pixels pinned by §5.2 — 4:3 and 16:9 are user requirements, not defaults.
EXPECTED_PIXELS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
}

#: Files allowed to spell pixels out: the generated copy and the single
#: derivation module the panels import.
PIXEL_OWNERS = {"sharedConstants.ts", "aspects.ts"}
PIXEL_LITERAL = re.compile(r"\b(1920|1440|1080)\b")


# ------------------------------------------------------------ generated copy
def test_generated_ts_is_up_to_date():
    want = sync_shared_constants.render()
    assert GENERATED_TS.exists(), f"缺少生成物 {GENERATED_TS}"
    have = GENERATED_TS.read_text(encoding="utf-8")
    assert have == want, (
        "web/src/lib/sharedConstants.ts 与 shared/constants.json 不一致，"
        "请运行：python scripts/sync_shared_constants.py")


def test_generated_ts_carries_every_top_level_key():
    import json
    data = json.loads(SHARED_JSON.read_text(encoding="utf-8"))
    text = GENERATED_TS.read_text(encoding="utf-8")
    for key in data:
        assert f'"{key}"' in text, f"生成物缺 key: {key}"


# ------------------------------------------------------- backend ↔ json
def test_backend_reads_the_same_json():
    raw = C.constants()
    assert C.ASPECTS == raw["aspects"]
    assert C.DEFAULT_ASPECT == raw["default_aspect"]
    assert C.MUSIC_MAX_MB == raw["music"]["max_mb"]
    assert list(C.MUSIC_EXTS) == list(raw["music"]["exts"])
    assert list(C.MUSIC_MODES) == list(raw["music"]["modes"])
    assert C.MAX_PHOTOS_PER_LISTING == raw["limits"]["max_photos_per_listing"]
    assert C.MAX_UPLOAD_MB == raw["limits"]["max_upload_mb"]
    assert C.MAX_BATCH_LISTINGS == raw["limits"]["max_batch_listings"]
    assert C.MAX_VERSIONS_PER_LISTING == raw["limits"]["max_versions_per_listing"]
    assert C.UPLOAD_CONCURRENCY_DEFAULT == raw["concurrency"]["upload_default"]
    assert C.UPLOAD_CONCURRENCY_MAX == raw["concurrency"]["upload_max"]
    assert C.RENDER_CONCURRENCY_DEFAULT == raw["concurrency"]["render_default"]
    assert C.RENDER_CONCURRENCY_MAX == raw["concurrency"]["render_max"]
    assert C.LEASE_SECONDS == raw["lease_seconds"]
    assert C.PROGRESS_THROTTLE_MS == raw["progress_throttle_ms"]
    assert C.BUSY_TIMEOUT_MS == raw["busy_timeout_ms"]


def test_path_depth_pinned():
    """Two-level folders are listings, three-level folders are siblings (§2)."""
    assert C.PATH_DEPTH_LISTING_ROOT == 3
    assert C.ROOM_SEGMENT_INDEX == 1


# ---------------------------------------------------------------- aspects
def test_aspect_pixels_exact():
    assert set(C.ASPECTS) == set(EXPECTED_PIXELS)
    for name, (width, height) in EXPECTED_PIXELS.items():
        spec = C.ASPECTS[name]
        assert spec["width"] == width, name
        assert spec["height"] == height, name
        assert spec["label"].startswith(name), name


def test_aspects_are_not_square_silently():
    """A 1:1 copy-paste would make every ratio 1080×1080 — catch it."""
    shapes = {(s["width"], s["height"]) for s in C.ASPECTS.values()}
    assert len(shapes) == len(C.ASPECTS)


# ----------------------------------------------------------- concurrency
def test_concurrency_pinned():
    assert C.UPLOAD_CONCURRENCY_DEFAULT == 4
    assert C.UPLOAD_CONCURRENCY_MAX == 8
    assert C.RENDER_CONCURRENCY_DEFAULT == 2
    assert C.RENDER_CONCURRENCY_MAX == 8
    assert C.REMOTION_FRAME_CONCURRENCY == 2


def test_lease_and_throttle_pinned():
    assert C.LEASE_SECONDS == 30
    assert C.PROGRESS_THROTTLE_MS == 500
    assert C.BUSY_TIMEOUT_MS == 5000


# -------------------------------------------------------------- durations
def test_duration_math_pinned():
    assert (C.OPENER_SECONDS, C.OUTRO_SECONDS) == (4.2, 5.6)
    assert (C.SHOT_MIN_SECONDS, C.SHOT_MAX_SECONDS) == (2.4, 3.2)
    # 14 张 ≈ 50s, 17 张实测 55.06s (§5.5). The planner spreads shots inside
    # the range by measured weight, so the *envelope* must bracket those
    # numbers while the mid-point stays in the 47–58s band users saw.
    for count in (14, 17):
        floor = C.OPENER_SECONDS + count * C.SHOT_MIN_SECONDS + C.OUTRO_SECONDS
        ceiling = C.OPENER_SECONDS + count * C.SHOT_MAX_SECONDS + C.OUTRO_SECONDS
        assert floor < ceiling
        mid = C.OPENER_SECONDS + count * 2.8 + C.OUTRO_SECONDS
        assert 47.0 <= mid <= 58.0, (count, mid)
        assert floor <= mid <= ceiling, count


# --------------------------------------- modules that used to hardcode
def test_module_settings_aliases_shared_constants():
    from property_promo.settings import module_settings as M
    assert M.ASPECTS == C.ASPECTS
    defaults = M.DEFAULT_SETTINGS
    assert defaults["aspect"] == C.DEFAULT_ASPECT
    assert defaults["render_concurrency"] == C.RENDER_CONCURRENCY_DEFAULT
    assert defaults["upload_concurrency"] == C.UPLOAD_CONCURRENCY_DEFAULT
    assert defaults["max_versions_per_listing"] == C.MAX_VERSIONS_PER_LISTING
    assert M.MAX_MUSIC_MB == C.MUSIC_MAX_MB
    assert M.MUSIC_EXTS == set(C.MUSIC_EXTS)
    assert M.MUSIC_MODES == set(C.MUSIC_MODES)


def test_storyboard_timing_aliases_shared_constants():
    from property_promo.storyboard import schema
    assert schema.OPENER_SECONDS == C.OPENER_SECONDS
    assert schema.OUTRO_SECONDS == C.OUTRO_SECONDS
    assert schema.SHOT_MIN_SECONDS == C.SHOT_MIN_SECONDS
    assert schema.SHOT_MAX_SECONDS == C.SHOT_MAX_SECONDS


def test_store_and_upload_defaults_alias_shared_constants():
    from property_promo.ingestion.upload_service import UploadService
    from property_promo.jobs.store import Store

    prune = inspect.signature(Store.prune_versions).parameters["keep"]
    assert prune.default == C.MAX_VERSIONS_PER_LISTING

    init = inspect.signature(UploadService.__init__).parameters
    assert init["max_upload_mb"].default == C.MAX_UPLOAD_MB
    assert init["max_photos"].default == C.MAX_PHOTOS_PER_LISTING


# ----------------------------------------------------- frontend pixel table
def _web_sources() -> list[Path]:
    return [p for p in WEB_SRC.rglob("*")
            if p.suffix in (".ts", ".tsx") and p.name != "sharedConstants.ts"]


def test_no_panel_hardcodes_pixels():
    offenders = []
    for path in _web_sources():
        if path.name in PIXEL_OWNERS:
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if PIXEL_LITERAL.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, "画幅像素只能来自 shared/constants.json：\n" + "\n".join(offenders)


def test_aspects_module_derives_from_shared_constants():
    text = (WEB_SRC / "lib" / "aspects.ts").read_text(encoding="utf-8")
    assert "SHARED_CONSTANTS" in text, "aspects.ts 必须从生成物派生画幅表"
    assert "aspects" in text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
