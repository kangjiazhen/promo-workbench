"""Rendering layer: storyboard → MP4, plus the quality gate."""
from __future__ import annotations

from .base import RenderResult, Renderer
from .stub_renderer import StubRenderer
from .verifier import VerifyError, extract_frame, probe, verify

__all__ = [
    "RenderResult", "Renderer", "StubRenderer",
    "VerifyError", "extract_frame", "probe", "verify",
]

