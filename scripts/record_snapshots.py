"""Record, check and compare the raw snapshots in tests/snapshots/ (package SNAP).

    uv run --python 3.12 --all-extras python scripts/record_snapshots.py   # record every set
    uv run --python 3.12 --all-extras python scripts/record_snapshots.py --only modules
    uv run python scripts/record_snapshots.py --check    # record twice and compare the bytes
    uv run python scripts/record_snapshots.py --diff     # compare a new recording with git's
    uv run python scripts/record_snapshots.py --list     # the sets and their case counts

The sets and their cases are defined in tests/snapshots/inputs.py, the format in
tests/snapshots/store.py (tests/snapshots/README.md explains both).

Recording writes into tests/snapshots/ only under Python 3.12 on Linux, the
version the committed snapshots are recorded with (error messages of Python
itself differ between versions), and with every extra installed, as CI installs
them (without xlrd, data_check reads no .xls file). ``--out DIR`` records into
another folder with any version.

``--check`` records twice, in two processes with different ``PYTHONHASHSEED``
values, and fails unless both give the same bytes. Under Python 3.12 it also
fails when the recording differs from the committed snapshots.

``--diff`` lists the cases added, removed and changed against the committed
snapshots, with the paths that differ and a diff of each changed record.
``--from DIR`` compares a recording made earlier (``--out DIR``) instead of
recording again: the sets it holds, or those of ``--only``.

``--cases GLOB`` limits every mode to the matching case ids; it cannot write
into tests/snapshots/, since a partial set would drop the other cases.
"""

from __future__ import annotations

import argparse
import difflib
import fnmatch
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: the environment as the caller gave it, before anything below changes it
_CALLER_ENV = dict(os.environ)
sys.path.insert(0, str(ROOT))

from tests.snapshots import inputs, oracle, store

#: the Python the committed snapshots are recorded with, on Linux
PYTHON = (3, 12)
RECORDED_WITH = "Python 3.12 on linux"
RECORD_COMMAND = "uv run --python 3.12 --all-extras python scripts/record_snapshots.py"
#: the two hash seeds of --check
SEEDS = ("12345", "777")
SCRIPT = Path(__file__).resolve()
_TEMP_DIRS: list[tempfile.TemporaryDirectory[str]] = []

Recording = dict[str, dict[str, store.Record]]


def _hermetic_env() -> None:
    """No user or project config, and throwaway data and cache folders (as parity's)."""
    os.environ.setdefault("PYTACHECK_CONFIG", "none")
    for name in ("PYTACHECK_DATA_DIR", "PYTACHECK_CACHE_DIR"):
        if not os.environ.get(name):
            folder = tempfile.TemporaryDirectory(prefix="snapshots-")
            _TEMP_DIRS.append(folder)
            os.environ[name] = folder.name


def _python() -> str:
    return f"Python {'.'.join(map(str, sys.version_info[:2]))} on {sys.platform}"


def _on_recording_platform() -> bool:
    """Python 3.12 on Linux, as the committed snapshots are recorded."""
    return sys.version_info[:2] == PYTHON and sys.platform == "linux"


def _missing_extras() -> list[str]:
    """pytacheck's optional dependencies that are not installed."""
    from importlib import metadata

    missing: set[str] = set()
    from pytacheck._version import DISTRIBUTION

    requirements: list[str] = []
    for dist in (DISTRIBUTION, "pytacheck"):
        try:
            requirements = metadata.requires(dist) or []
            break
        except metadata.PackageNotFoundError:
            continue
    for requirement in requirements:
        if "extra ==" not in requirement:
            continue
        name = re.split(r"[\s\[<>=!~;]", requirement, maxsplit=1)[0]
        try:
            metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.add(name)
    return sorted(missing)


def select(only: list[str] | None) -> list[inputs.SnapshotSet]:
    """The sets named in *only* (a name, or a prefix such as ``suites``), or all."""
    if not only:
        return list(inputs.SETS.values())
    unknown = [o for o in only if not any(_in(name, o) for name in inputs.SETS)]
    if unknown:
        raise SystemExit(f"no snapshot set {', '.join(unknown)} (sets: {', '.join(inputs.SETS)})")
    return [s for name, s in inputs.SETS.items() if any(_in(name, o) for o in only)]


def _in(name: str, selector: str) -> bool:
    return name == selector or name.startswith(selector.rstrip("/") + "/")


def _wanted(case_id: str, patterns: list[str] | None) -> bool:
    return not patterns or any(fnmatch.fnmatchcase(case_id, p) for p in patterns)


# -- recording ----------------------------------------------------------------------------


