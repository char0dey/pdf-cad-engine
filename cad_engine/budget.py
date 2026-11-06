# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Quazar Digital OÜ
"""Time budget: cooperative checkpoints plus a SIGALRM hard stop (Unix).

The checkpoints (`Budget.check`) are called per page and every N entities.
The interval timer raises `BudgetExceeded` from the signal handler as soon as
the interpreter runs Python code again, which also covers long loops inside
ezdxf. A native call that never returns to Python (a MuPDF hang) cannot be
interrupted from inside the process: the caller must still kill the process
after its own timeout; `faulthandler` dumps the stacks to stderr shortly before
that so the hang is diagnosable.
"""

from __future__ import annotations

import faulthandler
import signal
import sys
import time

from cad_engine.errors import BudgetExceeded

#: Seconds after the budget at which the stacks are dumped to stderr.
STACK_DUMP_GRACE_S = 4.0


class Budget:
    def __init__(self, budget_ms: int | None, started: float | None = None) -> None:
        self.limit_s = budget_ms / 1000.0 if budget_ms and budget_ms > 0 else None
        self.started = time.monotonic() if started is None else started
        self.expired = False
        self._armed = False
        self._previous_handler = None

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def remaining_s(self) -> float | None:
        if self.limit_s is None:
            return None
        return self.limit_s - (time.monotonic() - self.started)

    def check(self) -> None:
        """Raise `BudgetExceeded` if the budget is spent."""
        if self.expired:
            raise BudgetExceeded(self._message())
        remaining = self.remaining_s()
        if remaining is not None and remaining <= 0:
            self.expired = True
            raise BudgetExceeded(self._message())

    def arm(self) -> None:
        """Start the hard-stop timer (main thread only, no-op without setitimer)."""
        remaining = self.remaining_s()
        if remaining is None:
            return
        self.check()
        if hasattr(signal, "setitimer"):
            self._previous_handler = signal.signal(signal.SIGALRM, self._on_alarm)
            signal.setitimer(signal.ITIMER_REAL, max(remaining, 0.001))
            self._armed = True
        try:
            faulthandler.dump_traceback_later(remaining + STACK_DUMP_GRACE_S, exit=False, file=sys.stderr)
        except (RuntimeError, ValueError, OSError):
            pass

    def disarm(self) -> None:
        if self._armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if self._previous_handler is not None:
                signal.signal(signal.SIGALRM, self._previous_handler)
            self._armed = False
        try:
            faulthandler.cancel_dump_traceback_later()
        except RuntimeError:
            pass

    def _on_alarm(self, signum, frame) -> None:  # signal handler signature
        self.expired = True
        raise BudgetExceeded(self._message())

    def _message(self) -> str:
        limit_ms = int(self.limit_s * 1000) if self.limit_s is not None else 0
        return f"time budget of {limit_ms} ms exceeded"


class Ticker:
    """Counts work units and calls `Budget.check` every `every` units."""

    __slots__ = ("budget", "every", "_n")

    def __init__(self, budget: Budget, every: int = 1000) -> None:
        self.budget = budget
        self.every = every
        self._n = 0

    def tick(self) -> None:
        self._n += 1
        if self._n % self.every == 0:
            self.budget.check()
