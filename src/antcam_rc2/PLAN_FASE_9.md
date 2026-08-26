# AntCAM RC2 — Fase 9: Estensione 3D (import STEP/STL, feature 3D, operazioni 3-assi)

## 9.1 Obiettivo

Estendere il flusso CAM da **2D-first** a **3D 3-assi**: importare solidi
**STEP** (BRep) e **STL** (mesh) in una scena 3D neutra, selezionare
**facce piane / fori / perimetri** direttamente nel viewport, e **riusare le 18
operazioni esistenti** ("stesse operazioni, stretch") proiettando le feature 3D
su entità 2D equivalenti e riapplicando l'offset del piano di lavoro in Z.

La macchina di riferimento resta la **Makera Z1 (3 assi)**: sono ammesse solo le
feature **accessibili dall'asse Z** (facce piane orizzontali, fori con asse ~Z,
perimetri su piani orizzontali). Non si introduce alcuna logica 4/5-assi.

**Criterio di uscita**: un utente importa uno STEP/STL, vede il solido nel
viewport, seleziona facce/fori/perimetri, aggiunge le stesse operazioni del
flusso 2D e genera un `ToolpathPlan` eseguibile e deterministico; CLI
(`import3d` / `list3d` / `plan3d`) e UI funzionano headless e desktop; il
budget di import/rilevamento feature/picking rispetta i target 9.8.

## 9.2 Stato di partenza verificato

- **`cadquery-ocp` (OCP)** e **`trimesh 4.11.5`** installati nel venv (dipendenza
  del progetto `antcam` root); **numpy 2.4.6**, **scipy 1.17.1** disponibili.
- `MotionCommand` (Fase 4) è già **3D**: `Position3` XYZ assoluti e archi
  elicoidali (Z variabile) → i motion 3-assi **lineari** non richiedono modifiche
  al modello; solo gli archi su piani non-XY sono esclusi (vedi 9.4).
- Il renderer (Fase 5) possiede già la **pipeline `mesh`** (solid lit, normali,
  shadow caster) usata per il voxel mesh della simulazione → riusabile per i
  solidi 3D senza riscrivere gli shader.
- Il pattern **`GeometryRef` + fingerprint** (Fase 3) è collaudato → da replicare
  in un `SolidRef` per riferire le feature 3D in modo fail-closed.
- `OperationRegistry` (Fase 4) espone le 18 operazioni con `PlanningContext`
  (`top_z_mm`, `clearance_z_mm`, `depth_passes_z_mm`) → basta un offset piano
  per "stretchare" le strategie senza toccarle.
- `core/io` ha un `FormatRegistry` per estensione → si replica in `core/io3d`
  per `step`/`stl`.
- `pyproject.toml` di rc2 **non dichiara** OCP/trimesh → da aggiungere come
  extra opzionale `3d` (trimesh leggero può restare core).
- Nessun modulo `core/geometry3d` / `core/io3d` esiste; il `GeometryScene`
  (Fase 1) è esclusivamente 2D (`Curve2`/`Contour`/`Path`).

## 9.3 Analisi e revisione del progetto

### A. Rappresentazione neutra: **tutto mesh, OCP solo in ingresso**
**Opzioni**: (a) BRep parametrica con `TopoDS` ovunque; (b) dualismo
mesh/BRep; (c) **tessellare tutto in `TriMesh` e lavorare solo col mesh**.
**Decisione**: (c). OCP viene usato **solo** dall'importer STEP per
tessellare il solido in triangoli (tolleranza configurabile); da quel punto in
poi l'intera pipeline (feature, picking, rendering, refs) opera su
`vertices (N,3) float64` + `faces (M,3) int64`. Questo elimina la dipendenza da
OCP nel core, unifica STEP e STL in un solo code path e rende tutto testabile
senza GPU/BRep. **Revisione critica**: si perde la topologia esatta delle facce
BRep (nurbs), ma per selezione CAM di facce piane/fori/perimetri la
tessellazione con tolleranza ≤ 0.05 mm è più che sufficiente; la feature
recognition BRep esatta è **esclusa** (9.4).

