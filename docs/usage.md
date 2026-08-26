# Usage

To import AntCAM in a project:

```python
import antcam
```

## Launch The Viewer

Use the repository launcher when you want to inspect recognized features and toolpath overlays in the GUI.

In VS Code:

1. Open Run and Debug.
2. Select `AntCAM Viewer: Prompt Model`.
3. Enter a STEP/STL path relative to the repository root, for example `tests/data/flange.step`.

For bundled samples under `tests/data`, the launcher also accepts the short filename form, for example `flange.step`.

You can also use the ready-made `AntCAM Viewer: Sample Automatic Plan` launch configuration.

Inside the viewer, press `T` to cycle toolpath visibility between `all`, `drilling`, `roughing`, and `finishing`. Use `Shift+T` to cycle backward.

From a terminal in the repository root:

```powershell
.venv\Scripts\python.exe run_main_viewer.py tests/data/flange.step --parameter-mode automatic --tool-library standard_mm --material-profile aluminum --cwd-root
```

Equivalent short form for bundled samples:

```powershell
.venv\Scripts\python.exe run_main_viewer.py flange.step --parameter-mode automatic --tool-library standard_mm --material-profile aluminum --cwd-root
```
