# AntCAM RC2 — Piano di Sviluppo

## 1. Visione e Principi

Approccio **operation-centric** (non feature-extraction-centric come rc1): l'utente importa un disegno 2D, definisce un Progetto (macchina, stock, fixtures), aggiunge Operazioni e per ciascuna seleziona le geometrie → il motore genera il toolpath, calcola feed/speed automatici e può simulare la lavorazione.

Principi:

- **Core ↔ UI totalmente separati** (il core non importa PySide6): stesso kernel per UI desktop e web
- **Operazioni come plugin registrati** → modularità reale (Fase 1 = 18 ops, Fase 2 = ops avanzate)
- **Determinismo e spiegabilità**: ogni parametro derivato è calcolato da una regola documentata
- **2D-first (DXF/SVG)**, con percorso di estensione 3D già previsto nell'architettura
- **Tutto serializzabile in JSON** (progetto, operazioni, toolpath, report)
- **Pydantic v2** per modelli validati, `jsonschema` per i cataloghi dati

## 2. Stack Tecnologico

| Area | Tecnologia |
|---|---|
| Runtime | Python 3.13 (venv esistente) |
| Package/QA | `uv`, ruff, ty (typecheck), pytest, coverage |
| Modelli/validazione | pydantic v2, pydantic-settings |
| Geometria 2D | shapely, pyclipper (offset), scipy |
| Import DXF | ezdxf |
| Import SVG | svgpathtools |
| Spaziale/perf | numpy, scipy `cKDTree`, shapely `STRtree`, `joblib`/lazy cache |
| GUI (default) | PySide6 6.11 |
| Rendering | Custom OpenGL via `PySide6.QtOpenGL` (QOpenGLWidget + GLSL) — nessuna dipendenza extra |
| 3D (Fase 9) | OCP + trimesh (già installati) |
| Logging | stdlib `logging` strutturato |

## 3. Architettura a Livelli

```
┌─────────────────────────────────────────────────────────┐
│ Frontends (UI)  — Nessun import del core in altre direzioni  │
│   ├─ frontends/pyside   (desktop, default)                  │
│   └─ frontends/web      (futuro, API + three.js)            │
├─────────────────────────────────────────────────────────┤
│ App Kernel (antcam_rc2.app)                              │
│   DI container, event bus, application lifecycle          │
├─────────────────────────────────────────────────────────┤
│ Application Services (antcam_rc2.core.services)          │
│   ProjectService, CommandStack (undo/redo), Events       │
├─────────────────────────────────────────────────────────┤
│ Core Engine (antcam_rc2.core)                            │
│   geometry · io · project · databases · operations ·     │
│   toolpath · simulation · post · feeds_speeds            │
└─────────────────────────────────────────────────────────┘
```

Il **kernel è agnostico alla UI**: espone servizi e oggetti; gli eventi viaggiano su un event bus interno. La UI desktop consuma gli stessi servizi che userà la web.

## 4. Struttura dei Moduli

```
src/antcam_rc2/
├── pyproject.toml · README.md · PLAN.md · PLAN_FASE_0.md
├── src/antcam_rc2/
│   ├── __init__.py
│   ├── app/                     # kernel applicativo (DI, event bus)
│   ├── core/
│   │   ├── geometry/            # primitives, topology, offset, boolean ops
│   │   ├── io/                  # registry formati + DXF/SVG importer
│   │   ├── project/             # Project, Stock, Operation, serialization
│   │   ├── databases/           # machine/tool/material/cooling cataloghi
│   │   ├── feeds_speeds/        # motore calcolo RPM/feed
│   │   ├── operations/          # registry plugin + 18 operazioni
│   │   ├── toolpath/            # MotionCommand, plan, passi, entry, ottimizzatore
│   │   ├── simulation/          # voxel, swept volume, collisioni
│   │   ├── post/                # base + grbl/linuxcnc/makera
│   │   └── services/            # ProjectService, CommandStack, Events
│   └── data/                    # cataloghi JSON seed (machines/tools/materials/cooling)
├── frontends/
│   ├── pyside/                  # app desktop
│   │   ├── viewport/            # renderer GL custom, shader, camera, picking, luci
│   │   ├── panels/ · dialogs/ · widgets/
│   │   └── theme.py             # palette moderna
│   └── web/                     # (fase 10)
└── tests/                       # unit, integration, data (corpus 2D)
```

