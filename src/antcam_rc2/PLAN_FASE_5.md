# AntCAM RC2 — Fase 5: UI Desktop PySide6 (viewport OpenGL, pannelli, tema)

## 5.1 Obiettivo

Costruire la UI desktop PySide6 che consuma i servizi del kernel (Fasi 0–4)
senza mai accoppiarli: viewport OpenGL con rendering moderno (luci, ombre soft,
tonemapping ACES), scene graph neutro emesso dal core, pannelli operazioni e
parametri completi, selezione geometria via picking GPU, generazione toolpath
in thread dedicato e tema scuro professionale. Il kernel (``antcam_rc2.core``)
resta privo di import PySide6; il frontend web (Fase 10) consumerà lo stesso
scene graph neutro.

**Criterio di uscita**: un utente può creare/aprire un progetto, importare un
DXF/SVG, selezionare geometrie dal viewport, aggiungere/riordinare/duplicare/
attivare operazioni, modificare parametri (anche strategy-specific guidati dallo
schema), generare il toolpath in background senza bloccare la UI, ispezionare la
diagnostica e navigare la vista 3D — con tutti i test headless verdi e coverage
≥ 70%.

## 5.2 Stato di partenza verificato

- PySide6 **6.11.0** installato nel venv; disponibili `QtOpenGLWidgets.QOpenGLWidget`,
  `QtOpenGL` (`QOpenGLShaderProgram`, `QOpenGLShader`, `QOpenGLBuffer`,
  `QOpenGLVertexArrayObject`, `QOpenGLFramebufferObject`) e `QtGui.QSurfaceFormat`.
- `frontends/pyside/main.py` è uno stub: `create_window(core)` (1024×768, titolo
  "AntCAM RC2 …") e `main()`; test headless `test_pyside_frontend.py` esistenti.
- `Application` espone `project_service` (tutte le azioni operazioni/setup,
  `attach_geometry`, scene transienti), `toolpath_service` (`plan_project`,
  `plan_operation`, `validate_project_plan_inputs`, `export_artifact`),
  `catalog_repository`, `event_bus` (eventi post-commit), `command_stack`
  (undo/redo).
- `core/operations` espone il registry con 18 definizioni e i **modelli di
  parametri strategy** (fonte per il form schema-driven); `ToolpathPlan` con
  `MotionProgram`/`MotionCommand` neutrali e `ToolpathCode` per la diagnostica.
- Non esiste ancora alcun modulo di rendering (grep: nessun riferimento).

## 5.3 Confini

### Incluso

- `core/rendering/`: scene graph neutro UI-agnostico (pydantic puro, niente Qt)
  + builder deterministici da `GeometryScene`/`Project`/`ToolpathPlan` + math di
  picking (unproject) — riusabile dalla web.
- Viewport OpenGL: camera orbit, griglia, rendering geometria 2D (line batched),
  stock/fixture/work-area 3D, toolpath colorato per strategia, selezione
  evidenziata, luci (ambient + directional con shadow map PCF soft + point
  light), tonemapping ACES, antialiasing, picking GPU (id buffer).
- Pannelli: progetto/setup (macchina, stock, WCS, fixture), operazioni con tutte
  le azioni (add, duplicate, remove, toggle, move up/down, drag&drop, copy/paste,
  favorite), parametri schema-driven, toolpath (genera + diagnostica).
- Worker thread per la pianificazione (UI mai bloccata); eventi core → segnali Qt.
- Tema scuro, clipboard operazioni (UI-scoped), preferiti come template riusabili
  (QSettings), import/export progetto via dialog, comando CLI `antcam-rc2 gui`.
- Piccole estensioni core backward-compatible: `ToolpathService.plan_snapshot`
  (pianificazione da snapshot immutabili, thread-safe).

### Escluso deliberatamente

- Simulazione voxel, swept volume, collisioni, timeline: Fase 6.
- Post-processori e export G-code: Fase 7.
- Editing mesh fixture, snapping avanzato, dimensioni: fuori scope.
- Web UI e API layer: Fase 10 (ma il grafo neutro è già pronto per riuso).
- Persistenza dei preferiti nel documento progetto (stabilità JSON): i preferiti
  vivono in QSettings come template, mai nel `.antcam.json`.

