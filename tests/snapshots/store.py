"""The snapshot format: records, their digests, and the two layouts on disk.

A *record* is what one case gave on base: ``{"ok": true, "value": ..., "frames":
...}`` or ``{"ok": false, "error": "Type: message"}``. ``value`` is the parity
encoding (``parity.canonical``); ``frames`` keeps what that encoding drops
about each DataFrame and Series in the value: the dtypes, an index other than
``0..n-1``, and which missing value (``None``, ``NaN``, ``pd.NA``, ``NaT``) an
object column holds, since the encoding writes them all as ``null``.

A *set* of records lives in ``tests/snapshots/<set>/``:

- ``INDEX``: one line per case, ``<digest> <bytes> <case id>``, sorted by
  case id. The digest is the first 16 hex digits of the SHA-256 of the
  record's compact JSON, so a changed case shows up as a changed line.
- layout ``json``: ``<group>.json`` per group (a module), ``{case id: record}``,
  indented, so a review sees the changed cells.
- layout ``xz``: ``records.jsonl.xz``, each distinct record once, as
  ``{"digest": ..., "record": ...}`` lines in the order of the cases that
  first use them (the large suites, where many cases give the same output).
"""

from __future__ import annotations

import hashlib
import json
import lzma
import math
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from parity.canonical import canonical, portable

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = ROOT / "tests" / "snapshots"

Record = dict[str, Any]

INDEX = "INDEX"
XZ_FILE = "records.jsonl.xz"
LAYOUTS = ("json", "xz")
_INDEX_HEADER = "# <digest> <bytes> <case id>, sorted by case id (tests/snapshots/README.md)\n"


class SnapshotError(Exception):
    """A snapshot set on disk is missing, or does not match its INDEX."""


# -- records ----------------------------------------------------------------------------


def envelope(value: Any) -> Record:
    """*value* as the body of a record: its parity encoding plus ``frames``."""
    body: Record = {"value": canonical(value)}
    frames: dict[str, Any] = {}
    _walk(value, "$", frames)
    if frames:
        body["frames"] = frames
    return body


def outcome(call: Callable[[], Any]) -> Record:
    """The record of *call*: its value, or the error it raised."""
    try:
        value = call()
    except Exception as exc:
        return {"ok": False, "error": portable(f"{type(exc).__name__}: {exc}")}
    return {"ok": True, **envelope(value)}


def _walk(x: Any, path: str, out: dict[str, Any]) -> None:
    """Add the ``frames`` entry of every DataFrame and Series in *x*, by path."""
    from pytacheck.module import ModuleOutput
    from pytacheck.papers import Paper, PaperList

    if isinstance(x, pd.DataFrame):
        out[path] = _frame_meta(x)
    elif isinstance(x, pd.Series):
        out[path] = _series_meta(x)
    elif isinstance(x, ModuleOutput):
        for k in list(x.keys()):
            if k not in ("paper", "prev_outputs"):  # as parity.canonical leaves them out
                _walk(x.get(k), f"{path}.{k}", out)
    elif isinstance(x, Paper):
        for k in list(x.keys()):
            _walk(x[k], f"{path}.{k}", out)
    elif isinstance(x, PaperList):
        for i, p in enumerate(x):
            _walk(p, f"{path}[{i}]", out)
    elif isinstance(x, Mapping):
        for k, v in x.items():
            _walk(v, f"{path}.{k}", out)
    elif isinstance(x, list | tuple):
        for i, v in enumerate(x):
            _walk(v, f"{path}[{i}]", out)


def _frame_meta(df: pd.DataFrame) -> dict[str, Any]:
    meta: dict[str, Any] = {"dtypes": [str(t) for t in df.dtypes]}
    index = _index_meta(df.index)
    if index is not None:
        meta["index"] = index
    na = [[str(name), kinds] for name, col in df.items() if (kinds := _na_kinds(col)) is not None]
    if na:
        meta["na"] = na
    return meta


def _series_meta(s: pd.Series) -> dict[str, Any]:
    meta: dict[str, Any] = {"dtype": str(s.dtype)}
    if s.name is not None:
        meta["name"] = str(s.name)
    index = _index_meta(s.index)
    if index is not None:
        meta["index"] = index
    kinds = _na_kinds(s)
    if kinds is not None:
        meta["na"] = kinds
    return meta


def _index_meta(index: pd.Index) -> dict[str, Any] | None:
    """The index, unless it is the default ``0..n-1`` without a name."""
    if (
        isinstance(index, pd.RangeIndex)
        and index.start == 0
        and index.step == 1
        and index.name is None
    ):
        return None
    meta: dict[str, Any] = {"dtype": str(index.dtype), "values": canonical(index.to_list())}
    if any(n is not None for n in index.names):
        meta["names"] = [None if n is None else str(n) for n in index.names]
    return meta


def _na_kind(v: Any) -> str | None:
    if v is None:
        return "None"
    if v is pd.NA:
        return "NA"
    if v is pd.NaT or (isinstance(v, np.datetime64) and np.isnat(v)):
        return "NaT"
    if isinstance(v, float | np.floating) and math.isnan(v):
        return "NaN"
    return None


