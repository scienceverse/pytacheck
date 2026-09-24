"""R-compatibility layer.

Everything a port needs to reproduce R/dplyr semantics exactly lives here:
regular expressions (:mod:`pytacheck._r.regex`), base-R formatting and
collation (:mod:`pytacheck._r.base`) and dplyr idioms
(:mod:`pytacheck._r.frames`). See ``docs/PORTING.md``.
"""

from pytacheck._r.base import (
    as_character,
    format_num,
    is_na,
    nchar,
    paste,
    paste0,
    plural,
    r_sort_key,
    r_sorted,
    signif,
    substr,
    trimws,
)
from pytacheck._r.frames import bind_rows, count, empty_like, ensure_columns
from pytacheck._r.regex import (
    RegexError,
    compile_r,
    gregexpr_all,
    grep,
    grepl,
    gsub,
    regexec,
    regextract,
    regextract_all,
    strsplit,
    sub,
)

__all__ = [
    "RegexError",
    "as_character",
    "bind_rows",
    "compile_r",
    "count",
    "empty_like",
    "ensure_columns",
    "format_num",
    "gregexpr_all",
    "grep",
    "grepl",
    "gsub",
    "is_na",
    "nchar",
    "paste",
    "paste0",
    "plural",
    "r_sort_key",
    "r_sorted",
    "regexec",
    "regextract",
    "regextract_all",
    "signif",
    "strsplit",
    "sub",
    "substr",
    "trimws",
]