def record(
    sets: list[inputs.SnapshotSet], patterns: list[str] | None = None
) -> tuple[Recording, dict[str, dict[str, str]]]:
    """Record *sets* on this tree: ``{set: {case id: record}}`` and ``{set: {case id: group}}``."""
    recorded: Recording = {}
    groups: dict[str, dict[str, str]] = {}
    for s in sets:
        cases = [c for c in s.cases() if _wanted(c.id, patterns)]
        started = time.perf_counter()
        recorded[s.name] = {c.id: inputs.record(c) for c in cases}
        groups[s.name] = {c.id: c.group for c in cases}
        errors = sum(not r["ok"] for r in recorded[s.name].values())
        seconds = time.perf_counter() - started
        print(f"{s.name}: {len(cases)} cases ({errors} raise), {seconds:.1f} s", flush=True)
        for line in s.notes(recorded[s.name]) if s.notes else []:
            print(f"  {line}")
    return recorded, groups


def problems(recorded: Recording) -> list[str]:
    """The cases that used the network or R: they cannot be recorded."""
    return [
        f"{name}: {case_id}: {r['problem']}"
        for name, records in recorded.items()
        for case_id, r in records.items()
        if "problem" in r
    ]


def cmd_record(args: argparse.Namespace) -> int:
    target = Path(args.out).resolve() if args.out else store.SNAPSHOT_DIR
    committed = target == store.SNAPSHOT_DIR
    if committed and args.cases:
        raise SystemExit("--cases records part of a set: use --out DIR, --check or --diff")
    if committed and not _on_recording_platform() and not args.any_python:
        raise SystemExit(
            f"the snapshots are recorded with {RECORDED_WITH}, this is {_python()}: run "
            f"`{RECORD_COMMAND}` (tests/snapshots/README.md)"
        )
    missing = _missing_extras() if committed else []
    if missing:
        raise SystemExit(
            f"the snapshots are recorded with every extra installed, as CI installs them; "
            f"{', '.join(missing)} missing: run `{RECORD_COMMAND}` (tests/snapshots/README.md)"
        )
    sets = select(args.only)
    recorded, groups = record(sets, args.cases)
    found = problems(recorded)
    if found:
        print("not written: these cases used the network or R", *found, sep="\n  ")
        return 1
    for s in sets:
        folder = store.write_set(target, s.name, s.layout, recorded[s.name], groups[s.name])
        size = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
        print(f"wrote {folder.relative_to(target.parent)}: {size:,} bytes")
    return 0


# -- --check ------------------------------------------------------------------------------


