from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich import print as rprint

from antcam.feature_extractor import FeatureExtractor
from antcam.importer import Model
from antcam.logger import setup_logger

app = typer.Typer(
    name="antcam",
    help="CNC CAM tools — open source CAM engine",
    no_args_is_help=True,
)


def _load_model(
    file_path: str,
    rx: float = 0,
    ry: float = 0,
    rz: float = 0,
) -> Model:
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in (".step", ".stp"):
        return Model.from_step(path, rx=rx, ry=ry, rz=rz)
    elif suffix == ".stl":
        return Model.from_stl(path, rx=rx, ry=ry, rz=rz)
    else:
        raise ValueError(f"Unsupported format: {suffix} (supported: .step, .stp, .stl)")


@app.command()
def info(
    file_path: str = typer.Argument(..., help="Path to STEP/STL file"),
    rx: float = typer.Option(0, "--rx", help="Rotation around X axis (degrees)"),
    ry: float = typer.Option(0, "--ry", help="Rotation around Y axis (degrees)"),
    rz: float = typer.Option(0, "--rz", help="Rotation around Z axis (degrees)"),
) -> None:
    model = _load_model(file_path, rx=rx, ry=ry, rz=rz)
    data = model.to_dict()
    rprint(f"[bold cyan]Model:[/] {data['source']}")
    rprint(f"  Faces     : {data['face_count']}")
    rprint(f"  Bounds    : {data['bounds']}")
    rprint(f"  Mesh only : {data['mesh_only']}")
    rprint(f"  Rotation  : {data['rotation']}")


@app.command()
def plan(
    file_path: str = typer.Argument(..., help="Path to STEP/STL file"),
    output: Optional[str] = typer.Option(None, "-o", "--output", help="Output file path"),
    rx: float = typer.Option(0, "--rx", help="Rotation around X axis (degrees)"),
    ry: float = typer.Option(0, "--ry", help="Rotation around Y axis (degrees)"),
    rz: float = typer.Option(0, "--rz", help="Rotation around Z axis (degrees)"),
    post: Optional[str] = typer.Option(None, "--post", help="Post-processor (linuxcnc, haas, grbl)"),
) -> None:
    model = _load_model(file_path, rx=rx, ry=ry, rz=rz)

    plan_data = {
        "model": model.to_dict(),
        "operations": [],
        "working_plane_normal": [0, 0, 1],
        "safe_projection": 10.0,
        "metadata": {
            "generator": "antcam",
            "version": "0.1.0",
        },
    }

    if output:
        Path(output).write_text(json.dumps(plan_data, indent=2))
        rprint(f"[green]Plan written to:[/] {output}")
    else:
        print(json.dumps(plan_data, indent=2))


@app.command()
def features(
    file_path: str = typer.Argument(..., help="Path to STEP/STL file"),
    rx: float = typer.Option(0, "--rx", help="Rotation around X axis (degrees)"),
    ry: float = typer.Option(0, "--ry", help="Rotation around Y axis (degrees)"),
    rz: float = typer.Option(0, "--rz", help="Rotation around Z axis (degrees)"),
) -> None:
    model = _load_model(file_path, rx=rx, ry=ry, rz=rz)
    if model.is_mesh_only:
        rprint("[yellow]Feature extraction from STL is experimental.[/]")

    extractor = FeatureExtractor()
    features = extractor.extract(model.shape)

    rprint(f"[bold cyan]Features:[/] {len(features)} found")
    for f in features:
        rprint(f"  [green]{f.type:20s}[/] {f.props}")


@app.command()
def viewer(
    file_path: str = typer.Argument(..., help="Path to STEP/STL file"),
    rx: float = typer.Option(0, "--rx", help="Rotation around X axis (degrees)"),
    ry: float = typer.Option(0, "--ry", help="Rotation around Y axis (degrees)"),
    rz: float = typer.Option(0, "--rz", help="Rotation around Z axis (degrees)"),
) -> None:
    try:
        from antcam.viewer import launch_viewer
    except ImportError as e:
        rprint("[red]Viewer dependencies not available.[/]")
        rprint("Install with: uv pip install -e '.[gui]'")
        raise typer.Exit(code=1) from e

    model = _load_model(file_path, rx=rx, ry=ry, rz=rz)
    launch_viewer(model)


if __name__ == "__main__":
    app()