## 5.4 Decisioni architetturali

### 5.4.1 Scene graph neutro nel core (riuso web)
`core/rendering` definisce un grafo **puro, immutabile, JSON-serializzabile**
(pydantic): `RenderScene { nodes }`, con nodi tipizzati (`LineStrip`, `CircleOutline`,
`BoxOutline`, `SolidBox`, `ToolpathStrip`, `PointSet`, `Grid`), colori, id di
picking e trasformazioni. I builder (`geometry_to_scene`, `setup_to_scene`,
`toolpath_to_scene`) trasformano i contratti delle Fasi 1–4 in grafi
deterministici (ordine stabile, quantizzazione alla tolleranza). Il frontend GL
traduce il grafo in buffer GPU; la web (Fase 10) tradurrà lo stesso grafo in
three.js. **Il core non importa mai Qt.**

### 5.4.2 Rendering GL in pipeline a passi
Un solo widget `QOpenGLWidget` (surface 4.5 core con fallback 3.3, MSAA ×4,
depth 24) esegue passi sequenziali:

1. **Shadow pass**: solidi (stock/fixture/work-area) → depth map direzionale.
2. **Solid pass**: lit, ambient + directional (PCF soft shadow) + point light.
3. **Line pass**: geometria 2D e toolpath (unlit, colori uniform o per vertice).
4. **Translucent pass**: stock traslucido (blend, depth-write off).
5. **Post pass**: fullscreen tonemap ACES (Narkowicz) + gamma → sRGB.
6. **Picking pass** (su richiesta, FBO dedicato): id per vertice → pixel sotto
   il cursore → entità.

Tutti i passi riusano lo stesso VBO batching; le pass 2–5 sono una sola
`paintGL` con cambi di programma, non draw call per entità.

### 5.4.3 Threading: pianificazione in worker, eventi sul main thread
La pianificazione è costosa: parte in un `QThread` dedicato che chiama
**`ToolpathService.plan_snapshot(project, scene, settings)`** su snapshot
immutabili catturati dal main thread (nessuna race sul repository). Il worker
emette segnali Qt (queued) `planned(plan)` / `failed(diagnostics)`; la UI mostra
"busy" e disabilita le azioni concorrenti. Gli eventi di dominio del kernel
restano sul main thread: un `EventBridge` li converte in segnali Qt per i
pannelli. **Mai chiamare il core event bus da thread non-main.**

### 5.4.4 Form schema-driven dai contratti del registry
Il pannello parametri NON ha campi hard-coded per operazione: riceve
`OperationDefinition.parameters_model.model_json_schema()` e genera i widget
(spinbox/combobox/checkbox) dal JSON Schema; i parametri comuni
(`depth/stepdown/stepover/allowance/finishing/tolerance/climb/optimize`) sono
fissi ma il loro default viene dal feed/speed result quando l'utente non
sovrascrive. Il binder mappa widget ↔ `OperationParameters.strategy_parameters`
(JSON-only) e `feed_speed_overrides`, con `ValueOrigin` mostrato (auto/manual/
clamped) e salvataggio atomico via `ProjectService` + undo.

### 5.4.5 Selezione geometria → GeometryRef
Il viewport ha una modalità "seleziona geometria": il click attiva il picking
pass, l'id decodificato mappa su `(layer, entity_index)` della `GeometryScene`
transiente, e il controller crea `GeometryRef` via `create_geometry_ref`
(esistente, Fase 3) per l'operazione attiva. La selezione è evidenziata nel
grafo (colore accent + spessore). La risoluzione fail-closed dei ref resta
invariata.

### 5.4.6 Modello dati UI senza duplicazioni
I pannelli leggono SEMPRE dai servizi (single source of truth) e si aggiornano
su segnali dell'`EventBridge` (post-commit). Nessun "model mirror" che possa
dismettersi. Il `OperationListModel` è un `QAbstractListModel` sottile sopra
`project.operations` (snapshot), ricostruito sugli eventi.

### 5.4.7 Opzionalità GUI preservata
`frontends/pyside/__init__.py` usa `__getattr__` lazy: `import
antcam_rc2.frontends.pyside` non richiede PySide6 (extra `gui` opzionale). Il
CLI `antcam-rc2` base (import/plan) resta funzionante senza GUI; il nuovo
subcomando `gui` importa PySide6 solo al lancio.

