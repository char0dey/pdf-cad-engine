# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""CLI contract: --version, one JSON line on stdout, --meta, exit codes 3 / 5, outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from importlib.metadata import version

import pytest

import cad_engine
from cad_engine import cli
from cad_engine.errors import BudgetExceeded, InputError

from conftest import ENGINE_DIR, run_engine

META_KEYS = {
    "version", "command", "pages", "entities", "layers", "text_entities", "images", "has_vectors",
    "text_as_outlines", "traced_pages", "recognized", "scale", "units", "empty", "dxf_version",
    "page_size_mm", "layouts", "scanned", "missing", "ms_entities", "ms_drawable", "empty_reason",
    "simplified_hatches", "model_blank",
    "warnings", "ms", "code", "message",
}


def test_version_isolated_mode():
    proc = subprocess.run([sys.executable, "-I", "-m", "cad_engine", "--version"], cwd=ENGINE_DIR,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert proc.stdout == "1.4.1\n"


def test_version_matches_package_metadata():
    assert cad_engine.__version__ == "1.4.1"
    assert version("pdf-cad-engine") == cad_engine.__version__


def test_stdout_is_exactly_one_json_line(sample_plan_pdf, tmp_path):
    run = run_engine("pdf2dxf", "--in", sample_plan_pdf, "--out", tmp_path / "x.dxf", "--meta", "-")
    assert run.returncode == 0, run.stderr
    assert run.stdout.count("\n") == 1 and run.stdout.endswith("\n")
    meta = json.loads(run.stdout)
    assert set(meta) == META_KEYS
    assert meta["version"] == "1.4.1"
    assert meta["command"] == "pdf2dxf"
    assert meta["code"] is None and meta["message"] is None
    assert isinstance(meta["ms"], int) and meta["ms"] >= 0
    assert meta["traced_pages"] == []
    # phase-2 keys with every flag off
    assert meta["recognized"] == {"circles": 0, "arcs": 0, "hatches": 0, "linetypes": 0, "merged": 0}
    assert meta["warnings"] == []
    assert (meta["scale"], meta["units"]) == ("asis", "mm")
    assert meta["layouts"] is None


def test_meta_file_matches_stdout(sample_plan_dxf, tmp_path):
    meta_path = tmp_path / "meta.json"
    run = run_engine("probe-dxf", "--in", sample_plan_dxf, "--meta", meta_path)
    assert run.returncode == 0, run.stderr
    assert json.loads(meta_path.read_text()) == run.meta


def test_budget_1ms_exits_3_without_output(sample_plan_pdf, tmp_path):
    out = tmp_path / "x.dxf"
    preview = tmp_path / "x.png"
    run = run_engine("pdf2dxf", "--in", sample_plan_pdf, "--out", out, "--preview", preview, "--budget-ms", 1)
    assert run.returncode == 3, run.stderr
    assert run.meta["code"] == "budget_exceeded"
    assert run.stdout.count("\n") == 1
    assert not out.exists() and not preview.exists()
    assert not list(tmp_path.glob("*.part"))


def test_budget_1ms_dxf2pdf(sample_plan_dxf, tmp_path):
    run = run_engine("dxf2pdf", "--in", sample_plan_dxf, "--out", tmp_path / "x.pdf", "--budget-ms", 1)
    assert run.returncode == 3
    assert run.meta["code"] == "budget_exceeded"
    assert not (tmp_path / "x.pdf").exists()


def test_missing_input_exits_5(tmp_path):
    run = run_engine("pdf2dxf", "--in", tmp_path / "nope.pdf", "--out", tmp_path / "x.dxf")
    assert run.returncode == 5
    assert run.meta["code"] == "engine_error"
    assert "not found" in run.meta["message"]


@pytest.mark.parametrize(
    "args",
    [
        ["pdf2dxf", "--in", "x.pdf"],  # missing --out
        ["convert", "--in", "x.pdf"],  # unknown command
        ["probe-pdf"],  # missing --in
        ["dxf2pdf", "--in", "x.dxf", "--out", "y.pdf", "--preview", "y.png"],  # --preview is pdf2dxf-only
    ],
)
def test_usage_errors_exit_5_not_2(args):
    run = run_engine(*args)
    assert run.returncode == 5
    assert run.meta["code"] == "engine_error"
    assert run.meta["message"].startswith("usage error")


def test_output_equal_to_input_is_rejected(sample_plan_pdf):
    run = run_engine("pdf2dxf", "--in", sample_plan_pdf, "--out", sample_plan_pdf)
    assert run.returncode == 5
    assert sample_plan_pdf.exists()


def test_input_error_meta_and_no_output(scan_pdf, tmp_path):
    out = tmp_path / "scan.dxf"
    run = run_engine("pdf2dxf", "--in", scan_pdf, "--out", out, "--meta", tmp_path / "m.json")
    assert run.returncode == 2
    assert run.meta["code"] == "no_vectors"
    assert run.meta["message"]
    assert run.meta["pages"] == 1
    assert run.meta["has_vectors"] is False
    assert run.meta["images"] == 1
    assert run.stdout.count("\n") == 1
    assert not out.exists()
    assert json.loads((tmp_path / "m.json").read_text())["code"] == "no_vectors"


def test_tmp_dir_is_created(sample_plan_dxf, tmp_path):
    tmp_dir = tmp_path / "engine-tmp"
    run = run_engine("probe-dxf", "--in", sample_plan_dxf, "--tmp", tmp_dir)
    assert run.returncode == 0
    assert tmp_dir.is_dir()


@pytest.mark.parametrize(
    ("exc", "exit_code", "code"),
    [
        (MemoryError(), 4, "memory_limit"),
        (RuntimeError("code=1: malloc (734003200 bytes) failed in rasterizer"), 4, "memory_limit"),
        (RuntimeError("boom"), 5, "engine_error"),
        (BudgetExceeded("time budget of 5 ms exceeded"), 3, "budget_exceeded"),
        (InputError("too_complex", "too many entities"), 2, "too_complex"),
    ],
)
def test_exit_code_mapping(monkeypatch, capfd, sample_plan_dxf, tmp_path, exc, exit_code, code):
    """In-process: exceptions from a command map to exit codes, meta, and output cleanup.
    (RLIMIT_AS itself is not exercised: macOS ignores it and a tiny limit on Linux breaks imports.)"""
    out = tmp_path / "x.pdf"

    def failing_dispatch(args, budget, meta):
        out.write_bytes(b"partial")
        raise exc

    monkeypatch.setattr(cli, "_dispatch", failing_dispatch)
    monkeypatch.setattr(cli, "_limit_memory", lambda: None)
    rc = cli.main(["dxf2pdf", "--in", str(sample_plan_dxf), "--out", str(out)])
    captured = capfd.readouterr()
    assert rc == exit_code
    lines = captured.out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["code"] == code
    assert not out.exists()