## 5. Formati 2D supportati

| Formato | Estensione | Fase |
|---|---|---|
| **DXF** (Drawing Exchange) | .dxf | **1** |
| **SVG** (Scalable Vector) | .svg | **1** |
| HPGL/PLT (plotter) | .plt, .hpg | 2 |
| G-code re-import (come path) | .nc, .tap, .gcode | 2 |
| Gerber (PCB) | .gbr | 2 |
| CSV/JSON point-list custom | .csv, .json | 2 |
| AI / EPS (via estrazione path) | .ai, .eps | 3 |
| PDF (vettoriale) | .pdf | 3 |
| DWG (via libredwg) | .dwg | 3 |

Ogni importer produce un **modello geometrico neutro** (`GeometryScene`: entità su layer, con unità e tolleranze normalizzate), validato per: contorni chiusi, unità, sovrapposizioni, direzione dei loop.

## 6. Modello Dati

- **Project**: name, units, WCS (G54…), machine_profile_id, stock, fixtures[], operations[]
- **Stock**: box (X/Y/Z + position), material_id, offset da origine, metodo di centraggio (4 opzioni)
- **Fixture**: box con posizione, parte fissa/morsa, mesh opzionale
- **Operation**: op_type, name, enabled, geometry_refs[], tool_id, params (tolleranza, stepdown, stepover, finishing passes, depth, stock allowance), optimization (direzione, ordine, entry), feed/speed (auto o manuale)
- **Tool**: id, type (end_mill/ball/bull/v-bit/drill/tap/bore/thread_mill/t-slot), diameter, shank, flute_count, flute_length, overall_length, tool_material, coating, max_depth
- **Material**: id, family, hardness, surface_speed_m_min, chip_load_mm_tooth, plunge_ratio, machinability_factor
- **MachineProfile**: id, work_area, spindle (power, min/max RPM), max_feed, collet sizes, **spindle+collet dimensions (per collisioni)**, cooling options, native post
- **CoolingProfile**: none / air / mist / flood + fattori su Vc e chip load

**Makera Z1 seeded** (dati raccolti): area 200×200×100 mm, spindle 150 W @ 0–13.000 RPM, collet 3,175 mm default (opz. 4/6/6,35), AeroDust (aria integrata, nessun input aria esterno), max feed cautelativo.

## 7. Motore Feed/Speed automatico

- `RPM = (1000 × Vc) / (π × D)` → clamp a [min, max] macchina
- `Feed = RPM × fz × Z` (chip load × flutes)
- `PlungeFeed = Feed × plunge_ratio`
- Fattori moltiplicativi: **materiale** (machinability), **operazione** (drilling vs milling vs v-carve), **raffreddamento** (none=0.85, air=1.0, mist=1.05, flood=1.15), **impegno radiale/assiale** (stepover/stepdown ratio)
- Output: RPM, cut feed, plunge feed, stepdown, stepover, passi — tutto clampsato sui limiti macchina
- L'utente può **sovrascrivere ogni valore** (il motore marca i valori come `auto` o `manual`)

## 8. Catalogo Operazioni — Fase 1 (18)

Face Top · Roughing · Facing · Pocketing · Profiling · V-Carve Roughing · V-Carving · Engraving · Slotting · Chamfering · Filleting · Hole Pocketing · Thread Milling · T-Slotting · Holes · Drill · Tapping · Boring

