# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""All-or-nothing output files: write ``<path>.part``, rename on success."""

from __future__ import annotations

import contextlib
import os

PART_SUFFIX = ".part"


def part_path(path: str) -> str:
    return path + PART_SUFFIX


def write_part(path: str, data: bytes) -> None:
    with open(part_path(path), "wb") as fp:
        fp.write(data)


def commit(*paths: str | None) -> None:
    """Move every ``<path>.part`` into place."""
    for path in paths:
        if path:
            os.replace(part_path(path), path)


def discard(*paths: str | None) -> None:
    """Remove outputs and leftovers of a failed run (no partial results)."""
    for path in paths:
        if not path:
            continue
        for candidate in (path, part_path(path)):
            with contextlib.suppress(FileNotFoundError, IsADirectoryError, PermissionError):
                os.remove(candidate)
