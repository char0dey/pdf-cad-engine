# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""PNG preview of the geometry extracted by pdf2dxf.

The preview is drawn from the same primitives that were written to the DXF
(not by re-rendering the DXF, which takes tens of seconds for large drawings):
vectors go into one raw PDF content stream of a scratch PyMuPDF page sized to the
content extents, texts are added on top, and MuPDF rasterises the page once.
White strokes are drawn black, as a CAD viewer on a white background would.
"""

from __future__ import annotations

import math

PT2MM = 25.4 / 72.0
LONGEST_SIDE_PX = 1400
MARGIN_RATIO = 0.02
MIN_STROKE_PX = 0.6
MIN_TEXT_PX = 0.5
SUBPATHS_PER_STROKE = 2000
_FONT_RES = "CEHelv"
_RESOURCES = (
    f"<</Font<</{_FONT_RES}<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>>>>>"
)

Style = tuple[tuple[int, int, int], float]  # (rgb 0..255, stroke width in pt)


def display_rgb(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    if rgb[0] >= 240 and rgb[1] >= 240 and rgb[2] >= 240:
        return (0.0, 0.0, 0.0)
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)


def _pdf_string(data: bytes) -> str:
    out = []
    for byte in data:
        if byte in (0x28, 0x29, 0x5C):
            out.append("\\" + chr(byte))
        elif 32 <= byte < 127:
            out.append(chr(byte))
        else:
            out.append(f"\\{byte:03o}")
    return "".join(out)


class PreviewRecorder:
    """Collects primitives in DXF coordinates (mm, y up) grouped by stroke style."""

    def __init__(self) -> None:
        self.paths: dict[Style, list[tuple[list[tuple[float, float]], bool | None]]] = {}
        self.texts: list[tuple[tuple[float, float], str, float, float, tuple[int, int, int]]] = []

    def polyline(self, style: Style, pts: list[tuple[float, float]], closed: bool) -> None:
        self.paths.setdefault(style, []).append((pts, closed))

    def bezier(self, style: Style, cps: list[tuple[float, float]]) -> None:
        self.paths.setdefault(style, []).append((cps, None))

    def text(self, insert: tuple[float, float], text: str, size_mm: float, rotation: float,
             rgb: tuple[int, int, int]) -> None:
        self.texts.append((insert, text, size_mm, rotation, rgb))

    def render_png(self, bounds: tuple[float, float, float, float],
                   longest_px: int = LONGEST_SIDE_PX) -> bytes:
        import pymupdf

        min_x, min_y, max_x, max_y = bounds
        if not all(math.isfinite(v) for v in bounds) or min_x > max_x or min_y > max_y:
            min_x, min_y, max_x, max_y = 0.0, 0.0, 1.0, 1.0
        w = max(max_x - min_x, 1e-6)
        h = max(max_y - min_y, 1e-6)
        margin = max(w, h) * MARGIN_RATIO
        scale = longest_px / (max(w, h) + 2 * margin)  # px (= pt at 72 dpi) per mm
        page_w = max(1, round((w + 2 * margin) * scale))
        page_h = max(1, round((h + 2 * margin) * scale))
        ox = min_x - margin
        oy = min_y - margin

        doc = pymupdf.open()
        page = doc.new_page(width=page_w, height=page_h)
        text_stream, unicode_texts = self._text_stream(ox, oy, scale)
        stream = self._vector_stream(ox, oy, scale) + text_stream
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, stream.encode("ascii"))
        doc.xref_set_key(page.xref, "Contents", f"{xref} 0 R")
        doc.xref_set_key(page.xref, "Resources", _RESOURCES)
        page = doc.reload_page(page)
        self._unicode_text(page, unicode_texts, ox, oy, scale, page_h)
        pix = page.get_pixmap(alpha=False)
        return pix.tobytes("png")

    # -- vectors -------------------------------------------------------------------------
    def _vector_stream(self, ox: float, oy: float, s: float) -> str:
        out = ["q 1 J 1 j\n"]
        for (rgb, width_pt), items in self.paths.items():
            r, g, b = display_rgb(rgb)
            lw = max(width_pt * PT2MM * s, MIN_STROKE_PX)
            out.append(f"{r:.3f} {g:.3f} {b:.3f} RG {lw:.3f} w\n")
            pending = 0
            for pts, closed in items:
                if closed is None:
                    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
                    out.append(
                        f"{(x0 - ox) * s:.2f} {(y0 - oy) * s:.2f} m "
                        f"{(x1 - ox) * s:.2f} {(y1 - oy) * s:.2f} {(x2 - ox) * s:.2f} {(y2 - oy) * s:.2f} "
                        f"{(x3 - ox) * s:.2f} {(y3 - oy) * s:.2f} c\n"
                    )
                else:
                    x, y = pts[0]
                    seg = [f"{(x - ox) * s:.2f} {(y - oy) * s:.2f} m"]
                    seg.extend(f"{(x - ox) * s:.2f} {(y - oy) * s:.2f} l" for x, y in pts[1:])
                    if closed:
                        seg.append("h")
                    seg.append("\n")
                    out.append(" ".join(seg))
                pending += 1
                if pending >= SUBPATHS_PER_STROKE:
                    out.append("S\n")
                    pending = 0
            if pending:
                out.append("S\n")
        out.append("Q\n")
        return "".join(out)

    # -- texts ---------------------------------------------------------------------------
    def _text_stream(self, ox: float, oy: float, s: float) -> tuple[str, list]:
        """Texts that fit WinAnsi go straight into the content stream (fast path);
        the rest is returned for `_unicode_text`."""
        out = []
        unicode_texts: list = []
        for (x, y), text, size_mm, rotation, rgb in self.texts:
            size_px = size_mm * s
            if size_px < MIN_TEXT_PX:
                continue
            try:
                data = text.encode("cp1252")
            except UnicodeEncodeError:
                unicode_texts.append(((x, y), text, size_px, rotation, rgb))
                continue
            r, g, b = display_rgb(rgb)
            rad = math.radians(rotation)
            c, sn = math.cos(rad), math.sin(rad)
            out.append(
                f"BT /{_FONT_RES} {size_px:.2f} Tf {r:.3f} {g:.3f} {b:.3f} rg "
                f"{c:.4f} {sn:.4f} {-sn:.4f} {c:.4f} {(x - ox) * s:.2f} {(y - oy) * s:.2f} Tm "
                f"({_pdf_string(data)}) Tj ET\n"
            )
        return "".join(out), unicode_texts

    @staticmethod
    def _unicode_text(page, texts: list, ox: float, oy: float, s: float, page_h: float) -> None:
        """Other scripts (Cyrillic, Greek, CJK...) via TextWriter with Unicode fonts (slower)."""
        if not texts:
            return
        import pymupdf

        fonts: dict[str, pymupdf.Font] = {}

        def font_for(text: str):
            name = "cjk" if any(ord(ch) >= 0x2E80 for ch in text) else "helv"
            if name not in fonts:
                fonts[name] = pymupdf.Font(name)
            return fonts[name]

        writers: dict[tuple[float, float, float], pymupdf.TextWriter] = {}
        for (x, y), text, size_px, rotation, rgb in texts:
            pos = pymupdf.Point((x - ox) * s, page_h - (y - oy) * s)
            color = display_rgb(rgb)
            try:
                if rotation:
                    tw = pymupdf.TextWriter(page.rect)
                    tw.append(pos, text, font=font_for(text), fontsize=size_px)
                    tw.write_text(page, color=color, morph=(pos, pymupdf.Matrix(rotation)))
                    continue
                tw = writers.get(color)
                if tw is None:
                    tw = writers[color] = pymupdf.TextWriter(page.rect)
                tw.append(pos, text, font=font_for(text), fontsize=size_px)
            except (ValueError, RuntimeError):
                continue  # an unprintable span must not break the preview
        for color, tw in writers.items():
            tw.write_text(page, color=color)
