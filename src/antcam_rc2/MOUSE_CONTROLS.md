# AntCAM RC2 — Mouse Control Rules

## Viewport Mouse Controls

### Current Mapping (Implemented in `gl_viewport.py`)

| Mouse Button | Action | Description |
|--------------|--------|-------------|
| **Middle** + drag | **Pan** | Scene follows mouse cursor direction |
| **Right** + drag | **Orbit** | Rotate view around target |
| **Left** + drag | *None* (unless picking) | Used for picking when enabled |

### Pan Direction Convention

**Pan follows mouse cursor**: When dragging with middle button, the scene moves in the same direction as the mouse cursor.

**Implementation** (in `gl_viewport.py` lines 182-183):
```python
elif event.buttons() & Qt.MouseButton.MiddleButton:
    # Pan: scene follows mouse
    self._camera.pan_pixels(delta.x(), delta.y(), (self.width(), self.height()))
```

**Delta convention**: `delta = event.position() - self._last_mouse` (current minus previous)

**Pan delta sign**: **Positive** (`+delta.x()`, `+delta.y()`) — the scene moves in the same direction as the mouse cursor ("scene follows mouse").

### Orbit Controls

**Right button** triggers orbit:
```python
elif event.buttons() & Qt.MouseButton.RightButton:
    # Orbit with right button
    self._camera.orbit(-delta.x() * 0.3, delta.y() * 0.3)
```

- Horizontal mouse movement → yaw rotation (negative X for natural feel)
- Vertical mouse movement → pitch rotation (positive Y for natural feel)
- Sensitivity factor: 0.3

### Drag Initialization

All three buttons can initiate dragging:
```python
if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
    self._last_mouse = event.position()
    self._dragging = True
```

### Picking Mode Override

When picking modes are active, they take precedence over navigation:
- **Geometry picking** (`_picking_enabled`): Left-click picks geometry entities
- **Solid feature picking** (`_solid_picking_enabled`): Left-click picks 3D features

In these modes, dragging for pan/orbit is disabled.

---

## Delta Convention Summary

| Variable | Definition | Direction |
|----------|------------|-----------|
| `delta` | `current_pos - last_pos` | Mouse movement direction |
| Pan (middle) | `+delta.x(), +delta.y()` | Scene follows mouse |
| Orbit (right) | `-delta.x(), +delta.y()` | Natural orbit feel |

### Quick Reference

| Button | Drag Action | Delta Sign |
|--------|-------------|------------|
| **Middle** | Pan | `+delta.x, +delta.y` (scene follows) |
| **Right** | Orbit | `-delta.x, +delta.y` |
| **Left** | Pick (when enabled) | N/A |