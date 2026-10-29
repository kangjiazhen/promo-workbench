"""FastAPI app assembly for property_promo (upload + workbench)."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..domain import constants, errors
from ..domain.models import UploadState
from ..ingestion import media_paths
from ..ingestion.batch_upload_service import BatchUploadService
from ..ingestion.upload_service import UploadService
from ..jobs.service import RenderService
from ..jobs.store import Store
from ..jobs.worker_pool import WorkerPool
from ..rendering.remotion_renderer import RemotionRenderer
from ..rendering.stub_renderer import StubRenderer
from ..selection.selector import Selector
from ..settings import module_settings
from . import workbench as workbench_api

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.json"
EXAMPLE_CONFIG = Path(__file__).resolve().parents[3] / "config.example.json"


def load_config() -> dict:
    src = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG
    cfg = json.loads(src.read_text(encoding="utf-8"))
    base = src.parent
    cfg["_data_dir"] = (base / cfg.get("data_dir", "./data")).resolve()
    cfg["_renders_dir"] = (base / cfg.get("renders_dir", "./data/renders")).resolve()
    return cfg


CFG = load_config()
CFG["_data_dir"].mkdir(parents=True, exist_ok=True)

store = Store(CFG["_data_dir"] / "property_promo.db")
service = UploadService(
    store, CFG["_data_dir"] / "media",
    max_upload_mb=int(CFG.get("max_upload_mb", 30)),
    max_photos=int(CFG.get("max_photos_per_listing", 300)))
batch_service = BatchUploadService(store)
render_service = RenderService(store, media_dir=CFG["_data_dir"] / "media",
                               renders_dir=CFG["_renders_dir"])
pool = WorkerPool(store, StubRenderer(), media_dir=CFG["_data_dir"] / "media",
                  renders_dir=CFG["_renders_dir"], service=render_service)

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Start the render workers with the server, and stop them with it.

    Without this the queue is decorative: jobs are accepted, stored and
    displayed as 排队渲染, but no worker ever claims one. Stage ①'s tests
    only passed because each test called ``pool.start()`` by hand.

    `current_pool` is looked up per call (late binding), so a test that
    swaps the store gets a pool bound to the new store.
    """
    live = current_pool()
    live.sweep_stale_jobs()   # §5.4: a job left 渲染中 by a crash is failed
    live.start()
    try:
        yield
    finally:
        try:
            live.stop()
        except Exception:  # noqa: BLE001 — never block shutdown
            pass


app = FastAPI(title="property_promo", version="0.2.0", lifespan=_lifespan)


# ------------------------------------------------------------- live runtime
# The workbench talks to the store through these helpers instead of through
# the objects that existed at import time. A test — or a future "reset the
# workspace" action — can then replace `store` and the very next request
# already uses the new one, instead of a stale service pointing at a
# database that is no longer in play.
def _media_dir() -> Path:
    return CFG["_data_dir"] / "media"


def _renders_dir() -> Path:
    return CFG["_renders_dir"]


_render_cache: dict[str, object] = {"store": None, "service": None}
_pool_cache: dict[str, object] = {"store": None, "pool": None}
_batch_cache: dict[str, object] = {"store": None, "service": None}


def current_render_service() -> RenderService:
    """The render service bound to the store that is live *now*."""
    if _render_cache["store"] is not store:
        _render_cache["store"] = store
        _render_cache["service"] = RenderService(
            store, media_dir=_media_dir(), renders_dir=_renders_dir())
    return _render_cache["service"]  # type: ignore[return-value]


def current_pool() -> WorkerPool:
    """The worker pool bound to the store that is live *now*."""
    if _pool_cache["store"] is not store:
        old = _pool_cache["pool"]
        if old is not None:
            try:
                old.stop()
            except Exception:  # noqa: BLE001 — a stale pool must not break us
                pass
        _pool_cache["store"] = store
        _pool_cache["pool"] = WorkerPool(
            store, build_renderer(), media_dir=_media_dir(),
            renders_dir=_renders_dir(), service=current_render_service(),
            concurrency=_wanted_concurrency())
    return _pool_cache["pool"]  # type: ignore[return-value]


def build_renderer():
    """Choose the renderer once per pool.

    `config.json → renderer` selects it: `remotion` (the real film),
    `stub` (fast ffmpeg placeholder) or `auto` (remotion when this machine
    can actually run it, stub otherwise, with the reason logged). Tests
    pin `stub`; production leaves it on `auto`.
    """
    import os

    choice = (os.environ.get("PROPERTY_PROMO_RENDERER")
              or CFG.get("renderer") or "auto").strip().lower()
    if choice == "stub":
        return StubRenderer()
    renderer = RemotionRenderer()
    ok, why = renderer.available()
    if choice == "remotion":
        return renderer
    if ok:
        return renderer
    print(f"[property_promo] Remotion 不可用，改用桩渲染：{why}")
    return StubRenderer()


def _wanted_concurrency() -> int:
    """The pool width the user configured in 全局设置 (§5.3)."""
    try:
        n = module_settings.normalize(store.get_settings()).get(
            "render_concurrency", constants.RENDER_CONCURRENCY_DEFAULT)
        return max(1, min(constants.RENDER_CONCURRENCY_MAX, int(n)))
    except Exception:  # noqa: BLE001 — a broken settings row must not stop the app
        return constants.RENDER_CONCURRENCY_DEFAULT


def current_batch_service() -> BatchUploadService:
    """The batch service bound to the store that is live *now*."""
    if _batch_cache["store"] is not store:
        _batch_cache["store"] = store
        _batch_cache["service"] = BatchUploadService(store)
    return _batch_cache["service"]  # type: ignore[return-value]


