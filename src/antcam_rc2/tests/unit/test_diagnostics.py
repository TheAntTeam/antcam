"""Tests for core.io.diagnostics."""

from __future__ import annotations

from antcam_rc2.core.io.diagnostics import ImportDiagnostics


class TestImportDiagnostics:
    def test_empty(self) -> None:
        diag = ImportDiagnostics()
        assert not diag.has_errors
        assert diag.warnings == []
        assert diag.errors == []

    def test_add_warning_and_error(self) -> None:
        diag = ImportDiagnostics()
        diag.add_warning("SPLINE approximated")
        diag.add_error("missing block")
        assert diag.warnings == ["SPLINE approximated"]
        assert diag.errors == ["missing block"]
        assert diag.has_errors

    def test_entity_reference(self) -> None:
        diag = ImportDiagnostics()
        diag.add_warning("skipped", entity=12)
        diag.add_error("bad layer", entity="L0")
        assert diag.warnings == ["skipped (entity=12)"]
        assert diag.errors == ["bad layer (entity='L0')"]

    def test_summary(self) -> None:
        diag = ImportDiagnostics()
        diag.add_warning("w")
        assert diag.summary() == "1 warning(s), 0 error(s)"

    def test_bool(self) -> None:
        assert not ImportDiagnostics()
        diag = ImportDiagnostics()
        diag.add_error("e")
        assert bool(diag) is True
