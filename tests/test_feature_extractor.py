import math
import os
from types import SimpleNamespace

import numpy as np
import pytest

from antcam.feature_extractor import ChamferFeature, CountersunkHoleFeature, FeatureExtractor, HoleFeature, OpeningFeature, RecognitionTolerances, StepFeature
from antcam.importer import Model

@pytest.fixture
def step_file_path():
    """Return the reference STEP sample path for feature extraction tests."""
    path = "tests/data/cube.step"
    if not os.path.exists(path):
        pytest.skip(f"Test fixture not found: {path}")
    return path


@pytest.fixture
def stl_file_path():
    """Return the reference STL sample path for mesh-only extraction tests."""
    path = "tests/data/cube.stl"
    if not os.path.exists(path):
        pytest.skip(f"Test fixture not found: {path}")
    return path


def test_extract_from_real_step(step_file_path):
    """STEP input should still produce at least one semantic feature."""
    model = Model.from_step(step_file_path)
    extractor = FeatureExtractor(model)
    features = extractor.extract()

    assert len(features) > 0


def test_extract_from_real_stl_returns_no_brep_features(stl_file_path):
    """STL input should stay mesh-only and skip BRep semantic extraction."""
    model = Model.from_stl(stl_file_path)
    extractor = FeatureExtractor(model)
    features = extractor.extract()

    assert features == []


def test_extract_skips_brep_pipeline_for_mesh_only_model():
    """Models without a BRep should short-circuit cleanly."""
    class MockModel:
        def __init__(self, brep=None, mesh=None):
            self.brep = brep
            self.mesh = mesh

    extractor = FeatureExtractor(MockModel(mesh=object()))

    assert extractor.extract() == []
    assert extractor._arc_groups == []
    assert extractor.diagnostics["skip_reason"] == "no_brep"
    assert extractor.diagnostics["feature_count"] == 0
    assert extractor.diagnostics["feature_types"] == {}


def test_composite_feature_logic_mock():
    """Coaxial cylinder and cone data should form a countersunk-hole feature."""
    class MockModel:
        def __init__(self, brep=None, mesh=None):
            self.brep = brep
            self.mesh = mesh

    extractor = FeatureExtractor(MockModel())

    cylinders = [{
        "radius": 5.0,
        "axis": (0, 0, 1),
        "center": (0, 0, 0),
        "depth": 10.0,
        "face": None
    }]
    
    cones = [{
        "angle": 45.0,
        "radii": (5.0, 8.0),
        "axis": (0, 0, 1),
        "center": (0, 0, 10.0),
        "v_range": (0, 5),
        "face": None
    }]

    extractor._group_composite_features(cylinders, cones)

    assert any(isinstance(f, CountersunkHoleFeature) for f in extractor.features)
    cs_hole = next(f for f in extractor.features if isinstance(f, CountersunkHoleFeature))
    assert cs_hole.props["diameter"] == 10.0
    assert cs_hole.props["cs_diameter"] == 16.0


def test_group_holes_uses_configured_tolerances():
    """Hole grouping should honor injected tolerances instead of hidden constants."""
    extractor = FeatureExtractor(
        SimpleNamespace(brep=None, mesh=None),
        tolerances=RecognitionTolerances(hole_group_diameter=0.01, hole_group_depth=0.1),
    )
    extractor.features = [
        HoleFeature(diameter=10.0, axis=(0.0, 0.0, 1.0), center=(0.0, 0.0, 0.0), depth=5.0, through=False),
        HoleFeature(diameter=10.05, axis=(0.0, 0.0, 1.0), center=(1.0, 0.0, 0.0), depth=5.05, through=False),
    ]

    extractor._group_holes()

    hole_groups = [feature for feature in extractor.features if feature.type == "hole_group"]
    assert len(hole_groups) == 2


def test_half_cylindrical_span_uses_configured_tolerance():
    """Half-cylinder detection should honor the injected angular tolerance."""
    extractor = FeatureExtractor(
        SimpleNamespace(brep=None, mesh=None),
        tolerances=RecognitionTolerances(half_cylinder_angle_tolerance=0.01),
    )

    assert extractor._is_half_cylindrical_span(math.pi + 0.005) is True
    assert extractor._is_half_cylindrical_span(math.pi + 0.02) is False


