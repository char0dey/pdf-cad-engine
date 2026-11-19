# pdf-cad-engine

PDF ↔ DXF conversion engine used by [PDFConvert](https://pdfconvert2.com): extracts vector
drawings and text from PDF into DXF, renders DXF drawings to PDF, and produces PNG previews.
It runs as a separate process with a small, stable command-line contract.

- Version: **1.4.0** (`cad_engine.__version__`, `pyproject.toml`)
- Licence: **AGPL-3.0-or-later** (see [LICENSE](LICENSE)), © 2026 Quazar Digital OÜ
- Python ≥ 3.12

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # exact pins (server installs), OpenCV included
.venv/bin/pip install -e .                  # or: pip install .
.venv/bin/python -I -m cad_engine --version # -> 1.7.0
```

Development: `.venv/bin/pip install -e '.[dev,raster]'` (pytest, ruff, OpenCV).

OpenCV (`opencv-python-headless`, ~60 MB) is an **optional** extra `[raster]`, used only by
`pdf2dxf --raster-fallback`. `requirements.txt` pins it, so servers get it; a plain
`pip install .` does not. Without it the engine works and `--raster-fallback` reports the
warning `raster_fallback_unavailable` in the meta instead of tracing (see below).

## Command-line contract

```
python -I -m cad_engine <command> --in <file> [--out <file>] [--preview <png>]
                        [--meta <path>] [--budget-ms N] [--tmp <dir>]
                        [pdf2dxf options] [dxf2pdf options] [--light]
python -I -m cad_engine --version          # prints "1.7.0"
cad-engine ...                             # console script, same arguments
```

| Command       | Input | Output (`--out`)  | Notes |
|---------------|-------|-------------------|-------|
| `pdf2dxf`     | PDF   | DXF (R2010, mm; `--cut`: R2000) | `--preview out.png`: PNG of the extracted geometry |
| `dxf2pdf`     | DXF   | PDF (1 page; + layouts) | ASCII or binary DXF; damaged files are recovered |
| `probe-pdf`   | PDF   | —                 | pages, `vector_paths` / `vector_segments` of all pages (1.6), `has_vectors` / `text_entities` on the first 5 pages, `suggested_purpose` (1.7) |
| `probe-dxf`   | DXF   | —                 | `entities`, `empty` / `empty_reason`, `dxf_version`, `layers`, `layouts`, the content scan; `--light`: the scan only |
| `preview-dxf` | DXF   | PNG               | model space, longest side 1400 px, white background |

Options:

- `--meta <path>` — also write the JSON meta to a file (`-` = stdout only).
- `--budget-ms N` — time budget, default 90 000 ms. Checked per page and every
  1 000 entities (200 while rendering DXF), plus a `SIGALRM` interval timer. A native
  call that never returns to Python cannot be interrupted from inside the process, so
  the caller should still kill the process a few seconds after the budget (the engine
  dumps its stacks to stderr 4 s after the budget to make such hangs diagnosable).
- `--tmp <dir>` — directory for temporary files (created if missing; sets `TMPDIR`).

### pdf2dxf options (1.1)

All off by default: without them the output is identical to engine 1.0. The caller passes
the ones it wants explicitly. Using a pdf2dxf option with another command is a usage error
(exit 5), like `--preview`.

| Flag | Effect |
|------|--------|
| `--arcs` | A run of consecutive cubic Béziers of one contiguous subpath that lies on one circle (every sample point within 0.5 % of the radius, and of the chord for short curves) becomes one entity: a closed run with a full 360° turn → `CIRCLE` (e.g. the 4 Béziers of a PDF circle); otherwise → `ARC`, counter-clockwise from `start_angle` to `end_angle` as DXF requires (1 Bézier of 90° → ARC 0°–90°, 2 Béziers of 180° → one ARC). Ellipses (axis ratio ≠ 1), S-curves and other curves stay `SPLINE`. |
| `--hatch-fills` | (1.7) A fill-only path becomes the area the PDF paints — its fill rule applied (`cad_engine/fill_regions.py`: nonzero or even-odd, so overlapping letters of one path stay filled and holes stay holes) — as one solid `HATCH` per polygon of that area (pattern `SOLID`, colour BYLAYER on its `FILL_RRGGBB` / PDF layer, which keeps the fill colour), with polyline boundaries (outline + holes; curves flattened within 0.01 paper mm), **plus** one closed outline per subpath on `OUTLINE_RRGGBB` (or the PDF layer): a closed `LWPOLYLINE` for a subpath of lines, a `CIRCLE` for a whole circle with `--arcs`, otherwise one closed `SPLINE` through the same control points (lines as straight Béziers); fill outlines are not cut into arcs. Text drawn as glyph outlines thus comes out filled and outlined. Paths with both fill and stroke stay outlines. Up to 1.6 a HATCH had one loop per subpath with spline edges and odd parity: overlaps became holes, and cutting programs (LightBurn, Fusion, Inkscape, LibreCAD) read no outline. |
| `--linetypes` | (1) Runs of ≥ 4 consecutive single-segment subpaths of one path that are collinear (within 0.01 mm), advance in one direction, have near-equal dashes and gaps (±20 %; the first and the last dash may be shorter) and dashes/gaps ≤ 25 mm → one `LINE` from the start of the first dash to the end of the last, linetype `DOT` when the dash is ≤ 0.6 mm and not longer than the gap, else `DASHED`. A dashed curve is split into straight runs that stay within 0.01 mm. (2) A path stroked with a PDF dash array (`[6 3] 0`) keeps its geometry with linetype `DASHED`/`DOT` (polylines get the PLINEGEN flag). In both cases the entity linetype scale (`ltscale`) makes one pattern repeat equal to the PDF period (dash + gap). Segments that do not form such a run stay as they are. `DASHED`/`DOT` are the ezdxf standard linetypes in the document. |
| `--merge` | Drops polyline vertices that lie within 0.01 mm on the line between their neighbours (and zero-length steps); joins consecutive Béziers of one subpath into one exact `SPLINE` (triple interior knots); turns a filled contour (fill-only subpath with curves, e.g. a glyph) into one `SPLINE` (straight edges as degree-elevated segments) instead of alternating polylines and splines; drops exact duplicates (same type, geometry, layer, colour, lineweight, linetype). The picture does not change (preview pixel difference < 1 % in the tests); entity counts drop ≥ 10 % on the sample plan and ≥ 30 % on the floorplan PDF. |
| `--scale asis\|1-50\|1-100\|1-200` | Drawing scale (default `asis`: 1 pt = 25.4/72 mm). Coordinates, text heights, page gaps and linetype scales are multiplied by 1, 50, 100 or 200; lineweights are not scaled. A 100 mm line on paper at `1-100` → 10 000 mm. |
| `--units mm\|inch` | DXF units (default `mm`). `inch` divides the millimetre values by 25.4 and sets `$INSUNITS = 1` (inches) and `$MEASUREMENT = 0`; `mm` sets `$INSUNITS = 4` and `$MEASUREMENT = 1` (1.7: always both — Inkscape's "Read from file" scales by `$MEASUREMENT` alone). 72 pt at `asis` + `inch` → 1.0. |
| `--cut` | (1.7) Closed contours for cutting programs (CNC, laser, plasma, plotter), AutoCAD R2000 (`AC1015`); `--arcs`, `--hatch-fills`, `--linetypes` and `--merge` are ignored. Fills (with or without stroke; the paper — a white fill over ≥ 95 % of the page — skipped): the painted area of each path (fill rule applied, as `--hatch-fills`) in drawing order — a later fill covers the part of earlier fills of other colours under it, a white fill (every channel ≥ 250) over colour cuts out of it and stays a contour only where nothing coloured is under it; per colour the union is written as closed `LWPOLYLINE`s (each outline and each hole) on `CUT_RRGGBB` with the nearest ACI colour (R2000 has no true colour; 0, 7 and 255 excluded; the exact RGB is in the name). Strokes without fill: every subpath one `LWPOLYLINE` (open or closed as in the PDF), dashes stay separate solid segments. Curves are flattened with a chord error ≤ 0.01 paper mm (`FLATTEN_TOL_MM`, scaled by `--scale`). No `HATCH`, `SPLINE`, `ARC`, `CIRCLE`, `ELLIPSE` or linetypes. Real text stays `TEXT` (counted in `cut_text_kept`: it will not be cut). Traced pages as without `--cut`. Nothing to cut (only the paper) → exit 2 `empty_drawing`. The preview shows the contours as lines (≥ 1.5 px), no fills. |
| `--raster-fallback` | Pages without vectors (nothing extracted: no visible paths, no visible text) that contain images are rendered at 150 dpi in grey (lower for pages over 40 Mpx), binarised (halfway between Otsu's threshold and the paper grey), and the outlines of the dark areas are traced with OpenCV (`findContours` → `approxPolyDP`, 1 px; specks under 8 px dropped) → closed `LWPOLYLINE`s on layer `TRACED` (colour 7) in that page's slot. Their indices (0-based) go to `traced_pages`. Strokes come out as thin closed outlines, not centre lines; no TEXT. A scan-only PDF then converts (exit 0, `has_vectors: false`); a mixed PDF gets vectors on its vector pages and tracing on its scanned pages. The 500 000 entity limit applies. Without OpenCV: one line on stderr, `"raster_fallback_unavailable"` in `warnings`, the page stays empty (a scan-only PDF still exits 2 `no_vectors`). |

The PNG preview (`--preview`) is drawn from the written entities: arcs and circles, solid
hatches (true colour, odd parity) and linetype dash patterns included.

### dxf2pdf options (1.1)

Defaults reproduce engine 1.0. Using them with another command is a usage error (exit 5).

| Flag | Effect |
|------|--------|
| `--page auto\|a4\|a3\|a2\|a1` | Paper size of the model-space page (default `auto`: A4, or A3 for drawings over 20 m or 5 000 entities). Fixed sizes: A4 297×210, A3 420×297, A2 594×420, A1 841×594 mm; orientation still follows the extents (portrait drawing → portrait page). Content is fitted with 10 mm margins. |
| `--colors color\|mono` | `color` (default) keeps the colours (white → black); `mono` draws every entity black on white (ezdxf `ColorPolicy.BLACK`), lineweights kept. |
| `--layouts` | After the model-space page, one page per paper-space layout in tab order that has content: paper-space entities, or viewports that show model space (status > 0, not the "active" sheet viewport, top views — the rules of the ezdxf frontend), each in its own paper size from the layout's page setup (A4 landscape when it has none). Viewports show model space through their window (clipped). Paper space is drawn 1:1 in mm when it fits the printable area (and fills at least 25 % of it), otherwise fitted to the page. A layout whose viewports look at an empty part of model space becomes a blank sheet (as a CAD program would plot it), so `pages` = 1 + the layouts `probe-dxf` reports. A layout that fails to render is skipped with the warning `layout_skipped:<name>`. An empty model space (no entities, no extents, or only underlays / images / xref inserts) gives (1.4) a PDF of the sheets that draw something of their own — a paper-space entity other than a viewport, with extents — and no model page (`model_blank`); sheets with viewports alone are left out, as they would show the empty model. Without such sheets, or without `--layouts`, an empty model space is refused as before. |

**stdout** carries exactly one line: the JSON meta, printed at the end of every run,
whatever the exit code (except `--version`, which prints the version). Everything else,
library warnings included, goes to **stderr**. Outputs are written as `<file>.part` and
renamed on success; on any failure the outputs are removed (no partial results).

### Exit codes

| Code | Meaning | `code` in meta |
|------|---------|----------------|
| 0 | success | `null` |
| 2 | unusable input | `no_vectors`, `empty_drawing`, `aec_objects`, `external_refs_only`, `too_many_pages`, `dwg_not_dxf`, `too_complex`, `unsupported_input` |
| 3 | time budget exceeded | `budget_exceeded` |
| 4 | memory limit hit | `memory_limit` |
| 5 | any other failure: missing/unreadable input, usage error, bug (traceback on stderr) | `engine_error` |

Input error codes:

- `no_vectors` — `pdf2dxf`: no vector paths and no visible text on any page (scans; invisible
  OCR text does not count).
- `too_many_pages` — `pdf2dxf`: more than 50 pages (checked before converting).
- `too_complex` — `pdf2dxf`: more than 500 000 DXF entities; `pdf2dxf` and `probe-pdf` (1.3): a page
  whose paths would take more than ~1.2 GB in PyMuPDF's `get_drawings()` (estimated from the
  operators in its content streams and forms, before reading the paths — `cad_engine/complexity.py`).
- `dwg_not_dxf` — the file starts with a DWG signature (`AC10xx`), checked before parsing.
- `aec_objects`, `external_refs_only`, `empty_drawing` (1.2) — `dxf2pdf`: nothing to draw in model
  space (no entities, no extents, only underlays / images / xref inserts, or — 1.3 — entities of which
  none is visible and no paper-space layout to print instead; 1.4 — with `--layouts` an empty model
  space is refused only when no sheet draws anything of its own), with the reason by
  priority: `aec_objects` when the content scan found AutoCAD Architecture / MEP / Civil 3D entity
  classes (their objects are not in the DXF), else `external_refs_only` when it found xrefs,
  underlays or images, else `empty_drawing`. `preview-dxf` refuses an empty drawing with
  `empty_drawing` only. The meta of the refusal carries the scan (`missing`).
- `unsupported_input` — not a PDF / not a DXF / password-protected PDF / unrecoverable DXF.

Probes report facts and exit 0; they exit 2 only for `unsupported_input`, `dwg_not_dxf` and (1.3,
`probe-pdf`) `too_complex`
(`probe-pdf` reports `pages` and leaves the 50-page limit to the caller; `probe-dxf`
reports `empty: true` and `empty_reason` — the code `dxf2pdf` would refuse with — instead of failing).

### Content scan (1.2)

`probe-dxf` and `dxf2pdf` first scan the DXF in one pass over its group codes (ASCII or
binary, no document is built) for content a PDF of it cannot show:

- `xrefs` — blocks flagged as xref (70 & 4) or overlay (70 & 8) that are inserted at least once
  anywhere (model space, a paper-space layout, another block); a bound xref is an ordinary
  block, an xref nobody inserts does not count;
- `underlays` — PDF / DWF / DGN underlay definitions referenced (group 340) by an underlay entity;
- `images` — IMAGEDEF objects referenced by an IMAGE entity (two inserts of one image = one name);
- `aec` — product families of registered entity classes (281 = 1): `AEC_` / `AECS_` →
  `architecture`, `AECB_` → `mep`, `AECC_` → `civil`. A converter that does not know these
  objects keeps the classes and drops the objects; how many there were is unknown.

File names are base names (`C:\Projects\plan.dwg` → `plan.dwg`), unique, in order of
appearance. A scan that fails sets `scanned: false` and reports nothing — it never stops
the command. `probe-dxf --light` runs only the scan (for big files: ≈ 2 s for 40 MB on a
laptop, against ≈ 16 s for the full probe): `entities` is the scan's model-space count,
`empty` is true only when model space has nothing drawable (`ms_drawable == 0`) and — 1.4 — paper
space neither (the entities of every layout: in `ENTITIES` with group 67 = 1 and in the
`*Paper_Space…` blocks; viewports, underlays, images and xref inserts do not count); both stay
`null` for a file cut off before `EOF`.

### JSON meta

All keys are always present; `null` when not applicable to the command.

| Key | Type | Set by |
|-----|------|--------|
| `version` | string | all (`"1.7.0"`) |
| `command` | string | all |
| `pages` | int | `pdf2dxf`, `probe-pdf`, `dxf2pdf` (PDF pages written: 1 + layouts; the layouts alone with `model_blank`) |
| `entities` | int | `pdf2dxf` (DXF entities written), `dxf2pdf` / `probe-dxf` / `preview-dxf` (model space) |
| `layers` | int | `pdf2dxf` (layers used), DXF commands (layer table size) |
| `text_entities` | int | `pdf2dxf` (TEXT entities), `probe-pdf` (text spans on the first 5 pages) |
| `images` | int | `pdf2dxf`, `probe-pdf` — embedded images (ignored by the conversion) |
| `has_vectors` | bool | `pdf2dxf`, `probe-pdf` |
| `text_as_outlines` | bool | `pdf2dxf`, `probe-pdf` — no real text but vector drawings (text is glyph outlines) |
| `vector_paths` / `vector_segments` | int | `probe-pdf` (1.6) — paths (painting operators) and segments (`l c v y re`) of all pages, forms counted once per time drawn (`cad_engine/complexity.py`). The work `pdf2dxf` does grows with their sum: the service sends a PDF whose sum is over its threshold to the background. Written before the text and drawings are read, so also present in the meta of a run that spent its budget (exit 3) |
| `traced_pages` | int[] | `pdf2dxf` — 0-based pages traced by `--raster-fallback` (`[]` otherwise) |
| `recognized` | object | `pdf2dxf` — `{"circles", "arcs", "hatches", "linetypes", "merged"}` counts (all 0 with the flags off): CIRCLE / ARC / HATCH entities written, entities given a DASHED/DOT linetype, entities saved by `--merge` (joined pieces + dropped duplicates) |
| `scale` | string | `pdf2dxf` — `asis`, `1-50`, `1-100`, `1-200` |
| `units` | string | `pdf2dxf` — `mm`, `inch` |
| `purpose` | string | `pdf2dxf` (1.7) — `cut` with `--cut`, otherwise `cad` |
| `contours` | int | `pdf2dxf --cut` (1.7) — closed `LWPOLYLINE`s written for fill areas (outlines + holes) |
| `cut_text_kept` | int | `pdf2dxf --cut` (1.7) — real text spans written as `TEXT` (a cutting program will not cut them) |
| `fill_paths` / `stroke_paths` | int | `probe-pdf` (1.7) — visible fills without stroke (the paper not counted) / stroked paths, counted on the pages read while the suggestion is open: the first stroke, real text or image settles it and the rest is not read |
| `suggested_purpose` | string | `probe-pdf` (1.7) — `cut` for a PDF of fills only (≤ 5 pages, none too heavy to read, ≥ 1 fill, no stroke, no real text, no image), otherwise `cad`; null when the budget ran out first |
| `empty` | bool | `probe-dxf` — nothing to draw: the answer for `dxf2pdf --layouts` (1.4: an empty model space with sheets that draw something is not empty) |
| `empty_reason` | string | `probe-dxf` with `empty: true` — `aec_objects`, `external_refs_only` or `empty_drawing` |
| `scanned` | bool | `probe-dxf`, `dxf2pdf` — the content scan finished (`false`: it failed, no findings) |
| `missing` | object | `probe-dxf`, `dxf2pdf` — `{"xrefs", "underlays", "images": [file names], "aec": [families]}` |
| `ms_entities`, `ms_drawable` | int | `probe-dxf`, `dxf2pdf` — model-space entities seen by the scan, and those that draw something (not underlays, images or xref inserts) |
| `dxf_version` | string | DXF commands, e.g. `"AC1024"` |
| `page_size_mm` | [w, h] | `dxf2pdf` — model-space page, e.g. `[297.0, 210.0]` |
| `layouts` | string[] | `dxf2pdf` — paper-space layouts rendered as extra pages (`[]` without `--layouts`); `probe-dxf` — paper-space layouts with content (1.4: with an empty model space, the sheets the PDF will hold) |
| `simplified_hatches` | int | `dxf2pdf` (1.3) — hatches drawn as their outline because the pattern would produce more than 1 000 000 line segments (model space and rendered layouts, each insert counted) |
| `far_parts` | int | `dxf2pdf`, `preview-dxf` (1.5) — drawn parts left out because they lie more than 1000 times the drawing's size away from it, or have broken coordinates (not finite, or beyond 1e12); model space and rendered layouts together |
| `skipped_entities` | int | DXF commands that loaded the drawing (1.5.2) — entities left out of the drawing because ezdxf raised on them while rendering (an entity seen through two viewports counts twice); `probe-dxf` does not render: 0 |
| `entity_errors` | object[] \| null | DXF commands that loaded the drawing (1.5.2) — up to 10 different errors the safety net caught, the most frequent first: `{"stage": "extents" \| "render", "type": "SPLINE", "error": "ValueError: invalid order", "where": "ezdxf/acc/bspline.pyx:72 < …", "count": 38}` (`where`: the three innermost traceback frames); also on failure (exit 5) |
| `model_blank` | bool | `dxf2pdf` (1.3) — `true` when the PDF holds only the paper-space layouts, no model page: model space has entities but none visible, or (1.4) nothing to draw at all (`page_size_mm` is then the first layout's) |
| `warnings` | string[] | all — `[]` or e.g. `"raster_fallback_unavailable"`, `"layout_skipped:<name>"` |
| `ms` | int | all — wall time since start |
| `code`, `message` | string | on failure (exit ≠ 0) |

`has_vectors` (`pdf2dxf`) counts real vectors and text only, not traced outlines.

Examples (`pdf2dxf --arcs --hatch-fills --linetypes --merge --raster-fallback` on the sample
plan PDF; `dxf2pdf --layouts` on a plan with two paper-space layouts):

```json
{"version":"1.4.0","command":"pdf2dxf","pages":1,"entities":1273,"layers":8,"text_entities":0,"images":0,"has_vectors":true,"text_as_outlines":true,"traced_pages":[],"recognized":{"circles":1,"arcs":4,"hatches":10,"linetypes":179,"merged":3},"scale":"asis","units":"mm","empty":null,"dxf_version":null,"page_size_mm":null,"layouts":null,"scanned":null,"missing":null,"ms_entities":null,"ms_drawable":null,"empty_reason":null,"warnings":[],"ms":319,"code":null,"message":null}
{"version":"1.4.0","command":"dxf2pdf","pages":3,"entities":19,"layers":9,"text_entities":null,"images":null,"has_vectors":null,"text_as_outlines":null,"traced_pages":null,"recognized":null,"scale":null,"units":null,"empty":null,"dxf_version":"AC1024","page_size_mm":[297.0,210.0],"layouts":["Plan A3","Detail"],"scanned":true,"missing":{"xrefs":[],"underlays":[],"images":[],"aec":[]},"ms_entities":19,"ms_drawable":19,"empty_reason":null,"warnings":[],"ms":863,"code":null,"message":null}
```

## What the conversions do

**PDF → DXF** (`pdf2dxf`, PyMuPDF `get_drawings()` / `get_text()` → ezdxf), with the
options off: AutoCAD 2010 DXF in millimetres (1 pt = 25.4/72 mm, Y up, page rotation applied);
consecutive line segments → LWPOLYLINE; rectangles/quads → closed LWPOLYLINE; cubic
Béziers → degree-3 SPLINE with the same control points; fill-only paths → closed
LWPOLYLINE on `FILL_RRGGBB` layers (a white fill covering the whole page is treated as
paper and skipped); real text → TEXT (string, height = 0.72 × font size, rotation,
baseline origin); invisible text is skipped; text drawn as outlines stays outlines.
Layers: the PDF optional-content layer name when present (layers that are off in the PDF
are off in the DXF), otherwise `LINES_RRGGBB` / `FILL_RRGGBB` / `TEXT_RRGGBB` with a
true-colour layer colour. PDF line widths → nearest standard DXF lineweight. Multi-page
PDFs: all pages side by side along +X, gap max(20 mm, 10 % of the page width).
`$EXTMIN/$EXTMAX` and the initial view are set to the content extents. Images are ignored
(unless `--raster-fallback` traces an image-only page). The options above add recognition
of circles/arcs, hatches, linetypes, merging, scale/units and tracing.
The preview is drawn from the extracted primitives with PyMuPDF (sub-second for ~40 000
entities), not by re-rendering the DXF.

Fill areas (1.7, `cad_engine/fill_regions.py`, used by `--hatch-fills` and `--cut`): every subpath
of a path is flattened into a closed ring (cubic Béziers in n = ceil(sqrt(6·D / (8·tol))) equal
parameter steps, D the larger second difference of the control points, tol 0.01 paper mm); rings
whose bounding boxes do not touch are independent, and one simple ring is its own polygon (fast
path: 20 000 paths of one 4-Bézier ring in 0.7 s on an Apple M-series laptop,
`tests/test_fill_regions.py`); overlapping rings are noded and split into faces with shapely, and
a face is painted when the winding number of a point inside it is non-zero (nonzero) or odd
(even-odd). Rings under 1e-6 pt² are dropped.

**DXF → PDF** (`dxf2pdf`, ezdxf drawing add-on + PyMuPDF backend): model space on one page,
A4 or A3 (drawing larger than 20 m in `$INSUNITS`, or more than 5 000 entities) unless
`--page` fixes the size, orientation from the extents, fitted with 10 mm margins, white
background, colours kept (ACI 7 and true-colour white print black) or all black with
`--colors mono`, relative lineweights, invisible layers and entities skipped; with
`--layouts` the paper-space layouts follow as extra pages (pages merged with PyMuPDF). Damaged files go through `ezdxf.recover`; a file cut off at the end is
closed after the last complete object first, so everything that was read is rendered.
ezdxf bugs that real drawings hit are fixed in the engine process (`cad_engine/ezdxf_fixes.py`,
installed by `load_dxf`, only for the pinned ezdxf 1.4.5): since 1.5.1 a leader (MULTILEADER)
whose landing has length 0 is transformed without dividing by zero — ezdxf transforms it whenever a
block is exploded, so one such leader in an inserted block used to fail the whole drawing (exit 5).
Since 1.5.2 B-splines of degree 11 and up (AutoCAD allows up to 25) go to ezdxf's own pure-Python
B-spline code: its C extension takes orders below 12 only and refused such a spline with
`ValueError: invalid order`, failing the whole drawing (exit 5).
A safety net (1.5.2, `cad_engine/entity_guard.py`) keeps one entity ezdxf raises on from failing the
drawing: measuring the extents (model, sheets) tries the whole set first and, only when that raises,
entity by entity; rendering catches the error per entity in the frontend's `draw_entity`, which runs
for the entities inside block references too, so an entity in a block is left out on its own. What
was left out is in the meta (`skipped_entities`, `entity_errors`) and on stderr (one line per new
error). Engine errors, `MemoryError`, out-of-memory errors of the libraries and the time budget pass
through; when nothing at all is left to draw, that is exit 5 with the first error, not
`empty_drawing`.
The recording is written to PDF by `cad_engine/pdf_backend.py` (1.2), a subclass of ezdxf's
PyMuPDF backend with the same output (paths, colours, widths, one PDF layer per drawing
layer) but linear time: the page content goes into a new content stream every 500 paths
instead of one ever-growing string, the PDF layer of a drawing layer is looked up once,
and line segments (hatch patterns) are written as `m` / `l` operators with 0.001 pt
precision instead of one `Shape.draw_line` call each — drawings with tens of thousands of
paths or millions of hatch segments render 4–7 times faster.

## Limits and environment

- 50 PDF pages, 500 000 DXF entities, default budget 90 s.
- A PDF page is refused (`too_complex`) when its paths would take more than ~1.2 GB in
  `get_drawings()` (~1.25 KB per path + 0.37 KB per segment, measured with PyMuPDF 1.26.7).
- `probe-pdf` does not read a page of 20 000 paths or more with `get_drawings()` (1.6,
  `PROBE_FULL_READ_MAX_PATHS`; ~75 µs a path, 11 s on a page of 148 000 glyph outlines): such a page
  counts as drawing something (`has_vectors`), so the probe of a heavy page stays within its budget.
- A hatch pattern that would produce more than 1 000 000 line segments is drawn as the hatch's
  outline (`simplified_hatches`); AutoCAD's HPMAXLINES has the same number but counts pattern lines.
- A drawn part far from the rest of the drawing is left out (`far_parts`, 1.5): the drawing's core
  is the box between the 1st and 99th percentile of the drawn parts' bounding boxes, and a part
  reaching more than 1000 × the core's longer side beyond it — or with a coordinate that is not
  finite or beyond 1e12 — is not drawn and does not count for the page size. Kept, such a part
  shrinks the drawing to a dot; past 1e-9 fit scale ezdxf stops scaling and a coordinate above
  3.4e38 made PyMuPDF fail (exit 5). Ordinary drawings are not touched.
- Address space limited to 2 GB (`RLIMIT_AS`, Linux; macOS ignores it). Override with
  `CAD_ENGINE_MEMORY_MB` (0 disables).
- BLAS/OpenMP thread pools are limited to one thread (OpenCV too: `cv2.setNumThreads(1)`).
- ezdxf keeps a font cache in `$XDG_CACHE_HOME/ezdxf` (default `~/.cache/ezdxf`); the
  first run on a host builds it by scanning system fonts. Make sure the directory is
  writable for the service user (set `XDG_CACHE_HOME` otherwise) and warm it up after
  installing, e.g. with one `dxf2pdf` run.
- The engine reads only the files passed to it and makes no network requests.

## Tests

```bash
.venv/bin/pytest -q          # runs the CLI as `python -I -m cad_engine` subprocesses
.venv/bin/ruff check .
```

Inputs are generated at session start (`tests/make_sample.py`) into pytest's temp dir.
`scripts/make_heavy_pdf.py OUT.pdf UNITS [--kind outlines|fills|lines] [--pages N]` (1.6; `fills` 1.7) writes a synthetic
heavy PDF of about `UNITS` paths + segments — text as glyph outlines or one-line-per-path linework —
for calibrating the service's background threshold and the memory limit on a server. `tests/test_pdf_backend.py` renders the same drawings with
ezdxf's stock PyMuPDF backend and compares pages pixel by pixel, the PDF layers and the timing.
Optional large samples (`floorplan.dxf`, `Ceco.NET-Architecture-Tm-53.dxf`) are read from
`$CAD_ENGINE_SAMPLES_DIR`; tests that need them are skipped otherwise. Exit codes 3/4/5
are also checked in-process (the `RLIMIT_AS` limit itself is not exercised by the tests).
Raster-fallback tests that need OpenCV are skipped when it is not installed; the
"OpenCV missing" path is tested in-process.

Committed fixtures (`tests/fixtures/`): `sample_plan.dxf` and `underlay-only.dxf` (generated by
`tests/make_sample.py`); `texts.dxf`, `dimensions.dxf`, `hatches.dxf` from the test suite
of [bjnortier/dxf](https://github.com/bjnortier/dxf) (MIT); `AecObjects.dxf` — the sample
`samples/aec_objects/AecObjects.dwg` of [ACadSharp](https://github.com/DomCR/ACadSharp) (MIT,
Copyright (c) 2021 Albert Domenech, notice in `tests/fixtures/LICENSE-ACadSharp.txt`) converted to DXF by ACadSharp 3.4.9 (as the PDFConvert
site does in the browser); `host_xref.dxf` — a generated DWG with one xref (`plan.dwg`),
converted the same way. Every PDF the tests use
(forms, scans, layered and rotated pages, circles/arcs, fills, dashed runs, duplicates,
scale, scanned drawing, mixed vector + scan) and the DXF with paper-space layouts are
generated at test time with PyMuPDF / ezdxf.

## Third-party libraries

| Library | Licence | Used for |
|---------|---------|----------|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) 1.26.7 (MuPDF) | AGPL-3.0 (or Artifex commercial) | PDF reading, PDF/PNG output |
| [ezdxf](https://github.com/mozman/ezdxf) 1.4.5 | MIT | DXF reading/writing, DXF rendering |
| [Pillow](https://github.com/python-pillow/Pillow) 12.3.0 | HPND (MIT-CMU) | images in the ezdxf drawing add-on |
| [numpy](https://numpy.org) 2.5.4 | BSD-3-Clause | ezdxf geometry, winding numbers of fill areas |
| [shapely](https://github.com/shapely/shapely) 2.2.0 (GEOS) | BSD-3-Clause (GEOS: LGPL-2.1) | fill areas and `--cut` contours (1.7) |
| [OpenCV](https://opencv.org) (`opencv-python-headless` 4.14.0.94), optional extra `[raster]` | Apache-2.0 | `--raster-fallback` tracing |
| fontTools 4.68.0, pyparsing 3.3.3, typing_extensions 4.16.0 | MIT, MIT, PSF-2.0 | ezdxf dependencies |

Because PyMuPDF is AGPL-licensed, this engine is published under the AGPL-3.0 as well and
runs as a separate program; services that run it over a network must offer its source code
(this repository, at the tag of the deployed version).
