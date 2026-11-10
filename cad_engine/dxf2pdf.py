# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""DXF -> PDF: ezdxf drawing add-on rendered with the PyMuPDF backend.

Model space goes onto the first page: by default (``--page auto``) A4, or A3 when the
drawing is larger than 20 m or has more than 5 000 entities; ``--page a4|a3|a2|a1`` fixes
the paper size. The orientation follows the extents; the content is fitted with 10 mm
margins on a white background. Colours are kept (ACI 7 and true-colour white become
black) or, with ``--colors mono``, everything is black; lineweights are relative to the
drawing size; invisible layers and entities are skipped by the ezdxf frontend.

``--layouts`` adds one page per paper-space layout that has content (entities or
viewports that show model space), in tab order after Model space, each in its own paper
size (A4 landscape when the layout has none); viewports show model space through their
clipping window. A layout whose viewports show an empty part of model space comes out as a
blank sheet (as a CAD program would plot it).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from cad_engine import outputs
from cad_engine.budget import Budget, Ticker
from cad_engine.dxfio import check_dxf_signature, extents_mm_factor, load_dxf
from cad_engine.errors import EngineError, InputError
from cad_engine.scan import EMPTY_MESSAGES, ScanResult, apply_to_meta, empty_reason, safe_scan

if TYPE_CHECKING:
    from ezdxf.document import Drawing
    from ezdxf.layouts import Layout

    from cad_engine.pdf_backend import FastPdfBackend

A4_MM = (297.0, 210.0)
A3_MM = (420.0, 297.0)
PAGE_SIZES_MM = {"a4": A4_MM, "a3": A3_MM, "a2": (594.0, 420.0), "a1": (841.0, 594.0)}
PAGE_CHOICES = ("auto", *PAGE_SIZES_MM)
COLOR_CHOICES = ("color", "mono")
LARGE_EXTENT_MM = 20_000.0
LARGE_ENTITY_COUNT = 5_000
MARGIN_MM = 10.0
LAYOUT_FIT_MIN_RATIO = 0.25  # paper-space content smaller than this share of the page is scaled up


def _white_to_black(entity, properties) -> None:  # ezdxf override signature
    color = properties.color
    if color[:7].lower() == "#ffffff":
        properties.color = "#000000" + color[7:]


def check_not_empty(doc: Drawing, scan: ScanResult | None = None) -> tuple[float, float]:
    """Return the model-space extents (w, h) in drawing units, or raise when there is nothing to
    draw — with the reason the content scan gives (``aec_objects`` > ``external_refs_only`` >
    ``empty_drawing``, spec cad-missing-content). The scan's own «nothing drawable» (only
    underlays, images or xref inserts) counts only when it saw the same model space as ezdxf."""
    from ezdxf import bbox

    code = empty_reason(scan)
    detail = EMPTY_MESSAGES[code]
    msp = doc.modelspace()
    if len(msp) == 0:
        raise InputError(code, f"{detail} (no entities in model space)")
    if scan is not None and scan.nothing_to_draw and scan.ms_entities == len(msp):
        raise InputError(code, f"{detail} (only external references in model space)")
    ext = bbox.extents(msp, fast=True)
    if not ext.has_data:
        raise InputError(code, f"{detail} (nothing to draw in model space)")
    w, h = float(ext.size.x), float(ext.size.y)
    if w <= 0 and h <= 0:
        raise InputError(code, f"{detail} (zero-size extents)")
    return w, h


def drawn_viewports(layout: Layout) -> list:
    """The viewports the ezdxf frontend draws (same rules as ``frontend._draw_viewports``):
    status > 0, minus the "active" viewport (status 1, the sheet itself), top views only."""
    viewports = sorted((e for e in layout if e.dxftype() == "VIEWPORT"), key=lambda e: e.dxf.status)
    viewports = [vp for vp in viewports if vp.dxf.status > 0]
    if viewports and viewports[0].dxf.get("status", 1) == 1:
        viewports.pop(0)
    return [vp for vp in viewports if vp.is_top_view]


def layout_content(layout: Layout) -> list:
    """Paper-space entities that draw something: everything but viewports, plus the
    viewports that show model space."""
    return [e for e in layout if e.dxftype() != "VIEWPORT"] + drawn_viewports(layout)


