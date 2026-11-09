# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Builders for generated test inputs (written to pytest's tmp dirs, not the repo).

`build_sample_plan` is the realistic architectural DXF from the prototype
(polylines, lines, arc, circle, ellipse, spline, TEXT/MTEXT, linear dimensions,
ANSI31 hatch, block inserts, 7 layers, DASHED linetype); `build_layouts_dxf` adds two
paper-space layouts with viewports. The PDF builders create small vector / scan-like /
multi-page / layered PDFs with PyMuPDF, plus the phase-2 inputs (circles and arcs,
fills, dashed runs, duplicates, scale, scanned drawing, mixed vector + scan) and the
engine 1.2 drawings with external content (`build_refs_dxf`: xrefs, underlays, images,
AutoCAD Architecture classes).

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


def build_layouts_dxf(out: Path) -> Path:
    """The sample plan plus paper-space layouts "Plan A3" (A3 landscape, one viewport on the
    whole plan) and "Detail" (A4 landscape, a viewport on the door and a TEXT title);
    the default "Layout1" stays empty."""
    import ezdxf

    plan = build_sample_plan(out.with_suffix(".plan.dxf"))
    doc = ezdxf.readfile(plan)
    sheet = doc.layouts.new("Plan A3")
    sheet.page_setup(size=(420, 297), margins=(10, 10, 10, 10), units="mm")
    sheet.add_viewport(center=(210, 148.5), size=(380, 250), view_center_point=(5000, 3000), view_height=7000)
    detail = doc.layouts.new("Detail")
    detail.page_setup(size=(297, 210), margins=(10, 10, 10, 10), units="mm")
    detail.add_viewport(center=(148.5, 105), size=(250, 170), view_center_point=(5000, 2500),
                        view_height=2500)
    detail.add_text("DETAIL", height=5).set_placement((20, 20))
    doc.saveas(out)
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


# -- missing content (engine 1.2) ---------------------------------------------------------
def add_aec_classes(path: Path, names=("AEC_WALL", "AEC_DOOR"), *, objects=("AECB_DISP_PROPS_DUCT",)) -> Path:
    """Insert CLASS records of AutoCAD vertical products into the CLASSES section of an ASCII
    DXF (ezdxf writes only the classes it knows): ``names`` as entity classes (281 = 1),
    ``objects`` as non-entity classes (281 = 0), both with instance count 0 as ACadSharp
    writes them."""
    text = path.read_text(encoding="utf-8")
    marker = "  2\nCLASSES\n"
    assert marker in text, "no CLASSES section"

    def record(name: str, is_entity: int) -> str:
        return (f"  0\nCLASS\n  1\n{name}\n  2\nAecDb{name.split('_', 1)[1].title()}\n"
                f"  3\nAecArchBase|Product Desc:     Autodesk Architecture\n 90\n4095\n 91\n0\n"
                f"280\n0\n281\n{is_entity}\n")

    records = "".join(record(n, 1) for n in names) + "".join(record(n, 0) for n in objects)
    path.write_text(text.replace(marker, marker + records, 1), encoding="utf-8")
    return path


