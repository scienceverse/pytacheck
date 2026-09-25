"""The accuracy report: metacheck and pytacheck on the realistic corpus.

``python -m parity accuracy [--generate] [--gate] [-m MODULE] [--md OUT]``

Parity cases check functions branch by branch; this report checks the end
result users see. It runs every module of ``parity/accuracy/matrix.toml`` on
every input there (21 real papers through the offline paper modules, 10 real
repositories through the repository modules with ``local_only = TRUE``), each
with its default arguments, in Python, and scores each output against R's:

========================  ===========================================================
``run``                   both succeed, or both fail
``traffic_light``         exact
``summary_table``         column names and order and the row count exact; numbers to
                          a relative 1e-9; text after whitespace normalisation
``table``                 row count and column names exact; the rows as a multiset
                          (text whitespace-normalised, numbers to 10 digits): F1
``summary_text``          after whitespace normalisation (runs of whitespace are one
                          space), and the multiset of (signed) numbers in it
``report``                the prose (as parity cases compare it), likewise
========================  ===========================================================

Each difference has a level: ``whitespace`` (equal once all whitespace is
removed: ``p =0.152`` vs ``p = 0.152``), ``wording`` (other text, same numbers)
or ``values`` (numbers, rows, traffic lights, a failed run). Every difference
must be explained by an entry of ``parity/accuracy/expected.yaml``::

    - module: ref_accuracy          # globs (* matches anything, / included)
      input: "*"                    # the input path, or its short name
      field: traffic_light          # run, traffic_light, summary_text, report,
                                    # summary_table[.<column>], table[.<column>]
      match: values                 # the highest level it explains
      kind: r_bug_fixed             # as a parity mark (a tier-1 kind)
      ref: U30
      reason: ...

``--gate`` fails on any unexplained difference, an entry that explains none (a
stale entry; checked on full runs only), a missing golden, or an output that used
the network or started R. A module whose traffic lights agree on fewer than 90%
of its inputs is a warning (``needs_review`` in the JSON report), which the
upstream sync turns into the ``needs-human-review`` label.

R's outputs are committed, gzip-compressed, in ``parity/accuracy/golden/<module>
.json.gz``, so the report needs no R. ``--generate`` rewrites them by running the
reference R (``parity/r/run_cases.R --out``, R's network disabled); the Parity
workflow regenerates them and fails when they drift.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import fnmatch
import gzip
import io
import math
import os
import re
import subprocess
import tempfile
import time
import tomllib
import warnings
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import orjson
import yaml

from parity.cases import (
    ROOT,
    RWithoutReference,
    _watch_for_r,
    check_mark,
    deterministic_ids,
    load_corpus,
    metacheck_defaults,
    utc,
)
from parity.compare import Comparator, Options, compare

ACCURACY_DIR = ROOT / "parity" / "accuracy"
MATRIX_FILE = ACCURACY_DIR / "matrix.toml"
EXPECTED_FILE = ACCURACY_DIR / "expected.yaml"
GOLDEN_DIR = ACCURACY_DIR / "golden"
OUT_DIR = ROOT / "parity" / "_out"
#: the area name of the transient case file run through parity/r/run_cases.R
AREA = "accuracy"

#: difference levels, least severe first
LEVELS = ("whitespace", "wording", "values")
#: a module whose traffic lights agree on fewer inputs than this needs human review
TL_FLOOR = 0.9
#: relative tolerance for numbers in summary tables
TOL = 1e-9
ENTRY_KEYS = frozenset({"module", "input", "field", "match", "kind", "ref", "reason"})
FLOOR_KEYS = frozenset({"module", "traffic_light", "kind", "ref", "reason"})

# short names of inputs: the path without the fixture directory it lives in
_SHORT_PREFIXES = (
    ("upstream/metacheck/tests/testthat/fixtures/", ""),
    ("upstream/metacheck/inst/demos/", "demos/"),
    ("tests/mod_data_check/fixtures/", ""),
)


def short(path: str) -> str:
    """*path* without the fixture directory it lives in (``problems/203020.xml``)."""
    for prefix, repl in _SHORT_PREFIXES:
        if path.startswith(prefix):
            return repl + path[len(prefix) :]
    return path


# -- the matrix -------------------------------------------------------------------------


@dataclass(frozen=True)
class Output:
    """One module run on one input."""

    module: str
    input: str  # repository-relative path
    kind: str  # "paper" or "repository"

    @property
    def id(self) -> str:
        return f"{self.module}.{short(self.input).replace('/', '--')}"

    def spec(self) -> dict[str, Any]:
        """The parity case (parity/r/run_cases.R) that runs it in R."""
        if self.kind == "paper":
            args: dict[str, Any] = {"paper": {"$paper": self.input}}
        else:
            args = {"paper": {"$paper": "demo"}, "local_path": {"$file": self.input}}
            args["local_only"] = True
        return {"id": self.id, "module": self.module, "args": args}


def load_matrix(path: Path = MATRIX_FILE) -> list[Output]:
    """Every output of the matrix, module by module; its inputs must be corpus inputs."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    corpus = load_corpus()
    outputs: list[Output] = []
    problems: list[str] = []
    for kind, table in (("paper", "papers"), ("repository", "repositories")):
        part = data.get(table, {})
        problems += [
            f"{inp} is not an input of the realistic corpus (corpus.toml)"
            for inp in part.get("inputs", [])
            if inp not in corpus
        ]
        for module in part.get("modules", []):
            outputs += [Output(module, inp, kind) for inp in part.get("inputs", [])]
    ids = Counter(o.id for o in outputs)
    problems += [f"two outputs are both called {i}" for i, n in ids.items() if n > 1]
    if problems:
        raise ValueError(f"{path.name}: " + "; ".join(problems))
    return outputs


