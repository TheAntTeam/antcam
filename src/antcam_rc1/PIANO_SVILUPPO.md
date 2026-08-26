# AntCAM — Piano di Sviluppo (da zero)

## Visione

Sistema CAM open source deterministico, modulare, basato su Python + OCP (OpenCascade).
Target: fresatura 2.5D/3D a 3 assi, espresso in Python puro con kernel geometrico B-Rep.

Principi architetturali:
- **Separazione netta** tra CAM Engine e GUI/G-code
- **Topological Data Scripting** — mappatura diretta B-Rep IDs → logica percorso
- **Neutral internal dialect** RS274/NGC → post-processori swappabili
- **Determinismo e spiegabilità** — niente scatole nere
- **Test come regressione semantica**, non solo smoke test

---

## Stack Tecnologico

| Componente | Tecnologia |
|---|---|
| Linguaggio | Python ≥3.13 |
| Kernel geometrico | OCP (cadquery-ocp) — OpenCASCADE B-Rep |
| Mesh | trimesh |
| CLI | typer |
| Viewport 3D | PySide6 + OCC viewer |
| Calcoli | numpy |
| Lint/test/type | ruff, pytest, mypy |

---

## Roadmap — Fasi

---

### Fase 0 — Scheletro del Progetto (Settimana 1)

**Obiettivo**: struttura del package, CLI base, tooling.

- [ ] Scaffolding package (`antcam/` con `pyproject.toml`)
- [ ] CLI entry point (typer — comandi `viewer`, `plan`)
- [ ] Configurazione QA: ruff, pytest, mypy, coverage
- [ ] Logger strutturato
- [ ] README tecnico + `CONTRIBUTING.md`
- [ ] CI su GitHub Actions (lint + test + typecheck)

**Deliverable**: `antcam --help` funzionante.

---

### Fase 1 — Kernel di Importazione (Settimane 2-3)

**Obiettivo**: caricamento STEP/STL robusto e contract esplicito.

Base: `importer.py` dal package esistente.

- [ ] `Model` class: wrapper attorno a STEP (`OCP.STEPControl`) e STL (`trimesh`)
- [ ] Rotazioni Eulero (RX, RY, RZ) all'import
- [ ] `Part_CheckGeometry` su ogni solido importato
- [ ] Schema unità "Metric Small Parts & CNC" (mm interni, mm/s velocità)
- [ ] Separazione netta tra percorso production (STEP/STP) e sperimentale (mesh→BRep)
- [ ] Errori di import espliciti e diagnostici
- [ ] Test: import da file reali, rotazioni, geometrie malformate

**Deliverable**: `antcam plan file.step --rx 90` produce JSON con analisi geometria.

---

### Fase 2 — Feature Recognition Engine (Settimane 4-8)

**Obiettivo**: pipeline di rilevamento features deterministico, modulare, testabile.

Base: `feature_extractor.py` (1500+ righe) — va **refactorizzata** in detector stages separati.

Architettura a pipeline:

```
Model (B-Rep)
  │
  ├─ FaceClassifier        ─── classifica ogni faccia (piano, cilindro, cono, toro, sfera)
  ├─ HoleDetector           ─── fori cilindrici, through/blind, coppie coniche (svasature)
  ├─ ArcGroupDetector       ─── archi su facce verticali → gruppi foro
  ├─ SlotDetector           ─── coppie di semicilindri / archi allineati
  ├─ PocketDetector         ─── facce piane orizzontali con pareti chiuse
  ├─ StepDetector           ─── facce piane orizzontali con bordi aperti
  ├─ OpeningDetector        ─── through-pocket (nessuna faccia di fondo)
  ├─ FilletDetector         ─── cilindri concavi (raccordo)
  └─ ChamferDetector        ─── piani inclinati tra parete verticale e fondo
```

- [ ] `RecognitionTolerances` centralizzata (non sparsa)
- [ ] Ogni detector: input B-Rep → output lista features + diagnostica
- [ ] Viewer diagnostico: mostra output di ogni detector come layer separato
- [ ] `HoleGroup`: aggregazione per diametro+profondità, flag `tap_candidate`
- [ ] Test: regression corpus con features attese per ogni pezzo campione

**Deliverable**: riconoscimento features su 10+ pezzi campione con metriche di copertura note.

---

### Fase 3 — Estrazione Contorni 2.5D (Settimane 9-10)

**Obiettivo**: contorni di lavorazione proiettati, perimetri, aree di lavorazione.

Base: `contour_extractor.py`.

- [ ] Shadow projection: proiezione ortogonale facce accessibili su piano Z-min
- [ ] Fusione booleana delle proiezioni (unione regioni)
- [ ] Sottrazione fori passanti dalla forma ombra
- [ ] Perimetro esterno e contorni interni (isole)
- [ ] Offset 2D dei contorni (tolleranza utensile, stock allowance)
- [ ] Classificazione wire: CCW = esterno, CW = interno
- [ ] Test: verifica su forme semplici e composte

**Deliverable**: contorno di lavorazione con isole visibile in viewer.

---

### Fase 4 — Tool & Material Libraries (Settimane 11-12)