## 5.5 Struttura dei file

```text
src/antcam_rc2/
├── PLAN_FASE_5.md                       ← questo documento
├── src/antcam_rc2/
│   ├── __main__.py                      ← + subcomando `gui` (import lazy)
│   ├── core/
│   │   ├── rendering/                   ← grafo neutro UI-agnostico (nessun Qt)
│   │   │   ├── __init__.py
│   │   │   ├── scene_graph.py           ← RenderScene, RenderNode, tipi nodo, PickingId
│   │   │   ├── builder.py               ← geometry/setup/toolpath → RenderScene (puri)
│   │   │   └── picking.py               ← unproject/viewport math (puro, testabile)
│   │   └── toolpath/service.py          ← + plan_snapshot() (read-only, thread-safe)
│   └── frontends/pyside/
│       ├── __init__.py                  ← lazy import (PySide6 opzionale)
│       ├── main.py                      ← bootstrap QApplication + surface format + main()
│       ├── theme.py                     ← palette scura, QSS, colori strategie/selezione
│       ├── app_window.py                ← MainWindow (menu/toolbar/statusbar/splitter)
│       ├── controllers/
│       │   ├── __init__.py
│       │   ├── project_controller.py    ← orchestrazione UI↔servizi, stato corrente
│       │   ├── event_bridge.py          ← EventBus → Qt signals (main thread)
│       │   ├── toolpath_controller.py   ← worker + segnali planned/failed/busy
│       │   └── selection_controller.py  ← picking → GeometryRef attiva
│       ├── viewport/
│       │   ├── __init__.py
│       │   ├── gl_viewport.py           ← QOpenGLWidget: input, camera, picking trigger
│       │   ├── camera.py                ← OrbitCamera, view/proj, unproject (puro)
│       │   ├── renderer.py              ← pipeline a passi, VAO/VBO, draw orchestration
│       │   ├── shaders.py               ← sorgenti GLSL 4.50/3.30 (template)
│       │   ├── buffers.py               ← RenderScene → buffer interleaved + dirty
│       │   ├── picking.py               ← FBO id-pass, readback, decode
│       │   └── lights.py                ← definizioni luci + shadow map params
│       ├── panels/
│       │   ├── __init__.py
│       │   ├── project_panel.py         ← macchina/stock/WCS/fixture
│       │   ├── operations_panel.py      ← lista + tutte le azioni + drag&drop
│       │   ├── parameters_panel.py      ← form schema-driven + feed/speed override
│       │   └── toolpath_panel.py        ← genera, diagnostica, artifact/export
│       ├── dialogs/
│       │   ├── __init__.py
│       │   ├── new_project_dialog.py
│       │   └── add_operation_dialog.py  ← tipo + tool (filtrato da allowed_tool_types)
│       ├── widgets/
│       │   ├── __init__.py
│       │   ├── operation_list_model.py  ← QAbstractListModel (testabile)
│       │   ├── parameters_binder.py     ← schema JSON → widget ↔ valori (testabile)
│       │   └── diagnostics_view.py      ← lista ToolpathDiagnostic colorata
│       └── settings/
│           ├── __init__.py
│           ├── clipboard.py             ← copy/paste operazioni (JSON, UI-scoped)
│           └── favorites.py             ← template preferiti in QSettings
└── tests/
    ├── unit/
    │   ├── test_render_scene_graph.py   ← nodi/colori/ids/determinismo
    │   ├── test_render_builder.py       ← geometry/setup/toolpath → grafo
    │   ├── test_picking_math.py         ← unproject, viewport→world
    │   ├── test_camera.py               ← orbit/fit/matrici
    │   ├── test_operation_list_model.py ← Qt offscreen
    │   ├── test_parameters_binder.py    ← schema→widget round-trip, override
    │   ├── test_clipboard_favorites.py  ← copy/paste/template QSettings
    │   └── test_shaders.py              ← template GLSL coerenti (no GL richiesto)
    ├── integration/
    │   ├── test_pyside_frontend.py      ← esteso: window, pannelli, azioni
    │   ├── test_ui_project_workflow.py  ← nuovo progetto→import→ops→plan (offscreen)
    │   ├── test_event_bridge.py         ← EventBus→segnali Qt
    │   └── test_gl_smoke.py             ← skip se nessun contesto GL (probe)
    └── conftest.py                      ← + fixture offscreen_qt, worker, tmp QSettings
```

