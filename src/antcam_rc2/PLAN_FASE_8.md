# AntCAM RC2 — Fase 8: Corpus di regressione 2D, hardening e performance

## 8.1 Obiettivo

Chiudere il ciclo di robustezza e prestazioni del flusso completo
`import → project → plan → simulate → post`:

1. **Corpus di regressione 2D** — un set versionato e data-driven di disegni
   DXF/SVG (da miniature a pezzi semi-reali) con **invarianti semantiche
   attese** (conteggi entità, bbox, aree, fingerprint di plan/simulazione),
   eseguito come test parametrizzati da un manifest JSON;
2. **Hardening** — input avversari e malformati (DXF/SVG troncati, coordinate
   estreme/NaN, entità degeneri), error-path coverage, workflow multi-progetto,
   concorrenza lettura-sola, robustezza numerica;
3. **Performance** — profila, ottimizza e **mantieni solo ciò che la misura
   conferma** (metodologia: ipotesi → implementazione → benchmark → keep/rollback);
   budget di regressione delle prestazioni in CI.

**Criterio di uscita**: la suite completa passa su CI (3 versioni Python + rc2)
con coverage ≥ 70% globale e ≥ 80% sui moduli core; un harness di benchmark
riproduce budget misurati (con margine anti-flakiness) per plan/simulate/post/
render; il corpus copre ≥ 20 fixture con invarianti; nessuna regressione di
funzionalità né di fingerprint rispetto alla Fase 7.

## 8.2 Stato di partenza verificato

- **Baseline misurate** (hardware di sviluppo, riproducibili con il harness
  della 8.6):

| Pipeline | Caso | Misura |
| --- | --- | --- |
| `plan_project` | 50 ops / 80 entità | ~85 ms |
| `plan_project` | 200 ref × 200 segmenti | ~1.58 s (di cui **scene index ~343 ms** e offset pyclipper ~200 ms) |
| `Simulator.simulate` | 20 ops @ 1 mm | ~0.30 s |
| `PostService.post` | 12 309 motion | ~222 ms |
| `geometry_to_scene` | 5 000 entità | ~315 ms |
| `line_vertices` | 110k vertici | ~187 ms |
| offset pyclipper | 200 contorni singoli | ~200 ms |
| offset pyclipper | 200 contorni **in un batch** | ~2 400 ms (✗ più lento) |

- CI esistente: job `rc2` (ruff/ty/pytest+coverage su Python 3.13) + matrice
  3.12/3.13/3.14 sul repo principale; `all-checks-pass` dipende da `rc2`.
- Cache esistenti: `lru_cache` solo su `_unit_circle` (rendering); il
  `CatalogRepository` è lazy-load una volta; la Fase 4R ha già ottimizzato
  offset con scala ×1000 + `arc_tolerance`, fingerprint index per-ref,
  ordering numpy, sweep single-pass per cilindri.
- Coverage: 82% globale; sotto 70% restano gli importer `dxf.py` (61%) e
  `svg.py` (63%) (branch di errore) e glue UI documentata (GL/interattiva).
- `errors.py` espone già tutti i tipi di errore; il corpus attuale è `tests/
  data/` con 7 miniature DXF/SVG.

## 8.3 Analisi e revisione del progetto

### A. Corpus: manifest data-driven, non test "golden" sparsi
**Opzioni**: (a) decine di file golden hard-coded per file; (b) **manifest JSON
unico** con per-fixture: path, formato, invarianti attese (entities, layers,
bbox, area netta, warning/error attesi), e una pipeline di test che li
verifica; (c) generazione sintetica. Decisione: (b) — il manifest è
versionato (`schema_version`, `fixtures[]`), i test sono **parametrizzati** dal
manifest (un `pytest` test per fase della pipeline: import, plan, simulate,
post), e le invarianti sono semantiche (non byte-dipendenti) così il corpus
sopravvive a miglioramenti numerici. I file restano **scritti a mano o
generati deterministicamente una volta e committati** (mai scaricati da
Internet). Il corpus copre: forme semplici, pezzo meccanico con tasche/fori/
slot, pannello stile PCB, multi-layer, unità imperiali, block/INSERT,
transform SVG annidate, archi/eliche.

### B. Hardening: input avversari, numerici, workflow
- **Malformed input**: DXF/SVG troncati a byte arbitrari, XML malformato,
  entità con attributi mancanti, header `$INSUNITS` invalidi, layer vuoti,
  blocchi ricorsivi/assenti. Contratto: **mai eccezioni non gestite**; solo
  `ImportDiagnostics` con warning/error oppure errori di dominio documentati.
