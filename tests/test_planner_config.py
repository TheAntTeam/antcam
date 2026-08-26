import math
import json

import pytest

from antcam.path_generator import OperationRequest
from antcam.planner_config import PlannerRuntimeConfig


def test_runtime_config_uses_builtin_library_and_material_profile():
    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="small_parts_mm",
        material_profile="aluminum",
        tool_diameter=2.5,
    )

    tool_manager = config.build_tool_manager()
    resolver = config.build_parameter_resolver()

    assert tool_manager.get_tool("endmill_2mm") is not None
    assert tool_manager.get_tool("drill_1_5mm") is not None
    assert resolver.automatic_surface_speed_m_per_min == 250.0
    assert resolver.automatic_chip_load == 0.04
    assert resolver.automatic_plunge_ratio == 0.45
    assert resolver.automatic_stepdown_ratio == 0.7
    assert resolver.automatic_lead_ratio == 0.35
    assert resolver.material_factor == pytest.approx(1.1)


def test_runtime_config_rejects_tool_not_in_selected_library():
    with pytest.raises(ValueError, match="drill_tool_id"):
        PlannerRuntimeConfig(tool_library="small_parts_mm", drill_tool_id="drill_10mm")


def test_runtime_config_explicit_automatic_override_wins_over_material_profile():
    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        material_profile="steel",
        automatic_chip_load=0.021,
        automatic_lead_ratio=0.4,
    )

    resolver = config.build_parameter_resolver()

    assert resolver.automatic_surface_speed_m_per_min == 90.0
    assert resolver.automatic_chip_load == 0.021
    assert resolver.automatic_lead_ratio == 0.4
    assert resolver.material_factor == pytest.approx(0.85)


def test_runtime_config_build_planner_forwards_profile_rough_only_flag():
    config = PlannerRuntimeConfig(profile_rough_only=True, profile_stock_allowance=0.3)

    planner = config.build_planner((0.0, 0.0, 1.0))

    assert planner.profile_rough_only is True


def test_runtime_config_build_planner_forwards_piece_roughing_only_flag():
    config = PlannerRuntimeConfig(piece_roughing_only=True)

    planner = config.build_planner((0.0, 0.0, 1.0))

    assert planner.piece_roughing_only is True


