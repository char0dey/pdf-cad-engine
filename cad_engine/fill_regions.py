# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Fill regions of PDF paths (engine 1.7): the area a PDF fill really paints.

A PDF fills a path by its rule — nonzero winding (the default) or even-odd. A DXF HATCH fills
by odd parity, and programs that cut (CAM, laser, plotter) want the closed outline of the
painted area. `fill_region` turns the subpaths of one path into that area as shapely polygons:

1. every subpath is a closed ring; cubic Béziers are flattened with a chord error of at most
   ``tol`` (`flatten_cubic`);
2. rings whose bounding boxes do not touch cannot interact: each group of touching rings is
   handled on its own, and a group of one simple ring is its own polygon (fast path — most
   paths of a drawing are one ring);
3. a group of overlapping rings is noded (``union_all`` of its edges) and split into faces
   (``polygonize``); a face is painted when the winding number of a point inside it is non-zero
   (nonzero) or odd (even-odd) — the definition of both rules, so self-intersections, holes
   and islands come out as the PDF paints them;
4. the region of the group is the union of its painted faces.

Pure geometry on (x, y) tuples in any unit (pdf2dxf passes DXF output units); shapely
(BSD-3-Clause) and numpy, no PyMuPDF / ezdxf.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from shapely.geometry import Polygon

Point = tuple[float, float]

MAX_CUBIC_STEPS = 1000  # a pathological control polygon must not explode the vertex count
_WINDING_CHUNK = 4_000_000  # points x edges evaluated at once (memory of the winding test)


# -- flattening --------------------------------------------------------------------------------
def cubic_steps(p0: Point, p1: Point, p2: Point, p3: Point, tol: float) -> int:
    """Equal parameter steps that keep the chords of a cubic Bézier within `tol` of the curve.

    |B''(t)| <= 6·D with D the larger second difference of the control points, and uniform
    subdivision into n steps deviates by at most max|B''| / (8 n²): n = ceil(sqrt(6·D / (8·tol))).
    """
    d = max(math.hypot(p0[0] - 2.0 * p1[0] + p2[0], p0[1] - 2.0 * p1[1] + p2[1]),
            math.hypot(p1[0] - 2.0 * p2[0] + p3[0], p1[1] - 2.0 * p2[1] + p3[1]))
    if d <= 0.0 or tol <= 0.0:
        return 1
    return min(MAX_CUBIC_STEPS, max(1, math.ceil(math.sqrt(0.75 * d / tol))))


def flatten_cubic(p0: Point, p1: Point, p2: Point, p3: Point, tol: float, out: list[Point]) -> None:
    """Append the points of the flattened curve after `p0` (the last one is exactly `p3`)."""
    n = cubic_steps(p0, p1, p2, p3, tol)
    if n == 1:
        out.append(p3)
        return
    # B(t) = ((a t + b) t + c) t + p0
    ax = -p0[0] + 3.0 * (p1[0] - p2[0]) + p3[0]
    ay = -p0[1] + 3.0 * (p1[1] - p2[1]) + p3[1]
    bx = 3.0 * (p0[0] - 2.0 * p1[0] + p2[0])
    by = 3.0 * (p0[1] - 2.0 * p1[1] + p2[1])
    cx = 3.0 * (p1[0] - p0[0])
    cy = 3.0 * (p1[1] - p0[1])
    x0, y0 = p0
    step = 1.0 / n
    for i in range(1, n):
        t = i * step
        out.append((((ax * t + bx) * t + cx) * t + x0, ((ay * t + by) * t + cy) * t + y0))
    out.append(p3)


def flatten_segments(segs: list, tol: float) -> list[Point]:
    """One subpath — ``('l', a, b)`` / ``('c', p0, p1, p2, p3)`` segments — as a list of points
    (start of the first segment, then the end of each piece; curves flattened)."""
    pts: list[Point] = [segs[0][1]]
    for seg in segs:
        if seg[0] == "l":
            pts.append(seg[2])
        else:
            flatten_cubic(pts[-1], seg[2], seg[3], seg[4], tol, pts)
    return pts


def subpath_ring(kind: str, data: list, tol: float) -> list[Point]:
    """A pdf2dxf subpath (``("p", 4 corners)`` or ``("s", segments)``) as an implicitly closed
    ring: the closing point is not repeated."""
    pts = list(data) if kind == "p" else flatten_segments(data, tol)
    if len(pts) > 1 and pts[-1][0] == pts[0][0] and pts[-1][1] == pts[0][1]:
        pts.pop()
    return pts


