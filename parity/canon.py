"""The canonicaliser: Band B differences removed before a comparison.

Band B (docs/design/FIDELITY.md §2.1, §4) is what a result may change without a
record: row and column order, columns Python adds, dtypes and list shapes, the
spelling of NA, how numbers are written, whitespace. Canon works on the
canonical encoding both sides already produce (parity/canonical.py,
parity/r/canonical.R):

:func:`form`
    one side's canonical value, for lock digests and display. It is idempotent:
    ``form(form(x)) == form(x)``, and :func:`compare` finds no difference between
    a value and its form.
:func:`compare`
    R's and Python's values compared under the rules. It takes the values as
    encoded, not their forms, so it can read the printed precision of numbers.
:func:`paired`, :func:`digests`
    the paired projection: both forms, with Python's data frames cut to the
    columns R has, and their lock digests. A column Python adds changes no
    digest, and R's digest never depends on Python.

Each rule can be switched off (``Profile.rules``), so the flip audit can run them
one at a time; with none, :func:`compare` is :class:`parity.compare.Comparator`.

``order``
    Rows are a multiset. They are paired by a row key and compared, and a row
    without a partner is a difference. The key is the module's ``row_key`` in
    parity/canon.toml, or by default the columns that are numeric on neither
    side. Rows that share a key are compared as full-row multisets, so the key
    only decides how rows pair up: every row is compared with its partner or
    reported. Column order and the order of named list elements are free too.
``extra_column``
    A data-frame column only Python has is reported as info
    (``Comparison.info``). A column only R has is always a difference.
``dtype``
    Integers and doubles are one type, a text cell that is only a number equals
    that number, and list shapes are free: a list of length-one vectors is the
    vector, a list of records the data frame, a one-row matrix the vector.
``na``
    NA, None, NaN and "" are one value, and NULL, a zero-length value and a
    single NA are one value too.
``number_text``
    How a number is written is free: '.05' is '0.050', '2.2e-16' is
    '2.2 × 10^-16', a '<' or '>' bound is kept. Numbers in text compare at the
    printed precision of the less precise side, within half a unit in its last
    printed digit (give or take float noise, but not between whole numbers,
    which may be ids). In a data frame they must also match to the relative
    tolerance (1e-9), as its numbers do: 'p = 0.05' and 'p = 0.051' are equal in
    summary text, but not in a table cell. Digits that are no number are
    compared as written: an id ('007', more than 15 digits) and a number that is
    part of a name ('10.1037/…', '4.10.1'; see :func:`tokens`).
``whitespace``
    Text compares with its whitespace removed, but its numbers stay apart:
    'Hamilton1964' equals 'Hamilton 1964', and 'Study 12' differs from
    'Study 1 2'.

The permanent façades (``[facade.*]`` in parity/canon.toml) keep their row
order (``ordered``) and their NA spelling (``na_strict``). A module output's
traffic light is compared exactly, as FIDELITY.md §2.3 asks. Error texts never
reach canon: the harness only checks that both sides failed.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import math
import re
import tomllib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from parity.compare import (
    _VECTOR_TYPES,
    Comparator,
    Options,
    _fmt,
    _is_empty,
    _presence_view,
    _records_as_frame,
)
from parity.lockfile import _DIGITS, _rounded, digest

CANON_TOML = Path(__file__).resolve().parent / "canon.toml"

#: the rules, each with the Band B category it removes (FIDELITY.md §4.3, guard 5)
RULES = {
    "order": "row and column order",
    "extra_column": "a column only Python has",
    "dtype": "dtypes and list shapes",
    "na": "the spelling of NA",
    "number_text": "how numbers are written",
    "whitespace": "whitespace",
}

_NUMERIC = {"int", "dbl"}
_LISTS = {"list", "module_output", "paper", "paperlist"}
_PRINTED = 15  # significant digits R prints of a double
_NOISE = 1e-14  # relative float noise in the last of those digits


def _rule_set(rules: Iterable[str]) -> frozenset[str]:
    out = frozenset(rules)
    unknown = sorted(out - set(RULES))
    if unknown:
        raise ValueError(f"unknown canon rules {unknown} (one of {list(RULES)})")
    return out


# -- configuration: parity/canon.toml -------------------------------------------------


@dataclass(frozen=True)
class Facade:
    """The flags of a permanent façade (FIDELITY.md §2.2, rule 4)."""

    #: rows keep their order
    ordered: bool = False
    #: NA, None, NaN and "" stay apart
    na_strict: bool = False


@dataclass(frozen=True)
class Config:
    """parity/canon.toml: module row keys and façade flags."""

    #: module name -> the columns that identify a row of its ``table``
    row_keys: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: R function name (``*`` matches anything) -> its flags
    facades: Mapping[str, Facade] = field(default_factory=dict)

    def facade(self, r_function: str | None) -> Facade:
        """The flags of the façade a case's ``r:`` names (none when it names none).

        The name may have a ``pkg::`` prefix (an exported function); a
        ``pkg:::`` helper or an R expression is no façade.
        """
        m = _R_FUNCTION.fullmatch(r_function or "")
        if m is None:
            return Facade()
        for pattern, flags in self.facades.items():
            if fnmatch.fnmatchcase(m["name"], pattern):
                return flags
        return Facade()


_R_FUNCTION = re.compile(r"(?:[A-Za-z][\w.]*::)?(?P<name>[A-Za-z.][\w.]*)")


def parse_config(data: Mapping[str, Any], where: str = "canon.toml") -> Config:
    """A :class:`Config` from the parsed TOML *data* (errors name *where*)."""
    unknown = sorted(set(data) - {"module", "facade"})
    if unknown:
        raise ValueError(f"{where}: unknown tables {unknown} (module, facade)")
    for table in ("module", "facade"):
        if not isinstance(data.get(table, {}), dict):
            raise ValueError(f"{where}: {table} is a table of [{table}.<name>] entries")
    row_keys: dict[str, tuple[str, ...]] = {}
    for name, entry in data.get("module", {}).items():
        key = entry.get("row_key") if isinstance(entry, dict) else None
        ok = (
            isinstance(entry, dict)
            and set(entry) == {"row_key"}
            and isinstance(key, list)
            and bool(key)
            and all(isinstance(c, str) and c for c in key)
            and len(set(key)) == len(key)
        )
        if not ok:
            raise ValueError(
                f"{where}: [module.{name}] is row_key = a list of distinct column names, "
                f"not {entry!r}"
            )
        row_keys[name] = tuple(entry["row_key"])
    facades: dict[str, Facade] = {}
    for name, entry in data.get("facade", {}).items():
        ok = (
            isinstance(entry, dict)
            and bool(entry)
            and set(entry) <= {"ordered", "na_strict"}
            and all(isinstance(v, bool) for v in entry.values())
        )
        if not ok:
            raise ValueError(f"{where}: [facade.{name}] sets ordered and na_strict, not {entry!r}")
        facades[name] = Facade(**entry)
    return Config(row_keys, facades)


@functools.cache
def load_config(path: Path = CANON_TOML) -> Config:
    """parity/canon.toml (read once)."""
    with path.open("rb") as f:
        return parse_config(tomllib.load(f), path.name)


@dataclass(frozen=True)
class Profile:
    """How canon treats one case's values."""

    #: the rules that apply (all by default; the flip audit runs them one at a time)
    rules: frozenset[str] = frozenset(RULES)
    #: a permanent façade: rows keep their order
    ordered: bool = False
    #: a permanent façade: NA, None, NaN and "" stay apart
    na_strict: bool = False
    #: module name -> the row key of that module's ``table``
    module_keys: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def only(self, *rules: str) -> Profile:
        """This profile with only *rules* (none: the old comparison)."""
        return replace(self, rules=_rule_set(rules))

    @property
    def sorts_rows(self) -> bool:
        return "order" in self.rules and not self.ordered

    @property
    def na(self) -> bool:
        return "na" in self.rules and not self.na_strict


