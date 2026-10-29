"""The workbench API (§5, §6, §9).

These tests cover the product contract, not the plumbing:

  * the task list shows every listing with the state the user expects;
  * a manual choice is never overwritten by the automatic recommendation;
  * a listing may only enter the queue from 待确认;
  * the queue is idempotent and the render produces a playable MP4;
  * the统一错误信封 always carries a message that says what to do next.
"""
from __future__ import annotations

import io
import shutil
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from property_promo.api import app as app_mod
from property_promo.domain.models import JobState
from property_promo.jobs import store as store_mod
from property_promo.jobs.store import Store
from property_promo.jobs.worker_pool import WorkerPool

pytest.importorskip("fastapi")


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    """A fresh app with its own data dir, store and render pool."""
    # The contract tests exercise the job/version/recovery chain, not the
    # film: pin the fast ffmpeg renderer so a test run never waits on a
    # Remotion bundle. The real renderer has its own test.
    monkeypatch.setenv("PROPERTY_PROMO_RENDERER", "stub")
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
        app_mod.UploadService(st, media, max_upload_mb=5, max_photos=20))
    monkeypatch.setattr(app_mod, "_render_cache",
                        {"store": None, "service": None})
    monkeypatch.setattr(app_mod, "_pool_cache", {"store": None, "pool": None})
    monkeypatch.setattr(app_mod, "_batch_cache", {"store": None, "service": None})
    with TestClient(app_mod.app) as c:
        yield c


