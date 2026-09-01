# AntCAM RC2 — Fix Geometrie 2D: dialog posizionamento, placement e panel

## 1. Obiettivo
Allineare il flusso 2D a quello 3D: import DXF/SVG via dialog modeless non distruttivo con live preview, posizionamento manuale `X/Y/Z_offset_from_top + Rotation Z + Mirror X/Y`, auto-centraggio solo XY. Nel frame `2D Geometries` aggiungere campo path read-only e lista di *shapes/linee* (entità singole, non layer). Fixare `Remove` per pulizia totale (scena + binding + placement + refs + toolpath). `Edit Position` e import usano lo stesso dialog/stesso comportamento.

**Criterio di uscita**: import → dialog con valori precompilati (centro XY) → modifica live → Accept persiste `GeometryPlacement` + `GeometryScene` → nome in campo + lista entità → `Remove` pulisce tutto senza stale refs → re-import sostituisce; `ty`/`ruff`/`pytest` verdi.

## 2. Stato partenza verificato
- `frontends/pyside/panels/geometries_panel.py:34-58` — `2D Geometries` con `QLineEdit _geom2d_name` (readOnly, placeholder `Nessuna geometria 2D`) `L38-41` e `QListWidget _geom2d_list` `L44-46` tip `"Layer della geometria 2D"`. `refresh():105-127` mostra `scene.source.path` nel campo e itera `scene.layers` → item `Layer 'name' (n entità)` con `data=("geometry", layer.name)` `L117-121`; fallback `(nessun layer)` `L122-125`.
- Pulsanti `L48-52` `Import 2D Geometry...` `Edit Position...` `Remove` (`danger`), rimossi `Recenter` nel fix precedente. Connessioni `L88-90` a `_on_import_2d/_on_edit_2d/_on_remove_2d`; `scene_changed → refresh` `L98`, `busy_changed → _on_import_busy` `L101`.
- `_on_import_2d():174-185` — `QFileDialog` → `controller.import_geometry(Path)` fire-and-forget, nessun dialog.
- `_on_edit_2d():210-221` — stub `status_message "... not yet implemented"`.
- `_on_remove_2d():223-255` — **bug**: costruisce nuova `GeometryScene(source=SourceInfo(format="dxf",path=""), units, layers=new_layers, diagnostics=ImportDiagnostics(), tolerance)` `L241-246`, muta direttamente `controller._scene` `L248` e `_geometry_graph` `L248`, bypassa `ProjectService.attach_geometry()` → binding stale, non invalida `SceneFingerprintIndex`, non pulisce `Operation.geometry_refs`, non undoable.
- `frontends/pyside/controllers/project_controller.py:175-191` — `import_geometry()` delega a `GeometryImportController` async; `_on_geometry_imported():181-191` fa ` _center_geometry_in_stock(scene)` mutazione in-place `L184` → `attach_geometry(id, scene)` `L185` → `_scene=scene` `_geometry_graph=geometry_to_scene(scene)` `L186-187` → `_rebuild_setup_graph()` `L188` → `scene_changed` `clear_toolpaths`. Nessun dialog, nessun placement.
- `_center_geometry_in_stock():716-742` — calcola `bbox.center` via `GeometryScene.bounding_box()` `L726`, target `stock_center_xy` WCS-aware (`center_xy_*` vs `corner_xy_*`) `L732-741`, poi `scene.translate(dx,dy)` in-place `L742` (solo XY, Z=0, no rot/mirror). Distruttivo.
- `frontends/pyside/dialogs/import_solid_dialog.py:29-239` — riferimento: modeless `QDialog` `setModal(False)` `L242`, `placement_changed=Signal(object)` `L41`, init con `scene, stock, wcs, placement` `L42-55`, default via `default_placement_for_scene()` `L58-60`, `QFormLayout` con `QComboBox _origin` `L76-85`, spin `_offset_x/y/z` `L90-95`, `_rot_x/y/z` `L98-106`, `_scale` `L108-111`, preset `Center/SnapTop/Bottom/Reset` `L115-123`, live `valueChanged → _update_preview()` `L130-133` che fa `apply_placement` + label bbox + `emit placement_changed`.
- `core/io/scene.py:47-147` — `GeometryScene` con `source, units, layers: list[SceneLayer], diagnostics, tolerance`, metodi `layer()`, `add_entity()`, `iter_entities()` ordine = `GeometryRef` order `L81`, `bounding_box()` `L91-134` (Arc approssimato a full bbox), `translate(dx,dy)` in-place via `Affine2D.translate` `L136-147`, `normalize()`; manca `apply(Affine2D)` non distruttivo.
- `core/geometry/transform.py:26-222` — `Affine2D(a,b,c,d,e,f)` con `identity/translate/scale/rotate/mirror_x/mirror_y/compose/chain/apply_point/apply_vec`, `apply()` overload per `Point2/Vec2/LineSegment/Circle/Arc/Contour/Path` `L187-222`; già supporta rot Z e mirror richiesti.
- `core/project/models.py:242-268` — `Project` con `geometry_binding: GeometryBinding|None` `L255`, `solid_binding/solid_placement` `L256-257`, nessuna `geometry_placement`; `GeometryBinding` `L137`, `SolidPlacement` `L153-171` template (translation, rotation tuple, scale, stock_origin + validate finite).
- `core/services/project_service.py:270-279` — `attach_geometry(project_id, scene)` fa `invalidate_scene_index_cache()` `L276`, `_scenes[id]=scene`, `_commit(_updated(geometry_binding=bind_geometry(scene)))`; senza placement, senza pulizia refs. `get_solid_scene` pattern diverso.
- `core/rendering/builder.py:211-244` — `geometry_to_scene(scene, color_for_layer, width_px)` puro, itera `layers` → `_entity_shape()` con `flat_points([(x,y,0)])` Z sempre 0 `L647-654`, `picking_id` per layer/entity, nessuna placement.

