# AntCAM RC2 — Fix Solidi 3D: gestione, selezione e pulizia

## 1. Obiettivo

Correggere la gestione dei solidi 3D su UI e core: singolo solido attivo, campo nome dedicato, lista feature, evidenziazione nel viewport, rimozione totale (scena + binding + `solid_refs` + toolpath), sostituzione automatica all'import. Rimuovere infine i pulsanti `Recenter on Stock` (2D e 3D).

**Criterio di uscita**: `pytest` verde, `ruff`/`ty` puliti; workflow manuale verificato: import STEP/STL → nome in QLineEdit + lista feature → selezione multipla evidenzia mesh → generazione toolpath → `Remove` pulisce viewport/core/toolpath/refs; re-import senza `Remove` sostituisce correttamente; nessun bottone `Recenter on Stock` residuo.

## 2. Stato di partenza verificato

- `frontends/pyside/panels/geometries_panel.py:54-114` — `QGroupBox("3D Solids (STEP/STL)")` contiene solo `QListWidget` + 4 pulsanti (`Import 3D Solid...`, `Recenter on Stock`, `Edit Position/Rotation...`, `Remove`). `refresh():108-114` inserisce un unico `QListWidgetItem` `3D Solid: {scene.source.path}` con `data=("solid","all")`; nessun `QLineEdit` per il nome, nessuna iterazione su `bodies`/`features`.
- `frontends/pyside/panels/geometries_panel.py:32-49` — analogo per 2D con `Recenter on Stock` che verrà rimosso su richiesta utente.
- `frontends/pyside/controllers/project_controller.py:50-680` — stato `ProjectController` con `_solid_scene: SolidScene|None` `L69`, `_solid_graph: RenderScene` `L74`, `_selected_operation_id` `L75`. `import_solid():192-203` delega a `geometry_import_controller.import_solid` (thread); `_on_solid_imported():205-210` apre `_open_solid_placement_dialog(scene, is_import=True)`. `_open_solid_placement_dialog():220-318` è modeless, preview live `solid_to_scene(scene, initial)` `L253` + `viewport.set_solid_scene(apply_placement(scene,initial))` `L255`, `placement_changed` `L258-268` e `finished` `L270-312` con `attach_solid`/`replace_solid_placement`, `clear_toolpaths()` solo su accept `L308`.
- `project_controller.py:335-346` `select_solid_feature()` sostituisce con singolo `SolidRef` via `replace_solid_refs(..., (ref,))`, nessun multi-select, nessun highlight.
- `project_controller.py:520-531` `generate_toolpath()` richiede `_scene is not None`; progetto solo-3D bloccato in UI (CLI `__main__.py:519` usa `GeometryScene` vuota).
- `project_controller.py:589-599` `_reload_project()` fa `if svc_scene is not None: self._solid_scene = svc_scene` — non azzera su `detach` (undo detach lascia grafo stale).
- `core/services/project_service.py:281-320` — `attach_solid():281-299` persiste `solid_binding`+`solid_placement`, `detach_solid():306-316` azzera binding/placement e `_solid_scenes` ma non tocca `Operation.solid_refs` residui sulle operazioni.
- `core/rendering/builder.py:758-789` `solid_to_scene(scene, placement)` → un `RenderMesh` per body, colore fisso `(0.72,0.73,0.76)`, `picking_id=body+1`, nessun parametro selezione.
- `core/project/models.py:153-257` — `SolidPlacement`/`SolidBinding`/`SolidRef`/`Project.solid_binding/solid_placement`/`Operation.solid_refs`.
- `core/project/solid_refs.py:32-66` — `create_solid_ref`/`resolve_solid_ref` fail-closed su fingerprint.
- `frontends/pyside/viewport/gl_viewport.py:38,79-89,178-188` — `set_solid_scene` + `set_solid_picking_enabled` + `ray_mesh_hit` → `solid_feature_picked(body,feat)`; `app_window.py:81,245` wiring.

## 3. Analisi difetti

### A. Remove incompleto
`geometries_panel.py:233-247` `_on_remove_3d` fa `detach_solid` + `clear_toolpaths` ma non rimuove `solid_refs` residui dalle operazioni → `toolpath/service.py:160-166` `resolve_solid_ref` fallisce con `stale solid reference` invece di stato pulito. Se lista non selezionata è no-op silenzioso. Non svuota campo nome (inesistente). Undo di `detach` non pulisce `_solid_scene` per `project_controller.py:596` guard `is not None`.

