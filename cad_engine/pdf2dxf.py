# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""PDF -> DXF: PyMuPDF vector/text extraction written as DXF with ezdxf.

What ends up in the DXF (AutoCAD 2010, Y up; millimetres at 1 pt = 25.4/72 mm by default):

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

Optional recognition (all off by default, see `Options` and README):
``--arcs`` circles/arcs, ``--hatch-fills`` solid HATCH, ``--linetypes`` DASHED/DOT,
``--merge`` joined pieces and dropped duplicates, ``--scale``/``--units`` output units,
``--raster-fallback`` traced polylines for pages that are only images (OpenCV).
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from cad_engine import outputs
from cad_engine.budget import Budget
from cad_engine.errors import InputError
from cad_engine.preview import PreviewRecorder
from cad_engine.recognize import (
    ArcRun,
    DashRun,
    arc_beziers,
    bezier_chain_knots,
    find_arc_runs,
    find_dash_runs,
    line_as_bezier,
    simplify_collinear,
)

if TYPE_CHECKING:
    import pymupdf

PT2MM = 25.4 / 72.0
MM_PER_INCH = 25.4
MAX_PAGES = 50
MAX_ENTITIES = 500_000
PAGE_GAP_MIN_MM = 20.0
PAGE_GAP_RATIO = 0.10
TEXT_HEIGHT_RATIO = 0.72  # font size (em) -> approximate cap height
BACKGROUND_AREA_RATIO = 0.95  # a white fill covering this much of the page is the paper, not content
CHECK_EVERY = 1000  # budget checkpoint every N entities

# Tolerances in paper millimetres (multiplied by the output factor).
JOIN_TOL_MM = 1e-4  # end of one segment = start of the next
EPS_MM = 1e-6
MERGE_LATERAL_TOL_MM = 0.01  # --merge: collinear vertices
DASH_LATERAL_TOL_MM = 0.01  # --linetypes: dashes on one line
DASH_MAX_MM = 25.0  # --linetypes: longest dash / gap of a dashed run
DOT_MAX_MM = 0.6  # --linetypes: dashes up to this length (and not longer than the gap) are dots

SCALES = {"asis": 1.0, "1-50": 50.0, "1-100": 100.0, "1-200": 200.0}
UNITS = ("mm", "inch")
# Pattern length (drawing units) of the linetypes ezdxf defines with setup=True; the entity
# linetype scale is set so that one pattern repeat equals the period found in the PDF.
LINETYPE_PERIOD = {"DASHED": 0.6, "DOT": 0.2}
LINETYPE_DASH_RATIO = {"DASHED": 0.5 / 0.6, "DOT": 0.0}  # dash length / period (for the preview)
TRACED_LAYER = "TRACED"
_KNOTS4 = (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)

VALID_LINEWEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140,
                     158, 200, 211)
_LAYER_NAME_BAD = set('<>/\\":;?*=`')
MAX_LAYER_NAME = 255


@dataclass(frozen=True)
class Options:
    """pdf2dxf flags (CLI ``--arcs`` ... ``--units``); the defaults reproduce engine 1.0."""

    arcs: bool = False
    hatch_fills: bool = False
    linetypes: bool = False
    merge: bool = False
    raster_fallback: bool = False
    scale: str = "asis"
    units: str = "mm"

    def __post_init__(self) -> None:
        if self.scale not in SCALES:
            raise ValueError(f"unknown scale {self.scale!r}")
        if self.units not in UNITS:
            raise ValueError(f"unknown units {self.units!r}")

    @property
    def factor(self) -> float:
        """Output drawing units per paper millimetre."""
        k = SCALES[self.scale]
        return k / MM_PER_INCH if self.units == "inch" else k

    @property
    def recognizes(self) -> bool:
        return self.arcs or self.hatch_fills or self.linetypes or self.merge


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


def parse_dashes(dashes) -> tuple[float, float] | None:
    """PyMuPDF ``dashes`` (``"[ 6 3 ] 0"``, page-space pt) -> (dash, gap) in pt, None if solid."""
    if not dashes or not isinstance(dashes, str) or "[" not in dashes:
        return None
    inner = dashes[dashes.index("[") + 1: dashes.index("]") if "]" in dashes else len(dashes)]
    try:
        values = [abs(float(v)) for v in inner.replace(",", " ").split()]
    except ValueError:
        return None
    if not values or sum(values) <= 0:
        return None
    if len(values) % 2:  # an odd array repeats: [3] = 3 on, 3 off
        values = values * 2
    pairs = len(values) // 2
    return sum(values[0::2]) / pairs, sum(values[1::2]) / pairs  # mean dash, mean gap


