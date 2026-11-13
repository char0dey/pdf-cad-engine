# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Parts far from the drawing (engine 1.5, `cad_engine.far_objects`).

PROD 2026-11-13: a DWG converted in the browser had one object with a coordinate of the
order of 1e39; ezdxf stopped scaling the page (fit scale < 1e-9) and PyMuPDF refused the
coordinate — dxf2pdf exit 5, 503 for the visitor. A stray object at a smaller distance gave a
blank PDF without any error. Far parts are now left out and counted in ``far_parts``.
"""

from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from cad_engine.far_objects import FAR_RATIO, MAX_COORD, far_mask
from conftest import non_white_fraction, run_engine


def boxes(*rows) -> np.ndarray:
    return np.array(rows, dtype=np.float64)


def grid(n: int, size: float = 100.0, ox: float = 0.0, oy: float = 0.0) -> list[tuple]:
    """``n`` small boxes spread over a ``size`` square at (ox, oy)."""
    step = size / max(n - 1, 1)
    return [(ox + i * step, oy, ox + i * step + 1, oy + size) for i in range(n)]


# -- the rule ---------------------------------------------------------------------------------

def test_nothing_far_in_an_ordinary_drawing():
    assert not far_mask(boxes(*grid(200))).any()


def test_stray_object_at_the_origin_of_a_survey_drawing():
    b = boxes(*grid(200, ox=5e6, oy=4e6), (0, 0, 0, 0))
    far = far_mask(b)
    assert far.tolist() == [False] * 200 + [True]


def test_broken_coordinates_are_far_even_in_a_tiny_drawing():
    """Fewer records than the percentiles need: the core is everything sane, and a coordinate
    beyond MAX_COORD, inf or NaN is far by itself."""
    b = boxes((0, 0, 10, 10), (0, 0, 1e39, 10), (0, 0, np.inf, 1), (np.nan, 0, 1, 1), (5, 5, 6, 6))
    assert far_mask(b).tolist() == [False, True, True, True, False]


def test_edges_of_the_rule():
    """Enough parts that the percentiles leave both extremes out of the core (with ≤ 101 parts
    only the single most extreme part on each side is left out of it)."""
    size = 100.0
    inside = (0, 0, size, size)
    just_within = (size + FAR_RATIO * size * 0.99, 0, size + FAR_RATIO * size * 0.99 + 1, 1)
    beyond = (size + FAR_RATIO * size * 1.01, 0, size + FAR_RATIO * size * 1.01 + 1, 1)
    b = boxes(*([inside] * 500), just_within, beyond)
    assert far_mask(b).tolist() == [False] * 500 + [False, True]


def test_a_real_second_cluster_stays():
    """Two halves of a drawing 50 sizes apart are both the drawing; a small detail (under 1 %
    of the parts) 50 sizes away is inside the reach too."""
    halves = boxes(*grid(100), *grid(100, ox=5000))
    assert not far_mask(halves).any()
    detail = boxes(*grid(300), *grid(2, ox=5000))
    assert not far_mask(detail).any()


def test_everything_on_one_point():
    """A core of size 0 counts as 1 drawing unit: «far» is then 1000 units away."""
    b = boxes(*([(3, 3, 3, 3)] * 300), (500, 3, 500, 3), (3 + 2000, 3, 3 + 2000, 3))
    assert far_mask(b).tolist() == [False] * 300 + [False, True]


def test_all_broken():
    assert far_mask(boxes((0, 0, MAX_COORD * 10, 1), (np.nan, 0, 0, 0))).all()


# -- the engine -------------------------------------------------------------------------------

def render(dxf, tmp_path, *extra):
    out = tmp_path / (dxf.stem + ".pdf")
    run = run_engine("dxf2pdf", "--in", dxf, "--out", out, *extra)
    assert run.returncode == 0, run.stderr
    return run, pymupdf.open(out)


def pixels(page) -> bytes:
    return page.get_pixmap(dpi=40).samples


@pytest.mark.parametrize("kind", ["huge", "stray", "in_block"])
def test_far_part_left_out_and_the_drawing_drawn(kind, far_dxf, tmp_path):
    """The drawing comes out exactly as without the far object: same page, same picture."""
    ref_run, ref = render(far_dxf("plain"), tmp_path)
    run, pdf = render(far_dxf(kind), tmp_path)
    assert run.meta["far_parts"] == 1
    assert ref_run.meta["far_parts"] == 0
    assert run.meta["page_size_mm"] == ref_run.meta["page_size_mm"]
    assert "1 drawn part(s) left out in 'Model'" in run.stderr
    if kind == "stray":  # the same picture, moved: compare how much ink there is
        a = non_white_fraction(ref[0].get_pixmap(dpi=40).tobytes("png"))
        b = non_white_fraction(pdf[0].get_pixmap(dpi=40).tobytes("png"))
        assert a > 0.01 and abs(a - b) / a < 0.02
    else:
        assert pixels(pdf[0]) == pixels(ref[0])


def test_prod_case_no_longer_fails(far_dxf, tmp_path):
    """The PROD failure: before 1.5 this exited 5 with «TypeError … new_FzPoint»."""
    run, pdf = render(far_dxf("huge"), tmp_path)
    assert "new_FzPoint" not in run.stderr
    assert non_white_fraction(pdf[0].get_pixmap(dpi=40).tobytes("png")) > 0.01


def test_real_second_cluster_is_kept(far_dxf, tmp_path):
    run, pdf = render(far_dxf("near"), tmp_path)
    assert run.meta["far_parts"] == 0
    assert "left out" not in run.stderr
    assert non_white_fraction(pdf[0].get_pixmap(dpi=40).tobytes("png")) > 0.0005


def test_far_part_on_a_sheet(far_dxf, tmp_path):
    """Paper space: the sheet is drawn without the far line — the plan fills the sheet instead of
    shrinking to a dot; the count adds up over pages."""
    run, pdf = render(far_dxf("paper"), tmp_path, "--layouts")
    assert run.meta["layouts"] == ["Sheet"]
    assert pdf.page_count == 2
    assert run.meta["far_parts"] == 1
    assert "1 drawn part(s) left out in 'Sheet'" in run.stderr
    sheet = pdf[1]
    assert non_white_fraction(sheet.get_pixmap(dpi=40).tobytes("png")) > 0.01
    xs = [p.x for d in sheet.get_drawings() for item in d["items"] for p in item[1:] if hasattr(p, "x")]
    assert (max(xs) - min(xs)) * 25.4 / 72 > 250  # mm of the 420 mm sheet


def test_preview_leaves_far_parts_out(far_dxf, tmp_path):
    out = tmp_path / "preview.png"
    run = run_engine("preview-dxf", "--in", far_dxf("huge"), "--out", out)
    assert run.returncode == 0, run.stderr
    assert run.meta["far_parts"] == 1
    assert non_white_fraction(out) > 0.01


def test_ordinary_drawing_reports_zero(far_dxf, tmp_path):
    run, _ = render(far_dxf("plain"), tmp_path)
    assert run.meta["far_parts"] == 0
    assert "left out" not in run.stderr


def test_model_of_broken_coordinates_only(tmp_path):
    """Every drawn part beyond 1e12: model space draws nothing — the refusal of an empty drawing
    (exit 2, ``empty_drawing``), not a crash."""
    import ezdxf

    src = tmp_path / "all_broken.dxf"
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((1e39, 0), (1e39 + 1, 1))
    msp.add_solid([(2e39, 0), (3e39, 0), (2e39, 1e39)])
    doc.saveas(src)
    run = run_engine("dxf2pdf", "--in", src, "--out", tmp_path / "out.pdf")
    assert run.returncode == 2, run.stderr
    assert run.meta["code"] == "empty_drawing"
    assert "new_FzPoint" not in run.stderr
