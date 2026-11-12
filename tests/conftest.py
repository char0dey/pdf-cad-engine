# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Shared fixtures: generated inputs (session tmp dir) and a CLI runner.

The CLI is always run as a real subprocess in isolated mode
(``python -I -m cad_engine ...``) from the package directory, exactly as the
service runs it. Optional large real-world samples (floorplan.dxf,
Ceco.NET-Architecture-Tm-53.dxf) are looked up in ``$CAD_ENGINE_SAMPLES_DIR``
(fallback: the prototype download folder on the developer machine); tests that
need them are skipped when they are missing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

import make_sample

ENGINE_DIR = Path(__file__).resolve().parents[1]
FIXTURES = ENGINE_DIR / "tests" / "fixtures"
SAMPLES_ENV = "CAD_ENGINE_SAMPLES_DIR"
_DEV_SAMPLES = Path(
    "/private/tmp/claude-501/-Users-charodey-MyProjects-saaspdf/"
    "32daa1bb-ee9a-4698-8ead-e9f0521c57ee/scratchpad/proto/samples_dl"
)

A4_LANDSCAPE_PT = (841.89, 595.28)
A4_PORTRAIT_PT = (595.28, 841.89)
A3_LANDSCAPE_PT = (1190.55, 841.89)


@dataclass
class EngineRun:
    returncode: int
    meta: dict | None
    stdout: str
    stderr: str


def run_engine(*args, env: dict | None = None, timeout: float = 300) -> EngineRun:
    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-I", "-m", "cad_engine", *map(str, args)],
        cwd=ENGINE_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=proc_env,
    )
    lines = proc.stdout.splitlines()
    meta = None
    if lines:
        try:
            meta = json.loads(lines[-1])
        except json.JSONDecodeError:
            meta = None
    return EngineRun(proc.returncode, meta, proc.stdout, proc.stderr)


def extra_sample(name: str) -> Path | None:
    for base in (os.environ.get(SAMPLES_ENV), _DEV_SAMPLES):
        if base and (Path(base) / name).is_file():
            return Path(base) / name
    return None


def non_white_fraction(png: Path | bytes, threshold: int = 245) -> float:
    import numpy as np
    import pymupdf

    pix = pymupdf.Pixmap(str(png)) if isinstance(png, Path) else pymupdf.Pixmap(png)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    return float((data.min(axis=2) < threshold).mean())


def dxf_to_pdf_in_process(src: Path, out: Path) -> Path:
    from cad_engine.budget import Budget
    from cad_engine.dxf2pdf import convert

    convert(str(src), str(out), budget=Budget(None), meta={})
    return out


