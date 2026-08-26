# AntCAM RC2 — Fase 1: Geometria 2D & Import DXF/SVG

## 1.1 Obiettivo

Costruire il **layer geometrico 2D neutro** del core e i primi due importer (`DXF`, `SVG`) che producono un modello validato e normalizzato, consumabile dalla Fase 4 (toolpath engine) e dalla Fase 5 (UI):

- Primitives 2D tipizzate (`Point2`, `Vec2`, `Box2`) e curve (`LineSegment`, `Arc`, `Circle`)
- Contorni/percorsi compositi (`Contour`, `Path`) con proprietà geometriche (lunghezza, area, orientamento)
- Operazioni su geometria: bounding box, welding dei punti coincidenti, offset (pyclipper), boolean (shapely), orientamento contorni
- Trasformazioni affini (scale/translate/rotate/mirror) applicabili all'intera scena o a singole entità
- **`GeometryScene`**: modello neutro di output (entità su layer, unità, sorgente, tolleranze) + **diagnostica** (warnings/errori raccolti durante l'import)
- **Registry formati** con `import_file(path) -> GeometryScene` (dispatch per estensione, API pubblica della Fase 1)
- **Importer DXF** (ezdxf) e **SVG** (svgpathtools) con mappatura entità → primitives e validazione
- **CLI di verifica**: `antcam-rc2 import <file> --dump` per ispezione da terminale
- Fixture di test **miniature scritte a mano** in `tests/data/` (deterministiche, nessun file binario)

**Criterio di uscita**: `import_file()` carica un `.dxf` e un `.svg` di esempio in una `GeometryScene` validata (layer, unità risolte, bounding box corretti); tutte le primitive/operazioni hanno test unit; curve non-native (bezier/ellissi/spline) sono **approssimate a polyline campionata con tolleranza** (mai rifiutate); coverage ≥ 70% mantenuto; nessun import verso i package legacy.

## 1.2 Struttura dei file da creare

```
src/antcam_rc2/
├── PLAN.md                        ← piano generale (fasi 0–10)
├── PLAN_FASE_1.md                 ← questo documento
├── pyproject.toml                 ← + shapely, pyclipper, ezdxf, svgpathtools
└── src/antcam_rc2/
    ├── __main__.py                ← + subcomando `import` (CLI verifica)
    └── core/
        ├── geometry/
        │   ├── __init__.py        ← re-export API pubblica
        │   ├── primitives.py      ← Point2, Vec2, Box2 (frozen)
        │   ├── curves.py          ← LineSegment, Arc, Circle
        │   ├── paths.py           ← Contour, Path (percorsi composti)
        │   ├── ops.py             ← bbox, weld, orient, offset, boolean
        │   ├── transform.py       ← Affine2D (scale/translate/rotate/mirror)
        │   └── sampling.py        ← campionamento curve → polyline (tolleranza)
        └── io/
            ├── __init__.py        ← import_file(), FormatRegistry
            ├── scene.py           ← GeometryScene, SceneLayer, SourceInfo
            ├── diagnostics.py     ← ImportDiagnostics (warnings/errors)
            ├── registry.py        ← dispatch per estensione, errori formato
            ├── dxf.py             ← importer ezdxf
            └── svg.py             ← importer svgpathtools
tests/
├── data/
│   ├── mini_square.dxf            ← miniature handmade (ASCII DXF)
│   ├── mini_polyline_bulge.dxf    ← LWPOLYLINE con bulge (arco)
│   ├── mini_arc_circle.dxf        ← ARC + CIRCLE + LINE, unità mm
│   ├── mini_block_insert.dxf      ← blocco definito + INSERT
│   ├── mini_rect.svg              ← <rect> + <line> + unità mm
│   ├── mini_path.svg              ← <path> con M/L/H/V/A/C/S, viewBox
│   └── mini_groups_transform.svg  ← <g> annidati + transform/translate/scale
├── unit/
│   ├── test_primitives.py
│   ├── test_curves.py
│   ├── test_paths.py
│   ├── test_ops.py
│   ├── test_transform.py
│   ├── test_sampling.py
│   ├── test_scene.py
│   ├── test_diagnostics.py
│   ├── test_dxf_import.py
│   └── test_svg_import.py
└── integration/
    ├── test_import_pipeline.py    ← file → scene validata (bbox/area/layer)
    └── test_no_legacy_imports.py  ← esteso: geometry/io incluse nel grep
```

## 1.3 Dettaglio per modulo

### `pyproject.toml`
- `dependencies` aggiunte: `shapely`, `pyclipper`, `ezdxf`, `svgpathtools` (pyclipper **non è incluso in shapely**: shapely = wrapper GEOS, pyclipper = Clipper; servono entrambi — offset esatto stile CAM su path aperti/chiusi con join types)

### `core/geometry/primitives.py`
- `Point2` (frozen dataclass: `x: float`, `y: float`) con `distance_to`, `__add__/__sub__`, `__eq__` con tolleranza
- `Vec2` (frozen dataclass) con `length`, `normalize`, `dot`, `cross`, `rotate`
- `Box2` (frozen dataclass: `min_x, min_y, max_x, max_y`) con `width/height`, `center`, `union`, `contains`, `expand(margin)`
- Costanti condivise riusate da `core/units.py`: `TOLERANCE_MM`, `SCALE_EPS`

### `core/geometry/curves.py`
- `LineSegment(start: Point2, end: Point2)`: `length`, `point_at(t)`, `reverse`, `transform`, `to_polyline` (2 punti)
- `Arc(center, radius, start_angle, end_angle, ccw: bool)`: `length` (arco), `point_at(t)`, `reverse`, `transform`, `sweep_angle`, determinismo su normalizzazione angoli
- `Circle(center, radius)`: `length`, `point_at(t)`, `reverse`, `transform`
- ABC comune `Curve2` con `start/end`, `length()`, `to_polyline(tolerance)`, `transform(affine)`, `reverse()`

### `core/geometry/paths.py`
- `Contour(segments: list[Curve2])`: `closed` (bool), `is_closed()` (verifica geometrica: end≈start dell'ultimo), `length`, `area()` (Green), `orientation()` (CCW/CW), `reverse()`, `transform`, `to_polyline`
- `Path`: insieme di `Contour` (più loop), `bbox`, `area` (somma algebrica per fori), `layer`
- Validazione alla costruzione: segmenti consecutivi con punti di giunzione coerenti (warning se gap > tolleranza)

### `core/geometry/ops.py`
- `bounding_box(entities) -> Box2`
- `weld(points/entities, tolerance)`: unisce punti entro tolleranza (base per chiusura contorni)
- `orient(contour)`: normalizza loop esterni CCW / fori CW (convenzione interna documentata)
- `offset(contour|path, distance, join_type, miter_limit)`: **pyclipper** `ClipperOffset` (join square/round/miter) — core per profiling/pocketing futuri
- `boolean(subject, clip, op)`: **shapely** union/difference/intersection/xor su poligoni 2D
- Conversioni bidirezionali `shapely.geometry ↔ primitives` (LinearRing/Polygon ↔ Contour) confinate qui (shapely mai esposto fuori da `ops.py`)

### `core/geometry/transform.py`
- `Affine2D` (matrice 3×3 omogenea): `identity`, `translate`, `scale`, `rotate`, `mirror_x`, `mirror_y`, `compose`
- `apply(affine, entity)` su tutte le primitive/contour/path; catena applicata in ordine corretto
- Test: round-trip, composizione non commutativa (ordine documentato), invariance bbox

### `core/geometry/sampling.py`
- `sample_curve(curve, tolerance_mm) -> list[Point2]`: campionamento adattivo
- `approximate_to_polyline(entity, tolerance_mm) -> list[Point2]`: usato per bezier/ellissi/spline sia in SVG che in DXF (**mai rifiutate**, solo approssimate)
- Tolleranza di default da `TOLERANCE_MM` scaling (relativa alla grandezza del disegno via bbox)

### `core/io/scene.py`
- `GeometryScene` (Pydantic v2): `source: SourceInfo`, `units: UnitSystem`, `layers: list[SceneLayer]`, `entities: list[Contour|Curve2]`, `diagnostics: ImportDiagnostics`, `bbox: Box2`
- `SceneLayer`: `name`, `color` (opzionale), `entities`
- `SourceInfo`: `format` (dxf|svg), `path`, `original_units` (se dichiarate), `version`
- `normalize()`: applica conversione unità → mm, orientamento contorni, welding opzionale, ricalcolo bbox
- Metodi: `add_entity`, `layer(name)`, `iter_entities()`, `bbox`

### `core/io/diagnostics.py`
- `ImportDiagnostics` (Pydantic): `warnings: list[str]`, `errors: list[str]`
- `add_warning/add_error` con categoria + entità referenziata (es. `entity=10, layer=0: SPLINE approssimata`)
- `has_errors` (blocca la validazione se vero), `summary()` testuale per CLI

### `core/io/registry.py`
- `FormatRegistry`: `register(ext, importer_callable)`, `extensions()` (per dialog UI futura)
- `import_file(path) -> GeometryScene`: risolve estensione (case-insensitive), errori `FileNotFoundError` → `UnsupportedFormatError` se estensione sconosciuta; `GeometryError` se l'import fallisce
- **Point of entry unico** della Fase 1 (usato da CLI e dalla futura UI)

### `core/io/dxf.py` (ezdxf)
- Lettura: `ezdxf.readfile` con `recover=True` (recupero entity corrotte → diagnostics)
- Mappatura entità:
  - `LINE` → `LineSegment`
  - `ARC` → `Arc`
  - `CIRCLE` → `Circle`
  - `LWPOLYLINE` / `POLYLINE` → `Contour` (bulge ≠ 0 → `Arc`; vertici duplicati → warning)
  - `ELLIPSE` / `SPLINE` → **`approximate_to_polyline`** (mai rifiutate)
  - `INSERT` (block ref) → esplosione del blocco (se definito; altrimenti warning + skip)
  - `HATCH`, `TEXT`, `MTEXT`, `DIMENSION`, `POINT` → warning + skip (fuori scope 2D CAM in Fase 1)
- Unità: header `$INSUNITS` (0=unitless → warning + default mm) → `UnitSystem`; conversione in `normalize()`
- Layer: `entity.dxf.layer` → `SceneLayer` (auto-creazione)
- Ogni scarto/approximazione registrato in `diagnostics`

### `core/io/svg.py` (svgpathtools)
- Parsing: `svgpathtools.parse_path` per `<path d>`; tree traversal per gli altri elementi
- Mappatura:
  - `<path d>` → segmenti `Line`/`Arc` diretti; `CubicBezier`/`QuadraticBezier` → **`approximate_to_polyline`**
  - `<line>`, `<polyline>`, `<polygon>` → `LineSegment`/`Contour`
  - `<rect>`, `<circle>`, `<ellipse>` → `Contour`/`Circle` (ellisse → polyline campionata)
  - `<g>` ricorsivo con **accumulo transform** (matrix/translate/scale/rotate/skewX/skewY)
- Unità: `width/height` + `viewBox` + attributi `unit` (mm/in/px; px = 96dpi) → `UnitSystem`; viewBox non presente → default unitless + warning
- Layer/raggruppamento: attributi `id`/`inkscape:label` usati come nome layer quando presenti, altrimenti layer default `"0"`
- Ogni approssimazione registrata in `diagnostics`

### `core/io/__init__.py`
- Re-export: `import_file`, `GeometryScene`, `SceneLayer`, `SourceInfo`, `ImportDiagnostics`, `UnsupportedFormatError`, `GeometryError`

### `__main__.py` (CLI)
- Nuovo subcomando `antcam-rc2 import <file> [--dump]`:
  - esegue `import_file`, stampa summary (`format`, `units`, `layers`, `entities`, `bbox`)
  - `--dump`: elenco dettagliato entità per layer (tipo, lunghezza, punti chiave) + diagnostics
  - exit code non-zero se `diagnostics.has_errors`

### Test fixtures (`tests/data/`)
- Miniature **scritte a mano** in testo (DXF ASCII minimale, SVG XML esplicito) — niente file generati/da Internet:
  - `mini_square.dxf`: 4 LINE chiuse su layer `0`, unità mm → contorno chiuso area attesa
  - `mini_polyline_bulge.dxf`: LWPOLYLINE con un bulge (semicerchio) → Contour con Arc
  - `mini_arc_circle.dxf`: LINE + ARC + CIRCLE separati → entità distinte, lengths attese
  - `mini_block_insert.dxf`: `BLOCK` rettangolare + `INSERT` in offset/rotazione → esplosione attesa
  - `mini_rect.svg`: `<rect width="10" height="20" unit="mm">` → Contour 10×20 mm
  - `mini_path.svg`: `<path d="M0,0 L10,0 H10 V10 A5,5 0 0 0 0,10 C0,0 20,0 0,0 ...">` → Line/Arc/Polyline mappati
  - `mini_groups_transform.svg`: `<g transform="translate(50,50) scale(2)">` con `<g>` annidati → bbox atteso dopo trasformazione
- Ogni fixture ha valori attesi hard-coded nel test (area, lunghezza, bbox) → **regressione semantica**

## 1.4 QA Tooling

- Ruff format + lint (E,W,F,I,B,UP, line-length 120) sul nuovo codice
- `ty check` — le conversioni shapely↔primitives tipizzate con protocolli locali in `ops.py`
- Pytest con coverage (`fail_under = 70` mantenuto); nuovi test in `unit/` e `integration/`
- `test_no_legacy_imports.py` **esteso** per coprire `core/geometry/` e `core/io/` (grep su tutto il tree `src/antcam_rc2/` già esistente, quindi nessuna azione extra oltre a conferma)
- Verifica manuale: `antcam-rc2 import tests/data/mini_square.dxf --dump`

## 1.5 Ordine di implementazione

1. `pyproject.toml` + install dipendenze (shapely, pyclipper, ezdxf, svgpathtools)
2. `geometry/primitives.py` (+ test) → `curves.py` (+ test) → `paths.py` (+ test)
3. `geometry/transform.py` (+ test) → `sampling.py` (+ test)
4. `geometry/ops.py` (weld/orient/bbox; poi offset pyclipper; poi boolean shapely) (+ test)
5. `io/diagnostics.py` (+ test) → `io/scene.py` (+ test) → `io/registry.py`
6. `io/dxf.py` + fixtures DXF (+ test unit per mappatura)
7. `io/svg.py` + fixtures SVG (+ test unit per mappatura)
8. `io/__init__.py` re-export + `import_file` pipeline
9. CLI `import` in `__main__.py` + test integration
10. `test_import_pipeline.py` (file → scene validata) + estensione anti-legacy
11. QA completo: ruff, ty, pytest, coverage, CI job rc2 aggiornato se necessario

## 1.6 Rischi e mitigazioni

| Rischio | Mitigazione |
|---|---|
| pyclipper/shapely incompatibili con Python 3.13 | Verificati in fase di setup: shapely 2.1.2, pyclipper 1.4.0, ezdxf 1.4.4, svgpathtools 1.7.2 importano e girano |
| svgpathtools non espone `__version__` | Verifica import tramite istanza (`svgpathtools.Path`), non attributo modulo |
| DXF con `recover=True` cambia semantica di alcuni entity | Tracciare in diagnostics le entity recuperate; test su `mini_block_insert.dxf` |
| Bezier/ellissi/spline con molti punti → scene pesanti | Campionamento adattivo con tolleranza proporzionale a bbox (`sampling.py`), non a passo fisso |
| Shapely esposto accidentalmente nell'API pubblica | Conversioni confinate in `ops.py`; l'API pubblica espone solo primitive native |
| Bulge DXF con segno/parametrizzazione ambigua | Regole documentate in `dxf.py` (bulge positivo = CCW); test dedicati su `mini_polyline_bulge.dxf` |
| Unità miste/dichiarate male nei file | `normalize()` centralizza conversione → mm; unitless → default mm + warning in diagnostics |
| Coordinate fuori dalla work area Makera Z1 (200×200×100) | La Fase 1 importa e normalizza senza troncare (validazione stock avviene in Fase 3) — solo warning se bbox > 200 mm |
| Coverage < 70% per via di branch di errore | `exclude_also` su path irraggiungibili; test mirati su ogni branch diagnostics/errore |

## 1.7 Stato di implementazione (aggiornato)

**Completato** — tutti i punti 1.1–1.5 sono stati implementati e verificati:

- ✅ `pyproject.toml` + dipendenze (shapely 2.1.2, pyclipper 1.4.0, ezdxf 1.4.4, svgpathtools 1.7.2)
- ✅ `core/geometry/` completo: primitives, curves, paths, transform (Affine2D + `apply` con overload), sampling, ops (offset pyclipper, boolean shapely)
- ✅ `core/io/`: diagnostics, scene, dxf, svg, registry (`import_file`), `__init__` re-export
- ✅ Fixture DXF (4) e SVG (3) in `tests/data/` + test unit dedicati (13 DXF, 9 SVG, 8 registry)
- ✅ CLI `antcam-rc2 import <file> [--dump]` + test integration
- ✅ `test_import_pipeline.py` + anti-legacy esteso (grep già copriva tutto il tree)

**QA finale:**

| Strumento | Esito |
|---|---|
| `ruff check` (E/W/F/I/B/UP, 120) | ✅ All checks passed |
| `ruff format --check` | ✅ 62 file già formattati |
| `ty check` (con `VIRTUAL_ENV` settato) | ✅ All checks passed |
| `pytest tests/` | ✅ 226 passed |
| `coverage report` (fail_under=70) | ✅ 84% |

Nota: `ty` eseguito da sottocartelle richiede `VIRTUAL_ENV` esplicito (`$env:VIRTUAL_ENV = "C:\TheAntFarmRepo\antcam\.venv"`), quirk preesistente del progetto.