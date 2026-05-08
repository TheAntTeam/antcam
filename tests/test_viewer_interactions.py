from types import SimpleNamespace

import antcam.viewer as viewer_module
from antcam.viewer import AntCamViewerWindow


class _FakeStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, msg):
        self.messages.append(msg)


class _FakeOcpWidget:
    def __init__(self, detected_faces):
        self._detected_faces = detected_faces

    def get_detected_faces(self, pos=None):
        return list(self._detected_faces)


class _FakeSelectionService:
    def __init__(self, by_shape=None, by_id=None):
        self.by_shape = by_shape or {}
        self.by_id = by_id or {}

    def find_candidate_by_shape(self, shape, accessible_only=False):
        candidate = self.by_shape.get(shape)
        if candidate is None:
            return None
        if accessible_only and not candidate.accessible_from_top:
            return None
        return candidate

    def get_candidate(self, face_id):
        return self.by_id.get(face_id)


def _build_window_for_logic_tests():
    win = AntCamViewerWindow.__new__(AntCamViewerWindow)
    win._accessible_only = True
    win._selected_face_id = None
    status = _FakeStatusBar()
    win.statusBar = lambda: status
    return win, status


def test_cycle_candidate_under_cursor_uses_only_accessible_detected_faces():
    win, _ = _build_window_for_logic_tests()

    # Due facce sotto il mouse: una non accessibile e una accessibile.
    shape_blocked = object()
    shape_ok = object()
    c_blocked = SimpleNamespace(face_id=1, accessible_from_top=False)
    c_ok = SimpleNamespace(face_id=2, accessible_from_top=True)

    win.ocp_widget = _FakeOcpWidget([shape_blocked, shape_ok])
    win._selection_service = _FakeSelectionService(
        by_shape={shape_blocked: c_blocked, shape_ok: c_ok},
        by_id={1: c_blocked, 2: c_ok},
    )

    candidate = win._cycle_candidate_under_cursor(step=1, mouse_pos=(100, 100))
    assert candidate is c_ok


def test_cycle_candidate_under_cursor_wraps_inside_detected_stack():
    win, _ = _build_window_for_logic_tests()

    shape_a = object()
    shape_b = object()
    c_a = SimpleNamespace(face_id=10, accessible_from_top=True)
    c_b = SimpleNamespace(face_id=11, accessible_from_top=True)

    win.ocp_widget = _FakeOcpWidget([shape_a, shape_b])
    win._selection_service = _FakeSelectionService(
        by_shape={shape_a: c_a, shape_b: c_b},
        by_id={10: c_a, 11: c_b},
    )

    win._selected_face_id = 11
    candidate = win._cycle_candidate_under_cursor(step=1, mouse_pos=(0, 0))
    assert candidate is c_a


def test_context_menu_uses_current_selection_without_reselecting():
    win, _ = _build_window_for_logic_tests()

    selected = SimpleNamespace(face_id=42, accessible_from_top=True, surface_type="plane")
    clicked_other_face = object()

    win._selected_face_id = 42
    win._selection_service = _FakeSelectionService(by_id={42: selected})

    called = {}

    def _capture(candidate, global_pos):
        called["candidate"] = candidate
        called["global_pos"] = global_pos

    win._show_face_context_menu = _capture

    win._on_context_menu_requested(clicked_other_face, (10, 20))

    assert called["candidate"] is selected
    assert called["global_pos"] == (10, 20)


def test_cycle_requested_reports_when_no_accessible_face_under_cursor():
    win, status = _build_window_for_logic_tests()

    win._selection_service = _FakeSelectionService()
    win._cycle_candidate_under_cursor = lambda step, mouse_pos: None

    selected = {}

    def _select(candidate):
        selected["candidate"] = candidate

    win._select_face_candidate = _select

    win._on_cycle_requested(1, (5, 5))

    assert "Nessuna faccia accessibile sotto il puntatore" in status.messages[-1]
    assert "candidate" not in selected


def test_cycle_requested_selects_candidate_when_available():
    win, status = _build_window_for_logic_tests()

    candidate = SimpleNamespace(face_id=7, accessible_from_top=True)
    win._selection_service = _FakeSelectionService(by_id={7: candidate})
    win._cycle_candidate_under_cursor = lambda step, mouse_pos: candidate

    selected = {}

    def _select(cand):
        selected["candidate"] = cand

    win._select_face_candidate = _select

    win._on_cycle_requested(1, (5, 5))

    assert selected["candidate"] is candidate
    assert all("Nessuna faccia accessibile sotto il puntatore" not in m for m in status.messages)


def test_context_menu_without_selection_shows_reset_actions(monkeypatch):
    win, _ = _build_window_for_logic_tests()
    win._selection_service = _FakeSelectionService()
    win._selected_face_id = None

    captured = {}

    class _FakeAction:
        def __init__(self, text):
            self.text = text
            self.enabled = True

        def setEnabled(self, enabled):
            self.enabled = enabled

    class _FakeMenu:
        def __init__(self, parent):
            self.parent = parent
            self.actions = []
            self.exec_pos = None
            captured["menu"] = self

        def addAction(self, text, callback=None):
            self.actions.append((text, callback))
            return _FakeAction(text)

        def addSeparator(self):
            self.actions.append(("---", None))

        def exec(self, global_pos):
            self.exec_pos = global_pos

    monkeypatch.setattr(viewer_module, "QMenu", _FakeMenu)

    called = {"clear": 0, "deselect_all": 0, "clear_all": 0}
    win._clear_face_selection = lambda: called.__setitem__("clear", called["clear"] + 1)
    win._deselect_all_faces = lambda: called.__setitem__("deselect_all", called["deselect_all"] + 1)
    win._clear_all_manual_assignments = lambda: called.__setitem__("clear_all", called["clear_all"] + 1)

    win._on_context_menu_requested(face_shape=object(), global_pos=(10, 20))

    fake_menu = captured["menu"]
    labels = [text for text, _cb in fake_menu.actions if text != "---"]
    assert "Nessuna faccia selezionata" in labels
    assert "Deseleziona tutte le facce" in labels
    assert "Deseleziona faccia corrente" in labels
    assert "Rimuovi tutte le assegnazioni manuali" in labels
    assert fake_menu.exec_pos == (10, 20)
    assert called == {"clear": 0, "deselect_all": 0, "clear_all": 0}