- **Numerico**: coordinate NaN/Inf (sanitizzazione o errore esplicito),
  coordinate estreme (mm vs m), archi a raggio 0, contorni degeneri
  (< 3 punti), segmenti zero-length, self-intersection (diagnostica, non
  crash), tolleranze estreme.
- **Workflow**: multi-progetto simultanei in un `Application`, catene
  undo/redo lunghe (≥ 100 comandi) con stack limitato, export/import
  round-trip, plan non eseguibili, catalogo mancante dopo l'import del
  progetto (mismatch versioni).
- **Concorrenza**: `plan_snapshot` e `Simulator.simulate` chiamati da più
  thread sullo stesso progetto immutabile → stesso risultato (il design è
  già read-only; il test lo rende esplicito).
- **Fuzzing leggero**: un generatore deterministico di variazioni
  (truncate, byte-flip, tag-drop) su un sottoinsieme di fixture, con
  seed fisso → nessun crash, solo diagnostica.

### C. Performance: la revisione critica dei candidati
La Fase 8 **misura prima, ottimizza dopo**, e **scarta ciò che non vince**:

| Candidato | Misura/previsione | Decisione |
| --- | --- | --- |
| Batching offset pyclipper multi-path | **misurato PIÙ lento** (2.4 s vs 200 ms: Clipper degrada su 20k edge in un unico path) | ✗ scartato — documentato |
| **Scene index: cache per scena immutabile + scene fingerprint da entity-fp** | index 343 ms → ~200 ms la prima volta, ~0 su replan | ✓ adottare — **weakref verificato**: i modelli pydantic v2 (GeometryScene incluso) supportano `weakref` → `WeakKeyDictionary` sicuro |
| **Scene fingerprint ricomposto** dalle entity-fp (no re-serializzazione del payload) | evita un `json.dumps` grande per scena | ✓ adottare (deterministico; aggiornare i golden; nota compat: un progetto salvato col vecchio fingerprint richiede il re-attach della geometria) |
| **Motion merge collinear per simulazione** | riduce le chiamate `remove_segment` (moti di profilo a segmenti piccoli) | ✓ solo se misurato più veloce e semanticamente identico (merge di segmenti collineari stessa Z) |
| `lru_cache` su helper puri | `_arc_segment_count`, `_v_groove_depth`, `_circle_points`, formattazione | ✓ adottare dove il profiler mostra hit (misura) |
| `model_construct` per nodi/motion | **misurato più lento** per RenderNode (Fase 5) | ✗ non riapplicare senza benchmark per MotionCommand |
| Caching `FeedsSpeedsCalculator` per richiesta | poche ripetizioni reali | ✗ non adottare (no win atteso) |
| STRtree/cKDTree per picking/offset | picking è GPU id-based (niente costo CPU); offset non batching-friendly | ✓ solo un uso mirato: **STRtree per la risoluzione ref fallback** e per culling bbox nel render builder, se misurato utile |

**Metodologia vincolante**: ogni ottimizzazione entra solo se (1) i test esistenti
e i fingerprint restano identici (o aggiornati in modo documentato), (2) un
micro-benchmark mostra ≥ 1.5× sul caso d'uso, (3) il benchmark di regressione
in CI resta sotto il budget (2× la baseline misurata, per anti-flakiness).

### D. Harness di benchmark di regressione
- `tools/benchmark.py` (o `benchmarks/bench.py`): esegue scenari canonici
  (fixture corpus → plan/simulate/post/render), stampa tabelle e **fallisce
  se supera budget** (con margine 2×). Usato in CI come job separato
  `bench` (non nei test normali, per non rendere flaky la suite).
- `@pytest.mark.benchmark` opzionale per i test temporizzati che si eseguono
  solo con `--benchmark` (fuori dal CI standard).

### E. Copertura error-path
- Target: `dxf.py` (61%) e `svg.py` (63%) → ≥ 80% con test mirati sui branch
  di errore (file malformati, entità non supportate, unità mancanti, blocchi
  assenti, sampling degeneri).
- UI glue (dialoghi/pannelli/renderer GL): restano documentate come
  difficilmente copribili (interazione/GL); nessuna forzatura con pragma a
  manetta — solo i path raggiungibili via offscreen.