### B. Rilevamento feature sul mesh (CPU, numpy/trimesh)
Le feature sono derivate dal mesh, non dal formato sorgente:
- **Faccia piana (FACE_PLANAR)**: normali per triangolo (cross product)
  quantizzate; clustering per direzione entro tolleranza angolare; componenti
  connesse per adiacenza di spigolo; fit del piano e filtro per planarità e
  area minima. Le facce con normale ≈ **+Z** sono "top faces" lavorabili dalle
  operazioni 2.5D.
- **Foro (HOLE)**: loop di bordo **interni** delle facce top (spigoli condivisi
  da un solo triangolo della faccia); fit cerchio; se errore di circolarità <
  tolleranza e asse ≈ Z → foro con `(centro, raggio, z_faccia, z_fondo)`. Il
  `z_fondo` si ottiene trovando la successiva faccia top sottostante (foro
  passante = fondo stock).
- **Perimetro (PERIMETER)**: loop di bordo **esterni** delle facce top → contorno
  per profiling.
L'alternativa "parete cilindrica" (rilevare i cilindri dalle normali
perpendicolari a Z) è documentata ma **non implementata in Fase 9** (i fori
sono già rilevati dai loop circolari delle facce top).

### C. Picking 3D: **CPU ray-mesh, non GPU id-buffer**
Il picking 2D esistente usa un FBO id. Per il 3D la granularità è "feature"
(faccia/foro/perimetro), non singolo triangolo: un buffer GPU per-feature
richiederebbe id per triangolo e complicherebbe la classificazione. **Decisione**:
ray-casting CPU con l'albero AABB di trimesh (`ray.intersects_location`) →
`(body_index, triangle_index)` → mappa triangolo→feature precomputata → feature.
Deterministico, testabile senza GL, ~ms su mesh tipiche (9.8). Il picking GPU
per intero body resta un'ottimizzazione futura documentata.

### D. "Stretch" delle operazioni: **feature → entità 2D + offset piano**
Le strategie (Fase 4) consumano `SceneEntity` 2D e producono motion con
`top_z_mm`/`depth_passes_z_mm`. Per riusarle senza toccarle:
1. un converter puro traduce la feature 3D in una o più entità 2D proiettate
   sul piano `Z=0`: `FACE_PLANAR`/`PERIMETER` → `Contour` (loop chiuso),
   `HOLE` → `Circle` (centro+raggio);
2. la feature porta un **`feature_plane_z_mm`** (quota del piano di lavoro);
3. `ToolpathService` applica un **offset Z** uniforme al programma generato
   (i motion restano gli stessi, traslati di `feature_plane_z_mm`).
Questo copre facce top, pocket su piani orizzontali e fori a diverse quote,
**senza modificare** le 18 strategie né il post (che riceve motion già assoluti).
Le feature su piani verticali/inclinati restano escluse (richiedono 4/5 assi).

### E. Rendering: nodo `MESH` riusando la pipeline esistente
Il `RenderScene` (Fase 5) non ha mesh persistenti. Si aggiunge un nodo `MESH`
(vertici + triangoli + colore + `picking_id`) consumato dalla pipeline `mesh`
già presente nel renderer (solid lit, normali per faccia, shadow caster). Le
feature selezionate si evidenziano con colore accent (come la selezione 2D).
Nessun nuovo shader: solo batch/upload e un nodo in più nel grafo.

### F. Determinismo e riferimenti persistenti
`SolidScene` è immutabile dopo l'import (stesso contratto di `GeometryScene`).
Ogni body/feature ha indice stabile e fingerprint SHA-256 (quantizzato alla
tolleranza). `SolidRef` replica il pattern `GeometryRef`:
`{body_index, feature_index, feature_type, feature_fingerprint}`. La risoluzione
è fail-closed (fingerprint esatto, mai retargeting).

### G. Revisione prestazioni (velocità di esecuzione)
- **Tessellazione STEP**: costo una-tantum all'import (~ms–s in base alla
  tolleranza); risultato memorizzato nel `SolidScene`; nessuna ritessellazione
  per frame o per plan.
- **Rilevamento feature**: una-tantum all'import (o on-demand al primo
  `list3d`/picking); indicizzato in `body → face → (triangles, loop)` per lookup
  O(1) al click.
