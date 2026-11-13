# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Engine 1.5.1: a leader (MULTILEADER) whose landing has length 0, inside an inserted block.

PROD 2026-11-13: dwg-to-pdf of a Civil 3D drawing failed twice (dxf2pdf exit 5, ``ZeroDivisionError``
in ezdxf's ``LeaderData.transform``, mleader.py:1188): ezdxf normalizes the transformed landing,
a null vector when its length is 0, and transforms the entity whenever a block is exploded.
"""

from __future__ import annotations

import math

import numpy as np
import pymupdf
import pytest

from cad_engine import ezdxf_fixes

from conftest import non_white_fraction, run_engine


def render(dxf, tmp_path, *extra, name=None):
    out = tmp_path / ((name or dxf.stem) + ".pdf")
    run = run_engine("dxf2pdf", "--in", dxf, "--out", out, *extra)
    assert run.returncode == 0, run.stderr
    assert run.meta["code"] is None
    assert "ZeroDivisionError" not in run.stderr
    pdf = pymupdf.open(out)
    assert pdf.page_count == run.meta["pages"]
    return run.meta, pdf


def gray(page, dpi: int = 60) -> np.ndarray:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).astype(int)


def dark(page) -> int:
    return int((gray(page) < 128).sum())


# -- the PROD case and its edges --------------------------------------------------------------------
def test_leader_without_landing_in_block_renders_like_in_model(mleader_dxf, tmp_path):
    """The leader in an inserted block draws exactly like the same leader placed in model space."""
    _, in_block = render(mleader_dxf("in_block"), tmp_path)
    _, in_model = render(mleader_dxf("in_model"), tmp_path)
    _, plain = render(mleader_dxf("plain"), tmp_path)
    assert in_block[0].rect == in_model[0].rect
    assert np.abs(gray(in_block[0]) - gray(in_model[0])).max() <= 32  # same picture, anti-aliasing aside
    assert dark(in_block[0]) > dark(plain[0]) + 200  # the leader line and its text are on the page


def test_rotated_and_scaled_insert(mleader_dxf, tmp_path):
    _, rotated = render(mleader_dxf("rotated"), tmp_path)
    _, in_model = render(mleader_dxf("in_model"), tmp_path)
    assert dark(rotated[0]) > dark(in_model[0])  # twice the size


def test_nested_block_and_sheet(mleader_dxf, tmp_path):
    meta, pdf = render(mleader_dxf("nested_paper"), tmp_path, "--layouts")
    assert meta["layouts"] == ["Sheet"] and pdf.page_count == 2
    _, in_model = render(mleader_dxf("in_model"), tmp_path)
    assert dark(pdf[0]) == dark(in_model[0])  # model page: TAG inside OUTER, nothing else moved
    assert dark(pdf[1]) > 200  # the sheet: its frame and the leader


def test_null_landing_direction(mleader_dxf, tmp_path):
    _, null_vector = render(mleader_dxf("null_vector"), tmp_path)
    _, in_block = render(mleader_dxf("in_block"), tmp_path)
    assert np.abs(gray(null_vector[0]) - gray(in_block[0])).max() <= 32


def test_non_uniform_insert_does_not_fail(mleader_dxf, tmp_path):
    """ezdxf leaves a MULTILEADER out of a non-uniformly scaled block (as 1.5.0 did); the rest draws."""
    _, pdf = render(mleader_dxf("nonuniform"), tmp_path)
    assert non_white_fraction(pdf[0].get_pixmap(dpi=50).tobytes("png")) > 0.001


def test_leader_with_landing_draws(mleader_dxf, tmp_path):
    _, pdf = render(mleader_dxf("dogleg"), tmp_path)
    assert dark(pdf[0]) > 200


def test_probe_and_preview(mleader_dxf, tmp_path):
    src = mleader_dxf("in_block")
    probe = run_engine("probe-dxf", "--in", src)
    assert probe.returncode == 0, probe.stderr
    assert probe.meta["code"] is None and probe.meta["entities"] == 9
    png = tmp_path / "preview.png"
    preview = run_engine("preview-dxf", "--in", src, "--out", png)
    assert preview.returncode == 0, preview.stderr
    assert non_white_fraction(png) > 0.001


# -- the replacement against ezdxf's own transform ---------------------------------------------------
def _leader(dogleg_vector, dogleg_length):
    from ezdxf.entities.mleader import LeaderData, LeaderLine
    from ezdxf.math import Vec3

    data = LeaderData()
    data.last_leader_point = Vec3(10, 5, 0)
    data.dogleg_vector = Vec3(dogleg_vector)
    data.dogleg_length = dogleg_length
    data.breaks = [Vec3(1, 2, 0), Vec3(3, 4, 0)]
    line = LeaderLine()
    line.vertices = [Vec3(-20, -10, 0), Vec3(0, 0, 0)]
    data.lines = [line]
    return data


def _matrices():
    from ezdxf.math import Matrix44

    return [
        Matrix44(),
        Matrix44.translate(200, 150, 0),
        Matrix44.chain(Matrix44.scale(2, 2, 2), Matrix44.z_rotate(math.radians(90)), Matrix44.translate(5, 6, 0)),
        Matrix44.chain(Matrix44.scale(1.5, 1.5, 1.5), Matrix44.z_rotate(math.radians(30))),
        Matrix44.chain(Matrix44.scale(-1, 1, 1), Matrix44.z_rotate(math.radians(-45))),  # mirrored
    ]


def _state(data):
    return (data.last_leader_point, data.dogleg_vector, data.dogleg_length, data.breaks,
            [list(line.vertices) for line in data.lines])


@pytest.mark.parametrize("vector", [(1, 0, 0), (0.6, 0.8, 0), (0, 0, 0)])
@pytest.mark.parametrize("length", [25.0, 2.5, -4.0])
def test_same_numbers_as_ezdxf_where_ezdxf_works(vector, length):
    """A landing of non-zero length: the replacement gives ezdxf's exact numbers (unchanged since 1.5.0)."""
    from ezdxf.math import WCSTransform

    assert ezdxf_fixes.apply()
    for m in _matrices():
        ours, stock = _leader(vector, length), _leader(vector, length)
        ezdxf_fixes._leader_transform(ours, WCSTransform(m))
        ezdxf_fixes.stock_leader_transform(stock, WCSTransform(m))
        assert _state(ours) == _state(stock)


@pytest.mark.parametrize("vector", [(1, 0, 0), (0, 1, 0), (0, 0, 0)])
def test_landing_of_length_zero(vector):
    from ezdxf.math import Matrix44, Vec3, WCSTransform

    assert ezdxf_fixes.apply()
    m = Matrix44.chain(Matrix44.scale(2, 2, 2), Matrix44.z_rotate(math.radians(90)), Matrix44.translate(5, 6, 0))
    data = _leader(vector, 0.0)
    ezdxf_fixes._leader_transform(data, WCSTransform(m))
    expected = m.transform_direction(Vec3(vector) if any(vector) else Vec3(1, 0, 0)).normalize()
    assert data.dogleg_length == 0.0
    assert data.dogleg_vector.isclose(expected)
    assert data.last_leader_point.isclose(m.transform(Vec3(10, 5, 0)))
    assert data.lines[0].vertices[0].isclose(m.transform(Vec3(-20, -10, 0)))


def test_collapsing_transformation():
    """A block inserted with scale 0 collapses the landing too: no exception, the direction stays."""
    from ezdxf.math import Matrix44, Vec3, WCSTransform

    assert ezdxf_fixes.apply()
    data = _leader((0, 1, 0), 25.0)
    ezdxf_fixes._leader_transform(data, WCSTransform(Matrix44.scale(0, 0, 0)))
    assert data.dogleg_length == 0.0 and data.dogleg_vector.isclose(Vec3(0, 1, 0))


def test_apply_is_idempotent_and_keeps_the_stock_method():
    from ezdxf.entities import mleader

    assert ezdxf_fixes.apply() and ezdxf_fixes.apply()
    assert mleader.LeaderData.transform is ezdxf_fixes._leader_transform
    assert ezdxf_fixes.stock_leader_transform is not ezdxf_fixes._leader_transform


def test_stock_ezdxf_still_needs_the_fix():
    """Watchdog: when an ezdxf upgrade fixes LeaderData.transform, this fails — then drop the fix
    (``ezdxf_fixes``) and this test. ``apply()`` also refuses any version but the pinned one."""
    from ezdxf.math import Matrix44, WCSTransform

    assert ezdxf_fixes.apply()
    with pytest.raises(ZeroDivisionError):
        ezdxf_fixes.stock_leader_transform(_leader((1, 0, 0), 0.0), WCSTransform(Matrix44.translate(1, 0, 0)))
