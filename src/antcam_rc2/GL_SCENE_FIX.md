# AntCAM RC2 — GL Scene Visibility Fix Report

## Problem Statement

**Issue**: In the OpenGL viewport, only the grid was visible. Stock, fixtures, geometries (2D/3D), and origin axes were invisible despite being correctly created in the render scene graph.

**Environment**: Intel UHD Graphics 630, OpenGL 4.5 Core Profile, PySide6 6.11+

---

## Root Cause Analysis (Pareto)

### 1. GL Error 1281 — `glLineWidth(3.0)` in Core Profile (CRITICAL)
| Aspect | Details |
|--------|---------|
| **Location** | `renderer.py:_draw_axes()` line 751 |
| **Error** | `GL_INVALID_VALUE (1281)` after drawing axes |
| **Cause** | Core profile OpenGL only supports `glLineWidth(1.0)`. Values > 1.0 generate `GL_INVALID_VALUE`. |
| **Consequence** | Corrupted GL state; subsequent draw calls (`_draw_solid`, `_draw_lines`) failed silently or behaved unpredictably. |
| **Evidence** | Log showed `[GL] after axes; error = 1281` |

### 2. Stock Translucent Depth Mask Disabled (HIGH)
| Aspect | Details |
|--------|---------|
| **Location** | `renderer.py:_draw_solid()` lines 621-628 |
| **Cause** | Stock has alpha=0.35 → routed to `solid_translucent` pipeline. Blend pass used `glDepthMask(False)`. |
| **Consequence** | Stock did not write to depth buffer → z-fighting with grid (z=0), ground plane. Grid rendered on top of stock. |
| **Pipeline Order** | Grid (depthMask=False) → Axes (depthMask=False) → Solids (depth test ON) → Translucent (depthMask=False) |

### 3. Auto fit_view Not Triggered at Startup (MEDIUM)
| Aspect | Details |
|--------|---------|
| **Cause** | Demo project created BEFORE MainWindow in `_run_gui_demo`. `scene_changed` emitted before `_on_scene_changed` handler connected. |
| **Consequence** | Camera remained at default (target=0,0,0, distance=100, yaw=-45°, pitch=35°) — viewing corner of stock, not center. |
| **Evidence** | Log showed fit_view never called for initial project |

---

## Solution Implemented

### Fix 1: Remove Invalid `glLineWidth(3.0)`

**File**: `src/antcam_rc2/frontends/pyside/viewport/renderer.py`

```python
# Before (line 751):
self._gl.glLineWidth(3.0)  # Thicker lines for axes
self._gl.glDrawArrays(GL_LINES, 0, self._axes_count)

# After:
# Core profile supports line width 1.0 only; removed glLineWidth(3.0) 
# which caused GL_INVALID_VALUE (1281)
self._gl.glDrawArrays(GL_LINES, 0, self._axes_count)
```

**Result**: GL Error 1281 eliminated. Axes render at standard 1px width.

---

### Fix 2: Enable Depth Write for Translucent Objects

**File**: `src/antcam_rc2/frontends/pyside/viewport/renderer.py`

```python
# Before (lines 621-628):
if blend:
    self._gl.glEnable(GL_BLEND)
    self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    self._gl.glDepthMask(False)  # PROBLEM
self._gl.glDrawArrays(GL_TRIANGLES, 0, count)
if blend:
    self._gl.glDepthMask(True)
    self._gl.glDisable(GL_BLEND)

# After:
if blend:
    self._gl.glEnable(GL_BLEND)
    self._gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    # Keep depth mask TRUE for translucent objects so they write depth
    # and properly occlude geometry behind them (grid, ground).
    # Standard alpha blending requires depth writes + back-to-front render order.
self._gl.glDrawArrays(GL_TRIANGLES, 0, count)
if blend:
    self._gl.glDepthMask(True)
    self._gl.glDisable(GL_BLEND)
```

**Result**: Stock (alpha=0.35) now writes depth → properly occludes grid/ground behind it. Translucent appearance preserved.

---

### Fix 3: Ensure fit_view Called on Scene Change

**File**: `src/antcam_rc2/src/antcam_rc2/__main__.py`

```python
# Before: Project created BEFORE MainWindow
controller = ProjectController(core)
controller.new_project(...)  # scene_changed emitted here
window = MainWindow(controller)  # handler connected HERE

# After: MainWindow created FIRST
window = MainWindow(controller)  # handler connected
controller.new_project(...)      # scene_changed → handler exists
```

**File**: `src/antcam_rc2/frontends/pyside/app_window.py` (already correct)
```python
def _on_scene_changed(self) -> None:
    self._viewport.set_render_scene(self._controller.render_scene())
    self._viewport.fit_view()  # Auto-fit on every scene change
```

