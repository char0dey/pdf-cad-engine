# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Engine 1.2: the content scan (xrefs, underlays, images, AutoCAD vertical-product classes),
the refusal codes for drawings with nothing to draw, and ``probe-dxf --light``."""

from __future__ import annotations

import pytest

from cad_engine import scan as scan_module
from cad_engine.budget import Budget
from cad_engine.scan import base_name, empty_reason, safe_scan, scan_dxf

import make_sample
from conftest import FIXTURES, run_engine

NO_FINDINGS = {"xrefs": [], "underlays": [], "images": [], "aec": []}
WINDOWS_PLAN = r"C:\Projects\Client\plan.dwg"


@pytest.fixture(scope="session")
def refs(gen):
    """Factory: `make_sample.build_refs_dxf` into the session dir, cached by name."""
    built: dict = {}

    def make(name: str, **kwargs):
        if name not in built:
            built[name] = make_sample.build_refs_dxf(gen / f"refs_{name}.dxf", **kwargs)
        return built[name]

    return make


@pytest.fixture(scope="session")
def underlay_only_dxf() -> object:
    return FIXTURES / "underlay-only.dxf"


def probe(path, *extra):
    run = run_engine("probe-dxf", "--in", path, *extra)
    assert run.returncode == 0, run.stderr
    return run.meta


# -- detection ---------------------------------------------------------------------------
def test_base_name():
    assert base_name(WINDOWS_PLAN) == "plan.dwg"
    assert base_name("..\\pdf-definition.pdf") == "pdf-definition.pdf"
    assert base_name(".\\image.JPG") == "image.JPG"
    assert base_name("../site/site.dwg") == "site.dwg"
    assert base_name("plan.dwg") == "plan.dwg"


def test_inserted_xref_windows_path(refs):
    meta = probe(refs("xref", xrefs=[("PLAN", WINDOWS_PLAN)]))
    assert meta["scanned"] is True
    assert meta["missing"] == {**NO_FINDINGS, "xrefs": ["plan.dwg"]}
    assert meta["empty"] is False and meta["empty_reason"] is None
    assert (meta["ms_entities"], meta["ms_drawable"]) == (2, 1)


def test_overlay_counts_as_xref(refs):
    meta = probe(refs("overlay", overlays=[("SITE", "../site/site.dwg")]))
    assert meta["missing"]["xrefs"] == ["site.dwg"]


def test_bound_and_unused_xrefs_do_not_count(refs):
    meta = probe(refs("bound_unused", bound=["PLAN$0$WALLS"], unused_xrefs=[("OLD", "old.dwg")]))
    assert meta["missing"] == NO_FINDINGS


def test_xref_nested_in_a_block_counts(refs):
    path = refs("nested", nested_xrefs=[("SITE", r"X:\survey\site.dwg")], lines=False)
    assert scan_dxf(str(path)).xrefs == ["site.dwg"]


def test_xref_names_are_unique(refs):
    path = refs("dupes", xrefs=[("A", r"C:\x\plan.dwg"), ("B", r"D:\y\PLAN.dwg"), ("C", "site.dwg")])
    assert scan_dxf(str(path)).xrefs == ["plan.dwg", "site.dwg"]


def test_underlay_and_two_images_of_one_definition(refs):
    path = refs("images", underlays=[r"..\pdf-definition.pdf"], images=[r".\image.JPG"], image_inserts=2)
    result = scan_dxf(str(path))
    assert result.underlays == ["pdf-definition.pdf"]
    assert result.images == ["image.JPG"]
    assert (result.ms_entities, result.ms_drawable) == (4, 1)


def test_aec_entity_classes(refs):
    meta = probe(refs("aec", aec=("AEC_WALL", "AEC_DOOR")))
    assert meta["missing"]["aec"] == ["architecture"]
    assert meta["empty"] is False  # the line is still drawn: a soft notice, not a refusal


def test_aec_families_and_object_classes(gen):
    path = make_sample.build_refs_dxf(gen / "families.dxf")
    make_sample.add_aec_classes(path, ("AECC_ALIGNMENT", "AECB_DUCT", "AECS_MEMBER"),
                                objects=("AEC_DISP_PROPS_WALL",))
    assert scan_dxf(str(path)).aec == ["civil", "mep", "architecture"]
    only_objects = make_sample.build_refs_dxf(gen / "aec_objects_only.dxf")
    make_sample.add_aec_classes(only_objects, (), objects=("AEC_DISP_PROPS_WALL",))
    assert scan_dxf(str(only_objects)).aec == []


def test_binary_dxf(refs):
    meta = probe(refs("binary", xrefs=[("PLAN", WINDOWS_PLAN)], fmt="bin"))
    assert meta["missing"]["xrefs"] == ["plan.dwg"]


def test_plain_drawing_has_no_findings(sample_plan_dxf):
    meta = probe(sample_plan_dxf)
    assert meta["scanned"] is True
    assert meta["missing"] == NO_FINDINGS
    assert meta["ms_entities"] == meta["entities"] == 19


# -- refusal codes -----------------------------------------------------------------------
def test_empty_reason_priority():
    assert empty_reason(None) == "empty_drawing"
    both = scan_module.ScanResult(xrefs=["plan.dwg"], aec=["architecture"])
    assert empty_reason(both) == "aec_objects"
    assert empty_reason(scan_module.ScanResult(images=["a.jpg"])) == "external_refs_only"
    assert empty_reason(scan_module.ScanResult()) == "empty_drawing"


def test_aec_and_xref_with_nothing_drawn(refs, tmp_path):
    path = refs("aec_xref_empty", aec=("AEC_WALL",), xrefs=[("PLAN", "plan.dwg")], lines=False)
    meta = probe(path)
    assert (meta["empty"], meta["empty_reason"]) == (True, "aec_objects")
    run = run_engine("dxf2pdf", "--in", path, "--out", tmp_path / "x.pdf")
    assert (run.returncode, run.meta["code"]) == (2, "aec_objects")
    assert run.meta["missing"]["xrefs"] == ["plan.dwg"]  # the findings travel with the refusal
    assert not (tmp_path / "x.pdf").exists()


@pytest.mark.parametrize("light", [False, True])
def test_underlays_only(underlay_only_dxf, light):
    meta = probe(underlay_only_dxf, *(["--light"] if light else []))
    assert (meta["empty"], meta["empty_reason"]) == (True, "external_refs_only")
    assert meta["missing"]["underlays"] == ["site-plan.pdf", "survey.pdf"]


def test_underlays_only_dxf2pdf(underlay_only_dxf, tmp_path):
    run = run_engine("dxf2pdf", "--in", underlay_only_dxf, "--out", tmp_path / "x.pdf", "--layouts")
    assert (run.returncode, run.meta["code"]) == (2, "external_refs_only")
    assert run.meta["scanned"] is True


def test_empty_drawing_reason(empty_dxf, tmp_path):
    meta = probe(empty_dxf)
    assert (meta["empty"], meta["empty_reason"]) == (True, "empty_drawing")
    run = run_engine("dxf2pdf", "--in", empty_dxf, "--out", tmp_path / "x.pdf")
    assert (run.returncode, run.meta["code"]) == (2, "empty_drawing")
    assert run.meta["missing"] == NO_FINDINGS


def test_xref_inside_a_block_is_refused_at_the_run(refs, tmp_path):
    # the light scan sees one drawable INSERT; only the render finds nothing — same code at the run
    path = refs("nested", nested_xrefs=[("SITE", r"X:\survey\site.dwg")], lines=False)
    meta = probe(path, "--light")
    assert meta["empty"] is False
    run = run_engine("dxf2pdf", "--in", path, "--out", tmp_path / "x.pdf")
    assert (run.returncode, run.meta["code"]) == (2, "external_refs_only")


# -- real converter output (DXF made by ACadSharp 3.4.9 in the browser, tests/fixtures/README) ---
def test_acadsharp_aec_objects(tmp_path):
    meta = probe(FIXTURES / "AecObjects.dxf")
    assert (meta["empty"], meta["empty_reason"]) == (True, "aec_objects")
    assert meta["missing"]["aec"] == ["architecture"]
    run = run_engine("dxf2pdf", "--in", FIXTURES / "AecObjects.dxf", "--out", tmp_path / "x.pdf", "--layouts")
    assert (run.returncode, run.meta["code"]) == (2, "aec_objects")


def test_acadsharp_host_with_xref(tmp_path):
    run = run_engine("dxf2pdf", "--in", FIXTURES / "host_xref.dxf", "--out", tmp_path / "x.pdf", "--layouts")
    assert run.returncode == 0, run.stderr
    assert run.meta["missing"] == {**NO_FINDINGS, "xrefs": ["plan.dwg"]}
    assert run.meta["scanned"] is True


# -- light probe ---------------------------------------------------------------------------
LIGHT_CASES = {
    "xref": dict(xrefs=[("PLAN", WINDOWS_PLAN)]),
    "overlay": dict(overlays=[("SITE", "../site/site.dwg")]),
    "bound_unused": dict(bound=["PLAN$0$WALLS"], unused_xrefs=[("OLD", "old.dwg")]),
    "images": dict(underlays=[r"..\pdf-definition.pdf"], images=[r".\image.JPG"], image_inserts=2),
    "aec": dict(aec=("AEC_WALL", "AEC_DOOR")),
    "binary": dict(xrefs=[("PLAN", WINDOWS_PLAN)], fmt="bin"),
}


@pytest.mark.parametrize("name", [*LIGHT_CASES, "AecObjects.dxf", "host_xref.dxf", "underlay-only.dxf"])
def test_light_matches_full_probe(refs, name):
    path = refs(name, **LIGHT_CASES[name]) if name in LIGHT_CASES else FIXTURES / name
    full, light = probe(path), probe(path, "--light")
    for key in ("missing", "scanned", "ms_entities", "ms_drawable", "empty", "empty_reason"):
        assert light[key] == full[key], key
    assert light["entities"] == full["entities"]
    assert light["layers"] is None  # nothing is loaded


def test_light_probe_rejects_dwg(fake_dwg):
    run = run_engine("probe-dxf", "--light", "--in", fake_dwg)
    assert (run.returncode, run.meta["code"]) == (2, "dwg_not_dxf")


def test_light_only_for_probe_dxf(sample_plan_dxf, tmp_path):
    run = run_engine("dxf2pdf", "--light", "--in", sample_plan_dxf, "--out", tmp_path / "x.pdf")
    assert run.returncode == 5
    assert "--light only supported by probe-dxf" in run.meta["message"]


def test_cut_off_file_is_not_called_empty_by_light(truncated_dxf):
    meta = probe(truncated_dxf(0.7), "--light")  # cut before ENTITIES: nothing seen, nothing decided
    assert meta["scanned"] is True
    assert meta["empty"] is None and meta["empty_reason"] is None


# -- a failed scan never stops the conversion ------------------------------------------------
def test_safe_scan_on_garbage(txt_as_dxf):
    assert safe_scan(str(txt_as_dxf)) is None


def test_failed_scan_keeps_the_probe_and_the_run(monkeypatch, sample_plan_dxf, tmp_path):
    from cad_engine.dxf2pdf import convert
    from cad_engine.probe import probe_dxf

    def boom(*args, **kwargs):
        raise ValueError("odd structure")

    monkeypatch.setattr(scan_module, "scan_dxf", boom)
    meta: dict = {}
    probe_dxf(str(sample_plan_dxf), budget=Budget(None), meta=meta)
    assert meta["scanned"] is False and meta["missing"] == NO_FINDINGS
    assert (meta["empty"], meta["entities"]) == (False, 19)
    meta = {}
    convert(str(sample_plan_dxf), str(tmp_path / "x.pdf"), budget=Budget(None), meta=meta)
    assert meta["scanned"] is False and (tmp_path / "x.pdf").stat().st_size > 0
