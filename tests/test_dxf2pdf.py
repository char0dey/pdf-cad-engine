# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""dxf2pdf: page choice, rendering, recovery, DWG / empty / non-DXF inputs."""

from __future__ import annotations

import pymupdf
import pytest

from cad_engine.dxf2pdf import auto_page

from conftest import (
    A3_LANDSCAPE_PT,
    A4_LANDSCAPE_PT,
    A4_PORTRAIT_PT,
    FIXTURES,
    non_white_fraction,
    run_engine,
)


def render_ok(dxf, tmp_path):
    out = tmp_path / (dxf.stem + ".pdf")
    run = run_engine("dxf2pdf", "--in", dxf, "--out", out)
    assert run.returncode == 0, run.stderr
    pdf = pymupdf.open(out)
    assert pdf.page_count == 1
    pix = pdf[0].get_pixmap(dpi=50)  # MuPDF renders it
    assert non_white_fraction(pix.tobytes("png")) > 0.001
    return run.meta, pdf


def page_size(pdf):
    rect = pdf[0].rect
    return (rect.width, rect.height)


def assert_size(actual, expected):
    assert actual[0] == pytest.approx(expected[0], abs=1.5)
    assert actual[1] == pytest.approx(expected[1], abs=1.5)


def test_sample_plan_a4_landscape(sample_plan_dxf, tmp_path):
    meta, pdf = render_ok(sample_plan_dxf, tmp_path)
    assert_size(page_size(pdf), A4_LANDSCAPE_PT)
    assert meta["page_size_mm"] == [297.0, 210.0]
    assert meta["pages"] == 1
    assert meta["entities"] == 19
    assert meta["layers"] >= 7
    assert meta["dxf_version"] == "AC1024"


def test_committed_sample_plan(tmp_path):
    _, pdf = render_ok(FIXTURES / "sample_plan.dxf", tmp_path)
    assert_size(page_size(pdf), A4_LANDSCAPE_PT)


def test_dimensions_portrait(tmp_path):
    meta, pdf = render_ok(FIXTURES / "dimensions.dxf", tmp_path)
    assert_size(page_size(pdf), A4_PORTRAIT_PT)
    assert meta["page_size_mm"] == [210.0, 297.0]


@pytest.mark.parametrize("name", ["texts.dxf", "hatches.dxf"])
def test_real_world_small_samples(name, tmp_path):
    meta, pdf = render_ok(FIXTURES / name, tmp_path)
    assert meta["page_size_mm"] in ([297.0, 210.0], [210.0, 297.0])


def test_binary_dxf(binary_dxf, tmp_path):
    assert binary_dxf.read_bytes().startswith(b"AutoCAD Binary DXF")
    _, pdf = render_ok(binary_dxf, tmp_path)
    assert_size(page_size(pdf), A4_LANDSCAPE_PT)


def test_large_extents_a3(big_extent_dxf, tmp_path):
    meta, pdf = render_ok(big_extent_dxf, tmp_path)
    assert_size(page_size(pdf), A3_LANDSCAPE_PT)
    assert meta["page_size_mm"] == [420.0, 297.0]


def test_auto_page_rules():
    assert auto_page(10_000, 6_000, 19, 1.0) == (297.0, 210.0)
    assert auto_page(6_000, 10_000, 19, 1.0) == (210.0, 297.0)
    assert auto_page(45_000, 30_000, 12_000, 1.0) == (420.0, 297.0)
    assert auto_page(100, 60, 5_001, 1.0) == (420.0, 297.0)
    assert auto_page(25, 10, 10, 1000.0) == (420.0, 297.0)  # 25 m drawn in metres


@pytest.mark.parametrize(("ratio", "entities"), [(0.78, (1, 18)), (0.9, (19, 19))])
def test_truncated_dxf_is_recovered(truncated_dxf, tmp_path, ratio, entities):
    # cut inside ENTITIES -> what was read so far; cut inside OBJECTS -> everything
    meta, _ = render_ok(truncated_dxf(ratio), tmp_path)
    assert entities[0] <= meta["entities"] <= entities[1]


def test_truncated_before_entities_is_empty(truncated_dxf, tmp_path):
    run = run_engine("dxf2pdf", "--in", truncated_dxf(0.7), "--out", tmp_path / "x.pdf")
    assert run.returncode == 2
    assert run.meta["code"] == "empty_drawing"


def test_hidden_layer_not_rendered(hidden_layer_dxf, tmp_path):
    _, pdf = render_ok(hidden_layer_dxf, tmp_path)
    colors = {tuple(round(c, 2) for c in d["color"]) for d in pdf[0].get_drawings() if d.get("color")}
    assert (1.0, 0.0, 0.0) not in colors
    assert (0.0, 0.0, 0.0) in colors


def test_white_becomes_black(white_dxf, tmp_path):
    _, pdf = render_ok(white_dxf, tmp_path)
    strokes = [d for d in pdf[0].get_drawings() if d.get("color") is not None]
    assert len(strokes) >= 2
    assert all(tuple(d["color"]) == (0.0, 0.0, 0.0) for d in strokes)


def test_dwg_is_rejected(fake_dwg, tmp_path):
    run = run_engine("dxf2pdf", "--in", fake_dwg, "--out", tmp_path / "x.pdf")
    assert run.returncode == 2
    assert run.meta["code"] == "dwg_not_dxf"
    assert not (tmp_path / "x.pdf").exists()


def test_empty_drawing(empty_dxf, tmp_path):
    run = run_engine("dxf2pdf", "--in", empty_dxf, "--out", tmp_path / "x.pdf")
    assert run.returncode == 2
    assert run.meta["code"] == "empty_drawing"


def test_text_file_is_unsupported(txt_as_dxf, tmp_path):
    run = run_engine("dxf2pdf", "--in", txt_as_dxf, "--out", tmp_path / "x.pdf")
    assert run.returncode == 2
    assert run.meta["code"] == "unsupported_input"


def test_floorplan(floorplan_dxf, tmp_path):
    meta, pdf = render_ok(floorplan_dxf, tmp_path)
    expected = A3_LANDSCAPE_PT if meta["page_size_mm"] == [420.0, 297.0] else A4_LANDSCAPE_PT
    assert_size(page_size(pdf), expected)


def test_ceco(ceco_dxf, tmp_path):
    meta, pdf = render_ok(ceco_dxf, tmp_path)
    assert meta["page_size_mm"] in ([297.0, 210.0], [210.0, 297.0], [420.0, 297.0], [297.0, 420.0])
