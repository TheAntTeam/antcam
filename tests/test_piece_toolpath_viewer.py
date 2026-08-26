from types import SimpleNamespace

from antcam.piece_toolpath_viewer import PieceToolpathPreviewWindow
from antcam.viewer import AntCamViewerWindow


def _build_preview_window_for_logic_tests():
    win = PieceToolpathPreviewWindow.__new__(PieceToolpathPreviewWindow)
    win._toolpath_plan = None
    win._toolpath_ais = []
    win._toolpath_mode_cycle = ("visible", "hidden")
    win._toolpath_mode_filter = "visible"
    win._make_polyline_wire = lambda points: tuple(tuple(float(v) for v in point) for point in points)
    win._build_operation_display_shapes = AntCamViewerWindow._build_operation_display_shapes.__get__(win, PieceToolpathPreviewWindow)
    win._split_toolpath_motion_paths = AntCamViewerWindow._split_toolpath_motion_paths.__get__(win, PieceToolpathPreviewWindow)
    win._split_rapid_motion_paths = PieceToolpathPreviewWindow._split_rapid_motion_paths.__get__(win, PieceToolpathPreviewWindow)
    win._build_profile_preview_shapes = PieceToolpathPreviewWindow._build_profile_preview_shapes.__get__(win, PieceToolpathPreviewWindow)
    win._should_display_toolpath_operation = PieceToolpathPreviewWindow._should_display_toolpath_operation.__get__(win, PieceToolpathPreviewWindow)
    win._toolpath_filter_label = PieceToolpathPreviewWindow._toolpath_filter_label.__get__(win, PieceToolpathPreviewWindow)
    win._cycle_toolpath_filter = PieceToolpathPreviewWindow._cycle_toolpath_filter.__get__(win, PieceToolpathPreviewWindow)
    return win


def test_build_profile_preview_shapes_separates_cut_and_rapid_paths_for_single_level_contour():
    win = _build_preview_window_for_logic_tests()

    operation = SimpleNamespace(
        strategy="2p5d_profile",
        metadata={"cut_feed": 300.0},
        motions=[
            SimpleNamespace(move="rapid", point=(0.0, 0.0, 5.0)),
            SimpleNamespace(move="rapid", point=(0.0, 0.0, 1.0)),
            SimpleNamespace(move="linear", point=(0.0, 0.0, 0.0), feed=120.0),
            SimpleNamespace(move="linear", point=(4.0, 0.0, 0.0), feed=300.0),
            SimpleNamespace(move="linear", point=(4.0, 4.0, 0.0), feed=300.0),
            SimpleNamespace(move="rapid", point=(4.0, 4.0, 5.0)),
            SimpleNamespace(move="rapid", point=(8.0, 4.0, 5.0)),
            SimpleNamespace(move="rapid", point=(8.0, 4.0, 1.0)),
            SimpleNamespace(move="linear", point=(8.0, 4.0, 0.0), feed=120.0),
            SimpleNamespace(move="linear", point=(8.0, 8.0, 0.0), feed=300.0),
            SimpleNamespace(move="rapid", point=(8.0, 8.0, 5.0)),
        ],
    )

    shapes = win._build_profile_preview_shapes(operation)

    assert len(shapes["rapid"]) == 3
    assert len(shapes["cut"]) == 2
    assert shapes["rapid"][0][0] == (0.0, 0.0, 5.0)
    assert shapes["rapid"][0][-1] == (0.0, 0.0, 1.0)
    assert shapes["rapid"][1][0] == (4.0, 4.0, 0.0)
    assert shapes["rapid"][1][-1] == (8.0, 4.0, 1.0)
    assert shapes["rapid"][2][0] == (8.0, 8.0, 0.0)
    assert shapes["rapid"][2][-1] == (8.0, 8.0, 5.0)
    assert shapes["cut"][0][0] == (0.0, 0.0, 0.0)
    assert shapes["cut"][0][-1] == (4.0, 4.0, 0.0)
    assert shapes["cut"][1][0] == (8.0, 4.0, 0.0)
    assert shapes["cut"][1][-1] == (8.0, 8.0, 0.0)


