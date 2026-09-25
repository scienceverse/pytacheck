"""Saving and reloading a paper's module outputs.

Port of ``capture_module_tables()``, ``collect_module_tables()`` and
``.load_module_tables()`` (``R/module.R``). A run can save each paper's
module results so a later single-module retest (e.g. the reproducibility
check alone) reloads a paper's already-computed ``data_check`` /
``code_check`` / ``psychds_check`` outputs instead of recomputing them, and
so one module's tables can be stacked across a corpus.

metacheck saves ``<paper_id>.rds`` with ``saveRDS()``. pytacheck saves
``<paper_id>.json``: a safe, Python-native encoding (no pickle) that keeps
data frames lossless -- column names and order, pandas dtypes, list columns,
``NA``/``NaN`` and ``attrs``. :func:`collect_module_tables` and
:func:`_load_module_tables` also read the ``.rds`` files metacheck writes,
through pytacheck's R unserializer, so results move between the two.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import warnings
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from pytacheck._r.base import r_sort_key
from pytacheck._r.frames import bind_rows

__all__ = ["capture_module_tables", "collect_module_tables"]

_FORMAT = "pytacheck.module_tables"
_VERSION = 1

# Non-result elements of a module output (``.module_output_plumbing``) and the
# large non-archival ones (``.module_output_archive_exclude``).
_PLUMBING = ("paper", "prev_outputs", "module", "title", "section")
_ARCHIVE_EXCLUDE = ("previews",)


# ---------------------------------------------------------------------------
# a lossless JSON encoding of module results
# ---------------------------------------------------------------------------


def _encode_float(x: float) -> Any:
    if math.isnan(x):
        return {"$float": "nan"}
    if math.isinf(x):
        return {"$float": "inf" if x > 0 else "-inf"}
    return x


def _encode_index(idx: pd.Index) -> Any:
    if isinstance(idx, pd.RangeIndex) and idx.start == 0 and idx.step == 1:
        return None
    return {"name": _encode(idx.name), "values": [_encode(v) for v in idx.tolist()]}


def _encode_series_data(s: pd.Series) -> dict[str, Any]:
    dtype = s.dtype
    out: dict[str, Any] = {"dtype": str(dtype)}
    if isinstance(dtype, pd.CategoricalDtype):
        out["categories"] = [_encode(c) for c in dtype.categories.tolist()]
        out["ordered"] = bool(dtype.ordered)
        out["data"] = [_encode(None if pd.isna(v) else v) for v in s.astype(object).tolist()]
        return out
    if pd.api.types.is_datetime64_any_dtype(dtype):
        out["data"] = [None if pd.isna(v) else pd.Timestamp(v).isoformat() for v in s.tolist()]
        return out
    out["data"] = [_encode(v) for v in s.tolist()]
    return out


def _encode(x: Any) -> Any:
    """*x* as JSON-safe data that :func:`_decode` restores."""
    if x is None:
        return None
    if x is pd.NA:
        return {"$na": True}
    if x is pd.NaT:
        return {"$nat": True}
    if isinstance(x, bool | np.bool_):
        return bool(x)
    if isinstance(x, int | np.integer):
        return int(x)
    if isinstance(x, float | np.floating):
        return _encode_float(float(x))
    if isinstance(x, str):
        return x
    if isinstance(x, pd.Timestamp | dt.datetime):
        return {"$datetime": x.isoformat()}
    if isinstance(x, dt.date):
        return {"$date": x.isoformat()}
    if isinstance(x, pd.DataFrame):
        return {
            "$df": {
                "names": [_encode(c) for c in x.columns.tolist()],
                "columns": [_encode_series_data(x.iloc[:, i]) for i in range(x.shape[1])],
                "nrow": len(x),
                "index": _encode_index(x.index),
                "attrs": _encode(dict(x.attrs)) if x.attrs else None,
            }
        }
    if isinstance(x, pd.Series):
        return {
            "$series": {
                "name": _encode(x.name),
                **_encode_series_data(x),
                "index": _encode_index(x.index),
            }
        }
    if isinstance(x, np.ndarray):
        return {
            "$ndarray": {
                "dtype": str(x.dtype),
                "shape": list(x.shape),
                "data": [_encode(v) for v in x.ravel().tolist()],
            }
        }
    if isinstance(x, Mapping):
        if all(isinstance(k, str) and not k.startswith("$") for k in x):
            return {k: _encode(v) for k, v in x.items()}
        return {"$dict": [[_encode(k), _encode(v)] for k, v in x.items()]}
    if isinstance(x, tuple):
        return {"$tuple": [_encode(v) for v in x]}
    if isinstance(x, set | frozenset):
        return {"$set": [_encode(v) for v in x]}
    if isinstance(x, list):
        return [_encode(v) for v in x]
    warnings.warn(
        f"capture_module_tables(): a {type(x).__name__} cannot be saved; its repr is kept",
        stacklevel=3,
    )
    return {"$repr": repr(x), "$type": type(x).__name__}


def _decode_series(spec: Mapping[str, Any], n: int | None = None) -> pd.Series:
    dtype = spec.get("dtype", "object")
    raw = spec.get("data", [])
    if dtype == "category":
        cats = [_decode(c) for c in spec.get("categories", [])]
        values = [_decode(v) for v in raw]
        return pd.Series(
            pd.Categorical(values, categories=cats, ordered=bool(spec.get("ordered")))
        )
    if dtype.startswith("datetime64"):
        return pd.Series(pd.to_datetime(raw)).astype(dtype)
    values = [_decode(v) for v in raw]
    if dtype == "object":
        s = pd.Series([None] * len(values), dtype=object)
        for i, v in enumerate(values):
            s.iat[i] = v
        return s
    try:
        return pd.Series(values, dtype=dtype)
    except (TypeError, ValueError):
        return pd.Series(values, dtype=object)


def _decode_index(spec: Any) -> pd.Index | None:
    if spec is None:
        return None
    return pd.Index([_decode(v) for v in spec.get("values", [])], name=_decode(spec.get("name")))


def _decode(x: Any) -> Any:
    """Restore a value :func:`_encode` wrote."""
    if isinstance(x, list):
        return [_decode(v) for v in x]
    if not isinstance(x, dict):
        return x
    if len(x) == 1 or (len(x) == 2 and "$repr" in x):
        key = next(iter(x))
        val = x[key]
        if key == "$na":
            return pd.NA
        if key == "$nat":
            return pd.NaT
        if key == "$float":
            return float(val)
        if key == "$datetime":
            return pd.Timestamp(val)
        if key == "$date":
            return dt.date.fromisoformat(val)
        if key == "$dict":
            return {_decode(k): _decode(v) for k, v in val}
        if key == "$tuple":
            return tuple(_decode(v) for v in val)
        if key == "$set":
            return {_decode(v) for v in val}
        if key == "$ndarray":
            data = [_decode(v) for v in val["data"]]
            return np.array(data, dtype=val["dtype"]).reshape(val["shape"])
        if key == "$series":
            s = _decode_series(val)
            s.name = _decode(val.get("name"))
            idx = _decode_index(val.get("index"))
            if idx is not None:
                s.index = idx
            return s
        if key == "$df":
            names = [_decode(c) for c in val["names"]]
            cols = [_decode_series(c) for c in val["columns"]]
            nrow = int(val.get("nrow", len(cols[0]) if cols else 0))
            df = pd.DataFrame(dict(enumerate(cols))) if cols else pd.DataFrame(index=range(nrow))
            df.columns = pd.Index(names, dtype=object) if cols else df.columns
            idx = _decode_index(val.get("index"))
            if idx is not None:
                df.index = idx
            attrs = val.get("attrs")
            if attrs:
                df.attrs.update(_decode(attrs))
            return df
        if key == "$repr":
            return x["$repr"]
    return {k: _decode(v) for k, v in x.items()}


# ---------------------------------------------------------------------------
# metacheck's .rds files
# ---------------------------------------------------------------------------


def _from_r(obj: Any, unbox: bool = True) -> Any:
    """A deserialized R value (:mod:`pytacheck.datacheck._files_rdata`) in Python.

    Data frames become pandas frames (list columns hold Python lists), named
    lists and named vectors dicts, unnamed lists lists; a length-1 atomic
    vector is a scalar when *unbox*. Functions, environments and calls stay
    opaque ``RObject`` values.
    """
    from pytacheck.datacheck import _files_rdata as rd

    if not isinstance(obj, rd.RObject):
        return obj
    t = obj.type
    if t == rd.NILSXP:
        return None
    if t == rd.VECSXP and "data.frame" in obj.classes:
        return _frame_from_r(obj)
    names = rd._strvec(obj.attr("names"))
    if t in (rd.STRSXP, rd.LGLSXP, rd.INTSXP, rd.REALSXP, rd.CPLXSXP):
        s = rd._column_to_pandas(obj, len(obj))
        vals = [None if _scalar_na(v) else _py_scalar(v) for v in s.astype(object).tolist()]
        if names and len(names) == len(vals):
            return {("" if k is None else k): v for k, v in zip(names, vals, strict=True)}
        return vals[0] if unbox and len(vals) == 1 else vals
    if t in (rd.VECSXP, rd.EXPRSXP):
        items = [_from_r(v) for v in obj.value or []]
        if names and len(names) == len(items):
            out: dict[str, Any] = {}
            for k, v in zip(names, items, strict=True):
                out.setdefault("" if k is None else k, v)
            return out
        return items
    return obj


def _scalar_na(v: Any) -> bool:
    if v is None or v is pd.NA or v is pd.NaT:
        return True
    return isinstance(v, float) and math.isnan(v)


def _py_scalar(v: Any) -> Any:
    if isinstance(v, np.generic):
        return v.item()
    return v


def _frame_from_r(df: Any) -> pd.DataFrame:
    from pytacheck.datacheck import _files_rdata as rd

    n = rd._nrow(df)
    names = rd._strvec(df.attr("names"))
    cols = list(df.value or [])
    names = (names + [""] * len(cols))[: len(cols)]
    series: list[pd.Series] = []
    for col in cols:
        if isinstance(col, rd.RObject) and col.type == rd.VECSXP and "data.frame" not in col.classes:
            s = pd.Series([None] * n, dtype=object)
            for i, v in enumerate((col.value or [])[:n]):
                s.iat[i] = _from_r(v, unbox=False)
            series.append(s)
        else:
            series.append(rd._column_to_pandas(col, n).reset_index(drop=True))
    out = pd.DataFrame(dict(enumerate(series))) if series else pd.DataFrame(index=range(n))
    if series:
        out.columns = pd.Index(["" if nm is None else nm for nm in names], dtype=object)
    return out


# ---------------------------------------------------------------------------
# reading and writing saved results
# ---------------------------------------------------------------------------


def _read_saved(path: str | os.PathLike[str]) -> dict[str, Any] | None:
    """A saved results file (``.json`` or metacheck's ``.rds``), or ``None``."""
    p = Path(path)
    try:
        if p.suffix == ".rds":
            from pytacheck.datacheck._files_rdata import read_rds

            saved = _from_r(read_rds(p))
        else:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("format") != _FORMAT:
                return None
            saved = _decode(raw)
    except Exception:
        return None
    if not isinstance(saved, dict):
        return None
    modules = saved.get("modules")
    if modules is None:
        saved["modules"] = {}
    elif not isinstance(modules, dict):
        return None
    return saved


def _chain_items(chain: Any) -> list[Any]:
    """The module outputs of a chain (a list, a ``report_module_run()`` result, ...)."""
    from pytacheck.module import ModuleOutput

    if isinstance(chain, ModuleOutput):
        # one output: its whole chain, earlier modules first
        return [*chain.prev_outputs.values(), replace(chain, prev_outputs={})]
    if isinstance(chain, Mapping):
        return list(chain.values())
    if isinstance(chain, Iterable) and not isinstance(chain, str | bytes):
        return list(chain)
    return [chain]


def _elements(mo: Any) -> list[tuple[str, Any]]:
    """``(name, value)`` of every element of a module output."""
    from pytacheck.module import ModuleOutput

    if isinstance(mo, ModuleOutput):
        return [(k, mo.get(k)) for k in mo.keys()]
    if isinstance(mo, Mapping):
        return [(str(k), v) for k, v in mo.items()]
    return []


def _get(mo: Any, key: str) -> Any:
    return dict(_elements(mo)).get(key)


def capture_module_tables(
    chain: Any, results_dir: str | os.PathLike[str], paper_id: str | None = None
) -> str:
    """Save one paper's full module outputs to disk.

    Port of ``R/module.R::capture_module_tables()``. *chain* is a list of
    module outputs, e.g. :func:`~pytacheck.report.report_module_run`'s result
    (a single :class:`~pytacheck.module.ModuleOutput` stands for its whole
    chain). Every result element of every module is kept (the plumbing --
    ``paper``, ``prev_outputs``, ``module``, ``title``, ``section`` -- and
    ``data_check``'s ``previews`` are dropped), with the widest non-empty
    ``summary_table`` stored once at the top level. Writes
    ``<results_dir>/<paper_id>.json`` and returns its path.
    """
    from pytacheck.module import ModuleOutput

    items = _chain_items(chain)
    mods = [m for m in items if isinstance(m, ModuleOutput)]
    if not mods:
        mods = items

    def blank(x: Any) -> bool:
        return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x)) or x == ""

    chain_pid: Any = paper_id
    if blank(chain_pid):
        for mo in mods:
            st = _get(mo, "summary_table")
            if isinstance(st, pd.DataFrame) and "paper_id" in st.columns and len(st) > 0:
                cand = st["paper_id"].iloc[0]
                cand = None if _scalar_na(cand) else str(cand)
                if cand is not None and cand != "":
                    chain_pid = cand
                    break
    pid = "paper" if chain_pid is None else chain_pid
    if _scalar_na(pid) or pid == "":
        pid = "paper" if paper_id is None else paper_id
    pid = str(pid)

    drop = set(_PLUMBING) | set(_ARCHIVE_EXCLUDE)
    outputs = [{k: v for k, v in _elements(mo) if k not in drop} for mo in mods]
    mod_names: list[str] = []
    seen: set[str] = set()
    for i, mo in enumerate(mods, 1):
        name = _get(mo, "module")
        name = None if name is None or _scalar_na(name) else str(name)
        if name is None or name == "" or name in seen:
            if name is not None:
                seen.add(name)
            name = f"module_{i}"
        else:
            seen.add(name)
        mod_names.append(name)

    combined: pd.DataFrame | None = None
    for mo in mods:
        st = _get(mo, "summary_table")
        if (
            isinstance(st, pd.DataFrame)
            and len(st) > 0
            and st.shape[1] > (combined.shape[1] if combined is not None else 0)
        ):
            combined = st

    os.makedirs(results_dir, exist_ok=True)
    path = os.path.join(os.fspath(results_dir), f"{pid}.json")
    payload = {
        "format": _FORMAT,
        "version": _VERSION,
        "paper_id": pid,
        "generated": dt.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary_table": _encode(combined),
        "modules": {name: _encode(out) for name, out in zip(mod_names, outputs, strict=True)},
    }
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), "utf-8")
    return path


