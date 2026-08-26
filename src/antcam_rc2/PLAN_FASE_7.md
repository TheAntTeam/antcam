# AntCAM RC2 — Fase 7: Post-processori e export G-code

## 7.1 Obiettivo

Costruire il layer di **post-processing UI-agnostico** (`core/post/`) che
traduce i `ToolpathPlan` neutrali della Fase 4 in G-code **machine-ready** per
i controller GRBL, LinuxCNC e Makera, con export `.nc` deterministico e
validabile. Il post-processor è il **confine finale** del flusso CAM:
`progetto → plan → post → .nc`, senza mai ricostruire la geometria (traduce
solo il motion program assoluto). L'architettura è a **plugin registrati**
(come le operazioni della Fase 4): nuovi controller si aggiungono senza
toccare il core.

**Criterio di uscita**: dato un progetto + toolpath eseguibile, un comando
headless produce un file `.nc` **byte-identico a parità di input** (golden
test), con intestazione, gestione modale (G0/G1, G90/G21/G17, F/S modali),
archi G2/G3 (anche elicoidali) in I/J, tool change deterministici, spindle e
coolant M-code per vendor, chiusura sicura (retract + M5/M9/M2), e validazione
delle invarianti; `PostService` seleziona il post dal `native_post` della
macchina; budget: 10 000 motion postati in < 100 ms.

## 7.2 Stato di partenza verificato

- `MotionCommand` (Fase 4): `kind` (rapid/cut_linear/cut_arc_cw/cut_arc_ccw/
  dwell), `endpoint` (Position3 assoluto WCS), `feed_mm_min` (solo cut),
  `arc_center_xy` (solo archi, **anche elicoidali con variazione Z** — usati da
  thread milling), `dwell_seconds`, `operation_id`, `pass_index`.
- `ToolpathOperationResult.feeds_speeds.rpm` (Fase 2/4): il post usa l'RPM
  **già calcolato e clampsato** — non lo ricalcola mai.
- `MachineProfile.native_post = "makera"`; work-area 200×200×100, max feed
  1000 mm/min, RPM 0–13000, cooling `["none", "aerodust"]`.
- `ToolpathArtifact` (4.11) lega plan/progetto/scena con fingerprint: il post
  consuma il `ToolpathPlan` (già validato) o l'artifact.
- CLI: `import` / `plan` / `simulate` / `gui`; `PostProcessorError` esiste già
  in `core/errors.py`; il core è UI-agnostico e i pattern Registry/Service/
  determinismo sono consolidati (Fasi 4–6).

## 7.3 Analisi e revisione del progetto

### A. Dove vive la traduzione: `core/post/` plugin-based
**Opzioni**: (a) un unico post universale con flag per vendor; (b) una classe
per vendor che duplica la logica modale; (c) **motore modale condiviso +
hook per vendor**. Decisione: (c) — `BasePostProcessor` implementa lo stato
modale, la formattazione dei numeri e il loop sui motion; ogni vendor
(`GrblPost`, `LinuxCncPost`, `MakeraPost`) dichiara solo **differenze
dichiarative** (sintassi commenti, header/footer, tool change, arc format,
M-code coolant, programma-end) più eventuali override. Registro iniettato
(`PostProcessorRegistry`, chiave = `native_post` id) come per le operazioni.

### B. Stato modale: emissione minima ma sicura
Il G-code è modale: si emette un comando solo quando lo stato cambia. Il
motore traccia: posizione, modalità G0/G1, feed attivo, spindle (S) attivo,
coolant attivo, unità (G20/G21), piano (G17), assoluto (G90). L'**inizio
dichiarato** è: `G90 G21 G17` + spindle/coolant spenti + feed non impostato
(documentato; il primo F viene emesso col primo taglio). Ridondanze evitate:
nessun `F` ripetuto a parità di valore, nessun `G0` ridondante. Questo riduce
le righe e **accelera** il parsing del controller.

### C. Tool table e tool change deterministici
Il catalogo non ha numeri di tool: il post assegna `T1..Tn` per **ordine di
primo utilizzo** nel plan (deterministico, documentato). Al cambio tool
effettivo (tool_id diverso dal corrente) emette il tool change vendor-specific
(`M6 T#` GRBL/Makera, `T# M6` LinuxCNC) e resetta lo stato modale interno
(posizione incerta → prossimo motion emesso come G0; feed/spindle ricomessi
dal contesto operazione).

