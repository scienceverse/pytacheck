"""docs/UPSTREAM_ISSUES.md agrees with itself and with the parity marks that cite it.

* Every D- and U-entry has a unique ID; entries of the right-sizing lanes sit in their
  lane's section, within its reserved IDs, and no other entry uses those IDs.
* Every U-entry has a status (fixed / partly fixed / kept / n/a / open) that matches
  its notes (**Fixed in pytacheck**, **Partly fixed**, **Kept**, **Not applicable**).
* Every ``ref`` of a ``known_divergence`` (in ``parity/divergences/*.yaml`` or inline in
  ``parity/cases/*.yaml``) names an existing entry, and a mark that says pytacheck fixes
  the bug cites a U-entry whose status is fixed or partly fixed.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "UPSTREAM_ISSUES.md"

STATUSES = ("fixed", "partly fixed", "kept", "n/a", "open")
#: mark kinds that say pytacheck differs from R because it fixes the entry
FIXING_KINDS = {"r_bug_fixed", "better_logic", "deliberate"}
FIXED = ("fixed", "partly fixed")

_ROW = re.compile(r"^\| ([DU])(\d+) \|(.*)$")
_LANE = re.compile(r"^### Lane (\d+): .*\(D(\d+)-D(\d+), U(\d+)-U(\d+)\)\s*$")
_REF = re.compile(r"^[DU]\d+$")


@dataclass
class Entry:
    id: str
    line: int
    lane: int | None
    status: str | None
    text: str


@functools.cache
def _parse() -> tuple[dict[str, Entry], dict[int, dict[str, range]], list[str]]:
    entries: dict[str, Entry] = {}
    lanes: dict[int, dict[str, range]] = {}
    duplicates: list[str] = []
    lane: int | None = None
    for n, line in enumerate(DOC.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("## "):
            lane = None
        m = _LANE.match(line)
        if m:
            lane = int(m[1])
            d0, d1, u0, u1 = (int(g) for g in m.groups()[1:])
            lanes[lane] = {"D": range(d0, d1 + 1), "U": range(u0, u1 + 1)}
            continue
        m = _ROW.match(line)
        if not m:
            continue
        kind, num, rest = m[1], m[2], m[3]
        status = None
        if kind == "U":
            status = rest.split("|", 2)[0].strip()
        entry = Entry(f"{kind}{num}", n, lane, status, line)
        if entry.id in entries:
            duplicates.append(entry.id)
        entries[entry.id] = entry
    return entries, lanes, duplicates


def _yaml_load(path: Path) -> Any:
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with path.open(encoding="utf-8") as fh:
        return yaml.load(fh, Loader=loader)


def _inline_marks(node: Any, where: str, out: list[tuple[str, str, str]]) -> None:
    if isinstance(node, dict):
        mark = node.get("known_divergence")
        if isinstance(mark, dict) and mark.get("ref") is not None:
            out.append((where, str(mark.get("kind")), str(mark["ref"])))
        for value in node.values():
            _inline_marks(value, where, out)
    elif isinstance(node, list):
        for value in node:
            _inline_marks(value, where, out)


@functools.cache
def _marks() -> list[tuple[str, str, str]]:
    """``(where, kind, ref)`` of every mark that cites an entry."""
    out: list[tuple[str, str, str]] = []
    for path in sorted((ROOT / "parity" / "divergences").glob("*.yaml")):
        for key, mark in (_yaml_load(path) or {}).items():
            if isinstance(mark, dict) and mark.get("ref") is not None:
                out.append((f"{path.name}: {key}", str(mark.get("kind")), str(mark["ref"])))
    for path in sorted((ROOT / "parity" / "cases").glob("*.yaml")):
        _inline_marks(_yaml_load(path), path.name, out)
    return out


def _refs(ref: str) -> list[str]:
    return [r for r in re.split(r"[\s,/;]+", ref.strip()) if r]


def test_entries_are_unique_and_numbered() -> None:
    entries, _, duplicates = _parse()
    assert not duplicates
    for prefix, at_least in (("D", 28), ("U", 158)):
        main = {int(e.id[1:]) for e in entries.values() if e.id[0] == prefix and e.lane is None}
        assert main == set(range(1, max(main) + 1)), f"gaps in the {prefix}-entries"
        assert max(main) >= at_least


def test_lane_sections_reserve_disjoint_id_ranges() -> None:
    _, lanes, _ = _parse()
    assert sorted(lanes) == [2, 3, 4, 5, 6, 7]
    for prefix in ("D", "U"):
        taken: set[int] = set()
        for ranges in lanes.values():
            ids = set(ranges[prefix])
            assert not ids & taken
            taken |= ids


def test_entries_stay_in_their_lane_and_range() -> None:
    entries, lanes, _ = _parse()
    problems = []
    for entry in entries.values():
        prefix, num = entry.id[0], int(entry.id[1:])
        owner = next((lane for lane, r in lanes.items() if num in r[prefix]), None)
        if entry.lane != owner:
            where = f"lane {entry.lane}'s section" if entry.lane else "the main tables"
            problems.append(
                f"{entry.id} (line {entry.line}) is in {where}"
                + (f", but its ID is reserved for lane {owner}" if owner else "")
            )
    assert not problems, "\n".join(problems)


def _status_from_notes(text: str) -> str:
    notes = re.findall(r"\*\*([^*]+?)\*\*", text)
    fixed = "Fixed in pytacheck" in notes
    partly = any(n.startswith("Partly fixed") for n in notes)
    kept = "Kept" in notes
    if partly or (fixed and kept):
        return "partly fixed"
    if fixed:
        return "fixed"
    if kept:
        return "kept"
    if "Not applicable" in notes:
        return "n/a"
    return "open"


def test_every_upstream_issue_has_a_status_matching_its_notes() -> None:
    entries, _, _ = _parse()
    problems = []
    for entry in entries.values():
        if not entry.id.startswith("U"):
            continue
        if entry.status not in STATUSES:
            problems.append(f"{entry.id}: status {entry.status!r} is not one of {STATUSES}")
        elif entry.status != _status_from_notes(entry.text):
            problems.append(
                f"{entry.id}: status {entry.status!r}, but its notes say "
                f"{_status_from_notes(entry.text)!r}"
            )
    assert not problems, "\n".join(problems)


def test_marks_cite_existing_entries_with_a_matching_status() -> None:
    entries, _, _ = _parse()
    marks = _marks()
    assert marks, "no parity marks found"
    problems = []
    for where, kind, ref in marks:
        for r in _refs(ref):
            if not _REF.match(r):
                problems.append(f"{where}: ref {r!r} is not a D- or U-entry ID")
                continue
            entry = entries.get(r)
            if entry is None:
                problems.append(f"{where}: {r} is not in docs/UPSTREAM_ISSUES.md")
            elif kind in FIXING_KINDS and r.startswith("U") and entry.status not in FIXED:
                problems.append(f"{where}: a {kind} mark cites {r}, whose status is {entry.status}")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("| U9 | fixed | x | bug. **Fixed in pytacheck**: y. |", "fixed"),
        (
            "| U9 | partly fixed | x | bug. **Fixed in pytacheck**: y. **Kept**: z. |",
            "partly fixed",
        ),
        ("| U9 | partly fixed | x | bug. **Partly fixed in pytacheck**: y. |", "partly fixed"),
        ("| U9 | kept | x | bug. **Kept**: z. |", "kept"),
        ("| U9 | n/a | x | dead code. **Not applicable**: not ported. |", "n/a"),
        ("| U9 | open | x | bug. |", "open"),
    ],
)
def test_status_from_notes(text: str, status: str) -> None:
    assert _status_from_notes(text) == status