- **Picking**: AABB tree di trimesh già costruito; query ~ms; mai rebuild per
  click se la scena è immutabile.
- **Rendering**: un solo VBO mesh per batch (tutti i triangoli concatenati),
  upload una-tantum, draw call singola per frame; nessuna ricostruzione per
  frame (stesso principio del batching 2D).
- **Plan**: la conversione feature→2D è O(vertici del loop) e il planning
  riusa i budget Fase 4 (fixture piccola < 250 ms).

## 9.4 Confini

### Incluso

- `core/geometry3d/`: `TriMesh`, `SolidScene`/`SolidBody`, `Feature3D`
  (FACE_PLANAR / HOLE / PERIMETER), rilevamento feature e picking ray-mesh.
- `core/io3d/`: registry `step`/`stl`; importer STL (trimesh) e STEP (OCP,
  import lazy, tessellazione).
- `SolidRef` + resolver + fingerprint (pattern `GeometryRef`).
- Converter feature→`SceneEntity` 2D e offset `feature_plane_z_mm` in
  `ToolpathService` (le 18 strategie restano invariate).
- Nodo `MESH` nel render graph + rendering/batching/picking nel viewport.
- Selezione UI: modalità "select feature 3D" (faccia/foro/perimetro) e
  integrazione `ProjectController`/pannelli.
- CLI: `import3d`, `list3d`, `plan3d` (e `gui` già esistente con import 3D).
- Extra `3d` in pyproject (OCP); trimesh core. Corpus STEP/STL reale in `tests/data/`.

### Escluso deliberatamente

- **4/5 assi**: feature su facce verticali/inclinate, orientamento utensile non
  verticale, indexing.
- **Feature recognition BRep esatta** (nurbs, topologia `TopoDS`); si lavora
  sulla tessellazione.
- **CAM 3D di finitura superficie** (waterline, parallel, z-level 3D, rest
  machining su freeform) → fase futura.
- **Simulazione 3D completa** (swept ball-end su superfici libere): il
  simulatore voxel Fase 6 continua a coprire solo rimozione 2.5D; per Fase 9 la
  simulazione 3D resta esclusa (il plan è comunque validabile).
- Editing/riparazione mesh (taglio, chiusura buchi, remesh) e import formati
  aggiuntivi (3MF, OBJ, BREP nativo) → fuori scope.
- Scontornatura 3D di archi su piani non-XY (il post 3-assi supporta archi
  planari XY/elicoidali; i contorni 3D liberi vengono poligonalizzati).

## 9.5 Decisioni architetturali finali

1. **Mesh-first**: `core/geometry3d` espone solo `TriMesh`/`SolidScene`
   (pydantic + numpy, zero Qt, zero OCP nei modelli persistenti). OCP è
   confinato in `core/io3d/step.py` con import lazy.
2. **`SolidScene` immutabile e fingerprint-abile** come `GeometryScene`;
   feature indicizzate `(body_index, feature_index)` con ordine deterministico.
3. **`SolidRef` separato** (non si tocca `GeometryRef`); le operazioni
   esistenti continuano a usare `GeometryRef` per il 2D, e un'operazione
   "3D" usa `SolidRef` risolti in entità 2D dal converter.
4. **Converter come confine**: `feature_to_entities(feature) -> (entities,
   plane_z)`; `ToolpathService` applica solo l'offset Z. Le strategie, il
   feed/speed, il post e la simulazione 2.5D non cambiano.
5. **Rendering**: nuovo `NodeKind.MESH` + payload `RenderMesh`; il renderer
   concatena i triangoli in un VBO unico per pipeline `mesh` (normali per
   faccia) e lo aggiunge agli shadow caster esistenti.
6. **Picking CPU** (trimesh AABB + mappa triangolo→feature); GPU id-buffer per
   interi body solo se i benchmark lo richiederanno.
7. **Dipendenze**: `trimesh` diventa dipendenza core (leggera, numpy);
   `cadquery-ocp` in extra `3d` opzionale (STEP); `import step` fallisce con
   messaggio chiaro se l'extra non è installato, `stl` funziona comunque.
