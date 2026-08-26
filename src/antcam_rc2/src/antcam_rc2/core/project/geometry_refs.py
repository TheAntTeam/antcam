"""Safe persistent references to entities held in transient geometry scenes.

The scene fingerprint index precomputes every entity fingerprint in one pass so
that per-reference resolution becomes O(1) for the fast path and O(layer) for
the reorder fallback — planning projects with hundreds of references stays fast
and deterministic.

The index is **cached per scene object** (``WeakKeyDictionary``): re-planning
the same immutable scene skips the rebuild entirely.  Contract: a
:class:`GeometryScene` is immutable after ``attach_geometry``; mutating a scene
that was already indexed requires a fresh instance (or an explicit
``invalidate_scene_index_cache`` call).
"""

from __future__ import annotations

import hashlib
import json
import weakref
from dataclasses import dataclass
from typing import Any

from antcam_rc2.core.errors import GeometryReferenceError
from antcam_rc2.core.geometry.curves import Arc, Circle, Curve2, LineSegment
from antcam_rc2.core.geometry.paths import Contour, Path
from antcam_rc2.core.io.scene import GeometryScene, SceneEntity
from antcam_rc2.core.project.models import GeometryBinding, GeometryRef


class _SceneIndexCache:
    """Weak scene-keyed index cache.

    Pydantic v2 models are weakref-able but not hashable (mutable fields), so
    a plain ``WeakKeyDictionary`` is unusable.  We key by ``id(scene)`` and
    validate liveness with a weakref: dead scenes are swept on the next write.
    """

    def __init__(self) -> None:
        self._entries: dict[int, tuple[weakref.ReferenceType, SceneFingerprintIndex]] = {}

    def get(self, scene: GeometryScene) -> SceneFingerprintIndex | None:
        entry = self._entries.get(id(scene))
        if entry is not None and entry[0]() is scene:
            return entry[1]
        return None

    def put(self, scene: GeometryScene, index: SceneFingerprintIndex) -> None:
        self._entries[id(scene)] = (weakref.ref(scene), index)
        if len(self._entries) > 64:
            self._sweep()

    def clear(self) -> None:
        self._entries.clear()

    def _sweep(self) -> None:
        for key, (reference, _) in list(self._entries.items()):
            if reference() is None:
                del self._entries[key]


# Cached fingerprint indices keyed by scene identity.  Weak references let the
# cache die with the scene (GC invalidation); ``attach_geometry`` also
# invalidates explicitly so a re-attached scene is always rebuilt.
_SCENE_INDEX_CACHE = _SceneIndexCache()


@dataclass(frozen=True, slots=True)
class SceneFingerprintIndex:
    """Precomputed canonical fingerprints for one transient scene.

    ``entity_fingerprints`` maps ``(layer_name, entity_index)`` to the entity
    fingerprint; ``scene_fingerprint`` is the binding fingerprint for the whole
    scene.  The index is immutable and scoped to a single planning call.
    """

    scene_fingerprint: str
    entity_fingerprints: dict[tuple[str, int], str]

    def entity_fingerprint(self, layer_name: str, entity_index: int) -> str | None:
        """Return the precomputed fingerprint or ``None`` when out of range."""
        return self.entity_fingerprints.get((layer_name, entity_index))


def entity_fingerprint(entity: SceneEntity, tolerance_mm: float) -> str:
    """Return a canonical SHA-256 fingerprint for one native scene entity.

    Coordinates are quantized to the scene tolerance, making fingerprints
    stable across numerically equivalent importer results while preserving
    curve types and direction.
    """
    if tolerance_mm <= 0:
        raise ValueError("tolerance_mm must be positive")
    return _fingerprint(_entity_payload(entity, tolerance_mm))


def scene_fingerprint(scene: GeometryScene) -> str:
    """Return a canonical fingerprint of layer order and native entities."""
    return build_scene_index(scene).scene_fingerprint


def build_scene_index(scene: GeometryScene) -> SceneFingerprintIndex:
    """Build the fingerprint index for a scene in a single pass (cached).

    Entity payloads are computed once and reused both for the per-entity
    fingerprints and the scene-level fingerprint, halving the fingerprint cost
    for typical multi-reference projects.  The result is cached per scene
    object; see the module contract for mutability guarantees.
    """
    cached = _SCENE_INDEX_CACHE.get(scene)
    if cached is not None:
        return cached
    index = _build_scene_index_uncached(scene)
    _SCENE_INDEX_CACHE.put(scene, index)
    return index


def invalidate_scene_index_cache() -> None:
    """Drop every cached index (used on attach and in tests)."""
    _SCENE_INDEX_CACHE.clear()


def _build_scene_index_uncached(scene: GeometryScene) -> SceneFingerprintIndex:
    """Compute the index without consulting the cache."""
    if scene.tolerance_mm <= 0:
        raise ValueError("tolerance_mm must be positive")
    layers_payload: list[dict[str, Any]] = []
    entity_fingerprints: dict[tuple[str, int], str] = {}
    for layer in scene.layers:
        entities_payload: list[dict[str, Any]] = []
        for index, entity in enumerate(layer.entities):
            payload = _entity_payload(entity, scene.tolerance_mm)
            entities_payload.append(payload)
            entity_fingerprints[(layer.name, index)] = _fingerprint(payload)
        layers_payload.append({"name": layer.name, "entities": entities_payload})
    scene_payload = {
        "layers": layers_payload,
        "tolerance": _quantize(scene.tolerance_mm, scene.tolerance_mm),
        "units": scene.units.value,
    }
    return SceneFingerprintIndex(scene_fingerprint=_fingerprint(scene_payload), entity_fingerprints=entity_fingerprints)


