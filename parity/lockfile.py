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
1e-9), and a change the comparison would see is. What differs from run to run
is written as parity/r/run_cases.R writes it in goldens: the temporary
directory ``<tmp>``, the random names of temporary files and directories in it
``<tempfile>``, and date-time stamps from while the case ran ``<now>``.

``python -m parity lock`` writes the entries; ``python -m parity check`` reports
a marked case whose fingerprints still match as ``xfail``, and otherwise as
``xpass`` (it matches R now: remove the mark), ``r_changed`` (the golden
changed), ``py_changed`` (Python's result or the differing paths changed) or
``unlocked`` (no entry). See docs/PARITY.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from parity.cases import ROOT

LOCK_DIR = ROOT / "parity" / "lock"

#: ``diff`` of a case where R raised an error and Python returned a value
R_ERROR_PY_VALUE = "<R error, Python value>"
#: ``diff`` of a case where R returned a value and Python raised an exception
R_VALUE_PY_ERROR = "<R value, Python error>"
#: ``diff`` of a case whose error messages differ (``compare: {error: contains|exact}``)
ERROR_MESSAGE = "<error message>"

_DIGITS = 10  # significant digits of a double in a digest


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
        return 0.0 if x == 0 else float(f"{x:.{_DIGITS}g}")
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    return x


def digest(value: Any, ran: tuple[float, float] | None = None) -> str:
    """The digest of a canonical value (see the module docstring).

    *ran* is when the value was made (``time.time()`` before and after): its
    date-time stamps from then are ``<now>``.
    """
    text = json.dumps(_rounded(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(_steady(text, ran).encode("ascii")).hexdigest()[:16]


_TMP_ROOTS = sorted(
    {tempfile.gettempdir(), os.path.realpath(tempfile.gettempdir())}, key=len, reverse=True
)
_TMP_PATH = re.compile(
    "(?:" + "|".join(re.escape(r) for r in _TMP_ROOTS) + r")(?![^/\s\"'\\<>])((?:/[^/\s\"'\\<>]+)*)"
)
#: a name tempfile gives (``tmp9npv2h8g``, ``pytacheck-parity-cache-x1_y2z3w``)
_TEMP_NAME = re.compile(r"[A-Za-z_.-]*[a-z0-9_]{8}")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?")


def _steady(text: str, ran: tuple[float, float] | None) -> str:
    """*text* with the temporary directory, temporary names and (with *ran*) the
    stamps of the run written as placeholders."""
    names: set[str] = set()

    def tmp(m: re.Match[str]) -> str:
        for part in m.group(1).split("/")[1:]:
            stem = part.split(".", 1)[0] if not part.startswith(".") else part
            if _TEMP_NAME.fullmatch(stem):
                names.add(stem)
        return "<tmp>" + m.group(1)

    text = _TMP_PATH.sub(tmp, text)
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, "<tempfile>")
    if ran is not None:
        text = _STAMP.sub(lambda m: "<now>" if _during(m, ran) else m.group(0), text)
    return text


def _during(m: re.Match[str], ran: tuple[float, float]) -> bool:
    """Whether the stamp *m* is a time from *ran* (a zone-less stamp is UTC: cases
    run in UTC)."""
    try:
        t = dt.datetime.fromisoformat(m.group(0).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return int(ran[0]) - 1 <= t.timestamp() <= ran[1] + 1


def r_digest(golden: dict[str, Any]) -> str:
    """The ``r`` fingerprint of a golden: its ``ok`` flag and value, not R's error text."""
    return digest({"ok": golden["ok"], "value": golden.get("value")})


def raised(exc: BaseException) -> str:
    """The ``py`` fingerprint of a Python side that raised *exc*."""
    return f"raises:{type(exc).__name__}"


# -- the files ------------------------------------------------------------------------------


def lock_path(area: str) -> Path:
    return LOCK_DIR / f"{area}.json"


_CACHE: dict[str, tuple[int, dict[str, Fingerprint]]] = {}


def read_lock(area: str) -> dict[str, Fingerprint]:
    """The lock entries of *area* (case id -> fingerprint); empty without a file.

    Re-read when the file changes, so a long-lived process (pytest) sees a new lock.
    """
    path = lock_path(area)
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        _CACHE.pop(area, None)
        return {}
    cached = _CACHE.get(area)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: a lock file maps case ids to entries")
    entries = {k: Fingerprint.from_json(f"{path.name}: {k}", v) for k, v in data.items()}
    _CACHE[area] = (mtime, entries)
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
    _CACHE.pop(area, None)
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
