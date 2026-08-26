# AntCAM RC2 - Fase 2: Cataloghi di lavorazione e motore feed/speed

## 2.1 Obiettivo

Costruire il dominio dati di lavorazione **UI-agnostico, immutabile e serializzabile** che permettera alla Fase 3 di salvare riferimenti a macchina, utensile, materiale e cooling, e alla Fase 4 di generare toolpath con parametri spiegabili.

La Fase 2 consegna:

- cataloghi JSON versionati per macchine, utensili, materiali e raffreddamento;
- modelli Pydantic v2 validati e API repository read-only;
- profilo seed Makera Z1 con provenienza verificabile dei dati;
- calcolatore puro e deterministico per RPM, cut feed, plunge feed, stepdown e stepover;
- override manuali con origine esplicita e clamp di sicurezza sempre applicati;
- contratti JSON Schema per i cataloghi e test unit/integration completi.

**Criterio di uscita**: un programma headless puo caricare i cataloghi package-data, risolvere Makera Z1 + utensile + materiale + cooling, calcolare parametri serializzabili entro i limiti macchina, spiegare origine e clamp di ogni valore e superare l'intera suite QA con coverage globale >= 70%.

## 2.2 Stato di partenza verificato

Fase 0 e Fase 1 sono effettivamente presenti nel package:

- `app.Application` registra Container, EventBus, CommandStack e lo stub `ProjectService`; l'ultimo resta volutamente non implementato fino alla Fase 3.
- `core.io.import_file()` e l'entry point pubblico DXF/SVG e `GeometryScene` e il contratto neutro fra import e dominio futuro.
- `pyproject.toml` ha Pydantic v2, pydantic-settings, ruff, ty, pytest e coverage.
- Nessun modulo `core.databases/`, `core.feeds_speeds/` o catalogo package-data esiste ancora.

La Fase 2 deve rimanere indipendente dai package legacy `antcam` e `antcam_rc1`. Le loro formule e test possono essere solo riferimento di dominio, non dipendenze o codice copiato.

## 2.3 Confini e decisioni di scope

### Incluso

- Cataloghi statici read-only: machine, tool, material, cooling.
- Validazione JSON Schema e Pydantic v2.
- Lookup del catalogo, integrita referenziale e caricamento di bundle package-data o directory esplicita.
- Feed/speed automatici per famiglie `milling`, `drilling` e `carving`, con input esplicito.
- Parametri manuali, clamp ai limiti fisici e tracciabilita della risoluzione.
- Makera Z1 seed e una baseline prudente di utensili/materiali/cooling.

### Escluso deliberatamente

- `Project`, `Stock`, `Fixture`, `Operation`, preferiti, clipboard, JSON di progetto e `ProjectService`: Fase 3.
- `OperationRegistry`, selezione automatica utensile, passi/entry/ramp, toolpath e le 18 strategie: Fase 4.
- UI, CLI per modifica cataloghi, scrittura in `user_data_dir`, simulazione e post-processori.
- Adattamento dinamico chip-thinning e parametri specifici di tapping: richiedono geometria/toolpath e sono rinviati alla Fase 4.
- HPGL, G-code re-import, Gerber e gli altri formati segnati "Fase 2" nella tabella formati di `PLAN.md`: confliggono con la tabella delle fasi. Questa fase segue la roadmap dei cataloghi/feed-speed; l'estensione formati resta backlog separato da ripianificare esplicitamente.

## 2.4 Struttura dei file

```text
src/antcam_rc2/
├── PLAN_FASE_2.md
├── pyproject.toml
├── src/antcam_rc2/
│   ├── app/
│   │   └── application.py
│   ├── core/
│   │   ├── databases/
│   │   │   ├── __init__.py
│   │   │   ├── models.py
│   │   │   └── repository.py
│   │   └── feeds_speeds/
│   │       ├── __init__.py
│   │       ├── models.py
│   │       └── calculator.py
│   └── data/
│       ├── __init__.py
│       ├── machines.json
│       ├── tools.json
│       ├── materials.json
│       └── cooling.json
└── tests/
    ├── data/catalogs_invalid/
    ├── unit/
    │   ├── test_database_models.py
    │   ├── test_catalog_repository.py
    │   └── test_feeds_speeds_calculator.py
    └── integration/
        └── test_catalogs_and_feeds_speeds.py
```

Usare `core.databases` e `core.feeds_speeds` come moduli fratelli, come stabilito in `PLAN.md`; non collocare il calcolatore in `databases/`.

## 2.5 Contratti del catalogo

Ogni file catalogo usa un envelope con `schema_version`, `catalog_id` e `items`. Il repository carica un bundle completo e fallisce atomicamente: nessun catalogo parzialmente caricato o stato cache parziale e osservabile.

Gli ID nei cataloghi sono chiavi semantiche e stabili, ad esempio `makera_z1`, `end_mill_3_175_2f`, `aluminum_6061`, `aerodust`. Non usare `new_id()` ne `is_valid_id()`: quelle API generano ID runtime con suffisso UUID e sono destinate a entita di progetto della Fase 3. Applicare invece un pattern snake_case documentato, unicita per catalogo e riferimenti esterni espliciti.

