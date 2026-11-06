# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Geometry recognition on extracted PDF path segments (pdf2dxf phase-2 flags).

Pure Python on (x, y) tuples in DXF output units; no ezdxf/PyMuPDF imports.
A path segment is ``('l', a, b)`` (line) or ``('c', p0, p1, p2, p3)`` (cubic Bézier).

- `find_arc_runs` (``--arcs``): runs of consecutive Béziers lying on one circle within
  0.5 % of the radius -> CIRCLE (closed, 360°) or ARC;
- `find_dash_runs` (``--linetypes``): runs of >= 4 short collinear line segments with
  near-equal dashes and gaps -> one LINE with a linetype;
- `simplify_collinear` (``--merge``): drops polyline vertices that lie on the line between
  their neighbours (lateral tolerance) and zero-length steps;
- `bezier_chain_knots`: knot vector that represents joined Bézier pieces as one exact
  degree-3 B-spline (interior knots of multiplicity 3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Point = tuple[float, float]

ARC_REL_TOL = 0.005  # 0.5 % of the radius
_SAMPLES = (0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875)
_MAX_BEZIER_SWEEP = math.pi + 0.1  # one cubic cannot follow more than ~180° within tolerance
_FULL_TURN_TOL = math.radians(1.0)

MIN_DASHES = 4
DASH_REL_TOL = 0.2  # dashes / gaps of one run differ by at most 20 % (+ an absolute epsilon)


# -- Bézier helpers ----------------------------------------------------------------------------
def bezier_point(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    s = 1.0 - t
    a, b, c, d = s * s * s, 3 * s * s * t, 3 * s * t * t, t * t * t
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def line_as_bezier(a: Point, b: Point) -> tuple[Point, Point, Point, Point]:
    """A straight segment as an exact cubic Bézier (control points at 1/3 and 2/3)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    return (a, (a[0] + dx / 3.0, a[1] + dy / 3.0), (a[0] + 2.0 * dx / 3.0, a[1] + 2.0 * dy / 3.0), b)


def bezier_chain_knots(pieces: int) -> list[float]:
    """Knots of a degree-3 B-spline made of `pieces` Bézier segments (3 * pieces + 1 control points)."""
    knots = [0.0] * 4
    for i in range(1, pieces):
        knots.extend((float(i),) * 3)
    knots.extend((float(pieces),) * 4)
    return knots


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# -- circles and arcs --------------------------------------------------------------------------
@dataclass
class BezierArc:
    cx: float
    cy: float
    r: float
    start: float  # angle of the segment start (radians)
    sweep: float  # signed: > 0 counter-clockwise


def _circumcircle(a: Point, b: Point, c: Point) -> tuple[float, float, float] | None:
    # relative to `a` for numerical stability with large coordinates
    bx, by = b[0] - a[0], b[1] - a[1]
    cx, cy = c[0] - a[0], c[1] - a[1]
    d = 2.0 * (bx * cy - by * cx)
    scale = max(abs(bx), abs(by), abs(cx), abs(cy))
    if scale == 0.0 or abs(d) <= 1e-12 * scale * scale:
        return None
    b2, c2 = bx * bx + by * by, cx * cx + cy * cy
    ux = (cy * b2 - by * c2) / d
    uy = (bx * c2 - cx * b2) / d
    return a[0] + ux, a[1] + uy, math.hypot(ux, uy)


def _wrap(angle: float) -> float:
    """Angle difference -> (-pi, pi]."""
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    while angle > math.pi:
        angle -= 2.0 * math.pi
    return angle


def bezier_arc(p0: Point, p1: Point, p2: Point, p3: Point, rel_tol: float = ARC_REL_TOL) -> BezierArc | None:
    """The circular arc a cubic Bézier approximates, or None.

    Every sample point must be within ``rel_tol * min(radius, chord)`` of the circle
    (``min`` keeps nearly straight curves, which fit huge circles, out) and the angle must
    progress monotonically.
    """
    chord = _dist(p0, p3)
    if chord <= 0.0:
        return None
    mid = bezier_point(p0, p1, p2, p3, 0.5)
    circle = _circumcircle(p0, mid, p3)
    if circle is None:
        return None
    cx, cy, r = circle
    if r <= 0.0:
        return None
    tol = rel_tol * min(r, chord)
    start = math.atan2(p0[1] - cy, p0[0] - cx)
    prev = start
    sweep = 0.0
    direction = 0.0
    for t in (*_SAMPLES, 1.0):
        q = p3 if t == 1.0 else bezier_point(p0, p1, p2, p3, t)
        if abs(math.hypot(q[0] - cx, q[1] - cy) - r) > tol:
            return None
        angle = math.atan2(q[1] - cy, q[0] - cx)
        step = _wrap(angle - prev)
        if step == 0.0 or (direction and (step > 0) != (direction > 0)):
            return None
        direction = step
        sweep += step
        prev = angle
    if abs(sweep) > _MAX_BEZIER_SWEEP:
        return None
    return BezierArc(cx, cy, r, start, sweep)


@dataclass
class ArcRun:
    first: int  # index of the first segment of the run
    end: int  # index after the last segment
    cx: float
    cy: float
    r: float
    start: float  # radians, angle of the run start
    sweep: float  # signed total sweep (radians)
    circle: bool

    def dxf_angles(self) -> tuple[float, float]:
        """(start, end) in degrees, counter-clockwise as DXF ARC expects."""
        a0 = self.start if self.sweep > 0 else self.start + self.sweep
        a1 = a0 + abs(self.sweep)
        return _deg(a0), _deg(a1)


def arc_beziers(cx: float, cy: float, r: float, start: float, sweep: float) -> list[tuple[Point, ...]]:
    """Cubic Bézier approximation of a circular arc (pieces of at most 90°), for previews."""
    pieces = max(1, math.ceil(abs(sweep) / (math.pi / 2.0) - 1e-9))
    step = sweep / pieces
    kappa = 4.0 / 3.0 * math.tan(step / 4.0)
    out = []
    a = start
    for _ in range(pieces):
        b = a + step
        ca, sa, cb, sb = math.cos(a), math.sin(a), math.cos(b), math.sin(b)
        p0 = (cx + r * ca, cy + r * sa)
        p3 = (cx + r * cb, cy + r * sb)
        p1 = (p0[0] - kappa * r * sa, p0[1] + kappa * r * ca)
        p2 = (p3[0] + kappa * r * sb, p3[1] - kappa * r * cb)
        out.append((p0, p1, p2, p3))
        a = b
    return out


def _deg(angle: float) -> float:
    deg = round(math.degrees(angle) % 360.0, 9)
    return 0.0 if deg >= 360.0 else deg + 0.0


def _same_circle(a: BezierArc, b: BezierArc, rel_tol: float) -> bool:
    tol = rel_tol * a.r
    return (
        (a.sweep > 0) == (b.sweep > 0)
        and abs(a.r - b.r) <= tol
        and math.hypot(a.cx - b.cx, a.cy - b.cy) <= tol
    )


def find_arc_runs(segs: list[tuple], rel_tol: float = ARC_REL_TOL) -> list[ArcRun]:
    """Maximal runs of consecutive Bézier segments of one contiguous subpath on one circle.

    A run whose sweep is a full turn (it then ends where it starts) is a circle; a run of
    more than a full turn is left alone (it is not a plain circle or arc).
    """
    runs: list[ArcRun] = []
    n = len(segs)
    i = 0
    while i < n:
        seg = segs[i]
        arc = bezier_arc(*seg[1:]) if seg[0] == "c" else None
        if arc is None:
            i += 1
            continue
        members = [arc]
        sweep = arc.sweep
        j = i + 1
        while j < n and segs[j][0] == "c" and abs(sweep) < 2.0 * math.pi - _FULL_TURN_TOL:
            nxt = bezier_arc(*segs[j][1:])
            if nxt is None or not _same_circle(arc, nxt, rel_tol):
                break
            members.append(nxt)
            sweep += nxt.sweep
            j += 1
        cx = sum(m.cx for m in members) / len(members)
        cy = sum(m.cy for m in members) / len(members)
        r = sum(m.r for m in members) / len(members)
        if abs(sweep) > 2.0 * math.pi + _FULL_TURN_TOL:  # overlapping turns: not a plain circle
            i = j
            continue
        full = abs(abs(sweep) - 2.0 * math.pi) <= _FULL_TURN_TOL  # a full turn on one circle is closed
        runs.append(ArcRun(i, j, cx, cy, r, math.atan2(segs[i][1][1] - cy, segs[i][1][0] - cx), sweep, full))
        i = j
    return runs


# -- dashed runs -------------------------------------------------------------------------------
@dataclass
class DashRun:
    first: int  # index of the first dash (subpath index)
    end: int  # index after the last dash
    start: Point  # start of the first dash
    stop: Point  # end of the last dash
    dash: float  # median dash length
    gap: float  # median gap length


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _near(value: float, ref: float, eps: float) -> bool:
    return abs(value - ref) <= DASH_REL_TOL * ref + eps


def _collect_dashes(dashes: list[tuple[Point, Point] | None], i: int, *, lat_tol: float, max_len: float,
                    eps: float) -> int:
    """Greedy end index of a dash run starting at `i`.

    The direction is refined on the way (origin -> end of the last accepted dash), gaps must
    stay near the first gap and dashes near the second dash (the first and the last dash of
    a pattern may be cut short).
    """
    a0, b0 = dashes[i]  # type: ignore[misc]
    first_len = _dist(a0, b0)
    if first_len > max_len:
        return i + 1
    origin = a0
    tip = b0 if first_len > eps else None
    prev_end = b0
    ref_gap: float | None = None
    ref_dash: float | None = None
    k = i + 1
    while k < len(dashes) and dashes[k] is not None:
        a, b = dashes[k]  # type: ignore[misc]
        base = tip if tip is not None else a
        ux, uy = base[0] - origin[0], base[1] - origin[1]
        length = math.hypot(ux, uy)
        if length <= eps:
            break
        ux, uy = ux / length, uy / length
        if (abs((a[0] - origin[0]) * uy - (a[1] - origin[1]) * ux) > lat_tol
                or abs((b[0] - origin[0]) * uy - (b[1] - origin[1]) * ux) > lat_tol):
            break
        pa = (a[0] - origin[0]) * ux + (a[1] - origin[1]) * uy
        pb = (b[0] - origin[0]) * ux + (b[1] - origin[1]) * uy
        pe = (prev_end[0] - origin[0]) * ux + (prev_end[1] - origin[1]) * uy
        gap, dash = pa - pe, pb - pa
        if dash < -eps or gap <= eps or gap > max_len or dash > max_len:
            break
        if ref_gap is not None and not _near(gap, ref_gap, eps):
            break
        if ref_dash is None:
            if first_len > dash * (1 + DASH_REL_TOL) + eps:
                break
            ref_dash = dash
        elif not _near(dash, ref_dash, eps):
            if dash < ref_dash:  # a shorter last dash ends the pattern
                k += 1
            break
        ref_gap = gap if ref_gap is None else ref_gap
        prev_end = b
        tip = b
        k += 1
    return k


def _check_run(dashes: list[tuple[Point, Point] | None], i: int, j: int, lat_tol: float,
               eps: float) -> DashRun | int:
    """dashes[i:j] against the line from the first start to the last end: the run, or the
    index of the first dash that leaves the lateral tolerance (cut the run there)."""
    start = dashes[i][0]  # type: ignore[index]
    stop = dashes[j - 1][1]  # type: ignore[index]
    ux, uy = stop[0] - start[0], stop[1] - start[1]
    total = math.hypot(ux, uy)
    if total <= eps:
        return i
    ux, uy = ux / total, uy / total
    lengths: list[float] = []
    gaps: list[float] = []
    prev = None
    for k in range(i, j):
        a, b = dashes[k]  # type: ignore[misc]
        if (abs((a[0] - start[0]) * uy - (a[1] - start[1]) * ux) > lat_tol
                or abs((b[0] - start[0]) * uy - (b[1] - start[1]) * ux) > lat_tol):
            return max(k, i + 1)
        pa = (a[0] - start[0]) * ux + (a[1] - start[1]) * uy
        pb = (b[0] - start[0]) * ux + (b[1] - start[1]) * uy
        if prev is not None:
            gaps.append(pa - prev)
        lengths.append(pb - pa)
        prev = pb
    interior = lengths[1:-1] or lengths
    return DashRun(i, j, start, stop, _median(interior), _median(gaps))


def find_dash_runs(dashes: list[tuple[Point, Point] | None], *, lat_tol: float, max_len: float,
                   eps: float, min_count: int = MIN_DASHES) -> list[DashRun]:
    """Runs of >= `min_count` consecutive single-segment subpaths that form one dashed line.

    ``dashes[i]`` is the (start, end) of subpath i when it is a single line segment, else None.
    Dashes must be collinear (within `lat_tol` of the line from the first start to the last
    end), advance in one direction, be at most `max_len` long with gaps of at most `max_len`,
    and have near-equal lengths (the first and the last may be shorter) and near-equal gaps.
    A dashed curve is split into straight runs that stay within the tolerance.
    """
    runs: list[DashRun] = []
    n = len(dashes)
    i = 0
    while i < n:
        if dashes[i] is None:
            i += 1
            continue
        j = _collect_dashes(dashes, i, lat_tol=lat_tol, max_len=max_len, eps=eps)
        run: DashRun | None = None
        while j - i >= min_count:
            checked = _check_run(dashes, i, j, lat_tol, eps)
            if isinstance(checked, DashRun):
                run = checked
                break
            j = checked if checked < j else j - 1
        if run is not None:
            runs.append(run)
            i = run.end
        else:
            i += 1
    return runs


# -- collinear vertices ------------------------------------------------------------------------
_MAX_SKIPPED = 256


def simplify_collinear(pts: list[Point], tol: float, eps: float) -> list[Point]:
    """Drop zero-length steps and vertices that lie (within `tol`) on the straight line from
    the last kept vertex to the next one, keeping the direction (no fold-backs)."""
    if len(pts) <= 2:
        return pts
    deduped = [pts[0]]
    for p in pts[1:]:
        if _dist(p, deduped[-1]) > eps:
            deduped.append(p)
    if len(deduped) < 2:
        return pts[:2]
    pts = deduped
    out: list[Point] = [pts[0]]
    skipped: list[Point] = []
    n = len(pts)
    for idx in range(1, n - 1):
        v = pts[idx]
        anchor = out[-1]
        if _dist(v, anchor) <= eps:
            continue
        nxt = pts[idx + 1]
        if _removable(anchor, v, nxt, skipped, tol, eps) and len(skipped) < _MAX_SKIPPED:
            skipped.append(v)
            continue
        out.append(v)
        skipped = []
    last = pts[-1]
    if _dist(last, out[-1]) > eps or len(out) == 1:
        out.append(last)
    return out


def _removable(a: Point, v: Point, b: Point, skipped: list[Point], tol: float, eps: float) -> bool:
    ux, uy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(ux, uy)
    if length <= eps:
        return False
    ux, uy = ux / length, uy / length
    for p in (*skipped, v):
        px, py = p[0] - a[0], p[1] - a[1]
        along = px * ux + py * uy
        if along <= eps or along >= length - eps:
            return False
        if abs(px * uy - py * ux) > tol:
            return False
    return True
