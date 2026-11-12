# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Engine 1.4: an empty model space whose drawing is on paper-space sheets converts to the
sheets only, and neither probe calls it empty; empty sheets are still refused."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
import pytest

from cad_engine.budget import Budget
from cad_engine.dxf2pdf import convert
from cad_engine.errors import InputError
from cad_engine.probe import probe_dxf
from cad_engine.scan import scan_dxf

import make_sample
from conftest import A3_LANDSCAPE_PT, run_engine


@pytest.fixture(scope="session")
def sheets(gen):
    """Factory: `make_sample.build_sheets_only_dxf` into the session dir, cached by name."""
    built: dict = {}

    def make(name: str, **kwargs) -> Path:
        if name not in built:
            built[name] = make_sample.build_sheets_only_dxf(gen / f"sheets_{name}.dxf", **kwargs)
        return built[name]

    return make


def render(src: Path, out: Path, **kwargs) -> dict:
    meta: dict = {}
    convert(str(src), str(out), budget=Budget(None), meta=meta, **kwargs)
    return meta


def probe(src: Path, *, light: bool) -> dict:
    meta: dict = {}
    probe_dxf(str(src), budget=Budget(None), meta=meta, light=light)
    return meta


def inked(page) -> bool:
    pix = page.get_pixmap(dpi=50, alpha=False)
    return bool((np.frombuffer(pix.samples, dtype=np.uint8) < 200).any())


# -- the drawing on its sheets ---------------------------------------------------------------
def test_empty_model_with_sheets_gives_the_sheets(sheets, tmp_path):
    meta = render(sheets("drawing"), tmp_path / "out.pdf", layouts=True)
    assert meta["entities"] == 0
    assert meta["model_blank"] is True
    assert meta["layouts"] == ["A1", "A2"]
    assert meta["pages"] == 2
    assert meta["page_size_mm"] == [420.0, 297.0]
    with pymupdf.open(tmp_path / "out.pdf") as pdf:
        assert pdf.page_count == 2
        for page in pdf:
            assert abs(page.rect.width - A3_LANDSCAPE_PT[0]) < 1
            assert abs(page.rect.height - A3_LANDSCAPE_PT[1]) < 1
            assert inked(page)


def test_cli_empty_model_with_sheets(sheets, tmp_path):
    out = tmp_path / "cli.pdf"
    run = run_engine("dxf2pdf", "--in", sheets("drawing"), "--out", out, "--layouts")
    assert run.returncode == 0, run.stderr
    assert (run.meta["pages"], run.meta["layouts"], run.meta["model_blank"]) == (2, ["A1", "A2"], True)
    assert run.meta["code"] is None
    assert out.stat().st_size > 0


def test_without_layouts_the_empty_model_is_still_refused(sheets, tmp_path):
    with pytest.raises(InputError) as exc:
        render(sheets("drawing"), tmp_path / "out.pdf", layouts=False)
    assert exc.value.code == "empty_drawing"
    run = run_engine("dxf2pdf", "--in", sheets("drawing"), "--out", tmp_path / "cli.pdf")
    assert (run.returncode, run.meta["code"]) == (2, "empty_drawing")
    assert not (tmp_path / "out.pdf").exists() and not (tmp_path / "cli.pdf").exists()


@pytest.mark.parametrize("light", [False, True])
def test_probe_does_not_call_it_empty(sheets, light):
    meta = probe(sheets("drawing"), light=light)
    assert meta["empty"] is False
    assert "empty_reason" not in meta
    assert (meta["entities"], meta["ms_entities"], meta["ms_drawable"]) == (0, 0, 0)
    if not light:
        assert meta["layouts"] == ["A1", "A2"]


@pytest.mark.parametrize("fmt", ["asc", "bin"])
def test_scan_counts_paper_space(sheets, fmt):
    # A1 is the active layout (ENTITIES, group 67 = 1), A2 lives in its *Paper_Space1 block
    result = scan_dxf(str(sheets(f"drawing_{fmt}", fmt=fmt)))
    assert (result.ms_entities, result.ms_drawable) == (0, 0)
    assert (result.ps_entities, result.ps_drawable) == (10, 10)  # frame, 2 lines, 2 texts × 2 sheets
    assert result.model_nothing_to_draw is True
    assert result.nothing_to_draw is False


def test_scan_keeps_the_model_space_counts(layouts_dxf, sample_plan_dxf):
    with_sheets, plain = scan_dxf(str(layouts_dxf)), scan_dxf(str(sample_plan_dxf))
    assert (with_sheets.ms_entities, with_sheets.ms_drawable) == (19, 19)
    assert (plain.ms_entities, plain.ms_drawable) == (19, 19)
    # the DETAIL title; viewports are not counted
    assert (with_sheets.ps_entities, with_sheets.ps_drawable) == (1, 1)
    assert (plain.ps_entities, plain.ps_drawable) == (0, 0)


def test_viewport_only_sheet_is_left_out_when_the_model_is_empty(sheets, tmp_path):
    import ezdxf

    doc = ezdxf.readfile(sheets("drawing"))
    view = doc.layouts.new("View")
    view.page_setup(size=(297, 210), margins=(10, 10, 10, 10), units="mm")
    view.add_viewport(center=(148.5, 105), size=(250, 170), view_center_point=(0, 0), view_height=1000)
    src = tmp_path / "with_view.dxf"
    doc.saveas(src)
    meta = render(src, tmp_path / "out.pdf", layouts=True)
    assert meta["layouts"] == ["A1", "A2"]  # the viewport would show an empty model: no blank sheet
    assert probe(src, light=False)["layouts"] == ["A1", "A2"]


def test_big_sheet_with_light_probe(sheets, tmp_path):
    # past the full-probe limit the service runs --light; 20 000 entities on one sheet
    src = sheets("big", sheets=("Sheet1",), lines=20_000)
    meta = probe(src, light=True)
    assert meta["empty"] is False
    meta = render(src, tmp_path / "big.pdf", layouts=True)
    assert (meta["pages"], meta["layouts"], meta["model_blank"]) == (1, ["Sheet1"], True)


# -- nothing on the sheets either --------------------------------------------------------------
@pytest.mark.parametrize(
    ("content", "code"),
    [("none", "empty_drawing"), ("viewport", "empty_drawing"), ("xref", "external_refs_only")],
)
def test_empty_model_and_empty_sheets_are_refused(sheets, tmp_path, content, code):
    src = sheets(content, content=content)
    with pytest.raises(InputError) as exc:
        render(src, tmp_path / "out.pdf", layouts=True)
    assert exc.value.code == code
    assert not (tmp_path / "out.pdf").exists()
    for light in (False, True):
        meta = probe(src, light=light)
        assert (meta["empty"], meta["empty_reason"]) == (True, code), light
    run = run_engine("dxf2pdf", "--in", src, "--out", tmp_path / "cli.pdf", "--layouts")
    assert (run.returncode, run.meta["code"]) == (2, code)
