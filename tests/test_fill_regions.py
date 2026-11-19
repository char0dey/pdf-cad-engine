"""Fill regions (engine 1.7, `cad_engine.fill_regions`): the nonzero / even-odd area of one
PDF path, the fast path, degenerate rings and the 0.01 mm flattening tolerance."""

from __future__ import annotations

import math
import time

import pytest

from cad_engine.fill_regions import (
    cubic_steps,
    fill_region,
    flatten_cubic,
    flatten_segments,
    polygon_rings,
    subpath_ring,
    winding_numbers,
)

MIN_AREA = 1e-9


def square(x0, y0, size, ccw=True):
    pts = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]
    return pts if ccw else pts[::-1]


def area(polys) -> float:
    return sum(p.area for p in polys)


def holes(polys) -> int:
    return sum(len(p.interiors) for p in polys)


# -- the PDF fill rules ------------------------------------------------------------------------
def test_overlapping_letters_nonzero_are_one_region():
    # two "letters" drawn in the same direction that overlap at their sides (the 19.11 logo)
    a = square(0, 0, 10)
    b = square(8, 0, 10)
    polys = fill_region([a, b], even_odd=False, min_area=MIN_AREA)
    assert len(polys) == 1 and holes(polys) == 0
    assert area(polys) == pytest.approx(180.0)


def test_overlapping_letters_even_odd_leave_the_overlap_empty():
    polys = fill_region([square(0, 0, 10), square(8, 0, 10)], even_odd=True, min_area=MIN_AREA)
    assert area(polys) == pytest.approx(160.0)


def test_square_in_square_even_odd_is_a_hole():
    polys = fill_region([square(0, 0, 10), square(3, 3, 4)], even_odd=True, min_area=MIN_AREA)
    assert len(polys) == 1 and holes(polys) == 1
    assert area(polys) == pytest.approx(84.0)


def test_letter_o_with_opposite_directions_is_a_hole_under_nonzero():
    polys = fill_region([square(0, 0, 10), square(3, 3, 4, ccw=False)], even_odd=False, min_area=MIN_AREA)
    assert len(polys) == 1 and holes(polys) == 1
    assert area(polys) == pytest.approx(84.0)


def test_island_in_the_same_direction_is_filled_under_nonzero():
    polys = fill_region([square(0, 0, 10), square(3, 3, 4)], even_odd=False, min_area=MIN_AREA)
    assert len(polys) == 1 and holes(polys) == 0
    assert area(polys) == pytest.approx(100.0)


def star(r=10.0):
    """Five-pointed star drawn as one self-intersecting ring (every second vertex)."""
    pts = [(r * math.cos(math.pi / 2 + i * 2 * math.pi / 5), r * math.sin(math.pi / 2 + i * 2 * math.pi / 5))
           for i in range(5)]
    return [pts[(2 * i) % 5] for i in range(5)]


def test_self_intersecting_star():
    from shapely.geometry import Point as ShapelyPoint

    full = fill_region([star()], even_odd=False, min_area=MIN_AREA)
    rays = fill_region([star()], even_odd=True, min_area=MIN_AREA)
    assert len(full) == 1 and holes(full) == 0  # nonzero: the whole star
    assert full[0].contains(ShapelyPoint(0, 0))
    # even-odd: the five rays (they touch only at the pentagon corners), the pentagon stays empty
    assert len(rays) == 5
    assert not any(p.contains(ShapelyPoint(0, 0)) for p in rays)
    pentagon = full[0].area - area(rays)
    r_in = 10 * math.cos(2 * math.pi / 5) / math.cos(math.pi / 5)  # inner radius of the {5/2} star
    assert pentagon == pytest.approx(2.5 * r_in ** 2 * math.sin(2 * math.pi / 5))


def test_disjoint_rings_take_the_fast_path_and_stay_separate():
    rings = [square(0, 0, 1), square(5, 5, 1), square(10, 0, 1)]
    polys = fill_region(rings, even_odd=False, min_area=MIN_AREA)
    assert len(polys) == 3 and area(polys) == pytest.approx(3.0)


def test_degenerate_rings_do_not_fail():
    zero = [(0, 0), (5, 0), (10, 0)]  # zero area
    two = [(0, 0), (1, 1)]
    touching = [(0, 0), (4, 0), (4, 4), (2, 0), (0, 4)]  # touches itself at (2, 0)
    assert fill_region([zero], even_odd=False, min_area=MIN_AREA) == []
    assert fill_region([two], even_odd=False, min_area=MIN_AREA) == []
    assert fill_region([], even_odd=True, min_area=MIN_AREA) == []
    polys = fill_region([touching], even_odd=False, min_area=MIN_AREA)
    assert area(polys) == pytest.approx(8.0)
    polys = fill_region([touching, zero], even_odd=True, min_area=MIN_AREA)
    assert area(polys) == pytest.approx(8.0)


def test_duplicate_ring_nonzero_and_even_odd():
    sq = square(0, 0, 10)
    assert area(fill_region([sq, sq], even_odd=False, min_area=MIN_AREA)) == pytest.approx(100.0)
    assert fill_region([sq, sq], even_odd=True, min_area=MIN_AREA) == []  # painted twice = not painted


