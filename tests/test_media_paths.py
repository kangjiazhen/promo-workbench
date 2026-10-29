"""Regression tests for the photo-path contract.

These tests exist because of a real bug: the original sanitiser replaced
every non-ASCII character with "_", so the Chinese room names 客厅 / 主卧 /
书房 all collapsed onto the *same* directory and the photos overwrote each
other on disk — 17 photos in the database, 6 files on disk.

The contract these tests pin down:

  1. two different rooms never share an on-disk folder;
  2. two different files never share an on-disk name;
  3. the file extension survives (ffmpeg/Remotion detect the format by it);
  4. a photo's file is found by *one* resolver, used by the uploader, the
     API, the selector and the renderer alike.
"""
from pathlib import Path

import pytest

from property_promo.domain.models import Listing, Photo
from property_promo.ingestion import media_paths
from property_promo.ingestion import relative_path as rp


# --------------------------------------------------------------- collisions
def test_chinese_rooms_do_not_collide():
    """The bug: 客厅/01.jpg, 主卧/01.jpg, 书房/01.jpg must not share a path."""
    paths = [rp.safe_rel(f"{room}/01.jpg")
             for room in ("客厅", "主卧", "书房", "厨房", "卫生间")]
    assert len(set(paths)) == len(paths), f"房间目录撞车：{paths}"


def test_same_room_same_name_different_files_differ():
    a = rp.safe_rel("客厅/01.jpg")
    b = rp.safe_rel("客厅/02.jpg")
    assert a != b
    assert a.split("/")[0] == b.split("/")[0]


def test_case_only_difference_is_kept_distinct():
    """Windows treats 01.jpg and 01.JPG as one file — they must differ."""
    assert rp.safe_rel("客厅/01.jpg") != rp.safe_rel("客厅/01.JPG")


def test_extension_survives():
    for ext in (".jpg", ".JPG", ".png", ".webp", ".jpeg"):
        out = rp.safe_rel(f"客厅/01{ext}")
        assert out.endswith(ext), f"{ext} 的扩展名被改掉了：{out}"


def test_safe_rel_keeps_exactly_two_segments():
    out = rp.safe_rel("客厅/01.jpg")
    assert len(out.split("/")) == 2


def test_safe_rel_keeps_cjk_readable():
    """The folder name should still be recognisable to a human."""
    out = rp.safe_rel("客厅/01.jpg")
    assert "客厅" in out


def test_reserved_windows_names_are_escaped():
    out = rp.safe_rel("CON/01.jpg")
    assert out.split("/")[0] != "CON"
    assert out.split("/")[0].startswith("_")


# ------------------------------------------------------------------ resolver
def test_photo_path_is_the_single_resolver(tmp_path: Path):
    rel = "客厅/01.jpg"
    p = media_paths.photo_path(tmp_path, "abc123", rel)
    assert p.parent.parent == tmp_path / "abc123"
    assert p.name.endswith(".jpg")
    # The resolver is idempotent: asking twice gives the same file.
    assert p == media_paths.photo_path(tmp_path, "abc123", rel)


def test_media_rel_is_listing_relative(tmp_path: Path):
    """A frozen storyboard must not carry an absolute path (§4)."""
    rel = media_paths.media_rel("客厅/01.jpg")
    assert not Path(rel).is_absolute()
    assert "\\" not in rel


def test_uploader_and_resolver_agree(tmp_path: Path):
    """The uploader writes exactly where the resolver looks."""
    from property_promo.ingestion.upload_service import UploadService

    class _FakeStore:
        def add_photo(self, photo, listing_id):
            pass

        def set_state(self, listing_id, state):
            pass

    service = UploadService(_FakeStore(), tmp_path, max_upload_mb=30,
                             max_photos=10)
    listing = Listing(listing_id="L1", name="测试", root_dir_name="R",
                      state=None)
    # A 1x1 PNG is a valid image the probe can read.
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="PNG")
    payload = buf.getvalue()
    # The browser sends the full webkitRelativePath (房源/房间/图片).
    listing.expected_files = {"客厅/01.png": len(payload)}
    photo = service.store_file(listing, "R/客厅/01.png", payload)
    on_disk = media_paths.photo_path(tmp_path, "L1", photo.rel_path)
    assert on_disk.is_file(), f"上传写入的位置和解析位置不一致：{on_disk}"


def test_photo_rel_path_round_trip(tmp_path: Path):
    """The listing's rel_path is the canonical 房间/文件 key."""
    photo = Photo(photo_id="p1", room_dir="客厅", rel_path="客厅/01.jpg",
                  file_name="01.jpg", size_bytes=1, width=4, height=4)
    p = media_paths.photo_path(tmp_path, "L1", photo.rel_path)
    assert p.name.startswith("01")
    assert p.suffix == ".jpg"


@pytest.mark.parametrize("bad", ["../x.jpg", "客厅/../../x.jpg"])
def test_unsafe_rel_rejected(bad):
    with pytest.raises(Exception):
        rp.safe_rel(bad)
