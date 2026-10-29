"""SQLite persistence for the batch workbench.

Concurrency contract (§12.3):
  * WAL journal mode — readers never block the writer pool.
  * busy_timeout = 5000 ms — parallel uploads + progress writes + UI
    polling must not raise "database is locked".
  * Short transactions only; progress writes are throttled by the caller.

Tables
  listings        — one row per property listing (the task list)
  photos          — uploaded photos
  manifests       — declared (rel_path, size) per listing
  render_jobs     — the render queue (§5.4)
  video_versions  — finished MP4 versions, newest first (§5.4)
  batches         — upload batches (§3.1)
  global_settings — key/value runtime settings (§5.3)
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..domain import constants
from ..domain.models import (JobState, Listing, ListingState, Photo, RenderJob,
                             RenderStage, Room, VideoVersion)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_dir_name TEXT NOT NULL,
    state TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}',
    batch_id TEXT,
    error TEXT,
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS photos (
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
    manual_order INTEGER,
    manual INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS manifests (
    listing_id TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    PRIMARY KEY (listing_id, rel_path)
);
CREATE TABLE IF NOT EXISTS rooms (
    listing_id TEXT NOT NULL,
    room_dir TEXT NOT NULL,
    display_name TEXT NOT NULL,
    order_index INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (listing_id, room_dir)
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS render_jobs (
    job_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL,
    state TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'preparing',
    error TEXT,
    aspect TEXT NOT NULL DEFAULT '16:9',
    storyboard TEXT NOT NULL DEFAULT '{}',
    lease_expires_at REAL NOT NULL DEFAULT 0,
    worker_id TEXT,
    batch_id TEXT,
    created_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    started_at REAL NOT NULL DEFAULT 0,
    finished_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS video_versions (
    version_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    aspect TEXT NOT NULL DEFAULT '16:9',
    duration_s REAL NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS listing_music (
    listing_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS global_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_photos_listing ON photos(listing_id);
CREATE INDEX IF NOT EXISTS idx_jobs_listing ON render_jobs(listing_id);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON render_jobs(state);
CREATE INDEX IF NOT EXISTS idx_versions_listing ON video_versions(listing_id);
"""

#: Columns added after the first release. `CREATE TABLE IF NOT EXISTS`
#: never adds a column to an existing table, so an older database would
#: keep the old shape and every new query would fail with "no such
#: column". The migration below adds exactly these.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "listings": [
        ("batch_id", "TEXT"),
        ("error", "TEXT"),
        ("progress", "REAL NOT NULL DEFAULT 0"),
        ("stage", "TEXT"),
        ("created_at", "REAL NOT NULL DEFAULT 0"),
        ("updated_at", "REAL NOT NULL DEFAULT 0"),
    ],
    "photos": [
        ("manual", "INTEGER DEFAULT 0"),
    ],
    "render_jobs": [
        ("batch_id", "TEXT"),
    ],
}


