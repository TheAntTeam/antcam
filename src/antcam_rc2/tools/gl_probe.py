"""Minimal GL draw probe: one triangle into an FBO, read back.

Isolates whether basic drawing works on this driver/PySide6 combo, independent
of the renderer's uniforms/camera setup.
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLFramebufferObject,
    QOpenGLFramebufferObjectFormat,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVertexArrayObject,
)

GL_COLOR_BUFFER_BIT = 0x4000
GL_TRIANGLES = 0x0004
GL_FLOAT = 0x1406
GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401


def main() -> int:
    QGuiApplication.instance() or QGuiApplication([])
    fmt = QSurfaceFormat()
    fmt.setVersion(4, 5)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(24)
    QSurfaceFormat.setDefaultFormat(fmt)
    ctx = QOpenGLContext()
    if not ctx.create():
        print("context FAILED")
        return 1
    surface = QOffscreenSurface()
    surface.setFormat(ctx.format())
    surface.create()
    ctx.makeCurrent(surface)
    gl = ctx.functions()
    gl.initializeOpenGLFunctions()
    print("GL:", ctx.format().majorVersion(), ctx.format().minorVersion(), flush=True)

    vertex = """#version 450
    layout(location = 0) in vec3 a_position;
    void main() { gl_Position = vec4(a_position, 1.0); }
    """
    fragment = """#version 450
    out vec4 frag_color;
    void main() { frag_color = vec4(1.0, 0.0, 0.0, 1.0); }
    """
    program = QOpenGLShaderProgram()
    ok = program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, vertex)
    print("vertex shader:", ok, flush=True)
    ok = program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, fragment)
    print("fragment shader:", ok, flush=True)
    ok = program.link()
    print("link:", ok, program.log()[:200] if not ok else "", flush=True)

    import struct

    verts = struct.pack("<9f", -0.5, -0.5, 0.0, 0.5, -0.5, 0.0, 0.0, 0.5, 0.0)
    vao = QOpenGLVertexArrayObject()
    vao.create()
    vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
    vbo.create()
    vao.bind()
    vbo.bind()
    vbo.allocate(verts, len(verts))
    program.bind()
    program.enableAttributeArray(0)
    program.setAttributeBuffer(0, GL_FLOAT, 0, 3, 3 * 4)
    program.release()
    vbo.release()
    vao.release()

    fbo = QOpenGLFramebufferObject(64, 64, QOpenGLFramebufferObjectFormat())
    print("fbo valid:", fbo.isValid(), flush=True)
    fbo.bind()
    gl.glViewport(0, 0, 64, 64)
    gl.glClearColor(0.0, 0.0, 0.0, 1.0)
    gl.glClear(GL_COLOR_BUFFER_BIT)
    program.bind()
    vao.bind()
    gl.glDrawArrays(GL_TRIANGLES, 0, 3)
    vao.release()
    program.release()
    fbo.release()
    print("glGetError:", gl.glGetError(), flush=True)

    image = fbo.toImage()
    red = 0
    for y in range(64):
        for x in range(64):
            c = image.pixelColor(x, y)
            if c.red() > 200 and c.green() < 60:
                red += 1
    print(f"red pixels: {red} / 4096", flush=True)
    return 0 if red > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