def bind_geometry(scene: GeometryScene) -> GeometryBinding:
    """Create persistent source provenance for an attached transient scene."""
    return GeometryBinding(source=scene.source.model_copy(deep=True), scene_fingerprint=scene_fingerprint(scene))


def create_geometry_ref(scene: GeometryScene, layer_name: str, entity_index: int) -> GeometryRef:
    """Build a validated persistent reference from one entity selected in a scene."""
    try:
        layer = scene.layer(layer_name, create=False)
    except KeyError as exc:
        raise GeometryReferenceError(f"geometry layer not found: {layer_name}") from exc
    try:
        entity = layer.entities[entity_index]
    except IndexError as exc:
        raise GeometryReferenceError(f"geometry entity index out of bounds: {layer_name}[{entity_index}]") from exc
    return GeometryRef(
        layer_name=layer_name,
        entity_index=entity_index,
        entity_type=type(entity).__name__,
        entity_fingerprint=entity_fingerprint(entity, scene.tolerance_mm),
    )


def resolve_geometry_ref(
    reference: GeometryRef,
    scene: GeometryScene,
    index: SceneFingerprintIndex | None = None,
) -> SceneEntity:
    """Resolve ``reference`` without ever retargeting changed geometry.

    The saved index is a fast path only: with a precomputed index the lookup is
    O(1).  If the indexed entity no longer matches exactly, the resolver accepts
    exactly one fingerprint match in the same layer.  Missing or multiple
    matching entities are stale and must be resolved by the user.
    """
    try:
        layer = scene.layer(reference.layer_name, create=False)
    except KeyError as exc:
        raise GeometryReferenceError(f"stale geometry reference: layer {reference.layer_name!r} is missing") from exc

    if index is not None:
        if reference.entity_index < len(layer.entities):
            fingerprint = index.entity_fingerprint(reference.layer_name, reference.entity_index)
            if (
                fingerprint is not None
                and fingerprint == reference.entity_fingerprint
                and _type_matches(reference, layer.entities[reference.entity_index])
            ):
                return layer.entities[reference.entity_index]
        matches = [
            entity
            for entity_index, entity in enumerate(layer.entities)
            if index.entity_fingerprint(reference.layer_name, entity_index) == reference.entity_fingerprint
            and _type_matches(reference, entity)
        ]
    else:
        if reference.entity_index < len(layer.entities):
            indexed = layer.entities[reference.entity_index]
            if _matches(reference, indexed, scene.tolerance_mm):
                return indexed
        matches = [entity for entity in layer.entities if _matches(reference, entity, scene.tolerance_mm)]

    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise GeometryReferenceError(
            f"stale geometry reference: {reference.layer_name}[{reference.entity_index}] no longer matches"
        )
    raise GeometryReferenceError(
        f"ambiguous geometry reference: {reference.layer_name}[{reference.entity_index}] has {len(matches)} matches"
    )


def _type_matches(reference: GeometryRef, entity: SceneEntity) -> bool:
    return type(entity).__name__ == reference.entity_type


def _matches(reference: GeometryRef, entity: SceneEntity, tolerance_mm: float) -> bool:
    return _type_matches(reference, entity) and entity_fingerprint(entity, tolerance_mm) == reference.entity_fingerprint


def _entity_payload(entity: SceneEntity, tolerance_mm: float) -> dict[str, Any]:
    if isinstance(entity, LineSegment):
        return {
            "type": "LineSegment",
            "start": _point_payload(entity.start, tolerance_mm),
            "end": _point_payload(entity.end, tolerance_mm),
        }
    if isinstance(entity, Circle):
        return {
            "type": "Circle",
            "center": _point_payload(entity.center, tolerance_mm),
            "radius": _quantize(entity.radius, tolerance_mm),
        }
    if isinstance(entity, Arc):
        return {
            "type": "Arc",
            "center": _point_payload(entity.center, tolerance_mm),
            "radius": _quantize(entity.radius, tolerance_mm),
            "start_angle": _quantize(entity.start_angle, tolerance_mm),
            "end_angle": _quantize(entity.end_angle, tolerance_mm),
            "ccw": entity.ccw,
        }
    if isinstance(entity, Contour):
        return {"type": "Contour", "segments": [_curve_payload(segment, tolerance_mm) for segment in entity.segments]}
    if isinstance(entity, Path):
        return {
            "type": "Path",
            "contours": [
                {"segments": [_curve_payload(segment, tolerance_mm) for segment in contour.segments]}
                for contour in entity.contours
            ],
        }
    raise TypeError(f"unsupported scene entity type for fingerprinting: {type(entity).__name__}")


def _curve_payload(curve: Curve2, tolerance_mm: float) -> dict[str, Any]:
    return _entity_payload(curve, tolerance_mm)


def _point_payload(point, tolerance_mm: float) -> list[int]:
    return [_quantize(point.x, tolerance_mm), _quantize(point.y, tolerance_mm)]


def _quantize(value: float, tolerance_mm: float) -> int:
    return round(value / tolerance_mm)


def _fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
