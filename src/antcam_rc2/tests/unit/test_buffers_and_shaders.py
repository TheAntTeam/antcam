"""Tests for the pure GPU buffer construction and shader templates."""

from __future__ import annotations

import numpy as np
import pytest

from antcam_rc2.core.rendering.scene_graph import NodeKind, RenderBox, RenderNode, RenderScene, flat_points
from antcam_rc2.frontends.pyside.viewport.buffers import decode_picking_id, line_vertices, solid_vertices
from antcam_rc2.frontends.pyside.viewport.shaders import shader_sources


def make_line_scene() -> RenderScene:
    return RenderScene(
        nodes=(
            RenderNode(
                kind=NodeKind.LINE_STRIP,
                points=flat_points([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]),
                color=(1.0, 0.0, 0.0, 1.0),
                picking_id=7,
            ),
        )
    )


def test_line_vertices_expand_strips_to_pairs() -> None:
    buffer = line_vertices(make_line_scene())
    assert buffer.shape[1] == 8
    # 3 points -> 2 segments -> 4 vertices
    assert buffer.shape[0] == 4
    np.testing.assert_allclose(buffer[0, 0:3], (0.0, 0.0, 0.0))
    np.testing.assert_allclose(buffer[3, 0:3], (1.0, 1.0, 0.0))
    assert (buffer[:, 3:7] == (1.0, 0.0, 0.0, 1.0)).all()


def test_closed_polyline_closes_the_loop() -> None:
    scene = RenderScene(
        nodes=(
            RenderNode(
                kind=NodeKind.CLOSED_POLYLINE,
                points=flat_points([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)]),
            ),
        )
    )
    buffer = line_vertices(scene)
    assert buffer.shape[0] == 6  # 3 open segments + 1 closing segment


def test_picking_vertices_encode_and_decode_id() -> None:
    buffer = line_vertices(make_line_scene(), picking=True)
    assert buffer.shape[1] == 7
    decoded = decode_picking_id(tuple(buffer[0, 3:7]))
    assert decoded == 7
    assert decode_picking_id((0.0, 0.0, 0.0, 1.0)) == 0


def test_solid_vertices_build_box_with_normals() -> None:
    scene = RenderScene(
        nodes=(
            RenderNode(
                kind=NodeKind.SOLID_BOX,
                box=RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=2.0, max_y=1.0, max_z=1.0),
                color=(0.5, 0.5, 0.5, 1.0),
            ),
        )
    )
    buffer = solid_vertices(scene)
    assert buffer.shape == (36, 10)
    assert (buffer[:, 6:10] == (0.5, 0.5, 0.5, 1.0)).all()
    # normals are unit length
    norms = np.linalg.norm(buffer[:, 3:6], axis=1)
    np.testing.assert_allclose(norms, 1.0, atol=1e-6)


def test_translucent_filter_split() -> None:
    scene = RenderScene(
        nodes=(
            RenderNode(
                kind=NodeKind.SOLID_BOX,
                box=RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=1.0, max_y=1.0, max_z=1.0),
                color=(1.0, 1.0, 1.0, 0.35),
            ),
            RenderNode(
                kind=NodeKind.SOLID_BOX,
                box=RenderBox(min_x=2.0, min_y=0.0, min_z=0.0, max_x=3.0, max_y=1.0, max_z=1.0),
                color=(1.0, 1.0, 1.0, 1.0),
            ),
        )
    )
    assert solid_vertices(scene, translucent_only=False).shape[0] == 36
    assert solid_vertices(scene, translucent_only=True).shape[0] == 36


def test_empty_scene_returns_empty_buffers() -> None:
    assert line_vertices(RenderScene()).shape == (0, 8)
    assert solid_vertices(RenderScene()).shape == (0, 10)


def test_grid_vertices_generate_parallel_lines() -> None:
    from antcam_rc2.frontends.pyside.viewport.buffers import grid_vertices

    buffer = grid_vertices(
        RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=10.0, max_y=10.0, max_z=0.0), spacing_mm=5.0
    )
    assert buffer.shape[0] == 12  # 3 lines along X + 3 along Y -> 6 segments


def test_grid_vertices_use_major_and_minor_colors() -> None:
    from antcam_rc2.frontends.pyside.viewport.buffers import grid_vertices

    box = RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=10.0, max_y=10.0, max_z=0.0)
    buffer = grid_vertices(box, spacing_mm=5.0, major_spacing_mm=10.0)
    # Each line segment is two vertices; colors are constant per segment.
    segments = buffer.reshape(-1, 2, 8)
    alphas = segments[:, 0, 6]
    np.testing.assert_allclose(sorted(float(value) for value in np.unique(alphas)), [0.65, 0.9])
    # The grid is centred on the box: the major line sits at the centre (y=5),
    # while y=0 and y=10 are minor lines.
    np.testing.assert_allclose(alphas[:3], [0.65, 0.9, 0.65])


