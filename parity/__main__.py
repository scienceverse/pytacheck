"""Parity tooling: ``python -m parity <command>``.

Commands
--------
generate  Run the R reference (metacheck) and write golden JSON files.
check     Run the Python port and compare it with the goldens.
lock      Pin the marked cases' differences in parity/lock/<area>.json.
list      List cases, their tiers and whether they have goldens.
accuracy  Score pytacheck against metacheck on the realistic corpus (parity.accuracy).

Examples::

    python -m parity generate --area text          # needs R + metacheck
    python -m parity check --area text -v
    python -m parity check --area core,text_extract+review  # core, text_extract(_review)
    python -m parity check --only text_search.demo.significant
    python -m parity check --tier 1 --jobs 4       # the realistic corpus, 4 processes
    python -m parity check --strict --jobs 4       # a changed tier-2 mark fails too
    python -m parity check --md summary.md         # also a Markdown summary
    python -m parity check --report out.json       # the per-case JSON report
    python -m parity lock --area text              # after adding a mark
    python -m parity lock --area text --reviewed   # after reviewing the changes it printed
    python -m parity lock -k json_expand --suggest # also propose marks for R crashes
    python -m parity accuracy --gate               # the realistic-corpus report and gate

``--area`` takes one area, several separated by commas, or ``<area>+review``
for an area and its ``<area>_review`` cases, and may be repeated.

``generate`` uses the ``Rscript`` on PATH unless ``PYTACHECK_RSCRIPT`` or
``--rscript`` points elsewhere, and always runs R under ``C.UTF-8`` / UTC
so goldens do not depend on the machine's locale.

``check`` and ``lock`` run each case offline (a connection or host-name lookup
is an ``error``), with a fresh cache directory, and keep what it prints out of
the terminal (``-v`` shows it; the report keeps it for failing cases). The
report goes to a new ``parity/_out/report-<time>-<pid>.json`` unless
``--report`` names a file, so runs side by side do not overwrite each other.

Statuses of ``check`` (see docs/PARITY.md and ``parity.lockfile``): ``pass``;
``xfail`` (marked ``known_divergence``, and R's golden, Python's result and the
paths where they differ are as locked); ``xpass`` (marked, but it matches R: remove
the mark); ``r_changed`` / ``py_changed`` (a marked case whose golden / Python
result changed since it was locked: a failure for a tier-1 case, a warning for a
tier-2 one unless a value became an exception or ``--strict`` is given);
``unlocked`` (marked, no lock entry); ``fail`` / ``error`` (unmarked, differs
from R / Python raised where R returned); ``skip`` (needs the reference R);
``missing`` (no golden); ``quarantined`` (would fail, but parity/quarantine.yaml
lists it with a reason: never a failure, ``--strict`` included).

``check`` and ``lock`` stop with a hint, before running anything, when the
checkout lacks its ``upstream/metacheck`` submodule or the ``data`` extra's readers.

``lock`` prints each entry it adds, changes or removes: the case, its tier and
mark, the old and new fingerprints, and how Python differs from R. It adds new
entries and removes stale ones, but changes an existing entry only with
``--reviewed``, once that diff has been read (``--md`` also writes it as a
Markdown table for the pull request).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import orjson

from parity.canonical import canonical
from parity.cases import (
    NEEDS_R_REASON,
    ROOT,
    VALUE_WHERE_R_FAILS_KINDS,
    Case,
    RWithoutReference,
    UnknownAreaError,
    expected_to_fail,
    iter_case_files,
    load_cases,
    load_quarantine,
    parse_areas,
    r_text,
    run_python,
    skip_reason,
    without_credentials,
)
from parity.compare import (
    Options,
    comparable,
    compare_paths,
    rewrite_r_text,
    summarize,
)
from parity.lockfile import (
    R_ERROR_PY_VALUE,
    R_VALUE_PY_ERROR,
    Fingerprint,
    digest,
    entry_diff,
    lock_path,
    locked_areas,
    r_digest,
    raised,
    read_lock,
    watch_run,
    write_lock,
)

OUT_DIR = ROOT / "parity" / "_out"


def _rscript(explicit: str | None) -> str:
    candidate = explicit or os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript")
    if not candidate:
        sys.exit("Rscript not found: install R + metacheck or set PYTACHECK_RSCRIPT")
    # metacheck needs R >= 4.5; an older R on PATH silently produces different goldens.
    version = subprocess.run(
        [candidate, "--vanilla", "-e", "cat(as.character(getRversion()))"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    parts = tuple(int(p) for p in version.split(".")[:2] if p.isdigit())
    if parts < (4, 5):
        sys.exit(
            f"{candidate} is R {version or '?'}; goldens need R >= 4.5 with metacheck "
            "installed (set PYTACHECK_RSCRIPT or pass --rscript)"
        )
    return candidate


def cmd_generate(ns: argparse.Namespace) -> int:
    files = [str(f) for f in iter_case_files(ns.area)]
    if not files:
        print("no case files matched")
        return 1
    rscript = _rscript(ns.rscript)
    env = without_credentials({**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"})
    status = 0
    # one R session per case file: what a case leaves behind (a package it
    # loaded, an option it set) must not change the goldens of other areas, so
    # that `--area x` and a full run write the same files
    for f in files:
        cmd = [rscript, str(ROOT / "parity" / "r" / "run_cases.R"), str(ROOT), f]
        if ns.only:
            cmd += ["--only", ",".join(ns.only)]
        status = max(status, subprocess.call(cmd, env=env, cwd=ROOT))
    return status


# -- one case ---------------------------------------------------------------------------

PASS, XFAIL, XPASS = "pass", "xfail", "xpass"
PY_CHANGED, R_CHANGED, UNLOCKED = "py_changed", "r_changed", "unlocked"
FAIL, ERROR, SKIP, MISSING = "fail", "error", "skip", "missing"
QUARANTINED = "quarantined"
#: the order statuses are listed in
STATUSES = (
    PASS,
    XFAIL,
    SKIP,
    QUARANTINED,
    XPASS,
    R_CHANGED,
    PY_CHANGED,
    UNLOCKED,
    FAIL,
    ERROR,
    MISSING,
)
#: the statuses that parity/quarantine.yaml turns into ``quarantined``: all that can fail
_QUARANTINABLE = (XPASS, R_CHANGED, PY_CHANGED, UNLOCKED, FAIL, ERROR, MISSING)


@dataclass
class CaseResult:
    """What ``check`` found for one case."""

    key: str
    area: str
    id: str
    tier: int
    status: str = PASS
    #: whether the status fails the run (``r_changed``/``py_changed`` of a tier-2
    #: case only warn, unless the check is strict)
    failing: bool = False
    problems: list[str] = field(default_factory=list)
    seconds: float = 0.0
    kind: str | None = None
    ref: str | None = None
    #: the case's mark has ``r_text`` substitutions
    r_text: bool = False
    #: an expected failure's fingerprints, as ``lock`` would record them
    fingerprint: Fingerprint | None = None
    #: an expected failure's differences from R (its problems, without advice)
    differences: list[str] = field(default_factory=list)
    #: R's error message when R raised and Python returned a value
    r_error: str | None = None
    #: what the case printed (its last ``OUTPUT_LIMIT`` characters), when the
    #: runner keeps it (``run_cases``)
    output: str = ""

    @classmethod
    def of(cls, case: Case) -> CaseResult:
        div = case.spec.get("known_divergence")
        div = div if isinstance(div, dict) else {}
        return cls(
            key=case.key,
            area=case.area,
            id=case.id,
            tier=case.tier,
            kind=div.get("kind"),
            ref=div.get("ref"),
            r_text=bool(r_text(case.spec)),
        )

    def done(
        self, status: str, problems: list[str] | None = None, failing: bool | None = None
    ) -> CaseResult:
        self.status = status
        self.problems = problems or []
        self.failing = (
            status in (XPASS, UNLOCKED, FAIL, ERROR, MISSING) if failing is None else failing
        )
        return self

    @property
    def warning(self) -> bool:
        """A changed marked case that only warns (tier 2)."""
        return self.status in (R_CHANGED, PY_CHANGED) and not self.failing

    def as_json(self) -> dict[str, Any]:
        return {
            "case": self.key,
            "tier": self.tier,
            "status": self.status,
            "failing": self.failing,
            "kind": self.kind,
            "ref": self.ref,
            "r_text": self.r_text,
            "lock": self.fingerprint.as_json() if self.fingerprint else None,
            "problems": self.problems,
            "seconds": round(self.seconds, 4),
            **({"output": self.output} if self.output and (self.failing or self.warning) else {}),
        }


_UNNEEDED_R_TEXT = (
    "the case matches R's golden without its r_text substitutions (they change only "
    "text the comparison skips): remove the mark"
)


def check_case(case: Case) -> tuple[str, list[str], float]:
    """``(status, problems, seconds)`` of *case* (see :func:`run_case`)."""
    res = run_case(case)
    return res.status, res.problems, res.seconds


def run_case(
    case: Case, lock: Mapping[str, Fingerprint] | None = None, strict: bool = False
) -> CaseResult:
    """:func:`_run_case`, with a quarantined case that would fail reported as such.

    A case listed in parity/quarantine.yaml is ``quarantined`` instead of any status
    that can fail (all but ``pass``, ``xfail`` and ``skip``), and never fails the
    run, strict or not; the reason comes first among its problems. One that passes
    stays a pass.
    """
    res = _run_case(case, lock, strict)
    reason = load_quarantine().get(case.key)
    if reason is not None and res.status in _QUARANTINABLE:
        res.done(QUARANTINED, [f"quarantined: {reason}", *res.problems], failing=False)
    return res


def _run_case(
    case: Case, lock: Mapping[str, Fingerprint] | None = None, strict: bool = False
) -> CaseResult:
    """Run *case*'s Python side and compare it with R's golden.

    *lock* is its area's lock (default: ``parity/lock/<area>.json``). With
    *strict*, a tier-2 case that changed since it was locked fails, as a tier-1
    one does, instead of warning. A
    ``known_divergence`` with ``r_text`` compares Python with R's golden as
    rewritten by its substitutions: such a case passes when that is all that
    differs (and fails when a substitution changes nothing, or when the case
    matches R's golden as it is), and is an expected failure only with ``xfail:
    true``. An expected failure is ``xfail`` only while it differs from R as its
    lock entry says.
    """
    res = CaseResult.of(case)
    if not case.golden_path.exists():
        return res.done(MISSING, ["no golden file; run `python -m parity generate`"])
    reason = skip_reason(case)
    if reason:
        return res.done(SKIP, [reason])
    golden = orjson.loads(case.golden_path.read_bytes())
    subs = r_text(case.spec)
    used = [False] * len(subs)
    compared = golden
    if subs and golden["ok"]:  # R's error text is never compared: nothing to rewrite
        compared = {**golden, "value": rewrite_r_text(golden["value"], subs, used)}
    options = Options.from_case(case.spec.get("compare"))
    from tests.httpmock import no_network

    attempts: list[str] = []
    err: Exception | None = None
    start = time.perf_counter()
    try:
        with watch_run() as run, no_network(attempts), _case_cache_dir():
            result = run_python(case)
    except RWithoutReference:
        res.seconds = time.perf_counter() - start
        return res.done(SKIP, [NEEDS_R_REASON])
    except Exception as exc:
        result, err = None, exc
    res.seconds = time.perf_counter() - start
    if attempts:
        return res.done(
            ERROR,
            [
                f"the case used the network ({attempts[0]}): parity cases run offline, "
                "against recorded responses (mock_dir) or a fake"
            ],
        )

    py = None if err is not None else canonical(result)
    problems, paths = _differences(compared, py, err, options)
    if subs:
        stale = [
            f"r_text {sub} changes nothing in R's golden: remove it from the mark"
            for sub, u in zip(subs, used, strict=True)
            if not u
        ]
        # a case that matches R's golden as it is does not differ from R where it compares
        unneeded = not problems and not _differences(golden, py, err, options)[0]
        if stale or unneeded:
            return res.done(FAIL, stale + ([_UNNEEDED_R_TEXT] if unneeded else []) + problems)
    expected = expected_to_fail(case.spec)
    if not problems:
        if not expected:
            return res.done(PASS)
        stale_mark = (
            "matches R's golden as its r_text rewrites it: drop `xfail: true` from the mark"
            if subs
            else "matches R: the known_divergence is stale, remove the mark"
        )
        return res.done(XPASS, [stale_mark])
    if not golden["ok"] and err is None:
        res.r_error = str(golden.get("error") or "")
    if not expected:
        return res.done(ERROR if golden["ok"] and err is not None else FAIL, problems)
    if res.r_error is not None and res.kind not in VALUE_WHERE_R_FAILS_KINDS:
        return res.done(
            FAIL,
            [
                f"R fails and Python returns a value, which only a mark of kind "
                f"{' / '.join(sorted(VALUE_WHERE_R_FAILS_KINDS))} (with its U/D-entry) "
                f"explains, not {res.kind}",
                *problems,
            ],
        )

    fp = Fingerprint(
        r=r_digest(golden),
        py=raised(err) if err is not None else digest(comparable(py, options), run),
        diff=tuple(paths),
    )
    res.fingerprint, res.differences = fp, problems
    entry = (read_lock(case.area) if lock is None else lock).get(case.id)
    if entry is None:
        return res.done(
            UNLOCKED,
            [
                f"no lock entry: review the difference, then `python -m parity lock -k {case.id}`",
                *problems,
            ],
        )
    if entry == fp:
        return res.done(XFAIL, problems)
    # a tier-2 change only warns, unless the check is strict or Python now raises
    # where it returned a value
    failing = strict or case.tier == 1 or (fp.raises and not entry.raises)
    if entry.r != fp.r:
        what = ["R's golden changed since the case was locked: check that the mark still holds"]
        return res.done(R_CHANGED, what + _changes(entry, fp, case.id) + problems, failing)
    return res.done(PY_CHANGED, _changes(entry, fp, case.id) + problems, failing)


def _changes(entry: Fingerprint, fp: Fingerprint, case_id: str) -> list[str]:
    out = []
    if entry.py != fp.py:
        out.append(f"Python's result changed since the case was locked ({entry.py} -> {fp.py})")
    if entry.diff != fp.diff:
        out.append(f"the differing paths changed: {list(entry.diff)} -> {list(fp.diff)}")
    return [*out, f"re-lock it once reviewed: `python -m parity lock -k {case_id} --reviewed`"]


def _differences(
    golden: dict[str, Any], py: Any, err: Exception | None, options: Options
) -> tuple[list[str], list[str]]:
    """The problems of Python's canonical result *py* (or exception *err*) against
    *golden*, and the paths where they differ (see ``parity.lockfile``).

    When R raised an error, Python raising too is a match, whatever the texts say.
    """
    if not golden["ok"]:
        if err is None:
            return (
                [f"R raised an error ({golden['error']}) but Python returned a value"],
                [R_ERROR_PY_VALUE],
            )
        return [], []
    if err is not None:
        tb = "".join(traceback.format_exception_only(type(err), err)).strip()
        return [f"Python raised {tb}"], [R_VALUE_PY_ERROR]
    return compare_paths(golden["value"], py, options)


@contextlib.contextmanager
def _case_cache_dir() -> Iterator[None]:
    """A fresh ``PYTACHECK_CACHE_DIR`` for one case, inside the run's.

    No case sees what another cached (a repository listing, an LLM answer), so a
    result does not depend on which cases ran before it in the same process.
    """
    base = os.environ.get("PYTACHECK_CACHE_DIR")
    if base:
        os.makedirs(base, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="case-", dir=base or None, ignore_cleanup_errors=True
    ) as folder:
        os.environ["PYTACHECK_CACHE_DIR"] = folder
        try:
            yield
        finally:
            if base is None:
                os.environ.pop("PYTACHECK_CACHE_DIR", None)
            else:
                os.environ["PYTACHECK_CACHE_DIR"] = base


# -- many cases -------------------------------------------------------------------------

#: the end of a case's output kept in its result
OUTPUT_LIMIT = 4000


@contextlib.contextmanager
def _captured(res: CaseResult) -> Iterator[None]:
    """Keep what a case prints in *res* instead of the terminal: Python's
    ``sys.stdout``/``sys.stderr`` (``print``, warnings) and the process's file
    descriptors 1 and 2 (R and any other program it starts)."""
    sys.stdout.flush()
    sys.stderr.flush()
    saved_fds = os.dup(1), os.dup(2)
    saved_streams = sys.stdout, sys.stderr
    with tempfile.TemporaryFile() as sink:
        text = open(  # noqa: SIM115 -- closed below, before the sink
            sink.fileno(), "w", encoding="utf-8", errors="backslashreplace", closefd=False
        )
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        sys.stdout = sys.stderr = text
        try:
            yield
        finally:
            text.close()
            sys.stdout, sys.stderr = saved_streams
            for fd, old in zip((1, 2), saved_fds, strict=True):
                os.dup2(old, fd)
                os.close(old)
            size = sink.seek(0, os.SEEK_END)
            sink.seek(max(0, size - OUTPUT_LIMIT))
            kept = sink.read().decode("utf-8", "replace")
            res.output = ("..." if size > OUTPUT_LIMIT else "") + kept


def _run_quietly(
    case: Case, lock: Mapping[str, Fingerprint] | None, strict: bool = False
) -> CaseResult:
    holder = CaseResult.of(case)
    with _captured(holder):
        res = run_case(case, lock, strict)
    res.output = holder.output
    return res


_POOL_CASES: list[Case] = []
_POOL_USE_LOCK = True
_POOL_STRICT = False


def _pool_init(keys: list[str], use_lock: bool, strict: bool) -> None:
    global _POOL_CASES, _POOL_USE_LOCK, _POOL_STRICT
    _POOL_USE_LOCK, _POOL_STRICT = use_lock, strict
    if not _POOL_CASES:  # a spawned (not forked) worker loads the cases itself
        _hermetic_env()
        by_key = {c.key: c for c in load_cases()}
        _POOL_CASES = [by_key[k] for k in keys]


def _pool_task(i: int) -> tuple[int, CaseResult]:
    return i, _run_quietly(_POOL_CASES[i], None if _POOL_USE_LOCK else {}, _POOL_STRICT)


def run_cases(
    cases: list[Case],
    jobs: int = 1,
    use_lock: bool = True,
    progress: Callable[[CaseResult], None] | None = None,
    strict: bool = False,
) -> list[CaseResult]:
    """:func:`run_case` for every case, in *jobs* processes (0: one per CPU), with
    each case's output kept in its result; results in case order.

    Without *use_lock*, expected failures come out ``unlocked`` with their
    fingerprints (what ``lock`` records). *strict* is :func:`run_case`'s.
    """
    global _POOL_CASES
    jobs = jobs if jobs > 0 else os.cpu_count() or 1
    lock: Mapping[str, Fingerprint] | None = None if use_lock else {}
    if jobs == 1 or len(cases) < 2:
        out = []
        for case in cases:
            res = _run_quietly(case, lock, strict)
            if progress:
                progress(res)
            out.append(res)
        return out
    results: list[CaseResult | None] = [None] * len(cases)
    # forked workers share the loaded cases and the imported modules; fork is
    # unsafe with the system libraries of macOS, where workers are spawned
    method = "fork" if sys.platform.startswith("linux") else "spawn"
    ctx = multiprocessing.get_context(method)
    _POOL_CASES = cases if method == "fork" else []
    try:
        with ctx.Pool(
            min(jobs, len(cases)),
            initializer=_pool_init,
            initargs=([c.key for c in cases], use_lock, strict),
        ) as pool:
            for i, res in pool.imap_unordered(_pool_task, range(len(cases)), chunksize=1):
                results[i] = res
                if progress:
                    progress(res)
    finally:
        _POOL_CASES = []
    return [r for r in results if r is not None]


def _select(ns: argparse.Namespace) -> list[Case]:
    cases = load_cases(ns.area, tier=ns.tier)
    if ns.only:
        cases = [c for c in cases if c.id in ns.only or c.key in ns.only]
    if ns.k:
        cases = [c for c in cases if ns.k in c.key]
    return cases


def _partial(ns: argparse.Namespace) -> bool:
    """Whether the selection may leave out some cases of an area."""
    return bool(ns.only or ns.k or ns.tier)


def stale_lock_entries(cases: list[Case], areas: list[str] | None = None) -> list[str]:
    """Lock entries (``area/id``) that name no expected failure among *cases*, which
    must be every case of their areas; *areas* also checks lock files of areas
    without cases (``None``: every lock file)."""
    expected: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        expected.setdefault(case.area, set())
        if expected_to_fail(case.spec):
            expected[case.area].add(case.id)
    checked = set(expected) | set(locked_areas() if areas is None else areas)
    return [
        f"{area}/{case_id}"
        for area in sorted(checked)
        for case_id in sorted(read_lock(area))
        if case_id not in expected.get(area, ())
    ]


def stale_quarantine_entries(cases: list[Case], areas: list[str] | None = None) -> list[str]:
    """Quarantined cases (``area/id``) that are not among *cases*, which must be
    every case of their areas; with *areas*, only those of these areas are judged."""
    known = {c.key for c in cases}
    return [
        key
        for key in load_quarantine()
        if key not in known and (areas is None or key.partition("/")[0] in areas)
    ]


#: modules the goldens of the data-reading areas need (the ``data`` extra's readers)
_EXTRA_MODULES = ("pyreadstat", "xlrd")


def environment_problems() -> list[str]:
    """What a fresh checkout still lacks to run the cases, with the command that
    supplies it. Without it, hundreds of cases fail for one missing piece."""
    problems = []
    if not (ROOT / "upstream" / "metacheck" / "DESCRIPTION").is_file():
        problems.append(
            "upstream/metacheck is empty (the cases read its fixtures and recorded "
            "responses): run `git submodule update --init`"
        )
    lacking = [m for m in _EXTRA_MODULES if importlib.util.find_spec(m) is None]
    if lacking:
        problems.append(
            f"{', '.join(lacking)} not installed (the data-reading cases need them): "
            "run `uv sync --locked --all-extras`"
        )
    return problems


def _environment_ok() -> bool:
    problems = environment_problems()
    for problem in problems:
        print(f"parity: {problem}", file=sys.stderr)
    return not problems


def _root_entries() -> set[str]:
    return {p.name for p in ROOT.iterdir()}


class _Progress:
    """Prints each case that fails or warns (every case with *verbose*; none
    without *cases*) as it finishes, and on a terminal how many cases are done."""

    def __init__(self, total: int, verbose: bool, cases: bool = True) -> None:
        self.total, self.verbose, self.cases, self.done = total, verbose, cases, 0
        self.tty = sys.stderr.isatty()

    def __call__(self, res: CaseResult) -> None:
        self.done += 1
        if self.tty:
            sys.stderr.write("\r\033[K")
        if self.cases and (self.verbose or res.failing or res.warning):
            note = " (warning)" if res.warning else ""
            print(f"[{res.status.upper():10}] {res.key} ({res.seconds * 1000:.0f} ms){note}")
            if res.problems:
                print(summarize(res.problems))
            if res.output and (self.verbose or res.failing):
                print(textwrap.indent(res.output.rstrip(), "  | "))
            sys.stdout.flush()
        if self.tty and self.done < self.total:
            sys.stderr.write(f"{self.done}/{self.total} cases")
            sys.stderr.flush()


def _default_report() -> Path:
    return OUT_DIR / f"report-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}.json"


def cmd_check(ns: argparse.Namespace) -> int:
    if not _environment_ok():
        return 2
    root_before = _root_entries()
    started = time.perf_counter()
    cases = _select(ns)
    progress = _Progress(len(cases), ns.verbose)
    results = run_cases(cases, ns.jobs, progress=progress, strict=ns.strict)
    elapsed = time.perf_counter() - started
    stale = [] if _partial(ns) else stale_lock_entries(cases, ns.area)
    unknown = [] if _partial(ns) else stale_quarantine_entries(cases, ns.area)

    report = Path(ns.report) if ns.report else _default_report()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_bytes(orjson.dumps([r.as_json() for r in results], option=orjson.OPT_INDENT_2))
    print()
    for line in _summary_lines(results, stale, elapsed):
        print(line)
    print(f"report: {report}")
    if unknown:
        print(f"{len(unknown)} quarantine entries name no case: {', '.join(unknown)}")
    if ns.md:
        text = markdown_summary(results, stale, elapsed)
        if ns.md == "-":
            print(text)
        else:
            Path(ns.md).write_text(text, encoding="utf-8")
            print(f"summary: {ns.md}")
    # a case whose Python side saves a file without a save_path writes to the
    # working directory, the checkout
    left = sorted(_root_entries() - root_before)
    if left:
        print(f"cases left {', '.join(left)} in the repository root {ROOT}: give them a save_path")
    failing = [r for r in results if r.failing and not (r.status == MISSING and ns.allow_missing)]
    return 0 if not failing and not stale and not unknown and not left else 1


def _count(results: list[CaseResult]) -> Counter[str]:
    return Counter(r.status for r in results)


def _counts_text(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{counts[s]} {s}" for s in STATUSES if counts.get(s))


def _summary_lines(results: list[CaseResult], stale: list[str], elapsed: float) -> list[str]:
    lines = [f"{len(results)} cases in {elapsed:.0f} s: {_counts_text(_count(results))}"]
    for tier in (1, 2):
        of_tier = [r for r in results if r.tier == tier]
        if of_tier:
            lines.append(f"  tier {tier}: {len(of_tier)} cases, {_counts_text(_count(of_tier))}")
    rewritten = sum(r.status == PASS and r.r_text for r in results)
    if rewritten:
        lines.append(
            f"{rewritten} of the passes compare with R's text as pytacheck corrects it (r_text)"
        )
    marks: dict[int, Counter[str]] = defaultdict(Counter)
    for r in results:
        if r.kind:
            marks[r.tier][r.kind] += 1
    for tier in sorted(marks):
        kinds = ", ".join(f"{n} {k}" for k, n in sorted(marks[tier].items()))
        lines.append(f"marks, tier {tier}: {kinds}")
    warnings = [r for r in results if r.warning]
    if warnings:
        lines.append(
            f"{len(warnings)} tier-2 marked cases changed since they were locked (warnings): "
            "review them and re-lock"
        )
    quarantined = [r for r in results if r.status == QUARANTINED]
    if quarantined:
        lines.append(f"{len(quarantined)} quarantined (parity/quarantine.yaml; not failures):")
        lines += [f"  {r.key}: {r.problems[0].removeprefix('quarantined: ')}" for r in quarantined]
    failing = [r for r in results if r.failing]
    if failing:
        lines.append(f"{len(failing)} failing:")
        lines += [f"  {r.status:10} {r.key}" for r in failing[:60]]
        if len(failing) > 60:
            lines.append(f"  ... and {len(failing) - 60} more (see the report)")
    if stale:
        lines.append(
            f"{len(stale)} lock entries name no marked case (run `python -m parity lock`): "
            + ", ".join(stale[:10])
            + (" ..." if len(stale) > 10 else "")
        )
    return lines


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:200]


def markdown_summary(results: list[CaseResult], stale: list[str], elapsed: float) -> str:
    """A Markdown summary of a check: statuses by tier, marks by tier, kind and ref,
    and the cases that fail or warn."""
    counts = _count(results)
    shown = [s for s in STATUSES if counts.get(s)]
    out = [
        "# Parity check",
        "",
        f"{len(results)} cases in {elapsed:.0f} s: {_counts_text(counts)}.",
        "",
        "| tier | cases | " + " | ".join(shown) + " |",
        "|---|--:|" + "--:|" * len(shown),
    ]
    for tier in (1, 2):
        of_tier = [r for r in results if r.tier == tier]
        c = _count(of_tier)
        out.append(
            f"| {tier} | {len(of_tier)} | " + " | ".join(str(c.get(s, 0)) for s in shown) + " |"
        )
    groups: dict[tuple[int, str, str], Counter[str]] = defaultdict(Counter)
    for r in results:
        if r.kind:
            groups[(r.tier, r.kind, r.ref or "")][r.status] += 1
    if groups:
        marked_shown = [s for s in STATUSES if any(g.get(s) for g in groups.values())]
        out += [
            "",
            "## Marked cases by tier, kind and ref",
            "",
            "| tier | kind | ref | cases | " + " | ".join(marked_shown) + " |",
            "|---|---|---|--:|" + "--:|" * len(marked_shown),
        ]
        for (tier, kind, ref), c in sorted(
            groups.items(), key=lambda kv: (kv[0][0], kv[0][1], _ref_key(kv[0][2]))
        ):
            out.append(
                f"| {tier} | {kind} | {ref or '-'} | {sum(c.values())} | "
                + " | ".join(str(c.get(s, 0)) for s in marked_shown)
                + " |"
            )
    for title, picked in (
        ("Failing", [r for r in results if r.failing]),
        ("Warnings (tier-2 marked cases that changed)", [r for r in results if r.warning]),
    ):
        if picked:
            out += [
                "",
                f"## {title}",
                "",
                "| case | tier | status | first problem |",
                "|---|---|---|---|",
            ]
            for r in picked[:200]:
                first = _cell(r.problems[0] if r.problems else "")
                out.append(f"| `{r.key}` | {r.tier} | {r.status} | {first} |")
            if len(picked) > 200:
                out.append(f"| ... and {len(picked) - 200} more | | | |")
    if stale:
        out += ["", "## Stale lock entries", ""] + [f"- `{k}`" for k in stale]
    return "\n".join(out) + "\n"


def _ref_key(ref: str) -> tuple[str, int]:
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    return (m.group(1), int(m.group(2))) if m else (ref, 0)


# -- lock -----------------------------------------------------------------------------

#: R and dplyr/vctrs errors that say R's code broke, not that the input is invalid
R_CRASH = re.compile(
    "|".join(
        [
            r"subscript out of bounds",
            r"argument is of length zero",
            r"missing value where TRUE/FALSE needed",
            r"argument is not interpretable as logical",
            r"the condition has length > 1",
            r"object of type '\w+' is not subsettable",
            r"\$ operator is invalid for atomic vectors",
            r"non-numeric argument to (?:binary operator|mathematical function)",
            r"arguments imply differing number of rows",
            r"replacement has \d+ rows?, data has \d+",
            r"number of items to replace is not a multiple",
            r"undefined columns selected",
            r"incorrect number of dimensions",
            r"invalid 'type' \(\w+\) of argument",
            r"invalid subscript type",
            r"no applicable method for",
            r"attempt to (?:select less than one element|apply non-function)",
            r"object '[^']+' not found",
            r"could not find function",
            r"Can't (?:combine|subset|recycle|convert|compute|join|find)",
            r"must be (?:size|a vector|compatible)",
            r"Join columns in `[xy]` must be present",
            r"In argument: ",
            r"Problem while computing",
        ]
    )
)


#: the differences from R shown for each lock change
_SHOWN = 3


@dataclass
class LockChange:
    """A lock entry that ``lock`` adds, changes or removes."""

    key: str
    old: Fingerprint | None
    new: Fingerprint | None
    #: the case's result, when it ran
    result: CaseResult | None = None
    #: a change of an existing entry that was not written (it needs ``--reviewed``)
    held: bool = False

    @property
    def what(self) -> str:
        return "new" if self.old is None else "removed" if self.new is None else "changed"

    def about(self) -> str:
        """The case's tier and mark, why a removed entry goes, and whether it was held."""
        res, parts = self.result, []
        if res is not None:
            parts.append(f"tier {res.tier}")
            if res.kind:
                parts.append(" ".join(x for x in (res.kind, res.ref) if x))
        if self.what == "removed":
            xpass = res is not None and res.status == XPASS
            parts.append("it matches R now" if xpass else "no longer an expected failure")
        if self.held:
            parts.append("not written: needs --reviewed")
        return "; ".join(parts)

    def differences(self) -> list[str]:
        """How Python differs from R where the entry stays, the first few."""
        found = self.result.differences if self.result is not None and self.new else []
        more = [f"... and {len(found) - _SHOWN} more"] if len(found) > _SHOWN else []
        return found[:_SHOWN] + more


def _lock_change_lines(changes: list[LockChange]) -> list[str]:
    lines = []
    for ch in changes:
        lines.append(f"  {ch.what:8} {ch.key} ({ch.about()})")
        lines += [f"      {name:5}{text}" for name, text in entry_diff(ch.old, ch.new)]
        if ch.differences():
            lines.append("      differs from R:")
            lines += [f"        - {d}" for d in ch.differences()]
    return lines


def lock_markdown(changes: list[LockChange]) -> str:
    """A ``lock`` run's changes as a Markdown table, for the pull request."""
    counts = Counter(ch.what for ch in changes)
    held = sum(ch.held for ch in changes)
    out = [
        "# Lock changes",
        "",
        ", ".join(f"{counts[k]} {k}" for k in ("new", "changed", "removed"))
        + (f"; {held} changed entries not written (they need --reviewed)" if held else "")
        + ".",
    ]
    if changes:
        out += [
            "",
            "| case | change | tier and mark | r | py | diff | differs from R |",
            "|---|---|---|---|---|---|---|",
        ]
    for ch in changes:
        fps = dict(entry_diff(ch.old, ch.new))
        out.append(
            f"| `{ch.key}` | {ch.what} | {_cell(ch.about())} | "
            + " | ".join(_cell(fps.get(k, "")) for k in ("r", "py", "diff"))
            + f" | {'<br>'.join(_cell(d) for d in ch.differences())} |"
        )
    return "\n".join(out) + "\n"


def cmd_lock(ns: argparse.Namespace) -> int:
    if not _environment_ok():
        return 2
    started = time.perf_counter()
    cases = _select(ns)
    targets = cases if ns.suggest else [c for c in cases if c.spec.get("known_divergence")]
    results = run_cases(
        targets, ns.jobs, use_lock=False, progress=_Progress(len(targets), False, cases=False)
    )
    by_area: dict[str, list[CaseResult]] = defaultdict(list)
    for res in results:
        by_area[res.area].append(res)
    # every case of the areas, to tell stale entries from unselected ones
    everything = load_cases(ns.area) if _partial(ns) else cases
    expected: dict[str, set[str]] = defaultdict(set)
    for case in everything:
        if expected_to_fail(case.spec):
            expected[case.area].add(case.id)
    selected = {c.key for c in cases}
    areas = set(by_area) | {c.area for c in cases}
    if not _partial(ns):  # lock files of areas without cases
        areas |= set(locked_areas() if ns.area is None else ns.area)
    changes: list[LockChange] = []
    total = 0
    for area in sorted(areas):
        old = read_lock(area)
        new = {
            case_id: fp
            for case_id, fp in old.items()
            if case_id in expected[area] and f"{area}/{case_id}" not in selected
        }
        ran = {res.id: res for res in by_area.get(area, [])}
        for res in ran.values():
            if res.status == UNLOCKED and res.fingerprint is not None:
                new[res.id] = res.fingerprint
            elif res.id in old and res.id in expected[area] and res.status != XPASS:
                new[res.id] = old[res.id]  # not run (skip) or broken (error): keep the entry
        for case_id in sorted(set(old) | set(new)):
            before, after = old.get(case_id), new.get(case_id)
            if before == after:
                continue
            change = LockChange(f"{area}/{case_id}", before, after, ran.get(case_id))
            # a new entry pins a mark added in the same change, and removing one
            # only tightens the check; changing a pinned difference needs a review
            if change.what == "changed" and not ns.reviewed:
                new[case_id], change.held = before, True
            changes.append(change)
        write_lock(area, new)
        total += len(new)
    written = Counter(ch.what for ch in changes if not ch.held)
    counts = ", ".join(f"{written[k]} {k}" for k in ("new", "changed", "removed"))
    print(
        f"locked {total} cases in {sum(lock_path(a).exists() for a in areas)} areas ({counts}) "
        f"in {time.perf_counter() - started:.0f} s"
    )
    for line in _lock_change_lines(changes):
        print(line)
    held = [ch for ch in changes if ch.held]
    if held:
        print(
            f"{len(held)} locked entries changed and were not written: review the "
            "differences above, then run the same command with --reviewed"
        )
    if ns.md:
        text = lock_markdown(changes)
        if ns.md == "-":
            print(text)
        else:
            Path(ns.md).write_text(text, encoding="utf-8")
            print(f"changes: {ns.md}")
    problems = [
        r for r in results if r.status in (XPASS, FAIL, ERROR) and (r.kind or r.status == XPASS)
    ]
    for res in problems:
        print(f"[{res.status.upper():10}] {res.key}")
        print(summarize(res.problems[:5]))
    if ns.suggest:
        _suggest([r for r in results if not r.kind])
    return 1 if problems or held else 0


def _suggest(unmarked: list[CaseResult]) -> None:
    """Propose ``r_bug_fixed`` marks for cases where R crashed and Python returns a value."""
    crashes = [r for r in unmarked if r.r_error is not None and R_CRASH.search(r.r_error)]
    others = [r for r in unmarked if r.failing]
    if not others:
        print("--suggest: no unmarked case differs from R")
        return
    print(
        f"\n--suggest: {len(crashes)} of {len(others)} unmarked failing cases are R crashes where "
        "Python returns a value. Check each value, record the bug as a U-entry in "
        "docs/UPSTREAM_ISSUES.md and add the mark to your lane's parity/divergences file:"
    )
    for r in crashes:
        first = r.r_error.strip().splitlines()[0] if r.r_error and r.r_error.strip() else ""
        reason = f"metacheck fails ({first[:120]}); pytacheck returns <what it returns>"
        print(f'"{r.key}": {{kind: r_bug_fixed, ref: U?, reason: {orjson.dumps(reason).decode()}}}')
    rest = [r for r in others if r not in crashes]
    if rest:
        print(f"not R crashes (fix Python, or mark by hand): {', '.join(r.key for r in rest[:20])}")


def cmd_list(ns: argparse.Namespace) -> int:
    for case in load_cases(ns.area, tier=ns.tier):
        mark = "golden" if case.golden_path.exists() else "MISSING"
        print(f"{case.key:70} tier{case.tier} {mark}")
    return 0


_DATA_DIR: Any = None  # the throwaway data dir, removed at exit
_CACHE_DIR: Any = None  # the throwaway cache dir, removed at exit


def _hermetic_env() -> None:
    """No user/project config, and throwaway data and cache dirs, unless the caller set them.

    As under pytest (``tests/conftest.py``): without ``PYTACHECK_CACHE_DIR``
    the caches (``.metacheck_repo_cache``, the LLM cache, ...) would go to the
    working directory, the checkout, as metacheck's do (the R runner points
    them at a temporary directory too). Each case gets a fresh directory inside
    the cache dir (``_case_cache_dir``).
    """
    global _DATA_DIR, _CACHE_DIR
    import atexit

    os.environ.setdefault("PYTACHECK_CONFIG", "none")
    if not os.environ.get("PYTACHECK_DATA_DIR"):
        _DATA_DIR = tempfile.TemporaryDirectory(prefix="pytacheck-parity-data-")
        atexit.register(_DATA_DIR.cleanup)
        os.environ["PYTACHECK_DATA_DIR"] = _DATA_DIR.name
    if not os.environ.get("PYTACHECK_CACHE_DIR"):
        _CACHE_DIR = tempfile.TemporaryDirectory(prefix="pytacheck-parity-cache-")
        atexit.register(_CACHE_DIR.cleanup)
        os.environ["PYTACHECK_CACHE_DIR"] = _CACHE_DIR.name


_TIER_HELP = "only tier-1 (realistic corpus, parity/corpus.toml) or tier-2 (synthetic) cases"
_JOBS_HELP = "run the cases in N processes (0: one per CPU; default 1)"
_AREA_HELP = (
    "only these areas: one, several separated by commas, or <area>+review for an area "
    "and its <area>_review cases; may be repeated"
)


def _areas(value: str) -> list[str]:
    try:
        return parse_areas(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _add_area(p: argparse.ArgumentParser) -> None:
    p.add_argument("--area", action="extend", type=_areas, metavar="AREAS", help=_AREA_HELP)


def _add_selection(p: argparse.ArgumentParser) -> None:
    _add_area(p)
    p.add_argument("--only", nargs="*", help="case ids or area/id keys")
    p.add_argument("-k", help="substring filter on area/id")
    p.add_argument("--tier", type=int, choices=(1, 2), help=_TIER_HELP)
    p.add_argument("-j", "--jobs", type=int, default=1, metavar="N", help=_JOBS_HELP)


def main(argv: list[str] | None = None) -> int:
    _hermetic_env()
    parser = argparse.ArgumentParser(
        prog="python -m parity",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write goldens by running R")
    _add_area(g)
    g.add_argument("--only", nargs="*")
    g.add_argument("--rscript")
    g.set_defaults(func=cmd_generate)
    c = sub.add_parser("check", help="compare Python with goldens")
    _add_selection(c)
    c.add_argument("-v", "--verbose", action="store_true")
    c.add_argument("--allow-missing", action="store_true")
    c.add_argument(
        "--strict",
        action="store_true",
        help="a tier-2 marked case that changed since it was locked fails instead of warning",
    )
    c.add_argument(
        "--report",
        metavar="PATH",
        help="the per-case JSON report (default: a new parity/_out/report-<time>-<pid>.json)",
    )
    c.add_argument(
        "--md",
        nargs="?",
        const="-",
        metavar="PATH",
        help="also write a Markdown summary (to stdout without PATH)",
    )
    c.set_defaults(func=cmd_check)
    lk = sub.add_parser("lock", help="pin the marked cases' differences (parity/lock/)")
    _add_selection(lk)
    lk.add_argument(
        "--suggest",
        action="store_true",
        help="also run unmarked cases and propose r_bug_fixed marks where R crashed",
    )
    lk.add_argument(
        "--reviewed",
        action="store_true",
        help="also change existing entries (after reviewing the changes a run without it prints)",
    )
    lk.add_argument(
        "--md",
        nargs="?",
        const="-",
        metavar="PATH",
        help="also write the changes as a Markdown table (to stdout without PATH)",
    )
    lk.set_defaults(func=cmd_lock)
    ls = sub.add_parser("list", help="list cases")
    _add_area(ls)
    ls.add_argument("--tier", type=int, choices=(1, 2), help=_TIER_HELP)
    ls.set_defaults(func=cmd_list)
    from parity import accuracy

    accuracy.add_parser(sub)
    ns = parser.parse_args(argv)
    try:
        return int(ns.func(ns))
    except UnknownAreaError as exc:  # a usage error, as argparse reports them
        print(f"python -m parity {ns.cmd}: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
