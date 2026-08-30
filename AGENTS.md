# AntCAM — Istruzioni per l'agente opencode

Questo file è caricato automaticamente da opencode (vedi `opencode.json` > `instructions`).

## 1. Scope del progetto

- Il progetto attivo è **esclusivamente** `src/antcam_rc2/`.
- Ignora completamente le altre versioni/cartelle legacy presenti nel repo (es. `src/antcam/`, `antcam_demo/`, `project/`, `src/antcam_rc1/` se presente, ecc.). Non leggerle, non modificarle, non prenderle come riferimento per implementazione o test a meno che l'utente non lo richieda esplicitamente.
- Tutta l'implementazione, i test e la documentazione rilevante sono sotto `src/antcam_rc2/`:
  - codice: `src/antcam_rc2/src/antcam_rc2/`
  - test: `src/antcam_rc2/tests/`
  - tool/script: `src/antcam_rc2/tools/`
  - config: `src/antcam_rc2/pyproject.toml`, `src/antcam_rc2/ty.toml`

## 2. Ambiente Python

- Ambiente virtuale: `.venv/` nella root del repo (`C:\TheAntFarmRepo\antcam\.venv`).
- Su Windows, attivazione cmd: `.venv\Scripts\activate.bat` (oppure `.\.venv\Scripts\Activate.ps1` per PowerShell, ma il terminale di riferimento è **cmd.exe** — vedi sotto).
- Runtime: Python 3.13. Usa `uv` come package manager quando serve (`uv pip`, `uv run`).
- Non creare altri venv e non reinstallare dipendenze globalmente senza richiesta.

## 3. Terminale

- Il terminale è **cmd.exe di Windows** (`"shell": "cmd.exe"` in `opencode.json`).
- Evita sintassi bash/zsh (`&&` va bene anche in cmd, ma evita `;`, `||` con semantica bash, `export`, `source`, `ls -la` ecc.).
- Preferisci comandi compatibili cmd / PowerShell quando possibile, oppure usa `python -c` / `py` per operazioni inline.
- I path sono Windows (`C:\...` con backslash). Quota i path con spazi con doppi apici.
- `"tail"` `"head"` non sono comandi validi, non usarli, usane altri compatibili con windows cmd.

## 4. Test — quando lanciarli

- **NON lanciare l'intera suite di test dopo ogni modifica al codice.**
- Lancia solo i test rilevanti/minimali per la modifica corrente (es. `uv run pytest src/antcam_rc2/tests/test_<modulo>.py -k <filtro>` o `uv run pytest src/antcam_rc2/tests/test_xyz.py::test_foo`).
- La suite completa (`uv run pytest`, `coverage`, ecc.) va lanciata solo su richiesta esplicita dell'utente.
- Stesso principio per `ruff check`, `ty check`: usali mirati, non sull'intero repo ad ogni edit.

## 5. Piano e documentazione fasi

- Il piano di sviluppo e la descrizione delle varie fasi/migliorie si trovano sotto `src/antcam_rc2/` sotto forma di file Markdown:
  - `src/antcam_rc2/PLAN.md` — visione, stack, architettura, fasi 0-10
  - `src/antcam_rc2/PLAN_FASE_0.md` … `PLAN_FASE_10.md` — dettaglio per fase
  - `src/antcam_rc2/PLAN_FASE_4_REVISION.md`, `PLAN_PRO_FIXTURE.md`, `GL_SCENE_FIX.md`, `MOUSE_CONTROLS.md` — revisioni/fix specifici
  - `src/antcam_rc2/README.md` — overview rc2
- Consulta sempre questi file prima di pianificare o implementare una fase. Sono la fonte di verità per priorità, criteri di uscita e contratti core/UI.

## 6. Git — divieto di commit/push/branch automatici

- **NON fare `git commit`, `git push`, `git branch`, `git checkout -b`, `git switch -c`, `git tag` se non su esplicita richiesta dell'utente.**
- Configurazione in `opencode.json` > `permission.bash` imposta queste operazioni su `ask` — rispetta sempre la richiesta di conferma.
- Puoi usare liberamente `git status`, `git diff`, `git log`, `git show` per ispezione in sola lettura.

## 7. Altre convenzioni

- Core ↔ UI totalmente separati: il core (`src/antcam_rc2/src/antcam_rc2/core/`) non importa mai `PySide6`.
- Pydantic v2 per modelli, `uv` + `ruff` + `ty` + `pytest` per QA.
- Mantieni lo stile esistente; non riformattare file non toccati dalla task corrente.
- Dopo aver modificato `opencode.json` o file sotto `.opencode/`, ricorda all'utente di **riavviare opencode** (la config è caricata solo all'avvio).
