# AntCAM RC2 — Piano Fixture Avanzate: rendering, persistenza e modelli 3D

## Obiettivo

Estendere la gestione delle fixture (già implementata in Fase 3/5) con tre capacità
complementari, mantenendo la separazione core/UI e i contratti esistenti:

1. **Rendering fixture opaco**: le fixture devono apparire come solidi rosso scuro
   (non trasparenti) nel viewport 3D, distinguibili dallo stock traslucido.
2. **Persistenza forma/posizione fixture**: salvare e ricaricare libreria fixture
   (forme box + posizione) indipendentemente dal progetto, per riutilizzo rapido.
3. **Import modello 3D (STEP/STL)**: associare un file mesh a una fixture per
   visualizzazione realistica e collision detection preciso (Fase 6+).

Il core rimane privo di dipendenze Qt; il frontend PySide6 consuma il scene graph
neutro e usa l'existing `import_file_3d` per i modelli fixture.

## Stato di partenza verificato

- `Fixture` model (Fase 3) ha già `mesh_path: str | None` (line 111
  `src/antcam_rc2/core/project/models.py`) — campo dormiente, mai usato.
- `FixtureKind` enum ha `FIXED`, `VISE`, `SCREW` (aggiunto recentemente).
- `setup_to_scene()` in `core/rendering/builder.py` renderizza fixture come
  `SOLID_BOX` grigio con alpha 0.9 (traslucido) + outline.
- `import_file_3d()` (Fase 5, `core/io3d/registry.py`) importa STEP/STL in
  `SolidScene` → `RenderMesh` via `solid_to_scene()`.
- `ProjectController.import_solid()` carica solidi 3D nel viewport ma non li
  associa alle fixture.
- Persistenza progetto (`.antcam.json`) serializza `Project.fixtures` tuple;
  non esiste libreria fixture separata.
- Tema PySide6 espone `FIXTURE_COLOR = (0.55, 0.57, 0.62, 0.9)` (grigio
  traslucido).

## Confini di responsabilità

### Incluso

- Cambio colore fixture nel scene graph: rosso scuro opaco (es. `(0.7, 0.15,
  0.15, 1.0)`), outline coerente.
- Nuovo file `core/project/fixture_library.py`: repository fixture indipendente
  dal progetto (JSON in `~/.antcam/fixtures/` o path configurabile).
- UI: dialog "Load Fixture from File" (STEP/STL) che importa mesh, calcola
  bounding box, pre-compila larghezza/lunghezza/altezza/posizione, salva in
  libreria e/o aggiunge al progetto corrente.
- UI: pannello "Fixture Library" (nuova tab o sezione in ProjectPanel) per
  salvare fixture corrente in libreria, caricare da libreria, eliminare.
- Core: `Fixture.mesh_path` diventa percorso relativo alla libreria fixture
  (non al progetto); al salvataggio progetto il path è copiato/referenziato.
- Scene graph: se `fixture.mesh_path` esiste e file leggibile → emetti
  `RenderMesh` (da `SolidScene`) invece di `SOLID_BOX`; fallback a box se
  import fallisce.
- Collision detection (Fase 6+): se mesh presente → usa mesh per test
  cilindro-vs-mesh (accurato) invece di box AABB; altrimenti box come oggi.

### Escluso deliberatamente

- Editing mesh nel viewport (traslazione/rotazione/scaling con manipolatori):
  Fase 6+ o backlog UI.
- Snapping fixture a geometria stock/operazioni: Fase 6+.
- Fixture parametriche (es. morsa con ganasce mobili): backlog.
- Simulazione fixture deformabili: fuori scope.
- Web UI (Fase 10): consumerà stesso scene graph; libreria fixture gestita
  lato server o localStorage.

## Decisioni architetturali

### 1. Colore fixture — solo tema + builder

Il colore non va nel modello `Fixture` (dominio puro); è responsabilità del
frontend. `theme.py` espone `FIXTURE_COLOR_SOLID` (rosso scuro opaco) e
`FIXTURE_OUTLINE_COLOR`. `setup_to_scene()` riceve questi parametri (già
previsti come argomenti con default) — nessuna modifica al contratto.

### 2. Libreria fixture — file-based, non database

Una fixture library è una lista di `Fixture` serializzata in JSON
(`fixtures.json` nella config dir). Nessun nuovo catalogo SQL; riusa
`ProjectRepository` pattern (file atomico, validazione Pydantic). Ogni fixture
in libreria ha `id` (runtime `fix_*`), `name`, tutti i campi `Fixture` incluso
`mesh_path` (relativo a `fixtures/meshes/`).