I modelli catalogo usano `ConfigDict(frozen=True, extra="forbid")`; gli envelope e i result usano `model_dump(mode="json")` e `model_validate()` per un round-trip JSON stabile.

### MachineProfile

Definire `MachineProfile` con almeno:

- `id`, `name`, `vendor`, `model`, `catalog_version`, `source_reference`;
- work envelope X/Y/Z in mm;
- spindle power, `min_rpm`, `max_rpm` e `max_feed_mm_min`;
- collet sizes in mm e `default_collet_size_mm`;
- cooling IDs supportati, default cooling ID e post nativo;
- diametro/lunghezza del gruppo spindle-collet, necessari alla collision detection della Fase 6.

Validare dimensioni/feed/RPM non negativi, massimo RPM maggiore del minimo, collet default incluso nella lista e default cooling incluso fra gli ID supportati. `min_rpm = 0` e ammissibile per rappresentare stop spindle, ma il calcolatore non puo produrre 0 RPM per una lavorazione: applica un minimo fisico di 1 RPM prima dei clamp.

### Tool, MaterialProfile e CoolingProfile

Definire `Tool` con `id`, `name`, `tool_type`, diametro tagliente, diametro gambo, numero taglienti, flute length, overall length, materiale, coating opzionale, max cutting depth opzionale e collet compatibility opzionale.

`tool_type` e un `Enum` con: end mill, ball, bull, v-bit, drill, tap, bore, thread mill e t-slot. Validare diametri e lunghezze positivi, flute count positivo per utensili rotanti e coerenza delle dimensioni. La compatibilita geometrica con una specifica operazione e la selezione automatica utensile restano Fase 4.

`MaterialProfile` contiene `id`, `name`, `family`, hardness opzionale, `surface_speed_m_min`, `chip_load_mm_tooth`, `plunge_ratio` e `machinability_factor`. `CoolingProfile` contiene `id`, `name`, `kind`, `surface_speed_factor` e `chip_load_factor`. I fattori cooling seed sono: `none=0.85`, `air/aerodust=1.0`, `mist=1.05`, `flood=1.15`; sono policy iniziali e non possono superare limiti macchina.

### JSON Schema e package data

Aggiungere `jsonschema` alle dipendenze runtime. Il modello Pydantic e la sorgente canonica: esporre uno schema Draft 2020-12 per ogni envelope e validare il payload prima della validazione Pydantic dettagliata.

I JSON seed vivono in `src/antcam_rc2/src/antcam_rc2/data/`, con `__init__.py`, e sono letti con `importlib.resources`; non devono essere cercati in `AppConfig.user_data_dir`, copiati o mutati. `CatalogRepository.from_package()` carica gli asset distribuiti, mentre `CatalogRepository.from_directory(path)` e esplicito e read-only per test e cataloghi esterni futuri.

## 2.6 Repository e Application

`CatalogRepository` e un servizio read-only, costruito con una source esplicita e senza singleton globale. Espone:

- `load() -> CatalogBundle`, idempotente per istanza;
- `machine(id)`, `tool(id)`, `material(id)`, `cooling(id)`;
- enumerate ordinati e `catalog_versions()`.

Durante `load()`, validare JSON, JSON Schema, Pydantic, duplicati e riferimenti cross-catalogo. Utilizzare `ConfigurationError` per source assente, JSON non leggibile o configurazione invalida; aggiungere `CatalogError(ConfigurationError)` con `code = "catalog_error"` per lookup sconosciuti e integrita di bundle.

In `Application`, registrare una singola istanza `CatalogRepository.from_package()` come servizio lazy (`catalog_repository` property). Non forzare il load durante bootstrap. Nessun nuovo evento e necessario.

## 2.7 Motore feed/speed

### Input e output

In `core.feeds_speeds.models` definire:

- `OperationFamily`: `milling`, `drilling`, `carving`;
- `FeedSpeedRequest`: tool, material, machine, cooling, famiglia, diametro effettivo opzionale, stepdown/stepover opzionali e engagement opzionale;
- `FeedSpeedOverrides`: rpm, cut feed, plunge feed, stepdown e stepover opzionali;
- `ValueOrigin`: `automatic`, `manual`, `manual_clamped`;
- `FeedSpeedResult`: valori finali, origine per campo, clamp applicati, coefficienti effettivi e trace serializzabile.

Un V-bit richiede `effective_cutting_diameter_mm` quando il diametro di taglio reale differisce da quello nominale; la geometria che lo determina e Fase 4.

### Regole deterministiche V1

