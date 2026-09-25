"""Structural comparison of canonical R and Python values.

The comparison is strict about content (strings must match exactly, numbers
within a relative tolerance, row and column order preserved) and lenient
only about representation details that carry no meaning:

* ``int`` and ``dbl`` vectors compare numerically;
* ``NULL``, a zero-length vector/list, and a single ``NA`` are equivalent
  (R often holds ``NA``/``list()``/``NULL`` in a list-column cell where
  Python holds ``None``/``[]``);
* ``NaN`` and ``NA`` are equivalent for doubles;
* a list whose elements are all length-1 vectors equals the vector of them;
* a data frame with no rows and no columns (R's ``tibble()``) is empty too;
* a matrix with a single row or column equals the vector of its values.
* an unnamed list of records (named lists of scalars with the same names)
  equals the data frame of them (the Python encoder turns a list of
  same-keyed dicts into a data frame; R keeps a list of named lists).

Per-case options (``compare:`` in the case YAML):

``ignore``      list of element paths to skip (``table.formatted``, ``report``)
``unordered``   list of paths (or ``true`` for the root) whose data-frame rows
                may be in any order
``tol``         relative tolerance for doubles (default ``1e-9``)
``report``      ``prose`` (default: compare text blocks, skip table widgets
                and R code chunks), ``exact`` or ``ignore``
``col_order``   compare data-frame column order (default ``true``)
``ws``          collapse runs of whitespace before comparing strings
``strict_names`` compare named lists by position, names in order, instead of
                as maps (a name R repeats must be repeated in Python even
                without this)
``error``       when R raised an error, how Python's error message must match
                R's (see :func:`error_matches`): ``contains`` (default),
                ``exact`` or ``any``
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

_NULLISH = {"null"}
_VECTOR_TYPES = {"chr", "dbl", "int", "lgl", "cplx", "raw"}


@dataclass
class Options:
    ignore: set[str] = field(default_factory=set)
    unordered: set[str] = field(default_factory=set)
    unordered_root: bool = False
    tol: float = 1e-9
    report: str = "prose"
    col_order: bool = True
    ws: bool = False
    strict_names: bool = False
    error: str = "contains"

    @classmethod
    def from_case(cls, spec: dict[str, Any] | None) -> Options:
        spec = spec or {}
        unordered = spec.get("unordered", [])
        root = unordered is True
        return cls(
            ignore=set(spec.get("ignore", [])),
            unordered=set() if root else set(unordered or []),
            unordered_root=root,
            tol=float(spec.get("tol", 1e-9)),
            report=spec.get("report", "prose"),
            col_order=bool(spec.get("col_order", True)),
            ws=bool(spec.get("ws", False)),
            strict_names=bool(spec.get("strict_names", False)),
            error=_error_mode(spec.get("error", "contains")),
        )


def _error_mode(mode: Any) -> str:
    if mode not in ("contains", "exact", "any"):
        raise ValueError(f"compare: error must be contains, exact or any, not {mode!r}")
    return str(mode)


# cli/rlang bullets at the start of a line of an R condition message
_BULLET = re.compile(r"(?m)^[ \t]*[!✖ℹ✔•*](?=[ \t])[ \t]*")
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_ERROR_IN = re.compile(r"^(?:Error in .*? : |Error: )", re.S)


def normalize_error(msg: str) -> str:
    """An error message without ANSI colours, a leading ``Error in <call> :``,
    cli bullets (``!``, ``✖``, ``ℹ``, ...) and runs of whitespace."""
    msg = _ANSI.sub("", msg)
    msg = _ERROR_IN.sub("", msg.strip())
    msg = _BULLET.sub("", msg)
    return re.sub(r"\s+", " ", msg).strip()


def error_matches(r_msg: str | None, py_msg: str | None, mode: str = "contains") -> bool:
    """Whether Python's error message matches R's.

    ``exact``: equal after :func:`normalize_error`. ``contains`` (the default):
    also when R's message is part of Python's (Python adds detail, e.g. a
    module name) or Python's is the start of R's (Python leaves out R's cli
    details). ``any``: any error matches.
    """
    if mode == "any":
        return True
    r, p = normalize_error(r_msg or ""), normalize_error(py_msg or "")
    if r == p:
        return True
    if mode == "exact":
        return False
    return r in p or (p != "" and r.startswith(p))


def _is_empty(x: dict[str, Any]) -> bool:
    t = x.get("t")
    if t in _NULLISH:
        return True
    if t in _VECTOR_TYPES or t == "list":
        v = x.get("v", [])
        return len(v) == 0 or (len(v) == 1 and v[0] is None)
    if t == "df":
        return x.get("nrow") == 0 and not x.get("names")
    return False


def _as_vector(x: dict[str, Any]) -> dict[str, Any] | None:
    """View a list of length-1 vectors as a vector.

    A named list keeps its names, so an R named vector (``c(power_n = 0)``)
    equals the Python ``dict`` of the same names and values.
    """
    if x.get("t") in _VECTOR_TYPES:
        return x
    if x.get("t") != "list":
        return None
    vals = []
    types = set()
    for el in x.get("v", []):
        if el.get("t") in _NULLISH:
            vals.append(None)
            continue
        if el.get("t") not in _VECTOR_TYPES or len(el.get("v", [])) != 1:
            return None
        types.add(el["t"])
        vals.append(el["v"][0])
    t = "dbl" if types <= {"int", "dbl"} and types else (types.pop() if len(types) == 1 else "chr")
    out: dict[str, Any] = {"t": t, "v": vals}
    if x.get("names"):
        out["names"] = x["names"]
    return out


def _records_as_frame(x: dict[str, Any]) -> dict[str, Any] | None:
    """View an unnamed list of same-named records of scalars as a data frame."""
    items = x.get("v", [])
    if x.get("t") != "list" or x.get("names") or not items:
        return None
    names = items[0].get("names")
    if not names:
        return None
    cols: list[list[dict[str, Any]]] = [[] for _ in names]
    for rec in items:
        if rec.get("t") != "list" or rec.get("names") != names:
            return None
        for k, el in enumerate(rec.get("v", [])):
            if not (_is_empty(el) or (el.get("t") in _VECTOR_TYPES and len(el.get("v", [])) == 1)):
                return None
            cols[k].append(el)
    columns = [_as_vector({"t": "list", "names": None, "v": col}) for col in cols]
    if any(c is None for c in columns):
        return None
    return {"t": "df", "nrow": len(items), "names": list(names), "v": columns}


def _fmt(x: Any, limit: int = 160) -> str:
    s = repr(x)
    return s if len(s) <= limit else s[: limit - 3] + "..."


class Comparator:
    def __init__(self, options: Options) -> None:
        self.o = options
        self.problems: list[str] = []

    def fail(self, path: str, msg: str) -> None:
        if len(self.problems) < 25:
            self.problems.append(f"{path or '<root>'}: {msg}")

    # -- scalars ---------------------------------------------------------------

    def _num_eq(self, a: Any, b: Any) -> bool:
        if a is None or a == "NaN":
            return b is None or b == "NaN"
        if b is None or b == "NaN":
            return False
        if isinstance(a, str) or isinstance(b, str):
            return a == b
        if isinstance(a, bool) or isinstance(b, bool):
            return bool(a) == bool(b)
        fa, fb = float(a), float(b)
        if math.isinf(fa) or math.isinf(fb):
            return fa == fb
        return math.isclose(fa, fb, rel_tol=self.o.tol, abs_tol=self.o.tol * 1e-3)

    def _str_eq(self, a: Any, b: Any) -> bool:
        if a is None or b is None:
            return a is None and b is None
        if self.o.ws:
            a, b = re.sub(r"\s+", " ", str(a)).strip(), re.sub(r"\s+", " ", str(b)).strip()
        return a == b

    def vector(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        rv, pv = r.get("v", []), p.get("v", [])
        if len(rv) != len(pv):
            self.fail(path, f"length R={len(rv)} py={len(pv)}; R={_fmt(rv)} py={_fmt(pv)}")
            return
        rt, pt = r["t"], p["t"]
        numeric = {"int", "dbl"}
        mismatch = rt != pt and not (rt in numeric and pt in numeric)
        if mismatch and not all(v is None for v in rv) and not all(v is None for v in pv):
            self.fail(path, f"type R={rt} py={pt}; R={_fmt(rv[:5])} py={_fmt(pv[:5])}")
            return
        for i, (a, b) in enumerate(zip(rv, pv, strict=True)):
            same = (
                self._num_eq(a, b)
                if rt in numeric or pt in numeric
                else (self._str_eq(a, b) if rt == "chr" or pt == "chr" else a == b)
            )
            if not same:
                self.fail(f"{path}[{i}]", f"R={_fmt(a)} py={_fmt(b)}")
        if r.get("names") and p.get("names") and r["names"] != p["names"]:
            self.fail(path, f"names R={_fmt(r['names'])} py={_fmt(p['names'])}")

    # -- containers --------------------------------------------------------------

    def frame(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        if r["nrow"] != p["nrow"]:
            self.fail(path, f"nrow R={r['nrow']} py={p['nrow']}")
        rn, pn = r["names"], p["names"]
        rn_kept = [n for n in rn if f"{path}.{n}".lstrip(".") not in self.o.ignore]
        pn_kept = [n for n in pn if f"{path}.{n}".lstrip(".") not in self.o.ignore]
        if self.o.col_order and rn_kept != pn_kept:
            self.fail(path, f"columns R={rn_kept} py={pn_kept}")
        elif set(rn_kept) != set(pn_kept):
            self.fail(
                path,
                f"column sets differ: only R={sorted(set(rn_kept) - set(pn_kept))} "
                f"only py={sorted(set(pn_kept) - set(rn_kept))}",
            )
        if r["nrow"] != p["nrow"]:
            return
        rcols = dict(zip(rn, r["v"], strict=True))
        pcols = dict(zip(pn, p["v"], strict=True))
        common = [n for n in rn_kept if n in pcols]
        order_r = order_p = list(range(r["nrow"]))
        if (self.o.unordered_root and path == "") or path in self.o.unordered:
            order_r = self._row_order(rcols, common, r["nrow"])
            order_p = self._row_order(pcols, common, p["nrow"])
        for n in common:
            self.column(rcols[n], pcols[n], f"{path}.{n}".lstrip("."), order_r, order_p)

    def _row_order(self, cols: dict[str, Any], names: list[str], nrow: int) -> list[int]:
        def cell(col: dict[str, Any], i: int) -> str:
            v = col.get("v", [])
            return repr(v[i]) if i < len(v) else ""

        return sorted(range(nrow), key=lambda i: tuple(cell(cols[n], i) for n in names))

    def column(
        self,
        r: dict[str, Any],
        p: dict[str, Any],
        path: str,
        order_r: list[int],
        order_p: list[int],
    ) -> None:
        def pick(x: dict[str, Any], order: list[int]) -> dict[str, Any]:
            v = x.get("v", [])
            return {**x, "v": [v[i] for i in order] if len(v) == len(order) else v}

        self.value(pick(r, order_r), pick(p, order_p), path)

    def named(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        rn = r.get("names") or []
        pn = p.get("names") or []
        repeated = sorted({n for n in rn if rn.count(n) > 1 and n != ""})
        if self.o.strict_names or repeated:
            # a map would collapse the repeated names: compare by position
            if rn != pn:
                what = f"repeated names {repeated} in R; " if repeated else ""
                self.fail(path, f"{what}names R={_fmt(rn)} py={_fmt(pn)}")
                return
            for i, (n, a, b) in enumerate(zip(rn, r.get("v", []), p.get("v", []), strict=True)):
                sub = f"{path}.{n}".lstrip(".") if n else f"{path}[{i}]"
                if sub in self.o.ignore:
                    continue
                if n == "report" and r.get("t") == "module_output":
                    self.report(a, b, sub)
                else:
                    self.value(a, b, sub)
            return
        rmap = dict(zip(rn, r.get("v", []), strict=False))
        pmap = dict(zip(pn, p.get("v", []), strict=False))
        for n in rn:
            sub = f"{path}.{n}".lstrip(".")
            if sub in self.o.ignore:
                continue
            if n not in pmap:
                if not _is_empty(rmap[n]):
                    self.fail(sub, f"missing in Python (R={_fmt(rmap[n])})")
                continue
            if n == "report" and r.get("t") == "module_output":
                self.report(rmap[n], pmap[n], sub)
            else:
                self.value(rmap[n], pmap[n], sub)
        for n in pn:
            sub = f"{path}.{n}".lstrip(".")
            if n not in rmap and sub not in self.o.ignore and not _is_empty(pmap[n]):
                self.fail(sub, f"extra in Python (py={_fmt(pmap[n])})")

    def unnamed(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        rv, pv = r.get("v", []), p.get("v", [])
        if len(rv) != len(pv):
            self.fail(path, f"list length R={len(rv)} py={len(pv)}")
            return
        for i, (a, b) in enumerate(zip(rv, pv, strict=True)):
            self.value(a, b, f"{path}[{i}]")

    # -- report prose --------------------------------------------------------------

    # an R code chunk (scroll_table()), also when embedded in a longer string,
    # e.g. collapse_section(scroll_table(x)); its closing fence is on its own line
    _R_CHUNK = re.compile(r"```\{r\}.*?\n```(?=\n|$)", re.S)

    @classmethod
    def _prose(cls, x: dict[str, Any]) -> list[str]:
        blocks: list[str] = []

        def walk(node: dict[str, Any]) -> None:
            t = node.get("t")
            if t == "chr":
                for s in node.get("v", []):
                    if s is None:
                        continue
                    stripped = cls._R_CHUNK.sub(" ", s).strip()
                    if not stripped or stripped.startswith("```{r}"):
                        continue
                    blocks.append(re.sub(r"\s+", " ", stripped))
            elif t == "list":
                for el in node.get("v", []):
                    walk(el)

        walk(x)
        return blocks

    def report(self, r: dict[str, Any], p: dict[str, Any], path: str) -> None:
        if self.o.report == "ignore":
            return
        if self.o.report == "exact":
            self.value(r, p, path)
            return
        rb, pb = self._prose(r), self._prose(p)
        # the same prose split differently into blocks (a pytacheck block list
        # where R pasted a table chunk into one string) is still the same prose
        if rb != pb and " ".join(rb) != " ".join(pb):
            for i in range(max(len(rb), len(pb))):
                a = rb[i] if i < len(rb) else None
                b = pb[i] if i < len(pb) else None
                if a != b:
                    self.fail(f"{path}[{i}]", f"R={_fmt(a, 400)} py={_fmt(b, 400)}")
                    break

    # -- dispatch -----------------------------------------------------------------

    def value(self, r: dict[str, Any], p: dict[str, Any], path: str = "") -> None:
        if path in self.o.ignore:
            return
        if _is_empty(r) and _is_empty(p):
            return
        rt, pt = r.get("t"), p.get("t")
        # a one-row / one-column matrix is just a vector (jsonlite artefact)
        if rt == "matrix" and pt != "matrix" and 1 in r.get("dim", []):
            r, rt = r["v"], r["v"]["t"]
        if pt == "matrix" and rt != "matrix" and 1 in p.get("dim", []):
            p, pt = p["v"], p["v"]["t"]
        if rt == "list" and pt == "df" and (rf := _records_as_frame(r)) is not None:
            r, rt = rf, "df"
        if pt == "list" and rt == "df" and (pf := _records_as_frame(p)) is not None:
            p, pt = pf, "df"
        if rt in _VECTOR_TYPES or pt in _VECTOR_TYPES:
            rv, pv = _as_vector(r), _as_vector(p)
            if rv is None or pv is None:
                self.fail(path, f"kind R={rt} py={pt}; R={_fmt(r)} py={_fmt(p)}")
                return
            self.vector(rv, pv, path)
            return
        if rt == "df" and pt == "df":
            self.frame(r, p, path)
            return
        if rt in ("list", "module_output", "paper", "paperlist") and pt in (
            "list",
            "module_output",
            "paper",
            "paperlist",
        ):
            if r.get("names") or p.get("names"):
                self.named(r, p, path)
            else:
                self.unnamed(r, p, path)
            return
        if rt != pt:
            self.fail(path, f"kind R={rt} py={pt}; R={_fmt(r)} py={_fmt(p)}")
            return
        if rt == "matrix":
            self.value(r["v"], p["v"], path)
            return
        if r != p:
            self.fail(path, f"R={_fmt(r)} py={_fmt(p)}")


def compare(r: dict[str, Any], p: dict[str, Any], options: Options) -> list[str]:
    """Return a list of human-readable differences (empty when equal)."""
    c = Comparator(options)
    c.value(r, p, "")
    return c.problems


def summarize(problems: Iterable[str]) -> str:
    return "\n".join(f"  - {p}" for p in problems)
