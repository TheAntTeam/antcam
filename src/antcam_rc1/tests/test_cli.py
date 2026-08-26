from typer.testing import CliRunner

from antcam.cli import app

runner = CliRunner()


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Commands" in result.output
    assert "info" in result.output
    assert "plan" in result.output
    assert "viewer" in result.output


def test_info_no_file():
    result = runner.invoke(app, ["info", "nonexistent.step"])
    assert result.exit_code != 0