### 3. Import mesh fixture — flusso UI → core

```
User: "Import Fixture Mesh" (STEP/STL)
  → FileDialog → ProjectController.import_fixture_mesh(path)
  → core.io3d.import_file_3d(path) → SolidScene
  → calcola bbox → suggerisce width/length/height/position (centro bbox = 0,0,0)
  → apre FixtureDialog pre-compilato con mesh_path = path copiato in
    fixtures/meshes/<fixture_id>.<ext>
  → utente conferma → add_fixture(fixture) → libreria opzionale
```

### 4. Scene graph fixture con mesh

`setup_to_scene()` itera `project.fixtures`:
- se `fixture.mesh_path` e file esiste → `import_file_3d(mesh_path)` →
  `solid_to_scene()` → trasla mesh in posizione fixture → emetti `RenderMesh`
  con `fixture_color_solid`
- else → `SOLID_BOX` come oggi (backward compatible)

La mesh è importata **ogni volta** che si ricostruisce il setup graph (costo
trascurabile: fixture poche, mesh piccole). Opzionale: cache LRU keyed by
`(mesh_path, mtime, fixture_id)`.

### 5. Collision detection con mesh (preparazione Fase 6)

`check_fixture()` in `core/simulation/collision.py` riceve `Fixture` tuple.
Estensione futura: se `fixture.mesh_path` → carica `SolidScene` (cached) e
usa test cilindro-vs-triangoli (es. `trimesh` o `pyvista` se dipendenze
accettabili, altrimenti AABB tree custom). Per ora: **documentare** che
`mesh_path` abilita collisione accurata in Fase 6; oggi usa box AABB.

## Struttura file (aggiunte/modifiche)

```
src/antcam_rc2/
├── PLAN_PRO_FIXTURE.md                    ← questo documento
├── src/antcam_rc2/
│   ├── core/
│   │   ├── project/
│   │   │   ├── __init__.py
│   │   │   ├── models.py                  ← + docstring mesh_path usage
│   │   │   ├── fixture_library.py         ← NUOVO: load/save/list fixtures
│   │   │   └── persistence.py             ← + fixture_library_path opzionale
│   │   ├── rendering/
│   │   │   └── builder.py                 ← setup_to_scene: mesh support
│   │   └── simulation/
│   │       └── collision.py               ← + doc: mesh_path per Fase 6
│   └── frontends/pyside/
       ├── theme.py                         ← + FIXTURE_COLOR_SOLID, OUTLINE
       ├── app_window.py                    ← + fixture library panel/tab
       ├── controllers/
       │   └── project_controller.py        ← + import_fixture_mesh(),
                                                 save_fixture_to_library(),
                                                 load_fixture_from_library()
       ├── panels/
       │   ├── project_panel.py             ← + "Import Mesh", "Save to Lib",
                                                 "Load from Lib" buttons
       │   └── fixture_library_panel.py     ← NUOVO: lista libreria fixture
       └── dialogs/
           └── fixture_dialog.py            ← + mesh_path field (read-only,
                                                 mostra nome file se presente)
```

## Dettaglio implementazione per modulo

### `core/project/models.py` (modifiche minime)

- `Fixture.mesh_path`: docstring chiarisce che è path relativo a fixture library
  meshes dir, o assoluto se utente non usa libreria. Validazione: se presente,
  file deve esistere al momento dell'uso (non al salvataggio progetto — path
  può essere su macchina diversa).

### `core/project/fixture_library.py` (NUOVO)

```python
class FixtureLibrary:
    """File-based fixture library: list/save/load/delete fixtures."""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.meshes_dir = base_dir / "meshes"
        self.index_path = base_dir / "fixtures.json"
        self.meshes_dir.mkdir(parents=True, exist_ok=True)

    def list_fixtures(self) -> list[Fixture]: ...
    def get_fixture(self, fixture_id: str) -> Fixture | None: ...
    def save_fixture(self, fixture: Fixture, mesh_source: Path | None = None) -> Fixture:
        """Copy mesh to meshes_dir/<fixture_id>.<ext>, update mesh_path, save index."""

    def delete_fixture(self, fixture_id: str) -> None: ...
```

- `fixtures.json`: `{ "fixtures": [ fixture_dict, ... ] }` (array di oggetti
  `Fixture` serializzati). Validazione Pydantic al load.
- `mesh_path` salvato come relativo: `meshes/fix_abc12345.step`.

### `core/rendering/builder.py` — `setup_to_scene()`

