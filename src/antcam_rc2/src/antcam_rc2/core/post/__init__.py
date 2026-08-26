"""Post-processing engine: neutral ToolpathPlan -> vendor G-code (UI-agnostic)."""

from __future__ import annotations

from antcam_rc2.core.post.base import BasePostProcessor, ModalEngine
from antcam_rc2.core.post.grbl import GrblPost
from antcam_rc2.core.post.linuxcnc import LinuxCncPost
from antcam_rc2.core.post.makera import MakeraPost
from antcam_rc2.core.post.models import (
    GCodeProgram,
    OperationMeta,
    PostContext,
    PostSettings,
    ToolEntry,
    ToolTable,
)
from antcam_rc2.core.post.registry import PostProcessorRegistry, build_standard_posts
from antcam_rc2.core.post.service import PostService
from antcam_rc2.core.post.validator import validate

__all__ = [
    "BasePostProcessor",
    "GCodeProgram",
    "GrblPost",
    "LinuxCncPost",
    "MakeraPost",
    "ModalEngine",
    "OperationMeta",
    "PostContext",
    "PostProcessorRegistry",
    "PostService",
    "PostSettings",
    "ToolEntry",
    "ToolTable",
    "build_standard_posts",
    "validate",
]
