# AntCAM RC2 — Fase 6: Simulatore voxel, collisioni e timeline

## 6.1 Obiettivo

Costruire il motore di simulazione **UI-agnostico** (`core/simulation/`) che
consuma i `ToolpathPlan` della Fase 4: rimozione materiale su griglia voxel del
stock, swept volume dell'utensile, **collision detection** dell'assemblaggio
utensile (portacolletto e spindle contro materiale residuo; gambo e
portacolletto/spindle contro fixture; tip nel work-area), timeline con
play/pause/step e **report eventi esportabile in JSON**. Il tutto deterministico,
veloce (numpy vettorizzato, niente cicli Python sui voxel) e consumabile sia da
CLI sia dal frontend
PySide6 (Fase 5) con rendering del volume via marching cubes.

**Criterio di uscita**: dato un progetto + toolpath eseguibile, un comando
headless produce un `SimulationReport` ripetibile (stesso input → stesso
fingerprint) con: volume rimosso, profondità massima, eventi di collisione
(severità/codice/posizione/operazione), timeline campionata e statistiche;
la UI permette play/pause/step, scrubbing su checkpoint e visualizzazione del
materiale rimosso nel viewport.

## 6.2 Stato di partenza verificato

- numpy **2.4.6**, scipy **1.17.1** (disponibili `scipy.ndimage` per dilatazione
  e `scipy.measure.marching_cubes` per il mesh di visualizzazione).
