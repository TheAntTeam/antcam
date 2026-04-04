# AntCAM

![PyPI version](https://img.shields.io/pypi/v/antcam.svg)

CNC CAM tools.

* Created by **[The Ant Team](https://github.com/TheAntTeam)**
  * PyPI: https://pypi.org/user/TheAntTeam/
* PyPI package: https://pypi.org/project/antcam/
* Free software: MIT License

## Features

### Feature Extraction (2.5D CNC - 3-axis)

The feature extractor recognizes the following machining features from STEP files:

| Feature | Type | Description |
|---|---|---|
| `step` | `StepFeature` | Horizontal face with open edges (step/shoulder) |
| `pocket` | `PocketFeature` | Closed horizontal face with surrounding walls |
| `opening` | `OpeningFeature` | Through-pocket (no bottom face) |
| `hole_group` | `HoleGroup` | Group of holes with same diameter and depth |
| `fillet` | `FilletFeature` | Concave cylindrical fillet (corner radius) |
| `chamfer` | `ChamferFeature` | Inclined planar face between horizontal and vertical faces |
| `countersunk_hole` | `CountersunkHoleFeature` | Countersunk hole (cylinder + cone) |
| `slot` | `SlotFeature` | Elongated slot with constant width |

### Hole classification

- **Through holes** (`through=True`): no cap face at the bottom — visualized in magenta
- **Blind holes** (`through=False`): has a planar cap face closing the bottom — visualized in red
- Holes are grouped by diameter and depth into `HoleGroup` and marked as `tap_candidate`

### Contour extraction

- **Shadow projection**: orthogonal projection of all accessible faces onto the z-min plane
- **Perimeter**: outer and inner contours of the projected shadow (orange wires)
- Through holes are subtracted from the shadow shape

### Model loading

- STEP files via `Model.from_step(path, rx, ry, rz)` with optional Euler rotations
- Working axis configurable (default: Z+)

## Documentation

* TODO

## Development

To set up for local development:

```bash
# Clone your fork
git clone git@github.com:your_username/antcam.git
cd antcam

# Install in editable mode with live updates
uv tool install --editable .
```

This installs the CLI globally but with live updates - any changes you make to the source code are immediately available when you run `antcam`.

Run tests:

```bash
uv run pytest
```

Run quality checks (format, lint, type check, test):

```bash
just qa
```

## Author

AntCAM was created in 2026 by The Ant Team.

Built with [Cookiecutter](https://github.com/cookiecutter/cookiecutter) and the [audreyfeldroy/cookiecutter-pypackage](https://github.com/audreyfeldroy/cookiecutter-pypackage) project template.
