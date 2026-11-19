# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Write a synthetic heavy PDF for calibrating the background threshold (engine 1.6).

    python scripts/make_heavy_pdf.py OUT.pdf UNITS [--kind outlines|fills|lines] [--pages N]

UNITS is paths + segments in all, as ``probe-pdf`` reports them (``vector_paths`` +
``vector_segments``). ``outlines`` is text converted to curves (filled and stroked glyph outlines),
``fills`` the same glyphs filled only (engine 1.7: the paths that become fill areas),
``lines`` a line drawing (one line per path). Development only: no customer data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import make_sample  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("units", type=int)
    parser.add_argument("--kind", choices=sorted(make_sample.HEAVY_UNITS_PER_PATH), default="outlines")
    parser.add_argument("--pages", type=int, default=1)
    args = parser.parse_args()
    make_sample.build_heavy_pdf(args.out, args.units, kind=args.kind, pages=args.pages)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
