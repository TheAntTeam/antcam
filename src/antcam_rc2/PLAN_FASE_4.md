# AntCAM RC2 - Fase 4: Toolpath engine e operazioni registrate

## 4.1 Obiettivo

Trasformare il setup persistente della Fase 3 in un piano di lavorazione 2.5D
**deterministico, spiegabile, serializzabile e indipendente da UI e postprocessor**.
Il motore risolve esclusivamente le `GeometryRef` sicure, applica dati macchina,
utensile, materiale e cooling della Fase 2, quindi genera un `ToolpathPlan`
neutro. La UI della Fase 5, il simulatore della Fase 6 e i postprocessor della
Fase 7 devono consumare lo stesso output senza ricostruirne la logica.

La Fase 4 consegna:

- modello motion 3D neutro, validato e JSON-stabile;
- `OperationRegistry` iniettato e plugin strategy per le 18 operazioni;
- planner condivisi per profondita, compensazione, entry/exit e ordine locale;
- `ToolpathService` headless con risultati, diagnostica e criteri di eseguibilita;
- integrazione sicura con `Project`, `GeometryRef`, cataloghi e feed/speed;
- CLI `plan` per il primo test manuale tecnico, senza GUI o G-code;
- corpus DXF/SVG, test di determinismo e budget di performance.

**Criterio di uscita**: dato un progetto JSON, un DXF/SVG compatibile e i
cataloghi package-data, il comando headless genera un JSON `ToolpathPlan`
ripetibile, con tutte le geometrie referenziate validate e senza errori per le
operazioni abilitate. Il piano dichiara in modo machine-readable ogni warning,
parametro derivato e causa di non eseguibilita.

## 4.2 Stato di partenza verificato

- Fase 1 fornisce `GeometryScene`, primitive native, offset `pyclipper`, boolean
  `shapely`, DXF/SVG normalizzati e `GeometryRef` fail-closed della Fase 3.
- Fase 2 fornisce cataloghi read-only e `FeedsSpeedsCalculator` puro con clamp
  e provenienza `automatic`/`manual`.
- Fase 3 conserva `OperationType` per tutte le 18 operazioni, parametri comuni,
  override feed/speed, stock/WCS/fixture e ordine esplicito delle operazioni.
- Non esistono ancora `core/toolpath/`, `core/operations/`, registry, motion
  commands, generazione passate o comando CLI per pianificare.

## 4.3 Confini di responsabilita

### Incluso

- Toolpath 2.5D, coordinate WCS assolute, safe/clearance motion, cut/plunge,
  archi quando preservabili, e metadata setup non ambigui.
- Risoluzione delle 18 definizioni registrate e validazione dei parametri per
  strategia tramite modelli Pydantic propri del plugin.
- Compensazione utensile 2D, profondita multipass, entry/exit/ramp consentiti,
  ordinamento delle isole all'interno di una singola operazione e diagnostica.
- Planning progetto in sequenza dichiarata, planning operazione singola,
  JSON canonico e CLI di ispezione/export.
- Operazioni non supportate dalle capacita macchina/utensile/geometria vengono
  rifiutate con errori espliciti, mai convertite in movimenti approssimativi.

### Escluso deliberatamente

- UI, picking, viewport e thread Qt: Fase 5.
- Verifica collisione, rimozione voxel, swept volume e simulazione: Fase 6.
- G-code, cicli macchina, postprocessor e invio alla CNC: Fase 7.
- STEP/STL, orientamento utensile, facce/spigoli 3D e strategie 3D: Fase 9.
- Adaptive clearing, rest machining, z-level, waterline, multi-side e probe:
  backlog plugin successivo, non parte delle 18 operazioni di questa fase.
- Tool selection automatica o modifica cataloghi: la strategia richiede un tool
  gia selezionato dal progetto e usa il repository solo per validarlo.
- Riordinamento globale delle operazioni, che altererebbe intenzione e sicurezza
  del setup definita dall'utente in Fase 3.

