# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""pdf2dxf: geometry, text, layers, lineweights, pages, limits, preview."""

from __future__ import annotations

import time
from collections import Counter

import ezdxf
import pytest

from cad_engine.budget import Budget
from cad_engine.errors import InputError
from cad_engine.pdf2dxf import PT2MM, convert, lineweight

from conftest import non_white_fraction, run_engine

A4_W_MM = 595.28 * PT2MM  # 210 mm
GAP_MM = max(20.0, 0.1 * A4_W_MM)


def convert_ok(pdf, tmp_path, *extra):
    out = tmp_path / (pdf.stem + ".dxf")
    run = run_engine("pdf2dxf", "--in", pdf, "--out", out, *extra)
    assert run.returncode == 0, run.stderr
    doc = ezdxf.readfile(out)
    return run.meta, doc


def assert_audit_clean(doc):
    auditor = doc.audit()
    assert not auditor.has_errors, [str(e.message) for e in auditor.errors]


def test_sample_plan_pdf_round_trip(sample_plan_pdf, tmp_path):
    meta, doc = convert_ok(sample_plan_pdf, tmp_path)
    assert_audit_clean(doc)
    assert doc.dxfversion == "AC1024"  # AutoCAD 2010
    assert doc.units == ezdxf.units.MM
    msp = doc.modelspace()
    types = Counter(e.dxftype() for e in msp)
    assert types["LWPOLYLINE"] > 0 and types["SPLINE"] > 0
    assert meta["entities"] == len(msp)
    assert meta["pages"] == 1
    assert meta["has_vectors"] is True
    # ezdxf renders DXF text as glyph outlines -> no TEXT entities, flagged in the meta
    assert meta["text_entities"] == 0
    assert meta["text_as_outlines"] is True
    assert not any(e.dxftype() == "TEXT" for e in msp)
    # the PDF from dxf2pdf carries the DXF layer names as optional content -> they come back
    layer_names = {layer.dxf.name.lower() for layer in doc.layers}
    assert {"walls", "doors", "text", "hatch"} <= layer_names
    # header extents are real values, not the 1e20 defaults
    extmin, extmax = doc.header["$EXTMIN"], doc.header["$EXTMAX"]
    assert 0 <= extmin[0] < extmax[0] < 300 and 0 <= extmin[1] < extmax[1] < 220


def test_cad_like_plan(cad_like_pdf, tmp_path):
    meta, doc = convert_ok(cad_like_pdf, tmp_path)
    assert_audit_clean(doc)
    msp = doc.modelspace()
    texts = [e for e in msp if e.dxftype() == "TEXT"]
    assert [t.dxf.text for t in texts] == ["KITCHEN 12.5 m²"]
    kitchen = texts[0]
    assert kitchen.dxf.layer.startswith("TEXT_")
    assert kitchen.dxf.height == pytest.approx(14 * PT2MM * 0.72, rel=1e-3)
    assert kitchen.dxf.rotation == pytest.approx(0.0)
    assert kitchen.dxf.insert.x == pytest.approx(100 * PT2MM, abs=0.01)
    assert kitchen.dxf.insert.y == pytest.approx((595 - 120) * PT2MM, abs=0.01)
    # door arc: one Bézier -> one degree-3 spline with exactly the same control points
    splines = [e for e in msp if e.dxftype() == "SPLINE" and e.dxf.layer == "LINES_FF0000"]
    assert len(splines) == 1
    expected = [(400, 300), (400, 255.8), (435.8, 220), (480, 220)]
    got = [(p[0], p[1]) for p in splines[0].control_points]
    for (ex, ey), (gx, gy) in zip(expected, got, strict=True):
        assert gx == pytest.approx(ex * PT2MM, abs=1e-4)  # MuPDF keeps float32 coordinates
        assert gy == pytest.approx((595 - ey) * PT2MM, abs=1e-4)
    assert splines[0].dxf.degree == 3
    # column circle = 4 Bézier segments (arc recognition is phase 2)
    assert sum(1 for e in msp if e.dxftype() == "SPLINE" and e.dxf.layer == "LINES_000000") == 4
    # walls: one closed polyline; hatch: 10 separate line polylines
    walls = [e for e in msp if e.dxftype() == "LWPOLYLINE" and e.closed and len(e) == 4]
    assert walls
    hatch = [e for e in msp if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "LINES_808080"]
    assert len(hatch) == 10
    assert meta["text_as_outlines"] is False