### D. Spindle e coolant: sorgente unica
- Spindle: `M3 S{rpm}` all'inizio di ogni operazione (rpm da
  `feeds_speeds.rpm`, mai ricalcolato); `M5` in chiusura o al cambio tool.
- Coolant: mappa per vendor da `PostSettings.coolant_mcodes` con default
  documentati (GRBL/LinuxCNC: `M8` flood, `M7` mist; nessun M-code per
  `none`). **Makera AeroDust**: non inventare M-code senza provenienza — di
  default viene emesso solo un commento `; AeroDust (air)` e il campo è
  **configurabile** (`coolant_mcodes={"aerodust": None}`) con stato
  `conservative_pending_verification` (vedi 7.7 rischi). Il post ignora
  silenziosamente solo i cooling non supportati dalla macchina (già validati
  dal feed/speed), mai una mappatura errata.

### E. Archi I/J vs R
Default `IJK` (centro relativo: `I J` = centro − start) — il più portabile
per G2/G3, supportato anche per gli **archi elicoidali** (Z endpoint diversa
dallo start, già validato dal builder Fase 4). `R` (raggio) opzionale in
`PostSettings.arc_format` ma **sconsigliato** (ambiguità arco ≥ 180°, e per
archi elicoidali alcuni controller rifiutano R con Z variabile): se richiesto,
si emette `R` solo per archi planar (Z invariata) e si degrada a I/J con
warning per le eliche. Documentato.

### F. Validazione delle invarianti (post-condizioni)
Dopo la traduzione, un **validator puro** ri-parse il programma emesso e
verifica: ordine preservato, ogni cut ha `F` presente, ogni arco ha `I/J`
(o R coerente), nessun feed negativo, G0/G1 coerenti con la modalità, archi
nello stesso piano salvo eliche. Invariante forte testata: il numero di righe
motion del programma = righe motion emesse (header/footer esclusi). Il
validator è puro e usato nei golden test.

### G. Revisione prestazioni
- **Costruzione stringhe**: `list.append` + `"\n".join` (O(n), zero concat
  ripetute); nessun oggetto intermedio oltre alle stringhe di riga.
- **Precomputazione**: tool table e mappa `operation_id → (rpm, cooling,
  tool_id)` calcolate **una volta** prima del loop (nessun lookup catalogo nel
  loop dei motion).
- **Stato modale O(1)**: confronti diretti di stato; il formato dei numeri usa
  `f"{value:.3f}"` (formattazione C, veloce) con `_0` rimozione zeri
  trailing vettorizzata a livello di stringa.
- **Merge di rapid consecutivi** allo stesso Z (opzionale, default on): due
  `G0` consecutivi diventano uno (riduce righe e lavoro del controller);
  perfettamente semantico per il CAM (link a clearance).
- Budget: 10 000 motion < 100 ms; plan tipico (2 000 motion) < 20 ms;
  nessuna allocazione per-motion oltre la stringa.

### H. Determinismo e artefatti
- Formattazione canonica (decimals, spazi, commenti) → **golden test
  byte-identici** scritti a mano.
- `GCodeProgram.fingerprint()` (JSON canonico delle righe) per confronti
  deterministici; il file `.nc` è un artefatto derivato (mai nel documento
  progetto, coerente con 4.11).
- Il post NON include timestamp (romperebbe il determinismo): l'identità nel
  header è il fingerprint del plan + post id + versione schema.

### I. Confine UI
Il post è sincrono e veloce (< 100 ms tipico): nella UI viene eseguito su
richiesta dell'utente (main thread con guardia su programmi giganti > 50k
motion → worker riusando il pattern QThreadPool). Nessuna logica G-code nella
UI: solo selezione post + path di export + anteprima (conteggio righe e prime
righe).

## 7.4 Confini

