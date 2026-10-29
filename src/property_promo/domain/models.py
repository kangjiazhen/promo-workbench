"""Domain models for the property_promo module (batch workbench v2.1).

Status machine (§5.1 of the design doc):

    待上传 created → 上传中 uploading → 待确认 awaiting_confirm
        → 排队渲染 queued → 渲染中 rendering → 完成 complete
        ↘ 失败 failed / 已取消 cancelled

`awaiting_confirm` is the ONLY state that may enter `queued`: a listing
must be manually confirmed before it is allowed to render (§6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ListingState(str, Enum):
    CREATED = "created"                # 已创建，等待文件
    UPLOADING = "uploading"            # 部分文件已到
    AWAITING_CONFIRM = "awaiting_confirm"  # 上传完成，等待人工确认
    QUEUED = "queued"                  # 已确认，排队等待渲染
    RENDERING = "rendering"            # 渲染中
    COMPLETE = "complete"              # 渲染完成
    FAILED = "failed"                  # 上传或渲染失败
    CANCELLED = "cancelled"            # 用户取消

    @property
    def label(self) -> str:
        return {
            ListingState.CREATED: "待上传",
            ListingState.UPLOADING: "上传中",
            ListingState.AWAITING_CONFIRM: "待确认",
            ListingState.QUEUED: "排队渲染",
            ListingState.RENDERING: "渲染中",
            ListingState.COMPLETE: "完成",
            ListingState.FAILED: "失败",
            ListingState.CANCELLED: "已取消",
        }[self]


#: Allowed transitions. Used by the API layer to reject illegal moves with
#: a user-actionable message instead of silently corrupting a job.
TRANSITIONS: dict[ListingState, set[ListingState]] = {
    ListingState.CREATED: {ListingState.UPLOADING, ListingState.FAILED,
                           ListingState.CANCELLED},
    ListingState.UPLOADING: {ListingState.AWAITING_CONFIRM, ListingState.FAILED,
                             ListingState.CANCELLED},
    ListingState.AWAITING_CONFIRM: {ListingState.QUEUED, ListingState.FAILED,
                                    ListingState.CANCELLED},
    ListingState.QUEUED: {ListingState.RENDERING, ListingState.AWAITING_CONFIRM,
                          ListingState.FAILED, ListingState.CANCELLED},
    ListingState.RENDERING: {ListingState.COMPLETE, ListingState.FAILED,
                             ListingState.CANCELLED},
    ListingState.COMPLETE: {ListingState.QUEUED, ListingState.AWAITING_CONFIRM},
    ListingState.FAILED: {ListingState.QUEUED, ListingState.UPLOADING,
                          ListingState.AWAITING_CONFIRM, ListingState.CANCELLED},
    ListingState.CANCELLED: {ListingState.AWAITING_CONFIRM, ListingState.QUEUED},
}


def can_transition(src: ListingState, dst: ListingState) -> bool:
    return dst in TRANSITIONS.get(src, set())


#: Backwards-compatible alias used by the upload service.
UploadState = ListingState


class JobState(str, Enum):
    QUEUED = "queued"
    RENDERING = "rendering"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RenderStage(str, Enum):
    PREPARING = "preparing"    # 素材准备
    RENDERING = "rendering"    # 渲染
    CHECKING = "checking"      # 质检


class PhotoFlag(str, Enum):
    """用户可理解的照片标记（不暴露内部模型分数）。"""
    BLURRY = "模糊"
    DARK = "过暗"
    DUPLICATE = "重复"


@dataclass
class Photo:
    photo_id: str
    room_dir: str               # 原始房间目录名，如 "客厅"
    rel_path: str               # webkitRelativePath 去掉首段，如 "客厅/01.jpg"
    file_name: str              # "01.jpg"
    size_bytes: int
    width: int = 0
    height: int = 0
    selected: bool = True
    manual_order: int | None = None
    manual: bool = False        # 用户手动改过 → 自动推荐不得覆盖
    flags: list[str] = field(default_factory=list)

    @property
    def room_key(self) -> str:
        return self.room_dir


@dataclass
class Room:
    room_dir: str               # 原始目录名（唯一键）
    display_name: str           # 可改的显示名
    order: int
    photos: list[Photo] = field(default_factory=list)


@dataclass
class Listing:
    listing_id: str
    name: str                   # 默认取根文件夹名，仅应用内识别
    root_dir_name: str          # 上传根文件夹名（如 AJ13103）
    state: ListingState = ListingState.CREATED
    rooms: list[Room] = field(default_factory=list)
    expected_files: dict[str, int] = field(default_factory=dict)  # rel_path -> size
    settings: dict = field(default_factory=dict)                 # 单套覆盖
    batch_id: str | None = None
    error: str | None = None
    progress: float = 0.0       # 0..1，当前阶段进度
    stage: str | None = None    # preparing/rendering/checking
    created_at: float = 0.0
    updated_at: float = 0.0

    def room(self, room_dir: str) -> Room | None:
        return next((r for r in self.rooms if r.room_dir == room_dir), None)

    def all_photos(self) -> list[Photo]:
        return [p for r in self.rooms for p in r.photos]

    def total_photos(self) -> int:
        return sum(len(r.photos) for r in self.rooms)

    def selected_photos(self) -> list[Photo]:
        return [p for p in self.all_photos() if p.selected]


@dataclass
class RenderJob:
    job_id: str
    listing_id: str
    state: JobState = JobState.QUEUED
    progress: float = 0.0
    stage: RenderStage = RenderStage.PREPARING
    error: str | None = None
    aspect: str = "16:9"
    storyboard: dict = field(default_factory=dict)
    lease_expires_at: float = 0.0
    worker_id: str | None = None
    batch_id: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class VideoVersion:
    version_id: str
    listing_id: str
    job_id: str
    file_path: str
    aspect: str = "16:9"
    duration_s: float = 0.0
    size_bytes: int = 0
    created_at: float = 0.0
