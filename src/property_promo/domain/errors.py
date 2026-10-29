"""User-actionable business errors (never bare 400 strings)."""
from __future__ import annotations


class DomainError(Exception):
    """业务错误：message 面向用户，必须说清哪里错、怎么改。

    The API serialises it as the统一信封 (§9):
    `{code, message, listing_id?, rel_path?, hint?}`.
    """

    def __init__(self, code: str, message: str, *, listing_id: str | None = None,
                 rel_path: str | None = None, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.listing_id = listing_id
        self.rel_path = rel_path
        self.hint = hint

    def to_dict(self) -> dict:
        out: dict = {"code": self.code, "message": self.message}
        if self.listing_id:
            out["listing_id"] = self.listing_id
        if self.rel_path:
            out["rel_path"] = self.rel_path
        if self.hint:
            out["hint"] = self.hint
        return out


def root_loose_images(names: list[str]) -> DomainError:
    shown = "、".join(f"“{n}”" for n in names[:5]) + ("等" if len(names) > 5 else "")
    return DomainError(
        "root_loose_images",
        f"图片直接放在了房源根目录：{shown}。"
        f"请把照片移进对应的房间文件夹（客厅/、主卧/…）后重新上传。")


def nested_room_dirs(items: list[str]) -> DomainError:
    shown = "、".join(f"“{n}”" for n in items[:5]) + ("等" if len(items) > 5 else "")
    return DomainError(
        "nested_room_dirs",
        f"房间文件夹里又嵌套了一层目录：{shown}。"
        f"请把图片直接放在房间文件夹这一层（例如“客厅/01.jpg”，而不是“客厅/实拍/01.jpg”）。")


def unsupported_files(items: list[str]) -> DomainError:
    shown = "、".join(f"“{n}”" for n in items[:8]) + ("等" if len(items) > 8 else "")
    return DomainError(
        "unsupported_files",
        f"存在不支持的文件类型：{shown}。目前支持 JPG / JPEG / PNG / WebP，"
        f"请转换或移除后重新上传。")


def no_images() -> DomainError:
    return DomainError(
        "no_images",
        "没有找到任何图片。请确认房源文件夹里的房间子文件夹中放了照片"
        "（空文件夹不会被浏览器上传）。")


def unsafe_path(path: str) -> DomainError:
    return DomainError(
        "unsafe_path",
        f"文件路径不安全，已拒绝：{path}。路径不能包含“..”、盘符或绝对路径。")


def too_many_files(limit: int) -> DomainError:
    return DomainError(
        "too_many_files",
        f"照片数量超过上限（{limit} 张）。请精简后重新上传。")


def file_too_large(name: str, limit_mb: int) -> DomainError:
    return DomainError(
        "file_too_large",
        f"“{name}”超过单文件上限 {limit_mb} MB，请压缩后重新上传。")


def listing_not_found(listing_id: str) -> DomainError:
    return DomainError("listing_not_found", f"房源不存在或已删除：{listing_id}")


def manifest_mismatch(missing: list[str], size_bad: list[str]) -> DomainError:
    parts = []
    if missing:
        parts.append("缺少 " + "、".join(f"“{m}”" for m in missing[:5])
                     + ("等" if len(missing) > 5 else ""))
    if size_bad:
        parts.append("大小不符 " + "、".join(f"“{s}”" for s in size_bad[:5])
                     + ("等" if len(size_bad) > 5 else ""))
    return DomainError(
        "manifest_mismatch",
        "上传清单核对未通过：" + "；".join(parts)
        + "。请重试失败的文件或重新选择该房源文件夹续传。")


# --------------------------------------------------------------------------
# Batch workbench (v2) errors. One catalogue, one place (§9).
# --------------------------------------------------------------------------

def listing_not_confirmed(name: str) -> DomainError:
    return DomainError(
        "listing_not_confirmed",
        f"“{name}”还没经过人工确认，不能开始渲染。"
        f"请先在右侧详情面板确认照片与设置，再点“生成本套”或勾选后点“批量生成”。")


def illegal_transition(name: str, src: str, dst: str) -> DomainError:
    return DomainError(
        "illegal_transition",
        f"“{name}”当前是「{src}」，不能直接变成「{dst}”。"
        f"请刷新页面后重试。")


def job_not_found(job_id: str) -> DomainError:
    return DomainError("job_not_found", f"渲染任务不存在或已结束：{job_id}")


def version_not_found(version_id: str) -> DomainError:
    return DomainError("version_not_found", f"成片版本不存在：{version_id}")


def bad_setting(key: str, detail: str) -> DomainError:
    return DomainError("bad_setting", f"设置项「{key}」无效：{detail}")


def music_too_large(name: str, limit_mb: int) -> DomainError:
    return DomainError(
        "music_too_large",
        f"音乐文件“{name}”超过 {limit_mb} MB 上限，请压缩或换一首。")


def music_unsupported(name: str) -> DomainError:
    return DomainError(
        "music_unsupported",
        f"不支持的音乐格式：{name}。只支持 MP3 / M4A / WAV / OGG。")


def music_file_missing(name: str) -> DomainError:
    return DomainError(
        "music_file_missing",
        f"「{name}」选了自定义音乐，但还没有上传音乐文件。"
        "请在单套设置里上传一首，或把音乐改回「内置合成」。")


def batch_too_large(limit: int) -> DomainError:
    return DomainError(
        "batch_too_large",
        f"一次最多添加 {limit} 套房源。请分批处理。")


def empty_batch() -> DomainError:
    return DomainError(
        "empty_batch",
        "没有发现房源文件夹。请选择包含房源子文件夹的父目录，"
        "或直接选择一套房源文件夹（里面的一级子文件夹是房间名）。")


def ambiguous_folder(name: str) -> DomainError:
    return DomainError(
        "ambiguous_folder",
        f"“{name}”既是房源（直接放了图片）又像父文件夹（子文件夹里也有图片）。"
        f"请把它拆开：要么把根目录的图片移进房间文件夹，要么把房源文件夹单独添加。")


def nothing_selected() -> DomainError:
    return DomainError(
        "nothing_selected",
        "还没有勾选任何房源。请在左侧任务列表勾选要生成的套，再点“批量生成”。")


def render_failed(reason: str) -> DomainError:
    return DomainError("render_failed", f"渲染失败：{reason}")


# --------------------------------------------------------------------------
# v2.1 additions — batch upload / workbench (§3, §5, §9).
# --------------------------------------------------------------------------

def stray_files_ignored(names: list[str]) -> DomainError:
    """父文件夹根部的散图不属于任何房源（黄色警告，§12.2）。"""
    shown = "、".join(f"“{n}”" for n in names[:5]) + ("等" if len(names) > 5 else "")
    return DomainError(
        "stray_files_ignored",
        f"父文件夹根部的 {shown} 不属于任何房源，将被忽略。"
        f"如果这些照片属于某套房源，请直接添加那套房源文件夹。",
        hint="把房源文件夹单独添加即可。", )


def listing_ambiguous(name: str) -> DomainError:
    return DomainError(
        "listing_ambiguous",
        f"“{name}”既是房源（直接放了图片）又像父文件夹（子文件夹里也有图片）。"
        f"请把它拆开：要么把根目录的图片移进房间文件夹，要么把房源文件夹单独添加。",
        hint="两处图片分开添加。", )


def batch_limit_exceeded(limit: int) -> DomainError:
    return DomainError(
        "batch_limit_exceeded",
        f"一次最多添加 {limit} 套房源。请分批处理。",
        hint="分批上传。", )


def no_listings_found() -> DomainError:
    return DomainError(
        "no_listings_found",
        "未发现房源文件夹。请选择包含多套房源的父文件夹，"
        "或直接选择一套房源文件夹（里面的一级子文件夹是房间名）。",
        hint="换一个有照片的文件夹再试。", )


def file_level_failed(name: str, reason: str) -> DomainError:
    return DomainError(
        "file_level_failed",
        f"文件“{name}”处理失败：{reason}",
        rel_path=name, )


def worker_lease_expired(job_id: str) -> DomainError:
    return DomainError(
        "worker_lease_expired",
        f"渲染任务 {job_id} 的执行进程已失联（服务器可能被中断）。"
        f"已标记为失败，可以一键重试。",
        hint="点击“重试”重新排队。", )


def already_queued(name: str) -> DomainError:
    return DomainError(
        "already_queued",
        f"“{name}”已经在队列里了，本次没有重复排队。",
        hint="在渲染队列里查看进度。", )


def selection_conflict(name: str) -> DomainError:
    return DomainError(
        "selection_conflict",
        f"“{name}”的选片正在被渲染使用，本次修改将在下次生成时生效。",
        hint="等本次生成结束再改，或直接再生成一个新版本。", )


def version_limit_reached(limit: int) -> DomainError:
    return DomainError(
        "version_limit_reached",
        f"每套最多保留 {limit} 个版本，旧版本已自动清理。",
        hint="历史版本可在版本列表中查看。", )