def profile(
    spec: Mapping[str, Any] | None = None,
    *,
    config: Config | None = None,
    rules: Iterable[str] = RULES,
) -> Profile:
    """The profile of a case (*spec*, as in parity/cases/<area>.yaml): the façade
    flags of its ``r:`` function and the module row keys of *config*
    (parity/canon.toml by default)."""
    config = config if config is not None else load_config()
    flags = Facade()
    if spec and "module" not in spec:
        r_function = spec.get("r")
        flags = config.facade(r_function if isinstance(r_function, str) else None)
    return Profile(
        rules=_rule_set(rules),
        ordered=flags.ordered,
        na_strict=flags.na_strict,
        module_keys=config.row_keys,
    )


# -- numbers in text -----------------------------------------------------------------

#: a number in text: parity/accuracy.py's _NUM (a minus sign, but not a hyphen after a
#: word or a closing parenthesis: 'COVID-19', '(3)-5'; a fraction without its leading
#: zero) with a '<' or '>' bound before it and an exponent ('e-16', '× 10^-16') after it.
#: A bound may hold whitespace ('> = 5'), so text without its whitespace reads the same.
#: An exponent ends the number: '3e2e14f' in a hash has none, nor has 'e2.01e+19'.
_NUMBER = re.compile(
    r"(?P<bound>[<>≤≥](?:\s*=)?\s*)?"
    r"(?P<sign>(?<![\w)])[-−])?"
    r"(?P<digits>\d+(?:\.\d+)?|(?<![\w.])\.\d+)"
    r"(?P<exp>(?:[eE][-−+]?\d+|\s*[×x*]\s*10\s*\^\s*[-−+]?\d+)(?!\w|\.\d))?"
)
_WS = re.compile(r"\s+")
#: text before a number that ends a word with a '.' ('j.cognition.2020.104010')
_WORD_DOT = re.compile(r"\w\.$")
_BOUNDS = {"≤": "<=", "≥": ">="}
_EXPONENT = re.compile(r"([-−+]?)(\d+)$")


@dataclass(frozen=True)
class Number:
    """A number read from text."""

    #: as written, or in a normal form with ``number_text`` ('.050' -> '0.05')
    text: str
    value: float
    #: half a unit in its last printed digit
    half: float
    #: '<', '>', '<=', '>=' or ''
    bound: str = ""
    #: digits no one prints as a number (a leading zero, more than 15 digits, a
    #: value no double has, part of a name: an id, a hash, a DOI, a version):
    #: compared as written
    literal: bool = False
    #: written without a fraction or an exponent: it carries no float noise
    whole: bool = False