def paper_layouts(doc: Drawing) -> list[str]:
    """Names of the paper-space layouts (tab order) with content: paper-space entities or
    viewports that show model space (see `drawn_viewports`)."""
    names = []
    for name in doc.layouts.names_in_taborder():
        if name.lower() == "model":
            continue
        layout = doc.layouts.get(name)
        if layout is not None and layout_content(layout):
            names.append(name)
    return names


def _configuration(colors: str):
    from ezdxf.addons.drawing import config

    return config.Configuration(
        background_policy=config.BackgroundPolicy.WHITE,
        color_policy=config.ColorPolicy.BLACK if colors == "mono" else config.ColorPolicy.COLOR,
        lineweight_policy=config.LineweightPolicy.RELATIVE,
    )


def render_layout(doc: Drawing, layout, budget: Budget, colors: str = "color") -> FastPdfBackend:
    """Record one layout with the ezdxf frontend (budget checked every 200 entities); the
    recording replays into the faster PDF output of `cad_engine.pdf_backend`. Hatch patterns
    are capped (`cad_engine.hatch_limit`); ``backend.simplified_hatches`` says how many hatches
    of this layout were drawn as outlines."""
    from ezdxf.addons.drawing import RenderContext

    from cad_engine.hatch_limit import EngineFrontend
    from cad_engine.pdf_backend import FastPdfBackend

    ctx = RenderContext(doc)
    backend = FastPdfBackend()
    frontend = EngineFrontend(ctx, backend, config=_configuration(colors), tick=budget.check)
    frontend.push_property_override_function(_white_to_black)
    ticker = Ticker(budget, every=200)

    def keep(entity) -> bool:
        ticker.tick()
        return True

    frontend.draw_layout(layout, finalize=True, filter_func=keep)
    backend.simplified_hatches = frontend.simplified_hatches
    return backend


def render_modelspace(doc: Drawing, budget: Budget, colors: str = "color") -> FastPdfBackend:
    return render_layout(doc, doc.modelspace(), budget, colors)


def auto_page(w: float, h: float, entities: int, mm_factor: float) -> tuple[float, float]:
    big = max(w, h) * mm_factor > LARGE_EXTENT_MM or entities > LARGE_ENTITY_COUNT
    pw, ph = A3_MM if big else A4_MM
    if w < h:
        pw, ph = ph, pw
    return pw, ph


def choose_page(page: str, w: float, h: float, entities: int, mm_factor: float) -> tuple[float, float]:
    if page == "auto":
        return auto_page(w, h, entities, mm_factor)
    pw, ph = PAGE_SIZES_MM[page]
    return (ph, pw) if w < h else (pw, ph)


def _blank_page(width_mm: float, height_mm: float) -> bytes:
    import pymupdf

    with pymupdf.open() as doc:
        doc.new_page(width=width_mm * 72.0 / 25.4, height=height_mm * 72.0 / 25.4)
        return doc.tobytes()


def _layout_pdf(doc: Drawing, name: str, budget: Budget, colors: str) -> tuple[bytes, tuple[float, float], int]:
    """One paper-space layout as a one-page PDF in its paper size. A layout whose viewports
    look at an empty part of model space draws nothing: it becomes a blank sheet, as a CAD
    program would plot it."""
    from ezdxf import bbox
    from ezdxf.addons.drawing import layout as page_layout

    paper = doc.layouts.get(name)
    if paper.dxf.paper_width > 0 and paper.dxf.paper_height > 0:
        page = page_layout.Page.from_dxf_layout(paper)
    else:
        page = page_layout.Page(*A4_MM, page_layout.Units.mm, margins=page_layout.Margins.all(MARGIN_MM))
    backend = render_layout(doc, paper, budget, colors)
    hatches = backend.simplified_hatches
    budget.check()
    # paper space is drawn in paper units: keep 1:1 when the content fits the printable area,
    # otherwise (inch-based or oversized layouts) fit it to the page
    ext = bbox.extents(layout_content(paper), fast=True)
    inner_w = page.width_in_mm - page.margins_in_mm.left - page.margins_in_mm.right
    inner_h = page.height_in_mm - page.margins_in_mm.top - page.margins_in_mm.bottom
    one_to_one = False
    if ext.has_data and inner_w > 0 and inner_h > 0:
        w, h = float(ext.size.x), float(ext.size.y)
        fits = w <= inner_w * 1.001 and h <= inner_h * 1.001
        one_to_one = fits and max(w / inner_w, h / inner_h) >= LAYOUT_FIT_MIN_RATIO
    settings = page_layout.Settings(fit_page=not one_to_one, scale=1.0)
    size = (round(page.width_in_mm, 1), round(page.height_in_mm, 1))
    try:
        data = backend.get_pdf_bytes(page, settings=settings)
    except ValueError as exc:
        if "empty bounding box" not in str(exc):
            raise
        print(f"cad-engine: layout {name!r} draws nothing; blank sheet", file=sys.stderr)
        return _blank_page(*size), size, hatches
    if not data:
        raise EngineError(f"the PDF backend returned no data for layout {name!r}")
    return data, size, hatches