def test_chamfer_angle_candidate_uses_configured_bounds():
    """Chamfer angle filtering should honor custom dot-product bounds."""
    extractor = FeatureExtractor(
        SimpleNamespace(brep=None, mesh=None),
        tolerances=RecognitionTolerances(chamfer_dot_min=0.25, chamfer_dot_max=0.75),
    )

    assert extractor._is_chamfer_angle_candidate(0.2) is False
    assert extractor._is_chamfer_angle_candidate(0.5) is True
    assert extractor._is_chamfer_angle_candidate(0.8) is False


def test_slot_half_pairing_uses_configured_distance_thresholds(monkeypatch):
    """Slot-half pairing should honor the injected center-distance limits."""
    extractor = FeatureExtractor(
        SimpleNamespace(brep=None, mesh=None),
        tolerances=RecognitionTolerances(slot_center_distance_max=4.0),
    )
    monkeypatch.setattr(extractor, "_cylinder_has_bottom", lambda face: False)

    extractor._append_slot_features_from_slot_halves([
        {"radius": 2.0, "axis": (1.0, 0.0, 0.0), "center": (0.0, 0.0, 0.0), "depth": 3.0, "face": None},
        {"radius": 2.0, "axis": (1.0, 0.0, 0.0), "center": (10.0, 0.0, 0.0), "depth": 3.0, "face": None},
    ])

    assert not any(feature.type == "slot" for feature in extractor.features)


def test_arc_forward_slot_uses_center_delta_as_slot_axis(monkeypatch):
    """Arc-forward slot grouping should store an in-plane slot direction usable by the planner."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))
    monkeypatch.setattr(extractor, "_cylinder_has_bottom", lambda face: False)

    extractor._append_arc_forward_group_feature(
        [
            {"radius": 2.0, "axis": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 0.0), "depth": 3.0, "u_range": 1.0, "face": None},
            {"radius": 2.0, "axis": (0.0, 0.0, 1.0), "center": (10.0, 0.0, 0.0), "depth": 3.0, "u_range": 1.0, "face": None},
        ]
    )

    slot = next(feature for feature in extractor.features if feature.type == "slot")
    assert slot.props["axis"] == (1.0, 0.0, 0.0)
    assert slot.props["length"] == 14.0


def test_countersink_matching_uses_configured_center_slack():
    """Countersink pairing should honor the injected cylinder-to-cone slack distance."""
    extractor = FeatureExtractor(
        SimpleNamespace(brep=None, mesh=None),
        tolerances=RecognitionTolerances(countersink_center_distance_slack=0.1),
    )

    extractor._append_countersinks_and_fillets(
        [
            (
                0,
                {
                    "type": "cylinder",
                    "radius": 5.0,
                    "axis": (0.0, 0.0, 1.0),
                    "center": (0.0, 0.0, 0.0),
                    "depth": 1.0,
                    "face": None,
                    "is_fillet": False,
                },
            )
        ],
        [
            {
                "angle": 45.0,
                "radii": (5.0, 8.0),
                "axis": (0.0, 0.0, 1.0),
                "center": (0.0, 0.0, 2.0),
                "face": None,
            }
        ],
    )

    assert not any(isinstance(feature, CountersunkHoleFeature) for feature in extractor.features)


def test_partition_cylindrical_candidates_defaults_missing_type_to_cylinder():
    """Composite grouping should treat untyped cylindrical mocks as regular cylinders."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    slot_halves, arc_forwards, normal_cyls = extractor._partition_cylindrical_candidates([
        {"radius": 5.0, "axis": (0.0, 0.0, 1.0), "center": (0.0, 0.0, 0.0), "depth": 3.0, "face": None},
        {"type": "arc_forward", "radius": 6.0, "axis": (0.0, 0.0, 1.0), "center": (1.0, 0.0, 0.0), "depth": 4.0, "u_range": 1.0, "face": None},
        {"type": "slot_half", "radius": 4.0, "axis": (1.0, 0.0, 0.0), "center": (0.0, 1.0, 0.0), "depth": 2.0, "face": None},
    ])

    assert len(slot_halves) == 1
    assert len(arc_forwards) == 1
    assert len(normal_cyls) == 1
    assert normal_cyls[0][1]["radius"] == 5.0