class Store:
    """Thread-safe SQLite store.

    One connection guarded by a re-entrant lock: the whole module is a
    single-user local app, and short transactions under one lock are
    simpler and safer than sharing a connection across threads.
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_path, check_same_thread=False,
                                   timeout=5.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.execute("PRAGMA synchronous=NORMAL")
        with self._db:
            self._db.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Add columns that an older database file does not have yet.

        `CREATE TABLE IF NOT EXISTS` leaves an existing table untouched, so
        a database created before a column existed keeps the old shape and
        every query against the new column fails. This brings the file up
        to the current schema without touching the data.
        """
        for table, columns in _ADDED_COLUMNS.items():
            existing = {r["name"] for r in self._db.execute(
                f"PRAGMA table_info({table})")}
            for name, decl in columns:
                if name in existing:
                    continue
                self._db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # ------------------------------------------------------------- helpers
    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------ listings
    def create_listing(self, name: str, root_dir_name: str,
                       batch_id: str | None = None) -> Listing:
        lid = self.new_id()
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO listings (listing_id, name, root_dir_name, state,"
                " batch_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (lid, name, root_dir_name, ListingState.CREATED.value,
                 batch_id, now, now))
        return Listing(listing_id=lid, name=name, root_dir_name=root_dir_name,
                       batch_id=batch_id, created_at=now, updated_at=now)

    def get_listing(self, listing_id: str) -> Listing | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM listings WHERE listing_id = ?",
                (listing_id,)).fetchone()
            if not row:
                return None
            listing = Listing(
                listing_id=row["listing_id"], name=row["name"],
                root_dir_name=row["root_dir_name"],
                state=ListingState(row["state"]),
                settings=json.loads(row["settings"]),
                batch_id=row["batch_id"], error=row["error"],
                progress=float(row["progress"]), stage=row["stage"],
                created_at=float(row["created_at"]),
                updated_at=float(row["updated_at"]))
            listing.rooms = self._load_rooms(listing_id)
            listing.expected_files = {
                r["rel_path"]: r["size_bytes"]
                for r in self._db.execute(
                    "SELECT rel_path, size_bytes FROM manifests WHERE listing_id = ?",
                    (listing_id,))}
            return listing

    def _load_rooms(self, listing_id: str) -> list[Room]:
        from ..domain.room_order import natural_key
        rows = self._db.execute(
            "SELECT * FROM photos WHERE listing_id = ?", (listing_id,)).fetchall()
        rooms: dict[str, Room] = {}
        for r in rows:
            room = rooms.setdefault(r["room_dir"], Room(
                room_dir=r["room_dir"], display_name=r["room_dir"], order=0))
            room.photos.append(_photo_from_row(r))
        # Display names and room order are user-editable (§5.2); the
        # photo rows stay the source of truth for membership.
        meta = {r["room_dir"]: r for r in self._db.execute(
            "SELECT room_dir, display_name, order_index FROM rooms"
            " WHERE listing_id = ?", (listing_id,)).fetchall()}
        natural = {name: i for i, name in
                   enumerate(sorted(rooms, key=natural_key))}
        # A room the user never touched keeps its natural viewing position
        # instead of being pushed behind the reordered ones — otherwise
        # renaming one room would silently move it to the front of the film.
        ordered = sorted(rooms.values(), key=lambda rm: (
            meta[rm.room_dir]["order_index"] if rm.room_dir in meta
            else natural[rm.room_dir],
            natural_key(rm.room_dir)))
        for i, rm in enumerate(ordered):
            rm.order = i
            if rm.room_dir in meta:
                rm.display_name = meta[rm.room_dir]["display_name"]
        return ordered

    # ------------------------------------------------------------- naming
    def rename_listing(self, listing_id: str, name: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE listings SET name = ?, updated_at = ?"
                " WHERE listing_id = ?",
                (name, time.time(), listing_id))

    def set_room_meta(self, listing_id: str, room_dir: str, *,
                      display_name: str | None = None,
                      order_index: int | None = None) -> None:
        """Update one room's display name and/or position (§5.2)."""
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT display_name, order_index FROM rooms"
                " WHERE listing_id = ? AND room_dir = ?",
                (listing_id, room_dir)).fetchone()
            if row is None:
                self._db.execute(
                    "INSERT INTO rooms (listing_id, room_dir, display_name,"
                    " order_index) VALUES (?, ?, ?, ?)",
                    (listing_id, room_dir, display_name or room_dir,
                     order_index if order_index is not None else 0))
                return
            self._db.execute(
                "UPDATE rooms SET display_name = ?, order_index = ?"
                " WHERE listing_id = ? AND room_dir = ?",
                (display_name if display_name is not None else row["display_name"],
                 order_index if order_index is not None else row["order_index"],
                 listing_id, room_dir))

    def list_rooms(self, listing_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT room_dir, display_name, order_index FROM rooms"
                " WHERE listing_id = ? ORDER BY order_index, rowid",
                (listing_id,)).fetchall()
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- photos
    def list_photos(self, listing_id: str) -> list[Photo]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM photos WHERE listing_id = ?"
                " ORDER BY room_dir, file_name", (listing_id,)).fetchall()
        return [_photo_from_row(r) for r in rows]

    def get_photo(self, listing_id: str, photo_id: str) -> Photo | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM photos WHERE listing_id = ? AND photo_id = ?",
                (listing_id, photo_id)).fetchone()
        return _photo_from_row(row) if row else None

    def find_photo(self, photo_id: str) -> tuple[str, Photo] | None:
        """Find a photo by id alone and report which listing owns it.

        The thumbnail route addresses photos by id only, so the server has
        to resolve the owning listing itself — the client never sends a
        path.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM photos WHERE photo_id = ?", (photo_id,)).fetchone()
            if not row:
                return None
            return row["listing_id"], _photo_from_row(row)

    # ---------------------------------------------------------------- music
    def set_listing_music(self, listing_id: str, file_path: str,
                          file_name: str, size_bytes: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO listing_music (listing_id, file_path, file_name,"
                " size_bytes, created_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(listing_id) DO UPDATE SET"
                " file_path = excluded.file_path,"
                " file_name = excluded.file_name,"
                " size_bytes = excluded.size_bytes",
                (listing_id, file_path, file_name, size_bytes, time.time()))

    def get_listing_music(self, listing_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM listing_music WHERE listing_id = ?",
                (listing_id,)).fetchone()
        return dict(row) if row else None

    def clear_listing_music(self, listing_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM listing_music WHERE listing_id = ?",
                (listing_id,))

    def set_state(self, listing_id: str, state: ListingState) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE listings SET state = ?, updated_at = ?"
                " WHERE listing_id = ?",
                (state.value, time.time(), listing_id))

    def set_state_with_error(self, listing_id: str, state: ListingState,
                             error: str | None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE listings SET state = ?, error = ?, updated_at = ?"
                " WHERE listing_id = ?",
                (state.value, error, time.time(), listing_id))

    def set_settings(self, listing_id: str, settings: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE listings SET settings = ?, updated_at = ?"
                " WHERE listing_id = ?",
                (json.dumps(settings, ensure_ascii=False), time.time(),
                 listing_id))

    def set_progress(self, listing_id: str, progress: float,
                     stage: str | None = None) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE listings SET progress = ?, stage = ?, updated_at = ?"
                " WHERE listing_id = ?",
                (float(progress), stage, time.time(), listing_id))

    def list_listings(self, limit: int = 200) -> list[Listing]:
        with self._lock:
            rows = self._db.execute(
                "SELECT listing_id FROM listings"
                " ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            li = self.get_listing(r["listing_id"])
            if li:
                out.append(li)
        return out

    def list_listings_by_state(self, states: list[ListingState],
                                limit: int = 500) -> list[Listing]:
        """Task list filtered by state (§5.1)."""
        if not states:
            return self.list_listings(limit)
        marks = ",".join("?" for _ in states)
        with self._lock:
            rows = self._db.execute(
                f"SELECT listing_id FROM listings WHERE state IN ({marks})"
                " ORDER BY rowid DESC LIMIT ?",
                (*[s.value for s in states], limit)).fetchall()
        out = []
        for r in rows:
            li = self.get_listing(r["listing_id"])
            if li:
                out.append(li)
        return out

    def count_listings_by_state(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT state, COUNT(*) AS c FROM listings"
                " GROUP BY state").fetchall()
        return {r["state"]: int(r["c"]) for r in rows}

    # ------------------------------------------------------------ manifest
    def set_manifest(self, listing_id: str, files: dict[str, int]) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM manifests WHERE listing_id = ?",
                             (listing_id,))
            self._db.executemany(
                "INSERT INTO manifests (listing_id, rel_path, size_bytes)"
                " VALUES (?, ?, ?)",
                [(listing_id, rp, sz) for rp, sz in files.items()])

    def add_photo(self, photo: Photo, listing_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO photos"
                " (photo_id, listing_id, room_dir, file_name, rel_path,"
                "  size_bytes, width, height, flags, selected, manual_order,"
                "  manual)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (photo.photo_id, listing_id, photo.room_dir,
                 photo.file_name, photo.rel_path, photo.size_bytes,
                 photo.width, photo.height,
                 json.dumps(photo.flags, ensure_ascii=False),
                 int(photo.selected), photo.manual_order,
                 int(photo.manual)))

    def set_photo_selection(self, listing_id: str, selections: dict[str, bool],
                             orders: dict[str, int] | None = None) -> None:
        """Apply a manual selection / ordering patch (§5.2).

        Photos touched here are marked `manual = 1` so the auto-selector
        never overrides a human decision.
        """
        with self._lock, self._db:
            for pid, sel in selections.items():
                self._db.execute(
                    "UPDATE photos SET selected = ?, manual = 1"
                    " WHERE photo_id = ? AND listing_id = ?",
                    (int(sel), pid, listing_id))
            for pid, order in (orders or {}).items():
                self._db.execute(
                    "UPDATE photos SET manual_order = ?, manual = 1"
                    " WHERE photo_id = ? AND listing_id = ?",
                    (order, pid, listing_id))

    def apply_auto_selection(self, listing_id: str,
                             selections: dict[str, bool],
                             flags: dict[str, list[str]] | None = None) -> int:
        """Apply an automatic recommendation without touching manual edits.

        Returns the number of photos actually changed. Photos the user
        already decided on are left alone (§5.2 "人工调整不会被自动推荐覆盖").

        The reason markers are written together with the decision: a photo
        that is not recommended without a reason on screen is exactly the
        behaviour §5.2 forbids, and the reason has to survive a page reload.
        """
        changed = 0
        with self._lock, self._db:
            for pid, sel in selections.items():
                cur = self._db.execute(
                    "SELECT selected, manual FROM photos"
                    " WHERE photo_id = ? AND listing_id = ?",
                    (pid, listing_id)).fetchone()
                if cur is None:
                    continue
                marks = json.dumps((flags or {}).get(pid, []),
                                   ensure_ascii=False)
                self._db.execute(
                    "UPDATE photos SET flags = ?"
                    " WHERE photo_id = ? AND listing_id = ?",
                    (marks, pid, listing_id))
                if cur["manual"]:
                    continue
                if bool(cur["selected"]) != bool(sel):
                    self._db.execute(
                        "UPDATE photos SET selected = ?"
                        " WHERE photo_id = ? AND listing_id = ?",
                        (int(sel), pid, listing_id))
                    changed += 1
        return changed

    def uploaded_bytes(self, listing_id: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) AS s FROM photos"
                " WHERE listing_id = ?", (listing_id,)).fetchone()
        return int(row["s"])

    def find_missing_media(self, listing_id: str, media_dir: Path) -> list[str]:
        """The photos of a listing whose file is not on disk.

        The upload contract says every declared photo must have a file.
        When they do not (an interrupted upload, a hand-edited media
        folder, a bug like the room-name collision), the listing must say
        so instead of pretending it is complete.
        """
        from ..ingestion import media_paths

        missing: list[str] = []
        for photo in self.list_photos(listing_id):
            path = media_paths.photo_path(media_dir, listing_id,
                                          photo.rel_path)
            if not path.is_file():
                missing.append(photo.rel_path)
        return missing

    # ------------------------------------------------------------- batches
    def create_batch(self, label: str = "") -> str:
        bid = self.new_id()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO batches (batch_id, label, created_at)"
                " VALUES (?, ?, ?)", (bid, label, time.time()))
        return bid

    def close_batch(self, batch_id: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE batches SET state = 'closed' WHERE batch_id = ?",
                (batch_id,))

    def open_batches(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM batches WHERE state = 'open' ORDER BY rowid DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_batches(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM batches ORDER BY rowid DESC").fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------- render jobs
    def create_job(self, job_id: str, listing_id: str, aspect: str,
                   storyboard: dict, batch_id: str | None = None) -> RenderJob:
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO render_jobs (job_id, listing_id, state, aspect,"
                " storyboard, batch_id, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, listing_id, JobState.QUEUED.value, aspect,
                 json.dumps(storyboard, ensure_ascii=False), batch_id, now, now))
        return RenderJob(job_id=job_id, listing_id=listing_id,
                         state=JobState.QUEUED, aspect=aspect,
                         storyboard=storyboard, batch_id=batch_id,
                         created_at=now, updated_at=now)

    def get_job(self, job_id: str) -> RenderJob | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM render_jobs WHERE job_id = ?",
                (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> RenderJob:
        return RenderJob(
            job_id=row["job_id"], listing_id=row["listing_id"],
            state=JobState(row["state"]), progress=float(row["progress"]),
            stage=RenderStage(row["stage"]), error=row["error"],
            aspect=row["aspect"],
            storyboard=json.loads(row["storyboard"]),
            lease_expires_at=float(row["lease_expires_at"]),
            worker_id=row["worker_id"], batch_id=row["batch_id"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            started_at=float(row["started_at"]),
            finished_at=float(row["finished_at"]))

    def list_jobs(self, limit: int = 200) -> list[RenderJob]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM render_jobs ORDER BY rowid DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._job_from_row(r) for r in rows]

    def list_live_jobs(self) -> list[RenderJob]:
        """Jobs the pool still owes the user: queued first, then rendering.

        Finished jobs (complete / failed / cancelled) are deliberately
        excluded: their outcome lives on the listing (成片版本 / 失败原因),
        and keeping them here would leave the queue panel showing yesterday's
        work forever (§5.4 — the queue is a live view, not a history log).
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM render_jobs WHERE state IN (?, ?)"
                " ORDER BY COALESCE(started_at, created_at) ASC, rowid ASC",
                (JobState.QUEUED.value, JobState.RENDERING.value)).fetchall()
        return [self._job_from_row(r) for r in rows]

    def list_jobs_by_state(self, state: JobState) -> list[RenderJob]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM render_jobs WHERE state = ? ORDER BY rowid ASC",
                (state.value,)).fetchall()
        return [self._job_from_row(r) for r in rows]

    def claim_next_job(self, worker_id: str) -> RenderJob | None:
        """Atomically move the oldest queued job into `rendering`.

        The UPDATE ... WHERE state='queued' guard makes the claim safe even
        with several workers polling the same queue.
        """
        now = time.time()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT job_id FROM render_jobs WHERE state = ?"
                " ORDER BY rowid ASC LIMIT 1",
                (JobState.QUEUED.value,)).fetchone()
            if not row:
                return None
            job_id = row["job_id"]
            cur = self._db.execute(
                "UPDATE render_jobs SET state = ?, worker_id = ?,"
                " lease_expires_at = ?, stage = ?, progress = 0,"
                " started_at = ?, updated_at = ?"
                " WHERE job_id = ? AND state = ?",
                (JobState.RENDERING.value, worker_id, now + 30.0,
                 RenderStage.PREPARING.value, now, now,
                 job_id, JobState.QUEUED.value))
            if cur.rowcount != 1:
                return None
        return self.get_job(job_id)

    def update_job(self, job_id: str, *, state: JobState | None = None,
                   progress: float | None = None,
                   stage: RenderStage | None = None,
                   error: str | None = None,
                   lease_expires_at: float | None = None) -> None:
        sets, vals = [], []
        if state is not None:
            sets.append("state = ?")
            vals.append(state.value)
            # Every terminal transition must stamp the end time, otherwise a
            # finished job reads as started-at with finished_at = 0 and the
            # workbench cannot show 完成时间/耗时 (§5.1).
            if state in (JobState.COMPLETE, JobState.FAILED,
                         JobState.CANCELLED):
                sets.append("finished_at = ?")
                vals.append(time.time())
        if progress is not None:
            sets.append("progress = ?")
            vals.append(float(progress))
        if stage is not None:
            sets.append("stage = ?")
            vals.append(stage.value)
        if error is not None:
            sets.append("error = ?")
            vals.append(error)
        if lease_expires_at is not None:
            sets.append("lease_expires_at = ?")
            vals.append(float(lease_expires_at))
        if not sets:
            return
        sets.append("updated_at = ?")
        vals.append(time.time())
        vals.append(job_id)
        with self._lock, self._db:
            self._db.execute(
                f"UPDATE render_jobs SET {', '.join(sets)} WHERE job_id = ?",
                vals)

    def renew_lease(self, job_id: str, seconds: float = 30.0) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE render_jobs SET lease_expires_at = ?, updated_at = ?"
                " WHERE job_id = ?",
                (time.time() + seconds, time.time(), job_id))

    def cancel_job(self, job_id: str) -> RenderJob | None:
        """Cancel a queued or rendering job (§5.4)."""
        job = self.get_job(job_id)
        if not job:
            return None
        now = time.time()
        with self._lock, self._db:
            self._db.execute(
                "UPDATE render_jobs SET state = ?, error = ?, updated_at = ?,"
                " finished_at = ? WHERE job_id = ?",
                (JobState.CANCELLED.value, "用户取消", now, now, job_id))
        return self.get_job(job_id)

    def stale_rendering_jobs(self, now: float | None = None) -> list[RenderJob]:
        """Rendering jobs whose worker lease has expired (§5.4 startup sweep)."""
        now = now if now is not None else time.time()
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM render_jobs WHERE state = ?"
                " AND lease_expires_at < ?",
                (JobState.RENDERING.value, now)).fetchall()
        return [self._job_from_row(r) for r in rows]

    # ------------------------------------------------------ video versions
    def add_version(self, version: VideoVersion) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO video_versions (version_id, listing_id, job_id,"
                " file_path, aspect, duration_s, size_bytes, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (version.version_id, version.listing_id, version.job_id,
                 version.file_path, version.aspect, version.duration_s,
                 version.size_bytes, version.created_at))

    def list_versions(self, listing_id: str) -> list[VideoVersion]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM video_versions WHERE listing_id = ?"
                " ORDER BY created_at DESC", (listing_id,)).fetchall()
        return [VideoVersion(
            version_id=r["version_id"], listing_id=r["listing_id"],
            job_id=r["job_id"], file_path=r["file_path"], aspect=r["aspect"],
            duration_s=float(r["duration_s"]), size_bytes=int(r["size_bytes"]),
            created_at=float(r["created_at"])) for r in rows]

    def get_version(self, version_id: str) -> VideoVersion | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM video_versions WHERE version_id = ?",
                (version_id,)).fetchone()
        if not row:
            return None
        return VideoVersion(
            version_id=row["version_id"], listing_id=row["listing_id"],
            job_id=row["job_id"], file_path=row["file_path"], aspect=row["aspect"],
            duration_s=float(row["duration_s"]), size_bytes=int(row["size_bytes"]),
            created_at=float(row["created_at"]))

    def prune_versions(self, listing_id: str,
                       keep: int = constants.MAX_VERSIONS_PER_LISTING) -> list[str]:
        """Keep the newest `keep` versions; return removed file paths."""
        versions = self.list_versions(listing_id)
        if len(versions) <= keep:
            return []
        doomed = versions[keep:]
        with self._lock, self._db:
            self._db.executemany(
                "DELETE FROM video_versions WHERE version_id = ?",
                [(v.version_id,) for v in doomed])
        return [v.file_path for v in doomed]

    # ------------------------------------------------------ global settings
    def get_settings(self) -> dict:
        with self._lock:
            rows = self._db.execute(
                "SELECT key, value FROM global_settings").fetchall()
        out: dict = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except json.JSONDecodeError:
                out[r["key"]] = r["value"]
        return out

    def set_global_setting(self, key: str, value) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO global_settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))

    def set_global_settings(self, values: dict) -> None:
        """Write several global settings in one transaction (§5.3)."""
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO global_settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(k, json.dumps(v, ensure_ascii=False))
                 for k, v in values.items()])


def _photo_from_row(row: sqlite3.Row) -> Photo:
    """Build a Photo from a photos row (one place, so the `manual` flag
    can never be forgotten)."""
    return Photo(
        photo_id=row["photo_id"], room_dir=row["room_dir"],
        rel_path=row["rel_path"], file_name=row["file_name"],
        size_bytes=row["size_bytes"], width=row["width"],
        height=row["height"], selected=bool(row["selected"]),
        manual_order=row["manual_order"], manual=bool(row["manual"]),
        flags=json.loads(row["flags"]))
