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


def build_broken_insert_dxf(out: Path) -> Path:
    """A line and a block of a circle, plus three block references that point at no block: one without
    a name in model space, one naming an undefined block, one without a name inside the block definition
    (what dwgdxf 1.0.5 wrote from a DWG on PROD 2026-11-12). ezdxf writes only valid ones, so the names
    are cut out of the file text afterwards."""
    import ezdxf

    doc = ezdxf.new("R2018")
    block = doc.blocks.new("B")
    block.add_circle((0, 0), 300)
    doc.blocks.new("GONE").add_line((0, 0), (1, 1))
    doc.blocks.new("UNDEFINED").add_line((0, 0), (1, 1))
    block.add_blockref("GONE", (0, 0))  # nameless in the block definition
    msp = doc.modelspace()
    msp.add_line((-1000, -1000), (1000, 1000))
    msp.add_blockref("B", (500, 0))
    msp.add_blockref("GONE", (0, 500))  # nameless in model space
    msp.add_blockref("UNDEFINED", (0, -500))  # its block is removed below
    doc.saveas(out)
    text = out.read_text(encoding="utf-8")
    nameless = "\nAcDbBlockReference\n  2\nGONE\n"
    assert text.count(nameless) == 2
    text = text.replace(nameless, "\nAcDbBlockReference\n")
    text = text.replace("\nAcDbBlockReference\n  2\nUNDEFINED\n", "\nAcDbBlockReference\n  2\nNOT_A_BLOCK\n")
    out.write_text(text, encoding="utf-8")
    return out


FAR_KINDS = ("plain", "huge", "stray", "in_block", "paper", "near")


def build_far_object_dxf(out: Path, kind: str) -> Path:
    """A small plan (frame, grid, a filled triangle) plus, by ``kind``, one object far from it
    (engine 1.5, far parts):

    - ``plain``: the plan alone, the reference picture;
    - ``huge``: a SOLID with a vertex at x = 1e39 — PROD 2026-11-13 (dwg-to-pdf, exit 5
      «TypeError … new_FzPoint»);
    - ``stray``: the plan in survey coordinates (5e6, 4e6) and a POINT left at the origin;
    - ``in_block``: the plan drawn through a block whose definition also holds a SOLID 1e9 away;
    - ``paper``: the plan on a sheet (paper space, A3 layout) with a LINE end at 1e39 on it;
    - ``near``: a second copy of the plan 50 plan sizes away — far, but a real part of a drawing.
    """
    import ezdxf

    if kind not in FAR_KINDS:
        raise ValueError(kind)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    ox, oy = (5e6, 4e6) if kind == "stray" else (0.0, 0.0)

    def plan(space, dx: float = 0.0, dy: float = 0.0) -> None:
        x, y = ox + dx, oy + dy
        space.add_lwpolyline([(x, y), (x + 400, y), (x + 400, y + 300), (x, y + 300)], close=True)
        for i in range(1, 8):
            space.add_line((x + i * 50, y), (x + i * 50, y + 300))
        space.add_solid([(x + 20, y + 20), (x + 120, y + 20), (x + 20, y + 120)])

    if kind == "in_block":
        block = doc.blocks.new("PLAN")
        plan(block)
        block.add_solid([(1e9, 1e9), (1e9 + 10, 1e9), (1e9, 1e9 + 10)])
        msp.add_blockref("PLAN", (0, 0))
    elif kind == "paper":
        sheet = doc.layouts.new("Sheet")
        sheet.page_setup(size=(420, 297), margins=(10, 10, 10, 10), units="mm")
        plan(sheet, 10, -2)
        sheet.add_line((50, 50), (1e39, 50))
        msp.add_line((0, 0), (100, 100))
    else:
        plan(msp)
        if kind == "huge":
            msp.add_solid([(0, 0), (1e39, 0), (0, 10)])
        elif kind == "stray":
            msp.add_point((0, 0))
        elif kind == "near":
            plan(msp, 50 * 400, 0)
    doc.saveas(out)
    return out


MLEADER_KINDS = ("plain", "in_model", "in_block", "rotated", "nested_paper", "null_vector", "nonuniform", "dogleg")