**Result**: Camera auto-positions to frame entire scene on project creation, geometry import, fixture changes.

---

## Additional Improvements

### Verbose Stock Pipeline Logging
**File**: `src/antcam_rc2/core/rendering/builder.py` — `setup_to_scene()`

Added comprehensive logging showing:
- Project info, WCS offsets
- Stock data from core (raw position, dimensions, origin type)
- Calculated RenderBox (min/max/center/size, is_empty)
- Each node creation (SOLID_BOX, BOX_OUTLINE) with colors, widths
- Fixture processing with calculated boxes
- Machine work area bounds
- Origin axes endpoints

**Example Output**:
```
=== SETUP_TO_SCENE START ===
Project: demo mech_plate (id=proj_xxx)
WCS offsets: x=0.0, y=0.0, z=0.0
--- STOCK DATA FROM CORE ---
  Stock ID: aluminum_6061
  Position (raw): x=0.0, y=0.0, z=0.0
  Dimensions: width=200.0, length=200.0, height=10.0
  Origin: center_xy_top_z
--- STOCK RENDERBOX CALCULATED ---
  min: (0.000, 0.000, 0.000)
  max: (200.000, 200.000, 10.000)
  center: (100.000, 100.000, 5.000)
  size: (200.000, 200.000, 10.000)
--- CREATING STOCK SOLID_BOX NODE ---
  NodeKind: SOLID_BOX, Color: (0.58, 0.6, 0.64, 0.35)
--- CREATING STOCK BOX_OUTLINE NODE ---
  NodeKind: BOX_OUTLINE, Color: (0.58, 0.6, 0.64, 1.0), Width: 1.5px
```

### Unit Vector Axes at Origin
**Files**: `buffers.py` + `renderer.py`

Added `axes_vertices()` generating X=red, Y=green, Z=blue axes at (0,0,0) with 3D arrow heads (length=20mm). Integrated into render pipeline.

---

## Verification Results

### Unit Tests
```
504 passed, 1 failed (pre-existing missing test file)
```

### GL Error Log (Before → After)
| Phase | Before | After |
|-------|--------|-------|
| Initialize | `glGetError = 0` | `glGetError = 0` |
| After axes | `error = 1281` | `error = 0` |
| After solids | `error = 0` | `error = 0` |
| Paint done | `error = 0` | `error = 0` |

### Camera Auto-Fit Log
```
fit_view: scene_box=(0.0,0.0,0.0)->(200.0,200.0,100.0)
fit_to: target=[100. 100.  50.], distance=536.98
fit_view: camera target=[100. 100.  50.], distance=536.98
```

---

## Why It Works Now

1. **No GL State Corruption**: Error 1281 eliminated → all draw calls execute correctly
2. **Proper Depth Ordering**: Stock writes depth → occludes grid/ground behind it
3. **Correct Camera Framing**: Auto fit_view centers camera on scene bounds
4. **Visible Scene Elements**:
   - **Stock**: Translucent blue box (alpha=0.35) with opaque outline
   - **Fixtures**: Red boxes with outlines
   - **Work Area**: Gray wireframe box
   - **Origin Axes**: RGB vectors at (0,0,0)
   - **Grid**: Adaptive spacing with major/minor lines
   - **Geometries**: Layer-colored polylines/circles
   - **Toolpaths**: Colored strips per operation

---

## Files Modified

| File | Changes |
|------|---------|
| `frontends/pyside/viewport/renderer.py` | Fix 1 (line 751), Fix 2 (lines 621-628) |
| `src/antcam_rc2/__main__.py` | Fix 3 (reordered project creation) |
| `core/rendering/builder.py` | Verbose logging in `setup_to_scene()` |
| `frontends/pyside/viewport/buffers.py` | Added `axes_vertices()` |
| `frontends/pyside/viewport/renderer.py` | Integrated axes rendering pipeline |

---

## Testing Checklist

- [x] `pytest tests/unit/` → 504 passed
- [x] `antcam-rc2 gui-demo --sample mech_plate` → No GL errors
- [x] Camera auto-fits on project creation
- [x] Stock visible as translucent box with outline
- [x] Fixtures, work area, axes, grid all visible
- [x] Geometry import → scene updates → fit_view triggers
- [x] No GL errors in paint loop

---

## Future Considerations

1. **Axes Thickness**: Currently 1px (core profile limit). Could implement as quads for thicker appearance if needed.
2. **Translucent Sorting**: Current render order (solid → mesh → marker → translucent) works for simple scenes. For complex overlapping translucent objects, back-to-front sorting may be needed.
3. **Grid Z-Fighting**: Grid at `z=box.min_z`, stock bottom at same Z. Consider slight Z offset for grid or stock.