def build_refs_dxf(out: Path, *, xrefs=(), overlays=(), unused_xrefs=(), bound=(), nested_xrefs=(),
                   underlays=(), images=(), image_inserts: int = 1, aec=(), lines: bool = True,
                   fmt: str = "asc") -> Path:
    """A drawing with external content: ``xrefs`` / ``overlays`` / ``nested_xrefs`` are
    ``(block name, path)`` pairs inserted into model space (nested: inside a block FRAME that
    model space inserts), ``unused_xrefs`` are defined but never inserted, ``bound`` are
    ordinary blocks (a bound xref) inserted into model space; ``underlays`` are PDF underlay
    paths, ``images`` raster image paths (each inserted ``image_inserts`` times); ``aec``
    adds AutoCAD Architecture entity classes (ASCII only); ``lines`` adds a line to draw."""
    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    if lines:
        msp.add_line((0, 0), (1000, 500))
    x = 0
    for name, path in xrefs:
        doc.add_xref_def(path, name)
        msp.add_blockref(name, (x, 0))
        x += 100
    for name, path in overlays:
        doc.add_xref_def(path, name, flags=4 | 8 | 16)
        msp.add_blockref(name, (x, 0))
        x += 100
    for name, path in unused_xrefs:
        doc.add_xref_def(path, name)
    for name in bound:
        blk = doc.blocks.new(name)
        blk.add_circle((0, 0), 50)
        msp.add_blockref(name, (x, 0))
        x += 100
    if nested_xrefs:
        frame = doc.blocks.new("FRAME")
        for name, path in nested_xrefs:
            doc.add_xref_def(path, name)
            frame.add_blockref(name, (0, 0))
        msp.add_blockref("FRAME", (x, 0))
    for i, path in enumerate(underlays):
        underlay_def = doc.add_underlay_def(path, fmt="pdf", name=str(i + 1))
        msp.add_underlay(underlay_def, insert=(i * 300, 0), scale=1.0)
    for path in images:
        image_def = doc.add_image_def(filename=path, size_in_pixel=(640, 480))
        for i in range(image_inserts):
            msp.add_image(image_def, insert=(i * 700, 1000), size_in_units=(640, 480))
    doc.saveas(out, fmt=fmt)
    if aec:
        add_aec_classes(out, aec)
    return out


def build_underlay_only_dxf(out: Path) -> Path:
    """Model space holds only three PDF underlays (committed as tests/fixtures/underlay-only.dxf)."""
    return build_refs_dxf(out, underlays=(r"..\pdf\site-plan.pdf", r"..\pdf\site-plan.pdf",
                                          r"C:\Projects\Client\survey.pdf"), lines=False)


