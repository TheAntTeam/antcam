"""Neutral 3D geometry for the Fase 9 extension (mesh-first, UI-agnostic)."""

from __future__ import annotations

from antcam_rc2.core.geometry3d.mesh import TriMesh
from antcam_rc2.core.geometry3d.scene import (
    Feature3D,
    FeatureKind,
    SolidBody,
    SolidScene,
    SolidSourceInfo,
)

__all__ = [
    "Feature3D",
    "FeatureKind",
    "SolidBody",
    "SolidScene",
    "SolidSourceInfo",
    "TriMesh",
]
