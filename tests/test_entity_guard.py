# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Engine 1.5.2: the safety net — an entity ezdxf raises on is left out, the rest is drawn.

The failing entity here is a real one: a SPLINE of degree 0 (order 1), which ezdxf's C extension
refuses with ``ValueError: invalid order`` both when measuring and when drawing it.
"""

from __future__ import annotations

import ezdxf
import numpy as np
import pymupdf
import pytest

from cad_engine import entity_guard, ezdxf_fixes
from cad_engine.errors import BudgetExceeded, EngineError

from conftest import dxf_to_pdf_in_process, non_white_fraction, run_engine

PLAN = [((0, 0), (300, 0)), ((300, 0), (300, 200)), ((300, 200), (0, 200)), ((0, 200), (0, 0)),
        ((0, 0), (300, 200))]
TRIANGLE = [((0, 0), (40, 0)), ((40, 0), (40, 40)), ((40, 40), (0, 0))]


def broken_spline(layout, x0: float = 10.0, y0: float = 10.0):
    sp = layout.add_spline()
    sp.dxf.unprotected_set("degree", 0)  # as a broken converter writes it: ezdxf refuses to set 0
    sp.control_points = [(x0, y0, 0), (x0 + 20, y0 + 30, 0), (x0 + 40, y0, 0)]
    sp.knots = [0.0, 0.3, 0.6, 1.0]
    return sp


def plan_doc():
    doc = ezdxf.new("R2000")
    msp = doc.modelspace()
    for a, b in PLAN:
        msp.add_line(a, b)
    return doc, msp


def build(path, kind: str) -> None:
    doc, msp = plan_doc()
    if kind == "model":
        broken_spline(msp)
    elif kind in ("block", "block_clean"):
        blk = doc.blocks.new("TRI")
        for a, b in TRIANGLE:
            blk.add_line(a, b)
        if kind == "block":
            broken_spline(blk, 0, 0)
        msp.add_blockref("TRI", (120, 60))
    elif kind == "sheet":
        sheet = doc.layouts.new("Sheet1")
        for a, b in TRIANGLE:
            sheet.add_line(a, b)
        broken_spline(sheet, 50, 50)
    elif kind == "only":
        for e in list(msp):
            msp.delete_entity(e)
        broken_spline(msp)
    elif kind != "plain":
        raise ValueError(kind)
    doc.saveas(path)


@pytest.fixture
def sample(tmp_path):
    def make(kind: str):
        path = tmp_path / f"{kind}.dxf"
        build(path, kind)
        return path
    return make


def gray(page, dpi: int = 100) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(int)


def dxf2pdf(src, tmp_path, *extra):
    out = tmp_path / (src.stem + ".pdf")
    run = run_engine("dxf2pdf", "--in", src, "--out", out, "--page", "auto", "--colors", "color", *extra)
    return run, out


# -- the drawing survives -----------------------------------------------------------------------------
def test_entity_failing_in_model_is_left_out(sample, tmp_path):
    run, out = dxf2pdf(sample("model"), tmp_path)
    assert run.returncode == 0, run.stderr
    meta = run.meta
    assert meta["code"] is None and meta["pages"] == 1
    assert meta["skipped_entities"] == 1
    stages = {(e["stage"], e["type"]) for e in meta["entity_errors"]}
    assert stages == {("extents", "SPLINE"), ("render", "SPLINE")}
    error = next(e for e in meta["entity_errors"] if e["stage"] == "render")
    assert error["error"] == "ValueError: invalid order" and error["count"] == 1
    assert error["where"].startswith("ezdxf/acc/bspline.pyx:72 < ")  # the C extension's order check
    assert "ezdxf/math/bspline.py:" in error["where"]
    assert "cad-engine: SPLINE skipped while rendering (ValueError: invalid order at " in run.stderr
    plain, plain_out = dxf2pdf(sample("plain"), tmp_path)
    ours, theirs = pymupdf.open(out)[0], pymupdf.open(plain_out)[0]
    assert ours.rect == theirs.rect
    assert np.array_equal(gray(ours), gray(theirs))  # the plan, exactly as without the broken spline


def test_entity_in_a_block_is_left_out_alone(sample, tmp_path):
    run, out = dxf2pdf(sample("block"), tmp_path)
    assert run.returncode == 0, run.stderr
    assert run.meta["skipped_entities"] == 1
    render = [e for e in run.meta["entity_errors"] if e["stage"] == "render"]
    assert [e["type"] for e in render] == ["SPLINE"]  # not the whole INSERT
    clean, clean_out = dxf2pdf(sample("block_clean"), tmp_path)
    ours, theirs = pymupdf.open(out)[0], pymupdf.open(clean_out)[0]
    assert ours.rect == theirs.rect
    assert np.array_equal(gray(ours), gray(theirs))  # the block's triangle is drawn


def test_entity_on_a_sheet(sample, tmp_path):
    run, out = dxf2pdf(sample("sheet"), tmp_path, "--layouts")
    assert run.returncode == 0, run.stderr
    assert run.meta["pages"] == 2 and run.meta["layouts"] == ["Sheet1"]
    assert run.meta["skipped_entities"] == 1
    assert "layout_skipped:Sheet1" not in run.meta["warnings"]
    assert non_white_fraction(pymupdf.open(out)[1].get_pixmap(dpi=60).tobytes("png")) > 0


def test_preview_and_probe(sample, tmp_path):
    src = sample("model")
    png = tmp_path / "preview.png"
    preview = run_engine("preview-dxf", "--in", src, "--out", png)
    assert preview.returncode == 0, preview.stderr
    assert preview.meta["skipped_entities"] == 1 and png.stat().st_size > 0
    probe = run_engine("probe-dxf", "--in", src)
    assert probe.returncode == 0, probe.stderr
    assert probe.meta["empty"] is False and probe.meta["code"] is None
    assert probe.meta["skipped_entities"] == 0  # the analysis measures, it does not draw
    assert [e["stage"] for e in probe.meta["entity_errors"]] == ["extents"]


def test_ordinary_drawing_reports_nothing(sample_plan_dxf, tmp_path):
    run, _ = dxf2pdf(sample_plan_dxf, tmp_path, "--layouts")
    assert run.returncode == 0, run.stderr
    assert run.meta["skipped_entities"] == 0 and run.meta["entity_errors"] is None
    assert "skipped while" not in run.stderr


def test_pdf_commands_have_no_safety_net_fields(sample_plan_pdf):
    run = run_engine("probe-pdf", "--in", sample_plan_pdf)
    assert run.returncode == 0
    assert run.meta["skipped_entities"] is None and run.meta["entity_errors"] is None


# -- failures that stay failures ---------------------------------------------------------------------
def test_nothing_left_is_an_engine_failure_not_an_empty_drawing(sample, tmp_path):
    run, out = dxf2pdf(sample("only"), tmp_path, "--layouts")
    assert run.returncode == 5
    assert run.meta["code"] == "engine_error"
    assert run.meta["message"].startswith("nothing left to draw: ezdxf failed on 1 entity(ies) (SPLINE: ")
    assert run.meta["entity_errors"][0]["error"] == "ValueError: invalid order"
    assert not out.exists()


@pytest.mark.parametrize("exc", [BudgetExceeded("budget"), MemoryError(), EngineError("ours"),
                                 RuntimeError("cannot allocate memory")])
def test_budget_memory_and_engine_errors_pass_through(sample, tmp_path, monkeypatch, exc):
    from ezdxf.addons.drawing import Frontend

    stock = Frontend.draw_entity

    def draw_entity(self, entity, properties):
        if entity.dxftype() == "LINE":
            raise exc
        stock(self, entity, properties)

    monkeypatch.setattr(Frontend, "draw_entity", draw_entity)
    with pytest.raises(type(exc)):
        dxf_to_pdf_in_process(sample("plain"), tmp_path / "out.pdf")
    assert entity_guard.LOG.count("render") == 0


def test_extents_failure_only(sample, tmp_path, monkeypatch):
    """ezdxf fails measuring an entity it draws fine: drawn, logged at «extents», nothing left out."""
    from ezdxf import bbox

    stock = bbox.extents

    def extents(entities, **kw):
        entities = list(entities)
        if any(e.dxftype() == "INSERT" for e in entities):
            raise ZeroDivisionError("float division")
        return stock(entities, **kw)

    monkeypatch.setattr(bbox, "extents", extents)
    out = dxf_to_pdf_in_process(sample("block_clean"), tmp_path / "out.pdf")
    assert entity_guard.LOG.count("render") == 0
    (logged,) = entity_guard.LOG.to_meta()
    assert (logged["stage"], logged["type"], logged["count"]) == ("extents", "INSERT", 1)
    assert logged["error"] == "ZeroDivisionError: float division"
    monkeypatch.setattr(bbox, "extents", stock)
    reference = dxf_to_pdf_in_process(sample("block_clean"), tmp_path / "ref.pdf")
    ours, theirs = pymupdf.open(out)[0], pymupdf.open(reference)[0]
    assert ours.rect == theirs.rect  # the page follows what was drawn
    assert np.array_equal(gray(ours), gray(theirs))


def test_without_the_spline_fix_the_net_keeps_the_drawing(tmp_path, monkeypatch):
    """Both layers: with the stock C classes back, degree-11 splines are left out, not fatal."""
    from ezdxf.math import bspline
    from test_spline_high_degree import build_incident

    src = tmp_path / "incident.dxf"
    build_incident(src, splines=6)
    assert ezdxf_fixes.apply()
    monkeypatch.setattr(bspline, "Basis", ezdxf_fixes.stock_basis)
    monkeypatch.setattr(bspline, "Evaluator", ezdxf_fixes.stock_evaluator)
    dxf_to_pdf_in_process(src, tmp_path / "out.pdf")
    assert entity_guard.LOG.count("render") == 6
    assert {e["error"] for e in entity_guard.LOG.to_meta()} == {"ValueError: invalid order"}


def test_stacks_restored_after_a_block_fails_to_explode(tmp_path, monkeypatch):
    """A block reference whose explosion raises half-way (clipped, so both stacks are open): it
    keeps what it drew, the next entities draw normally, and no stack stays open."""
    from ezdxf import xclip
    from ezdxf.addons.drawing import RenderContext
    from ezdxf.entities import Insert

    from cad_engine.hatch_limit import EngineFrontend
    from cad_engine.pdf_backend import FastPdfBackend

    doc, msp = plan_doc()
    blk = doc.blocks.new("TRI")
    for a, b in TRIANGLE:
        blk.add_line(a, b)
    ref = msp.add_blockref("TRI", (120, 60))
    xclip.XClip(ref).set_block_clipping_path([(-5, -5), (45, -5), (45, 45), (-5, 45)])
    msp.add_line((10, 190), (290, 190))  # after the reference

    stock = Insert.virtual_entities

    def virtual_entities(self, *args, **kwargs):
        for i, entity in enumerate(stock(self, *args, **kwargs)):
            if i == 1:
                raise RuntimeError("explode failed")
            yield entity

    monkeypatch.setattr(Insert, "virtual_entities", virtual_entities)
    entity_guard.reset()
    backend = FastPdfBackend()
    frontend = EngineFrontend(RenderContext(doc), backend)
    frontend.draw_layout(msp, finalize=True)
    assert frontend.ctx._saved_states == []
    assert frontend.pipeline.clipping_portal.stack_depth == 0
    assert entity_guard.LOG.to_meta()[0]["type"] == "INSERT"
    assert len(backend.records) == len(PLAN) + 1 + 1  # plan, the line drawn before the failure, the last line


# -- the log --------------------------------------------------------------------------------------------
def test_log_ranks_caps_and_describes():
    log = entity_guard.SkipLog(quiet=True)
    log.reset()

    class Fake:
        def __init__(self, t):
            self.t = t

        def dxftype(self):
            return self.t

    def raised(exc):
        try:
            raise exc
        except Exception as caught:  # noqa: BLE001 - a traceback to describe
            return caught

    for i in range(12):
        log.add("render", Fake(f"T{i:02d}"), raised(ValueError(f"bad {i}")))
    for _ in range(3):
        log.add("extents", Fake("SPLINE"), raised(ValueError("multi\nline   message " + "x" * 300)))
    meta = log.to_meta()
    assert len(meta) == entity_guard.MAX_META_ERRORS
    assert meta[0]["count"] == 3 and meta[0]["stage"] == "extents"
    assert "\n" not in meta[0]["error"] and len(meta[0]["error"]) == entity_guard.MAX_ERROR_CHARS
    assert meta[0]["error"].startswith("ValueError: multi line message x")
    assert meta[0]["where"].startswith("tests/test_entity_guard.py:")
    assert log.count() == 15 and log.count("render") == 12
    assert log.first_error().startswith("SPLINE: ValueError: multi line message")
    log.reset()
    assert log.to_meta() is None and log.count() == 0
