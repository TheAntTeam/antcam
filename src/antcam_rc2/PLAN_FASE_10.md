# AntCAM RC2 — Fase 10: Web UI (API layer + frontend three.js)

## 10.1 Obiettivo

Costruire il **frontend web** di AntCAM RC2: un **API layer HTTP** che espone i
servizi del kernel (progetti, catalogo, geometria 2D/3D, operazioni, toolpath,
simulazione, post) e una **SPA statica** che consuma lo stesso `RenderScene`
neutro della UI desktop e lo traduce in **three.js**. Nessuna logica CAM nel
frontend: la web è un **client del kernel**, esattamente come PySide6.

**Criterio di uscita**: avviando `antcam-rc2 web` (o `uvicorn`) un utente apre il
browser, crea un progetto, carica un DXF/SVG e/o uno STEP/STL, vede il grafo nel
viewport three.js, seleziona geometrie/feature, aggiunge/riordina operazioni,
genera il toolpath, esegue la simulazione e scarica il G-code — con API
documentate (OpenAPI), test end-to-end headless e copertura ≥ 70%.

## 10.2 Stato di partenza verificato

- Il kernel è **UI-agnostico**: `Application` espone `project_service`,
  `toolpath_service`, `catalog_repository`, `event_bus`, `command_stack`; il
  frontend desktop li consuma già senza logica di dominio.
- `core/rendering` emette un **`RenderScene` JSON puro** (nodi 2D, `SOLID_BOX`,
  `RenderMesh` 3D, picking id) pensato fin dalla Fase 5 per il riuso web
  ("lo stesso grafo servirà per la web") → il frontend traduce JSON in three.js.
- `ToolpathService.plan_snapshot` (thread-safe), `Simulator`, `PostService` sono
  già headless e deterministici → l'API li richiama direttamente.
- I modelli pydantic (`Project`, `ToolpathPlan`, `SimulationReport`,
  `RenderScene`, …) hanno già `model_dump_json` → contratti API quasi gratis.
- `core/io`/`core/io3d` importano DXF/SVG e STEP/STL; il caricamento file è già
  isolato in `import_file`/`import_file_3d`.
- `frontends/web/` è un **placeholder vuoto**; nessuna dipendenza HTTP presente.
- Il frontend desktop ha già `__getattr__` lazy per PySide6 → stesso pattern per
  FastAPI (extra opzionale `web`).

## 10.3 Analisi e revisione del progetto

### A. Framework API: FastAPI + uvicorn (extra opzionale)
**Opzioni**: (a) `http.server` stdlib (zero deps, ma multipart/validazione a
mano); (b) Flask (sync, dipendenza); (c) **FastAPI + uvicorn** (ASGI, OpenAPI
automatico, pydantic v2 nativo, multipart via `python-multipart`). **Decisione**:
(c) — per velocità di esecuzione e livello professionale: la validazione dei
payload riusa i modelli pydantic esistenti, l'OpenAPI è generata gratis e il
`TestClient` rende i test API headless e rapidi. FastAPI vive in un **extra
`web`** con import lazy (il CLI base non cambia).

### B. Modello di stato: Application singleton in-memory
Il server tiene **una singola `Application`** per processo (repository progetti
in-memory, come nel desktop). Persistenza = export/import `.antcam.json`.
Multi-tenant/DB/persistenza automatica sono **fuori scope** (10.4): per una web
CAM locale mono-utente è sufficiente e azzera il rischio di race su repository
condivisi. Documentato nel contratto API (`application/json`, stato volatile).

### C. Threading: CPU-bound fuori dal loop event
`plan`/`simulate` possono durare secondi. Gli endpoint sono `async def` che
delegato il lavoro con `asyncio.to_thread` (o `run_in_threadpool`) al
`ToolpathService`/`Simulator` **thread-safe** già esistenti. Niente job-queue in
Fase 10: la richiesta resta bloccante per il client (spinner nel frontend), ma
non blocca il loop event del server. Un job-queue/streaming è documentato come
estensione futura.

### D. Upload file: multipart streaming, non base64
DXF/SVG/STEP/STL viaggiano come `multipart/form-data` (`UploadFile`), salvati su
un `SpooledTemporaryFile`/`tempfile` e passati a `import_file`/`import_file_3d`.
Niente base64 (sovraccarico 33%) né parsing multipart manuale.

### E. Contratti API: JSON = modelli core
Le response sono `model_dump_json()` dei contratti già esistenti (`Project`,
`RenderScene`, `ToolpathPlan`, `SimulationReport`, `PostProgram`). Le request
sono piccoli schemi pydantic dedicati in `schemas.py` (create project, add
operation, plan settings, …). **Nessun DTO duplicato** oltre ai campi d'ingresso.

