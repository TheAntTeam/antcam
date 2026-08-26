from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor


def _default_theme() -> dict[str, Any]:
    return {
        "background": {"gradient_top": [0.22, 0.25, 0.30], "gradient_bottom": [0.08, 0.10, 0.14], "gradient_direction": "vertical"},
        "grid": {"minor_step_mm": 1.0, "major_step_mm": 10.0, "margin_mm": 30.0, "minor_color": [0.18, 0.28, 0.35], "major_color": [0.55, 0.42, 0.28]},
        "model": {"body_color": [0.55, 0.56, 0.54], "edge_color": [0.92, 0.92, 0.88]},
        "features": {"highlight_color": [0.95, 0.65, 0.20], "highlight_transparency": 0.3},
        "rendering": {"msaa_samples": 4, "refresh_fps": 15},
        "ui": {
            "qss": (
                "QMainWindow { background-color: #1e1e1e; }"
                "QMenuBar { background-color: #2a2a2a; color: #c8c8c8; border-bottom: 1px solid #3a3a3a; }"
                "QMenuBar::item:selected { background-color: #3a3a3a; }"
                "QMenu { background-color: #2a2a2a; color: #c8c8c8; border: 1px solid #3a3a3a; }"
                "QMenu::item:selected { background-color: #404060; }"
                "QStatusBar { background-color: #1e1e1e; color: #888888; border-top: 1px solid #2a2a2a; font-size: 11px; padding: 2px 8px; }"
            ),
            "qt_style": "Fusion",
        },
    }


def load_theme(path: str | Path | None = None) -> dict[str, Any]:
    cfg = _default_theme()
    if path is not None:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                user = json.load(f)
            _deep_merge(cfg, user)
    return cfg


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def occ_color(rgb: list[float]) -> Quantity_Color:
    return Quantity_Color(rgb[0], rgb[1], rgb[2], Quantity_TypeOfColor.Quantity_TOC_RGB)


# Built-in search paths
SEARCH_PATHS = [
    Path("data/theme.json"),
    Path(__file__).resolve().parent.parent.parent / "data" / "theme.json",
]