def convert(src: str, dst: str, *, budget: Budget, meta: dict, page: str = "auto", colors: str = "color",
            layouts: bool = False) -> None:
    from ezdxf.addons.drawing import layout

    if page not in PAGE_CHOICES:
        raise ValueError(f"unknown page size {page!r}")
    if colors not in COLOR_CHOICES:
        raise ValueError(f"unknown colour mode {colors!r}")
    check_dxf_signature(src)
    scan = safe_scan(src, budget)
    apply_to_meta(meta, scan)  # findings travel with a refusal too
    doc = load_dxf(src)
    msp = doc.modelspace()
    entities = len(msp)
    meta["entities"] = entities
    meta["layers"] = len(doc.layers)
    meta["dxf_version"] = doc.dxfversion
    meta["layouts"] = []
    w, h = check_not_empty(doc, scan)
    budget.check()

    pw, ph = choose_page(page, w, h, entities, extents_mm_factor(doc))
    backend = render_modelspace(doc, budget, colors)
    simplified = backend.simplified_hatches
    budget.check()
    model_page = layout.Page(pw, ph, layout.Units.mm, margins=layout.Margins.all(MARGIN_MM))
    names = paper_layouts(doc) if layouts else []
    try:
        pdf_bytes: bytes | None = backend.get_pdf_bytes(model_page, settings=layout.Settings(fit_page=True))
    except ValueError as exc:
        if "empty bounding box" not in str(exc):
            raise
        # entities, but none visible (all on layers that are off or frozen …): the sheets, or a refusal
        code = empty_reason(scan)
        if not names:
            raise InputError(code, f"{EMPTY_MESSAGES[code]} (nothing visible in model space)") from exc
        print("cad-engine: model space draws nothing visible; the PDF holds the layouts only", file=sys.stderr)
        meta["model_blank"] = True
        pdf_bytes = None
    if pdf_bytes is not None and not pdf_bytes:
        raise EngineError("the PDF backend returned no data")
    rendered: list[tuple[str, tuple[float, float]]] = []
    if names:
        import pymupdf

        # Model space first, keeps its optional content; a blank model leaves only the sheets
        merged = pymupdf.open("pdf", pdf_bytes) if pdf_bytes is not None else pymupdf.open()
        for name in names:
            budget.check()
            try:
                data, size, hatches = _layout_pdf(doc, name, budget, colors)
            except MemoryError:
                raise
            except Exception as exc:  # one broken layout must not cost the model-space page
                print(f"cad-engine: layout {name!r} skipped ({type(exc).__name__}: {exc})",
                      file=sys.stderr)
                meta.setdefault("warnings", []).append(f"layout_skipped:{name}")
                continue
            with pymupdf.open("pdf", data) as part:
                merged.insert_pdf(part)
            rendered.append((name, size))
            simplified += hatches
        budget.check()
        if merged.page_count == 0:  # a blank model and every layout skipped
            merged.close()
            code = empty_reason(scan)
            raise InputError(code, f"{EMPTY_MESSAGES[code]} (nothing visible in model space, no layout rendered)")
        pdf_bytes = merged.tobytes(garbage=1, deflate=True)
        merged.close()
    model_pages = 0 if meta.get("model_blank") else 1
    meta["pages"] = model_pages + len(rendered)
    meta["page_size_mm"] = [pw, ph] if model_pages else list(rendered[0][1])
    meta["layouts"] = [name for name, _ in rendered]
    meta["simplified_hatches"] = simplified
    budget.check()
    outputs.write_part(dst, pdf_bytes)
    outputs.commit(dst)
    sizes = " ".join(f"{name}={size[0]:g}x{size[1]:g}mm" for name, size in rendered)
    print(f"cad-engine: dxf2pdf entities={entities} extents={w:.1f}x{h:.1f} page={pw:g}x{ph:g}mm "
          f"colors={colors} layouts=[{sizes}] pdf={len(pdf_bytes) // 1024}KB", file=sys.stderr)