```python
def setup_to_scene(
    project: Project,
    ...,
    fixture_color: RGBA = theme.FIXTURE_COLOR_SOLID,      # NUOVO default
    fixture_outline_color: RGBA = theme.FIXTURE_OUTLINE_COLOR,  # NUOVO
    fixture_mesh_color: RGBA = theme.FIXTURE_COLOR_SOLID, # per mesh
) -> RenderScene:
    ...
    for fixture in project.fixtures:
        if fixture.mesh_path and Path(fixture.mesh_path).exists():
            mesh_scene = import_file_3d(fixture.mesh_path)
            # trasla mesh in posizione fixture
            mesh_graph = solid_to_scene(mesh_scene, color=fixture_mesh_color)
            # applica offset fixture.position_* a tutti i vertici mesh
            nodes.extend(_translate_mesh_nodes(mesh_graph.meshes, fixture))
        else:
            # box fallback (colore nuovo)
            nodes.append(RenderNode(kind=NodeKind.SOLID_BOX, box=fixture_box,
                                    color=fixture_color))
            nodes.append(RenderNode(kind=NodeKind.BOX_OUTLINE, box=fixture_box,
                                    color=fixture_outline_color, width_px=1.0))
```

- `_translate_mesh_nodes()`: helper puro che aggiunge offset a vertici
  `RenderMesh` (tuple flat → list → add offset → tuple).

### `frontends/pyside/theme.py`

```python
FIXTURE_COLOR_SOLID: RGBA = (0.7, 0.15, 0.15, 1.0)  # rosso scuro opaco
FIXTURE_OUTLINE_COLOR: RGBA = (0.9, 0.3, 0.3, 1.0)  # rosso chiaro outline
FIXTURE_MESH_COLOR: RGBA = (0.65, 0.1, 0.1, 1.0)  # per mesh importate
# deprecate/alias per compatibilità:
FIXTURE_COLOR = FIXTURE_COLOR_SOLID
```

### `frontends/pyside/controllers/project_controller.py`

```python
def import_fixture_mesh(self, path: Path) -> Fixture | None:
    """Import STEP/STL, compute bbox, open FixtureDialog pre-filled, return fixture or None."""
    scene = import_file_3d(path)
    bbox = scene.bounding_box()  # implementare in SolidScene
    center = bbox.center
    fixture = Fixture(
        id=new_id("fix"),
        name=path.stem,
        kind=FixtureKind.FIXED,
        width_mm=bbox.width,
        length_mm=bbox.height,  # o depth secondo orientamento
        height_mm=bbox.depth,
        position_x_mm=-center[0],
        position_y_mm=-center[1],
        position_z_mm=-center[2],
        mesh_path=None,  # sarà impostato dal dialog dopo copia in libreria
    )
    dialog = FixtureDialog(self._viewport, fixture=fixture, mesh_source=path)
    if dialog.exec():
        return dialog.result_fixture()
    return None


def save_fixture_to_library(self, fixture: Fixture, mesh_source: Path | None = None) -> Fixture:
    """Save fixture to user library, copying mesh if provided."""
    lib = FixtureLibrary.get_default()
    saved = lib.save_fixture(fixture, mesh_source)
    self.fixture_library_changed.emit()  # NUOVO segnale
    return saved


def load_fixture_from_library(self, fixture_id: str) -> Fixture | None:
    lib = FixtureLibrary.get_default()
    fixture = lib.get_fixture(fixture_id)
    if fixture:
        self.add_fixture(fixture)  # aggiunge al progetto corrente
    return fixture
```

### `frontends/pyside/panels/project_panel.py`

- Aggiungi 3 pulsanti sotto lista fixture:
  - **"Import Mesh…"** → `QFileDialog` STEP/STL → `controller.import_fixture_mesh()`
  - **"Save to Library…"** → salva fixture selezionata in libreria
  - **"Load from Library…"** → apre `FixtureLibraryDialog` (nuovo) per scegliere

### `frontends/pyside/panels/fixture_library_panel.py` (NUOVO)

- `QListWidget` con fixture da libreria (nome, kind, dimensioni, anteprima
  thumbnail opzionale).
- Pulsanti: "Add to Project", "Delete", "Refresh".
- Doppio click → "Add to Project".

### `frontends/pyside/dialogs/fixture_dialog.py`

- Nuovo parametro opzionale `mesh_source: Path | None`.
- Se `mesh_source` fornito → copia in `FixtureLibrary.meshes_dir` al accept,
  imposta `fixture.mesh_path` relativo.