@app.exception_handler(errors.DomainError)
async def _domain_error(_req: Request, exc: errors.DomainError):
    """The统一错误信封 (§9): {code, message, listing_id?, rel_path?, hint?}."""
    return JSONResponse(status_code=422, content=exc.to_dict())


class ManifestItem(BaseModel):
    path: str
    size: int


class CreateListing(BaseModel):
    """Create one listing from a manifest.

    The wire contract is `房源根/房间/图片` — exactly three segments —
    because the room name is what makes a photo unique inside a listing.
    `root_dir_name` is the canonical field; `rootDirName` is accepted too
    so the browser client and the API can never disagree about the
    spelling of one field.
    """

    root_dir_name: str | None = None
    rootDirName: str | None = None  # noqa: N815 — wire alias
    manifest: list[ManifestItem]
    batch_id: str | None = None

    def listing_name(self) -> str:
        return (self.root_dir_name or self.rootDirName or "").strip()


@app.post("/api/listings")
async def create_listing(body: CreateListing):
    listing = service.plan_listing(
        body.listing_name(),
        [{"path": m.path, "size": m.size} for m in body.manifest],
        batch_id=getattr(body, "batch_id", None))
    return {
        "listing_id": listing.listing_id,
        "name": listing.name,
        "state": listing.state.value,
        "expected_files": len(listing.expected_files),
        "batch_id": listing.batch_id,
    }


@app.post("/api/batch/uploads")
async def create_batch(body: dict):
    """Declare a batch of listings in one call (§3.1).

    The client has already split a parent folder into per-listing
    manifests; the server re-validates every one of them, so a malformed
    tree is refused before any file is uploaded.
    """
    items = body.get("listings") or []
    normalised = []
    for item in items:
        root = (item.get("root_dir_name") or item.get("rootDirName")
                or "").strip()
        normalised.append({
            "rootDirName": root,
            "manifest": item.get("manifest") or [],
        })
    listings = current_batch_service().create_listings(
        normalised, batch_id=body.get("batch_id"))
    return {
        "batch_id": listings[0].batch_id if listings else None,
        "listings": [
            {"listing_id": li.listing_id, "name": li.name,
             "state": li.state.value, "expected_files": len(li.expected_files),
             "batch_id": li.batch_id}
            for li in listings
        ],
    }


@app.post("/api/batches")
async def open_batch(body: dict | None = None):
    """Open a batch — one upload session (§3.1).

    A batch is organizational: it lets one session's listings be grouped,
    and 「完成上传」closes it so 「继续添加」starts a new one. Listings created
    afterwards with this ``batch_id`` belong to it.
    """
    label = str((body or {}).get("label") or "").strip()
    batch_id = current_batch_service().open_batch(label)
    return {"batch_id": batch_id, "state": "open"}


@app.post("/api/batch/{batch_id}/close")
async def close_batch(batch_id: str):
    """「完成上传」— close the batch so 「继续添加」 starts a new one."""
    current_batch_service().close_batch(batch_id)
    return {"batch_id": batch_id, "state": "closed"}


@app.post("/api/listings/{listing_id}/files")
async def upload_file(listing_id: str, rel_path: str, file: UploadFile):
    listing = store.get_listing(listing_id)
    if not listing:
        raise errors.listing_not_found(listing_id)
    payload = await file.read()
    photo = service.store_file(listing, rel_path, payload)
    return {"photo_id": photo.photo_id, "rel_path": photo.rel_path,
            "room": photo.room_dir, "flags": photo.flags,
            "width": photo.width, "height": photo.height}


@app.post("/api/listings/{listing_id}/upload-complete")
async def upload_complete(listing_id: str):
    listing = store.get_listing(listing_id)
    if not listing:
        raise errors.listing_not_found(listing_id)
    result = service.complete(listing)
    # §6: 批量上传 → 每套自动选片 → 逐套人工确认. Without this call every
    # photo arrives selected (Photo.selected defaults to True), so the
    # user would be asked to confirm 19/19 instead of a recommendation.
    listing = store.get_listing(listing_id) or listing
    try:
        records = Selector(_media_dir()).analyze(listing.all_photos(), listing_id)
        store.apply_auto_selection(
            listing_id, {r["photo_id"]: bool(r["selected"]) for r in records},
            {r["photo_id"]: list(r["flags"]) for r in records})
    except Exception:  # noqa: BLE001 — a bad photo must not break the upload
        pass
    listing = store.get_listing(listing_id) or listing
    return {"listing_id": listing_id, "state": listing.state.value,
            "selected": len(listing.selected_photos()), **result}


@app.get("/api/listings/{listing_id}/photos/{photo_id}")
async def get_photo(listing_id: str, photo_id: str):
    """Serve an uploaded photo by IDs only — never by server path."""
    from fastapi.responses import FileResponse
    listing = store.get_listing(listing_id)
    if not listing:
        raise errors.listing_not_found(listing_id)
    photo = next((p for p in listing.all_photos()
                  if p.photo_id == photo_id), None)
    if not photo:
        raise HTTPException(404, "没有这张照片")
    safe = media_paths.photo_path(CFG["_data_dir"] / "media", listing_id,
                                  photo.rel_path)
    if not safe.is_file():
        raise HTTPException(404, "照片文件缺失，请重新上传该文件")
    return FileResponse(safe)


# The workbench owns the listing list/detail routes now — the payloads
# live in api/payloads.py so the task list and the detail panel can
# never drift apart.
#
# The router receives late-bound handles (lambdas), not the objects
# themselves, so a test — or a future "reset the workspace" action — that
# swaps `app_mod.store` takes effect on the very next request instead of
# only on the next process start.
app.include_router(workbench_api.build_router(
    store=lambda: store,
    service=current_render_service,
    pool=current_pool,
    media_dir=_media_dir,
    renders_dir=_renders_dir))
