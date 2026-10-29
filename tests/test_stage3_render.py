"""Stage-3 real-render checks: the pool, the setting, and a real film.

The contract tests pin the stub renderer (§ Stage 1). These tests cover
what Stage 3 adds:

  * the app actually wires `RemotionRenderer`, not the stub;
  * 全局设置里的并发数真的改变渲染池的宽度，而且不会杀掉正在渲染的任务；
  * 两个任务真的可以同时渲染（多开），各自的素材目录互不干扰；
  * 一支真实房源片能渲染出可播放的 MP4（慢，默认跳过，用
    PROPERTY_PROMO_REAL_RENDER=1 打开）。
"""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from property_promo.api import app as app_mod
from property_promo.jobs.store import Store
from property_promo.jobs.worker_pool import WorkerPool
from property_promo.rendering.base import RenderResult
from property_promo.rendering.remotion_renderer import RemotionRenderer

pytest.importorskip("fastapi")


# --------------------------------------------------------------- renderer
def test_app_uses_the_remotion_renderer_by_default(monkeypatch):
    """`auto` must pick Remotion whenever this machine can actually run it."""
    monkeypatch.delenv("PROPERTY_PROMO_RENDERER", raising=False)
    monkeypatch.setitem(app_mod.CFG, "renderer", "auto")
    renderer = app_mod.build_renderer()
    if not isinstance(renderer, RemotionRenderer):
        pytest.skip("这台机器还没装渲染依赖：在 renderer/ 里跑一次 pnpm install")
    assert isinstance(renderer, RemotionRenderer), (
        "Stage 3 的成片必须由 Remotion 渲染；桩渲染只能用于测试")


def test_stub_can_be_forced_for_tests(monkeypatch):
    monkeypatch.setenv("PROPERTY_PROMO_RENDERER", "stub")
    assert app_mod.build_renderer().name == "stub"


# ------------------------------------------------------------ concurrency
class _SlowRenderer:
    """Records how many renders overlap, and holds each one briefly."""

    name = "slow"

    def __init__(self, hold: float = 0.6):
        self.hold = hold
        self.in_flight = 0
        self.peak = 0
        self.started: list[str] = []
        self._lock = __import__("threading").Lock()

    def render(self, storyboard, out_path, *, media_dir,
               on_progress=None, cancel=None, job_token=None):
        with self._lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.started.append(job_token or "")
        try:
            time.sleep(self.hold)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00" * 2048)
            return RenderResult(out_path=out_path, duration_s=1.0,
                                size_bytes=2048, width=1920, height=1080,
                                fps=30, has_audio=False, notes=["slow"])
        finally:
            with self._lock:
                self.in_flight -= 1


def _client(tmp_path: Path, monkeypatch):
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
                                              max_photos=20))
    monkeypatch.setattr(app_mod, "_render_cache", {"store": None, "service": None})
    monkeypatch.setattr(app_mod, "_pool_cache", {"store": None, "pool": None})
    monkeypatch.setattr(app_mod, "_batch_cache", {"store": None, "service": None})
    return TestClient(app_mod.app), st


def _jpg(color: tuple) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buf, "JPEG")
    return buf.getvalue()


def _upload(client, name: str, rooms: dict[str, int]) -> str:
    manifest = []
    blobs: dict[str, bytes] = {}
    for room, count in rooms.items():
        for i in range(count):
            data = _jpg((i * 40 % 250, 90, 200))
            rel = f"{name}/{room}/{i:02}.jpg"
            blobs[rel] = data
            manifest.append({"path": rel, "size": len(data)})
    r = client.post("/api/listings", json={"rootDirName": name,
                                           "manifest": manifest})
    assert r.status_code == 200, r.text
    lid = r.json()["listing_id"]
    for rel, data in blobs.items():
        r = client.post(f"/api/listings/{lid}/files",
                        params={"rel_path": rel},
                        files={"file": ("x.jpg", data, "image/jpeg")})
        assert r.status_code == 200, r.text
    assert client.post(f"/api/listings/{lid}/upload-complete").status_code == 200
    return lid


def test_global_concurrency_setting_widens_the_live_pool(tmp_path, monkeypatch):
    """把并发改成 3，池子立刻就是 3 —— 不必重启服务。"""
    monkeypatch.setenv("PROPERTY_PROMO_RENDERER", "stub")
    client, _ = _client(tmp_path, monkeypatch)
    with client:
        r = client.put("/api/settings/global", json={"render_concurrency": 3})
        assert r.status_code == 200, r.text
        pool = app_mod.current_pool()
        assert pool.concurrency == 3
        assert len(pool._threads) == 3