def _number(m: re.Match[str], whitespace: bool, number_text: bool, named: bool) -> Number:
    """The number *m* matched (*named*: part of a name, see :func:`tokens`)."""
    digits = m["digits"]
    integer, _, fraction = digits.partition(".")
    exponent = 0
    e = _EXPONENT.search(m["exp"] or "")
    if e is not None:
        exponent = -int(e[2]) if e[1] in ("-", "−") else int(e[2])
    mantissa = "0" + digits if digits.startswith(".") else digits
    minus = "-" if m["sign"] else ""
    value = float(f"{minus}{mantissa}e{exponent}")
    literal = (
        named
        or (len(integer) > 1 and integer.startswith("0"))
        or (len(integer) > 15 and not fraction and not m["exp"])
        # no double: '9834E334677' in a checksum, or R's DBL_MAX to 15 digits
        or math.isinf(value)
        or (value == 0 and digits.strip("0.") != "")
    )
    half = float(f"5e{exponent - len(fraction) - 1}")
    bound = _WS.sub("", m["bound"] or "")
    bound = _BOUNDS.get(bound, bound)
    whole = not fraction and not m["exp"]
    if literal or not number_text:
        text = _WS.sub("", m.group(0)) if whitespace else m.group(0)
    elif whole:  # every digit, as an id or a count needs
        text = f"{bound}{minus}{digits}"
    else:  # to the significant digits R prints ('1e+05' and '100000.0' are '100000')
        text = f"{bound}{value:.{_PRINTED}g}"
        if not whitespace and "." not in text and "e" not in text:
            # with no space after it, it would run into the text there: '8911e715e3'
            # would read as 8911e715000
            text += ".0"
    return Number(text, value, half, bound, literal, whole)


@functools.lru_cache(maxsize=1 << 16)
def tokens(s: str, whitespace: bool = True, number_text: bool = True) -> tuple[str | Number, ...]:
    """*s* as text and numbers, in order; with *whitespace*, the text without whitespace
    (empty pieces left out).

    A number is part of a name, and compared as written, when the text next to it
    (whitespace aside) is a '/' ('10.1037/abc', 'osf.io/1.50'), a '.' between it and
    another number ('4.10.1'), or a word and a '.' before it
    ('j.cognition.2020.104010'). It is read from the text between numbers, which a
    form leaves as it is, so a form reads the same.
    """
    matches = list(_NUMBER.finditer(s))
    ends = [0, *(m.end() for m in matches)]
    starts = [*(m.start() for m in matches), len(s)]
    pieces = [s[a:b] for a, b in zip(ends, starts, strict=True)]
    bare = [_WS.sub("", piece) for piece in pieces]
    out: list[str | Number] = []
    for i, m in enumerate(matches):
        before, after = bare[i], bare[i + 1]
        named = (
            before.endswith("/")
            or after.startswith("/")
            or (before == "." and i > 0)
            or (after == "." and i + 1 < len(matches))
            or bool(_WORD_DOT.search(before))
        )
        piece = bare[i] if whitespace else pieces[i]
        if piece:
            out.append(piece)
        out.append(_number(m, whitespace, number_text, named))
    piece = bare[-1] if whitespace else pieces[-1]
    if piece:
        out.append(piece)
    return tuple(out)


def text_form(s: str, whitespace: bool = True, number_text: bool = True) -> str:
    """The canonical form of the text *s*: numbers normalised (*number_text*), and
    with *whitespace* the text without whitespace and each number set apart by one
    space ('Study 12' -> 'Study 12', 'Hamilton1964' -> 'Hamilton 1964')."""
    parts = [t if isinstance(t, str) else t.text for t in tokens(s, whitespace, number_text)]
    return (" " if whitespace else "").join(parts)


def number_only(s: str, whitespace: bool = True, number_text: bool = True) -> Number | None:
    """The number *s* is, when it is only one (no bound, not a literal)."""
    toks = tokens(s, whitespace, number_text)
    if len(toks) != 1 or isinstance(toks[0], str):
        return None
    n = toks[0]
    return None if n.bound or n.literal else n


def _as_double(n: Number | None) -> bool:
    """Whether a form may hold the number *n* as a double: a lock digest keeps 10
    significant digits of a double, and every digit of a whole number (an ISBN)."""
    return n is not None and not (n.whole and abs(n.value) >= 10**_DIGITS)


# -- helpers on the encoding -----------------------------------------------------------


def _na_value(x: Any, t: str | None, whitespace: bool) -> Any:
    """*x* (an element of a vector of type *t*), with NaN and "" as None (with
    *whitespace*, also text that is only whitespace)."""
    if t == "dbl" and x == "NaN":
        return None
    if t == "chr" and isinstance(x, str) and not (_WS.sub("", x) if whitespace else x):
        return None
    return x


def _scalar_vector(x: dict[str, Any]) -> dict[str, Any] | None:
    """An unnamed list of length-one vectors (or NULLs) of one type as that vector
    (as ``parity.compare`` reads it, without mixing types)."""
    items = x.get("v") or []
    if x.get("names") or not items:
        return None
    values: list[Any] = []
    types = set()
    for el in items:
        t = el.get("t") if isinstance(el, dict) else None
        if t == "null":
            values.append(None)
            continue
        if t not in _VECTOR_TYPES or len(el.get("v") or []) != 1:
            return None
        if el["v"][0] is not None:
            types.add(t)
        values.append(el["v"][0])
    if len(types) > 1 and not types <= _NUMERIC:
        return None
    t = "dbl" if len(types) > 1 else (types.pop() if types else "lgl")
    return {"t": t, "v": values}


def _module_name(x: dict[str, Any]) -> str | None:
    """The ``module`` element of a module output."""
    if x.get("t") != "module_output":
        return None
    for n, el in zip(x.get("names") or [], x.get("v") or [], strict=False):
        if n == "module" and isinstance(el, dict) and el.get("t") == "chr" and el.get("v"):
            return str(el["v"][0])
    return None


