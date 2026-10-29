"""Request/response models for the workbench API (§9).

Every model here is a *transport* shape. Business rules live in the
services; the API layer only validates shapes and maps domain errors
into the统一信封 `{code, message, listing_id?, rel_path?, hint?}`.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ upload
class ManifestItem(BaseModel):
    path: str
    size: int = 0


class CreateListing(BaseModel):
    """Single-listing creation (v1 compatible, still used by the picker)."""
    root_dir_name: str
    manifest: list[ManifestItem] = Field(default_factory=list)
    batch_id: str | None = None


class BatchCreate(BaseModel):
    """Declare a batch of listings in one call (§3.1)."""
    label: str = ""
    batch_id: str | None = None
    listings: list[CreateListing] = Field(default_factory=list)


class BatchSummary(BaseModel):
    batch_id: str
    label: str = ""
    state: str = "open"
    listing_ids: list[str] = Field(default_factory=list)
    created_at: float = 0.0


# ---------------------------------------------------------------- settings
class ContactBlock(BaseModel):
    name: str = ""
    phone: str = ""
    wechat: str = ""
    agency: str = ""
    closing: str = ""
    qr_note: str = ""


class MusicBlock(BaseModel):
    mode: Literal["none", "synth", "custom"] = "synth"
    track: str = "warm"
    file_name: str = ""


class MotionBlock(BaseModel):
    enabled: bool = True
    pattern: Literal["auto", "push", "pull", "truck"] = "auto"
    zoom: float = 0.26
    drift: float = 0.55
    max_upscale: float = 2.6
    ease: str = "lin"


class SettingsPatch(BaseModel):
    """A partial settings patch — every field is optional."""
    contact: ContactBlock | None = None
    aspect: str | None = None
    music: MusicBlock | None = None
    motion: MotionBlock | None = None
    render_concurrency: int | None = None
    upload_concurrency: int | None = None
    max_versions_per_listing: int | None = None

    def to_payload(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in self.model_dump(exclude_none=True).items():
            out[key] = value
        return out


# --------------------------------------------------------------- selection
class SelectionPatch(BaseModel):
    """Manual selection / ordering (§5.2)."""
    selections: dict[str, bool] = Field(default_factory=dict)
    orders: dict[str, int] = Field(default_factory=dict)
    name: str | None = None


class RoomPatch(BaseModel):
    display_name: str | None = None
    order_index: int | None = None


class RoomsPatch(BaseModel):
    rooms: list[RoomPatch] = Field(default_factory=list)


# ------------------------------------------------------------------ render
class RenderBatchRequest(BaseModel):
    listing_ids: list[str] = Field(default_factory=list)
    aspect: str | None = None


class RenderJobRequest(BaseModel):
    listing_id: str
    aspect: str | None = None
