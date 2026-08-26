"""Tool and material preset loading for early CAM planning.

The default libraries ship as JSON files inside the package, and callers can
optionally point to external JSON files to override them without touching code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from antcam.path_generator import AutomaticParameterOverrides, ToolDefinition


@dataclass(frozen=True)
class MaterialProfile:
    """Named automatic-machining preset for a material family."""

    profile_id: str
    name: str
    surface_speed_m_per_min: float
    chip_load: float
    plunge_ratio: float
    stepdown_ratio: float
    lead_ratio: float
    material_factor: float = 1.0
    automatic_mode_overrides: dict[str, AutomaticParameterOverrides] = field(default_factory=dict)
    automatic_strategy_overrides: dict[str, AutomaticParameterOverrides] = field(default_factory=dict)
    automatic_strategy_mode_overrides: dict[str, dict[str, AutomaticParameterOverrides]] = field(default_factory=dict)
    automatic_feature_overrides: dict[str, AutomaticParameterOverrides] = field(default_factory=dict)


PACKAGE_DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_TOOL_LIBRARY_FILE = PACKAGE_DATA_DIR / "tool_libraries.json"
DEFAULT_MATERIAL_PROFILE_FILE = PACKAGE_DATA_DIR / "material_profiles.json"


def _resolve_data_path(raw_path: Optional[str], default_path: Path) -> Path:
    if raw_path is None:
        return default_path
    return Path(raw_path).expanduser().resolve()


def _load_json_mapping(raw_path: Optional[str], default_path: Path, label: str) -> dict[str, Any]:
    path = _resolve_data_path(raw_path, default_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} file is not valid JSON: {path} ({exc.msg})") from exc
    except OSError as exc:
        raise ValueError(f"{label} file could not be read: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{label} file must contain a JSON object at the root: {path}")
    return payload


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _tool_from_dict(payload: Any) -> ToolDefinition:
    if not isinstance(payload, dict):
        raise ValueError("Each tool definition must be a JSON object")
    try:
        return ToolDefinition(
            tool_id=str(payload["tool_id"]),
            name=str(payload["name"]),
            tool_type=str(payload["tool_type"]).strip().lower(),
            diameter=float(payload["diameter"]),
            flute_count=int(payload.get("flute_count", 2)),
            spindle_speed=_optional_float(payload.get("spindle_speed")),
            cut_feed=_optional_float(payload.get("cut_feed")),
            plunge_feed=_optional_float(payload.get("plunge_feed")),
            max_stepdown=_optional_float(payload.get("max_stepdown")),
            stepover_ratio=_optional_float(payload.get("stepover_ratio")),
            flute_length=_optional_float(payload.get("flute_length")),
            stickout=_optional_float(payload.get("stickout")),
            tool_material=str(payload["tool_material"]).strip().lower() if payload.get("tool_material") is not None else None,
            compatible_materials=tuple(str(value).strip().lower() for value in payload["compatible_materials"])
            if payload.get("compatible_materials") is not None
            else None,
            max_depth=_optional_float(payload.get("max_depth")),
            supported_strategies=tuple(str(value).strip().lower() for value in payload["supported_strategies"])
            if payload.get("supported_strategies") is not None
            else None,
            supported_modes=tuple(str(value).strip().lower() for value in payload["supported_modes"])
            if payload.get("supported_modes") is not None
            else None,
        )
    except KeyError as exc:
        raise ValueError(f"Tool definition is missing required key: {exc.args[0]}") from exc


def _automatic_parameter_overrides_from_dict(payload: Any) -> AutomaticParameterOverrides:
    if not isinstance(payload, dict):
        raise ValueError("Automatic override entry must be a JSON object")
    return AutomaticParameterOverrides(
        surface_speed_m_per_min=_optional_float(payload.get("surface_speed_m_per_min")),
        chip_load=_optional_float(payload.get("chip_load")),
        plunge_ratio=_optional_float(payload.get("plunge_ratio")),
        stepdown_ratio=_optional_float(payload.get("stepdown_ratio")),
        lead_ratio=_optional_float(payload.get("lead_ratio")),
        stepover_ratio=_optional_float(payload.get("stepover_ratio")),
        material_factor=_optional_float(payload.get("material_factor")),
    )


def _automatic_override_map(payload: Any, label: str) -> dict[str, AutomaticParameterOverrides]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object keyed by strategy or feature name")
    return {
        str(key).strip().lower(): _automatic_parameter_overrides_from_dict(value)
        for key, value in payload.items()
    }


def _automatic_nested_override_map(payload: Any, label: str) -> dict[str, dict[str, AutomaticParameterOverrides]]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object keyed first by strategy and then by mode")

    nested_overrides: dict[str, dict[str, AutomaticParameterOverrides]] = {}
    for strategy_key, strategy_payload in payload.items():
        if not isinstance(strategy_payload, dict):
            raise ValueError(f"{label} entries must be JSON objects keyed by mode")
        nested_overrides[str(strategy_key).strip().lower()] = {
            str(mode_key).strip().lower(): _automatic_parameter_overrides_from_dict(mode_payload)
            for mode_key, mode_payload in strategy_payload.items()
        }
    return nested_overrides


def _material_profile_from_dict(payload: Any) -> MaterialProfile:
    if not isinstance(payload, dict):
        raise ValueError("Each material profile must be a JSON object")
    try:
        return MaterialProfile(
            profile_id=str(payload["profile_id"]),
            name=str(payload["name"]),
            surface_speed_m_per_min=float(payload["surface_speed_m_per_min"]),
            chip_load=float(payload["chip_load"]),
            plunge_ratio=float(payload["plunge_ratio"]),
            stepdown_ratio=float(payload["stepdown_ratio"]),
            lead_ratio=float(payload["lead_ratio"]),
            material_factor=float(payload.get("material_factor", 1.0)),
            automatic_mode_overrides=_automatic_override_map(
                payload.get("automatic_mode_overrides"),
                "automatic_mode_overrides",
            ),
            automatic_strategy_overrides=_automatic_override_map(
                payload.get("automatic_strategy_overrides"),
                "automatic_strategy_overrides",
            ),
            automatic_strategy_mode_overrides=_automatic_nested_override_map(
                payload.get("automatic_strategy_mode_overrides"),
                "automatic_strategy_mode_overrides",
            ),
            automatic_feature_overrides=_automatic_override_map(
                payload.get("automatic_feature_overrides"),
                "automatic_feature_overrides",
            ),
        )
    except KeyError as exc:
        raise ValueError(f"Material profile is missing required key: {exc.args[0]}") from exc


def _load_tool_libraries(tool_library_file: Optional[str] = None) -> dict[str, tuple[ToolDefinition, ...]]:
    payload = _load_json_mapping(tool_library_file, DEFAULT_TOOL_LIBRARY_FILE, "Tool library")
    libraries: dict[str, tuple[ToolDefinition, ...]] = {}
    for library_id, tools in payload.items():
        if not isinstance(tools, list):
            raise ValueError(f"Tool library '{library_id}' must be a JSON array of tools")
        libraries[str(library_id).strip().lower()] = tuple(_tool_from_dict(tool) for tool in tools)
    return libraries


def _load_material_profiles(material_profile_file: Optional[str] = None) -> dict[str, MaterialProfile]:
    payload = _load_json_mapping(material_profile_file, DEFAULT_MATERIAL_PROFILE_FILE, "Material profile")
    profiles: dict[str, MaterialProfile] = {}
    for profile_id, profile_payload in payload.items():
        profiles[str(profile_id).strip().lower()] = _material_profile_from_dict(profile_payload)
    return profiles


def list_tool_libraries(tool_library_file: Optional[str] = None) -> tuple[str, ...]:
    return tuple(_load_tool_libraries(tool_library_file))


def list_material_profiles(material_profile_file: Optional[str] = None) -> tuple[str, ...]:
    return tuple(_load_material_profiles(material_profile_file))


def list_builtin_tool_libraries() -> tuple[str, ...]:
    return list_tool_libraries()


def list_builtin_material_profiles() -> tuple[str, ...]:
    return list_material_profiles()


def get_tool_library(library_id: str, tool_library_file: Optional[str] = None) -> list[ToolDefinition]:
    libraries = _load_tool_libraries(tool_library_file)
    key = library_id.strip().lower()
    if key not in libraries:
        choices = ", ".join(sorted(libraries))
        raise ValueError(f"Unknown tool_library '{library_id}'. Available: {choices}")
    return [ToolDefinition(**tool.__dict__) for tool in libraries[key]]


def get_material_profile(profile_id: str, material_profile_file: Optional[str] = None) -> MaterialProfile:
    profiles = _load_material_profiles(material_profile_file)
    key = profile_id.strip().lower()
    if key not in profiles:
        choices = ", ".join(sorted(profiles))
        raise ValueError(f"Unknown material_profile '{profile_id}'. Available: {choices}")
    return MaterialProfile(**profiles[key].__dict__)


def get_builtin_tool_library(library_id: str) -> list[ToolDefinition]:
    return get_tool_library(library_id)


def get_builtin_material_profile(profile_id: str) -> MaterialProfile:
    return get_material_profile(profile_id)