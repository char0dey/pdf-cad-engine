# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Light scan of a DXF for content a PDF of it cannot show (engine 1.2).

One pass over the group-code / value pairs (ASCII or binary DXF), without building a
document, so it costs little even for drawings of tens of megabytes. It finds:

- ``xrefs`` — external references to other drawings (block flag 4 = xref, 8 = overlay) that
  are inserted at least once anywhere (model space, a paper-space sheet or another block);
  a bound xref is an ordinary block and an xref definition nobody inserts does not count;
- ``underlays`` — PDF / DWF / DGN underlay definitions referenced by an underlay entity;
- ``images`` — raster image definitions referenced by an IMAGE entity (a DWG never embeds them);
- ``aec`` — registered classes of AutoCAD vertical products that are entities: ``AEC_`` /
  ``AECS_`` (Architecture), ``AECB_`` (MEP), ``AECC_`` (Civil 3D). Their objects are not
  part of the DXF a converter makes, and how many there were is unknown.

File names are base names (no folder), unique, in order of appearance. It also counts the
model-space entities as ezdxf counts them (``ms_entities``) and those that draw something
(``ms_drawable``: everything but underlays, images and inserts of xref blocks).
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

from cad_engine.budget import Budget
from cad_engine.dxfio import sniff_file

UNDERLAY_ENTITIES = frozenset({"PDFUNDERLAY", "DWFUNDERLAY", "DGNUNDERLAY"})
UNDERLAY_DEFINITIONS = frozenset({"PDFDEFINITION", "DWFDEFINITION", "DGNDEFINITION"})
REFERENCE_ENTITIES = UNDERLAY_ENTITIES | {"IMAGE"}
NOT_DRAWN = REFERENCE_ENTITIES
#: Not counted as model-space entities, as ezdxf does not count them: parts of an INSERT /
#: POLYLINE, and viewports (some writers put paper-space viewports into ENTITIES without 67).
NOT_COUNTED = frozenset({"ATTRIB", "VERTEX", "SEQEND", "VIEWPORT"})
#: Longest prefix first is not needed: "AEC_" does not match "AECB_" / "AECC_" / "AECS_".
AEC_FAMILIES = (("AEC_", "architecture"), ("AECS_", "architecture"), ("AECB_", "mep"), ("AECC_", "civil"))
XREF_FLAGS = 4 | 8
MODEL_SPACE_BLOCK = "*MODEL_SPACE"
TICK_EVERY = 50_000  # records between budget checks

#: Group codes kept per record type (everything else is skipped without a look).
_CLASS_CODES = frozenset({1, 281})
_BLOCK_CODES = frozenset({1, 2, 70})
_ENTITY_CODES = frozenset({67})
_INSERT_CODES = frozenset({2, 67})
_REFERENCE_CODES = frozenset({67, 340})
_DEFINITION_CODES = frozenset({1, 5})
_SECTION_CODES = frozenset({2})


@dataclass
class ScanResult:
    xrefs: list[str] = field(default_factory=list)
    underlays: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    aec: list[str] = field(default_factory=list)
    ms_entities: int = 0
    ms_drawable: int = 0
    #: The scan reached EOF; a cut-off file still reports what was found, but its counts
    #: are not used to call the drawing empty.
    complete: bool = False

    def missing(self) -> dict:
        return {"xrefs": self.xrefs, "underlays": self.underlays, "images": self.images, "aec": self.aec}

    @property
    def has_external(self) -> bool:
        return bool(self.xrefs or self.underlays or self.images)

    @property
    def nothing_to_draw(self) -> bool:
        return self.complete and self.ms_drawable == 0


def base_name(path: str) -> str:
    """``C:\\Projects\\plan.dwg`` / ``../x/plan.dwg`` / ``plan.dwg`` -> ``plan.dwg``."""
    return PureWindowsPath(path.strip()).name


def _decode(value) -> str:
    text = value if isinstance(value, str) else str(value)
    if "\\U+" in text:
        from ezdxf.lldxf.encoding import decode_dxf_unicode

        text = decode_dxf_unicode(text)
    return text.strip()


def _int(value) -> int:
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip() or 0)
    except ValueError:
        return 0


def _unique(names) -> list[str]:
    seen: set[str] = set()
    out = []
    for name in names:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _ascii_encoding(path: str) -> str:
    try:
        from ezdxf.filemanagement import dxf_file_info

        encoding = dxf_file_info(path).encoding or "cp1252"
    except Exception:  # an unreadable header: the tags still parse, names may lose accents
        encoding = "cp1252"
    return "utf-8-sig" if encoding.lower().replace("-", "") in ("utf8", "utf8sig") else encoding


def _tags(path: str):
    from ezdxf.lldxf.tagger import ascii_tags_loader, binary_tags_loader

    if sniff_file(path) == "binary":
        with open(path, "rb") as fp:
            yield from binary_tags_loader(fp.read(), errors="replace")
        return
    with open(path, encoding=_ascii_encoding(path), errors="replace") as fp:
        yield from ascii_tags_loader(fp)