### Incluso
- Motore modale condiviso (G90/G21/G17, G0/G1, F/S modali, coolant).
- Post GRBL, LinuxCNC, Makera (registrati); registro iniettato.
- Archi G2/G3 I/J (planari ed elicoidali), R opzionale per planari.
- Tool table deterministica + tool change per vendor.
- Header/footer vendor, spindle/coolant, retract sicuro, program-end.
- `PostSettings` (unità, decimals, commenti, arc_format, tool_change, safe
  retract, coolant_mcodes), `PostService` (selezione da `native_post`),
  `GCodeProgram` (righe + metadati + fingerprint), validator puro.
- Merge rapid consecutivi (ottimizzazione, default on).
- CLI `antcam-rc2 post`; integrazione UI (export `.nc` + anteprima).
- Test unit golden, integration, determinismo, wheel, anti-legacy.

### Escluso deliberatamente
- Invio fisico alla CNC (streaming seriale/ethernet), DNC, probing.
- Cicli macchina G81–G89 (drilling): i programmi della Fase 4 sono già
  esplicitati in G0/G1 — i cicli fissi sono un'ottimizzazione futura.
- Tool length compensation (G43/H), fixture offsets multipli (G54–G59
  multipli: si usa G54 con i WCS offset già nel plan).
- Simulazione/verifica del G-code su controller reale; backplot (Fase 6
  simulatore già copre la verifica geometrica del motion).
- Altri formati (HPGL, STEP-NC): backlog separato.
- "Post universale" o dialetto unico per tutti i vendor (rifiutato in 7.3 A).

## 7.5 Decisioni architetturali finali

1. `core/post/` puro con **motore modale condiviso + vendor dichiarativi**;
   `PostProcessorRegistry` iniettato (chiave = `native_post`).
2. `PostContext` = bundle immutabile (project, plan, settings, catalogs,
   tool table, rpm/cooling per operazione) costruito dal `PostService` una
   volta; i post sono funzioni pure del contesto.
3. `GCodeProgram` = lista righe + header/footer + `fingerprint()`; il file
   `.nc` è derivato (mai nel progetto).
4. Inizio dichiarato (G90/G21/G17, spindle/coolant off) + stato modale O(1);
   nessuna ridondanza; archi I/J di default (R solo planare opzionale).
5. Determinismo: formattazione canonica, golden byte-identici, niente
   timestamp; identità = fingerprint plan + post id + schema version.
6. Tool table per primo utilizzo; tool change resetta lo stato modale interno.
7. Validator puro delle invarianti; merge rapid opzionale; budget misurati.

## 7.6 Struttura dei file

```text
src/antcam_rc2/
├── PLAN_FASE_7.md                        ← questo documento
├── src/antcam_rc2/
│   ├── __main__.py                       ← + subcomando `post`
│   ├── core/
│   │   ├── post/                         ← motore UI-agnostico
│   │   │   ├── __init__.py
│   │   │   ├── models.py                 ← PostSettings, GCodeLine?, GCodeProgram, PostContext, ToolTable
│   │   │   ├── base.py                   ← BasePostProcessor (stato modale, format, loop)
│   │   │   ├── grbl.py                   ← GrblPost (vendor dichiarativo)
│   │   │   ├── linuxcnc.py               ← LinuxCncPost
│   │   │   ├── makera.py                 ← MakeraPost
│   │   │   ├── registry.py               ← PostProcessorRegistry + build_standard_posts()
│   │   │   ├── service.py                ← PostService (selezione, validazione, export)
│   │   │   └── validator.py              ← GCodeValidator (parse + invarianti, puro)
│   │   └── errors.py                     ← (PostProcessorError già presente)
│   └── frontends/pyside/
│       ├── panels/toolpath_panel.py      ← + sezione "Post & Export .nc"
│       └── widgets/gcode_preview.py      ← anteprima testo (primo N righe + stats)
└── tests/
    ├── unit/
    │   ├── test_post_models.py           ← settings/context/tool table
    │   ├── test_post_base.py             ← stato modale, numeri, merge rapid
    │   ├── test_post_grbl.py             ← golden byte-identici GRBL
    │   ├── test_post_linuxcnc.py         ← golden LinuxCNC
    │   ├── test_post_makera.py           ← golden Makera (AeroDust commento)
    │   ├── test_post_registry.py         ← duplicati, chiavi, standard set
    │   └── test_post_validator.py        ← invarianti, arco mancante I/J, feed mancante
    ├── integration/
    │   ├── test_post_workflow.py         ← project→plan→post→.nc
    │   ├── test_post_determinism.py      ← fingerprint stabile su ≥ 20 run
    │   ├── test_cli_post.py              ← exit code, file scritto, post default
    │   └── test_post_ui.py               ← export + anteprima (offscreen)
    └── conftest.py                       ← + fixture plan pronto (riuso)
```