## 4.4 Decisioni architetturali critiche

### Ordine e atomicita

L'ordine `Project.operations` e autorevole e non viene modificato dal planner.
`optimize_path_order` agisce solo sulle isole/contorni della singola operazione.
Un toolpath plan non deve riposizionare automaticamente drilling, pocketing e
profiling come farebbe un planner legacy: l'utente ha gia definito la sequenza
con `move_operation`, e una modifica nascosta puo rendere invalido il workholding.

Il risultato di progetto contiene uno stato per operazione:

- `succeeded`: program valido e diagnostica senza errori;
- `skipped_disabled`: operazione disabilitata, nessun motion;
- `failed`: input/geometria/capacita non valida, nessun motion eseguibile.

`ToolpathPlan.is_executable` e vero solo quando tutte le operazioni abilitate
sono `succeeded`. CLI e futuri postprocessor devono rifiutare un plan non
eseguibile per evitare che un JSON parziale venga confuso con un programma sicuro.

### Geometria e sicurezza

`ToolpathService` riceve una `GeometryScene` transiente e prima confronta
`GeometryBinding.scene_fingerprint`; poi risolve ogni `GeometryRef` con exact
fingerprint. Una geometria mancante, ambigua o modificata produce `ToolpathError`
contestuale con `operation_id` e ref coinvolto. Non e consentito fare fallback a
bbox, layer intero o indice non verificato.

L'output Fase 4 non certifica collisioni. Genera coordinate e envelope utensile
sufficienti alla Fase 6, ma non dichiara che fixture/spindle/collet siano sicuri.

### Coordinate e profondita

Tutti i `MotionCommand` usano mm e coordinate assolute nel WCS del progetto.
Il setup frame deriva dalla `Stock` e dalla sua `StockOrigin`; Fase 4 aggiunge
metodi puri per calcolare top/bottom stock in WCS. La semantica e:

- `OperationParameters.depth_mm` e positiva, misurata dal top stock verso -Z;
- il planner deriva `target_z_mm` assoluto dal top stock;
- `clearance_z_mm` e un input obbligatorio di `PlanningSettings`, positivo
  sopra il top stock; non viene indovinato da una strategia;
- ogni movimento cut e a `target_z_mm` o a una delle quote intermedie;
- link e rapid rimangono alla clearance configurata, mai impliciti a Z=0.

Una richiesta che attraversa il fondo stock, con clearance non positiva o con
profondita assente quando la strategia la richiede fallisce prima del planning.

### Parametri operation-specific

`Operation.parameters.strategy_parameters` resta JSON-only nel documento di
progetto, ma non e piu considerato validato in modo generico. Ogni strategy
espone un `parameters_model: type[BaseModel]`, genera JSON Schema e valida il
dict al planning. I parametri comuni rimangono in `OperationParameters`; esempi
specifici includono `side` per profiling, `pitch_mm` per thread milling,
`angle_deg` per V-carve e `hole_diameter_mm` per boring.

La registry e l'unica fonte di verita per schema, family, compatibilita tool e
requisiti geometria. Non introdurre 18 sottoclassi persistenti di `Operation`:
renderebbe incompatibile il JSON Fase 3 e accoppierebbe il Project domain al
motore toolpath.

### Determinismo

Non usare set/dict non ordinati per sequenze motion, iterazioni di geometria o
tie-break dell'ottimizzatore. I sort usano chiavi complete e stabili; coordinate,
feed e archi sono quantizzati per la serializzazione con precisione documentata.
`ToolpathPlan.fingerprint()` usa JSON canonico (`sort_keys`, float normalizzate)
ed e oggetto di test: stesso input deve dare stesso fingerprint su esecuzioni
ripetute.

## 4.5 Struttura dei moduli

