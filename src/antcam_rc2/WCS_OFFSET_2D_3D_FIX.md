# AntCAM RC2 — Fix: geometrie 2D e solidi 3D su due spazi visivi diversi

## 1. Problema

Caricando nel viewport una geometria 2D (DXF/SVG) e un solido 3D (STEP/STL),
le due entità apparivano su **due spazi completamente diversi**:

- lo `z=0` delle geometrie 2D risultava **molto più in basso** dello `z=0` dei
  solidi 3D (le linee 2D non rientravano nemmeno nel cono visibile);
- le geometrie 2D risultavano **ruotate attorno a Z di qualche grado** rispetto
  ai solidi (rotazione non da 90°, dell'ordine di 10-20°);
- il fenomeno era **indipendente** da offset, placement, `StockOrigin` o WCS:
  si manifestava anche con offset zero e placement default.

La causa NON era nel posizionamento (i due spazi *mondo* condividevano già
stessa origine, unità e orientamento, come verificato con test headless su
`mini_square.dxf` + `planar_surf.stp` e `sottobicchiere2.dxf`): i due grafi
`RenderScene` composti davano bbox coincidenti e distanza centri `0.0`.

## 2. Diagnosi (analisi causa radice)

### 2.1 Spazi mondo coincidenti (escluso il placement)

Test headless `compare_planar.py` / `extract_gl.py` su
`sottobicchiere2.dxf` (317 entità, `LINE:305 ARC:12`, tutte a `Z=0.0`,
`EXTMIN (0,0,0) EXTMAX (100,100,0)`) e `planar_surf.stp` (quadrato 50×50 su
`Z=0`, `verts min/max z = 0.0`):

- `geometry_placement.default_placement` → `offset (-50,-50), z_offset 0`,
  `stock_origin=center_xy_top_z`; placed bbox `-50,-50 → 50,50`.
- `solid_placement.default_placement` → `translation (-25,-25,0)`; placed bbox
  `-25,-25,0 → 25,25,0`.
- `geometry_to_scene` → vertici `flat_points` con `Z = stock_top - z_offset = 0`.
- `solid_to_scene` → vertici mesh con `Z range 0.0..0.0`.
- Compose bbox `-100,-100,-10 → 200,200,100`; distanza centri XY `0.0`.

I due spazi mondo sono quindi **identici** a livello di grafo.

### 2.2 La matrice di view applicata due volte (bug nel renderer)

La divergenza visiva stava nella **pipeline GL delle linee**. I vertex shader
calcolano sempre:

```glsl
gl_Position = u_projection * u_view * vec4(a_position, 1.0);
```

In `frontends/pyside/viewport/renderer.py::paint()` il valore di `view_projection`
è già `projection @ view`. Le pipeline impostano però uniform diverse:

| Pipeline | `u_view` | `u_projection` | Risultato shader |
|---|---|---|---|
| `solid` / `mesh` / `marker` | `np.eye(4)` | `projection @ view` | `projection @ view @ pos` ✅ |
| `grid` / `axes` / `ground` | `np.eye(4)` | `projection @ view` | `projection @ view @ pos` ✅ |
| **`line`** (2D, toolpath, selezione) | **`view`** | `projection @ view` | **`projection @ view² @ pos`** ❌ |
| `picking` | **`view`** | `projection @ view` | **`projection @ view² @ pos`** ❌ |

La pipeline `line` passava la **matrice di view vera** come `u_view`, mentre
`u_projection` conteneva già `projection @ view`. Il shader applicava quindi la
view **due volte**: `clip = projection @ (view @ view) @ pos`.

Con camera orbit a distanza di fit (~500 mm) la verifica numerica dà:

```
view² * origin = (2.90, -406.87, -795.02)
```

Ovvero un punto all'origine (2D a `Z=0`) finiva proiettato a `Z ≈ -795` e
spostato di centinaia di mm in XY, **fuori dal cono visibile** e con la
rotazione composta di `view²` (rotazione orbitale, quindi "di qualche grado").
I solidi (pipeline mesh) usavano `u_view = identity` e restavano corretti a
`Z=0` → l'effetto percepito era "due spazi diversi".

Stesso bug nel `pick()` (`renderer.py`), che usava la stessa convenzione errata
per la pass di selezione.

## 3. Soluzione

### 3.1 Fix pipeline line/picking (causa del doppio spazio)

In `frontends/pyside/viewport/renderer.py`:

- `paint()`: la chiamata alla pipeline `line` ora passa `u_view = np.eye(4)`:

```python
# u_view must be identity: the vertex shader computes u_projection * u_view
# and u_projection already carries the full projection @ view (like solids/grid).
self._draw_lines(view_projection, np.eye(4), "line")
```

- `pick()`: idem per la pass di picking:

```python
self._draw_lines(projection @ view, np.eye(4), "picking")
```

Così `clip = (projection @ view) @ pos`, identico alle pipeline solid/grid/axes:
le linee 2D tornano nello **stesso spazio mondo** dei solidi 3D.

### 3.2 Inversione del segno di "Z offset from top" (semantica)

Requisito utente: offset positivo deve **alzare** la geometria sopra il top
dello stock (non abbassarla). Modifiche:

- `core/rendering/builder.py::geometry_to_scene`: il piano Z delle entità 2D ora
  è

```python
z_for_points = stock_top_z(stock, wcs) + placement.z_offset_from_top_mm
```

  (prima `stock_top_z - offset`), con docstring aggiornata.

- `core/project/models.py::GeometryPlacement`: docstring aggiornata
  (`positive == raises the geometry above the top`).

- `frontends/pyside/dialogs/import_geometry_dialog.py::_preset_snap_bottom`:
  per portare la geometria al fondo (bottom) ora imposta
  `z_offset = -stock.height_mm` (così `z = top - height = bottom`); il preset
  `Snap to Top` resta `z_offset = 0`.

## 4. Verifica

- `ruff check` pulito su `renderer.py`, `builder.py`, `import_geometry_dialog.py`.
- Test headless su `sottobicchiere2.dxf` + `planar_surf.stp`: bbox piazzati
  centrati, `Z = 0` per entrambi, distanza centri `0.0`.
- Numerico `view²` vs `view` confermato prima del fix (`-795`) e corretto dopo
  (`u_view = identity`).

## 5. File modificati

| File | Modifica |
|---|---|
| `frontends/pyside/viewport/renderer.py` | Fix `u_view = identity` per pipeline `line` e `picking` (doppia view eliminata) |
| `core/rendering/builder.py` | Segno `Z offset from top` invertito (+ alza) + docstring |
| `core/project/models.py` | Docstring `GeometryPlacement.z_offset_from_top_mm` aggiornata |
| `frontends/pyside/dialogs/import_geometry_dialog.py` | Preset `Snap to Bottom` = `-height` per coerenza |