# -- regions -----------------------------------------------------------------------------------
def _signed_area(arr: np.ndarray) -> float:
    x, y = arr[:, 0], arr[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _groups(boxes: list[tuple[float, float, float, float]]) -> list[list[int]]:
    """Indices grouped into connected components of touching bounding boxes (sweep over x)."""
    n = len(boxes)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(n), key=lambda i: boxes[i][0])
    active: list[int] = []
    for i in order:
        x0, y0, x1, y1 = boxes[i]
        still = []
        for j in active:
            bj = boxes[j]
            if bj[2] < x0:
                continue  # ends before this one starts: no later box can touch it either
            still.append(j)
            if bj[1] <= y1 and y0 <= bj[3]:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
        still.append(i)
        active = still
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def winding_numbers(points: np.ndarray, rings: list[np.ndarray]) -> np.ndarray:
    """Winding number of every point (P x 2) with respect to all closed rings together."""
    a = np.concatenate(rings)
    b = np.concatenate([np.roll(r, -1, axis=0) for r in rings])
    x0, y0, x1, y1 = a[:, 0], a[:, 1], b[:, 0], b[:, 1]
    out = np.zeros(len(points), dtype=np.int64)
    chunk = max(1, _WINDING_CHUNK // max(1, len(a)))
    for start in range(0, len(points), chunk):
        px = points[start:start + chunk, 0:1]
        py = points[start:start + chunk, 1:2]
        left = (x1 - x0) * (py - y0) - (px - x0) * (y1 - y0)
        up = (y0 <= py) & (y1 > py) & (left > 0)
        down = (y0 > py) & (y1 <= py) & (left < 0)
        out[start:start + chunk] = up.sum(axis=1) - down.sum(axis=1)
    return out


def _painted_faces(rings: list[np.ndarray], even_odd: bool, min_area: float) -> list[Polygon]:
    import shapely

    closed = [np.vstack([r, r[:1]]) for r in rings]
    indices = np.repeat(np.arange(len(closed)), [len(c) for c in closed])
    noded = shapely.union_all(shapely.linestrings(np.concatenate(closed), indices=indices))
    faces = shapely.get_parts(shapely.polygonize(shapely.get_parts(noded)))
    if len(faces) == 0:
        return []
    inside = shapely.get_coordinates(shapely.point_on_surface(faces))
    winding = winding_numbers(inside, rings)
    painted = (winding % 2 == 1) if even_odd else (winding != 0)
    if not painted.any():
        return []
    region = shapely.union_all(faces[painted])
    if not region.is_valid:
        region = shapely.make_valid(region)
    return [p for p in shapely.get_parts(region) if p.geom_type == "Polygon" and p.area >= min_area]


def fill_region(rings: list[list[Point]], *, even_odd: bool, min_area: float) -> list[Polygon]:
    """The painted area of one PDF path as polygons (with holes), [] when it paints nothing.

    `rings` are the closed subpaths (closing point not repeated), `even_odd` the path's fill
    rule, `min_area` drops degenerate rings and slivers (same unit squared).
    """
    import shapely

    arrays: list[np.ndarray] = []
    boxes: list[tuple[float, float, float, float]] = []
    for pts in rings:
        if len(pts) < 3:
            continue
        arr = np.asarray(pts, dtype=float)
        if abs(_signed_area(arr)) < min_area:
            continue
        arrays.append(arr)
        mn, mx = arr.min(axis=0), arr.max(axis=0)
        boxes.append((float(mn[0]), float(mn[1]), float(mx[0]), float(mx[1])))
    if not arrays:
        return []
    out: list[Polygon] = []
    for group in _groups(boxes) if len(arrays) > 1 else [[0]]:
        if len(group) == 1:
            ring = shapely.linearrings(arrays[group[0]])
            if shapely.is_simple(ring):
                out.append(shapely.Polygon(ring))
                continue
        out.extend(_painted_faces([arrays[i] for i in group], even_odd, min_area))
    return out


def polygon_rings(poly: Polygon) -> list[list[list[float]]]:
    """Exterior then interior rings of a polygon as [x, y] point lists without the closing point."""
    import shapely

    rings = [poly.exterior, *poly.interiors]
    return [shapely.get_coordinates(ring)[:-1].tolist() for ring in rings]
