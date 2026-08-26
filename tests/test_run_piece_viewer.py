from pathlib import Path
import sys

from types import SimpleNamespace

import run_piece_viewer


def test_main_forwards_resolved_input_to_piece_viewer(monkeypatch):
    captured = {}

    def _run_piece_viewer(file_path, **kwargs):
        captured["file_path"] = file_path
        captured["kwargs"] = kwargs

    monkeypatch.setitem(sys.modules, "antcam.main_viewer", SimpleNamespace(run_piece_viewer=_run_piece_viewer))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_piece_viewer.py",
            "flange.step",
            "--rx",
            "10",
            "--ry",
            "20",
            "--rz",
            "30",
        ],
    )

    exit_code = run_piece_viewer.main()

    assert exit_code == 0
    assert Path(captured["file_path"]) == (run_piece_viewer.run_main_viewer.SAMPLE_DATA_ROOT / "flange.step").resolve()
    assert captured["kwargs"] == {"rx": 10.0, "ry": 20.0, "rz": 30.0}