1. `Vc_eff = material.surface_speed_m_min * material.machinability_factor * cooling.surface_speed_factor`.
2. `fz_eff = material.chip_load_mm_tooth * cooling.chip_load_factor * operation_family.chip_load_factor * engagement_factor`.
3. `requested_rpm = (1000 * Vc_eff) / (pi * effective_diameter_mm)`, clamp a `[max(machine.min_rpm, 1), machine.max_rpm]`.
4. `cut_feed = rpm_final * fz_eff * tool.flute_count`; la formula per dente e valida anche per drilling. Tapping viene rifiutato esplicitamente in V1 perche richiede pitch/sync.
5. `plunge_feed = cut_feed * material.plunge_ratio`, poi clamp nel range macchina.
6. Applicare default policy versionata per stepdown/stepover per famiglia, come ratio del diametro effettivo. Per drilling lo stepover e `None`, non zero.
7. Applicare engagement policy conservativa solo quando entrambe le dimensioni sono note: riduce, mai aumenta, il chip load. Senza engagement, factor 1.0 e trace esplicita.
8. Applicare override dopo il calcolo automatico, ma sempre clampare RPM/feed e dimensioni ai limiti macchina/policy. Un override fuori limite diventa `manual_clamped` e compare in `clamps_applied`.

Non usare `tool.max_cutting_depth` per clampare uno stepdown: quel campo riguarda il reach/depth complessivo e sara verificato dalla strategia/toolpath in Fase 4.

## 2.8 Seed Makera Z1

Popolare un bundle minimo:

- una `makera_z1` con envelope 200 x 200 x 100 mm, spindle 150 W, range fino a 13,000 RPM, collet 3.175 mm default e opzioni 4/6/6.35 mm, AeroDust/air e post Makera;
- utensili per famiglie base: end-mill 3.175 mm e 6 mm, drill 2/3/6 mm, ball/bull, V-bit, bore, thread-mill, tap e T-slot;
- materiali `generic`, `aluminum_6061`, `steel_mild`, `plastic`;
- cooling `none`, `aerodust`, `mist`, `flood`.

Non inventare `max_feed_mm_min`, dimensioni spindle-collet, Vc/fz o altri valori non confermati. Ogni record seed include `source_reference` e `data_status` (`verified` o `conservative_pending_verification`).

## 2.9 Test e QA

### Unit

- `test_database_models.py`: range, enum, immutabilita, chiavi semantiche, duplicate IDs e JSON round-trip.
- `test_catalog_repository.py`: package/directory load, idempotenza, lookup, bundle atomico, JSON malformato, schema/Pydantic invalidi e riferimenti cross-catalogo invalidi.
- `test_feeds_speeds_calculator.py`: formula RPM, clamp, cooling/material/family factors, V-bit, drilling, stepover N/A, engagement, tapping, manual value, manual clamp e trace.

### Integration e packaging

- `test_catalogs_and_feeds_speeds.py`: `Application` risolve il repository lazy, carica seed Makera e calcola un result entro i limiti.
- Estendere l'anti-legacy test ai moduli aggiunti.
- Buildare una wheel e verificare `antcam_rc2/data/*.json` e `CatalogRepository.from_package()` in ambiente isolato.

### Comandi di uscita

Da `src/antcam_rc2`:

1. `.venv\Scripts\python.exe -m ruff check .`
2. `.venv\Scripts\python.exe -m ruff format --check .`
3. impostare `VIRTUAL_ENV` se richiesto, poi `.venv\Scripts\python.exe -m ty check .`
4. `.venv\Scripts\python.exe -m pytest tests/`
5. `.venv\Scripts\python.exe -m coverage run -m pytest tests/` e `.venv\Scripts\python.exe -m coverage report`
6. build wheel e smoke test package-data isolato.

Mantenere `fail_under = 70`.

## 2.10 Ordine di implementazione

1. Definire envelope, modelli e schema JSON con test di validazione e round-trip.
2. Creare cataloghi seed package-data con provenienza e testare schema + inclusione wheel.
3. Implementare `CatalogRepository` con carico atomico, lookup e integrita referenziale.
4. Aggiungere repository lazy in `Application` e il test integration del servizio.
5. Definire request/override/result e `OperationFamily`; scrivere test della formula base.
6. Implementare policy, clamp e trace; aggiungere casi limite e manual override.
7. Eseguire prova end-to-end Makera Z1 e QA/packaging completi.

## 2.11 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| Dati Makera o parametri di taglio non attendibili | Provenienza per record/campo, status esplicito e valori prudenti. |
| Il catalogo JSON non entra nella wheel | Test di wheel isolata; cambiare Hatch solo come risposta a un fallimento dimostrato. |
| Cataloghi parzialmente validi generano lookup incoerenti | Parse/validate completo prima di sostituire la cache. |
| Override manuale bypassa sicurezza | Clamp anche sui manual value, origine `manual_clamped` e trace machine-readable. |
| Confusione fra ID seed e ID progetto | Chiavi semantiche per cataloghi; `new_id()` solo per entita runtime future. |
| Formula generica usata per processi non modellati | Supporto limitato a milling/drilling/carving; tapping rifiutato fino alla Fase 4. |
| Il calcolatore cresce in planner prematuro | Nessuna selezione utensile, geometria, profondita totale, eventi o toolpath in Fase 2. |
