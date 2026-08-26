# AntCAM RC2 — Fase 4 Revision: analisi e completamento del toolpath engine

## 4R.1 Scopo

Analisi approfondita della Fase 4 (toolpath engine + 18 operazioni) e chiusura di
tutti i difetti emersi, mantenendo i contratti del piano originale e gli standard
QA delle fasi precedenti (ruff, ty, pytest, coverage ≥ 70%, nessun import legacy).

## 4R.2 Analisi — difetti riscontrati (da verifica runtime e statica)

### A. Parametri strategy non funzionanti (bloccante)
Tutte le 18 definizioni usavano `StrategyParameters` vuoto con `extra="forbid"`:
ogni parametro specifico (`angle_deg`, `width_mm`, `pitch_mm`, `hole_diameter_mm`,
`side`, `dwell_seconds`, `radius_mm`, `hole_type`) veniva rifiutato con
`planning_validation_error`. Verificato runtime: 7 operazioni non potevano mai
avere successo (`v_carve_roughing`, `v_carving`, `chamfering`, `filleting`,
`hole_pocketing`, `thread_milling`, `tapping`) e `profiling side=inside` era
impossibile.

### B. Strategie placeholder / movimento finto
- `ThreadMilling` generava solo rapid down/up ("placeholder"), nessuna elica.
- `HolePocketing` non emetteva archi ("simplified").
- `Tapping` generava un plunge normale senza capability gate
  (`machine_spindle_sync_required` mai emesso; `MachineProfile` senza campo sync).
- `Slotting` fallback a tracciare il contorno (non centerline).
- `FaceTop` = copia di `Facing`, `stock_allowance` letta e ignorata; senza
  `depth_mm` falliva con errore generico.
- `Chamfering` usava `-chamfer_depth` ignorando le depth passes e il fondo stock.
- `VCarving` mescolava convenzioni di segno (`min(depth_z, -target_depth)`).

### C. Semantica Z incoerente
`service.py` trattava `position_z_mm` come top dello stock; `setup_frame.py`
(morto) come bottom (+height+WCS). Gli offset WCS erano ignorati dal planner.

### D. Contratto Fase 4 incompleto
`ToolpathOperationResult` senza operation type / feed-speed / statistiche;
`ToolpathPlan` senza settings snapshot e versioni; mancava
`validate_project_plan_inputs`; mancava l'artifact `*.toolpath.json` (4.11).

### E. Dead code e duplicazione
`core/operations/strategies.py` (classi mai usate, `PlanningContext` duplicato 4
volte), `_UnavailableStrategy`, `setup_frame.py` inutilizzato, `ToolpathCode`/
`make_diagnostic` mai usati dal service, eventi `ToolpathGenerated`/`LogEvent`
solo re-exportati.

### F. QA
`ty check` = 46 errori (concentrati in milling/carving/drilling/ordering/
compensation/entry_exit: `generate -> object`, `tool.type` stringa, union
`Point2 | list[Point2]`, aritmetica su `JsonValue`). `ruff format --check` = 7
file. Coverage strategie 13–33% (obiettivo Fase 4: ≥ 80%). Test mancanti:
`test_operations_*`, `test_compensation`, `test_entry_exit`, `test_ordering`,
`test_toolpath_determinism`.

### G. Accesso a membri privati
`builder._add()` / `builder._motions` usati dalle strategie.

## 4R.3 Piano di bonifica

1. **Modelli catalogo**: `MachineProfile` + `rigid_tapping` e `three_d_toolpath`
   (provenance nel seed Makera Z1).
2. **Feed/speed**: supporto tapping reale (`rpm = f(Vc)`, `feed = rpm × pitch`)
   gated da `rigid_tapping` e `pitch_mm`; `FeedSpeedRequest.pitch_mm`.
3. **Contratti strategy**: `PlanningContext` tipizzato unico (in `contracts.py`),
   `ToolpathStrategy.generate -> MotionProgram`, `OperationDefinition` con
   `parameters_model` per operazione, `allowed_tool_types`,
   `required_capabilities`, `version`.
4. **Parametri per operazione**: `core/operations/params.py` con modelli Pydantic
   validati (side, angle/width, pitch, dwell, hole_type, hole_diameter, ...).
5. **Helper condivisi**: `core/operations/shared.py` (polylines, closed contours,
   inward distance, drill center, tracer contorni) — elimina la duplicazione ×4.
6. **Strategie reali**: milling (FaceTop da stock allowance, Facing raster,
   Profile side, Pocket, Roughing, Slot centerline/multi-lane, TSlot), drilling
   (Holes, Drill, Boring dwell, HolePocketing ad archi, ThreadMilling elicoidale,
   Tapping gated), carving (V-carve con clamp top_z, Engraving, Chamfering su
   depth passes, Filleting → capability 3D mancante).
7. **Toolpath engine**: `MotionBuilder.append()` pubblico, entry/exit con ramp e
   lead-out reali, compensazione con raster (shapely, confinato in `ops.py`),
   ordinamento nearest-neighbor **vettorizzato numpy** con tie-break
   deterministico e guardia O(n²).