### F. Frontend: vanilla JS + three.js da CDN (niente build toolchain)
Per velocità di esecuzione si evita Node/npm/Vite/TypeScript: una SPA a file
statici (`index.html`, `app.css`, `js/*.js` ES modules) con three.js e
`OrbitControls` da CDN. Il browser carica i moduli nativamente. La traduzione
`RenderScene → three.js` è un singolo modulo `render_scene.js` (buffer geometry
batched, come il renderer GL desktop). Build tooling/TypeScript restano fuori
scope (10.4).

### G. Revisione prestazioni (velocità di esecuzione e di runtime)
- **Una sola chiamata scena**: `GET /projects/{id}/scene` restituisce l'intero
  `RenderScene` (2D + setup + 3D + toolpath) → il frontend ricostruisce il
  three.js solo su eventi (fingerprint in header `ETag`/`X-Scene-Fingerprint`).
- **Batching three.js**: linee → un `LineSegments` con `BufferAttribute`
  concatenato (un draw call); mesh 3D → un `BufferGeometry` per body; nessun
  oggetto per entità.
- **Picking web**: `THREE.Raycaster` su `LineSegments`/mesh con `userData` =
  picking id (già nel grafo); nessun FBO server.
- **API headless testabili**: `TestClient` (nessun server reale) → test veloci.
- **Server statico**: `StaticFiles` per `static/`; niente reverse proxy richiesto.

## 10.4 Confini

### Incluso

- `frontends/web/api/`: app FastAPI (factory), router per catalogo/progetti/
  operazioni/scena/toolpath/simulazione/post, schemi request/response,
  gestione upload 2D/3D, export/import progetto, undo/redo.
- `frontends/web/static/`: SPA vanilla JS + three.js (viewport, pannelli,
  traduzione `RenderScene`, picking, upload).
- `frontends/web/main.py`: entrypoint `antcam-rc2 web` (uvicorn) con import
  lazy di FastAPI; extra `web` in pyproject.
- CLI: subcomando `web` (e opzionale `--host/--port/--reload`).
- API versionata sotto `/api/v1` + OpenAPI `/docs`.

### Escluso deliberatamente

- **Persistenza multi-tenant/DB** e autenticazione/sessione: la web Fase 10 è
  mono-utente locale; export/import JSON come unica persistenza.
- **Job-queue/streaming** (progress di simulazione, SSE/WebSocket) → futuro.
- **Build toolchain JS** (Node/npm/TypeScript/Vite) e test browser E2E
  (Playwright) → fuori scope; il frontend è coperto da contract smoke.
- **Editing 3D avanzato nel browser** (mesh editing, snapping) e **multi-view
  sincronizzati** → fuori scope.
- **AuthN/AuthZ, TLS, deployment container** → fuori scope.

## 10.5 Decisioni architetturali finali

1. **FastAPI** (extra `web`: `fastapi`, `uvicorn`, `python-multipart`) dietro
   import lazy in `frontends/web/__init__.py` (stesso pattern di PySide6).
2. **Application singleton** per processo; `dependencies.py` crea e fa il
   `shutdown()` dell'app (lifespan FastAPI); repository in-memory.
3. **Endpoint async** che eseguono il lavoro CPU-bound in `asyncio.to_thread`;
   nessun blocco del loop event.
4. **API JSON = contratti core**: response da `model_dump_json`; request da
   schemi pydantic dedicati in `schemas.py`.
5. **`/api/v1`** prefix + OpenAPI; errori come `{"detail": ...}` con status
   coerenti (422 validazione, 404, 409, 500).
6. **Frontend statico servito dalla stessa app** (`StaticFiles(html=True)` per
   SPA fallback a `index.html`).
7. **Traduzione `RenderScene → three.js`** in un modulo JS puro e testabile
   (`render_scene.js`) che consuma il JSON del grafo neutro.
8. **Nessuna logica CAM nel frontend**: il browser chiama l'API e disegna; il
   kernel resta l'unica fonte di verità.

## 10.6 Struttura dei file

