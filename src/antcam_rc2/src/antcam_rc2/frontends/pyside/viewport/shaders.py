"""GLSL sources for the viewport pipeline.

Shaders are plain string templates parameterized by the GLSL version so the
renderer can compile 4.50 core with a 3.30 core fallback.  Each template must
compile for both versions (the version string is injected as the first line).
"""

from __future__ import annotations

VERTEX_HEADER = """\
{version}

uniform mat4 u_view;
uniform mat4 u_projection;
"""

FRAGMENT_HEADER = """\
{version}
"""

_LINE_VERTEX = (
    VERTEX_HEADER
    + """\
layout(location = 0) in vec3 a_position;
layout(location = 1) in vec4 a_color;

out vec4 v_color;

void main() {{
    v_color = a_color;
    gl_Position = u_projection * u_view * vec4(a_position, 1.0);
}}
"""
)

_LINE_FRAGMENT = (
    FRAGMENT_HEADER
    + """\
in vec4 v_color;
out vec4 frag_color;

void main() {{
    frag_color = v_color;
}}
"""
)

_SOLID_VERTEX = (
    VERTEX_HEADER
    + """\
layout(location = 0) in vec3 a_position;
layout(location = 1) in vec3 a_normal;
layout(location = 2) in vec4 a_color;

out vec3 v_world_pos;
out vec3 v_normal;
out vec4 v_color;

void main() {{
    v_world_pos = a_position;
    v_normal = a_normal;
    v_color = a_color;
    gl_Position = u_projection * u_view * vec4(a_position, 1.0);
}}
"""
)

_SOLID_FRAGMENT = (
    FRAGMENT_HEADER
    + """\
uniform vec3 u_camera_pos;
uniform vec3 u_light_dir;          // direction light shines TOWARD the scene
uniform vec3 u_light_color;
uniform vec3 u_ambient;
uniform vec3 u_point_pos;
uniform vec3 u_point_color;
uniform float u_point_radius;
uniform sampler2D u_shadow_map;
uniform mat4 u_light_view_projection;
uniform vec2 u_shadow_texel;

in vec3 v_world_pos;
in vec3 v_normal;
in vec4 v_color;
out vec4 frag_color;

float shadow_attenuation(vec4 light_clip) {{
    vec3 ndc = light_clip.xyz / light_clip.w;
    vec2 uv = ndc.xy * 0.5 + 0.5;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0 || ndc.z < 0.0 || ndc.z > 1.0) {{
        return 1.0;
    }}
    float current = ndc.z;
    float shadow = 0.0;
    for (int x = -1; x <= 1; x++) {{
        for (int y = -1; y <= 1; y++) {{
            vec3 enc = texture(u_shadow_map, uv + vec2(float(x), float(y)) * u_shadow_texel).rgb;
            float closest = enc.r + enc.g / 255.0 + enc.b / 65025.0;
            shadow += (current - 0.0025) > closest ? 0.0 : 1.0;
        }}
    }}
    return shadow / 9.0;
}}

vec3 aces_tonemap(vec3 color) {{
    return clamp((color * (2.51 * color + 0.03)) / (color * (2.43 * color + 0.59) + 0.14), 0.0, 1.0);
}}

void main() {{
    vec3 normal = normalize(v_normal);
    vec3 view_dir = normalize(u_camera_pos - v_world_pos);
    float n_dot_l = max(dot(normal, u_light_dir), 0.0);

    vec4 light_clip = u_light_view_projection * vec4(v_world_pos, 1.0);
    float shadow = shadow_attenuation(light_clip);
    vec3 diffuse = u_light_color * n_dot_l * shadow;

    vec3 half_dir = normalize(u_light_dir + view_dir);
    float spec = pow(max(dot(normal, half_dir), 0.0), 32.0);
    vec3 specular = u_light_color * spec * 0.35;

    vec3 point_dir = u_point_pos - v_world_pos;
    float point_dist = length(point_dir);
    float point_atten = u_point_radius / (point_dist * point_dist + 1e-4);
    vec3 point_light = u_point_color * max(dot(normal, normalize(point_dir)), 0.0) * point_atten;

    vec3 lit = v_color.rgb * (u_ambient + diffuse + point_light) + specular;
    frag_color = vec4(aces_tonemap(lit), v_color.a);
}}
"""
)