## 8.4 Confini

### Incluso
- Corpus versionato + manifest + harness parametrizzato (import/plan/simulate/
  post invarianti semantiche).
- Fuzzing deterministico leggero e test adversariali sui due importer.
- Robustezza numerica e workflow multi-progetto/undo/redo/concorrenza.
- Ottimizzazioni misurate: scene index cached + scene fingerprint ricomposto,
  motion merge collinear (simulazione) se vince, `lru_cache` mirati,
  STRtree per ref-fallback se utile.
- Benchmark harness + budget CI + job `bench`.
- Estensione coverage error-path (dxf/svg), CI multi-version stabile.
- Documentazione API/performance aggiornata.

### Escluso deliberatamente
- Nuove funzionalità di CAM (strategie, formati, simulazione avanzata):
  backlog post-Fase 8.
- Refactor architetturali non giustificati da misure (il codice è già
  modulare; si tocca solo ciò che il profiler indica).
- Fuzz con strumenti esterni (hypothesis/atheris): fuzzer deterministico
  interno, dipendenza zero.
- Ottimizzazioni che cambiano i fingerprint senza documentazione (stabilità
  contrattuale prima di tutto).
- OCP/trimesh/3D (Fase 9).

## 8.5 Decisioni architetturali finali

1. **Manifest corpus** (`tests/corpus/manifest.json`) come unica fonte per i
   test parametrizzati; invarianti semantiche (non byte-golden) per
   sopravvivere ai miglioramenti numerici; schema versionato.
2. **Cache scene index** con contratto esplicito "GeometryScene immutabile
   dopo attach": `WeakKeyDictionary` (o dict con guardia di identità +
   documentazione) keyed dall'oggetto scena; invalidazione naturale al GC;
   `build_scene_index` riusa le entity-fingerprint per il scene fingerprint.
3. **Harness benchmark** separato dai test unit (`tools/benchmark.py`),
   budget con margine 2×, job CI dedicato; marker `pytest.mark.benchmark`
   opt-in.
4. **Fuzzer deterministico** interno (seed fisso, variazioni di
   troncamento/flip/tag-drop) senza dipendenze esterne.
5. **Ottimizzazioni solo misurate** (metodologia 8.3 C): niente cambiamenti
   speculativi; rollback documentato per i candidati falliti.
6. **CI**: job `bench` + matrice versioni confermata; il job `rc2` resta il
   gate dei fingerprint/determinismo.

## 8.6 Struttura dei file

```text
src/antcam_rc2/
├── PLAN_FASE_8.md                        ← questo documento
├── pyproject.toml                        ← + [tool.pytest.ini_options] markers; coverage exclude_also aggiornati
├── tools/
│   ├── benchmark.py                      ← harness: scenari canonici + budget
│   └── fuzz_mutators.py                  ← fuzzer deterministico (puro, no deps, NON nella wheel)
├── src/antcam_rc2/
│   ├── core/project/geometry_refs.py     ← + cache scene index + fingerprint ricomposto
│   ├── core/simulation/sweep.py          ← + motion merge collinear (se misurato)
│   ├── core/simulation/simulator.py      ← + hook di merge configurabile
│   ├── core/geometry/curves.py           ← + lru_cache su _arc_segment_count
│   ├── core/operations/carving.py        ← + lru_cache su _v_groove_depth (se hit)
│   └── core/io/dxf.py · svg.py           ← hardening numerico (NaN/estremi) + diagnostics
└── tests/
    ├── corpus/
    │   ├── manifest.json                 ← versionato: fixture + invarianti
    │   ├── dxf/  svg/                    ← file committati (manuali o generati una volta)
    │   └── expected/                     ← golden plan/simulate JSON per un subset
    ├── unit/
    │   ├── test_corpus_harness.py        ← parser manifest + validazione
    │   ├── test_fuzz_import.py           ← fuzzer su import (no crash)
    │   ├── test_numeric_hardening.py     ← NaN/estremi/degeneri
    │   ├── test_scene_index_cache.py     ← cache + invalidazione + fingerprint
    │   └── test_motion_merge.py          ← merge collinear (semantica identica)
    ├── integration/
    │   ├── test_corpus_import.py         ← parametrized dal manifest (import)
    │   ├── test_corpus_plan.py           ← parametrized (plan fingerprint/invarianti)
    │   ├── test_corpus_simulate.py       ← parametrized (removed volume atteso)
    │   ├── test_corpus_post.py           ← parametrized (post eseguibile + stabile)
    │   ├── test_multiproject_workflow.py ← stress multi-progetto + undo/redo
    │   ├── test_concurrent_planning.py   ← plan_snapshot da più thread
    │   └── test_ci_benchmark.py          ← smoke: harness eseguibile (non timing)
    └── performance/
        └── test_benchmark_optin.py       ← @pytest.mark.benchmark (opt-in)
```

