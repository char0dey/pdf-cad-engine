# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""PDF -> DXF: PyMuPDF vector/text extraction written as DXF with ezdxf.

What ends up in the DXF (AutoCAD 2010, millimetres, 1 pt = 25.4/72 mm, Y up):

- consecutive line segments of one path -> one LWPOLYLINE;
- rectangles and quads -> closed LWPOLYLINEs;
- cubic Béziers -> degree-3 SPLINEs with the same control points (exact);
- fill-only paths -> closed LWPOLYLINEs on ``FILL_RRGGBB`` layers;
- real PDF text -> TEXT (string, height from the font size, rotation, baseline origin);
  text drawn as glyph outlines stays outlines; invisible text (OCR layers) is skipped;
- layers: the PDF optional-content layer name when present, else
  ``LINES_RRGGBB`` / ``FILL_RRGGBB`` / ``TEXT_RRGGBB`` with a true-colour layer colour;
- PDF line widths -> nearest standard DXF lineweight;
- all pages side by side along +X (left to right) with a gap of
  max(20 mm, 10 % of the page width); images are ignored (only counted).
"""

from __future__ import annotations

import math
import sys
from functools import lru_cache
from typing import TYPE_CHECKING

from cad_engine import outputs
from cad_engine.budget import Budget
from cad_engine.errors import InputError
from cad_engine.preview import PreviewRecorder

if TYPE_CHECKING:
    import pymupdf

PT2MM = 25.4 / 72.0
MAX_PAGES = 50
MAX_ENTITIES = 500_000
PAGE_GAP_MIN_MM = 20.0
PAGE_GAP_RATIO = 0.10
TEXT_HEIGHT_RATIO = 0.72  # font size (em) -> approximate cap height
BACKGROUND_AREA_RATIO = 0.95  # a white fill covering this much of the page is the paper, not content
CHECK_EVERY = 1000  # budget checkpoint every N entities

VALID_LINEWEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140,
                     158, 200, 211)
_LAYER_NAME_BAD = set('<>/\\":;?*=`')
MAX_LAYER_NAME = 255


def open_pdf(path: str) -> pymupdf.Document:
    """Open a PDF or raise `InputError('unsupported_input')`."""
    import pymupdf

    try:
        doc = pymupdf.open(path, filetype="pdf")
    except MemoryError:
        raise
    except Exception as exc:
        raise InputError("unsupported_input", "the file is not a readable PDF") from exc
    if doc.needs_pass:
        raise InputError("unsupported_input", "the PDF is password-protected")
    if doc.page_count < 1:
        raise InputError("unsupported_input", "the PDF has no pages")
    return doc


def text_flags() -> int:
    import pymupdf

    # dict extraction without image payloads (images are only counted)
    return pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES


@lru_cache(maxsize=1024)
def lineweight(width_pt: float) -> int:
    """PDF line width (pt) -> nearest valid DXF lineweight (1/100 mm)."""
    target = width_pt * PT2MM * 100.0
    return min(VALID_LINEWEIGHTS, key=lambda v: abs(v - target))


def rgb_of(color) -> tuple[int, int, int]:
    """PyMuPDF colour (float tuple or packed sRGB int) -> 0..255 RGB."""
    if color is None:
        return (0, 0, 0)
    if isinstance(color, int):
        return ((color >> 16) & 255, (color >> 8) & 255, color & 255)
    values = [min(max(float(v), 0.0), 1.0) for v in color]
    if len(values) == 1:
        values = values * 3
    elif len(values) == 4:  # CMYK
        c, m, y, k = values
        values = [(1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)]
    return tuple(int(round(v * 255)) for v in values[:3])  # type: ignore[return-value]


def sanitize_layer_name(name: str) -> str:
    cleaned = "".join("_" if (ch in _LAYER_NAME_BAD or ord(ch) < 32) else ch for ch in name)
    return cleaned.strip()[:MAX_LAYER_NAME].strip()


def clean_text(text: str) -> str:
    return "".join(" " if ord(ch) < 32 else ch for ch in text).strip()


def visible(color, opacity) -> bool:
    return color is not None and (opacity is None or opacity > 0)


class PageMap:
    """Maps PyMuPDF page coordinates (unrotated, y down, pt) to DXF (mm, y up)."""

    __slots__ = ("m", "x0", "y1", "ox", "area")

    def __init__(self, page: pymupdf.Page, x_offset_mm: float) -> None:
        rect = page.rect  # rotated (display) page rectangle
        if page.rotation:
            rm = page.rotation_matrix
            self.m: tuple[float, ...] | None = (rm.a, rm.b, rm.c, rm.d, rm.e, rm.f)
        else:
            self.m = None
        self.x0 = rect.x0
        self.y1 = rect.y1
        self.ox = x_offset_mm
        self.area = abs(rect.width * rect.height)

    def is_background(self, rect) -> bool:
        return rect is not None and abs(rect.width * rect.height) >= BACKGROUND_AREA_RATIO * self.area

    def pt(self, x: float, y: float) -> tuple[float, float]:
        m = self.m
        if m is not None:
            x, y = m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]
        return ((x - self.x0) * PT2MM + self.ox, (self.y1 - y) * PT2MM)

    def angle(self, dx: float, dy: float) -> float:
        """Text direction (page coords) -> DXF rotation in degrees (CCW)."""
        m = self.m
        if m is not None:
            dx, dy = m[0] * dx + m[2] * dy, m[1] * dx + m[3] * dy
        deg = -math.degrees(math.atan2(dy, dx)) % 360.0
        deg = round(deg, 6)
        return 0.0 if deg >= 360.0 else deg + 0.0


class DxfWriter:
    """Accumulates DXF entities, layers, extents, limits and preview geometry."""

    def __init__(self, budget: Budget, *, max_entities: int, record_preview: bool) -> None:
        import ezdxf
        from ezdxf import units

        self._ezdxf = ezdxf
        self.doc = ezdxf.new("R2010", setup=True)
        self.doc.units = units.MM
        self.msp = self.doc.modelspace()
        self.budget = budget
        self.max_entities = max_entities
        self.entities = 0
        self.text_entities = 0
        self.vector_entities = 0
        self._layers: dict[str, tuple[str, tuple[int, int, int] | None]] = {}  # lower name -> (name, rgb)
        self.layers_off: set[str] = set()  # PDF layer names that are off
        self._hidden: set[str] = set()  # lower-case DXF layer names created off
        self.min_x = self.min_y = math.inf
        self.max_x = self.max_y = -math.inf
        self.preview = PreviewRecorder() if record_preview else None

    # -- bookkeeping -------------------------------------------------------------------
    def _tick(self) -> None:
        self.entities += 1
        if self.entities > self.max_entities:
            raise InputError(
                "too_complex",
                f"the drawing has more than {self.max_entities} entities, the limit for online conversion",
            )
        if self.entities % CHECK_EVERY == 0:
            self.budget.check()

    def _extend(self, pts) -> None:
        for x, y in pts:
            if x < self.min_x:
                self.min_x = x
            if x > self.max_x:
                self.max_x = x
            if y < self.min_y:
                self.min_y = y
            if y > self.max_y:
                self.max_y = y

    @property
    def has_extents(self) -> bool:
        return self.min_x <= self.max_x and self.min_y <= self.max_y

    @property
    def layer_count(self) -> int:
        return len(self._layers)

    def layer(self, prefix: str, rgb: tuple[int, int, int], ocg: str | None) -> tuple[str, bool]:
        """Return (layer name, entity needs its own true colour)."""
        if ocg:
            name = sanitize_layer_name(ocg)
            if name:
                off = ocg in self.layers_off
                entry = self._layers.get(name.lower()) or self._new_layer(name, rgb, off=off)
                return entry[0], entry[1] != rgb
        name = f"{prefix}_{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
        entry = self._layers.get(name.lower()) or self._new_layer(name, rgb, off=False)
        return entry[0], entry[1] != rgb

    def _new_layer(self, name: str, rgb: tuple[int, int, int], *, off: bool):
        layers = self.doc.layers
        if layers.has_entry(name):
            # a standard layer such as "0" or "Defpoints" (PDFs made by CAD exporters use them):
            # keep its definition, entities carry their own colour
            entry = (layers.get(name).dxf.name, None)
        else:
            layer = layers.add(name, true_color=self._ezdxf.rgb2int(rgb))
            if off:
                layer.off()
                self._hidden.add(name.lower())
            entry = (name, rgb)
        self._layers[name.lower()] = entry
        return entry

    def _attribs(self, prefix: str, rgb, ocg: str | None, lineweight_value: int | None) -> dict:
        name, own_color = self.layer(prefix, rgb, ocg)
        attribs: dict = {"layer": name}
        if lineweight_value is not None:
            attribs["lineweight"] = lineweight_value
        if own_color:
            attribs["true_color"] = self._ezdxf.rgb2int(rgb)
        return attribs

    # -- entities ----------------------------------------------------------------------
    def add_path(self, drawing: dict, pm: PageMap) -> None:
        stroke = drawing.get("color")
        fill = drawing.get("fill")
        if not visible(stroke, drawing.get("stroke_opacity")):
            stroke = None
        if not visible(fill, drawing.get("fill_opacity")):
            fill = None
        if stroke is None and fill is None:
            return
        fill_only = stroke is None
        rgb = rgb_of(fill if fill_only else stroke)
        if fill_only and min(rgb) >= 250 and pm.is_background(drawing.get("rect")):
            return  # white page background painted by the exporter
        width_pt = 0.0 if fill_only else float(drawing.get("width") or 0.0)
        attribs = self._attribs("FILL" if fill_only else "LINES", rgb, drawing.get("layer"),
                                0 if fill_only else lineweight(width_pt))
        style = (rgb, width_pt)
        msp = self.msp
        rec = self.preview if attribs["layer"].lower() not in self._hidden else None
        P = pm.pt
        pts: list[tuple[float, float]] = []

        def flush(closed: bool = False) -> None:
            if len(pts) >= 2:
                self._tick()
                self.vector_entities += 1
                msp.add_lwpolyline(pts, close=closed, dxfattribs=attribs)
                self._extend(pts)
                if rec is not None:
                    rec.polyline(style, list(pts), closed)
            pts.clear()

        def closed_poly(corners: list[tuple[float, float]]) -> None:
            self._tick()
            self.vector_entities += 1
            msp.add_lwpolyline(corners, close=True, dxfattribs=attribs)
            self._extend(corners)
            if rec is not None:
                rec.polyline(style, corners, True)

        for item in drawing["items"]:
            kind = item[0]
            if kind == "l":
                a = P(item[1].x, item[1].y)
                b = P(item[2].x, item[2].y)
                if not pts or abs(pts[-1][0] - a[0]) > 1e-4 or abs(pts[-1][1] - a[1]) > 1e-4:
                    flush()
                    pts.append(a)
                pts.append(b)
            elif kind == "re":
                flush()
                r = item[1]
                closed_poly([P(r.x0, r.y0), P(r.x1, r.y0), P(r.x1, r.y1), P(r.x0, r.y1)])
            elif kind == "qu":
                flush()
                q = item[1]
                closed_poly([P(q.ul.x, q.ul.y), P(q.ur.x, q.ur.y), P(q.lr.x, q.lr.y), P(q.ll.x, q.ll.y)])
            elif kind == "c":
                flush()
                cps = [P(p.x, p.y) for p in item[1:5]]
                self._tick()
                self.vector_entities += 1
                msp.add_open_spline(cps, degree=3, dxfattribs=attribs)
                self._extend(cps)
                if rec is not None:
                    rec.bezier(style, cps)
        flush(closed=bool(drawing.get("closePath")))

    def add_text(self, text: str, size_pt: float, origin, direction, color, bbox, pm: PageMap,
                 ocg: str | None) -> None:
        if size_pt <= 0:
            return
        rgb = rgb_of(color)
        attribs = self._attribs("TEXT", rgb, ocg, None)
        insert = pm.pt(origin[0], origin[1])
        rotation = pm.angle(direction[0], direction[1])
        height = max(size_pt * PT2MM * TEXT_HEIGHT_RATIO, 0.01)
        attribs["insert"] = insert
        attribs["style"] = "OpenSans"
        self._tick()
        self.text_entities += 1
        self.msp.add_text(text, height=height, rotation=rotation, dxfattribs=attribs)
        x0, y0, x1, y1 = bbox
        self._extend((insert, pm.pt(x0, y0), pm.pt(x1, y1)))
        if self.preview is not None and attribs["layer"].lower() not in self._hidden:
            self.preview.text(insert, text, size_pt * PT2MM, rotation, rgb)

    # -- output ------------------------------------------------------------------------
    def finish_header(self) -> None:
        if not self.has_extents:
            return
        # ezdxf copies the model-space layout extents into $EXTMIN/$EXTMAX when saving
        self.msp.reset_extents((self.min_x, self.min_y, 0.0), (self.max_x, self.max_y, 0.0))
        width = self.max_x - self.min_x
        height = self.max_y - self.min_y
        center = ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)
        view_height = max(height, width / 1.6, 1.0) * 1.05
        self.doc.set_modelspace_vport(height=view_height, center=center)


def _ocg_state(pdf: pymupdf.Document) -> tuple[bool, set[str]]:
    """(document has optional content, names of layers that are off by default).

    Switches every layer on afterwards (in memory only): MuPDF does not extract the
    content of hidden layers, while the DXF gets that content on layers that are off.
    """
    try:
        ocgs = pdf.get_ocgs() or {}
    except Exception:  # broken optional-content dictionaries are not fatal
        return False, set()
    off = {info.get("name") for info in ocgs.values() if not info.get("on", True) and info.get("name")}
    if off:
        try:
            for item in pdf.layer_ui_configs():
                if not item.get("on") and not item.get("locked"):
                    pdf.set_layer_ui_config(item["number"], action=0)
        except Exception as exc:  # the hidden content is then simply not extracted
            print(f"cad-engine: cannot switch hidden PDF layers on ({exc})", file=sys.stderr)
    return bool(ocgs), off


def _text_layer_index(page: pymupdf.Page) -> dict[tuple[float, float], str]:
    """Glyph origin -> optional-content layer name (only for text inside a layer)."""
    index: dict[tuple[float, float], str] = {}
    try:
        traces = page.get_texttrace()
    except Exception:  # text trace is best effort (layer names only)
        return index
    for span in traces:
        name = span.get("layer")
        if not name:
            continue
        for char in span.get("chars", ()):
            ox, oy = char[2]
            index[(round(ox, 1), round(oy, 1))] = name
    return index


def convert(src: str, dst: str, *, budget: Budget, meta: dict, preview_path: str | None = None,
            max_entities: int = MAX_ENTITIES, max_pages: int = MAX_PAGES) -> None:
    pdf = open_pdf(src)
    pages = pdf.page_count
    meta["pages"] = pages
    if pages > max_pages:
        raise InputError("too_many_pages", f"the PDF has {pages} pages; the limit is {max_pages} pages")

    has_ocg, layers_off = _ocg_state(pdf)
    writer = DxfWriter(budget, max_entities=max_entities, record_preview=preview_path is not None)
    writer.layers_off = layers_off
    flags = text_flags()
    images = 0
    x_offset = 0.0
    try:
        for pno in range(pages):
            budget.check()
            page = pdf[pno]
            pm = PageMap(page, x_offset)
            for drawing in page.get_drawings():
                writer.add_path(drawing, pm)
            budget.check()
            text_layers = _text_layer_index(page) if has_ocg else None
            for block in page.get_text("dict", flags=flags)["blocks"]:
                if block.get("type", 0) != 0:
                    continue
                for line in block["lines"]:
                    direction = line["dir"]
                    for span in line["spans"]:
                        if span.get("alpha", 255) == 0:  # invisible text (e.g. OCR layer of a scan)
                            continue
                        text = clean_text(span["text"])
                        if not text:
                            continue
                        origin = span["origin"]
                        ocg = None
                        if text_layers:
                            ocg = text_layers.get((round(origin[0], 1), round(origin[1], 1)))
                        writer.add_text(text, span["size"], origin, direction, span["color"], span["bbox"],
                                        pm, ocg)
            images += len(page.get_images())
            width_mm = page.rect.width * PT2MM
            x_offset += width_mm + max(PAGE_GAP_MIN_MM, PAGE_GAP_RATIO * width_mm)
    finally:
        meta["entities"] = writer.entities
        meta["layers"] = writer.layer_count
        meta["text_entities"] = writer.text_entities
        meta["images"] = images

    has_vectors = writer.entities > 0
    meta["has_vectors"] = has_vectors
    meta["text_as_outlines"] = writer.text_entities == 0 and writer.vector_entities > 0
    meta["traced_pages"] = []
    if not has_vectors:
        raise InputError("no_vectors", "the PDF has no vector drawings or text (scanned image?)")

    budget.check()
    writer.finish_header()
    writer.doc.saveas(outputs.part_path(dst))
    if preview_path is not None and writer.preview is not None:
        budget.check()
        bounds = (writer.min_x, writer.min_y, writer.max_x, writer.max_y)
        outputs.write_part(preview_path, writer.preview.render_png(bounds))
    budget.check()
    outputs.commit(dst, preview_path)
    print(f"cad-engine: pdf2dxf pages={pages} entities={writer.entities} texts={writer.text_entities} "
          f"layers={writer.layer_count} images={images}", file=sys.stderr)
