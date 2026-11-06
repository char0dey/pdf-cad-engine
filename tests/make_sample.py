# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Builders for generated test inputs (written to pytest's tmp dirs, not the repo).

`build_sample_plan` is the realistic architectural DXF from the prototype
(polylines, lines, arc, circle, ellipse, spline, TEXT/MTEXT, linear dimensions,
ANSI31 hatch, block inserts, 7 layers, DASHED linetype). The PDF builders create
small vector / scan-like / multi-page / layered PDFs with PyMuPDF.

Run directly to write the sample plan: ``python tests/make_sample.py out.dxf``.
"""

from __future__ import annotations

import sys
from pathlib import Path

A4_W, A4_H = 595.28, 841.89  # pt


# -- DXF ----------------------------------------------------------------------------------
def build_sample_plan(out: Path, *, fmt: str = "asc") -> Path:
    import ezdxf
    from ezdxf import units
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new("R2010", setup=True)
    doc.units = units.MM
    msp = doc.modelspace()

    doc.layers.add("WALLS", color=7, lineweight=50)
    doc.layers.add("DOORS", color=1)
    doc.layers.add("TEXT", color=3)
    doc.layers.add("DIM", color=4)
    doc.layers.add("HATCH", color=8)
    doc.layers.add("HIDDEN", color=5, linetype="DASHED")
    doc.layers.add("FURNITURE", color=6)

    # outer + inner wall outlines (10 m x 6 m, 200 mm walls)
    msp.add_lwpolyline([(0, 0), (10000, 0), (10000, 6000), (0, 6000)], close=True,
                       dxfattribs={"layer": "WALLS"})
    msp.add_lwpolyline([(200, 200), (9800, 200), (9800, 5800), (200, 5800)], close=True,
                       dxfattribs={"layer": "WALLS"})
    # partition wall with a door opening
    msp.add_line((5000, 200), (5000, 2500), dxfattribs={"layer": "WALLS"})
    msp.add_line((5000, 3400), (5000, 5800), dxfattribs={"layer": "WALLS"})
    # door leaf + swing arc
    msp.add_line((5000, 2500), (5900, 2500), dxfattribs={"layer": "DOORS"})
    msp.add_arc(center=(5000, 2500), radius=900, start_angle=0, end_angle=90, dxfattribs={"layer": "DOORS"})
    # column
    msp.add_circle((2500, 3000), 300, dxfattribs={"layer": "WALLS"})
    # ellipse (table)
    msp.add_ellipse((7500, 4500), major_axis=(800, 0), ratio=0.5, dxfattribs={"layer": "FURNITURE"})
    # spline (free-form path)
    msp.add_spline(fit_points=[(1000, 1000), (2000, 1800), (3000, 1200), (4000, 2000)],
                   dxfattribs={"layer": "HIDDEN"})
    # dashed hidden line
    msp.add_line((200, 4000), (4800, 4000), dxfattribs={"layer": "HIDDEN"})
    # text
    msp.add_text("KITCHEN 12.5 m²", height=250, dxfattribs={"layer": "TEXT"}).set_placement(
        (2500, 5000), align=TextEntityAlignment.MIDDLE_CENTER
    )
    mtext = msp.add_mtext("Living room\nArea: 28.8 m²", dxfattribs={"char_height": 250, "layer": "TEXT"})
    mtext.set_location((6000, 3500))
    msp.add_text("Scale 1:100", height=180, dxfattribs={"layer": "TEXT", "rotation": 90}).set_placement(
        (-1500, 3000)
    )
    # dimensions (readable text and arrows at 1:1 mm)
    dim_override = {"dimtxt": 200, "dimasz": 150, "dimexe": 100, "dimexo": 100, "dimgap": 50}
    msp.add_linear_dim(base=(0, -800), p1=(0, 0), p2=(10000, 0), dimstyle="EZDXF", override=dim_override,
                       dxfattribs={"layer": "DIM"}).render()
    msp.add_linear_dim(base=(-800, 0), p1=(0, 0), p2=(0, 6000), angle=90, dimstyle="EZDXF",
                       override=dim_override, dxfattribs={"layer": "DIM"}).render()
    # hatch
    hatch = msp.add_hatch(color=8, dxfattribs={"layer": "HATCH"})
    hatch.set_pattern_fill("ANSI31", scale=50)
    hatch.paths.add_polyline_path([(7000, 500), (9500, 500), (9500, 1800), (7000, 1800)], is_closed=True)
    # block with 3 inserts
    blk = doc.blocks.new("CHAIR")
    blk.add_lwpolyline([(0, 0), (450, 0), (450, 450), (0, 450)], close=True)
    blk.add_arc(center=(225, 450), radius=225, start_angle=0, end_angle=180)
    for i in range(3):
        msp.add_blockref("CHAIR", (6000 + i * 600, 800), dxfattribs={"layer": "FURNITURE"})

    doc.saveas(out, fmt=fmt)
    return out


def build_empty_dxf(out: Path) -> Path:
    import ezdxf

    ezdxf.new("R2010").saveas(out)
    return out


def build_hidden_layer_dxf(out: Path) -> Path:
    """A visible circle plus a red line on layer CONSTRUCTION, which is switched off."""
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.layers.add("CONSTRUCTION", color=1).off()
    msp = doc.modelspace()
    msp.add_circle((0, 0), 1000)
    msp.add_line((-1000, -1000), (1000, 1000), dxfattribs={"layer": "CONSTRUCTION", "color": 1})
    doc.saveas(out)
    return out


def build_white_dxf(out: Path) -> Path:
    """Lines in ACI 7 and in true-colour white: both must print black on white paper."""
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_line((0, 0), (1000, 0), dxfattribs={"color": 7})
    msp.add_line((0, 200), (1000, 200), dxfattribs={"true_color": ezdxf.rgb2int((255, 255, 255))})
    doc.saveas(out)
    return out


def build_big_extent_dxf(out: Path) -> Path:
    """A 45 m x 30 m site plan (few entities) -> A3 landscape."""
    import ezdxf
    from ezdxf import units

    doc = ezdxf.new("R2010")
    doc.units = units.MM
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (45000, 0), (45000, 30000), (0, 30000)], close=True)
    msp.add_line((0, 0), (45000, 30000))
    doc.saveas(out)
    return out


def truncate_file(src: Path, out: Path, ratio: float) -> Path:
    data = src.read_bytes()
    out.write_bytes(data[: int(len(data) * ratio)])
    return out


# -- PDF ----------------------------------------------------------------------------------
def build_three_page_pdf(out: Path) -> Path:
    """3 A4 pages with the same geometry in red / green / blue, plus a page label."""
    import pymupdf

    doc = pymupdf.open()
    colors = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    for i, color in enumerate(colors):
        page = doc.new_page(width=A4_W, height=A4_H)
        page.draw_rect(pymupdf.Rect(100, 100, 200, 150), color=color, width=1)
        page.draw_line((100, 300), (400, 300), color=color, width=0.5)
        page.draw_bezier((100, 400), (150, 350), (250, 350), (300, 400), color=color)
        page.insert_text((100, 500), f"PAGE {i + 1}", fontsize=12, color=color)
    doc.save(out)
    return out


def build_cad_like_pdf(out: Path) -> Path:
    """Spec scenario "plan from a CAD export": wall polylines, door arc (one Bézier),
    column circle, real text "KITCHEN 12.5 m²", hatch lines."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    shape = page.new_shape()
    shape.draw_polyline([(50, 50), (750, 50), (750, 500), (50, 500)])
    shape.finish(color=(0, 0, 0), width=1.5, closePath=True)
    shape.draw_bezier((400, 300), (400, 255.8), (435.8, 220), (480, 220))
    shape.finish(color=(1, 0, 0), width=0.25)
    shape.draw_circle((200, 200), 30)
    shape.finish(color=(0, 0, 0), width=0.25)
    for k in range(10):  # hatch lines
        shape.draw_line((550 + k * 15, 480), (600 + k * 15, 400))
    shape.finish(color=(0.5, 0.5, 0.5), width=0.25)
    shape.commit()
    font = pymupdf.Font("helv")
    writer = pymupdf.TextWriter(page.rect)
    writer.append((100, 120), "KITCHEN 12.5 m²", font=font, fontsize=14)
    writer.write_text(page, color=(0, 0.5, 0))
    doc.save(out)
    return out


