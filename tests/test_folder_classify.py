"""§3.1 分类规则：按**图片深度**判断，不是「根目录有没有图片」。

这条规则曾经写错，而且错了整整两个阶段没人发现：用户真实结构是
`房源/房间/图片`，房源根目录里根本没有图片；旧规则「直接子级含图片才算
房源」把它当成父文件夹，于是**一套房源被拆成 6 套**（每个房间一套）。
只有真浏览器端到端跑一遍（tests/e2e_browser.py 选文件夹那一刻）才暴露。

所以这里直接钉死四种形态，并同时校验后端实现与前端源码里的同一口径。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from property_promo.ingestion import folder_classify as fc

MODULE = Path(__file__).resolve().parents[1]
WEB_LIB = MODULE / "web" / "src" / "lib" / "batchUpload.ts"


def classify(paths: list[str], root: str = "房源A"):
    return fc.classify(fc.scan(paths), root)


# ---------------------------------------------------------------- 真实结构
def test_a_normal_listing_is_one_listing_not_one_per_room():
    """`房源/房间/图片` → 一套房源，房间是内部结构。"""
    paths = [f"客厅/0{i}.jpg" for i in range(1, 7)] + [
        "主卧/01.jpg", "厨房/01.jpg", "卫生间/01.jpg"]
    r = classify(paths, "AJ13103")
    assert r.mode == "listing"
    assert len(r.units) == 1, "一套房源不能被拆成多套"
    assert r.units[0].root_dir_name == "AJ13103"
    rooms = {e.path.split("/")[0] for e in r.units[0].entries}
    assert rooms == {"客厅", "主卧", "厨房", "卫生间"}
    assert not r.units[0].loose


def test_a_room_folder_only_album_is_accepted():
    """19 个分组文件夹的真实相册（按房间分）必须是单套。"""
    template_rooms = ["办公室公共区域", "餐厅", "厨房", "次卧", "次卧1",
                      "独立办公室1", "客餐厅", "客厅", "其他", "书房",
                      "卫生间", "卧室", "阳台", "主卧"]
    paths = [f"{room}/01.jpg" for room in template_rooms]
    r = classify(paths, "AJ13103")
    assert r.mode == "listing"
    assert len(r.units) == 1
    assert len(r.units[0].entries) == len(template_rooms)


def test_a_parent_folder_of_listings_still_splits():
    """`父/房源/房间/图片` → 每个子文件夹一套。"""
    paths = ["房源A/客厅/01.jpg", "房源A/主卧/01.jpg",
             "房源B/客厅/01.jpg", "房源B/次卧/01.jpg"]
    r = classify(paths, "十套模板")
    assert r.mode == "parent"
    assert [u.root_dir_name for u in r.units] == ["房源A", "房源B"]
    assert all(len(u.entries) == 2 for u in r.units)


# ------------------------------------------------------------------ 冲突态
def test_images_at_two_depths_is_an_error_not_a_guess():
    """两层都有图片 → 拒绝，并点名是哪两个文件夹，别让用户猜。"""
    paths = ["客厅/01.jpg", "客厅/实拍/01.jpg", "厨房/02.jpg"]
    r = classify(paths, "X")
    assert r.mode == "ambiguous"
    assert r.units == []
    assert "客厅" in r.message and "实拍" in r.message
    assert "房源/房间/图片" in r.message


# ------------------------------------------------------------------ 散图
def test_loose_images_in_the_listing_root_are_blocked_not_split():
    """根层散图仍是单套（不是父文件夹），由结构校验标红。"""
    r = classify(["01.jpg", "02.jpg", "客厅/03.jpg"], "示例花园1201")
    assert r.mode == "listing"
    assert sorted(r.units[0].loose) == ["01.jpg", "02.jpg"]
    assert [e.path for e in r.units[0].entries] == ["客厅/03.jpg"]


def test_an_empty_folder_explains_the_browser_limit():
    r = classify([], "空模板")
    assert r.mode == "empty"
    assert "空文件夹" in r.message


def test_junk_files_never_decide_the_mode():
    """`.DS_Store` 不能让一个房源看起来像父文件夹。"""
    r = classify([".DS_Store", "客厅/.DS_Store", "客厅/01.jpg"], "A")
    assert r.mode == "listing"
    assert len(r.units) == 1


# --------------------------------------------------- 前后端同一口径（源码级）
@pytest.mark.parametrize("needle", [
    "depthOf",              # 按深度判断
    "roomLevel",            # 第二层有图片 → 房源
    "deepLevel",            # 第三层有图片 → 父文件夹
])
def test_the_client_judges_by_depth_too(needle: str):
    """前端是同一套规则的第二个实现，口径必须对齐（§3.2 共享常量同理）。"""
    src = WEB_LIB.read_text(encoding="utf-8")
    assert needle in src, f"batchUpload.ts 缺少 {needle}：前后端分类口径已经漂了"
