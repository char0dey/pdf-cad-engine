# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""A faster PDF output for the ezdxf drawing add-on (engine 1.2).

ezdxf's ``PyMuPdfRenderBackend`` (1.4.5) draws a whole page into one ``pymupdf.Shape``.
Four things make big drawings slow there, none of them ezdxf's frontend:

1. ``Shape.finish`` appends every path to ``self.totalcont`` — a string attribute, so each
   append copies all the page content written so far: quadratic in the number of paths.
2. ``finish(oc=...)`` looks the layer's optional-content group up in the page resources
   again for every path.
3. ``Shape.draw_line`` builds ``Point`` / ``Matrix`` objects per vertex; dense hatch
   patterns come as millions of line segments.
4. ``Shape.draw_bezier`` / ``draw_curve`` do the same per curve; text is drawn as glyph
   outlines, so a drawing with thousands of TEXT entities comes as millions of curves
   (1.2.1).

`FastRenderBackend` keeps the output the same — the same paths, colours, widths and one PDF
layer (OCG) per drawing layer — and removes these costs: the shape's content is moved into
its own content stream every `CHUNK_PATHS` paths (each path is wrapped in ``q … Q``, so a
chunk is balanced; the final ``commit`` of ezdxf wraps the page once), the OCG name is
cached per page, and lines and curves are written as ``m`` / ``l`` / ``c`` operators
directly (`_PathWriter`): through the same inverse page matrix as ``Shape``, with the same
``m`` only where a segment does not start at the current point, the same subpath closing as
ezdxf's ``add_path_to_shape`` and the same quadratic → cubic conversion as
``Shape.draw_curve``; numbers have fixed three decimals (0.001 pt; PDF allows no exponent
notation).

It relies on internals of the pinned versions (``Shape.totalcont`` / ``text_cont`` /
``draw_cont`` / ``last_point`` / ``ipctm``, ``Page._get_optional_content``,
``TOOLS._insert_contents``); tests/test_pdf_backend.py compares pixels, layers and timing
against the stock backend, so a library update that breaks this shows up there.
"""

from __future__ import annotations

from collections.abc import Iterable

import pymupdf
from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend, PyMuPdfRenderBackend
from ezdxf.path import Command

#: Paths per content stream; small enough to keep appends cheap, few streams per page.
CHUNK_PATHS = 500
#: ``Shape.draw_curve`` turns a quadratic curve into a cubic one with this factor.
CURVE_KAPPA = 0.55228474983


class _PathWriter:
    """PDF path operators for one shape, written the way ``Shape.draw_line`` /
    ``draw_bezier`` / ``draw_curve`` write them, without a Point / Matrix per vertex.
    Points are ``(x, y)`` tuples in page coordinates."""

    __slots__ = ("matrix", "parts", "last")

    def __init__(self, shape) -> None:
        self.matrix = tuple(shape.ipctm)
        self.parts: list[str] = []
        self.last = None if shape.last_point is None else (shape.last_point.x, shape.last_point.y)

    def _xy(self, x: float, y: float) -> str:
        a, b, c, d, e, f = self.matrix
        return f"{x * a + y * c + e:.3f} {x * b + y * d + f:.3f}"

    def _start(self, p) -> None:
        if self.last != p:
            self.parts.append(self._xy(*p) + " m\n")

    def line(self, p0, p1) -> None:
        self._start(p0)
        self.parts.append(self._xy(*p1) + " l\n")
        self.last = p1

    def bezier(self, p0, c1, c2, p3) -> None:
        self._start(p0)
        self.parts.append(f"{self._xy(*c1)} {self._xy(*c2)} {self._xy(*p3)} c\n")
        self.last = p3

    def curve(self, p0, ctrl, p2) -> None:
        k1 = (p0[0] + (ctrl[0] - p0[0]) * CURVE_KAPPA, p0[1] + (ctrl[1] - p0[1]) * CURVE_KAPPA)
        k2 = (p2[0] + (ctrl[0] - p2[0]) * CURVE_KAPPA, p2[1] + (ctrl[1] - p2[1]) * CURVE_KAPPA)
        self.bezier(p0, k1, k2, p2)

    def path(self, path, close: bool) -> None:
        """ezdxf's ``add_path_to_shape``: segments from the current point; with ``close``, a
        subpath that does not end where it started gets a closing line."""
        start = path.start
        sub_start = start
        for command in path.commands():
            end = command.end
            kind = command.type
            if kind == Command.MOVE_TO:
                if close and not sub_start.isclose(end):
                    self.line((start.x, start.y), (sub_start.x, sub_start.y))
                sub_start = end
            elif kind == Command.LINE_TO:
                self.line((start.x, start.y), (end.x, end.y))
            elif kind == Command.CURVE3_TO:
                ctrl = command.ctrl
                self.curve((start.x, start.y), (ctrl.x, ctrl.y), (end.x, end.y))
            elif kind == Command.CURVE4_TO:
                c1, c2 = command.ctrl1, command.ctrl2
                self.bezier((start.x, start.y), (c1.x, c1.y), (c2.x, c2.y), (end.x, end.y))
            start = end
        if close and not sub_start.isclose(start):
            self.line((start.x, start.y), (sub_start.x, sub_start.y))

    def commit(self, shape) -> None:
        if self.parts:
            shape.draw_cont += "".join(self.parts)
            shape.last_point = pymupdf.Point(self.last)


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

    # -- direct path operators ----------------------------------------------------------------
    def _segments(self, segments: Iterable) -> None:
        writer = _PathWriter(self.content_shape)
        for start, end in segments:
            writer.line((start.x, start.y), (end.x, end.y))
        writer.commit(self.content_shape)

    def draw_line(self, start, end, properties) -> None:
        self._segments(((start, end),))
        self.finish_line(self.content_shape, properties, close=False)

    def draw_point(self, pos, properties) -> None:
        self._segments(((pos, pos),))
        self.finish_line(self.content_shape, properties, close=False)

    def draw_solid_lines(self, lines, properties) -> None:
        self._segments(lines)
        self.finish_line(self.content_shape, properties, close=False)

    def draw_path(self, path, properties) -> None:
        if len(path) == 0:
            return
        writer = _PathWriter(self.content_shape)
        writer.path(path, close=False)
        writer.commit(self.content_shape)
        self.finish_line(self.content_shape, properties, close=False)

    def draw_filled_paths(self, paths, properties) -> None:
        writer = _PathWriter(self.content_shape)
        for path in paths:
            writer.path(path, close=True)
        writer.commit(self.content_shape)
        self.finish_filling(self.content_shape, properties)


class FastPdfBackend(PyMuPdfBackend):
    """``PyMuPdfBackend`` whose replay goes to `FastRenderBackend`."""

    def make_backend(self, page, settings) -> FastRenderBackend:
        return FastRenderBackend(page, settings)
