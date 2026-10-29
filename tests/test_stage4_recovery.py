"""Stage-4 checks: crash recovery and video quality gates (§10 阶段④).

Three things must be true before a film can be called delivered:

  * a server that died mid-render leaves nothing lying around — the job is
    marked failed with a readable reason and can be retried in one click;
  * a job cannot report 完成 for a film that is blank, or that has a
    soundtrack of pure silence;
  * the QA helpers accept a genuinely rendered film (checked here against
    a real render, when one exists on disk).
"""
from __future__ import annotations

import io
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from property_promo.api import app as app_mod
from property_promo.domain.models import JobState, ListingState
from property_promo.jobs.store import Store
from property_promo.jobs.worker_pool import WorkerPool
from property_promo.rendering import verifier
from property_promo.rendering.base import RenderResult

pytest.importorskip("fastapi")

# 这些测试都要真的编解码一段片子（质检测试没有 ffmpeg 就无从谈起），
# 机器上没装就整体跳过，而不是红一片。
pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要 ffmpeg / ffprobe（质检与抽帧都靠它）",
)


# ----------------------------------------------------------------- helpers
def _ffmpeg(*args: str) -> None:
    exe = shutil.which("ffmpeg")
    assert exe, "需要 ffmpeg 才能跑质检测试"
    proc = subprocess.run([exe, "-v", "error", "-y", *args],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def _blank_film(path: Path, seconds: float = 2.0,
                size: str = "1920x1080") -> Path:
    """Playable film with a fine soundtrack and nothing on screen.

    The tone matters: without it the audio gate fires first and the test
    would prove the wrong guard. Here only the frame check can catch it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i", f"color=c=black:s={size}:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path))
    return path


def _silent_film(path: Path, seconds: float = 2.0,
                 size: str = "1920x1080") -> Path:
    """Real picture, real audio stream — but the audio is digital silence.

    This is exactly what the module shipped before the music bed was wired:
    `has_audio` was True and the waveform measured -91 dB.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i",
            f"testsrc2=size={size}:rate=30:duration={seconds}",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(path))
    return path


def _client(tmp_path: Path, monkeypatch):
    media = tmp_path / "media"
    renders = tmp_path / "renders"
    media.mkdir()
    renders.mkdir()
    monkeypatch.setenv("PROPERTY_PROMO_RENDERER", "stub")
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


class _BlankRenderer:
    """Renders a real, playable, completely black film — QA must catch it."""

    name = "remotion"

    def __init__(self, film: Path, *, width: int = 1920, height: int = 1080,
                 has_audio: bool = False):
        self.film = film
        self.width = width
        self.height = height
        self.has_audio = has_audio

    def render(self, storyboard, out_path, *, media_dir,
               on_progress=None, cancel=None, job_token=None):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.film, out_path)
        return RenderResult(out_path=out_path, duration_s=2.0,
                            size_bytes=out_path.stat().st_size,
                            width=self.width, height=self.height, fps=30,
                            has_audio=self.has_audio, notes=["blank"])


class _SilentRenderer(_BlankRenderer):
    """Has an audio track, but it is digital silence."""


def _run_with(renderer, client, st, tmp_path, *, expect_audio_in_board=False):
    """Queue one listing and run it through a pool with `renderer`."""
    lid = _upload(client, "QA", {"客厅": 2})
    r = client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    assert r.status_code == 200, r.text
    job_id = r.json()["jobs"][0]["job_id"]
    pool = WorkerPool(st, renderer, media_dir=tmp_path / "media",
                      renders_dir=tmp_path / "renders",
                      service=app_mod.current_render_service(), concurrency=1)
    pool.start()
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            job = st.get_job(job_id)
            if job and job.state.value in ("complete", "failed", "cancelled"):
                break
            time.sleep(0.1)
    finally:
        pool.stop()
    return lid, st.get_job(job_id)


# ------------------------------------------------------------------ quality
def test_a_blank_film_is_never_reported_as_complete(tmp_path, monkeypatch):
    """ffprobe alone would pass a black film; the frame check must not."""
    client, st = _client(tmp_path, monkeypatch)
    film = _blank_film(tmp_path / "blank.mp4")
    _lid, job = _run_with(_BlankRenderer(film), client, st, tmp_path)
    assert job.state == JobState.FAILED, "全黑成片不能报完成"
    assert "纯黑" in (job.error or "") or "空白" in (job.error or ""), job.error
    assert st.list_jobs_by_state(JobState.COMPLETE) == []


