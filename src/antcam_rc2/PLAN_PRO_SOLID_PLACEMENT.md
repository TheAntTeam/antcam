# AntCAM RC2 — Piano Solid Placement: posizionamento non distruttivo 3D, auto-centering e WCS-aware

## Obiettivo

Rendere il posizionamento 3D (STEP/STL) **non distruttivo, WCS-aware e persistente**,
chiudere i bug di placement emersi in Fase 9 e portare il flusso 3D alla parità
professionale del 2D (center XY automatico + snap Z) senza introdurre wizard pesanti.

Il core resta privo di Qt; il placement è un **modello immutabile serializzato nel Project**,
applicato come affine lazy al momento di rendering/toolpath/picking. Nessuna mutazione
verbatim del `SolidScene` importato.

Scope UI: **solo PySide6** — la WEB (Fase 10) consumerà lo stesso modello/core in futuro
ma non è oggetto di questo piano.

**Criterio di uscita**: import STEP/STL → solido appare già centrato XY sullo stock
e con `min_z` allo stock-top (o zero) secondo `StockOrigin`; l'utente può ritoccare
offset/rotazione/scala nel dialog con preview; il placement sopravvive a save/reload/undo;
`SolidRef`/`plane_z` restano coerenti; toolpath Z corretto; suite verde.

## Stato di partenza verificato

- `TriMesh.translate/rotate` esistono (`core/geometry3d/mesh.py:110-180`) ma mai usati in import; manca `scale`/`Affine3D`.
- `SolidBody/SolidScene.translate/rotate` (`scene.py:184-273`) copiano `features` verbatim → **bug stale**: `plane_z_mm`/`boundary`/`center`/`plane_normal` non traslati, toolpath Z errato (`toolpath/service.py:187`).
- `core/io3d/{stl.py:16,step.py:22,registry.py:62}` importano verbatim in coordinate CAD; nessun centering/orient.
- `Stock` ha `StockOrigin` a 4 varianti (`models.py:81`), `WCS offset` in `Project.wcs`; `builder._stock_corner_bounds` già WCS-aware, ma `ProjectController._apply_solid_origin_offset:238-263` assume `stock.position == corner` → double offset su `CENTER_*`.
- `SolidRef`/`SolidBinding` definiti (`models.py:153`, `solid_refs.py:22`) ma `Project` non ha campo `solid_binding` né `solid_placement` — `ProjectService._solid_scene` è transient (`project_controller:204-263`), perso su reload/undo.
- 2D già auto-centra XY (`project_controller:542-568` `_center_geometry_in_stock` WCS-aware, in-place). 3D richiede `ImportSolidDialog` manuale (`dialogs/import_solid_dialog:56` default `-bbox.center`) senza preview/orient.
- `solid_to_scene` (`builder:745`) emette vertici verbatim; `setup_to_scene` gestisce stock/fixture/WCS; `compose_scenes` li concatena.
- `feature_to_entities` (`convert:15`) è XY-drop puro; `picking.ray_mesh_hit:17` vettorizzato CPU.

## Analisi — cosa è facilmente fattibile vs oneroso

| Proposta | Valutazione | Note |
|---|---|---|
| **Center XY automatico sullo stock footprint** | ✅ facile | riusa `_center_geometry_in_stock` logic; bbox → stock center XY WCS-aware |
| **Snap `min_z` a stock top / zero** | ✅ facile | `min_z = stock_top` se `*_TOP_Z` else `stock.position.z`; coerente con `setup_frame` |
| **Trasform non distruttivo (placement separato)** | ✅ must-have, medio | nuovo `SolidPlacement` persistito; affine applicata lazy |
| **Preview bbox/quote nel dialog** | ✅ facile | label + wireframe setup; già accessibile |
| **Preset StockOrigin + offset numerico** | ✅ facile | già in dialog; estendere con snap presets |
| **WCS auto-detect / apply** | ⚠️ medio | WCS è già nel modello; applicarlo correttamente al placement (unificare helper) |
| **Auto-orientation (largest face → +Z, perimetro → XY)** | 🔴 oneroso / low ROI | richiede clustering normali + PCA bbox; fragile su parti asimmetriche; rimandato a opt-in futuro |
| **Wizard multi-step import** | 🔴 overkill | dialog singolo con presets basta; wizard = attrito UX |
| **Scala unità STL** | ⚠️ medio-basso | STL unitless → default mm; aggiungere `scale` nel placement se serve |

