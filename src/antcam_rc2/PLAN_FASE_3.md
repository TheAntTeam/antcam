# AntCAM RC2 - Fase 3: Progetto, setup e persistenza

## 3.1 Obiettivo

Costruire il dominio **operation-centric** persistente e UI-agnostico: il
progetto conserva la configurazione macchina, stock, fixture, operazioni e
riferimenti sicuri al disegno 2D. `ProjectService` diventa il punto applicativo
per le modifiche atomiche, undo/redo, eventi e import/export JSON.

La Fase 3 consegna:

- modelli Pydantic v2 immutabili per progetto, stock, fixture e operazione;
- riferimenti robusti alle entita di `GeometryScene`, senza serializzare la scena;
- repository in memoria e documenti JSON versionati con scrittura atomica;
- lifecycle di progetto e gestione operazioni via `CommandStack`;
- validazione di riferimenti catalogo, eventi di dominio e compatibilita cataloghi;
- test unit/integration headless e contratti pubblici stabili per Fase 4.

**Criterio di uscita**: un programma headless crea un progetto Makera Z1,
collega un DXF/SVG gia importato, configura stock/fixture, crea e riordina
operazioni, esegue undo/redo, esporta/importa JSON e rileva in modo esplicito
riferimenti catalogo o geometrici non piu validi.

## 3.2 Stato di partenza verificato

- Fase 0 offre `Application`, `Container`, `EventBus`, `CommandStack` e il
  contratto `ProjectService`.
- Fase 1 offre `GeometryScene`, `SourceInfo` e import DXF/SVG normalizzati.
- Fase 2 offre cataloghi immutabili, `CatalogRepository` e
  `FeedsSpeedsCalculator` puro.
- Il core rimane senza import PySide6 o package legacy.

## 3.3 Confini

### Incluso

- `Project`, `Stock`, `Fixture`, WCS, operazioni e parametri comuni.
- Associazione transiente di `GeometryScene` e riferimenti persistenti sicuri.
- Catalog snapshot/version diagnostics e validazione di ID referenziati.
- JSON import/export, repository in memoria, comandi, undo/redo ed eventi.
- Add/remove/move/toggle/duplicate operazioni; macchina, stock e fixture.

### Escluso deliberatamente

- `OperationRegistry`, strategie, tool selection, feed/speed calcolati,
  passate, entry/ramp, toolpath e G-code: Fase 4 e 7.
- Serializzazione integrale di `GeometryScene`: richiede un formato geometrico
  dedicato e resta esterno al documento progetto in questa fase.
- UI, clipboard, favorites, batch transactions, mesh fixture, collisioni e
  editing simultaneo di piu progetti.
- I formati aggiuntivi elencati in `PLAN.md` non rientrano in Fase 3 senza una
  roadmap dedicata.

## 3.4 Decisioni architetturali

### Snapshot immutabili e comandi

I modelli dominio sono `frozen=True` e `extra="forbid"`. Ogni comando contiene
uno snapshot `before` e `after` di `Project` ed e l'unico componente che
sostituisce lo stato nel repository durante execute/undo. Il service non deve
mutare una seconda volta dopo `CommandStack.push()`.

La Fase 3 usa la `CommandStack` applicativa in una sessione monoprogetto.
Ogni comando contiene comunque `project_id`, cosi la futura estensione a stack
per progetto non richiedera cambiare il contratto.

Gli eventi sono notifiche dopo il commit: non sono validatori e un errore di un
subscriber non puo annullare uno stato gia committato.

### Persistenza progetto

Il file JSON e un envelope Pydantic:

```json
{
  "schema_version": "1.0",
  "project": { "...": "setup persistente" },
  "catalog_snapshot": { "versions": { "machines": "1.0" } },
  "geometry_binding": { "source": { "format": "dxf" }, "scene_fingerprint": "sha256:..." }
}
```

Il documento conserva intent, ID catalogo e override manuali. Non conserva
`FeedSpeedResult`, diametro effettivo V-bit, passate, toolpath o cache di
simulazione. Export usa JSON UTF-8 canonico e file temporaneo nello stesso
directory seguito da replace atomico.

Versioni catalogo diverse generano un report di compatibilita non bloccante;
ID macchina/materiale/utensile/cooling assenti sono errori bloccanti.

### GeometryRef sicuro

`GeometryScene` resta transiente: il progetto conserva solo `GeometryBinding`
e `GeometryRef`. Ogni ref contiene `layer_name`, `entity_index`, `entity_type`
e fingerprint SHA-256 dell'entita canonica. La risoluzione:

1. accetta l'indice soltanto se tipo e fingerprint coincidono;
2. cerca lo stesso fingerprint nel layer se l'entita e stata riordinata;
3. rifiuta ref mancanti, ambigui o geometricamente modificati.

