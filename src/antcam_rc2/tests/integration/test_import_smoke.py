"""Import smoke: every module in the installed package imports cleanly.

Guards against broken imports (circular imports, missing deps, syntax errors)
that unit tests may not reach; mirrors the wheel smoke from Phase 8 QA.
"""

from __future__ import annotations

import importlib
import pkgutil

import antcam_rc2

_EXCLUDE = {"antcam_rc2.frontends.pyside.main"}  # GUI entry: needs Qt runtime


def _all_modules() -> list[str]:
    return [
        module.name
        for module in pkgutil.walk_packages(antcam_rc2.__path__, prefix="antcam_rc2.")
        if module.name not in _EXCLUDE
    ]


def test_all_modules_import() -> None:
    failures: list[str] = []
    for name in _all_modules():
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - report every broken module
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    assert failures == [], f"broken imports: {failures}"


def test_package_has_expected_modules() -> None:
    modules = set(_all_modules())
    for expected in (
        "antcam_rc2.core.io.dxf",
        "antcam_rc2.core.io.svg",
        "antcam_rc2.core.simulation.simulator",
        "antcam_rc2.core.post.service",
        "antcam_rc2.core.toolpath.service",
        "antcam_rc2.core.project.geometry_refs",
    ):
        assert expected in modules, f"missing module {expected}"
