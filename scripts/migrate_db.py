"""Open a database with the Store so the migration runs, then report."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from property_promo.jobs.store import Store  # noqa: E402

path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/property_promo.db")
store = Store(path)
for table in ("listings", "photos", "render_jobs"):
    cols = [r["name"] for r in store._db.execute(  # noqa: SLF001
        f"PRAGMA table_info({table})")]
    print(f"{table}: {cols}")
store.close()