```text
src/antcam_rc2/
├── PLAN_FASE_10.md                      ← questo documento
├── pyproject.toml                       ← + extra [web] = fastapi, uvicorn, python-multipart
├── src/antcam_rc2/
│   ├── frontends/web/
│   │   ├── __init__.py                  ← lazy import (__getattr__) per FastAPI
│   │   ├── main.py                      ← entrypoint `antcam-rc2 web` (uvicorn)
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── app.py                   ← create_app() factory + lifespan
│   │   │   ├── dependencies.py          ← Application singleton + threadpool helpers
│   │   │   ├── schemas.py               ← request/response pydantic (create project, op, settings)
│   │   │   └── routes/
│   │   │       ├── __init__.py          ← include_router aggregator
│   │   │       ├── catalog.py           ← GET /catalog
│   │   │       ├── projects.py          ← CRUD + import/export + undo/redo
│   │   │       ├── geometry.py          ← upload DXF/SVG + upload STEP/STL
│   │   │       ├── operations.py        ← add/remove/toggle/move/duplicate/params/refs
│   │   │       ├── scene.py             ← GET /scene (RenderScene JSON)
│   │   │       ├── toolpath.py          ← plan + plan status + artifact
│   │   │       ├── simulation.py        ← simulate + report
│   │   │       └── post.py              ← post → G-code text
│   │   └── static/
│   │       ├── index.html
│   │       ├── css/app.css
│   │       └── js/
│   │           ├── api.js               ← fetch client (JSON + multipart)
│   │           ├── app.js               ← stato globale + bootstrap SPA
│   │           ├── render_scene.js      ← RenderScene JSON → three.js objects
│   │           ├── viewport.js          ← scene, camera, OrbitControls, picking
│   │           └── panels.js            ← DOM pannelli (progetto/ops/params/toolpath/sim)
│   └── __main__.py                      ← + subcomando `web`
└── tests/
    ├── unit/
    │   └── test_web_schemas.py          ← schemi request/response round-trip
    └── integration/
        ├── test_web_api_workflow.py     ← TestClient end-to-end (progetto→import→plan→post→sim)
        └── test_web_static.py           ← file statici presenti + index.html coerente
```

## 10.7 Dettaglio per modulo

### `frontends/web/api/app.py`
- `create_app() -> FastAPI`: prefix `/api/v1`, `lifespan` che crea/chiude
  l'`Application`, monta i router e `StaticFiles` su `/` (SPA fallback).
- Imposta `CORS` (dev locale) e `docs_url="/docs"`.

### `frontends/web/api/dependencies.py`
- `get_application()` dependency: restituisce l'`Application` dal `app.state`
  (creata nel lifespan).
- `run_sync(fn, *args)`: helper `asyncio.to_thread` per i servizi CPU-bound.
- Mappatura errori dominio (`ProjectError`, `ToolpathError`, …) → HTTP status.

### `frontends/web/api/schemas.py`
- `ProjectCreate { name, machine_id, stock }`, `OperationCreate { type, tool_id,
  cooling_id, name, parameters }`, `PlanRequest { clearance_z_mm }`,
  `SimulateRequest { voxel_resolution_mm }`, `PostRequest { post_id }`,
  `GeometryRefRequest`, `SolidRefRequest`, `OperationUpdate`.
- Riuso dei modelli core per le risposte (nessun DTO ridondante).

### `routes/catalog.py`
- `GET /catalog` → macchine/tools/materiali/cooling + versioni.

### `routes/projects.py`
- `POST /projects` (create), `GET /projects` (lista), `GET /projects/{id}`,
  `DELETE /projects/{id}`, `GET /projects/{id}/export`,
  `POST /projects/import` (upload `.antcam.json`), `POST /projects/{id}/undo`,
  `POST /projects/{id}/redo`.

### `routes/geometry.py`
- `POST /projects/{id}/geometry` (upload DXF/SVG) → `attach_geometry` +
  centraggio (stessa logica del controller desktop) → `RenderScene`.
- `POST /projects/{id}/solid` (upload STEP/STL) → `import_file_3d` → scena 3D.
- `GET /projects/{id}/scene` → `RenderScene` composto (setup+2D+3D+toolpath)
  con header `X-Scene-Fingerprint`.

### `routes/operations.py`
- `GET /projects/{id}/operations`, `POST /operations`, `PATCH` per
  enable/move/params, `DELETE`, `POST /duplicate`, `PUT` refs (2D e 3D).
- Tutte le mutazioni passano da `ProjectService` (undo-aware).

### `routes/toolpath.py` / `simulation.py` / `post.py`
- `POST /projects/{id}/plan` → `plan_snapshot` (con `solid_scene` se presente);
  `GET /plan`; `POST /simulate` → `SimulationReport`; `POST /post` → testo G-code
  (`Content-Disposition` download opzionale).