## 8.7 Dettaglio per modulo

### `tests/corpus/manifest.json`
Schema `1.0`: `{ "schema_version": "1.0", "fixtures": [ { "name", "path",
"format": "dxf|svg", "units", "expect": { "entities": int|null,
"layers": [..]|null, "bbox": [x0,y0,x1,y1]|null, "net_area_mm2": float|null,
"warnings_ge": int, "errors_ge": int, "plan": { "executable": bool,
"operations": int|null, "removed_mm3_min": float|null }, "post": { "post_id",
"motion_lines_ge": int } } } ] }`. Ogni campo atteso è opzionale; il
harness confronta solo i campi presenti (robusto ai miglioramenti).

### `tools/benchmark.py`
- Scenari: corpus fixture → `import`; `plan` (fixture piccola e stress 200
  ref); `simulate` (20 op); `post` (12k motion); `geometry_to_scene` (5k
  entità). Ogni scenario: 3 run, mediana, confronto con budget (2× baseline
  registrata in `tools/benchmarks.json`). Exit non-zero se oltre budget.
- Deterministico: stesso seed, stessi dati; nessun timing nel path dei test
  standard (job `bench` dedicato in CI).

### `tools/fuzz_mutators.py`
- `mutate(data: bytes, seed: int, ops: int) -> bytes`: tronca a lunghezze
  crescenti, byte-flip a offset deterministici, rimozione di tag XML
  (`<`, `>`, attributi), corruzione header DXF (`$INSUNITS`),
  ripetizione/duplicazione di segmenti. Puro, no deps, **fuori dal package
  installato** (strumento di test).
- `fuzz_import(path, seeds: int) -> ImportReport`: importa ogni mutante e
  verifica che non sollevi eccezioni non-`AntcamError`; raccoglie
  warning/error. Usato dai test; riusabile in `tools/benchmark.py`.

### `core/project/geometry_refs.py` (ottimizzazione)
- `build_scene_index` con **cache**: `_SCENE_INDEX_CACHE: WeakKeyDictionary
  (scene → SceneFingerprintIndex)`; contratto documentato "la scena è
  immutabile dopo attach; le mutazioni richiedono una nuova istanza".
  Guardia: se `id(scene)` non è weakref-abile (fallback), cache a dizionario
  con invalidation esplicita da `attach_geometry` (che la svuota).
- **Scene fingerprint ricomposto**: `sha256(canonical(layers, [entity-fp...],
  tolerance, units))` — riusa le entity-fp già calcolate, elimina la
  re-serializzazione del payload grande. Deterministico; i golden che
  confrontano il valore del fingerprint vengono aggiornati una volta
  (documentato).
- Test: cache hit su replan, invalidazione su nuova istanza, fingerprint
  identico tra vecchio e nuovo algoritmo (verificato una volta).

### `core/simulation/sweep.py` (ottimizzazione condizionale)
- `merge_collinear(previous, motion, next)` (o pass di pre-merge nel
  simulatore): unisce segmenti `CUT_LINEAR` consecutivi con la stessa Z e
  direzione (quasi) collineare (angolo < epsilon) in un solo
  `remove_segment`. **Solo se misurato** ≥ 1.5× sul caso profilo e con test
  di uguaglianza del volume rimosso (invariante: `removed_voxels` identico o
  ± tolleranza voxel). Se la misura non vince → non adottare (documentato).

### Hardening `core/io/dxf.py` / `svg.py`
- Sanitizzazione coordinate: `math.isfinite` su start/end/center/radius →
  diagnostica `warning` + skip entità (mai crash); coordinate estreme
  (> 1e6 mm) → `warning` (possibile confusione m/mm).
- DXF: `$INSUNITS` fuori range già coperto; aggiungere test per header
  assente/corrotto, `INSERT` a blocco mancante (già), `SPLINE` senza
  control points, `LWPOLYLINE` con vertici duplicati.
