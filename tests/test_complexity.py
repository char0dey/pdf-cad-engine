# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""PDF page complexity before get_drawings() (engine 1.3, cad_engine.complexity)."""

from __future__ import annotations

import time

import pytest

from cad_engine.complexity import MAX_PAGE_BYTES, check_pdf_complexity, page_cost
from cad_engine.errors import InputError

import make_sample
from conftest import run_engine


def _cost(path):
    import pymupdf

    with pymupdf.open(path) as doc:
        return page_cost(doc, doc[0])


@pytest.fixture(scope="session")
def dense_pdf(gen):
    # 1.5 million one-segment paths: get_drawings() needs ~2.4 GB, past the engine's 2 GB limit
    return make_sample.build_dense_lines_pdf(gen / "dense_lines.pdf", 1_500_000)


def test_counts_paths_and_segments(gen):
    cost = _cost(make_sample.build_dense_lines_pdf(gen / "lines_20k.pdf", 20_000))
    assert (cost.paths, cost.segments) == (20_000, 20_000)


def test_form_counts_once_per_call(gen):
    cost = _cost(make_sample.build_dense_lines_pdf(gen / "form_20k_x100.pdf", 20_000, form_calls=100))
    assert (cost.paths, cost.segments) == (2_000_000, 2_000_000)


def test_strings_comments_and_inline_images_are_not_operators(gen):
    noise = (
        b"BT /F1 12 Tf (S f B b \\( S \\) (nested S) f) Tj <53 66> Tj ET\n"
        b"% S f B a comment\n"
        b"BI /W 2 /H 1 /BPC 8 /CS /G ID S f EI\n"
        b"10 10 m 20 20 l W n\n"  # a clipping path, not a drawing
        b"0 0 100 100 re f\n"
    )
    cost = _cost(make_sample.build_dense_lines_pdf(gen / "noise.pdf", 3, noise=noise))
    # + the filled rectangle; `l` of the clip counts as a segment
    assert (cost.paths, cost.segments) == (3 + 1, 3 + 1 + 1)


def test_ordinary_pdfs_pass(sample_plan_pdf, antrag_pdf):
    import pymupdf

    for path in (sample_plan_pdf, antrag_pdf):
        with pymupdf.open(path) as doc:
            check_pdf_complexity(doc)
            assert page_cost(doc, doc[0]).bytes < MAX_PAGE_BYTES / 100


def test_dense_page_is_refused_in_process(dense_pdf):
    import pymupdf

    started = time.perf_counter()
    with pymupdf.open(dense_pdf) as doc, pytest.raises(InputError) as exc:
        check_pdf_complexity(doc)
    assert exc.value.code == "too_complex"
    assert "page 1" in str(exc.value)
    assert time.perf_counter() - started < 5


@pytest.mark.parametrize("command", ["probe-pdf", "pdf2dxf"])
def test_dense_page_is_refused_by_the_cli(dense_pdf, tmp_path, command):
    args = ["--in", dense_pdf, "--budget-ms", "60000"]
    if command == "pdf2dxf":
        args += ["--out", tmp_path / "out.dxf"]
    started = time.perf_counter()
    run = run_engine(command, *args)
    assert run.returncode == 2, run.stderr
    assert run.meta["code"] == "too_complex"
    assert time.perf_counter() - started < 10
    assert not (tmp_path / "out.dxf").exists()
