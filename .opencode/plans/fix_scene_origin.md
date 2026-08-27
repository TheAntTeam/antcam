# Fix Plan: Coordinate System Alignment - Origin Axes and Stock Positioning

## Problem Analysis

The red triad (origin axes) and stock corner are hundreds of mm apart because:

1. **Stock rendering** uses `CORNER_XY_TOP_Z` with position (0,0,0) → stock bounds: (0,0,-10) to (200,200,0)
   - Stock top at Z=0, bottom at Z=-10
   - Work area is at Z=0 to Z=100

2. **Origin axes** are drawn at `(min_x, min_y, min_z)` = (0, 0, -10) - the **BOTTOM** of the stock
   - But they should be at the stock origin corner which for `CORNER_XY_TOP_Z` is the **TOP** corner at Z=0

3. **Work area** is at Z=0 to Z=100 (matches stock top)

### Root Cause
The origin axes code (line 398 in builder.py) uses:
```python
origin_x, origin_y, origin_z = min_x, min_y, min_z
```
This always puts the triad at the **minimum** corner (bottom-left-back), but the "origin" corner depends on `Stock.origin`:
- `*_TOP_Z` variants: origin is at **top** (max_z)
- `*_ZERO_Z` variants: origin is at **bottom** (min_z)

## Fix Plan

### 1. Add `_stock_origin_corner()` helper function to `builder.py`
Compute the correct origin corner based on `Stock.origin`:
- `CENTER_XY_TOP_Z`: position=center XY, top Z → origin at `(-w/2, -l/2, pz + h/2)`
- `CORNER_XY_TOP_Z`: position=corner XY, top Z → origin at `(px, py, pz)` 
- `CENTER_XY_ZERO_Z`: position=center XY, zero Z → origin at `(-w/2, -l/2, pz - h/2)`
- `CORNER_XY_ZERO_Z`: position=corner XY, zero Z → origin at `(px, py, pz)`

### 2. Update Origin Axes Drawing
Replace `origin_x, origin_y, origin_z = min_x, min_y, min_z` with call to `_stock_origin_corner(project)`

### 3. Update Work Area Box (Optional)
The work area is currently at `min_x=offset_x, min_y=offset_y, min_z=offset_z` - it should probably be relative to stock corner too.

### Files to Modify
- `src/antcam_rc2/core/rendering/builder.py` - Add helper, fix origin axes, fix work area

### Testing
- Run unit tests: `python -m pytest src/antcam_rc2/tests/unit/test_render_builder.py -v`
- Verify visually: run `python -m antcam_rc2 gui` and check red triad aligns with stock corner and work area