def _saved_files(results_dir: str | os.PathLike[str]) -> list[str]:
    """The saved results files in *results_dir* (``.json`` and ``.rds``), sorted as R does."""
    d = os.fspath(results_dir)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    files = [
        os.path.join(d, f)
        for f in names
        if (f.endswith(".rds") or f.endswith(".json")) and os.path.isfile(os.path.join(d, f))
    ]
    return sorted(files, key=r_sort_key)


def collect_module_tables(
    results_dir: str | os.PathLike[str], module: str, element: str = "table"
) -> pd.DataFrame:
    """Stack one module's tables across every saved paper.

    Port of ``R/module.R::collect_module_tables()``. Reads every saved
    results file in *results_dir* (pytacheck's ``<paper_id>.json`` and
    metacheck's ``<paper_id>.rds``) and binds *element* (default
    ``"table"``; also ``"summary_table"`` or a module's extra frame) of
    *module* across papers, tagged with ``paper_id`` (first column). An
    empty frame, with a warning, when there is nothing to collect.
    """
    rfiles = _saved_files(results_dir)
    if not rfiles:
        warnings.warn(
            f"No *.json or *.rds files found in {os.fspath(results_dir)}. "
            "Did the run call capture_module_tables()?",
            stacklevel=2,
        )
        return pd.DataFrame()
    parts: list[pd.DataFrame] = []
    for f in rfiles:
        j = _read_saved(f)
        if j is None:
            continue
        mod = j["modules"].get(module)
        if mod is None or not isinstance(mod, Mapping):
            continue
        el = mod.get(element)
        if not isinstance(el, pd.DataFrame) or len(el) == 0:
            continue
        if "paper_id" not in el.columns and j.get("paper_id") is not None:
            el = el.copy()
            el["paper_id"] = pd.Series([str(j["paper_id"])] * len(el), dtype="string", index=el.index)
        parts.append(el.reset_index(drop=True))
    if not parts:
        warnings.warn(
            f"{len(rfiles)} *.json/*.rds file(s) found in {os.fspath(results_dir)}, "
            f'but none had a "{module}" module with a non-empty "{element}".',
            stacklevel=2,
        )
        return pd.DataFrame()
    out = bind_rows(parts)
    front = [c for c in ["paper_id"] if c in out.columns]
    return out.loc[:, front + [c for c in out.columns if c not in front]]