## 3. Analisi difetti
**A.** Import distruttivo: `_center_geometry_in_stock` muta scena sorgente; impossibile fare preview/undo senza clonare. Serve placement non distruttivo come `SolidPlacement`.
**B.** Mancanza dialog: import diretto impedisce regolazione manuale Z offset, rot Z, mirror; auto center XY non disattivabile.
**C.** Lista layer invece di entità: requisito chiede shapes/linee (ogni `Contour/LineSegment/Arc/Circle/Path`) con conteggio e tipo, non aggregazione per layer.
**D.** Edit stub e Remove con mutazione privata: bypassa service, stale binding, stale refs, non undoable, diagnostics persi.
**E.** Assenza `GeometryPlacement` persistito: `Project` non conserva offset/rot/mirror; toolpath e rendering non possono riprodurre placement dopo reload.
**F.** Builder senza placement: `geometry_to_scene` non applica affine né Z offset da top (`placed_z = stock_top_z - z_offset`).

## 4. Confini
**Incluso:** `GeometryPlacement` model + `geometry_placement.py` helper, `GeometryScene.apply_affine` (non distruttivo), estensione `Project.geometry_placement`, `ProjectService.attach_geometry(scene, placement)/replace_geometry_placement/detach_geometry`, `geometry_to_scene(scene, placement, stock, wcs)`, dialog `ImportGeometryDialog` modeless, controller `_geometry_dialog` state + `_open_geometry_placement_dialog` + `_rebuild_geometry_graph`, panel campo path + lista entità + fix Remove/Edit, rotazione solo Z, mirror X/Y, Z offset da top, auto-center XY.
**Escluso:** scala non uniforme (distorgerebbe cerchi), Z per-entità variabile (2D coplanare), multi-geometria, editing layer, snapping, simulazione.