### B. Nome nel posto sbagliato
`geometries_panel.py:111` mette `source.path` come testo item lista; manca `QLineEdit` read-only per `source.path`/`body.name` nel frame `3D Solids`.

### C. Lista non è feature
`refresh():108-114` mai itera `scene.bodies`/`features`; utente si aspetta lista feature (tipo, quota Z, raggio/area) selezionabili multipli. Mancano `QAbstractItemView.ExtendedSelection`, `data=(body_index, feature_index)`, tooltip quota.

### D. Highlight assente
`solid_to_scene` senza `selected`; `ProjectController` senza `Set[selected]`; `GeometriesPanel` non sincronizza selezione lista ↔ controller; `gl_viewport` picking già emette ma controller sostituisce singolo ref senza evidenziare mesh.

### E. Sostituzione non atomica
Re-import apre dialog modeless sovrapponendo preview `self._solid_scene = scene` `L252` prima di `detach`; accept sovrascrive `solid_binding` ma lascia `solid_refs` del solido precedente sulle operazioni. Richiesto singolo solido: nuovo solido elimina precedente + refs + toolpath.

### F. Recenter obsoleto
`geometries_panel.py:41,63,77,82,143-174` `Recenter on Stock` (2D e 3D) diverge da `default_placement_for_scene` (center XYZ vs snap-top) e per 3D non fa `clear_toolpaths`. Su richiesta utente va rimosso da entrambi i frame.

### G. Minori
- `compose_scenes` duplica `picking_id` tra solid/geometry.
- `generate_toolpath` blocca solo-3D.
- `ty`/`ruff` da verificare.

## 4. Confini

### Incluso
- `QLineEdit` read-only nome solido nel `QGroupBox("3D Solids")` (sopra la lista).
- Lista feature: iterazione deterministica su `SolidScene.iter_features()` / `bodies[].features`, testo `"{KIND} #{i} z={plane_z:.2f} {r mm/area}"`, selezione estesa, sincronizzazione bidirezionale lista ↔ controller ↔ viewport.
- `solid_to_scene(..., selected)` con mesh accent / boundary highlight.
- `ProjectService.clear_solid_refs(project_id)` e integrazione in `detach_solid` + sostituzione su `attach_solid`.
- `ProjectController`: stato `_selected_solid_features`, metodi `set/toggle/clear`, `_rebuild_solid_graph(selected)`, fix `_reload_project` (azzera anche su `None`), `generate_toolpath` solo-3D, sostituzione singoletto con pulizia refs/toolpath.
- Rimozione `Recenter on Stock` da GeometriesPanel (2D e 3D) come ultimo step.

### Escluso
- Multi-solido, editing mesh, snapping parametrico, job queue, simulazione volumetrica 3D, adaptive.

## 5. Decisioni architetturali

1. **Single solid invariante**: `Project` ha già `solid_binding` singolo; `import_solid` quando `self._solid_scene is not None` esegue `detach + clear_solid_refs` atomico prima di `attach` (attraverso `ProjectService`), anche su re-import senza `Remove` esplicito.
2. **Nome come view**: `QLineEdit` mostra `scene.source.path` (fallback `body.name`); nessun campo modello aggiuntivo.
3. **Lista = feature**: `GeometriesPanel.refresh()` itera `scene.bodies` → `features`, ordine già deterministico in `core/geometry3d/features.py`; nessuna cache extra.
4. **Selezione multi + highlight**: `ProjectController._selected_solid_features: set[tuple[int,int]]` è fonte verità; `GeometriesPanel` riflette selezione; `solid_to_scene` riceve `selected` e produce mesh accent (colore `theme.ACCENT` o `selection_color`) o `LINE_STRIP` boundary.
5. **Remove totale**: `ProjectService.detach_solid` + `clear_solid_refs` + `ProjectController.clear_toolpaths()` + `clear_selected_solid_features()` + `scene_changed`.
6. **Core UI-agnostico**: nessun `PySide6` in `core/*`.

## 6. Struttura file (delta)

