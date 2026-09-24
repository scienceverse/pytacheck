"""Helpers for the statout_core parity cases and tests (R base idioms)."""

from __future__ import annotations

from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"


def read_lines(con: str | Path) -> list[str]:
    """R ``readLines(con)`` of a UTF-8 file (``\\n``, ``\\r\\n`` or ``\\r`` endings)."""
    text = Path(con).read_text(encoding="utf-8")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def read_text(con: str | Path) -> str:
    """``paste(readLines(con), collapse = "\\n")``."""
    return "\n".join(read_lines(con))
