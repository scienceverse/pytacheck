"""R conventions that reach users.

metacheck's behaviour that users see depends on a few R conventions, kept here
(docs/PORTING.md, section 3): R's regular expressions with R's replacement
syntax (:mod:`metacheck._r.regex`), R's number formatting, rounding and string
collation (:mod:`metacheck._r.base`) and the dplyr idioms whose pandas
spelling gives a different table (:mod:`metacheck._r.frames`).
"""

from metacheck._r.base import (
    as_character,
    format_num,
    is_na,
    local_epoch,
    local_utc_offset,
    paste,
    plural,
    r_round,
    r_sort_key,
    r_sorted,
    signif,
    slashed,
    trimws,
)
from metacheck._r.frames import bind_rows, count
from metacheck._r.regex import (
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
    "format_num",
    "gregexpr_all",
    "grep",
    "grepl",
    "gsub",
    "is_na",
    "local_epoch",
    "local_utc_offset",
    "paste",
    "plural",
    "r_round",
    "r_sort_key",
    "r_sorted",
    "regexec",
    "regextract",
    "regextract_all",
    "signif",
    "slashed",
    "strsplit",
    "sub",
    "trimws",
]