def test_runtime_config_uses_external_library_files(tmp_path):
    tool_library_file = tmp_path / "tool_libraries.json"
    tool_library_file.write_text(
        json.dumps(
            {
                "fixture_tools": [
                    {
                        "tool_id": "fixture_drill",
                        "name": "Fixture Drill",
                        "tool_type": "drill",
                        "diameter": 5.0,
                        "flute_count": 2,
                    },
                    {
                        "tool_id": "fixture_mill",
                        "name": "Fixture Mill",
                        "tool_type": "end_mill",
                        "diameter": 4.0,
                        "flute_count": 3,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    material_profile_file = tmp_path / "material_profiles.json"
    material_profile_file.write_text(
        json.dumps(
            {
                "foam": {
                    "profile_id": "foam",
                    "name": "Foam",
                    "surface_speed_m_per_min": 320.0,
                    "chip_load": 0.05,
                    "plunge_ratio": 0.55,
                    "stepdown_ratio": 0.85,
                    "lead_ratio": 0.4,
                    "material_factor": 1.25,
                }
            }
        ),
        encoding="utf-8",
    )

    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="fixture_tools",
        tool_library_file=str(tool_library_file),
        material_profile="foam",
        material_profile_file=str(material_profile_file),
        tool_diameter=4.0,
        drill_tool_id="fixture_drill",
        mill_tool_id="fixture_mill",
    )

    tool_manager = config.build_tool_manager()
    resolver = config.build_parameter_resolver()

    assert tool_manager.get_tool("fixture_drill") is not None
    assert tool_manager.get_tool("fixture_mill") is not None
    assert resolver.automatic_surface_speed_m_per_min == 320.0
    assert resolver.automatic_chip_load == 0.05
    assert resolver.automatic_plunge_ratio == 0.55
    assert resolver.automatic_stepdown_ratio == 0.85
    assert resolver.automatic_lead_ratio == 0.4
    assert resolver.material_factor == pytest.approx(1.25)


def test_runtime_config_applies_strategy_specific_automatic_overrides(tmp_path):
    tool_library_file = tmp_path / "tool_libraries.json"
    tool_library_file.write_text(
        json.dumps(
            {
                "fixture_tools": [
                    {
                        "tool_id": "fixture_drill",
                        "name": "Fixture Drill",
                        "tool_type": "drill",
                        "diameter": 5.0,
                        "flute_count": 2
                    },
                    {
                        "tool_id": "fixture_mill",
                        "name": "Fixture Mill",
                        "tool_type": "end_mill",
                        "diameter": 4.0,
                        "flute_count": 3
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    material_profile_file = tmp_path / "material_profiles.json"
    material_profile_file.write_text(
        json.dumps(
            {
                "foam": {
                    "profile_id": "foam",
                    "name": "Foam",
                    "surface_speed_m_per_min": 200.0,
                    "chip_load": 0.04,
                    "plunge_ratio": 0.5,
                    "stepdown_ratio": 0.6,
                    "lead_ratio": 0.3,
                    "material_factor": 1.0,
                    "automatic_strategy_overrides": {
                        "drilling": {
                            "surface_speed_m_per_min": 100.0,
                            "chip_load": 0.01,
                            "stepdown_ratio": 2.0
                        },
                        "slot_milling": {
                            "chip_load": 0.02,
                            "plunge_ratio": 0.25,
                            "stepdown_ratio": 0.25,
                            "lead_ratio": 0.1
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="fixture_tools",
        tool_library_file=str(tool_library_file),
        material_profile="foam",
        material_profile_file=str(material_profile_file),
    )

    tool_manager = config.build_tool_manager()
    resolver = config.build_parameter_resolver()
    drill_tool = tool_manager.get_tool("fixture_drill")
    mill_tool = tool_manager.get_tool("fixture_mill")
    assert drill_tool is not None
    assert mill_tool is not None

    drill_params = resolver.resolve(
        OperationRequest(strategy="drilling", feature_type="hole_group", depth=6.0, diameter=5.0),
        drill_tool,
    )
    drill_spindle = (100.0 * 1000.0) / (math.pi * 5.0)
    assert drill_params.spindle_speed == pytest.approx(drill_spindle, rel=1e-4)
    assert drill_params.cut_feed == pytest.approx(drill_spindle * 0.005, rel=1e-4)
    assert drill_params.max_stepdown == 10.0
    assert drill_params.lead_in_distance == 0.0

    slot_params = resolver.resolve(
        OperationRequest(strategy="slot_milling", feature_type="slot", depth=6.0, width=4.5, length=20.0),
        mill_tool,
    )
    slot_spindle = (200.0 * 1000.0) / (math.pi * 4.0)
    slot_cut_feed = slot_spindle * 3 * 0.02
    assert slot_params.spindle_speed == pytest.approx(slot_spindle, rel=1e-4)
    assert slot_params.cut_feed == pytest.approx(slot_cut_feed, rel=1e-4)
    assert slot_params.plunge_feed == pytest.approx(slot_cut_feed * 0.25, rel=1e-4)
    assert slot_params.max_stepdown == 1.0
    assert slot_params.lead_in_distance == 0.4
    assert slot_params.lead_out_distance == 0.4


def test_runtime_config_mode_overrides_can_split_same_strategy(tmp_path):
    tool_library_file = tmp_path / "tool_libraries.json"
    tool_library_file.write_text(
        json.dumps(
            {
                "finish_tools": [
                    {
                        "tool_id": "universal_endmill",
                        "name": "Universal End Mill",
                        "tool_type": "end_mill",
                        "diameter": 4.0,
                        "flute_count": 3,
                        "flute_length": 12.0,
                        "stickout": 24.0,
                        "tool_material": "carbide",
                        "compatible_materials": ["resin"],
                        "supported_strategies": ["2p5d_profile"],
                        "supported_modes": ["roughing", "finishing"]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    material_profile_file = tmp_path / "material_profiles.json"
    material_profile_file.write_text(
        json.dumps(
            {
                "resin": {
                    "profile_id": "resin",
                    "name": "Resin",
                    "surface_speed_m_per_min": 150.0,
                    "chip_load": 0.02,
                    "plunge_ratio": 0.4,
                    "stepdown_ratio": 0.5,
                    "lead_ratio": 0.2,
                    "material_factor": 1.0,
                    "automatic_mode_overrides": {
                        "roughing": {
                            "chip_load": 0.03,
                            "stepdown_ratio": 0.7,
                            "lead_ratio": 0.18
                        },
                        "finishing": {
                            "chip_load": 0.01,
                            "stepdown_ratio": 0.15,
                            "lead_ratio": 0.04,
                            "stepover_ratio": 0.1
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="finish_tools",
        tool_library_file=str(tool_library_file),
        material_profile="resin",
        material_profile_file=str(material_profile_file),
    )

    tool = config.build_tool_manager().get_tool("universal_endmill")
    assert tool is not None
    resolver = config.build_parameter_resolver()

    roughing = resolver.resolve(
        OperationRequest(
            strategy="2p5d_profile",
            feature_type="perimeter",
            operation_mode="roughing",
            depth=6.0,
            span_u=20.0,
            span_v=12.0,
        ),
        tool,
    )
    finishing = resolver.resolve(
        OperationRequest(
            strategy="2p5d_profile",
            feature_type="perimeter",
            operation_mode="finishing",
            depth=6.0,
            span_u=20.0,
            span_v=12.0,
        ),
        tool,
    )

    assert roughing.cut_feed > finishing.cut_feed
    assert roughing.max_stepdown > finishing.max_stepdown
    assert roughing.lead_in_distance > finishing.lead_in_distance
    assert finishing.stepover_ratio == 0.1


def test_runtime_config_builtin_library_prefers_mode_specific_profile_tools():
    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="standard_mm",
        material_profile="aluminum",
        tool_diameter=6.0,
        profile_stock_allowance=0.4,
    )
    planner = config.build_planner((0.0, 0.0, 1.0))
    square = [
        (0.0, 0.0, -2.0),
        (10.0, 0.0, -2.0),
        (10.0, 10.0, -2.0),
        (0.0, 10.0, -2.0),
    ]

    plan = planner.generate(features=[], perimeter_polylines=[square])
    profile_ops = [op for op in plan.operations if op.strategy == "2p5d_profile"]

    assert [op.metadata["operation_mode"] for op in profile_ops] == ["roughing", "finishing"]
    assert profile_ops[0].metadata["tool_id"] == "endmill_6mm_rougher"
    assert profile_ops[0].metadata["radial_stock_to_leave"] == 0.4
    assert profile_ops[1].metadata["tool_id"] == "endmill_6mm_finisher"


def test_runtime_config_strategy_mode_overrides_can_split_profile_strategy(tmp_path):
    tool_library_file = tmp_path / "tool_libraries.json"
    tool_library_file.write_text(
        json.dumps(
            {
                "profile_tools": [
                    {
                        "tool_id": "profile_endmill",
                        "name": "Profile End Mill",
                        "tool_type": "end_mill",
                        "diameter": 6.0,
                        "flute_count": 3,
                        "supported_strategies": ["2p5d_profile"],
                        "supported_modes": ["roughing", "finishing"]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    material_profile_file = tmp_path / "material_profiles.json"
    material_profile_file.write_text(
        json.dumps(
            {
                "foam": {
                    "profile_id": "foam",
                    "name": "Foam",
                    "surface_speed_m_per_min": 200.0,
                    "chip_load": 0.04,
                    "plunge_ratio": 0.5,
                    "stepdown_ratio": 0.6,
                    "lead_ratio": 0.3,
                    "material_factor": 1.0,
                    "automatic_mode_overrides": {
                        "roughing": {
                            "chip_load": 0.03,
                            "stepdown_ratio": 0.7,
                            "stepover_ratio": 0.35,
                            "lead_ratio": 0.18
                        },
                        "finishing": {
                            "chip_load": 0.01,
                            "stepdown_ratio": 0.15,
                            "stepover_ratio": 0.1,
                            "lead_ratio": 0.04
                        }
                    },
                    "automatic_strategy_mode_overrides": {
                        "2p5d_profile": {
                            "roughing": {
                                "chip_load": 0.05,
                                "stepdown_ratio": 0.9,
                                "stepover_ratio": 0.42,
                                "lead_ratio": 0.11
                            },
                            "finishing": {
                                "chip_load": 0.008,
                                "plunge_ratio": 0.2,
                                "stepdown_ratio": 0.11,
                                "stepover_ratio": 0.06,
                                "lead_ratio": 0.02
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="profile_tools",
        tool_library_file=str(tool_library_file),
        material_profile="foam",
        material_profile_file=str(material_profile_file),
        tool_diameter=6.0,
    )

    tool_manager = config.build_tool_manager()
    resolver = config.build_parameter_resolver()
    tool = tool_manager.get_tool("profile_endmill")
    assert tool is not None

    roughing = resolver.resolve(
        OperationRequest(
            strategy="2p5d_profile",
            feature_type="perimeter",
            operation_mode="roughing",
            depth=4.0,
            span_u=20.0,
            span_v=12.0,
        ),
        tool,
    )
    finishing = resolver.resolve(
        OperationRequest(
            strategy="2p5d_profile",
            feature_type="perimeter",
            operation_mode="finishing",
            depth=4.0,
            span_u=20.0,
            span_v=12.0,
        ),
        tool,
    )

    assert roughing.stepover_ratio == 0.42
    assert roughing.max_stepdown == pytest.approx(5.4)
    assert roughing.lead_in_distance == pytest.approx(0.66)
    assert finishing.stepover_ratio == 0.06
    assert finishing.max_stepdown == pytest.approx(0.66)
    assert finishing.lead_in_distance == pytest.approx(0.12)
    assert roughing.cut_feed > finishing.cut_feed
    assert finishing.plunge_feed < finishing.cut_feed


def test_runtime_config_builtin_material_profile_has_profile_specific_mode_tuning():
    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="standard_mm",
        material_profile="aluminum",
        tool_diameter=6.0,
    )

    tool_manager = config.build_tool_manager()
    resolver = config.build_parameter_resolver()
    roughing_request = OperationRequest(
        strategy="2p5d_profile",
        feature_type="perimeter",
        operation_mode="roughing",
        depth=4.0,
        span_u=20.0,
        span_v=12.0,
    )
    finishing_request = OperationRequest(
        strategy="2p5d_profile",
        feature_type="perimeter",
        operation_mode="finishing",
        depth=4.0,
        span_u=20.0,
        span_v=12.0,
    )

    roughing_tool = tool_manager.select_tool(roughing_request)
    finishing_tool = tool_manager.select_tool(finishing_request)
    assert roughing_tool is not None
    assert finishing_tool is not None

    roughing = resolver.resolve(roughing_request, roughing_tool)
    finishing = resolver.resolve(finishing_request, finishing_tool)

    assert roughing_tool.tool_id == "endmill_6mm_rougher"
    assert finishing_tool.tool_id == "endmill_6mm_finisher"
    assert roughing.stepover_ratio == 0.42
    assert roughing.max_stepdown == pytest.approx(5.1)
    assert roughing.lead_in_distance == pytest.approx(0.72)
    assert finishing.stepover_ratio == 0.07
    assert finishing.max_stepdown == pytest.approx(0.96)
    assert finishing.lead_in_distance == pytest.approx(0.21)


def test_runtime_config_builtin_material_profile_has_slot_specific_mode_tuning():
    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="standard_mm",
        material_profile="aluminum",
        tool_diameter=6.0,
    )

    tool_manager = config.build_tool_manager()
    resolver = config.build_parameter_resolver()
    roughing_request = OperationRequest(
        strategy="slot_milling",
        feature_type="slot",
        operation_mode="roughing",
        depth=4.0,
        width=8.0,
        length=20.0,
    )
    finishing_request = OperationRequest(
        strategy="slot_milling",
        feature_type="slot",
        operation_mode="finishing",
        depth=4.0,
        width=8.0,
        length=20.0,
    )

    roughing_tool = tool_manager.select_tool(roughing_request)
    finishing_tool = tool_manager.select_tool(finishing_request)
    assert roughing_tool is not None
    assert finishing_tool is not None

    roughing = resolver.resolve(roughing_request, roughing_tool)
    finishing = resolver.resolve(finishing_request, finishing_tool)

    assert roughing_tool.tool_id == "endmill_6mm_rougher"
    assert finishing_tool.tool_id == "endmill_6mm_finisher"
    assert roughing.max_stepdown == pytest.approx(3.0)
    assert roughing.lead_in_distance == pytest.approx(0.96)
    assert finishing.max_stepdown == pytest.approx(0.84)
    assert finishing.lead_in_distance == pytest.approx(0.24)
    assert roughing.cut_feed > finishing.cut_feed
    assert finishing.plunge_feed == pytest.approx(finishing.cut_feed * 0.24)


def test_runtime_config_builtin_material_profile_has_cavity_specific_mode_tuning():
    config = PlannerRuntimeConfig(
        parameter_mode="automatic",
        tool_library="standard_mm",
        material_profile="aluminum",
        tool_diameter=6.0,
    )

    tool_manager = config.build_tool_manager()
    resolver = config.build_parameter_resolver()
    roughing_request = OperationRequest(
        strategy="cavity_clearing",
        feature_type="pocket",
        operation_mode="roughing",
        depth=4.0,
        span_u=20.0,
        span_v=12.0,
    )
    finishing_request = OperationRequest(
        strategy="cavity_clearing",
        feature_type="pocket",
        operation_mode="finishing",
        depth=4.0,
        span_u=20.0,
        span_v=12.0,
    )

    roughing_tool = tool_manager.select_tool(roughing_request)
    finishing_tool = tool_manager.select_tool(finishing_request)
    assert roughing_tool is not None
    assert finishing_tool is not None

    roughing = resolver.resolve(roughing_request, roughing_tool)
    finishing = resolver.resolve(finishing_request, finishing_tool)

    assert roughing_tool.tool_id == "endmill_6mm_rougher"
    assert finishing_tool.tool_id == "endmill_6mm_finisher"
    assert roughing.stepover_ratio == 0.48
    assert roughing.max_stepdown == pytest.approx(3.6)
    assert roughing.lead_in_distance == pytest.approx(1.44)
    assert finishing.stepover_ratio == 0.08
    assert finishing.max_stepdown == pytest.approx(0.96)
    assert finishing.lead_in_distance == pytest.approx(0.24)
    assert roughing.cut_feed > finishing.cut_feed