### `frontends/web/static/js/render_scene.js`
- `buildScene(renderScene) -> THREE.Group`: mappa `NodeKind`:
  - `LINE_STRIP`/`CLOSED_POLYLINE`/`CIRCLE_OUTLINE`/`TOOLPATH_STRIP`/`BOX_OUTLINE`
    → `LineSegments` con `BufferGeometry` concatenato;
  - `SOLID_BOX` → `Mesh` (`BoxGeometry`) traslucido con `EdgesGeometry`;
  - `MESH` (`RenderMesh`) → `Mesh` `BufferGeometry` (vertici+indici);
  - `GRID` → `GridHelper`; `POINT_SET` → `Points`.
- `userData.pickingId` per picking; colori dal grafo (RGBA float → THREE.Color).

### `frontends/web/static/js/viewport.js`
- `THREE.WebGLRenderer`, `PerspectiveCamera`, `OrbitControls`, `Raycaster`;
  selezione (click → picking id → `entity_picked`/`feature_picked`), fit-view,
  aggiornamento scena su cambiamento fingerprint.

### `frontends/web/static/js/api.js` / `app.js` / `panels.js`
- `api.js`: fetch wrapper JSON + multipart, gestione errori.
- `app.js`: stato (progetto, scena fingerprint, selezione), orchestratore.
- `panels.js`: rendering DOM dei pannelli (project/operations/parameters/
  toolpath/simulation) e azioni; nessun framework.

### `frontends/web/main.py`
- `main()`: costruisce `create_app()` e avvia `uvicorn.run` con `--host/--port`;
  import FastAPI lazy; subcomando CLI `antcam-rc2 web`.

## 10.8 Contratti API (riepilogo)

| Metodo | Path | Corpo/Query | Risposta |
| --- | --- | --- | --- |
| GET | `/api/v1/catalog` | — | `CatalogBundle` (id→record) |
| GET/POST | `/api/v1/projects` | `ProjectCreate` | `Project` / lista |
| GET/DELETE | `/api/v1/projects/{id}` | — | `Project` |
| POST | `/api/v1/projects/{id}/undo` `…/redo` | — | `Project` |
| POST | `/api/v1/projects/import` | file `.antcam.json` | `Project` |
| GET | `/api/v1/projects/{id}/export` | — | file JSON |
| POST | `/api/v1/projects/{id}/geometry` | file DXF/SVG | `RenderScene` |
| POST | `/api/v1/projects/{id}/solid` | file STEP/STL | `SolidScene` |
| GET | `/api/v1/projects/{id}/scene` | — | `RenderScene` (+ fingerprint header) |
| GET/POST | `/api/v1/projects/{id}/operations` | `OperationCreate` | lista / `Operation` |
| PATCH/DELETE | `/api/v1/operations/{op_id}` | `OperationUpdate` | `Operation` / 204 |
| POST | `/api/v1/projects/{id}/plan` | `PlanRequest` | `ToolpathPlan` |
| POST | `/api/v1/projects/{id}/simulate` | `SimulateRequest` | `SimulationReport` |
| POST | `/api/v1/projects/{id}/post` | `PostRequest` | `{ post_id, text }` |

## 10.9 Performance e velocità di esecuzione

- **Implementazione**: FastAPI + pydantic v2 riducono il codice di validazione a
  quasi zero; `TestClient` evita server reali nei test; frontend vanilla evita
  la build toolchain (nessun npm).
- **Runtime API**: endpoint CPU-bound su threadpool; scene servita in un solo
  JSON; file via multipart streaming; nessun polling (risposte sincrone).
- **Runtime frontend**: ricostruzione three.js solo su `X-Scene-Fingerprint`
  cambiato; buffer batched (1 draw call per pipeline); upload con `FormData`.
- **Budget** (target, da misurare a fine fase):
  - `GET /scene` per 5k entità < 50 ms (serializzazione già misurata in Fase 8);
  - `POST /plan` fixture piccola < 250 ms + overhead HTTP < 50 ms;
  - `POST /simulate` 20 ops @ 1 mm < 600 ms (budget Fase 8);
  - primo caricamento SPA < 1 s (statici locali); ricostruzione scena < 100 ms
    per 5k entità.

## 10.10 Test e QA

### Unit (puri)
- `test_web_schemas.py`: round-trip request/response, validazione pydantic,
  errori 422.

### Integration (TestClient, headless)
- `test_web_api_workflow.py`: `create project → upload DXF → add op → ref →
  plan → post → simulate → export/import`; undo/redo; upload STEP/STL; errori
  dominio mappati a status HTTP.
