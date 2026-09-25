"""Terminal output and prompts for pack and store commands (rich, on stderr).

Everything that asks the user goes through :func:`confirm` / :func:`ask`, so
tests (and embedding applications) can replace them. Without an interactive
terminal nothing is ever confirmed: pass ``yes=True`` / ``--yes`` instead.
Questions are shown through :func:`pytacheck.packs.auth.redact`, so a URL
typed with a password or token is never echoed back.
"""

from __future__ import annotations

import sys
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console

__all__ = ["ask", "confirm", "console", "human_size", "interactive"]


@cache
def _console(stderr: bool) -> Console:
    from rich.console import Console

    return Console(stderr=stderr, highlight=False, soft_wrap=True)  # commands stay copyable


def console(*, stderr: bool = True) -> Console:
    """The shared rich console (stderr by default, so stdout stays clean for JSON)."""
    return _console(stderr)


def interactive() -> bool:
    """Whether a person can answer prompts (stdin is a terminal)."""
    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def confirm(question: str, *, default: bool = False) -> bool:
    """Ask a yes/no question (``[y/N]``); ``False`` when nobody can answer.

    The question is shown redacted (a URL's password or token never is).
    """
    from pytacheck.packs.auth import redact

    question = redact(question)
    if not interactive():
        console().print(
            f"{question} [y/N] [dim](no terminal to ask: pass --yes / yes=True to confirm)[/]"
        )
        return False
    from rich.prompt import Confirm

    return bool(Confirm.ask(question, default=default, console=console()))


def ask(question: str, *, default: str = "") -> str:
    """Ask for a line of text (the default when nobody can answer); shown redacted."""
    if not interactive():
        return default
    from rich.prompt import Prompt

    from pytacheck.packs.auth import redact

    question = redact(question)

    return str(Prompt.ask(question, default=default, console=console()))


def human_size(n: int) -> str:
    """``1536`` -> ``"1.5 kB"``."""
    size = float(n)
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"  # pragma: no cover
