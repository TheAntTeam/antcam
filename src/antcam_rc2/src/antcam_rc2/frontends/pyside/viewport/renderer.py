"""OpenGL renderer: shadow pass, lit solids, translucent blend, lines, ACES post.

The renderer is a thin Qt/GL layer over the pure :mod:`buffers` math: it
uploads the interleaved vertex arrays once, reuses them across frames and
renders the whole scene graph with one draw call per pipeline.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QMatrix4x4, QOpenGLContext
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)

from antcam_rc2.core.rendering.scene_graph import RenderBox, RenderScene
from antcam_rc2.frontends.pyside.viewport.buffers import (
    axes_vertices,
    decode_picking_id,
    grid_vertices,
    ground_vertices,
    line_vertices,
    mesh_vertices,
    solid_vertices,
    unit_box_mesh,
)
from antcam_rc2.frontends.pyside.viewport.camera import OrbitCamera
from antcam_rc2.frontends.pyside.viewport.gl_log import gl_log
from antcam_rc2.frontends.pyside.viewport.lights import Lights, directional_light_matrices
from antcam_rc2.frontends.pyside.viewport.shaders import shader_sources

_SHADOW_MAP_SIZE = 2048
_GRID_SPACING_MM = 10.0
_GRID_MAJOR_SPACING_MM = 50.0

# Professional dark viewport palette: a vertical gradient background (instead
# of a flat near-black clear color) plus a soft ground glow under the scene.
_BACKGROUND_TOP = (0.060, 0.065, 0.080)
_BACKGROUND_BOTTOM = (0.170, 0.180, 0.220)
_GROUND_COLOR = (0.20, 0.21, 0.24)

# OpenGL enum values: PySide6 >= 6.11 does not expose GL_* constants on
# QOpenGLFunctions, so the renderer uses the stable spec literals.
GL_FLOAT = 0x1406
GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100
GL_FRAMEBUFFER = 0x8D40
GL_TEXTURE0 = 0x84C0
GL_TEXTURE_2D = 0x0DE1
GL_TRIANGLES = 0x0004
GL_LINES = 0x0001
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_DEPTH_TEST = 0x0B71
GL_VENDOR = 0x1F00
GL_RENDERER = 0x1F01
GL_VERSION = 0x1F02
GL_SHADING_LANGUAGE_VERSION = 0x8B8C


def _to_qmatrix4x4(array: np.ndarray) -> QMatrix4x4:
    """Build a QMatrix4x4 from a row-major numpy 4x4."""
    return QMatrix4x4(*array.flatten().tolist())


def _set_uniform(gl, program, name: str, value) -> None:
    """Set one shader uniform at its resolved location.

    PySide6 >= 6.11 has a broken *name-based* ``setUniformValue`` overload
    resolution (the QColor overload contains ``typing.Any`` and breaks
    matching) and a broken ``glUniformMatrix4fv`` (silently stores zeros), but
    the *location-based* overloads work, so uniforms are resolved via
    ``uniformLocation`` and set by location.
    """
    del gl  # uniform is set through the program's location-based overloads
    location = program.uniformLocation(name.encode("ascii"))
    if location < 0:
        return
    if isinstance(value, np.ndarray):
        if value.ndim == 2 and value.shape == (4, 4):
            program.setUniformValue(location, _to_qmatrix4x4(value))
        elif value.ndim == 1:
            _set_uniform_vector(program, location, value.tolist())
        else:
            raise TypeError(f"unsupported uniform array shape: {value.shape}")
    elif isinstance(value, (list, tuple)):
        _set_uniform_vector(program, location, list(value))
    elif isinstance(value, (int, bool)):
        # Use the explicit 1i setter: the generic location-based overload is
        # unreliable on PySide6 >= 6.11 (it can bind to glUniform1i and trigger
        # GL_INVALID_OPERATION for float uniforms).
        program.setUniformValue1i(location, int(value))
    else:
        # Use the explicit 1f setter for scalar floats for the same reason.
        program.setUniformValue1f(location, float(value))


def _set_uniform_vector(program, location: int, values: list) -> None:
    if len(values) == 2:
        program.setUniformValue(location, float(values[0]), float(values[1]))
    elif len(values) == 3:
        program.setUniformValue(location, float(values[0]), float(values[1]), float(values[2]))
    elif len(values) == 4:
        program.setUniformValue(location, float(values[0]), float(values[1]), float(values[2]), float(values[3]))
    else:
        raise TypeError(f"unsupported uniform vector length: {len(values)}")


def _render_mesh_vertices(scene: RenderScene) -> np.ndarray:
    """Concatenate the render-graph mesh nodes into one solid-pipeline array."""
    if not scene.meshes:
        return np.empty((0, 10), dtype=np.float32)
    blocks: list[np.ndarray] = []
    for mesh in scene.meshes:
        vertices = np.asarray(mesh.vertices, dtype=np.float64).reshape(-1, 3)
        triangles = np.asarray(mesh.triangles, dtype=np.int64).reshape(-1, 3)
        blocks.append(mesh_vertices(vertices, triangles, mesh.color))
    return np.concatenate(blocks, axis=0)


class Renderer:
    """Owns every GL resource and draws one :class:`RenderScene` per frame."""

    _PIPELINES = ("line", "solid", "solid_translucent", "picking", "mesh", "marker", "axes")

    def __init__(self) -> None:
        self._programs: dict[str, QOpenGLShaderProgram] = {}
        self._vaos: dict[str, QOpenGLVertexArrayObject] = {}
        self._vbos: dict[str, QOpenGLBuffer] = {}
        self._counts: dict[str, int] = {}
        self._grid_vbo: QOpenGLBuffer | None = None
        self._grid_vao: QOpenGLVertexArrayObject | None = None
        self._grid_count = 0
        self._background_vao: QOpenGLVertexArrayObject | None = None
        self._background_vbo: QOpenGLBuffer | None = None
        self._ground_vao: QOpenGLVertexArrayObject | None = None
        self._ground_vbo: QOpenGLBuffer | None = None
        self._ground_count = 0
        self._axes_vbo: QOpenGLBuffer | None = None
        self._axes_vao: QOpenGLVertexArrayObject | None = None
        self._axes_count = 0
        self._post_vao: QOpenGLVertexArrayObject | None = None
        self._post_vbo: QOpenGLBuffer | None = None
        self._scene_fbo: QOpenGLFramebufferObject | None = None
        self._resolve_fbo: QOpenGLFramebufferObject | None = None
        self._shadow_fbo: QOpenGLFramebufferObject | None = None
        self._pick_fbo: QOpenGLFramebufferObject | None = None
        self._viewport = QSize(1, 1)
        self._gl = None

    # ------------------------------------------------------------------ setup
    def initialize(self) -> None:
        """Create programs and buffers (called once from initializeGL)."""
        self._gl = QOpenGLContext.currentContext().functions() if QOpenGLContext.currentContext() else None
        if self._gl is not None:
            vendor = self._gl.glGetString(GL_VENDOR)
            renderer = self._gl.glGetString(GL_RENDERER)
            version = self._gl.glGetString(GL_VERSION)
            glsl = self._gl.glGetString(GL_SHADING_LANGUAGE_VERSION)
            gl_log("context vendor/renderer:", vendor, "|", renderer, "|", version, "| GLSL", glsl)
        else:
            gl_log("NO CURRENT GL CONTEXT in initialize()")
        self._programs = self._compile_programs()
        gl_log("programs compiled:", sorted(self._programs))
        for name in self._PIPELINES:
            self._vbos[name] = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            vbo_ok = self._vbos[name].create()
            self._vaos[name] = QOpenGLVertexArrayObject()
            vao_ok = self._vaos[name].create()
            self._counts[name] = 0
            gl_log(f"buffer {name}: vbo_ok={vbo_ok} vao_ok={vao_ok}")
        self._create_post_quad()
        self._create_background_quad()
        gl_log("initialize() done; glGetError =", self._gl_error())

    def _compile_programs(self) -> dict[str, QOpenGLShaderProgram]:
        """Compile 4.50 core shaders with a 3.30 core fallback."""
        last_error: str | None = None
        for version in (450, 330):
            programs: dict[str, QOpenGLShaderProgram] = {}
            try:
                for name, sources in shader_sources(version).items():
                    program = QOpenGLShaderProgram()
                    if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, sources["vertex"]):
                        raise RuntimeError(program.log())
                    if not program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, sources["fragment"]):
                        raise RuntimeError(program.log())
                    if name == "solid":
                        program.bindAttributeLocation("a_position", 0)
                        program.bindAttributeLocation("a_normal", 1)
                        program.bindAttributeLocation("a_color", 2)
                    elif name in {"line", "grid", "picking"}:
                        program.bindAttributeLocation("a_position", 0)
                        program.bindAttributeLocation("a_color", 1)
                    elif name == "post":
                        program.bindAttributeLocation("a_position", 0)
                        program.bindAttributeLocation("a_texcoord", 1)
                    if not program.link():
                        raise RuntimeError(program.log())
                    programs[name] = program
            except RuntimeError as exc:
                last_error = str(exc)
                gl_log(f"GLSL {version} failed: {last_error[:300]}")
                if version == 450:
                    continue
                raise RuntimeError(f"shader compilation failed: {last_error}") from exc
            gl_log(f"GLSL {version} programs linked OK")
            return programs
        raise RuntimeError(f"no GLSL version compiled: {last_error}")

    def _create_post_quad(self) -> None:
        vertices = np.array(
            [
                -1.0,
                -1.0,
                0.0,
                0.0,
                1.0,
                -1.0,
                1.0,
                0.0,
                1.0,
                1.0,
                1.0,
                1.0,
                -1.0,
                -1.0,
                0.0,
                0.0,
                1.0,
                1.0,
                1.0,
                1.0,
                -1.0,
                1.0,
                0.0,
                1.0,
            ],
            dtype=np.float32,
        )
        vao = QOpenGLVertexArrayObject()
        vao.create()
        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        vbo.create()
        vao.bind()
        vbo.bind()
        vbo.allocate(vertices.tobytes(), vertices.nbytes)
        program = self._programs["post"]
        program.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 2, 4 * 4)
        program.enableAttributeArray(1)
        program.setAttributeBuffer(1, GL_FLOAT, 2 * 4, 2, 4 * 4)
        program.release()
        vbo.release()
        vao.release()
        self._post_vao = vao
        self._post_vbo = vbo

    def _create_background_quad(self) -> None:
        """Create the fullscreen quad used for the gradient background."""
        vertices = np.array(
            [
                -1.0,
                -1.0,
                1.0,
                -1.0,
                1.0,
                1.0,
                -1.0,
                -1.0,
                1.0,
                1.0,
                -1.0,
                1.0,
            ],
            dtype=np.float32,
        )
        vao = QOpenGLVertexArrayObject()
        vao.create()
        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        vbo.create()
        vao.bind()
        vbo.bind()
        vbo.allocate(vertices.tobytes(), vertices.nbytes)
        program = self._programs["background"]
        program.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 2, 2 * 4)
        program.release()
        vbo.release()
        vao.release()
        self._background_vao = vao
        self._background_vbo = vbo

    # ------------------------------------------------------------------ upload
    def set_scene(self, scene: RenderScene, box: RenderBox | None = None) -> None:
        """Upload (or refresh) every vertex buffer for a render scene."""
        gl_log("set_scene: uploading scene buffers...")
        self._upload("line", line_vertices(scene), layout=((0, 3), (1, 4)), stride=8)
        self._upload("solid", solid_vertices(scene, translucent_only=False), layout=((0, 3), (1, 3), (2, 4)), stride=10)
        self._upload(
            "solid_translucent",
            solid_vertices(scene, translucent_only=True),
            layout=((0, 3), (1, 3), (2, 4)),
            stride=10,
        )
        self._upload("picking", line_vertices(scene, picking=True), layout=((0, 3), (1, 4)), stride=7)
        mesh_data = _render_mesh_vertices(scene)
        self._upload("mesh", mesh_data, layout=((0, 3), (1, 3), (2, 4)), stride=10)
        grid = (
            grid_vertices(box, _GRID_SPACING_MM, major_spacing_mm=_GRID_MAJOR_SPACING_MM)
            if box is not None
            else np.empty((0, 8), dtype=np.float32)
        )
        self._upload_grid(grid)
        # Upload axes (unit vectors at origin)
        axes = axes_vertices() if box is not None else np.empty((0, 8), dtype=np.float32)
        self._upload_axes(axes)
        self._upload_ground(box)

    def set_voxel_mesh(self, vertices, triangles, color) -> None:
        """Upload a voxel surface mesh (solid pipeline, lit)."""
        self._upload("mesh", mesh_vertices(vertices, triangles, color), layout=((0, 3), (1, 3), (2, 4)), stride=10)

    def set_tool_marker(
        self, position: tuple[float, float, float] | None, size_mm: float = 4.0, color=(0.95, 0.3, 0.3, 1.0)
    ) -> None:
        """Upload a small tool marker box at ``position`` (``None`` hides it)."""
        if position is None:
            self._upload("marker", np.empty((0, 10), dtype=np.float32), layout=((0, 3), (1, 3), (2, 4)), stride=10)
            return
        corners, triangles = unit_box_mesh(size_mm, color)
        corners = corners + np.asarray(position, dtype=np.float64)
        self._upload("marker", mesh_vertices(corners, triangles, color), layout=((0, 3), (1, 3), (2, 4)), stride=10)

    def _upload(self, name: str, data: np.ndarray, *, stride: int, layout: tuple) -> None:
        vbo = self._vbos[name]
        vbo.bind()
        if data.size:
            vbo.allocate(data.tobytes(), data.nbytes)
        else:
            vbo.allocate(b"", 0)
        vbo.release()
        self._counts[name] = data.shape[0] if data.ndim == 2 else 0
        gl_log(f"upload {name}: {self._counts[name]} vertices ({data.nbytes} bytes)")

        vao = self._vaos[name]
        vao.bind()
        vbo.bind()
        program = (
            self._programs["solid"]
            if name in {"solid", "solid_translucent", "mesh", "marker"}
            else self._programs["line"]
        )
        program.bind()
        offset = 0
        for location, size in layout:
            program.enableAttributeArray(location)
            program.setAttributeBuffer(location, GL_FLOAT, offset, size, stride * 4)
            offset += size * 4
        program.release()
        vbo.release()
        vao.release()

    def _upload_grid(self, data: np.ndarray) -> None:
        if self._grid_vbo is None:
            self._grid_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            self._grid_vbo.create()
        if self._grid_vao is None:
            self._grid_vao = QOpenGLVertexArrayObject()
            self._grid_vao.create()
        self._grid_vbo.bind()
        if data.size:
            self._grid_vbo.allocate(data.tobytes(), data.nbytes)
        else:
            self._grid_vbo.allocate(b"", 0)
        self._grid_vbo.release()
        self._grid_count = data.shape[0] if data.ndim == 2 else 0

    def _upload_axes(self, data: np.ndarray) -> None:
        if self._axes_vbo is None:
            self._axes_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            self._axes_vbo.create()
        if self._axes_vao is None:
            self._axes_vao = QOpenGLVertexArrayObject()
            self._axes_vao.create()
        self._axes_vbo.bind()
        if data.size:
            self._axes_vbo.allocate(data.tobytes(), data.nbytes)
        else:
            self._axes_vbo.allocate(b"", 0)
        self._axes_vbo.release()
        self._axes_count = data.shape[0] if data.ndim == 2 else 0

    def _upload_ground(self, box: RenderBox | None) -> None:
        """Upload the soft ground quad for the current scene bounding box."""
        data = ground_vertices(box) if box is not None else np.empty((0, 3), dtype=np.float32)
        if self._ground_vbo is None:
            self._ground_vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            self._ground_vbo.create()
        if self._ground_vao is None:
            self._ground_vao = QOpenGLVertexArrayObject()
            self._ground_vao.create()
        self._ground_vbo.bind()
        if data.size:
            self._ground_vbo.allocate(data.tobytes(), data.nbytes)
        else:
            self._ground_vbo.allocate(b"", 0)
        self._ground_vbo.release()
        self._ground_count = data.shape[0] if data.ndim == 2 else 0

        vao = self._ground_vao
        vbo = self._ground_vbo
        vao.bind()
        vbo.bind()
        program = self._programs["ground"]
        program.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 3 * 4)
        program.release()
        vbo.release()
        vao.release()

    # ------------------------------------------------------------------ drawing
    def resize(self, width: int, height: int) -> None:
        self._viewport = QSize(max(1, width), max(1, height))
        self._scene_fbo = self._make_fbo(samples=4)
        if self._scene_fbo is not None and not self._scene_fbo.isValid():
            # MSAA framebuffers fail on some drivers (e.g. Intel iGPU core
            # profiles); retry without multisampling rather than render black.
            gl_log("MSAA scene FBO INVALID - retrying without multisampling")
            self._scene_fbo = self._make_fbo(samples=0)
        self._resolve_fbo = self._make_fbo(samples=0)
        self._pick_fbo = self._make_fbo(samples=0)
        shadow_format = QOpenGLFramebufferObjectFormat()
        shadow_format.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        self._shadow_fbo = QOpenGLFramebufferObject(QSize(_SHADOW_MAP_SIZE, _SHADOW_MAP_SIZE), shadow_format)
        gl_log(
            "fbo valid: scene =",
            self._scene_fbo is not None and self._scene_fbo.isValid(),
            "| resolve =",
            self._resolve_fbo is not None and self._resolve_fbo.isValid(),
            "| pick =",
            self._pick_fbo is not None and self._pick_fbo.isValid(),
            "| shadow =",
            self._shadow_fbo is not None and self._shadow_fbo.isValid(),
            "| glGetError =",
            self._gl_error(),
        )

    def _make_fbo(self, samples: int) -> QOpenGLFramebufferObject:
        format = QOpenGLFramebufferObjectFormat()
        format.setAttachment(QOpenGLFramebufferObject.Attachment.Depth)
        format.setSamples(samples)
        return QOpenGLFramebufferObject(self._viewport, format)

    def paint(self, camera: OrbitCamera, lights: Lights, scene_box: RenderBox | None = None) -> None:
        if self._scene_fbo is None or self._resolve_fbo is None:
            gl_log("paint skipped: FBOs not created")
            return
        gl_log(
            "paint: counts =",
            {name: count for name, count in self._counts.items() if count},
        )
        width = self._viewport.width()
        height = self._viewport.height()
        view = camera.view_matrix()
        projection = camera.projection_matrix(width / height)
        view_projection = projection @ view

        light_view_proj = None
        if scene_box is not None and self._shadow_fbo is not None:
            light_view, light_projection = directional_light_matrices(lights, scene_box)
            light_view_proj = light_projection @ light_view
            self._render_shadow_map(light_view_proj)

        self._scene_fbo.bind()
        self._gl.glViewport(0, 0, width, height)
        self._gl.glClearDepthf(1.0)
        self._gl.glClear(GL_DEPTH_BUFFER_BIT)
        self._gl.glDisable(GL_DEPTH_TEST)
        gl_log("scene fbo bound; error =", self._gl_error())

        self._draw_background()
        gl_log("after background; error =", self._gl_error())
        self._draw_ground(view_projection, scene_box, light_view_proj)
        gl_log("after ground; error =", self._gl_error())
        self._draw_grid(view_projection, camera)
        gl_log("after grid; error =", self._gl_error())
        self._draw_axes(view_projection, camera)
        gl_log("after axes; error =", self._gl_error())

        self._gl.glEnable(GL_DEPTH_TEST)
        self._draw_solid("solid", view_projection, camera, lights, light_view_proj, blend=False)
        self._draw_solid("mesh", view_projection, camera, lights, light_view_proj, blend=False)
        self._draw_solid("marker", view_projection, camera, lights, light_view_proj, blend=False)
        self._draw_solid("solid_translucent", view_projection, camera, lights, light_view_proj, blend=True)
        gl_log("after solids; error =", self._gl_error())
        self._draw_lines(view_projection, camera.view_matrix(), "line")
        gl_log("after lines; error =", self._gl_error())
        self._scene_fbo.release()

        # PySide6 >= 6.11 has no overload with None rects; the two-argument form
        # blits the full source into the (same-size) resolve target, performing
        # the MSAA resolve.
        QOpenGLFramebufferObject.blitFramebuffer(self._resolve_fbo, self._scene_fbo)
        self._gl.glBindFramebuffer(GL_FRAMEBUFFER, 0)
        self._gl.glViewport(0, 0, width, height)
        self._gl.glDisable(GL_DEPTH_TEST)
        program = self._programs["post"]
        program.bind()
        self._gl.glActiveTexture(GL_TEXTURE0)
        self._gl.glBindTexture(GL_TEXTURE_2D, self._resolve_fbo.texture())
        _set_uniform(self._gl, program, "u_scene", 0)
        _set_uniform(self._gl, program, "u_exposure", 1.0)
        self._post_vao.bind()
        self._gl.glDrawArrays(GL_TRIANGLES, 0, 6)
        self._post_vao.release()
        program.release()
        gl_log("paint done; glGetError =", self._gl_error())

    @staticmethod
    def _gl_error() -> int:
        """Return the current OpenGL error code (0 == GL_NO_ERROR)."""
        gl = QOpenGLContext.currentContext()
        if gl is None:
            return -1
        functions = gl.functions()
        return int(functions.glGetError()) if functions else -1

    def pick(self, camera: OrbitCamera, x: int, y: int) -> int:
        """Render the picking pass and return the id under ``(x, y)``."""
        if self._pick_fbo is None:
            return 0
        width = self._viewport.width()
        height = self._viewport.height()
        view = camera.view_matrix()
        projection = camera.projection_matrix(width / height)
        self._pick_fbo.bind()
        self._gl.glViewport(0, 0, width, height)
        self._gl.glClearColor(0.0, 0.0, 0.0, 1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self._gl.glEnable(GL_DEPTH_TEST)
        self._draw_lines(projection @ view, view, "picking")
        self._pick_fbo.release()
        image = self._pick_fbo.toImage()
        color = image.pixelColor(x, height - y - 1)
        return decode_picking_id((color.redF(), color.greenF(), color.blueF(), color.alphaF()))

    # ------------------------------------------------------------------ internals
    def _render_shadow_map(self, light_view_projection: np.ndarray) -> None:
        """Render every solid box (opaque and translucent) into the shadow map.

        Translucent stock/fixtures still cast a shadow, matching the plan's
        'soft shadows' look; the mesh (simulated material) casts too.
        """
        casters = self._counts.get("solid", 0) + self._counts.get("solid_translucent", 0) + self._counts.get("mesh", 0)
        if self._shadow_fbo is None or casters == 0:
            return
        self._shadow_fbo.bind()
        self._gl.glViewport(0, 0, _SHADOW_MAP_SIZE, _SHADOW_MAP_SIZE)
        self._gl.glClearColor(1.0, 1.0, 1.0, 1.0)
        self._gl.glClearDepthf(1.0)
        self._gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self._gl.glEnable(GL_DEPTH_TEST)
        program = self._programs["shadow"]
        program.bind()
        _set_uniform(self._gl, program, "u_light_view_projection", light_view_projection)
        for pipeline in ("solid", "solid_translucent", "mesh"):
            count = self._counts.get(pipeline, 0)
            if count == 0:
                continue
            self._vaos[pipeline].bind()
            self._gl.glDrawArrays(GL_TRIANGLES, 0, count)
            self._vaos[pipeline].release()
        program.release()
        self._shadow_fbo.release()

    def _draw_solid(
        self,
        pipeline: str,
        view_projection: np.ndarray,
        camera: OrbitCamera,
        lights: Lights,
        light_view_proj,
        *,
        blend: bool,
    ) -> None:
        count = self._counts.get(pipeline, 0)
        if count == 0:
            return
        program = self._programs["solid"]
        program.bind()
        self._vaos[pipeline].bind()
        _set_uniform(self._gl, program, "u_view", np.eye(4))
        _set_uniform(self._gl, program, "u_projection", view_projection)
        _set_uniform(self._gl, program, "u_camera_pos", camera.eye.tolist())
        _set_uniform(self._gl, program, "u_light_dir", lights.normalized_light_dir.tolist())
        _set_uniform(self._gl, program, "u_light_color", lights.light_color)
        _set_uniform(self._gl, program, "u_ambient", lights.ambient)
        _set_uniform(self._gl, program, "u_point_pos", lights.point_pos)
        _set_uniform(self._gl, program, "u_point_color", lights.point_color)
        _set_uniform(self._gl, program, "u_point_radius", lights.point_radius)
        if light_view_proj is not None and self._shadow_fbo is not None:
            self._gl.glActiveTexture(GL_TEXTURE0)
            self._gl.glBindTexture(GL_TEXTURE_2D, self._shadow_fbo.texture())
            _set_uniform(self._gl, program, "u_shadow_map", 0)
            _set_uniform(self._gl, program, "u_light_view_projection", light_view_proj)
            _set_uniform(self._gl, program, "u_shadow_texel", (1.0 / _SHADOW_MAP_SIZE, 1.0 / _SHADOW_MAP_SIZE))
        else:
            _set_uniform(self._gl, program, "u_shadow_map", 0)
            _set_uniform(self._gl, program, "u_shadow_texel", (1.0, 1.0))
        if blend:
            self._gl.glEnable(GL_BLEND)
            self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            # Keep depth mask TRUE for translucent objects so they write depth
            # and properly occlude geometry behind them (grid, ground).
            # Standard alpha blending requires depth writes + back-to-front render order.
        self._gl.glDrawArrays(GL_TRIANGLES, 0, count)
        if blend:
            self._gl.glDepthMask(True)
            self._gl.glDisable(GL_BLEND)
        self._vaos[pipeline].release()
        program.release()

    def _draw_lines(self, view_projection: np.ndarray, view: np.ndarray, pipeline: str) -> None:
        count = self._counts.get(pipeline, 0)
        if count == 0:
            return
        program = self._programs["line"]
        program.bind()
        self._vaos[pipeline].bind()
        _set_uniform(self._gl, program, "u_view", view)
        _set_uniform(self._gl, program, "u_projection", view_projection)
        self._gl.glLineWidth(1.0)  # core profile supports width 1.0 only
        self._gl.glDrawArrays(GL_LINES, 0, count)
        self._vaos[pipeline].release()
        program.release()

    def _draw_background(self) -> None:
        """Draw a fullscreen vertical-gradient background into the scene FBO."""
        program = self._programs["background"]
        program.bind()
        self._background_vao.bind()
        _set_uniform(self._gl, program, "u_top_color", _BACKGROUND_TOP)
        _set_uniform(self._gl, program, "u_bottom_color", _BACKGROUND_BOTTOM)
        self._gl.glDepthMask(False)
        self._gl.glDrawArrays(GL_TRIANGLES, 0, 6)
        self._gl.glDepthMask(True)
        self._background_vao.release()
        program.release()

    def _draw_ground(self, view_projection: np.ndarray, box: RenderBox | None, light_view_proj) -> None:
        """Draw a soft blended ground glow with the stock/fixture shadow."""
        if self._ground_count == 0 or box is None:
            return
        program = self._programs["ground"]
        program.bind()
        self._ground_vao.bind()
        _set_uniform(self._gl, program, "u_view", np.eye(4))
        _set_uniform(self._gl, program, "u_projection", view_projection)
        _set_uniform(self._gl, program, "u_ground_color", _GROUND_COLOR)
        _set_uniform(
            self._gl,
            program,
            "u_ground_center",
            ((box.min_x + box.max_x) / 2.0, (box.min_y + box.max_y) / 2.0, box.min_z),
        )
        _set_uniform(
            self._gl, program, "u_ground_radius", max(box.width, box.height) / 2.0 + max(box.width, box.height) * 0.35
        )
        if light_view_proj is not None and self._shadow_fbo is not None:
            self._gl.glActiveTexture(GL_TEXTURE0)
            self._gl.glBindTexture(GL_TEXTURE_2D, self._shadow_fbo.texture())
            _set_uniform(self._gl, program, "u_shadow_map", 0)
            _set_uniform(self._gl, program, "u_light_view_projection", light_view_proj)
            _set_uniform(self._gl, program, "u_shadow_texel", (1.0 / _SHADOW_MAP_SIZE, 1.0 / _SHADOW_MAP_SIZE))
        else:
            _set_uniform(self._gl, program, "u_shadow_map", 0)
            _set_uniform(self._gl, program, "u_light_view_projection", np.eye(4))
            _set_uniform(self._gl, program, "u_shadow_texel", (1.0, 1.0))
        self._gl.glEnable(GL_BLEND)
        self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self._gl.glDepthMask(False)
        self._gl.glDrawArrays(GL_TRIANGLES, 0, self._ground_count)
        self._gl.glDepthMask(True)
        self._gl.glDisable(GL_BLEND)
        self._ground_vao.release()
        program.release()

    def _draw_grid(self, view_projection: np.ndarray, camera: OrbitCamera) -> None:
        if self._grid_count == 0 or self._grid_vbo is None:
            return
        program = self._programs["grid"]
        program.bind()
        vao = self._grid_vao
        vbo = self._grid_vbo
        if vao is None or vbo is None:
            program.release()
            return
        vao.bind()
        vbo.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 8 * 4)
        program.enableAttributeArray(1)
        program.setAttributeBuffer(1, GL_FLOAT, 3 * 4, 4, 8 * 4)
        _set_uniform(self._gl, program, "u_view", np.eye(4))
        _set_uniform(self._gl, program, "u_projection", view_projection)
        _set_uniform(self._gl, program, "u_camera_pos", camera.eye.tolist())
        _set_uniform(self._gl, program, "u_fade_near", max(1.0, camera.distance * 0.05))
        _set_uniform(self._gl, program, "u_fade_far", max(50.0, camera.distance * 2.5))
        self._gl.glEnable(GL_BLEND)
        self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self._gl.glDepthMask(False)
        self._gl.glLineWidth(1.0)
        self._gl.glDrawArrays(GL_LINES, 0, self._grid_count)
        self._gl.glDepthMask(True)
        self._gl.glDisable(GL_BLEND)
        vbo.release()
        vao.release()
        program.release()

    def _draw_axes(self, view_projection: np.ndarray, camera: OrbitCamera) -> None:
        """Draw unit vector axes (X=red, Y=green, Z=blue) at origin."""
        if self._axes_count == 0 or self._axes_vbo is None:
            return
        program = self._programs["line"]  # Use line shader for axes
        program.bind()
        vao = self._axes_vao
        vbo = self._axes_vbo
        if vao is None or vbo is None:
            program.release()
            return
        vao.bind()
        vbo.bind()
        program.enableAttributeArray(0)
        program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 8 * 4)
        program.enableAttributeArray(1)
        program.setAttributeBuffer(1, GL_FLOAT, 3 * 4, 4, 8 * 4)
        _set_uniform(self._gl, program, "u_view", np.eye(4))
        _set_uniform(self._gl, program, "u_projection", view_projection)
        self._gl.glEnable(GL_BLEND)
        self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self._gl.glDepthMask(False)
        # Core profile supports line width 1.0 only; removed glLineWidth(3.0) which caused GL_INVALID_VALUE (1281)
        self._gl.glDrawArrays(GL_LINES, 0, self._axes_count)
        self._gl.glDepthMask(True)
        self._gl.glDisable(GL_BLEND)
        vbo.release()
        vao.release()
        program.release()