**Obiettivo**: sistema completo di gestione utensili e materiali.

Base: `machining_library.py`, dati in `data/`.

Architettura a 4 livelli (ToolBit):

```
ToolShape    ─── definizione parametrica forma (cilindrica, toroidale, sferica, conica)
ToolBit      ─── istanza fisica con dimensioni reali
ToolLibrary  ─── database persistente (JSON) di utensili disponibili
ToolController ─── associazione ToolBit + Feed/Speed per un Job specifico
```

- [ ] Forme supportate: flat end mill, ball end mill, bull nose, drill, chamfer, v-bit
- [ ] Librerie: `standard_mm`, `small_parts_mm` (da espandere)
- [ ] `MaterialProfile`: surface speed, chip load, stepdown ratio, plunge ratio
- [ ] Calcolo automatico parametri: RPM = (1000 × Vc) / (π × D), feed = RPM × fz × Z
- [ ] Override per strategia (roughing vs finishing) e per feature
- [ ] Test: loading, validazione, calcolo parametri

**Deliverable**: tool library completa, material profiles, parametri di taglio automatici.

---

### Fase 5 — Toolpath Engine — 2.5D (Settimane 13-18)

**Obiettivo**: generazione percorsi per operazioni 2.5D standard.

Base: `path_generator.py` (2867 righe) — da ricostruire con architettura pulita.

#### 5.1 Struttura Dati Interna

```
ToolpathPlan
 ├── working_plane_normal, safe_projection, stock
 └── Operation[]
      ├── op_id, strategy, feature_type, tool_id
      ├── metadata (passes, stepdown, stock_allowance)
      └── MotionCommand[]
           ├── move (RAPID, LINEAR, CW_ARC, CCW_ARC, DWELL)
           ├── x, y, z, i, j, k
           └── feed
```

#### 5.2 Operazioni

- [ ] **Drilling** — G81/G82/G83 con peck cycle opzionale
  - Fori semplici, through-hole allowance, peck incrementale
- [ ] **Slot milling** — centerline, multi-lane, trochoidale
  - Larghezza utensile ≥ larghezza slot → centerline
  - Larghezza utensile < larghezza slot → lane multiple
  - Slot stretti/profondi → trochoidale
- [ ] **Cavity clearing (pocket)** — contour-parallel + raster fallback
  - Offset 2D boundary via OCP `BRepOffsetAPI_MakeOffset`
  - Stepdown Z-level progressivo
  - Rampa di entrata (helical ramp)
- [ ] **Profilatura (contour)** — rough + finish
  - Compensazione sinistra/destra per loop esterno/interno
  - Stock allowance su operazione di sgrossatura
  - Passate multiple in Z
- [ ] **Z-level roughing** — waterline
  - Sezione B-Rep a Z costante, offset 2D del contorno
  - Riempimento area anulare (contorno espanso − parte come isola)
  - Contour-parallel o raster fill
  - Rampa elicoidale tra livelli Z

#### 5.3 Path Dressups (Decorator Pattern)

- [ ] Lead-in / Lead-out (tangente, arco, retta)
- [ ] Rampa elicoidale (helical entry)
- [ ] Dogbone (scarichi angoli interni)
- [ ] Tabs (ponticelli di tenuta)

**Deliverable**: piano completo di toolpath 2.5D esportabile in JSON.

---

### Fase 6 — Post-Processor System (Settimane 19-21)

**Obiettivo**: trasformazione del piano neutro in G-code macchina-specifico.

Architettura:

```
ToolpathPlan (JSON neutro)
  │
  ├─ PostProcessor (classe base astratta)
  │    ├─ LinuxCNCPostProcessor
  │    ├─ HaasPostProcessor
  │    ├─ FanucPostProcessor
  │    ├─ GrblPostProcessor
  │    └─ (altri)
  │
  └─ output.nc
```

- [ ] Classe base `PostProcessor` con metodi:
  - `program_start()`, `program_end()`
  - `rapid_move()`, `linear_move()`, `arc_move()`
  - `canned_cycle_start()`, `canned_cycle_end()`
  - `tool_change()`, `spindle_control()`, `coolant_control()`
- [ ] Conversione unità: mm/s interni → mm/min output
- [ ] Mappatura cicli: drilling → G81/G83, tapping → G84/G33.1
- [ ] Compensazione utensile: G41/G42
- [ ] Selezione coordinate: G54-G59.3
- [ ] Test: confronto output con G-code noto per ogni controller

**Deliverable**: `antcam plan file.step --post linuxcnc` produce file `.nc`.

---

### Fase 7 — Interactive 3D Viewer (Settimane 22-25)

**Obiettivo**: viewer professionale per ispezione features e toolpath.

Base: `viewer.py` (1158 righe) — da ricostruire.

- [ ] `QOCPWidget` — viewport OCC embedded in PySide6
  - Mouse: left=ruota, middle=pan, wheel=zoom
  - 30ms redraw (~33 FPS)