def test_extract_arc_group_features_updates_diagnostics(monkeypatch):
    """The dedicated arc-group stage should both track diagnostics and forward grouped arcs."""
    extractor = FeatureExtractor(SimpleNamespace(brep=object(), mesh=None))
    seen = {}

    monkeypatch.setattr(extractor, "find_vertical_faces_with_xy_arcs", lambda: [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(extractor, "_find_holes_from_arc_groups", lambda groups: seen.setdefault("groups", groups))

    arc_groups = extractor._extract_arc_group_features()

    assert arc_groups == [{"id": 1}, {"id": 2}]
    assert extractor.diagnostics["raw_candidates"]["arc_groups"] == 2
    assert seen["groups"] == arc_groups


def test_axis_aligned_cylindrical_face_keeps_partial_forward_arcs_reachable(monkeypatch):
    """Partial forward-facing aligned cylinders should emit arc-forward candidates."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    monkeypatch.setattr(extractor, "_is_reversed_face", lambda face: False)
    monkeypatch.setattr(extractor, "_is_forward_face", lambda face: True)

    candidate = extractor._analyze_axis_aligned_cylindrical_face(
        face=object(),
        surf=None,
        radius=4.0,
        axis_dir=np.array([0.0, 0.0, 1.0]),
        center=(1.0, 2.0, 3.0),
        depth=6.0,
        u_range=1.2,
    )

    assert candidate["type"] == "arc_forward"
    assert candidate["u_range"] == 1.2
    assert candidate["center"] == (1.0, 2.0, 3.0)


def test_axis_aligned_cylindrical_face_uses_bottom_heuristic_for_full_holes(monkeypatch):
    """Full aligned cylinders should still derive through/blind state from the bottom heuristic."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    monkeypatch.setattr(extractor, "_cylinder_has_real_bottom", lambda face, surf: True)

    candidate = extractor._analyze_axis_aligned_cylindrical_face(
        face=object(),
        surf=object(),
        radius=5.0,
        axis_dir=np.array([0.0, 0.0, 1.0]),
        center=(0.0, 0.0, 0.0),
        depth=10.0,
        u_range=2 * math.pi,
    )

    assert candidate["type"] == "cylinder"
    assert candidate["is_fillet"] is False
    assert candidate["through"] is False


def test_in_plane_cylindrical_face_emits_slot_half_for_reversed_semicylinder(monkeypatch):
    """Half-cylinders in the working plane should stay isolated as slot-half candidates."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    monkeypatch.setattr(extractor, "_is_reversed_face", lambda face: True)

    candidate = extractor._analyze_in_plane_cylindrical_face(
        face=object(),
        radius=3.0,
        axis_dir=np.array([1.0, 0.0, 0.0]),
        center=(2.0, 0.0, 0.0),
        depth=4.0,
        u_range=math.pi,
    )

    assert candidate["type"] == "slot_half"
    assert candidate["depth"] == 4.0


def test_count_horizontal_cylinder_caps_aggregates_bottom_and_top(monkeypatch):
    """Cylinder bottom detection should keep scan counts separate for bottom and top cap faces."""
    extractor = FeatureExtractor(SimpleNamespace(brep=object(), mesh=None))

    monkeypatch.setattr(extractor, "_get_faces", lambda shape: ["bottom", "top", "other"])
    monkeypatch.setattr(extractor, "_get_face_surface", lambda face: face)
    monkeypatch.setattr(
        extractor,
        "_classify_cylinder_cap_surface",
        lambda surf, proj_bot, proj_top: {"bottom": "bottom", "top": "top", "other": None}[surf],
    )

    bottom_caps, top_caps = extractor._count_horizontal_cylinder_caps(1.0, 4.0)

    assert bottom_caps == 1
    assert top_caps == 1


def test_has_internal_cylinder_bottom_requires_bottom_without_top():
    """Blind-cylinder decision should require an internal bottom cap and no top cap."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))
    extractor._min_proj_z = 0.0

    assert extractor._has_internal_cylinder_bottom(bottom_caps=1, top_caps=0, proj_bot=3.0) is True
    assert extractor._has_internal_cylinder_bottom(bottom_caps=0, top_caps=0, proj_bot=3.0) is False
    assert extractor._has_internal_cylinder_bottom(bottom_caps=1, top_caps=1, proj_bot=3.0) is False
    assert extractor._has_internal_cylinder_bottom(bottom_caps=1, top_caps=0, proj_bot=0.0) is False


def test_arc_group_hole_depth_uses_bbox_when_only_one_complete_level_exists():
    """Arc-group hole depth should fall back to the model top when only one full circle is present."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))
    extractor._max_proj_z = 12.0

    assert extractor._get_arc_group_hole_depth([3.0]) == 9.0
    assert extractor._get_arc_group_hole_depth([3.0, 7.5]) == 4.5


def test_arc_group_hole_through_depends_on_bottom_cap_faces(monkeypatch):
    """Arc-group through classification should depend on whether bottom caps are present."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    monkeypatch.setattr(extractor, "_get_cap_faces_at_level", lambda cap_faces, z_level: ["cap"])
    assert extractor._is_arc_group_hole_through(["cap"], [2.0, 5.0]) is False

    monkeypatch.setattr(extractor, "_get_cap_faces_at_level", lambda cap_faces, z_level: [])
    assert extractor._is_arc_group_hole_through(["cap"], [2.0, 5.0]) is True


def test_matches_hole_level_normalizes_near_zero_values():
    """Hole-level matching should collapse numerical noise around zero before comparing Z levels."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    assert extractor._matches_hole_level(-1e-10, 0.0) is True
    assert extractor._matches_hole_level(0.5, 0.0) is False


def test_cap_face_is_linked_to_hole_uses_bottom_interior_fallback(monkeypatch):
    """Cap-face linking should fall back to an interior test on the bottom Z when shared edges are missing."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    monkeypatch.setattr(extractor, "_cap_face_shares_cylinder_edge", lambda face, cyl_edge_indices: False)
    monkeypatch.setattr(extractor, "_project_planar_face_origin", lambda face_surface: 1.0)
    monkeypatch.setattr(extractor, "_matches_hole_level", lambda face_proj, z_level: True)
    monkeypatch.setattr(
        extractor,
        "_cap_face_passes_bottom_interior_test",
        lambda face, face_proj, center_xy, radius: True,
    )

    assert extractor._cap_face_is_linked_to_hole(
        face="cap",
        face_surface=object(),
        center_xy=np.array([0.0, 0.0]),
        radius=2.0,
        z_bot=1.0,
        cyl_edge_indices={1, 2},
    ) is True


def test_build_hole_feature_from_arc_group_skips_forward_faces(monkeypatch):
    """Arc-group hole building should reject external forward-oriented cylindrical faces."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    monkeypatch.setattr(extractor, "_find_tool_aligned_group_cylinder_face", lambda faces: object())
    monkeypatch.setattr(extractor, "_is_forward_face", lambda face: True)

    feature = extractor._build_hole_feature_from_arc_group(
        {
            "complete_z": {0.0: 2 * math.pi},
            "faces": [object()],
            "radius": 5.0,
            "center_xy": np.array([0.0, 0.0]),
        }
    )

    assert feature is None


def test_plane_has_floor_returns_false_for_open_vertical_neighbor(monkeypatch):
    """Planar floor detection should classify a cavity as through when a vertical wall is open."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    monkeypatch.setattr(extractor, "_iter_adjacent_faces", lambda face: ["wall", "non_wall"])
    monkeypatch.setattr(extractor, "_get_face_surface", lambda face: face)
    monkeypatch.setattr(extractor, "_is_vertical_planar_surface", lambda surf: surf == "wall")
    monkeypatch.setattr(extractor, "_face_has_open_boundary", lambda face: face == "wall")

    assert extractor._plane_has_floor("floor", proj_z=0.0) is False


def test_plane_has_floor_returns_true_when_vertical_neighbors_are_closed(monkeypatch):
    """Planar floor detection should keep a cavity blind when vertical wall boundaries are closed."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    monkeypatch.setattr(extractor, "_iter_adjacent_faces", lambda face: ["wall"])
    monkeypatch.setattr(extractor, "_get_face_surface", lambda face: face)
    monkeypatch.setattr(extractor, "_is_vertical_planar_surface", lambda surf: surf == "wall")
    monkeypatch.setattr(extractor, "_face_has_open_boundary", lambda face: False)

    assert extractor._plane_has_floor("floor", proj_z=0.0) is True


def test_get_cone_opening_axis_flips_toward_wider_end():
    """Cone opening normalization should point toward the widening side."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    opening_axis = extractor._get_cone_opening_axis(np.array([0.0, 0.0, 1.0]), (6.0, 4.0))

    assert tuple(opening_axis) == (0.0, 0.0, -1.0)


def test_cone_opens_toward_tool_uses_parallel_tolerance():
    """Cone alignment should honor the same axis-parallel tolerance bundle used elsewhere."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    assert extractor._cone_opens_toward_tool(np.array([0.0, 0.0, 1.0])) is True
    assert extractor._cone_opens_toward_tool(np.array([0.0, 0.0, -1.0])) is False


def test_build_horizontal_planar_feature_returns_step_for_open_edges():
    """Horizontal planar helpers should still emit a step when the face is open."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))
    extractor._max_proj_z = 12.0

    feature = extractor._build_horizontal_planar_feature(
        face="floor",
        proj_z=5.0,
        open_edges=2,
        normal=np.array([0.0, 0.0, 1.0]),
    )

    assert isinstance(feature, StepFeature)
    assert feature.props["depth"] == 7.0
    assert feature.props["open_edges"] == 2


def test_build_horizontal_planar_feature_returns_opening_without_floor(monkeypatch):
    """Horizontal planar helpers should emit an opening when no floor closes the cavity."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))
    extractor._max_proj_z = 10.0
    monkeypatch.setattr(extractor, "_plane_has_floor", lambda face, proj_z: False)

    feature = extractor._build_horizontal_planar_feature(
        face="floor",
        proj_z=4.0,
        open_edges=0,
        normal=np.array([0.0, 0.0, 1.0]),
    )

    assert isinstance(feature, OpeningFeature)
    assert feature.props["depth"] == 6.0
    assert feature.props["through"] is True


def test_is_chamfer_angle_candidate_enforces_bounds():
    """Chamfer angle filtering should reject near-horizontal and near-vertical planes."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    assert extractor._is_chamfer_angle_candidate(0.50) is True
    assert extractor._is_chamfer_angle_candidate(0.10) is False
    assert extractor._is_chamfer_angle_candidate(0.99) is False


def test_build_chamfer_feature_uses_shorter_surface_param_span():
    """Chamfer width should remain driven by the tighter U/V extent of the face."""
    extractor = FeatureExtractor(SimpleNamespace(brep=None, mesh=None))

    class FakeSurf:
        def FirstUParameter(self):
            return 0.0

        def LastUParameter(self):
            return 8.0

        def FirstVParameter(self):
            return 2.0

        def LastVParameter(self):
            return 5.5

    feature = extractor._build_chamfer_feature(face="chamfer", surf=FakeSurf(), dot=0.5)

    assert isinstance(feature, ChamferFeature)
    assert round(feature.props["angle"], 6) == round(60.0, 6)
    assert feature.props["width"] == 3.5


def test_extract_populates_structured_diagnostics(monkeypatch):
    """Extraction diagnostics should summarize raw candidates and final semantic output."""
    extractor = FeatureExtractor(SimpleNamespace(brep=object(), mesh=None))

    monkeypatch.setattr(extractor, "_prepare_brep_context", lambda: None)

    def _fake_collect_surface_candidates():
        extractor.features.append(SimpleNamespace(type="pocket", props={}, geometry=None))
        return [{"id": "cylinder_1"}], [{"id": "cone_1"}, {"id": "cone_2"}]

    monkeypatch.setattr(extractor, "_collect_surface_candidates", _fake_collect_surface_candidates)
    monkeypatch.setattr(
        extractor,
        "_group_composite_features",
        lambda cylinders, cones: extractor.features.append(SimpleNamespace(type="hole_group", props={}, geometry=None)),
    )
    monkeypatch.setattr(extractor, "find_vertical_faces_with_xy_arcs", lambda: [{"id": 1}, {"id": 2}, {"id": 3}])
    monkeypatch.setattr(extractor, "_find_holes_from_arc_groups", lambda arc_groups: None)
    monkeypatch.setattr(extractor, "_group_holes", lambda: None)

    features = extractor.extract()

    assert len(features) == 2
    assert extractor.diagnostics["has_brep"] is True
    assert extractor.diagnostics["skip_reason"] is None
    assert extractor.diagnostics["raw_candidates"] == {"cylinders": 1, "cones": 2, "arc_groups": 3}
    assert extractor.diagnostics["pre_group_feature_count"] == 1
    assert extractor.diagnostics["feature_count"] == 2
    assert extractor.diagnostics["feature_types"] == {"hole_group": 1, "pocket": 1}