Organizzate in famiglie condivise (stessa infrastruttura passi/entry): **Drilling** (drill, tapping, boring, holes, hole pocketing, thread milling), **2.5D Milling** (facing, roughing, pocketing, profiling, slotting, t-slotting), **Carve** (v-carve rough, v-carve, engraving, chamfering, filleting).

**Fase 2 (modulare)**: adaptive clearing, z-level roughing, rest machining, 3D finishing/waterline, multi-side, probe ops — via `OperationRegistry` plugin, senza toccare il core.

## 9. Simulatore & Collisioni

- **Stock voxel-grid** (risoluzione configurabile) per rimozione materiale veloce
- **Swept volume del tool** (cilindro/cono/sfera/v-bit per tipo utensile) sottratto al voxel
- **Timeline** play/pause/step con colori per stato
- **Collision detection**: pre-filtro AABB → test preciso; include **spindle e colletto** (dimensioni nel machine profile) vs stock, fixtures e pezzo
- Report eventi (warning/error/critical) esportabile in JSON

## 10. Visualizzazione (Custom OpenGL)

- Renderer su `QOpenGLWidget` con GLSL 4.5 core (via `QtOpenGL`, nessuna dipendenza extra)
- **Scene graph** neutro emesso dal core → GPU (stesso grafo servirà per la web)
- **Luci**: ambient + directional con ombre soft + point light; **IBL/tonemapping** (ACES) per look moderno tipo Fusion
- **Palette**: tema scuro moderno (superficie, stock traslucido, toolpath colorato per strategia, selezione evidenziata)
- **Picking** GPU (id buffer) per selezione di linee/shape dal disegno e regioni
- Multithreading UI: generazione toolpath/simulazione in `QThread`/`QRunnable`, UI mai bloccata

## 11. Fasi di Implementazione (dettaglio)

| Fase | Contenuto | Durata |
|---|---|---|
| **0** | Scaffolding, pyproject, CI (ruff/ty/pytest/coverage), logging, DI, event bus, CommandStack, contratto core/UI | 2 sett |
| **1** | Geometria 2D (primitives, offset, boolean) + import **DXF e SVG** con validazione | 3 sett |
| **2** | Database catalogs (machine/tool/material/cooling) + motore feed/speed + profilo Makera Z1 | 3 sett |
| **3** | Modello Progetto, Stock, fixtures, Operation, ProjectService (add/remove/move up-down/enable/clone/copy-paste/favorites/import/export) + serializzazione JSON | 3 sett |
| **4** | Toolpath engine (comandi, passi, entry lead-in/out/ramp, ottimizzatore) + **18 operazioni** in 3 sottogruppi | 5 sett |
| **5** | UI PySide6: viewport OpenGL (luci/ombre/picking), pannello operazioni con tutte le azioni, pannello parametri, tema | 5 sett |
| **6** | Simulatore voxel + collisioni (spindle/collet) + timeline + report | 4 sett |
| **7** | Post-processori GRBL / LinuxCNC / Makera + export .nc | 3 sett |
| **8** | Corpus regressione 2D, hardening, ottimizzazioni performance (vectorizzazione, spatial index, cache) | 3 sett |
| **9** | Estensione 3D: import STEP/STL, selezione facce/fori/perimetri, stesse operazioni (stretch) | 3 sett |
| **10** | Web UI (futuro): API layer + frontend | separata |

**Criteri di uscita per ogni fase**: test passano, interfaccia pubblica documentata, output JSON stabile, casi limite documentati.

## 12. Tecniche professionali

- Pydantic v2 (validazione, `model_validate` per round-trip JSON), `jsonschema` per cataloghi dati
- Pattern: Registry (operazioni/formati), Command+Undo stack, Observer/EventBus, Repository (cataloghi), DI container
- Performance: numpy vectorizzato, `cKDTree`/`STRtree` per picking e offset, lazy-load cataloghi, caching (`lru_cache`) su calcoli ripetuti, generazione toolpath in thread dedicato
- Test: unit + integration + corpus regressione con fixture DXF/SVG reali; `coverage` ≥ 70%