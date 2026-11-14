# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Engine 1.5.2: B-splines of degree 11 and up.

PROD 2026-11-14: dwg-to-pdf of a drawing with 38 splines of degree 11 (12 control points, 24 knots)
failed twice in the background (dxf2pdf exit 5, ``ValueError: invalid order`` in
``ezdxf.acc.bspline.Basis.__cinit__`` while measuring the extents): ezdxf's C extension takes orders
below 12 only. ``ezdxf_fixes`` hands orders ≥ 12 to ezdxf's pure-Python implementation.
"""

from __future__ import annotations

import math

import ezdxf
import numpy as np
import pymupdf
import pytest

from cad_engine import ezdxf_fixes

from conftest import dxf_to_pdf_in_process, run_engine

PLAN = [((0, 0), (300, 0)), ((300, 0), (300, 200)), ((300, 200), (0, 200)), ((0, 200), (0, 0))]


def _control_points(x0: float, y0: float, n: int) -> list[tuple[float, float, float]]:
    return [(x0 + i * 8.0, y0 + 25 * math.sin(i * 0.9), 0.0) for i in range(n)]


def _clamped_spline(layout, cps, degree):
    """A clamped spline whose knot vector makes it one Bézier curve when len(cps) == degree + 1."""
    sp = layout.add_spline()
    sp.dxf.degree = degree
    sp.control_points = cps
    n = len(cps)
    inner = n - degree - 1
    sp.knots = [0.0] * (degree + 1) + [(i + 1) / (inner + 1) for i in range(inner)] + [1.0] * (degree + 1)
    return sp


def build_incident(path, *, splines: int = 38, degree: int = 11) -> None:
    """Like the PROD drawing: lines and splines of degree 11 with 12 control points, 24 knots."""
    doc = ezdxf.new("R2000")
    msp = doc.modelspace()
    for a, b in PLAN:
        msp.add_line(a, b)
    for k in range(splines):
        _clamped_spline(msp, _control_points(10 + (k % 2) * 140, 20 + (k // 2) * 9, degree + 1), degree)
    doc.saveas(path)


def build_hatch_and_block(path) -> None:
    """A hatch bounded by a spline edge of degree 12, a block holding a spline of degree 15."""
    doc = ezdxf.new("R2000")
    msp = doc.modelspace()
    for a, b in PLAN:
        msp.add_line(a, b)
    hatch = msp.add_hatch(color=3)
    edges = hatch.paths.add_edge_path()
    cps = [(20 + i * 10.0, 120 + 30 * math.sin(i * 0.5), 0) for i in range(13)]
    edges.add_spline(control_points=cps, knot_values=[0.0] * 13 + [1.0] * 13, degree=12)
    edges.add_line((cps[-1][0], cps[-1][1]), (20, 120))
    blk = doc.blocks.new("CURVE")
    _clamped_spline(blk, _control_points(0, 0, 16), 15)
    msp.add_blockref("CURVE", (150, 30), dxfattribs={"xscale": 0.8, "yscale": 0.8, "rotation": 10})
    doc.saveas(path)


def build_low_degrees(path) -> None:
    doc = ezdxf.new("R2000")
    msp = doc.modelspace()
    for a, b in PLAN:
        msp.add_line(a, b)
    for d in range(1, 6):
        _clamped_spline(msp, _control_points(10, 30 * d, d + 4), d)
    doc.saveas(path)


def gray(page, dpi: int = 72) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(int)


def dark(page) -> int:
    return int((gray(page, 150) < 230).sum())


def render(dxf, tmp_path):
    out = tmp_path / (dxf.stem + ".pdf")
    run = run_engine("dxf2pdf", "--in", dxf, "--out", out, "--page", "auto", "--colors", "color", "--layouts")
    assert run.returncode == 0, run.stderr
    assert "invalid order" not in run.stderr
    return run.meta, pymupdf.open(out)


# -- the PROD case ----------------------------------------------------------------------------------
def test_incident_drawing_renders_its_splines(tmp_path):
    src = tmp_path / "incident.dxf"
    build_incident(src)
    meta, pdf = render(src, tmp_path)
    assert meta["code"] is None and meta["pages"] == 1
    assert meta["skipped_entities"] == 0 and meta["entity_errors"] is None
    plain = tmp_path / "plain.dxf"
    build_incident(plain, splines=0)
    _, plain_pdf = render(plain, tmp_path)
    assert dark(pdf[0]) > dark(plain_pdf[0]) + 500  # the 38 curves are on the page


@pytest.mark.parametrize("degree", [11, 12, 17, 25])
def test_points_match_the_bezier_curve(degree):
    """A clamped spline with degree + 1 control points is one Bézier curve: an independent check."""
    assert ezdxf_fixes.apply()
    doc = ezdxf.new("R2000")
    cps = _control_points(0, 0, degree + 1)
    bs = _clamped_spline(doc.modelspace(), cps, degree).construction_tool()
    n = len(cps) - 1

    def bernstein(t: float) -> list[float]:
        return [sum(math.comb(n, i) * (1 - t) ** (n - i) * t ** i * cps[i][k] for i in range(n + 1))
                for k in range(3)]

    worst = max(max(abs(a - b) for a, b in zip(bs.point(j / 64), bernstein(j / 64), strict=True))
                for j in range(65))
    assert worst < 1e-9
    assert len(list(bs.flattening(0.01))) > 10  # the evaluator ezdxf's renderer uses works too


def test_hatch_edge_and_block_of_high_degree(tmp_path):
    src = tmp_path / "hatch_block.dxf"
    build_hatch_and_block(src)
    meta, pdf = render(src, tmp_path)
    assert meta["skipped_entities"] == 0 and meta["entity_errors"] is None
    page = pdf[0].get_pixmap(dpi=72)
    rgb = np.frombuffer(page.samples, np.uint8).reshape(page.height, page.width, page.n)[..., :3].astype(int)
    green = (rgb[..., 1] > 150) & (rgb[..., 0] < 100) & (rgb[..., 2] < 100)
    assert green.sum() > 300  # the hatch (ACI 3) is filled


def test_low_degrees_draw_as_before(tmp_path, monkeypatch):
    """Degrees 1–5 go to the stock classes: the same page as without the fix."""
    from ezdxf.math import bspline

    src = tmp_path / "low.dxf"
    build_low_degrees(src)
    ours = pymupdf.open(dxf_to_pdf_in_process(src, tmp_path / "ours.pdf"))
    assert ezdxf_fixes.apply()
    monkeypatch.setattr(bspline, "Basis", ezdxf_fixes.stock_basis)
    monkeypatch.setattr(bspline, "Evaluator", ezdxf_fixes.stock_evaluator)
    stock = pymupdf.open(dxf_to_pdf_in_process(src, tmp_path / "stock.pdf"))
    assert ours[0].rect == stock[0].rect
    assert np.array_equal(gray(ours[0], 100), gray(stock[0], 100))


def test_orders_go_to_the_right_implementation():
    from ezdxf.math import _bspline

    assert ezdxf_fixes.apply()
    knots12 = [0.0] * 12 + [1.0] * 12
    high = ezdxf_fixes._basis(knots12, 12, 12)
    assert isinstance(high, _bspline.Basis)
    assert isinstance(ezdxf_fixes._evaluator(high, _control_points(0, 0, 12)), _bspline.Evaluator)
    low = ezdxf_fixes._basis([0.0] * 4 + [1.0] * 4, 4, 4)
    assert type(low) is ezdxf_fixes.stock_basis


def test_stock_ezdxf_still_needs_the_fix():
    """Guard: when ezdxf's C extension takes order 12, this fails — then remove the fix."""
    from ezdxf.acc import USE_C_EXT

    if not USE_C_EXT:
        pytest.skip("ezdxf without its C extension has no order limit")
    from ezdxf.acc.bspline import Basis

    with pytest.raises(ValueError, match="invalid order"):
        Basis([0.0] * 12 + [1.0] * 12, 12, 12)