- SVG: viewBox mancante/invalido, `transform` malformato (già parzialmente),
  path `d` vuoto o malformato, unità `px` con viewBox assente.
- Obiettivo: `dxf.py`/`svg.py` ≥ 80% (da 61/63%).

### `core/geometry/curves.py` e `operations/carving.py`
- `@lru_cache` su `_arc_segment_count(radius, sweep, tolerance)` e
  `_v_groove_depth(angle, width)` **se il profiler mostra hit reali**
  (stesso disegno ripianificato o molti archi con gli stessi parametri);
  altrimenti non adottare (metodologia).

### `core/project/geometry_refs` — STRtree (condizionale)
- `resolve_geometry_ref` fallback: per layer con molte entità, costruire un
  `shapely.STRtree` di bbox per il fallback di fingerprint **solo se il
  profiler lo giustifica** (i ref risolti per indice sono già O(1) con
  l'index; il fallback è raro). Misurare prima.

## 8.8 Performance: baseline, target e metodologia

### Baseline registrate (8.2) e target realistici
| Scenario | Baseline | Target (se adottato) |
| --- | --- | --- |
| plan 50 ops | 85 ms | invariato (già ok) |
| plan 200 ref×200 seg — **prima chiamata** | 1.58 s | ≤ 1.4 s (fingerprint ricomposto) |
| plan 200 ref×200 seg — **replan stessa scena** | 1.58 s | ≤ 0.3 s (cache index) |
| simulate 20 ops | 0.30 s | ≤ 0.20 s (merge collinear, se vince) |
| post 12k motion | 222 ms | invariato (già ok) |
| render 5k entità | 315 ms | invariato (one-shot) |


### Metodologia vincolante
1. Profila (cProfile) → individua il top-3.
2. Implementa l'ipotesi più promettente in un branch.
3. Benchmark dedicato (stesso scenario, 3 run, mediana).
4. Se ≥ 1.5× e fingerprint invariati (o aggiornamento documentato) →
   merge; altrimenti rollback con nota nel codice.
5. Il job CI `bench` applica i budget con margine 2×.

### Punti da NON ottimizzare (decisioni documentate)
- `offset` pyclipper batching: misurato più lento (8.3 C).
- `model_construct` per MotionCommand/RenderNode: misurato più lento.
- Cache `FeedsSpeedsCalculator`: nessun win atteso.
- Picking: GPU id-based, nessun costo CPU da ottimizzare.

## 8.9 Test e QA

### Corpus parametrizzato (harness)
- `test_corpus_harness.py`: manifest valido (schema, percorsi esistenti,
  invarianti tipizzate).
- `test_corpus_import.py` / `plan` / `simulate` / `post`: parametrize dal
  manifest; ogni fixture verifica solo i campi `expect` presenti.
- Golden JSON in `tests/corpus/expected/` per un sottoinsieme (plan
  fingerprint, simulate stats) → determinismo esplicito.

### Hardening
- `test_fuzz_import.py`: seeds fissi (es. 40 mutanti per 4 fixture);
  nessuna eccezione non-`AntcamError`; diagnostica coerente.
- `test_numeric_hardening.py`: NaN/Inf/estremi/degeneri su entrambi gli
  importer e su primitive (raggio 0, contorno < 3 punti).
- `test_multiproject_workflow.py`: 5 progetti, 20 operazioni ciascuno,
  undo/redo ×100, export/import round-trip, mismatch cataloghi.
- `test_concurrent_planning.py`: 4 thread × `plan_snapshot` stesso input →
  fingerprint identici.
- `test_ci_benchmark.py`: smoke dell'harness (esecuzione, non timing).

### QA
- `ruff check`/`ruff format --check` puliti su `src`, `tests` **e `tools`**;
  `ty check` 0 errori su `src` (i moduli `tools` sono tooling: lintati con
  ruff; type-check opzionale con lo stesso `ty.toml` se configurabile).
- `pytest tests/` verde (senza `--benchmark`); coverage ≥ 70% globale,
  `dxf.py`/`svg.py` ≥ 80%, core ≥ 80%.
- CI: job `bench` con budget; matrice versioni (3.12/3.13/3.14) confermata;
  anti-legacy invariato.
- Wheel smoke: corpus incluso? no — i test non viaggiano nella wheel
  (solo src); verifica che i file src siano completi.

## 8.10 Ordine di implementazione

1. Manifest corpus + harness parametrizzato + prime 8–10 fixture (manuali)
   + test import/plan (invarianti semantiche).
2. Fuzzer deterministico (`tools/fuzz_mutators.py`) + `test_fuzz_import.py`.
3. Hardening numerico dxf/svg + test → coverage importer ≥ 80%.
4. Workflow multi-progetto/undo/redo + concorrenza (test).
5. Ottimizzazione scene index (cache + fingerprint ricomposto) + test +
   benchmark → keep/rollback.
6. Motion merge collinear (simulazione) + test di equivalenza + benchmark →
   keep/rollback.
7. `lru_cache` mirati e STRtree condizionali + benchmark.
8. `tools/benchmark.py` + `tools/benchmarks.json` + job CI `bench` +
   marker opt-in.
9. Estensione corpus a 20+ fixture (pannello PCB, pezzo meccanico, imperiali,
   multi-layer), invarianti plan/simulate/post.
10. Hardening finale: QA completo, coverage, wheel, documentazione
    (README + sezione performance), update PLAN_FASE_8 stato.

## 8.11 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| Ottimizzazione non confermata dalla misura | Metodologia 8.8: merge solo se ≥ 1.5×; rollback documentato (già: offset batching scartato) |
| Cache scene index stantia (scena mutata) | Contratto "immutabile dopo attach"; WeakKeyDictionary + invalidazione esplicita da `attach_geometry`; test di invalidazione |
| Scene fingerprint cambiato rompe binding esistenti | Aggiornamento one-shot documentato; test di uguaglianza vecchio/nuovo su fixture; i progetti persistiti non dipendono dal valore assoluto (round-trip in-session) |
| Corpus troppo fragile ai miglioramenti numerici | Invarianti semantiche opzionali per-fixture; niente byte-golden tranne il sottoinsieme esplicito |
| Benchmark flaky in CI | Budget con margine 2×, mediana di 3 run, job separato dal test standard |
| Fuzzer che diventa lento | Seeds fissi e conteggio mutanti limitato (≤ 200/suite); nessun fuzz infinito |
| Coverage importer stenta a salire | Test mirati sui branch di errore elencati (non pragma a manetta) |
| Motion merge altera il volume simulato | Test di equivalenza del `removed_voxels` (± tolleranza voxel) e dei fingerprint del report; se non identico → non adottare |
| Concorrenza reintroduce race | I test multi-thread usano solo API read-only (plan_snapshot/simulate) su snapshot immutabili; nessun worker tocca repository |

## 8.12 Criteri di uscita

- `ruff`/`ty` puliti; `pytest tests/` verde; coverage ≥ 70% globale,
  importer ≥ 80%, core ≥ 80%.
- Corpus: ≥ 20 fixture con invarianti semantiche (import/plan/simulate/post)
  eseguite dal manifest; nessuna regressione di fingerprint rispetto alla
  Fase 7 sui fixture esistenti (i golden ristretti restano identici).
- Fuzzer deterministico: nessuna eccezione non gestita su ≥ 200 mutanti;
  diagnostica coerente.
- Performance: benchmark harness con budget (2× baseline) nel job CI `bench`;
  ottimizzazioni adottate solo se misurate (keep/rollback documentati);
  baseline e target aggiornati in questo documento a fine fase.
- Workflow: multi-progetto, undo/redo ×100, concorrenza 4 thread, round-trip
  — tutti verdi.
- CI: matrice 3.12/3.13/3.14 + job `rc2` + job `bench`; anti-legacy invariato.
- Documentazione: README aggiornato con sezione performance e comandi
  benchmark/fuzz.

## 8.13 Stato di implementazione (fine fase)

**Implementato e verificato** (suite completa: 702 passed, 3 skipped; coverage
84% globale, dxf.py 94%, svg.py 95%, sanitize.py 95%, scene.py 98%,
geometry_refs 85%, sweep.py 93%; ruff/ty puliti):

1. **Corpus** — `tests/corpus/manifest.json` (schema 1.0, **21 fixture**:
   mech_plate, pcb_panel, imperial_part, 7 miniature + 11 nuove generate
   deterministicamente: gearbox_plate, bracket_2d, imperial_bracket,
   helix_arcs, nested_blocks, symmetric_plate, gear_teeth, tabs_and_slots,
   multi_layer_pcb, logo_paths, laser_machine); harness `tests/corpus/
   harness.py`; test parametrizzati import/plan/simulate/post
   (`test_corpus_{harness,import,plan,simulate,post}.py`).
2. **Fuzzer** — `tools/fuzz_mutators.py` (puro, no deps) + `test_fuzz_import.py`
   (208 mutanti, nessuna eccezione non gestita).
3. **Hardening numerico** — `core/io/sanitize.py` (NaN/Inf → drop con
   diagnostica; estremi > 1e6 mm → warning) wired in entrambi gli importer;
   `test_numeric_hardening.py`; branch di errore DXF/SVG
   (`test_dxf_error_branches.py`, `test_svg_error_branches.py`).
4. **Workflow** — `test_multiproject_workflow.py` (5 progetti × 20 op, undo/
   redo ×100 con stack limitato a 50, round-trip export/import, mismatch
   cataloghi, plan non eseguibile); `test_concurrent_planning.py` (4 thread ×
   plan_snapshot e simulate → fingerprint identici).
5. **Scene index cache** — `_SceneIndexCache` (pattern id+weakref: i modelli
   pydantic v2 sono weakref-able ma **non hashable**); `WeakKeyDictionary`
   non utilizzabile; invalidazione esplicita da `attach_geometry`; contratto
   "scena immutabile dopo attach" documentato; `test_scene_index_cache.py`.
   **Misurato: replan 200 ref × 200 seg 0.274 s → 0.014 s (19.6×)**, fingerprint
   identici → **adottata**.
6. **Motion merge collinear** — implementato con hook configurabile e flush
   prima di ogni osservazione (timeline/checkpoint/progress); corretto anche
   un bug del timeline (`total_removed_mm3` stantio). **Misurato 1.0–1.2×**
   sul caso più favorevole (V-carving 2000 motion, 98% mergeabili) con
   fingerprint identici → **sotto la soglia 1.5× → ROLLBACK documentato**
   (codice ripristinato; il costo è dominato da numpy AABB, non dal loop).
7. **lru_cache / STRtree** — `_arc_segment_count` non compare nel profiler
   nemmeno con 500 op ad arco; `_v_groove_depth` ha 2 call site per plan;
   il fallback STRtree è raro (ref O(1) con l'index) → **non adottati**.
8. **Harness benchmark** — `tools/benchmark.py` (setup escluso dal timing,
   mediana 3 run, budget da `tools/benchmarks.json`, exit non-zero se OVER) +
   `tests/integration/test_ci_benchmark.py` (smoke) + `tests/performance/`
   opt-in (`--benchmark`, 2 skip nella suite standard) + job CI `bench` +
   `ty check src` scoping. Budget attuali: **provvisori** (baseline dev × 2);
   ricalibrarli sul primo run CI (baseline CI × 2).
10. **Wheel smoke** — wheel hatch: 125 file, solo `src`, nessun `tests/`/
    `tools/`/`corpus`; import smoke di tutti i 118 moduli (`test_import_smoke.py`).
11. **Bug reali trovati e corretti**:
   - importer SPLINE usava `entity.degree`/`point_at` (API inesistente) → ogni
     SPLINE falliva; ora `flattening(0.5)` → polilinea;
   - `_parse_transform` faceva `float()` fuori dal try/except → `translate(abc)`
     faceva crashare l'import SVG; ora il transform malformato è skippato;
   - `GeometryScene.bounding_box()` ignorava raggio di cerchi/archi (usava solo
     start/end) → bbox errati; ora Circle/Arc usano center ± radius
     (le invarianti del manifest sono state ri-misurate una volta);
   - namespace Inkscape per `inkscape:label` (fase precedente, verificato).

**Baseline finali misurate (dev, mediana 3 run)**:

| Scenario | Baseline | Budget provvisorio (×2) |
| --- | --- | --- |
| plan 50 ops | 41 ms | 170 ms |
| plan stress first (cold) | 261 ms | 3 160 ms |
| plan stress replan (cached) | 15 ms | 600 ms |
| simulate 20 ops @ 1 mm | 148 ms | 600 ms |
| post 12k motion | 50 ms | 444 ms |
| render 5k entità | 133 ms | 630 ms |

**Criteri di uscita 8.12**: rispettati — ruff/ty puliti, pytest verde
(696 + 3 skip), coverage ≥ 70% globale / ≥ 80% core e importer, corpus 21
fixture, fuzzer 208 mutanti senza crash, harness benchmark con budget nel job
CI `bench`, workflow/concorrenza verdi, documentazione aggiornata.