def build_form_pdf(out: Path, pages: int = 4, labels_per_page: int = 25) -> Path:
    """A 4-page form: label texts (dark blue / red) and field frames."""
    import pymupdf

    doc = pymupdf.open()
    for p in range(pages):
        page = doc.new_page(width=A4_W, height=A4_H)
        for i in range(labels_per_page):
            y = 60 + i * 30
            color = (0.8, 0, 0) if i % 5 == 0 else (0, 0, 0.4)
            page.insert_text((50, y), f"Field {p + 1}.{i + 1}", fontsize=9, color=color)
            page.draw_rect(pymupdf.Rect(200, y - 12, 540, y + 4), color=(0, 0, 0), width=0.5)
    doc.save(out)
    return out


def build_ocg_pdf(out: Path) -> Path:
    """Optional-content layers WALLS / DOORS / TEXT like an AutoCAD PDF export,
    plus CONSTRUCTION, which is switched off in the PDF."""
    import pymupdf

    doc = pymupdf.open()
    walls = doc.add_ocg("WALLS")
    doors = doc.add_ocg("DOORS")
    text = doc.add_ocg("TEXT")
    construction = doc.add_ocg("CONSTRUCTION", on=False)
    page = doc.new_page(width=A4_W, height=A4_H)
    page.draw_line((0, 0), (500, 500), color=(0.5, 0.5, 0.5), oc=construction)
    page.draw_rect(pymupdf.Rect(50, 50, 500, 400), color=(0, 0, 0), width=1, oc=walls)
    page.draw_bezier((200, 400), (200, 350), (250, 320), (300, 320), color=(1, 0, 0), oc=doors)
    page.insert_text((100, 200), "LIVING ROOM", fontsize=12, color=(0, 0.6, 0), oc=text)
    doc.save(out)
    return out


