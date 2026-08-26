"""OpenGL diagnostic for the AntCAM RC2 viewport.

Run this ON THE DISPLAY SESSION where the GUI is launched (not over a
headless/remote session without a GPU):

    python tools/gl_check.py            # context + shaders + one rendered frame
    python tools/gl_check.py --frame    # also save an offscreen render as gl_check.png

The script reports:

- whether a Qt OpenGL context can be created (and its version/renderer/vendor);
- the GLSL version exposed by the driver;
- whether every viewport shader (4.50 core and 3.30 core) compiles;
- a real offscreen frame render with the demo scene, saving ``gl_check.png`` so
  you can SEE whether geometry is drawn at all.

Typical findings and fixes:

- ``Context: INVALID`` / version 1.x -> the GPU driver is missing or the
  session has no hardware GL (e.g. Remote Desktop).  Try the software
  fallback shipped with PySide6:  ``QT_OPENGL=software`` before the command.
- Shaders fail to compile -> a driver/GLSL limitation; try ``QT_OPENGL=software``.
- Everything OK but the GUI canvas is still black -> the issue is in the
  viewport pipeline (camera/scene), not the GPU: re-check the demo flow.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AntCAM RC2 OpenGL diagnostic")
    parser.add_argument("--frame", action="store_true", help="render one frame to gl_check.png")
    parser.add_argument(
        "--format",
        default="4.5",
        choices=("3.3", "4.5"),
        help="requested OpenGL core version (default 4.5, matching the viewport)",
    )
    args = parser.parse_args(argv)

    print("== AntCAM RC2 OpenGL diagnostic ==", flush=True)
    print(f"working dir   : {Path.cwd()}   <- gl_check.png will be saved here", flush=True)
    print(f"platform plugin : {os.environ.get('QT_QPA_PLATFORM', '(default)')}", flush=True)
    print(f"QT_OPENGL       : {os.environ.get('QT_OPENGL', '(default)')}", flush=True)

    print(
        "\nNote: run this from a normal desktop session.  In non-interactive sessions"
        " (SSH/remote shells, session 0, CI) Windows often refuses to create a hardware"
        " OpenGL context and Qt may crash instead of failing cleanly.",
        flush=True,
    )

    # A QGuiApplication must exist before any QOpenGLContext is created on
    # Windows: without it the platform plugin is not initialized and Qt may
    # crash instead of reporting a clean failure.
    from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat

    _ = QGuiApplication.instance() or QGuiApplication([])

    try:
        fmt = QSurfaceFormat()
        major, minor = (int(v) for v in args.format.split("."))
        fmt.setVersion(major, minor)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        QSurfaceFormat.setDefaultFormat(fmt)

        context = QOpenGLContext()
        created = context.create()
    except Exception as exc:  # noqa: BLE001 - surface the failure instead of crashing
        print(f"\nContext creation raised: {type(exc).__name__}: {exc}", flush=True)
        print("-> GPU driver missing or no hardware GL in this session (RDP/remote?).")
        print("-> Try:  QT_OPENGL=software python tools/gl_check.py")
        return 2

    if not created:
        print("\nContext: INVALID - could not create an OpenGL context.", flush=True)
        print("-> GPU driver missing or no hardware GL in this session (RDP?).")
        print("-> Try:  QT_OPENGL=software python tools/gl_check.py")
        return 2

    surface = QOffscreenSurface()
    surface.setFormat(context.format())
    surface.create()
    if not context.makeCurrent(surface):
        print("\nContext: created but makeCurrent() failed.", flush=True)
        return 2

    print("\nContext: OK")
    print(f"  requested   : {major}.{minor} core")
    print(f"  actual      : {context.format().majorVersion()}.{context.format().minorVersion()}")

    from PySide6.QtGui import QOpenGLFunctions
    from PySide6.QtOpenGL import QOpenGLShader

    gl = QOpenGLFunctions(context)
    gl.initializeOpenGLFunctions()
    renderer = gl.glGetString(0x1F01)  # GL_RENDERER
    vendor = gl.glGetString(0x1F00)  # GL_VENDOR
    version = gl.glGetString(0x1F02)  # GL_VERSION
    glsl = gl.glGetString(0x8B8C)  # GL_SHADING_LANGUAGE_VERSION
    print(f"  vendor      : {vendor}")
    print(f"  renderer    : {renderer}")
    print(f"  GL version  : {version}")
    print(f"  GLSL version: {glsl}")

    # --- shader compilation -------------------------------------------------
    from antcam_rc2.frontends.pyside.viewport.shaders import shader_sources

    print("\nShaders:")
    failures = 0
    for glsl_version in (330, 450):
        for program, stages in shader_sources(glsl_version).items():
            for stage, source in stages.items():
                shader = QOpenGLShader(QOpenGLShader.Vertex if stage == "vertex" else QOpenGLShader.Fragment)
                if shader.compileSourceCode(source):
                    print(f"  {glsl_version} {program:8s} {stage:9s} ok")
                else:
                    failures += 1
                    print(f"  {glsl_version} {program:8s} {stage:9s} FAIL")
                    print("    " + (shader.log() or "no log").replace("\n", "\n    ")[:600])
    if failures:
        print(f"\n{failures} shader(s) failed to compile.")
        return 3

    if not args.frame:
        print("\nAll shaders compile. Add --frame to render and save gl_check.png.")
        return 0

    # --- one real frame -----------------------------------------------------
    print("\nRendering one frame of the demo scene (gl_check.png)...")
    from PySide6.QtOpenGL import QOpenGLFramebufferObject

    from antcam_rc2.core.io import import_file
    from antcam_rc2.core.rendering.builder import geometry_to_scene
    from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera
    from antcam_rc2.frontends.pyside.viewport.lights import Lights
    from antcam_rc2.frontends.pyside.viewport.renderer import Renderer

    sample = _REPO / "src" / "antcam_rc2" / "samples" / "mech_plate.dxf"
    scene = geometry_to_scene(import_file(sample))
    box = scene.bounding_box()
    camera = OrbitCamera()
    camera.fit_to(box, (800, 600))

    width, height = 800, 600
    fbo = QOpenGLFramebufferObject(width, height)
    fbo.bind()
    gl.glViewport(0, 0, width, height)
    gl.glClearColor(0.12, 0.12, 0.14, 1.0)
    gl.glClear(0x4000 | 0x100)  # GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT

    os.environ["ANTCAM_GL_DEBUG"] = "1"  # surface the renderer's own [GL] diagnostics
    renderer = Renderer()
    renderer.initialize()
    renderer.resize(width, height)
    renderer.set_scene(scene, box)
    renderer.paint(camera, Lights(), box)

    fbo_ok = renderer._scene_fbo is not None and renderer._scene_fbo.isValid()
    print(f"\nscene FBO valid: {fbo_ok}", flush=True)
    if not fbo_ok:
        print("-> MSAA framebuffer failed; the viewport renders black. Check the [GL] lines above.", flush=True)

    # The renderer draws into its own FBOs and finishes on the default
    # framebuffer; capture its resolve FBO (the finished scene, pre-tonemap)
    # to verify geometry is actually drawn.
    fbo.release()
    resolved = renderer._resolve_fbo
    image = resolved.toImage() if resolved is not None else fbo.toImage()
    output = Path("gl_check.png")
    image.save(str(output))
    print(f"Frame saved: {output.resolve()} ({image.width()}x{image.height()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
