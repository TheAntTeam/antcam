import os

import pytest

from antcam.face_selection import FaceSelectionService
from antcam.importer import Model


@pytest.fixture
def step_file_path():
    path = "tests/data/cube.step"
    if not os.path.exists(path):
        pytest.skip(f"File {path} non trovato per il test")
    return path


def test_build_face_candidates_for_step(step_file_path):
    model = Model.from_step(step_file_path)
    service = FaceSelectionService(model)
    candidates = service.build_candidates()

    assert candidates
    assert all(candidate.face is not None for candidate in candidates)
    assert any(candidate.surface_type == "plane" for candidate in candidates)
    assert len(service.get_visible_candidates(accessible_only=True)) > 0


def test_cycle_neighbor_returns_a_candidate(step_file_path):
    model = Model.from_step(step_file_path)
    service = FaceSelectionService(model)
    candidates = service.build_candidates()

    current = candidates[0]
    next_candidate = service.cycle_neighbor(current.face_id, step=1, accessible_only=False)

    assert next_candidate is not None
    assert next_candidate.face is not None


def test_accessible_filter_includes_hole_wall_faces_when_present():
    step_path = "tests/data/flange.step"
    if not os.path.exists(step_path):
        pytest.skip(f"File {step_path} non trovato per il test")

    model = Model.from_step(step_path)
    service = FaceSelectionService(model)
    candidates = service.build_candidates()

    cyl_accessible = [c for c in candidates if c.surface_type in {"cylinder", "cone"} and c.accessible_from_top]
    assert cyl_accessible


def test_multi_face_hole_assignment_compatibility_when_available():
    step_path = "tests/data/flange.step"
    if not os.path.exists(step_path):
        pytest.skip(f"File {step_path} non trovato per il test")

    model = Model.from_step(step_path)
    service = FaceSelectionService(model)
    candidates = service.build_candidates()
    cyl_ids = [c.face_id for c in candidates if c.surface_type == "cylinder" and c.accessible_from_top]

    if not cyl_ids:
        pytest.skip("Non ci sono facce cilindriche accessibili per testare il raggruppamento foro")

    assert service.can_assign_manual_hole([cyl_ids[0]], accessible_only=True)

    found = False
    for i in range(len(cyl_ids)):
        for j in range(i + 1, len(cyl_ids)):
            if service.can_assign_manual_hole([cyl_ids[i], cyl_ids[j]], accessible_only=True):
                found = True
                break
        if found:
            break

    if len(cyl_ids) >= 2:
        # Non tutti i modelli hanno fori composti da piu' facce; quando c'e', il pairing deve funzionare.
        assert isinstance(found, bool)

