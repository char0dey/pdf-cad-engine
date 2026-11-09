# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""A faster PDF output for the ezdxf drawing add-on (engine 1.2).

ezdxf's ``PyMuPdfRenderBackend`` (1.4.5) draws a whole page into one ``pymupdf.Shape``.
Three things make big drawings slow there, none of them ezdxf's frontend:

1. ``Shape.finish`` appends every path to ``self.totalcont`` — a string attribute, so each
   append copies all the page content written so far: quadratic in the number of paths.
2. ``finish(oc=...)`` looks the layer's optional-content group up in the page resources
   again for every path.
3. ``Shape.draw_line`` builds ``Point`` / ``Matrix`` objects per vertex; dense hatch
   patterns come as millions of line segments.

`FastRenderBackend` keeps the output the same — the same paths, colours, widths and one PDF
layer (OCG) per drawing layer — and removes the three costs: the shape's content is moved
into its own content stream every `CHUNK_PATHS` paths (each path is wrapped in ``q … Q``,
so a chunk is balanced; the final ``commit`` of ezdxf wraps the page once), the OCG name
is cached per page, and line segments are written as ``m`` / ``l`` operators directly,
through the same inverse page matrix as ``Shape.draw_line``, with fixed three decimals
(0.001 pt; PDF allows no exponent notation).

It relies on internals of the pinned versions (``Shape.totalcont`` / ``text_cont`` /
``draw_cont`` / ``last_point`` / ``ipctm``, ``Page._get_optional_content``,
``TOOLS._insert_contents``); tests/test_pdf_backend.py compares pixels, layers and timing
against the stock backend, so a library update that breaks this shows up there.
"""

from __future__ import annotations

from collections.abc import Iterable

import pymupdf
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend, PyMuPdfRenderBackend

#: Paths per content stream; small enough to keep appends cheap, few streams per page.
CHUNK_PATHS = 500


class FastRenderBackend(PyMuPdfRenderBackend):
    def __init__(self, page, settings) -> None:
        super().__init__(page, settings)
        self._paths = 0
        names: dict[int, str | None] = {}
        lookup = self.page._get_optional_content

        def cached(oc):
            if oc not in names:
                names[oc] = lookup(oc)
            return names[oc]

        self.page._get_optional_content = cached  # per page instance: Shape.finish calls it

    # -- chunked content ------------------------------------------------------------------
    def _path_done(self) -> None:
        self._paths += 1
        if self._paths % CHUNK_PATHS == 0:
            self.flush_content()

    def flush_content(self) -> None:
        """``Shape.commit`` without ``Page.wrap_contents`` (which re-counts the q/Q balance of
        all page content on every call): the chunk becomes a new content stream on top."""
        shape = self.content_shape
        data = (shape.totalcont + shape.text_cont).encode()
        if data:
            xref = pymupdf.TOOLS._insert_contents(self.page, b" ", True)
            self.doc.update_stream(xref, data)
        shape.totalcont = ""
        shape.text_cont = ""

    def finish_line(self, shape, properties, close: bool) -> None:
        super().finish_line(shape, properties, close)
        self._path_done()

    def finish_filling(self, shape, properties) -> None:
        super().finish_filling(shape, properties)
        self._path_done()

    # -- direct line segments -------------------------------------------------------------
    def _add_segments(self, segments: Iterable) -> None:
        """What ``Shape.draw_line`` writes for each segment (``m`` only where the segment does
        not start at the current point), without a Point / Matrix per vertex."""
        shape = self.content_shape
        ma, mb, mc, md, me, mf = tuple(shape.ipctm)
        last = None if shape.last_point is None else (shape.last_point.x, shape.last_point.y)
        parts = []
        for start, end in segments:
            x1, y1 = start.x, start.y
            if last != (x1, y1):
                parts.append(f"{x1 * ma + y1 * mc + me:.3f} {x1 * mb + y1 * md + mf:.3f} m\n")
            x2, y2 = end.x, end.y
            parts.append(f"{x2 * ma + y2 * mc + me:.3f} {x2 * mb + y2 * md + mf:.3f} l\n")
            last = (x2, y2)
        if parts:
            shape.draw_cont += "".join(parts)
            shape.last_point = pymupdf.Point(last)

    def draw_line(self, start, end, properties) -> None:
        self._add_segments(((start, end),))
        self.finish_line(self.content_shape, properties, close=False)

    def draw_point(self, pos, properties) -> None:
        self._add_segments(((pos, pos),))
        self.finish_line(self.content_shape, properties, close=False)

    def draw_solid_lines(self, lines, properties) -> None:
        self._add_segments(lines)
        self.finish_line(self.content_shape, properties, close=False)


class FastPdfBackend(PyMuPdfBackend):
    """``PyMuPdfBackend`` whose replay goes to `FastRenderBackend`."""

    def make_backend(self, page, settings) -> FastRenderBackend:
        return FastRenderBackend(page, settings)
