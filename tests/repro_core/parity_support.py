"""Python side of the ``repro_core`` parity cases that need files, a sandbox or R.

Case code receives a namespace ``m`` with ``m.core``, ``m.docker`` and
``m.tables`` (the :mod:`metacheck.repro` modules), ``m.pc`` (pytacheck),
``m.pd`` (pandas) and the helpers below. :func:`run` calls it from the
repository root (the R runner's working directory), so relative fixture
paths resolve the same way on both sides.

Cases that run R code on the Python side too (``repro_run_scripts()``, the
installed-package lookups) call :func:`need_r` first: without an
``Rscript`` they are skipped under pytest.
"""

from __future__ import annotations

import contextlib
import inspect
import os
import shutil
import tempfile
import types
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPRO_FIXTURES = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures" / "repro"


def need_r() -> None:
    """Skip (under pytest) when no ``Rscript`` is available to run R code."""
    if os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript"):
        return
    import sys

    if "pytest" in sys.modules:
        import pytest

        pytest.skip("Rscript not available")
    raise RuntimeError("Rscript not available")


def files_df(
    file_name: Sequence[str],
    reads: Sequence[Any] | None = None,
    writes: Sequence[Any] | None = None,
    sources: Sequence[Any] | None = None,
) -> Any:
    """A ``repro_run_order()`` input: ``file_name`` plus list columns."""
    import pandas as pd

    df = pd.DataFrame({"file_name": pd.Series(list(file_name), dtype="string")})
    for name, col in (("reads", reads), ("writes", writes), ("sources", sources)):
        if col is not None:
            s = pd.Series([None] * len(df), dtype=object)
            for i, v in enumerate(col):
                s.iat[i] = [v] if isinstance(v, str) else list(v)
            df[name] = s
    return df


def run_order_full(files: Any, extra_edges: Any = None) -> list[Any]:
    """``repro_run_order()`` and its three attributes."""
    from metacheck.repro.core import repro_run_order

    out = repro_run_order(files, extra_edges)
    return [out, out.attrs.get("cycle"), out.attrs.get("ambiguous"), out.attrs.get("fuzzy_sources")]


def _listing(root: str) -> list[str]:
    """``sort(list.files(root, recursive = TRUE, all.files = TRUE, include.dirs = TRUE))``."""
    from metacheck._r.base import r_sort_key

    out: list[str] = []
    for d, dirs, files in os.walk(root):
        rel = os.path.relpath(d, root)
        for name in [*dirs, *files]:
            out.append(name if rel == "." else f"{rel}/{name}")
    return sorted(out, key=r_sort_key)


def materialize(plan: Any, structure_df: Any) -> dict[str, Any]:
    """``repro_materialize_layout()`` into a fresh directory, with the tree it built."""
    from metacheck.repro.core import repro_materialize_layout

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        out = repro_materialize_layout(plan, structure_df, root)
        return {
            "files": _listing(root),
            "materialised": out.attrs["materialised"],
            "is_root": str(out) == root,
        }


def _unroot(x: Any, root: str) -> Any:
    import pandas as pd

    if x is None or x is pd.NA:
        return None
    return str(x).replace(root, "<root>")


def write_scripts(
    code_text_list: Any, rewrite_list: Any, plan: Any, inject_libs: Any = None
) -> dict[str, Any]:
    """``repro_write_scripts()`` into a fresh directory; paths shown relative to it."""
    from metacheck.repro.core import repro_write_scripts

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(root)
        out = repro_write_scripts(code_text_list, rewrite_list, plan, root, inject_libs=inject_libs)
        written = [
            [_unroot(s, root) for s in Path(p).read_text(encoding="utf-8").split("\n")[:-1]]
            for p in out["script_path"].tolist()
        ]
        tbl = out.copy()
        for col in ("script_path", "run_dir"):
            tbl[col] = tbl[col].map(lambda v: _unroot(v, root)).astype("string")
        return {"table": tbl, "written": written, "files": _listing(root)}