def test_three_pages_side_by_side(three_page_pdf, tmp_path):
    meta, doc = convert_ok(three_page_pdf, tmp_path)
    assert_audit_clean(doc)
    assert meta["pages"] == 3
    msp = doc.modelspace()

    def rect_min_x(layer):
        rects = [e for e in msp if e.dxftype() == "LWPOLYLINE" and e.closed and e.dxf.layer == layer]
        assert len(rects) == 1
        return min(p[0] for p in rects[0].get_points("xy"))

    x1, x2, x3 = rect_min_x("LINES_FF0000"), rect_min_x("LINES_00FF00"), rect_min_x("LINES_0000FF")
    assert x2 - x1 == pytest.approx(A4_W_MM + GAP_MM, abs=1e-3)
    assert x3 - x2 == pytest.approx(A4_W_MM + GAP_MM, abs=1e-3)
    labels = sorted((t.dxf.insert.x, t.dxf.text) for t in msp if t.dxftype() == "TEXT")
    assert [text for _, text in labels] == ["PAGE 1", "PAGE 2", "PAGE 3"]


def test_form_text_and_frames(form_pdf, tmp_path):
    meta, doc = convert_ok(form_pdf, tmp_path)
    assert_audit_clean(doc)
    msp = doc.modelspace()
    texts = [e for e in msp if e.dxftype() == "TEXT"]
    assert meta["pages"] == 4
    assert meta["text_entities"] == len(texts) == 100
    assert {t.dxf.text for t in texts} >= {"Field 1.1", "Field 4.25"}
    # label colours survive as layers: dark blue and red text layers
    text_layers = {t.dxf.layer for t in texts}
    assert text_layers == {"TEXT_000066", "TEXT_CC0000"}
    assert doc.layers.get("TEXT_CC0000").rgb == (204, 0, 0)
    frames = [e for e in msp if e.dxftype() == "LWPOLYLINE" and e.closed]
    assert len(frames) == 100


def test_real_form_antrag(antrag_pdf, tmp_path):
    meta, doc = convert_ok(antrag_pdf, tmp_path)
    assert_audit_clean(doc)
    assert meta["text_entities"] >= 80
    assert meta["pages"] == 4
    assert any("Antrag" in e.dxf.text for e in doc.modelspace() if e.dxftype() == "TEXT")


def test_optional_content_layers(ocg_pdf, tmp_path):
    meta, doc = convert_ok(ocg_pdf, tmp_path)
    assert_audit_clean(doc)
    by_type = {}
    for e in doc.modelspace():
        by_type.setdefault(e.dxftype(), set()).add(e.dxf.layer)
    assert by_type["LWPOLYLINE"] == {"WALLS", "CONSTRUCTION"}
    assert by_type["SPLINE"] == {"DOORS"}
    assert by_type["TEXT"] == {"TEXT"}
    assert meta["layers"] == 4
    assert doc.layers.get("CONSTRUCTION").is_off()
    assert doc.layers.get("WALLS").is_on()


def test_lineweights(lineweight_pdf, tmp_path):
    _, doc = convert_ok(lineweight_pdf, tmp_path)
    weights = {e.dxf.layer: e.dxf.lineweight for e in doc.modelspace()}
    assert weights == {"LINES_FF0000": 9, "LINES_0000FF": 53}  # 0.09 mm and 0.53 mm
    assert lineweight(0.0) == 0
    assert lineweight(100.0) == 211