def _cells(col: dict[str, Any], nrow: int) -> list[Any] | None:
    """The rows of a data-frame column (``None`` when it is not a column of rows)."""
    if col.get("t") not in _VECTOR_TYPES and col.get("t") not in ("list", "presence"):
        return None
    v = col.get("v") or []
    return [v[i] if i < len(v) else None for i in range(nrow)]


def _pick(col: dict[str, Any], rows: list[int]) -> dict[str, Any]:
    """The column *col* with only *rows*, in that order."""
    v = col.get("v") or []
    filler = {"t": "null"} if col.get("t") == "list" else None
    return {**col, "v": [v[i] if i < len(v) else filler for i in rows]}


def _sig(cell: Any) -> str:
    """A cell's signature, for sorting and pairing rows: its JSON, doubles rounded
    as in a lock digest."""
    return json.dumps(_rounded(cell), sort_keys=True, ensure_ascii=False, default=str)


def _key_columns(
    declared: tuple[str, ...], names: list[tuple[str, int]], numeric: Iterable[bool]
) -> list[int]:
    """The positions in *names* of the row key: the *declared* columns there are, or
    else the columns that are not numeric."""
    key = [names.index((n, 0)) for n in declared if (n, 0) in names]
    return key or [i for i, num in enumerate(numeric) if not num]


def _occurrences(names: list[str]) -> list[tuple[str, int]]:
    """Each column name with its occurrence (a data frame can repeat a name)."""
    seen: Counter[str] = Counter()
    out = []
    for n in names:
        out.append((n, seen[n]))
        seen[n] += 1
    return out


def _sub(path: str, name: str) -> str:
    return f"{path}.{name}".lstrip(".")


# -- one side's form -------------------------------------------------------------------


