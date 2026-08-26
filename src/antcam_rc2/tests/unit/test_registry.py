"""Tests for core.io.registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from antcam_rc2.core.errors import GeometryError, UnsupportedFormatError
from antcam_rc2.core.io.registry import FormatRegistry, import_file, registry

DATA = Path(__file__).parent.parent / "data"


class TestFormatRegistry:
    def test_registered_extensions(self) -> None:
        assert "dxf" in registry.extensions()
        assert "svg" in registry.extensions()

    def test_import_dxf(self) -> None:
        scene = import_file(DATA / "mini_square.dxf")
        assert scene.source.format == "dxf"
        assert len(scene.entities()) == 4

    def test_import_svg(self) -> None:
        scene = import_file(DATA / "mini_rect.svg")
        assert scene.source.format == "svg"
        assert len(scene.entities()) == 2

    def test_case_insensitive_extension(self) -> None:
        # Copy square with a .DXF suffix to exercise case handling
        tmp = DATA.parent / "case_upper.DXF"
        tmp.write_text((DATA / "mini_square.dxf").read_text(encoding="utf-8"), encoding="utf-8")
        try:
            scene = import_file(tmp)
            assert scene.source.format == "dxf"
        finally:
            tmp.unlink(missing_ok=True)

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            import_file(DATA / "nope.dxf")

    def test_unsupported_extension(self) -> None:
        tmp = DATA.parent / "bad.xyz"
        tmp.write_text("hello", encoding="utf-8")
        try:
            with pytest.raises(UnsupportedFormatError):
                import_file(tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def test_corrupt_dxf_raises_geometry_error(self) -> None:
        tmp = DATA.parent / "corrupt.dxf"
        tmp.write_text("not a real dxf", encoding="utf-8")
        try:
            with pytest.raises(GeometryError):
                import_file(tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def test_custom_registry(self) -> None:
        reg = FormatRegistry()

        def fake_importer(path):
            return None  # type: ignore[return-value]

        reg.register("txt", fake_importer)
        assert reg.extensions() == ["txt"]
        assert reg.import_file is not None
