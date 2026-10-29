"""Batch upload service (§3).

One batch = one session of added folders. Each added folder is split into
one or more listings (房源), and every listing is created server side with
its own manifest, so the listing contract stays "房源根/房间/图片".

The browser already splits parent folders into per-listing manifests
(client-side, §3.1); this service is the authoritative second gate: it
re-validates every manifest and refuses to create a listing whose
structure violates the contract.
"""
from __future__ import annotations

from ..domain import constants, errors
from ..domain.models import Listing, ListingState
from ..jobs.store import Store
from . import relative_path as rp


class BatchUploadService:
    def __init__(self, store: Store, *, max_listings: int | None = None,
                 max_photos: int | None = None):
        self.store = store
        self.max_listings = max_listings or constants.MAX_BATCH_LISTINGS
        self.max_photos = max_photos or constants.MAX_PHOTOS_PER_LISTING

    # ------------------------------------------------------------- batches
    def open_batch(self, label: str = "") -> str:
        return self.store.create_batch(label)

    def close_batch(self, batch_id: str) -> None:
        self.store.close_batch(batch_id)

    def open_batch_id(self) -> str | None:
        """The most recent still-open batch, if any (§3.1 继续添加)."""
        batches = self.store.open_batches()
        return batches[0]["batch_id"] if batches else None

    # ------------------------------------------------------------ listings
    def create_listings(self, listings: list[dict], *,
                        batch_id: str | None = None) -> list[Listing]:
        """Create several listings in one call.

        `listings` = [{rootDirName, manifest:[{path, size}]}]. The parent
        segment is already stripped by the client; each manifest must be
        `房间/图片` (two segments) or a full `房源/房间/图片` — both map to
        the same canonical key.
        """
        if not listings:
            raise errors.no_listings_found()
        if len(listings) > self.max_listings:
            raise errors.batch_limit_exceeded(self.max_listings)

        bid = batch_id or self.open_batch()
        out: list[Listing] = []
        for item in listings:
            root = (item.get("rootDirName") or "").strip()
            if not root:
                raise errors.DomainError(
                    "missing_root_name",
                    "房源名称不能为空。请重新添加该文件夹。")
            manifest = item.get("manifest") or []
            out.append(self._create_one(bid, root, manifest))
        return out

    def _create_one(self, batch_id: str, root: str,
                    manifest: list[dict]) -> Listing:
        if not manifest:
            raise errors.no_images()

        paths = [m.get("path", "") for m in manifest]
        sizes = {m.get("path", ""): int(m.get("size", 0)) for m in manifest}

        parsed = rp.parse_manifest(paths)
        if parsed["root_loose"]:
            raise errors.root_loose_images(parsed["root_loose"])
        if parsed["nested"]:
            raise errors.nested_room_dirs(parsed["nested"])
        if parsed["unsupported"]:
            raise errors.unsupported_files(parsed["unsupported"])
        if not parsed["rooms"]:
            raise errors.no_images()

        n = sum(len(v) for v in parsed["rooms"].values())
        if n > self.max_photos:
            raise errors.too_many_files(self.max_photos)

        listing = self.store.create_listing(root, root, batch_id=batch_id)
        rel_sizes = {rp.canonical_key(p): sizes.get(p, 0) for p in paths}
        self.store.set_manifest(listing.listing_id, rel_sizes)
        self.store.set_state(listing.listing_id, ListingState.CREATED)
        listing.expected_files = rel_sizes
        listing.batch_id = batch_id
        return listing
