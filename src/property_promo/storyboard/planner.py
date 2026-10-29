"""Storyboard planning: photos + settings → frozen render contract.

The planner is intentionally deterministic: same listing + same selection +
same settings ⇒ same storyboard. It never fabricates property facts and
never carries an address (§4).
"""
from __future__ import annotations

import time
from typing import Any

from ..domain.models import Listing, Photo
from ..domain.room_order import natural_key
from ..ingestion import media_paths
from ..settings.module_settings import (ASPECTS, DEFAULT_OUTRO_LINE,
                                        DEFAULT_ROOM_LABELS, DEFAULT_TITLE)
from . import schema


#: Camera-move vocabulary (ported concept from the verified old stack:
#: pull / push / truck, each with a zoom budget so the softness cost is
#: stated rather than hidden).
MOVE_KINDS = ("pull", "push", "truck")
_TRAVEL_DIRS = ((1.0, 0.35), (-1.0, -0.35), (-0.55, 1.0), (0.55, -1.0))

DEFAULT_MOTION = {
    "enabled": True,
    "pattern": "auto",
    "kinds": list(MOVE_KINDS),
    "zoom": 0.26,
    "drift": 0.55,
    "max_upscale": 2.6,
    "ease": "lin",
}


def plan_move(index: int, photo: Photo, canvas_w: int, canvas_h: int,
              motion: dict | None = None) -> dict[str, Any]:
    """Choose a camera move for one photo and report its on-screen scale.

    Movement is *created* by a zoom and budgeted explicitly: `max_upscale`
    caps how far the picture may be stretched beyond its own resolution, so
    the cost of the move is stated in the board rather than hidden.
    """
    mcfg = {**DEFAULT_MOTION, **(motion or {})}
    shot_w = photo.width or 1
    shot_h = photo.height or 1
    cover = max(canvas_w / shot_w, canvas_h / shot_h)
    zoom = float(mcfg.get("zoom", 0.26))
    drift = float(mcfg.get("drift", 0.55))
    max_up = float(mcfg.get("max_upscale", 2.6))
    ease = str(mcfg.get("ease", "lin"))

    room = max_up / cover - 1.0 if cover > 0 else 0.0
    z = max(0.0, min(zoom, room))

    if not mcfg.get("enabled", True) or z < 0.02:
        # Explicitly static — never fall through to a legacy pan path.
        return {"z0": 1.0, "z1": 1.0, "x0": 0.0, "x1": 0.0,
                "y0": 0.0, "y1": 0.0, "ease": "lin", "kind": "static"}

    surplus_x = shot_w * cover - canvas_w
    surplus_y = shot_h * cover - canvas_h
    big_x = surplus_x >= canvas_w * 0.20
    big_y = surplus_y >= canvas_h * 0.28

    kinds = list(mcfg.get("kinds") or MOVE_KINDS)
    pattern = str(mcfg.get("pattern", "auto")).lower()
    if pattern in ("push", "pull", "truck"):
        kind = pattern
    else:
        kind = "pull" if index == 0 else str(kinds[(index - 1) % len(kinds)])

    dx, dy = _TRAVEL_DIRS[index % len(_TRAVEL_DIRS)]

    if big_x and pattern not in ("push", "pull"):
        sx = 1.0 if index % 2 == 0 else -1.0
        return {"z0": 1.0, "z1": 1.0, "x0": -sx, "x1": sx,
                "y0": 0.0, "y1": 0.0, "ease": "lin", "kind": "truck-native"}
    if big_y and not big_x and pattern not in ("push", "pull"):
        sy = 1.0 if index % 2 == 0 else -1.0
        return {"z0": 1.0, "z1": 1.0, "x0": 0.0, "x1": 0.0,
                "y0": -sy, "y1": sy, "ease": "lin", "kind": "tilt-native"}

    if kind == "truck":
        zz = z * 0.9
        sx = 1.0 if dx >= 0 else -1.0
        return {"z0": 1.0 + zz, "z1": 1.0 + zz,
                "x0": -sx * 0.95, "x1": sx * 0.95,
                "y0": 0.0, "y1": 0.0, "ease": "lin", "kind": "truck"}
    if kind == "pull":
        return {"z0": 1.0 + z, "z1": 1.0 + z * 0.35,
                "x0": dx * drift, "x1": -dx * drift * 0.6,
                "y0": dy * drift, "y1": -dy * drift * 0.6,
                "ease": "lin", "kind": "pull"}
    return {"z0": 1.0 + z * 0.14, "z1": 1.0 + z,
            "x0": -dx * drift, "x1": dx * drift,
            "y0": -dy * drift, "y1": dy * drift,
            "ease": ease, "kind": "push"}