Non e ammesso selezionare silenziosamente una nuova entita allo stesso indice.
Il fingerprint vive in `core.project.geometry_refs`, non modifica Fase 1, ed e
type-tagged, deterministico, privo di path/diagnostica e quantizzato alla
tolleranza della scena.

### Operazioni e ordine

`OperationType` definisce il vocabolario stabile delle 18 operazioni previste
in `PLAN.md`, senza introdurre il registry plugin di Fase 4. `Operation`
contiene ID, nome, enabled, tipo, tool/cooling selezionati, geometry refs,
parametri comuni e un dizionario JSON-only limitato per futuri parametri
strategia. La validazione specifica della strategia resta Fase 4.

- add: append alla fine;
- duplicate: inserisce immediatamente dopo l'originale;
- move: sposta l'elemento esistente a un indice valido;
- move senza cambiamento: no-op, senza history;
- favorites e clipboard non alterano la sequenza e sono fuori scope.

## 3.5 Struttura prevista

```text
src/antcam_rc2/
├── PLAN_FASE_3.md
├── src/antcam_rc2/
│   ├── app/
│   │   ├── application.py
│   │   ├── event_bus.py
│   │   └── events.py                 # re-export di compatibilita
│   └── core/
│       ├── project/
│       │   ├── __init__.py
│       │   ├── geometry_refs.py
│       │   ├── models.py
│       │   ├── persistence.py
│       │   └── repository.py
│       └── services/
│           ├── events.py
│           ├── project_commands.py
│           └── project_service.py
└── tests/
    ├── unit/test_project_models.py
    ├── unit/test_geometry_refs.py
    ├── unit/test_project_repository.py
    ├── unit/test_project_persistence.py
    ├── unit/test_project_commands.py
    └── integration/test_project_workflow.py
```

## 3.6 Modello dominio

- `WorkCoordinateSystem`: G54 default, offset XYZ in mm.
- `Stock`: dimensioni positive, posizione XYZ, `material_id` e una delle quattro
  politiche di origine previste dal piano.
- `Fixture`: ID runtime `fix_*`, box, posizione e ruolo fixed/vise; il path mesh
  opzionale e metadata per Fase 6, non viene caricato qui.
- `Project`: ID `proj_*`, timestamp UTC, nome, machine ID, stock, fixture e
  operazioni ordinate; fixture e operation ID unici.
- `CatalogSnapshot`: versioni di tutti i cataloghi al salvataggio.

Errori di dominio usano `ProjectError`; reference stale usa
`GeometryReferenceError`. Errori Pydantic restano appropriati per JSON con
struttura o tipi invalidi.

## 3.7 Lifecycle applicativo

`ProjectService` riceve esplicitamente repository progetto, catalog repository,
command stack, publisher eventi e clock UTC. `Application` costruisce e
registra queste dipendenze in questo ordine. I metodi pubblici sono:

- `create_project`, `get_project`, `import_project`, `export_project`;
- `list_operations`, `add_operation`, `remove_operation`, `move_operation`,
  `toggle_operation`, `duplicate_operation`;
- `replace_stock`, `add_fixture`, `remove_fixture`, `select_machine`;
- `attach_geometry`, `validate_geometry_references`, `undo`, `redo`.

Il service valida i catalog IDs prima di creare un comando. `FeedsSpeedsCalculator`
non e invocato: Fase 4 fornira geometria e famiglia operazione effettive.

## 3.8 Ordine di implementazione

1. Modelli progetto, errori e unit test di validazione/round-trip.
2. Fingerprint/ref geometrico e test di reorder, stale e ambiguita.
3. Repository in memoria, documento versionato e persistenza atomica.
4. Comandi snapshot e test execute/undo/redo.
5. ProjectService DI, catalog validation, eventi e Application wiring.
6. Workflow integration, anti-legacy, typecheck, coverage e wheel smoke test.

## 3.9 QA e criteri di uscita

- Test unit per invarianti, fingerprint, persistence, comandi e error paths.
- Workflow integration: Makera + stock alluminio + scena DXF + operazioni +
  undo/redo + export/import + catalog mismatch report.
- `ruff check .`, `ruff format --check .`, `ty check .`, `pytest tests/` e
  coverage eseguiti dalla directory `src/antcam_rc2` con soglia >= 70%.
- Build wheel e smoke test installato fuori dal source tree.
- API pubbliche documentate, JSON stabile, casi limite documentati.

## 3.10 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| Re-import seleziona una curva diversa | Fingerprint obbligatorio, fallback solo exact-match, ref stale bloccante. |
| Doppia mutazione service/comando | Snapshot before/after; il comando e l'unico writer. |
| Catalogo aggiornato rompe un progetto | Snapshot versioni, report mismatch, ID mancanti bloccanti. |
| Toolpath prematuro nel modello | Persistire solo intent e override; derive state resta Fase 4. |
| JSON parzialmente scritto | Temp file nella stessa directory e replace atomico. |
| UI influenza il core | Dipendenze tramite protocolli e test anti-PySide6 invariati. |