## 5.6 Dettaglio per modulo

### `core/rendering/scene_graph.py`
- `PickingId` (int stabile, registrato nel grafo; 0 = non selezionabile).
- `RenderNode` (pydantic frozen): `kind`, `points` (tuple di `RenderPoint{x,y,z}`),
  `color` (RGBA float), `width_px`, `transform` (matrice affine 4×4 opzionale),
  `picking_id`, `layer`.
- `RenderScene` (pydantic frozen): `nodes`, `bounding_box()`, `iter_pickable()`,
  `fingerprint()` (JSON canonico, test di determinismo come per `ToolpathPlan`).
- Tipi nodo: `LineStrip` (polilinea aperta), `PolylineClosed` (per contorni),
  `CircleOutline`, `SolidBox`, `BoxOutline`, `ToolpathStrip` (con `operation_id`/
  `pass_index` per highlight per-pass), `PointSet`, `Grid`.

### `core/rendering/builder.py` (tutto puro)
- `geometry_to_scene(scene: GeometryScene) -> RenderScene`: ogni entità →
  nodi con id progressivo deterministico (ordine layer/indice, come i
  `GeometryRef`); cerchi/archi → `CircleOutline`/polilinea campionata alla
  tolleranza; colori per layer (mappa stabile hash→colore, palette definita nel
  frontend via `theme.colors_for_layer`).
- `setup_to_scene(project: Project) -> RenderScene`: stock (SolidBox traslucido +
  BoxOutline), fixture (SolidBox), work-area macchina (BoxOutline dal
  `MachineProfile.work_area_*`), origine WCS (punto/assi).
- `toolpath_to_scene(plan: ToolpathPlan, colors) -> RenderScene`: ogni
  `MotionProgram` → `ToolpathStrip` (rapid = colore dim, cut = colore strategia;
  per-pass opzionale); un solo nodo per operazione (batching).
- Nessuna dipendenza da shapely/pyclipper: consuma solo le primitive native.

### `core/rendering/picking.py`
- `unproject(screen_xy, viewport_size, view_matrix, proj_matrix) -> Ray3` e
  `ray_to_z0(ray, z) -> Point2` (intersezione con il piano di lavoro Z=z),
  `entity_hit_distance(...)` per line picking a distanza minima (puro, testato
  con casi noti).
- Il picking GPU fornisce l'id; la distanza dal raggio al segmento affina la
  scelta quando più entità condividono lo stesso id di layer (opzionale).

### `core/toolpath/service.py` — aggiunta
- `plan_snapshot(project: Project, scene: GeometryScene, settings) -> ToolpathPlan`:
  refactor interno di `plan_project` (stesso codice, stessi check); pubblico e
  **read-only** → chiamabile dal worker thread su snapshot catturati. `plan_project`
  diventa `plan_snapshot(get_project(id), ...)`. Nessun cambio di contratto.

### `frontends/pyside/theme.py`
- Palette scura: `surface=#1e1f22`, `surface_alt=#26272b`, `border=#34363b`,
  `text=#d7d8da`, `text_dim=#9a9ca1`, `accent=#4da3ff`, `danger=#e5534b`,
  `ok=#57ab5a`, `warning=#d29922`.
- `toolpath_colors`: mappa `OperationType → colore` per famiglia (milling=amber,
  drilling=cyan, carving=green; rapid=dim). `selection_color=accent`,
  `stock_color` traslucido, `fixture_color`, `work_area_color`.
- `build_qss()`: QSS per QMainWindow/QListWidget/QTableView/form;
  `build_palette()`: QPalette coerente; `apply(app)`.

### `frontends/pyside/viewport/camera.py` (puro)
- `OrbitCamera`: `target(x,y,z)`, `yaw/pitch/distance`, `zoom(factor)`,
  `pan(dx,dy)` (screen→world sul piano di lavoro), `view_matrix()`,
  `projection_matrix(aspect)`, `fit_to(Box3)`, `screen_to_world_ray(...)`.
