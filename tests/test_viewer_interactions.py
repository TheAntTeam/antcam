from types import SimpleNamespace

import pytest

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
    win._toolpath_mode_cycle = ("all", "drilling", "roughing", "finishing")
    win._toolpath_mode_filter = "all"
    win._toolpath_plan = None
    win._toolpath_ais = []
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


def test_split_toolpath_motion_paths_hides_linking_moves_and_keeps_cut_start():
    win, _ = _build_window_for_logic_tests()

    motions = [
        SimpleNamespace(move="rapid", point=(0.0, 0.0, 5.0)),
        SimpleNamespace(move="linear", point=(0.0, 0.0, 0.0), feed=120.0),
        SimpleNamespace(move="linear", point=(3.0, 0.0, 0.0), feed=300.0),
        SimpleNamespace(move="rapid", point=(6.0, 0.0, 5.0)),
        SimpleNamespace(move="linear", point=(6.0, 0.0, 0.0), feed=120.0),
        SimpleNamespace(move="linear", point=(6.0, 4.0, 0.0), feed=300.0),
    ]

    paths = win._split_toolpath_motion_paths(motions, cut_feed=300.0)

    assert len(paths) == 2
    assert tuple(float(v) for v in paths[0][0]) == pytest.approx((0.0, 0.0, 0.0))
    assert tuple(float(v) for v in paths[0][-1]) == pytest.approx((3.0, 0.0, 0.0))
    assert tuple(float(v) for v in paths[1][0]) == pytest.approx((6.0, 0.0, 0.0))
    assert tuple(float(v) for v in paths[1][-1]) == pytest.approx((6.0, 4.0, 0.0))


def test_split_toolpath_motion_paths_accepts_rounded_cut_feed_metadata():
    win, _ = _build_window_for_logic_tests()

    motions = [
        SimpleNamespace(move="rapid", point=(0.0, 0.0, 5.0)),
        SimpleNamespace(move="linear", point=(0.0, 0.0, 0.0), feed=102.367412),
        SimpleNamespace(move="linear", point=(5.0, 0.0, 0.0), feed=286.479754),
        SimpleNamespace(move="linear", point=(5.0, 5.0, 0.0), feed=286.479754),
    ]

    paths = win._split_toolpath_motion_paths(motions, cut_feed=286.4798)

    assert len(paths) == 1
    assert tuple(float(v) for v in paths[0][0]) == pytest.approx((0.0, 0.0, 0.0))
    assert tuple(float(v) for v in paths[0][-1]) == pytest.approx((5.0, 5.0, 0.0))


def test_display_toolpath_plan_uses_mode_specific_styles():
    win, _ = _build_window_for_logic_tests()

    displayed = []

    class _ToolpathWidget:
        def display_wire(self, shape, color=(1.0, 1.0, 1.0), width=1.0, selectable=False):
            displayed.append({
                "shape": shape,
                "color": color,
                "width": width,
                "selectable": selectable,
            })
            return shape

        def remove_interactive(self, overlay):
            displayed.append({"removed": overlay})

    roughing = SimpleNamespace(strategy="slot_milling", metadata={"operation_mode": "roughing"}, motions=[])
    finishing = SimpleNamespace(strategy="cavity_clearing", metadata={"operation_mode": "finishing"}, motions=[])
    drilling = SimpleNamespace(strategy="drilling", metadata={"operation_mode": "drilling"}, motions=[])

    win.ocp_widget = _ToolpathWidget()
    win._toolpath_plan = SimpleNamespace(operations=[roughing, finishing, drilling])
    win._toolpath_ais = []
    win._build_operation_display_shapes = lambda operation: [f"shape_{operation.strategy}_{operation.metadata['operation_mode']}"]

    win._display_toolpath_plan()

    assert displayed == [
        {"shape": "shape_slot_milling_roughing", "color": (0.1, 0.45, 1.0), "width": 3.2, "selectable": False},
        {"shape": "shape_cavity_clearing_finishing", "color": (0.15, 1.0, 0.35), "width": 2.4, "selectable": False},
        {"shape": "shape_drilling_drilling", "color": (1.0, 0.2, 1.0), "width": 2.6, "selectable": False},
    ]


def test_display_toolpath_plan_can_filter_single_mode():
    win, _ = _build_window_for_logic_tests()

    displayed = []

    class _ToolpathWidget:
        def display_wire(self, shape, color=(1.0, 1.0, 1.0), width=1.0, selectable=False):
            displayed.append({
                "shape": shape,
                "color": color,
                "width": width,
                "selectable": selectable,
            })
            return shape

        def remove_interactive(self, overlay):
            displayed.append({"removed": overlay})

    roughing = SimpleNamespace(strategy="cavity_clearing", metadata={"operation_mode": "roughing"}, motions=[])
    finishing = SimpleNamespace(strategy="slot_milling", metadata={"operation_mode": "finishing"}, motions=[])
    drilling = SimpleNamespace(strategy="drilling", metadata={"operation_mode": "drilling"}, motions=[])

    win.ocp_widget = _ToolpathWidget()
    win._toolpath_plan = SimpleNamespace(operations=[roughing, finishing, drilling])
    win._toolpath_mode_filter = "roughing"
    win._build_operation_display_shapes = lambda operation: [f"shape_{operation.strategy}_{operation.metadata['operation_mode']}"]

    win._display_toolpath_plan()

    assert displayed == [
        {"shape": "shape_cavity_clearing_roughing", "color": (1.0, 0.6, 0.0), "width": 3.3, "selectable": False},
    ]


def test_toolpath_legend_html_lists_distinct_strategy_colors():
    win, _ = _build_window_for_logic_tests()

    legend_html = win._toolpath_legend_html()

    assert "drill" in legend_html
    assert "slot rough" in legend_html
    assert "slot finish" in legend_html
    assert "cavity rough" in legend_html
    assert "cavity finish" in legend_html
    assert "profile rough" in legend_html
    assert "profile finish" in legend_html


def test_cycle_toolpath_filter_updates_mode_and_status():
    win, status = _build_window_for_logic_tests()
    win._toolpath_plan = SimpleNamespace(operations=[SimpleNamespace(metadata={"operation_mode": "roughing"})])

    rendered_modes = []
    win._display_toolpath_plan = lambda: rendered_modes.append(win._toolpath_mode_filter)

    win._cycle_toolpath_filter(1)
    assert win._toolpath_mode_filter == "drilling"
    assert rendered_modes[-1] == "drilling"
    assert "Visualizzazione toolpath: solo drilling" in status.messages[-1]

    win._cycle_toolpath_filter(1)
    assert win._toolpath_mode_filter == "roughing"

    win._cycle_toolpath_filter(-1)
    assert win._toolpath_mode_filter == "drilling"


def test_cycle_toolpath_filter_reports_missing_toolpath():
    win, status = _build_window_for_logic_tests()
    win._toolpath_plan = SimpleNamespace(operations=[])

    win._cycle_toolpath_filter(1)

    assert status.messages[-1] == "Nessun toolpath disponibile da filtrare"


