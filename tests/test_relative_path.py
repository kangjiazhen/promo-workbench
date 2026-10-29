import pytest

from property_promo.domain import errors
from property_promo.ingestion import relative_path as rp


def test_valid_room_file():
    assert rp.classify("AJ13103/客厅/01.jpg") == ("room", "客厅", "01.jpg")


def test_same_name_different_rooms_ok():
    a = rp.classify("A/主卧/01.jpg")
    b = rp.classify("A/厨房/01.jpg")
    assert a == ("room", "主卧", "01.jpg")
    assert b == ("room", "厨房", "01.jpg")


def test_root_loose_image():
    assert rp.classify("AJ13103/01.jpg")[0] == "root"


def test_nested_dir():
    kind, a, b = rp.classify("AJ13103/客厅/实拍/01.jpg")
    assert kind == "unsupported" and a == "nested"
    assert "实拍" in b


def test_unsupported_ext():
    assert rp.classify("AJ13103/客厅/01.heic")[:2] == ("unsupported", "ext")


def test_junk_skipped():
    assert rp.classify("AJ13103/客厅/.DS_Store")[0] == "junk"
    assert rp.classify("AJ13103/客厅/._01.jpg")[0] == "junk"


@pytest.mark.parametrize("bad", [
    "../客厅/01.jpg", "C:\\客厅\\01.jpg", "\\\\server\\客厅\\01.jpg",
    "/etc/passwd", "AJ13103/../../x.jpg", "", "客厅\x00/01.jpg",
])
def test_unsafe_paths_rejected(bad):
    with pytest.raises(errors.DomainError) as e:
        rp.classify(bad)
    assert e.value.code == "unsafe_path"


def test_hidden_room_dir_rejected():
    kind, a, _ = rp.classify("AJ13103/.git/01.jpg")
    assert kind == "unsupported" and a == "hidden_room"


def test_manifest_groups():
    m = rp.parse_manifest([
        "A/客厅/01.jpg", "A/客厅/02.jpg", "A/主卧/01.jpg",
        "A/README.md", "A/客厅/实拍/03.jpg", "A/根图.png",
    ])
    assert m["rooms"] == {"客厅": ["01.jpg", "02.jpg"], "主卧": ["01.jpg"]}
    assert m["root_loose"] == ["根图.png"]
    assert m["nested"] == ["客厅/实拍/03.jpg"]
    assert m["unsupported"] == ["README.md"]