def build_lineweight_pdf(out: Path) -> Path:
    """Lines of 0.25 pt (red) and 1.5 pt (blue)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_W, height=A4_H)
    page.draw_line((50, 100), (500, 100), color=(1, 0, 0), width=0.25)
    page.draw_line((50, 200), (500, 200), color=(0, 0, 1), width=1.5)
    doc.save(out)
    return out


def build_rotated_pdf(out: Path) -> Path:
    """A portrait page with /Rotate 90 (displayed landscape) and one horizontal line."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_W, height=A4_H)
    page.draw_line((10, 10), (100, 10), color=(0, 0, 0))
    page.insert_text((50, 50), "ROT", fontsize=10)
    page.set_rotation(90)
    doc.save(out)
    return out


def build_invisible_text_pdf(out: Path) -> Path:
    """Scan with an OCR text layer: an image plus invisible text only."""
    import pymupdf

    doc = pymupdf.open(build_scan_pdf(out.with_suffix(".scan.pdf")))
    doc[0].insert_text((100, 100), "OCR TEXT LAYER", fontsize=12, render_mode=3)
    doc.save(out)
    return out


def _noise_png(width: int = 300, height: int = 400) -> bytes:
    import numpy as np
    import pymupdf

    rng = np.random.default_rng(42)
    data = rng.integers(0, 256, size=(height, width, 3), dtype=np.uint8)
    data[100:110, :, :] = 0  # a "drawn" line in the scan
    pix = pymupdf.Pixmap(pymupdf.csRGB, width, height, data.tobytes(), False)
    return pix.tobytes("png")


def build_scan_pdf(out: Path) -> Path:
    """One page that only contains an embedded image (a scanned drawing)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_W, height=A4_H)
    page.insert_image(page.rect, stream=_noise_png())
    doc.save(out)
    return out


def build_many_pages_pdf(out: Path, pages: int = 80) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=A4_W, height=A4_H)
        page.draw_line((50, 50), (300, 300))
    doc.save(out)
    return out


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "sample_plan.dxf")
    build_sample_plan(target)
    print(target)
