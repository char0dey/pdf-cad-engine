# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Hatch pattern cap and a model space without visible entities (engine 1.3)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from cad_engine import hatch_limit
from cad_engine.budget import Budget
from cad_engine.dxf2pdf import convert
from cad_engine.errors import InputError

import make_sample
from conftest import FIXTURES, run_engine


def render(src: Path, out: Path, **kwargs) -> dict:
    meta: dict = {}
    convert(str(src), str(out), budget=Budget(None), meta=meta, **kwargs)
    return meta


def pixels(pdf: Path) -> list[np.ndarray]:
    with pymupdf.open(pdf) as doc:
        out = []
        for page in doc:
            pix = page.get_pixmap(dpi=100, alpha=False)
            out.append(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n))
        return out


def test_hatch_across_kilometres_is_outlined(gen, tmp_path):
    src = make_sample.build_dense_hatch_dxf(gen / "civil_dense.dxf")
    started = time.perf_counter()
    meta = render(src, tmp_path / "out.pdf")
    assert time.perf_counter() - started < 30
    assert meta["simplified_hatches"] == 1
    assert meta["pages"] == 1
    # the outline and the 300 lines are there
    assert (pixels(tmp_path / "out.pdf")[0] < 200).any()


def test_each_block_insert_counts(gen, tmp_path):
    src = make_sample.build_dense_hatch_dxf(gen / "civil_dense_x20.dxf", inserts=20, lines=0)
    assert render(src, tmp_path / "out.pdf")["simplified_hatches"] == 20


def test_dense_hatch_on_a_sheet_counts(gen, tmp_path):
    src = make_sample.build_dense_hatch_dxf(gen / "sheet_dense.dxf", paper=True)
    meta = render(src, tmp_path / "out.pdf", layouts=True)
    assert meta["layouts"] == ["Sheet1"]
    assert meta["simplified_hatches"] == 1


def test_solid_fill_of_any_size_is_not_simplified(gen, tmp_path):
    src = make_sample.build_dense_hatch_dxf(gen / "civil_solid.dxf", pattern="SOLID")
    assert render(src, tmp_path / "out.pdf")["simplified_hatches"] == 0


def test_counter_stops_a_pattern_the_estimate_lets_through(gen, tmp_path, monkeypatch):
    # ANSI31 is continuous: ~one segment per hatch line; 400 x 400 at scale 0.5 → a few thousand
    src = make_sample.build_dense_hatch_dxf(gen / "ansi31_mid.dxf", pattern="ANSI31", scale=0.5,
                                            width=400, height=400, lines=0)
    assert render(src, tmp_path / "a.pdf")["simplified_hatches"] == 0
    monkeypatch.setattr(hatch_limit, "MAX_HATCH_SEGMENTS", 200)
    monkeypatch.setattr(hatch_limit, "EARLY_OUT_FACTOR", 10**9)  # only the counter can stop it
    assert render(src, tmp_path / "b.pdf")["simplified_hatches"] == 1


@pytest.mark.parametrize("src_name", ["hatches", "dense_hatches"])
def test_ordinary_hatches_look_as_before(src_name, gen, tmp_path, monkeypatch):
    src = FIXTURES / "hatches.dxf" if src_name == "hatches" else make_sample.build_heavy_dxf(
        gen / "dense_hatches_reg.dxf", lines=0, hatches=300)
    meta = render(src, tmp_path / "capped.pdf")
    assert meta["simplified_hatches"] == 0
    from ezdxf.addons.drawing import Frontend

    class Stock(Frontend):
        def __init__(self, *args, max_hatch_segments=None, tick=None, **kwargs):
            super().__init__(*args, **kwargs)
            self.simplified_hatches = 0

    monkeypatch.setattr(hatch_limit, "EngineFrontend", Stock)
    render(src, tmp_path / "stock.pdf")
    a, b = pixels(tmp_path / "capped.pdf"), pixels(tmp_path / "stock.pdf")
    assert len(a) == len(b)
    for capped, stock in zip(a, b, strict=True):
        differing = (np.abs(capped.astype(int) - stock.astype(int)).max(axis=2) > 64).mean()
        assert differing <= 0.001, differing


def test_blank_model_with_a_sheet_gives_the_sheet(gen, tmp_path):
    src = make_sample.build_blank_model_dxf(gen / "blank_model_sheet.dxf", with_layout=True)
    meta = render(src, tmp_path / "out.pdf", layouts=True)
    assert meta["model_blank"] is True
    assert meta["layouts"] == ["Sheet1"]
    assert meta["pages"] == 1
    with pymupdf.open(tmp_path / "out.pdf") as doc:
        assert doc.page_count == 1
    assert (pixels(tmp_path / "out.pdf")[0] < 200).any()


def test_blank_model_without_a_sheet_is_refused(gen, tmp_path):
    src = make_sample.build_blank_model_dxf(gen / "blank_model.dxf", with_layout=False)
    with pytest.raises(InputError) as exc:
        render(src, tmp_path / "out.pdf", layouts=True)
    assert exc.value.code == "empty_drawing"
    run = run_engine("dxf2pdf", "--in", src, "--out", tmp_path / "cli.pdf", "--layouts")
    assert run.returncode == 2, run.stderr
    assert run.meta["code"] == "empty_drawing"
    assert not (tmp_path / "cli.pdf").exists()
