"""Regression tests for scalar-uniform uploads in the GL viewport.

PySide6 >= 6.11's generic ``QOpenGLShaderProgram.setUniformValue(location,
float)`` overload binds to the wrong GL call for scalar floats and triggers
``GL_INVALID_OPERATION`` (1282) on real drivers.  These tests render the
affected passes (lit solids with ``u_point_radius`` and the post pass with
``u_exposure``) and assert the GL error flag stays clean and the frame is not
black.

The context is created on an offscreen surface (like :mod:`tools.gl_probe`),
which is more reliable in headless/CI sessions than a ``QOpenGLWidget``
context.
"""

from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture(scope="module")
def gl_context():
    """A working 4.5-core OpenGL context on an offscreen surface, or skip.

    The context is created on the platform Qt was started with: on a normal
    desktop this is the hardware GL platform; in CI/headless runs other test
    modules force the offscreen platform, where ``QOpenGLContext.create()``
    often returns ``False`` and the test is skipped (the static guard below
    still covers the regression without GL).
    """
    pytest.importorskip("PySide6")
    from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat

    _ = QGuiApplication.instance() or QGuiApplication([])
    fmt = QSurfaceFormat()
    fmt.setVersion(4, 5)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)

    context = QOpenGLContext()
    if not context.create():
        pytest.skip("no offscreen OpenGL context available")
    surface = QOffscreenSurface()
    surface.setFormat(context.format())
    surface.create()
    if not context.makeCurrent(surface):
        pytest.skip("OpenGL context makeCurrent() failed")
    yield context


def _renderer_and_scene():
    from antcam_rc2.core.rendering.scene_graph import NodeKind, RenderBox, RenderNode, RenderScene
    from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera
    from antcam_rc2.frontends.pyside.viewport.lights import Lights
    from antcam_rc2.frontends.pyside.viewport.renderer import Renderer

    # One translucent solid (exercises the lit solid pass + u_point_radius)
    # plus a line strip (exercises the line pass) and the post pass (u_exposure).
    scene = RenderScene(
        nodes=(
            RenderNode(
                kind=NodeKind.SOLID_BOX,
                box=RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=10.0, max_y=10.0, max_z=10.0),
                color=(0.5, 0.5, 0.5, 0.35),
            ),
            RenderNode(
                kind=NodeKind.LINE_STRIP,
                points=(0.0, 0.0, 0.0, 10.0, 10.0, 10.0),
                color=(1.0, 0.2, 0.1, 1.0),
            ),
        )
    )
    renderer = Renderer()
    renderer.initialize()
    renderer.resize(320, 240)
    renderer.set_scene(scene, scene.bounding_box())
    return renderer, scene, OrbitCamera(), Lights()


def test_lit_and_post_pass_leave_gl_error_clear(gl_context) -> None:
    """The full pipeline (solids + post) must not set GL_INVALID_OPERATION."""
    del gl_context
    renderer, scene, camera, lights = _renderer_and_scene()
    renderer.paint(camera, lights, scene.bounding_box())

    error = int(renderer._gl.glGetError())
    assert error == 0, f"OpenGL error after paint: {error} (GL_INVALID_OPERATION = 1282)"


def test_post_pass_writes_non_background_pixels(gl_context) -> None:
    """The resolve FBO must contain drawn geometry, not just the clear color."""
    del gl_context
    renderer, scene, camera, lights = _renderer_and_scene()
    renderer.paint(camera, lights, scene.bounding_box())

    resolve = renderer._resolve_fbo
    assert resolve is not None
    image = resolve.toImage()
    image = image.convertToFormat(image.Format.Format_RGBA8888)
    width, height = image.width(), image.height()
    ptr = image.bits()
    # QImage bits are BGRA on little-endian; only look for "not the clear color".
    channels = np.frombuffer(bytes(ptr), dtype=np.uint8, count=width * height * 4).reshape(height, width, 4)

    # The scene is cleared to a dark blue-grey (0.05, 0.05, 0.06, 1.0) before
    # drawing, so any pixel clearly brighter than that proves a draw call ran.
    bright = (channels[:, :, :3].astype(np.int32).sum(axis=2) > 80).sum()
    assert bright > 0, "resolve FBO is empty: nothing was drawn"


def test_scalar_float_setter_uses_explicit_overloads() -> None:
    """Static guard: scalar uniforms must use setUniformValue1f/1i, not the
    generic ``setUniformValue(location, value)`` overload that is broken on
    PySide6 >= 6.11.
    """
    import re
    from pathlib import Path

    renderer_path = (
        Path(__file__).parent.parent.parent / "src" / "antcam_rc2" / "frontends" / "pyside" / "viewport" / "renderer.py"
    )
    text = renderer_path.read_text(encoding="utf-8")
    # The scalar branches must never call the generic two-argument overload.
    assert "program.setUniformValue1f(location, float(value))" in text
    assert "program.setUniformValue1i(location, int(value))" in text
    bad = re.findall(r"program\.setUniformValue\(location,\s*(?:int|float)\(value\)\)", text)
    assert bad == [], f"generic scalar setUniformValue overload still used: {bad}"
