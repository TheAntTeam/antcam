"""Guard rail: AntCAM RC2 must never import from the legacy packages."""

from __future__ import annotations

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "antcam_rc2" / "src" / "antcam_rc2"
CORE_ROOT = PACKAGE_ROOT / "core"

LEGACY_NAMES = ("antcam", "antcam_rc1")

_IMPORT_RE = re.compile(r"^(?:from|import)\s+(?P<module>[a-zA-Z_][a-zA-Z0-9_]*)")


def _iter_py_files() -> list[Path]:
    return [p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts]


def _imported_module(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith(("import ", "from ")):
        return None
    match = _IMPORT_RE.match(stripped)
    return match.group("module") if match else None


def test_no_legacy_package_imports() -> None:
    offenders: list[str] = []
    for path in _iter_py_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip().startswith("#"):
                continue
            module = _imported_module(line)
            if module in LEGACY_NAMES:
                offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, "Legacy imports found:\n" + "\n".join(offenders)


def test_core_does_not_import_pyside() -> None:
    core_root = CORE_ROOT
    offenders = []
    for path in core_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith(("from ", "import ")):
                if "PySide6" in line or "PyQt" in line:
                    offenders.append(f"{path}:{lineno}: {stripped}")
    assert not offenders, f"Core must stay UI-agnostic, found: {offenders}"
