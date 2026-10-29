"""房间名真的画在成片上了吗？—— 用 Remotion 渲染两帧实拍对比。

`tests/test_room_labels.py` 只证明 storyboard 里写着 `room_label`；那只是
契约。契约和像素之间隔着渲染器，所以这里把同一帧渲染两遍：

* A：房间名开着；
* B：把这一张的 `room_label` 抹掉，其余（照片、机位、时长、字体）完全一样。

两帧在左下角（`RoomLabel` 的位置）必须有明显差异，而在画面中央必须几乎
完全一致——否则「不一样」只可能是照片或运镜不同造成的，说明这个测试没在
测它以为在测的东西。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]
RENDERER = MODULE / "renderer"
STILL = RENDERER / "still-job.mjs"
BUNDLE = RENDERER / ".cache" / "bundle"

pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js"),
    pytest.mark.skipif(
        not (RENDERER / "node_modules" / "remotion").is_dir(),
        reason="渲染依赖没装（在 renderer/ 里跑一次 pnpm install）",
    ),
]


def _bundle_is_current() -> tuple[bool, str]:
    """Is `.cache/bundle` a build of the *current* renderer source?

    A stale bundle would render last week's composition and fail this test for
    a reason that has nothing to do with room names — so ask instead of guess.
    """
    probe = (
        "import fs from 'node:fs';"
        "import * as bc from %s;"
        "const fresh = bc.bundleIsFresh(%s, bc.sourceStamp(%s));"
        "process.stdout.write(String(fresh));"
    )
    node_import = json.dumps((RENDERER / "bundle-cache.mjs").as_uri())
    script = probe % (
        node_import,
        json.dumps(str(BUNDLE)),
        json.dumps(str(RENDERER / "src")),
    )
    proc = subprocess.run(
        [shutil.which("node"), "-e", script], capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        return False, proc.stderr[-400:]
    return proc.stdout.strip() == "true", ""

def _chrome() -> Path | None:
    """Let the renderer decide which Chromium to use (env, repo, download)."""
    sys.path.insert(0, str(MODULE / "src"))
    from property_promo.rendering.remotion_renderer import local_chrome

    return local_chrome()


CHROME = _chrome()


def _still(props: dict, out: Path, frame: int) -> Path:
    job = out.with_suffix(".json")
    job.write_text(json.dumps(props), encoding="utf-8")
    proc = subprocess.run(
        [shutil.which("node"), str(STILL), str(job), str(out), str(frame)],
        cwd=str(RENDERER), capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert out.exists(), proc.stdout[-500:]
    return out


def _changed_pixels(a: Path, b: Path, box: tuple[int, int, int, int]) -> tuple[int, int]:
    """(how many pixels differ by more than 24 levels, the biggest difference)."""
    from PIL import Image, ImageChops

    with Image.open(a) as ia, Image.open(b) as ib:
        diff = ImageChops.difference(
            ia.convert("RGB").crop(box), ib.convert("RGB").crop(box)
        ).convert("L")
        raw = (getattr(diff, "get_flattened_data", None) or diff.getdata)()
        values = list(raw)
    return sum(1 for v in values if v > 24), max(values)


def test_the_room_name_is_painted_where_the_design_says(tmp_path: Path) -> None:
    if not (BUNDLE / "index.html").is_file():
        pytest.skip("还没有构建好的合成工程（先跑一次渲染）")
    fresh, why = _bundle_is_current()
    if not fresh:
        pytest.skip(f"合成工程落后于渲染器源码（先跑一次渲染）{why}")
    if CHROME is None or not CHROME.is_file():
        pytest.skip("这台机器没有 chrome-headless-shell（用 PROMO_CHROME 指一个）")

    sys.path.insert(0, str(MODULE / "src"))
    from property_promo.jobs.store import Store
    from property_promo.settings import module_settings
    from property_promo.storyboard import planner

    db = MODULE / "data" / "property_promo.db"
    if not db.is_file():
        pytest.skip("模块还没有数据")
    store = Store(db)
    labelled = None
    for listing in store.list_listings():
        board = planner.build_storyboard(
            listing,
            module_settings.normalize(store.get_settings()),
        )
        shots = [s for s in board["shots"] if s["kind"] == "shot"]
        if len(shots) >= 2 and shots[0].get("room_label"):
            labelled = (listing, board, shots)
            break
    if labelled is None:
        pytest.skip("库里没有带房间名的成片素材")
    listing, board, shots = labelled

    media = MODULE / "data" / "media" / listing.listing_id
    if not media.is_dir():
        pytest.skip("找不到这套房源的素材目录")

    canvas = board["canvas"]
    w, h = int(canvas["width"]), int(canvas["height"])
    # 片头之后 1 秒：落在第一张（有房间名）里，淡入已完成、淡出还没开始。
    frame = int(round((4.2 + 1.0) * board["fps"]))

    def props(b: dict) -> dict:
        # `media_path` 在 board 里是「媒体根相对」的；`render-job.mjs` 到边
        # 缘才补上挂载名，`still-job.mjs` 按原样渲染 —— 所以这里自己补。
        staged = json.loads(json.dumps(b))
        for shot in staged["shots"]:
            if shot.get("media_path"):
                shot["media_path"] = f"media/{shot['media_path']}"
        return {
            "storyboard": staged,
            "asset_roots": [{"name": "media", "path": str(media.resolve())}],
            "bed_file": None,
            "chromium": str(CHROME),
            "concurrency": 2,
        }

    # `media_path` 是「媒体根相对」的；挂载名固定成 media，两张帧都一样。
    with_labels = json.loads(json.dumps(board))
    without = json.loads(json.dumps(board))
    for shot in without["shots"]:
        shot.pop("room_label", None)

    a = _still(props(with_labels), tmp_path / "with.png", frame)
    b = _still(props(without), tmp_path / "without.png", frame)

    label_box = (int(w * 0.02), int(h * 0.72), int(w * 0.45), int(h * 0.97))
    control_box = (int(w * 0.30), int(h * 0.20), int(w * 0.70), int(h * 0.45))

    label_px, label_peak = _changed_pixels(a, b, label_box)
    control_px, control_peak = _changed_pixels(a, b, control_box)

    # 除标签之外，两帧必须是同一画面：否则下面的差异无从归因。
    assert control_px == 0, (
        f"画面中央本该逐像素一致，却有 {control_px} 个像素不同（峰值 {control_peak}）"
        " —— 这两帧不是同一画面，对比失去意义"
    )
    # 房间名 = 一道 brass 竖线 + 两个字；实测约 1700 个像素。给足上下限，
    # 既不放过「没画」，也不接受「整块糊了」。
    assert 400 < label_px < 12000, (
        f"左下角只有 {label_px} 个像素发生变化：房间名没有画上去（或画成了别的形状）"
    )
    assert label_peak > 90, f"房间名像素太暗（峰值 {label_peak}），应当是可读的浅色字"