def scan_tags(tags, budget: Budget | None = None) -> ScanResult:
    """The scan itself over ``(code, value)`` pairs (see the module docstring)."""
    result = ScanResult()
    section = ""
    block = ""  # upper-case name of the block being read (BLOCKS section)
    rtype = ""
    fields: dict | None = None  # kept group codes of the current record; None = not interesting
    wanted: frozenset = frozenset()
    records = 0

    xref_blocks: dict[str, str] = {}  # upper-case block name -> base name of the referenced file
    inserted: set[str] = set()
    ms_inserts: Counter = Counter()
    not_drawn = 0
    referenced: set[str] = set()  # handles of definitions referenced by an underlay / IMAGE
    underlay_defs: list[tuple[str, str]] = []
    image_defs: list[tuple[str, str]] = []
    aec: list[str] = []

    def finish() -> None:
        nonlocal section, block, not_drawn
        if fields is None:
            return
        if rtype == "SECTION":
            section = _decode(fields.get(2, "")).upper()
            return
        if section == "CLASSES":
            if _int(fields.get(281, 0)) == 1:
                name = _decode(fields.get(1, "")).upper()
                for prefix, family in AEC_FAMILIES:
                    if name.startswith(prefix):
                        aec.append(family)
                        break
            return
        if section == "OBJECTS":
            handle = _decode(fields.get(5, "")).upper()
            target = underlay_defs if rtype in UNDERLAY_DEFINITIONS else image_defs
            target.append((handle, base_name(_decode(fields.get(1, "")))))
            return
        if rtype == "BLOCK":
            name = _decode(fields.get(2, ""))
            block = name.upper()
            if _int(fields.get(70, 0)) & XREF_FLAGS:
                xref_blocks[block] = base_name(_decode(fields.get(1, ""))) or name
            return
        # an entity, in ENTITIES or inside a block
        if rtype == "INSERT":
            inserted.add(_decode(fields.get(2, "")).upper())
        elif rtype in REFERENCE_ENTITIES and 340 in fields:
            referenced.add(_decode(fields[340]).upper())
        in_model = (section == "ENTITIES" or block == MODEL_SPACE_BLOCK) and _int(fields.get(67, 0)) != 1
        if not in_model or rtype in NOT_COUNTED:
            return
        result.ms_entities += 1
        if rtype in NOT_DRAWN:
            not_drawn += 1
        elif rtype == "INSERT":
            ms_inserts[_decode(fields.get(2, "")).upper()] += 1

    for code, value in tags:
        if code != 0:
            if fields is not None and code in wanted and code not in fields:
                fields[code] = value
            continue
        finish()
        rtype = value.strip() if isinstance(value, str) else str(value)
        records += 1
        if budget is not None and records % TICK_EVERY == 0:
            budget.check()
        fields, wanted = {}, _ENTITY_CODES
        if rtype == "SECTION":
            wanted = _SECTION_CODES
        elif rtype == "ENDSEC":
            section, block, fields = "", "", None
        elif rtype == "EOF":
            fields = None
            result.complete = True
            break
        elif section == "CLASSES":
            wanted = _CLASS_CODES if rtype == "CLASS" else frozenset()
        elif section == "BLOCKS":
            if rtype == "BLOCK":
                wanted = _BLOCK_CODES
            elif rtype == "ENDBLK":
                block, fields = "", None
            elif rtype == "INSERT":
                wanted = _INSERT_CODES
            elif rtype in REFERENCE_ENTITIES:
                wanted = _REFERENCE_CODES
        elif section == "ENTITIES":
            if rtype == "INSERT":
                wanted = _INSERT_CODES
            elif rtype in REFERENCE_ENTITIES:
                wanted = _REFERENCE_CODES
        elif section == "OBJECTS" and (rtype in UNDERLAY_DEFINITIONS or rtype == "IMAGEDEF"):
            wanted = _DEFINITION_CODES
        else:
            fields = None
    finish()

    result.xrefs = _unique(path for name, path in xref_blocks.items() if name in inserted)
    result.underlays = _unique(name for handle, name in underlay_defs if handle in referenced)
    result.images = _unique(name for handle, name in image_defs if handle in referenced)
    result.aec = _unique(aec)
    xref_inserts = sum(count for name, count in ms_inserts.items() if name in xref_blocks)
    result.ms_drawable = max(result.ms_entities - not_drawn - xref_inserts, 0)
    return result


def scan_dxf(path: str, budget: Budget | None = None) -> ScanResult:
    """Scan a DXF file; raises on a file the tagger cannot read (see `safe_scan`)."""
    return scan_tags(_tags(path), budget)


def safe_scan(path: str, budget: Budget | None = None) -> ScanResult | None:
    """`scan_dxf`, or None when the scan fails: a failed detection never stops the conversion.
    The time budget (`BudgetExceeded`) is not swallowed."""
    try:
        return scan_dxf(path, budget)
    except MemoryError:
        raise
    except Exception as exc:
        print(f"cad-engine: content scan failed ({type(exc).__name__}: {exc})", file=sys.stderr)
        return None


def empty_reason(scan: ScanResult | None) -> str:
    """Why a drawing with nothing to draw is refused, by priority (spec cad-missing-content)."""
    if scan is not None and scan.aec:
        return "aec_objects"
    if scan is not None and scan.has_external:
        return "external_refs_only"
    return "empty_drawing"


EMPTY_MESSAGES = {
    "aec_objects": "the drawing holds only AutoCAD Architecture / MEP / Civil 3D objects, which are not read",
    "external_refs_only": "the drawing only references external files (xrefs, underlays, images)",
    "empty_drawing": "the drawing is empty",
}


def apply_to_meta(meta: dict, scan: ScanResult | None) -> None:
    """Findings into the meta; a failed scan reports ``scanned: false`` and no findings."""
    meta["scanned"] = scan is not None
    meta["missing"] = (scan or ScanResult()).missing()
    meta["ms_entities"] = scan.ms_entities if scan is not None else None
    meta["ms_drawable"] = scan.ms_drawable if scan is not None else None
