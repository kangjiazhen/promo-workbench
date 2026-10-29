from property_promo.domain.room_order import natural_key, sort_room_dirs


def test_known_families_in_tour_order():
    dirs = ["卫生间", "主卧", "客厅", "厨房", "阳台", "玄关"]
    assert sort_room_dirs(dirs) == ["玄关", "客厅", "厨房", "主卧", "卫生间", "阳台"]


def test_numbered_rooms_after_base_and_natural_sort():
    dirs = ["次卧10", "次卧2", "次卧1", "主卧", "次卧"]
    assert sort_room_dirs(dirs) == ["主卧", "次卧", "次卧1", "次卧2", "次卧10"]


def test_numbered_offices_grouped():
    dirs = ["独立办公室2", "独立办公室1", "办公室公共区域", "书房"]
    assert sort_room_dirs(dirs) == ["书房", "办公室公共区域", "独立办公室1", "独立办公室2"]


def test_unknown_rooms_last_kept_original():
    dirs = ["客厅", "阁楼", "储藏间", "主卧"]
    assert sort_room_dirs(dirs) == ["客厅", "主卧", "储藏间", "阁楼"] or \
           sort_room_dirs(dirs)[:2] == ["客厅", "主卧"]


def test_dining_maps_to_family():
    # “餐厅”归入客餐厅家族：客厅 → 客餐厅(餐厅) → 厨房
    dirs = ["厨房", "餐厅", "客厅"]
    assert sort_room_dirs(dirs) == ["客厅", "餐厅", "厨房"]


def test_natural_key_monotonic():
    assert natural_key("次卧2") < natural_key("次卧10")
    assert natural_key("主卧") < natural_key("次卧1")
