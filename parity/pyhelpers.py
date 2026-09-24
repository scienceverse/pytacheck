"""Python counterparts of parity/r/helpers.R (vectorised base-R idioms)."""

from __future__ import annotations

from typing import Any

from pytacheck._r import base as rb
from pytacheck._r import regex as rx


def as_character(x: list[Any]) -> list[str | None]:
    return [rb.as_character(v) for v in x]


def format_num(x: list[Any], digits: int = 7) -> list[str]:
    return [rb.format_num(v, digits) for v in x]


def trimws(x: list[Any], which: str = "both") -> Any:
    return rb.trimws(list(x), which=which)


def r_sort(x: list[Any]) -> list[Any]:
    return rb.r_sorted(x)


grepl = rx.grepl
gsub = rx.gsub
sub = rx.sub
regextract = rx.regextract
regextract_all = rx.regextract_all
regexec = rx.regexec
strsplit = rx.strsplit