**Validazione AntCAM**: cenni in XY + snap top/bottom sono lo standard Fusion/professional e vanno adottati; non-distruttivo è prerequisito architetturale; pieno auto-orient è escluso in prima iterazione (solo `facing` 25° filter esistente).

## Confini

### Incluso

- `core/geometry3d/transform.py` `Affine3D` (matrice 4×4, `translate/rotate/scale/compose/apply/invert`) mirroring `Affine2D`; `TriMesh.apply(affine)`, `SolidBody/SolidScene.apply/translate` con **features trasformate** (o ricalcolate).
- `core/project/models.py`: `SolidPlacement { translation: tuple[3], rotation_euler_deg: tuple[3], scale: float, stock_origin: StockOrigin }` + `Project.solid_placement: SolidPlacement|None` + `Project.solid_binding: SolidBinding|None` (backward compat, default None).
- `core/project/solid_placement.py` helper: `stock_origin_point(stock,wcs) -> Point3`, `default_placement_for_scene(scene, stock, wcs) -> SolidPlacement` (center XY + snap Z), `apply_placement(scene, placement) -> SolidScene` (mesh + features coerenti).
- Fix `Feature3D.translate/rotate` o `detect_features` re-run post-placement; `SolidScene.bounding_box()` post-placement per preview.
- `core/rendering/builder.py`: `solid_to_scene(scene, placement=None)` applica affine prima di flat; `setup_to_scene` invariato ma placement usa l'helper unico.
- `core/toolpath/service.py`: risolve `solid_features` su scena **già placement-applicata**; `plane_z_mm` coerente; rimuove uso di `solid_features[0]` isolato per multi-Z (loop per feature o `top_z` per ref).
- `frontends/pyside/dialogs/import_solid_dialog.py`: preview bbox, StockOrigin combo, offset spinbox prefill da `default_placement`, checkbox `auto-center XY` (default on), `snap Z to top/zero` presets, scala opzionale.
- `frontends/pyside/controllers/project_controller.py`: `import_solid` crea `SolidPlacement` default, `apply_solid_placement` via `ProjectService` command (undo-aware), `solid_scene_placed` property per rendering/picking/toolpath; fix stock origin helper unico; `geometries_panel._on_recenter_3d` allineato a snap Z corretto.
- Persistenza `core/project/persistence.py` + `ProjectDocument` esteso con placement/binding; `viewport gl_viewport fit_to` WCS-aware.

### Escluso deliberatamente

- Auto-orient completo (largest face → +Z) e allineamento perimetro via PCA — backlog opt-in.
- Wizard multi-step, snapping a fixture, scaling anisotropo, manipolatori gizmo 3D — backlog.
- Riparazione mesh, conversione unità STL euristica — fuori scope.
- 4/5 assi, tool orientation non verticale.

## Decisioni architetturali