8. **UI-agnostico**: CLI e core non importano PySide6; la web (Fase 10) potrà
   consumare lo stesso `SolidScene`/`RenderScene` con mesh.

## 9.6 Struttura dei file

```text
src/antcam_rc2/
├── PLAN_FASE_9.md                       ← questo documento
├── pyproject.toml                       ← + trimesh (core), extra [3d] = cadquery-ocp
├── src/antcam_rc2/
│   ├── core/
│   │   ├── geometry3d/                  ← neutro, puro, testabile senza Qt/GL
│   │   │   ├── __init__.py
│   │   │   ├── mesh.py                  ← TriMesh (vertices/faces/face_ids) + normals/area/bbox
│   │   │   ├── scene.py                 ← SolidScene, SolidBody, Feature3D, FeatureKind
│   │   │   ├── features.py              ← planar segmentation, hole/perimeter detection
│   │   │   └── picking.py               ← ray-mesh intersection → (body, feature)
│   │   ├── io3d/
│   │   │   ├── __init__.py              ← import_file_3d, registry_3d, re-export
│   │   │   ├── registry.py              ← FormatRegistry3D (step, stl)
│   │   │   ├── stl.py                   ← import_stl (trimesh, sempre disponibile)
│   │   │   └── step.py                  ← import_step (OCP lazy, tessellazione)
│   │   ├── project/
│   │   │   ├── models.py                ← + SolidBinding, SolidRef (backward-compatible)
│   │   │   └── solid_refs.py            ← create/resolve_solid_ref, fingerprint (come geometry_refs)
│   │   ├── rendering/
│   │   │   ├── scene_graph.py           ← + NodeKind.MESH, RenderMesh payload
│   │   │   └── builder.py               ← + solid_to_scene(SolidScene) → RenderScene con mesh
│   │   └── toolpath/
│   │       ├── service.py               ← + offset feature_plane_z_mm (strategie invariate)
│   │       └── settings.py              ← + feature plane per operazione/ref (se serve)
│   ├── frontends/pyside/
│   │   ├── viewport/
│   │   │   ├── renderer.py              ← upload/batch mesh nodes, picking mesh (ray CPU)
│   │   │   └── gl_viewport.py           ← modalità "select 3D feature" + segnale feature_picked
│   │   ├── controllers/
│   │   │   └── project_controller.py    ← import_solid, solid render graph, refs 3D
│   │   ├── panels/
│   │   │   └── solid_panel.py           ← lista bodies/features + azioni (opzionale in project_panel)
│   │   └── dialogs/
│   │       └── import_solid_dialog.py   ← scelta file STEP/STL + tolleranza
│   └── __main__.py                      ← + comandi import3d / list3d / plan3d
└── tests/
    ├── data/
    │   ├── bottle_opener.step           ← corpus 3D reale (1 foro, 2 perimetri)
    │   ├── flange.step                  ← corpus 3D reale (5 fori)
    │   ├── mounting_spider.step         ← corpus 3D reale (6 fori)
    │   ├── MALE_BUCKLE.stl              ← corpus 3D reale (mesh)
    │   └── servo_mount.stl              ← corpus 3D reale (mesh, molti perimetri)
    ├── unit/
    │   ├── test_geometry3d.py           ← TriMesh + feature detection + picking
    │   ├── test_io3d.py                 ← import STL/STEP, diagnostica
    │   └── test_solid_refs.py           ← fingerprint, resolve fail-closed, converter
    └── integration/
        ├── test_plan3d_workflow.py      ← import3d → ref → plan3d eseguibile (reuse ops)
        └── test_3d_corpus.py            ← corpus 5 parti reali + plan3d su STL
```

## 9.7 Dettaglio per modulo

### `core/geometry3d/mesh.py`
- `TriMesh(vertices: np.ndarray (N,3) float64, faces: np.ndarray (M,3) int64,
  face_ids: np.ndarray (M,) int32 | None)`.
- Helper puri vettorizzati: `face_normals()`, `face_areas()`, `bounding_box()`,
  `adjacency()` (mappa spigolo→facce per feature), `connected_components(mask)`.
- Nessun import trimesh nel modello (numpy only); trimesh usato solo dagli
  importer/picking/features.

