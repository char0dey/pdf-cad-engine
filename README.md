# pdf-cad-engine

PDF ↔ DXF conversion engine used by [PDFConvert](https://pdfconvert2.com): extracts vector
drawings and text from PDF into DXF, renders DXF drawings to PDF, and produces PNG previews.
It runs as a separate process with a small, stable command-line contract.

- Version: **1.0.0** (`cad_engine.__version__`, `pyproject.toml`)
- Licence: **AGPL-3.0-or-later** (see [LICENSE](LICENSE)), © 2026 Quazar Digital OÜ
- Python ≥ 3.12

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # exact pins (server installs)
.venv/bin/pip install -e .                  # or: pip install .
.venv/bin/python -I -m cad_engine --version # -> 1.0.0
```

Development: `.venv/bin/pip install -e '.[dev]'` (pytest, ruff).

## Command-line contract

```
python -I -m cad_engine <command> --in <file> [--out <file>] [--preview <png>]
                        [--meta <path>] [--budget-ms N] [--tmp <dir>]
python -I -m cad_engine --version          # prints "1.0.0"
cad-engine ...                             # console script, same arguments
```

| Command       | Input | Output (`--out`)  | Notes |
|---------------|-------|-------------------|-------|
| `pdf2dxf`     | PDF   | DXF (R2010, mm)   | `--preview out.png`: PNG of the extracted geometry |
| `dxf2pdf`     | DXF   | PDF (1 page)      | ASCII or binary DXF; damaged files are recovered |
| `probe-pdf`   | PDF   | —                 | pages, `has_vectors` / `text_entities` on the first 5 pages |
| `probe-dxf`   | DXF   | —                 | `entities`, `empty`, `dxf_version`, `layers` |
| `preview-dxf` | DXF   | PNG               | model space, longest side 1400 px, white background |

Options:

- `--meta <path>` — also write the JSON meta to a file (`-` = stdout only).
- `--budget-ms N` — time budget, default 90 000 ms. Checked per page and every
  1 000 entities (200 while rendering DXF), plus a `SIGALRM` interval timer. A native
  call that never returns to Python cannot be interrupted from inside the process, so
  the caller should still kill the process a few seconds after the budget (the engine
  dumps its stacks to stderr 4 s after the budget to make such hangs diagnosable).
- `--tmp <dir>` — directory for temporary files (created if missing; sets `TMPDIR`).

**stdout** carries exactly one line: the JSON meta, printed at the end of every run,
whatever the exit code (except `--version`, which prints the version). Everything else,
library warnings included, goes to **stderr**. Outputs are written as `<file>.part` and
renamed on success; on any failure the outputs are removed (no partial results).

### Exit codes

| Code | Meaning | `code` in meta |
|------|---------|----------------|
| 0 | success | `null` |
| 2 | unusable input | `no_vectors`, `empty_drawing`, `too_many_pages`, `dwg_not_dxf`, `too_complex`, `unsupported_input` |
| 3 | time budget exceeded | `budget_exceeded` |
| 4 | memory limit hit | `memory_limit` |
| 5 | any other failure: missing/unreadable input, usage error, bug (traceback on stderr) | `engine_error` |

Input error codes:

- `no_vectors` — `pdf2dxf`: no vector paths and no visible text on any page (scans; invisible
  OCR text does not count).
- `too_many_pages` — `pdf2dxf`: more than 50 pages (checked before converting).
- `too_complex` — `pdf2dxf`: more than 500 000 DXF entities.
- `dwg_not_dxf` — the file starts with a DWG signature (`AC10xx`), checked before parsing.
- `empty_drawing` — `dxf2pdf` / `preview-dxf`: model space has no entities or no extents.
- `unsupported_input` — not a PDF / not a DXF / password-protected PDF / unrecoverable DXF.

Probes report facts and exit 0; they exit 2 only for `unsupported_input` and `dwg_not_dxf`
(`probe-pdf` reports `pages` and leaves the 50-page limit to the caller; `probe-dxf`
reports `empty: true` instead of failing).

### JSON meta

All keys are always present; `null` when not applicable to the command.

| Key | Type | Set by |
|-----|------|--------|
| `version` | string | all (`"1.0.0"`) |
| `command` | string | all |
| `pages` | int | `pdf2dxf`, `probe-pdf`, `dxf2pdf` (always 1) |
| `entities` | int | `pdf2dxf` (DXF entities written), `dxf2pdf` / `probe-dxf` / `preview-dxf` (model space) |
| `layers` | int | `pdf2dxf` (layers used), DXF commands (layer table size) |
| `text_entities` | int | `pdf2dxf` (TEXT entities), `probe-pdf` (text spans on the first 5 pages) |
| `images` | int | `pdf2dxf`, `probe-pdf` — embedded images (ignored by the conversion) |
| `has_vectors` | bool | `pdf2dxf`, `probe-pdf` |
| `text_as_outlines` | bool | `pdf2dxf`, `probe-pdf` — no real text but vector drawings (text is glyph outlines) |
| `traced_pages` | int[] | `pdf2dxf` — always `[]` in 1.x (raster tracing is planned) |
| `empty` | bool | `probe-dxf` |
| `dxf_version` | string | DXF commands, e.g. `"AC1024"` |
| `page_size_mm` | [w, h] | `dxf2pdf`, e.g. `[297.0, 210.0]` |
| `ms` | int | all — wall time since start |
| `code`, `message` | string | on failure (exit ≠ 0) |

Example:

```json
{"version":"1.0.0","command":"pdf2dxf","pages":1,"entities":6343,"layers":9,"text_entities":0,"images":0,"has_vectors":true,"text_as_outlines":true,"traced_pages":[],"empty":null,"dxf_version":null,"page_size_mm":null,"ms":775,"code":null,"message":null}
```

## What the conversions do

**PDF → DXF** (`pdf2dxf`, PyMuPDF `get_drawings()` / `get_text()` → ezdxf):
AutoCAD 2010 DXF in millimetres (1 pt = 25.4/72 mm, Y up, page rotation applied);
consecutive line segments → LWPOLYLINE; rectangles/quads → closed LWPOLYLINE; cubic
Béziers → degree-3 SPLINE with the same control points; fill-only paths → closed
LWPOLYLINE on `FILL_RRGGBB` layers (a white fill covering the whole page is treated as
paper and skipped); real text → TEXT (string, height = 0.72 × font size, rotation,
baseline origin); invisible text is skipped; text drawn as outlines stays outlines.
Layers: the PDF optional-content layer name when present (layers that are off in the PDF
are off in the DXF), otherwise `LINES_RRGGBB` / `FILL_RRGGBB` / `TEXT_RRGGBB` with a
true-colour layer colour. PDF line widths → nearest standard DXF lineweight. Multi-page
PDFs: all pages side by side along +X, gap max(20 mm, 10 % of the page width).
`$EXTMIN/$EXTMAX` and the initial view are set to the content extents. Images are ignored.
The preview is drawn from the extracted primitives with PyMuPDF (sub-second for ~40 000
entities), not by re-rendering the DXF.

**DXF → PDF** (`dxf2pdf`, ezdxf drawing add-on + PyMuPDF backend): model space on one page,
A4 or A3 (drawing larger than 20 m in `$INSUNITS`, or more than 5 000 entities),
orientation from the extents, fitted with 10 mm margins, white background, colours kept
(ACI 7 and true-colour white print black), relative lineweights, invisible layers and
entities skipped. Damaged files go through `ezdxf.recover`; a file cut off at the end is
closed after the last complete object first, so everything that was read is rendered.

## Limits and environment

- 50 PDF pages, 500 000 DXF entities, default budget 90 s.
- Address space limited to 2 GB (`RLIMIT_AS`, Linux; macOS ignores it). Override with
  `CAD_ENGINE_MEMORY_MB` (0 disables).
- BLAS/OpenMP thread pools are limited to one thread.
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
Optional large samples (`floorplan.dxf`, `Ceco.NET-Architecture-Tm-53.dxf`) are read from
`$CAD_ENGINE_SAMPLES_DIR`; tests that need them are skipped otherwise. Exit codes 3/4/5
are also checked in-process (the `RLIMIT_AS` limit itself is not exercised by the tests).

Committed fixtures (`tests/fixtures/`): `sample_plan.dxf` (generated by
`tests/make_sample.py`); `texts.dxf`, `dimensions.dxf`, `hatches.dxf` from the test suite
of [bjnortier/dxf](https://github.com/bjnortier/dxf) (MIT). Every PDF the tests use
(forms, scans, layered and rotated pages) is generated at test time with PyMuPDF.

## Third-party libraries

| Library | Licence | Used for |
|---------|---------|----------|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) 1.26.7 (MuPDF) | AGPL-3.0 (or Artifex commercial) | PDF reading, PDF/PNG output |
| [ezdxf](https://github.com/mozman/ezdxf) 1.4.5 | MIT | DXF reading/writing, DXF rendering |
| [Pillow](https://github.com/python-pillow/Pillow) 12.3.0 | HPND (MIT-CMU) | images in the ezdxf drawing add-on |
| [numpy](https://numpy.org) 2.5.4 | BSD-3-Clause | ezdxf geometry |
| fontTools 4.68.0, pyparsing 3.3.3, typing_extensions 4.16.0 | MIT, MIT, PSF-2.0 | ezdxf dependencies |

Because PyMuPDF is AGPL-licensed, this engine is published under the AGPL-3.0 as well and
runs as a separate program; services that run it over a network must offer its source code
(this repository, at the tag of the deployed version).