class _Form:
    """One side's canonical form under a profile (see :func:`form`).

    With *shapes_only* it only reshapes (list shapes, order, NA) and leaves text as
    it is, so a comparison can still read the printed precision of its numbers.
    """

    def __init__(self, prof: Profile, options: Options, shapes_only: bool = False) -> None:
        self.p = prof
        self.o = options
        rules = prof.rules
        self.order = "order" in rules
        self.dtype = "dtype" in rules
        self.ws = "whitespace" in rules and not shapes_only
        self.nt = "number_text" in rules and not shapes_only
        #: text that is only a number reads as that number (``dtype``)
        self.numbers = self.dtype and not shapes_only
        #: the path of a module's ``table`` -> its row key
        self.keys: dict[str, tuple[str, ...]] = {}

    def empty(self, x: dict[str, Any]) -> bool:
        if _is_empty(x):
            return True
        t, v = x.get("t"), x.get("v")
        if t == "matrix" and isinstance(v, dict) and 1 in (x.get("dim") or []):
            return self.empty(v)
        if t == "list" and self.dtype and not x.get("names") and isinstance(v, list):
            # a list of one empty value reads as that value (list shapes)
            return len(v) == 1 and isinstance(v[0], dict) and self.empty(v[0])
        return (
            t in _VECTOR_TYPES
            and isinstance(v, list)
            and len(v) == 1
            and _na_value(v[0], t, self.ws) is None
        )

    def value(self, x: Any, path: str = "") -> Any:
        if not isinstance(x, dict):
            return x
        t = x.get("t")
        if self.p.na and self.empty(x):
            return {"t": "null"}
        if t in _VECTOR_TYPES:
            return self.vector(x)
        if t == "df":
            return self.frame(x, path)
        if t in _LISTS:
            return self.list(x, path)
        if t == "matrix" and isinstance(x.get("v"), dict):
            if self.dtype and 1 in (x.get("dim") or []):
                return self.value(x["v"], path)
            return {**x, "v": self.vector(x["v"])}
        if t == "prose":
            return self.prose(x.get("v") or [])
        return x

    def text(self, s: str) -> str:
        return text_form(s, self.ws, self.nt) if self.ws or self.nt else s

    def vector(self, x: dict[str, Any]) -> dict[str, Any]:
        t, v = x.get("t"), list(x.get("v") or [])
        if self.p.na:
            v = [_na_value(e, t, self.ws) for e in v]
        if t == "dbl":  # JSON writes a whole double without its '.0'
            v = [float(e) if type(e) is int else e for e in v]
        if t == "chr":
            v = [self.text(e) if isinstance(e, str) else e for e in v]
        if self.dtype:
            if t == "int":
                t, v = "dbl", [None if e is None else float(e) for e in v]
            elif t == "chr" and self.numbers and any(e is not None for e in v):
                nums = [number_only(e, self.ws, self.nt) if isinstance(e, str) else None for e in v]
                if all(_as_double(n) or e is None for n, e in zip(nums, v, strict=True)):
                    t, v = "dbl", [None if n is None else n.value for n in nums]
            if all(e is None for e in v):
                t = "lgl"
        out: dict[str, Any] = {"t": t, "v": v}
        if x.get("names"):
            out["names"] = x["names"]
        return out

    def prose(self, blocks: list[Any]) -> dict[str, Any]:
        """A module output's report as the comparison reads it (its prose blocks)."""
        texts = [b for b in blocks if isinstance(b, str)]
        if self.ws:  # blocks split differently are the same prose
            return {"t": "prose", "v": [self.text(" ".join(texts))] if texts else []}
        return {"t": "prose", "v": [self.text(b) for b in texts]}

    def column(self, col: dict[str, Any], path: str) -> dict[str, Any]:
        t = col.get("t")
        if t in _VECTOR_TYPES:
            return self.vector(col)
        if t == "list":
            out = {**col, "v": [self.value(e, path) for e in col.get("v") or []]}
            # a list of cells as the vector of them, formed as a whole (see list())
            for shaped in (col, out) if self.dtype else ():
                vec = _scalar_vector(shaped)
                if vec is not None:
                    return self.vector(vec)
            return out
        formed: dict[str, Any] = self.value(col, path)
        return formed

    def frame(self, x: dict[str, Any], path: str) -> dict[str, Any]:
        cols = []
        for n, col in zip(x.get("names") or [], x.get("v") or [], strict=False):
            sub = _sub(path, n)
            if sub in self.o.ignore:
                continue
            if self.o.is_presence(n, sub) and col.get("t") != "presence":
                col = _presence_view(col)
            cols.append((n, self.column(col, sub)))
        if self.order:
            cols.sort(key=lambda nc: nc[0])
        out = {
            "t": "df",
            "nrow": x.get("nrow", 0),
            "names": [n for n, _ in cols],
            "v": [c for _, c in cols],
        }
        return self.sort_rows(out, path) if self.p.sorts_rows else out

    def sort_rows(self, df: dict[str, Any], path: str) -> dict[str, Any]:
        """*df* with its rows sorted by the row key, then by the whole row."""
        nrow, cols = df["nrow"], df["v"]
        cells = [_cells(c, nrow) for c in cols]
        if nrow < 2 or any(c is None for c in cells):
            return df
        sigs = [[_sig(v) for v in c or []] for c in cells]
        numeric = [c.get("t") in _NUMERIC for c in cols]
        key = _key_columns(self.keys.get(path, ()), _occurrences(df["names"]), numeric)
        order = sorted(range(nrow), key=lambda i: ([sigs[k][i] for k in key], [s[i] for s in sigs]))
        return {**df, "v": [_pick(c, order) for c in cols]}

    def list(self, x: dict[str, Any], path: str) -> dict[str, Any]:
        t, names, items = x.get("t"), x.get("names"), list(x.get("v") or [])
        key = self.p.module_keys.get(_module_name(x) or "")
        if key:
            self.keys[_sub(path, "table")] = key
        kept: list[tuple[str | None, Any]] = []
        cells: list[Any] = []  # the elements kept, as they are
        for i, el in enumerate(items):
            n = names[i] if names and i < len(names) else None
            sub = _sub(path, n) if n else f"{path}[{i}]"
            if sub in self.o.ignore:
                continue
            cells.append(el)
            if n == "report" and t == "module_output":
                if self.o.report == "ignore":
                    continue
                if self.o.report == "prose":
                    kept.append((n, self.report(el)))
                    continue
            if n and self.o.is_presence(n, sub) and el.get("t") != "presence":
                el = _presence_view(el)
            # a traffic light is compared exactly (FIDELITY.md §2.3)
            exact = n == "traffic_light" and t == "module_output"
            kept.append((n, el if exact else self.value(el, sub)))
        unique = names and len(set(names)) == len(names)
        if self.order and unique and not self.o.strict_names:
            kept.sort(key=lambda ne: ne[0] or "")
        out = {
            **x,
            "names": [n or "" for n, _ in kept] if names else names,
            "v": [e for _, e in kept],
        }
        if t != "list" or not self.dtype:
            return out
        # list shapes: the elements as a vector or data frame formed as a whole (one
        # cell alone could read as a number where its column is text), or else
        # once the elements have their forms (a list of one-element lists)
        for shaped in ({**x, "v": cells}, out):
            vec = _scalar_vector(shaped)
            if vec is not None:
                return self.vector(vec)
            frame = _records_as_frame(shaped)
            if frame is not None:
                return self.frame(frame, path)
        return out

    def report(self, x: Any) -> dict[str, Any]:
        if isinstance(x, dict) and x.get("t") == "prose":
            return self.prose(x.get("v") or [])
        return self.prose(Comparator._prose(x) if isinstance(x, dict) else [])

    def project(self, r: Any, p: Any, path: str = "") -> Any:
        """*p* (a form) cut to the data-frame columns the form *r* has."""
        if not (isinstance(r, dict) and isinstance(p, dict)):
            return p
        rt, pt = r.get("t"), p.get("t")
        if rt == "df" and pt == "df":
            have = set(_occurrences(r.get("names") or []))
            keep = [i for i, nk in enumerate(_occurrences(p.get("names") or [])) if nk in have]
            if len(keep) == len(p.get("names") or []):
                return p
            out = {**p, "names": [p["names"][i] for i in keep], "v": [p["v"][i] for i in keep]}
            return self.sort_rows(out, path) if self.p.sorts_rows else out
        if rt in _LISTS and pt in _LISTS:
            rn, pn = r.get("names"), p.get("names")
            rv, pv = r.get("v") or [], p.get("v") or []
            if rn and pn:
                rmap = dict(zip(rn, rv, strict=False))
                v = [
                    self.project(rmap[n], el, _sub(path, n)) if n in rmap else el
                    for n, el in zip(pn, pv, strict=False)
                ]
                return {**p, "v": v}
            if not rn and not pn and len(rv) == len(pv):
                v = [
                    self.project(a, b, f"{path}[{i}]")
                    for i, (a, b) in enumerate(zip(rv, pv, strict=True))
                ]
                return {**p, "v": v}
        return p