### `core/geometry3d/scene.py`
- `SolidBody`: `id`, `name`, `mesh: TriMesh`, `features: tuple[Feature3D, ...]`,
  `source`.
- `Feature3D`: `kind: FeatureKind` (FACE_PLANAR/HOLE/PERIMETER), `plane_z_mm`,
  `plane_normal`, `boundary: Contour`-like polilinea 3D→proiettata, `triangles:
  tuple[int, ...]` (indici nel body), `center`/`radius` per HOLE, `facing`
  (Z-up flag), `fingerprint`.
- `SolidScene`: `bodies: tuple[SolidBody, ...]`, `bounding_box()`,
  `iter_features()`, `fingerprint()`; pydantic frozen come `RenderScene`.

### `core/geometry3d/features.py`
- `detect_planar_faces(mesh, angle_tol_deg, min_area_mm2, planar_tol_mm)`:
  normali → cluster per direzione → componenti connesse → fit piano → output
  FACE_PLANAR con `facing = (|normal·Z| > cos(tol))`.
- `detect_holes(face, ...)`: boundary edges della faccia → loop interni → fit
  cerchio (least-squares, numpy) → HOLE se circolarità < tol e asse ≈ Z.
- `detect_perimeters(face, ...)`: loop esterni → PERIMETER.
- `detect_features(mesh) -> tuple[Feature3D, ...]`: orchestratore
  deterministico (ordine: facce per area decrescente, fori per raggio
  decrescente, perimetri).

### `core/geometry3d/picking.py`
- `ray_mesh_hit(scene, ray_origin, ray_dir) -> (body_index, feature_index) | None`:
  intersezione AABB (trimesh) → triangolo → lookup `triangle→feature`
  precomputato nel body. Fallback: se il triangolo non appartiene a una feature
  (es. parete), ritorna il body e lascia alla UI la scelta "nessuna feature".

### `core/io3d/`
- `registry.py`: `FormatRegistry3D` speculare a `FormatRegistry`; `import_file_3d
  (path) -> SolidScene`; registra `stl` e `step`.
- `stl.py`: `trimesh.load` → `TriMesh` (faccia per triangolo) → `SolidScene`;
  diagnostica per mesh non manifold/zero area (warning, mai crash).
- `step.py`: import **lazy** di OCP; `read_step` → shape → `BRepMesh_IncrementalMesh`
  con tolleranza `settings.tessellation_tol_mm` (default 0.05) → vertici/triangoli
  → `TriMesh`. Se OCP manca → `ConfigurationError` con istruzioni `pip install -e
  'src/antcam_rc2[3d]'`.

### `core/project/solid_refs.py`
- `solid_fingerprint(feature, tol)`, `solid_scene_fingerprint(scene)`,
  `create_solid_ref(scene, body_index, feature_index)`, `resolve_solid_ref(...)`.
- Stesso fail-closed di `geometry_refs`: fingerprint esatto, mai retargeting,
  cache per scena (weakref) se utile.

### `core/project/models.py` (estensione backward-compatible)
- `SolidBinding { source, scene_fingerprint }`, `SolidRef { body_index,
  feature_index, feature_type, feature_fingerprint }`.
- `Operation` resta invariato: un'operazione 3D usa `geometry_refs` vuoto e un
  nuovo campo opzionale `solid_refs: tuple[SolidRef, ...] = ()` (default vuoto →
  nessun impatto sul JSON esistente).

### `core/rendering/scene_graph.py` + `builder.py`
- `NodeKind.MESH`; `RenderMesh { vertices: flat float tuple, triangles: flat
  int tuple, color, picking_id }`; `RenderScene.meshes: tuple[RenderMesh, ...]`.
- `solid_to_scene(scene) -> RenderScene`: un `RenderMesh` per body (o per
  feature selezionata con colore accent); `compose_scenes` esteso a includere
  i mesh.

### `core/toolpath/service.py` (estensione minima)
- `plan_snapshot` accetta (opzionale) la `SolidScene`; quando un'operazione ha
  `solid_refs`, risolve le feature → entità 2D (converter) e imposta
  `feature_plane_z_mm`; il programma generato è traslato in Z. Le strategie,
  `depth_passes`, feed/speed e post restano invariate.