- `test_web_static.py`: file statici presenti, `index.html` referenzia i moduli
  JS e il mount SPA risponde.

### QA
- `ruff check`/`ruff format` su `src` e `tests`; `ty check src` con override per
  `frontends/web` (stub FastAPI/starlette se necessario).
- Coverage globale ≥ 70%; `frontends/web/api` ≥ 80% (i moduli statici JS sono
  esclusi dal coverage Python ma coperti da contract smoke).
- Anti-legacy esteso a `frontends/web` (nessun import da `antcam`/`antcam_rc1`).
- Wheel smoke: extra `web` installato separatamente; `import
  antcam_rc2.frontends.web` senza FastAPI installato non fallisce (lazy).

## 10.11 Ordine di implementazione (ottimizzato per velocità)

1. **Extra + skeleton API** — pyproject `[web]`, `app.py`/`dependencies.py`/
   `schemas.py`, `GET /health` + `/catalog`; test TestClient di bootstrap.
2. **Progetti + geometria** — `projects.py`, `geometry.py` (upload 2D/3D), scena
   composta con fingerprint; test workflow upload.
3. **Operazioni + refs** — `operations.py` (CRUD, refs 2D/3D); test undo/redo.
4. **Toolpath/simulazione/post** — `toolpath.py`/`simulation.py`/`post.py`
   (threadpool); test end-to-end completo.
5. **Frontend statico** — `index.html`, `render_scene.js` (traduzione grafo),
   `viewport.js` (OrbitControls + picking), `api.js`/`app.js`/`panels.js`.
6. **Entrypoint CLI** — `main.py` + `antcam-rc2 web`; smoke manuale browser.
7. **Hardening + QA** — anti-legacy, coverage, wheel smoke, OpenAPI check.

## 10.12 Rischi e mitigazioni

| Rischio | Mitigazione |
| --- | --- |
| FastAPI/uvicorn non installati nel CLI base | Extra `web` + import lazy; messaggio chiaro in `antcam-rc2 web` |
| Stato condiviso in-memory perso al riavvio | Documentato (mono-utente locale); export/import JSON esplicito |
| Endpoint CPU-bound bloccano il server | `asyncio.to_thread` su servizi thread-safe; mai chiamate sincrone nel loop |
| Multipart/upload grandi | `UploadFile` spooled su disco + limite dimensione; niente base64 |
| Picking three.js su linee/mesh non preciso | `Raycaster` con soglia `linePrecision`; id nel `userData`; fallback selezione da lista |
| Frontend senza build toolchain diventa disordinato | Moduli ES separati e responsabilità chiare (`render_scene` puro, `viewport`, `panels`); contract smoke sui file statici |
| Divergenza contratto API/frontend | OpenAPI come contratto; test API headless; header fingerprint per invalidation |
| Wheel non include gli statici | `[tool.hatch.build.targets.wheel]` include `frontends/web/static` come package-data (verifica smoke) |

## 10.13 Criteri di uscita

- `ruff`/`ty`/`coverage` verdi; coverage globale ≥ 70%, `frontends/web/api` ≥ 80%.
- `TestClient` end-to-end: progetto → import 2D/3D → operazioni → plan →
  simulate → post → export/import, con undo/redo e mappatura errori HTTP.
- `antcam-rc2 web` avvia uvicorn e serve `/` (SPA) + `/api/v1` + `/docs`.
- OpenAPI generata e coerente con gli schemi; header fingerprint presente.
- Frontend: viewport three.js mostra il `RenderScene`, picking funzionante,
  upload file, pannelli operazioni/parametri/toolpath/simulazione.
- Anti-legacy esteso a `frontends/web`; wheel smoke con extra `web`.

## 10.14 Checklist di completamento (da spuntare a fine fase)

**Stato: fase non ancora avviata.**

- [ ] Extra `[web]` + skeleton FastAPI (`app.py`, `dependencies.py`, `schemas.py`)
- [ ] Router catalog/projects/geometry (upload 2D+3D, scena + fingerprint)
- [ ] Router operations (CRUD, refs 2D/3D, undo/redo)
- [ ] Router toolpath/simulation/post (threadpool)
- [ ] Frontend statico (three.js: `render_scene.js`, `viewport.js`, pannelli)
- [ ] CLI `antcam-rc2 web` + entrypoint uvicorn
- [ ] Test API end-to-end + contract smoke statici
- [ ] Benchmark 10.9 registrati
- [ ] `ruff`/`ty`/`coverage`/anti-legacy/wheel smoke verdi
