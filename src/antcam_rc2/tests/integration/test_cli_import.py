"""Integration tests for the CLI import subcommand."""

from __future__ import annotations

from pathlib import Path

from antcam_rc2.__main__ import main

DATA = Path(__file__).parent.parent / "data"


def test_import_cli_ok(capsys) -> None:
    code = main(["import", str(DATA / "mini_square.dxf")])
    assert code == 0
    out = capsys.readouterr().out
    assert "format:    dxf" in out
    assert "entities:  4" in out
    assert "bbox:" in out


def test_import_cli_dump(capsys) -> None:
    code = main(["import", str(DATA / "mini_square.dxf"), "--dump"])
    assert code == 0
    out = capsys.readouterr().out
    assert "LineSegment" in out


def test_import_cli_missing_file(capsys) -> None:
    code = main(["import", str(DATA / "nope.dxf")])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_import_cli_unsupported(capsys) -> None:
    tmp = DATA.parent / "bad.xyz"
    tmp.write_text("x", encoding="utf-8")
    try:
        code = main(["import", str(tmp)])
        assert code == 1
        assert "Unsupported" in capsys.readouterr().err
    finally:
        tmp.unlink(missing_ok=True)


def test_main_no_args(capsys) -> None:
    code = main([])
    assert code == 0
    assert "usage" in capsys.readouterr().out.lower()
