# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Safety net (engine 1.5.2): one entity that ezdxf cannot process does not fail the drawing.

Four times in three days (PROD 2026-11-12…14) a single entity made ezdxf raise and the whole drawing
failed with exit 5: a block reference without its block, a far outlier, a leader without a landing,
a spline of degree 11. Each was fixed in the engine, but the next unknown ezdxf bug would do the same.

Two places go over the geometry of every entity and catch the error of one entity here:

- extents (`extents`): first the whole set, as before; only when that raises, entity by entity,
  leaving out the ones that raise;
- rendering: ``hatch_limit.EngineFrontend.draw_entity`` — called for every entity, the virtual
  entities inside block references too, so an entity in a block is left out on its own.

Errors of the engine itself (`EngineError`: input refusals, the memory limit), ``MemoryError`` and
errors that read as out-of-memory (`looks_like_oom`) pass through; the time budget
(`BudgetExceeded`) is a ``BaseException`` and is never caught.

`LOG` records what was left out — one log per engine process, which converts one drawing; it is
reset by ``dxfio.load_dxf``. The first time an error is seen, one line goes to stderr; the meta gets
``skipped_entities`` (entities left out of the drawing) and ``entity_errors`` (`to_meta`).
"""

from __future__ import annotations

import sys
import traceback
from dataclasses import dataclass, field

from cad_engine.errors import EngineError, looks_like_oom

#: Exceptions the safety net never swallows (``BudgetExceeded`` is a BaseException anyway).
PASS_THROUGH: tuple[type[BaseException], ...] = (MemoryError, EngineError)
#: The meta lists at most this many different errors (the most frequent first).
MAX_META_ERRORS = 10
#: Error text cut to this length (one line).
MAX_ERROR_CHARS = 160
#: Innermost traceback frames in ``where``.
WHERE_FRAMES = 3

STAGES = ("extents", "render")


def describe_error(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    line = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return line[:MAX_ERROR_CHARS]


def _short_path(filename: str) -> str:
    """``ezdxf/math/bspline.py`` from a site-packages path, a Cython source path
    (``src/ezdxf/acc/bspline.pyx``) or the engine's source tree."""
    path = filename.replace("\\", "/")
    marker = "site-packages/"
    i = path.rfind(marker)
    if i >= 0:
        return path[i + len(marker):]
    for package in ("ezdxf/", "cad_engine/"):
        j = path.rfind(package)
        if j == 0 or (j > 0 and path[j - 1] == "/"):
            return path[j:]
    return "/".join(path.split("/")[-2:])


def describe_where(exc: BaseException) -> str:
    """The innermost frames of the traceback, innermost first: ``a.py:1 < b.py:2 < c.py:3``."""
    frames = traceback.extract_tb(exc.__traceback__)[-WHERE_FRAMES:]
    return " < ".join(f"{_short_path(f.filename)}:{f.lineno}" for f in reversed(frames))


def _dxftype(entity) -> str:
    try:
        return str(entity.dxftype())
    except Exception:
        return type(entity).__name__


@dataclass
class SkipLog:
    """Entities the safety net left out, counted by (stage, type, error, where)."""

    counts: dict[tuple[str, str, str, str], int] = field(default_factory=dict)
    #: Whether a drawing was loaded since the last reset (the meta reports only then).
    active: bool = False
    #: stderr gets one line per new error; tests silence it.
    quiet: bool = False

    def reset(self) -> None:
        self.counts.clear()
        self.active = True

    def add(self, stage: str, entity, exc: BaseException) -> None:
        assert stage in STAGES, stage
        key = (stage, _dxftype(entity), describe_error(exc), describe_where(exc))
        first = key not in self.counts
        self.counts[key] = self.counts.get(key, 0) + 1
        if first and not self.quiet:
            what = "rendering" if stage == "render" else "measuring the extents"
            print(f"cad-engine: {key[1]} skipped while {what} ({key[2]} at {key[3]})", file=sys.stderr)

    def count(self, stage: str | None = None) -> int:
        return sum(n for k, n in self.counts.items() if stage is None or k[0] == stage)

    def first_error(self) -> str:
        if not self.counts:
            return ""
        (_, dxftype, error, where), _ = max(self.counts.items(), key=lambda kv: kv[1])
        return f"{dxftype}: {error} at {where}"

    def to_meta(self) -> list[dict] | None:
        if not self.counts:
            return None
        ranked = sorted(self.counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"stage": s, "type": t, "error": e, "where": w, "count": n}
                for (s, t, e, w), n in ranked[:MAX_META_ERRORS]]


LOG = SkipLog()


def reset() -> None:
    """A new drawing: forget what was left out of the previous one."""
    LOG.reset()


def clear() -> None:
    """A new engine command (in-process runs, the tests): no drawing loaded yet."""
    LOG.counts.clear()
    LOG.active = False


def extents(entities, *, fast: bool = True):
    """``ezdxf.bbox.extents`` of `entities` with the safety net: the whole set as before, and only
    when that raises, entity by entity, leaving out (and logging) the ones that raise."""
    from ezdxf import bbox

    items = list(entities)
    try:
        return bbox.extents(items, fast=fast)
    except PASS_THROUGH:
        raise
    except Exception as exc:
        if looks_like_oom(exc):
            raise
        # find the entities that raise, one by one
    box = bbox.BoundingBox()
    for entity in items:
        part = entity_extents(entity, fast=fast)
        if part is not None and part.has_data:
            box.extend((part.extmin, part.extmax))
    return box


def entity_extents(entity, *, fast: bool = True):
    """Extents of one entity, or None (logged) when ezdxf raises on it."""
    from ezdxf import bbox

    try:
        return bbox.extents((entity,), fast=fast)
    except PASS_THROUGH:
        raise
    except Exception as exc:
        if looks_like_oom(exc):
            raise
        LOG.add("extents", entity, exc)
        return None