```text
src/antcam_rc2/
├── PLAN_FASE_4.md
├── src/antcam_rc2/src/antcam_rc2/
│   ├── __main__.py
│   ├── app/application.py
│   └── core/
│       ├── operations/
│       │   ├── __init__.py
│       │   ├── contracts.py          # Strategy, definition, context, result
│       │   ├── registry.py           # injected registry, no global mutable state
│       │   ├── milling.py            # facing, roughing, pocket, profile, slot, T-slot, face top
│       │   ├── drilling.py           # holes, drill, bore, hole pocket, thread mill, tap
│       │   └── carving.py            # V rough, V carve, engrave, chamfer, fillet
│       ├── toolpath/
│       │   ├── __init__.py
│       │   ├── models.py             # Position3, MotionCommand, MotionProgram, ToolpathPlan
│       │   ├── diagnostics.py        # severity, code, operation/ref context
│       │   ├── settings.py           # immutable PlanningSettings
│       │   ├── setup_frame.py        # stock/WCS to absolute Z coordinates
│       │   ├── depth_passes.py       # deterministic depth decomposition
│       │   ├── compensation.py       # side/loop-role tool-center offsets
│       │   ├── entry_exit.py         # safe rapid, plunge, lead/ramp helpers
│       │   ├── ordering.py           # local island ordering only
│       │   ├── builder.py            # validates legal motion sequences
│       │   └── service.py            # ToolpathService project/operation API
│       └── databases/
│           └── models.py             # narrowly extended capability/tool geometry fields if verified
└── tests/
    ├── data/toolpath/                # hand-authored DXF/SVG and expected semantic results
    ├── unit/test_motion_models.py
    ├── unit/test_operation_registry.py
    ├── unit/test_depth_passes.py
    ├── unit/test_compensation.py
    ├── unit/test_entry_exit.py
    ├── unit/test_ordering.py
    ├── unit/test_operations_milling.py
    ├── unit/test_operations_drilling.py
    ├── unit/test_operations_carving.py
    ├── integration/test_toolpath_service.py
    ├── integration/test_toolpath_determinism.py
    └── integration/test_cli_plan.py
```

`core.toolpath` non importa `core.operations` per evitare cicli: strategies
dipendono da modelli/planner toolpath, mentre `ToolpathService` riceve la
registry tramite DI. `Application` costruisce la registry standard e il service;
test possono iniettare registry minime.

## 4.6 Motion model e contratti JSON

### Posizioni e comandi

Definire Pydantic frozen/strict:

- `Position3(x_mm, y_mm, z_mm)`;
- `MotionKind`: `rapid`, `cut_linear`, `cut_arc_cw`, `cut_arc_ccw`, `dwell`;
- `MotionCommand`: endpoint assoluto, feed opzionale solo per cut, arc center
  obbligatorio solo per arc, tag `operation_id`, `pass_index`, `is_cut`;
- `MotionProgram`: sequenza non vuota, validazione di transizioni, bounds e
  continuita opzionale; non contiene G-code o oggetti Shapely/OCC;
- `ToolpathOperationResult`: operation ID/type, status, program opzionale,
  feed/speed result, pass count, diagnostica e statistiche;
- `ToolpathPlan`: versione schema, project ID, settings snapshot, risultati
  ordinati, fingerprint, `is_executable` e report aggregato.

Ogni comando e espresso come posizione finale. Il postprocessor Fase 7 tradurra
da uno stato iniziale dichiarato, senza ricostruire la geometria. `rapid` non ha
feed; `cut_linear` ha feed positivo; un arco richiede endpoint e centro in XY,
stesso Z per start/end, e direzione esplicita. I comandi non devono emettere
spindle/coolant o cicli G-code: sono responsabilita del postprocessor.

Il serializzatore JSON quantizza coordinate/feed a una precisione stabile e
conserva il valore non arrotondato solo internamente durante la generazione.
Pubblicare `model_json_schema()` e testare round-trip/schema per plan e motion.

### Diagnostica

