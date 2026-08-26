"""Visual probe for the AntCAM RC2 OpenGL canvas.

Run this ON THE DISPLAY SESSION where the GUI is launched:

    python tools/gl_canvas_probe.py               # open the real widget, grab a PNG, exit
    python tools/gl_canvas_probe.py --live        # open the real widget and keep it open
    python tools/gl_canvas_probe.py --offscreen   # render through the renderer, save PNG

The script draws a *known* test scene (a translucent stock box, bright line
strips forming a triangle and a cross, plus the ground grid) so a screenshot is
unambiguous: if the OpenGL canvas activates you will see a dark blue-grey
background, a light grid, a translucent box and bright lines.  It reports:

- the GL context version/vendor/renderer;
- the GL error code after the frame (0 == clean, 1282 == GL_INVALID_OPERATION);
- how many pixels are not the clear color (proof a draw call actually ran).

Output images are written next to the current directory:
``gl_canvas_window.png`` / ``gl_canvas_offscreen.png``.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))


def _set_surface_format() -> None:
    from PySide6.QtGui import QSurfaceFormat

    fmt = QSurfaceFormat()
    fmt.setVersion(4, 5)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    fmt.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(fmt)


def _build_scene():
    from antcam_rc2.core.rendering.scene_graph import NodeKind, RenderBox, RenderNode, RenderScene, flat_points

    # A recognizable test pattern: a translucent stock box + bright outline.
    box = RenderBox(min_x=-20.0, min_y=-20.0, min_z=0.0, max_x=20.0, max_y=20.0, max_z=10.0)
    triangle = flat_points([(0.0, 0.0, 0.0), (18.0, 0.0, 0.0), (9.0, 16.0, 0.0), (0.0, 0.0, 0.0)])
    cross = flat_points([(-15.0, -15.0, 0.0), (15.0, 15.0, 0.0), (-15.0, 15.0, 0.0), (15.0, -15.0, 0.0)])
    return RenderScene(
        nodes=(
            RenderNode(kind=NodeKind.SOLID_BOX, box=box, color=(0.4, 0.55, 0.7, 0.35)),
            RenderNode(kind=NodeKind.CLOSED_POLYLINE, points=triangle, color=(1.0, 0.35, 0.1, 1.0), width_px=2.0),
            RenderNode(kind=NodeKind.LINE_STRIP, points=cross, color=(0.2, 0.9, 0.4, 1.0), width_px=2.0),
        )
    )


def _pixel_stats(image) -> tuple[int, int]:
    """Return ``(non_clear_pixels, total_pixels)`` for a rendered frame."""
    width, height = image.width(), image.height()
    total = width * height
    non_clear = 0
    for y in range(height):
        for x in range(width):
            color = image.pixelColor(x, y)
            if color.red() > 30 or color.green() > 30 or color.blue() > 40:
                non_clear += 1
    return non_clear, total


def _report_gl(context, *, prefix: str) -> None:
    functions = context.functions()
    vendor = functions.glGetString(0x1F00)
    renderer = functions.glGetString(0x1F01)
    version = functions.glGetString(0x1F02)
    glsl = functions.glGetString(0x8B8C)
    print(f"[{prefix}] vendor     : {vendor}", flush=True)
    print(f"[{prefix}] renderer   : {renderer}", flush=True)
    print(f"[{prefix}] GL version : {version}", flush=True)
    print(f"[{prefix}] GLSL       : {glsl}", flush=True)


def run_window(live: bool) -> int:
    """Show the real GLViewport widget and grab its framebuffer."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMainWindow

    _set_surface_format()
    app = QApplication.instance() or QApplication([])

    from antcam_rc2.frontends.pyside.viewport.gl_viewport import GLViewport

    window = QMainWindow()
    window.setWindowTitle("AntCAM RC2 — GL canvas probe")
    viewport = GLViewport(window)
    window.setCentralWidget(viewport)
    window.resize(800, 600)
    viewport.set_render_scene(_build_scene())
    viewport.fit_view()
    window.show()

    # Process enough events for initializeGL + one paintGL.
    for _ in range(5):
        app.processEvents()

    context = viewport.context()
    if context is not None and context.isValid():
        _report_gl(context, prefix="window")

    error = int(viewport._renderer._gl.glGetError()) if viewport._renderer._gl is not None else -1
    print(f"[window] GL error after first frame: {error} (0 = clean, 1282 = GL_INVALID_OPERATION)", flush=True)

    image = viewport.grabFramebuffer()
    output = Path("gl_canvas_window.png")
    image.save(str(output))
    non_clear, total = _pixel_stats(image)
    print(f"[window] frame saved: {output.resolve()} ({image.width()}x{image.height()})", flush=True)
    print(f"[window] non-clear pixels: {non_clear}/{total}", flush=True)

    if not live:
        window.close()
        return 0

    print("[window] keeping the canvas open; close the window to exit.", flush=True)
    QTimer.singleShot(0, lambda: None)
    return app.exec()


def run_offscreen() -> int:
    """Render the same scene through the offscreen renderer and save a PNG."""
    from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext

    _set_surface_format()
    _ = QGuiApplication.instance() or QGuiApplication([])

    context = QOpenGLContext()
    if not context.create():
        print("[offscreen] could not create an OpenGL context.", flush=True)
        return 2
    surface = QOffscreenSurface()
    surface.setFormat(context.format())
    surface.create()
    if not context.makeCurrent(surface):
        print("[offscreen] makeCurrent() failed.", flush=True)
        return 2

    _report_gl(context, prefix="offscreen")

    from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera
    from antcam_rc2.frontends.pyside.viewport.lights import Lights
    from antcam_rc2.frontends.pyside.viewport.renderer import Renderer

    scene = _build_scene()
    box = scene.bounding_box()
    camera = OrbitCamera()
    camera.fit_to(box, (800, 600))

    renderer = Renderer()
    renderer.initialize()
    renderer.resize(800, 600)
    renderer.set_scene(scene, box)
    renderer.paint(camera, Lights(), box)

    error = int(renderer._gl.glGetError())
    print(f"[offscreen] GL error after render: {error} (0 = clean, 1282 = GL_INVALID_OPERATION)", flush=True)

    resolve = renderer._resolve_fbo
    image = resolve.toImage() if resolve is not None else None
    if image is None:
        print("[offscreen] resolve FBO is missing.", flush=True)
        return 3
    output = Path("gl_canvas_offscreen.png")
    image.save(str(output))
    non_clear, total = _pixel_stats(image)
    print(f"[offscreen] frame saved: {output.resolve()} ({image.width()}x{image.height()})", flush=True)
    print(f"[offscreen] non-clear pixels: {non_clear}/{total}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AntCAM RC2 OpenGL canvas probe")
    parser.add_argument("--live", action="store_true", help="keep the window open after the first frame")
    parser.add_argument("--offscreen", action="store_true", help="render through the offscreen renderer instead")
    args = parser.parse_args(argv)

    print("== AntCAM RC2 GL canvas probe ==", flush=True)
    print(f"platform plugin : {os.environ.get('QT_QPA_PLATFORM', '(default)')}", flush=True)
    print(f"QT_OPENGL       : {os.environ.get('QT_OPENGL', '(default)')}", flush=True)

    if args.offscreen:
        return run_offscreen()
    return run_window(args.live)


if __name__ == "__main__":
    raise SystemExit(main())