def build_heavy_dxf(out: Path, *, lines: int = 20_000, hatches: int = 0, hatch_scale: float = 0.2) -> Path:
    """The pattern of the drawings that ran out of the background budget on 2026-11-09 (engine 1.2,
    design D8), generated: ``lines`` short lines as inserts of a 50-line block over 20 layers, plus
    ``hatches`` small ANSI31 hatches at ``hatch_scale`` (dense pattern lines)."""
    import random

    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    rnd = random.Random(7)
    for i in range(20):
        doc.layers.add(f"L{i}", color=i % 7 + 1)
    part = doc.blocks.new("PART")
    for i in range(50):
        x, y = rnd.uniform(0, 1000), rnd.uniform(0, 1000)
        end = (x + rnd.uniform(-80, 80), y + rnd.uniform(-80, 80))
        part.add_line((x, y), end, dxfattribs={"layer": f"L{i % 20}"})
    for i in range(lines // 50):
        msp.add_blockref("PART", ((i % 40) * 1100, (i // 40) * 1100))
    for i in range(hatches):
        hatch = msp.add_hatch(color=i % 7 + 1, dxfattribs={"layer": f"L{i % 20}"})
        hatch.set_pattern_fill("ANSI31", scale=hatch_scale)
        x, y = (i % 20) * 120, (i // 20) * 120 - 5000
        square = [(x, y), (x + 100, y), (x + 100, y + 100), (x, y + 100)]
        hatch.paths.add_polyline_path(square, is_closed=True)
    doc.saveas(out)
    return out


def build_text_heavy_dxf(out: Path, *, texts: int = 2_000) -> Path:
    """Many TEXT entities plus a few lines and circles: text is drawn as glyph outlines, i.e.
    curves — the stress case of the 40 MB TEST run on 2026-11-09 (engine 1.2.1)."""
    import random

    import ezdxf

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    rnd = random.Random(3)

    def spot() -> tuple[float, float]:
        return rnd.uniform(0, 30000), rnd.uniform(0, 30000)

    for i in range(texts):
        msp.add_text(f"Room {i} 12.5 m²", height=200).set_placement(spot())
        if i % 4 == 0:
            msp.add_line(spot(), spot())
    for _ in range(100):
        msp.add_circle((rnd.uniform(0, 30000), rnd.uniform(0, 30000)), 300)
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


def build_scan_drawing_pdf(out: Path, dpi: int = 100) -> Path:
    """A scanned drawing: a frame, a circle and a diagonal drawn in black 2 pt lines,
    rasterised in grey at `dpi` and placed as the only content of an A4 landscape page."""
    import pymupdf

    src = pymupdf.open()
    page = src.new_page(width=A4_H, height=A4_W)
    page.draw_rect(pymupdf.Rect(60, 60, 780, 530), color=(0, 0, 0), width=2)
    page.draw_circle((250, 250), 60, color=(0, 0, 0), width=2)
    page.draw_line((400, 450), (700, 150), color=(0, 0, 0), width=2)
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    doc = pymupdf.open()
    scan = doc.new_page(width=A4_H, height=A4_W)
    scan.insert_image(scan.rect, stream=pix.tobytes("png"))
    doc.save(out)
    return out


def build_mixed_pdf(out: Path) -> Path:
    """Page 1: vector lines and text; page 2: a scanned drawing (image only)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_H, height=A4_W)
    page.draw_rect(pymupdf.Rect(60, 60, 780, 530), color=(0, 0, 1), width=1)
    page.insert_text((100, 100), "VECTOR PAGE", fontsize=12)
    doc.insert_pdf(pymupdf.open(build_scan_drawing_pdf(out.with_suffix(".scan.pdf"))))
    doc.save(out)
    return out


def _arc_bezier(cx: float, cy: float, r: float, a0: float, a1: float):
    """Control points (page coordinates, y down) of a cubic Bézier for the arc a0 -> a1 (deg)."""
    import math

    a, b = math.radians(a0), math.radians(a1)
    k = 4.0 / 3.0 * math.tan((b - a) / 4.0)
    p0 = (cx + r * math.cos(a), cy - r * math.sin(a))
    p3 = (cx + r * math.cos(b), cy - r * math.sin(b))
    p1 = (p0[0] - k * r * math.sin(a), p0[1] - k * r * math.cos(a))
    p2 = (p3[0] + k * r * math.sin(b), p3[1] + k * r * math.cos(b))
    return p0, p1, p2, p3


def build_circles_pdf(out: Path) -> Path:
    """Spec scenarios for --arcs on an A4 landscape page:
    a circle r = 50 pt as 4 Béziers (red), a 90° arc as 1 Bézier (blue, counter-clockwise from
    0° to 90° in DXF terms), a 180° arc as 2 Béziers (green), a 2:1 ellipse as 4 Béziers (black),
    and an S-curve of 2 Béziers that is no arc (magenta)."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_H, height=A4_W)
    shape = page.new_shape()
    shape.draw_circle((150, 150), 50)
    shape.finish(color=(1, 0, 0), width=0.5, closePath=False)
    shape.draw_bezier(*_arc_bezier(350, 200, 60, 0, 90))
    shape.finish(color=(0, 0, 1), width=0.5, closePath=False)
    first = _arc_bezier(550, 200, 40, 0, 90)
    second = _arc_bezier(550, 200, 40, 90, 180)
    shape.draw_bezier(*first)
    shape.draw_bezier(*second)
    shape.finish(color=(0, 0.6, 0), width=0.5, closePath=False)
    shape.draw_oval(pymupdf.Rect(100, 350, 300, 450))
    shape.finish(color=(0, 0, 0), width=0.5, closePath=False)
    shape.draw_bezier((400, 400), (450, 350), (500, 350), (550, 400))
    shape.draw_bezier((550, 400), (600, 450), (650, 450), (700, 400))
    shape.finish(color=(1, 0, 1), width=0.5, closePath=False)
    shape.commit()
    doc.save(out)
    return out


def build_fills_pdf(out: Path) -> Path:
    """--hatch-fills: a red fill-only path with a rectangle and a circle (2 loops), a blue
    even-odd square with a square hole, and a green fill + black stroke rectangle."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_H, height=A4_W)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(50, 50, 150, 120))
    shape.draw_circle((250, 85), 35)
    shape.finish(fill=(1, 0, 0), color=None)
    shape.draw_rect(pymupdf.Rect(50, 200, 250, 400))
    shape.draw_rect(pymupdf.Rect(100, 250, 200, 350))
    shape.finish(fill=(0, 0, 1), color=None, even_odd=True)
    shape.draw_rect(pymupdf.Rect(400, 200, 600, 400))
    shape.finish(fill=(0, 1, 0), color=(0, 0, 0), width=1)
    shape.commit()
    doc.save(out)
    return out


MM = 72.0 / 25.4  # pt per mm


def build_dashes_pdf(out: Path) -> Path:
    """--linetypes on an A4 landscape page (lengths in paper mm):
    - red: 40 dashes of 3 mm with 2 mm gaps in one path (spec scenario) -> one DASHED LINE;
    - green: 30 dots of 0.3 mm with 1.2 mm gaps -> one DOT LINE;
    - blue: a line stroked with the PDF dash array [6 3] -> DASHED polyline;
    - black: 5 collinear segments with irregular gaps, and 3 regular dashes (too few) -> unchanged."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_H, height=A4_W)
    shape = page.new_shape()
    x0, y = 20 * MM, 30 * MM
    for i in range(40):
        x = x0 + i * 5 * MM
        shape.draw_line((x, y), (x + 3 * MM, y))
    shape.finish(color=(1, 0, 0), width=0.5, closePath=False)
    y = 50 * MM
    for i in range(30):
        x = x0 + i * 1.5 * MM
        shape.draw_line((x, y), (x + 0.3 * MM, y))
    shape.finish(color=(0, 1, 0), width=0.5, closePath=False)
    shape.commit()
    page.draw_line((x0, 70 * MM), (x0 + 150 * MM, 70 * MM), color=(0, 0, 1), width=0.5, dashes="[6 3] 0")
    shape = page.new_shape()
    xs = [0, 4, 9, 30, 33, 60, 62, 90, 100, 101]
    for a, b in zip(xs[0::2], xs[1::2], strict=True):
        shape.draw_line((x0 + a * MM, 90 * MM), (x0 + b * MM, 90 * MM))
    for i in range(3):
        shape.draw_line((x0 + i * 5 * MM, 110 * MM), (x0 + (i * 5 + 3) * MM, 110 * MM))
    shape.finish(color=(0, 0, 0), width=0.5, closePath=False)
    shape.commit()
    doc.save(out)
    return out


def build_merge_pdf(out: Path) -> Path:
    """--merge: the same red line drawn twice, the same line once more in blue (not a
    duplicate), a black polyline with collinear vertices, and a green S-curve of 3 Béziers."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_H, height=A4_W)
    for color in ((1, 0, 0), (1, 0, 0), (0, 0, 1)):
        page.draw_line((50, 50), (300, 50), color=color, width=0.5)
    page.draw_polyline([(50, 100), (100, 100), (150, 100), (200, 100), (200, 200), (200, 250)],
                       color=(0, 0, 0), width=0.5, closePath=False)
    shape = page.new_shape()
    shape.draw_bezier((50, 300), (80, 250), (120, 250), (150, 300))
    shape.draw_bezier((150, 300), (180, 350), (220, 350), (250, 300))
    shape.draw_bezier((250, 300), (280, 250), (320, 250), (350, 300))
    shape.finish(color=(0, 0.5, 0), width=0.5, closePath=False)
    shape.commit()
    doc.save(out)
    return out


def build_scale_pdf(out: Path) -> Path:
    """--scale / --units: a black line 100 mm long and a red line 72 pt long, text 10 pt."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=A4_W, height=A4_H)
    page.draw_line((50, 100), (50 + 100 * MM, 100), color=(0, 0, 0), width=0.5)
    page.draw_line((50, 200), (122, 200), color=(1, 0, 0), width=0.5)
    page.insert_text((50, 300), "SCALE", fontsize=10)
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
