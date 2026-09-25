"""Parity tooling: ``python -m parity <command>``.

Commands
--------
generate  Run the R reference (metacheck) and write golden JSON files.
check     Run the Python port and compare it with the goldens.
list      List cases and whether they have goldens.

Examples::

    python -m parity generate --area text          # needs R + metacheck
    python -m parity check --area text -v
    python -m parity check --only text_search.demo.significant

``generate`` uses the ``Rscript`` on PATH unless ``PYTACHECK_RSCRIPT`` or
``--rscript`` points elsewhere, and always runs R under ``C.UTF-8`` / UTC
so goldens do not depend on the machine's locale.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import orjson

from parity.canonical import canonical, portable
from parity.cases import (
    NEEDS_R_REASON,
    ROOT,
    Case,
    RWithoutReference,
    divergence_kind,
    expected_to_fail,
    iter_case_files,
    load_cases,
    r_text,
    run_python,
    skip_reason,
)
from parity.compare import Options, compare, error_matches, rewrite_r_text, summarize


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
    env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
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


def check_case(case: Case) -> tuple[str, list[str], float]:
    """Return ``(status, problems, seconds)``; status is pass/fail/missing/xfail/error/skip.

    ``skip`` is a case whose Python side runs R when no reference R is
    configured (see ``parity.cases.run_python``). A ``known_divergence`` with
    ``r_text`` compares Python with R's golden as rewritten by its
    substitutions; a difference left over is a failure unless the mark also
    says ``xfail: true``.
    """
    if not case.golden_path.exists():
        return "missing", ["no golden file; run `python -m parity generate`"], 0.0
    reason = skip_reason(case)
    if reason:
        return "skip", [reason], 0.0
    golden = orjson.loads(case.golden_path.read_bytes())
    subs = r_text(case.spec)
    if not subs:
        return _check(case, golden)
    used = [False] * len(subs)
    key = "value" if golden["ok"] else "error"
    rewritten = {**golden, key: rewrite_r_text(golden[key], subs, used)}
    stale = [
        f"r_text {sub} changes nothing in R's golden: remove it from the mark"
        for sub, u in zip(subs, used, strict=True)
        if not u
    ]
    status, problems, elapsed = _check(case, rewritten, raw=golden)
    if stale and status != "skip":
        return "fail", stale + problems, elapsed
    return status, problems, elapsed


def _check(
    case: Case, golden: dict[str, Any], raw: dict[str, Any] | None = None
) -> tuple[str, list[str], float]:
    """Run the Python side and compare it with *golden*.

    *raw* is R's golden before its ``r_text`` substitutions: a case that
    matches it too does not differ from R where it compares, so its mark is
    stale (a mark never goes on a case that passes).
    """
    options = Options.from_case(case.spec.get("compare"))
    start = time.perf_counter()
    try:
        result = run_python(case)
        err = None
    except RWithoutReference:
        return "skip", [NEEDS_R_REASON], time.perf_counter() - start
    except Exception as exc:
        result = None
        err = exc
    elapsed = time.perf_counter() - start
    expected = expected_to_fail(case.spec)
    failed = "xfail" if expected else "fail"
    if not golden["ok"]:
        if err is None:
            return (
                failed,
                [f"R raised an error ({golden['error']}) but Python returned a value"],
                elapsed,
            )
        message = portable(str(err))
        if error_matches(golden["error"], message, options.error):
            if raw is not None and error_matches(raw["error"], message, options.error):
                return "fail", [_UNNEEDED_R_TEXT], elapsed
            return "pass", [], elapsed
        return (
            failed,
            [f"error: R={golden['error']!r} py={type(err).__name__}: {message!r}"],
            elapsed,
        )
    if err is not None:
        tb = "".join(traceback.format_exception_only(type(err), err)).strip()
        status = "xfail" if expected else "error"
        return status, [f"Python raised {tb}"], elapsed
    py = canonical(result)
    problems = compare(golden["value"], py, options)
    if problems:
        return failed, problems, elapsed
    if raw is not None and not compare(raw["value"], py, options):
        return "fail", [_UNNEEDED_R_TEXT], elapsed
    return "pass", [], elapsed


_UNNEEDED_R_TEXT = (
    "the case matches R's golden without its r_text substitutions (they change only "
    "text the comparison skips): remove the mark"
)


def _root_entries() -> set[str]:
    return {p.name for p in ROOT.iterdir()}


def cmd_check(ns: argparse.Namespace) -> int:
    root_before = _root_entries()
    cases = load_cases(ns.area)
    if ns.only:
        cases = [c for c in cases if c.id in ns.only or c.key in ns.only]
    if ns.k:
        cases = [c for c in cases if ns.k in c.key]
    counts: dict[str, int] = {}
    kinds: dict[str, int] = {}
    rewritten = 0  # passes compared with R's text as pytacheck corrects it (r_text)
    report: list[dict[str, Any]] = []
    for case in cases:
        status, problems, secs = check_case(case)
        counts[status] = counts.get(status, 0) + 1
        kind = divergence_kind(case.spec) if status == "xfail" else None
        if kind:
            kinds[kind] = kinds.get(kind, 0) + 1
        with_r_text = bool(r_text(case.spec))
        rewritten += status == "pass" and with_r_text
        report.append(
            {
                "case": case.key,
                "status": status,
                "kind": kind,
                "r_text": with_r_text,
                "problems": problems,
                "seconds": secs,
            }
        )
        if status not in ("pass",) or ns.verbose:
            print(f"[{status.upper():7}] {case.key} ({secs * 1000:.0f} ms)")
            if problems and (ns.verbose or status in ("fail", "error")):
                print(summarize(problems))
    out = ROOT / "parity" / "_out"
    out.mkdir(exist_ok=True)
    (out / "report.json").write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2))
    total = sum(counts.values())
    print(f"\n{total} cases: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    if rewritten:
        print(f"{rewritten} of the passes compare with R's text as pytacheck corrects it (r_text)")
    if kinds:
        print("xfail by kind: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())))
    # a case whose Python side saves a file without a save_path writes to the
    # working directory, the checkout
    left = sorted(_root_entries() - root_before)
    if left:
        print(f"cases left {', '.join(left)} in the repository root {ROOT}: give them a save_path")
    return (
        0
        if counts.get("fail", 0) == counts.get("error", 0) == 0
        and (ns.allow_missing or counts.get("missing", 0) == 0)
        and not left
        else 1
    )


def cmd_list(ns: argparse.Namespace) -> int:
    for case in load_cases(ns.area):
        mark = "golden" if case.golden_path.exists() else "MISSING"
        print(f"{case.key:70} {mark}")
    return 0


_DATA_DIR: Any = None  # the throwaway data dir, removed at exit
_CACHE_DIR: Any = None  # the throwaway cache dir, removed at exit


def _hermetic_env() -> None:
    """No user/project config, and throwaway data and cache dirs, unless the caller set them.

    As under pytest (``tests/conftest.py``): without ``PYTACHECK_CACHE_DIR``
    the caches (``.metacheck_repo_cache``, the LLM cache, ...) would go to the
    working directory, the checkout, as metacheck's do (the R runner points
    them at a temporary directory too).
    """
    global _DATA_DIR, _CACHE_DIR
    import atexit
    import tempfile

    os.environ.setdefault("PYTACHECK_CONFIG", "none")
    if not os.environ.get("PYTACHECK_DATA_DIR"):
        _DATA_DIR = tempfile.TemporaryDirectory(prefix="pytacheck-parity-data-")
        atexit.register(_DATA_DIR.cleanup)
        os.environ["PYTACHECK_DATA_DIR"] = _DATA_DIR.name
    if not os.environ.get("PYTACHECK_CACHE_DIR"):
        _CACHE_DIR = tempfile.TemporaryDirectory(prefix="pytacheck-parity-cache-")
        atexit.register(_CACHE_DIR.cleanup)
        os.environ["PYTACHECK_CACHE_DIR"] = _CACHE_DIR.name


def main(argv: list[str] | None = None) -> int:
    _hermetic_env()
    parser = argparse.ArgumentParser(
        prog="python -m parity",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write goldens by running R")
    g.add_argument("--area")
    g.add_argument("--only", nargs="*")
    g.add_argument("--rscript")
    g.set_defaults(func=cmd_generate)
    c = sub.add_parser("check", help="compare Python with goldens")
    c.add_argument("--area")
    c.add_argument("--only", nargs="*")
    c.add_argument("-k", help="substring filter on area/id")
    c.add_argument("-v", "--verbose", action="store_true")
    c.add_argument("--allow-missing", action="store_true")
    c.set_defaults(func=cmd_check)
    ls = sub.add_parser("list", help="list cases")
    ls.add_argument("--area")
    ls.set_defaults(func=cmd_list)
    ns = parser.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