def test_ground_vertices_build_two_triangles_on_min_z() -> None:
    from antcam_rc2.frontends.pyside.viewport.buffers import ground_vertices

    box = RenderBox(min_x=0.0, min_y=0.0, min_z=0.0, max_x=10.0, max_y=10.0, max_z=5.0)
    buffer = ground_vertices(box)
    assert buffer.shape == (6, 3)
    assert (buffer[:, 2] == box.min_z).all()
    # The ground quad extends beyond the box so the soft falloff happens outside.
    assert buffer[:, 0].min() < box.min_x
    assert buffer[:, 0].max() > box.max_x


@pytest.mark.parametrize("version", [330, 450])
def test_shader_templates_are_consistent(version: int) -> None:
    sources = shader_sources(version)
    assert set(sources) == {"line", "solid", "shadow", "post", "picking", "grid", "background", "ground"}
    for program in sources.values():
        for source in program.values():
            assert source.startswith(f"#version {version}")
            assert "{" not in source or "void main" in source  # formatted templates
            assert "\n" in source


def test_solid_fragment_projection_is_vec4() -> None:
    """Regression: ``mat4 * vec4`` yields a vec4, never a vec3.

    A previous version declared ``vec3 light_clip = u_light_view_projection *
    vec4(...)`` which failed to compile on real GL drivers (the error only
    surfaced at runtime; this static guard catches it without a GL context).
    """
    import re

    for version in (330, 450):
        sources = shader_sources(version)
        for program, stages in sources.items():
            fragment = stages["fragment"]
            # any "vec3 name = <expr containing vec4(...)>" is a type mismatch
            mismatches = [
                line.strip() for line in fragment.splitlines() if re.match(r"vec3\s+\w+\s*=", line) and "vec4(" in line
            ]
            assert mismatches == [], f"{program} fragment has vec3-typed vec4 result: {mismatches}"
    solid = shader_sources(450)["solid"]["fragment"]
    assert "vec4 light_clip = u_light_view_projection * vec4(v_world_pos, 1.0);" in solid
    assert "float shadow_attenuation(vec4 light_clip)" in solid


def test_fragment_shaders_are_brace_balanced() -> None:
    for version in (330, 450):
        for program, stages in shader_sources(version).items():
            for stage, source in stages.items():
                assert source.count("{") == source.count("}"), f"{program}.{stage} braces"
                assert source.count("(") == source.count(")"), f"{program}.{stage} parens"


def test_shader_sources_reject_unknown_version() -> None:
    with pytest.raises(ValueError, match="GLSL"):
        shader_sources(300)


def test_renderer_uses_literal_gl_constants() -> None:
    """Regression: PySide6 >= 6.11 exposes no GL_* enums on QOpenGLFunctions.

    The renderer previously read ``self._gl.GL_COLOR_BUFFER_BIT`` etc., which
    raises AttributeError on the first frame and leaves the viewport black
    (or aborts during initialize with the debug hooks).  All GL enum values
    must come from the module-level literals instead.
    """
    import re
    from pathlib import Path

    renderer_path = (
        Path(__file__).parent.parent.parent / "src" / "antcam_rc2" / "frontends" / "pyside" / "viewport" / "renderer.py"
    )
    text = renderer_path.read_text(encoding="utf-8")
    bad = re.findall(r"self\._gl\.GL_[A-Z_]+", text)
    assert bad == [], f"GL constants must be module literals, found: {sorted(set(bad))}"
    for constant in ("GL_COLOR_BUFFER_BIT", "GL_DEPTH_BUFFER_BIT", "GL_TRIANGLES", "GL_FRAMEBUFFER"):
        assert f"{constant} = " in text, f"missing literal {constant}"


def test_renderer_uses_real_gl_float_enum() -> None:
    """Regression: setAttributeBuffer must use GL_FLOAT (0x1406 = 5126), not 6.

    The renderer previously passed ``6`` as the attribute type, which is not a
    valid GL enum -> GL_INVALID_ENUM and invisible geometry (the root cause of
    the black viewport on real GL drivers).
    """
    import re
    from pathlib import Path

    renderer_path = (
        Path(__file__).parent.parent.parent / "src" / "antcam_rc2" / "frontends" / "pyside" / "viewport" / "renderer.py"
    )
    text = renderer_path.read_text(encoding="utf-8")
    bad = re.findall(r"setAttributeBuffer\(\s*[^,]+,\s*6\s*,", text)
    assert bad == [], f"setAttributeBuffer must use GL_FLOAT, found: {bad}"
    assert "GL_FLOAT = 0x1406" in text
    assert text.count("setAttributeBuffer(") == text.count("setAttributeBuffer(")
    assert "setAttributeBuffer(0, GL_FLOAT" in text