def build_mleader_dxf(out: Path, kind: str) -> Path:
    """A small plan plus, by ``kind``, a leader (MULTILEADER with text) whose landing (dogleg) has
    length 0 — engine 1.5.1. ezdxf 1.4.5 divides by zero transforming such a leader, i.e. whenever it
    sits in an inserted block: PROD 2026-11-13 (dwg-to-pdf, Civil 3D, exit 5 «ZeroDivisionError» in
    ``mleader.py`` ``LeaderData.transform``).

    - ``plain``: the plan alone;
    - ``in_model``: the leader drawn straight in model space at P = (200, 150), the reference picture;
    - ``in_block``: the same leader in block TAG, inserted at P (the PROD case);
    - ``rotated``: TAG inserted at P rotated 90° with a uniform scale of 2;
    - ``nested_paper``: TAG inside block OUTER inserted in model space, plus TAG on a sheet;
    - ``null_vector``: landing length 0 and landing direction (0, 0, 0);
    - ``nonuniform``: TAG inserted with x scale 2, y scale 1;
    - ``dogleg``: a landing 25 units long, TAG inserted rotated 30° with scale 1.5 (unchanged case).
    """
    import ezdxf
    from ezdxf.math import Vec2, Vec3
    from ezdxf.render.mleader import ConnectionSide

    if kind not in MLEADER_KINDS:
        raise ValueError(kind)
    doc = ezdxf.new("R2018", setup=True)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (400, 0), (400, 300), (0, 300)], close=True)
    for i in range(1, 8):
        msp.add_line((i * 50, 0), (i * 50, 300))
    if kind == "plain":
        doc.saveas(out)
        return out

    def leader(space, at: tuple[float, float]) -> None:
        x, y = at
        ml = space.add_multileader_mtext("Standard")
        ml.set_content("STA 1+00", char_height=12)
        ml.add_leader_line(ConnectionSide.left, [Vec2(x - 90, y - 60)])
        ml.build(insert=Vec2(x, y))
        for data in ml.context.leaders:
            data.dogleg_length = 25.0 if kind == "dogleg" else 0.0
            if kind == "null_vector":
                data.dogleg_vector = Vec3(0, 0, 0)

    p = (200, 150)
    if kind == "in_model":
        leader(msp, p)
        doc.saveas(out)
        return out
    tag = doc.blocks.new("TAG")
    leader(tag, (0, 0))
    if kind == "rotated":
        msp.add_blockref("TAG", p, dxfattribs={"rotation": 90, "xscale": 2, "yscale": 2, "zscale": 2})
    elif kind == "nested_paper":
        outer = doc.blocks.new("OUTER")
        outer.add_blockref("TAG", (0, 0))
        msp.add_blockref("OUTER", p)
        sheet = doc.layouts.new("Sheet")
        sheet.page_setup(size=(420, 297), margins=(10, 10, 10, 10), units="mm")
        sheet.add_lwpolyline([(20, 20), (400, 20), (400, 277), (20, 277)], close=True)
        sheet.add_blockref("TAG", (210, 150))
    elif kind == "nonuniform":
        msp.add_blockref("TAG", p, dxfattribs={"xscale": 2, "yscale": 1})
    elif kind == "dogleg":
        msp.add_blockref("TAG", p, dxfattribs={"rotation": 30, "xscale": 1.5, "yscale": 1.5, "zscale": 1.5})
    else:  # in_block, null_vector
        msp.add_blockref("TAG", p)
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


def _raw_pdf(out: Path, objects: list[bytes]) -> Path:
    """A PDF from raw object bodies (object 1 = catalog …); stdlib only, so a million paths stay cheap."""
    data = bytearray(b"%PDF-1.7\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(data))
        data += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    data += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    out.write_bytes(bytes(data))
    return out


def _stream(content: bytes, extra: bytes = b"") -> bytes:
    import zlib

    packed = zlib.compress(content, 6)
    head = b"<< /Length %d /Filter /FlateDecode %s>>\nstream\n" % (len(packed), extra)
    return head + packed + b"\nendstream"


def _lines(n: int, seed: int = 7) -> bytes:
    import random

    rnd = random.Random(seed)
    parts = [b"0.1 w\n"]
    for _ in range(n):
        x, y = rnd.uniform(0, 1190), rnd.uniform(0, 842)
        parts.append(b"%.1f %.1f m %.1f %.1f l S\n" % (x, y, x + 3, y + 2))
    return b"".join(parts)


def build_dense_lines_pdf(out: Path, lines: int, *, form_calls: int = 0, noise: bytes = b"") -> Path:
    """One A3 page with ``lines`` separate one-segment paths (PROD 2026-11-10 pattern). With
    ``form_calls`` the lines live in a Form XObject drawn that many times; ``noise`` is prepended
    to the page content (strings, inline images … that must not count as paths)."""
    page = b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 1190 842] /Contents 4 0 R%s >>"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"]
    if form_calls:
        objects.append(page % b" /Resources << /XObject << /F0 5 0 R >> >>")
        objects.append(_stream(noise + b"q /F0 Do Q\n" * form_calls))
        objects.append(_stream(_lines(lines), b"/Type /XObject /Subtype /Form /BBox [0 0 1190 842] "))
    else:
        objects.append(page % b"")
        objects.append(_stream(noise + _lines(lines)))
    return _raw_pdf(out, objects)