```
src/antcam_rc2/
├── PLAN_FIX_SOLID_3D.md                         # questo documento
├── src/antcam_rc2/core/services/project_service.py
├── src/antcam_rc2/core/rendering/builder.py
├── src/antcam_rc2/core/rendering/scene_graph.py # opzionale note picking_id
├── src/antcam_rc2/frontends/pyside/controllers/project_controller.py
├── src/antcam_rc2/frontends/pyside/panels/geometries_panel.py
├── src/antcam_rc2/frontends/pyside/viewport/gl_viewport.py
└── tests/
    ├── unit/test_solid_refs.py                  # esteso con clear
    └── integration/test_solid_remove_workflow.py # offscreen workflow
```

## 7. Dettaglio per modulo

### 7.1 `core/services/project_service.py:281-320`
- Aggiungere `clear_solid_refs(project_id: str) -> None` che rimuove `solid_refs` da tutte le `Operation` costruendo `after = _updated(project, operations=tuple(op.model_copy(update={"solid_refs": ()}) for op in project.operations))` e `_commit`.
- Modificare `detach_solid()` per chiamare `clear_solid_refs` prima o dopo `_commit` (due command separati o uno unico: scelto due commit sequenziali per history chiara; in alternativa incorporare pulizia refs nello stesso `after` di `detach` — preferito singolo commit che azzera `solid_binding`, `solid_placement` e `solid_refs` di tutte le ops).
- Modificare `attach_solid()` per quando `project.solid_binding is not None` e fingerprint diverso, includere pulizia `solid_refs` nello stesso `after`.

### 7.2 `core/rendering/builder.py:758-789`
- Firma: `def solid_to_scene(scene: SolidScene, placement: object | None = None, selected: set[tuple[int,int]] | None = None) -> RenderScene`.
- Quando `selected` non vuoto: per ogni `body, feat` in `selected`, raccogliere boundary o triangoli della feature e produrre overlay: opzione A mesh con `color=theme.SELECTION` (+ alpha), opzione B `RenderNode(kind=LINE_STRIP, points=feature.boundary..., color=ACCENT, width_px=3.0)`. Mantenere `picking_id` stabile; non duplicare id tra solid/geometry (offset 10000 per mesh solide se necessario).

### 7.3 `frontends/pyside/controllers/project_controller.py`
- Stato: `self._selected_solid_features: set[tuple[int,int]] = set()` `L69-74`.
- Metodi: `selected_solid_features -> set`, `set_selected_solid_features(features: set[tuple[int,int]])`, `toggle_solid_feature(body,feat)`, `clear_selected_solid_features()`. Ogni mutazione chiama `_rebuild_solid_graph()` e `scene_changed`.
- Modificare `select_solid_feature()` per supportare multi: `self._selected_solid_features.add((body,feat))` poi `refs = tuple(create_solid_ref(self._solid_scene, b,f) for b,f in sorted(self._selected_solid_features))` poi `replace_solid_refs`.
- Modificare `_rebuild_solid_graph(self, selected=None)` per passare `self._selected_solid_features` a `solid_to_scene`.
- Fix `_reload_project():595-597` → `self._solid_scene = svc_scene` anche quando `None` (rimuovere guard `is not None`).
- Fix `generate_toolpath():520-531` → se `self._scene is None` e `self._solid_scene is not None`, creare `GeometryScene` vuota (`SourceInfo(format="dxf", path="")` + 0 layer) come in `__main__.py:519`.
- Implementare `remove_solid_completely()` che fa `project_service.detach_solid` (che già pulisce refs), `self._selected_solid_features.clear()`, `clear_toolpaths()`, `_reload_project()`, `scene_changed`.
- Su `_open_solid_placement_dialog` con `is_import` e `self._solid_scene is not None`, prima di preview eseguire detach implicito o almeno assicurare che `prev_scene` sia originale e che accept pulisca refs.