def linetype_for(dash: float, gap: float) -> str:
    """Dash / gap in paper mm -> ``DOT`` (short dashes, not longer than the gap) or ``DASHED``."""
    return "DOT" if dash <= DOT_MAX_MM and dash <= gap else "DASHED"


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class PageMap:
    """Maps PyMuPDF page coordinates (unrotated, y down, pt) to DXF (output units, y up)."""

    __slots__ = ("m", "x0", "y0", "y1", "ox", "area", "k", "width", "height")

    def __init__(self, page: pymupdf.Page, x_offset: float, factor: float = 1.0) -> None:
        rect = page.rect  # rotated (display) page rectangle
        if page.rotation:
            rm = page.rotation_matrix
            self.m: tuple[float, ...] | None = (rm.a, rm.b, rm.c, rm.d, rm.e, rm.f)
        else:
            self.m = None
        self.x0 = rect.x0
        self.y0 = rect.y0
        self.y1 = rect.y1
        self.width = rect.width
        self.height = rect.height
        self.ox = x_offset
        self.area = abs(rect.width * rect.height)
        self.k = PT2MM * factor  # output units per pt

    def is_background(self, rect) -> bool:
        return rect is not None and abs(rect.width * rect.height) >= BACKGROUND_AREA_RATIO * self.area

    def pt(self, x: float, y: float) -> tuple[float, float]:
        m = self.m
        if m is not None:
            x, y = m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]
        return ((x - self.x0) * self.k + self.ox, (self.y1 - y) * self.k)

    def display(self, x: float, y: float) -> tuple[float, float]:
        """Display (rotated page) coordinates in pt -> DXF."""
        return ((x - self.x0) * self.k + self.ox, (self.y1 - y) * self.k)

    def angle(self, dx: float, dy: float) -> float:
        """Text direction (page coords) -> DXF rotation in degrees (CCW)."""
        m = self.m
        if m is not None:
            dx, dy = m[0] * dx + m[2] * dy, m[1] * dx + m[3] * dy
        deg = -math.degrees(math.atan2(dy, dx)) % 360.0
        deg = round(deg, 6)
        return 0.0 if deg >= 360.0 else deg + 0.0


class _Ctx:
    """Per-path emission context: DXF attributes, dedupe key part, preview style."""

    __slots__ = ("attribs", "akey", "style", "rec")

    def __init__(self, attribs: dict, style, rec) -> None:
        self.attribs = attribs
        self.akey = tuple(sorted(attribs.items()))
        self.style = style
        self.rec = rec


def _rounded(points) -> tuple:
    return tuple(round(v, 6) for p in points for v in p)