def form(x: Any, prof: Profile | None = None, options: Options | None = None) -> Any:
    """The canonical form of one side's value *x* under *prof* (``profile()`` by
    default), as the comparison with *options* sees it: ignored elements left
    out, ``presence`` elements as whether each value is there, a module output's
    report as its prose."""
    return _Form(prof or profile(), options or Options()).value(x)


def paired(
    r: Any, p: Any, prof: Profile | None = None, options: Options | None = None
) -> tuple[Any, Any]:
    """The paired projection: the forms of *r* and *p*, with Python's data frames
    cut to the columns R has (FIDELITY.md §4.1). The columns only R has stay in R's
    form, so R's side never depends on Python's."""
    f = _Form(prof or profile(), options or Options())
    fr, fp = f.value(r), f.value(p)
    if "extra_column" in f.p.rules:
        fp = f.project(fr, fp)
    return fr, fp


def digests(
    r: Any, p: Any, prof: Profile | None = None, options: Options | None = None
) -> tuple[str, str]:
    """The lock digests (``parity.lockfile.digest``) of the paired projection."""
    fr, fp = paired(r, p, prof, options)
    return digest(fr), digest(fp)


# -- the comparison --------------------------------------------------------------------


@dataclass
class Comparison:
    """What :func:`compare` found."""

    #: the differences (at most ``Comparator.MAX_PROBLEMS``)
    problems: list[str]
    #: every differing path, element indices written ``[]``
    paths: list[str]
    #: what differs without being a difference: the columns only Python has
    info: list[str]

    @property
    def equal(self) -> bool:
        return not self.paths


