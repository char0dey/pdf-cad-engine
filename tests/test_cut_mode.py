# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""pdf2dxf --cut (engine 1.7): closed contours for cutting programs, DXF R2000.

Inputs are the synthetic logos of `make_sample` (no customer file): PDF user space (y up) maps
to DXF as x · k, y · k with k = 1/72 in or 25.4/72 mm per pt."""

from __future__ import annotations

import math
from collections import Counter

import ezdxf
import pytest
from shapely.geometry import Point, Polygon

import make_sample
from conftest import has_cv2, preview_pixel, run_engine

NODE_FLAGS = ("--arcs", "--hatch-fills", "--linetypes", "--merge")  # what the service passes for «cad»
FORBIDDEN = {"HATCH", "SPLINE", "ARC", "CIRCLE", "ELLIPSE"}
PT2IN = 1 / 72
PT2MM = 25.4 / 72


def cut(pdf, tmp_path, *extra, units="inch", preview=False):
    out = tmp_path / f"{pdf.stem}-cut-{units}.dxf"
    args = ["pdf2dxf", "--in", pdf, "--out", out, "--cut", "--units", units, *extra]
    png = tmp_path / f"{pdf.stem}-cut-{units}.png"
    if preview:
        args += ["--preview", png]
    run = run_engine(*args)
    assert run.returncode == 0, run.stderr
    doc = ezdxf.readfile(out)
    auditor = doc.audit()
    assert not auditor.has_errors, [str(e.message) for e in auditor.errors]
    return run.meta, doc, png


def entities(doc, dxftype=None, layer=None):
    return [e for e in doc.modelspace()
            if (dxftype is None or e.dxftype() == dxftype) and (layer is None or e.dxf.layer == layer)]


def polygon(e) -> Polygon:
    return Polygon([(p[0], p[1]) for p in e.get_points("xy")])


def layer_names(doc) -> set[str]:
    return {e.dxf.layer for e in doc.modelspace()}


# -- 3.1 the flag, the format, the header ------------------------------------------------------
@pytest.mark.parametrize("units, insunits, measurement", [("inch", 1, 0), ("mm", 4, 1)])
def test_cut_overrides_recognition_and_writes_r2000(plate_pdf, tmp_path, units, insunits, measurement):
    meta, doc, _ = cut(plate_pdf, tmp_path, *NODE_FLAGS, units=units)
    assert doc.dxfversion == "AC1015"
    # Inkscape «Read from file» scales by $MEASUREMENT only (the 19.11 prototype trap)
    assert doc.header["$INSUNITS"] == insunits and doc.header["$MEASUREMENT"] == measurement
    assert not {e.dxftype() for e in doc.modelspace()} & FORBIDDEN
    assert meta["purpose"] == "cut"
    assert meta["recognized"] == {"circles": 0, "arcs": 0, "hatches": 0, "linetypes": 0, "merged": 0}


@pytest.mark.parametrize("units, insunits, measurement", [("inch", 1, 0), ("mm", 4, 1)])
def test_cad_header_units_agree(logo_pdf, tmp_path, units, insunits, measurement):
    out = tmp_path / f"logo-cad-{units}.dxf"
    run = run_engine("pdf2dxf", "--in", logo_pdf, "--out", out, *NODE_FLAGS, "--units", units)
    assert run.returncode == 0, run.stderr
    doc = ezdxf.readfile(out)
    assert doc.header["$INSUNITS"] == insunits and doc.header["$MEASUREMENT"] == measurement
    assert run.meta["purpose"] == "cad" and run.meta["contours"] is None


# -- 3.2 fills ---------------------------------------------------------------------------------
def test_logo_for_cutting(logo_pdf, tmp_path):
    """Spec «Логотип для різання (випадок 19.11.2026)»: only closed LWPOLYLINEs on one layer per
    colour, the overlapping letters one outline, the «O» an outline with a hole, inch sizes."""
    meta, doc, _ = cut(logo_pdf, tmp_path, *NODE_FLAGS)
    types = Counter(e.dxftype() for e in doc.modelspace())
    assert set(types) == {"LWPOLYLINE"}
    assert all(e.closed for e in doc.modelspace())
    assert layer_names(doc) == {"CUT_0D338C", "CUT_4D99E5"}
    dark = [polygon(e) for e in entities(doc, layer="CUT_0D338C")]
    assert len(dark) == 3  # the joined letters, the «O» and its hole
    word = max(dark, key=lambda p: p.area)
    x0, y0 = make_sample.LETTERS[0]
    x1 = make_sample.LETTERS[-1][0] + make_sample.LETTER_W
    minx, miny, maxx, maxy = word.bounds
    assert (minx, miny) == pytest.approx((x0 * PT2IN, y0 * PT2IN), abs=1e-4)
    assert (maxx, maxy) == pytest.approx((x1 * PT2IN, (y0 + make_sample.LETTER_H) * PT2IN), abs=1e-4)
    overlap = Point((make_sample.LETTERS[1][0] + 4) * PT2IN, (y0 + 45) * PT2IN)
    assert word.contains(overlap)  # no gap where the letters overlap
    ox, oy, ow, oh = make_sample.O_BOX
    o_outer = [p for p in dark if p is not word and p.contains(Point((ox + 5) * PT2IN, (oy + 45) * PT2IN))]
    o_centre = Point((ox + ow / 2) * PT2IN, (oy + oh / 2) * PT2IN)
    o_hole = [p for p in dark if p is not word and p.contains(o_centre)]
    assert len(o_outer) == 1 and len(o_hole) == 2  # the hole lies inside the outer outline too
    assert meta["contours"] == 4 and meta["cut_text_kept"] == 0
    for layer in doc.layers:
        if layer.dxf.name.startswith("CUT_"):
            assert layer.dxf.color not in (0, 7, 255)


def test_white_text_on_a_coloured_plate(plate_pdf, tmp_path):
    meta, doc, _ = cut(plate_pdf, tmp_path)
    assert layer_names(doc) == {"CUT_0000FF"}  # no layer of white letters
    contours = [polygon(e) for e in entities(doc, layer="CUT_0000FF")]
    disc = max(contours, key=lambda p: p.area)
    holes = [p for p in contours if p is not disc]
    assert len(holes) == len(make_sample.PLATE_LETTERS)
    assert all(disc.contains(h) for h in holes)
    assert meta["contours"] == 5


def test_white_logo_without_background(white_logo_pdf, tmp_path):
    meta, doc, _ = cut(white_logo_pdf, tmp_path)
    assert layer_names(doc) == {"CUT_FFFFFF"}
    assert meta["contours"] == 2 and meta["entities"] == 2


def test_later_fill_covers_the_earlier_one(overlap_colors_pdf, tmp_path):
    _, doc, _ = cut(overlap_colors_pdf, tmp_path, units="mm")
    red = [polygon(e) for e in entities(doc, layer="CUT_FF0000")]
    blue = [polygon(e) for e in entities(doc, layer="CUT_0000FF")]
    assert len(red) == 1 and len(blue) == 1
    square = 200 * 200 * PT2MM ** 2
    disc = math.pi * (60 * PT2MM) ** 2
    assert blue[0].area == pytest.approx(disc, rel=2e-3)
    assert red[0].area == pytest.approx(square - disc / 2, rel=2e-3)  # half the disc lies over it
    assert not red[0].contains(Point(280 * PT2MM, 200 * PT2MM))


def test_even_odd_star(star_pdf, tmp_path):
    _, doc, _ = cut(star_pdf, tmp_path, units="mm")
    rays = [polygon(e) for e in entities(doc)]
    assert len(rays) == 5
    cx, cy = make_sample.STAR_CENTER
    assert not any(p.contains(Point(cx * PT2MM, cy * PT2MM)) for p in rays)  # the pentagon is empty
    tip = make_sample.star_points()[0]
    inner = Point((0.9 * tip[0] + 0.1 * cx) * PT2MM, (0.9 * tip[1] + 0.1 * cy) * PT2MM)
    assert any(p.contains(inner) for p in rays)


def test_paper_is_skipped_and_a_paper_only_pdf_is_empty(paper_only_pdf, tmp_path):
    run = run_engine("pdf2dxf", "--in", paper_only_pdf, "--out", tmp_path / "paper.dxf", "--cut")
    assert run.returncode == 2 and run.meta["code"] == "empty_drawing"
    assert not (tmp_path / "paper.dxf").exists()
    # «cad» is unchanged: the same PDF has no vectors there
    run = run_engine("pdf2dxf", "--in", paper_only_pdf, "--out", tmp_path / "paper-cad.dxf", *NODE_FLAGS)
    assert run.returncode == 2 and run.meta["code"] == "no_vectors"


# -- 3.3 strokes -------------------------------------------------------------------------------
def test_dashed_segments_stay_separate_and_solid(dash_segments_pdf, tmp_path):
    _, doc, _ = cut(dash_segments_pdf, tmp_path, *NODE_FLAGS)
    lines = entities(doc)
    assert len(lines) == 10 and {e.dxftype() for e in lines} == {"LWPOLYLINE"}
    assert {e.dxf.linetype for e in lines} <= {"BYLAYER", "Continuous"}
    assert all(doc.layers.get(e.dxf.layer).dxf.linetype == "Continuous" for e in lines)


@pytest.mark.parametrize("name", ["sample_plan_pdf", "circles_pdf", "dashes_pdf", "fills_pdf",
                                  "cad_like_pdf"])
def test_mixed_drawings_have_no_forbidden_entities(name, request, tmp_path):
    pdf = request.getfixturevalue(name)
    _, doc, _ = cut(pdf, tmp_path, *NODE_FLAGS, units="mm")
    assert not {e.dxftype() for e in doc.modelspace()} & FORBIDDEN
    assert all(e.dxf.linetype in ("BYLAYER", "Continuous") for e in doc.modelspace())


def test_curve_accuracy_circle_r50(tmp_path):
    """Spec «Точність кривих»: a circle of radius 50 mm (4 Béziers, as PDF writers draw it) →
    one closed polyline whose vertices and chord midpoints lie within 0.01 mm of the PDF curve."""
    from test_fill_regions import distance_to_curve

    r = 50 / PT2MM
    cx, cy = 300.0, 200.0
    pdf = make_sample._content_pdf(tmp_path / "circle50.pdf",
                                   [b"0 0 1 rg " + make_sample._ops(make_sample._circle(cx, cy, r)) + b"f\n"])
    _, doc, _ = cut(pdf, tmp_path, units="mm")
    (poly,) = entities(doc)
    pts = [(p[0], p[1]) for p in poly.get_points("xy")]
    quarters = [tuple((x * PT2MM, y * PT2MM) for x, y in q) for q in make_sample._circle(cx, cy, r)]

    def to_curve(p):
        return min(distance_to_curve(p, q, samples=400) for q in quarters)

    worst = 0.0
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        worst = max(worst, to_curve(a), to_curve(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)))
    assert worst <= 0.01 + 1e-6


# -- 3.4 text, meta, traced pages --------------------------------------------------------------
def test_real_text_stays_text(logo_variant_pdf, tmp_path):
    meta, doc, _ = cut(logo_variant_pdf("text"), tmp_path)
    texts = entities(doc, "TEXT")
    assert [t.dxf.text for t in texts] == ["Made in USA"]
    assert meta["cut_text_kept"] == 1
    cut_polylines = [e for e in entities(doc, "LWPOLYLINE") if e.dxf.layer.startswith("CUT_")]
    assert meta["contours"] == len(cut_polylines) == 4


@pytest.mark.skipif(not has_cv2(), reason="OpenCV (extra [raster]) not installed")
def test_scan_is_traced_as_without_cut(scan_drawing_pdf, tmp_path):
    plain = run_engine("pdf2dxf", "--in", scan_drawing_pdf, "--out", tmp_path / "scan.dxf",
                       "--raster-fallback")
    meta, doc, _ = cut(scan_drawing_pdf, tmp_path, "--raster-fallback", units="mm")
    assert plain.returncode == 0
    assert meta["traced_pages"] == plain.meta["traced_pages"] == [0]
    assert layer_names(doc) == {"TRACED"}
    assert meta["entities"] == plain.meta["entities"]


# -- 3.5 preview -------------------------------------------------------------------------------
def test_preview_shows_contours_only(logo_pdf, tmp_path):
    _, doc, png = cut(logo_pdf, tmp_path, preview=True)
    lo, hi = doc.header["$EXTMIN"], doc.header["$EXTMAX"]
    bounds = (lo[0], lo[1], hi[0], hi[1])
    x, y = make_sample.LETTERS[0]
    centre = preview_pixel(png, bounds, (x + 30) * PT2IN, (y + 45) * PT2IN)
    assert min(centre) >= 245  # inside a letter: white, nothing is filled
    edge = preview_pixel(png, bounds, x * PT2IN, (y + 45) * PT2IN, radius=2)
    assert min(edge) < 200  # on the outline: the layer colour
