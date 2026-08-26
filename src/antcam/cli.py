"""Command-line interface for AntCAM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console

from antcam.contour_extractor import ContourExtractor
from antcam.feature_extractor import FeatureExtractor
from antcam.importer import Model
from antcam.machining_library import list_tool_libraries, list_material_profiles
from antcam.planner_config import PlannerRuntimeConfig

app = typer.Typer(help="AntCAM CLI")
console = Console()

ParameterModeOption = Annotated[
    str,
    typer.Option(help="Planner mode: manual uses explicit feeds, automatic computes machining parameters.", case_sensitive=False),
]
SafeZOffsetOption = Annotated[float, typer.Option(help="Safe travel distance above the top projection.")]
ClearanceZOption = Annotated[float, typer.Option(help="Clearance distance above the current cut plane before plunging.")]
CutFeedOption = Annotated[float, typer.Option(help="Linear cutting feed used in manual mode.")]
PlungeFeedOption = Annotated[float, typer.Option(help="Plunge feed used in manual mode.")]
MaxStepdownOption = Annotated[float, typer.Option(help="Maximum axial cut per pass.")]
ToolDiameterOption = Annotated[float, typer.Option(help="Preferred base tool diameter for the default local tool set.")]
StepoverRatioOption = Annotated[float, typer.Option(help="Radial engagement ratio used in manual mode.")]
LeadInOption = Annotated[float, typer.Option("--lead-in", help="Lead-in distance used in manual mode.")]
LeadOutOption = Annotated[float, typer.Option("--lead-out", help="Lead-out distance used in manual mode.")]
ProfileStockAllowanceOption = Annotated[
    float,
    typer.Option("--profile-stock-allowance", help="Radial stock to leave for a rough profile pass before the final finishing profile."),
]
PerimeterProfileFallbackOption = Annotated[
    bool,
    typer.Option(
        "--enable-perimeter-profile-fallback/--disable-perimeter-profile-fallback",
        help="Allow automatic 2.5D profile cuts from contour silhouettes even when semantic operations already exist. Disabled by default because it can be unsafe without setup/stock context.",
    ),
]
ProfileRoughOnlyOption = Annotated[
    bool,
    typer.Option(
        "--profile-rough-only/--disable-profile-rough-only",
        help="Debug mode: emit only 2.5D profile roughing operations from perimeter geometry and disable drilling, slot milling, cavity clearing, and profile finishing.",
    ),
]
ToolLibraryOption = Annotated[
    str,
    typer.Option(help=f"Tool library ID. Built-in libraries: {', '.join(list_tool_libraries())}."),
]
MaterialProfileOption = Annotated[
    str,
    typer.Option(help=f"Material profile ID. Built-in profiles: {', '.join(list_material_profiles())}."),
]
ToolLibraryFileOption = Annotated[
    Optional[str],
    typer.Option("--tool-library-file", help="Optional JSON file containing tool libraries; --tool-library selects the library inside that file."),
]
MaterialProfileFileOption = Annotated[
    Optional[str],
    typer.Option("--material-profile-file", help="Optional JSON file containing material profiles; --material-profile selects the profile inside that file."),
]
DrillToolOption = Annotated[
    Optional[str],
    typer.Option("--drill-tool", help="Optional built-in drill tool ID to force for drilling."),
]
MillToolOption = Annotated[
    Optional[str],
    typer.Option("--mill-tool", help="Optional built-in end-mill tool ID to force for milling strategies."),
]
AutoSurfaceSpeedOption = Annotated[
    Optional[float],
    typer.Option("--auto-surface-speed", help="Automatic mode surface speed in m/min."),
]
AutoChipLoadOption = Annotated[
    Optional[float],
    typer.Option("--auto-chip-load", help="Automatic mode chip load in mm/tooth."),
]
AutoPlungeRatioOption = Annotated[
    Optional[float],
    typer.Option("--auto-plunge-ratio", help="Automatic mode plunge ratio relative to cut feed."),
]
AutoStepdownRatioOption = Annotated[
    Optional[float],
    typer.Option("--auto-stepdown-ratio", help="Automatic mode stepdown ratio relative to tool diameter."),
]
AutoLeadRatioOption = Annotated[
    Optional[float],
    typer.Option("--auto-lead-ratio", help="Automatic mode lead distance ratio relative to tool diameter."),
]
MaterialFactorOption = Annotated[float, typer.Option(help="Global material multiplier applied in automatic mode.")]


def _load_model(file_path: str, rx: float, ry: float, rz: float) -> Model:
    ext = Path(file_path).suffix.lower()
    if ext in {".step", ".stp"}:
        return Model.from_step(file_path, rx=rx, ry=ry, rz=rz)
    if ext == ".stl":
        return Model.from_stl(file_path)
    raise typer.BadParameter(f"Unsupported file format: {ext}. Use STEP/STP or STL")


def _build_planner_config(
    parameter_mode: str,
    safe_z_offset: float,
    clearance_z: float,
    cut_feed: float,
    plunge_feed: float,
    max_stepdown: float,
    tool_diameter: float,
    stepover_ratio: float,
    lead_in_distance: float,
    lead_out_distance: float,
    profile_stock_allowance: float,
    enable_perimeter_profile_fallback: bool,
    profile_rough_only: bool,
    tool_library: str,
    tool_library_file: Optional[str],
    material_profile: str,
    material_profile_file: Optional[str],
    drill_tool_id: Optional[str],
    mill_tool_id: Optional[str],
    automatic_surface_speed_m_per_min: Optional[float],
    automatic_chip_load: Optional[float],
    automatic_plunge_ratio: Optional[float],
    automatic_stepdown_ratio: Optional[float],
    automatic_lead_ratio: Optional[float],
    material_factor: float,
) -> PlannerRuntimeConfig:
    try:
        return PlannerRuntimeConfig(
            parameter_mode=parameter_mode,
            safe_z_offset=safe_z_offset,
            clearance_z=clearance_z,
            cut_feed=cut_feed,
            plunge_feed=plunge_feed,
            max_stepdown=max_stepdown,
            tool_diameter=tool_diameter,
            stepover_ratio=stepover_ratio,
            lead_in_distance=lead_in_distance,
            lead_out_distance=lead_out_distance,
            profile_stock_allowance=profile_stock_allowance,
            enable_perimeter_profile_fallback=enable_perimeter_profile_fallback,
            profile_rough_only=profile_rough_only,
            tool_library=tool_library,
            tool_library_file=tool_library_file,
            material_profile=material_profile,
            material_profile_file=material_profile_file,
            drill_tool_id=drill_tool_id,
            mill_tool_id=mill_tool_id,
            automatic_surface_speed_m_per_min=automatic_surface_speed_m_per_min,
            automatic_chip_load=automatic_chip_load,
            automatic_plunge_ratio=automatic_plunge_ratio,
            automatic_stepdown_ratio=automatic_stepdown_ratio,
            automatic_lead_ratio=automatic_lead_ratio,
            material_factor=material_factor,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def viewer(
    file_path: str = typer.Argument(..., help="CAD file path (.step/.stp/.stl)"),
    rx: float = typer.Option(0.0, help="Rotation around X in degrees (STEP only)"),
    ry: float = typer.Option(0.0, help="Rotation around Y in degrees (STEP only)"),
    rz: float = typer.Option(0.0, help="Rotation around Z in degrees (STEP only)"),
    parameter_mode: ParameterModeOption = "manual",
    safe_z_offset: SafeZOffsetOption = 5.0,
    clearance_z: ClearanceZOption = 1.0,
    cut_feed: CutFeedOption = 300.0,
    plunge_feed: PlungeFeedOption = 120.0,
    max_stepdown: MaxStepdownOption = 4.0,
    tool_diameter: ToolDiameterOption = 6.0,
    stepover_ratio: StepoverRatioOption = 0.6,
    lead_in_distance: LeadInOption = 0.0,
    lead_out_distance: LeadOutOption = 0.0,
    profile_stock_allowance: ProfileStockAllowanceOption = 0.0,
    enable_perimeter_profile_fallback: PerimeterProfileFallbackOption = False,
    profile_rough_only: ProfileRoughOnlyOption = False,
    tool_library: ToolLibraryOption = "standard_mm",
    tool_library_file: ToolLibraryFileOption = None,
    material_profile: MaterialProfileOption = "generic",
    material_profile_file: MaterialProfileFileOption = None,
    drill_tool_id: DrillToolOption = None,
    mill_tool_id: MillToolOption = None,
    automatic_surface_speed_m_per_min: AutoSurfaceSpeedOption = None,
    automatic_chip_load: AutoChipLoadOption = None,
    automatic_plunge_ratio: AutoPlungeRatioOption = None,
    automatic_stepdown_ratio: AutoStepdownRatioOption = None,
    automatic_lead_ratio: AutoLeadRatioOption = None,
    material_factor: MaterialFactorOption = 1.0,
) -> None:
    """Open the interactive viewer with automatic and manual features."""
    from antcam.main_viewer import run_feature_viewer

    planner_config = _build_planner_config(
        parameter_mode=parameter_mode,
        safe_z_offset=safe_z_offset,
        clearance_z=clearance_z,
        cut_feed=cut_feed,
        plunge_feed=plunge_feed,
        max_stepdown=max_stepdown,
        tool_diameter=tool_diameter,
        stepover_ratio=stepover_ratio,
        lead_in_distance=lead_in_distance,
        lead_out_distance=lead_out_distance,
        profile_stock_allowance=profile_stock_allowance,
        enable_perimeter_profile_fallback=enable_perimeter_profile_fallback,
        profile_rough_only=profile_rough_only,
        tool_library=tool_library,
        tool_library_file=tool_library_file,
        material_profile=material_profile,
        material_profile_file=material_profile_file,
        drill_tool_id=drill_tool_id,
        mill_tool_id=mill_tool_id,
        automatic_surface_speed_m_per_min=automatic_surface_speed_m_per_min,
        automatic_chip_load=automatic_chip_load,
        automatic_plunge_ratio=automatic_plunge_ratio,
        automatic_stepdown_ratio=automatic_stepdown_ratio,
        automatic_lead_ratio=automatic_lead_ratio,
        material_factor=material_factor,
    )
    run_feature_viewer(file_path, rx=rx, ry=ry, rz=rz, planner_config=planner_config)


@app.command("plan")
def plan_toolpath(
    file_path: str = typer.Argument(..., help="CAD file path (.step/.stp/.stl)"),
    rx: float = typer.Option(0.0, help="Rotation around X in degrees (STEP only)"),
    ry: float = typer.Option(0.0, help="Rotation around Y in degrees (STEP only)"),
    rz: float = typer.Option(0.0, help="Rotation around Z in degrees (STEP only)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Write the JSON plan to a file"),
    parameter_mode: ParameterModeOption = "manual",
    safe_z_offset: SafeZOffsetOption = 5.0,
    clearance_z: ClearanceZOption = 1.0,
    cut_feed: CutFeedOption = 300.0,
    plunge_feed: PlungeFeedOption = 120.0,
    max_stepdown: MaxStepdownOption = 4.0,
    tool_diameter: ToolDiameterOption = 6.0,
    stepover_ratio: StepoverRatioOption = 0.6,
    lead_in_distance: LeadInOption = 0.0,
    lead_out_distance: LeadOutOption = 0.0,
    profile_stock_allowance: ProfileStockAllowanceOption = 0.0,
    enable_perimeter_profile_fallback: PerimeterProfileFallbackOption = False,
    profile_rough_only: ProfileRoughOnlyOption = False,
    tool_library: ToolLibraryOption = "standard_mm",
    tool_library_file: ToolLibraryFileOption = None,
    material_profile: MaterialProfileOption = "generic",
    material_profile_file: MaterialProfileFileOption = None,
    drill_tool_id: DrillToolOption = None,
    mill_tool_id: MillToolOption = None,
    automatic_surface_speed_m_per_min: AutoSurfaceSpeedOption = None,
    automatic_chip_load: AutoChipLoadOption = None,
    automatic_plunge_ratio: AutoPlungeRatioOption = None,
    automatic_stepdown_ratio: AutoStepdownRatioOption = None,
    automatic_lead_ratio: AutoLeadRatioOption = None,
    material_factor: MaterialFactorOption = 1.0,
) -> None:
    """Generate a first automatic toolpath plan (drilling + slot milling + cavity clearing + 2.5D profile)."""
    planner_config = _build_planner_config(
        parameter_mode=parameter_mode,
        safe_z_offset=safe_z_offset,
        clearance_z=clearance_z,
        cut_feed=cut_feed,
        plunge_feed=plunge_feed,
        max_stepdown=max_stepdown,
        tool_diameter=tool_diameter,
        stepover_ratio=stepover_ratio,
        lead_in_distance=lead_in_distance,
        lead_out_distance=lead_out_distance,
        profile_stock_allowance=profile_stock_allowance,
        enable_perimeter_profile_fallback=enable_perimeter_profile_fallback,
        profile_rough_only=profile_rough_only,
        tool_library=tool_library,
        tool_library_file=tool_library_file,
        material_profile=material_profile,
        material_profile_file=material_profile_file,
        drill_tool_id=drill_tool_id,
        mill_tool_id=mill_tool_id,
        automatic_surface_speed_m_per_min=automatic_surface_speed_m_per_min,
        automatic_chip_load=automatic_chip_load,
        automatic_plunge_ratio=automatic_plunge_ratio,
        automatic_stepdown_ratio=automatic_stepdown_ratio,
        automatic_lead_ratio=automatic_lead_ratio,
        material_factor=material_factor,
    )

    model = _load_model(file_path, rx=rx, ry=ry, rz=rz)
    if not model.brep and not model.mesh:
        raise typer.BadParameter("The file does not contain valid geometry")

    extractor = FeatureExtractor(model)
    features = extractor.extract()
    normal = tuple(float(v) for v in extractor.working_plane_normal)

    perimeter = []
    contour_extractor = ContourExtractor(model, working_plane_normal=normal, features=features)
    contour_shadow = contour_extractor.extract()
    if contour_shadow is not None:
        perimeter = contour_extractor.extract_perimeter(contour_shadow)

    planner = planner_config.build_planner(normal)
    plan = planner.generate(features=features, perimeter_wires=perimeter)
    serialized = json.dumps(plan.to_dict(), indent=2)

    if output:
        out_path = Path(output)
        out_path.write_text(serialized + "\n", encoding="utf-8")
        console.print(f"Plan written to: [green]{out_path}[/green]")
    else:
        console.print(serialized)

    drill_ops = sum(1 for op in plan.operations if op.strategy == "drilling")
    slot_ops = sum(1 for op in plan.operations if op.strategy == "slot_milling")
    cavity_ops = sum(1 for op in plan.operations if op.strategy == "cavity_clearing")
    profile_ops = sum(1 for op in plan.operations if op.strategy == "2p5d_profile")
    console.print(
        f"Generated operations: [bold]{len(plan.operations)}[/bold] "
        f"(drilling={drill_ops}, slot={slot_ops}, cavity={cavity_ops}, profile={profile_ops}, warning={len(plan.warnings)})"
    )


@app.command()
def main() -> None:
    """Default command: print a short help message."""
    console.print("Use one of the available commands, for example:")
    console.print("  antcam viewer tests/data/flange.step")
    console.print("  antcam plan tests/data/flange.step -o plan.json")
    console.print("  antcam plan tests/data/flange.step --parameter-mode automatic --tool-library standard_mm --material-profile aluminum")


if __name__ == "__main__":
    app()
