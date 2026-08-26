import json
from types import SimpleNamespace

from typer.testing import CliRunner

from antcam import cli, main_viewer


runner = CliRunner()


def test_viewer_command_forwards_planner_config(monkeypatch):
    calls = {}

    def _run_feature_viewer(file_path, rx=0.0, ry=0.0, rz=0.0, planner_config=None, **kwargs):
        calls["viewer"] = {
            "file_path": file_path,
            "rx": rx,
            "ry": ry,
            "rz": rz,
            "planner_config": planner_config,
            "extra_kwargs": kwargs,
        }

    monkeypatch.setattr(main_viewer, "run_feature_viewer", _run_feature_viewer)

    result = runner.invoke(
        cli.app,
        [
            "viewer",
            "tests/data/flange.step",
            "--rx",
            "10",
            "--parameter-mode",
            "automatic",
            "--tool-diameter",
            "8",
            "--tool-library",
            "small_parts_mm",
            "--material-profile",
            "aluminum",
            "--drill-tool",
            "drill_4mm",
            "--mill-tool",
            "endmill_3mm",
            "--profile-stock-allowance",
            "0.35",
            "--profile-rough-only",
            "--lead-in",
            "1.5",
            "--material-factor",
            "0.8",
        ],
    )

    assert result.exit_code == 0
    assert calls["viewer"]["file_path"] == "tests/data/flange.step"
    assert calls["viewer"]["rx"] == 10.0
    assert calls["viewer"]["planner_config"].parameter_mode == "automatic"
    assert calls["viewer"]["planner_config"].tool_diameter == 8.0
    assert calls["viewer"]["planner_config"].tool_library == "small_parts_mm"
    assert calls["viewer"]["planner_config"].material_profile == "aluminum"
    assert calls["viewer"]["planner_config"].drill_tool_id == "drill_4mm"
    assert calls["viewer"]["planner_config"].mill_tool_id == "endmill_3mm"
    assert calls["viewer"]["planner_config"].profile_stock_allowance == 0.35
    assert calls["viewer"]["planner_config"].profile_rough_only is True
    assert calls["viewer"]["planner_config"].lead_in_distance == 1.5
    assert calls["viewer"]["planner_config"].material_factor == 0.8


def test_plan_command_builds_planner_from_cli_options(monkeypatch):
    calls = {}

    monkeypatch.setattr(cli, "_load_model", lambda file_path, rx, ry, rz: SimpleNamespace(brep=object(), mesh=None))

    class _FakeFeatureExtractor:
        def __init__(self, model):
            self.model = model
            self.working_plane_normal = [0.0, 0.0, 1.0]

        def extract(self):
            return [SimpleNamespace(type="hole_group", props={}, holes=[])]

    class _FakeContourExtractor:
        def __init__(self, model, working_plane_normal, features):
            self.model = model
            self.working_plane_normal = working_plane_normal
            self.features = features

        def extract(self):
            return "shadow"

        def extract_perimeter(self, contour_shadow):
            calls["contour_shadow"] = contour_shadow
            return ["wire_1"]

    def _build_planner(self, working_plane_normal):
        calls["planner_config"] = self
        calls["planner_normal"] = tuple(float(v) for v in working_plane_normal)

        class _FakePlanner:
            def generate(self, features, perimeter_wires=None):
                calls["generate"] = {
                    "features": features,
                    "perimeter_wires": perimeter_wires,
                }
                operations = [
                    SimpleNamespace(strategy="drilling"),
                    SimpleNamespace(strategy="2p5d_profile"),
                ]
                return SimpleNamespace(
                    operations=operations,
                    warnings=[],
                    to_dict=lambda: {"operation_count": len(operations), "warnings": []},
                )

        return _FakePlanner()

    monkeypatch.setattr(cli, "FeatureExtractor", _FakeFeatureExtractor)
    monkeypatch.setattr(cli, "ContourExtractor", _FakeContourExtractor)
    monkeypatch.setattr(cli.PlannerRuntimeConfig, "build_planner", _build_planner)

    result = runner.invoke(
        cli.app,
        [
            "plan",
            "tests/data/flange.step",
            "--parameter-mode",
            "automatic",
            "--tool-diameter",
            "8",
            "--tool-library",
            "standard_mm",
            "--material-profile",
            "steel",
            "--max-stepdown",
            "2.5",
            "--drill-tool",
            "drill_6mm",
            "--mill-tool",
            "endmill_4mm",
            "--profile-stock-allowance",
            "0.25",
            "--profile-rough-only",
            "--auto-chip-load",
            "0.015",
            "--auto-lead-ratio",
            "0.4",
        ],
    )

    assert result.exit_code == 0
    assert '"operation_count": 2' in result.stdout
    assert calls["planner_normal"] == (0.0, 0.0, 1.0)
    assert calls["generate"]["perimeter_wires"] == ["wire_1"]
    assert calls["planner_config"].parameter_mode == "automatic"
    assert calls["planner_config"].tool_diameter == 8.0
    assert calls["planner_config"].tool_library == "standard_mm"
    assert calls["planner_config"].material_profile == "steel"
    assert calls["planner_config"].max_stepdown == 2.5
    assert calls["planner_config"].drill_tool_id == "drill_6mm"
    assert calls["planner_config"].mill_tool_id == "endmill_4mm"
    assert calls["planner_config"].profile_stock_allowance == 0.25
    assert calls["planner_config"].profile_rough_only is True
    assert calls["planner_config"].automatic_chip_load == 0.015
    assert calls["planner_config"].automatic_lead_ratio == 0.4


def test_viewer_command_accepts_external_library_files(tmp_path, monkeypatch):
    calls = {}

    tool_library_file = tmp_path / "tool_libraries.json"
    tool_library_file.write_text(
        json.dumps(
            {
                "custom_tools": [
                    {
                        "tool_id": "custom_drill",
                        "name": "Custom Drill",
                        "tool_type": "drill",
                        "diameter": 5.0,
                        "flute_count": 2,
                    },
                    {
                        "tool_id": "custom_mill",
                        "name": "Custom Mill",
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
                    "surface_speed_m_per_min": 300.0,
                    "chip_load": 0.05,
                    "plunge_ratio": 0.6,
                    "stepdown_ratio": 0.9,
                    "lead_ratio": 0.45,
                    "material_factor": 1.2,
                }
            }
        ),
        encoding="utf-8",
    )

    def _run_feature_viewer(file_path, rx=0.0, ry=0.0, rz=0.0, planner_config=None, **kwargs):
        calls["viewer"] = {
            "file_path": file_path,
            "planner_config": planner_config,
        }

    monkeypatch.setattr(main_viewer, "run_feature_viewer", _run_feature_viewer)

    result = runner.invoke(
        cli.app,
        [
            "viewer",
            "tests/data/flange.step",
            "--tool-library",
            "custom_tools",
            "--tool-library-file",
            str(tool_library_file),
            "--material-profile",
            "foam",
            "--material-profile-file",
            str(material_profile_file),
            "--drill-tool",
            "custom_drill",
            "--mill-tool",
            "custom_mill",
        ],
    )

    assert result.exit_code == 0
    assert calls["viewer"]["planner_config"].tool_library == "custom_tools"
    assert calls["viewer"]["planner_config"].tool_library_file == str(tool_library_file)
    assert calls["viewer"]["planner_config"].material_profile == "foam"
    assert calls["viewer"]["planner_config"].material_profile_file == str(material_profile_file)
    assert calls["viewer"]["planner_config"].drill_tool_id == "custom_drill"
    assert calls["viewer"]["planner_config"].mill_tool_id == "custom_mill"