def run_scripts(
    scripts: Sequence[str],
    order: Sequence[str] | None = None,
    extra: dict[str, str] | None = None,
    lib: bool = False,
    **kwargs: Any,
) -> Any:
    """``repro_run_scripts()`` on copies of fixture scripts in a fresh directory.

    *scripts* are file names under metacheck's ``fixtures/repro`` or this
    area's ``fixtures/scripts``; *extra* maps file names to script text.
    ``data.csv`` is always copied. With *lib*, an empty ``lib_x`` library is
    passed. ``elapsed`` is dropped and the directory is shown as ``<root>``.
    """
    need_r()
    from metacheck.repro.core import repro_run_scripts

    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "root")
        os.makedirs(root)
        shutil.copy(REPRO_FIXTURES / "data.csv", os.path.join(root, "data.csv"))
        names: list[str] = []
        for s in scripts:
            src = REPRO_FIXTURES / s
            if not src.exists():
                src = FIXTURES / "scripts" / s
            shutil.copy(src, os.path.join(root, s))
            names.append(s)
        for name, text in (extra or {}).items():
            Path(root, name).write_text(text, encoding="utf-8")
            names.append(name)
        import pandas as pd

        run_tbl = pd.DataFrame(
            {
                "file_name": names,
                "script_path": [os.path.join(root, n) for n in names],
                "run_dir": [root] * len(names),
            }
        )
        lib_dir = None
        if lib:
            lib_dir = os.path.join(root, "lib_x")
            os.makedirs(lib_dir)
        out = repro_run_scripts(
            run_tbl, order=list(order) if order is not None else names, lib_dir=lib_dir, **kwargs
        )
        out = out.drop(columns=["elapsed"])
        for col in ("error", "stdout", "stderr"):
            out[col] = out[col].map(lambda v: _unroot(v, root)).astype("string")
        out["script_lines"] = [
            [_unroot(s, root) for s in lines] for lines in out["script_lines"].tolist()
        ]
        return out


def materialize_review() -> dict[str, Any]:
    """Twin of ``rc_materialize_review()``: copies that fail quietly next to good ones."""
    import pandas as pd

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.csv")
        Path(src).write_text("a\n", encoding="utf-8")
        srcdir = os.path.join(td, "adir")
        os.makedirs(srcdir)
        plan = pd.DataFrame(
            {
                "file_name": ["src.csv", "src2.csv", "adir", "missing.csv", "src.csv"],
                "target_path": ["a", "a/b.csv", "d/dir", "m/x.csv", "e/f/g.csv"],
                "original_target": [None, "a/c/orig.csv", None, "m/orig.csv", ""],
            }
        )
        sd = pd.DataFrame(
            {"file_name": ["src.csv", "src2.csv", "adir"], "file_location": [src, src, srcdir]}
        )
        x = materialize(plan, sd)
        mat = x["materialised"].copy()
        mat["source"] = mat["source"].map(lambda v: _unroot(v, td)).astype("string")
        x["materialised"] = mat
        return x


def _namespace() -> types.SimpleNamespace:
    import pandas as pd

    import metacheck as pc
    from metacheck.repro import core, docker, tables

    return types.SimpleNamespace(
        core=core,
        docker=docker,
        tables=tables,
        pc=pc,
        pd=pd,
        root=ROOT,
        fx=lambda *p: str(FIXTURES.joinpath(*p)),
        need_r=need_r,
        files_df=files_df,
        run_order_full=run_order_full,
        materialize=materialize,
        materialize_review=materialize_review,
        write_scripts=write_scripts,
        run_scripts=run_scripts,
        tmp=tmp,
        with_options=with_options,
        r_sorted=r_sorted,
    )


def with_options(values: dict[str, Any], fn: Callable[[], Any]) -> Any:
    """``withr::with_options(values, fn())``."""
    from metacheck.utils import local_options

    with local_options(values):
        return fn()


def r_sorted(x: Sequence[Any]) -> list[Any]:
    """R ``sort()`` of a character vector."""
    from metacheck._r.base import r_sort_key

    return sorted(x, key=r_sort_key)


def tmp(fn: Callable[[str], Any]) -> Any:
    """``fn(dir)`` with a fresh, existing temporary directory."""
    with tempfile.TemporaryDirectory() as d:
        return fn(d)


def run(x: Callable[..., Any]) -> Any:
    """Call *x* (with the namespace if it takes an argument) from the repository root."""
    with contextlib.chdir(ROOT):
        if inspect.signature(x).parameters:
            return x(_namespace())
        return x()
