# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""probe-pdf, probe-dxf, preview-dxf."""

from __future__ import annotations

import pymupdf

from cad_engine.dxfio import sniff

from conftest import non_white_fraction, run_engine


def test_probe_pdf_vector(sample_plan_pdf):
    run = run_engine("probe-pdf", "--in", sample_plan_pdf)
    assert run.returncode == 0, run.stderr
    assert run.meta["pages"] == 1
    assert run.meta["has_vectors"] is True
    assert run.meta["text_entities"] == 0
    assert run.meta["text_as_outlines"] is True


def test_probe_pdf_text_form(antrag_pdf):
    run = run_engine("probe-pdf", "--in", antrag_pdf)
    assert run.returncode == 0
    assert run.meta["has_vectors"] is True
    assert run.meta["text_entities"] >= 80
    assert run.meta["text_as_outlines"] is False


def test_probe_pdf_scan(scan_pdf):
    run = run_engine("probe-pdf", "--in", scan_pdf)
    assert run.returncode == 0
    assert run.meta["has_vectors"] is False
    assert run.meta["images"] == 1


def test_probe_pdf_ocr_scan(invisible_text_pdf):
    run = run_engine("probe-pdf", "--in", invisible_text_pdf)
    assert run.returncode == 0
    assert run.meta["has_vectors"] is False


def test_probe_pdf_reports_pages_beyond_limit(many_pages_pdf):
    run = run_engine("probe-pdf", "--in", many_pages_pdf)
    assert run.returncode == 0  # probes report facts; the caller applies the 50-page limit
    assert run.meta["pages"] == 80


def test_probe_pdf_not_a_pdf(txt_as_pdf):
    run = run_engine("probe-pdf", "--in", txt_as_pdf)
    assert run.returncode == 2
    assert run.meta["code"] == "unsupported_input"


def test_probe_dxf(sample_plan_dxf):
    run = run_engine("probe-dxf", "--in", sample_plan_dxf)
    assert run.returncode == 0
    assert run.meta["entities"] == 19
    assert run.meta["empty"] is False
    assert run.meta["dxf_version"] == "AC1024"
    assert run.meta["layers"] >= 7


def test_probe_dxf_empty(empty_dxf):
    run = run_engine("probe-dxf", "--in", empty_dxf)
    assert run.returncode == 0
    assert run.meta["empty"] is True
    assert run.meta["entities"] == 0


def test_probe_dxf_dwg(fake_dwg):
    run = run_engine("probe-dxf", "--in", fake_dwg)
    assert run.returncode == 2
    assert run.meta["code"] == "dwg_not_dxf"


def test_probe_dxf_binary(binary_dxf):
    run = run_engine("probe-dxf", "--in", binary_dxf)
    assert run.returncode == 0
    assert run.meta["entities"] == 19


def test_probe_dxf_text_file(txt_as_dxf):
    run = run_engine("probe-dxf", "--in", txt_as_dxf)
    assert run.returncode == 2
    assert run.meta["code"] == "unsupported_input"


def test_preview_dxf(sample_plan_dxf, tmp_path):
    out = tmp_path / "preview.png"
    run = run_engine("preview-dxf", "--in", sample_plan_dxf, "--out", out, "--budget-ms", 12000)
    assert run.returncode == 0, run.stderr
    pix = pymupdf.Pixmap(str(out))
    assert pix.width == 1400 and pix.height < pix.width
    assert pix.pixel(0, 0)[:3] == (255, 255, 255)
    assert non_white_fraction(out) > 0.005


def test_preview_dxf_portrait(tmp_path):
    from conftest import FIXTURES

    out = tmp_path / "dims.png"
    run = run_engine("preview-dxf", "--in", FIXTURES / "dimensions.dxf", "--out", out)
    assert run.returncode == 0, run.stderr
    pix = pymupdf.Pixmap(str(out))
    assert pix.height == 1400 and pix.width < pix.height


def test_preview_dxf_errors(fake_dwg, empty_dxf, tmp_path):
    run = run_engine("preview-dxf", "--in", fake_dwg, "--out", tmp_path / "a.png")
    assert (run.returncode, run.meta["code"]) == (2, "dwg_not_dxf")
    run = run_engine("preview-dxf", "--in", empty_dxf, "--out", tmp_path / "b.png")
    assert (run.returncode, run.meta["code"]) == (2, "empty_drawing")
    assert not (tmp_path / "a.png").exists() and not (tmp_path / "b.png").exists()


def test_sniff():
    assert sniff(b"AC1027\x00\x00") == "dwg"
    assert sniff(b"AC1.50\x00") == "dwg"
    assert sniff(b"AutoCAD Binary DXF\r\n\x1a\x00rest") == "binary"
    assert sniff(b"999\r\ncomment\r\n  0\r\nSECTION\r\n  2\r\nHEADER\r\n") == "ascii"
    assert sniff(b"\xef\xbb\xbf0\nSECTION\n2\nENTITIES\n") == "ascii"
    assert sniff(b"hello world\n0\nSECTIONS are great\n") is None
    assert sniff(b"%PDF-1.7\n") is None


def test_probe_dxf_layouts(layouts_dxf, sample_plan_dxf):
    run = run_engine("probe-dxf", "--in", layouts_dxf)
    assert run.returncode == 0, run.stderr
    assert run.meta["layouts"] == ["Plan A3", "Detail"]
    run = run_engine("probe-dxf", "--in", sample_plan_dxf)
    assert run.meta["layouts"] == []