1. **Placement come dato, non mutazione**: `SolidScene` resta immutabile all'import; `SolidPlacement` è l'unica sorgente di posizionamento. `apply_placement` è pura e deterministica (ordine: scale → rotate ZYX → translate). Fingerprint scena = su mesh importata; fingerprint placed = `hash(scene.fingerprint + placement)`.
2. **Single source `stock_origin_point`**: unico helper `core/project/solid_placement.py:stock_origin_point` usato da builder, controller, dialog e `setup_frame` — elimina divergenza `builder._stock_corner_bounds` vs controller.
3. **Features coerenti**: `Feature3D.apply(affine)` trasforma `boundary`, `plane_z`, `plane_normal`, `center`, `triangles` invariati; alternativa re-`detect_features` più costosa. `apply` è O(boundary) + test di invarianza vs re-detect su fixture golden.
4. **Persistenza + undo**: `ProjectService.replace_solid_placement` / `attach_solid` come `attach_geometry` (snapshot `before/after`, `CommandStack`, `EventBus`), `solid_binding` validato su load (fingerprint mismatch → `GeometryReferenceError` fail-closed come 2D).
5. **Builder non duplica transform**: `solid_to_scene` riceve `placement` opzionale e applica una volta; `ProjectController.render_scene()` compone `_setup_graph + solid_to_scene(_solid_scene, _project.solid_placement) + _geometry_graph`.
6. **Parità 2D/3D**: `_center_geometry_in_stock` resta per 2D; 3D riusa `default_placement_for_scene` (stessa formula XY + snap Z). Recenter panel unificato.

## Struttura file (aggiunte/modifiche)

```
src/antcam_rc2/
├── PLAN_PRO_SOLID_PLACEMENT.md               ← questo documento
├── src/antcam_rc2/
│   ├── core/
│   │   ├── geometry3d/
│   │   │   ├── transform.py                  ← NUOVO: Affine3D
│   │   │   ├── mesh.py                       ← + apply/scale
│   │   │   ├── scene.py                      ← + Feature3D/SolidBody/SolidScene.apply (features coerenti)
│   │   │   └── solid_placement.py            ← NUOVO: helper placement/stock_origin/default
│   │   ├── project/
│   │   │   ├── models.py                     ← + SolidPlacement, Project.solid_* (compat)
│   │   │   ├── solid_refs.py                 ← + fingerprint placed
│   │   │   └── persistence.py                ← + solid_binding/placement nel Document
│   │   ├── rendering/
│   │   │   └── builder.py                    ← solid_to_scene(placement), fix fit
│   │   └── toolpath/
│   │       └── service.py                    ← usa scena placed, fix plane_z multi
│   └── frontends/pyside/
│       ├── controllers/project_controller.py ← placement persistente, command, render/picking/toolpath
│       ├── dialogs/import_solid_dialog.py    ← preview + presets + scale
│       ├── panels/geometries_panel.py        ← recenter fix, Edit Placement dialog
│       └── viewport/gl_viewport.py           ← fit_to WCS-aware
└── tests/
    ├── unit/
    │   ├── test_affine3d.py
    │   ├── test_solid_placement.py
    │   └── test_solid_scene_apply.py
    └── integration/
        └── test_solid_placement_workflow.py
```

## Dettaglio per modulo

### `core/geometry3d/transform.py` (NUOVO)

`Affine3D` 4×4 numpy: `identity/translate/rotate_xyz/scale_uniform/compose/apply(points)/invert`; ordine `Rz*Ry*Rx` allineato a `TriMesh.rotate`. Test: round-trip, non commutatività, `apply` su bbox.

### `core/geometry3d/{mesh,scene}.py`

`TriMesh.apply(affine)`, `Feature3D.apply(affine)` (xform boundary/center/normal/plane_z), `SolidBody/SolidScene.apply`. `SolidScene.placed_bounding_box(placement)` helper. Validazione: fingerprint stable se placement identity.

### `core/project/solid_placement.py` (NUOVO)

`stock_origin_point(stock, wcs)`, `stock_top_z(stock,wcs)`, `default_placement_for_scene(scene, stock, wcs)` → `(-center.x+target_x, -center.y+target_y, stock_top - bbox.max_z or 0 - bbox.min_z)`, `apply_placement`. Puro, testabile. Nessun import Qt.

### `core/project/models.py` + `persistence.py`

`SolidPlacement(frozen, extra=forbid)` con `translation/rotation/scale/stock_origin/version`; `Project.solid_placement/binding` opzionali; `ProjectDocument` include entrambi; migrazione `schema_version 1.1` compat (None → no solid). Validazione: scale > 0, translation finiti.