def build_dense_hatch_dxf(out: Path, *, pattern: str = "AR-CONC", scale: float = 0.005, width: float = 8000.0,
                          height: float = 2000.0, inserts: int = 0, paper: bool = False,
                          lines: int = 300) -> Path:
    """The Civil 3D plan of PROD 2026-11-10, generated: metres, ``lines`` ordinary lines and one
    hatch ``pattern`` at ``scale`` over ``width`` × ``height`` — in model space, in a block inserted
    ``inserts`` times, or (``paper``) on a paper-space layout. ``pattern="SOLID"`` gives a fill."""
    import random

    import ezdxf

    doc = ezdxf.new("R2010", units=6)
    msp = doc.modelspace()
    rnd = random.Random(7)
    for _ in range(lines):
        x, y = rnd.uniform(0, width), rnd.uniform(0, height)
        msp.add_line((x, y), (x + rnd.uniform(-50, 50), y + rnd.uniform(-50, 50)))

    def add_hatch(space, w: float, h: float, origin=(0.0, 0.0)) -> None:
        hatch = space.add_hatch(color=3)
        if pattern == "SOLID":
            hatch.set_solid_fill(color=3)
        else:
            hatch.set_pattern_fill(pattern, scale=scale)
        x, y = origin
        hatch.paths.add_polyline_path([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], is_closed=True)

    if paper:
        sheet = doc.layouts.new("Sheet1")
        sheet.add_line((0, 0), (400, 0))
        add_hatch(sheet, 300.0, 200.0, (10.0, 10.0))
    elif inserts:
        block = doc.blocks.new("SITE")
        add_hatch(block, width, height)
        for i in range(inserts):
            msp.add_blockref("SITE", (i * (width + 100), 0))
    else:
        add_hatch(msp, width, height)
    doc.saveas(out)
    return out


def build_blank_model_dxf(out: Path, *, with_layout: bool) -> Path:
    """Model space whose every entity is on a layer that is off (the R14 converter bug of
    PROD 2026-11-10, or a drawing saved that way); optionally a sheet with a frame and a title."""
    import ezdxf

    doc = ezdxf.new("R2010")
    doc.layers.add("HIDDEN", color=1).off()
    msp = doc.modelspace()
    for i in range(50):
        msp.add_line((i * 10, 0), (i * 10, 100), dxfattribs={"layer": "HIDDEN"})
    if with_layout:
        sheet = doc.layouts.new("Sheet1")
        sheet.add_lwpolyline([(0, 0), (400, 0), (400, 280), (0, 280)], close=True)
        sheet.add_text("TITLE", height=5).set_placement((300, 10))
    doc.saveas(out)
    return out


def build_sheets_only_dxf(out: Path, *, sheets=("A1", "A2"), content: str = "drawing", lines: int = 2,
                          fmt: str = "asc") -> Path:
    """Engine 1.4: model space without a single entity, the drawing on paper-space sheets (the DWG
    of PROD 2026-11-12). Each sheet is A3 landscape; ``content``: ``"drawing"`` — a frame,
    ``lines`` lines, a title and a stamp text; ``"viewport"`` — only a viewport onto the empty
    model; ``"xref"`` — only an inserted xref (an external title block); ``"none"`` — nothing.
    The first sheet is the active layout, so its entities are written to ENTITIES with group
    67 = 1 and the others' to their ``*Paper_Space<n>`` blocks."""
    import ezdxf

    doc = ezdxf.new("R2010")
    if content == "xref":
        doc.add_xref_def(r"C:\Standards\title-block.dwg", "TITLE_BLOCK")
    for name in sheets:
        sheet = doc.layouts.new(name)
        sheet.page_setup(size=(420, 297), margins=(10, 10, 10, 10), units="mm")
        if content == "drawing":
            sheet.add_lwpolyline([(0, 0), (400, 0), (400, 277), (0, 277)], close=True)
            for i in range(lines):
                sheet.add_line((20, 40 + i * 0.01), (380, 40 + (i % 200) + 20))
            sheet.add_text(f"SHEET {name}", height=6).set_placement((20, 250))
            sheet.add_text("STAMP / SCALE 1:100", height=3).set_placement((300, 10))
        elif content == "viewport":
            sheet.add_viewport(center=(200, 138), size=(380, 257), view_center_point=(0, 0), view_height=1000)
        elif content == "xref":
            sheet.add_blockref("TITLE_BLOCK", (0, 0))
    if sheets:
        doc.layouts.set_active_layout(sheets[0])
    doc.saveas(out, fmt=fmt)
    return out