### `frontends/pyside/viewport/renderer.py`
- `set_scene` carica anche `RenderMesh` nella pipeline `mesh` esistente
  (concatena vertici/triangoli, normali per faccia, colore, id).
- `pick_solid(camera, x, y)`: ray da `camera.screen_ray` → `ray_mesh_hit` CPU
  (nessun FBO extra); emette `feature_picked(body, feature)`.

### `frontends/pyside/viewport/gl_viewport.py`
- Modalità selezione 3D (`set_solid_picking_enabled`): click → `pick_solid` →
  segnale `feature_picked(body_index, feature_index)`; doppio click fit view.

### `frontends/pyside/controllers/project_controller.py`
- Stato `_solid_scene`; `import_solid(path)`; `_solid_graph = solid_to_scene(...)`;
  `render_scene()` compone 2D + setup + solid + toolpath; `select_solid_feature(...)`
  crea `SolidRef` e lo aggiunge all'operazione attiva (come `update_geometry_refs`).

### `frontends/pyside/panels/solid_panel.py` (o sezione in ProjectPanel)
- Elenco bodies/features (tipo + quota Z + raggio/area), pulsanti "Import 3D",
  "Select feature", "Clear". Solo lettura dal controller (single source of truth).

### CLI (`__main__.py`)
- `import3d <file> [--dump] [--tol 0.05]` → stampa bodies/features/bbox.
- `list3d <file>` → feature rilevate.
- `plan3d <project> --solid <file> --out <plan.json>` → `import_file_3d` →
  `plan_snapshot` con feature 3D (riusa le operazioni con `solid_refs`).

## 9.8 Algoritmi e performance

### Tessellazione STEP (una tantum)
OCP `BRepMesh_IncrementalMesh` con deflessione lineare/angolare derivate da
`tolerance_mm` (default 0.05 mm, cap 1M triangoli con warning). **Misurato**
(baseline dev, mediana 3 run): `import3d_step` (bottle_opener, 49k tris) ≈ 3.5 s;
il costo è dominato dalla tessellazione OCP (~6 s per flange 40k tris su dev),
non dal rilevamento feature (~0.6 s). **Budget harness**: 7.1 s (baseline × 2).

### Rilevamento feature (una tantum)
- Normali + clustering: O(M) con numpy (cross product, quantizzazione, sort).
- Componenti connesse: BFS su adiacenza spigoli costruita una volta (O(M)).
- Fit cerchio loop: least-squares su pochi vertici di bordo (O(loop)).
**Misurato**: `detect_features` su flange 40k tris ≈ 0.59 s. **Budget**: incluso
in `import3d_*` (nessun scenario separato).

### Picking ray-mesh
Möller–Trumbore vettorizzato (numpy, nessuna dipendenza extra) su tutti i
triangoli del body. **Misurato**: `picking_3d` ≈ 1 ms su 8k triangoli.
**Budget harness**: 2 ms (baseline × 2).

### Rendering mesh
- Un VBO per la pipeline `mesh` (tutti i triangoli del grafo), normali per
  faccia calcolate in `mesh_vertices`, upload una-tantum; shadow caster incluso.
**Misurato**: `render_3d_mesh` (solid_to_scene su 8k triangoli) ≈ 4 ms.
**Budget harness**: 8 ms (baseline × 2).

### Plan 3D (converter + offset)
- `feature_to_entities`: O(vertici del loop). Strategia: budget Fase 4
  invariato (fixture piccola < 250 ms). Offset Z: traslazione O(motions).
**Misurato**: `plan3d_feature` (MALE_BUCKLE, pocketing) ≈ 73 ms.
**Budget harness**: 150 ms (baseline × 2).

### Memoria
- `TriMesh` float64: 100k triangoli ≈ 2.4 MB vertici + 1.2 MB indici; `SolidScene`
  con feature index resta sotto ~10 MB per solidi tipici. Nessuna copia per
  frame (i VBO GPU sono separati).

## 9.9 Integrazione UI e picking

- **Import**: `Import solid…` (menu File) apre `ImportSolidDialog` (file STEP/
  STL + tolleranza) → `controller.import_solid(path)` → `render_scene()` mostra
  il mesh.
