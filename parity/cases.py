"""Loading parity cases and running their Python side.

A case file ``parity/cases/<area>.yaml`` looks like::

    area: text
    cases:
      - id: text_search.demo.significant   # golden: parity/golden/text/<id>.json
        r: text_search                     # function in the metacheck namespace
        py: pytacheck.text.text_search     # dotted path of the Python port
        args:                              # R argument names; see below
          paper: {$paper: demo}
          pattern: significant
          ignore.case: false
        compare: {unordered: true}         # see parity.compare
      - id: marginal.demo
        module: marginal                   # module_run(paper, "marginal", ...)
        args: {paper: {$paper: demo}}

Argument names are translated for Python by replacing ``.`` with ``_`` and
appending ``_`` to Python keywords (``return`` -> ``return_``). ``py_args``
adds or overrides Python arguments and ``py_drop`` removes some.

Argument constructors (a one-key mapping whose key starts with ``$``):

``$paper: demo | <path>``      demopaper() / read(path)   (paths relative to repo root)
``$read: [<path>, ...]``        read() of several files -> paper list
``$test_paper: {text, url}``    test_paper(text, url)
``$df: {col: [...], ...}``      a data frame (``null`` -> NA)
``$chr|$int|$dbl|$lgl: [...]``  a typed vector (use for empty or length-1 vectors)
``$list: [...]``                an unnamed list
``$null: true`` / ``$NA: true`` NULL / NA
``$file: <path>``               an absolute path string
``$expr: {r: <code>, py: <code>}``  escape hatch; py code sees ``pc`` (pytacheck), ``pd``, ``np``
``$call: {r: fn, py: dotted, args: {...}}``  the result of another call
``$catch: <spec>``              the value of *spec*, or ``{error: true}`` when it fails

When R raises an error, Python must raise too, and that is all: error (and
warning) texts are never compared, pytacheck writes its own. A case whose call
fails needs nothing special. ``$catch`` is for an error that is one part of a
larger result (one call of several in a ``$list``): it yields the value, or
``{error: true}`` without the message, on both sides (``pc_catch()`` in
parity/r/helpers.R and ``parity.pyhelpers.catch()`` do the same inside helper
code). Warnings are not captured. ``compare: {presence: [...]}`` compares
fields of free error text (a ``repo_error`` column) for presence only.

A case that differs from R on purpose sets ``known_divergence`` (an expected
failure, ``xfail``), a mapping ``{kind, ref, reason}``: ``kind`` is one of
``DIVERGENCE_KINDS`` and ``ref`` names the docs/UPSTREAM_ISSUES.md entry
(``U13``, ``D6``). Cases in generated case files are marked from
``parity/divergences/*.yaml`` (``{"<area>/<id>": {kind, ref, reason}}``, one
file per lane), which ``load_cases`` applies. ``load_cases`` validates every
mark (``check_mark``): ``r_bug_fixed`` needs a U-entry, ``better_logic`` and
``deliberate`` a D-entry, every ``ref`` must exist in docs/UPSTREAM_ISSUES.md,
every mark needs a reason, a tier-1 case may only carry the kinds in
``TIER1_KINDS``, and a case is marked in one place only. A key with ``*`` (which
matches any run of characters, e.g. ``"rcompat_regex/pcre.mid_char.*"``) marks
every case it matches, all of which must be tier 2.

Every case has a ``tier`` (``classify_tier``): 1 for a case on the realistic
corpus of ``parity/corpus.toml`` (real papers, repositories, data and code
files, recorded API responses), 2 for synthetic edge cases. ``tier: {level: 1
| 2, reason: ...}`` on a case or at the top of a hand-written case file, or an
``[[override]]`` in parity/corpus.toml, sets it explicitly.

A mark whose difference is text that pytacheck corrects (a typo, a plural, a
full stop in report prose) says how with ``r_text``, a list of substitutions
applied in order to every string value of R's golden before the comparison (R's
error text is never compared, so an ``r_text`` mark on a case where R fails is
stale)::

    known_divergence:
      kind: r_bug_fixed
      ref: U83
      reason: "the caveat says 'likely' (metacheck: 'likley')"
      r_text: [["likley", "likely"], ["sizes$", "sizes.", regex]]

``[old, new]`` replaces literal text, ``[old, new, regex]`` is Python's
``re.sub(old, new, s)``. Such a case is no expected failure: it passes when R's
rewritten result equals Python's and fails on any other difference, and a
substitution that changes nothing in R's golden fails it (a stale mark), as
does a match with R's golden as it is (the substitutions changed only text the
comparison skips). A case that also differs for other reasons adds ``xfail:
true``; it stays an expected failure, locked against R's rewritten golden, and
``r_text`` only removes the text corrections from its reported differences.

A case where R fails and Python returns a value can only be marked
``r_bug_fixed``, ``better_logic`` or ``deliberate`` (``VALUE_WHERE_R_FAILS_KINDS``).

``needs_r: true`` marks a case whose Python side runs R. Such cases, and any
case whose Python side starts ``Rscript``/``R`` (the harness watches for it),
run only with the reference R (``PYTACHECK_RSCRIPT`` naming an R >= 4.5) and
are reported as ``skip`` otherwise, never as pass or fail: another R gives
other results.

Both sides number papers created without an id (``test_paper()``) the same
way within a case (``deterministic_ids``), run in UTC, and see the checkout
directory as ``<repo>``, so goldens do not change from run to run or machine
to machine.

``mock_dir: apis`` runs the case against recorded HTTP responses on both
sides: R inside ``httptest2::with_mock_dir()``, Python inside
``tests.httpmock.replay()``. Relative names are metacheck test mock
directories (``apis``, ``apis_papers_retag``, ...); paths starting with
``tests/`` or ``parity/`` are relative to this repository.

The Python side runs with metacheck's defaults where pytacheck deliberately
changed one (``METACHECK_DEFAULTS``): ``read()`` and ``grobid_to_bibr()``
convert Grobid TEI with ``schema_version=None``, metacheck's older
conversion, so ``$paper``/``$read`` of an XML file, ``$expr`` code and every
function that reads TEI (``convert()``, ``convert_grobid()``, ...) match R's
``read()``, and ``paper_write()`` saves the paper object. A case compares the
bibr 12.0 conversion by passing ``schema_version = "12.0"`` on both sides.
"""

