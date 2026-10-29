"""Store-level contract tests: schema migration and media reconciliation.

The migration test exists because of a real failure: the dev database was
created before the `listings.progress` column existed, and
`CREATE TABLE IF NOT EXISTS` never adds a column — so every query against
the new column failed with "no such column".

The reconciliation test exists because of the photo-collision bug: a
listing claimed 17 photos while only 6 files existed on disk.
"""
from pathlib import Path

import sqlite3

from property_promo.domain.models import ListingState
from property_promo.jobs.store import Store


def _old_schema_db(path: Path) -> None:
    """A database in the *first release* shape: no progress column."""
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE listings (
            listing_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            root_dir_name TEXT NOT NULL,
            state TEXT NOT NULL,
            settings TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE photos (
            photo_id TEXT PRIMARY KEY,
            listing_id TEXT NOT NULL,
            room_dir TEXT NOT NULL,
            file_name TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            flags TEXT NOT NULL DEFAULT '[]',
            selected INTEGER DEFAULT 1,
            manual_order INTEGER
        );
        """)
    db.commit()
    db.close()


def test_migration_adds_missing_columns(tmp_path: Path):
    db_path = tmp_path / "old.db"
    _old_schema_db(db_path)

    store = Store(db_path)  # opening runs the migration
    cols = {r["name"] for r in store._db.execute(  # noqa: SLF001
        "PRAGMA table_info(listings)")}
    assert {"progress", "stage", "error", "batch_id",
            "created_at", "updated_at"} <= cols
    photo_cols = {r["name"] for r in store._db.execute(  # noqa: SLF001
        "PRAGMA table_info(photos)")}
    assert "manual" in photo_cols
    store.close()


def test_migration_keeps_existing_rows(tmp_path: Path):
    db_path = tmp_path / "old.db"
    _old_schema_db(db_path)
    db = sqlite3.connect(db_path)
    db.execute(
        "INSERT INTO listings (listing_id, name, root_dir_name, state,"
        " settings) VALUES ('L1', '旧的房源', 'R', 'complete', '{}')")
    db.commit()
    db.close()

    store = Store(db_path)
    listing = store.get_listing("L1")
    assert listing is not None
    assert listing.name == "旧的房源"
    assert listing.progress == 0.0
    store.close()


def test_migration_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "old.db"
    _old_schema_db(db_path)
    Store(db_path).close()
    store = Store(db_path)  # second open must not fail
    store.close()


# --------------------------------------------------------------- reconcile
def test_missing_media_is_detected(tmp_path: Path):
    """A listing whose files vanished must be reported, not silently kept."""
    from property_promo.domain.models import Photo

    store = Store(tmp_path / "db.sqlite")
    listing = store.create_listing("测试", "R")
    store.add_photo(Photo(
        photo_id="p1", room_dir="客厅", rel_path="客厅/01.jpg",
        file_name="01.jpg", size_bytes=10, width=100, height=100),
        listing.listing_id)
    # No file is written on purpose — this is the corrupted case.
    missing = store.find_missing_media(listing.listing_id, tmp_path / "media")
    assert len(missing) == 1
    assert missing[0] == "客厅/01.jpg"
    store.close()


def test_present_media_is_not_reported(tmp_path: Path):
    from property_promo.domain.models import Photo
    from property_promo.ingestion import media_paths

    store = Store(tmp_path / "db.sqlite")
    listing = store.create_listing("测试", "R")
    store.add_photo(Photo(
        photo_id="p1", room_dir="客厅", rel_path="客厅/01.jpg",
        file_name="01.jpg", size_bytes=10, width=100, height=100),
        listing.listing_id)
    p = media_paths.photo_path(tmp_path / "media", listing.listing_id,
                               "客厅/01.jpg")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    assert store.find_missing_media(listing.listing_id, tmp_path / "media") == []
    store.close()
