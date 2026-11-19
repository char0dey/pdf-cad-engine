# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Fast input analysis (probe-pdf, probe-dxf) and the DXF preview (preview-dxf).

Probes report facts and exit 0; they exit 2 only when the input cannot be
identified or read (``unsupported_input``, ``dwg_not_dxf``) or, for a PDF, when a page is too
heavy to read at all (``too_complex``, `cad_engine.complexity`). An empty drawing is a fact
(``empty`` + ``empty_reason``), not an exit 2.
"""

from __future__ import annotations

from cad_engine import entity_guard, outputs
from cad_engine.budget import Budget
from cad_engine.complexity import check_pdf_complexity, total_cost
from cad_engine.dxf2pdf import check_not_empty, paper_layouts, render_modelspace, sheet_layouts
from cad_engine.dxfio import check_dxf_signature, load_dxf
from cad_engine.errors import EngineError, InputError
from cad_engine.pdf2dxf import (
    BACKGROUND_AREA_RATIO,
    clean_text,
    is_white,
    open_pdf,
    rgb_of,
    text_flags,
    visible,
)
from cad_engine.scan import apply_to_meta, empty_reason, safe_scan

PROBE_PDF_PAGES = 5
#: A page with at least this many paths is not read with ``get_drawings()`` (~75 µs a path, 11 s
#: on the PROD 2026-11-16 page): it draws, and its count is known from the content (engine 1.6).
PROBE_FULL_READ_MAX_PATHS = 20_000
PREVIEW_LONGEST_PX = 1400
PREVIEW_MARGIN_MM = 5.0
PREVIEW_LONG_SIDE_MM = 297.0  # an A4-like sheet; the raster is scaled to PREVIEW_LONGEST_PX
PREVIEW_MIN_SHORT_SIDE_MM = 40.0


def _is_paper(drawing: dict, page_area: float) -> bool:
    rect = drawing.get("rect")
    return (rect is not None and is_white(rgb_of(drawing.get("fill")))
            and abs(rect.width * rect.height) >= BACKGROUND_AREA_RATIO * page_area)


def probe_pdf(src: str, *, budget: Budget, meta: dict) -> None:
    """Facts about a PDF. ``vector_paths`` / ``vector_segments`` (engine 1.6) are the sum over all
    pages of what ``pdf2dxf`` will read — the service decides by them whether the conversion
    goes to the background. They are written before the slower reads below, so a run that
    spends its budget later still reports them.

    ``suggested_purpose`` (engine 1.7) is ``"cut"`` for a PDF that is only fills — a logo, a
    lettering in curves: at most `PROBE_PDF_PAGES` pages, none too heavy to read, at least one
    visible fill without stroke (the paper does not count), no stroke, no real text, no image;
    otherwise ``"cad"``. ``fill_paths`` / ``stroke_paths`` are counted on the pages read while
    the answer is open: the first stroke, text or image settles it and the rest is not read
    (a drawing costs no more than before). Written last: a run out of budget leaves it null."""
    pdf = open_pdf(src)
    meta["pages"] = pdf.page_count
    # before any get_drawings(): past the memory limit it crashes instead of raising
    costs = check_pdf_complexity(pdf, budget=budget)
    total = total_cost(costs)
    meta["vector_paths"] = total.paths
    meta["vector_segments"] = total.segments
    flags = text_flags()
    has_drawings = False
    texts = 0
    images = 0
    fill_paths = 0
    stroke_paths = 0
    heavy = False
    for pno in range(min(pdf.page_count, PROBE_PDF_PAGES)):
        budget.check()
        page = pdf[pno]
        for block in page.get_text("dict", flags=flags)["blocks"]:
            if block.get("type", 0) != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if span.get("alpha", 255) != 0 and span.get("size", 0) > 0 and clean_text(span["text"]):
                        texts += 1
        images += len(page.get_images())
        budget.check()
        if costs[pno].paths >= PROBE_FULL_READ_MAX_PATHS:
            has_drawings = True  # a page this heavy draws something; reading it would take seconds
            heavy = True
            continue
        if not heavy and texts == 0 and images == 0 and stroke_paths == 0:
            # only fills so far: read every path of the page (a logo takes milliseconds)
            area = abs(page.rect.width * page.rect.height)
            for d in page.get_drawings():
                if visible(d.get("color"), d.get("stroke_opacity")):
                    has_drawings = True
                    stroke_paths += 1
                    break  # the answer is "cad"
                if visible(d.get("fill"), d.get("fill_opacity")):
                    has_drawings = True
                    if not _is_paper(d, area):
                        fill_paths += 1
        elif not has_drawings:
            has_drawings = any(
                visible(d.get("color"), d.get("stroke_opacity"))
                or visible(d.get("fill"), d.get("fill_opacity"))
                for d in page.get_drawings()
            )
    meta["text_entities"] = texts
    meta["images"] = images
    meta["has_vectors"] = has_drawings or texts > 0
    meta["text_as_outlines"] = has_drawings and texts == 0
    meta["fill_paths"] = fill_paths
    meta["stroke_paths"] = stroke_paths
    only_fills = fill_paths > 0 and stroke_paths == 0 and texts == 0 and images == 0
    meta["suggested_purpose"] = (
        "cut" if only_fills and not heavy and pdf.page_count <= PROBE_PDF_PAGES else "cad"
    )


def probe_dxf(src: str, *, budget: Budget, meta: dict, light: bool = False) -> None:
    """Facts about a DXF: the content scan (`cad_engine.scan`) and, unless ``light``, the
    loaded document (entities, layers, layouts, extents). ``empty`` is true when there is
    nothing to draw, ``empty_reason`` then says why (the code `dxf2pdf` would refuse with).
    The answer is the one for ``dxf2pdf --layouts`` (the service always passes it): an empty
    model space with sheets that draw something is not empty, ``layouts`` then lists those
    sheets — the pages of the PDF (1.4).
    ``light`` (big files) skips loading: ``entities`` / ``empty`` come from the scan alone and
    stay null when the scan failed or the file is cut off."""
    check_dxf_signature(src)
    scan = safe_scan(src, budget)
    apply_to_meta(meta, scan)
    budget.check()
    if light:
        if scan is not None:
            meta["entities"] = scan.ms_entities
        if scan is not None and scan.complete:
            meta["empty"] = scan.nothing_to_draw
            if scan.nothing_to_draw:
                meta["empty_reason"] = empty_reason(scan)
        return
    doc = load_dxf(src)
    budget.check()
    meta["entities"] = len(doc.modelspace())
    meta["layers"] = len(doc.layers)
    meta["dxf_version"] = doc.dxfversion
    meta["layouts"] = paper_layouts(doc)
    try:
        extents = check_not_empty(doc, scan, sheets=True)
    except InputError as exc:
        meta["empty"] = True
        meta["empty_reason"] = exc.code
        return
    meta["empty"] = False
    if extents is None:  # empty model space: the PDF is the sheets that draw something
        meta["layouts"] = sheet_layouts(doc)


def _preview_page(w: float, h: float):
    from ezdxf.addons.drawing import layout

    long_side = PREVIEW_LONG_SIDE_MM
    inner = long_side - 2 * PREVIEW_MARGIN_MM
    ratio = min(w, h) / max(w, h) if max(w, h) > 0 else 1.0
    short_side = max(inner * ratio + 2 * PREVIEW_MARGIN_MM, PREVIEW_MIN_SHORT_SIDE_MM)
    pw, ph = (long_side, short_side) if w >= h else (short_side, long_side)
    return layout.Page(pw, ph, layout.Units.mm, margins=layout.Margins.all(PREVIEW_MARGIN_MM))


def preview_dxf(src: str, dst: str, *, budget: Budget, meta: dict) -> None:
    import pymupdf
    from ezdxf.addons.drawing import layout

    doc = load_dxf(src)
    meta["entities"] = len(doc.modelspace())
    meta["layers"] = len(doc.layers)
    meta["dxf_version"] = doc.dxfversion
    w, h = check_not_empty(doc)
    measured_with_skips = entity_guard.LOG.count("extents") > 0
    budget.check()
    backend = render_modelspace(doc, budget)
    budget.check()
    meta["far_parts"] = backend.far.dropped
    if (backend.far.dropped or measured_with_skips) and backend.far.size is not None:
        # the drawing without its far parts (1.5), or as drawn when measuring skipped entities (1.5.2)
        w, h = backend.far.size
    rendered = backend.get_replay(_preview_page(w, h), settings=layout.Settings(fit_page=True))
    page = rendered.page
    zoom = PREVIEW_LONGEST_PX / max(page.rect.width, page.rect.height)
    png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    if not png:
        raise EngineError("the PNG backend returned no data")
    budget.check()
    outputs.write_part(dst, png)
    outputs.commit(dst)