def _na_kinds(col: pd.Series) -> str | list[str] | None:
    """Which missing values an object column holds: one name when all its missing
    cells hold the same one, else one name per missing cell, in row order."""
    if col.dtype != object:
        return None  # its dtype decides
    kinds = [k for v in col.tolist() if (k := _na_kind(v)) is not None]
    if not kinds:
        return None
    return kinds[0] if len(set(kinds)) == 1 else kinds


# -- serialisation --------------------------------------------------------------------


def _json(x: Any, **kw: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False, allow_nan=False, **kw)


def _bytes(text: str) -> bytes:
    # a lone surrogate (text decoded with surrogateescape) survives the round trip
    return text.encode("utf-8", "surrogatepass")


def _text(data: bytes) -> str:
    return data.decode("utf-8", "surrogatepass")


def dumps(record: Record) -> bytes:
    """*record* as compact JSON with sorted keys: what its digest is taken of."""
    return _bytes(_json(record, separators=(",", ":")))


def pretty(x: Any) -> str:
    """*x* as indented JSON with sorted keys, one scalar per line (for review)."""
    return _json(x, indent=1) + "\n"


def digest(record: Record) -> str:
    return hashlib.sha256(dumps(record)).hexdigest()[:16]


# -- sets on disk ---------------------------------------------------------------------


def write_set(
    root: Path, name: str, layout: str, records: Mapping[str, Record], groups: Mapping[str, str]
) -> Path:
    """Write the set *name* under *root*: its INDEX and the files of *layout*.

    *groups* maps each case id to its group (the ``json`` layout's file name).
    Files of an earlier recording that this one does not write are removed.
    """
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}")
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    ids = sorted(records)
    files: dict[str, bytes] = {INDEX: _index_text(records, ids)}
    if layout == "json":
        by_group: dict[str, dict[str, Record]] = {}
        for case_id in ids:
            by_group.setdefault(groups[case_id], {})[case_id] = records[case_id]
        for group, members in by_group.items():
            files[f"{group}.json"] = _bytes(pretty(members))
    else:
        lines: dict[str, bytes] = {}
        for case_id in ids:
            d = digest(records[case_id])
            if d not in lines:
                lines[d] = _bytes(
                    _json({"digest": d, "record": records[case_id]}, separators=(",", ":"))
                )
        files[XZ_FILE] = lzma.compress(b"\n".join(lines.values()) + b"\n")
    for old in folder.iterdir():
        if old.is_file() and _is_snapshot_file(old.name) and old.name not in files:
            old.unlink()
    for file_name, data in files.items():
        (folder / file_name).write_bytes(data)
    return folder


def _is_snapshot_file(name: str) -> bool:
    return name in (INDEX, XZ_FILE) or name.endswith(".json")


def _index_text(records: Mapping[str, Record], ids: list[str]) -> bytes:
    lines = [_INDEX_HEADER]
    for case_id in ids:
        record = records[case_id]
        lines.append(f"{digest(record)} {len(dumps(record)):>8} {case_id}\n")
    return _bytes("".join(lines))


def read_index(root: Path, name: str) -> dict[str, str]:
    """``{case id: digest}`` of the set *name* under *root*."""
    path = root / name / INDEX
    if not path.is_file():
        raise SnapshotError(f"no snapshot set {name!r} in {root} (no {path.name})")
    index: dict[str, str] = {}
    for line in _text(path.read_bytes()).splitlines():
        if not line or line.startswith("#"):
            continue
        d, _size, case_id = line.split(maxsplit=2)
        index[case_id] = d
    return index


def read_set(root: Path, name: str) -> dict[str, Record]:
    """``{case id: record}`` of the set *name* under *root*, checked against its INDEX."""
    index = read_index(root, name)
    folder = root / name
    xz = folder / XZ_FILE
    if xz.is_file():
        by_digest: dict[str, Record] = {}
        for line in _text(lzma.decompress(xz.read_bytes())).splitlines():
            if line:
                entry = json.loads(line)
                by_digest[entry["digest"]] = entry["record"]
        missing = sorted(i for i, d in index.items() if d not in by_digest)
        if missing:
            raise SnapshotError(f"{name}: {len(missing)} cases have no record, e.g. {missing[0]}")
        records = {case_id: by_digest[d] for case_id, d in index.items()}
    else:
        records = {}
        for path in sorted(folder.glob("*.json")):
            records.update(json.loads(_text(path.read_bytes())))
        if set(records) != set(index):
            extra = sorted(set(records) ^ set(index))
            raise SnapshotError(
                f"{name}: the files and the INDEX list different cases, e.g. {extra[0]}"
            )
    wrong = sorted(i for i, r in records.items() if digest(r) != index[i])
    if wrong:
        raise SnapshotError(
            f"{name}: {len(wrong)} records do not match their INDEX digest, e.g. {wrong[0]}"
        )
    return records


def committed_sets(root: Path) -> list[str]:
    """The names of the sets recorded under *root* (each folder holding an INDEX)."""
    return sorted(p.parent.relative_to(root).as_posix() for p in root.rglob(INDEX))