# -- generated inputs ------------------------------------------------------------------------
@pytest.fixture(scope="session")
def gen(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("generated")


@pytest.fixture(scope="session")
def sample_plan_dxf(gen) -> Path:
    return make_sample.build_sample_plan(gen / "sample_plan.dxf")


@pytest.fixture(scope="session")
def sample_plan_pdf(gen, sample_plan_dxf) -> Path:
    return dxf_to_pdf_in_process(sample_plan_dxf, gen / "sample_plan.pdf")


@pytest.fixture(scope="session")
def binary_dxf(gen) -> Path:
    return make_sample.build_sample_plan(gen / "sample_plan_binary.dxf", fmt="bin")


@pytest.fixture(scope="session")
def empty_dxf(gen) -> Path:
    return make_sample.build_empty_dxf(gen / "empty.dxf")


@pytest.fixture(scope="session")
def truncated_dxf(gen, sample_plan_dxf):
    """Factory: the sample plan cut at a fraction of its size (ENTITIES spans ~72-80 %)."""

    def make(ratio: float) -> Path:
        return make_sample.truncate_file(sample_plan_dxf, gen / f"truncated_{int(ratio * 100)}.dxf", ratio)

    return make


@pytest.fixture(scope="session")
def fake_dwg(gen) -> Path:
    path = gen / "plan.dxf"  # DWG bytes under a .dxf name, as a visitor would upload it
    path.write_bytes(b"AC1027" + b"\x00" * 2048)
    return path


@pytest.fixture(scope="session")
def txt_as_dxf(gen) -> Path:
    path = gen / "notes.dxf"
    path.write_text("These are meeting notes, not a drawing.\n" * 50)
    return path


@pytest.fixture(scope="session")
def txt_as_pdf(gen) -> Path:
    path = gen / "notes.pdf"
    path.write_text("These are meeting notes, not a PDF.\n" * 50)
    return path


@pytest.fixture(scope="session")
def hidden_layer_dxf(gen) -> Path:
    return make_sample.build_hidden_layer_dxf(gen / "hidden_layer.dxf")


@pytest.fixture(scope="session")
def broken_insert_dxf(gen) -> Path:
    return make_sample.build_broken_insert_dxf(gen / "broken_insert.dxf")


@pytest.fixture(scope="session")
def white_dxf(gen) -> Path:
    return make_sample.build_white_dxf(gen / "white.dxf")


@pytest.fixture(scope="session")
def big_extent_dxf(gen) -> Path:
    return make_sample.build_big_extent_dxf(gen / "site_45x30m.dxf")


@pytest.fixture(scope="session")
def three_page_pdf(gen) -> Path:
    return make_sample.build_three_page_pdf(gen / "three_pages.pdf")


@pytest.fixture(scope="session")
def cad_like_pdf(gen) -> Path:
    return make_sample.build_cad_like_pdf(gen / "cad_like.pdf")


@pytest.fixture(scope="session")
def form_pdf(gen) -> Path:
    return make_sample.build_form_pdf(gen / "form.pdf")


@pytest.fixture(scope="session")
def ocg_pdf(gen) -> Path:
    return make_sample.build_ocg_pdf(gen / "layers.pdf")


@pytest.fixture(scope="session")
def lineweight_pdf(gen) -> Path:
    return make_sample.build_lineweight_pdf(gen / "lineweights.pdf")


@pytest.fixture(scope="session")
def rotated_pdf(gen) -> Path:
    return make_sample.build_rotated_pdf(gen / "rotated.pdf")


@pytest.fixture(scope="session")
def scan_pdf(gen) -> Path:
    return make_sample.build_scan_pdf(gen / "scan.pdf")


@pytest.fixture(scope="session")
def invisible_text_pdf(gen) -> Path:
    return make_sample.build_invisible_text_pdf(gen / "scan_ocr.pdf")


@pytest.fixture(scope="session")
def many_pages_pdf(gen) -> Path:
    return make_sample.build_many_pages_pdf(gen / "eighty_pages.pdf", pages=80)


# -- phase 2 (engine 1.1) inputs ----------------------------------------------------------
@pytest.fixture(scope="session")
def layouts_dxf(gen) -> Path:
    return make_sample.build_layouts_dxf(gen / "plan_layouts.dxf")


@pytest.fixture(scope="session")
def circles_pdf(gen) -> Path:
    return make_sample.build_circles_pdf(gen / "circles.pdf")


@pytest.fixture(scope="session")
def fills_pdf(gen) -> Path:
    return make_sample.build_fills_pdf(gen / "fills.pdf")


@pytest.fixture(scope="session")
def dashes_pdf(gen) -> Path:
    return make_sample.build_dashes_pdf(gen / "dashes.pdf")


@pytest.fixture(scope="session")
def merge_pdf(gen) -> Path:
    return make_sample.build_merge_pdf(gen / "merge.pdf")


@pytest.fixture(scope="session")
def scale_pdf(gen) -> Path:
    return make_sample.build_scale_pdf(gen / "scale.pdf")


@pytest.fixture(scope="session")
def scan_drawing_pdf(gen) -> Path:
    return make_sample.build_scan_drawing_pdf(gen / "scan_drawing.pdf")


@pytest.fixture(scope="session")
def mixed_pdf(gen) -> Path:
    return make_sample.build_mixed_pdf(gen / "mixed.pdf")


def has_cv2() -> bool:
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def pixel_difference(a: Path, b: Path, threshold: int = 64) -> float:
    """Share of pixels whose colour differs by more than `threshold` in any channel."""
    import numpy as np
    import pymupdf

    def pixels(path: Path):
        pix = pymupdf.Pixmap(str(path))
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]

    pa, pb = pixels(a), pixels(b)
    assert pa.shape == pb.shape, (pa.shape, pb.shape)
    return float((np.abs(pa.astype(int) - pb.astype(int)).max(axis=2) > threshold).mean())


# -- committed fixtures and optional real-world samples -----------------------------------
@pytest.fixture(scope="session")
def antrag_pdf(gen) -> Path:
    """A 4-page multilingual application form with real text and rules — generated, so the public
    repository ships no third-party blank (it stands in for the official form used during research)."""
    import pymupdf

    path = gen / "antrag_like.pdf"
    labels = [
        "Familienname / Family name / Nom de famille", "Vornamen / First names / Prénoms",
        "Geburtsdatum / Date of birth / Date de naissance", "Geburtsort / Place of birth",
        "Staatsangehörigkeit(en) / Nationality", "Geschlecht: [ ] männlich [ ] weiblich",
        "Familienstand / Marital status / État civil", "Anschrift / Address / Adresse",
        "Straße, Hausnummer", "Postleitzahl, Ort", "Telefon / E-Mail", "Reisepass Nr. / Passport no.",
        "ausgestellt am / issued on", "gültig bis / valid until", "Zweck des Aufenthalts / Purpose of stay",
        "Arbeitgeber / Employer / Employeur", "Einkommen / Income / Revenu",
        "Krankenversicherung / Health insurance",
        "Unterschrift / Signature", "Datum / Date",
    ]
    doc = pymupdf.open()
    for p in range(4):
        page = doc.new_page(width=595, height=842)
        page.insert_text((50, 60), f"Antrag auf Erteilung eines Aufenthaltstitels — Seite {p + 1}/4",
                         fontsize=14, fontname="helv")
        y = 100.0
        for label in labels:
            page.insert_text((50, y), label, fontsize=9, fontname="helv", color=(0, 0, 0.4))
            page.draw_rect(pymupdf.Rect(300, y - 12, 545, y + 4), color=(0, 0, 0), width=0.5)
            y += 30
    doc.save(path)
    doc.close()
    return path


@pytest.fixture(scope="session")
def floorplan_dxf() -> Path:
    path = extra_sample("floorplan.dxf")
    if path is None:
        pytest.skip(f"floorplan.dxf not available (set {SAMPLES_ENV})")
    return path


@pytest.fixture(scope="session")
def ceco_dxf() -> Path:
    path = extra_sample("Ceco.NET-Architecture-Tm-53.dxf")
    if path is None:
        pytest.skip(f"Ceco.NET-Architecture-Tm-53.dxf not available (set {SAMPLES_ENV})")
    return path


@pytest.fixture(scope="session")
def floorplan_pdf(gen, floorplan_dxf) -> Path:
    return dxf_to_pdf_in_process(floorplan_dxf, gen / "floorplan.pdf")
