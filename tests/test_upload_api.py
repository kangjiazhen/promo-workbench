import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from property_promo.api import app as app_mod

os = pytest.importorskip("fastapi")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """Fresh app with isolated data dir per test."""
    media = tmp_path / "media"
    media.mkdir()
    app_mod.CFG["_data_dir"] = tmp_path
    monkeypatch.setattr(app_mod, "store", app_mod.Store(tmp_path / "t.db"))
    monkeypatch.setattr(
        app_mod, "service",
        app_mod.UploadService(app_mod.store, media, max_upload_mb=5,
                              max_photos=10))
    return TestClient(app_mod.app)


def _jpg(color: tuple, size: (int, int) = (640, 480)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _manifest(*items):
    return [{"path": p, "size": s} for p, s in items]


def test_full_upload_flow(client):
    # 1) create with manifest: two rooms, same filename in both
    a, b, c = _jpg((200, 30, 30)), _jpg((30, 200, 30)), _jpg((30, 30, 200))
    r = client.post("/api/listings", json={
        "root_dir_name": "AJ13103",
        "manifest": _manifest(("AJ13103/客厅/01.jpg", len(a)),
                              ("AJ13103/主卧/01.jpg", len(b)),
                              ("AJ13103/厨房/01.jpg", len(c)))})
    assert r.status_code == 200, r.text
    lid = r.json()["listing_id"]

    # 2) upload each file
    for rel, data in [("AJ13103/客厅/01.jpg", a),
                      ("AJ13103/主卧/01.jpg", b),
                      ("AJ13103/厨房/01.jpg", c)]:
        rr = client.post(
            f"/api/listings/{lid}/files",
            params={"rel_path": rel},
            files={"file": ("x.jpg", data, "image/jpeg")})
        assert rr.status_code == 200, rr.text

    # 3) incomplete before complete? all files are there → awaiting_confirm
    r = client.post(f"/api/listings/{lid}/upload-complete")
    assert r.status_code == 200, r.text
    body = r.json()
    # §6: 上传完成 = 待确认（人工确认后才能渲染），从来不是 complete。
    assert body["state"] == "awaiting_confirm"
    assert body["rooms"] == 3 and body["photos"] == 3

    # 4) listing detail: rooms in tour order, same filename twice
    r = client.get(f"/api/listings/{lid}")
    assert r.status_code == 200
    rooms = r.json()["rooms"]
    assert [x["room_dir"] for x in rooms] == ["客厅", "厨房", "主卧"]
    names = [p["file_name"] for x in rooms for p in x["photos"]]
    assert names == ["01.jpg", "01.jpg", "01.jpg"]


def test_manifest_mismatch_missing_file(client):
    a = _jpg((1, 2, 3))
    r = client.post("/api/listings", json={
        "root_dir_name": "X",
        "manifest": _manifest(("X/客厅/a.jpg", len(a)),
                              ("X/客厅/b.jpg", len(a)))})
    lid = r.json()["listing_id"]
    client.post(f"/api/listings/{lid}/files", params={"rel_path": "X/客厅/a.jpg"},
                files={"file": ("a.jpg", a, "image/jpeg")})
    r = client.post(f"/api/listings/{lid}/upload-complete")
    assert r.status_code == 422
    assert "b.jpg" in r.json()["message"]
    assert "重新" in r.json()["message"] or "重试" in r.json()["message"]


def test_root_loose_images_rejected_with_actionable_message(client):
    r = client.post("/api/listings", json={
        "root_dir_name": "X",
        "manifest": _manifest(("X/客厅/a.jpg", 10), ("X/散图.jpg", 10))})
    assert r.status_code == 422
    msg = r.json()["message"]
    assert "散图.jpg" in msg and "房间" in msg


def test_nested_dirs_rejected(client):
    r = client.post("/api/listings", json={
        "root_dir_name": "X",
        "manifest": _manifest(("X/客厅/实拍/01.jpg", 10))})
    assert r.status_code == 422
    assert "客厅/实拍/01.jpg" in r.json()["message"]


def test_unsupported_ext_listed(client):
    r = client.post("/api/listings", json={
        "root_dir_name": "X",
        "manifest": _manifest(("X/客厅/a.jpg", 10), ("X/客厅/b.heic", 10))})
    assert r.status_code == 422
    msg = r.json()["message"]
    assert "b.heic" in msg and "JPG" in msg


def test_empty_manifest_rejected(client):
    r = client.post("/api/listings", json={
        "root_dir_name": "X", "manifest": []})
    assert r.status_code == 422


def test_too_many_photos(client):
    items = [(f"X/客厅/{i:03}.jpg", 100) for i in range(11)]
    r = client.post("/api/listings", json={
        "root_dir_name": "X", "manifest": _manifest(*items)})
    assert r.status_code == 422
    assert "300" not in r.json()["message"]  # 测试实例限 10
    assert "上限" in r.json()["message"]


def test_unsafe_manifest_path_rejected(client):
    r = client.post("/api/listings", json={
        "root_dir_name": "X",
        "manifest": _manifest(("X/../../evil.jpg", 10))})
    assert r.status_code == 422
    assert r.json()["code"] == "unsafe_path"


def test_upload_wrong_size_detected(client):
    a = _jpg((9, 9, 9))
    r = client.post("/api/listings", json={
        "root_dir_name": "X",
        "manifest": _manifest(("X/客厅/a.jpg", len(a) + 999))})
    lid = r.json()["listing_id"]
    r = client.post(f"/api/listings/{lid}/files",
                    params={"rel_path": "X/客厅/a.jpg"},
                    files={"file": ("a.jpg", a, "image/jpeg")})
    assert r.status_code == 422
    assert "大小" in r.json()["message"] or "不一致" in r.json()["message"]


def test_undecodable_image_rejected_explicitly(client):
    junk = b"not an image!!"  # 与清单声明的大小一致，排除大小校验的干扰
    r = client.post("/api/listings", json={
        "root_dir_name": "X",
        "manifest": _manifest(("X/客厅/fake.jpg", len(junk)))})
    lid = r.json()["listing_id"]
    r = client.post(f"/api/listings/{lid}/files",
                    params={"rel_path": "X/客厅/fake.jpg"},
                    files={"file": ("fake.jpg", junk, "image/jpeg")})
    assert r.status_code == 422
    assert "无法作为图片打开" in r.json()["message"]


def test_resume_upload_idempotent(client):
    a = _jpg((5, 5, 5))
    r = client.post("/api/listings", json={
        "root_dir_name": "X",
        "manifest": _manifest(("X/客厅/a.jpg", len(a)),
                              ("X/主卧/b.jpg", len(a)))})
    lid = r.json()["listing_id"]
    # re-send the same file twice (client retry)
    for _ in range(2):
        rr = client.post(f"/api/listings/{lid}/files",
                         params={"rel_path": "X/客厅/a.jpg"},
                         files={"file": ("a.jpg", a, "image/jpeg")})
        assert rr.status_code == 200
    # then finish the rest and complete
    client.post(f"/api/listings/{lid}/files",
                params={"rel_path": "X/主卧/b.jpg"},
                files={"file": ("b.jpg", a, "image/jpeg")})
    r = client.post(f"/api/listings/{lid}/upload-complete")
    assert r.status_code == 200