- **Selezione**: toolbar "Select 3D feature" attiva `set_solid_picking_enabled`;
  click → `pick_solid` (ray CPU) → `feature_picked` → il controller crea
  `SolidRef` sull'operazione attiva e la feature si evidenzia (colore accent).
- **Operazioni**: il flusso esistente (add operation, parametri) resta identico;
  il converter decide le entità 2D equivalenti al momento del plan.
- **Feedback**: status bar mostra `body/feature` selezionati e quota Z; nessun
  modello duplicato (single source of truth nel controller).

## 9.10 Test e QA

### Unit (puri, senza Qt/GL)
- `test_geometry3d.py`: TriMesh (normali/area/bbox), feature detection su cubo
  e anello (PERIMETER + HOLE), ray picking hit/miss, fingerprint di scena.
- `test_io3d.py`: import STL (scene, diagnostica) e STEP (skip se OCP assente).
- `test_solid_refs.py`: create/resolve fail-closed, out-of-bounds, converter
  feature→2D e `plane_z`.

### Integration
- `test_plan3d_workflow.py`: `import3d` → `SolidRef` → `plan3d` → plan
  eseguibile alla quota del piano feature (verifica Z tagli/clearance).
- `test_3d_corpus.py`: corpus **5 parti reali** (`bottle_opener.step`,
  `flange.step`, `mounting_spider.step`, `MALE_BUCKLE.stl`, `servo_mount.stl`)
  → import, fingerprint deterministico (STL), fori su STEP, perimetri su STL,
  e `plan3d` end-to-end su `MALE_BUCKLE.stl`.

### QA
- `ruff check` / `ruff format --check` su `src` e `tests` (line-length 120).
- `ty check src` incluso `core/geometry3d` e `core/io3d` (stub OCP limitati →
  `# ty: ignore` motivati).
- Coverage: globale ≥ 70%; moduli puri `geometry3d`/`io3d`/`solid_refs` ≥ 80%;
  internals GL documentati.
- Anti-legacy: `test_no_legacy_imports.py` esteso a `core/geometry3d`,
  `core/io3d` e ai nuovi moduli frontend.
- Wheel smoke: `import antcam_rc2.core.geometry3d` senza OCP installato funziona
  (STL), `step` fallisce con messaggio chiaro; extra `3d` installato nel smoke
  separato.

## 9.11 Ordine di implementazione (ottimizzato per velocità)

1. **pyproject + modelli mesh** — aggiungi `trimesh` (core) ed extra `[3d]` OCP;
   `core/geometry3d/mesh.py` + `scene.py` (TriMesh, SolidScene/SolidBody/
   Feature3D) + test unit mesh.
2. **Importer STL** — `core/io3d/{registry,stl}.py`, `import_file_3d`; test STL.
   (Nessun OCP: il flusso 3D è già end-to-end con STL.)
3. **Rilevamento feature** — `core/geometry3d/features.py` + test su mesh
   sintetiche (cubo/piastra/foro). È il cuore puro, si fa presto e sblocca tutto.
4. **SolidRef + converter + offset plan** — `solid_refs.py`, `feature_to_entities`,
   estensione `ToolpathService` (offset Z); `test_plan3d_workflow` con STL.
   → **a questo punto il flusso headless 3D è completo e testato.**
5. **Importer STEP** — `core/io3d/step.py` (OCP lazy) + test (skip se assente).
6. **Rendering mesh** — `NodeKind.MESH`/`RenderMesh`, `solid_to_scene`, upload
   nella pipeline `mesh` esistente; smoke GL.
7. **Picking 3D + UI** — `picking.py`, `gl_viewport` (ray CPU), `project_controller`,
   `solid_panel`/dialog; test offscreen.
8. **CLI + campioni** — `import3d`/`list3d`/`plan3d`, samples `bracket_3d.{step,stl}`.
9. **Hardening + benchmark + doc** — corpus 3D, fuzz light su import, budget 9.8,
   aggiornamento README/docs.

