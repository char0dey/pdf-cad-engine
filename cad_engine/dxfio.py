# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Reading DXF input: signature sniffing (DXF / binary DXF / DWG) and loading
with ezdxf, falling back to ``ezdxf.recover`` for damaged files."""

from __future__ import annotations

import io
import re
import sys
from typing import TYPE_CHECKING

from cad_engine.errors import InputError

if TYPE_CHECKING:
    from ezdxf.document import Drawing

SNIFF_BYTES = 64 * 1024
BINARY_DXF_SENTINEL = b"AutoCAD Binary DXF\r\n\x1a\x00"
# DWG files start with the version string: AC1012 ... AC1032 (R13+), AC1.40 ... AC2.10 (very old).
_DWG_SIGNATURE = re.compile(rb"^AC(?:10\d\d|\d\.\d\d)")
# An ASCII DXF has a "0 / SECTION" group pair near the start (after optional 999 comments).
_ASCII_SECTION = re.compile(rb"(?m)^[ \t]*0[ \t]*\r?\n[ \t]*SECTION[ \t]*\r?$")


def sniff(head: bytes) -> str | None:
    """Classify the first bytes of a file: ``'ascii'``, ``'binary'``, ``'dwg'`` or None."""
    if _DWG_SIGNATURE.match(head):
        return "dwg"
    if head.startswith(BINARY_DXF_SENTINEL):
        return "binary"
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    if _ASCII_SECTION.search(head):
        return "ascii"
    return None


def sniff_file(path: str) -> str | None:
    with open(path, "rb") as fp:
        return sniff(fp.read(SNIFF_BYTES))


def check_dxf_signature(path: str) -> str:
    """Reject DWG and non-DXF files before ezdxf touches them."""
    kind = sniff_file(path)
    if kind == "dwg":
        raise InputError("dwg_not_dxf", "the file is a DWG drawing, not DXF")
    if kind is None:
        raise InputError("unsupported_input", "the file is not a DXF drawing")
    return kind


def repair_truncated(data: bytes) -> bytes | None:
    """Close an ASCII DXF whose end is cut off; None if it is not truncated.

    Drops the incomplete last line and the possibly incomplete last object (back
    to the last ``0`` group code), then appends ``ENDSEC`` (if needed) and ``EOF``,
    so ezdxf.recover keeps everything that was read. Assumes group codes on even
    lines, which holds for files written by CAD programs.
    """
    if data.rstrip().endswith(b"EOF"):
        return None
    lines = data.split(b"\n")[:-1]  # the last element is either empty or a partial line
    last_zero = None
    for i in range((len(lines) - 2) // 2 * 2, -1, -2):  # pairs (code, value), backwards
        if lines[i].strip() == b"0":
            last_zero = i
            break
    if last_zero is None:
        return None
    kept = [line.rstrip(b"\r") for line in lines[:last_zero]]
    closes_section = len(kept) >= 2 and kept[-2].strip() == b"0" and kept[-1].strip() == b"ENDSEC"
    tail = [b"  0", b"EOF"] if closes_section else [b"  0", b"ENDSEC", b"  0", b"EOF"]
    eol = b"\r\n" if b"\r\n" in data[:4096] else b"\n"
    return eol.join(kept + tail) + eol


def drop_broken_block_refs(doc: Drawing) -> int:
    """Delete block references (INSERT) without a block name or whose block is not defined; returns
    how many. Every block lookup of such an INSERT raises (extents, rendering), so one of them failed
    the whole drawing. ``ezdxf.recover`` deletes them in its audit (``Insert.audit``), ``ezdxf.readfile``
    does not audit. Seen in a DXF the browser's DWG converter (dwgdxf 1.0.5) wrote from an AutoCAD 2018
    drawing: PROD 2026-11-12, dxf2pdf «DXFTypeError: name has to be a string, got NoneType»."""
    broken = []
    for entity in doc.entitydb.values():
        if entity.is_alive and entity.dxftype() == "INSERT":
            name = entity.dxf.get("name")
            if not isinstance(name, str) or name not in doc.blocks:
                broken.append(entity)
    for entity in broken:  # model space, paper space or a block definition: all are block records
        owner = doc.entitydb.get(entity.dxf.get("owner"))
        if owner is not None and owner.dxftype() == "BLOCK_RECORD":
            owner.delete_entity(entity)
        else:
            doc.entitydb.delete_entity(entity)
    if broken:
        print(f"cad-engine: {len(broken)} block reference(s) without a block definition dropped",
              file=sys.stderr)
    return len(broken)


def load_dxf(path: str) -> Drawing:
    """Load a DXF (ASCII or binary); damaged files go through ezdxf.recover. Block references that
    point at no block are dropped either way (``drop_broken_block_refs``). The engine's ezdxf fixes
    (``ezdxf_fixes``) are installed here, before anything reads the drawing."""
    import ezdxf

    from cad_engine import ezdxf_fixes

    ezdxf_fixes.apply()
    kind = check_dxf_signature(path)
    try:
        doc = ezdxf.readfile(path)
    except MemoryError:
        raise
    except Exception as exc:  # any parse failure gets a recovery attempt
        print(f"cad-engine: DXF read failed ({type(exc).__name__}: {exc}); trying recover", file=sys.stderr)
        return _recover_dxf(path, kind)
    drop_broken_block_refs(doc)
    return doc


def _recover_dxf(path: str, kind: str) -> Drawing:
    """``ezdxf.recover`` (its audit drops broken block references too), after closing a truncated file."""
    from ezdxf import recover

    attempts = []
    if kind == "ascii":
        with open(path, "rb") as fp:
            repaired = repair_truncated(fp.read())
        if repaired is not None:
            attempts.append(("truncated file closed", lambda: recover.read(io.BytesIO(repaired))))
    attempts.append(("recover", lambda: recover.readfile(path)))

    last_exc: Exception | None = None
    for label, attempt in attempts:
        try:
            doc, auditor = attempt()
        except MemoryError:
            raise
        except Exception as exc:
            print(f"cad-engine: DXF {label} failed ({type(exc).__name__}: {exc})", file=sys.stderr)
            last_exc = exc
            continue
        print(
            f"cad-engine: DXF loaded via {label}: {len(auditor.fixes)} fixes, "
            f"{len(auditor.errors)} unrecoverable errors",
            file=sys.stderr,
        )
        drop_broken_block_refs(doc)  # normally nothing left after the audit; cheap insurance
        return doc
    raise InputError("unsupported_input", "the DXF file is damaged and cannot be read") from last_exc


def extents_mm_factor(doc: Drawing) -> float:
    """Factor from drawing units ($INSUNITS) to mm; unitless drawings count as mm."""
    from ezdxf import units

    try:
        code = int(doc.units or 0)
        return units.conversion_factor(code, units.MM) if code else 1.0
    except (ValueError, KeyError, TypeError):
        return 1.0
