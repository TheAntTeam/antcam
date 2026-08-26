# AntCAM RC2 — Fase 0: Scaffolding & Fondamenta Architetturali

## 0.1 Obiettivo

Creare il package `antcam_rc2` come **greenfield indipendente** (nessun import da `antcam`/`antcam_rc1`), con:

- Struttura modulare completa (core kernel agnostico UI + app + frontends)
- QA tooling identico a quello esistente (ruff, ty, pytest, coverage)
- Mattoni architetturali: config, logging, unità, errori, DI container, event bus, command stack (undo/redo), contract core/UI
- Entry point CLI minimale verificabile

**Criterio di uscita**: `antcam-rc2 --version` + `--help` funzionano; tutti i test unit/integration passano; coverage ≥ 70%; nessun import verso i package legacy (verificato da grep in CI).

## 0.2 Struttura dei file da creare

```
src/antcam_rc2/
├── PLAN_FASE_0.md                        ← questo documento
├── pyproject.toml                         ← package `antcam-rc2`, hatchling
├── README.md                              ← install, comandi QA, layout moduli
├── src/antcam_rc2/
│   ├── __init__.py                        ← versione, __all__, docstring top-level
│   ├── __main__.py                        ← `python -m antcam_rc2`
│   ├── py.typed                            ← marker PEP 561
│   ├── app/
│   │   ├── __init__.py
│   │   ├── container.py                   ← DI container (registry + risoluzione lazy)
│   │   ├── event_bus.py                   ← EventBus sincrono tipizzato
│   │   ├── events.py                      ← gerarchia eventi di dominio (Pydantic)
│   │   └── application.py                 ← Application: bootstrap core, fornisce servizi
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                      ← AppConfig (pydantic-settings), logger config
│   │   ├── logging.py                     ← setup_logger strutturato, formato, handler
│   │   ├── units.py                       ← UnitSystem, conversione mm/in, Enum
│   │   ├── errors.py                      ← gerarchia eccezioni (AntcamError base)
│   │   ├── identifiers.py                 ← generatore ID deterministici (uuid)
│   │   └── services/
│   │       ├── __init__.py
│   │       ├── command_stack.py           ← CommandStack undo/redo con profondità max
│   │       ├── commands.py                ← Command (abstract), UndoableCommand
│   │       └── project_service.py         ← STUB (Fase 3) — firma + docstring contract
│   ├── frontends/
│   │   ├── __init__.py
│   │   ├── pyside/
│   │   │   ├── __init__.py
│   │   │   └── main.py                    ← STUB: crea QApplication + window vuota (Fase 5)
│   │   └── web/
│   │       └── __init__.py                ← placeholder (Fase 10)
├── tests/
│   ├── conftest.py                        ← fixtures condivise, env test
│   ├── unit/
│   │   ├── test_config.py
│   │   ├── test_logging.py
│   │   ├── test_units.py
│   │   ├── test_errors.py
│   │   ├── test_identifiers.py
│   │   ├── test_container.py
│   │   ├── test_event_bus.py
│   │   └── test_command_stack.py
│   └── integration/
│       ├── test_application.py            ← bootstrap + ciclo eventi completo
│       └── test_no_legacy_imports.py      ← grep: nessun `from antcam`/`antcam_rc1`
└── .gitignore
```

## 0.3 Dettaglio per modulo

### `pyproject.toml`
- `name = "antcam-rc2"`, `version = "0.0.1"`, hatchling, `requires-python = ">=3.13"`
- `dependencies`: `numpy`, `pydantic>=2`, `pydantic-settings>=2`, `scipy` — **core UI-agnostico**
- `[project.optional-dependencies] gui = ["PySide6>=6.6"]`
- `[project.scripts] antcam-rc2 = "antcam_rc2.__main__:main"`
- `[dependency-groups] dev` con ruff/ty/pytest/coverage
- `[tool.ruff]` line-length 120, select E/W/F/I/B/UP (come repo)
- `[tool.ty]`, `[tool.coverage]` (source `src/antcam_rc2/`)

### `core/config.py`
- `AppConfig` (pydantic-settings, `env_prefix="ANTCAM_"`): `log_level`, `log_file` (optional), `user_data_dir`, `cache_dir`
- `load_config()` con override da env; `frozen=True`
- Test: default, override env, percorso invalidato

### `core/logging.py`
- `setup_logger(name, level, log_file=None)` idempotente (no handler duplicati)
- Formatter strutturato `%(asctime)s %(levelname)-8s %(name)s  %(message)s` + rotazione file via `RotatingFileHandler`
- `get_logger(name)` helper che garantisce il setup con i valori di `AppConfig`
- Test: handler singolo, livello rispettato, file scritto

