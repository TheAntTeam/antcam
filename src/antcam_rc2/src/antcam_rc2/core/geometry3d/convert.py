"""Convert a 3D feature into the 2D entities consumed by the Fase 4 strategies.

This is the "stretch" boundary: a top face/perimeter becomes a closed
:class:`Contour`, a hole becomes a :class:`Circle`, and the feature plane Z is
carried separately so the toolpath service can plan at the right height.
"""

from __future__ import annotations

from antcam_rc2.core.geometry3d.scene import Feature3D, FeatureKind

__all__ = ["feature_to_entities", "feature_plane_z_mm"]


def feature_to_entities(feature: Feature3D):
    """Return the equivalent 2D ``SceneEntity`` tuple for a 3D feature."""
    from antcam_rc2.core.geometry.curves import Circle, Curve2, LineSegment
    from antcam_rc2.core.geometry.paths import Contour
    from antcam_rc2.core.geometry.primitives import Point2

    if feature.kind is FeatureKind.HOLE:
        if feature.center is None or feature.radius is None:
            return ()
        return (Circle(Point2(feature.center[0], feature.center[1]), feature.radius),)

    points = [(point[0], point[1]) for point in feature.boundary]
    if len(points) < 3:
        return ()
    vertices = [Point2(x, y) for x, y in points]
    if vertices[0].distance_to(vertices[-1]) <= 1e-9:
        vertices = vertices[:-1]
    if len(vertices) < 3:
        return ()
    segments: list[Curve2] = [
        LineSegment(a, b)
        for a, b in zip(vertices, vertices[1:] + [vertices[0]], strict=True)
        if a.distance_to(b) > 1e-9
    ]
    return (Contour(segments),) if segments else ()


def feature_plane_z_mm(feature: Feature3D) -> float:
    """The working-plane Z of a feature (only meaningful for Z-up features)."""
    return feature.plane_z_mm
