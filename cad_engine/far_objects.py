# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Drawn parts far away from the rest of the drawing (engine 1.5).

One object far from everything else — a stray point at the origin of a drawing in survey
coordinates, or a coordinate a broken DWG → DXF conversion wrote as 1e+39 — stretches the
extents: fitted to the page, the real drawing shrinks to a dot and the PDF looks blank. Once
the fit scale drops below 1e-9, ezdxf's page placement stops scaling at all
(``ezdxf.addons.drawing.layout.placement_matrix``: ``abs(sx) < 1e-9 → 1.0``), and a
coordinate above 3.4e38 then reaches PyMuPDF, which refuses it as a C float
(``TypeError: Wrong number or type of arguments for overloaded function 'new_FzPoint'``,
exit code 5).

`trim_far_parts` works on what the ezdxf frontend recorded — blocks already exploded,
invisible layers already skipped, one record per drawn primitive. The *core* of the drawing
is the box between the 1st and the 99th percentile of the records' bounding boxes; its size
is the longer side. A record that reaches further than `FAR_RATIO` × that size beyond the
core, or has a coordinate that is not finite or larger than `MAX_COORD`, is left out whole
(not clipped: clipping a curve that is 1e12 units long to drawing precision costs millions
of segments). When nothing is that far, the recording is not touched, so ordinary drawings
render exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: A part further than this many drawing sizes from the drawing is left out. Kept, it would
#: shrink the drawing to under 1/1000 of the page — about 0.3 mm on A3.
FAR_RATIO = 1000.0
#: Coordinates beyond this (drawing units) are broken data in any unit: 1e12 mm is a million km.
MAX_COORD = 1e12
#: The core spans the records between these percentiles (by their bounding boxes).
CORE_QUANTILE = 0.01
#: With fewer records the percentiles mean nothing: the core is every sane record.
MIN_RECORDS_FOR_CORE = 10


@dataclass(frozen=True)
class FarTrim:
    """What `trim_far_parts` left out and the extents of what stays (drawing units)."""

    dropped: int
    #: (min_x, min_y, max_x, max_y) of the records that stay; None when none stays.
    box: tuple[float, float, float, float] | None

    @property
    def size(self) -> tuple[float, float] | None:
        if self.box is None:
            return None
        x0, y0, x1, y1 = self.box
        return x1 - x0, y1 - y0


def _record_boxes(records) -> tuple[np.ndarray, np.ndarray]:
    """``(boxes, measured)``: one (x0, y0, x1, y1) row per record, and which records have a
    bounding box at all (a record without one draws nothing measurable and always stays)."""
    boxes = np.zeros((len(records), 4), dtype=np.float64)
    measured = np.zeros(len(records), dtype=bool)
    for i, record in enumerate(records):
        bb = record.bbox()
        if bb.has_data:
            boxes[i] = (bb.extmin.x, bb.extmin.y, bb.extmax.x, bb.extmax.y)
            measured[i] = True
    return boxes, measured


def _core(boxes: np.ndarray) -> tuple[float, float, float, float]:
    """The core box of sane records' boxes (see module doc)."""
    if len(boxes) < MIN_RECORDS_FOR_CORE:
        return (float(boxes[:, 0].min()), float(boxes[:, 1].min()),
                float(boxes[:, 2].max()), float(boxes[:, 3].max()))
    lo_x = float(np.quantile(boxes[:, 0], CORE_QUANTILE, method="higher"))
    lo_y = float(np.quantile(boxes[:, 1], CORE_QUANTILE, method="higher"))
    hi_x = float(np.quantile(boxes[:, 2], 1.0 - CORE_QUANTILE, method="lower"))
    hi_y = float(np.quantile(boxes[:, 3], 1.0 - CORE_QUANTILE, method="lower"))
    return min(lo_x, hi_x), min(lo_y, hi_y), max(lo_x, hi_x), max(lo_y, hi_y)


def far_mask(boxes: np.ndarray) -> np.ndarray:
    """True for each box that is far from the drawing (``boxes``: measured records only)."""
    finite = np.isfinite(boxes).all(axis=1)
    sane = finite.copy()
    sane[finite] = (np.abs(boxes[finite]) <= MAX_COORD).all(axis=1)
    if not sane.any():
        return np.ones(len(boxes), dtype=bool)
    x0, y0, x1, y1 = _core(boxes[sane])
    size = max(x1 - x0, y1 - y0)
    if size <= 0.0:
        size = 1.0  # everything on one point: «far» is then 1000 drawing units away
    reach = FAR_RATIO * size
    far = ~sane
    s = boxes[sane]
    far[sane] = ((s[:, 0] < x0 - reach) | (s[:, 1] < y0 - reach)
                 | (s[:, 2] > x1 + reach) | (s[:, 3] > y1 + reach))
    return far


def trim_far_parts(recorder) -> FarTrim:
    """Leave out the far records of an ezdxf ``Recorder`` (in place) and return what was done.
    ``box`` is the extents of the records that stay, so the caller can choose the page by them
    and give them to the replay instead of measuring every record again."""
    records = recorder.records
    if not records:
        return FarTrim(0, None)
    boxes, measured = _record_boxes(records)
    if not measured.any():
        return FarTrim(0, None)
    far = np.zeros(len(records), dtype=bool)
    far[measured] = far_mask(boxes[measured])
    dropped = int(far.sum())
    if dropped:
        recorder.records = [r for r, f in zip(records, far) if not f]
    kept = measured & ~far
    if not kept.any():
        return FarTrim(dropped, None)
    k = boxes[kept]
    box = (float(k[:, 0].min()), float(k[:, 1].min()), float(k[:, 2].max()), float(k[:, 3].max()))
    return FarTrim(dropped, box)