### `core/units.py`
- `UnitSystem` (Enum: `METRIC`, `IMPERIAL`)
- `mm_to_in`, `in_to_mm`, `convert(value, from_, to_)`
- `TOLERANCE_MM = 1e-6`, `SCALE_EPS = 1e-9` (costanti condivise future)
- Test: conversioni, round-trip, errore su sistema ignoto

### `core/errors.py`
- Base `AntcamError(Exception)` con `code: str`
- `ConfigurationError`, `UnsupportedFormatError`, `GeometryError`, `OperationError`, `ToolpathError`, `SimulationError`, `PostProcessorError`
- Test: cattura per base, `.code` valorizzato

### `core/identifiers.py`
- `new_id(prefix: str) -> str` basato su `uuid4().hex[:8]` con prefisso (es. `op_a1b2c3d4`)
- `is_valid_id(value, prefix)` per validazione
- Test: unicità, formato, prefisso

### `app/container.py`
- `Container`: registra fabbriche `register(key, factory)` e risolve lazy `resolve(key)` con **singleton cache**
- Supporta `override(key, instance)` per i test
- Test: lazy, singleton, override, errore su chiave sconosciuta

### `app/events.py` + `app/event_bus.py`
- `Event` (Pydantic): `ProjectCreated`, `OperationAdded`, `OperationRemoved`, `OperationReordered`, `ToolpathGenerated`, `LogEvent` — **minimo per contract Fase 3**
- `EventBus`: `subscribe(event_type, handler)`, `emit(event)`, `unsubscribe`, dispatch sincrono, handler che sollevano → wrappati in `EventBusError`
- Test: ricezione, filtri per tipo, unsubscribe, errore handler

### `core/services/commands.py` + `command_stack.py`
- `Command` (ABC): `name`, `execute()`, `undo()`
- `CommandStack`: `push`, `undo()`, `redo()`, `can_undo/can_redo`, `max_depth` (default 50, configurabile), `clear`, `on_change` callback (per UI)
- Test: push/undo/redo, profondità max (i più vecchi scartati), clear, callback

### `app/application.py`
- `Application`: riceve `AppConfig`, crea `Container`, registra `EventBus`, `CommandStack`, `ProjectService` (stub); espone `services` per la UI; `shutdown()` per flush logger/file
- Test: bootstrap, risoluzione servizi, shutdown idempotente

### `core/services/project_service.py` (stub)
- Firma anticipata: `list_operations()`, `add_operation(...)`, `remove_operation(...)`, `move_operation(...)`, `toggle_operation(...)`, `duplicate_operation(...)` → **`NotImplementedError`** con docstring di contract per Fase 3

### `frontends/pyside/main.py` (stub)
- `main()`: import **differito** di PySide6 (opzionale gui), crea `QApplication`, `Application` core, window vuota con titolo, `exec()`
- Test (skip se PySide6 assente): window creata, titolo corretto

## 0.4 QA Tooling

- Ruff format + lint (identici al repo: E,W,F,I,B,UP)
- `ty check` sul package
- Pytest con coverage (`fail_under = 70` per rc2)
- Test anti-regressione: `test_no_legacy_imports.py` greppa il tree `src/antcam_rc2/` per `from antcam` / `antcam_rc1`
- Comandi esposti in `README.md`:
  - `.venv\Scripts\python.exe -m pip install -e src/antcam_rc2`
  - `antcam-rc2 --version` / `--help`
  - `pytest`, `ruff check`, `ty check`

## 0.5 Ordine di implementazione

1. `pyproject.toml` + `README.md` + struttura folder
2. `__init__`, `__main__`, `py.typed` → verificare `antcam-rc2 --version`
3. `core` (units → errors → identifiers → logging → config)
4. `app` (container → events → event_bus → commands → command_stack → application)
5. `core/services/project_service.py` (stub)
6. `frontends/pyside/main.py` (stub)
7. Test unit per ciascun modulo (scritti **insieme** al modulo, non in coda)
8. Test integration + anti-legacy
9. QA completo + fix

## 0.6 Rischi e mitigazioni

| Rischio | Mitigazione |
|---|---|
| Nome package confonde con `antcam` esistente | `antcam-rc2` distinto; anti-legacy test in CI |
| Doppio package editable nello stesso venv | rc2 installato come package separato; niente conflitto di entry-point |
| Contract eventi/command troppo rigidi per Fase 3 | Events/Commands minimi e documentati, estensibili senza breaking |
| Coverage < 70% su stub | Stub esclusi da coverage (`exclude_also`), test sui mattoni reali |