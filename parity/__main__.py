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
    iter_case_files,
    load_cases,
    run_python,
    skip_reason,
)
from parity.compare import Options, compare, error_matches, summarize


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
    configured (see ``parity.cases.run_python``).
    """
    if not case.golden_path.exists():
        return "missing", ["no golden file; run `python -m parity generate`"], 0.0
    reason = skip_reason(case)
    if reason:
        return "skip", [reason], 0.0
    golden = orjson.loads(case.golden_path.read_bytes())
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
    failed = "xfail" if case.spec.get("known_divergence") else "fail"
    if not golden["ok"]:
        if err is None:
            return (
                failed,
                [f"R raised an error ({golden['error']}) but Python returned a value"],
                elapsed,
            )
        message = portable(str(err))
        if error_matches(golden["error"], message, options.error):
            return "pass", [], elapsed
        return (
            failed,
            [f"error: R={golden['error']!r} py={type(err).__name__}: {message!r}"],
            elapsed,
        )
    if err is not None:
        tb = "".join(traceback.format_exception_only(type(err), err)).strip()
        status = "xfail" if case.spec.get("known_divergence") else "error"
        return status, [f"Python raised {tb}"], elapsed
    problems = compare(golden["value"], canonical(result), options)
    if problems:
        return failed, problems, elapsed
    return "pass", [], elapsed


def cmd_check(ns: argparse.Namespace) -> int:
    cases = load_cases(ns.area)
    if ns.only:
        cases = [c for c in cases if c.id in ns.only or c.key in ns.only]
    if ns.k:
        cases = [c for c in cases if ns.k in c.key]
    counts: dict[str, int] = {}
    kinds: dict[str, int] = {}
    report: list[dict[str, Any]] = []
    for case in cases:
        status, problems, secs = check_case(case)
        counts[status] = counts.get(status, 0) + 1
        kind = divergence_kind(case.spec) if status == "xfail" else None
        if kind:
            kinds[kind] = kinds.get(kind, 0) + 1
        report.append(
            {
                "case": case.key,
                "status": status,
                "kind": kind,
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
    if kinds:
        print("xfail by kind: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())))
    return (
        0
        if counts.get("fail", 0) == counts.get("error", 0) == 0
        and (ns.allow_missing or counts.get("missing", 0) == 0)
        else 1
    )


def cmd_list(ns: argparse.Namespace) -> int:
    for case in load_cases(ns.area):
        mark = "golden" if case.golden_path.exists() else "MISSING"
        print(f"{case.key:70} {mark}")
    return 0


_DATA_DIR: Any = None  # the throwaway data dir, removed at exit


def _hermetic_env() -> None:
    """No user/project config and a throwaway data dir, unless the caller set them."""
    global _DATA_DIR
    os.environ.setdefault("PYTACHECK_CONFIG", "none")
    if not os.environ.get("PYTACHECK_DATA_DIR"):
        import atexit
        import tempfile

        _DATA_DIR = tempfile.TemporaryDirectory(prefix="pytacheck-parity-data-")
        atexit.register(_DATA_DIR.cleanup)
        os.environ["PYTACHECK_DATA_DIR"] = _DATA_DIR.name


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