from __future__ import annotations

import contextlib
import functools
import gc
import hashlib
import importlib
import inspect
import keyword
import os
import re
import shlex
import shutil
import subprocess
import time
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from parity.compare import TextSub, option_problems, parse_r_text

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "parity" / "cases"
GOLDEN_DIR = ROOT / "parity" / "golden"
CORPUS_FILE = ROOT / "parity" / "corpus.toml"
UPSTREAM_ISSUES = ROOT / "docs" / "UPSTREAM_ISSUES.md"

#: libyaml's parser: the same values as yaml.SafeLoader, about 8x faster
YAML_LOADER: Any = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def load_yaml(path: Path) -> Any:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=YAML_LOADER)


@dataclass
class Case:
    area: str
    id: str
    spec: dict[str, Any]
    file: Path
    #: 1 = realistic corpus input, 2 = synthetic edge case (see ``classify_tier``)
    tier: int = 2

    @property
    def golden_path(self) -> Path:
        return GOLDEN_DIR / self.area / f"{self.id}.json"

    @property
    def key(self) -> str:
        return f"{self.area}/{self.id}"


def iter_case_files(area: str | None = None) -> Iterator[Path]:
    for f in sorted(CASES_DIR.glob("*.yaml")):
        if area is None or f.stem == area:
            yield f


#: why a case may differ from R (docs/PORTING.md, section 1)
DIVERGENCE_KINDS = {
    "r_bug_fixed": "metacheck (or R) gets it wrong; pytacheck fixes it",
    "better_logic": "pytacheck does it differently on purpose, and better",
    "c_quirk": "a quirk of one of R's C libraries on malformed or synthetic input",
    "type_detail": "an R type or attribute detail that does not reach users",
    "deliberate": "a documented deliberate difference (a D-entry)",
    "r_nondeterministic": "R's own result is undefined or changes from run to run",
}
#: the kinds a tier-1 (realistic) case may carry: a difference that reaches
#: users on real inputs is never a quirk or a type detail
TIER1_KINDS = frozenset({"r_bug_fixed", "better_logic", "deliberate", "r_nondeterministic"})
#: kinds that need a docs/UPSTREAM_ISSUES.md entry of this series
REF_SERIES = {"r_bug_fixed": "U", "better_logic": "D", "deliberate": "D"}
#: the kinds that may mark a case where R fails and Python returns a value: a value
#: where metacheck gives none is a documented fix or decision, never a quirk
VALUE_WHERE_R_FAILS_KINDS = frozenset(REF_SERIES)
#: U-entry statuses an ``r_bug_fixed`` mark may cite (when the table has a status column)
FIXED_STATUSES = ("fixed", "partly fixed")
MARK_KEYS = frozenset({"kind", "ref", "reason", "r_text", "xfail"})

DIVERGENCES_DIR = ROOT / "parity" / "divergences"


def divergence_kind(spec: dict[str, Any]) -> str | None:
    """The ``kind`` of a case's ``known_divergence``, or ``None``."""
    div = spec.get("known_divergence")
    return str(div.get("kind")) if isinstance(div, dict) else None


def r_text(spec: dict[str, Any]) -> list[TextSub]:
    """The ``r_text`` substitutions of a case's ``known_divergence`` (see above)."""
    return parse_r_text(spec.get("known_divergence"))


def expected_to_fail(spec: dict[str, Any]) -> bool:
    """Whether a difference from R is an expected failure (``xfail``) for this case.

    Every ``known_divergence`` is, except one that ``r_text`` describes
    completely (no ``xfail: true``): its case must match R's rewritten golden.
    """
    div = spec.get("known_divergence")
    if not div:
        return False
    return not r_text(spec) or div.get("xfail") is True