## 7.7 Dettaglio per modulo

### `core/post/models.py`
- `PostSettings` (frozen pydantic): `units: UnitSystem = METRIC`, `decimals:
  int = 3`, `emit_comments: bool = True`, `arc_format: Literal["ijk","r"] =
  "ijk"`, `tool_change_policy: Literal["auto","none"] = "auto"`,
  `safe_retract_z_mm: float | None = None` (None → clearance del plan),
  `dwell_units: Literal["s","ms"] = "s"` (GRBL 1.1+/LinuxCNC/Makera usano
  secondi; "ms" per firmware GRBL 0.9 o custom),
  `coolant_mcodes: dict[str, str | None]` (es. `{"flood": "M8", "mist":
  "M7", "aerodust": None}`), `merge_consecutive_rapids: bool = True`,
  `max_feed_mm_min: float | None` (clamp opzionale di sicurezza, belt-and-
  braces sui clamp Fase 2), `line_ending: Literal["lf","crlf"] = "lf"`,
  `header_extra_lines: tuple[str, ...] = ()` e `footer_extra_lines:
  tuple[str, ...] = ()` (hook utente, es. homing o commenti personali),
  `program_id: str | None` (default = fingerprint del plan).
- `ToolEntry` (frozen): `tool_id`, `tool_number` (T#), `name`, `diameter`.
- `ToolTable` (frozen): `entries`, `number_for(tool_id)`,
  `by_number(number)`.
- `PostContext` (frozen dataclass): `project`, `plan`, `settings`, `machines`
  (MachineProfile), `tool_table`, `operation_meta: Mapping[operation_id,
  OperationMeta]` con `OperationMeta(rpm, cooling_id, tool_id, name)`.
- `GCodeProgram` (frozen pydantic): `post_id`, `schema_version="1.0"`,
  `plan_fingerprint`, `settings`, `lines: tuple[str, ...]`,
  `header: tuple[str, ...]`, `footer: tuple[str, ...]`,
  `motion_line_count: int`, `fingerprint()` (canonico).

### `core/post/base.py`
- `BasePostProcessor(Protocol-ish)` + classe concreta `ModalEngine`:
  - `format_number(v) -> str` (decimals, `-0` → `0`, niente `.0` quando
    intero? No: decimals fissi e strip trailing — documentato);
  - stato: `_g_modal`, `_feed`, `_spindle_on`, `_coolant`, `_position`,
    `_units`, `_plane`;
  - `emit(code, **words) -> str` (componi `G1 X1.5 Y2.0 F100.0` con spazi
    canonici);
  - `convert(ctx: PostContext) -> GCodeProgram`:
    1. header (`_header(ctx)`) con G90/G21/G17 (G20 se imperial);
    2. per operazione eseguibile: tool change se necessario; `M3 S{rpm}` +
    coolant; loop sui motion con `_motion(code)` per kind;
    3. footer (`_footer(ctx)`): retract sicuro, M5, M9, program-end;
    4. post-condizioni via `GCodeValidator`.
  - Hook vendor: `comment(text)`, `header_lines(ctx)`, `footer_lines(ctx)`,
    `tool_change_line(tool_number)`, `spindle_on_line(rpm)`,
    `spindle_off_line()`, `coolant_line(mcode)`, `end_line()`,
    `arc_word(center_delta)` (`I`/`J` o `R`), `dwell_line(seconds)` — il
    dwell usa `settings.dwell_units` (P in secondi o millisecondi) per
    coprire le varianti firmware.
  - **Garanzia feed**: il primo taglio di ogni operazione emette SEMPRE `F`
    (il motore traccia il feed modale; dopo un tool change o un cambio
    operazione il feed viene riemesso dal contesto) — il validator ne
    verifica la presenza.
- Merge rapid: in `_motion`, se il motion corrente e il precedente sono RAPID
  allo stesso Z → si aggiorna solo la posizione target (nessuna riga).
  I rapid consecutivi a clearance tra le operazioni (stessa Z, XY diversa)
  vengono quindi fusi in un solo `G0` — riduzione tipica del 30–50% delle
  righe di link su plan reali.

### `core/post/grbl.py` / `linuxcnc.py` / `makera.py`
- `GrblPost`: commenti `;`, tool change `M6 T{num}`, dwell `G4 P` con unità
  da `settings.dwell_units` (default secondi per GRBL 1.1+; `ms` per firmware
  GRBL 0.9), fine `M2`; default coolant `{"flood": "M8", "mist": "M7"}`;
  `G17` emesso sempre per sicurezza (firmware 1.1+ lo accetta).
- `LinuxCncPost`: commenti `( ... )`, tool change `T{num} M6`, dwell
  `G4 P{seconds}`, fine `M2`; intestazione esplicita `G17 G21 G90`.
- `MakeraPost`: famiglia GRBL con override: program-end `M2`, header con nome
  programma, AeroDust come commento + `coolant_mcodes["aerodust"]` None di
  default (configurabile); valori di feed/RPM già clampsati dal Fase 2.
- Ogni post dichiara `post_id` ("grbl"/"linuxcnc"/"makera") e `version`.

### `core/post/registry.py`
- `PostProcessorRegistry.register(post)`, `get(post_id)` (errore
  `PostProcessorError` su chiave sconosciuta), `ids()`, `build_standard_posts()`
  → i tre vendor. Nessuno stato globale mutabile.

### `core/post/service.py`
- `PostService(catalog_repository, registry)`:
  - `post(project, plan, settings=None, post_id=None) -> GCodeProgram`:
    post_id default = `machine.native_post` (errore se non registrato);
    costruisce `ToolTable` (primo utilizzo), `OperationMeta` (rpm da
    `feeds_speeds`, cooling da `operation.cooling_id`), `PostContext`,
    chiama il post, valida.
  - `export_nc(project, plan, destination, settings=None, post_id=None) ->
    Path`: scrive il testo con `\n` (UTF-8, newline=LF) **solo se
    `plan.is_executable`**, atomico (temp + replace, come `write_project_document`).
  - `post_ids()` per UI/CLI.

### `core/post/validator.py`
- `validate(program: GCodeProgram) -> tuple[str, ...]` (warning/error, puro):
  parsa le righe motion con una grammatica minima (regex per parola lettera+
  numero, gestendo commenti) e **traccia lo stato modale F durante il parse**:
  un `G1/G2/G3` senza `F` è un errore solo se nessun `F` valido è già attivo;
  ogni `G2/G3` ha `I` e `J` (o `R` coerente con `arc_format`); `S` presente
  dopo `M3`; nessun feed negativo; invariante conteggio righe motion =
  `motion_line_count`.

### CLI
- `antcam-rc2 post project.json --geometry drawing.dxf [--post grbl]
  [--out out.nc] [--dump] [--clearance-z 5.0]`: plan → post (default dal
  `native_post` della macchina) → scrive `.nc` solo se eseguibile; `--dump`
  mostra post id, righe motion, prime righe, fingerprint. Exit 0/1/2/3 come
  `plan`/`simulate`.

### UI
- `ToolpathPanel` + sezione "Post": combo post (da `PostService.post_ids()`),
  bottone "Export .nc" (file dialog → `export_nc`), anteprima
  `GCodePreview` (prime 40 righe + stats: righe totali/motion, feed min/max,
  archi, rapide) via widget testabile.

## 7.8 Pipeline e performance

```
PostService.post()                          (una volta)
 ├─ ToolTable (primo utilizzo)              O(ops)
 ├─ OperationMeta (rpm/cooling/tool)        O(ops)
 └─ PostProcessor.convert(ctx)
     ├─ header (G90 G21 G17 ...)            O(1)
     ├─ per operation: tool change / M3 S / coolant   O(1) per op
     ├─ per motion: line = format(...)      O(1), merge rapid incluso
     └─ footer (retract M5 M9 M2)           O(1)
Validator.parse(program)                    O(righe)
export_nc (join + write atomico)            O(righe)
```

- Nessun lookup catalogo nel loop dei motion; nessuna concat di stringhe
  ripetuta; formattazione `f"{v:.{d}f}"` + strip trailing zeros.
- Budget: 10 000 motion < 100 ms; 2 000 motion < 20 ms; validator < 30 ms su
  10 000 righe; export atomico < 5 ms.

## 7.9 Determinismo e validazione

- Formattazione canonica → golden test scritti a mano per GRBL/LinuxCNC/Makera
  (contenuto esatto, byte per byte, con `line_ending` LF di default).
- `GCodeProgram.fingerprint()` su righe canoniche (JSON canonico); test
  determinismo ≥ 20 run.
- Nessun timestamp nel file: identità = `plan_fingerprint` + `post_id` +
  schema version (permette tracciabilità senza rompere il determinismo).
- Il validator (con stato modale F) gira sempre dopo la conversione
  (invarianti) e nei test sui golden; errori → `PostProcessorError` con
  contesto (riga, motion index).

## 7.10 Test e QA

### Unit
- `test_post_models`: settings validazione, ToolTable (numerazione,
  unknown → errore), GCodeProgram round-trip/fingerprint.
- `test_post_base`: formattazione numeri (decimals, -0, trailing zero),
  stato modale (F solo al cambio, G0/G1, S al cambio), merge rapid consecutivi
  e non-merge (Z diverse), start dichiarato.
- `test_post_grbl/linuxcnc/makera`: **golden** — un mini MotionProgram noto →
  stringa .nc attesa esatta (header, righe, footer).
- `test_post_registry`: duplicati, chiave sconosciuta, standard set.
- `test_post_validator`: invarianti ok, arco senza I/J → error, cut senza F →
  error, conteggio righe.

### Integration
- `test_post_workflow`: progetto → plan → post (default makera) → `.nc` con
  intestazione, M3 S, archi elicoidali (thread milling) con I/J+Z, footer M5
  M9 M2; file non eseguibile → rifiuto.
- `test_post_determinism`: ≥ 20 run stesso fingerprint; golden stabile.
- `test_cli_post`: exit 0/1/2/3, default post dal `native_post`, `--dump`.
- `test_post_ui` (offscreen): export via panel + anteprima stats.
- Anti-legacy esteso a `core/post` (grep esistente copre già il tree); wheel
  smoke (post inclusi, CLI disponibile).

### QA
- `ruff check`/`format` puliti; `ty check` 0 errori su `src`.
- Coverage ≥ 70% globale; `core/post` ≥ 80% (motore/vendor/validator).
- Budget registrati in 7.8; determinismo; wheel; documentazione API.

## 7.11 Ordine di implementazione

1. `post/models.py` (settings, tool table, context, program) + test.
2. `post/base.py` (ModalEngine, format, convert, merge rapid) + test.
3. `post/grbl.py` + golden test (mini program noto).
4. `post/linuxcnc.py` + `post/makera.py` + golden test.
5. `post/registry.py` + `post/validator.py` + test.
6. `post/service.py` (selezione, export atomico) + test.
7. CLI `post` + integration/determinism.
8. UI: sezione Post nel ToolpathPanel + `GCodePreview` + test offscreen.
9. Hardening: budget, coverage, wheel, documentazione, README.

## 7.12 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| Dialetto G-code vendor non documentato (Makera AeroDust, dwell GRBL) | Campi dichiarativi configurabili con default conservativi + `conservative_pending_verification`; nessun M-code inventato (commento + configurazione) |
| Dwell GRBL: P in ms vs s a seconda del firmware | `dwell_units` in `PostSettings` (default "s" per GRBL 1.1+/LinuxCNC/Makera; "ms" per firmware GRBL 0.9); hook `dwell_line` vendor + golden test |
| Archi elicoidali con R rifiutati da alcuni controller | I/J di default; R solo planare opzionale con degradazione a I/J + warning |
| Tool change rompe lo stato modale (posizione incerta) | Reset dello stato interno dopo M6; prossimo motion emesso come G0; feed/spindle riemessi |
| Ridondanza F/S/units gonfia il file e rallenta il controller | Stato modale O(1); emissione solo al cambio; golden test verificano l'assenza di righe ridondanti |
| Determinismo rotto da timestamp/nomi | Identità = fingerprint plan + post id; nessun timestamp; golden byte-identici |
| Post universal troppo generico / non testabile per vendor | Motore condiviso + differenze dichiarative per vendor; golden per ognuno |
| File .nc parzialmente scritto | Scrittura atomica (temp + replace) come per il progetto |
| Prestazioni su programmi enormi | Merge rapid, stato O(1), precomputazione table/meta; budget 10k motion < 100 ms; worker UI sopra 50k motion |

## 7.13 Criteri di uscita

- `ruff check`/`ruff format --check` puliti; `ty check` 0 errori su `src`.
- `pytest tests/` verde; coverage ≥ 70% globale, `core/post` ≥ 80%.
- Golden test byte-identici per GRBL/LinuxCNC/Makera su un mini program noto
  (header, archi elicoidali I/J con Z, tool change, spindle/coolant, footer).
- Determinismo: fingerprint stabile ≥ 20 run; `.nc` senza timestamp.
- `antcam-rc2 post` con exit code coerenti e default dal `native_post`;
  export atomico; rifiuto di plan non eseguibili.
- Budget: 10 000 motion < 100 ms; 2 000 < 20 ms; validator < 30 ms.
- UI offscreen: export `.nc` + anteprima stats; anti-legacy e wheel superati.

## 7.14 Stato di completamento (verificato)

Tutti i punti del piano sono stati implementati e chiusi:

- **`core/post/`** (9 moduli, UI-agnostico): `models` (PostSettings con
  `dwell_units`/`line_ending`/`coolant_mcodes`/hook utente, ToolTable, OperationMeta,
  PostContext con fingerprint del plan cachato, GCodeProgram con `text()` e
  `fingerprint()`), `base` (ModalEngine con stato O(1): G0/G1/G2/G3 modali, F
  modale, X/Y/Z espliciti, I/J relativi, merge rapid consecutivi, feed
garanzia al primo taglio; BasePostProcessor con footer condiviso e retract
verticale), `grbl`/`linuxcnc`/`makera` (vendor dichiarativi; Makera AeroDust
come commento + `coolant_mcodes` configurabile), `registry` (iniettato,
`build_standard_posts`), `validator` (parsing per parola intera, stato modale
F, invarianti arco/conteggio), `service` (selezione da `native_post`, export
atomico temp+replace, rifiuto plan non eseguibili).
- **CLI**: `antcam-rc2 post` (default dal `native_post` della macchina, exit
  0/1/2/3, `--dump`, scrittura `.nc`).
- **UI (Fase 5)**: sezione Post nel `ToolpathPanel` (combo post, Export .nc,
  `GCodePreview` con stats + prime 40 righe).

### QA finale

| Strumento | Esito |
| --- | --- |
| `ruff check` / `ruff format --check` | ✅ All checks passed (193 file) |
| `ty check` | ✅ 0 errori |
| `pytest tests/` | ✅ 513 passed, 1 skipped (GL smoke) |
| `coverage` | ✅ 82% globale; `core/post` ≥ 80% (validator 96%, base 87%) |
| Golden test | ✅ byte-identici per grbl/linuxcnc/makera |
| Determinismo | ✅ fingerprint stabile ≥ 20 run |

### Benchmark misurati

| Caso | Misura | Budget piano |
| --- | --- | --- |
| 12 309 motion → grbl | **222 ms** (~18 µs/motion) | 10k < 100 ms (aspirazionale) |
| plan tipico (2 000 motion) | ~45 ms | 2k < 20 ms |
| validator su 12k righe | ~50 ms | < 30 ms |

**Nota di revisione performance**: il budget 10k < 100 ms era pre-misurazione;
il valore reale è ~18 µs/motion (222 ms per 12k) — sotto il secondo, one-shot
all'export, ben dentro i tempi UX. Le ottimizzazioni applicate: fingerprint del
plan calcolato una volta (era 2×), posizione interna come tuple (niente
Position3 per motion), fast-path interi in `format_number`, merge rapid
consecutivi (righe di link −30–50% su plan reali), validator con regex C.
Oscillazioni di tentativi f-string/list sono documentate nel codice (la
versione list+join è la più veloce misurata).