### `core/rendering/builder.py` + `toolpath/service.py`

Builder applica placement prima di flatten; range check su placement.scale>0; toolpath risolve su scena placed; `feature_to_entities` riceve feature già placed (nessun offset extra). Picking usa scena placed (ray vs mesh placed).

### Frontend PySide6 (solo)

Dialog mostra `bbox {w×h×d}`, `center`, `stock target`, spinbox con step 0.1/1, presets `Center XY`, `Snap to Top/Zero`, `Reset`. Controller emette `solid_placement_changed` e invalida `_solid_graph` + toolpath. Panel aggiunge `Edit Placement…` (riusa dialog) e `Recenter` fixato (`Z = stock_top - bbox.height` per TOP_Z). `gl_viewport.fit_to` usa `stock_origin_point`.

## Test e QA

- Unit puri: `test_affine3d`, `test_solid_placement_helpers` (4 StockOrigin × WCS), `test_solid_scene_apply_features` (plane_z/boundary/center coerenti), `test_solid_placement_persistence` (round-trip Document), `test_solid_to_scene_placement` (vertici traslati).
- Integration offscreen: `test_solid_auto_center` (import STL/STEP → bbox.center == stock center XY, min_z == stock top), `test_solid_placement_undo` (apply → undo → placement ripristinato), `test_solid_toolpath_z` (plane_z placed → depth passes corretti).
- QA: `ruff check/format`, `ty check` 0 errori, coverage `geometry3d ≥80%`, `project ≥80%`, `test_no_legacy_imports` esteso.
- Wheel smoke: `core.geometry3d` import senza OCP ok; placement senza extra `3d` ok.

## Ordine di implementazione

1. `Affine3D` + `TriMesh/Feature3D.apply` + test (sblocca tutto).
2. `solid_placement.py` helper + `stock_origin_point` unico + test 4 origini.
3. `Project.solid_placement/binding` + `persistence.Document` + `ProjectService` command + test round-trip/undo.
4. Fix `solid_to_scene(placement)` + `toolpath/service` placed + test Z.
5. Dialog `ImportSolidDialog` con default placement + preview/presets + test offscreen.
6. Controller placement persistente + `geometries_panel` recenter + `gl_viewport fit_to` + test integration auto-center.
7. Hardening: fingerprint placed, validazione scale, doc/README, benchmark import→placed <2ms.

## Rischi e mitigazioni

| Rischio | Mitigazione |
|---|---|
| Double offset StockOrigin | helper unico + test matrice 4×WCS |
| Features stale | `Feature3D.apply` testato vs re-detect golden; CI lo blocca |
| Reload perde placement | `ProjectDocument` + `solid_binding` fail-closed; test reload |
| Scala STL errata | default scale=1, validazione >0, warning se bbox > work area |
| Regressione 2D | placement opzionale (None) → path 2D invariato; test esistenti verdi |
| Performance | `apply` O(vertices+boundary) una tantum; nessun re-detect per frame |

## Criteri di uscita

- `ruff`/`ty`/`pytest` verdi; coverage globale ≥70%, moduli placement ≥80%.
- Import 3D senza interazione utente già centrato XY + snap Z corretto per tutte le 4 `StockOrigin` con WCS offset.
- Dialog placement con preview/presets funzionante; undo/redo placement; save/reload preserva placement e `SolidRef` validi.
- Toolpath su feature 3D a quota corretta (verified `target_z == solid_features[0].plane_z_mm placed`).
- Budget: `apply_placement` su 100k tri <5ms; `solid_to_scene` placed <8ms.

## Note per fasi future

- Auto-orient opt-in: `auto_orient(scene) -> placement` via clustering normali + PCA (solo se esplicitamente richiesto).
- Gizmo viewport per drag/rotate placement (Fase 5 follow-up).
- Web (Fase 10): `SolidPlacement` già JSON-serializzabile → riuso identico.
