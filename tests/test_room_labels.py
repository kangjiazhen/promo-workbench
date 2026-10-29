"""房间名上屏（room_label）—— 用户反馈「画面没有渲染房间名称」后的契约。

规则（冻结在 Storyboard 里，渲染器只照着画，不再查任何东西）：

* 名称来源：房间的显示名（默认就是上传时的文件夹名）；
* 只在**每个房间的第一张**照片上印一次 —— 客厅 6 张不会变成 6 条水印，
  但换房间时必须换标签，否则观众不知道看房走到哪了；
* `room_labels: false` 全关，board 里连标签字段都是 null（片子可以给不想
  暴露户型的客户看）；
* 单套可以覆盖全局，覆盖是稀疏的：置 null 回到全局。
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from property_promo.api import app as app_mod
from property_promo.jobs.store import Store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    media = tmp_path / "media"
    renders = tmp_path / "renders"
    media.mkdir()
    renders.mkdir()
    monkeypatch.setitem(app_mod.CFG, "_data_dir", tmp_path)
    monkeypatch.setitem(app_mod.CFG, "_renders_dir", renders)
    st = Store(tmp_path / "t.db")
    monkeypatch.setattr(app_mod, "store", st)
    monkeypatch.setattr(app_mod, "service",
                        app_mod.UploadService(st, media, max_upload_mb=5,
                                              max_photos=40))
    monkeypatch.setattr(app_mod, "_render_cache", {"store": None, "service": None})
    monkeypatch.setattr(app_mod, "_pool_cache", {"store": None, "pool": None})
    monkeypatch.setattr(app_mod, "_batch_cache", {"store": None, "service": None})
    with TestClient(app_mod.app) as c:
        yield c


def _textured_jpg(seed: int, size=(640, 480)) -> bytes:
    img = Image.new("RGB", size, (30 + seed % 40, 40, 50))
    draw = ImageDraw.Draw(img)
    for i in range(0, size[0], 17):
        draw.line([(i, 0), (size[0] - i, size[1])], fill=(200, 180 - i % 90, 120))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


def _upload(client, name: str, rooms: dict[str, int]) -> str:
    manifest, blobs = [], {}
    seed = 0
    for room, count in rooms.items():
        for i in range(count):
            # 每张照片字节都不同：相同的图会被「重复检测」正确地丢掉，
            # 那样测到的就是重复规则而不是房间规则了。
            seed += 1
            data = _textured_jpg(seed * 7 + 1)
            rel = f"{name}/{room}/{i:02}.jpg"
            blobs[rel] = data
            manifest.append({"path": rel, "size": len(data)})
    r = client.post("/api/listings",
                    json={"root_dir_name": name, "manifest": manifest})
    assert r.status_code == 200, r.text
    lid = r.json()["listing_id"]
    for rel, data in blobs.items():
        rr = client.post(f"/api/listings/{lid}/files",
                         params={"rel_path": rel},
                         files={"file": ("x.jpg", data, "image/jpeg")})
        assert rr.status_code == 200, rr.text
    r = client.post(f"/api/listings/{lid}/upload-complete")
    assert r.status_code == 200, r.text
    return lid


def _board(client, lid: str) -> dict:
    from property_promo.storyboard import planner
    listing = app_mod.store.get_listing(lid)
    return planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))


def _labels(board: dict) -> list[str | None]:
    return [s.get("room_label") for s in board["shots"] if s["kind"] == "shot"]


# ------------------------------------------------------------------ defaults

def test_room_labels_are_on_by_default(client):
    """上传文件夹的名字就是房间名——不印出来的话，用户等于白分房间。"""
    lid = _upload(client, "A", {"客厅": 2, "主卧": 2})
    board = _board(client, lid)
    assert board["shots"][0]["kind"] == "opener"
    assert _labels(board)[0] == "客厅"


def test_every_photo_carries_its_room_name(client):
    lid = _upload(client, "A", {"客厅": 3, "主卧": 2})
    board = _board(client, lid)
    # 每张都印（用户 2026-10-29 钉死）：客厅 x3 + 主卧 x2，一个 None 都不能有
    assert _labels(board) == ["客厅", "客厅", "客厅", "主卧", "主卧"]


def test_the_label_follows_the_room_display_name(client):
    lid = _upload(client, "A", {"客厅": 2})
    r = client.put(f"/api/listings/{lid}/rooms", json={"rooms": [
        {"room_dir": "客厅", "display_name": "客餐厅一体", "order_index": 0},
    ]})
    assert r.status_code == 200, r.text
    assert _labels(_board(client, lid)) == ["客餐厅一体", "客餐厅一体"]


def test_a_single_photo_room_still_gets_its_label(client):
    """只有一张图的房间也必须有名有姓。"""
    lid = _upload(client, "A", {"客厅": 1, "阳台": 1})
    assert _labels(_board(client, lid)) == ["客厅", "阳台"]


# ------------------------------------------------------------------- switches

def test_global_default_can_switch_room_labels_off(client):
    lid = _upload(client, "A", {"客厅": 2, "主卧": 1})
    g = client.get("/api/workbench").json()["settings"]
    r = client.put("/api/settings/global", json={**g, "room_labels": False})
    assert r.status_code == 200, r.text
    assert _labels(_board(client, lid)) == [None, None, None]


def test_one_listing_can_override_the_global_switch(client):
    lid = _upload(client, "A", {"客厅": 2})
    r = client.put(f"/api/listings/{lid}/settings", json={"room_labels": False})
    assert r.status_code == 200, r.text
    assert _labels(_board(client, lid)) == [None, None]
    # 恢复全局默认（稀疏覆盖里置 null）→ 回到「印」
    r = client.put(f"/api/listings/{lid}/settings", json={"room_labels": None})
    assert r.status_code == 200, r.text
    assert _labels(_board(client, lid)) == ["客厅", "客厅"]


def test_an_override_survives_a_global_change(client):
    """单套明确选了「不印」之后，改全局不该偷偷把它翻回去。"""
    lid = _upload(client, "A", {"客厅": 1})
    client.put(f"/api/listings/{lid}/settings", json={"room_labels": False})
    g = client.get("/api/workbench").json()["settings"]
    client.put("/api/settings/global", json={**g, "room_labels": True})
    assert _labels(_board(client, lid)) == [None]


def test_a_non_boolean_switch_is_refused(client):
    lid = _upload(client, "A", {"客厅": 1})
    r = client.put(f"/api/listings/{lid}/settings", json={"room_labels": "yes"})
    assert r.status_code == 422, r.text
    assert "房间名" in r.json()["message"]


# ------------------------------------------------------------ no address leak

def test_the_address_never_rides_along_with_the_label(client):
    """房间名可以印，地址不行：标签只取房间段，不取房源段。"""
    lid = _upload(client, "示例花园8号楼2单元1201", {"客厅": 2})
    board = _board(client, lid)
    text = json.dumps([s.get("room_label") for s in board["shots"]],
                      ensure_ascii=False)
    assert "示例花园" not in text
    assert board["shots"][0]["title"] == "房源实拍"


def test_a_blank_display_name_falls_back_to_the_folder_name(client):
    lid = _upload(client, "A", {"客厅": 1})
    client.put(f"/api/listings/{lid}/rooms", json={"rooms": [
        {"room_dir": "客厅", "display_name": "   ", "order_index": 0},
    ]})
    assert _labels(_board(client, lid)) == ["客厅"]