def select(outputs: list[Output], modules: list[str] | None) -> list[Output]:
    if not modules:
        return outputs
    unknown = sorted(set(modules) - {o.module for o in outputs})
    if unknown:
        raise SystemExit(f"not a module of the accuracy matrix: {', '.join(unknown)}")
    return [o for o in outputs if o.module in modules]


def by_module(outputs: Iterable[Output]) -> dict[str, list[Output]]:
    out: dict[str, list[Output]] = defaultdict(list)
    for o in outputs:
        out[o.module].append(o)
    return dict(out)


# -- R's outputs (the goldens) ----------------------------------------------------------


def golden_path(module: str) -> Path:
    return GOLDEN_DIR / f"{module}.json.gz"


def read_golden(module: str) -> dict[str, Any] | None:
    path = golden_path(module)
    if not path.exists():
        return None
    data: dict[str, Any] = orjson.loads(gzip.decompress(path.read_bytes()))
    return data


def write_golden(module: str, data: dict[str, Any]) -> None:
    raw = orjson.dumps(data, option=orjson.OPT_SORT_KEYS)
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    # mtime 0: the same outputs give the same bytes, so drift shows in `git diff`
    golden_path(module).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


#: R runs with its network disabled: a module of the matrix must not need it,
#: and an R module that tried would give other results where it is reachable
_NO_PROXY = "http://127.0.0.1:9"


