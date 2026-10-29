"""Print the latest frozen storyboard from the dev database (debug helper)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "property_promo.db"


def main() -> int:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("PRAGMA table_info(render_jobs)")]
    print("columns:", cols)
    rows = con.execute(
        "SELECT * FROM render_jobs ORDER BY rowid DESC LIMIT 1").fetchall()
    if not rows:
        print("no jobs")
        return 1
    row = dict(rows[0])
    key = next((k for k in row if "storyboard" in k), None)
    print("state:", row.get("state"))
    if not key:
        print("no storyboard column")
        return 1
    raw = row[key]
    if not raw:
        print("storyboard is null")
        return 1
    board = json.loads(raw)
    head = {k: v for k, v in board.items() if k != "shots"}
    print(json.dumps(head, ensure_ascii=False, indent=2))
    print("shots:", len(board.get("shots", [])))
    for s in board.get("shots", [])[:3] + board.get("shots", [])[-1:]:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
