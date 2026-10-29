import sqlite3
import sys
from pathlib import Path

db = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1
                     else "data/property_promo.db")
db.row_factory = sqlite3.Row
tables = [r[0] for r in db.execute(
    "select name from sqlite_master where type='table'")]
for t in tables:
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
    print(f"{t}: {cols}")
print("--- listings ---")
try:
    for r in db.execute("select * from listings"):
        print(dict(r))
except Exception as e:
    print("no listings:", e)
print("--- photo count ---")
try:
    print(db.execute("select count(*) from photos").fetchone()[0])
except Exception as e:
    print("no photos:", e)
