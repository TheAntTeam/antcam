# AntCAM RC2

Operation-centric CNC CAM engine for 2.5D / 3-axis machines.
Greenfield package: no imports from `antcam` or `antcam_rc1`.

## Install

```powershell
.venv\Scripts\python.exe -m pip install -e src/antcam_rc2
```

For the optional PySide6 GUI:

```powershell
.venv\Scripts\python.exe -m pip install -e "src/antcam_rc2[gui]"
```

## CLI

```powershell
antcam-rc2 --version
antcam-rc2 --help
```

## Layout

```text
src/antcam_rc2/
├── src/antcam_rc2/
│   ├── app/          # DI container, event bus, application bootstrap
│   ├── core/         # UI-agnostic kernel (config, geometry, operations, ...)
│   ├── frontends/    # UI backends (pyside default, web future)
│   └── data/         # JSON catalogs (machines/tools/materials/cooling)
└── tests/            # unit + integration
```

## QA

```powershell
.venv\Scripts\python.exe -m pytest src/antcam_rc2/tests
.venv\Scripts\python.exe -m ruff check src/antcam_rc2
.venv\Scripts\python.exe -m ruff format --check src/antcam_rc2
.venv\Scripts\python.exe -m ty check src/antcam_rc2
```

## Corpus di regressione 2D

Il corpus (``tests/corpus/``) è data-driven: il manifest ``manifest.json``
dichiara invarianti semantiche per ogni fixture (entità, layer, bbox, area,
plan/simulate/post attesi) e i test parametrizzati le verificano. Per
estendere il corpus basta aggiungere il file e la voce di manifest.

```powershell
# tutti i test incl. corpus, fuzz e concorrenza
.venv\Scripts\python.exe -m pytest src/antcam_rc2/tests
# benchmark opt-in (fuori dalla suite standard)
.venv\Scripts\python.exe -m pytest src/antcam_rc2/tests/performance --benchmark
```

## Performance

Baseline misurate (hardware di sviluppo, mediana di 3 run):

| Scenario | Mediana | Note |
| --- | --- | --- |
| plan 50 ops | ~41 ms | — |
| plan 200 ref × 200 seg (cold index) | ~260 ms | fingerprint index ricomposto |
| plan 200 ref × 200 seg (replan) | ~15 ms | cache scene index (**19×**) |
| simulate 20 ops @ 1 mm | ~150 ms | — |
| post 12k motion | ~50 ms | — |
| render 5k entità | ~130 ms | — |

L'harness di regressione con budget (2× baseline) è ``tools/benchmark.py``;
il job CI ``bench`` lo esegue a ogni push. Il fuzzer deterministico è
``tools/fuzz_mutators.py`` (usato da ``tests/integration/test_fuzz_import.py``,
≥ 200 mutanti, nessuna eccezione non gestita).

```powershell
.venv\Scripts\python.exe tools/benchmark.py
.venv\Scripts\python.exe tools/benchmark.py --scenario plan_stress_replan
```

Decisioni di ottimizzazione documentate in [PLAN_FASE_8.md](PLAN_FASE_8.md):
adottate la cache scene index e il fingerprint ricomposto; scartati (misurati
più lenti o senza win) il batching offset pyclipper, il motion merge collinear
della simulazione (1.0–1.2× < 1.5×) e gli ``lru_cache``/STRtree non giustificati
dal profiler.

## Demo

La pipeline completa in un comando, su un disegno campione incluso nella
installazione (``src/antcam_rc2/samples/``):

```powershell
.venv\Scripts\antcam-rc2 demo --sample mech_plate
.venv\Scripts\antcam-rc2 demo --sample pcb_panel --outdir demo_pcb
.venv\Scripts\antcam-rc2 demo --sample imperial_part --post grbl
.venv\Scripts\antcam-rc2 gui-demo --sample mech_plate   # GUI con progetto + geometria + operazioni precaricati
```

La variante ``gui-demo`` apre la finestra desktop con il sample già
importato, le operazioni auto-aggiunte e il planning avviato in background
(il toolpath compare nel viewport appena pronto).

Crea un progetto con operazioni automatiche (profiling/pocketing/drill),
esegue plan + simulate + post e scrive ``plan.json``, ``report.json`` e
``output.nc`` nella directory di output.

## CLI

```powershell
antcam-rc2 import drawing.dxf --dump
antcam-rc2 plan project.antcam.json --geometry drawing.dxf --out plan.json --artifact plan.artifact.json --dump
antcam-rc2 simulate project.antcam.json --geometry drawing.dxf --out report.simulation.json --resolution 1.0 --dump
antcam-rc2 post project.antcam.json --geometry drawing.dxf --out output.nc --post grbl --dump
antcam-rc2 demo
antcam-rc2 gui
```

See [PLAN_FASE_7.md](PLAN_FASE_7.md) for the post-processors (Phase 7),
[PLAN_FASE_6.md](PLAN_FASE_6.md) for the voxel simulation engine, [PLAN_FASE_5.md](PLAN_FASE_5.md) for the PySide6 desktop UI
and [PLAN_FASE_4_REVISION.md](PLAN_FASE_4_REVISION.md) for the toolpath engine.
