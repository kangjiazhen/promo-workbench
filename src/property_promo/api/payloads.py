"""The JSON shapes the workbench shows (§5).

One module owns every payload so the task list, the detail panel and the
poll endpoint can never drift apart — a field added to the summary is
then automatically present in the detail too.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.models import Listing, ListingState
from ..ingestion import media_paths
from ..jobs.store import Store
from ..storyboard import planner, schema


def estimate_duration(listing: Listing, settings: dict) -> float:
    """The length the film would have, from the planner the render uses.

    §5.5: 片头 4.2s + 每张 2.4~3.2s + 片尾 5.6s.
    """
    try:
        board = planner.build_storyboard(listing, settings)
    except Exception:  # noqa: BLE001 — an estimate must never break the page
        return 0.0
    return round(schema.total_duration(board), 1)


def photo_payload(photo) -> dict[str, Any]:
    return {
        "photo_id": photo.photo_id,
        "file_name": photo.file_name,
        "rel_path": photo.rel_path,
        "room": photo.room_dir,
        "size_bytes": photo.size_bytes,
        "width": photo.width,
        "height": photo.height,
        "selected": photo.selected,
        "manual": photo.manual,
        "manual_order": photo.manual_order,
        "flags": photo.flags,
    }


def listing_summary(listing: Listing, settings: dict) -> dict[str, Any]:
    selected = len(listing.selected_photos())
    return {
        "listing_id": listing.listing_id,
        "name": listing.name,
        "root_dir_name": listing.root_dir_name,
        "state": listing.state.value,
        "state_label": listing.state.label,
        "progress": round(listing.progress, 3),
        "stage": listing.stage,
        "error": listing.error,
        "room_count": len(listing.rooms),
        "photos": listing.total_photos(),
        "selected": selected,
        "expected": len(listing.expected_files),
        "batch_id": listing.batch_id,
        "created_at": listing.created_at,
        "updated_at": listing.updated_at,
        "can_render": listing.state in (
            ListingState.AWAITING_CONFIRM, ListingState.COMPLETE,
            ListingState.FAILED),
        "duration_estimate_s": estimate_duration(listing, settings),
    }


def listing_detail(store: Store, listing: Listing, settings: dict,
                   overrides: dict, media_dir: Path) -> dict[str, Any]:
    out = listing_summary(listing, settings)
    # `rooms` is the list of rooms — the stage-① upload page already reads
    # it that way, and the workbench needs the same rooms with their
    # photos. One payload serves both, so the two views can never disagree.
    rooms = [
        {
            "room_dir": r.room_dir,
            "display_name": r.display_name,
            "order": r.order,
            "photos": [photo_payload(p) for p in r.photos],
        }
        for r in listing.rooms
    ]
    out["rooms"] = rooms
    out["rooms_detail"] = rooms
    out["settings"] = settings
    out["overrides"] = overrides
    out["versions"] = [
        {
            "version_id": v.version_id,
            "job_id": v.job_id,
            "aspect": v.aspect,
            "duration_s": round(v.duration_s, 2),
            "size_bytes": v.size_bytes,
            "created_at": v.created_at,
        }
        for v in store.list_versions(listing.listing_id)
    ]
    music = store.get_listing_music(listing.listing_id)
    out["music_file"] = (
        {"file_name": music["file_name"], "size_bytes": music["size_bytes"]}
        if music else None)
    missing = store.find_missing_media(listing.listing_id, media_dir)
    out["missing_media"] = missing
    out["media_warning"] = (
        f"有 {len(missing)} 张照片在磁盘上找不到，请重新上传这些文件。"
        if missing else "")
    return out


def job_payload(store: Store, job) -> dict[str, Any]:
    listing = store.get_listing(job.listing_id)
    return {
        "job_id": job.job_id,
        "listing_id": job.listing_id,
        "listing_name": listing.name if listing else "",
        "state": job.state.value,
        "progress": round(job.progress, 3),
        "stage": job.stage.value if job.stage else None,
        "error": job.error,
        "aspect": job.aspect,
        "batch_id": job.batch_id,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


__all__ = ["estimate_duration", "job_payload", "listing_detail",
           "listing_summary", "media_paths", "photo_payload"]
