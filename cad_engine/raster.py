# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Raster fallback for pdf2dxf ``--raster-fallback``: outlines traced from image-only pages.

The page is rendered in grayscale (150 dpi), binarised (see `binarize`), and the
outlines of the dark areas are found with OpenCV (``findContours``) and simplified
(``approxPolyDP``, 1 px); specks with an outline shorter than 8 px are dropped.
Lines of a scanned drawing therefore come out as thin closed
outlines around each stroke, not as centre lines; the result is marked as traced in the
meta (``traced_pages``) and makes no claim to vector precision.

OpenCV (``opencv-python-headless``) is an optional dependency (extra ``[raster]``):
`load_cv2` returns None without it and the caller reports a warning instead of failing.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from cad_engine.budget import Budget, Ticker

if TYPE_CHECKING:
    import pymupdf

TRACE_DPI = 150
MAX_PIXELS = 40_000_000  # very large pages are rendered at a lower resolution
EPSILON_PX = 1.0  # approxPolyDP tolerance
MIN_PERIMETER_PX = 8.0  # specks below this outline length are scanner noise
PAPER_MARGIN = 12.0  # grey levels kept between the threshold and the paper


def load_cv2():
    """The cv2 module (one thread, like the rest of the engine) or None if not installed."""
    try:
        import cv2
    except ImportError:
        return None
    try:
        cv2.setNumThreads(1)
    except Exception:  # pragma: no cover - very old builds
        pass
    return cv2


def binarize(gray, cv2):
    """Ink -> 255, paper -> 0.

    The threshold sits halfway between Otsu's ink/paper split and the paper level (the
    median grey of the page, which is mostly paper), so thin anti-aliased lines that come
    out light grey are kept while the paper texture of a scan stays background.
    """
    import numpy as np

    otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    paper = float(np.median(gray))
    threshold = otsu
    if paper > otsu:
        threshold = max(otsu, min((otsu + paper) / 2.0, paper - PAPER_MARGIN))
    _, binary = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)
    return binary


def trace_page(page: pymupdf.Page, cv2, budget: Budget) -> list[list[tuple[float, float]]]:
    """Closed outlines of the dark areas of a page, in display (rotated) page coordinates (pt)."""
    import numpy as np
    import pymupdf

    rect = page.rect
    area_in2 = max(rect.width * rect.height, 1.0) / (72.0 * 72.0)
    dpi = min(TRACE_DPI, int(math.sqrt(MAX_PIXELS / area_in2)))
    pix = page.get_pixmap(dpi=max(dpi, 10), colorspace=pymupdf.csGRAY, alpha=False)
    gray = np.ascontiguousarray(
        np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, : pix.width]
    )
    budget.check()
    binary = binarize(gray, cv2)
    found = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = found[0] if len(found) == 2 else found[1]  # OpenCV 3 returned (image, contours, h)
    budget.check()
    sx = rect.width / pix.width
    sy = rect.height / pix.height
    x0, y0 = rect.x0, rect.y0
    ticker = Ticker(budget, every=2000)
    out: list[list[tuple[float, float]]] = []
    for contour in contours:
        ticker.tick()
        if cv2.arcLength(contour, True) < MIN_PERIMETER_PX:
            continue
        approx = cv2.approxPolyDP(contour, EPSILON_PX, True).reshape(-1, 2)
        if len(approx) < 2:
            continue
        out.append([(x0 + (float(x) + 0.5) * sx, y0 + (float(y) + 0.5) * sy) for x, y in approx])
    return out
