"""GL smoke test: renders one frame when a GL context is available."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _gl_available() -> bool:
    """Probe whether a functional OpenGL context can be created headless."""
    try:
        from PySide6.QtGui import QOpenGLContext, QSurfaceFormat
        from PySide6.QtWidgets import QApplication

        _ = QApplication.instance() or QApplication([])
        context = QOpenGLContext()
        context.setFormat(QSurfaceFormat.defaultFormat())
        return context.create()
    except Exception:
        return False


@pytest.mark.skipif(not _gl_available(), reason="no OpenGL context available in this environment")
def test_gl_viewport_renders_one_frame(qapp) -> None:
    """A minimal render scene produces a painted frame without errors."""
    from antcam_rc2.core.rendering.scene_graph import NodeKind, RenderNode, RenderScene, flat_points
    from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera
    from antcam_rc2.frontends.pyside.viewport.lights import Lights
    from antcam_rc2.frontends.pyside.viewport.renderer import Renderer

    scene = RenderScene(
        nodes=(
            RenderNode(
                kind=NodeKind.LINE_STRIP,
                points=flat_points([(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)]),
                color=(1.0, 0.6, 0.2, 1.0),
            ),
        )
    )
    renderer = Renderer()
    renderer.initialize()
    renderer.resize(320, 240)
    renderer.set_scene(scene, scene.bounding_box())
    renderer.paint(OrbitCamera(target=(5.0, 5.0, 0.0), distance=40.0), Lights())