### 7.4 `frontends/pyside/panels/geometries_panel.py`
- Sopra `self._geom3d_list` aggiungere `self._solid_name = QLineEdit(readOnly=True)` `L54-60`, placeholder `Nessun solido`, tooltip path completo.
- `refresh():94-114` → `self._solid_name.setText(scene.source.path if scene else "")`; svuota lista; se `scene` iterare `for bi, body in enumerate(scene.bodies): for feat in body.features: item=QListWidgetItem(f"{feat.kind.value} #{feat.feature_index} z={feat.plane_z_mm:.2f} " + (f"r={feat.radius:.2f}mm" if feat.kind.value=="hole" else f"area~{feat.boundary...}"))`; `item.setData(0x0100, (bi, feat.feature_index))`; `item.setToolTip(...)`.
- Impostare `self._geom3d_list.setSelectionMode(QAbstractItemView.ExtendedSelection)` e `itemSelectionChanged` → `controller.set_selected_solid_features({item.data(...) for item in selectedItems()})`.
- Sincronizzare selezione da controller a lista (evitare loop con `blockSignals`).
- `_on_remove_3d():233-247` → chiamare `controller.remove_solid_completely()` (o `detach + clear + clear_toolpaths`).
- Rimuovere `recenter_3d` e `recenter_2d` (vedi §9) per ultimo.

### 7.5 `frontends/pyside/viewport/gl_viewport.py`
- Estendere `set_solid_scene(scene, selected=None)` se highlight richiede stato viewport; per ora highlight via `solid_to_scene`, nessun cambio; `solid_feature_picked` già OK. Aggiungere opzionale `solid_feature_picked` toggle: `GLViewport._pick_solid_feature` continua a emettere, controller decide toggle.

## 8. Test e QA

- Unit: `test_solid_refs_clear` — attach → add op con solid_refs → detach → `all(op.solid_refs==())` e `solid_binding is None`.
- Unit: `solid_to_scene_selected` — `fingerprint` cambia con `selected`, overlay presente.
- Integration offscreen (`QT_QPA_PLATFORM=offscreen`): import STL → `panel._solid_name.text()` contiene path → lista `count()==feature_count` → `itemSelectionChanged` → `controller.selected_solid_features` aggiornato → `controller.render_scene().meshes` contiene accent; `generate_toolpath` poi `Remove` → `controller._solid_scene is None`, `project.solid_binding is None`, `all(solid_refs==())`, `last_plan is None`, lista vuota, nome vuoto.
- Re-import: carica `MALE_BUCKLE.stl` poi `bottle_opener.step` senza Remove → vecchio binding scartato, nessuna op con ref stale.
- QA: `ruff check`/`format --check` puliti, `ty check src` 0 errori, `pytest` verde, coverage globale ≥70% moduli toccati ≥80%, anti-legacy.

## 9. Ordine di implementazione

1. Scrivere `PLAN_FIX_SOLID_3D.md` in parallelo agli altri piani (questo file) — prima di qualsiasi edit.
2. `ProjectService.clear_solid_refs` + integrazione in `detach_solid`/`attach_solid` + test.
3. `ProjectController` stato multi-selezione + fix `_reload_project` + `generate_toolpath` solo-3D.
4. `builder.solid_to_scene(selected)` con highlight.
5. `GeometriesPanel` campo nome + lista feature + wiring bidirezionale selezione/highlight.
6. `Remove` totale (detach + clear refs + clear toolpaths + clear selezione + viewport) e singoletto import (sostituzione atomica).
7. Verifica highlight lista ↔ viewport (picking toggle).
8. QA completo (ruff/ty/pytest).
9. Rimuovere `Recenter on Stock` (2D `geometries_panel.py:41,46,77,143-149` e 3D `63,68,82,151-174`) — ultimo step.

## 10. Rischi e mitigazioni

| Rischio | Mitigazione |
|---|---|
| `picking_id` duplicati dopo `compose_scenes` | Offset `picking_id` mesh solide a 10000+ o `PickEntry.kind="solid"` dedicato |
| Mesh grandi highlight lento | Riusare stessi vertici con colore diverso, non duplicare array |
| Undo con refs stale | `clear_solid_refs` nello stesso commit di `detach` mantiene history coerente |
| Rimozione Recenter rompe UX 2D | Confermato da utente: rimuovere entrambi; stock centering resta via dialog import placement |

## 11. Criteri di uscita

- `ruff check`/`format` puliti, `ty check` 0 errori, `pytest` verde, coverage ≥70%.
- Workflow manuale: import → nome in campo dedicato (non in lista) + lista feature popolata → selezione multi evidenzia canvas → Remove elimina oggetto da interfaccia e core (binding, refs, toolpath) → re-import sostituisce precedente.
- Nessun bottone `Recenter on Stock` nel frame 2D/3D.
