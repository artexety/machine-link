"""Terminal output, the global options, and the one exception every command raises.

Diagnostics go to stderr; stdout carries content only (tables, summaries, JSON).
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from rich.console import Console
from rich.text import Text

NO_COLOR = bool(os.environ.get("NO_COLOR"))
err = Console(stderr=True, no_color=NO_COLOR, highlight=False, soft_wrap=True)
out = Console(no_color=NO_COLOR, highlight=False)


@dataclass
class Options:
    verbose: bool = False
    config: str | None = None


OPTS = Options()


class Fail(Exception):
    """Abort the command with an exit code, a diagnosis and one concrete fix."""

    def __init__(self, code: int, message: str, fix: str = ""):
        super().__init__(message)
        self.code, self.message, self.fix = code, message, fix


def say(message: str) -> None:
    err.print(message, style="dim", markup=False)


def warn(message: str) -> None:
    err.print(Text.assemble(("warning: ", "yellow"), message))


def report(exc: Fail) -> None:
    err.print(Text.assemble(("error: ", "bold red"), exc.message))
    if exc.fix:
        err.print(Text.assemble(("  fix: ", "bold"), exc.fix))


@dataclass
class Step:
    note: str = ""
    ok: bool = True

    def fail(self, note: str) -> None:
        """Mark the step failed without raising, for errors the caller absorbs."""
        self.ok, self.note = False, note


@contextmanager
def step(label: str) -> Iterator[Step]:
    """One checklist line: a tick or a cross, the label, a note, and the elapsed time."""
    st, started = Step(), time.monotonic()
    try:
        yield st
    except BaseException:
        st.ok = False
        raise
    finally:
        line = Text.assemble(("✓ ", "green") if st.ok else ("✗ ", "red"), label)
        if st.note:
            line.append(f"  {st.note}", style="dim")
        timing = f"{time.monotonic() - started:5.1f}s"
        line.append(" " * max(1, err.width - line.cell_len - len(timing)))
        line.append(timing, style="dim")
        err.print(line)
