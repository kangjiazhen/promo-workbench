"""Parse and validate webkitRelativePath values from browser folder upload.

Contract: 房源根/房间/图片 — exactly three segments, no tricks.
"""
from __future__ import annotations

import hashlib
import re

from ..domain import errors

SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# 系统/隐藏杂项文件：不携带房源内容，静默跳过（不算“不支持的类型”）
JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

_UNSAFE = re.compile(r"(^|/)\.\.(/|$)|^[A-Za-z]:|^\\\\|^/")

ROOT, ROOM, FILE, UNSUPPORTED, JUNK = (
    "root", "room", "file", "unsupported", "junk")


def validate_raw(rel: str) -> None:
    """Reject anything that could escape the listing media dir."""
    if not isinstance(rel, str) or not rel.strip():
        raise errors.unsafe_path(repr(rel))
    norm = rel.replace("\\", "/")
    if _UNSAFE.search(norm) or "\x00" in norm:
        raise errors.unsafe_path(rel)


def _ext(name: str) -> str:
    return name[name.rfind("."):].lower() if "." in name else ""


def classify(rel: str) -> tuple[str, str, str]:
    """Classify one webkitRelativePath.

    Returns one of:
      (JUNK,        "",  name)                   — 系统杂项，跳过
      (UNSUPPORTED, "ext", name)                 — 不支持的文件类型
      (ROOT,        "",  name)                   — 根目录散图（图片在根层）
      (UNSUPPORTED, "nested", "房间/…/文件")     — 房间内嵌套目录
      (ROOM,        room_dir, file_name)         — 合法：房间/图片

    判定顺序：杂项 → 扩展名 → 结构。扩展名先于结构，
    这样根目录的 README.md 报“不支持的类型”而不是误导性的“图片放错位置”。
    """
    validate_raw(rel)
    norm = rel.replace("\\", "/").strip("/")
    parts = [p for p in norm.split("/") if p]
    if not parts:
        raise errors.unsafe_path(rel)

    name = parts[-1]
    if name in JUNK_NAMES or name.startswith("._"):
        return (JUNK, "", name)

    if _ext(name) not in SUPPORTED_EXTS:
        return (UNSUPPORTED, "ext", name)

    rest = parts[1:]
    if len(rest) <= 1:
        # 根层直接放图片（root/file，或异常的只有文件名）
        return (ROOT, "", name)

    room_dir, file_name = rest[0], rest[-1]
    if len(rest) > 2:
        return (UNSUPPORTED, "nested", "/".join(rest))

    if room_dir.startswith("."):
        return (UNSUPPORTED, "hidden_room", room_dir)

    return (ROOM, room_dir, file_name)


def canonical_key(rel: str) -> str:
    """房源内规范键：去掉根文件夹段，返回“房间/文件”。

    用于清单与上传两侧的键对齐：两侧都收完整 webkitRelativePath。
    """
    kind, a, b = classify(rel)
    if kind != ROOM:
        raise errors.DomainError(
            "not_room_file",
            f"“{rel}”不符合“房间/图片”的结构。"
            f"请把图片直接放进房间文件夹这一层。")
    return f"{a}/{b}"


def parse_manifest(paths: list[str]) -> dict[str, list[str]]:
    """Classify a whole upload manifest. Returns
    {"rooms": {room_dir: [file names]}, "root_loose": [...],
     "nested": [...], "unsupported": [...]}.

    Raises DomainError only for unsafe paths; structural problems are
    returned so the caller can pick the most helpful error order.
    """
    out: dict[str, list[str]] = {
        "rooms": {}, "root_loose": [], "nested": [], "unsupported": [],
    }
    for rel in paths:
        kind, a, b = classify(rel)
        if kind == ROOM:
            out["rooms"].setdefault(a, []).append(b)
        elif kind == ROOT:
            out["root_loose"].append(b)
        elif kind == UNSUPPORTED:
            (out["nested"] if a == "nested" else out["unsupported"]).append(b)
    return out


#: On-disk safe name for one path segment.
#:
#: The naive rule "replace every non-ASCII character with _" is WRONG for
#: this module: room names are Chinese (客厅 / 主卧 / 书房), so every room
#: would collapse into the same "_" directory and the photos would
#: overwrite each other. Verified on disk: 17 photos in the DB, 6 files on
#: disk. So the rule is:
#:
#:   * keep every Unicode word character (CJK included) — Windows and
#:     macOS both store them fine;
#:   * replace only characters a filesystem actually rejects;
#:   * append a short hash of the *original* segment to every segment, so
#:     two different names can never land on the same file — including
#:     names that differ only in case, which Windows treats as equal.
_SAFE_STEM = re.compile(r"[^\w\-.]+", re.UNICODE)
_RESERVED = ({"CON", "PRN", "AUX", "NUL"}
             | {f"COM{i}" for i in range(1, 10)}
             | {f"LPT{i}" for i in range(1, 10)})
_MAX_STEM = 48


def _hash_tag(name: str) -> str:
    """Short, stable, lowercase disambiguator for one segment."""
    return "~" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]


def _safe_stem(stem: str) -> str:
    cleaned = _SAFE_STEM.sub("_", stem).strip("_")
    cleaned = cleaned.rstrip(". ")
    if not cleaned:
        cleaned = "_"
    if cleaned.upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned[:_MAX_STEM]


def _safe_file_segment(name: str) -> str:
    """Sanitise one *file* name, keeping its extension intact.

    The extension must survive: ffmpeg and Remotion detect the format
    from it, so `01.jpg` may become `01~a1b2c3.jpg` but never `01.jpg~…`.
    """
    stem, dot, ext = name.rpartition(".")
    if not dot:
        return f"{_safe_stem(name)}{_hash_tag(name)}"
    safe_ext = _SAFE_STEM.sub("", ext)[:12]
    tag = _hash_tag(name)
    if not safe_ext:
        return f"{_safe_stem(stem)}{tag}"
    return f"{_safe_stem(stem)}{tag}.{safe_ext}"


def safe_rel(rel: str) -> str:
    """Map a validated 房间/文件 relative path to a safe on-disk path.

    The result is exactly two segments (房间/文件) so a listing's media
    directory can never contain nested trees or traversal, and two
    different source paths can never collide on disk.
    """
    validate_raw(rel)
    norm = rel.replace("\\", "/").strip("/")
    parts = [p for p in norm.split("/") if p]
    if len(parts) != 2:
        raise errors.unsafe_path(rel)
    room, name = parts
    room_out = f"{_safe_stem(room)}{_hash_tag(room)}"
    return f"{room_out}/{_safe_file_segment(name)}"