- `MachineProfile` espone già `spindle_diameter_mm` / `spindle_length_mm`
  (usati per l'envelope di collisione), `default_collet_size_mm` e
  `collet_sizes_mm`.
- `Tool` espone `tool_type`, `cutting_diameter_mm`, `shank_diameter_mm`,
  `flute_length_mm`, `overall_length_mm`, `max_cutting_depth_mm`; **manca** la
  geometria di V-bit (angolo) e T-slot (collo/testa) → estensione catalog
  backward-compatible prevista.
- `MotionCommand` (Fase 4) ha endpoint assoluto, `arc_center_xy`, `pass_index`,
  `operation_id`: tutto il necessario per tracciare l'utensile in WCS; gli archi
  elicoidali (thread milling) includono variazione Z.
- `Stock` (box + material) e `Fixture` (box + `mesh_path` non caricato) nel
  `Project`; `ToolpathPlan` è l'input; `SimulationError` esiste già in
  `core/errors.py`.
- Nessun modulo `core/simulation/` esiste; i pannelli UI di Fase 5 e il pattern
  worker (`QThreadPool`+`QRunnable`) sono pronti per l'integrazione.

## 6.3 Analisi e revisione del progetto

### A. Rappresentazione del volume: voxel bool su griglia regolare
**Opzioni**: griglia voxel binaria (rimozione monotona) vs field a più stati vs
mesh CSG. **Decisione**: griglia `np.ndarray[np.bool_]` (1 byte/voxel) con
rimozione **monotona** (la rimozione non ri-aggiunge mai materiale: si scarta
il ri-taglio). Il field multi-stato è inutile per 2.5D (il simulatore non
ri-depone) e rallenta le operazioni; CSG è fuori scala per interattività.

**Analisi memoria (Makera 200×200×100)**: 1.0 mm → 4.0 M voxel → 4 MB; 0.5 mm →
32 M voxel → 32 MB; 0.25 mm → 256 M → 256 MB (non accettabile). **Politica**:
risoluzione di default derivata dalla stock box con tetto `MAX_VOXELS ≈ 32 M`
(≈ 32 MB) e floor 0.5 mm; l'utente può forzare ma oltre il tetto viene
clampata con warning nel report. Formula: `voxel = max(0.5, ceil(volume/MAX))`.

### B. Swept volume del tagliente
Per utensili ad asse verticale (tutta la Fase 4 è 2.5D con asse Z) lo swept
volume di un movimento lineare tra `p1` e `p2` a profondità costante è un
**capsuloide orizzontale**: voxel con distanza XY dal segmento `[p1, p2] ≤ r`
e `z ∈ [z_min, z_max]`. La formula vale anche per segmenti con variazione Z
(lineari) perché la proiezione XY è invariante.

**Tecnica vettorizzata**: bounding box del segmento + raggio → si estrae solo
il sotto-array dei voxel interessati (tipicamente poche migliaia) → distanza XY
vettorizzata via proiezione sul segmento (numpy broadcasting, niente meshgrid
materializzato) → maschera → `&= ~mask`. Gli **archi** si campionano a passo
`≤ voxel/2` in sotto-segmenti (riuso della logica lineare); l'elica del thread
milling è un arco con Z crescente (già coperta). I **rapid** non rimuovono
materiale ma partecipano alle collisioni.

**Envelope per tipo utensile** (nuovo modulo `tool_geometry.py`):
- end_mill/bull/drill/bore/thread_mill/tap: cilindro del tagliente
  (`cutting_diameter/2`) su `flute_length`, poi gambo (cilindro più stretto).
- ball: cilindro + calotta sferica inferiore (sweep = segmento XY + distanza
  alla calotta per la fetta inferiore).
- v_bit: **cono** — richiede `v_bit_angle_deg`; raggio alla quota z =
  `tan(angle/2)·(z - tip_z)`; per fetta Z si usa il raggio locale (mantiene la
  vettorizzazione per fetta).
- t_slot: collo (`t_slot_neck_diameter_mm`) + testa (`t_slot_head_diameter_mm`)
  come due cilindri assiali.
**Fallback documentato**: campi opzionali assenti → cilindro conservativo +
evento `warning` nel report (mai rompere il catalogo esistente).

### C. Collision detection: assemblaggio utensile vs mondo
Il modello a **stack verticale** (dal basso): tagliente (`flute_length`),
gambo (`overall - flute`), **portacolletto** (cilindro di raccordo tra colletto
e spindle, approssimato come `spindle_diameter` per 8 mm), spindle
(`spindle_diameter × spindle_length`). Ogni corpo ha un raggio e una quota
relativa al tip.

**Revisione critica del modello (importante)**: il catalogo valida
`shank_diameter ≥ cutting_diameter`, quindi **il gambo non è mai più stretto
del tagliente**: in un foro/tasca scavata dal tagliente, il gambo a diametro
uguale *occupa legittimamente* il foro. Verificare il gambo contro il materiale
residuo produrrebbe **falsi positivi sistematici** (le pareti della tasca a
distanza = raggio di taglio). Modello corretto e adottato:

- **gambo**: verificato solo contro **fixture** (box) — mai contro il materiale
  residuo (è dentro il foro che ha scavato);
- **portacolletto e spindle** (più larghi del foro): verificati contro
  **materiale residuo** (qualsiasi voxel occupato nel loro raggio è una vera
  collisione) + **fixture**;
- **work-area**: vincolo sul **tip** dell'utensile (e sul percorso) dentro il
  box macchina — mai sullo spindle (fisicamente appeso alla torretta, può
  sporgere sopra il piano di lavoro: un check sullo spindle contro il box
  produrrebbe falsi positivi su quote Z alte);
- **regola di reach**: se `profondità di taglio > flute_length + margine` →
  evento `error` deterministico (l'utensile fisicamente non raggiunge),
  indipendente dai voxel.

Pre-filtro AABB dell'envelope → early-out; verifica sul materiale per fetta
(sotto-array); margine di sicurezza `collision_margin_mm` arrotondato a
`≥ voxel_size` per essere significativo; sampling lungo il movimento a passo
`≤ voxel` per non perdere collisioni tra due tick.

### D. Timeline e scrubbing: checkpointing
Il report memorizza per tick: indice, posizione utensile, volume rimosso,
conteggio collisioni (numeri piccoli). Per lo **scrubbing** nella UI, ripristinare
il volume a un tick arbitrario ri-eseguendo i movimenti da zero sarebbe
costoso; la soluzione professionale è il **checkpointing**: ogni `K = 64`
tick si salva una copia della maschera. **Memoria vincolata**: il numero di
checkpoint è `min(16, floor(64 MB / bytes_per_mask))` — a 1 mm (4 MB/mask) fino
a 16 checkpoint (64 MB), a 0.5 mm (32 MB/mask) solo 2 (64 MB). Scrub =
ripristina il checkpoint più vicino + replay ≤ K step (~10–50 ms/step).

### E. Rendering del volume rimosso
Il viewport mostra il materiale residuo come **mesh** generata da
`scipy.measure.marching_cubes` sulla maschera (smoothing `scipy.ndimage` opzionale)
→ triangoli+normali → pipeline `solid` esistente della Fase 5 (nuova funzione
`mesh_vertices` in `buffers.py`). Aggiornamento ogni `S` step (es. 8) durante la
riproduzione; a riproduzione finita, mesh finale. L'utensile è un piccolo box/
cilindro posizionato sul tick corrente; le collisioni lampeggiano in rosso.

### F. Determinismo
Nessuna randomicità; tutte le posizioni sono quantizzate alla griglia voxel
prima di ogni operazione; fingerprint del report su JSON canonico (come
`ToolpathPlan.fingerprint()`); test dedicato (stesso input → stesso fingerprint
su ≥ 20 run).

### G. Dove vive la logica
`core/simulation/` è UI-agnostico (nessun Qt, nessuna dipendenza fuori da
numpy/scipy già presenti); il pannello timeline e il rendering vivono in
`frontends/pyside` (riuso del worker `QThreadPool`). La simulazione è un
**artefatto derivato**: mai scritta nel documento progetto (come il toolpath,
Fase 4.11); esportata in `*.simulation.json`. La cadenza di re-meshing del
volume (ogni N step) è **decisione UI**, non del core: `SimulationSettings`
resta puro (niente flag di rendering).

## 6.4 Confini

### Incluso
- Rimozione materiale voxel per tutte le 18 operazioni 2.5D (swept volume
  tagliente, archi inclusi, archi elicoidali inclusi).
- Collision detection dell'assemblaggio (tagliente escluso) vs materiale
  residuo, fixture (box) e work-area; pre-filtro AABB.
- Timeline (tick, play/pause/step) nel core come sequenza di stati + report;
  pannello UI dedicato.
- Report JSON versionato con eventi (info/warning/error/critical), statistiche
  (volume rimosso, profondità max, collisioni, durata stimata), fingerprint.
- CLI `antcam-rc2 simulate` (speculare a `plan`).
- Estensione catalog `Tool` con geometria opzionale V-bit/T-slot
  (backward-compatible, provenance + status).
- Rendering voxel via marching cubes + marker utensile + highlight collisioni
  nel viewport della Fase 5.
- Test unit/integration headless + smoke GL; determinismo; wheel test.

### Escluso deliberatamente
- Fisica: forze, deflessione utensile, vibrazioni, chip load dinamico, calore,
  coolant fisico.
- Mesh fixture: `mesh_path` resta non caricato; collisione su AABB box (il
  caricamento mesh è Fase 9/backlog).
- Stock 3D importato (STEP/STL): Fase 9; il simulatore parte dal box stock.
- Cinematica macchina, gantry, assi non lineari.
- Riposizionamento/ottimizzazione del piano (la simulazione non altera il plan).
- Simulazione multi-side e 4°asse.

## 6.5 Decisioni architetturali finali

1. **`core/simulation/`** puro: `models`, `voxels`, `tool_geometry`, `sweep`,
   `collision`, `simulator`. Il simulatore prende `Project` + `ToolpathPlan` +
   cataloghi (per risolvere i tool per operazione) + `SimulationSettings`;
   niente `GeometryScene` (il toolpath è già WCS assoluto).
2. **Grid monotona bool** con rimozione `&= ~mask`; conteggi scalari
   (rimosso/totale) incrementali; risoluzione derivata con tetto
   `MAX_VOXELS = 32M`.
3. **Swept volume vettorizzato per segmento**: AABB clip → sotto-array →
   distanza XY al segmento (broadcasting) → update. Archi campionati a passo
   ≤ voxel/2.
4. **Collisioni su stack verticale non-tagliente con responsabilità per corpo**
   (6.3 C): portacolletto/spindle vs materiale residuo + fixture; gambo vs
   fixture; tip/percorso nel work-area; regola di reach; pre-filtro AABB e
   verifica fetta-per-fetta.
5. **Worker thread** per la simulazione (pattern Fase 5); **checkpoint** ogni
   64 tick per lo scrubbing; mesh marching-cubes ogni 8 step durante la
   riproduzione.
6. **Artefatto derivato**: `SimulationReport` esportabile, mai nel documento
   progetto; `fingerprint()` canonica; round-trip JSON testato.
7. **Catalog esteso**: `Tool.v_bit_angle_deg`, `t_slot_neck_diameter_mm`,
   `t_slot_head_diameter_mm`, `tip_diameter_mm` opzionali (nullable), con
   fallback cilindro + warning; seed Makera aggiornati con
   `conservative_pending_verification`.

## 6.6 Struttura dei file

```text
src/antcam_rc2/
├── PLAN_FASE_6.md                        ← questo documento
├── src/antcam_rc2/
│   ├── __main__.py                       ← + subcomando `simulate`
│   ├── core/
│   │   ├── simulation/                   ← motore UI-agnostico (numpy/scipy)
│   │   │   ├── __init__.py
│   │   │   ├── models.py                 ← Settings, Event, Tick, Report, Severity
│   │   │   ├── voxels.py                 ← VoxelGrid (maschera, rimozione, conteggi)
│   │   │   ├── tool_geometry.py          ← envelope tagliente/gambo/v-bit/t-slot
│   │   │   ├── sweep.py                  ← swept volume segmenti/archi (vettorizzato)
│   │   │   ├── collision.py              ← stack utensile, AABB, verifica precisa
│   │   │   └── simulator.py              ← orchestrazione plan→report (+checkpoint)
│   │   ├── databases/models.py           ← + geometria opzionale Tool (nullable)
│   │   └── data/tools.json               ← + v_bit_angle / t_slot neck-head (seed)
│   └── frontends/pyside/
│       ├── controllers/simulation_controller.py   ← worker QThreadPool
│       ├── panels/simulation_panel.py             ← timeline play/pause/step
│       ├── widgets/timeline_widget.py             ← slider + pulsanti (testabile)
│       └── viewport/
│           ├── buffers.py                ← + mesh_vertices(triangoli+normali)
│           └── renderer.py               ← + pipeline mesh voxel (riuso solid)
└── tests/
    ├── data/simulation/                  ← fixture plan JSON + attese
    ├── unit/
    │   ├── test_voxel_grid.py
    │   ├── test_tool_geometry.py
    │   ├── test_sweep.py
    │   ├── test_collision.py
    │   ├── test_simulation_models.py
    │   └── test_simulation_report.py
    ├── integration/
    │   ├── test_simulation_workflow.py   ← project→plan→simulate→report
    │   ├── test_simulation_determinism.py
    │   ├── test_cli_simulate.py
    │   ├── test_simulation_ui.py         ← pannello + timeline (offscreen)
    │   └── test_voxel_mesh.py            ← marching-cubes → mesh (puro)
    └── conftest.py                       ← + fixture make_plan, tmp catalogs
```

## 6.7 Dettaglio per modulo

### `core/simulation/models.py`
- `SimulationSeverity(StrEnum)`: `info`, `warning`, `error`, `critical`.
- `SimulationCode(StrEnum)`: codici stabili per eventi: `tool_unknown_geometry`
  (warning, fallback cilindro), `resolution_clamped` (warning),
  `collision_fixture` (critical), `collision_stock_material` (critical,
  portacolletto/spindle), `outside_work_area` (warning, tip/percorso),
  `reach_exceeded` (error, `depth > flute_length + margin`),
  `plan_not_executable` (error, rifiuto), `operation_skipped` (info),
  `grid_limit_reached` (warning).
- `SimulationSettings` (frozen pydantic): `voxel_resolution_mm: float | None`,
  `collision_margin_mm: float = 0.5`, `checkpoint_every: int = 64`,
  `timeline_max_ticks: int = 4096`, `check_collisions: bool = True`,
  `max_voxels: int = 32_000_000`, `max_checkpoint_bytes: int = 67_108_864` (64 MB).
- `SimulationEvent` (frozen): `tick`, `motion_index`, `severity`, `code`,
  `message`, `position(x,y,z)`, `operation_id`, `details: dict`.
- `SimulationTick` (frozen): `index`, `motion_index`, `tool_position`,
  `removed_voxels`, `total_removed_mm3`, `collision_count`,
  `event_indices: tuple[int,...]` (riferimenti, non copie).
- `SimulationStats` (frozen): `initial_voxels`, `removed_voxels`,
  `removed_mm3`, `max_depth_reached_mm`, `collision_count`, `duration_estimate_s`,
  `operations_simulated`, `operations_skipped`.
- `SimulationReport` (frozen): `schema_version="1.0"`, `project_id`,
  `plan_fingerprint`, `settings`, `events: tuple[SimulationEvent,...]`,
  `timeline: tuple[SimulationTick,...]`, `stats`, `fingerprint()` (JSON
  canonico), `canonical_excludes() = {}` (nessun computed), round-trip JSON.

### `core/simulation/voxels.py`
- `VoxelGrid`: `origin(x,y,z)`, `voxel_size`, `shape`, `occupied:
  np.ndarray[bool]`, `total_voxels`, `removed_voxels` (int incrementale).
- `VoxelGrid.from_stock(stock, wcs, resolution, max_voxels, on_clamped)`
  (risoluzione derivata + clamp + warning).
- `world_to_voxel(x,y,z)`, `voxel_to_world(i,j,k)` (centro cella).
- `clear_region(mask) -> int`: `occupied &= ~mask` e conteggio rimosso.
- `count_occupied(bbox)` (per statistiche), `surface_mesh(scipy marching
  cubes, level=0.5) -> (vertices, triangles)` (usato dalla UI, testato come
  puro).
- Tutte le operazioni ricevono il **sotto-array** (vista `np.ndarray` su
  slice) → nessuna copia quando possibile; `np.shares_memory` usato nei test.

### `core/simulation/tool_geometry.py`
- `ToolAssembly` (dataclass frozen): calcola lo stack verticale dal `Tool` +
  `MachineProfile`: `CutterBody(z_min,z_max,radius, shape)` con `shape` in
  {cylinder, ball_cap, cone} + `radius_at(z)`; `ShankBody`, `ColletHolderBody`,
  `SpindleBody`. `top_z` e `tip_z` derivati dalla posizione corrente.
- `radius_at(z)` per cono V-bit: `tan(angle/2)·(z - tip_z)`; per t-slot: raggio
  testa sopra il collo.
- Funzioni pure, testate con golden (volume di un cilindro, cono, t-slot).

### `core/simulation/sweep.py`
- `remove_segment(grid, p1, p2, radius, z_min, z_max)`: AABB clip → sotto-array
  → distanza XY al segmento (proiezione clampata, broadcasting) → `clear_region`.
- `remove_arc(grid, start, center, end, ccw, radius, z_start, z_end)`: campiona
  a passo ≤ voxel/2 (stessa strategia del render builder Fase 5) e delega a
  `remove_segment`.
- `remove_motion(grid, motion, assembly, previous_endpoint, voxel_size)`: scelta
  corpo tagliente in base al tool; per V-bit usa `radius_at(z)` per fetta;
  per ball aggiunge la calotta alla fetta inferiore.
- `swept_aabb(...)` riusato dal collision pre-filter.

### `core/simulation/collision.py`
- `collision_bodies(assembly, tip_pose) -> list[Body]` (gambo, portacolletto,
  spindle) con la **responsabilità per corpo** decisa in 6.3 C: gambo → solo
  fixture; portacolletto/spindle → materiale residuo + fixture.
- `check_collisions(grid, fixture_boxes, bodies, margin) -> list[CollisionHit]`
  con `CollisionHit(code, body, position, detail)`.
- Per il materiale (solo portacolletto/spindle): per ogni corpo, AABB Z →
  sotto-array → `np.any(occupied & mask_vicina)`; per le fixture: AABB overlap
  + test distanza cilindro-box (approssimazione: cilindro asse Z vs box =
  distanza orizzontale dal punto più vicino del box ≤ raggio e overlap Z).
- `check_tip_in_work_area(tip, path_aabb, machine)`: il tip (e il percorso)
  dentro il box macchina; `check_reach(depth, flute_length, margin)`: regola
  `depth ≤ flute_length + margin` → altrimenti `error` deterministico.
- Sampling lungo il movimento a passo `≤ voxel` per non perdere collisioni
  tra due tick; margine effettivo `max(collision_margin_mm, voxel_size)`.

### `core/simulation/simulator.py`
- `Simulator(project_service|project, catalog_repository, registry?)` — input:
  `Project` (immutabile) + `ToolpathPlan` + `SimulationSettings`.
- `simulate(project, plan, settings) -> SimulationReport`:
  1. valida `plan.is_executable` (altrimenti evento `plan_not_executable` +
     report con zero simulazione);
  2. inizializza `VoxelGrid` da stock+WCS;
  3. per ogni operazione in ordine: skipped_disabled → evento info; failed →
     evento error e skip; succeeded → risolve tool dal catalogo, costruisce
     `ToolAssembly`, itera i motions mantenendo lo stato (posizione corrente,
     pass, operazione);
  4. per ogni motion: se cut → swept volume; collisioni sui corpi non taglienti
     (responsabilità per corpo: portacolletto/spindle vs residuo+fixture,
     gambo vs fixture, tip/percorso vs work-area, reach) sia a inizio che
     campionate lungo il segmento a passo ≤ voxel; eventi aggiunti; tick
     registrati con stride per rispettare `timeline_max_ticks`;
  5. checkpoint della maschera ogni `checkpoint_every` tick (per scrubbing);
  6. stats finali + report.
- `simulate_operation(project, plan, operation_id, settings)`: solo una
  operazione (riuso interno).
- Nessuna mutazione di progetto/piano; nessun accesso a repository da worker
  (snapshot immutabili in input, come `plan_snapshot`).

### Estensione catalog
- `Tool` + campi opzionali nullable: `v_bit_angle_deg`, `tip_diameter_mm`,
  `t_slot_neck_diameter_mm`, `t_slot_head_diameter_mm` (validati: angolo in
  (0,180), neck>0, head≥neck). JSON esistenti restano validi (default None).
- Seed: `v_bit_30` → `v_bit_angle_deg: 30`; `t_slot_6` → neck/head conservativi
  con `conservative_pending_verification`; test `test_database_models` esteso.

### CLI
- `antcam-rc2 simulate project.antcam.json --geometry drawing.dxf
  [--resolution 1.0] [--out report.simulation.json] [--dump]`.
  Importa progetto+scena, pianifica (riuso `plan`), simula, scrive solo se il
  plan è eseguibile; exit 0 / 1 / 2 / 3 come `plan`. `--dump` mostra eventi e
  statistiche.

## 6.8 Algoritmi e performance

### Rimozione per segmento (cuore, vettorizzato)
```
bbox = AABB(p1, p2) ± radius, ± z interval   → sub array S
dx = px - p1x; dy = py - p1y                  (broadcast su S)
t = clamp((dx·vx + dy·vy) / |v|², 0, 1)
d2 = (dx - t·vx)² + (dy - t·vy)²
mask = S && d2 ≤ r²   →   clear_region(mask)
```
Costo per segmento ≈ area del sotto-array (pochi K voxel), non l'intera
griglia. Per un plan tipico (50 op, ~4k motion) a 1 mm su 200×200×100:
budget **< 5 s** headless, singolo step **< 50 ms**.

### Archi ed eliche
Campionamento a passo `≤ voxel/2`; un giro di thread milling (48 sotto-archi in
Fase 4) → ≤ 48·(arc_length/step) segmenti piccoli; totale dominato dal numero
di segmenti, non dal raggio.

### Collisioni
Pre-filtro AABB per corpo; **solo portacolletto/spindle** verificati sul
materiale residuo per fetta (sotto-array); gambo e spindle/portacolletto
contro fixture via distanza cilindro-box vettorizzata; tip/percorso nel box
work-area; sampling lungo il movimento a passo `≤ voxel` (bound deterministico
sugli eventi); regola di reach `depth ≤ flute_length + margin`.

### Rendering UI
`marching_cubes` ogni 8 step su una copia downsampled (se grid > 8 M voxel,
`scipy.ndimage.zoom` fattore 2) → mesh aggiornata; marker utensile = box
piccolo nel grafo neutro (Fase 5) posizionato al tick corrente; highlight
collisione = flash rosso sul mesh + lista eventi.

## 6.9 Report e determinismo

- `SimulationReport.fingerprint()` su `model_dump(mode="json")` canonico
  (`sort_keys`, separators) — nessun float non quantizzato (tutte le posizioni
  passano per la griglia).
- Test determinismo: ≥ 20 run stesso input → stesso fingerprint; round-trip
  JSON; schema pubblicato.
- Il report NON contiene la maschera (solo statistiche/timeline/eventi); la
  maschera è stato transiente del worker.

## 6.10 Integrazione UI (Fase 5)

- `SimulationController` (QThreadPool + QRunnable + signal carrier, identico a
  `ToolpathController`): `simulate(project_id, plan, settings)`,
  `step_forward/backward`, `jump_to(tick)` (checkpoint replay),
  segnali `tick_updated`, `finished(report)`, `failed(msg)`, `busy(bool)`.
- `SimulationPanel`: play/pause/step/stop/reset, slider timeline (scrubbing via
  checkpoint), risoluzione voxel (spin, apply = re-simula), lista eventi
  colorata (riuso `DiagnosticsView`-style), statistiche.
- Viewport: `set_voxel_mesh(vertices, triangles, color)` → pipeline `solid`
  (nuova `mesh_vertices` in `buffers.py`); `set_tool_marker(position)` →
  box nel grafo neutro; reset su nuova scena.

## 6.11 Test e QA

### Unit
- `test_voxel_grid.py`: dims/origine, risoluzione derivata+clamp, clear_region
  e conteggi, condivisione memoria (no copie), mesh marching-cubes su cubo noto.
- `test_tool_geometry.py`: stack cilindro/cono/t-slot/ball con golden (raggio
  per quota, volumi), fallback cilindro con warning.
- `test_sweep.py`: segmento orizzontale (volume ≈ capsuloide atteso),
  segmento con Z, arco pieno, elica; AABB clip; determinismo.
- `test_collision.py`: plunge profondo → `collision_stock_material`; utensile
  vicino a fixture → `collision_fixture`; fuori work-area; clean profile →
  nessun evento.
- `test_simulation_models.py`: round-trip, fingerprint, validazione settings.
- `test_simulation_report.py`: statistiche attese su plan noto (mini DXF).

### Integration
- `test_simulation_workflow.py`: progetto → plan → simulate → report con eventi
  e stats; plan non eseguibile → `plan_not_executable`.
- `test_simulation_determinism.py`: 20 run stesso fingerprint; round-trip.
- `test_cli_simulate.py`: eseguibile → report scritto; non eseguibile → exit 1.
- `test_simulation_ui.py` (offscreen): pannello play/pause/step, worker, jump
  via checkpoint; `test_voxel_mesh.py`: marching-cubes → mesh con area attesa.

### QA
- `ruff check`/`format` puliti; `ty check` 0 errori su `src` (incl. simulation;
  override `ty.toml` solo per GL renderer).
- Coverage ≥ 70% globale, moduli `core/simulation` ≥ 80%.
- Anti-legacy esteso a `core/simulation` (grep esistente copre già il tree).
- Wheel smoke test (simulation inclusa, CLI `simulate` disponibile).

## 6.12 Ordine di implementazione

1. `simulation/models.py` (+ test) — contratti.
2. `voxels.py` (+ test) — griglia, risoluzione, rimozione, mesh.
3. `tool_geometry.py` (+ test) — assembly e envelope per tipo.
4. `sweep.py` (+ test) — segmenti/archi/eliche vettorizzati.
5. `collision.py` (+ test) — stack, AABB, verifica precisa.
6. `simulator.py` (+ test) — orchestrazione, checkpoint, report.
7. Estensione catalog Tool + seed + test.
8. CLI `simulate` + test integration + determinismo.
9. UI: `SimulationController`, `TimelineWidget`, `SimulationPanel`,
   `mesh_vertices` + rendering voxel.
10. Hardening: budget, coverage, wheel, documentazione API.

## 6.13 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| Memoria voxel esplode a risoluzione fine | Tetto `MAX_VOXELS = 32M` (~32 MB bool), risoluzione derivata, clamp con warning |
| Swept volume lento su plan grandi | Vettorizzazione per sotto-array (AABB clip), archi campionati, budget misurati |
| Collisioni false positive (gambo "dentro" il materiale da rimuovere) | Modello per-corpo: gambo solo vs fixture (occupa legittimamente il foro); portacolletto/spindle vs residuo; verifica post-movimento con sampling esplicito e margine `max(margin, voxel)` |
| Gambo a diametro ≥ tagliente produce falsi positivi sistematici | Non verificare il gambo contro il materiale residuo (6.3 C); regola di reach `depth ≤ flute_length + margin` per il "plunge troppo profondo" |
| Spindle fuori work-area a quote Z alte | Vincolo work-area solo sul tip/percorso, mai sullo spindle (6.3 C) |
| Memoria checkpoint eccessiva a risoluzione fine | Cap `min(16, floor(64 MB / bytes_per_mask))` (6.3 D) |
| Geometria V-bit/T-slot assente nei cataloghi | Campi opzionali nullable + fallback cilindro + evento warning (mai crash) |
| Scrubbing costoso | Checkpoint ogni 64 tick + replay ≤ 64 step (~<1 s worst case) |
| Determinismo rotto da float | Quantizzazione alla griglia prima di ogni operazione; fingerprint canonica testata |
| UI bloccata durante la simulazione | Worker QThreadPool (pattern Fase 5); mesh ogni 8 step, mai nel paint |
| Report non riproducibile senza maschera | Il report contiene solo dati scalari/timeline/eventi; la maschera è transiente |
| `marching_cubes` su griglie enormi | Downsample ×2 sopra 8 M voxel prima del mesh; budget documentato |

## 6.14 Criteri di uscita

- `ruff check`/`ruff format --check` puliti; `ty check` 0 errori su `src`.
- `pytest tests/` verde; coverage ≥ 70% globale, `core/simulation` ≥ 80%.
- Workflow headless: project → plan → simulate → `SimulationReport` con
  eventi/stats/timeline; determinismo (fingerprint stabile su ≥ 20 run).
- CLI `antcam-rc2 simulate` con exit code coerenti; report JSON round-trip.
- Budget: plan tipico (50 op) < 5 s headless a default; step singolo < 50 ms;
  memoria ≤ 32 M voxel (~32 MB).
- UI offscreen: play/pause/step, scrubbing via checkpoint, mesh voxel
  (boundary-quads) testato come puro; smoke GL skippato se nessun contesto.
- Anti-legacy e wheel smoke test superati.

## 6.15 Stato di completamento (verificato)

Tutti i punti del piano sono stati implementati e chiusi:

- **`core/simulation/`** (6 moduli, numpy/scipy esistenti, nessuna dipendenza
  nuova): `models` (Settings/Event/Tick/Stats/Report + fingerprint canonica),
  `voxels` (griglia bool monotona, risoluzione derivata con tetto 32M voxel,
  rimozione su viste, mesh boundary-quads), `tool_geometry` (stack
  tagliente/gambo/portacolletto/spindle con cilindro/cono/ball/T-slot),
  `sweep` (segmenti vettorizzati con AABB clip, archi/eliche campionate,
  path esatto per cilindri lineari), `collision` (modello per-corpo: gambo
  solo vs fixture, portacolletto/spindle vs residuo+fixture, tip nel
  work-area, regola di reach), `simulator` (orchestrazione, checkpoint per
  scrubbing, progress callback per la UI, `replay_mask_at`).
- **Estensione catalog**: `Tool.v_bit_angle_deg`, `t_slot_neck/head_diameter_mm`
  nullable con validazione; seed `v_bit_30`/`t_slot_6` aggiornati.
- **CLI**: `antcam-rc2 simulate` (plan → simulate → report JSON; exit 0/1/2/3;
  dump eventi+stats).
- **UI (Fase 5)**: `SimulationController` (worker QThreadPool + progress live),
  `TimelineWidget` (play/pause/step/slider), `SimulationPanel` (run, stats,
  eventi, scrub), viewport con `set_voxel_mesh`/`set_tool_marker` e pipeline
  `mesh`/`marker` nel renderer, `mesh_vertices`/`unit_box_mesh` in buffers;
  tab Simulation nella MainWindow.

### QA finale

| Strumento | Esito |
| --- | --- |
| `ruff check` | ✅ All checks passed |
| `ruff format --check` | ✅ 178 file |
| `ty check` | ✅ 0 errori |
| `pytest tests/` | ✅ 488 passed, 1 skipped (GL smoke) |
| `coverage` | ✅ 81% globale; `core/simulation` ≥ 80% (sweep 93%, voxels 96%) |
| CLI | ✅ `antcam-rc2 simulate` con exit code e dump |

### Benchmark misurati

| Caso | Misura | Budget piano |
| --- | --- | --- |
| 20 op (profilo+drill) @ 1 mm | **0.30 s** | 50 op < 5 s |
| 20 op @ 0.5 mm | **0.33 s** | — |
| determinismo | fingerprint identico su 20 run | 100% |
| memoria | ≤ 32 M voxel (bool, ~32 MB) | sì |

**Note di implementazione**: `scipy.measure.marching_cubes` non esiste più in
scipy 1.17 (migrato a scikit-image): il mesh di visualizzazione usa
**boundary-quads vettorizzati** (dipendenza-zero, deterministici). Il modello di
collisione segue la revisione 6.3 C (nessun falso positivo del gambo a diametro
uguale). Durante la simulazione la UI riceve progress con mask downsampled ×2
per mesh live; lo scrubbing usa checkpoint + `replay_mask_at` (solo sweep,
nessuna collisione — documentato).