`ToolpathDiagnostic` ha `severity` (`info`, `warning`, `error`), `code` stabile,
messaggio, operation ID opzionale, geometry ref opzionale e dettagli JSON.
Warning ammessi: feed clamped, catalogo provvisorio, entry degradato, ordine
locale non ottimizzabile. Errori bloccanti: ref stale, tool incompatibile,
parametri invalidi, stock/depth invalida, geometria non supportata o capability
macchina assente.

## 4.7 Registry e Strategy contract

`OperationRegistry` e istanza esplicita, immutabile dopo bootstrap. `register`
rifiuta tipi duplicati; `definition(operation_type)` fallisce con `OperationError`
contestuale; `definitions()` torna ordine canonico per UI/CLI futura.

Ogni `OperationDefinition` dichiara:

- `OperationType`, nome, famiglia feed/speed e supporto 2D;
- tool types ammessi, numero/tipo di geometry ref, parametri Pydantic;
- capability macchina richiesta (per esempio spindle sync);
- strategy factory stateless e versione schema parametri.

`PlanningContext` contiene solo dipendenze esplicite: project, operation,
resolved native entities, machine/tool/material/cooling, setup frame,
`PlanningSettings`, `FeedSpeedResult` e servizi planner puri. La strategy non
puo consultare repository, UI, filesystem o clock.

La service segue questa pipeline per ogni operazione abilitata:

1. confronta binding scena e risolve `GeometryRef` fail-closed;
2. trova definition e valida `strategy_parameters`;
3. verifica tool, collet/cooling, capability macchina e limiti stock;
4. calcola `FeedSpeedRequest` con family, override e engagement noto;
5. calcola quote Z, passate e geometria XY compensata;
6. produce motion tramite `MotionBuilder`, entry/exit e ordine locale;
7. valida il program e pubblica risultato/diagnostica.

Disabilitate non entrano nella registry e non generano feed/toolpath. Un errore
in una operazione non nasconde errori indipendenti delle successive, ma rende
`ToolpathPlan.is_executable=False`.

## 4.8 Planner condivisi

### Setup e profondita

`DepthPassPlanner` riceve top Z, target Z e max stepdown, quindi restituisce una
sequenza monotona di quote che termina esattamente al target. Il planner rifiuta
stepdown zero, target sopra top, target sotto stock bottom e pass non finiti.
Stepdown viene da override validato o `FeedSpeedResult.stepdown_mm`; non viene
ricavato di nuovo da una strategy.

### Compensazione e offset

`CompensationPlanner` converte loop geometrici in percorso centro utensile:

- profiling esterno: offset verso esterno; loop interni: verso interno;
- pocket/hole pocket: offset progressivi dentro la regione;
- facing/roughing: raster con clipping alla regione;
- slot: centerline, multi-lane o trochoidal solo quando la geometria richiede
  tale stile e i limiti di engagement sono soddisfatti.

La conversione Shapely resta confinata nei planner geometrici; l'API strategy
espone solo primitive native. Offset falliti, auto-intersezioni o regioni troppo
piccole per il tool producono errori/diagnostica, non path bbox di fallback.

### Entry, exit e link

`EntryExitPlanner` costruisce sempre sequenze esplicite:

`rapid clearance -> rapid entry XY -> plunge/ramp -> cut -> lead-out -> rapid clearance`.

Lead-in/out e rampa sono opt-in per strategy e richiedono spazio verificabile
nella regione. Se non esiste spazio, la strategy puo degradare solo a plunge
verticale quando l'operation lo consente, emettendo warning; non deve creare una
rampa fuori stock. Helical entry e adaptive ramp sono backlog successivo.

### Ordinamento locale

`LocalPathOptimizer` puo usare nearest-neighbor deterministico tra isole dello
stesso livello e stessa operazione; pareggi risolti con fingerprint geometrico e
indice originale. Non inverte direction, non riordina passate Z e non mescola
operazioni utente. Un limite configurabile evita O(n^2) non controllato; oltre
la soglia usa ordine di input e warning.

