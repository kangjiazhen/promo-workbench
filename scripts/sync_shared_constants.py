"""Generate the frontend copy of `shared/constants.json`.

The frontend must not carry its own list of supported formats / aspects /
limits — the v2.1 design pins one shared口径. This script writes
`web/src/lib/sharedConstants.ts` from the JSON, and
`tests/test_shared_constants.py` fails when the two disagree.

Usage:  python scripts/sync_shared_constants.py [--check]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "shared" / "constants.json"
DST = ROOT / "web" / "src" / "lib" / "sharedConstants.ts"

HEADER = """/**
 * GENERATED FILE — do not edit by hand.
 * Source: shared/constants.json  (regenerate: python scripts/sync_shared_constants.py)
 *
 * 前后端共享口径：支持格式、路径深度、画幅、音乐、并发与上限。
 */

"""


def render() -> str:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    body = json.dumps(data, ensure_ascii=False, indent=2)
    return f"{HEADER}export const SHARED_CONSTANTS = {body} as const;\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    want = render()
    if check:
        have = DST.read_text(encoding="utf-8") if DST.exists() else ""
        if have != want:
            print("sharedConstants.ts 与 shared/constants.json 不一致，"
                  "请运行：python scripts/sync_shared_constants.py")
            return 1
        print("shared constants in sync")
        return 0
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(want, encoding="utf-8")
    print(f"wrote {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
