# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Engine 1.2: the faster PDF output (`cad_engine.pdf_backend`, design D8 of change
dwg-missing-content-notices) against ezdxf's stock PyMuPDF backend — same picture, same PDF
layers, no quadratic growth with the number of paths."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pymupdf
import pytest

from cad_engine import pdf_backend
from cad_engine.budget import Budget
from cad_engine.dxf2pdf import convert

import make_sample
from conftest import FIXTURES


def render(src: Path, out: Path, **kwargs) -> Path:
    convert(str(src), str(out), budget=Budget(None), meta={}, **kwargs)
    return out


def timed(src: Path, out: Path) -> float:
    started = time.perf_counter()
    render(src, out)
    return time.perf_counter() - started


@pytest.fixture
def use_stock(monkeypatch):
    """Switch `render_layout` to ezdxf's own backend (it imports `FastPdfBackend` per call)."""
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend

    def switch() -> None:
        monkeypatch.setattr(pdf_backend, "FastPdfBackend", PyMuPdfBackend)

    return switch


@pytest.fixture(scope="session")
def heavy_5k(gen) -> Path:
    return make_sample.build_heavy_dxf(gen / "heavy_5k.dxf", lines=5_000)


@pytest.fixture(scope="session")
def heavy_20k(gen) -> Path:
    return make_sample.build_heavy_dxf(gen / "heavy_20k.dxf", lines=20_000)


@pytest.fixture(scope="session")
def text_heavy(gen) -> Path:
    return make_sample.build_text_heavy_dxf(gen / "text_heavy.dxf", texts=2_000)


@pytest.fixture(scope="session")
def dense_hatches(gen) -> Path:
    return make_sample.build_heavy_dxf(gen / "dense_hatches.dxf", lines=0, hatches=300)


def pages_pixels(pdf: Path) -> list[np.ndarray]:
    with pymupdf.open(pdf) as doc:
        out = []
        for page in doc:
            pix = page.get_pixmap(dpi=100, alpha=False)
            out.append(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n))
        return out


def layer_names(pdf: Path) -> list[str]:
    with pymupdf.open(pdf) as doc:
        return sorted(info["name"] for info in doc.get_ocgs().values())


@pytest.mark.parametrize("case", ["sample_plan", "layouts", "hatches", "texts", "dimensions", "text_heavy"])
def test_same_picture_and_layers_as_stock(case, request, use_stock, tmp_path):
    src = {
        "sample_plan": lambda: request.getfixturevalue("sample_plan_dxf"),
        "layouts": lambda: request.getfixturevalue("layouts_dxf"),
        "hatches": lambda: FIXTURES / "hatches.dxf",
        "texts": lambda: FIXTURES / "texts.dxf",
        "dimensions": lambda: FIXTURES / "dimensions.dxf",
        "text_heavy": lambda: request.getfixturevalue("text_heavy"),
    }[case]()
    layouts = case == "layouts"
    fast = render(src, tmp_path / "fast.pdf", layouts=layouts)
    use_stock()
    stock = render(src, tmp_path / "stock.pdf", layouts=layouts)
    a, b = pages_pixels(fast), pages_pixels(stock)
    assert len(a) == len(b)
    for fast_page, stock_page in zip(a, b, strict=True):
        assert fast_page.shape == stock_page.shape
        differing = (np.abs(fast_page.astype(int) - stock_page.astype(int)).max(axis=2) > 64).mean()
        assert differing <= 0.001, differing
    assert layer_names(fast) == layer_names(stock)
    with pymupdf.open(fast) as doc:
        assert all(page.is_wrapped for page in doc)


def test_page_content_is_written_in_chunks(heavy_5k, tmp_path):
    pdf = render(heavy_5k, tmp_path / "x.pdf")
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        assert len(page.get_contents()) >= 5_000 // pdf_backend.CHUNK_PATHS
        assert page.is_wrapped
    assert len(layer_names(pdf)) == 20  # one PDF layer per drawing layer that draws


def test_layer_lookup_once_per_layer(monkeypatch, heavy_5k, tmp_path):
    calls = []
    lookup = pymupdf.Page._get_optional_content

    def counting(self, oc):
        calls.append(oc)
        return lookup(self, oc)

    monkeypatch.setattr(pymupdf.Page, "_get_optional_content", counting)
    render(heavy_5k, tmp_path / "x.pdf")
    assert len(set(calls)) == 21  # 20 layers + oc 0 (no layer)
    assert len(calls) <= 21  # the stock backend asks once per path: 5 000 times


def test_faster_than_stock_on_many_paths(heavy_20k, use_stock, tmp_path):
    fast = timed(heavy_20k, tmp_path / "fast.pdf")
    use_stock()
    stock = timed(heavy_20k, tmp_path / "stock.pdf")
    assert stock / fast >= 2.5, (stock, fast)  # measured 4.5x on a laptop


def test_faster_than_stock_on_dense_hatches(dense_hatches, use_stock, tmp_path):
    fast = timed(dense_hatches, tmp_path / "fast.pdf")
    use_stock()
    stock = timed(dense_hatches, tmp_path / "stock.pdf")
    assert stock / fast >= 1.5, (stock, fast)  # measured 2.3x; pattern lines are ezdxf's own work too


def test_faster_than_stock_on_text(text_heavy, use_stock, tmp_path):
    # text is glyph outlines: curves written directly (1.2.1)
    fast = timed(text_heavy, tmp_path / "fast.pdf")
    use_stock()
    stock = timed(text_heavy, tmp_path / "stock.pdf")
    assert stock / fast >= 2.5, (stock, fast)  # measured 4.2x (33 s → 8 s on 4 000 TEXT)


def test_time_grows_linearly(heavy_5k, heavy_20k, tmp_path):
    render(heavy_5k, tmp_path / "warm.pdf")  # fonts, imports
    small = timed(heavy_5k, tmp_path / "small.pdf")
    big = timed(heavy_20k, tmp_path / "big.pdf")
    assert big / small <= 6, (small, big)  # 4x the paths; measured 3.7x