# -- docs/UPSTREAM_ISSUES.md ----------------------------------------------------------

_REF = re.compile(r"[UD][1-9]\d*")


@functools.cache
def upstream_refs(path: Path = UPSTREAM_ISSUES) -> dict[str, str | None]:
    """The entries of docs/UPSTREAM_ISSUES.md: ``U<n>``/``D<n>`` -> status.

    The status is the lower-cased cell of a ``status`` column, or ``None`` for
    a table without one.
    """
    refs: dict[str, str | None] = {}
    status_col: int | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if not cells:
            continue
        if cells[0] == "#":  # a table header
            lower = [c.lower() for c in cells]
            status_col = lower.index("status") if "status" in lower else None
        elif _REF.fullmatch(cells[0]):
            status = None
            if status_col is not None and status_col < len(cells):
                status = cells[status_col].strip("* ").lower()
            refs[cells[0]] = status
    return refs


# -- marks ------------------------------------------------------------------------------


def check_mark(
    div: Any, tier: int | None = None, refs: dict[str, str | None] | None = None
) -> list[str]:
    """What is wrong with a ``known_divergence`` (empty when it is valid).

    *tier* is the case's tier (``None`` skips the tier rule, ``tier_problem``),
    *refs* ``upstream_refs()``.
    """
    if not isinstance(div, dict):
        return [
            f"a mark is a mapping {{kind, ref, reason}}, not {type(div).__name__} {str(div)[:60]!r}"
        ]
    problems = []
    unknown = sorted(set(div) - MARK_KEYS)
    if unknown:
        problems.append(f"unknown keys {unknown} (a mark has {sorted(MARK_KEYS)})")
    kind = div.get("kind")
    if kind not in DIVERGENCE_KINDS:
        problems.append(f"kind {kind!r} is not one of {sorted(DIVERGENCE_KINDS)}")
    if not isinstance(div.get("reason"), str) or not div["reason"].strip():
        problems.append("a mark needs a reason")
    ref = div.get("ref")
    series = REF_SERIES.get(kind) if isinstance(kind, str) else None
    if ref is None:
        if series:
            problems.append(f"{kind} needs a ref to a {series}-entry of docs/UPSTREAM_ISSUES.md")
    elif not isinstance(ref, str) or not _REF.fullmatch(ref):
        problems.append(f"ref {ref!r} is not U<n> or D<n>")
    else:
        if series and not ref.startswith(series):
            problems.append(f"{kind} needs a {series}-entry, not {ref}")
        refs = upstream_refs() if refs is None else refs
        if ref not in refs:
            problems.append(f"{ref} is not an entry of docs/UPSTREAM_ISSUES.md")
        elif kind == "r_bug_fixed" and not (refs[ref] or "fixed").startswith(FIXED_STATUSES):
            problems.append(f"r_bug_fixed cites {ref}, whose status is {refs[ref]!r}")
    if tier is not None:
        problems += tier_problem(div, tier)
    try:
        subs = parse_r_text(div)
    except ValueError as exc:
        problems.append(str(exc))
        subs = []
    if "xfail" in div:
        if not subs:
            problems.append("xfail belongs to a mark with r_text")
        elif not isinstance(div["xfail"], bool):
            problems.append("xfail must be true or false")
    return problems


def tier_problem(div: Any, tier: int) -> list[str]:
    """Why a case of *tier* cannot carry the mark *div* (empty when it can)."""
    kind = div.get("kind") if isinstance(div, dict) else None
    if tier == 1 and kind in DIVERGENCE_KINDS and kind not in TIER1_KINDS:
        return [
            f"a tier-1 (realistic) case cannot be marked {kind}: a difference that reaches "
            f"users on real inputs is one of {sorted(TIER1_KINDS)}"
        ]
    return []


def is_glob_key(key: str) -> bool:
    """A divergences key with ``*`` marks every case it matches (tier-2 cases only)."""
    return "*" in key


def _key_glob(key: str) -> re.Pattern[str]:
    return re.compile(".*".join(map(re.escape, key.split("*"))) + r"\Z", re.DOTALL)


@dataclass(frozen=True)
class Mark:
    """A ``parity/divergences`` entry: where it is, its key and its mark."""

    file: str
    key: str
    div: dict[str, Any]


@dataclass
class Divergences:
    """``parity/divergences/*.yaml``: exact case keys and glob keys."""

    exact: dict[str, Mark] = field(default_factory=dict)
    #: area (or ``None`` for a glob whose area has ``*``) -> [(pattern, mark)]
    globs: dict[str | None, list[tuple[re.Pattern[str], Mark]]] = field(default_factory=dict)

    def marks(self) -> Iterator[Mark]:
        yield from self.exact.values()
        for entries in self.globs.values():
            for _, mark in entries:
                yield mark

    def matching(self, area: str, key: str) -> list[Mark]:
        found = [self.exact[key]] if key in self.exact else []
        for group in (area, None):
            found += [mark for rx, mark in self.globs.get(group, ()) if rx.match(key)]
        return found


