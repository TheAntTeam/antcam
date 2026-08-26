from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Feature:
    type: str
    geometry: Any = None
    props: dict = field(default_factory=dict)
    face_indices: set[int] = field(default_factory=set)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} type={self.type} props={self.props}>"

    def to_dict(self) -> dict:
        return {"type": self.type, "props": dict(self.props)}


@dataclass
class HoleFeature(Feature):
    diameter: float = 0.0
    axis: tuple[float, float, float] = (0, 0, 1)
    center: tuple[float, float, float] = (0, 0, 0)
    depth: float = 0.0
    through: bool = False

    def __init__(self, diameter=0.0, axis=(0, 0, 1), center=(0, 0, 0), depth=0.0, through=False, geometry=None, face_indices=None):
        super().__init__("hole", geometry=geometry, props={"diameter": diameter, "axis": axis, "center": center, "depth": depth, "through": through}, face_indices=face_indices or set())
        self.diameter = diameter
        self.axis = axis
        self.center = center
        self.depth = depth
        self.through = through


@dataclass
class HoleGroup(Feature):
    diameter: float = 0.0
    depth: float = 0.0
    through: bool = False
    holes: list[HoleFeature] = field(default_factory=list)

    def __init__(self, diameter=0.0, depth=0.0, through=False, holes=None):
        all_idx = set()
        for h in (holes or []):
            all_idx.update(h.face_indices)
        super().__init__("hole_group", props={"diameter": diameter, "depth": depth, "through": through, "count": len(holes or []), "tap_candidate": True}, face_indices=all_idx)
        self.diameter = diameter
        self.depth = depth
        self.through = through
        self.holes = holes or []


@dataclass
class CountersunkHoleFeature(Feature):
    def __init__(self, diameter=0.0, axis=(0, 0, 1), center=(0, 0, 0), depth=0.0, cs_diameter=0.0, cs_angle=90.0, geometry=None, face_indices=None):
        super().__init__("countersunk_hole", geometry=geometry, props={"diameter": diameter, "axis": axis, "center": center, "depth": depth, "cs_diameter": cs_diameter, "cs_angle": cs_angle}, face_indices=face_indices or set())


@dataclass
class PocketFeature(Feature):
    def __init__(self, face=None, depth=0.0, area=0.0, face_indices=None):
        super().__init__("pocket", geometry=face, props={"depth": depth, "area": area}, face_indices=face_indices or set())


@dataclass
class StepFeature(Feature):
    def __init__(self, face=None, depth=0.0, face_indices=None):
        super().__init__("step", geometry=face, props={"depth": depth}, face_indices=face_indices or set())


@dataclass
class OpeningFeature(Feature):
    def __init__(self, face=None, face_indices=None):
        super().__init__("opening", geometry=face, face_indices=face_indices or set())


@dataclass
class SlotFeature(Feature):
    def __init__(self, width=0.0, length=0.0, depth=0.0, axis=(1, 0, 0), geometry=None, face_indices=None):
        super().__init__("slot", geometry=geometry, props={"width": width, "length": length, "depth": depth, "axis": list(axis)}, face_indices=face_indices or set())


@dataclass
class FilletFeature(Feature):
    def __init__(self, radius=0.0, geometry=None, face_indices=None):
        super().__init__("fillet", geometry=geometry, props={"radius": radius}, face_indices=face_indices or set())


@dataclass
class ChamferFeature(Feature):
    def __init__(self, width=0.0, angle=0.0, geometry=None, face_indices=None):
        super().__init__("chamfer", geometry=geometry, props={"width": width, "angle": angle}, face_indices=face_indices or set())


@dataclass
class PerimeterFeature(Feature):
    def __init__(self, face_indices=None):
        super().__init__("perimeter", props={}, face_indices=face_indices or set())