- [ ] Layer multipli: modello solido (opaco), edges, shadow, perimetri, features, toolpaths
- [ ] Selezione interattiva facce (click + highlight)
- [ ] Face cycling (tasto N), filtri accessibilità (tasto I)
- [ ] Toolpath overlay: colorato per modalità (drilling=blu, roughing=rosso, finishing=verde)
- [ ] Toggle layer: T per ciclare toolpath visibility, Shift+T per backward
- [ ] Pannello features laterale: lista features con highlight al click
- [ ] Pannello operazioni: dettagli per ogni operazione del piano
- [ ] Pannello diagnostica: output detector per feature selezionata
- [ ] `PieceToolpathPreviewWindow`: offset 3D, contorni per-Z, percorsi rough+profile
- [ ] `RoughingBenchmarkWindow`: metriche (cut length, estimated time)

**Deliverable**: `antcam viewer file.step` apre finestra 3D completamente interattiva.

---

### Fase 8 — Simulation & Collision Detection (Settimane 26-30)

- [ ] Voxel-based material removal preview (veloce, indicativa)
- [ ] Mesh boolean differential removal (precisa, più lenta)
- [ ] Modello macchina: limiti assi, travel
- [ ] Utensile come swept volume
- [ ] Collision detection pipeline:
  1. Pre-filtro AABB
  2. B-Rep intersection check (OCP)
  3. Report collisioni con dettaglio posizione + entità coinvolte
- [ ] Timeline simulazione: play/pause/step
- [ ] Eventi classificati: warning, error, critical
- [ ] Report esportabile (JSON)

**Deliverable**: simulazione rimozione materiale + detection collisioni.

---

### Fase 9 — Corpus di Regressione & Hardening (Settimane 31-33)

- [ ] 15-20 pezzi campione (STEP/STL) con feature attese documentate
- [ ] Categorie: fori passanti/ciechi/gruppo, tasche, step, slot, smussi, raccordi, svasature
- [ ] Test di import: ogni pezzo si carica senza errori
- [ ] Test di recognition: feature attese vs trovate (precision/recall per pezzo)
- [ ] Test di toolpath: ogni feature genera operazioni valide
- [ ] Test di post-processing: ogni operazione si traduce in G-code valido
- [ ] Casi noti di esclusione e fallimento documentati

**Deliverable**: suite regressione con copertura nota, CI passa su tutti i pezzi.

---

### Fase 10 — Adaptive Clearing & 3D Avanzate (Settimane 34-38)

- [ ] **Adaptive Clearing**: carico laterale costante
  - Calcolo impegno utensile (engagement angle)
  - Traiettoria a impegno costante (trochoidale espansa)
  - Naive CAM Detector: collasso segmenti collineari (G64 Q-)
- [ ] **3D Surface finishing**: passate a Z costante su superficie 3D
  - Sezione superficie a Z costante (waterline)
  - Offset 3D per finitura parallela
- [ ] **V-carve** (utensile a V): percorsi su semantica wire
- [ ] **Thread milling** (G33/G33.1): maschiatura rigida

**Deliverable**: strategie 3D avanzate per superfici organiche e complesse.

---

### Fase 11 — Wizard Conversazionali (Settimane 39-41)

Ispirato a Haas VPS: programmazione conversazionale tramite form guidati.

- [ ] Job Setup Wizard: stock, origine, coordinate system (G54-G59)
- [ ] Feature Selection Wizard: click su faccia → proposta operazione
- [ ] Operation Parameter Wizard: tool, feeds/speeds, passes, lead-in/out
- [ ] Post-Processor Wizard: selezione macchina, unità, formato output
- [ ] Simulazione guidata: setup collision models, run, report

**Deliverable**: flusso completo da STEP a G-code in <10 click.

---

### Fase 12 — Polishing & Documentazione (Settimane 42-44)

- [ ] Documentazione Sphinx/ReadTheDocs
- [ ] API reference completa
- [ ] Tutorial: "da STEP a G-code in 5 minuti"
- [ ] Video demo operazioni principali
- [ ] Packaging: wheel + PyPI release
- [ ] Pagina GitHub con README tecnico
- [ ] Contributing guide e code of conduct

**Deliverable**: sito documentazione, package PyPI v1.0.0.

---

## Riferimenti e Ispirazione

| Progetto | Cosa imparare |
|---|---|
| FreeCAD CAM Workbench | Architettura Job/ToolController/PostProcessor, ToolBit |
| Fabex (BlenderCAM) | Strategie di fresatura, ponti/tabs, adaptive milling |
| LinuxCNC | Standard G-code RS274/NGC, cinematica, compensazione |
| Haas VPS | Programmazione conversazionale, diagnostica |
| Fusion 360 | Manufacturing Models, librerie cloud, parametri CAM |
| Thunder Path | Algoritmi a impegno costante in Rust |
| CAMotics | Simulazione voxel-based, timeline eventi |

## Criteri di Uscita per Ogni Fase

Ogni fase si considera completa quando:
1. Tutti i test passano (unit + integration + regression)
2. L'interfaccia pubblica è documentata
3. I casi limite noti sono documentati
4. Il formato output è stabile (nessuna breaking change senza major version)

---

*Piano generato il 2026-07-19. Basato su analisi del codice esistente, documentazione tecnica, e best practice CAM industriali.*