def test_a_silent_soundtrack_is_rejected_when_music_was_asked_for(tmp_path,
                                                                 monkeypatch):
    """有音轨但全程无声：配乐没写进去，不能当成功。"""
    client, st = _client(tmp_path, monkeypatch)
    film = _silent_film(tmp_path / "silent.mp4")

    class _SilentWithAudio(_SilentRenderer):
        pass

    _lid, job = _run_with(_SilentWithAudio(film, has_audio=True),
                          client, st, tmp_path)
    assert job.state == JobState.FAILED, "静音配乐不能报完成"
    assert "无声" in (job.error or ""), job.error


def test_the_frame_gate_accepts_a_normal_film(tmp_path):
    """Positive control: a gradient film with real detail passes."""
    film = tmp_path / "grad.mp4"
    film.parent.mkdir(parents=True, exist_ok=True)
    _ffmpeg("-f", "lavfi", "-i",
            "testsrc2=size=320x180:rate=30:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(film))
    frames = verifier.sample_frames(film, count=4, work_dir=tmp_path / "qa")
    assert len(frames) == 4
    verifier.check_frames(frames)


def test_loudness_reports_measured_levels(tmp_path):
    silent = _silent_film(tmp_path / "silent.mp4")
    facts = verifier.loudness(silent)
    assert facts["max_db"] < -60.0, facts


# ---------------------------------------------------------------- recovery
def test_a_crashed_render_is_swept_cleaned_and_retryable(tmp_path, monkeypatch):
    """服务端重启：渲染中作业 → 失败+清扫+清临时文件，且能一键重试。"""
    client, st = _client(tmp_path, monkeypatch)
    lid = _upload(client, "R", {"客厅": 2})
    r = client.post(f"/api/listings/{lid}/render", json={"aspect": "16:9"})
    job_id = r.json()["jobs"][0]["job_id"]

    # Simulate a worker that claimed the job and then died: lease expired,
    # a half-written temp file left behind.
    claimed = st.claim_next_job("worker-dead")
    assert claimed and claimed.job_id == job_id
    st.update_job(job_id, lease_expires_at=0.0)
    renders = Path(app_mod.CFG["_renders_dir"])
    leftover = renders / lid / f"{job_id}.partial.mp4"
    leftover.parent.mkdir(parents=True, exist_ok=True)
    leftover.write_bytes(b"half")

    pool = WorkerPool(st, app_mod.StubRenderer(), media_dir=tmp_path / "media",
                      renders_dir=renders)
    swept = pool.sweep_stale_jobs()
    assert swept == [job_id]
    job = st.get_job(job_id)
    assert job.state == JobState.FAILED
    assert "失联" in (job.error or "")
    assert not leftover.exists(), "半成品文件必须被清掉"
    listing = st.get_listing(lid)
    assert listing.state == ListingState.FAILED

    # One-click retry: a brand-new queued job, and it really renders.
    r = client.post(f"/api/render-jobs/{job_id}/retry")
    assert r.status_code == 200, r.text
    new_job_id = r.json()["job_id"]
    assert new_job_id != job_id
    assert r.json()["state"] == "queued"

    live = WorkerPool(st, app_mod.StubRenderer(), media_dir=tmp_path / "media",
                      renders_dir=renders,
                      service=app_mod.current_render_service())
    live.start()
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            done = st.get_job(new_job_id)
            if done and done.state.value in ("complete", "failed", "cancelled"):
                break
            time.sleep(0.1)
    finally:
        live.stop()
    done = st.get_job(new_job_id)
    assert done.state == JobState.COMPLETE, done.error


def test_startup_sweep_runs_before_the_pool_starts(tmp_path, monkeypatch):
    """App startup must sweep, not just the manual call (§5.4)."""
    _client(tmp_path, monkeypatch)
    st = app_mod.store
    listing = st.create_listing("S", "S", batch_id=None)
    st.set_manifest(listing.listing_id, {"客厅/01.jpg": 10})
    job = st.create_job("job-stale", listing.listing_id, "16:9", {"shots": []})
    st.claim_next_job("dead")
    st.update_job(job.job_id, lease_expires_at=0.0)

    with TestClient(app_mod.app):
        pass  # lifespan: sweep then start, then clean shutdown

    assert st.get_job("job-stale").state == JobState.FAILED


# ------------------------------------------------------------------- real
def test_qa_helpers_pass_on_a_really_rendered_film():
    """If a real render exists on disk, the QA gate must accept it."""
    renders = Path(app_mod.CFG["_renders_dir"])
    candidates = sorted(renders.glob("*/*.mp4"), key=lambda p: -p.stat().st_size)
    real = [p for p in candidates if p.stat().st_size > 5_000_000]
    if not real:
        pytest.skip("磁盘上没有真渲染成片，跳过")
    film = real[0]
    frames = verifier.sample_frames(film, count=5, work_dir=renders / "_qa")
    verifier.check_frames(frames)
    facts = verifier.loudness(film)
    assert facts["max_db"] > -30.0, f"{film.name} 的配乐太小声：{facts}"