def selected_photos_in_order(listing: Listing) -> list[Photo]:
    """Selected photos, ordered by room tour order then manual order/name.

    The room order is the one the user sees and can drag (§5.2). The store
    already resolves it (explicit `order_index`, else natural viewing order)
    into `Room.order`, so the storyboard must honour that and not re-sort by
    name — otherwise dragging a room would silently do nothing to the film.
    """
    rooms = sorted(listing.rooms, key=lambda r: (r.order, natural_key(r.room_dir)))
    out: list[Photo] = []
    for room in rooms:
        photos = [p for p in room.photos if p.selected]
        photos.sort(key=lambda p: (p.manual_order is None,
                                   p.manual_order if p.manual_order is not None
                                   else 0,
                                   p.file_name))
        out.extend(photos)
    return out


def build_storyboard(listing: Listing, settings: dict,
                     *, motion: dict | None = None,
                     fps: int = 30) -> dict[str, Any]:
    """Freeze a listing + settings into a Storyboard (§6)."""
    aspect = settings.get("aspect", "16:9")
    if aspect not in ASPECTS:
        aspect = "16:9"
    canvas = ASPECTS[aspect]
    cw, ch = int(canvas["width"]), int(canvas["height"])

    photos = selected_photos_in_order(listing)
    # Room name shown on screen: the user's editable display name if they
    # renamed the folder, else the folder name itself (the uploader's whole
    # premise is 「文件夹名就是房间名」). Frozen here so the renderer never
    # looks anything up — and so a later rename cannot change a finished
    # film's contract.
    room_names = {r.room_dir: ((r.display_name or "").strip() or r.room_dir)
                  for r in listing.rooms}
    show_room_labels = bool(settings.get("room_labels", DEFAULT_ROOM_LABELS))

    # The opener must NEVER carry the listing name: the name defaults to the
    # upload folder name, which is usually the address (§4 forbids an address
    # on screen). It uses the user-editable 片头标题 instead, defaulting to
    # the same generic line the verified old film printed.
    title = (settings.get("title") or "").strip() or DEFAULT_TITLE
    shots: list[dict[str, Any]] = [{
        "index": 0,
        "kind": "opener",
        "duration_s": schema.OPENER_SECONDS,
        "title": title,
        "aspect": aspect,
        "move": {"z0": 1.0, "z1": 1.06, "x0": 0.0, "x1": 0.0,
                 "y0": 0.0, "y1": 0.0, "ease": "lin", "kind": "push"},
    }]
    for i, p in enumerate(photos):
        # Shot length varies slightly with the move so a slow pull gets more
        # time than a punchy push (§5.5 range 2.4~3.2s).
        move = plan_move(i, p, cw, ch, motion)
        duration = schema.SHOT_MIN_SECONDS + 0.2 * (i % 5)
        if move["kind"] in ("pull", "truck-native", "tilt-native"):
            duration = min(schema.SHOT_MAX_SECONDS, duration + 0.4)
        # Every photo carries its room's name (user decision, 2026-10-29):
        # the label is how a client keeps track of which room they are looking
        # at, so it must not disappear on the second photo of a room.
        label = room_names.get(p.room_dir)
        shots.append({
            "index": i + 1,
            "kind": "shot",
            "photo_id": p.photo_id,
            "room": p.room_dir,
            "room_label": (label or None) if show_room_labels else None,
            "file_name": p.file_name,
            "rel_path": p.rel_path,
            # The renderer resolves photos against the listing's own media
            # folder, so the board carries a *media-relative* path — never
            # an absolute one, and never the sanitised name (§4).
            "media_path": media_paths.media_rel(p.rel_path),
            "width": p.width,
            "height": p.height,
            "duration_s": round(duration, 2),
            "move": move,
        })
    # An end card with no contact at all collapses to a lone heading. Fill in
    # one neutral line so the film always ends on a readable frame; the board
    # states it, so the UI and the tests can see exactly what will appear.
    contact = dict(settings.get("contact") or {})
    if not any((contact.get(k) or "").strip()
               for k in ("name", "phone", "wechat", "agency", "closing")):
        contact["closing"] = DEFAULT_OUTRO_LINE

    shots.append({
        "index": len(shots),
        "kind": "outro",
        "duration_s": schema.OUTRO_SECONDS,
        "contact": contact,
        "move": {"z0": 1.0, "z1": 1.0, "x0": 0.0, "x1": 0.0,
                 "y0": 0.0, "y1": 0.0, "ease": "lin", "kind": "static"},
    })

    board = {
        "version": schema.STORYBOARD_VERSION,
        "listing_id": listing.listing_id,
        "name": listing.name,
        "aspect": aspect,
        "canvas": {"width": cw, "height": ch},
        "fps": fps,
        "created_at": time.time(),
        "contact": contact,
        "music": dict(settings.get("music") or {"mode": "synth",
                                                 "track": "warm"}),
        "shots": shots,
        "audio": {"narration": [], "bed": None},
    }
    music = board["music"]
    if music.get("mode") == "synth":
        board["audio"]["bed"] = f"synth:{music.get('track', 'warm')}"
    return board
