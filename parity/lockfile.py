"""The divergence lock: ``parity/lock/<area>.json``.

A case marked ``known_divergence`` is an expected failure, but not an unchecked
one. The lock pins what R returned, what Python returned and where the two
differ, so a marked case still fails when either side changes. Each area's
file maps case ids to three fingerprints, one sorted line per case, so lanes
locking different cases merge cleanly::

    {
    "json_expand.null": {"r": "3f1c0b7e9a2d4c55", "py": "9a0b...", "diff": ["table.x[]"]},
    "read.bad": {"r": "0d4e...", "py": "raises:ValueError", "diff": ["<R value, Python error>"]}
    }

``r``
    a digest of R's golden: its ``ok`` flag and its value (the text of an R
    error is not part of it);
``py``
    a digest of Python's result as the comparison sees it (``parity.compare
    .comparable``: ignored elements dropped, a report as its prose, the rows of
    unordered data frames sorted), or ``raises:<ExceptionType>``;
``diff``
    the paths where they differ, element indices written ``[]``
    (``parity.compare.compare_paths``), or one of the ``<...>`` outcomes below.

A digest is the first 16 hex digits of the SHA-256 of the value's JSON, keys
sorted, with every double rounded to 10 significant digits: a last-digit
difference between platforms is not a change (the comparison allows a relative
1e-9), and a change the comparison would see is. What differs from one run of a
case to the next (:class:`Run`) is written as parity/r/run_cases.R writes it in
goldens: the random names ``tempfile`` gave while the case ran ``<tempfile>``,
the temporary directory ``<tmp>``, the run's data and cache directories
``<data>`` and ``<cache>``, and date-time stamps from while the case ran
``<now>``.

``python -m parity lock`` writes the entries; ``python -m parity check`` reports
a marked case whose fingerprints still match as ``xfail``, and otherwise as
``xpass`` (it matches R now: remove the mark), ``r_changed`` (the golden
changed), ``py_changed`` (Python's result or the differing paths changed) or
``unlocked`` (no entry). ``r_changed`` and ``py_changed`` fail a tier-1 case;
for a tier-2 case they are warnings (:class:`LockWarning` under pytest), unless
Python now raises where it returned a value or ``check --strict`` runs. See
docs/PARITY.md.

A digest alone says nothing a reviewer can check, so ``lock`` prints each
entry it adds, changes or removes (:func:`entry_diff`, with how Python differs
from R), and changes an existing entry only with ``--reviewed``.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import re
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from parity.cases import ROOT

LOCK_DIR = ROOT / "parity" / "lock"

#: ``diff`` of a case where R raised an error and Python returned a value
R_ERROR_PY_VALUE = "<R error, Python value>"
#: ``diff`` of a case where R returned a value and Python raised an exception
R_VALUE_PY_ERROR = "<R value, Python error>"

_DIGITS = 10  # significant digits of a double in a digest
#: a double this close to 0 digests as 0, as compare.py's absolute tolerance treats it
#: (round-off like a skewness of -4e-16 depends on the platform's long double)
_ZERO = 1e-12


class LockWarning(UserWarning):
    """A tier-2 marked case whose golden or Python result changed since it was
    locked (``r_changed`` / ``py_changed`` that only warn): review and re-lock it."""


@dataclass(frozen=True)
class Fingerprint:
    """A marked case's lock entry."""

    r: str
    py: str
    diff: tuple[str, ...]

    @property
    def raises(self) -> bool:
        """Whether Python raised an exception (``py`` is ``raises:<ExceptionType>``)."""
        return self.py.startswith("raises:")

    def as_json(self) -> dict[str, Any]:
        return {"r": self.r, "py": self.py, "diff": list(self.diff)}

    @classmethod
    def from_json(cls, where: str, x: Any) -> Fingerprint:
        ok = (
            isinstance(x, dict)
            and set(x) == {"r", "py", "diff"}
            and isinstance(x["r"], str)
            and isinstance(x["py"], str)
            and isinstance(x["diff"], list)
            and all(isinstance(p, str) for p in x["diff"])
        )
        if not ok:
            raise ValueError(f"{where}: a lock entry is {{r, py, diff}}, not {x!r}")
        return cls(x["r"], x["py"], tuple(x["diff"]))


def _rounded(x: Any) -> Any:
    if isinstance(x, float):
        return 0.0 if abs(x) < _ZERO else float(f"{x:.{_DIGITS}g}")
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    return x


@dataclass
class Run:
    """What differs from one run of a case to the next (see :func:`watch_run`)."""

    #: ``time.time()`` when the case started and finished
    started: float
    finished: float = math.inf
    #: the random names ``tempfile`` gave meanwhile (``tmp9npv2h8g`` -> ``9npv2h8g``)
    names: set[str] = field(default_factory=set)
    #: the run's own directories (``PYTACHECK_DATA_DIR``, ``PYTACHECK_CACHE_DIR``)
    dirs: dict[str, str] = field(default_factory=dict)