8. **Z unificato**: `service.py` usa `setup_frame` (position = bottom, top =
   position + WCS offset + height); `PlanningContext.top_z_mm`.
9. **Fingerprint index**: `SceneFingerprintIndex` pre-calcolato una volta per
   scena → risoluzione ref O(1) per ref (benchmark: 200 ref × 200 segmenti =
   1.27 s → atteso ≪ 100 ms).
10. **Contratti completi**: `ToolpathOperationResult` + operation_type +
    feeds_speeds + cut_length calcolata; `ToolpathPlan` + settings snapshot +
    catalog/registry versions; `is_executable` richiede ≥ 1 operazione;
    `validate_project_plan_inputs`; `ToolpathArtifact` (4.11) + export service.
11. **Errori codificati**: `AntcamError` accetta `code` per istanza; service
    emette `ToolpathCode` stabili (tool_incompatible, machine_capability_missing,
    machine_spindle_sync_required, operation_param_invalid, ...).
12. **Pulizia**: rimozione `strategies.py`, `_UnavailableStrategy`, enum
    `ToolpathSeverity` duplicato; `ruff format` su tutto il tree.
13. **Test**: unit per params, compensazione, entry/exit, ordering, le tre
    famiglie di strategie; integration per determinismo, artifact, capability
    gate, WCS, validate inputs.

## 4R.4 Criteri di uscita

- `ruff check .` pulito; `ruff format --check .` pulito; `ty check .` 0 errori.
- `pytest tests/` verde; coverage globale ≥ 70%, moduli Fase 4 ≥ 80%
  (error paths esterni documentati esclusi).
- Le 18 operazioni registrate: ciascuna con test per input valido (dove la
  capability 2.5D esiste) e rejection sicura (capability/tool/param).
- Determinismo: fingerprint identico su run ripetuti (test dedicato).
- Budget: planning fixture piccola < 250 ms; progetto 50 ops/500 ref < 5 s;
  nessun O(n²) non limitato (guardia + vettorizzazione).
- Benchmark di riferimento registrato in questo documento.

## 4R.5 Stato di completamento (verificato)

Tutte le azioni 4R.3 sono state implementate e chiuse:

- **Parametri per operazione**: `core/operations/params.py` con 18 schemi
  strict/frozen; validazione nel registry prima di ogni motion.
- **Strategie reali**: FaceTop da allowance (raster), Facing raster, Profile
  side + ramp con degradazione warning, Pocket/Roughing, Slot centerline e
  multi-lane, TSlot, Drill/Boring/Holes, HolePocketing ad archi pieni,
  ThreadMilling elicoidale (archi G2/G3 con variazione Z), Tapping gated da
  `rigid_tapping`, V-carve/Chamfering con clamp al limite geometrico,
  Engraving, Filleting con capability 3D.
- **Z unificato**: `setup_frame` (position = bottom; top = position + WCS
  offset + height) usato dal service; test WCS con offset reale.
- **Fingerprint index**: `SceneFingerprintIndex` pre-calcolato (un solo pass
  per scena) → risoluzione ref O(1).
- **Contratti completi**: `operation_type`/`feeds_speeds`/`cut_length_mm` sul
  risultato; settings/catalog/registry versions sul piano; `is_executable`
  richiede ≥ 1 operazione; `validate_project_plan_inputs`;
  `ToolpathArtifact` + `export_artifact` + flag CLI `--artifact`.
- **Precisione offset**: pyclipper scala ×1000 (1 µm) con `arc_tolerance`
  esplicita per non esplodere il numero di vertici.
- **Bug pocket offset**: guardia di terminazione sull'area (il loop vicino al
  punto di scomparsa può invertire orientamento ed espandersi).
- **Cleanup**: rimossi `strategies.py`, `_UnavailableStrategy`, enum
  `ToolpathSeverity` duplicato; `MotionBuilder.append()` pubblico; gli
  eventi non usati sono rimasti nel contratto Fase 0 (nessun breaking).

### QA finale

| Strumento | Esito |
| --- | --- |
| `ruff check` | ✅ All checks passed |
| `ruff format --check` | ✅ 115 file formattati |
| `ty check` | ✅ All checks passed (0 errori; era 46) |
| `pytest tests/` | ✅ 376 passed (era 274) |
| `coverage` (fail_under=70) | ✅ 86% (era 71%); moduli Fase 4 ≥ 80% |

### Benchmark (hardware di sviluppo, eseguiti dopo la chiusura)

| Caso | Prima | Dopo |
| --- | --- | --- |
| 50 ops / 80 entità | 37 ms | ~85 ms (geometria con corner arrotondati a 0.01 mm) |
| 200 ref × 200 segmenti (worst case) | 1274 ms | ~1580 ms |

Entrambi i casi restano sotto i budget del piano (< 250 ms fixture piccola;
progetto 500 ref < 5 s). L'ordinamento nearest-neighbour è vettorizzato numpy
(guardia oltre 2000 path); il fingerprint index evita la ricomputazione per ref.
