"""Stage ② — 选片与设置 (§5.2, §5.3, §6).

These tests exist because two real defects shipped past the stage-① build:

  1. `upload-complete` never ran the selector, so every photo arrived
     already selected and the user was asked to confirm 19/19 instead of
     a recommendation;
  2. there was no per-room cap, so six angles of one living room all went
     into the film;
  3. the storyboard ignored the room order the user had dragged, so
     reordering rooms silently did nothing to the finished video.

Each test asserts the *product* outcome, not an implementation detail.
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from property_promo.api import app as app_mod
from property_promo.jobs.store import Store
from property_promo.selection.selector import CAPPED_FLAG, Selector

pytest.importorskip("fastapi")


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
    monkeypatch.setattr(
        app_mod, "service",
        app_mod.UploadService(st, media, max_upload_mb=5, max_photos=40))
    monkeypatch.setattr(app_mod, "_render_cache",
                        {"store": None, "service": None})
    monkeypatch.setattr(app_mod, "_pool_cache", {"store": None, "pool": None})
    monkeypatch.setattr(app_mod, "_batch_cache",
                        {"store": None, "service": None})
    with TestClient(app_mod.app) as c:
        yield c


def _textured_jpg(seed: int, size=(640, 480)) -> bytes:
    """A photo with real detail — a flat colour has no sharpness to measure.

    `seed` must change actual pixel content: the duplicate detector compares
    perceptual hashes, so two "different" photos that are byte-identical
    would be (correctly) flagged as 重复 and quietly dropped from the test.
    """
    im = Image.new("RGB", size, (200 - seed % 40, 200, 200 + seed % 30))
    d = ImageDraw.Draw(im)
    for i in range(0, size[0], 16):
        d.line([(i, 0), (i + seed * 5, size[1])],
               fill=(20 + seed * 9 % 200, 40, 60), width=3)
    for i in range(0, size[1], 24):
        d.line([(0, i), (size[0], i + seed * 7)],
               fill=(220, 230 - seed * 3 % 120, 240), width=2)
    d.ellipse([seed * 11 % 300, seed * 13 % 200,
               seed * 11 % 300 + 120, seed * 13 % 200 + 90],
              outline=(10, 10, 10), width=4)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def _upload(client, name: str, rooms: dict[str, int]) -> str:
    manifest, blobs = [], {}
    seed = 0
    for room, count in rooms.items():
        for i in range(count):
            # A unique seed per photo: identical bytes across rooms would be
            # (correctly) flagged as 重复 and dropped, which would make these
            # tests assert the duplicate rule instead of the room rule.
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


# ------------------------------------------------- upload runs the selector
def test_upload_complete_runs_auto_selection(client):
    """§6: 批量上传 → 每套自动选片. Otherwise the user confirms 19/19."""
    lid = _upload(client, "A", {"客厅": 6})
    detail = client.get(f"/api/listings/{lid}").json()
    photos = detail["rooms"][0]["photos"]
    assert len(photos) == 6
    selected = [p for p in photos if p["selected"]]
    assert len(selected) == Selector.per_room_cap_default(), (
        "上传完成后应已按房间上限给出推荐，而不是把所有照片都选中")


def test_upload_complete_response_reports_the_recommendation(client):
    data = [_textured_jpg(i * 7 + 1) for i in range(1, 6)]
    manifest = [{"path": f"A/客厅/{i:02}.jpg", "size": len(data[i])}
                for i in range(5)]
    r = client.post("/api/listings",
                    json={"root_dir_name": "A", "manifest": manifest})
    lid = r.json()["listing_id"]
    for i, m in enumerate(manifest):
        client.post(f"/api/listings/{lid}/files",
                    params={"rel_path": m["path"]},
                    files={"file": ("x.jpg", data[i], "image/jpeg")})
    body = client.post(f"/api/listings/{lid}/upload-complete").json()
    assert body["state"] == "awaiting_confirm"
    assert body["selected"] == Selector.per_room_cap_default()
    assert body["photos"] == 5


# ------------------------------------------------------------ per-room cap
def test_per_room_cap_keeps_the_best_and_explains_why(client):
    lid = _upload(client, "A", {"客厅": 6, "主卧": 2})
    detail = client.get(f"/api/listings/{lid}").json()
    rooms = {r["room_dir"]: r["photos"] for r in detail["rooms"]}
    living = rooms["客厅"]
    assert len([p for p in living if p["selected"]]) == Selector.per_room_cap_default()
    dropped = [p for p in living if not p["selected"]]
    assert dropped, "被挤掉的照片必须仍然存在（只是没被推荐）"
    assert all(CAPPED_FLAG in p["flags"] for p in dropped)
    # a room under the cap is untouched
    assert all(p["selected"] for p in rooms["主卧"])


def test_per_room_cap_never_deletes_a_photo(client):
    lid = _upload(client, "A", {"客厅": 6})
    detail = client.get(f"/api/listings/{lid}").json()
    assert len(detail["rooms"][0]["photos"]) == 6


def test_user_can_put_a_capped_photo_back(client):
    lid = _upload(client, "A", {"客厅": 6})
    photos = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"]
    dropped = next(p for p in photos if not p["selected"])
    r = client.put(f"/api/listings/{lid}/selection",
                   json={"selections": {dropped["photo_id"]: True}})
    assert r.status_code == 200, r.text
    after = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"]
    assert len([p for p in after if p["selected"]]) == (
        Selector.per_room_cap_default() + 1)


def test_capped_photo_survives_a_later_auto_select(client):
    lid = _upload(client, "A", {"客厅": 6})
    photos = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"]
    dropped = next(p for p in photos if not p["selected"])
    client.put(f"/api/listings/{lid}/selection",
               json={"selections": {dropped["photo_id"]: True}})
    client.post(f"/api/listings/{lid}/auto-select")
    after = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"]
    assert {p["photo_id"]: p["selected"] for p in after}[dropped["photo_id"]] is True


# -------------------------------------------------------- room order → film
def test_dragged_room_order_reaches_the_storyboard(client):
    """§5.2: 房间顺序拖动. If the planner re-sorts by name, dragging is a lie."""
    lid = _upload(client, "A", {"客厅": 1, "厨房": 1, "主卧": 1})
    detail = client.get(f"/api/listings/{lid}").json()
    order = [r["room_dir"] for r in detail["rooms"]]
    assert order == ["客厅", "厨房", "主卧"], order

    # Drag 主卧 to the front.
    r = client.put(f"/api/listings/{lid}/rooms", json={"rooms": [
        {"room_dir": "主卧", "display_name": "主卧", "order_index": 0},
        {"room_dir": "客厅", "display_name": "客厅", "order_index": 1},
        {"room_dir": "厨房", "display_name": "厨房", "order_index": 2},
    ]})
    assert r.status_code == 200, r.text
    assert [r["room_dir"] for r in r.json()["rooms"]] == ["主卧", "客厅", "厨房"]

    from property_promo.storyboard import planner
    listing = app_mod.store.get_listing(lid)
    board = planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))
    shot_rooms = [s["room"] for s in board["shots"] if s["kind"] == "shot"]
    assert shot_rooms == ["主卧", "客厅", "厨房"], shot_rooms


def test_untouched_rooms_keep_their_natural_position(client):
    """Renaming one room must not shove it in front of every other room."""
    lid = _upload(client, "A", {"客厅": 1, "厨房": 1, "主卧": 1})
    client.put(f"/api/listings/{lid}/rooms", json={"rooms": [
        {"room_dir": "厨房", "display_name": "厨房区", "order_index": None},
    ]})
    detail = client.get(f"/api/listings/{lid}").json()
    order = [r["room_dir"] for r in detail["rooms"]]
    assert order == ["客厅", "厨房", "主卧"], order
    renamed = next(r for r in detail["rooms"] if r["room_dir"] == "厨房")
    assert renamed["display_name"] == "厨房区"


def test_the_room_name_only_ever_appears_as_the_on_screen_label(client):
    """The room name may be printed (it is what makes a walkthrough readable),
    but only in one place: `shot.room_label`. It must never become the opener
    title, the end-card line, or a fabricated sentence — those carry facts the
    film is sent out with (§4).
    """
    lid = _upload(client, "A", {"客厅": 1})
    client.put(f"/api/listings/{lid}/rooms", json={"rooms": [
        {"room_dir": "客厅", "display_name": "秘密房间名", "order_index": 0},
    ]})
    from property_promo.storyboard import planner
    listing = app_mod.store.get_listing(lid)
    board = planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))

    shots = [s for s in board["shots"] if s["kind"] == "shot"]
    assert [s["room_label"] for s in shots] == ["秘密房间名"]
    assert board["shots"][0]["title"] == "房源实拍"
    assert "秘密房间名" not in json.dumps(
        {**board, "shots": [s for s in board["shots"] if s["kind"] != "shot"]},
        ensure_ascii=False)


# ------------------------------------------------- opener title never the address
ADDRESS_LIKE = "示例花园8号楼2单元1201"


def test_opener_never_prints_the_folder_name_as_the_title(client):
    """The folder name is usually the address. It must never reach the film.

    A real E2E once rendered `示例花园8号楼2单元1201` as the big opener title
    because the planner used `listing.name` — the folder name — as the title.
    The film is sent to buyers, so the address cannot be on screen (§4).
    """
    from property_promo.storyboard import planner

    lid = _upload(client, ADDRESS_LIKE, {"客厅": 2})
    listing = app_mod.store.get_listing(lid)
    board = planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))

    opener = board["shots"][0]
    assert opener["kind"] == "opener"
    assert opener["title"] == "房源实拍", (
        "片头标题必须是通用文案，不能把房源文件夹名（往往是地址）印上画面")
    assert ADDRESS_LIKE not in str(opener)
    assert ADDRESS_LIKE not in str(board["contact"])


def test_the_user_can_set_their_own_opener_title(client):
    from property_promo.storyboard import planner

    lid = _upload(client, ADDRESS_LIKE, {"客厅": 2})
    r = client.put(f"/api/listings/{lid}/settings", json={"title": "整层三面采光"})
    assert r.status_code == 200, r.text
    listing = app_mod.store.get_listing(lid)
    board = planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))
    assert board["shots"][0]["title"] == "整层三面采光"
    assert ADDRESS_LIKE not in str(board["shots"][0])


def test_an_overlong_title_is_refused_with_an_actionable_message(client):
    lid = _upload(client, "A", {"客厅": 1})
    r = client.put(f"/api/listings/{lid}/settings", json={"title": "长" * 40})
    assert r.status_code == 422, r.text
    assert "标题" in r.json()["message"] or "title" in r.json()["message"].lower()


def test_global_default_title_reaches_every_listing(client):
    from property_promo.storyboard import planner

    lid = _upload(client, "A", {"客厅": 1})
    g = client.get("/api/workbench").json()["settings"]
    r = client.put("/api/settings/global", json={**g, "title": "实景拍摄"})
    assert r.status_code == 200, r.text
    listing = app_mod.store.get_listing(lid)
    board = planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))
    assert board["shots"][0]["title"] == "实景拍摄"


def test_an_empty_contact_card_still_ends_on_a_readable_line(client):
    """With no contact filled in the outro rendered as a lone heading.

    Verified on a real Remotion still: the end card was an empty black frame
    with 咨询与看房 and nothing else. A generic closing line keeps the last
    frame meaningful without inventing a phone number.
    """
    from property_promo.storyboard import planner

    lid = _upload(client, "A", {"客厅": 1})
    listing = app_mod.store.get_listing(lid)
    board = planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))
    outro = board["shots"][-1]
    assert outro["kind"] == "outro"
    assert outro["contact"]["closing"] == "欢迎预约看房"
    assert outro["contact"]["phone"] == ""


def test_a_real_contact_card_is_never_overwritten(client):
    from property_promo.storyboard import planner

    lid = _upload(client, "A", {"客厅": 1})
    client.put(f"/api/listings/{lid}/settings",
               json={"contact": {"name": "王经理", "phone": "13800138000"}})
    listing = app_mod.store.get_listing(lid)
    board = planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))
    outro = board["shots"][-1]
    assert outro["contact"]["name"] == "王经理"
    assert outro["contact"]["phone"] == "13800138000"
    assert outro["contact"]["closing"] == "", (
        "用户填了联系方式时不要塞默认结语")


# ------------------------------------------------------- photo drag order
def test_dragged_photo_order_reaches_the_storyboard(client):
    """§5.2: 照片拖动排序. The film must follow the order the user dragged to."""
    lid = _upload(client, "A", {"客厅": 3})
    detail = client.get(f"/api/listings/{lid}").json()
    photos = detail["rooms"][0]["photos"]
    names = [p["file_name"] for p in photos]
    assert names == ["00.jpg", "01.jpg", "02.jpg"], names

    # Drag 02.jpg to the front; the whole room is written back, like the UI does.
    orders = {photos[2]["photo_id"]: 0,
              photos[0]["photo_id"]: 1,
              photos[1]["photo_id"]: 2}
    r = client.put(f"/api/listings/{lid}/selection",
                   json={"selections": {}, "orders": orders})
    assert r.status_code == 200, r.text

    from property_promo.storyboard import planner
    listing = app_mod.store.get_listing(lid)
    board = planner.build_storyboard(
        listing, app_mod.current_render_service().effective_settings(lid))
    shot_names = [s["file_name"] for s in board["shots"] if s["kind"] == "shot"]
    assert shot_names == ["02.jpg", "00.jpg", "01.jpg"], shot_names


def test_dragged_photo_order_survives_a_later_auto_select(client):
    """Dragging is a manual decision: 自动推荐不得覆盖 (§5.2)."""
    lid = _upload(client, "A", {"客厅": 3})
    photos = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"]
    client.put(f"/api/listings/{lid}/selection",
               json={"selections": {}, "orders": {photos[2]["photo_id"]: 0}})
    client.post(f"/api/listings/{lid}/auto-select")
    after = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"]
    moved = next(p for p in after if p["photo_id"] == photos[2]["photo_id"])
    assert moved["manual_order"] == 0
    assert moved["manual"] is True


def test_collapsing_a_room_is_a_client_concern_only(client):
    """折叠是纯前端视图状态：后端不得因此丢掉这个房间的照片。"""
    lid = _upload(client, "A", {"客厅": 1, "主卧": 1})
    detail = client.get(f"/api/listings/{lid}").json()
    assert len(detail["rooms"]) == 2
    assert all(room["photos"] for room in detail["rooms"])


# ------------------------------------------------------------ settings wire
def test_music_custom_without_a_file_is_reported_at_queue_time(client):
    """Choosing 自定义上传 without uploading music must not render silently."""
    lid = _upload(client, "A", {"客厅": 1})
    r = client.put(f"/api/listings/{lid}/settings",
                   json={"music": {"mode": "custom"}})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    assert r.status_code == 422, r.text
    assert r.json()["code"] in ("music_file_missing", "music_missing")
    assert "音乐" in r.json()["message"]