def suggests_library(rscript: str) -> str:
    """The reference R's library of metacheck's suggested packages (``careless``),
    which parity/r/install-suggests.R fills. Only this report loads it: metacheck
    runs as users install it, while the parity cases switch ``careless`` on and
    off themselves."""
    lib = subprocess.run(
        [rscript, "--vanilla", "-e", 'cat(file.path(R.home(), "suggests"))'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not (Path(lib) / "careless" / "DESCRIPTION").exists():
        raise SystemExit(
            f"the R package careless is not installed in {lib}: run "
            "`Rscript parity/r/install-suggests.R` (see parity/r/setup-reference.sh)"
        )
    return lib


def _r_env(library: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.lower() != "no_proxy"}
    env.update(LANG="C.UTF-8", LC_ALL="C.UTF-8", TZ="UTC", R_LIBS=library)
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env[name] = _NO_PROXY
    return env


def generate(outputs: list[Output], rscript: str) -> float:
    """Run *outputs* in the reference R and rewrite their modules' goldens; the seconds R took."""
    with tempfile.TemporaryDirectory(prefix="pytacheck-accuracy-") as tmp:
        cases = Path(tmp) / f"{AREA}.yaml"
        spec = {"area": AREA, "cases": [o.spec() for o in outputs]}
        cases.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
        out = Path(tmp) / "out"
        cmd = [rscript, str(ROOT / "parity" / "r" / "run_cases.R"), str(ROOT), str(cases)]
        started = time.perf_counter()
        env = _r_env(suggests_library(rscript))
        status = subprocess.call([*cmd, "--out", str(out)], env=env, cwd=ROOT)
        seconds = time.perf_counter() - started
        if status:
            raise SystemExit(f"the R run failed (exit status {status})")
        for module, outs in by_module(outputs).items():
            data: dict[str, Any] = {"module": module, "outputs": {}}
            for o in outs:
                res = orjson.loads((out / AREA / f"{o.id}.json").read_bytes())
                data["metacheck_version"] = res.get("metacheck_version")
                data["r_version"] = res.get("r_version")
                data["outputs"][o.input] = {
                    "ok": bool(res["ok"]),
                    "error": res.get("error"),
                    "value": res.get("value"),
                }
            write_golden(module, data)
    return seconds


# -- Python's outputs -------------------------------------------------------------------


@contextlib.contextmanager
def _isolated() -> Iterator[None]:
    """A fresh cache directory, pytacheck's R parser, no output, no warnings."""
    saved = {k: os.environ.get(k) for k in ("PYTACHECK_CACHE_DIR", "PYTACHECK_R_PARSER")}
    base = saved["PYTACHECK_CACHE_DIR"]
    with (
        tempfile.TemporaryDirectory(prefix="accuracy-", dir=base or None) as cache,
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        os.environ.update(PYTACHECK_CACHE_DIR=cache, PYTACHECK_R_PARSER="python")
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def run_python(outputs: list[Output]) -> dict[Output, dict[str, Any]]:
    """Each output as ``{ok, value, error}`` (``problem``: it used the network or R).

    Papers are read once and each module gets its own copy. R's parser is
    pytacheck's port (``PYTACHECK_R_PARSER=python``), so the results do not
    depend on whether an R is installed.
    """
    import pytacheck as pc
    from parity.canonical import canonical
    from pytacheck.module import module_run
    from tests.httpmock import no_network

    papers: dict[str, Any] = {}

    def paper_for(o: Output) -> Any:
        key = o.input if o.kind == "paper" else "demo"
        if key not in papers:
            try:
                papers[key] = pc.demopaper() if key == "demo" else pc.read(ROOT / key)
            except Exception as exc:  # every module of this input fails, as in R
                papers[key] = exc
        paper = papers[key]
        if isinstance(paper, Exception):
            raise paper
        return copy.deepcopy(paper)

    results: dict[Output, dict[str, Any]] = {}
    with utc(), metacheck_defaults():
        for o in outputs:
            attempts: list[str] = []
            r_attempts: list[str] = []
            res: dict[str, Any]
            try:
                with (
                    _isolated(),
                    deterministic_ids(),
                    _watch_for_r(r_attempts),
                    no_network(attempts),
                ):
                    paper = paper_for(o)
                    args: dict[str, Any] = {}
                    if o.kind == "repository":
                        args = {"local_path": str(ROOT / o.input), "local_only": True}
                    res = {"ok": True, "value": canonical(module_run(paper, o.module, **args))}
            except RWithoutReference:
                res = {"ok": False, "error": "started R"}
            except Exception as exc:
                res = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if attempts:
                res["problem"] = f"used the network ({attempts[0]})"
            elif r_attempts:
                res["problem"] = f"started R ({r_attempts[0]})"
            results[o] = res
    return results


# -- scoring ----------------------------------------------------------------------------

_WS = re.compile(r"\s+")
# a number, with its minus sign (not a hyphen after a word or number: 'COVID-19', '1-2')
_NUM = re.compile(r"(?:(?<![\w)])[-−])?\d+(?:\.\d+)?")


def squash(s: str) -> str:
    """*s* with runs of whitespace as one space, trimmed (the text margin)."""
    return _WS.sub(" ", s).strip()


def numbers(s: str) -> Counter[str]:
    """The multiset of numbers written in *s* (a sign flip changes it)."""
    return Counter(n.replace("−", "-") for n in _NUM.findall(s))


def text_level(r: str, p: str) -> str | None:
    """How R's text *r* and Python's *p* differ: ``None`` (within the margin),
    ``whitespace``, ``wording`` (same numbers) or ``values``."""
    if squash(r) == squash(p):
        return None
    if _WS.sub("", r) == _WS.sub("", p):
        return "whitespace"
    return "wording" if numbers(r) == numbers(p) else "values"


def _max_level(levels: Iterable[str | None]) -> str | None:
    found = [lv for lv in levels if lv]
    return max(found, key=LEVELS.index) if found else None


def _elements(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("names"):
        return dict(zip(value["names"], value.get("v") or [], strict=False))
    return {}


def plain(x: Any) -> Any:
    """A canonical value as plain Python: vectors of length one as scalars."""
    if not isinstance(x, dict) or "t" not in x:
        return x
    t = x["t"]
    if t == "null":
        return None
    if t in ("chr", "dbl", "int", "lgl", "raw", "cplx"):
        v = x.get("v") or []
        return v[0] if len(v) == 1 else (None if not v else list(v))
    if t == "df":
        return {n: plain(c) for n, c in zip(x.get("names") or [], x.get("v") or [], strict=False)}
    if t in ("list", "module_output", "paper", "paperlist"):
        vs = [plain(e) for e in x.get("v") or []]
        names = x.get("names")
        return dict(zip(names, vs, strict=False)) if names else (vs or None)
    if t == "matrix":
        return plain(x.get("v"))
    return x


def as_text(x: Any) -> str:
    """A canonical value as the text users read (a character vector's strings)."""
    if not isinstance(x, dict) or x.get("t") == "null":
        return ""
    if x.get("t") == "chr":
        return " ".join(s for s in x.get("v") or [] if s is not None)
    return orjson.dumps(plain(x), option=orjson.OPT_SORT_KEYS).decode()


@dataclass
class Frame:
    names: list[str]
    nrow: int
    #: the cells of each column, plain Python
    cols: list[list[Any]]


def as_frame(x: Any) -> Frame | None:
    """A canonical data frame (``None`` for anything else; nothing is an empty frame)."""
    if not isinstance(x, dict) or x.get("t") == "null":
        return Frame([], 0, [])
    if x.get("t") != "df":
        return None
    nrow = int(x.get("nrow") or 0)
    cols = []
    for col in x.get("v") or []:
        if col.get("t") == "list":
            cells = [plain(e) for e in col.get("v") or []]
        else:
            cells = list(col.get("v") or [])
        cols.append((cells + [None] * nrow)[:nrow])
    return Frame(list(x.get("names") or []), nrow, cols)


def _is_number(x: Any) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def _norm(x: Any) -> Any:
    """A cell as a hashable key: text whitespace-normalised, numbers to 10 digits."""
    if isinstance(x, str):
        return squash(x)
    if _is_number(x):
        return float(f"{x:.10g}")
    if isinstance(x, list):
        return tuple(_norm(e) for e in x)
    if isinstance(x, dict):
        return tuple((k, _norm(v)) for k, v in x.items())
    return x


def _cell_text(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    if _is_number(x):
        return f"{x:.10g}"
    return orjson.dumps(x, option=orjson.OPT_SORT_KEYS, default=str).decode()


def cell_level(r: Any, p: Any) -> str | None:
    """How two summary-table cells differ (numbers to a relative 1e-9)."""
    if r == p:
        return None
    if _is_number(r) and _is_number(p):
        close = math.isclose(float(r), float(p), rel_tol=TOL, abs_tol=0.0)
        return None if close else "values"
    if r is None or p is None or r in ("NaN",) or p in ("NaN",):
        return "values"
    return text_level(_cell_text(r), _cell_text(p))


def _column_level(r: list[Any], p: list[Any]) -> str | None:
    """How two table columns differ as multisets of cells."""
    if Counter(map(_norm, r)) == Counter(map(_norm, p)):
        return None
    rt, pt = [_cell_text(c) for c in r], [_cell_text(c) for c in p]
    if Counter(_WS.sub("", c) for c in rt) == Counter(_WS.sub("", c) for c in pt):
        return "whitespace"
    rn = sum((numbers(c) for c in rt), Counter())
    pn = sum((numbers(c) for c in pt), Counter())
    return "wording" if rn == pn else "values"


def row_f1(r: Frame, p: Frame) -> float:
    """F1 of the two frames' rows as multisets (on their common columns)."""
    common = [n for n in r.names if n in p.names]
    ri = [r.names.index(n) for n in common]
    pi = [p.names.index(n) for n in common]
    a = Counter(tuple(_norm(r.cols[j][i]) for j in ri) for i in range(r.nrow))
    b = Counter(tuple(_norm(p.cols[j][i]) for j in pi) for i in range(p.nrow))
    total = sum(a.values()) + sum(b.values())
    return 1.0 if not total else 2 * sum((a & b).values()) / total


@dataclass
class Difference:
    module: str
    input: str
    field: str
    level: str
    detail: str
    #: the index of the expected.yaml entry that explains it
    explained_by: int | None = None


def _snippet(r: str, p: str, width: int = 70) -> str:
    """Where two texts start to differ."""
    r, p = squash(r), squash(p)
    i = next((k for k, (a, b) in enumerate(zip(r, p, strict=False)) if a != b), min(len(r), len(p)))
    lo = max(0, i - 20)
    return f"R={r[lo : lo + width]!r} py={p[lo : lo + width]!r}"


@dataclass
class Scores:
    """Agreement counts of one module: ``agree[name]`` of ``total[name]``."""

    agree: Counter[str] = field(default_factory=Counter)
    total: Counter[str] = field(default_factory=Counter)
    f1: list[float] = field(default_factory=list)

    def add(self, name: str, agree: int | bool, total: int = 1) -> None:
        self.total[name] += total
        self.agree[name] += int(agree)

    def rate(self, name: str) -> float | None:
        return self.agree[name] / self.total[name] if self.total[name] else None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {k: [self.agree[k], self.total[k]] for k in sorted(self.total)}
        out["table_row_f1"] = sum(self.f1) / len(self.f1) if self.f1 else None
        return out


def score_output(
    o: Output, r: Mapping[str, Any], p: Mapping[str, Any], scores: Scores
) -> list[Difference]:
    """Score one output (R's ``{ok, value, error}`` against Python's); its differences."""
    diffs: list[Difference] = []

    def diff(fld: str, level: str | None, detail: str) -> None:
        if level:
            diffs.append(Difference(o.module, o.input, fld, level, detail))

    both = bool(r["ok"]) and bool(p["ok"])
    scores.add("run", bool(r["ok"]) == bool(p["ok"]))
    if bool(r["ok"]) != bool(p["ok"]):
        failed = "R" if not r["ok"] else "Python"
        error = (r if not r["ok"] else p).get("error") or ""
        diff("run", "values", f"only {failed} fails: {str(error).strip()[:200]}")
    if not both:
        return diffs
    rv, pv = _elements(r["value"]), _elements(p["value"])

    tl_r, tl_p = as_text(rv.get("traffic_light")), as_text(pv.get("traffic_light"))
    scores.add("traffic_light", tl_r == tl_p)
    if tl_r != tl_p:
        diff("traffic_light", "values", f"R={tl_r!r} py={tl_p!r}")

    for name in ("summary_text", "report"):
        if name == "report":
            ta = " ".join(Comparator._prose(rv["report"])) if "report" in rv else ""
            tb = " ".join(Comparator._prose(pv["report"])) if "report" in pv else ""
        else:
            ta, tb = as_text(rv.get(name)), as_text(pv.get(name))
        level = text_level(ta, tb)
        scores.add(name, level is None)
        scores.add(f"{name}_numbers", numbers(ta) == numbers(tb))
        diff(name, level, _snippet(ta, tb))

    diffs += _score_summary_table(o, rv.get("summary_table"), pv.get("summary_table"), scores)
    diffs += _score_table(o, rv.get("table"), pv.get("table"), scores)
    return diffs


def _structure(o: Output, name: str, rf: Frame, pf: Frame) -> list[Difference]:
    out = []
    if rf.names != pf.names:
        only_r = [n for n in rf.names if n not in pf.names]
        only_p = [n for n in pf.names if n not in rf.names]
        what = (
            f"columns only in R {only_r}, only in Python {only_p}"
            if only_r or only_p
            else f"column order R={rf.names} py={pf.names}"
        )
        out.append(Difference(o.module, o.input, name, "values", what))
    if rf.nrow != pf.nrow:
        out.append(Difference(o.module, o.input, name, "values", f"rows R={rf.nrow} py={pf.nrow}"))
    return out


def _structural(o: Output, name: str, r: Any, p: Any) -> list[Difference]:
    """Compare what is not a data frame structurally (text whitespace-normalised)."""
    problems = compare(r or {"t": "null"}, p or {"t": "null"}, Options(ws=True))
    return [Difference(o.module, o.input, name, "values", problems[0])] if problems else []


def _score_summary_table(o: Output, r: Any, p: Any, scores: Scores) -> list[Difference]:
    rf, pf = as_frame(r), as_frame(p)
    if rf is None or pf is None:
        found = _structural(o, "summary_table", r, p)
        scores.add("summary_table_cells", not found)
        return found
    diffs = _structure(o, "summary_table", rf, pf)
    names = list(dict.fromkeys(rf.names + pf.names))
    nrow = max(rf.nrow, pf.nrow)
    agree = 0
    for name in names:
        if name not in rf.names or name not in pf.names:
            continue
        a, b = rf.cols[rf.names.index(name)], pf.cols[pf.names.index(name)]
        levels = [
            cell_level(a[i] if i < rf.nrow else None, b[i] if i < pf.nrow else None)
            for i in range(nrow)
        ]
        agree += sum(lv is None for lv in levels)
        level = _max_level(levels)
        if level and rf.nrow == pf.nrow:
            i = next(k for k, lv in enumerate(levels) if lv)
            detail = f"row {i + 1}: R={a[i]!r} py={b[i]!r}"
            diffs.append(Difference(o.module, o.input, f"summary_table.{name}", level, detail))
    scores.add("summary_table_cells", agree, len(names) * nrow)
    return diffs


def _score_table(o: Output, r: Any, p: Any, scores: Scores) -> list[Difference]:
    rf, pf = as_frame(r), as_frame(p)
    if rf is None or pf is None:
        found = _structural(o, "table", r, p)
        scores.add("table_nrow", not found)
        scores.f1.append(0.0 if found else 1.0)
        return found
    diffs = _structure(o, "table", rf, pf)
    scores.add("table_nrow", rf.nrow == pf.nrow)
    scores.f1.append(row_f1(rf, pf))
    if rf.nrow != pf.nrow:  # the columns cannot be lined up: the row count says it
        return diffs
    for name in rf.names:
        if name in pf.names:
            a, b = rf.cols[rf.names.index(name)], pf.cols[pf.names.index(name)]
            level = _column_level(a, b)
            if level:
                ra = Counter(map(_norm, a)) - Counter(map(_norm, b))
                pb = Counter(map(_norm, b)) - Counter(map(_norm, a))
                first_r = next(iter(ra), None)
                first_p = next(iter(pb), None)
                detail = f"R has {_short_repr(first_r)}, py {_short_repr(first_p)}"
                diffs.append(Difference(o.module, o.input, f"table.{name}", level, detail))
    return diffs


def _short_repr(x: Any, limit: int = 90) -> str:
    s = repr(x)
    return s if len(s) <= limit else s[: limit - 3] + "..."


# -- expected differences ---------------------------------------------------------------


def _globs(value: Any) -> tuple[str, ...] | None:
    """A glob or a list of globs (``None`` when *value* is neither)."""
    items = value if isinstance(value, list) else [value]
    if not items or not all(isinstance(g, str) and g for g in items):
        return None
    return tuple(items)


def _matches(name: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(name, g) for g in globs)


@dataclass
class Entry:
    """An entry of ``differences`` in parity/accuracy/expected.yaml."""

    index: int
    module: tuple[str, ...]
    input: tuple[str, ...]
    field: tuple[str, ...]
    match: str
    kind: str
    ref: str | None
    reason: str
    used: int = 0

    def explains(self, d: Difference) -> bool:
        return (
            _matches(d.module, self.module)
            and (_matches(d.input, self.input) or _matches(short(d.input), self.input))
            and _matches(d.field, self.field)
            and LEVELS.index(d.level) <= LEVELS.index(self.match)
        )

    def label(self) -> str:
        what = " / ".join(",".join(g) for g in (self.module, self.input, self.field))
        return f"{what} ({self.match}, {self.ref or self.kind})"


@dataclass
class Floor:
    """An entry of ``floors``: a module whose traffic lights agree with metacheck's
    on fewer inputs than ``TL_FLOOR`` on purpose, and the share expected."""

    module: str
    traffic_light: float
    kind: str
    ref: str | None
    reason: str

    def label(self) -> str:
        return f"{self.module} traffic lights >= {self.traffic_light} ({self.ref or self.kind})"


@dataclass
class Expected:
    entries: list[Entry]
    floors: dict[str, Floor]


def _mark_problems(where: str, e: dict[str, Any]) -> list[str]:
    mark = {k: e[k] for k in ("kind", "ref", "reason") if k in e}
    return [f"{where}: {p}" for p in check_mark(mark, tier=1)]


def load_expected(path: Path = EXPECTED_FILE) -> Expected:
    """parity/accuracy/expected.yaml, each entry validated as a parity mark of a tier-1
    case is (a tier-1 kind, a U/D ref that exists, a reason)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    data = data or {}
    if not isinstance(data, dict) or set(data) - {"differences", "floors"}:
        raise ValueError(f"{path.name}: a mapping with `differences` and `floors`")
    entries: list[Entry] = []
    floors: dict[str, Floor] = {}
    problems: list[str] = []
    for i, e in enumerate(data.get("differences") or []):
        where = f"{path.name} differences[{i}]"
        if not isinstance(e, dict):
            problems.append(f"{where}: a mapping")
            continue
        unknown = sorted(set(e) - ENTRY_KEYS)
        if unknown:
            problems.append(f"{where}: unknown keys {unknown} (one of {sorted(ENTRY_KEYS)})")
        globs = {key: _globs(e.get(key)) for key in ("module", "input", "field")}
        problems += [
            f"{where}: {k} is a glob or a list of globs" for k, g in globs.items() if not g
        ]
        if e.get("match") not in LEVELS:
            problems.append(f"{where}: match is one of {', '.join(LEVELS)}")
        problems += _mark_problems(where, e)
        entries.append(
            Entry(
                i,
                globs["module"] or (),
                globs["input"] or (),
                globs["field"] or (),
                str(e.get("match")),
                str(e.get("kind")),
                e.get("ref"),
                str(e.get("reason", "")),
            )
        )
    for i, f in enumerate(data.get("floors") or []):
        where = f"{path.name} floors[{i}]"
        if not isinstance(f, dict) or not isinstance(f.get("module"), str):
            problems.append(f"{where}: a mapping with a module")
            continue
        unknown = sorted(set(f) - FLOOR_KEYS)
        if unknown:
            problems.append(f"{where}: unknown keys {unknown} (one of {sorted(FLOOR_KEYS)})")
        share = f.get("traffic_light")
        if not isinstance(share, int | float) or not 0 <= share < TL_FLOOR:
            problems.append(f"{where}: traffic_light is a share below {TL_FLOOR}")
            share = 0.0
        problems += _mark_problems(where, f)
        floors[f["module"]] = Floor(
            f["module"], float(share), str(f.get("kind")), f.get("ref"), str(f.get("reason", ""))
        )
    if problems:
        raise ValueError("\n".join(problems))
    return Expected(entries, floors)


def explain(diffs: list[Difference], entries: list[Entry]) -> None:
    """Set each difference's ``explained_by`` (the first entry that explains it)."""
    for d in diffs:
        for e in entries:
            if e.explains(d):
                d.explained_by = e.index
                e.used += 1
                break


# -- the report -------------------------------------------------------------------------


@dataclass
class Report:
    outputs: int
    modules: dict[str, Scores]
    differences: list[Difference]
    expected: Expected
    #: goldens that are missing, outputs that used the network or R
    problems: list[str]
    partial: bool
    seconds: dict[str, float]
    metacheck_version: str | None = None

    @property
    def unexplained(self) -> list[Difference]:
        return [d for d in self.differences if d.explained_by is None]

    @property
    def stale(self) -> list[str]:
        """Entries that explain no difference, and floors no module needs (full runs)."""
        if self.partial:
            return []
        out = [f"differences[{e.index}]: {e.label()}" for e in self.expected.entries if not e.used]
        for module, floor in sorted(self.expected.floors.items()):
            rate = self.modules[module].rate("traffic_light") if module in self.modules else None
            if rate is None or rate >= TL_FLOOR:
                out.append(f"floors: {floor.label()}: not needed")
        return out

    @property
    def warnings(self) -> list[str]:
        """Modules whose traffic lights agree less often than their floor."""
        out = []
        for module, s in sorted(self.modules.items()):
            rate = s.rate("traffic_light")
            floor = self.expected.floors.get(module)
            limit = floor.traffic_light if floor else TL_FLOOR
            if rate is not None and rate < limit:
                out.append(
                    f"{module}: traffic lights agree with metacheck's on {s.agree['traffic_light']}"
                    f" of {s.total['traffic_light']} inputs (below {limit:.0%}): needs human review"
                )
        return out

    @property
    def passed(self) -> bool:
        return not (self.unexplained or self.stale or self.problems)

    def as_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "needs_review": bool(self.warnings),
            "metacheck_version": self.metacheck_version,
            "outputs": self.outputs,
            "seconds": self.seconds,
            "modules": {m: s.as_json() for m, s in sorted(self.modules.items())},
            "differences": [asdict(d) for d in self.differences],
            "unexplained": len(self.unexplained),
            "stale": self.stale,
            "problems": self.problems,
            "warnings": self.warnings,
            "entries": [{"entry": e.label(), "explains": e.used} for e in self.expected.entries],
        }


def score(
    outputs: list[Output],
    python: Mapping[Output, Mapping[str, Any]],
    expected: Expected,
    partial: bool,
    seconds: dict[str, float] | None = None,
) -> Report:
    """Score Python's *outputs* against R's goldens and explain the differences."""
    modules: dict[str, Scores] = {}
    diffs: list[Difference] = []
    problems: list[str] = []
    version = None
    for module, outs in by_module(outputs).items():
        golden = read_golden(module)
        scores = modules.setdefault(module, Scores())
        if golden is None:
            problems.append(
                f"{module}: no golden {golden_path(module).relative_to(ROOT)}: "
                "run `python -m parity accuracy --generate`"
            )
            continue
        version = version or golden.get("metacheck_version")
        for o in outs:
            r = golden["outputs"].get(o.input)
            if r is None:
                problems.append(f"{o.id}: not in R's golden: run `--generate -m {module}`")
                continue
            p = python[o]
            if p.get("problem"):
                problems.append(f"{o.id}: {p['problem']}")
            diffs += score_output(o, r, p, scores)
    explain(diffs, expected.entries)
    return Report(len(outputs), modules, diffs, expected, problems, partial, seconds or {}, version)


_COLUMNS = (
    ("run", "run"),
    ("TL", "traffic_light"),
    ("summary", "summary_text"),
    ("cells", "summary_table_cells"),
    ("nrow", "table_nrow"),
    ("row F1", None),
    ("report", "report"),
    ("report #", "report_numbers"),
)


def _rate(s: Scores, name: str | None) -> str:
    if name is None:
        return f"{sum(s.f1) / len(s.f1):.3f}" if s.f1 else "-"
    rate = s.rate(name)
    return "-" if rate is None else f"{rate:.3f}"


def _module_rows(rep: Report) -> list[list[str]]:
    rows = []
    for module, s in sorted(rep.modules.items()):
        diffs = [d for d in rep.differences if d.module == module]
        unexplained = sum(d.explained_by is None for d in diffs)
        rows.append(
            [module]
            + [_rate(s, name) for _, name in _COLUMNS]
            + [str(len(diffs)), str(unexplained)]
        )
    return rows


def text_summary(rep: Report) -> list[str]:
    head = ["module", *(c for c, _ in _COLUMNS), "diffs", "unexpl."]
    rows = _module_rows(rep)
    widths = [max(len(r[i]) for r in [head, *rows]) for i in range(len(head))]
    lines = [
        "  ".join(
            c.rjust(w) if i else c.ljust(w) for i, (c, w) in enumerate(zip(r, widths, strict=True))
        )
        for r in [head, *rows]
    ]
    n = len(rep.differences)
    lines.append(
        f"{rep.outputs} outputs, {n} differences: {n - len(rep.unexplained)} explained by "
        f"{sum(bool(e.used) for e in rep.expected.entries)} entries of expected.yaml, "
        f"{len(rep.unexplained)} unexplained"
    )
    lines += [
        f"  UNEXPLAINED {d.module} / {short(d.input)} / {d.field} ({d.level}): {d.detail}"
        for d in rep.unexplained[:60]
    ]
    if len(rep.unexplained) > 60:
        lines.append(f"  ... and {len(rep.unexplained) - 60} more (see the report)")
    lines += [f"  STALE {e}" for e in rep.stale]
    lines += [f"  PROBLEM {p}" for p in rep.problems]
    lines += [f"  WARNING {w}" for w in rep.warnings]
    return lines


def markdown(rep: Report) -> str:
    verdict = "passes" if rep.passed else "fails"
    out = [
        "# Accuracy on the realistic corpus",
        "",
        f"metacheck {rep.metacheck_version or '?'}; {rep.outputs} outputs; the gate {verdict}"
        f"{' (needs human review)' if rep.warnings else ''}.",
        "",
        "Agreement with metacheck per module (share of outputs; `cells`: summary-table "
        "cells; `row F1`: table rows as multisets; text after whitespace normalisation):",
        "",
        "| module | " + " | ".join(c for c, _ in _COLUMNS) + " | differences | unexplained |",
        "|---|" + "--:|" * (len(_COLUMNS) + 2),
    ]
    out += ["| " + " | ".join(r) + " |" for r in _module_rows(rep)]
    for title, picked in (
        ("Unexplained differences", rep.unexplained),
        ("Explained differences", [d for d in rep.differences if d.explained_by is not None]),
    ):
        if not picked:
            continue
        head = f"## {title} ({len(picked)})"
        if title.startswith("Explained"):  # folded: the list the report checks against
            head = f"<details><summary>{title} ({len(picked)})</summary>"
        out += ["", head, "", "| module | input | field | level | detail | entry |"]
        out.append("|---|---|---|---|---|---|")
        for d in picked[:300]:
            entry = None if d.explained_by is None else rep.expected.entries[d.explained_by]
            ref = "" if not entry else (entry.ref or entry.kind)
            detail = d.detail.replace("|", "\\|").replace("\n", " ")[:160]
            out.append(
                f"| {d.module} | {short(d.input)} | {d.field} | {d.level} | {detail} | {ref} |"
            )
        if len(picked) > 300:
            out.append(f"| ... and {len(picked) - 300} more | | | | | |")
        if title.startswith("Explained"):
            out += ["", "</details>"]
    if rep.stale:
        out += ["", "## Stale entries of expected.yaml", ""]
        out += [f"- {e}" for e in rep.stale]
    if rep.problems or rep.warnings:
        out += ["", "## Problems and warnings", ""]
        out += [f"- {p}" for p in rep.problems] + [f"- warning: {w}" for w in rep.warnings]
    return "\n".join(out) + "\n"


# -- command line -----------------------------------------------------------------------


def add_parser(sub: Any) -> None:
    a = sub.add_parser(
        "accuracy",
        help="score pytacheck against metacheck on the realistic corpus",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    a.add_argument(
        "--generate", action="store_true", help="first rewrite R's outputs (needs the reference R)"
    )
    a.add_argument(
        "--gate",
        action="store_true",
        help="fail on unexplained differences, stale entries, missing goldens",
    )
    a.add_argument(
        "-m", "--module", action="append", metavar="MODULE", help="only this module (repeatable)"
    )
    a.add_argument("--md", metavar="OUT", help="also write a Markdown report ('-': stdout)")
    a.add_argument(
        "--report",
        metavar="PATH",
        help="the JSON report (default: a new parity/_out/accuracy-<time>-<pid>.json)",
    )
    a.add_argument("--rscript", help="the reference Rscript for --generate")
    a.set_defaults(func=cmd_accuracy)


def cmd_accuracy(ns: argparse.Namespace) -> int:
    outputs = select(load_matrix(), ns.module)
    expected = load_expected()
    seconds: dict[str, float] = {}
    if ns.generate:
        from parity.__main__ import _rscript

        seconds["r"] = generate(outputs, _rscript(ns.rscript))
    started = time.perf_counter()
    python = run_python(outputs)
    seconds["python"] = time.perf_counter() - started
    rep = score(outputs, python, expected, partial=bool(ns.module), seconds=seconds)
    for line in text_summary(rep):
        print(line)
    report = (
        Path(ns.report)
        if ns.report
        else OUT_DIR / f"accuracy-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}.json"
    )
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(orjson.dumps(rep.as_json(), option=orjson.OPT_INDENT_2))
    print(f"report: {report}")
    if ns.md:
        text = markdown(rep)
        if ns.md == "-":
            print(text)
        else:
            Path(ns.md).write_text(text, encoding="utf-8")
            print(f"summary: {ns.md}")
    timing = ", ".join(f"{k} {v:.0f} s" for k, v in seconds.items())
    print(f"accuracy {'passes' if rep.passed else 'fails'} ({timing})")
    if rep.warnings and os.environ.get("GITHUB_ACTIONS"):
        for w in rep.warnings:
            print(f"::warning::{w}")
    return 1 if ns.gate and not rep.passed else 0