def read_divergences(directory: Path | None = None, errors: list[str] | None = None) -> Divergences:
    """Load ``parity/divergences/*.yaml``.

    A malformed mark raises ``ValueError``, or is added to *errors* when given.
    """
    out = Divergences()
    seen: dict[str, str] = {}
    raise_now = errors is None
    errors = [] if errors is None else errors
    refs = upstream_refs()
    for f in sorted((directory or DIVERGENCES_DIR).glob("*.yaml")):
        data = load_yaml(f) or {}
        if not isinstance(data, dict):
            errors.append(f"{f.name}: a divergences file maps case keys to marks")
            continue
        for key, div in data.items():
            key = str(key)
            where = f"{f.name}: {key}"
            if key in seen:
                errors.append(f"{where} is already marked in {seen[key]}")
                continue
            seen[key] = f.name
            errors += [f"{where}: {p}" for p in check_mark(div, refs=refs)]
            mark = Mark(f.name, key, div)
            if is_glob_key(key):
                area = key.split("/", 1)[0]
                group = None if "*" in area or "/" not in key else area
                out.globs.setdefault(group, []).append((_key_glob(key), mark))
            else:
                out.exact[key] = mark
    if raise_now:
        _raise(errors)
    return out


def load_divergences() -> dict[str, dict[str, Any]]:
    """``parity/divergences/*.yaml`` merged: key (a case key, or a glob) -> mark."""
    return {mark.key: mark.div for mark in read_divergences().marks()}


def _raise(errors: list[str], limit: int = 40) -> None:
    if errors:
        more = f"\n... and {len(errors) - limit} more" if len(errors) > limit else ""
        raise ValueError("invalid parity marks:\n" + "\n".join(errors[:limit]) + more)


# -- tiers --------------------------------------------------------------------------------


def _path_glob(pattern: str) -> str:
    """A regular expression for a repository-relative glob (see parity/corpus.toml)."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern.startswith("**", i):
            if not ((i == 0 or pattern[i - 1] == "/") and (i + 2 == n or pattern[i + 2] == "/")):
                raise ValueError(f"{pattern}: ** must be a whole path segment")
            if i + 2 == n:  # a trailing /**: the directory itself or anything below it
                if out and out[-1] == "/":
                    out[-1] = "(?:/.*)?"
                else:
                    out.append(".*")
                i += 2
            else:  # **/: any number of leading directories
                out.append("(?:.*/)?")
                i += 3
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return "".join(out)


def _any_glob(patterns: Iterable[str]) -> re.Pattern[str]:
    alternatives = [f"(?:{_path_glob(p)})" for p in patterns]
    return re.compile("|".join(alternatives) + r"\Z" if alternatives else r"(?!)", re.DOTALL)


@dataclass(frozen=True)
class TierOverride:
    """An ``[[override]]`` of parity/corpus.toml: the cases it sets to *tier*."""

    cases: tuple[str, ...]
    tier: int
    reason: str
    pattern: re.Pattern[str]


@dataclass
class Corpus:
    """parity/corpus.toml: the realistic inputs, and explicit tiers.

    With a *root*, a path must also exist under it: a file a case names only to
    see what happens when it is missing is not a realistic input.
    """

    include: re.Pattern[str]
    exclude: re.Pattern[str]
    overrides: tuple[TierOverride, ...] = ()
    root: Path | None = None
    _memo: dict[str, bool] = field(default_factory=dict, repr=False, compare=False)

    def __contains__(self, path: object) -> bool:
        if not isinstance(path, str):
            return False
        known = self._memo.get(path)
        if known is None:
            p = _normpath(path)
            known = bool(self.include.match(p)) and not self.exclude.match(p)
            if known and self.root is not None:
                known = (self.root / p).exists()
            self._memo[path] = known
        return known

    def override(self, key: str) -> TierOverride | None:
        found = [o for o in self.overrides if o.pattern.match(key)]
        if len(found) > 1:
            raise ValueError(f"parity/corpus.toml: {key} matches several [[override]] entries")
        return found[0] if found else None


@functools.cache
def load_corpus(path: Path = CORPUS_FILE, root: Path | None = ROOT) -> Corpus:
    """parity/corpus.toml (*path*); its inputs must exist under *root* (``None``:
    globs only)."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    corpus = data.get("corpus", {})
    include = [g for part in ("papers", "files", "mocks") for g in corpus.get(part, [])]
    overrides = []
    for i, o in enumerate(data.get("override", []), 1):
        where = f"{path.name}: override {i}"
        cases = o.get("cases")
        if not isinstance(cases, list) or not cases or not all(isinstance(c, str) for c in cases):
            raise ValueError(f"{where}: `cases` is a list of case-key globs")
        if o.get("tier") not in (1, 2):
            raise ValueError(f"{where}: `tier` is 1 or 2")
        if not isinstance(o.get("reason"), str) or not o["reason"].strip():
            raise ValueError(f"{where}: an explicit tier needs a reason")
        pattern = re.compile("|".join(f"(?:{_key_glob(c).pattern})" for c in cases), re.DOTALL)
        overrides.append(TierOverride(tuple(cases), o["tier"], o["reason"], pattern))
    return Corpus(_any_glob(include), _any_glob(corpus.get("exclude", [])), tuple(overrides), root)


