# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Fixes for ezdxf bugs that real drawings hit, applied in the engine process.

``apply()`` is called by ``dxfio.load_dxf`` (every DXF the engine reads goes through it) and is
idempotent. A fix is applied only to the ezdxf version it was written against; on another version
it is left out with a line on stderr, so an upgrade is decided by the tests, not by the patch.

Fixes:

- ``LeaderData.transform`` (MULTILEADER leader): a leader whose landing (dogleg) has length 0
  makes ezdxf 1.4.5 normalize a null vector → ``ZeroDivisionError``. ezdxf transforms the entity
  whenever a block reference is exploded (extents, rendering), so one such leader inside an
  inserted block failed the whole drawing: PROD 2026-11-13, dwg-to-pdf, Civil 3D drawing, dxf2pdf
  exit 5. The replacement computes exactly what ezdxf does whenever ezdxf succeeds; a landing of
  length 0 (or one a degenerate transformation collapses) keeps its direction and length 0.
"""

from __future__ import annotations

import sys

PATCHED_EZDXF = "1.4.5"

_applied = False
stock_leader_transform = None  # ezdxf's own LeaderData.transform, kept for the tests


def _leader_transform(self, wcs) -> None:
    """``ezdxf.entities.mleader.LeaderData.transform`` without the division by zero."""
    from ezdxf.math import X_AXIS, Vec3

    m = wcs.m
    self.last_leader_point = m.transform(self.last_leader_point)
    # Same expressions as ezdxf, so every case ezdxf gets through gives the same numbers.
    try:
        dog_leg = m.transform_direction(self.dogleg_vector.normalize(self.dogleg_length))
        direction = Vec3(self.dogleg_vector)
    except ZeroDivisionError:  # dogleg_vector is NULL: ezdxf's own fallback
        dog_leg = m.transform_direction(Vec3(self.dogleg_length, 0, 0))
        direction = X_AXIS
    try:
        self.dogleg_vector = dog_leg.normalize()
        self.dogleg_length = dog_leg.magnitude
    except ZeroDivisionError:  # the fix: a landing of length 0, or one the transformation collapses
        try:
            self.dogleg_vector = m.transform_direction(direction).normalize()
        except ZeroDivisionError:
            self.dogleg_vector = direction.normalize()
        self.dogleg_length = 0.0
    self.breaks = list(m.transform_vertices(self.breaks))
    for leader_line in self.lines:
        leader_line.transform(wcs)


def apply() -> bool:
    """Install the fixes once per process; returns whether they are in place."""
    global _applied, stock_leader_transform
    if _applied:
        return True
    import ezdxf
    from ezdxf.entities import mleader

    if ezdxf.__version__ != PATCHED_EZDXF:
        print(f"cad-engine: ezdxf {ezdxf.__version__} is not {PATCHED_EZDXF}; ezdxf fixes not applied",
              file=sys.stderr)
        return False
    stock_leader_transform = mleader.LeaderData.transform
    mleader.LeaderData.transform = _leader_transform
    _applied = True
    return True