@contextlib.contextmanager
def watch_run() -> Iterator[Run]:
    """Record what makes this run of a case differ from the next: when it ran,
    every random name ``tempfile`` gives (in any thread), and the data and cache
    directories it runs with."""
    run = Run(time.time())
    for var, placeholder in (("PYTACHECK_DATA_DIR", "<data>"), ("PYTACHECK_CACHE_DIR", "<cache>")):
        folder = os.environ.get(var)
        if folder:
            run.dirs.update(dict.fromkeys({folder, os.path.realpath(folder)}, placeholder))
    # every name tempfile makes up comes from this iterator class
    names: Any = getattr(tempfile, "_RandomNameSequence")  # noqa: B009 -- private
    saved = names.__next__

    def recorded(self: Any) -> str:
        name = str(saved(self))
        run.names.add(name)
        return name

    names.__next__ = recorded
    try:
        yield run
    finally:
        names.__next__ = saved
        run.finished = time.time()


def digest(value: Any, run: Run | None = None) -> str:
    """The digest of a canonical value (see the module docstring); *run* is the
    run of the case that made it."""
    text = json.dumps(_rounded(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    if run is not None:
        text = steady(text, run)
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


_TMP_ROOTS = {tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


def _dir(path: str) -> re.Pattern[str]:
    """*path* where it starts a path: not inside a longer one
    (``/opt/grobid/grobid-home/tmp/x`` is not in ``/tmp``)."""
    return re.compile(r"(?<![\w.~-])" + re.escape(path) + r"(?![\w.~-])")


def steady(text: str, run: Run) -> str:
    """*text* with what differs from run to run written as placeholders."""
    dirs = {**run.dirs, **dict.fromkeys(_TMP_ROOTS, "<tmp>")}
    if os.sep != "/":  # pytacheck writes paths with "/", as R does
        dirs.update({k.replace(os.sep, "/"): v for k, v in list(dirs.items())})
    # the longest first: the cache directory is in the temporary directory
    for folder in sorted(dirs, key=len, reverse=True):
        text = _dir(folder).sub(dirs[folder], text)
    for name in sorted(run.names, key=len, reverse=True):
        text = text.replace(name, "<tempfile>")
    return _STAMP.sub(lambda m: "<now>" if _during(m, run) else m.group(0), text)


def _during(m: re.Match[str], run: Run) -> bool:
    """Whether the stamp *m* is a time from *run* (a zone-less stamp is UTC: cases
    run in UTC)."""
    try:
        t = dt.datetime.fromisoformat(m.group(0).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return int(run.started) - 1 <= t.timestamp() <= run.finished + 1


def r_digest(golden: dict[str, Any]) -> str:
    """The ``r`` fingerprint of a golden: its ``ok`` flag and value, not R's error text."""
    return digest({"ok": golden["ok"], "value": golden.get("value")})


def raised(exc: BaseException) -> str:
    """The ``py`` fingerprint of a Python side that raised *exc*."""
    return f"raises:{type(exc).__name__}"


def entry_diff(old: Fingerprint | None, new: Fingerprint | None) -> list[tuple[str, str]]:
    """What a lock changes in one entry, as ``(fingerprint, text)`` pairs for ``r``,
    ``py`` and ``diff``: a changed digest as ``old -> new``, the differing paths
    that came (``+path``) and went (``-path``). A new or removed entry is shown
    as it is."""
    if old is None or new is None:
        fp = new if new is not None else old
        if fp is None:
            return []
        return [("r", fp.r), ("py", fp.py), ("diff", ", ".join(fp.diff))]
    out = [
        (name, f"{a} -> {b}" if a != b else f"{a} (same)")
        for name, a, b in (("r", old.r, new.r), ("py", old.py, new.py))
    ]
    if old.diff == new.diff:
        return [*out, ("diff", f"{', '.join(new.diff)} (same)")]
    moved = [f"+{p}" for p in new.diff if p not in old.diff]
    moved += [f"-{p}" for p in old.diff if p not in new.diff]
    return [*out, ("diff", ", ".join(moved))]


# -- the files ------------------------------------------------------------------------------


def lock_path(area: str) -> Path:
    return LOCK_DIR / f"{area}.json"


_CACHE: dict[Path, tuple[int, dict[str, Fingerprint]]] = {}


def read_lock(area: str) -> dict[str, Fingerprint]:
    """The lock entries of *area* (case id -> fingerprint); empty without a file.

    Re-read when the file changes, so a long-lived process (pytest) sees a new lock.
    """
    path = lock_path(area)
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        _CACHE.pop(path, None)
        return {}
    cached = _CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: a lock file maps case ids to entries")
    entries = {k: Fingerprint.from_json(f"{path.name}: {k}", v) for k, v in data.items()}
    _CACHE[path] = (mtime, entries)
    return entries


def format_lock(entries: dict[str, Fingerprint]) -> str:
    """A lock file's text: one line per case, sorted by case id."""
    lines = [
        json.dumps(k, ensure_ascii=False)
        + ": "
        + json.dumps(entries[k].as_json(), ensure_ascii=False, separators=(", ", ": "))
        for k in sorted(entries)
    ]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def write_lock(area: str, entries: dict[str, Fingerprint]) -> None:
    """Write *area*'s lock (remove the file when there is nothing to lock)."""
    path = lock_path(area)
    _CACHE.pop(path, None)
    if not entries:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = format_lock(entries)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


def locked_areas() -> list[str]:
    """The areas with a lock file."""
    return sorted(p.stem for p in LOCK_DIR.glob("*.json"))
