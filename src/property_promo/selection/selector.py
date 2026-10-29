"""Automatic photo selection.

The selector never hides a photo without telling the user why (§5.2): it
marks 模糊 / 过暗 / 重复 with plain language and keeps the rest selected.
Manual choices made by the user are never overwritten (§5.2 "人工调整不会
被自动推荐覆盖").
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..domain.models import Photo
from ..ingestion import media_paths
from . import duplicates, measure

#: 同一房间最多自动推荐几张（§5.5：14 张 ≈ 50s）。
#:
#: 一个房间拍 6 个角度时，把它们全塞进成片只会让观众在同一间屋子里
#: 走不动。上限只影响「自动推荐」，用户想全留就手动勾回来——手动选择
#: 永远优先（§5.2）。
PER_ROOM_CAP = 3

#: 被同一房间上限挤掉的照片，用这个可读标记（不显示内部分数）。
CAPPED_FLAG = "同房间多拍"


class Selector:
    """Selects photos for a listing from measured evidence only."""

    def __init__(self, media_dir: Path, *, per_room_cap: int = PER_ROOM_CAP):
        self.media_dir = media_dir
        self.per_room_cap = per_room_cap

    @classmethod
    def per_room_cap_default(cls) -> int:
        """The shipped default, for UI text and tests to quote."""
        return PER_ROOM_CAP

    def _abs(self, photo: Photo, listing_id: str) -> Path:
        # One resolver for every component: the uploader, the API that
        # serves a thumbnail, the selector and the renderer must agree on
        # where a photo lives.
        return media_paths.photo_path(self.media_dir, listing_id, photo.rel_path)

    def analyze(self, photos: Iterable[Photo],
                listing_id: str = "") -> list[dict]:
        """Measure every photo; returns one record per photo."""
        photos = list(photos)
        paths = [self._abs(p, listing_id) for p in photos]
        existing = [p for p in paths if p.is_file()]

        stats: list[dict | None] = [None] * len(photos)
        for i, path in enumerate(paths):
            if not path.is_file():
                continue
            try:
                stats[i] = measure.measure(path)
            except Exception:  # noqa: BLE001 — undecodable photos get flagged
                stats[i] = None

        # Near-duplicate groups over the readable subset.
        readable_idx = [i for i, s in enumerate(stats) if s is not None]
        dup_groups: dict[int, list[int]] = {}
        if len(readable_idx) > 1:
            try:
                dup_groups = duplicates.find_duplicates(
                    [paths[i] for i in readable_idx])
            except Exception:  # noqa: BLE001 — duplicates are advisory only
                dup_groups = {}

        dup_of: dict[int, int] = {}
        for group in dup_groups.values():
            # `find_duplicates` numbers its members inside the *readable
            # subset*; map them back to positions in `photos` before using
            # them as keys, or one undecodable photo shifts every mark.
            keeper = readable_idx[group[0]]
            for member in group[1:]:
                dup_of[readable_idx[member]] = keeper

        out: list[dict] = []
        for i, p in enumerate(photos):
            s = stats[i]
            flags: list[str] = []
            if s is None:
                flags.append("无法读取")
            else:
                flags.extend(measure.user_flags(s))
            if i in dup_of:
                flags.append("重复")
            out.append({
                "photo_id": p.photo_id,
                "room": p.room_dir,
                "file_name": p.file_name,
                "selected": "重复" not in flags and "无法读取" not in flags,
                "flags": flags,
                "score": measure.quality(s) if s else None,
            })
        self._apply_per_room_cap(out)
        return out

    def _apply_per_room_cap(self, records: list[dict]) -> None:
        """Keep at most `per_room_cap` recommendations per room, best first.

        Order of preference: measurable quality, then file name (capture
        order). Photos dropped here stay available — they are only
        *unrecommended*, and the reason is written in plain language so the
        user can put them back with one click.
        """
        if self.per_room_cap <= 0:
            return
        by_room: dict[str, list[dict]] = {}
        for rec in records:
            if not rec["selected"]:
                continue          # already excluded for its own reason
            by_room.setdefault(rec["room"], []).append(rec)
        for room_records in by_room.values():
            if len(room_records) <= self.per_room_cap:
                continue
            ranked = sorted(
                room_records,
                key=lambda r: (-(r["score"] or 0.0), str(r["file_name"])))
            for rec in ranked[self.per_room_cap:]:
                rec["selected"] = False
                rec["flags"] = [*rec["flags"], CAPPED_FLAG]
