# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""pdf2dxf phase-2 flags (engine 1.1): --arcs, --hatch-fills, --linetypes, --merge,
--scale / --units, --raster-fallback."""

from __future__ import annotations

import math
from collections import Counter

import ezdxf
import pytest

from cad_engine import recognize
from cad_engine.budget import Budget
from cad_engine.errors import InputError
from cad_engine.pdf2dxf import PT2MM, Options, convert

from conftest import has_cv2, non_white_fraction, pixel_difference, run_engine

PAGE_H_PT = 595.28  # A4 landscape height of the generated PDFs
ALL_RECOGNITION = ("--arcs", "--hatch-fills", "--linetypes", "--merge")


def convert_ok(pdf, tmp_path, *extra, name=None):
    out = tmp_path / ((name or pdf.stem) + ".dxf")
    run = run_engine("pdf2dxf", "--in", pdf, "--out", out, *extra)
    assert run.returncode == 0, run.stderr
    doc = ezdxf.readfile(out)
    auditor = doc.audit()
    assert not auditor.has_errors, [str(e.message) for e in auditor.errors]
    return run.meta, doc


def of_type(doc, dxftype, layer=None):
    return [e for e in doc.modelspace() if e.dxftype() == dxftype and (layer is None or e.dxf.layer == layer)]


def to_mm(x_pt, y_pt):
    return x_pt * PT2MM, (PAGE_H_PT - y_pt) * PT2MM


# -- --arcs ------------------------------------------------------------------------------------
def test_arcs_spec_scenarios(circles_pdf, tmp_path):
    meta, doc = convert_ok(circles_pdf, tmp_path, "--arcs")
    # circle of 4 Béziers -> one CIRCLE with centre and radius, no splines left on its layer
    circles = of_type(doc, "CIRCLE")
    assert len(circles) == 1 and circles[0].dxf.layer == "LINES_FF0000"
    cx, cy = to_mm(150, 150)
    assert circles[0].dxf.center.x == pytest.approx(cx, abs=0.01)
    assert circles[0].dxf.center.y == pytest.approx(cy, abs=0.01)
    assert circles[0].dxf.radius == pytest.approx(50 * PT2MM, rel=1e-3)
    assert not of_type(doc, "SPLINE", "LINES_FF0000")
    # 90° door arc of one Bézier -> one ARC 0°..90°
    door = of_type(doc, "ARC", "LINES_0000FF")
    assert len(door) == 1
    assert door[0].dxf.radius == pytest.approx(60 * PT2MM, rel=1e-3)
    assert door[0].dxf.start_angle == pytest.approx(0.0, abs=0.01)
    assert door[0].dxf.end_angle == pytest.approx(90.0, abs=0.01)
    # 180° in two Béziers -> one ARC 0°..180°
    half = of_type(doc, "ARC", "LINES_009900")
    assert len(half) == 1
    assert (half[0].dxf.start_angle, half[0].dxf.end_angle) == (pytest.approx(0.0, abs=0.01),
                                                                 pytest.approx(180.0, abs=0.01))
    # 2:1 ellipse and an S-curve stay splines
    assert len(of_type(doc, "SPLINE", "LINES_000000")) == 4
    assert len(of_type(doc, "SPLINE", "LINES_FF00FF")) == 2
    assert meta["recognized"] == {"circles": 1, "arcs": 2, "hatches": 0, "linetypes": 0, "merged": 0}


def test_arcs_on_sample_plan_round_trip(sample_plan_pdf, tmp_path):
    """The sample plan DXF -> PDF (dxf2pdf) -> DXF: column circle and door arc come back."""
    meta, doc = convert_ok(sample_plan_pdf, tmp_path, "--arcs")
    walls = [e for e in of_type(doc, "LWPOLYLINE", "walls") if e.closed]
    outer = max(walls, key=lambda e: max(p[0] for p in e.get_points("xy")))
    xs = [p[0] for p in outer.get_points("xy")]
    ys = [p[1] for p in outer.get_points("xy")]
    unit = (max(xs) - min(xs)) / 10_000.0  # DXF mm per plan mm (the outer wall is 10 m wide)

    def plan(x, y):
        return min(xs) + x * unit, min(ys) + y * unit

    circles = of_type(doc, "CIRCLE", "walls")
    assert len(circles) == 1
    assert circles[0].dxf.radius == pytest.approx(300 * unit, rel=5e-3)
    assert (circles[0].dxf.center.x, circles[0].dxf.center.y) == pytest.approx(plan(2500, 3000), abs=0.05)
    door = of_type(doc, "ARC", "doors")
    assert len(door) == 1
    assert door[0].dxf.radius == pytest.approx(900 * unit, rel=5e-3)
    assert (door[0].dxf.center.x, door[0].dxf.center.y) == pytest.approx(plan(5000, 2500), abs=0.05)
    assert door[0].dxf.start_angle == pytest.approx(0.0, abs=0.05)
    assert door[0].dxf.end_angle == pytest.approx(90.0, abs=0.05)
    # the three chair blocks: 180° arcs of two Béziers each; the 2:1 table ellipse stays 4 splines
    chairs = of_type(doc, "ARC", "furniture")
    assert len(chairs) == 3
    for chair in chairs:  # 0°..180° (float32 noise may put the start at 359.99°)
        assert (chair.dxf.end_angle - chair.dxf.start_angle) % 360 == pytest.approx(180.0, abs=0.05)
        assert min(chair.dxf.start_angle, 360 - chair.dxf.start_angle) == pytest.approx(0.0, abs=0.05)
    assert not of_type(doc, "CIRCLE", "furniture")
    assert len(of_type(doc, "SPLINE", "furniture")) == 4
    assert meta["recognized"]["circles"] >= 1


def test_bezier_arc_tolerance():
    # a quarter circle within 0.03 % of the radius is an arc; an ellipse quarter is not
    k = 0.5522847498
    assert recognize.bezier_arc((10, 0), (10, 10 * k), (10 * k, 10), (0, 10)) is not None
    assert recognize.bezier_arc((20, 0), (20, 10 * k), (20 * k, 10), (0, 10)) is None
    # nearly straight curves do not become huge arcs
    assert recognize.bezier_arc((0, 0), (10, 0.001), (20, -0.001), (30, 0)) is None
    run = recognize.find_arc_runs([("c", *b) for b in recognize.arc_beziers(0, 0, 5, 0, 2 * math.pi)])
    assert len(run) == 1 and run[0].circle and run[0].r == pytest.approx(5, rel=1e-6)


# -- --hatch-fills -----------------------------------------------------------------------------
def test_hatch_fills(fills_pdf, tmp_path):
    meta, doc = convert_ok(fills_pdf, tmp_path, "--hatch-fills")
    red = of_type(doc, "HATCH", "FILL_FF0000")
    assert len(red) == 1
    hatch = red[0]
    assert hatch.dxf.solid_fill == 1 and hatch.dxf.pattern_name == "SOLID"
    assert hatch.dxf.color == 256  # BYLAYER: the layer keeps the fill colour
    assert doc.layers.get("FILL_FF0000").rgb == (255, 0, 0)
    assert [type(p).__name__ for p in hatch.paths] == ["PolylinePath", "EdgePath"]  # rectangle, circle
    assert {e.type.name for e in hatch.paths[1].edges} == {"SPLINE"}
    blue = of_type(doc, "HATCH", "FILL_0000FF")  # even-odd square with a hole: two loops
    assert len(blue) == 1 and len(blue[0].paths) == 2
    # fill + stroke stays an outline, no closed polylines on FILL layers
    assert [e.dxf.layer for e in of_type(doc, "LWPOLYLINE")] == ["LINES_000000"]
    assert meta["recognized"]["hatches"] == 2


def test_hatch_fills_render(fills_pdf, tmp_path):
    _, doc = convert_ok(fills_pdf, tmp_path, "--hatch-fills")
    png = tmp_path / "fills.png"
    run = run_engine("preview-dxf", "--in", tmp_path / "fills.dxf", "--out", png)
    assert run.returncode == 0, run.stderr
    assert non_white_fraction(png) > 0.05  # solid areas, not just outlines


def test_hatch_fills_text_outlines(sample_plan_pdf, tmp_path):
    meta, doc = convert_ok(sample_plan_pdf, tmp_path, "--hatch-fills")
    plain, _ = convert_ok(sample_plan_pdf, tmp_path, name="plain")
    assert of_type(doc, "HATCH", "text")
    assert meta["entities"] < plain["entities"]


# -- --linetypes -------------------------------------------------------------------------------
def test_linetypes_dash_runs(dashes_pdf, tmp_path):
    meta, doc = convert_ok(dashes_pdf, tmp_path, "--linetypes")
    assert "DASHED" in doc.linetypes and "DOT" in doc.linetypes
    # 40 dashes of 3 mm with 2 mm gaps -> one DASHED LINE from the first start to the last end
    red = of_type(doc, "LINE", "LINES_FF0000")
    assert len(red) == 1 and not of_type(doc, "LWPOLYLINE", "LINES_FF0000")
    line = red[0]
    assert line.dxf.linetype == "DASHED"
    assert line.dxf.start.x == pytest.approx(20.0, abs=1e-3)
    assert line.dxf.end.x == pytest.approx(20.0 + 39 * 5 + 3, abs=1e-3)
    assert line.dxf.ltscale * 0.6 == pytest.approx(5.0, rel=1e-3)  # one pattern repeat = 5 mm
    # 0.3 mm dots with 1.2 mm gaps -> DOT
    green = of_type(doc, "LINE", "LINES_00FF00")
    assert len(green) == 1 and green[0].dxf.linetype == "DOT"
    assert green[0].dxf.ltscale * 0.2 == pytest.approx(1.5, rel=1e-3)
    # PDF dash array [6 3] -> the path keeps its geometry with a DASHED linetype
    blue = of_type(doc, "LWPOLYLINE", "LINES_0000FF")
    assert len(blue) == 1 and blue[0].dxf.linetype == "DASHED"
    assert blue[0].dxf.ltscale * 0.6 == pytest.approx(9 * PT2MM, rel=1e-3)
    assert blue[0].dxf.flags & 128  # continuous pattern over the vertices
    # irregular gaps and too few dashes stay as they are
    black = of_type(doc, "LWPOLYLINE", "LINES_000000")
    assert len(black) == 8 and all(e.dxf.linetype == "BYLAYER" for e in black)
    assert meta["recognized"]["linetypes"] == 3


def test_linetypes_sample_plan_hidden_line(sample_plan_pdf, tmp_path):
    """The DASHED hidden line of the sample plan, rendered as ~3000 dashes, comes back as one
    DASHED LINE; the dashed spline becomes short straight dashed pieces."""
    meta, doc = convert_ok(sample_plan_pdf, tmp_path, "--linetypes")
    plain, _ = convert_ok(sample_plan_pdf, tmp_path, name="plain")
    hidden = of_type(doc, "LINE", "hidden")
    longest = max(hidden, key=lambda e: e.dxf.start.distance(e.dxf.end))
    assert longest.dxf.linetype == "DASHED"
    assert longest.dxf.start.y == pytest.approx(longest.dxf.end.y, abs=1e-3)  # the horizontal line
    assert longest.dxf.start.distance(longest.dxf.end) > 100
    assert meta["entities"] < plain["entities"] / 2


def test_find_dash_runs_curved_path_is_split():
    # dashes along a circle of r = 30 mm: no LINE may leave the 0.01 mm tolerance
    dashes = []
    for i in range(200):
        a0, a1 = i * 0.02, i * 0.02 + 0.012
        dashes.append(((30 * math.cos(a0), 30 * math.sin(a0)), (30 * math.cos(a1), 30 * math.sin(a1))))
    runs = recognize.find_dash_runs(dashes, lat_tol=0.01, max_len=25, eps=1e-6)
    for run in runs:
        assert run.end - run.first >= 4
        start, stop = run.start, run.stop
        length = math.dist(start, stop)
        for a, b in dashes[run.first:run.end]:
            for p in (a, b):
                cross = (p[0] - start[0]) * (stop[1] - start[1]) - (p[1] - start[1]) * (stop[0] - start[0])
                assert abs(cross) / length <= 0.01 + 1e-9


# -- --merge -----------------------------------------------------------------------------------
def test_merge_duplicates_collinear_and_curves(merge_pdf, tmp_path):
    meta, doc = convert_ok(merge_pdf, tmp_path, "--merge")
    assert len(of_type(doc, "LWPOLYLINE", "LINES_FF0000")) == 1  # exact duplicate dropped
    assert len(of_type(doc, "LWPOLYLINE", "LINES_0000FF")) == 1  # same line, other colour: kept
    poly = of_type(doc, "LWPOLYLINE", "LINES_000000")
    assert len(poly) == 1 and len(poly[0]) == 3  # 6 vertices, 3 of them collinear
    curve = of_type(doc, "SPLINE", "LINES_008000")
    assert len(curve) == 1 and len(curve[0].control_points) == 10
    assert list(curve[0].knots) == [0, 0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 3]
    assert meta["recognized"]["merged"] == 3  # 1 duplicate + 2 joined Bézier pieces
    # the joined spline passes through the original joints
    bspline = curve[0].construction_tool()
    joints = bspline.points([bspline.max_t / 3, bspline.max_t * 2 / 3])
    points = [(round(p.x, 4), round(p.y, 4)) for p in joints]
    assert points == [pytest.approx(to_mm(150, 300), abs=1e-3), pytest.approx(to_mm(250, 300), abs=1e-3)]


def _merge_effect(pdf, tmp_path):
    plain_png, merged_png = tmp_path / "plain.png", tmp_path / "merged.png"
    plain, _ = convert_ok(pdf, tmp_path, "--preview", plain_png, name="plain")
    merged, _ = convert_ok(pdf, tmp_path, "--merge", "--preview", merged_png, name="merged")
    return plain, merged, pixel_difference(plain_png, merged_png)


def test_merge_sample_plan_fewer_entities_same_picture(sample_plan_pdf, tmp_path):
    plain, merged, diff = _merge_effect(sample_plan_pdf, tmp_path)
    assert merged["entities"] <= 0.9 * plain["entities"], (merged["entities"], plain["entities"])
    assert diff < 0.01


def test_merge_floorplan_fewer_entities_same_picture(floorplan_pdf, tmp_path):
    plain, merged, diff = _merge_effect(floorplan_pdf, tmp_path)
    assert merged["entities"] <= 0.7 * plain["entities"], (merged["entities"], plain["entities"])
    assert diff < 0.01


def test_simplify_collinear():
    pts = [(0, 0), (1, 0), (1, 0), (2, 0.001), (3, 0), (3, 5)]
    assert recognize.simplify_collinear(pts, tol=0.01, eps=1e-9) == [(0, 0), (3, 0), (3, 5)]
    folded = [(0, 0), (2, 0), (1, 0)]  # going back is not collinear continuation
    assert recognize.simplify_collinear(folded, tol=0.01, eps=1e-9) == folded


def test_all_flags_floorplan(floorplan_pdf, tmp_path):
    png = tmp_path / "all.png"
    meta, doc = convert_ok(floorplan_pdf, tmp_path, *ALL_RECOGNITION, "--preview", png)
    types = Counter(e.dxftype() for e in doc.modelspace())
    assert types["HATCH"] > 100 and types["CIRCLE"] > 0 and types["ARC"] > 0
    assert meta["entities"] < 15_000
    assert non_white_fraction(png) > 0.005


# -- --scale / --units -------------------------------------------------------------------------
def _line_lengths(doc):
    lengths = {}
    for e in of_type(doc, "LWPOLYLINE"):
        (x1, y1), (x2, y2) = e.get_points("xy")
        lengths[e.dxf.layer] = (math.hypot(x2 - x1, y2 - y1), e.dxf.lineweight)
    return lengths


def test_scale_1_100(scale_pdf, tmp_path):
    meta, doc = convert_ok(scale_pdf, tmp_path, "--scale", "1-100")
    lengths = _line_lengths(doc)
    assert lengths["LINES_000000"][0] == pytest.approx(10_000.0, abs=0.01)  # 100 mm wall at 1:100
    assert lengths["LINES_000000"][1] == 18  # lineweight is not scaled
    assert doc.units == ezdxf.units.MM
    text = of_type(doc, "TEXT")[0]
    assert text.dxf.height == pytest.approx(10 * PT2MM * 0.72 * 100, rel=1e-6)
    assert (meta["scale"], meta["units"]) == ("1-100", "mm")


def test_units_inch(scale_pdf, tmp_path):
    meta, doc = convert_ok(scale_pdf, tmp_path, "--units", "inch")
    lengths = _line_lengths(doc)
    assert lengths["LINES_FF0000"][0] == pytest.approx(1.0, abs=1e-5)  # 72 pt = 1 inch
    assert doc.units == ezdxf.units.IN
    assert doc.header["$INSUNITS"] == 1 and doc.header["$MEASUREMENT"] == 0
    _, doc50 = convert_ok(scale_pdf, tmp_path, "--units", "inch", "--scale", "1-50", name="inch50")
    assert _line_lengths(doc50)["LINES_FF0000"][0] == pytest.approx(50.0, abs=1e-4)
    assert (meta["scale"], meta["units"]) == ("asis", "inch")


def test_scale_keeps_page_layout(three_page_pdf, tmp_path):
    _, plain = convert_ok(three_page_pdf, tmp_path, name="plain")
    _, scaled = convert_ok(three_page_pdf, tmp_path, "--scale", "1-200", name="scaled")
    ext = [plain.header["$EXTMIN"], plain.header["$EXTMAX"]]
    ext200 = [scaled.header["$EXTMIN"], scaled.header["$EXTMAX"]]
    for a, b in zip(ext, ext200, strict=True):
        assert b[0] == pytest.approx(a[0] * 200, rel=1e-6)
        assert b[1] == pytest.approx(a[1] * 200, rel=1e-6)


def test_scale_preview_matches(sample_plan_pdf, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    convert_ok(sample_plan_pdf, tmp_path, "--preview", a, name="a")
    convert_ok(sample_plan_pdf, tmp_path, "--preview", b, "--scale", "1-200", "--units", "inch", name="b")
    assert pixel_difference(a, b) < 0.01


# -- --raster-fallback -------------------------------------------------------------------------
needs_cv2 = pytest.mark.skipif(not has_cv2(), reason="OpenCV (extra [raster]) not installed")


@needs_cv2
def test_raster_fallback_scan(scan_drawing_pdf, tmp_path):
    png = tmp_path / "scan.png"
    meta, doc = convert_ok(scan_drawing_pdf, tmp_path, "--raster-fallback", *ALL_RECOGNITION,
                           "--preview", png)
    assert meta["traced_pages"] == [0]
    assert meta["has_vectors"] is False
    assert meta["text_entities"] == 0
    msp = doc.modelspace()
    assert len(msp) >= 4 and {e.dxftype() for e in msp} == {"LWPOLYLINE"}
    assert {e.dxf.layer for e in msp} == {"TRACED"}
    assert all(e.closed for e in msp)
    # the frame comes back near its place: 60..780 x 60..530 pt
    xs = [p[0] for e in msp for p in e.get_points("xy")]
    assert min(xs) == pytest.approx(60 * PT2MM, abs=1.5) and max(xs) == pytest.approx(780 * PT2MM, abs=1.5)
    assert non_white_fraction(png) > 0.005
    assert meta["warnings"] == []


@needs_cv2
def test_raster_fallback_mixed(mixed_pdf, tmp_path):
    meta, doc = convert_ok(mixed_pdf, tmp_path, "--raster-fallback")
    assert meta["pages"] == 2 and meta["traced_pages"] == [1]
    page_w = 841.89 * PT2MM
    gap = max(20.0, 0.1 * page_w)
    traced = [e for e in doc.modelspace() if e.dxf.layer == "TRACED"]
    vectors = [e for e in doc.modelspace() if e.dxf.layer != "TRACED"]
    assert traced and vectors
    assert all(p[0] > page_w + gap - 1 for e in traced for p in e.get_points("xy"))
    assert of_type(doc, "TEXT")[0].dxf.text == "VECTOR PAGE"
    assert meta["has_vectors"] is True


def test_raster_fallback_off(mixed_pdf, tmp_path):
    meta, doc = convert_ok(mixed_pdf, tmp_path)
    assert meta["traced_pages"] == []
    assert not [e for e in doc.modelspace() if e.dxf.layer == "TRACED"]


def test_raster_fallback_without_opencv(monkeypatch, scan_drawing_pdf, mixed_pdf, tmp_path):
    from cad_engine import raster

    monkeypatch.setattr(raster, "load_cv2", lambda: None)
    opts = Options(raster_fallback=True)
    meta: dict = {"warnings": []}
    with pytest.raises(InputError) as info:  # scan only: still no vectors, but no crash
        convert(str(scan_drawing_pdf), str(tmp_path / "scan.dxf"), budget=Budget(None), meta=meta,
                options=opts)
    assert info.value.code == "no_vectors"
    assert meta["warnings"] == ["raster_fallback_unavailable"]
    meta = {"warnings": []}
    convert(str(mixed_pdf), str(tmp_path / "mixed.dxf"), budget=Budget(None), meta=meta, options=opts)
    assert meta["warnings"] == ["raster_fallback_unavailable"] and meta["traced_pages"] == []
    assert (tmp_path / "mixed.dxf").exists()


@needs_cv2
def test_raster_fallback_entity_limit(scan_drawing_pdf, tmp_path):
    meta: dict = {}
    with pytest.raises(InputError) as info:
        convert(str(scan_drawing_pdf), str(tmp_path / "x.dxf"), budget=Budget(None), meta=meta,
                options=Options(raster_fallback=True), max_entities=2)
    assert info.value.code == "too_complex"


# -- CLI ---------------------------------------------------------------------------------------
def test_flags_off_meta_defaults(cad_like_pdf, tmp_path):
    meta, _ = convert_ok(cad_like_pdf, tmp_path)
    assert meta["recognized"] == {"circles": 0, "arcs": 0, "hatches": 0, "linetypes": 0, "merged": 0}
    assert meta["warnings"] == [] and (meta["scale"], meta["units"]) == ("asis", "mm")


@pytest.mark.parametrize(
    "args",
    [
        ["--scale", "1-25"],
        ["--units", "cm"],
        ["--page", "a4"],  # dxf2pdf-only option
        ["--layouts"],
    ],
)
def test_pdf2dxf_option_errors(cad_like_pdf, tmp_path, args):
    run = run_engine("pdf2dxf", "--in", cad_like_pdf, "--out", tmp_path / "x.dxf", *args)
    assert run.returncode == 5
    assert run.meta["message"].startswith("usage error")


def test_service_flag_set_on_sample_plan(sample_plan_pdf, tmp_path):
    """The flag set the service passes (D9): every recognition flag + raster fallback + preview."""
    png = tmp_path / "plan.png"
    meta, doc = convert_ok(sample_plan_pdf, tmp_path, *ALL_RECOGNITION, "--raster-fallback",
                           "--scale", "1-100", "--units", "mm", "--preview", png)
    assert meta["traced_pages"] == [] and meta["warnings"] == []
    rec = meta["recognized"]
    assert rec["circles"] >= 1 and rec["arcs"] >= 4 and rec["hatches"] >= 6 and rec["linetypes"] >= 1
    assert meta["entities"] == len(doc.modelspace())
    assert non_white_fraction(png) > 0.005