def _load_module_tables(
    results_dir: str | os.PathLike[str], paper_id: str, paper: Any
) -> Any:
    """Load a paper's saved module outputs back into a module-output chain.

    Port of ``R/module.R::.load_module_tables()``. Reads
    ``<results_dir>/<paper_id>.json`` (else metacheck's ``.rds``) and returns
    the last module's :class:`~pytacheck.module.ModuleOutput` with every
    module in ``prev_outputs`` and the saved combined ``summary_table``, so
    :func:`~pytacheck.module.module_run` on it sees them as if they had just
    been computed (``get_prev_outputs("data_check", "structure")`` ...).
    ``None`` when nothing was saved for *paper_id*.
    """
    from pytacheck.module import ModuleOutput

    base = os.path.join(os.fspath(results_dir), str(paper_id))
    path = next((p for p in (base + ".json", base + ".rds") if os.path.exists(p)), None)
    if path is None:
        return None
    saved = _read_saved(path)
    if saved is None or len(saved["modules"]) == 0:
        return None

    fields = ("table", "report", "traffic_light", "summary_text", "summary_table")
    prev_outputs: dict[str, ModuleOutput] = {}
    for name, mo in saved["modules"].items():
        mo = dict(mo) if isinstance(mo, Mapping) else {}
        title = mo.pop("title", None)
        section = mo.pop("section", None)
        for k in ("module", "paper", "prev_outputs"):
            mo.pop(k, None)
        values = {k: mo.pop(k, None) for k in fields}
        prev_outputs[name] = ModuleOutput(
            module=name,
            title=name if title is None else title,
            section="general" if section is None else section,
            table=values["table"],
            report=values["report"],
            traffic_light=values["traffic_light"],  # type: ignore[arg-type]
            summary_text=values["summary_text"],
            summary_table=values["summary_table"],
            paper=paper,
            prev_outputs={},
            extras=mo,
        )
    last = list(prev_outputs.values())[-1]
    return replace(
        last, prev_outputs=dict(prev_outputs), summary_table=saved.get("summary_table")
    )