_SHADOW_VERTEX = """\
{version}

layout(location = 0) in vec3 a_position;

uniform mat4 u_light_view_projection;

void main() {{
    gl_Position = u_light_view_projection * vec4(a_position, 1.0);
}}
"""

_SHADOW_FRAGMENT = """\
{version}

out vec4 frag_color;

void main() {{
    float z = gl_FragCoord.z;
    frag_color = vec4(z, fract(z * 255.0), fract(z * 65025.0), 1.0);
}}
"""

_POST_VERTEX = """\
{version}

layout(location = 0) in vec2 a_position;
layout(location = 1) in vec2 a_texcoord;

out vec2 v_texcoord;

void main() {{
    v_texcoord = a_texcoord;
    gl_Position = vec4(a_position, 0.0, 1.0);
}}
"""

_POST_FRAGMENT = """\
{version}

uniform sampler2D u_scene;
uniform float u_exposure;

in vec2 v_texcoord;
out vec4 frag_color;

vec3 aces_tonemap(vec3 color) {{
    color = color * u_exposure;
    return clamp((color * (2.51 * color + 0.03)) / (color * (2.43 * color + 0.59) + 0.14), 0.0, 1.0);
}}

void main() {{
    vec4 color = texture(u_scene, v_texcoord);
    frag_color = vec4(aces_tonemap(color.rgb), color.a);
}}
"""

_PICK_VERTEX = """\
{version}

layout(location = 0) in vec3 a_position;
layout(location = 1) in vec4 a_color;

uniform mat4 u_view;
uniform mat4 u_projection;

out vec4 v_id_color;

void main() {{
    v_id_color = a_color;
    gl_Position = u_projection * u_view * vec4(a_position, 1.0);
}}
"""

_PICK_FRAGMENT = """\
{version}

in vec4 v_id_color;
out vec4 frag_color;

void main() {{
    frag_color = v_id_color;
}}
"""

_BACKGROUND_VERTEX = """\
{version}

layout(location = 0) in vec2 a_position;

out vec2 v_uv;

void main() {{
    v_uv = a_position * 0.5 + 0.5;
    gl_Position = vec4(a_position, 0.0, 1.0);
}}
"""

_BACKGROUND_FRAGMENT = """\
{version}

uniform vec3 u_top_color;
uniform vec3 u_bottom_color;

in vec2 v_uv;
out vec4 frag_color;

void main() {{
    vec3 color = mix(u_bottom_color, u_top_color, pow(v_uv.y, 1.25));
    vec2 centered = v_uv - 0.5;
    float vignette = 1.0 - 0.30 * dot(centered, centered);
    frag_color = vec4(color * vignette, 1.0);
}}
"""

_GROUND_VERTEX = """\
{version}

layout(location = 0) in vec3 a_position;

uniform mat4 u_view;
uniform mat4 u_projection;

out vec3 v_world_pos;

void main() {{
    v_world_pos = a_position;
    gl_Position = u_projection * u_view * vec4(a_position, 1.0);
}}
"""

