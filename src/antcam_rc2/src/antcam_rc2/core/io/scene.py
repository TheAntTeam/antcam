"""Neutral geometry scene produced by importers.

A :class:`GeometryScene` is the single public output of the import pipeline:
entities organised on layers, with resolved units, provenance and
diagnostics.  Coordinates are expected to be in millimetres after
:meth:`GeometryScene.normalize`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from antcam_rc2.core.geometry.curves import Arc, Circle, Curve2
from antcam_rc2.core.geometry.paths import Contour, Path
from antcam_rc2.core.geometry.primitives import Box2
from antcam_rc2.core.io.diagnostics import ImportDiagnostics
from antcam_rc2.core.units import UnitSystem

__all__ = ["SourceInfo", "SceneLayer", "GeometryScene"]

SceneEntity = Curve2 | Contour | Path


class SourceInfo(BaseModel):
    """Provenance metadata about the imported file."""

    model_config = ConfigDict(extra="forbid")

    format: str
    path: str = ""
    original_units: UnitSystem | None = None
    version: str = ""


class SceneLayer(BaseModel):
    """A named layer holding geometry entities."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    color: str | None = None
    entities: list[SceneEntity] = Field(default_factory=list)


class GeometryScene(BaseModel):
    """The neutral, validated representation of an imported drawing.

    Attributes:
        source: provenance of the file.
        units: resolved unit system (metric = millimetres).
        layers: named layers in insertion order.
        diagnostics: warnings and errors collected during import.
        tolerance_mm: geometric tolerance used for welding/validation.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: SourceInfo
    units: UnitSystem = UnitSystem.METRIC
    layers: list[SceneLayer] = Field(default_factory=list)
    diagnostics: ImportDiagnostics = Field(default_factory=ImportDiagnostics)
    tolerance_mm: float = 1e-6

    def layer(self, name: str, *, create: bool = True) -> SceneLayer:
        """Return the layer with ``name``, creating it if needed."""
        for layer in self.layers:
            if layer.name == name:
                return layer
        if not create:
            raise KeyError(f"Layer {name!r} not found")
        new_layer = SceneLayer(name=name)
        self.layers.append(new_layer)
        return new_layer

    def add_entity(self, entity: SceneEntity, layer_name: str = "0") -> None:
        """Append an entity to the given layer."""
        self.layer(layer_name).entities.append(entity)

    def iter_entities(self):
        """Yield ``(layer_name, entity)`` pairs in layer/insertion order."""
        for layer in self.layers:
            for entity in layer.entities:
                yield layer.name, entity

    def entities(self) -> list[SceneEntity]:
        """All entities across every layer."""
        return [entity for _, entity in self.iter_entities()]

    def bounding_box(self) -> Box2:
        """The overall axis-aligned bounding box of all entities.

        Returns an empty :class:`Box2` for an empty scene.
        """
        boxes: list[Box2] = []
        for _, entity in self.iter_entities():
            if isinstance(entity, (Path, Contour)):
                boxes.append(entity.bounding_box())
            elif isinstance(entity, Circle):
                boxes.append(
                    Box2(
                        entity.center.x - entity.radius,
                        entity.center.y - entity.radius,
                        entity.center.x + entity.radius,
                        entity.center.y + entity.radius,
                    )
                )
            elif isinstance(entity, Arc):
                boxes.append(
                    Box2(
                        entity.center.x - entity.radius,
                        entity.center.y - entity.radius,
                        entity.center.x + entity.radius,
                        entity.center.y + entity.radius,
                    )
                )
            else:
                start = entity.start
                end = entity.end
                boxes.append(
                    Box2(
                        min(start.x, end.x),
                        min(start.y, end.y),
                        max(start.x, end.x),
                        max(start.y, end.y),
                    )
                )
        if not boxes:
            return Box2()
        result = boxes[0]
        for box in boxes[1:]:
            result = result.union(box)
        return result

    def apply_affine(self, affine) -> GeometryScene:
        """Return a new scene with ``affine`` applied (non-destructive, for placement)."""
        from antcam_rc2.core.geometry.transform import apply

        new_layers: list[SceneLayer] = []
        for layer in self.layers:
            new_entities = [apply(affine, entity) for entity in layer.entities]
            new_layers.append(SceneLayer(name=layer.name, color=layer.color, entities=new_entities))
        return GeometryScene(
            source=self.source,
            units=self.units,
            layers=new_layers,
            diagnostics=self.diagnostics,
            tolerance_mm=self.tolerance_mm,
        )

    def translate(self, dx: float, dy: float) -> None:
        """Translate every entity by ``(dx, dy)`` millimetres (in place).

        Used to centre an imported drawing inside the project stock; the
        transform is applied to the source geometry so toolpaths and the
        render graph stay consistent.
        """
        from antcam_rc2.core.geometry.transform import Affine2D, apply

        affine = Affine2D.translate(dx, dy)
        for layer in self.layers:
            layer.entities = [apply(affine, entity) for entity in layer.entities]

    def normalize(self) -> None:
        """Normalise the scene to millimetres.

        Converts coordinates when the source units are imperial, and (in
        place) updates ``units`` to :attr:`UnitSystem.METRIC`.
        """
        if self.units is UnitSystem.IMPERIAL:
            self._scale(UnitSystem.IMPERIAL)
            self.units = UnitSystem.METRIC

    def _scale(self, from_units: UnitSystem) -> None:
        from antcam_rc2.core.units import convert

        if from_units is UnitSystem.METRIC:
            return
        factor = convert(1.0, from_units, UnitSystem.METRIC)

        def rescale(entity: Any) -> SceneEntity:
            from antcam_rc2.core.geometry.transform import Affine2D, apply

            return apply(Affine2D.scale(factor), entity)  # type: ignore[arg-type]

        for layer in self.layers:
            layer.entities = [rescale(e) for e in layer.entities]
