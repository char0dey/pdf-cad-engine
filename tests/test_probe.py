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