## 4.9 Cataloghi, capacita e 18 operazioni

Fase 4 puo estendere i modelli/cataloghi soltanto con dati necessari e
verificabili: angolo/tip diameter per V-bit, geometria T-slot, pitch per tap,
limiti di spindle sync e capability macchina. Ogni campo nuovo ha provenance,
status e migrazione JSON. Non inventare valori nei seed Makera Z1.

Le 18 definition sono registrate tutte. Un tipo e eseguibile solo per la sua
geometria/capability 2D dichiarata; una capability mancante produce errore
esplicito. Questo e preferibile a generare un percorso plausibile ma non sicuro.

| Famiglia | Operazioni | Contratto Fase 4 |
| --- | --- | --- |
| 2.5D milling | Face Top, Roughing, Facing, Pocketing, Profiling, Slotting, T-Slotting | Raster/contour 2D, passate Z, compensazione e tool geometry valida. |
| Drilling | Holes, Drill, Boring, Hole Pocketing, Thread Milling, Tapping | Cerchi/centri validati; thread richiede pitch/diametro; tapping richiede spindle sync dichiarato. |
| Carving | V-Carve Roughing, V-Carving, Engraving, Chamfering, Filleting | V/chamfer richiedono angolo utensile e depth/width; filleting 2D e eseguibile solo con parametri espliciti di profilo, altrimenti capability 3D non supportata. |

`Face Top` e `Facing` restano distinti: il primo livella una regione top da
stock allowance, il secondo lavora una regione selezionata a profondita target.
`Roughing` e una strategia generica region-based con stock allowance; non e
adaptive clearing. `Holes` orchestra piu `Drill`/`Boring` selezionati senza
duplicare motion. `Tapping` non genera un finto plunge: richiede supporto
sincronizzato, altrimenti `failed` con `machine_spindle_sync_required`.

## 4.10 API applicativa e CLI

Creare `ToolpathService` con:

- `plan_operation(project_id, operation_id, scene, settings) -> ToolpathOperationResult`;
- `plan_project(project_id, scene, settings) -> ToolpathPlan`;
- `validate_project_plan_inputs(project_id, scene, settings) -> ToolpathDiagnostics`.

Il service legge il `Project` da `ProjectService` o repository tramite protocollo
read-only; non modifica progetto, command stack o event bus. La futura UI avvia
il service in worker, ma il core rimane sincrono e cancellabile solo tramite un
token/protocollo opzionale, non tramite Qt.

Estendere la CLI con:

```powershell
antcam-rc2 plan project.antcam.json --geometry drawing.dxf --out plan.json --dump
```

Il comando importa il documento in un `Application` headless, importa la scena,
confronta fingerprint/source, pianifica e scrive JSON solo se `is_executable`.
`--dump` mostra operazioni, pass count, tool, feed/speed, warning/error e
fingerprint. Exit codes: `0` valido, `1` plan non eseguibile, `2` input/file
assente, `3` JSON/progetto invalido. Non generare G-code.

Questo e il primo test manuale utile: dopo Fase 4 si puo creare/importare un
progetto e ispezionare path/motion JSON. La prova ergonomica di selezione e
viewport arriva in Fase 5; simulazione/collisioni in Fase 6; CNC reale solo
dopo postprocessor Fase 7 e verifica controllata.

## 4.11 Persistenza e invalidazione

Il toolpath e un artefatto derivato, non parte dell'envelope Project Fase 3.
Può essere esportato in un file separato `*.toolpath.json` con:

- `toolpath_schema_version`;
- `project_id`, fingerprint progetto e fingerprint scena;
- catalog versions, PlanningSettings e registry definition versions;
- `ToolpathPlan` e diagnostica.

