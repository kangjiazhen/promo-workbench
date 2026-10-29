"""Per-job mounts must be junctions, and junctions must actually be removed.

`renderer/render-job.mjs` mounts each job's media folder inside
`bundle/public/` as a Windows junction (a directory link, no admin rights).
Windows Python and Node both report a junction through `lstat`, but
`Path.is_symlink()` is **False** — and `unlink()` fails on it. The original
cleanup therefore removed nothing: mounts piled up, and once their listing
was deleted they dangled. The next render then died on launch with exit
0xC0000409 before a single frame was written.

These tests pin the fix: the link goes, the target (the user's photos)
survives, and garbage from a crashed job is pruned before the next render.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from property_promo.rendering.remotion_renderer import (RemotionRenderer,
                                                       _drop_link, _is_link)

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="junction 是 Windows 概念")


def make_junction(link: Path, target: Path) -> None:
    """`mklink /J` needs no admin rights; os.symlink would need developer mode."""
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True, text=True)
    if result.returncode != 0:
        pytest.skip(f"无法创建 junction: {result.stderr.strip() or result.stdout.strip()}")


@pytest.fixture()
def media(tmp_path: Path) -> Path:
    folder = tmp_path / "media" / "listing"
    (folder / "客厅").mkdir(parents=True)
    (folder / "客厅" / "01.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    return folder


@WINDOWS
def test_is_link_sees_junctions(tmp_path: Path, media: Path):
    link = tmp_path / "m_job1"
    make_junction(link, media)
    plain = tmp_path / "not_a_link"
    plain.mkdir()

    assert _is_link(link), "junction 必须被识别为链接（is_symlink() 在这里返回 False）"
    assert not _is_link(plain)
    assert not _is_link(tmp_path / "does-not-exist")


@WINDOWS
def test_drop_link_removes_the_link_and_keeps_the_photos(tmp_path: Path, media: Path):
    link = tmp_path / "m_job1"
    make_junction(link, media)
    assert (link / "客厅" / "01.jpg").is_file()

    assert _drop_link(link) is True
    assert not link.exists() and not link.is_symlink()
    # The listing's photos must still be there — a recursive delete here
    # would have destroyed the user's album.
    assert (media / "客厅" / "01.jpg").is_file()


@WINDOWS
def test_drop_link_ignores_a_real_directory(tmp_path: Path):
    real = tmp_path / "m_something"
    real.mkdir()
    (real / "keep.txt").write_text("keep", encoding="utf-8")

    assert _drop_link(real) is False
    assert (real / "keep.txt").is_file()


@WINDOWS
def test_prune_mounts_drops_dangling_and_spares_live_ones(tmp_path: Path, media: Path):
    renderer = RemotionRenderer(bundle_dir=tmp_path / "bundle")
    public = renderer.bundle_dir / "public"
    public.mkdir(parents=True)

    live = public / "m_live"
    make_junction(live, media)

    gone_target = tmp_path / "media" / "deleted_listing"
    gone_target.mkdir()
    dangling = public / "m_dead"
    make_junction(dangling, gone_target)
    gone_target.rmdir()          # listing deleted → junction now dangles

    real_dir = public / "m_not_a_link"
    real_dir.mkdir()

    removed = renderer.prune_mounts()

    assert removed == ["m_dead"]
    assert not os.path.lexists(str(dangling))
    assert live.exists() and (live / "客厅" / "01.jpg").is_file()
    assert real_dir.is_dir()


@WINDOWS
def test_prune_mounts_is_quiet_when_clean(tmp_path: Path):
    renderer = RemotionRenderer(bundle_dir=tmp_path / "bundle")
    (renderer.bundle_dir / "public").mkdir(parents=True)
    assert renderer.prune_mounts() == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