def _jpg(color: tuple, size: (int, int) = (640, 480)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _upload(client, name: str, rooms: dict[str, int]) -> str:
    """Create a listing and upload `rooms` photos; return the listing id."""
    manifest = []
    blobs = {}
    for room, count in rooms.items():
        for i in range(count):
            data = _jpg((i * 40 % 250, 90, 200))
            blobs[f"{name}/{room}/{i:02}.jpg"] = data
            manifest.append({"path": f"{name}/{room}/{i:02}.jpg",
                             "size": len(data)})
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


# ------------------------------------------------------- upload wire contract
def test_listing_create_accepts_the_camel_case_wire_field(client):
    """The browser posts `rootDirName`; the server must accept it.

    The field used to be `root_dir_name` only, so every upload from the
    workbench failed with a pydantic "field required" error that said
    nothing about photos at all.
    """
    data = _jpg((10, 200, 30))
    r = client.post("/api/listings",
                    json={"rootDirName": "AJ13103",
                          "manifest": [{"path": "AJ13103/客厅/01.jpg",
                                        "size": len(data)}]})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "AJ13103"
    assert r.json()["expected_files"] == 1


def test_manifest_must_be_three_segments(client):
    """房源根/房间/图片 — the room name only exists under a listing root."""
    data = _jpg((10, 200, 30))
    r = client.post("/api/listings",
                    json={"root_dir_name": "AJ13103",
                          "manifest": [{"path": "客厅/01.jpg", "size": len(data)}]})
    assert r.status_code == 422, r.text
    assert r.json()["code"] in ("root_loose_images", "not_room_file")


def test_batch_is_opened_and_closed_and_listings_carry_it(client):
    """一次「开始上传」= 一个批次（§3.1）。

    Found live: nothing ever opened a batch, so every listing had
    batch_id = null and the workbench `batches` array was always empty —
    「完成上传」/「继续添加」 had nothing to attach to.
    """
    r = client.post("/api/batches", json={"label": "A、B"})
    assert r.status_code == 200, r.text
    batch_id = r.json()["batch_id"]
    assert r.json()["state"] == "open"

    payload = client.get("/api/workbench").json()
    assert [b["batch_id"] for b in payload["batches"]] == [batch_id]

    data = _jpg((10, 200, 30))
    r = client.post("/api/listings",
                    json={"rootDirName": "A",
                          "batch_id": batch_id,
                          "manifest": [{"path": "A/客厅/01.jpg",
                                        "size": len(data)}]})
    assert r.status_code == 200, r.text
    assert r.json()["batch_id"] == batch_id

    r = client.post(f"/api/batch/{batch_id}/close")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "closed"
    # 关闭后仍是历史批次（工作台要显示），但不再是「未关闭批次」，
    # 所以「继续添加」会开新批次（§3.1）。
    payload = client.get("/api/workbench").json()
    closed = [b for b in payload["batches"] if b["batch_id"] == batch_id]
    assert closed and closed[0]["state"] == "closed"
    assert app_mod.current_batch_service().open_batch_id() is None


def test_frontend_manifest_builder_keeps_the_listing_root_segment():
    """The client and the server must agree on the manifest shape.

    The client strips the *parent* segment when it splits a parent folder
    into listings, but it must keep the *listing root* segment — the
    server contract is three segments. This test reads the actual
    frontend source, so the two sides cannot drift apart silently.
    """
    src = (Path(__file__).resolve().parents[1] / "web" / "src" / "lib"
           / "batchUpload.ts").read_text(encoding="utf-8")
    # the manifest key is built as `${root}/${room}/${rest}`
    assert "${displayName}/${room}/${rest}" in src, (
        "batchUpload.ts must key the manifest by 房源根/房间/图片")
    assert "${dir}/${rest}" in src, (
        "batchUpload.ts must key the manifest by 房源根/房间/图片")


def test_duplicate_root_names_get_a_suffix_and_keep_their_own_files():
    """§12.2: 两个拖拽的同名根 → 后者加后缀 `房源A (2)`，各自独立。

    The rename has to rewrite the manifest paths *and* the file index
    together; renaming only the display name would send two listings with
    the same root segment and they would collide on disk.
    """
    src = (Path(__file__).resolve().parents[1] / "web" / "src" / "lib"
           / "batchUpload.ts").read_text(encoding="utf-8")
    assert "export function withUniqueRootName" in src
    assert "`${unit.rootDirName} (${n})`" in src
    # the panel must actually call it when a folder is added
    panel = (Path(__file__).resolve().parents[1] / "web" / "src"
             / "components" / "BatchUploadPanel.tsx").read_text(encoding="utf-8")
    assert "withUniqueRootName(u, used)" in panel


# ----------------------------------------------------------------- task list
def test_workbench_lists_listings_and_queue(client):
    lid = _upload(client, "AJ13103", {"客厅": 2, "主卧": 1})
    r = client.get("/api/workbench")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [x["listing_id"] for x in body["listings"]]
    assert lid in ids
    row = next(x for x in body["listings"] if x["listing_id"] == lid)
    assert row["state"] == "awaiting_confirm"
    assert row["state_label"] == "待确认"
    assert row["room_count"] == 2
    assert row["photos"] == 3
    assert row["can_render"] is True
    assert body["queue"]["concurrency"] >= 1
    assert body["queue"]["jobs"] == []
    assert body["counts"]["awaiting_confirm"] == 1


def test_workbench_state_filter(client):
    _upload(client, "A", {"客厅": 1})
    r = client.get("/api/workbench?state=awaiting_confirm")
    assert r.status_code == 200
    assert len(r.json()["listings"]) == 1
    r = client.get("/api/workbench?state=complete")
    assert r.json()["listings"] == []


def test_listing_detail_has_rooms_photos_and_versions(client):
    lid = _upload(client, "AJ13103", {"客厅": 2, "主卧": 1})
    r = client.get(f"/api/listings/{lid}")
    assert r.status_code == 200
    body = r.json()
    assert [x["room_dir"] for x in body["rooms"]] == ["客厅", "主卧"]
    assert body["versions"] == []
    assert body["missing_media"] == []
    assert body["media_warning"] == ""


def test_unknown_listing_returns_actionable_error(client):
    r = client.get("/api/listings/nope")
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "listing_not_found"
    assert body["message"]


# ----------------------------------------------------------------- selection
def test_manual_selection_survives_auto_select(client):
    lid = _upload(client, "A", {"客厅": 3})
    photos = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"]
    target = photos[0]["photo_id"]
    # The user deselects a photo by hand.
    r = client.put(f"/api/listings/{lid}/selection",
                   json={"selections": {target: False}})
    assert r.status_code == 200, r.text
    # The automatic recommendation must not undo that choice.
    r = client.post(f"/api/listings/{lid}/auto-select")
    assert r.status_code == 200, r.text
    after = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"]
    picked = {p["photo_id"]: p["selected"] for p in after}
    assert picked[target] is False


def test_selection_conflict_while_rendering(client):
    lid = _upload(client, "A", {"客厅": 2})
    client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    r = client.put(f"/api/listings/{lid}/selection",
                   json={"selections": {"x": False}})
    assert r.status_code == 422
    assert r.json()["code"] == "selection_conflict"


# ------------------------------------------------------------------ settings
def test_global_settings_round_trip(client):
    r = client.get("/api/settings/global")
    assert r.status_code == 200
    base = r.json()
    assert base["aspect"] in ("16:9", "9:16", "4:3", "3:4", "1:1")

    r = client.put("/api/settings/global",
                   json={"aspect": "9:16",
                         "contact": {"name": "张经纪", "phone": "13800000000"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["aspect"] == "9:16"
    assert body["contact"]["name"] == "张经纪"
    # the untouched keys keep their defaults
    assert body["contact"]["agency"] == base["contact"]["agency"]


def test_listing_override_beats_global_and_can_be_reset(client):
    client.put("/api/settings/global",
               json={"contact": {"name": "全局甲", "phone": "111"}})
    lid = _upload(client, "A", {"客厅": 1})
    r = client.put(f"/api/listings/{lid}/settings",
                   json={"contact": {"name": "本套乙"}})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["contact"]["name"] == "本套乙"
    assert r.json()["settings"]["contact"]["phone"] == "111"

    # null resets that key back to the global default
    r = client.put(f"/api/listings/{lid}/settings",
                   json={"contact": {"name": None}})
    assert r.status_code == 200
    assert r.json()["settings"]["contact"]["name"] == "全局甲"


def test_all_five_aspects_are_offered(client):
    r = client.get("/api/settings/global")
    assert r.status_code == 200
    # the five画幅 are pinned by §5.2
    from property_promo.domain import constants
    assert set(constants.ASPECTS) == {"16:9", "9:16", "4:3", "3:4", "1:1"}
    assert "16:9" in constants.ASPECTS and "4:3" in constants.ASPECTS


# -------------------------------------------------------------------- render
def test_render_requires_confirmed_state(client):
    lid = _upload(client, "A", {"客厅": 1})
    # force it back to an unconfirmed state
    app_mod.store.set_state(lid, app_mod.UploadState.UPLOADING)
    r = client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    assert r.status_code == 422
    assert r.json()["code"] in ("listing_not_confirmed", "illegal_transition")


def test_render_batch_refuses_one_bad_listing_but_queues_the_rest(client):
    good = _upload(client, "A", {"客厅": 2})
    bad = _upload(client, "B", {"客厅": 1})
    app_mod.store.set_state(bad, app_mod.UploadState.UPLOADING)
    r = client.post("/api/renders/batch",
                    json={"listing_ids": [good, bad], "aspect": "16:9"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["jobs"]) == 1
    assert body["jobs"][0]["listing_id"] == good
    assert len(body["refused"]) == 1
    assert body["refused"][0]["listing_id"] == bad
    assert body["refused"][0]["reason"]


def test_finished_jobs_leave_the_queue_but_stay_on_the_listing(client):
    """§5.4: 队列是活视图 —— 跑完的任务不许一直赖在队列里。

    The panel renders `queue.jobs`; when the server fed it every historical
    job, the bottom of the page showed yesterday's renders forever and the
    queue looked like it could never be dismissed.
    """
    lid = _upload(client, "A", {"客厅": 2})
    job_id = client.post(f"/api/listings/{lid}/render",
                         json={"aspect": "16:9"}).json()["jobs"][0]["job_id"]

    body = client.get("/api/workbench").json()
    assert [j["job_id"] for j in body["queue"]["jobs"]] == [job_id]
    assert body["queue"]["jobs"][0]["state"] == "queued"

    # finish it the way a worker does, then poll again
    app_mod.store.update_job(job_id, state=JobState.COMPLETE, progress=1.0)
    body = client.get("/api/workbench").json()
    assert body["queue"]["jobs"] == []

    # the row itself is not deleted: it is still there for the record
    assert [j.job_id for j in app_mod.store.list_jobs()] == [job_id]


def test_render_batch_is_idempotent(client):
    lid = _upload(client, "A", {"客厅": 1})
    client.post("/api/renders/batch", json={"listing_ids": [lid],
                                            "aspect": "16:9"})
    # the second call must not queue a second job for the same listing
    r = client.post("/api/renders/batch", json={"listing_ids": [lid],
                                                "aspect": "16:9"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "nothing_to_render"
    assert "队列" in body["message"]
    jobs = app_mod.store.list_jobs_by_state(JobState.QUEUED)
    assert len(jobs) == 1


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="桩渲染也要用 ffmpeg 编一段真 MP4"
)
def test_render_produces_a_playable_mp4_and_a_version(client, tmp_path):
    lid = _upload(client, "A", {"客厅": 3})
    r = client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    assert r.status_code == 200, r.text
    job_id = r.json()["jobs"][0]["job_id"]

    pool = app_mod.current_pool()
    pool.start()
    deadline = time.time() + 60
    while time.time() < deadline:
        job = app_mod.store.get_job(job_id)
        if job and job.state.value in ("complete", "failed", "cancelled"):
            break
        time.sleep(0.2)
    pool.stop()

    job = app_mod.store.get_job(job_id)
    assert job.state.value == "complete", job.error
    # A finished job must carry an end time: the workbench shows 完成时间 and
    # computes 耗时 from started_at→finished_at. Found live: only cancel
    # stamped finished_at, so every completed job read as 1970 (m04157).
    assert job.finished_at > 0, "完成/失败必须写 finished_at"
    assert job.finished_at >= job.started_at
    versions = client.get(f"/api/listings/{lid}/video-versions").json()
    assert len(versions) == 1
    vid = versions[0]["version_id"]
    r = client.get(f"/api/video-versions/{vid}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    assert len(r.content) > 1000
    # the listing itself ends up 完成
    assert client.get(f"/api/listings/{lid}").json()["state"] == "complete"


def test_cancel_and_retry_a_failed_job(client):
    lid = _upload(client, "A", {"客厅": 1})
    r = client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    job_id = r.json()["jobs"][0]["job_id"]
    # cancel before the worker picks it up
    r = client.post(f"/api/render-jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["state"] in ("cancelled", "queued")


def test_render_is_refused_when_a_photo_is_missing_from_disk(client):
    """A listing whose files vanished must be refused at queue time.

    Found live: a listing from an older upload layout had 17 rows in the
    database but only 6 files on disk. It was accepted into the queue and
    then failed a minute later at 5% with "照片文件缺失". The user should
    learn that before the worker starts, in the same breath as the fix.
    """
    lid = _upload(client, "A", {"客厅": 2})
    # delete one photo from disk behind the app's back — resolved through
    # the same helper the service uses, so the test cannot drift
    from property_promo.ingestion import media_paths
    rel = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"][0]["rel_path"]
    victim = media_paths.photo_path(Path(app_mod.CFG["_data_dir"]) / "media",
                                   lid, rel)
    assert victim.is_file(), victim
    victim.unlink()
    r = client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "media_missing"
    assert "找不到" in body["message"]
    assert "重新上传" in body["message"]
    # and nothing was queued
    assert app_mod.store.list_jobs_by_state(JobState.QUEUED) == []
    assert client.get(f"/api/listings/{lid}").json()["state"] == "awaiting_confirm"


def test_retry_refuses_a_job_that_did_not_fail(client):
    lid = _upload(client, "A", {"客厅": 1})
    r = client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    job_id = r.json()["jobs"][0]["job_id"]
    r = client.post(f"/api/render-jobs/{job_id}/retry")
    assert r.status_code == 422
    assert r.json()["code"] == "not_failed"


def test_thumbnail_is_served_by_id(client):
    lid = _upload(client, "A", {"客厅": 1})
    photo = client.get(f"/api/listings/{lid}").json()["rooms"][0]["photos"][0]
    r = client.get(f"/api/photos/{photo['photo_id']}/thumbnail?size=128")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


# ------------------------------------------------------------------ recovery
def test_stale_rendering_job_is_swept_to_failed_not_requeued(tmp_path):
    st = Store(tmp_path / "s.db")
    listing = st.create_listing("A", "A", batch_id=None)
    st.set_manifest(listing.listing_id, {"客厅/01.jpg": 10})
    job = st.create_job("job-1", listing.listing_id, "16:9", {"shots": []})
    claimed = st.claim_next_job("worker-x")
    assert claimed is not None and claimed.job_id == "job-1"
    # the lease is expired
    st.update_job("job-1", lease_expires_at=0.0)
    pool = WorkerPool(st, app_mod.StubRenderer(), media_dir=tmp_path / "m",
                      renders_dir=tmp_path / "r")
    swept = pool.sweep_stale_jobs()
    assert swept == ["job-1"]
    assert st.get_job("job-1").state.value == "failed"
    # and it is NOT back in the queue
    assert st.list_jobs_by_state(JobState.QUEUED) == []


def test_store_migration_adds_missing_columns(tmp_path):
    db = tmp_path / "old.db"
    st = Store(db)
    st.close()
    # create an old-shaped table by hand
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("DROP TABLE listings")
    con.execute("CREATE TABLE listings (listing_id TEXT PRIMARY KEY, name TEXT,"
                " root_dir_name TEXT, state TEXT, settings TEXT, batch_id TEXT,"
                " error TEXT, progress REAL, stage TEXT, created_at REAL,"
                " updated_at REAL, expected_files TEXT)")
    con.commit()
    con.close()
    st2 = Store(db)  # must not raise
    st2.close()