## 5. Decisioni architetturali
1. **Placement non distruttivo** sul modello di `SolidPlacement`: nuovo `GeometryPlacement(frozen)` con `offset_x_mm, offset_y_mm, z_offset_from_top_mm, rotation_z_deg, mirror_x, mirror_y, auto_center_xy`. Persistito in `Project.geometry_placement`; scena raw resta immutabile.
2. **Z offset da top**: `stock_top_z = stock_bottom_z + height` via `setup_frame.stock_top_z_mm()`; `placed_z = stock_top_z - z_offset_from_top_mm` applicato come traslazione Z in rendering (flat Z) e come `top_z` per `ToolpathService` (come fa `solid_features[0].plane_z`).
3. **Auto center XY**: se `auto_center_xy==True`, spin X/Y disabilitati, `dx/dy = stock_center_xy - bbox_center` (WCS+StockOrigin aware, riusa logica `solid_placement.translation_for_center_on_stock`); altrimenti usa offset manuali. Checkbox default checked.
4. **Rotazione solo Z attorno a bbox center**: `Affine2D.chain(translate(center), rotate(rad), mirror_x?mirror_x:identity, mirror_y?..., translate(-center), translate(dx,dy))`. `apply_affine` su clone scena per preview; `geometry_to_scene` applica affine prima di `_entity_shape`.
5. **Dialog unico**: `ImportGeometryDialog` riusa layout `ImportSolidDialog`: sostituisce `Rot X/Y/Z` con singolo `Rotation Z`, rimuove `Scale`, aggiunge `Mirror X/Y` checkbox, mantiene `Offset X/Y/Z` (Z label `Z offset from top`), `Auto center XY`, preset `Center XY`/`Reset`. Segnale `placement_changed` per preview live, `result_placement()` per commit.
6. **Single geometry invariante** (come single solid): `attach_geometry` con fingerprint diverso pulisce `geometry_refs` atomico + invalida cache; `detach_geometry` pulisce refs.
7. **Lista = entità**: `GeometriesPanel.refresh()` itera `scene.iter_entities()` → item per entità `"{Type} #{i} [{layer}]"` con dettaglio lunghezza/raggio, `data=("geometry", layer, entity_index)` per picking futuro; path in campo dedicato.

## 6. Struttura file (delta)
```
src/antcam_rc2/PLAN_FIX_2D_GEOMETRY.md                    # questo piano
src/antcam_rc2/src/antcam_rc2/core/project/geometry_placement.py  # NEW
src/antcam_rc2/src/antcam_rc2/core/project/models.py             # +GeometryPlacement, Project.geometry_placement
src/antcam_rc2/src/antcam_rc2/core/io/scene.py                    # +apply_affine / clone
src/antcam_rc2/src/antcam_rc2/core/services/project_service.py    # attach/replace/detach_geometry + placement
src/antcam_rc2/src/antcam_rc2/core/rendering/builder.py           # geometry_to_scene(placement, stock, wcs)
src/antcam_rc2/src/antcam_rc2/frontends/pyside/dialogs/import_geometry_dialog.py  # NEW (copia import_solid_dialog)
src/antcam_rc2/src/antcam_rc2/frontends/pyside/controllers/project_controller.py  # _geometry_dialog, import/edit/rebuild, placed scene per toolpath
src/antcam_rc2/src/antcam_rc2/frontends/pyside/panels/geometries_panel.py         # campo path + lista entità + fix Remove/Edit
tests/unit/test_geometry_placement.py / integration/test_2d_dialog_workflow.py
```

## 7. Dettaglio per modulo
**`core/project/geometry_placement.py` (NEW, ispirato a `solid_placement.py:1-250`)** — `apply_placement(scene, placement, stock, wcs) -> GeometryScene` clona via `scene.copy` + `Affine2D`; `default_placement_for_geometry(scene, stock, wcs) -> GeometryPlacement(auto_center_xy=True, offset=center, z_offset=0, rot=0, mirror=False)`; `affine_for_placement(placement, bbox, stock, wcs) -> Affine2D` con logica auto vs manuale; `stock_top_z()` helper.

**`core/project/models.py:153`** — aggiungere `class GeometryPlacement(_ProjectModel): offset_x_mm: float=0; offset_y_mm: float=0; z_offset_from_top_mm: float=0; rotation_z_deg: float in [-360,360]; mirror_x: bool=False; mirror_y: bool=False; auto_center_xy: bool=True` + validate finite + `Project.geometry_placement: GeometryPlacement|None = None` `L255`.

**`core/io/scene.py:136`** — aggiungere `def apply_affine(self, affine: Affine2D) -> GeometryScene` che ritorna nuova scena con `layers=[SceneLayer(name, color, [apply(affine,e) for e in lyr.entities])]` senza mutare originale; `def transformed(self, placement, stock, wcs)` wrapper che chiama helper placement. Mantenere `translate` legacy per compat.

**`core/services/project_service.py:270`** — modificare `attach_geometry(project_id, scene, placement: GeometryPlacement|None=None)` con `placement if supplied else project.geometry_placement`, gestione `needs_clear = geometry_binding != bind_geometry(scene)` → `operations` con `geometry_refs=()` + `invalidate_scene_index_cache()`; aggiungere `replace_geometry_placement(project_id, placement)` e `detach_geometry(project_id)` (pop `_scenes`, clear binding+placement+refs, invalida cache) mirror `detach_solid:323`.

