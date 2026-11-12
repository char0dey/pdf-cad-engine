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


def test_block_refs_without_block_are_dropped(broken_insert_dxf, tmp_path):
    """PROD 2026-11-12: one INSERT without a block name failed the whole drawing (exit 5,
    DXFTypeError). Now the broken references are dropped and the rest is drawn."""
    meta, pdf = render_ok(broken_insert_dxf, tmp_path)
    run = run_engine("dxf2pdf", "--in", broken_insert_dxf, "--out", tmp_path / "again.pdf")
    assert "3 block reference(s) without a block definition dropped" in run.stderr
    assert meta["entities"] == 2  # the line and the good INSERT of B
    kinds = {d["type"] for d in pdf[0].get_drawings()}
    assert kinds  # the line and B's circle are on the page
    probe = run_engine("probe-dxf", "--in", broken_insert_dxf)
    assert probe.returncode == 0, probe.stderr


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


# -- phase 2 (engine 1.1): --page, --colors, --layouts -----------------------------------------
MM2PT = 72.0 / 25.4


def render_pages(dxf, tmp_path, *extra, name=None):
    out = tmp_path / ((name or dxf.stem) + ".pdf")
    run = run_engine("dxf2pdf", "--in", dxf, "--out", out, *extra)
    assert run.returncode == 0, run.stderr
    pdf = pymupdf.open(out)
    assert pdf.page_count == run.meta["pages"]
    for page in pdf:
        assert non_white_fraction(page.get_pixmap(dpi=50).tobytes("png")) > 0.001
    return run.meta, pdf


@pytest.mark.parametrize(
    ("page", "expected_mm"),
    [("a4", (297.0, 210.0)), ("a3", (420.0, 297.0)), ("a2", (594.0, 420.0)), ("a1", (841.0, 594.0))],
)
def test_page_option(sample_plan_dxf, tmp_path, page, expected_mm):
    meta, pdf = render_pages(sample_plan_dxf, tmp_path, "--page", page)
    assert meta["page_size_mm"] == list(expected_mm)
    assert_size(page_size(pdf), (expected_mm[0] * MM2PT, expected_mm[1] * MM2PT))


def test_page_option_overrides_auto_and_keeps_orientation(big_extent_dxf, sample_plan_dxf, tmp_path):
    meta, _ = render_pages(big_extent_dxf, tmp_path, "--page", "a4")  # auto would choose A3
    assert meta["page_size_mm"] == [297.0, 210.0]
    meta, _ = render_pages(FIXTURES / "dimensions.dxf", tmp_path, "--page", "a3")  # portrait drawing
    assert meta["page_size_mm"] == [297.0, 420.0]
    meta, _ = render_pages(sample_plan_dxf, tmp_path, "--page", "auto", name="auto")
    assert meta["page_size_mm"] == [297.0, 210.0]


def _strokes(pdf):
    colors, widths, fills = set(), [], set()
    for page in pdf:
        for d in page.get_drawings():
            if d.get("color") is not None:
                colors.add(tuple(round(c, 3) for c in d["color"]))
                widths.append(round(d.get("width") or 0, 3))
            if d.get("fill") is not None:
                fills.add(tuple(round(c, 3) for c in d["fill"]))
    return colors, sorted(widths), fills


def test_mono_colors(sample_plan_dxf, tmp_path):
    _, color_pdf = render_pages(sample_plan_dxf, tmp_path, name="color")
    meta, mono_pdf = render_pages(sample_plan_dxf, tmp_path, "--colors", "mono", name="mono")
    colors, widths, fills = _strokes(color_pdf)
    mono_colors, mono_widths, mono_fills = _strokes(mono_pdf)
    assert len(colors) > 1
    assert mono_colors == {(0.0, 0.0, 0.0)}
    assert mono_fills <= {(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)}  # black ink, white paper
    assert mono_widths == widths  # lineweights are kept
    assert meta["page_size_mm"] == [297.0, 210.0]


def test_layouts_extra_pages(layouts_dxf, tmp_path):
    meta, pdf = render_pages(layouts_dxf, tmp_path, "--layouts")
    assert meta["pages"] == 3 and pdf.page_count == 3
    assert meta["layouts"] == ["Plan A3", "Detail"]  # Layout1 is empty and skipped
    assert_size((pdf[0].rect.width, pdf[0].rect.height), A4_LANDSCAPE_PT)  # Model space, auto
    assert_size((pdf[1].rect.width, pdf[1].rect.height), A3_LANDSCAPE_PT)
    assert_size((pdf[2].rect.width, pdf[2].rect.height), A4_LANDSCAPE_PT)
    # the "Detail" viewport is zoomed in on the door: its red door arc is larger than on page 1
    def door_width(page):
        doors = [d["rect"].width for d in page.get_drawings() if d.get("color") == (1.0, 0.0, 0.0)]
        return max(doors)

    assert door_width(pdf[2]) > 2 * door_width(pdf[0])


def test_layouts_with_mono_and_page(layouts_dxf, tmp_path):
    meta, pdf = render_pages(layouts_dxf, tmp_path, "--layouts", "--colors", "mono", "--page", "a3")
    assert meta["pages"] == 3
    assert meta["page_size_mm"] == [420.0, 297.0]
    colors, _, _ = _strokes(pdf)
    assert colors == {(0.0, 0.0, 0.0)}


def test_layouts_flag_off_or_no_paper_space(layouts_dxf, sample_plan_dxf, tmp_path):
    meta, pdf = render_pages(layouts_dxf, tmp_path, name="off")
    assert (meta["pages"], meta["layouts"]) == (1, [])
    meta, pdf = render_pages(sample_plan_dxf, tmp_path, "--layouts", name="none")
    assert (meta["pages"], meta["layouts"]) == (1, [])


@pytest.mark.parametrize(
    "args",
    [["--page", "a0"], ["--colors", "grey"], ["--arcs"], ["--scale", "1-100"], ["--raster-fallback"]],
)
def test_dxf2pdf_option_errors(sample_plan_dxf, tmp_path, args):
    run = run_engine("dxf2pdf", "--in", sample_plan_dxf, "--out", tmp_path / "x.pdf", *args)
    assert run.returncode == 5
    assert run.meta["message"].startswith("usage error")
    assert not (tmp_path / "x.pdf").exists()


def test_layout_viewing_empty_model_area_is_a_blank_sheet(sample_plan_dxf, tmp_path):
    import ezdxf

    doc = ezdxf.readfile(sample_plan_dxf)
    sheet = doc.layouts.new("Empty view")
    sheet.page_setup(size=(297, 210), margins=(10, 10, 10, 10), units="mm")
    sheet.add_viewport(center=(148.5, 105), size=(200, 150), view_center_point=(900_000, 900_000),
                       view_height=1000)
    path = tmp_path / "empty_view.dxf"
    doc.saveas(path)
    probe = run_engine("probe-dxf", "--in", path)
    assert probe.meta["layouts"] == ["Empty view"]
    out = tmp_path / "empty_view.pdf"
    run = run_engine("dxf2pdf", "--in", path, "--out", out, "--layouts")
    assert run.returncode == 0, run.stderr
    assert (run.meta["pages"], run.meta["layouts"]) == (2, ["Empty view"])
    pdf = pymupdf.open(out)
    assert_size((pdf[1].rect.width, pdf[1].rect.height), A4_LANDSCAPE_PT)
    assert non_white_fraction(pdf[1].get_pixmap(dpi=50).tobytes("png")) == 0.0