def _normpath(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return re.sub(r"/+", "/", path).rstrip("/")


#: the demo paper (``$paper: demo``, ``demopaper()``)
DEMO_PAPER = "upstream/metacheck/inst/demos/to_err_is_human.xml"
#: metacheck's test directory: relative ``mock_dir`` names live there
_UPSTREAM_TESTS = "upstream/metacheck/tests/testthat/"
_REPO_PATH = re.compile(r"(?:upstream|tests|parity)/")
_CODE_PATH = re.compile(r"""["']((?:upstream|tests|parity)/[^"'\s]*)["']""")
_CODE_DEMO = re.compile(r"\bdemopaper\(")
#: ``$expr`` R code that is only a constant or a temporary path (``n_rows = Inf``,
#: ``save_path = tempfile()``): an argument, neither an input nor a synthetic one
_CODE_CONSTANT = re.compile(
    r"\s*(?:-?Inf|NaN|NA(?:_[a-z]+_)?|TRUE|FALSE|NULL|-?\d+(?:\.\d+)?L?"
    r"|tempfile\([^()]*\)|tempdir\(\))\s*"
)
#: ``$expr`` code that builds a synthetic paper, whatever corpus paths it also names
_CODE_TEST_PAPER = re.compile(r"\btest_paper\(")
#: helper code a case sources or imports (not an input): a script at the top of
#: a tests/ package or of parity/
_HELPER = re.compile(r"(?:tests/[^/]+|parity(?:/r)?)/[^/]+\.(?:R|py)\Z")
#: argument constructors that build a synthetic input
SYNTHETIC_CONSTRUCTORS = frozenset({"$test_paper", "$df", "$chr", "$int", "$dbl", "$lgl", "$list"})


@dataclass
class CaseInputs:
    """What a case reads: repository paths, and the synthetic constructors it uses."""

    paths: list[str] = field(default_factory=list)
    synthetic: list[str] = field(default_factory=list)


def case_inputs(spec: dict[str, Any], corpus: Corpus | None = None) -> CaseInputs:
    """The inputs of a case: the paths its arguments and ``$expr`` code name
    (helper scripts aside) and its synthetic constructors. An ``$expr`` counts as
    synthetic when it names no corpus path or builds a ``test_paper()``; one that
    is only a constant or a temporary path (``Inf``, ``tempfile()``) is no input."""
    corpus = corpus or load_corpus()
    found = CaseInputs()
    _collect(spec.get("args"), found, corpus)
    _collect(spec.get("py_args"), found, corpus)
    mock = spec.get("mock_dir")
    if mock:
        mock = str(mock)
        found.paths.append(mock if _REPO_PATH.match(mock) else _UPSTREAM_TESTS + mock)
    return found


def _collect(x: Any, found: CaseInputs, corpus: Corpus) -> None:
    if isinstance(x, dict):
        if len(x) == 1:
            key, val = next(iter(x.items()))
            if isinstance(key, str) and key.startswith("$"):
                _collect_constructor(key, val, found, corpus)
                return
        for v in x.values():
            _collect(v, found, corpus)
    elif isinstance(x, list):
        for v in x:
            _collect(v, found, corpus)
    elif isinstance(x, str) and _REPO_PATH.match(x):
        found.paths.append(x)


def _collect_constructor(key: str, val: Any, found: CaseInputs, corpus: Corpus) -> None:
    if key == "$paper":
        found.paths.append(DEMO_PAPER if val == "demo" else str(val))
    elif key in ("$read", "$file"):
        found.paths += [str(v) for v in (val if isinstance(val, list) else [val])]
    elif key == "$expr":
        val = val or {}
        if _CODE_CONSTANT.fullmatch(str(val.get("r") or "")):
            return
        code = f"{val.get('r') or ''}\n{val.get('py') or ''}"
        paths = [p for p in _CODE_PATH.findall(code) if not _HELPER.search(p)]
        if _CODE_DEMO.search(code):
            paths.append(DEMO_PAPER)
        if _CODE_TEST_PAPER.search(code) or not any(p in corpus for p in paths):
            found.synthetic.append("$expr")
        found.paths += paths
    elif key == "$call":
        val = val or {}
        _collect(val.get("args"), found, corpus)
        _collect(val.get("py_args"), found, corpus)
    elif key == "$catch":
        _collect(val, found, corpus)
    elif key in SYNTHETIC_CONSTRUCTORS:
        found.synthetic.append(key)


def rule_tier(area: str, spec: dict[str, Any], corpus: Corpus | None = None) -> int:
    """The tier parity/corpus.toml's rule gives a case (explicit tiers aside).

    Tier 1 when the area is not ``*_review`` or ``rcompat*``, the case reads at
    least one corpus input (an existing file of parity/corpus.toml) and nothing
    outside the corpus, and it builds no synthetic input (``case_inputs``); tier 2
    otherwise.
    """
    if area.endswith("_review") or area.startswith("rcompat"):
        return 2
    corpus = corpus or load_corpus()
    inputs = case_inputs(spec, corpus)
    if inputs.synthetic or not inputs.paths:
        return 2
    return 1 if all(p in corpus for p in inputs.paths) else 2


def explicit_tier(where: str, value: Any) -> int:
    """A ``tier: {level, reason}`` of a case file or case."""
    if (
        not isinstance(value, dict)
        or set(value) != {"level", "reason"}
        or value["level"] not in (1, 2)
        or not isinstance(value["reason"], str)
        or not value["reason"].strip()
    ):
        raise ValueError(f"{where}: tier is {{level: 1 | 2, reason: <why>}}, not {value!r}")
    return int(value["level"])


def classify_tier(
    area: str,
    spec: dict[str, Any],
    corpus: Corpus | None = None,
    file_tier: int | None = None,
) -> int:
    """A case's tier: its own ``tier``, else a parity/corpus.toml ``[[override]]``,
    else its file's ``tier``, else ``rule_tier``."""
    if "tier" in spec:
        return explicit_tier(f"{area}/{spec.get('id')}", spec["tier"])
    corpus = corpus or load_corpus()
    override = corpus.override(f"{area}/{spec.get('id')}")
    if override is not None:
        return override.tier
    if file_tier is not None:
        return file_tier
    return rule_tier(area, spec, corpus)


# -- loading ------------------------------------------------------------------------------


def load_cases(area: str | None = None, tier: int | None = None) -> list[Case]:
    """The parity cases (of one *area*, of one *tier*), marks applied and validated."""
    # the case files build some 200,000 objects, none of them garbage: the
    # collector's passes over them would add a fifth to the time
    enabled = gc.isenabled()
    gc.disable()
    try:
        return _load_cases(area, tier)
    finally:
        if enabled:
            gc.enable()


def _load_cases(area: str | None, tier: int | None) -> list[Case]:
    cases: list[Case] = []
    seen: set[str] = set()
    errors: list[str] = []
    divergences = read_divergences(errors=errors)
    corpus = load_corpus()
    refs = upstream_refs()
    for f in iter_case_files(area):
        data = load_yaml(f) or {}
        a = data.get("area", f.stem)
        file_tier = explicit_tier(f.name, data["tier"]) if "tier" in data else None
        for spec in data.get("cases", []) or []:
            case = Case(area=a, id=str(spec["id"]), spec=spec, file=f)
            if case.key in seen:
                raise ValueError(f"duplicate parity case id {case.key}")
            seen.add(case.key)
            case.tier = classify_tier(a, spec, corpus, file_tier)
            marks = divergences.matching(a, case.key)
            where = f"{f.name}: {case.key}"
            errors += [f"{where}: {p}" for p in option_problems(spec.get("compare"))]
            if spec.get("known_divergence") is not None:
                if marks:
                    errors.append(
                        f"{where} is marked both in its case file and in "
                        + ", ".join(f"{m.file} ({m.key})" for m in marks)
                    )
                    continue
                errors += [
                    f"{where}: {p}" for p in check_mark(spec["known_divergence"], case.tier, refs)
                ]
            elif marks:
                if len(marks) > 1:
                    errors.append(
                        f"{case.key} is marked more than once: "
                        + ", ".join(f"{m.file} ({m.key})" for m in marks)
                    )
                    continue
                mark = marks[0]
                if is_glob_key(mark.key) and case.tier == 1:
                    errors.append(
                        f"{mark.file}: {mark.key} matches the tier-1 case {case.key}: "
                        "mark realistic cases one by one"
                    )
                errors += [f"{where}: {p}" for p in tier_problem(mark.div, case.tier)]
                spec["known_divergence"] = mark.div
            if tier is None or case.tier == tier:
                cases.append(case)
    _raise(errors)
    return cases


def _resolve(dotted: str) -> Any:
    module_name, _, attr = dotted.rpartition(".")
    obj = importlib.import_module(module_name)
    return getattr(obj, attr)


_PC_PATH = re.compile(r"\bpc((?:\.[A-Za-z_]\w*)+)")


def _import_named_modules(expr: str) -> None:
    """Import the pytacheck modules an expression names (``pc.statout.jasp.f``).
    A package exposes a submodule as an attribute only once it is imported, so
    without this a case would pass or fail with what earlier cases in the same
    process imported (``check --jobs`` and ``pytest -n`` order cases differently)."""
    for m in _PC_PATH.finditer(expr):
        obj: Any = importlib.import_module("pytacheck")
        for part in m.group(1).split(".")[1:]:
            name = f"{obj.__name__}.{part}"
            if not hasattr(obj, part):  # never import over an attribute (pc.read)
                try:
                    importlib.import_module(name)
                except ModuleNotFoundError as exc:
                    if exc.name != name:  # the module exists and failed to import
                        raise
                    break
            obj = getattr(obj, part, None)
            if not inspect.ismodule(obj):
                break


def py_name(r_name: str) -> str:
    name = r_name.replace(".", "_")
    return f"{name}_" if keyword.iskeyword(name) else name


def decode(x: Any) -> Any:
    """Decode a YAML argument spec into a Python value."""
    import numpy as np
    import pandas as pd

    import pytacheck as pc

    if isinstance(x, dict) and len(x) == 1 and next(iter(x)).startswith("$"):
        key, val = next(iter(x.items()))
        if key == "$paper":
            return pc.demopaper() if val == "demo" else pc.read(ROOT / val)
        if key == "$read":
            vals = val if isinstance(val, list) else [val]
            papers = pc.read([ROOT / v for v in vals])
            return papers if isinstance(papers, pc.PaperList) else pc.PaperList([papers])
        if key == "$test_paper":
            val = val or {}
            return pc.test_paper(val.get("text"), val.get("url") or [])
        if key == "$df":
            return pd.DataFrame(
                {k: [None if v is None else v for v in col] for k, col in val.items()}
            )
        if key in ("$chr", "$int", "$dbl", "$lgl"):
            vals = val if isinstance(val, list) else [val]
            conv = {"$chr": str, "$int": int, "$dbl": float, "$lgl": bool}[key]
            return [None if v is None else conv(v) for v in vals]
        if key == "$list":
            return [decode(v) for v in val]
        if key == "$null":
            return None
        if key == "$NA":
            return None
        if key == "$file":
            return str(ROOT / val)
        if key == "$expr":
            _import_named_modules(val["py"])
            return eval(val["py"], {"pc": pc, "pd": pd, "np": np})
        if key == "$call":
            fn = _resolve(val["py"])
            return fn(**decode_args(val.get("args") or {}, val.get("py_args"), val.get("py_drop")))
        if key == "$catch":
            from parity.pyhelpers import catch

            return catch(lambda: decode(val))
        raise ValueError(f"unknown argument constructor {key}")
    if isinstance(x, dict):
        return {k: decode(v) for k, v in x.items()}
    return x


def decode_args(
    args: dict[str, Any], py_args: dict[str, Any] | None = None, py_drop: list[str] | None = None
) -> dict[str, Any]:
    out = {py_name(k): decode(v) for k, v in args.items()}
    for k in py_drop or []:
        out.pop(py_name(k), None)
    for k, v in (py_args or {}).items():
        out[k] = decode(v)
    return out


def _mock_dir(spec: dict[str, Any]) -> Any:
    """``replay()`` context for a case's ``mock_dir`` (a no-op without one)."""
    import contextlib

    d = spec.get("mock_dir")
    if not d:
        return contextlib.nullcontext()
    from tests.httpmock import replay

    path = Path(d)
    if not path.is_absolute() and (d.startswith("tests/") or d.startswith("parity/")):
        path = ROOT / d
    return replay(path if path.is_absolute() else d)


# pytacheck defaults that deliberately differ from metacheck's:
# (module, function, argument, metacheck's default)
METACHECK_DEFAULTS: tuple[tuple[str, str, str, Any], ...] = (
    # bibr export schema 12.0 is pytacheck's paper format; metacheck's read()
    # and grobid_to_bibr() convert Grobid TEI to its older format
    ("pytacheck.io.read", "read", "schema_version", None),
    ("pytacheck.io.grobid", "grobid_to_bibr", "schema_version", None),
    # metacheck's paper_write() saves the paper object unless schema_version =
    # "12.0"; pytacheck's "auto" writes a 12.x paper as a 12.0 file
    ("pytacheck.papers.io", "paper_write", "schema_version", None),
)


@contextlib.contextmanager
def metacheck_defaults() -> Iterator[None]:
    """Run with metacheck's defaults for the arguments in ``METACHECK_DEFAULTS``.

    The functions' own default values are swapped for the duration, so every
    caller (argument constructors, ``$expr`` code, pytacheck functions that
    call them) sees metacheck's behaviour; an explicit argument still wins.
    """
    saved: list[tuple[Any, str, Any]] = []
    try:
        for module_name, fn_name, arg, value in METACHECK_DEFAULTS:
            fn = getattr(importlib.import_module(module_name), fn_name)
            param = inspect.signature(fn).parameters[arg]
            if param.kind is inspect.Parameter.KEYWORD_ONLY:
                kwdefaults = dict(fn.__kwdefaults__ or {})
                saved.append((fn, "__kwdefaults__", fn.__kwdefaults__))
                kwdefaults[arg] = value
                fn.__kwdefaults__ = kwdefaults
            else:
                names = [
                    n
                    for n, p in inspect.signature(fn).parameters.items()
                    if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
                    and p.default is not inspect.Parameter.empty
                ]
                defaults = list(fn.__defaults__)
                saved.append((fn, "__defaults__", fn.__defaults__))
                defaults[names.index(arg)] = value
                fn.__defaults__ = tuple(defaults)
        yield
    finally:
        for fn, attr, old in reversed(saved):
            setattr(fn, attr, old)


# -- deterministic paper ids ----------------------------------------------------


def parity_id(n: int) -> str:
    """The id of the *n*-th paper created without one in a case (as parity/r/run_cases.R)."""
    return hashlib.md5(f"pytacheck-parity-{n}".encode(), usedforsecurity=False).hexdigest()[:14]


@contextlib.contextmanager
def deterministic_ids() -> Iterator[None]:
    """Number id-less papers ``parity_id(1)``, ``parity_id(2)``, ... instead of
    hashing the time, as the R runner does."""
    from pytacheck.papers import model

    counter = iter(range(1, 1 << 62))
    saved = model._random_id
    model._random_id = lambda: parity_id(next(counter))
    try:
        yield
    finally:
        model._random_id = saved


# -- time zone ----------------------------------------------------------------------


@contextlib.contextmanager
def utc() -> Iterator[None]:
    """Run in UTC, as the goldens were made (``TZ=UTC``)."""
    old = os.environ.get("TZ")
    if old == "UTC" or not hasattr(time, "tzset"):
        yield
        return
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


# -- cases whose Python side runs R ---------------------------------------------

_R_PROGRAMS = {"Rscript", "R", "Rscript.exe", "R.exe"}
_NO_R = str(Path(os.sep) / "pytacheck-parity-no-reference-R" / "Rscript")


@functools.cache
def reference_r() -> str | None:
    """``PYTACHECK_RSCRIPT`` when it is an R >= 4.5 (the reference), else ``None``."""
    rscript = os.environ.get("PYTACHECK_RSCRIPT")
    if not rscript:
        return None
    try:
        version = subprocess.run(
            [rscript, "--vanilla", "-e", "cat(as.character(getRversion()))"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    parts = tuple(int(p) for p in version.split(".")[:2] if p.isdigit())
    return rscript if parts >= (4, 5) else None


class RWithoutReference(BaseException):
    """The Python side of a case started R, but no reference R is configured.

    A ``BaseException``, so that code catching ``Exception`` around its R call
    does not turn the attempt into an ordinary result.
    """


def _is_r(args: Any) -> bool:
    if isinstance(args, str | bytes | os.PathLike):
        text = os.fsdecode(args)
        try:
            first = shlex.split(text)[0] if text.strip() else ""
        except ValueError:
            first = text.split()[0] if text.split() else ""
    elif isinstance(args, list | tuple) and args:
        first = os.fsdecode(args[0])
    else:
        return False
    return first == _NO_R or os.path.basename(first) in _R_PROGRAMS


@contextlib.contextmanager
def _watch_for_r(attempts: list[str]) -> Iterator[None]:
    """Refuse to start R, recording each attempt in *attempts*; ``shutil.which()``
    finds a stand-in ``Rscript``, so code that looks for R first tries to start it."""
    popen_init = subprocess.Popen.__init__
    which = shutil.which

    def guarded_init(self: Any, args: Any, *a: Any, **kw: Any) -> None:
        if _is_r(args):
            attempts.append(str(args))
            raise RWithoutReference(str(args))
        popen_init(self, args, *a, **kw)

    def guarded_which(cmd: Any, *a: Any, **kw: Any) -> Any:
        if os.path.basename(os.fsdecode(cmd)) in _R_PROGRAMS:
            return _NO_R
        return which(cmd, *a, **kw)

    subprocess.Popen.__init__ = guarded_init  # type: ignore[method-assign]
    shutil.which = guarded_which
    try:
        yield
    finally:
        subprocess.Popen.__init__ = popen_init  # type: ignore[method-assign]
        shutil.which = which


def skip_reason(case: Case) -> str | None:
    """Why *case* cannot run here (it needs the reference R), or ``None``."""
    if case.spec.get("needs_r") and reference_r() is None:
        return NEEDS_R_REASON
    return None


NEEDS_R_REASON = (
    "its Python side runs R: set PYTACHECK_RSCRIPT to the reference R (>= 4.5 with "
    "metacheck) to check it"
)


def run_python(case: Case) -> Any:
    """Run the Python side of *case* and return its result.

    Without the reference R, a case whose Python side starts R raises
    :class:`RWithoutReference` (report it as skipped).
    """
    attempts: list[str] = []
    guard = _watch_for_r(attempts) if reference_r() is None else contextlib.nullcontext()
    try:
        with utc(), deterministic_ids(), guard, _mock_dir(case.spec), metacheck_defaults():
            result = _run_python(case)
    except BaseException:
        if attempts:
            raise RWithoutReference(attempts[0]) from None
        raise
    if attempts:  # the attempt was caught and turned into a result
        raise RWithoutReference(attempts[0])
    return result


def _run_python(case: Case) -> Any:
    spec = case.spec
    args = decode_args(spec.get("args") or {}, spec.get("py_args"), spec.get("py_drop"))
    if "module" in spec:
        from pytacheck.module import module_run

        paper = args.pop("paper")
        return module_run(paper, spec["module"], **args)
    fn = _resolve(spec["py"])
    return fn(**args)