_GROUND_FRAGMENT = """\
{version}

uniform vec3 u_ground_color;
uniform vec3 u_ground_center;
uniform float u_ground_radius;
uniform sampler2D u_shadow_map;
uniform mat4 u_light_view_projection;
uniform vec2 u_shadow_texel;

in vec3 v_world_pos;
out vec4 frag_color;

float shadow_attenuation(vec4 light_clip) {{
    vec3 ndc = light_clip.xyz / light_clip.w;
    vec2 uv = ndc.xy * 0.5 + 0.5;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0 || ndc.z < 0.0 || ndc.z > 1.0) {{
        return 1.0;
    }}
    float current = ndc.z;
    float shadow = 0.0;
    for (int x = -1; x <= 1; x++) {{
        for (int y = -1; y <= 1; y++) {{
            vec3 enc = texture(u_shadow_map, uv + vec2(float(x), float(y)) * u_shadow_texel).rgb;
            float closest = enc.r + enc.g / 255.0 + enc.b / 65025.0;
            shadow += (current - 0.0025) > closest ? 0.0 : 1.0;
        }}
    }}
    return shadow / 9.0;
}}

void main() {{
    float d = length(v_world_pos.xy - u_ground_center.xy);
    float t = clamp(1.0 - d / max(u_ground_radius, 1e-3), 0.0, 1.0);
    float fade = t * t * (3.0 - 2.0 * t);
    vec4 light_clip = u_light_view_projection * vec4(v_world_pos, 1.0);
    float shadow = shadow_attenuation(light_clip);
    vec3 color = u_ground_color * mix(0.55, 1.0, shadow);
    frag_color = vec4(color, fade * 0.60);
}}
"""

_GRID_VERTEX = """\
{version}

layout(location = 0) in vec3 a_position;
layout(location = 1) in vec4 a_color;

uniform mat4 u_view;
uniform mat4 u_projection;

out vec4 v_color;
out vec3 v_world_pos;

void main() {{
    v_color = a_color;
    v_world_pos = a_position;
    gl_Position = u_projection * u_view * vec4(a_position, 1.0);
}}
"""

_GRID_FRAGMENT = """\
{version}

uniform vec3 u_camera_pos;
uniform float u_fade_near;
uniform float u_fade_far;

in vec4 v_color;
in vec3 v_world_pos;
out vec4 frag_color;

void main() {{
    float dist = length(u_camera_pos - v_world_pos);
    float t = clamp((u_fade_far - dist) / max(u_fade_far - u_fade_near, 1e-4), 0.0, 1.0);
    float fade = t * t * (3.0 - 2.0 * t);
    frag_color = vec4(v_color.rgb, v_color.a * fade);
}}
"""


def _with_version(source: str, version_line: str) -> str:
    return source.format(version=version_line)


def shader_sources(glsl_version: int = 450) -> dict[str, dict[str, str]]:
    """Return ``{program: {"vertex": ..., "fragment": ...}}`` for a GLSL version.

    ``glsl_version`` must be 330 or 450; the version statement is injected
    automatically.
    """
    if glsl_version not in {330, 450}:
        raise ValueError(f"unsupported GLSL version: {glsl_version}")
    version = f"#version {glsl_version}"
    return {
        "line": {
            "vertex": _with_version(_LINE_VERTEX, version),
            "fragment": _with_version(_LINE_FRAGMENT, version),
        },
        "solid": {
            "vertex": _with_version(_SOLID_VERTEX, version),
            "fragment": _with_version(_SOLID_FRAGMENT, version),
        },
        "shadow": {
            "vertex": _with_version(_SHADOW_VERTEX, version),
            "fragment": _with_version(_SHADOW_FRAGMENT, version),
        },
        "post": {
            "vertex": _with_version(_POST_VERTEX, version),
            "fragment": _with_version(_POST_FRAGMENT, version),
        },
        "picking": {
            "vertex": _with_version(_PICK_VERTEX, version),
            "fragment": _with_version(_PICK_FRAGMENT, version),
        },
        "grid": {
            "vertex": _with_version(_GRID_VERTEX, version),
            "fragment": _with_version(_GRID_FRAGMENT, version),
        },
        "background": {
            "vertex": _with_version(_BACKGROUND_VERTEX, version),
            "fragment": _with_version(_BACKGROUND_FRAGMENT, version),
        },
        "ground": {
            "vertex": _with_version(_GROUND_VERTEX, version),
            "fragment": _with_version(_GROUND_FRAGMENT, version),
        },
    }


__all__ = ["shader_sources"]
