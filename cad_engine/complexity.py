# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""How heavy a PDF page is for ``page.get_drawings()`` — counted before calling it (engine 1.3).

PyMuPDF builds every path of a page in memory in one go, and past the engine's address-space
limit it crashes in C (SIGSEGV) instead of raising ``MemoryError``. On 2026-11-10 a one-page plan
with ~760 000 vector fragments did that twice in production; with 4 GB it took two minutes only
to be refused for having over 500 000 entities. So ``probe-pdf`` and ``pdf2dxf`` count the page
first and refuse it with ``too_complex``.

The count reads the page's decompressed content streams and Form XObjects (each form times the
number of times it is drawn, nested forms included) and counts operators with a regular
expression on the bytes:

* paths — the painting operators ``S s f F f* B B* b b*``: one ``get_drawings()`` entry each;
* segments — ``l c v y re``: one ``items`` entry each.

Literal and hex strings, comments and inline images are cut out first, so their bytes are not
mistaken for operators. ``n`` (end a clipping path without painting) is not counted: such paths
are not drawings.

Memory of ``get_drawings()`` measured on the servers (PyMuPDF 1.26.7, 2026-11-10): about
1.25 KB per path plus 0.37 KB per segment (100k / 200k / 400k one-segment paths: +164 / +326 /
+648 MB; 400k / 1.6M segments in ten paths: +149 / +587 MB).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from cad_engine.errors import InputError

#: Bytes ``get_drawings()`` takes per path and per segment (see the module docstring).
PATH_BYTES = 1280
SEGMENT_BYTES = 380
#: The share of the engine's 2 GB address space one page may take in ``get_drawings()``: the rest
#: is the interpreter, the libraries and the DXF being built from these paths.
MAX_PAGE_BYTES = 1200 * 1024 * 1024
#: Nested forms deeper than this are not followed (real drawings use one or two levels).
MAX_FORM_DEPTH = 8

_DELIM_BEFORE = rb"(?<![^\s\[\]\(\)<>{}/%])"
_DELIM_AFTER = rb"(?=[\s\[\]\(\)<>{}/%]|$)"
# one pass for both kinds: a 40 MB content stream is read once, not twice
_OPERATOR = re.compile(_DELIM_BEFORE + rb"(f\*|B\*|b\*|re|S|s|f|F|B|b|l|c|v|y)" + _DELIM_AFTER)
_PAINT_OPS = (b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*")
_SEGMENT_OPS = (b"l", b"c", b"v", b"y", b"re")
_FORM_CALL = re.compile(rb"/([^\s/\[\]\(\)<>{}%]+)\s+Do" + _DELIM_AFTER)

_ESCAPE = re.compile(rb"\\.", re.DOTALL)
_LITERAL = re.compile(rb"\([^()]*\)")
_HEX = re.compile(rb"<(?!<)[0-9A-Fa-f\s]*>")
_COMMENT = re.compile(rb"%[^\r\n]*")
_INLINE_IMAGE = re.compile(rb"(?<![^\s])BI\s.*?\sID\s.*?\sEI(?=\s|$)", re.DOTALL)


@dataclass(frozen=True)
class PageCost:
    paths: int
    segments: int

    @property
    def bytes(self) -> int:
        return self.paths * PATH_BYTES + self.segments * SEGMENT_BYTES

    def __add__(self, other: PageCost) -> PageCost:
        return PageCost(self.paths + other.paths, self.segments + other.segments)

    def times(self, k: int) -> PageCost:
        return PageCost(self.paths * k, self.segments * k)


def _strip(content: bytes) -> bytes:
    """Content without strings, comments and inline image data. Each pass copies the content, so
    it runs only when its opening byte is there at all (a vector-only stream has none)."""
    if b"BI" in content:
        content = _INLINE_IMAGE.sub(b" ", content)
    if b"(" in content:
        if b"\\" in content:
            content = _ESCAPE.sub(b"", content)
        # literal strings nest: peel the innermost level until none is left (a few passes in practice)
        for _ in range(16):
            stripped = _LITERAL.sub(b" ", content)
            if stripped == content:
                break
            content = stripped
    if b"<" in content:
        content = _HEX.sub(b" ", content)
    if b"%" in content:
        content = _COMMENT.sub(b" ", content)
    return content


def content_cost(content: bytes) -> tuple[PageCost, dict[bytes, int]]:
    """Paths and segments drawn by one content stream, and how often it draws each named XObject."""
    text = _strip(content)
    calls: dict[bytes, int] = {}
    for m in _FORM_CALL.finditer(text):
        calls[m.group(1)] = calls.get(m.group(1), 0) + 1
    ops = Counter(_OPERATOR.findall(text))
    return PageCost(sum(ops[o] for o in _PAINT_OPS), sum(ops[o] for o in _SEGMENT_OPS)), calls


def page_cost(doc, page) -> PageCost:
    """What ``page.get_drawings()`` would build, without building it."""
    own, calls = content_cost(page.read_contents() or b"")
    # Form XObjects of the page and of its forms: invoker xref (0 = the page) → {name: xref}
    children: dict[int, dict[bytes, int]] = {}
    for xref, name, invoker, _bbox in page.get_xobjects():
        if doc.xref_get_key(xref, "Subtype")[1] != "/Form":
            continue
        children.setdefault(invoker, {})[name.encode("latin-1", "replace")] = xref
    memo: dict[int, PageCost] = {}

    def form_cost(xref: int, depth: int, path: frozenset[int]) -> PageCost:
        if xref in memo:
            return memo[xref]
        if depth > MAX_FORM_DEPTH or xref in path:
            return PageCost(0, 0)
        cost, form_calls = content_cost(doc.xref_stream(xref) or b"")
        for name, k in form_calls.items():
            child = children.get(xref, {}).get(name)
            if child is not None:
                cost = cost + form_cost(child, depth + 1, path | {xref}).times(k)
        memo[xref] = cost
        return cost

    total = own
    for name, k in calls.items():
        xref = children.get(0, {}).get(name)
        if xref is not None:
            total = total + form_cost(xref, 1, frozenset()).times(k)
    return total


def check_pdf_complexity(doc, *, budget=None, max_bytes: int = MAX_PAGE_BYTES) -> None:
    """Refuse the PDF (``too_complex``) when any page would take more than ``max_bytes`` in
    ``get_drawings()``."""
    for pno in range(doc.page_count):
        if budget is not None:
            budget.check()
        cost = page_cost(doc, doc[pno])
        if cost.bytes > max_bytes:
            raise InputError(
                "too_complex",
                f"page {pno + 1} is too complex to convert: about {cost.paths} paths and "
                f"{cost.segments} segments (limit ~{max_bytes // (1024 * 1024)} MB of drawing data per page)",
            )