def test_probe_rejects_conversion_options(sample_plan_dxf, scan_pdf):
    run = run_engine("probe-dxf", "--in", sample_plan_dxf, "--layouts")
    assert run.returncode == 5
    run = run_engine("probe-pdf", "--in", scan_pdf, "--raster-fallback")
    assert run.returncode == 5


# -- engine 1.6: the PDF's cost in the meta, heavy pages not read ---------------------------
def test_probe_pdf_heavy_page_fits_the_budget(gen):
    import time

    import make_sample

    # 150 000 glyph outlines on one A3 page (the PROD 2026-11-16 pattern: text as curves)
    path = make_sample.build_heavy_pdf(gen / "probe_heavy.pdf", 150_000 * 6, kind="outlines")
    started = time.perf_counter()
    run = run_engine("probe-pdf", "--in", path, "--budget-ms", 10000)
    assert run.returncode == 0, run.stderr
    assert time.perf_counter() - started < 10
    assert run.meta["has_vectors"] is True
    assert run.meta["text_as_outlines"] is True
    assert run.meta["vector_paths"] == 150_000
    assert run.meta["vector_segments"] == 150_000 * 5
    assert run.meta["suggested_purpose"] == "cad"  # engine 1.7: a page too heavy to read


def test_probe_pdf_reports_the_cost_when_the_budget_runs_out_later(gen):
    import pytest

    from cad_engine.errors import BudgetExceeded
    from cad_engine.probe import probe_pdf

    import make_sample

    class SpentAfterCounting:
        """The budget runs out right after the cost of the page is counted."""

        def __init__(self, pages: int) -> None:
            self.calls = 0
            self.pages = pages

        def check(self) -> None:
            self.calls += 1
            if self.calls > self.pages:
                raise BudgetExceeded("time budget of 1 ms exceeded")

    path = make_sample.build_heavy_pdf(gen / "probe_spent.pdf", 60_000, kind="lines")
    meta: dict = {}
    with pytest.raises(BudgetExceeded):
        probe_pdf(str(path), budget=SpentAfterCounting(1), meta=meta)
    assert (meta["vector_paths"], meta["vector_segments"]) == (30_000, 30_000)
    assert "has_vectors" not in meta
    assert "suggested_purpose" not in meta  # engine 1.7: the service then takes «cad»


def test_probe_pdf_reads_an_ordinary_plan(sample_plan_pdf, monkeypatch):
    from cad_engine.budget import Budget
    from cad_engine.probe import probe_pdf

    calls = []
    original = pymupdf.Page.get_drawings

    def counting(self, *args, **kwargs):
        calls.append(self.number)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_drawings", counting)
    meta: dict = {}
    probe_pdf(str(sample_plan_pdf), budget=Budget(10000), meta=meta)
    assert calls == [0]
    assert meta["has_vectors"] is True and meta["text_as_outlines"] is True
    assert 0 < meta["vector_paths"] <= meta["vector_segments"]


def test_probe_pdf_scan_cost(scan_pdf):
    run = run_engine("probe-pdf", "--in", scan_pdf)
    assert run.returncode == 0
    assert run.meta["has_vectors"] is False
    assert run.meta["vector_paths"] == 0 and run.meta["vector_segments"] == 0


def test_probe_dxf_has_no_pdf_cost(sample_plan_dxf):
    run = run_engine("probe-dxf", "--in", sample_plan_dxf)
    assert run.meta["vector_paths"] is None and run.meta["vector_segments"] is None


# -- engine 1.7: the suggested purpose (cut for a PDF of fills only) --------------------------
def _suggest(pdf) -> dict:
    run = run_engine("probe-pdf", "--in", pdf)
    assert run.returncode == 0, run.stderr
    return run.meta


def test_probe_suggests_cut_for_a_logo(logo_pdf):
    meta = _suggest(logo_pdf)
    assert meta["suggested_purpose"] == "cut"
    assert meta["fill_paths"] == 2 and meta["stroke_paths"] == 0  # the paper does not count
    assert meta["text_entities"] == 0 and meta["images"] == 0


def test_probe_suggests_cad_for_a_plan(sample_plan_pdf, antrag_pdf, cad_like_pdf):
    for pdf in (sample_plan_pdf, antrag_pdf, cad_like_pdf):
        assert _suggest(pdf)["suggested_purpose"] == "cad", pdf.name


def test_probe_suggests_cad_for_a_logo_with_a_stroke_an_image_or_text(logo_variant_pdf):
    stroked = _suggest(logo_variant_pdf("stroke"))
    assert stroked["suggested_purpose"] == "cad" and stroked["stroke_paths"] == 1
    assert _suggest(logo_variant_pdf("image"))["suggested_purpose"] == "cad"
    assert _suggest(logo_variant_pdf("text"))["suggested_purpose"] == "cad"


def test_probe_suggests_cad_beyond_the_pages_read(logo_variant_pdf):
    meta = _suggest(logo_variant_pdf("six_pages"))
    assert meta["pages"] == 6 and meta["suggested_purpose"] == "cad"


def test_probe_suggests_cad_for_a_scan_and_paper_only(scan_pdf, paper_only_pdf):
    assert _suggest(scan_pdf)["suggested_purpose"] == "cad"
    assert _suggest(paper_only_pdf)["suggested_purpose"] == "cad"


def test_probe_suggests_cut_for_white_and_plate_logos(white_logo_pdf, plate_pdf):
    assert _suggest(white_logo_pdf)["suggested_purpose"] == "cut"
    assert _suggest(plate_pdf)["suggested_purpose"] == "cut"