def _files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def _record_elsewhere(out: Path, seed: str, args: argparse.Namespace) -> subprocess.Popen[str]:
    cmd = [sys.executable, str(SCRIPT), "--out", str(out)]
    if args.only:
        cmd += ["--only", *args.only]
    if args.cases:
        cmd += ["--cases", *args.cases]
    env = {**_CALLER_ENV, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.Popen(
        cmd, env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def cmd_check(args: argparse.Namespace) -> int:
    sets = select(args.only)
    with tempfile.TemporaryDirectory(prefix="snapshots-check-") as tmp:
        outs = [Path(tmp) / f"seed-{seed}" for seed in SEEDS]
        print(
            f"recording {', '.join(s.name for s in sets)} twice (PYTHONHASHSEED {' and '.join(SEEDS)})"
        )
        procs = [_record_elsewhere(out, seed, args) for out, seed in zip(outs, SEEDS, strict=True)]
        logs = [p.communicate()[0] for p in procs]
        for seed, p, log in zip(SEEDS, procs, logs, strict=True):
            if p.returncode:
                print(f"the recording with PYTHONHASHSEED={seed} failed:\n{log}")
                return 1
        print(logs[0], end="")
        first, second = _files(outs[0]), _files(outs[1])
        differ = sorted(k for k in first.keys() | second.keys() if first.get(k) != second.get(k))
        if differ:
            print("not deterministic: the two recordings differ in", *differ, sep="\n  ")
            for s in sets:
                a, b = store.read_index(outs[0], s.name), store.read_index(outs[1], s.name)
                for case_id in sorted(i for i in a.keys() | b.keys() if a.get(i) != b.get(i)):
                    print(f"  {s.name}: {case_id}")
            return 1
        cases = sum(len(store.read_index(outs[0], s.name)) for s in sets)
        print(f"deterministic: {len(first)} files and {cases} cases byte-identical")
        return _against_committed(sets, outs[0], args.cases)


def _against_committed(
    sets: list[inputs.SnapshotSet], recorded: Path, patterns: list[str] | None
) -> int:
    present = [s for s in sets if s.name in store.committed_sets(store.SNAPSHOT_DIR)]
    if not present:
        print("no committed snapshots of these sets to compare with")
        return 0
    if not _on_recording_platform():
        print(
            f"not compared with the committed snapshots: they are recorded with {RECORDED_WITH}, "
            f"this is {_python()}"
        )
        return 0
    status = 0
    for s in present:
        mine = store.read_index(recorded, s.name)
        theirs = {
            i: d
            for i, d in store.read_index(store.SNAPSHOT_DIR, s.name).items()
            if _wanted(i, patterns)
        }
        changed = sorted(i for i in mine.keys() | theirs.keys() if mine.get(i) != theirs.get(i))
        if changed:
            status = 1
            print(
                f"{s.name}: {len(changed)} cases differ from the committed snapshots, e.g. {changed[0]}"
            )
        else:
            print(f"{s.name}: equal to the committed snapshots")
    if status:
        print("run scripts/record_snapshots.py --diff to see the differences")
    return status


# -- --diff ---------------------------------------------------------------------------------


def _unified(a: store.Record, b: store.Record, max_lines: int) -> list[str]:
    lines = list(
        difflib.unified_diff(
            store.pretty(a).splitlines(),
            store.pretty(b).splitlines(),
            "snapshot",
            "recorded",
            lineterm="",
        )
    )
    if len(lines) > max_lines:
        lines = [*lines[:max_lines], f"... {len(lines) - max_lines} more diff lines"]
    return lines


def cmd_diff(args: argparse.Namespace) -> int:
    sets = select(args.only)
    if not _on_recording_platform():
        print(
            f"note: the committed snapshots are recorded with {RECORDED_WITH}, this is {_python()}"
        )
    if args.source:
        source = Path(args.source)
        absent = [s.name for s in sets if not (source / s.name / store.INDEX).is_file()]
        if absent and (args.only or len(absent) == len(sets)):  # else: the sets it holds
            raise SystemExit(f"{source} holds no recording of {', '.join(absent)}")
        sets = [s for s in sets if s.name not in absent]
        recorded = {s.name: store.read_set(source, s.name) for s in sets}
    else:
        recorded, _groups = record(sets, args.cases)
    committed = store.committed_sets(store.SNAPSHOT_DIR)
    status = 0
    for s in sets:
        mine = {i: r for i, r in recorded[s.name].items() if _wanted(i, args.cases)}
        theirs: dict[str, store.Record] = {}
        if s.name in committed:
            theirs = {
                i: r
                for i, r in store.read_set(store.SNAPSHOT_DIR, s.name).items()
                if _wanted(i, args.cases)
            }
        added = sorted(mine.keys() - theirs.keys())
        removed = sorted(theirs.keys() - mine.keys())
        changed = sorted(
            i for i in mine.keys() & theirs.keys() if store.dumps(mine[i]) != store.dumps(theirs[i])
        )
        print(
            f"{s.name}: {len(mine)} cases, {len(added)} added, {len(removed)} removed, "
            f"{len(changed)} changed" + ("" if s.name in committed else " (not committed yet)")
        )
        status |= bool(added or removed or changed)
        for case_id in added:
            print(f"+ {case_id}")
        for case_id in removed:
            print(f"- {case_id}")
        for case_id in changed:
            print(f"~ {case_id}")
            for d in oracle.differences(theirs[case_id], mine[case_id]):
                print(f"    {d}")
            for line in _unified(theirs[case_id], mine[case_id], args.max_lines):
                print(f"    {line}")
    return int(status)


# -- --list ---------------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    committed = store.committed_sets(store.SNAPSHOT_DIR)
    for s in select(args.only):
        n = sum(_wanted(c.id, args.cases) for c in s.cases())
        where = "committed" if s.name in committed else "not recorded"
        print(f"{s.name:<24} {s.layout:<5} {n:>6} cases  {where}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record, check and compare the snapshots in tests/snapshots/.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="record twice and compare the bytes")
    mode.add_argument("--diff", action="store_true", help="compare with the committed snapshots")
    mode.add_argument("--list", action="store_true", help="list the sets")
    parser.add_argument("--only", nargs="+", metavar="SET", help="only these sets (or prefixes)")
    parser.add_argument("--cases", nargs="+", metavar="GLOB", help="only the matching case ids")
    parser.add_argument("--out", metavar="DIR", help="record into DIR, not tests/snapshots/")
    parser.add_argument(
        "--from", dest="source", metavar="DIR", help="--diff: compare DIR's recording"
    )
    parser.add_argument(
        "--max-lines", type=int, default=80, metavar="N", help="--diff: diff lines per case"
    )
    parser.add_argument(
        "--any-python", action="store_true", help="write tests/snapshots/ with another Python"
    )
    args = parser.parse_args(argv)
    if args.source and not args.diff:
        parser.error("--from goes with --diff")
    if args.out and (args.check or args.diff or args.list):
        parser.error("--out goes with recording only")
    _hermetic_env()
    if args.check:
        return cmd_check(args)
    if args.diff:
        return cmd_diff(args)
    if args.list:
        return cmd_list(args)
    return cmd_record(args)


if __name__ == "__main__":
    sys.exit(main())
