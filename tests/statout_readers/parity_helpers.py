"""Python halves of the statout_readers parity cases that need glue code.

Each helper mirrors an inline R expression in ``parity/cases/statout_readers.yaml``:
file-writing functions are run into a temporary directory and their output
read back (as R's ``readLines()``), and R attributes (which the canonical
encoding drops) are pulled out explicitly.
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
_IMG = re.compile(r"data:image/[a-z+]+;base64,[A-Za-z0-9+/=]+")


def _fn(dotted: str) -> Any:
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)


def _read_lines(path: str) -> list[str]:
    from pytacheck.statout.spv import _read_lines as rl

    return rl(path)


def export_lines(fn: str, path: str, images: bool = False) -> list[str]:
    """Run an ``export_*_html()`` into a temp file; return its lines."""
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out.html")
        _fn(fn)(str(ROOT / path), out)
        lines = _read_lines(out)
    if images:
        lines = [_IMG.sub("data:IMG", ln) for ln in lines]
    return lines


def export_syntax(fn: str, path: str) -> Any:
    """Run a ``.*_export_syntax()`` on a copy of *path*; return the written file."""
    with tempfile.TemporaryDirectory() as d:
        src = shutil.copy(ROOT / path, d)
        p = _fn(fn)(src)
        if p is None:
            return None
        return {"path": p[len(d) :], "lines": _read_lines(p)}


def spv_attrs(path: str) -> list[dict[str, Any]]:
    """Per-table ``attrs`` of :func:`import_spv` (charts and tables)."""
    from pytacheck.statout.spv import import_spv

    out = []
    for t in import_spv(str(ROOT / path)):
        a = t["data"].attrs
        if t["is_chart"]:
            fits = a.get("spv_chart_fits") or []
            out.append(
                {
                    "type": a.get("spv_chart_type"),
                    "title": a.get("spv_chart_title"),
                    "xlab": a.get("spv_chart_xlab"),
                    "ylab": a.get("spv_chart_ylab"),
                    "fit_names": [f["name"] for f in fits],
                    "fit_exprs": [f["expr"] for f in fits],
                }
            )
        else:
            out.append(
                {
                    "title": a.get("spv_title"),
                    "rows": a.get("spv_row_dims"),
                    "cols": a.get("spv_col_dims"),
                    "layers": a.get("spv_layer_dims"),
                    "footnotes": [
                        f"{f['text']}|{'NA' if f['marker'] is None else f['marker']}"
                        for f in a.get("spv_footnotes") or []
                    ],
                }
            )
    return out


def data_labels(fn: str, path: str) -> dict[str, Any]:
    """Per-column variable and value labels of ``import_jasp()``/``import_omv()``."""
    df = _fn(fn)(str(ROOT / path))["data"]
    col_attrs = df.attrs.get("col_attrs", {})

    def lab(v: Any) -> Any:
        if isinstance(v, list):  # (label, code) pairs: R's named vector
            return {"codes": [p[1] for p in v], "labels": [p[0] for p in v]}
        return v

    out = {}
    for c in df.columns:
        a = col_attrs.get(c, {})
        labels = a.get("labels", [])
        out[str(c)] = {
            "label": lab(a.get("label")),
            "codes": [p[1] for p in labels],
            "labels": [p[0] for p in labels],
        }
    return out


def jasp_summary(path: str) -> list[str]:
    from pytacheck.statout.jasp import _jasp_analyses_summary, import_jasp

    return _jasp_analyses_summary(import_jasp(str(ROOT / path)).get("analyses"))


def labels_pairs(labs: list[tuple[str | None, float]]) -> dict[str, list[Any]]:
    """``(label, code)`` value-label pairs as R's ``unname()``/``names()`` pair."""
    return {"codes": [p[1] for p in labs], "labels": [p[0] for p in labs]}


def call(fn: str, **kwargs: Any) -> Any:
    """Call *fn* (dotted path) with keyword arguments."""
    return _fn(fn)(**kwargs)


def smcl_render_file(path: str) -> list[str]:
    from pytacheck.statout.spv import _read_lines as rl
    from pytacheck.statout.stata import _smcl_render

    return _smcl_render(rl(ROOT / path))


def smcl_chunks(path: str) -> list[dict[str, Any]]:
    from pytacheck.statout.stata import _smcl_command_chunks

    return _smcl_command_chunks(smcl_render_file(path))


def mplus_sections(path: str) -> list[dict[str, Any]]:
    from pytacheck.statout.mplus import _mplus_sections
    from pytacheck.statout.spv import _read_lines as rl

    return _mplus_sections(rl(ROOT / path))


def mplus_syntax(path: str) -> Any:
    from pytacheck.statout.mplus import _mplus_syntax_lines
    from pytacheck.statout.spv import _read_lines as rl

    return _mplus_syntax_lines(rl(ROOT / path))


def mplus_section_tables(path: str, title: str) -> Any:
    """``.mplus_output_tables()`` + ``consumed`` + label/value for one section."""
    from pytacheck.statout.mplus import _mplus_output_labelvalue, _mplus_output_tables

    sec = next(s for s in mplus_sections(path) if s["title"] == title)
    tabs = _mplus_output_tables(sec["lines"])
    remaining = [ln for ln, c in zip(sec["lines"], tabs.consumed, strict=True) if not c]
    return {
        "tables": [t["data"] for t in tabs],
        "consumed": tabs.consumed,
        "labelvalue": [b["data"] for b in _mplus_output_labelvalue(remaining, title)],
    }


def legacy_data(path: str, member: str) -> Any:
    """``.spv_decode_legacy_data()`` of one archive member, flattened to a table."""
    import zipfile

    import pandas as pd

    from pytacheck.statout.spv import _chr_dbl, _spv_decode_legacy_data

    with zipfile.ZipFile(ROOT / path) as zf:
        raw = zf.read(member)
    src = _spv_decode_legacy_data(raw)
    if src is None:
        return None
    rows = [
        (s["source_name"], v["var_name"], k, val["d"], val["s"])
        for s in src
        for v in s["variables"]
        for k, val in enumerate(v["values"], 1)
    ]
    return pd.DataFrame(
        {
            "source": pd.array([r[0] for r in rows], dtype="string"),
            "var": pd.array([r[1] for r in rows], dtype="string"),
            "k": pd.array([r[2] for r in rows], dtype="Int64"),
            "d": pd.array([_chr_dbl(r[3]) for r in rows], dtype="string"),
            "s": pd.array([r[4] for r in rows], dtype="string"),
        }
    )


def light_table(path: str, member: str) -> Any:
    """``.spv_decode_light_table()`` of one archive member."""
    import zipfile

    from pytacheck.statout.spv import _spv_decode_light_table

    with zipfile.ZipFile(ROOT / path) as zf:
        raw = zf.read(member)
    return _spv_decode_light_table(raw)


def spv_structure(path: str) -> Any:
    """``.spv_read_structure()`` of an archive, as a table."""
    import zipfile

    import pandas as pd

    from pytacheck.statout.spv import _spv_read_structure

    with tempfile.TemporaryDirectory() as d:
        with zipfile.ZipFile(ROOT / path) as zf:
            zf.extractall(d)
        rows = _spv_read_structure(d)
    if not rows:
        return None
    return pd.DataFrame(rows)