def test_polygon_rings_exterior_then_holes():
    polys = fill_region([square(0, 0, 10), square(3, 3, 4, ccw=False)], even_odd=False, min_area=MIN_AREA)
    rings = polygon_rings(polys[0])
    assert len(rings) == 2 and all(r[0] != r[-1] for r in rings)


def test_winding_numbers():
    pts = __import__("numpy").array([(5.0, 5.0), (20.0, 20.0)])
    import numpy as np

    rings = [np.array(square(0, 0, 10)), np.array(square(2, 2, 6))]
    assert winding_numbers(pts, rings).tolist() == [2, 0]


# -- flattening (0.01 mm on paper) -------------------------------------------------------------
def bezier(p0, p1, p2, p3, t):
    s = 1 - t
    return (s ** 3 * p0[0] + 3 * s * s * t * p1[0] + 3 * s * t * t * p2[0] + t ** 3 * p3[0],
            s ** 3 * p0[1] + 3 * s * s * t * p1[1] + 3 * s * t * t * p2[1] + t ** 3 * p3[1])


def distance_to_curve(point, cps, samples=2000) -> float:
    """Distance to the curve: coarse samples, then a ternary search around the nearest one."""
    best = min(range(samples + 1), key=lambda i: math.dist(point, bezier(*cps, i / samples)))
    lo, hi = max(0.0, (best - 1) / samples), min(1.0, (best + 1) / samples)
    for _ in range(80):
        m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if math.dist(point, bezier(*cps, m1)) < math.dist(point, bezier(*cps, m2)):
            hi = m2
        else:
            lo = m1
    return math.dist(point, bezier(*cps, (lo + hi) / 2))


def test_circle_r50_vertices_and_chord_midpoints_within_tolerance():
    """Circle of radius 50 mm drawn as 4 Béziers (as PDF writers do, kappa 0.5523): every vertex
    and every chord midpoint lies within 0.01 mm of the PDF curve. (The 4-Bézier circle itself
    deviates from a true circle by up to 0.0136 mm at r = 50 mm — the tolerance is about the PDF
    curve, spec «Точність кривих».)"""
    r, tol, k = 50.0, 0.01, 0.5522847498
    quarter = ((r, 0.0), (r, k * r), (k * r, r), (0.0, r))
    out = [quarter[0]]
    flatten_cubic(*quarter, tol, out)
    assert out[-1] == quarter[3]
    assert len(out) - 1 == cubic_steps(*quarter, tol)
    worst = 0.0
    for a, b in zip(out, out[1:], strict=False):
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        worst = max(worst, distance_to_curve(a, quarter), distance_to_curve(mid, quarter))
    assert worst <= tol
    # radial check against the circle: the Bézier's own error plus the chord sag
    radial = max(abs(math.hypot(*p) - r) for p in out)
    assert radial <= 0.0137


def test_flatten_keeps_lines_and_closes_ring():
    segs = [("l", (0, 0), (10, 0)), ("c", (10, 0), (12, 3), (8, 7), (10, 10)), ("l", (10, 10), (0, 0))]
    pts = flatten_segments(segs, 0.01)
    assert pts[0] == (0, 0) and pts[1] == (10, 0) and pts[-1] == (0, 0)
    ring = subpath_ring("s", segs, 0.01)
    assert ring[-1] != ring[0] and len(ring) == len(pts) - 1
    assert subpath_ring("p", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01) == [(0, 0), (1, 0), (1, 1), (0, 1)]


def test_straight_bezier_is_one_step():
    assert cubic_steps((0, 0), (1, 0), (2, 0), (3, 0), 0.01) == 1


# -- speed of the fast path --------------------------------------------------------------------
def test_fast_path_speed_20000_paths():
    """20 000 paths of one ring of 4 Béziers (glyph-sized circles, 2 mm radius) in ≤ 2 s."""
    k = 0.5522847498
    paths = []
    for i in range(20_000):
        cx, cy, r = (i % 200) * 5.0, (i // 200) * 5.0, 2.0
        quarters = []
        for q in range(4):
            a0 = q * math.pi / 2
            a1 = a0 + math.pi / 2
            c0, s0, c1, s1 = math.cos(a0), math.sin(a0), math.cos(a1), math.sin(a1)
            quarters.append(("c", (cx + r * c0, cy + r * s0),
                             (cx + r * (c0 - k * s0), cy + r * (s0 + k * c0)),
                             (cx + r * (c1 + k * s1), cy + r * (s1 - k * c1)),
                             (cx + r * c1, cy + r * s1)))
        paths.append(quarters)
    started = time.perf_counter()
    painted = 0.0
    for segs in paths:
        polys = fill_region([subpath_ring("s", segs, 0.01)], even_odd=False, min_area=MIN_AREA)
        painted += polys[0].area
    elapsed = time.perf_counter() - started
    print(f"fill_region fast path: 20000 paths in {elapsed:.2f} s")
    assert painted == pytest.approx(20_000 * math.pi * 4.0, rel=1e-2)  # inscribed polygons
    assert elapsed <= 2.0