- Campo read-only "Mesh: `<filename>`" visibile se `mesh_path` impostato.

## Test e QA

### Unit (puri, no Qt)
- `test_fixture_library.py`: save/load/delete/list, mesh copy, path relativi,
  validazione Pydantic, round-trip JSON.
- `test_fixture_rendering.py`: `setup_to_scene` emette `RenderMesh` se
  `mesh_path` valido, `SOLID_BOX` altrimenti; colori corretti; vertici
  traslati in posizione fixture.
- `test_fixture_collision_mesh.py` (documentale): `check_fixture` con mesh
  path presente → log warning "mesh collision coming in Fase 6"; oggi usa box.

### Integration (offscreen Qt)
- `test_fixture_mesh_import.py`: importa STEP/STL → fixture creata con
  dimensioni bbox → aggiunta a progetto → render scene contiene `RenderMesh`.
- `test_fixture_library_ui.py`: save fixture to library → load from library →
  fixture appare in progetto con mesh_path corretto.
- `test_fixture_color.py`: fixture renderizzate con `FIXTURE_COLOR_SOLID`
  (alpha=1.0), non `FIXTURE_COLOR` legacy.

### QA
- `ruff check` / `ruff format --check` puliti.
- `ty check src` 0 errori (stub PySide6 OK).
- Coverage: moduli nuovi `fixture_library`, `fixture_library_panel` ≥ 80%;
  modifiche `builder`, `collision` ≥ 80%.
- Anti-legacy: nessun import `antcam`/`antcam_rc1`.

## Ordine di implementazione

1. **Core**: `FixtureLibrary` + test unit.
2. **Core**: `setup_to_scene` mesh support + test unit rendering.
3. **Tema**: `FIXTURE_COLOR_SOLID`, `FIXTURE_OUTLINE_COLOR`, `FIXTURE_MESH_COLOR`.
4. **Controller**: `import_fixture_mesh`, `save_fixture_to_library`,
   `load_fixture_from_library` + segnali Qt.
5. **Dialog**: `FixtureDialog` + `mesh_source` + copia mesh in libreria.
6. **Panel**: `ProjectPanel` pulsanti Import/Save/Load + `FixtureLibraryPanel`.
7. **Finestra**: integra `FixtureLibraryPanel` (nuova tab o expander in
   ProjectPanel).
8. **Collision**: docstring `check_fixture` per Fase 6 mesh-aware.
9. **Test**: unit + integration + QA completo.

## Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| Mesh fixture grandi → performance rendering | Fixture tipiche piccole (morse, blocchi); `solid_to_scene` batching per mesh; fallback a box se > N triangoli (configurabile). |
| Path mesh non portabili tra macchine | `mesh_path` relativo a `fixtures/meshes/`; libreria per utente (`~/.antcam/fixtures/`); progetto salva solo reference, non copia mesh. |
| `import_file_3d` fallisce su STEP corrotti | Try/except in `import_fixture_mesh`; mostra errore in dialog, non crasha. |
| Libreria fixture cresce illimitatamente | UI mostra size; pulizia manuale; opzionale quota max in futuro. |
| Colore rosso scuro poco visibile su sfondo scuro | Outline rosso chiaro + opzione tema "high contrast" in futuro. |
| `ty check` su `FixtureLibrary` (pathlib, I/O) | Tipizzazione esplicita; `Path` nativo supportato. |

## Criteri di uscita

- `ruff check` / `ruff format --check` / `ty check` puliti.
- `pytest tests/` verde; coverage globale ≥ 70%, moduli fixture ≥ 80%.
- Workflow manuale verificato:
  1. Import STEP morsa → fixture creata con dimensioni corrette → appare
     rossa opaca nel viewport.
  2. Save to Library → fixture in `~/.antcam/fixtures/fixtures.json`.
  3. Nuovo progetto → Load from Library → morsa appare con mesh.
  4. Progetto salvato/ricaricato → fixture con mesh persistita.
- Budget: `setup_to_scene` con 10 fixture mesh < 50 ms (escluso import
  iniziale mesh, fatto in background UI).

## Note per fasi future

- **Fase 6 (Simulazione)**: `check_fixture` userà `fixture.mesh_path` per
  collisione cilindro-vs-mesh precisa (richiede `trimesh` o `pyembree`
  opzionale). Aggiungere dipendenza solo se valore dimostrato.
- **Fase 10 (Web)**: `FixtureLibrary` esposta via API REST o sincronizzata
  via localStorage; scene graph `RenderMesh` già compatibile three.js.