Un export e valido solo se tutti i fingerprint/version binding corrispondono.
Una modifica di progetto, tool, materiale, cooling, settings o scena invalida
l'artefatto; non esiste cache implicita. Fase 8 potra aggiungere cache content-
addressed senza cambiare il contratto.

## 4.12 Test, corpus e performance

### Unit

- motion/schema JSON: invarianti motion, archi, feed, continuita e fingerprint;
- registry: duplicate registration, param schema, tool/capability mismatch;
- frame/depth: tutte le stock origin, quote finali, clamp e limite fondo;
- compensation: outer/inner loops, offset concavo, tool troppo grande;
- entry/exit: clearance, plunge, ramp consentita/degradata e no escape;
- local ordering: stable tie-break, soglia performance, nessun reorder progetto;
- ogni famiglia strategy: geometry valida, geometry invalida, tool incompatibile,
  manual override, auto feed, warning e failure.

### Integration

- progetto Makera, stock alluminio, mini DXF/SVG, refs validate, plan JSON;
- disabled operation, ref stale, mismatch scene, catalog ID mancante e plan non
  eseguibile;
- mixed project con drilling/pocket/profile e ordine utente preservato;
- determinismo: due planning identici hanno byte/fingerprint identico;
- CLI `plan` produce JSON per fixture valida e non crea output eseguibile per
  errore; output non richiede GUI;
- compatibilita wheel installata fuori dal source tree.

Le fixture partono piccole, ASCII e con valori semantici attesi, poi aggiungono
un corpus di regressione reale 2D in Fase 8. I golden test confrontano il
contratto motion normalizzato e invarianti, non rumore di float non quantizzato.

### Budget

- planning di una fixture piccola: < 250 ms su hardware sviluppo;
- planning progetto con 50 operazioni/500 ref: < 5 s senza simulazione;
- determinismo: 100% fingerprint uguale su almeno 20 run dello stesso input;
- nessun algoritmo O(n^2) non limitato; l'optimizer documenta soglie/fallback;
- coverage globale rimane >= 70%, moduli nuovi core >= 80% salvo error paths
  esterni documentati.

## 4.13 Ordine di implementazione

1. Motion models, diagnostics, schema/fingerprint e test JSON/determinismo.
2. Setup frame, depth pass, builder e entry/exit con test di sicurezza Z.
3. Contracts, registry iniettata, param schema e bootstrap Application.
4. Compensation/local ordering e strategie 2.5D milling fondamentali:
   facing, pocketing, profiling, slotting, roughing, face top.
5. Strategie drilling e carving, con capability gating per thread/tap/fillet.
6. `ToolpathService`, artifact export e CLI `plan` headless.
7. Corpus, benchmark, error paths, wheel smoke, QA e documentazione API.

Le 18 definition devono essere registrate entro il punto 5. La definizione non
viene marcata "completa" finche non ha test per input supportato e per rejection
sicura di input/capability fuori scope.

## 4.14 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| Percorso riferito a geometria cambiata | Resolve fingerprint exact-match e failure non eseguibile. |
| Planner riordina setup dell'utente | Ottimizzazione solo locale; ordine Project invariabile. |
| Parametri strategy non validati | Pydantic model/schema per definition, non dict interpretato liberamente. |
| Feed coerente ma engagement non noto | Passare engagement reale quando disponibile; warning o rejection per strategie che lo richiedono. |
| Tool/capability insufficiente | Registry e catalog validation prima della prima motion. |
| Tapping/fillet 3D simulati impropriamente | Capability gate esplicito e diagnostic code, mai fallback pericoloso. |
| Offset fallisce su concavita | Errore diagnostico, nessun bbox fallback; regressioni geometriche dedicate. |
| Floating point rompe JSON/golden | Quantizzazione canonica, fingerprint e determinism tests. |
| CLI esporta plan parziale | Scrittura solo per `is_executable`, exit code non-zero per failure. |
| UI/simulazione entra nel core | Dipendenze pure e test anti-PySide6; confini Fase 5/6 invariati. |