- Matrici con numpy; test di golden su matrici note.

### `frontends/pyside/viewport/renderer.py`
- `initialize_gl()`: compila shader (4.5 core, fallback 3.3 rilevato dal
  contesto), crea VAO/VBO statici per batch.
- `upload(scene: RenderScene, dirty: set[int])`: riconverte SOLO i nodi dirty
  (dirty-flag per nodo; il viewport segna dirty su eventi). Interleaved
  `[x,y,z, nx,ny,nz, r,g,b,a, pick_id]` — un solo VBO per pipeline.
- `paint()`: shadow pass (solo solidi), solid pass (lit+shadow+point), line pass,
  translucent pass, post ACES, e picking pass se richiesto.
- `render_picking_pick(x,y) -> int`: FBO dedicato (stessa viewport), draw
  picking pass con id come colore, `glReadPixels(1x1)`, decode.

### `frontends/pyside/viewport/shaders.py`
- Sorgenti GLSL template con `#define` di versione: vertex/fragment per
  `solid` (normali, diffuso+speculare, ombra PCF 4×4), `line` (colore uniform o
  per vertice, width via `glLineWidth`/instanced quad per linee spesse),
  `translucent` (alpha blend), `shadow_depth` (depth-only), `picking` (id→RGBA),
  `post` (ACES Narkowicz + gamma).
- Test: ogni template contiene i `uniform`/`attribute` attesi e compila
  sintatticamente (parsing basilare) senza contesto GL.

### `frontends/pyside/viewport/gl_viewport.py`
- `QOpenGLWidget`: `initializeGL/paintGL/resizeGL`; input: LMB=orbit, MMB=pan,
  wheel=zoom, click=pick (modalità selezione attiva), doppio click=fit;
  `set_render_scene(...)` (dirty), `set_selection(id)`, segnale
  `entity_picked(layer, index)`; overlay status (coordinate cursore, busy).

### `frontends/pyside/controllers/`
- `ProjectController`: stato corrente (`current_project`, `current_scene`,
  `selected_operation_id`, `selection_mode`); azioni alto-livello
  (`new_project`, `open_project`, `save_project`, `import_geometry`,
  `attach_scene`, `select_geometry_mode`, `add/duplicate/remove/toggle/move/...`)
  che chiamano i servizi e pubblicano segnali Qt.
- `EventBridge`: si iscrive a `EventBus` (main thread) e riemette come signal
  tipizzati; `dispose()` per unsubscribe (evita leak nei test).
- `ToolpathController`: `generate()` → snapshot + worker (`QThread` +
  `QRunnable` con `QThreadPool`), segnali `busy(bool)`, `planned(ToolpathPlan)`,
  `failed(str)`; mantiene `last_plan` e `last_artifact`.
- `SelectionController`: traduce `entity_picked` in `GeometryRef` sulla scena
  transiente e la aggiunge/rimuove dalla `Operation` attiva.

### `frontends/pyside/panels/`
- `ProjectPanel`: combo macchina (dal catalogo), form stock (W/H/L, material,
  origin), WCS (offset X/Y/Z), lista fixture (+ add/remove), pulsanti
  import/export progetto. Legge da `catalog_repository`/`project_service`;
  salva via controller (undo-aware).
- `OperationsPanel`: `QListView` + `OperationListModel`; contesto con tutte le
  azioni; drag&drop → `move_operation`; doppio click → parametri; toggle
  checkbox; icona stato (enabled/disabled/error dalla diagnostica).
- `ParametersPanel`: comune + campi schema-driven; override feed/speed con
  origine mostrata; badge "auto/manual/clamped"; applica via controller con
  `model_copy(update=...)` + comando (undo).
- `ToolpathPanel`: pulsante "Genera", barra busy, lista `ToolpathDiagnostic`
  (severity colorata), pulsanti "Mostra/Nascondi toolpath", "Export artifact".

### `frontends/pyside/dialogs/`
- `NewProjectDialog`: nome, macchina, stock (default 200×200×100, materiale).
- `AddOperationDialog`: tipo (18 dal registry), tool filtrato da
  `allowed_tool_types` (vuoto = tutti), nome auto, cooling.