class _Comparator(Comparator):
    """:class:`parity.compare.Comparator` under canon's rules; each override falls
    back to it when its rules are off."""

    def __init__(self, options: Options, prof: Profile) -> None:
        super().__init__(options)
        self.p = prof
        rules = prof.rules
        self.dtype = "dtype" in rules
        self.ws = "whitespace" in rules
        self.nt = "number_text" in rules
        self.extra = "extra_column" in rules
        self.form = _Form(prof, options)
        self.shapes = _Form(prof, options, shapes_only=True)
        self.info: list[str] = []
        #: how deep inside data frames the comparison is (numbers in text compare
        #: to the relative tolerance there too)
        self.tables = 0
        #: the paths of the module outputs compared so far
        self.outputs: set[str] = set()

    # -- scalars ------------------------------------------------------------------

    def _number_eq(self, a: float, b: float, half: float, whole: bool = False) -> bool:
        """Numbers read from text: within *half* a unit in the last printed digit of
        the less precise side, give or take float noise (none between *whole*
        numbers); in a data frame also to the relative tolerance."""
        if a == b:
            return True
        if self.tables and not self._num_eq(a, b):
            return False
        noise = 0.0 if whole else _NOISE * max(abs(a), abs(b))
        return abs(a - b) <= half + noise

    def _token_eq(self, x: str | Number, y: str | Number) -> bool:
        if isinstance(x, str) or isinstance(y, str):
            return x == y
        if not self.nt or x.literal or y.literal:
            return x.text == y.text
        half = max(x.half, y.half)
        return x.bound == y.bound and self._number_eq(x.value, y.value, half, x.whole and y.whole)

    def _str_eq(self, a: Any, b: Any) -> bool:
        if a is None or b is None or not (self.ws or self.nt):
            return super()._str_eq(a, b)
        ta, tb = tokens(str(a), self.ws, self.nt), tokens(str(b), self.ws, self.nt)
        return len(ta) == len(tb) and all(map(self._token_eq, ta, tb))

    def _text_number_eq(self, s: Any, x: Any) -> bool:
        """Whether the text *s* is only the number *x*."""
        n = number_only(s, self.ws, self.nt) if isinstance(s, str) else None
        if n is None or isinstance(x, str | bool):
            return False
        return self._number_eq(n.value, float(x), n.half if self.nt else 0.0)

    def _cell_eq(self, a: Any, at: str, b: Any, bt: str) -> bool:
        if self.dtype and at != bt and a is not None and b is not None:
            if "lgl" in (at, bt):  # a flag is neither a number nor text
                return False
            if at == "chr" and bt in _NUMERIC:
                return self._text_number_eq(a, b)
            if bt == "chr" and at in _NUMERIC:
                return self._text_number_eq(b, a)
        if at in _NUMERIC or bt in _NUMERIC:
            return self._num_eq(a, b)
        if at == "cplx" or bt == "cplx":
            return self._cplx_eq(a, b)
        if at == "chr" or bt == "chr":
            return self._str_eq(a, b)
        return bool(a == b)

    def vector(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        if not (self.dtype or self.p.na or self.ws or self.nt):
            super().vector(r, p, path)
            return
        rv, pv = r.get("v", []), p.get("v", [])
        if len(rv) != len(pv):
            self.fail(path, f"length R={len(rv)} py={len(pv)}; R={_fmt(rv)} py={_fmt(pv)}")
            return
        rt, pt = r["t"], p["t"]
        if self.p.na:
            rv = [_na_value(x, rt, self.ws) for x in rv]
            pv = [_na_value(x, pt, self.ws) for x in pv]
        mismatch = rt != pt and not (rt in _NUMERIC and pt in _NUMERIC)
        if (
            mismatch
            and not self.dtype
            and not all(v is None for v in rv)
            and not all(v is None for v in pv)
        ):
            self.fail(path, f"type R={rt} py={pt}; R={_fmt(rv[:5])} py={_fmt(pv[:5])}")
            return
        for i, (a, b) in enumerate(zip(rv, pv, strict=True)):
            if not self._cell_eq(a, rt, b, pt):
                self.fail(f"{path}[{i}]", f"R={_fmt(a)} py={_fmt(b)}")
        if r.get("names") and p.get("names") and r["names"] != p["names"]:
            self.fail(path, f"names R={_fmt(r['names'])} py={_fmt(p['names'])}")

    # -- data frames ----------------------------------------------------------------

    def frame(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        self.tables += 1
        try:
            if "order" in self.p.rules or self.extra:
                self._frame(r, p, path)
            else:
                super().frame(r, p, path)
        finally:
            self.tables -= 1

    def _columns(self, x: dict[str, Any], path: str) -> dict[tuple[str, int], dict[str, Any]]:
        """The columns of *x* by (name, occurrence), ``ignore``d ones left out."""
        return {
            nk: col
            for nk, col in zip(_occurrences(x["names"]), x["v"], strict=False)
            if _sub(path, nk[0]) not in self.o.ignore
        }

    def _frame(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        rc, pc = self._columns(r, path), self._columns(p, path)
        for nk, col in rc.items():
            if nk not in pc:
                self.fail(
                    _sub(path, nk[0]), f"a column only R has (R={_fmt(col.get('v', [])[:5])})"
                )
        for nk in pc:
            if nk in rc:
                continue
            if self.extra:
                self.info.append(f"{_sub(path, nk[0])}: a column only Python has")
            else:
                self.fail(_sub(path, nk[0]), "a column only Python has")
        common = [nk for nk in rc if nk in pc]
        if "order" not in self.p.rules and self.o.col_order:
            rn, pn = [nk[0] for nk in common], [nk[0] for nk in pc if nk in rc]
            if rn != pn:
                self.fail(path, f"columns R={rn} py={pn}")
        pairs = self._pairs(r, p, rc, pc, common, path)
        if pairs is None:
            return
        rows_r, rows_p = [i for i, _ in pairs], [j for _, j in pairs]
        for nk in common:
            sub = _sub(path, nk[0])
            self.value(_pick(rc[nk], rows_r), _pick(pc[nk], rows_p), sub, nk[0])

    def _formed(self, col: dict[str, Any], name: str, path: str) -> dict[str, Any]:
        """The form of a column, for its row signatures (a ``presence`` column as its mask)."""
        sub = _sub(path, name)
        if self.o.is_presence(name, sub) and col.get("t") != "presence":
            col = _presence_view(col)
        return self.form.column(col, sub)

    def _pairs(
        self,
        r: dict[str, Any],
        p: dict[str, Any],
        rc: dict[tuple[str, int], dict[str, Any]],
        pc: dict[tuple[str, int], dict[str, Any]],
        common: list[tuple[str, int]],
        path: str,
    ) -> list[tuple[int, int]] | None:
        """R's and Python's rows paired up; the rows without a partner are reported."""
        nr, np_ = r.get("nrow", 0), p.get("nrow", 0)
        fr = [self._formed(rc[nk], nk[0], path) for nk in common]
        fp = [self._formed(pc[nk], nk[0], path) for nk in common]
        cells_r = [c for c in (_cells(col, nr) for col in fr) if c is not None]
        cells_p = [c for c in (_cells(col, np_) for col in fp) if c is not None]
        if not self.p.sorts_rows or len(cells_r) < len(fr) or len(cells_p) < len(fp):
            if nr != np_:
                self.fail(path, f"nrow R={nr} py={np_}")
                return None
            order_r = order_p = list(range(nr))
            if (self.o.unordered_root and path == "") or path in self.o.unordered:
                names = [n for n, _ in common]
                rcols = {n: rc[nk] for n, nk in zip(names, common, strict=True)}
                pcols = {n: pc[nk] for n, nk in zip(names, common, strict=True)}
                order_r = self._row_order(self._keys(rcols, path), names, nr)
                order_p = self._row_order(self._keys(pcols, path), names, np_)
            return list(zip(order_r, order_p, strict=True))
        sig_r = [tuple(_sig(c[i]) for c in cells_r) for i in range(nr)]
        sig_p = [tuple(_sig(c[j]) for c in cells_p) for j in range(np_)]
        numeric = [
            a.get("t") in _NUMERIC or b.get("t") in _NUMERIC for a, b in zip(fr, fp, strict=True)
        ]
        key = _key_columns(self.form.keys.get(path, ()), common, numeric)
        groups: dict[tuple[str, ...], tuple[list[int], list[int]]] = {}
        for i, s in enumerate(sig_r):
            groups.setdefault(tuple(s[k] for k in key), ([], []))[0].append(i)
        for j, s in enumerate(sig_p):
            groups.setdefault(tuple(s[k] for k in key), ([], []))[1].append(j)

        def equal(i: int, j: int) -> bool:
            scratch = _Comparator(self.o, self.p)
            scratch.tables = 1
            for nk in common:
                scratch.value(_pick(rc[nk], [i]), _pick(pc[nk], [j]), _sub(path, nk[0]), nk[0])
            return not scratch.paths

        pairs: list[tuple[int, int]] = []
        for k in sorted(groups):
            rows_r, rows_p = groups[k]
            if len(rows_r) == len(rows_p) == 1:
                pairs.append((rows_r[0], rows_p[0]))
                continue
            # a key collision, or rows on one side only: full-row multisets. Pair the
            # rows of the same form, then those equal within the tolerances, then the
            # rest in order (their differences are reported), and report the leftovers
            same: dict[tuple[str, ...], list[int]] = {}
            for j in rows_p:
                same.setdefault(sig_p[j], []).append(j)
            left_r = []
            for i in rows_r:
                if same.get(sig_r[i]):
                    pairs.append((i, same[sig_r[i]].pop(0)))
                else:
                    left_r.append(i)
            left_p = sorted(j for js in same.values() for j in js)
            rest_r: list[int] = []
            for i in left_r:
                partner = next((j for j in left_p if equal(i, j)), None)
                if partner is None:
                    rest_r.append(i)
                else:
                    pairs.append((i, partner))
                    left_p.remove(partner)
            rest_r.sort(key=lambda i: sig_r[i])
            left_p.sort(key=lambda j: sig_p[j])
            pairs.extend(zip(rest_r, left_p, strict=False))
            for i in rest_r[len(left_p) :]:
                self.fail(path, f"a row only R has: {self._row(rc, common, key, i)}")
            for j in left_p[len(rest_r) :]:
                self.fail(path, f"a row only Python has: {self._row(pc, common, key, j)}")
        return pairs

    @staticmethod
    def _row(
        cols: dict[tuple[str, int], dict[str, Any]],
        common: list[tuple[str, int]],
        key: list[int],
        i: int,
    ) -> str:
        """Row *i* as a message shows it: its key, or all of it without one."""
        row = {}
        for nk in [common[k] for k in key] or common:
            v = cols[nk].get("v") or []
            row[nk[0]] = v[i] if i < len(v) else None
        return _fmt(row)

    # -- the rest -----------------------------------------------------------------

    def _empty(self, x: dict[str, Any]) -> bool:
        return self.form.empty(x) if self.p.na else _is_empty(x)

    def _nulled(self, x: dict[str, Any]) -> dict[str, Any]:
        """A list with its empty elements as NULL, so a list of NAs and length-one
        vectors reads as a vector (``parity.compare._as_vector``)."""
        if x.get("t") != "list" or not any(isinstance(e, dict) and self._empty(e) for e in x["v"]):
            return x
        return {**x, "v": [{"t": "null"} if self._empty(e) else e for e in x["v"]]}

    def _exact(self, r: dict[str, Any], p: dict[str, Any], path: str, name: str | None) -> None:
        """*r* and *p* compared as :class:`parity.compare.Comparator` compares them."""
        plain = Comparator(self.o)
        plain.value(r, p, path, name)
        self.paths |= plain.paths
        self.problems += plain.problems[: max(0, self.MAX_PROBLEMS - len(self.problems))]

    def value(
        self, r: dict[str, Any], p: dict[str, Any], path: str = "", name: str | None = None
    ) -> None:
        if path in self.o.ignore:
            return
        if name == "traffic_light" and path.removesuffix(name).removesuffix(".") in self.outputs:
            self._exact(r, p, path, name)  # FIDELITY.md §2.3
            return
        if "module_output" in (r.get("t"), p.get("t")):
            self.outputs.add(path)
        if self.p.na:
            if self._empty(r) and self._empty(p):
                return
            r, p = self._nulled(r), self._nulled(p)
        kinds = {r.get("t"), p.get("t")}
        if self.dtype and "list" in kinds and len(kinds & {"df", *_VECTOR_TYPES}) == 1:
            # a list against a vector or data frame: both reshaped alike, their text
            # left as it is for the comparison to read its printed precision
            r, p = self.shapes.value(r, path), self.shapes.value(p, path)
        key = self.p.module_keys.get(_module_name(r) or _module_name(p) or "")
        if key:
            self.form.keys[_sub(path, "table")] = key
        super().value(r, p, path, name)

    def report(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        prose = "prose" in (r.get("t"), p.get("t"))
        if self.o.report != "prose" or not (self.ws or self.nt or prose):
            super().report(r, p, path)
            return

        def blocks(x: dict[str, Any]) -> list[str]:
            return list(x.get("v") or []) if x.get("t") == "prose" else self._prose(x)

        rb, pb = blocks(r), blocks(p)
        if rb == pb or self._str_eq(" ".join(rb), " ".join(pb)):
            return
        for i in range(max(len(rb), len(pb))):
            a = rb[i] if i < len(rb) else None
            b = pb[i] if i < len(pb) else None
            if a is None or b is None or not self._str_eq(a, b):
                self.fail(f"{path}[{i}]", f"R={_fmt(a, 400)} py={_fmt(b, 400)}")
                return
        self.fail(path, f"R={_fmt(' '.join(rb), 400)} py={_fmt(' '.join(pb), 400)}")

    def presence(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        def view(x: dict[str, Any]) -> dict[str, Any]:
            """A form's mask (``presence``) as values that are there or not."""
            if x.get("t") != "presence":
                return x
            return {"t": "chr", "v": ["there" if there else None for there in x.get("v") or []]}

        super().presence(view(r), view(p), path)


def compare(
    r: Any, p: Any, prof: Profile | None = None, options: Options | None = None
) -> Comparison:
    """R's value *r* and Python's *p* compared under *prof* (``profile()`` by
    default) and the case's *options* (``compare:`` in its YAML)."""
    c = _Comparator(options or Options(), prof or profile())
    c.value(r, p, "")
    return Comparison(c.problems, sorted(c.paths), c.info)