def test_display_toolpath_plan_shows_roughing_and_profile_preview_with_distinct_colors():
    win = _build_preview_window_for_logic_tests()
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

    class _StatusBar:
        def showMessage(self, message):
            displayed.append({"message": message})

    win.ocp_widget = _ToolpathWidget()
    win.statusBar = lambda: _StatusBar()
    win._toolpath_plan = SimpleNamespace(
        operations=[
            SimpleNamespace(strategy="cavity_clearing", metadata={"operation_mode": "roughing", "cut_feed": 300.0}, motions=[]),
            SimpleNamespace(strategy="2p5d_profile", metadata={"operation_mode": "finishing"}, motions=[]),
        ]
    )
    win._toolpath_ais = []
    win._build_operation_display_shapes = lambda operation: ["rough_shape"]
    win._build_profile_preview_shapes = lambda operation: {"rapid": ["rapid_shape"], "cut": ["cut_shape"]}

    PieceToolpathPreviewWindow._display_toolpath_plan(win)

    assert displayed[0] == {"shape": "rough_shape", "color": (1.0, 0.55, 0.0), "width": 3.0, "selectable": False}
    assert displayed[1] == {"shape": "rapid_shape", "color": (0.1, 0.8, 1.0), "width": 1.8, "selectable": False}
    assert displayed[2] == {"shape": "cut_shape", "color": (1.0, 0.9, 0.1), "width": 3.0, "selectable": False}


def test_display_toolpath_plan_renders_all_profile_operations_for_same_level():
    win = _build_preview_window_for_logic_tests()
    displayed = []

    class _ToolpathWidget:
        def display_wire(self, shape, color=(1.0, 1.0, 1.0), width=1.0, selectable=False):
            displayed.append(shape)
            return shape

        def remove_interactive(self, overlay):
            displayed.append(("removed", overlay))

    class _StatusBar:
        def showMessage(self, message):
            displayed.append(("message", message))

    win.ocp_widget = _ToolpathWidget()
    win.statusBar = lambda: _StatusBar()
    win._toolpath_plan = SimpleNamespace(
        operations=[
            SimpleNamespace(strategy="2p5d_profile", metadata={"operation_mode": "finishing"}, motions=[]),
            SimpleNamespace(strategy="2p5d_profile", metadata={"operation_mode": "finishing"}, motions=[]),
        ]
    )
    win._toolpath_ais = []
    preview_shapes = iter([
        {"rapid": ["rapid_outer"], "cut": ["cut_outer"]},
        {"rapid": ["rapid_hole"], "cut": ["cut_hole"]},
    ])
    win._build_profile_preview_shapes = lambda operation: next(preview_shapes)

    PieceToolpathPreviewWindow._display_toolpath_plan(win)

    assert displayed[:4] == ["rapid_outer", "cut_outer", "rapid_hole", "cut_hole"]


def test_cycle_toolpath_filter_hides_and_restores_preview_paths():
    win = _build_preview_window_for_logic_tests()
    displayed = []

    class _ToolpathWidget:
        def display_wire(self, shape, color=(1.0, 1.0, 1.0), width=1.0, selectable=False):
            displayed.append(("display", shape))
            return shape

        def remove_interactive(self, overlay):
            displayed.append(("remove", overlay))

    class _StatusBar:
        def __init__(self):
            self.messages = []

        def showMessage(self, message):
            self.messages.append(message)

    status = _StatusBar()
    win.ocp_widget = _ToolpathWidget()
    win.statusBar = lambda: status
    win._toolpath_plan = SimpleNamespace(
        operations=[SimpleNamespace(strategy="2p5d_profile", metadata={"operation_mode": "finishing"}, motions=[])]
    )
    win._build_profile_preview_shapes = lambda operation: {"rapid": ["rapid_shape"], "cut": ["cut_shape"]}

    PieceToolpathPreviewWindow._display_toolpath_plan(win)
    assert ("display", "rapid_shape") in displayed
    assert ("display", "cut_shape") in displayed

    displayed.clear()
    win._cycle_toolpath_filter(1)

    assert win._toolpath_mode_filter == "hidden"
    assert displayed == [("remove", "rapid_shape"), ("remove", "cut_shape")]
    assert status.messages[-1] == "Piece preview | path nascosti | T mostra"

    displayed.clear()
    win._cycle_toolpath_filter(1)

    assert win._toolpath_mode_filter == "visible"
    assert displayed == [("display", "rapid_shape"), ("display", "cut_shape")]