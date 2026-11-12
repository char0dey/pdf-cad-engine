# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""A cap on hatch patterns for the ezdxf drawing frontend (engine 1.3).

ezdxf 1.4.5 ``Frontend.draw_hatch_pattern`` collects every line segment of a pattern into one
list before drawing it, with a time limit but no size limit. A pattern at a tiny scale over a
large area therefore takes all memory: on 2026-11-10 one AR-CONC hatch at scale 0.005 across
~8 km of a Civil 3D plan (metres) exhausted the engine's 2 GB, and 4 GB too.

`EngineFrontend` draws a pattern the same way but counts the segments while it generates them.
Past `MAX_HATCH_SEGMENTS` it drops what it collected and draws only the hatch's boundary, in
the hatch's colour and layer — what ``HatchPolicy.SHOW_OUTLINE`` does for every hatch, here only
for this one. AutoCAD's HPMAXLINES has the same 1 000 000 but counts pattern lines before
dashing; the engine counts drawn segments (dash pieces), because that is what memory grows
with. Ordinary hatches give thousands to a few hundred thousand segments and are untouched.

A hatch whose estimated size is at least `EARLY_OUT_FACTOR` times the cap is not generated at
all: area / line spacing × dashes per unit length, per pattern line. The estimate only saves
time (twenty block inserts of such a hatch would each spend seconds before hitting the cap); at
the cap the counter decides.
"""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Callable, Iterable

from ezdxf.addons.drawing import Frontend
from ezdxf.addons.drawing.frontend import closed_loops, ignore_text_boxes
from ezdxf.render import hatching

#: Most line segments one hatch pattern may produce before it is drawn as an outline.
MAX_HATCH_SEGMENTS = 1_000_000
#: A hatch estimated at this many times the cap is outlined without generating its pattern.
EARLY_OUT_FACTOR = 20
#: How often (in segments) a long pattern checks the time budget.
TICK_SEGMENTS = 100_000


class _TooDense(Exception):
    """The pattern of the hatch being drawn would exceed the cap."""


def _loop_area(path) -> float:
    try:
        pts = list(path.flattening(distance=max(_extent(path) / 1000.0, 1e-9)))
    except Exception:
        return 0.0
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        s += a.x * b.y - b.x * a.y
    return abs(s) / 2.0


def _extent(path) -> float:
    try:
        bb = path.bbox()
        return max(float(bb.size.x), float(bb.size.y)) if bb.has_data else 0.0
    except Exception:
        return 0.0


def estimate_segments(polygon, paths: Iterable) -> float:
    """Rough number of segments the pattern of ``polygon`` would produce inside ``paths``
    (boundary loops in OCS): per pattern line, the hatched area / line spacing gives the total
    line length, times dashes per unit length, plus one piece per line."""
    paths = list(paths)
    area = max((_loop_area(p) for p in paths), default=0.0)
    span = max((_extent(p) for p in paths), default=0.0)
    if area <= 0 or span <= 0:
        return 0.0
    total = 0.0
    try:
        baselines = list(hatching.pattern_baselines(polygon))
    except hatching.HatchingError:
        return 0.0
    for baseline in baselines:
        spacing = abs(baseline.normal_distance)
        if spacing <= 0:
            continue
        length = area / spacing
        rows = span / spacing
        dashes = baseline.line_pattern
        if dashes:
            period = math.fsum(abs(d) for d in dashes)
            per_unit = len(dashes) / period if period > 0 else 0.0
            total += length * per_unit + rows
        else:
            total += rows
    return total


class EngineFrontend(Frontend):
    """ezdxf ``Frontend`` with a segment cap on hatch patterns; ``simplified_hatches`` counts
    the hatches drawn as outlines."""

    def __init__(self, *args, max_hatch_segments: int | None = None,
                 tick: Callable[[], None] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # read at construction, not at import: tests lower the module constant
        self.max_hatch_segments = max_hatch_segments or MAX_HATCH_SEGMENTS
        self.simplified_hatches = 0
        self._tick = tick

    def draw_hatch_pattern(self, polygon, paths, properties) -> None:
        # ezdxf 1.4.5 Frontend.draw_hatch_pattern + the cap (and its timeout message on stderr:
        # stdout of the engine carries only the meta line)
        if polygon.pattern is None or len(polygon.pattern.lines) == 0:
            return
        paths = list(paths)
        if estimate_segments(polygon, paths) >= EARLY_OUT_FACTOR * self.max_hatch_segments:
            raise _TooDense
        ocs = polygon.ocs()
        elevation = polygon.dxf.elevation.z
        properties.linetype_pattern = tuple()
        lines: list = []
        limit = self.max_hatch_segments
        t0 = time.perf_counter()
        max_time = self.config.hatching_timeout

        def timeout() -> bool:
            if time.perf_counter() - t0 > max_time:
                print(f"cad-engine: hatching timeout of {max_time}s reached for {polygon} - aborting",
                      file=sys.stderr)
                return True
            return False

        for baseline in hatching.pattern_baselines(
            polygon,
            min_hatch_line_distance=self.config.min_hatch_line_distance,
            jiggle_origin=True,
        ):
            for line in hatching.hatch_paths(baseline, paths, timeout):
                line_pattern = baseline.pattern_renderer(line.distance)
                for s, e in line_pattern.render(line.start, line.end):
                    if ocs.transform:
                        s, e = (
                            ocs.to_wcs((s.x, s.y, elevation)),
                            ocs.to_wcs((e.x, e.y, elevation)),
                        )
                    lines.append((s, e))
                    n = len(lines)
                    if n > limit:
                        raise _TooDense
                    if self._tick is not None and n % TICK_SEGMENTS == 0:
                        self._tick()
        self.pipeline.draw_solid_lines(lines, properties)

    def draw_hatch_entity(self, entity, properties, *, loops=None) -> None:
        try:
            super().draw_hatch_entity(entity, properties, loops=loops)
        except _TooDense:
            self.simplified_hatches += 1
            self._draw_outline(entity, properties, loops)

    def _draw_outline(self, entity, properties, loops) -> None:
        # the HatchPolicy.SHOW_OUTLINE branch of ezdxf's draw_hatch_entity
        if loops is not None:  # MPOLYGON
            paths = loops
        else:
            paths = closed_loops(
                list(entity.paths.rendering_paths(entity.dxf.hatch_style)),
                entity.ocs(),
                entity.dxf.elevation.z,
            )
        for p in ignore_text_boxes(paths):
            self.pipeline.draw_path(p, properties)