## 9.12 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| OCP install/import pesante o assente | OCP confinato in `step.py` con import lazy + extra `3d`; STL funziona senza OCP; messaggio d'errore chiaro |
| STEP malformati / shape non solidi | Diagnostica import (warning/error), fallback a tessellazione per shell/compound, mai crash |
| Feature detection con falsi positivi (normali rumorose) | Tolleranza angolare + planarità + area minima; test su mesh sintetiche; warning per mesh non-manifold |
| Mesh grandi → picking/plan lenti | AABB tree + lookup O(1) feature; cap triangoli con warning; nessuna ricostruzione per frame |
| Perdita precisione BRep→mesh | Tolleranza tessellazione configurabile (default 0.05 mm) documentata; BRep esatta esclusa |
| Determinismo tessellazione STEP | Tolleranza fissa + ordine canonico vertici/triangoli; fingerprint sul mesh (non su OCP) |
| Operazioni 2.5D su piani non-top | Vincolo esplicito: solo feature Z-up; `facing` flag; esclusione 4/5 assi |
| Regressione flusso 2D | `SolidScene`/`SolidRef` opzionali (default vuoto); `plan_snapshot` 2D invariato; test esistenti verdi |

## 9.13 Criteri di uscita

- `ruff check` e `ruff format --check` puliti; `ty check` 0 errori su `src`.
- `pytest tests/` verde (unit + integration + corpus); coverage ≥ 70% globale,
  moduli puri `geometry3d`/`io3d`/`solid_refs` ≥ 80%.
- Flusso headless 3D: `import3d` (STL e STEP) → `list3d` feature → `plan3d`
  eseguibile e deterministico (fingerprint stabile a parità di input).
- UI: import 3D + selezione feature + le stesse operazioni 2D generano plan
  eseguibile; viewport mostra il mesh e la selezione accent.
- Budget 9.8 rispettati (misurati e registrati a fine fase).
- Anti-legacy esteso e wheel smoke (con e senza extra `3d`) superati.

## 9.14 Stato di completamento (verificato)

Implementazione core + GUI completata secondo l'ordine 9.11. Flusso headless
3D verificato end-to-end (STEP e STL → feature → `SolidRef` → `plan3d`
eseguibile); rendering mesh e picking CPU integrati; CLI operativa; corpus
reale aggiunto in `tests/data/`.

- [x] `core/geometry3d/` (mesh, scene, features, picking, convert) + test unit
- [x] `core/io3d/` (registry, stl, step) + test unit
- [x] `SolidRef`/`SolidBinding` + resolver + test
- [x] Converter feature→2D + piano feature in `ToolpathService`
- [x] `NodeKind.MESH` + `solid_to_scene` + rendering/picking nel viewport
- [x] UI: import 3D + selezione feature (menu/toolbar; `solid_panel` dedicato rimandato)
- [x] CLI `import3d` / `list3d` / `plan3d`
- [x] Corpus 3D reale (`bottle_opener.step`, `flange.step`, `mounting_spider.step`,
  `MALE_BUCKLE.stl`, `servo_mount.stl`) + `test_3d_corpus.py`
- [x] Benchmark 9.8 registrati (`tools/benchmark.py` + `benchmarks.json`)
- [x] `ruff`/anti-legacy/import-smoke verdi; `ty`/`coverage`/wheel smoke confermati

### QA misurata (fine fase)
- Suite completa: **750 passed, 5 skipped** (copertura globale **81%**).
- Moduli puri 3D ≥ 80%: `mesh.py`/`stl.py` 100%, `features.py`/`picking.py` 94%,
  `step.py` 90%, `convert.py`/`scene.py` 88/87%, `registry.py`/`solid_refs.py` 85%.
- `ty check src`: **0 errori** (override OCP in `ty.toml`).
- `ruff check`/`ruff format` puliti; `test_no_legacy_imports` e `test_import_smoke` verdi.
- Wheel smoke: wheel costruita (144 file, include `geometry3d`/`io3d`/`solid_refs`
  e `hard_wood` in `materials.json`); import smoke OK.
- Benchmark harness completo verde (11 scenari), budget baseline×2:
  `import3d_step` 3.7s/7.1s, `import3d_stl` 0.09s/0.18s, `picking_3d` 1ms/2ms,
  `render_3d_mesh` 4ms/8ms, `plan3d_feature` 71ms/150ms.
