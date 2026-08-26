"""Safe persistent references to features in a transient 3D :class:`SolidScene`.

Mirrors :mod:`antcam_rc2.core.project.geometry_refs`: references are resolved
by exact feature fingerprint (never retargeted) and remain valid only for the
immutable scene they were created from.
"""

from __future__ import annotations

from antcam_rc2.core.errors import GeometryReferenceError
from antcam_rc2.core.geometry3d.scene import Feature3D, SolidScene
from antcam_rc2.core.project.models import SolidBinding, SolidRef

__all__ = [
    "bind_solid",
    "create_solid_ref",
    "resolve_solid_ref",
    "solid_scene_fingerprint",
]


def solid_scene_fingerprint(scene: SolidScene) -> str:
    """Return the canonical fingerprint of an imported 3D scene."""
    return scene.fingerprint()


def bind_solid(scene: SolidScene) -> SolidBinding:
    """Create persistent source provenance for an attached 3D scene."""
    return SolidBinding(source=scene.source, scene_fingerprint=solid_scene_fingerprint(scene))


def create_solid_ref(scene: SolidScene, body_index: int, feature_index: int) -> SolidRef:
    """Build a validated persistent reference from one selected feature."""
    if body_index < 0 or body_index >= len(scene.bodies):
        raise GeometryReferenceError(f"solid body index out of bounds: {body_index}")
    body = scene.bodies[body_index]
    if feature_index < 0 or feature_index >= len(body.features):
        raise GeometryReferenceError(f"solid feature index out of bounds: {body_index}[{feature_index}]")
    feature = body.features[feature_index]
    return SolidRef(
        body_index=body_index,
        feature_index=feature_index,
        feature_type=feature.kind.value,
        feature_fingerprint=feature.fingerprint(scene.tolerance_mm),
    )


def resolve_solid_ref(reference: SolidRef, scene: SolidScene) -> Feature3D:
    """Resolve ``reference`` against ``scene`` without ever retargeting it."""
    if reference.body_index < 0 or reference.body_index >= len(scene.bodies):
        raise GeometryReferenceError(f"stale solid reference: body {reference.body_index} is missing")
    body = scene.bodies[reference.body_index]
    if reference.feature_index < 0 or reference.feature_index >= len(body.features):
        raise GeometryReferenceError(
            f"stale solid reference: body {reference.body_index}[{reference.feature_index}] is missing"
        )
    feature = body.features[reference.feature_index]
    if feature.kind.value != reference.feature_type:
        raise GeometryReferenceError(
            f"stale solid reference: feature type changed for {reference.body_index}[{reference.feature_index}]"
        )
    if feature.fingerprint(scene.tolerance_mm) != reference.feature_fingerprint:
        raise GeometryReferenceError(
            f"stale solid reference: {reference.body_index}[{reference.feature_index}] no longer matches"
        )
    return feature
