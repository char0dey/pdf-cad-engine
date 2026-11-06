# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""DXF -> PDF: ezdxf drawing add-on rendered with the PyMuPDF backend.

Model space goes onto one page: A4, or A3 when the drawing is larger than
20 m or has more than 5 000 entities; orientation follows the extents; the
content is fitted with 10 mm margins on a white background. Colours are kept
(ACI 7 and true-colour white become black), lineweights are relative to the
drawing size, invisible layers and entities are skipped by the ezdxf frontend.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from cad_engine import outputs
from cad_engine.budget import Budget, Ticker
from cad_engine.dxfio import extents_mm_factor, load_dxf
from cad_engine.errors import EngineError, InputError

if TYPE_CHECKING:
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    from ezdxf.document import Drawing

A4_MM = (297.0, 210.0)
A3_MM = (420.0, 297.0)
LARGE_EXTENT_MM = 20_000.0
LARGE_ENTITY_COUNT = 5_000
MARGIN_MM = 10.0


def _white_to_black(entity, properties) -> None:  # ezdxf override signature
    color = properties.color
    if color[:7].lower() == "#ffffff":
        properties.color = "#000000" + color[7:]


def check_not_empty(doc: Drawing) -> tuple[float, float]:
    """Return the model-space extents (w, h) in drawing units or raise empty_drawing."""
    from ezdxf import bbox

    msp = doc.modelspace()
    if len(msp) == 0:
        raise InputError("empty_drawing", "the drawing is empty (no entities in model space)")
    ext = bbox.extents(msp, fast=True)
    if not ext.has_data:
        raise InputError("empty_drawing", "the drawing is empty (nothing to draw in model space)")
    w, h = float(ext.size.x), float(ext.size.y)
    if w <= 0 and h <= 0:
        raise InputError("empty_drawing", "the drawing is empty (zero-size extents)")
    return w, h


def render_modelspace(doc: Drawing, budget: Budget) -> PyMuPdfBackend:
    """Record model space with the ezdxf frontend (budget checked every 200 entities)."""
    from ezdxf.addons.drawing import Frontend, RenderContext, config
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

    ctx = RenderContext(doc)
    backend = PyMuPdfBackend()
    cfg = config.Configuration(
        background_policy=config.BackgroundPolicy.WHITE,
        color_policy=config.ColorPolicy.COLOR,
        lineweight_policy=config.LineweightPolicy.RELATIVE,
    )
    frontend = Frontend(ctx, backend, config=cfg)
    frontend.push_property_override_function(_white_to_black)
    ticker = Ticker(budget, every=200)

    def keep(entity) -> bool:
        ticker.tick()
        return True

    frontend.draw_layout(doc.modelspace(), finalize=True, filter_func=keep)
    return backend


def auto_page(w: float, h: float, entities: int, mm_factor: float) -> tuple[float, float]:
    big = max(w, h) * mm_factor > LARGE_EXTENT_MM or entities > LARGE_ENTITY_COUNT
    pw, ph = A3_MM if big else A4_MM
    if w < h:
        pw, ph = ph, pw
    return pw, ph


def convert(src: str, dst: str, *, budget: Budget, meta: dict) -> None:
    from ezdxf.addons.drawing import layout

    doc = load_dxf(src)
    msp = doc.modelspace()
    entities = len(msp)
    meta["entities"] = entities
    meta["layers"] = len(doc.layers)
    meta["dxf_version"] = doc.dxfversion
    w, h = check_not_empty(doc)
    budget.check()

    pw, ph = auto_page(w, h, entities, extents_mm_factor(doc))
    backend = render_modelspace(doc, budget)
    budget.check()
    page = layout.Page(pw, ph, layout.Units.mm, margins=layout.Margins.all(MARGIN_MM))
    pdf_bytes = backend.get_pdf_bytes(page, settings=layout.Settings(fit_page=True))
    if not pdf_bytes:
        raise EngineError("the PDF backend returned no data")
    meta["pages"] = 1
    meta["page_size_mm"] = [pw, ph]
    budget.check()
    outputs.write_part(dst, pdf_bytes)
    outputs.commit(dst)
    print(f"cad-engine: dxf2pdf entities={entities} extents={w:.1f}x{h:.1f} page={pw:g}x{ph:g}mm "
          f"pdf={len(pdf_bytes) // 1024}KB", file=sys.stderr)