**`core/rendering/builder.py:211`** — `def geometry_to_scene(scene, placement=None, stock=None, wcs=None, ...)`: se placement, `scene = apply_placement(scene, placement, stock, wcs)` (clone) poi loop layers come prima ma `flat_points` con Z = `stock_top_z - z_offset` se stock fornito altrimenti 0; mantenere `picking_id` stabile.

**`frontends/pyside/dialogs/import_geometry_dialog.py` (NEW)** — copiare `import_solid_dialog.py:29-239`, sostituire `SolidScene` con `GeometryScene`, `SolidPlacement` con `GeometryPlacement`, campi: `Offset X/Y` (`QDoubleSpinBox` ±1000, disabled quando auto), `Z offset from top` (0=top, + sotto), `Rotation Z` (-180..360, 1°), `Mirror X/Y` `QCheckBox`, `Auto center XY` `QCheckBox` default True connesso a `valueChanged` → `_update_preview()`. Preset `Center XY` chiama `translation_for_center(...)`, `Reset` → default. `placement_changed` + `preview QLabel` bbox.

**`frontends/pyside/controllers/project_controller.py`** — aggiungere `_geometry_dialog`, `_geometry_dialog_scene/prev_*` come per `_solid_dialog:94-98`; `_open_geometry_placement_dialog(scene, placement, is_import)` modeless con preview `geometry_to_scene(scene, placement, stock, wcs)` + `viewport`? (geometria via `render_scene`); `_on_geometry_imported` → apre dialog invece di attach diretto; `edit_geometry_placement()` riapre con `project.geometry_placement`; `_rebuild_geometry_graph()` applica placement; `generate_toolpath()` usa `placed_scene = apply_placement(_scene, project.geometry_placement, stock, wcs)`; `remove_geometry_completely()` come `remove_solid_completely:412`.

**`frontends/pyside/panels/geometries_panel.py`** — `refresh()` già con campo path; cambiare iterazione da `layers` a `scene.iter_entities()` enumerata → item `"{type} #{idx} [{layer}] len=..."` con `data=("geometry", layer, idx)`; `_on_import_2d` → `controller.import_geometry` (che ora apre dialog); `_on_edit_2d` → `controller.edit_geometry_placement()`; `_on_remove_2d` → `controller.remove_geometry_completely()`.

## 8. Test e QA
- Unit `test_geometry_placement`: default center, affine rot+mirror, clone non muta originale, z_offset.
- Unit `test_project_service_geometry_placement`: attach con placement persiste, replace, detach pulisce refs, undo.
- Integration offscreen: import DXF → dialog default centro → lista entità popolata (n == iter_entities), campo path, modifica Z/rot → preview bbox, Accept → `project.geometry_placement` valorizzato, `render_scene` con Z corretta, `Remove` → `geometry_binding is None`, `geometry_placement is None`, `all(geometry_refs==())`, `last_plan is None`, lista vuota.

## 9. Ordine implementazione
1. Scrivere `PLAN_FIX_2D_GEOMETRY.md` in parallelo agli altri piani (prima di qualsiasi edit).
2. `core/project/geometry_placement.py` + `core/io/scene.apply_affine`.
3. `core/project/models.GeometryPlacement` + `Project.geometry_placement`.
4. `ProjectService` attach/replace/detach con placement e pulizia refs.
5. `builder.geometry_to_scene` con placement/stock/wcs e Z offset.
6. `ImportGeometryDialog` (copia adattata da solid).
7. `ProjectController` dialog modeless, rebuild, toolpath placed scene, remove completely.
8. `GeometriesPanel` campo path + lista entità + wiring Edit/Remove.
9. QA `ruff`/`ty`/`pytest`.

## 10. Rischi
- Cerchi/Archi con `mirror`+`rotate` non uniforme → `Affine2D.apply` già rifiuta non-uniform scale (`L118 is_uniform_scale`), mirror singolo è uniform (det -1) → ok; testare.
- Z offset rendering vs toolpath: mantenere coerenza `stock_top_z - z_offset` unico.
- Undo con refs: commit atomico come per solid.

## 11. Criteri uscita
- `ruff/format`/`ty` 0 errori, `pytest` verde, workflow import→dialog→lista entità→Remove pulito, nessun `Recenter` (già rimosso), dialog import e Edit identici.