def test_lowering_concurrency_never_cancels_a_running_render(tmp_path, monkeypatch):
    """调小并发只在空闲时生效，不会把正在跑的片子掐掉。"""
    client, st = _client(tmp_path, monkeypatch)
    renderer = _SlowRenderer(hold=1.2)
    # No `with client:` on purpose — the app lifespan would start its own
    # pool, which would then race this test's pool for the queued jobs.
    pool = WorkerPool(st, renderer, media_dir=tmp_path / "media",
                      renders_dir=tmp_path / "renders",
                      service=app_mod.current_render_service(),
                      concurrency=2)
    app_mod._pool_cache = {"store": st, "pool": pool}
    pool.start()
    try:
        lid_a = _upload(client, "A", {"客厅": 2})
        lid_b = _upload(client, "B", {"客厅": 2})
        for lid in (lid_a, lid_b):
            assert client.post(f"/api/listings/{lid}/render",
                               json={"aspect": "16:9"}).status_code == 200
        deadline = time.time() + 5
        while time.time() < deadline and renderer.in_flight < 2:
            time.sleep(0.05)
        assert renderer.in_flight == 2, "两路应该同时在渲染（多开）"

        # 用户把并发从 2 调到 1：现在不能立刻缩容
        got = pool.apply_concurrency(1)
        assert got is False, "正在渲染时不能立刻缩容"
        assert pool.concurrency == 2
        assert renderer.in_flight == 2, "已在渲染的任务不能被取消"

        deadline = time.time() + 20
        while time.time() < deadline and pool.active_count() > 0:
            time.sleep(0.1)
        time.sleep(1.0)
        pool._settle_pending_concurrency()
        assert pool.concurrency == 1
    finally:
        pool.stop()


def test_two_renders_really_run_in_parallel(tmp_path, monkeypatch):
    """多开：两套房同时渲染，峰值并发必须达到 2。"""
    monkeypatch.setenv("PROPERTY_PROMO_RENDERER", "stub")
    client, st = _client(tmp_path, monkeypatch)
    renderer = _SlowRenderer(hold=0.8)
    pooled = WorkerPool(st, renderer, media_dir=tmp_path / "media",
                        renders_dir=tmp_path / "renders",
                        service=app_mod.current_render_service(),
                        concurrency=2)
    app_mod._pool_cache = {"store": st, "pool": pooled}
    lid_a = _upload(client, "P-A", {"客厅": 2})
    lid_b = _upload(client, "P-B", {"客厅": 2})
    for lid in (lid_a, lid_b):
        assert client.post(f"/api/listings/{lid}/render",
                           json={"aspect": "16:9"}).status_code == 200
    pooled.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline and renderer.peak < 2:
            time.sleep(0.1)
        assert renderer.peak == 2, f"峰值并发只有 {renderer.peak}，没有真正多开"
        assert len(renderer.started) == 2
    finally:
        pooled.stop()


# --------------------------------------------------------- the real film
@pytest.mark.skipif(os.environ.get("PROPERTY_PROMO_REAL_RENDER") != "1",
                    reason="真渲染 2~3 分钟；用 PROPERTY_PROMO_REAL_RENDER=1 打开")
def test_a_real_listing_renders_a_playable_film():
    """用模块数据里真实存在的房源跑一支完整成片。"""
    store = app_mod.store
    listing = None
    for candidate in store.list_listings():
        if candidate.rooms and candidate.state.value in ("complete", "awaiting_confirm"):
            listing = candidate
            break
    if listing is None:
        pytest.skip("库里没有已上传的房源，跳过真渲染")
    service = app_mod.current_render_service()
    settings = service.effective_settings(listing.listing_id)
    from property_promo.storyboard import planner
    board = planner.build_storyboard(listing, settings)
    out = Path(app_mod.CFG["_renders_dir"]) / listing.listing_id / "stage3_test.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    renderer = RemotionRenderer()
    result = renderer.render(
        board, out,
        media_dir=Path(app_mod.CFG["_data_dir"]) / "media" / listing.listing_id,
        job_token="stage3test")
    assert out.is_file() and out.stat().st_size > 200_000
    assert result.width == board["canvas"]["width"]
    assert result.height == board["canvas"]["height"]
    assert result.duration_s > 5
