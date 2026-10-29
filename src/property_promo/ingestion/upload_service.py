"""Upload service: validate manifest, store files, verify completeness."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..domain import constants, errors
from ..domain.models import Listing, Photo, UploadState
from ..domain.room_order import sort_room_dirs
from ..jobs.store import Store
from . import media_paths, media_probe, relative_path as rp


class UploadService:
    def __init__(self, store: Store, media_dir: Path, *,
                 max_upload_mb: int = constants.MAX_UPLOAD_MB,
                 max_photos: int = constants.MAX_PHOTOS_PER_LISTING):
        self.store = store
        self.media_dir = media_dir
        self.max_bytes = max_upload_mb * 1024 * 1024
        self.max_photos = max_photos

    # ------------------------------------------------------------ manifest
    def plan_listing(self, root_dir_name: str, manifest: list[dict],
                     batch_id: str | None = None) -> Listing:
        """Validate a client manifest [{path, size}] and create the listing.

        Raises DomainError with a single, most-helpful message when the
        structure violates the contract.
        """
        if not manifest:
            raise errors.no_images()

        paths = [m.get("path", "") for m in manifest]
        sizes = {m.get("path", ""): int(m.get("size", 0)) for m in manifest}

        parsed = rp.parse_manifest(paths)

        # 错误优先级：结构违规 > 不支持的类型 > 无图片 > 数量上限
        if parsed["root_loose"]:
            raise errors.root_loose_images(parsed["root_loose"])
        if parsed["nested"]:
            raise errors.nested_room_dirs(parsed["nested"])
        if parsed["unsupported"]:
            raise errors.unsupported_files(parsed["unsupported"])
        if not parsed["rooms"]:
            raise errors.no_images()

        n_files = sum(len(v) for v in parsed["rooms"].values())
        if n_files > self.max_photos:
            raise errors.too_many_files(self.max_photos)

        for p, sz in sizes.items():
            if p and sz > self.max_bytes:
                raise errors.file_too_large(p.split("/")[-1],
                                            self.max_bytes // (1024 * 1024))

        listing = self.store.create_listing(root_dir_name, root_dir_name,
                                            batch_id=batch_id)
        # 清单键与上传键同为 canonical_key（房间/文件），两侧对齐
        rel_sizes: dict[str, int] = {}
        for p in paths:
            key = rp.canonical_key(p)
            rel_sizes[key] = sizes.get(p, 0)
        self.store.set_manifest(listing.listing_id, rel_sizes)
        self.store.set_state(listing.listing_id, UploadState.CREATED)
        listing.expected_files = rel_sizes
        return listing

    # --------------------------------------------------------------- files
    def _safe_rel(self, rel: str) -> Path:
        """Map a validated 房间/文件 relative path to a safe on-disk path."""
        return Path(*rp.safe_rel(rel).split("/"))

    @staticmethod
    def _safe_name_sub(rel: str) -> Path:
        """Public helper for the media path of a validated relative path."""
        return Path(*rp.safe_rel(rel).split("/"))

    def store_file(self, listing: Listing, rel_path: str,
                   payload: bytes) -> Photo:
        """Persist one uploaded file; idempotent by (room, name, bytes).

        rel_path may arrive as the full webkitRelativePath or the canonical
        房间/文件 key — both map to the same canonical manifest key.
        """
        key = rp.canonical_key(rel_path)
        if key not in listing.expected_files:
            raise errors.DomainError(
                "unexpected_file",
                f"“{rel_path}”不在该房源的上传清单里。请刷新页面后重试。")
        if len(payload) > self.max_bytes:
            raise errors.file_too_large(
                key.split("/")[-1], self.max_bytes // (1024 * 1024))
        expected = listing.expected_files[key]
        if expected and len(payload) != expected:
            raise errors.DomainError(
                "size_mismatch",
                f"“{key}”的大小（{len(payload)} 字节）与选择文件夹时"
                f"（{expected} 字节）不一致，请重新选择该文件所在文件夹续传。")

        dst = media_paths.photo_path(self.media_dir, listing.listing_id, key)
        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists() and dst.stat().st_size == len(payload):
            pass  # 续传幂等：同文件已到，跳过写盘
        else:
            tmp = dst.with_suffix(dst.suffix + ".part")
            tmp.write_bytes(payload)
            tmp.replace(dst)

        try:
            meta = media_probe.probe(dst)
        except Exception as e:  # noqa: BLE001 — undecodable must be explicit
            dst.unlink(missing_ok=True)
            raise errors.DomainError(
                "undecodable_image",
                f"“{key}”无法作为图片打开（{type(e).__name__}）。"
                f"请确认为有效的 JPG/PNG/WebP 文件。") from e

        room_dir, file_name = key.split("/")
        photo = Photo(
            photo_id=hashlib.sha1(
                f"{listing.listing_id}/{key}".encode()).hexdigest()[:12],
            room_dir=room_dir, rel_path=key, file_name=file_name,
            size_bytes=len(payload), width=meta["width"],
            height=meta["height"], flags=meta["flags"])
        self.store.add_photo(photo, listing.listing_id)
        self.store.set_state(listing.listing_id, UploadState.UPLOADING)
        return photo

    # ---------------------------------------------------------- completion
    def complete(self, listing: Listing) -> dict:
        """Verify count + sizes against the manifest; finalize or raise."""
        expected = listing.expected_files
        got: dict[str, int] = {p.rel_path: p.size_bytes
                               for p in listing.all_photos()}
        missing = [rp for rp in expected if rp not in got]
        size_bad = [rp for rp in expected
                    if rp in got and expected[rp] and got[rp] != expected[rp]]
        if missing or size_bad:
            raise errors.manifest_mismatch(missing, size_bad)

        room_dirs = sort_room_dirs([r.room_dir for r in listing.rooms])
        for i, rd in enumerate(room_dirs):
            room = listing.room(rd)
            if room:
                room.order = i
        self.store.set_state(listing.listing_id, UploadState.AWAITING_CONFIRM)
        return {"rooms": len(room_dirs), "photos": listing.total_photos(),
                "bytes": self.store.uploaded_bytes(listing.listing_id)}
