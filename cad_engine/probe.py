# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Fast input analysis (probe-pdf, probe-dxf) and the DXF preview (preview-dxf).

Probes report facts and exit 0; they exit 2 only when the input cannot be
identified or read (``unsupported_input``, ``dwg_not_dxf``). An empty drawing is a fact
(``empty`` + ``empty_reason``), not an exit 2.
"""

from __future__ import annotations

from cad_engine import outputs
from cad_engine.budget import Budget
from cad_engine.dxf2pdf import check_not_empty, paper_layouts, render_modelspace
from cad_engine.dxfio import check_dxf_signature, load_dxf
from cad_engine.errors import EngineError, InputError
from cad_engine.pdf2dxf import clean_text, open_pdf, text_flags, visible
from cad_engine.scan import apply_to_meta, empty_reason, safe_scan

PROBE_PDF_PAGES = 5
PREVIEW_LONGEST_PX = 1400
PREVIEW_MARGIN_MM = 5.0
PREVIEW_LONG_SIDE_MM = 297.0  # an A4-like sheet; the raster is scaled to PREVIEW_LONGEST_PX
PREVIEW_MIN_SHORT_SIDE_MM = 40.0


def probe_pdf(src: str, *, budget: Budget, meta: dict) -> None:
    pdf = open_pdf(src)
    meta["pages"] = pdf.page_count
    flags = text_flags()
    has_drawings = False
    texts = 0
    images = 0
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
        if not has_drawings:
            budget.check()
            has_drawings = any(
                visible(d.get("color"), d.get("stroke_opacity"))
                or visible(d.get("fill"), d.get("fill_opacity"))
                for d in page.get_drawings()
            )
    meta["text_entities"] = texts
    meta["images"] = images
    meta["has_vectors"] = has_drawings or texts > 0
    meta["text_as_outlines"] = has_drawings and texts == 0


def probe_dxf(src: str, *, budget: Budget, meta: dict, light: bool = False) -> None:
    """Facts about a DXF: the content scan (`cad_engine.scan`) and, unless ``light``, the
    loaded document (entities, layers, layouts, extents). ``empty`` is true when there is
    nothing to draw, ``empty_reason`` then says why (the code `dxf2pdf` would refuse with).
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
        check_not_empty(doc, scan)
        meta["empty"] = False
    except InputError as exc:
        meta["empty"] = True
        meta["empty_reason"] = exc.code


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
    budget.check()
    backend = render_modelspace(doc, budget)
    budget.check()
    rendered = backend.get_replay(_preview_page(w, h), settings=layout.Settings(fit_page=True))
    page = rendered.page
    zoom = PREVIEW_LONGEST_PX / max(page.rect.width, page.rect.height)
    png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    if not png:
        raise EngineError("the PNG backend returned no data")
    budget.check()
    outputs.write_part(dst, png)
    outputs.commit(dst)
