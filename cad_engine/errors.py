# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Engine exceptions and the exit codes they map to (see README, "Exit codes")."""

from __future__ import annotations

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_BUDGET = 3
EXIT_MEMORY = 4
EXIT_FAILURE = 5

#: ``code`` values reported with exit code 2 (unusable input).
INPUT_ERROR_CODES = frozenset(
    {
        "no_vectors",
        "empty_drawing",
        "aec_objects",
        "external_refs_only",
        "too_many_pages",
        "dwg_not_dxf",
        "too_complex",
        "unsupported_input",
    }
)


class EngineError(Exception):
    """Any failure that is not the input's fault (exit code 5)."""

    exit_code = EXIT_FAILURE
    code = "engine_error"


class InputError(EngineError):
    """The input cannot be converted (exit code 2); ``code`` says why."""

    exit_code = EXIT_INPUT

    def __init__(self, code: str, message: str) -> None:
        if code not in INPUT_ERROR_CODES:
            raise ValueError(f"unknown input error code: {code!r}")
        super().__init__(message)
        self.code = code


class MemoryLimitExceeded(EngineError):
    """The process hit its address-space limit (exit code 4)."""

    exit_code = EXIT_MEMORY
    code = "memory_limit"


class BudgetExceeded(BaseException):
    """The time budget (``--budget-ms``) ran out (exit code 3).

    Derives from ``BaseException`` on purpose: library code that swallows
    ``Exception`` (ezdxf skips broken entities that way) must not swallow the
    timeout raised from the SIGALRM handler.
    """

    exit_code = EXIT_BUDGET
    code = "budget_exceeded"