class DxfWriter:
    """Accumulates DXF entities, layers, extents, limits and preview geometry."""

    def __init__(self, budget: Budget, *, max_entities: int, record_preview: bool,
                 options: Options | None = None) -> None:
        import ezdxf
        from ezdxf import units

        self._ezdxf = ezdxf
        self.opts = options or Options()
        self.factor = self.opts.factor
        self.doc = ezdxf.new("R2010", setup=True)
        if self.opts.units == "inch":
            self.doc.units = units.IN
            self.doc.header["$MEASUREMENT"] = 0  # imperial
        else:
            self.doc.units = units.MM
        self.msp = self.doc.modelspace()
        self.budget = budget
        self.max_entities = max_entities
        self.entities = 0
        self.text_entities = 0
        self.vector_entities = 0
        self.traced_entities = 0
        self.recognized = {"circles": 0, "arcs": 0, "hatches": 0, "linetypes": 0, "merged": 0}
        self._seen: set | None = set() if self.opts.merge else None
        self._layers: dict[str, tuple[str, tuple[int, int, int] | None]] = {}  # lower name -> (name, rgb)
        self.layers_off: set[str] = set()  # PDF layer names that are off
        self._hidden: set[str] = set()  # lower-case DXF layer names created off
        self.min_x = self.min_y = math.inf
        self.max_x = self.max_y = -math.inf
        self.preview = PreviewRecorder(unit_scale=self.factor) if record_preview else None
        k = self.factor
        self._join_tol = JOIN_TOL_MM * k
        self._eps = EPS_MM * k

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

    def _admit(self, key) -> bool:
        """Count a new entity; with --merge drop exact duplicates (same type, geometry, attributes)."""
        if self._seen is not None:
            if key in self._seen:
                self.recognized["merged"] += 1
                return False
            self._seen.add(key)
        self._tick()
        return True

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

    # -- entity primitives -------------------------------------------------------------
    def _polyline(self, pts: list, closed: bool, ctx: _Ctx) -> None:
        if self.opts.merge:
            pts = simplify_collinear(pts, MERGE_LATERAL_TOL_MM * self.factor, self._eps)
            if len(pts) < 2:
                return
            if not self._admit(("P", ctx.akey, closed, _rounded(pts))):
                return
        else:
            self._tick()
        self.vector_entities += 1
        entity = self.msp.add_lwpolyline(pts, close=closed, dxfattribs=ctx.attribs)
        if "linetype" in ctx.attribs:
            entity.dxf.flags |= 128  # linetype pattern runs over the vertices (PLINEGEN)
        self._extend(pts)
        if ctx.rec is not None:
            ctx.rec.polyline(ctx.style, list(pts), closed)

    def _spline(self, beziers: list, ctx: _Ctx) -> None:
        """One Bézier -> SPLINE with its 4 control points; several joined ones -> one exact
        degree-3 B-spline with triple interior knots."""
        cps = list(beziers[0])
        for bez in beziers[1:]:
            cps.extend(bez[1:])
        if self._seen is not None:
            if not self._admit(("S", ctx.akey, _rounded(cps))):
                return
        else:
            self._tick()
        self.vector_entities += 1
        if len(beziers) == 1:
            self.msp.add_open_spline(cps, degree=3, dxfattribs=ctx.attribs)
        else:
            self.msp.add_open_spline(cps, degree=3, knots=bezier_chain_knots(len(beziers)),
                                     dxfattribs=ctx.attribs)
        self._extend(cps)
        if ctx.rec is not None:
            for bez in beziers:
                ctx.rec.bezier(ctx.style, list(bez))

    def _arc(self, run: ArcRun, segs: list, ctx: _Ctx) -> None:
        center = (run.cx, run.cy)
        if run.circle:
            key = ("C", ctx.akey, _rounded([center]), round(run.r, 6))
        else:
            start, end = run.dxf_angles()
            key = ("A", ctx.akey, _rounded([center]), round(run.r, 6), round(start, 6), round(end, 6))
        if self._seen is not None:
            if not self._admit(key):
                return
        else:
            self._tick()
        self.vector_entities += 1
        if run.circle:
            self.msp.add_circle(center, run.r, dxfattribs=ctx.attribs)
            self.recognized["circles"] += 1
            pieces = arc_beziers(run.cx, run.cy, run.r, 0.0, 2.0 * math.pi)
            self._extend([(run.cx - run.r, run.cy - run.r), (run.cx + run.r, run.cy + run.r)])
        else:
            self.msp.add_arc(center, run.r, start, end, dxfattribs=ctx.attribs)
            self.recognized["arcs"] += 1
            pieces = arc_beziers(run.cx, run.cy, run.r, run.start, run.sweep)
            for seg in segs[run.first:run.end]:
                self._extend(seg[1:])
        if ctx.rec is not None:
            for bez in pieces:
                ctx.rec.bezier(ctx.style, list(bez))

    def _dash_line(self, run: DashRun, ctx: _Ctx) -> None:
        k = self.factor
        ltype = linetype_for(run.dash / k, run.gap / k)
        period = run.dash + run.gap
        attribs = dict(ctx.attribs)
        attribs["linetype"] = ltype
        attribs["ltscale"] = period / LINETYPE_PERIOD[ltype]
        pts = [run.start, run.stop]
        if self._seen is not None:
            if not self._admit(("L", tuple(sorted(attribs.items())), _rounded(pts))):
                return
        else:
            self._tick()
        self.vector_entities += 1
        self.recognized["linetypes"] += 1
        self.msp.add_line(run.start, run.stop, dxfattribs=attribs)
        self._extend(pts)
        if ctx.rec is not None:
            rgb, width_pt, _ = ctx.style
            on = period * LINETYPE_DASH_RATIO[ltype]
            ctx.rec.polyline((rgb, width_pt, (on, period - on)), pts, False)

    def _hatch(self, subpaths: list, ctx: _Ctx, rgb) -> None:
        loops: list[list[tuple]] = []
        for kind, data in subpaths:
            if kind == "p":
                loops.append([("l", data[i], data[(i + 1) % 4]) for i in range(4)])
                continue
            # edges joined exactly (the PDF may leave gaps up to the join tolerance), loop closed
            segs = [data[0]]
            for seg in data[1:]:
                segs.append((seg[0], segs[-1][-1], *seg[2:]))
            if _dist(segs[-1][-1], segs[0][1]) > self._join_tol:
                segs.append(("l", segs[-1][-1], segs[0][1]))
            else:
                last = segs[-1]
                segs[-1] = (*last[:-1], segs[0][1])
            if all(s[0] == "l" for s in segs) and len(segs) < 3:
                continue  # zero-area loop
            loops.append(segs)
        if not loops:
            return
        points = [p for loop in loops for seg in loop for p in seg[1:]]
        if self._seen is not None:
            if not self._admit(("H", ctx.akey, _rounded(points))):
                return
        else:
            self._tick()
        self.vector_entities += 1
        self.recognized["hatches"] += 1
        hatch = self.msp.add_hatch(color=256, dxfattribs=ctx.attribs)  # solid fill, BYLAYER colour
        for loop in loops:
            if all(s[0] == "l" for s in loop):
                hatch.paths.add_polyline_path([s[1] for s in loop], is_closed=True)
            else:
                edges = hatch.paths.add_edge_path()
                for seg in loop:
                    if seg[0] == "l":
                        edges.add_line(seg[1], seg[2])
                    else:
                        edges.add_spline(control_points=seg[1:], knot_values=_KNOTS4, degree=3)
        self._extend(points)
        if ctx.rec is not None:
            ctx.rec.fill(rgb, loops)

    # -- paths -------------------------------------------------------------------------
    def _subpaths(self, items, pm: PageMap) -> list[tuple[str, list]]:
        """Split path items into ("s", contiguous segments) and ("p", 4 corners of re/qu)."""
        P = pm.pt
        tol = self._join_tol
        out: list[tuple[str, list]] = []
        cur: list | None = None
        last = None
        for item in items:
            kind = item[0]
            if kind == "l" or kind == "c":
                pts = [P(p.x, p.y) for p in item[1:3 if kind == "l" else 5]]
                a = pts[0]
                if cur is None or abs(last[0] - a[0]) > tol or abs(last[1] - a[1]) > tol:
                    cur = []
                    out.append(("s", cur))
                cur.append((kind, *pts))
                last = pts[-1]
            elif kind == "re":
                r = item[1]
                out.append(("p", [P(r.x0, r.y0), P(r.x1, r.y0), P(r.x1, r.y1), P(r.x0, r.y1)]))
                cur = None
            elif kind == "qu":
                q = item[1]
                out.append(("p", [P(p.x, p.y) for p in (q.ul, q.ur, q.lr, q.ll)]))
                cur = None
        return out

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
        opts = self.opts
        rec = self.preview if attribs["layer"].lower() not in self._hidden else None
        subpaths = self._subpaths(drawing["items"], pm)
        if not subpaths:
            return

        if fill_only and opts.hatch_fills:
            self._hatch(subpaths, _Ctx(attribs, (rgb, width_pt, None), rec), rgb)
            return

        dash_style = None
        if opts.linetypes and not fill_only:
            pattern = parse_dashes(drawing.get("dashes"))
            if pattern is not None:
                dash_pt, gap_pt = pattern
                ltype = linetype_for(dash_pt * PT2MM, gap_pt * PT2MM)
                period = (dash_pt + gap_pt) * pm.k
                attribs["linetype"] = ltype
                attribs["ltscale"] = period / LINETYPE_PERIOD[ltype]
                on = period * LINETYPE_DASH_RATIO[ltype]
                dash_style = (on, period - on)
        ctx = _Ctx(attribs, (rgb, width_pt, dash_style), rec)
        typed_before = self.vector_entities

        runs: dict[int, DashRun] = {}
        if opts.linetypes and not fill_only and dash_style is None and len(subpaths) >= 4:
            singles = [(d[0][1], d[0][2]) if k == "s" and len(d) == 1 and d[0][0] == "l" else None
                       for k, d in subpaths]
            k = self.factor
            for run in find_dash_runs(singles, lat_tol=DASH_LATERAL_TOL_MM * k, max_len=DASH_MAX_MM * k,
                                      eps=self._eps):
                runs[run.first] = run

        close = bool(drawing.get("closePath"))
        last = len(subpaths) - 1
        i = 0
        while i <= last:
            run = runs.get(i)
            if run is not None:
                self._dash_line(run, ctx)
                i = run.end
                continue
            kind, data = subpaths[i]
            if kind == "p":
                self._polyline(data, True, ctx)
            else:
                self._segments(data, ctx, fill_only, close and i == last)
            i += 1
        if dash_style is not None:
            self.recognized["linetypes"] += self.vector_entities - typed_before

    def _segments(self, segs: list, ctx: _Ctx, fill_only: bool, close: bool) -> None:
        """One contiguous subpath: arcs (--arcs), then line runs -> LWPOLYLINE and Bézier
        runs -> SPLINE (joined with --merge; a filled contour becomes one SPLINE)."""
        opts = self.opts
        close_poly = False
        if close:
            if not opts.recognizes or all(s[0] == "l" for s in segs):
                close_poly = segs[-1][0] == "l"  # engine 1.0: the last line run is closed
            elif _dist(segs[-1][-1], segs[0][1]) > self._join_tol:
                segs = [*segs, ("l", segs[-1][-1], segs[0][1])]
        arc_runs = {run.first: run for run in find_arc_runs(segs)} if opts.arcs else {}
        n = len(segs)
        i = 0
        while i < n:
            run = arc_runs.get(i)
            if run is not None:
                self._arc(run, segs, ctx)
                i = run.end
                continue
            j = i + 1
            while j < n and j not in arc_runs:
                j += 1
            self._block(segs[i:j], ctx, fill_only, close_poly and j == n)
            i = j

    def _block(self, block: list, ctx: _Ctx, fill_only: bool, close_last: bool) -> None:
        merge = self.opts.merge
        if merge and fill_only and len(block) > 1 and any(s[0] == "c" for s in block):
            # a glyph / fill contour: one SPLINE instead of alternating polylines and splines
            beziers = [s[1:] if s[0] == "c" else line_as_bezier(s[1], s[2]) for s in block]
            pieces = sum(1 for idx, s in enumerate(block)
                         if s[0] == "c" or idx == 0 or block[idx - 1][0] != "l")
            self._spline(beziers, ctx)
            self.recognized["merged"] += pieces - 1
            return
        n = len(block)
        k = 0
        while k < n:
            kind = block[k][0]
            m = k
            while m < n and block[m][0] == kind:
                m += 1
            if kind == "l":
                pts = [block[k][1]]
                pts.extend(s[2] for s in block[k:m])
                self._polyline(pts, close_last and m == n, ctx)
            else:
                curves = [s[1:] for s in block[k:m]]
                if merge and len(curves) > 1:
                    self._spline(curves, ctx)
                    self.recognized["merged"] += len(curves) - 1
                else:
                    for bez in curves:
                        self._spline([bez], ctx)
            k = m

    # -- text and traced outlines ------------------------------------------------------
    def add_text(self, text: str, size_pt: float, origin, direction, color, bbox, pm: PageMap,
                 ocg: str | None) -> None:
        if size_pt <= 0:
            return
        rgb = rgb_of(color)
        attribs = self._attribs("TEXT", rgb, ocg, None)
        insert = pm.pt(origin[0], origin[1])
        rotation = pm.angle(direction[0], direction[1])
        height = max(size_pt * pm.k * TEXT_HEIGHT_RATIO, 0.01 * self.factor)
        if self._seen is not None:
            key = ("T", tuple(sorted(attribs.items())), text, _rounded([insert]), round(height, 6),
                   round(rotation, 6))
            if not self._admit(key):
                return
        else:
            self._tick()
        attribs["insert"] = insert
        attribs["style"] = "OpenSans"
        self.text_entities += 1
        self.msp.add_text(text, height=height, rotation=rotation, dxfattribs=attribs)
        x0, y0, x1, y1 = bbox
        self._extend((insert, pm.pt(x0, y0), pm.pt(x1, y1)))
        if self.preview is not None and attribs["layer"].lower() not in self._hidden:
            self.preview.text(insert, text, size_pt * pm.k, rotation, rgb)

    def add_traced(self, contours: list, pm: PageMap) -> int:
        """Closed polylines traced from a page image (display coordinates in pt) on layer TRACED."""
        if not self.doc.layers.has_entry(TRACED_LAYER):
            self.doc.layers.add(TRACED_LAYER, color=7)
            self._layers[TRACED_LAYER.lower()] = (TRACED_LAYER, None)
        attribs = {"layer": TRACED_LAYER}
        added = 0
        for contour in contours:
            pts = [pm.display(x, y) for x, y in contour]
            self._tick()
            self.traced_entities += 1
            added += 1
            self.msp.add_lwpolyline(pts, close=True, dxfattribs=attribs)
            self._extend(pts)
            if self.preview is not None:
                self.preview.polyline(((0, 0, 0), 0.0, None), pts, True)
        return added

    # -- output ------------------------------------------------------------------------
    def finish_header(self) -> None:
        if not self.has_extents:
            return
        # ezdxf copies the model-space layout extents into $EXTMIN/$EXTMAX when saving
        self.msp.reset_extents((self.min_x, self.min_y, 0.0), (self.max_x, self.max_y, 0.0))
        width = self.max_x - self.min_x
        height = self.max_y - self.min_y
        center = ((self.min_x + self.max_x) / 2.0, (self.min_y + self.max_y) / 2.0)
        view_height = max(height, width / 1.6, self.factor) * 1.05
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


