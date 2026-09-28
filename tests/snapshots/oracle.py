"""The oracle: this tree's outputs against base's snapshots (gate G5).

A migration test runs the snapshot's own case and compares::

    from tests.snapshots import oracle

    @pytest.mark.parametrize("case_id", oracle.cases("modules", module="ethics_check"))
    def test_ethics_check(case_id: str) -> None:
        oracle.check("modules", case_id)

or compares a value it computed itself with ``oracle.assert_equal``.

The comparison is exact for now: the record must be byte-identical to the
snapshot, CORE-1b's bar. ``through`` names another comparator; HARNESS-v2's
``parity/canon.py`` registers ``"canon"`` with :func:`register_comparator`
(ARCHITECTURE.md §4.1 G5), and a failure then lists the raw differences too.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tests.snapshots import inputs
from tests.snapshots.store import SNAPSHOT_DIR, Record, dumps, envelope, read_set

#: ``(case, expected, actual) -> differences``; an empty list means equal
Comparator = Callable[[inputs.Case, Record, Record], list[str]]

#: how many differences a failure lists
LIMIT = 20


def cases(set_name: str, *, module: str | None = None, group: str | None = None) -> list[str]:
    """The case ids of *set_name*, optionally only those of one module or group."""
    return [
        c.id
        for c in inputs.SETS[set_name].cases()
        if (module is None or c.module == module) and (group is None or c.group == group)
    ]


@functools.cache
def _snapshot(root: Path, set_name: str) -> dict[str, Record]:
    return read_set(root, set_name)


def load(set_name: str, case_id: str, *, root: Path = SNAPSHOT_DIR) -> Record:
    """The snapshot of *case_id*: base's record (``store.SnapshotError`` if the set
    is not recorded, ``KeyError`` if it has no such case)."""
    records = _snapshot(root, set_name)
    if case_id not in records:
        raise KeyError(f"{set_name} has no snapshot of {case_id!r}")
    return records[case_id]


def check(
    set_name: str, case_id: str, *, through: str = "exact", root: Path = SNAPSHOT_DIR
) -> None:
    """Run the case on this tree and compare its record with the snapshot."""
    assert_record(
        set_name, case_id, inputs.run(inputs.find(set_name, case_id)), through=through, root=root
    )


def assert_equal(
    set_name: str, case_id: str, value: Any, *, through: str = "exact", root: Path = SNAPSHOT_DIR
) -> None:
    """Compare *value*, a result computed by the caller, with the snapshot."""
    assert_record(set_name, case_id, {"ok": True, **envelope(value)}, through=through, root=root)


def assert_record(
    set_name: str,
    case_id: str,
    actual: Record,
    *,
    through: str = "exact",
    root: Path = SNAPSHOT_DIR,
) -> None:
    """Compare the record *actual* with the snapshot; raise ``AssertionError`` if they differ."""
    if through not in COMPARATORS:
        raise KeyError(
            f"no comparator {through!r} (there are: {', '.join(COMPARATORS)}); "
            "parity/canon.py registers 'canon' once HARNESS-v2's H0-17 lands"
        )
    case = inputs.find(set_name, case_id)
    expected = load(set_name, case_id, root=root)
    found = COMPARATORS[through](case, expected, actual)
    if not found:
        return
    lines = [f"{set_name}: {case_id} differs from its snapshot ({through}):"]
    lines += [f"  {d}" for d in found[:LIMIT]]
    if len(found) > LIMIT:
        lines.append(f"  ... and {len(found) - LIMIT} more")
    if through != "exact":
        lines.append("raw differences:")
        lines += [f"  {d}" for d in differences(expected, actual)]
    raise AssertionError("\n".join(lines))


def exact(case: inputs.Case, expected: Record, actual: Record) -> list[str]:
    """Byte-identical records, or their differences."""
    if dumps(expected) == dumps(actual):
        return []
    return differences(expected, actual, limit=10 * LIMIT) or ["the records differ"]


COMPARATORS: dict[str, Comparator] = {"exact": exact}


def register_comparator(name: str, compare: Comparator) -> None:
    """Make *compare* available as ``through=name``."""
    COMPARATORS[name] = compare


# -- differences ------------------------------------------------------------------------


def differences(expected: Any, actual: Any, limit: int = LIMIT) -> list[str]:
    """Where two records differ, as readable paths into them.

    Parity-encoded containers are followed by name, so a changed cell reads
    ``value.table.text[3]`` (the ``text`` column, row 3) rather than as
    positions in the encoding.
    """
    out: list[str] = []
    _diff(expected, actual, "", out, limit + 1)
    if len(out) > limit:
        out = [*out[:limit], "..."]
    return out


def _label(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    text = str(key)
    return f"{path}.{text}" if text.isidentifier() else f"{path}[{text!r}]"


def _short(x: Any, width: int = 70) -> str:
    text = repr(x)
    return text if len(text) <= width else text[: width - 3] + "..."


def _named(x: dict[str, Any]) -> bool:
    """A parity-encoded container whose elements have names (a frame, a list, a module output)."""
    names, v = x.get("names"), x.get("v")
    return isinstance(names, list) and isinstance(v, list) and len(names) == len(v)


def _vector(x: dict[str, Any]) -> bool:
    return set(x) == {"t", "v"} and isinstance(x["v"], list)


def _diff(a: Any, b: Any, path: str, out: list[str], limit: int) -> None:
    if len(out) >= limit:
        return
    where = path.lstrip(".") or "the record"
    if isinstance(a, dict) and isinstance(b, dict):
        if _named(a) and _named(b):
            _diff_named(a, b, path, out, limit)
        elif _vector(a) and _vector(b):
            if a["t"] != b["t"]:
                out.append(f"{where} (type): expected {a['t']}, got {b['t']}")
            _diff_list(a["v"], b["v"], path, out, limit)
        else:
            for k in sorted(set(a) | set(b)):
                if k not in b:
                    out.append(f"{_label(path, k).lstrip('.')}: missing, expected {_short(a[k])}")
                elif k not in a:
                    out.append(f"{_label(path, k).lstrip('.')}: unexpected, {_short(b[k])}")
                else:
                    _diff(a[k], b[k], _label(path, k), out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        _diff_list(a, b, path, out, limit)
    elif type(a) is not type(b) or a != b:
        out.append(f"{where}: expected {_short(a)}, got {_short(b)}")


def _diff_list(a: list[Any], b: list[Any], path: str, out: list[str], limit: int) -> None:
    for i, (x, y) in enumerate(zip(a, b, strict=False)):
        _diff(x, y, _label(path, i), out, limit)
    if len(a) != len(b):
        out.append(f"{path.lstrip('.') or 'the record'}: expected {len(a)} items, got {len(b)}")


def _diff_named(
    a: dict[str, Any], b: dict[str, Any], path: str, out: list[str], limit: int
) -> None:
    for k in sorted((set(a) | set(b)) - {"names", "v"}):
        _diff(a.get(k), b.get(k), _label(path, k), out, limit)
    if a["names"] == b["names"]:
        for name, x, y in zip(a["names"], a["v"], b["v"], strict=True):
            _diff(x, y, _label(path, name), out, limit)
        return
    where = path.lstrip(".") or "the record"
    out.append(f"{where} (names): expected {_short(a['names'])}, got {_short(b['names'])}")
    theirs: dict[Any, Any] = {}
    for name, y in zip(b["names"], b["v"], strict=True):
        theirs.setdefault(name, y)
    for name, x in zip(a["names"], a["v"], strict=True):
        if name in theirs:  # the elements both have, by name
            _diff(x, theirs.pop(name), _label(path, name), out, limit)
