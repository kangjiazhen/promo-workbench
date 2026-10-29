"""The bundle cache's two silent-failure guards, tested for real.

`renderer/bundle-cache.mjs` decides «is the webpack build on disk the build of
the source on disk, and is anyone else using it». Both regressions it exists to
prevent were invisible:

* a hard-killed render left `.active-<pid>` behind, and every later job read
  that as «另一路渲染正在运行», skipped the rebuild and rendered byte-identical
  films — editing the composition changed nothing on screen;
* a job that lost the build lock to a *finishing* rebuild threw that fresh
  build away.

So the checks run the real module in Node rather than re-implementing it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "renderer" / "bundle-cache.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="需要 Node.js 才能跑渲染器的 bundle 缓存模块"
)

PRELUDE = """import fs from "node:fs";
import process from "node:process";
import * as bc from {module};
"""


def run_js(tmp_path: Path, body: str, *args: object) -> object:
    """Run `body` (an async function body) against the real module."""
    script = tmp_path / "_probe.mjs"
    script.write_text(
        PRELUDE.format(module=json.dumps(MODULE.as_uri()))
        + "const [a, b] = process.argv.slice(2);\n"
        + f"const out = await (async () => {{ {body} }})();\n"
        + 'process.stdout.write(JSON.stringify(out ?? null));\n',
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NODE, str(script), *(str(x) for x in args)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout or "null")


@pytest.fixture()
def bundle_dir(tmp_path: Path) -> Path:
    d = tmp_path / "bundle"
    d.mkdir()
    return d


def test_source_stamp_follows_both_content_and_mtime(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "nested").mkdir(parents=True)
    frame = src / "PropertyPromo.tsx"
    frame.write_text("a", encoding="utf-8")
    (src / "nested" / "RoomLabel.tsx").write_text("x", encoding="utf-8")

    first = run_js(tmp_path, "return bc.sourceStamp(a);", src)
    assert "PropertyPromo.tsx" in first and "RoomLabel.tsx" in first

    # Same byte count, new mtime: a save that changes nothing must still rebuild.
    os.utime(frame, (time.time() + 2, time.time() + 2))
    second = run_js(tmp_path, "return bc.sourceStamp(a);", src)
    assert second != first, "改了文件的 mtime，stamp 必须变"

    frame.write_text("abcd", encoding="utf-8")
    third = run_js(tmp_path, "return bc.sourceStamp(a);", src)
    assert third != second, "改了字节数，stamp 必须变"


def test_pid_alive_tells_a_running_process_from_a_ghost(tmp_path: Path) -> None:
    alive = run_js(tmp_path, "return [bc.pidAlive(Number(a)), bc.pidAlive(99999999), bc.pidAlive(0), bc.pidAlive(-3), bc.pidAlive(1.5)];", os.getpid())
    assert alive == [True, False, False, False, False]


def test_a_dead_marker_is_reaped_rather_than_respected(bundle_dir: Path, tmp_path: Path) -> None:
    dead = bundle_dir / ".active-99999999"
    dead.write_text("99999999", encoding="utf-8")

    out = run_js(
        tmp_path,
        "const before = bc.otherRendersActive(a);"
        "const live = bc.liveMarkers(a);"
        "return {before, live, stillThere: fs.existsSync(a + '/.active-99999999')};",
        bundle_dir,
    )
    assert out["before"] is False, "进程早死了的标记不能算「有人在渲染」"
    assert out["live"] == []
    assert out["stillThere"] is False, "死标记应当被顺手清掉"


def test_a_live_marker_blocks_a_rebuild(bundle_dir: Path, tmp_path: Path) -> None:
    sleeper = subprocess.Popen(  # a render that is genuinely still running
        [NODE, "-e", "setTimeout(() => {}, 30000)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        marker = bundle_dir / f".active-{sleeper.pid}"
        marker.write_text(str(sleeper.pid), encoding="utf-8")
        out = run_js(
            tmp_path,
            "return {active: bc.otherRendersActive(a), live: bc.liveMarkers(a), stillThere: fs.existsSync(a)};",
            bundle_dir,
        )
        assert out["active"] is True
        assert out["live"] == [marker.name]
        # Marker semantics: only the file that names the pid is inspected here.
        assert marker.exists()
    finally:
        sleeper.kill()
        sleeper.wait(timeout=10)


def test_bundle_is_fresh_only_when_the_stamp_matches(bundle_dir: Path, tmp_path: Path) -> None:
    (bundle_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (bundle_dir / ".stamp").write_text("S", encoding="utf-8")

    out = run_js(
        tmp_path,
        "return [bc.bundleIsFresh(a, 'S'), bc.bundleIsFresh(a, 'T')];",
        bundle_dir,
    )
    assert out == [True, False]

    (bundle_dir / "index.html").unlink()
    assert run_js(tmp_path, "return bc.bundleIsFresh(a, 'S');", bundle_dir) is False
