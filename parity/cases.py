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

A case may set ``known_divergence: <reason>`` to record an intentional,
documented difference from R (the test is then an expected failure);
``HARNESS_DIVERGENCES`` below does the same for cases in other areas' files.

When R raises an error, Python must raise one whose message matches R's
(``compare: {error: contains|exact|any}``, see ``parity.compare.error_matches``).

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
import hashlib
import importlib
import inspect
import keyword
import os
import shlex
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "parity" / "cases"
GOLDEN_DIR = ROOT / "parity" / "golden"


@dataclass
class Case:
    area: str
    id: str
    spec: dict[str, Any]
    file: Path

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


# Divergences from R that the harness only sees since it compares error
# messages, repeated names and the element types of lists, in cases whose
# files belong to other areas: each is an expected failure (as if the case set
# ``known_divergence``) until the port matches R or the case file records it.
HARNESS_DIVERGENCES: dict[str, str] = {
    **dict.fromkeys(
        [
            "mod_codebook_review/codebook_check.rv_dupscale",
            "mod_codebook_review/codebook_check.llm.rv_dupscale",
            "mod_codebook_review/codebook_check.llm_odd.rv_dupscale",
            "mod_codebook_review/codebook_check.llm_odd_na.rv_dupscale",
        ],
        "D14: the OSD translations$en list repeats bfi_2 in R; pytacheck keeps one "
        "entry per name (a dict)",
    ),
    # a different error than R's
    "archives_d1_review/dryad_info.review.missing_id_col_error": (
        "wrong error: R's join fails on the missing id column ('Join columns in `x` must "
        "be present'), pytacheck raises its own column error"
    ),
    "archives_d2_review/.rbox_info.review.no_markup_error": (
        "error message lacks R's working directory ('(<repo>)')"
    ),
    "archives_d2_review/.rbox_info.review.two_redirects_error": (
        "error names the argument `base_url` where R names `url`"
    ),
    "archives_d2_review/.psycharchives_info.review.list_value_error": (
        "vapply() error names FUN(X[[1]]) where R names FUN(X[[2]])"
    ),
    "archives_gz_review/zenodo_info.review.factor_na": (
        "wrong error: R's bind_rows() cannot combine license <character> and <list>, "
        "pytacheck fails on missing row names"
    ),
    "archives_gz_review/zenodo_info.review.id_col_zero": (
        "wrong error: R 'attempt to select less than one element in get1index <real>', "
        "pytacheck 'subscript out of bounds'"
    ),
    "archives_osf_review/.osf_parse_response.review.json_nan_error": (
        "jsonlite's lexical error shows the text around the NaN; pytacheck does not"
    ),
    "bibr12/paper_write.error.version": (
        "D1: pytacheck's paper_write() also takes schema_version 'auto', and says so"
    ),
    "codecheck/code_read.null": (
        "wrong error for file = NULL: R \"invalid 'file' argument\", pytacheck "
        "'argument is of length zero'"
    ),
    **dict.fromkeys(
        [
            "codecheck/code_extract_r.text.bad_options.doc0",
            "codecheck/code_extract_r.text.bad_options.doc1",
            "codecheck/code_extract_r.text.bad_options.doc2",
        ],
        "knitr's chunk-option parse error: R parses 'alist( bad syntax' (1:12), "
        "pytacheck 'alist(bad syntax' (1:11) with an extra 'Invalid syntax' line",
    ),
    "db_review/crossref_query.replay.df_list_author_two": (
        "wrong error: R \"'length = 2' in coercion to 'logical(1)'\", pytacheck "
        "'values must be length 1'"
    ),
    **dict.fromkeys(
        [
            "grobid12/grobid_to_bibr.error.schema_version_11",
            "grobid12/grobid_to_bibr.error.schema_version_number",
            "grobid12/grobid_to_bibr.error.schema_version_vector",
            "grobid12/.grobid_to_bibr.error.schema_version_11",
        ],
        "error message says None where R says NULL ('schema_version must be NULL or \"12.0\"')",
    ),
    "io/.grobid_to_bibr.synthetic.table_norows": (
        "tibble's error says 'Existing data has 1 row' (pytacheck '1 rows') and goes on "
        "with details pytacheck leaves out"
    ),
    "io/.tei_bib.raw_reference_mismatch": (
        "tibble's error names the deparsed assigned expression; pytacheck names `value`"
    ),
    "io_review/papers_load.download_missing": (
        "error message: R 'Download failed: GET <url>', pytacheck 'Download failed "
        "(status 404): <url>'"
    ),
    **dict.fromkeys(
        [
            "mod_coi/coi_check.heading_last_sentence",
            "mod_coi_review/coi_check.paperlist_one_errors",
            "mod_funding/funding_check.error",
            "mod_effect_size_review/stat_effect_size.review.empty_paperlist",
            "mod_ref_db_review/ref_miscitation.bib_without_text_id",
        ],
        "module error: R reports dplyr's context ('In argument: ...'), pytacheck the "
        "underlying error",
    ),
    "mod_prereg/prereg_check.aspredicted.unreachable": (
        "module error: R 'GET <url>', pytacheck 'Failed to perform HTTP request: <url>'"
    ),
    "mod_prereg_review/prereg_check.synthetic.label_nonchar": (
        "R's message holds the character U+FFFF, pytacheck's the text '\\uffff'"
    ),
    "mod_ref_accuracy_review/ref_accuracy.error.character_key": (
        "module error: R's join type error, pytacheck pandas' merge error"
    ),
    "mod_ref_db_review/ref_miscitation.cermine_no_doi": (
        "module error: R 'Join columns in `x` must be present in the data.', pytacheck "
        "KeyError 'doi'"
    ),
    "repo_download_review/decision.cp437.zip": (
        "invalid multibyte string: R shows the text from the bad byte ('<82>es'), "
        "pytacheck the whole name ('donn<82>es')"
    ),
    "report/report.error.format": (
        "pytacheck's report() also writes 'md', and its message says so"
    ),
    "text_extract/extract_eq.chr": (
        "wrong error for character input: R 'Argument 1 must be a data frame or a "
        "named atomic vector.', pytacheck 'argument is of length zero'"
    ),
    **dict.fromkeys(
        [
            "text_extract_review/extract_p_values.review.df_no_text_col",
            "text_extract_review/extract_eq.review.df_no_text_col",
        ],
        "wrong error without a text column: R's tibble assignment error, pytacheck KeyError 'text'",
    ),
}


def load_cases(area: str | None = None) -> list[Case]:
    cases: list[Case] = []
    seen: set[str] = set()
    for f in iter_case_files(area):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        a = data.get("area", f.stem)
        for spec in data.get("cases", []) or []:
            case = Case(area=a, id=str(spec["id"]), spec=spec, file=f)
            if case.key in seen:
                raise ValueError(f"duplicate parity case id {case.key}")
            seen.add(case.key)
            if case.key in HARNESS_DIVERGENCES:
                spec.setdefault("known_divergence", HARNESS_DIVERGENCES[case.key])
            cases.append(case)
    return cases


def _resolve(dotted: str) -> Any:
    module_name, _, attr = dotted.rpartition(".")
    obj = importlib.import_module(module_name)
    return getattr(obj, attr)


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
            return eval(val["py"], {"pc": pc, "pd": pd, "np": np})
        if key == "$call":
            fn = _resolve(val["py"])
            return fn(**decode_args(val.get("args") or {}, val.get("py_args"), val.get("py_drop")))
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