def test_rotated_page_uses_display_orientation(rotated_pdf, tmp_path):
    _, doc = convert_ok(rotated_pdf, tmp_path)
    msp = doc.modelspace()
    line = next(e for e in msp if e.dxftype() == "LWPOLYLINE")
    (x1, y1), (x2, y2) = line.get_points("xy")
    # /Rotate 90: a horizontal line near the top-left of the portrait page becomes vertical
    # near the top-right of the landscape page
    assert x1 == pytest.approx(x2, abs=1e-4)
    assert x1 == pytest.approx((841.89 - 10) * PT2MM, abs=0.01)
    text = next(e for e in msp if e.dxftype() == "TEXT")
    assert text.dxf.rotation == pytest.approx(270.0)


def test_scan_has_no_vectors(scan_pdf, tmp_path):
    run = run_engine("pdf2dxf", "--in", scan_pdf, "--out", tmp_path / "scan.dxf")
    assert run.returncode == 2
    assert run.meta["code"] == "no_vectors"


def test_invisible_ocr_text_is_ignored(invisible_text_pdf, tmp_path):
    run = run_engine("pdf2dxf", "--in", invisible_text_pdf, "--out", tmp_path / "ocr.dxf")
    assert run.returncode == 2
    assert run.meta["code"] == "no_vectors"


def test_too_many_pages(many_pages_pdf, tmp_path):
    run = run_engine("pdf2dxf", "--in", many_pages_pdf, "--out", tmp_path / "many.dxf")
    assert run.returncode == 2
    assert run.meta["code"] == "too_many_pages"
    assert run.meta["pages"] == 80
    assert "50" in run.meta["message"]
    assert run.meta["entities"] is None  # rejected before converting


def test_text_file_is_unsupported(txt_as_pdf, tmp_path):
    run = run_engine("pdf2dxf", "--in", txt_as_pdf, "--out", tmp_path / "x.dxf")
    assert run.returncode == 2
    assert run.meta["code"] == "unsupported_input"


def test_entity_limit_too_complex(form_pdf, tmp_path):
    meta: dict = {}
    with pytest.raises(InputError) as info:
        convert(str(form_pdf), str(tmp_path / "x.dxf"), budget=Budget(None), meta=meta, max_entities=150)
    assert info.value.code == "too_complex"
    assert meta["entities"] == 151
    assert not (tmp_path / "x.dxf").exists()


def test_preview_from_extracted_geometry(sample_plan_pdf, tmp_path):
    png = tmp_path / "plan.png"
    meta, _ = convert_ok(sample_plan_pdf, tmp_path, "--preview", png)
    import pymupdf

    pix = pymupdf.Pixmap(str(png))
    assert max(pix.width, pix.height) == 1400
    assert pix.width > pix.height  # landscape plan
    fraction = non_white_fraction(png)
    assert 0.005 < fraction < 0.5
    # white background: the corners are white
    assert pix.pixel(0, 0)[:3] == (255, 255, 255)


def test_preview_with_unicode_text(antrag_pdf, form_pdf, tmp_path):
    for pdf in (antrag_pdf, form_pdf):
        png = tmp_path / (pdf.stem + ".png")
        convert_ok(pdf, tmp_path, "--preview", png)
        assert non_white_fraction(png) > 0.005


def test_preview_speed_floorplan(floorplan_pdf, tmp_path):
    from cad_engine.pdf2dxf import DxfWriter, PageMap, open_pdf

    pdf = open_pdf(str(floorplan_pdf))
    writer = DxfWriter(Budget(None), max_entities=500_000, record_preview=True)
    pm = PageMap(pdf[0], 0.0)
    for drawing in pdf[0].get_drawings():
        writer.add_path(drawing, pm)
    assert writer.entities > 30_000
    started = time.perf_counter()
    png = writer.preview.render_png((writer.min_x, writer.min_y, writer.max_x, writer.max_y))
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"preview took {elapsed:.2f}s for {writer.entities} entities"
    assert non_white_fraction(png) > 0.005


def test_floorplan_full_cli(floorplan_pdf, tmp_path):
    png = tmp_path / "floorplan.png"
    meta, doc = convert_ok(floorplan_pdf, tmp_path, "--preview", png)
    assert meta["entities"] > 30_000
    assert_audit_clean(doc)
    assert non_white_fraction(png) > 0.005
