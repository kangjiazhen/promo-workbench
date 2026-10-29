"""Room family mapping and natural sort for viewing order.

Ported conceptually from the old tools/property_promo.py ROOM_ORDER_DEFAULT
but rewritten for the new domain contract and unit-tested here.
"""
from __future__ import annotations

import re

# 看房顺序（由后端统一维护）
ROOM_ORDER_DEFAULT = [
    "玄关", "客厅", "客餐厅", "餐厅", "厨房",
    "主卧", "次卧", "卧室", "书房", "办公室",
    "卫生间", "阳台", "其他",
]

# 房间家族：名称里含这些词 → 归入该家族（顺序同 ROOM_ORDER_DEFAULT）
_FAMILY_HINTS: list[tuple[str, str]] = [
    ("玄关", "玄关"),
    ("客厅", "客厅"),
    ("客餐厅", "客餐厅"),
    ("餐厅", "客餐厅"),   # “餐厅”归客餐厅家族，排在客厅后
    ("餐厨", "客餐厅"),
    ("厨房", "厨房"),
    ("主卧", "主卧"),
    ("次卧", "卧室"),
    ("卧室", "卧室"),
    ("书房", "书房"),
    ("办公", "办公室"),
    ("卫", "卫生间"),
    ("阳台", "阳台"),
]

_NUM = re.compile(r"(\d+)")


def family_of(room_dir: str) -> tuple[int, str]:
    """返回 (家族序号, 家族名)。未识别 → (len, 原名)，排在已知家族之后。"""
    for hint, fam in _FAMILY_HINTS:
        if hint in room_dir:
            return ROOM_ORDER_DEFAULT.index(fam), fam
    return len(ROOM_ORDER_DEFAULT), room_dir


def natural_key(room_dir: str) -> tuple[int, int, str]:
    """同一家族内按编号自然排序：次卧2 < 次卧10。"""
    fam_idx, _ = family_of(room_dir)
    nums = [int(n) for n in _NUM.findall(room_dir)]
    num = nums[0] if nums else 0
    return (fam_idx, num, room_dir)


def sort_room_dirs(room_dirs: list[str]) -> list[str]:
    """按看房顺序排房间目录名；未知房间保留在最后，按自然序。"""
    return sorted(set(room_dirs), key=natural_key)