def _add_page_text(writer: DxfWriter, page: pymupdf.Page, pm: PageMap, flags: int, has_ocg: bool) -> None:
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
                writer.add_text(text, span["size"], origin, direction, span["color"], span["bbox"], pm, ocg)


def convert(src: str, dst: str, *, budget: Budget, meta: dict, preview_path: str | None = None,
            max_entities: int = MAX_ENTITIES, max_pages: int = MAX_PAGES,
            options: Options | None = None) -> None:
    opts = options or Options()
    meta["scale"] = opts.scale
    meta["units"] = opts.units
    warnings = meta.setdefault("warnings", [])
    if warnings is None:
        warnings = meta["warnings"] = []
    pdf = open_pdf(src)
    pages = pdf.page_count
    meta["pages"] = pages
    if pages > max_pages:
        raise InputError("too_many_pages", f"the PDF has {pages} pages; the limit is {max_pages} pages")

    has_ocg, layers_off = _ocg_state(pdf)
    writer = DxfWriter(budget, max_entities=max_entities, record_preview=preview_path is not None,
                       options=opts)
    writer.layers_off = layers_off
    flags = text_flags()
    images = 0
    x_offset = 0.0
    traced_pages: list[int] = []
    tracer = None  # cv2 module once loaded; False when unavailable
    try:
        for pno in range(pages):
            budget.check()
            page = pdf[pno]
            pm = PageMap(page, x_offset, writer.factor)
            before = writer.entities
            for drawing in page.get_drawings():
                writer.add_path(drawing, pm)
            budget.check()
            _add_page_text(writer, page, pm, flags, has_ocg)
            page_images = len(page.get_images())
            images += page_images
            if opts.raster_fallback and page_images and writer.entities == before:
                if tracer is None:
                    from cad_engine import raster

                    tracer = raster.load_cv2() or False
                    if tracer is False:
                        warnings.append("raster_fallback_unavailable")
                        print("cad-engine: --raster-fallback needs OpenCV (pip install "
                              "'pdf-cad-engine[raster]'); image-only pages stay empty", file=sys.stderr)
                if tracer:
                    from cad_engine import raster

                    budget.check()
                    if writer.add_traced(raster.trace_page(page, tracer, budget), pm):
                        traced_pages.append(pno)
            width = page.rect.width * PT2MM
            x_offset += (width + max(PAGE_GAP_MIN_MM, PAGE_GAP_RATIO * width)) * writer.factor
    finally:
        meta["entities"] = writer.entities
        meta["layers"] = writer.layer_count
        meta["text_entities"] = writer.text_entities
        meta["images"] = images
        meta["recognized"] = dict(writer.recognized)
        meta["traced_pages"] = traced_pages

    meta["has_vectors"] = writer.entities - writer.traced_entities > 0
    meta["text_as_outlines"] = writer.text_entities == 0 and writer.vector_entities > 0
    if writer.entities == 0:
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
          f"layers={writer.layer_count} images={images} traced={traced_pages} "
          f"recognized={writer.recognized}", file=sys.stderr)
