"""Command-line interface for AntCAM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from antcam.contour_extractor import ContourExtractor
from antcam.feature_extractor import FeatureExtractor
from antcam.importer import Model
from antcam.main_viewer import run_feature_viewer
from antcam.path_generator import AutoToolpathPlanner

app = typer.Typer(help="AntCAM CLI")
console = Console()


def _load_model(file_path: str, rx: float, ry: float, rz: float) -> Model:
    ext = Path(file_path).suffix.lower()
    if ext in {".step", ".stp"}:
        return Model.from_step(file_path, rx=rx, ry=ry, rz=rz)
    if ext == ".stl":
        return Model.from_stl(file_path, convert_to_brep=True)
    raise typer.BadParameter(f"Formato non supportato: {ext}. Usa STEP/STP oppure STL")


@app.command()
def viewer(
    file_path: str = typer.Argument(..., help="Percorso file CAD (.step/.stp/.stl)"),
    rx: float = typer.Option(0.0, help="Rotazione X in gradi (solo STEP)"),
    ry: float = typer.Option(0.0, help="Rotazione Y in gradi (solo STEP)"),
    rz: float = typer.Option(0.0, help="Rotazione Z in gradi (solo STEP)"),
) -> None:
    """Apri il viewer interattivo con feature automatiche/manuali."""
    run_feature_viewer(file_path, rx=rx, ry=ry, rz=rz)


@app.command("plan")
def plan_toolpath(
    file_path: str = typer.Argument(..., help="Percorso file CAD (.step/.stp/.stl)"),
    rx: float = typer.Option(0.0, help="Rotazione X in gradi (solo STEP)"),
    ry: float = typer.Option(0.0, help="Rotazione Y in gradi (solo STEP)"),
    rz: float = typer.Option(0.0, help="Rotazione Z in gradi (solo STEP)"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Scrive il piano JSON su file"),
) -> None:
    """Genera un piano toolpath automatico (drilling + profile 2.5D)."""
    model = _load_model(file_path, rx=rx, ry=ry, rz=rz)
    if not model.brep and not model.mesh:
        raise typer.BadParameter("Il file non contiene una geometria valida")

    extractor = FeatureExtractor(model)
    features = extractor.extract()
    normal = tuple(float(v) for v in extractor.working_plane_normal)

    perimeter = []
    contour_extractor = ContourExtractor(model, working_plane_normal=normal, features=features)
    contour_shadow = contour_extractor.extract()
    if contour_shadow is not None:
        perimeter = contour_extractor.extract_perimeter(contour_shadow)

    planner = AutoToolpathPlanner(working_plane_normal=normal)
    plan = planner.generate(features=features, perimeter_wires=perimeter)
    serialized = json.dumps(plan.to_dict(), indent=2)

    if output:
        out_path = Path(output)
        out_path.write_text(serialized + "\n", encoding="utf-8")
        console.print(f"Piano salvato in: [green]{out_path}[/green]")
    else:
        console.print(serialized)

    drill_ops = sum(1 for op in plan.operations if op.strategy == "drilling")
    profile_ops = sum(1 for op in plan.operations if op.strategy == "2p5d_profile")
    console.print(
        f"Operazioni generate: [bold]{len(plan.operations)}[/bold] "
        f"(drilling={drill_ops}, profile={profile_ops}, warning={len(plan.warnings)})"
    )


@app.command()
def main() -> None:
    """Comando di default: mostra help rapido."""
    console.print("Usa uno dei comandi disponibili, ad esempio:")
    console.print("  antcam viewer tests/data/flange.step")
    console.print("  antcam plan tests/data/flange.step -o plan.json")


if __name__ == "__main__":
    app()
