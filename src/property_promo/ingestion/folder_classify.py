"""Folder classification (§3.1) — server side, same rules as the client.

The browser normally splits a parent folder into listings itself and
sends one `房源/房间/文件` manifest per listing. This module is the
server-side mirror of that logic: it lets the API validate an incoming
tree, and it gives both sides one testable specification of the
classification rules.

Rules (v2.1, user-pinned — judged by **image depth**, not by "are there
images in the root"):

  * 有 `子/图.jpg`（深度 2）        → 该文件夹是**房源根**（子文件夹 = 房间）
  * 只有 `子/孙/图.jpg`（深度 ≥3）  → 该文件夹是**父文件夹**，
    每个含图片的子文件夹是一套房源
  * 两层都有图片                    → 错误：分不清哪一层是房间
  * 只有根层散图（深度 1）          → 房源根，散图按结构违规阻止上传
  * 父文件夹之下不递归拆第三层（孙子目录按嵌套规则报错）

The depth rule matters: the user's real layout is `房源/房间/图片`, where
the listing root has **no** images at all. Classifying by "root has images"
turned one property into six listings — one per room. Caught by the real
browser E2E run, not by any unit test.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import constants
from ..domain.errors import DomainError


@dataclass
class FolderEntry:
    """One file inside a dropped/selected folder tree."""
    path: str            # relative to the added folder, e.g. "客厅/01.jpg"
    size: int = 0
    is_image: bool = False
    is_junk: bool = False


@dataclass
class ListingUnit:
    """One property listing carved out of a dropped folder."""
    root_dir_name: str
    entries: list[FolderEntry] = field(default_factory=list)
    loose: list[str] = field(default_factory=list)        # 根层散图
    nested: list[str] = field(default_factory=list)       # 房间内嵌套
    unsupported: list[str] = field(default_factory=list)   # 不支持的类型
    empty: bool = False                                   # 整个文件夹没有图片


@dataclass
class FolderClassification:
    mode: str                    # "listing" | "parent" | "ambiguous" | "empty"
    units: list[ListingUnit] = field(default_factory=list)
    message: str = ""


def _is_junk(name: str) -> bool:
    return constants.is_junk(name)


def _is_image(name: str) -> bool:
    return constants.is_supported_image(name)


def scan(paths: list[str], sizes: dict[str, int] | None = None,
         root_name: str = "") -> list[FolderEntry]:
    """Turn a flat path list into entries classified by depth."""
    sizes = sizes or {}
    out: list[FolderEntry] = []
    for p in paths:
        norm = p.replace("\\", "/").strip("/")
        if not norm:
            continue
        name = norm.split("/")[-1]
        out.append(FolderEntry(
            path=norm,
            size=int(sizes.get(p, 0)),
            is_image=_is_image(name),
            is_junk=_is_junk(name),
        ))
    return out


def classify(entries: list[FolderEntry], root_name: str) -> FolderClassification:
    """Classify one added folder into listing / parent / ambiguous."""
    useful = [e for e in entries if not e.is_junk]
    images = [e for e in useful if e.is_image]
    if not images:
        return FolderClassification(
            mode="empty",
            message="这个文件夹里没有照片（空文件夹不会被浏览器上传）。")

    direct = [e for e in useful if "/" not in e.path]
    direct_images = [e for e in direct if e.is_image]

    # 判据是**图片所在的深度**，不是「根目录有没有图片」。
    #
    # 用户真实结构是 `房源/房间/图片`，房源根目录里根本没有图片。
    # 旧规则（"直接子级含图片才算房源"）会把它当父文件夹拆成 N 套，
    # 每个房间变成一套房源 —— 这是真实浏览器测试抓到的缺陷。
    #   深度 2（子/图.jpg）      → 这个文件夹是房源，子文件夹是房间
    #   深度 >=3（子/孙/图.jpg） → 这个文件夹是父文件夹，子文件夹是房源
    #   深度 1（根层散图）        → 房源，散图按结构违规报错
    depths = [len(e.path.split("/")) for e in images]
    room_level = any(d == 2 for d in depths)
    deep_level = any(d >= 3 for d in depths)

    if room_level and deep_level:
        with_rooms = sorted({e.path.split("/")[0] for e in images
                             if len(e.path.split("/")) == 2})
        with_deeper = sorted({"/".join(e.path.split("/")[:2]) for e in images
                              if len(e.path.split("/")) >= 3})
        return FolderClassification(
            mode="ambiguous",
            message=(f"「{'、'.join(with_rooms[:3])}」里直接就有图片，"
                     f"而「{'、'.join(with_deeper[:3])}」里还有一层文件夹，"
                     "分不清哪一层是房间。请按「房源/房间/图片」整理："
                     "图片直接放进房间文件夹，房间下面不要再套文件夹。"))

    if room_level or direct_images:
        return FolderClassification(
            mode="listing",
            units=[_listing_unit(root_name, useful, root_name)])

    child_dirs_with_images = {
        e.path.split("/")[0] for e in useful if e.is_image and "/" in e.path
    }
    if child_dirs_with_images:
        units = []
        for d in sorted(child_dirs_with_images):
            sub = [_rebase(e, d) for e in useful
                   if e.path.split("/")[0] == d and "/" in e.path]
            units.append(_listing_unit(d, sub, d))
        return FolderClassification(mode="parent", units=units)

    # 只有不支持的类型
    return FolderClassification(
        mode="empty",
        message="这个文件夹里没有可用的图片（JPG / PNG / WebP）。")


def _rebase(entry: FolderEntry, prefix: str) -> FolderEntry:
    """Drop the parent-folder segment from a path.

    A unit carved out of a parent folder must carry paths relative to
    **itself** (`客厅/01.jpg`), exactly like a directly-added listing —
    otherwise every picture inside it looks one level too deep and gets
    reported as 嵌套.
    """
    rest = entry.path.split("/", 1)[1] if "/" in entry.path else entry.path
    return FolderEntry(path=rest, size=entry.size,
                       is_image=entry.is_image, is_junk=entry.is_junk)


def _listing_unit(root_name: str, entries: list[FolderEntry],
                   display_root: str) -> ListingUnit:
    """Build one listing unit from the entries under one root."""
    unit = ListingUnit(root_dir_name=display_root)
    for e in entries:
        segs = e.path.split("/")
        if len(segs) == 1:
            # 根层文件：图片 → 散图（结构违规）；非图片 → 不支持
            if e.is_image:
                unit.loose.append(e.path)
            else:
                unit.unsupported.append(e.path)
            continue
        if len(segs) == 2:
            if e.is_image:
                unit.entries.append(e)
            else:
                unit.unsupported.append(e.path)
            continue
        # 三段以上 = 房间内嵌套
        if e.is_image:
            unit.nested.append(e.path)
        else:
            unit.unsupported.append(e.path)
    return unit


def split_parent(entries: list[FolderEntry], parent_name: str
                 ) -> tuple[list[ListingUnit], list[str]]:
    """Split a parent folder into listings; return (units, stray_paths).

    Stray files directly under the parent do not belong to any listing —
    they are reported as a yellow warning, never an error (§12.2).
    """
    stray = [e.path for e in entries
             if "/" not in e.path and not e.is_junk and e.is_image]
    units = []
    child_dirs = {e.path.split("/")[0] for e in entries
                  if "/" in e.path and not e.is_junk}
    for d in sorted(child_dirs):
        sub = [e for e in entries
               if e.path.split("/")[0] == d and "/" in e.path]
        if any(e.is_image for e in sub):
            units.append(_listing_unit(d, sub, d))
    return units, stray