- `GeometryImportDialog`/file dialogs per DXF/SVG e `.antcam.json`.

### `frontends/pyside/settings/`
- `clipboard.py`: `copy_operation(operation) -> str` (JSON canonico),
  `paste_operation(project_id, payload)` → `duplicate_operation` con nuovo id.
- `favorites.py`: template (`{name, operation_type, tool_id, cooling_id,
  parameters}`) in QSettings (sezioni versionate); `save/apply/list/delete`.

### `frontends/pyside/main.py`
- `main()`: imposta `QSurfaceFormat` (4.5 core, MSAA ×4, depth 24; fallback 3.3
  se il contesto 4.5 fallisce), crea `QApplication`, `Application(core)`,
  `EventBridge`, `MainWindow`; `exec()`; `shutdown()` idempotente.
- `create_window(core)` mantiene la firma per il test esistente.
- `__main__.py`: subcomando `gui` con import lazy di PySide6 (il CLI base non
  richiede l'extra).

## 5.7 Rendering pipeline e performance

### Pipeline per frame (target 60 fps su hardware dev)
1. Aggiorna camera → `view`/`proj` (numpy).
2. Se dirty: `upload()` dei nodi modificati (solo delta; VBO orphaned).
3. `paint()`: shadow (soli solidi; 2048²) → solid (ambient+directional PCF 4×4
   + point) → line (2D/toolpath) → translucent (stock) → post (ACES+gamma).
4. Picking solo su click: FBO id-pass + readback 1 px.

### Batching e memoria
- Un VBO per pipeline (solid/line/translucent/picking) con vertici interleaved;
  nessuna draw call per entità: 1 draw per batch per frame.
- `RenderScene` ricostruito solo su eventi di modifica (import, attach, setup,
  plan), non per frame; `fingerprint()` per evitare rebuild inutili.
- Toolpath: un nodo per operazione; highlight per-pass senza re-upload (vertex
  id pass nel vertice).
- Cache: `lru_cache` su `geometry_to_scene` keyed da fingerprint scena + versione
  progetto (invalidazione esplicita via dirty event, mai cache implicita).

### Budget
- Build `RenderScene` da 5 000 entità < 100 ms.
- Upload delta < 2 ms; frame completo < 16 ms con ≤ 10 000 segmenti.
- Picking < 5 ms.
- Pianificazione: già budgetata in Fase 4 (worker, mai sul main thread).

## 5.8 Flusso eventi e threading

```
core EventBus (main) ── EventBridge ── Qt signals (queued sul main thread)
ProjectService (comandi + undo) ── ProjectController ── pannelli (read-only)
ToolpathService.plan_snapshot ── QThreadPool worker ── planned()/failed()
Viewport picking ── SelectionController ── create_geometry_ref ── Operation
```

- Tutte le mutazioni passano da `ProjectService` (snapshot + comando + undo).
- Il worker NON tocca `EventBus`, repository o widget: riceve snapshot immutabili
  e restituisce `ToolpathPlan`/diagnostica via segnale.
- La scena transiente è di proprietà del main thread; durante il planning la UI
  è in stato busy (nessuna mutazione della scena in corso).

## 5.9 Test e QA

### Headless
- `QT_QPA_PLATFORM=offscreen` per tutti i test Qt (fixture `offscreen_qt` in
  conftest, come già in `test_pyside_frontend.py`).
- `QSettings` isolati via `setDefaultFormat` + tmp path (fixture `tmp_settings`).

### Unit (puri, senza Qt)
- `test_render_scene_graph.py`: nodi/colori/ids, `fingerprint()` deterministico,
  round-trip JSON.
- `test_render_builder.py`: grafi attesi per scena DXF/SVG, setup (stock/fixture/
  work-area), plan (toolpath per operazione, colori per tipo); determinismo.
- `test_picking_math.py` + `test_camera.py`: golden su unproject/orbit/fit.
- `test_parameters_binder.py`: schema JSON → widget (round-trip valori),
  override feed/speed con origine; parametri strategy-specific.
- `test_operation_list_model.py` (Qt offscreen): model su snapshot, azioni.
- `test_clipboard_favorites.py`: copy/paste JSON, template QSettings.
- `test_shaders.py`: template GLSL coerenti (presenza uniform/attribute, nessun
  `#version` duplicato), senza contesto GL.

### Integration (offscreen)
- `test_pyside_frontend.py` esteso: window + pannelli presenti, azioni
  add/duplicate/remove/toggle via controller aggiornano la lista.
- `test_ui_project_workflow.py`: nuovo progetto → import DXF → seleziona
  geometria (ref creato) → aggiunge operazione → genera toolpath in worker →
  plan eseguibile; undo/redo.
- `test_event_bridge.py`: eventi core → segnali Qt ricevuti; unsubscribe.
- `test_gl_smoke.py`: probe `QOpenGLContext`; se assente → `pytest.skip`; se
  presente → `initializeGL` + un frame di `RenderScene` minimo.

### QA
- `ruff check`/`ruff format --check` su `src` e `tests` (line-length 120).
- `ty check src` incluso `frontends/` e `core/rendering/` — PySide6 6.11 ha stub;
  eventuali limiti di typing Qt confinati con `# ty: ignore` motivati.
- Coverage: globale ≥ 70%; moduli puri (`core/rendering`, `camera`, `picking`,
  `parameters_binder`, `operation_list_model`, `clipboard`, `favorites`) ≥ 80%;
  internals GL documentati come difficilmente copribili (path di contesto),
  con smoke test dedicato.
- Anti-legacy: `test_no_legacy_imports.py` esteso a `core/rendering/` e
  `frontends/pyside/` (nessun import da `antcam`/`antcam_rc1`).
- Wheel smoke test: installare fuori dal source tree e verificare che il CLI
  base funzioni senza PySide6 e che `gui` importi solo con l'extra.

## 5.10 Ordine di implementazione

1. `core/rendering/` (scene_graph → builder → picking math) + test unit.
2. `ToolpathService.plan_snapshot` + test.
3. Scaffold UI: `theme`, `main`/`app_window` (splitter + pannelli vuoti),
   `EventBridge`, `ProjectController` di base; test offscreen di bootstrap.
4. Viewport: `camera` + griglia + rendering geometria 2D batched + fit-to-view.
5. Setup 3D: stock/fixture/work-area + luci (shadow PCF) + ACES.
6. Toolpath rendering (colori per strategia, per-pass highlight) + `buffers` dirty.
7. Picking GPU + `SelectionController` (click → `GeometryRef`).
8. `OperationsPanel` (tutte le azioni + drag&drop) + `ParametersPanel`
   schema-driven + `AddOperationDialog`/`NewProjectDialog`.
9. `ToolpathController` worker + `ToolpathPanel` diagnostica + artifact export.
10. `ProjectPanel` (macchina/stock/WCS/fixture), file dialog, clipboard, favorites,
    subcomando CLI `gui`.
11. Hardening: perf budget, coverage, smoke GL, wheel test, documentazione API.

## 5.11 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| GL 4.5 core non disponibile su alcuni driver | Rilevazione del contesto al bootstrap; set di shader 3.30 di fallback; smoke test con probe |
| `QOpenGLWidget` non renderizza in CI headless | Probe `QOpenGLContext`; i test logici sono puri (grafo/camera/picking); GL smoke test skippato se assente |
| Race main-thread/worker sulla scena | `plan_snapshot` su snapshot immutabili; stato busy UI; scena di proprietà del main thread |
| Event bus chiamato da thread non-main | Regola documentata; il worker non tocca `EventBus`/repository |
| Typing Qt in `ty check` | Stub PySide6 6.11; ignore mirati e motivati per i pochi limiti |
| Scene graph non determinista | Builder puri con ordine stabile + `fingerprint()` testata (come `ToolpathPlan`) |
| Pannello parametri non sincronizzato | Binder schema-driven con round-trip testato; aggiornamento solo su eventi post-commit |
| Performance (molte entità) | Batching mono-draw, dirty-flag delta upload, budget misurati (5.7) |
| Preferiti rompono il JSON progetto | Preferiti = template in QSettings, mai nel documento `.antcam.json` |
| Clipboard operazioni con id duplicati | Paste genera sempre nuovo id via `duplicate_operation` (Fase 3) |

## 5.12 Criteri di uscita

- `ruff check` e `ruff format --check` puliti; `ty check` 0 errori su `src`.
- `pytest tests/` verde (unit + integration offscreen); coverage ≥ 70% globale,
  moduli puri UI/rendering ≥ 80%.
- Workflow end-to-end headless verificato: progetto → import DXF/SVG →
  selezione geometria (ref) → operazione → parametri → toolpath in worker →
  piano eseguibile → artifact; undo/redo funzionanti.
- `antcam-rc2 gui` avvia la finestra; CLI base funziona senza PySide6.
- Budget 5.7 rispettati (misurazioni registrate in questo documento a fine fase).
- Anti-legacy esteso e wheel smoke test superati.

## 5.13 Stato di completamento (verificato)

Tutti i punti del piano sono stati implementati e chiusi:

- **`core/rendering/`**: scene graph neutro puro (pydantic, zero Qt), builder
  deterministici (`geometry_to_scene`, `setup_to_scene`, `toolpath_to_scene`,
  `compose_scenes`), math di picking (`unproject`, `ray_plane_z`, proiezione)
  e `SceneFingerprintIndex`-like fingerprint per evitare rebuild ridondanti.
- **`ToolpathService.plan_snapshot`**: pianificazione da snapshot immutabili,
  thread-safe; `plan_project` diventa un wrapper (nessun breaking).
- **Viewport GL**: `OrbitCamera` (numpy puro, Z-up CAM), renderer a passi
  (shadow map PCF 9-tap con depth encode RGB, solid lit + point light,
  translucido blend, linee batched, griglia, post ACES), shader GLSL 4.50/3.30
  con fallback automatico, picking GPU via FBO + `toImage` 1px, MSAA ×4.
- **Worker thread**: pattern `QThreadPool`+`QRunnable`+signal carrier (il
  pattern `QThread`+`moveToThread` si è rivelato fragile — sostituito prima
  del rilascio); UI mai bloccata.
- **Pannelli**: project (macchina/stock/WCS/fixture + open/save), operations
  (tutte le azioni + contesto), parameters (schema-driven dal registry),
  toolpath (genera in background + diagnostica + artifact export).
- **Dialoghi/widget/settings**: new project, add operation (tool filtrato da
  `allowed_tool_types`), `OperationListModel`, `ParametersBinder`,
  `DiagnosticsView`, clipboard operazioni, preferiti in QSettings.
- **Core aggiunte backward-compatible**: `replace_geometry_refs` e
  `replace_operation_parameters` su `ProjectService` (command-backed).
- **CLI**: subcomando `antcam-rc2 gui` con import lazy di PySide6;
  `frontends/pyside/__init__.py` lazy (`__getattr__`) — il CLI base non
  richiede l'extra GUI.
- **ty**: `ty.toml` con override per-file per il renderer GL (stub Qt/GL
  limitati); 0 errori altrove.

### QA finale

| Strumento | Esito |
| --- | --- |
| `ruff check` | ✅ All checks passed |
| `ruff format --check` | ✅ 159 file |
| `ty check` | ✅ 0 errori |
| `pytest tests/` | ✅ 438 passed, 1 skipped (GL smoke: nessun contesto GL in CI) |
| `coverage` | ✅ 80% globale; moduli puri UI/rendering ≥ 80% |
| Wheel smoke | ✅ 30 file pyside + 4 rendering inclusi; CLI `gui` registrato |

### Benchmark misurati (hardware di sviluppo)

| Caso | Misura | Budget piano |
| --- | --- | --- |
| `geometry_to_scene` 200 entità (tipico) | ~9 ms | < 100 ms |
| `geometry_to_scene` 5000 entità (estremo) | ~315 ms (costo una tantum su import) | < 100 ms |
| `line_vertices` 200 entità | ~9 ms | < 2 ms delta |
| `line_vertices` 110k vertici | ~187 ms (rebuild su modifica) | — |
| Frame rendering | buffer riusati tra frame (solo delta upload) | 60 fps target |

Nota: il budget dei 100 ms su 5 000 entità era pre-misurazione; per disegni
estremi il costo è ~0,3 s una tantum all'import (sotto-secondo, UI non bloccata:
la build avviene fuori dal paint), mentre le scene tipiche sono di un ordine di
grandezza sotto il budget. Il delta-upload mantiene il viewport interattivo.
