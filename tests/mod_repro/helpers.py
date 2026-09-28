"""Python side of the reproducibility_check parity cases (``parity/cases/mod_repro.yaml``).

reproducibility_check reads code_check's ``table``, psychds_check's ``table``
and data_check's ``structure`` from the module chain. :func:`rc_input` builds
that chain from a named scenario in ``tests/mod_repro/fixtures/scenarios.json``
exactly as the R twin ``tests/mod_repro/rc_helpers.R`` does, and
:func:`rc_run` runs the module on it (from the repository root, since the
scenarios use repository-relative file locations). Execute cases keep the
sandbox, then replace its random path by ``"<root>"`` and delete it.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
from functools import cache
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SCENARIOS = HERE / "fixtures" / "scenarios.json"
REVIEW_SCENARIOS = HERE / "fixtures" / "review_scenarios.json"

PSYCHSCI = [
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614522816.json",
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614527830.json",
    "upstream/metacheck/inst/demos/to_err_is_human.json",
]


@cache
def specs() -> dict[str, Any]:
    """The scenario files (R: ``rc_specs()``), the review cases' extra ones included."""
    out: dict[str, Any] = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    if REVIEW_SCENARIOS.exists():
        extra = json.loads(REVIEW_SCENARIOS.read_text(encoding="utf-8"))["scenarios"]
        out["scenarios"] = {**out["scenarios"], **extra}
    return out


def rc_df(cols: dict[str, list[Any]] | None) -> pd.DataFrame | None:
    """A column-oriented spec as a data frame (``parse_error`` logical, the rest character)."""
    if cols is None:
        return None
    data: dict[str, Any] = {}
    for name, values in cols.items():
        if name == "parse_error":
            data[name] = pd.array([None if v is None else bool(v) for v in values], dtype="boolean")
        else:
            data[name] = pd.array([None if v is None else str(v) for v in values], dtype="string")
    return pd.DataFrame(data)


def _with_pid(df: pd.DataFrame | None, pid: str) -> pd.DataFrame | None:
    if df is None or "paper_id" in df.columns:
        return df
    out = df.copy()
    out.insert(0, "paper_id", pd.array([pid] * len(df), dtype="string"))
    return out


def rc_paper(spec: dict[str, Any]) -> Any:
    import pytacheck as pc

    which = spec.get("paper", "test")
    if which == "test":
        p = pc.test_paper(spec.get("text"))
        p.paper_id = "p1"  # R's test_paper() ids come from the clock
        return p
    if which == "demo":
        return pc.demopaper()
    if which == "psychsci":
        return pc.read([ROOT / f for f in PSYCHSCI])
    if which == "bibr12":
        return pc.read(ROOT / "tests" / "fixtures" / "bibr_v12_full.json")
    raise ValueError(f"unknown paper {which}")


def _absolute(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Repository-relative file locations made absolute (R: ``rc_absolute()``)."""
    if df is None:
        return df
    out = df.copy()
    for col in ("file_location", "file_url"):
        if col in out.columns:
            out[col] = pd.array(
                [None if v is None or v is pd.NA else str(ROOT / v) for v in out[col].tolist()],
                dtype="string",
            )
    return out


def rc_input(name: str, paper: Any = None, absolute: bool = False) -> Any:
    """The fake upstream chain of scenario *name* (R: ``rc_input()``).

    *paper* replaces the scenario's paper (e.g. a bibr 12.x reading);
    *absolute* makes the file locations absolute, so the chain works from any
    working directory.
    """
    from pytacheck.module import ModuleOutput
    from pytacheck.papers.tables import paper_id

    spec = specs()["scenarios"][name]
    if paper is None:
        paper = rc_paper(spec)
    if spec.get("real"):
        return paper
    ids = list(paper_id(paper))
    code = _with_pid(rc_df(spec.get("code")), ids[0])
    structure = _with_pid(rc_df(spec.get("structure")), ids[0])
    if absolute:
        code, structure = _absolute(code), _absolute(structure)
    prev: dict[str, Any] = {}
    if spec.get("plan") is not None:
        prev["psychds_check"] = {"table": rc_df(spec["plan"])}
    summary = pd.DataFrame({"paper_id": pd.array(ids, dtype="string")})
    if code is not None:
        if structure is not None:
            prev["data_check"] = {"structure": structure}
        extras = {}
        if spec.get("version_pin") is not None:
            extras["version_pin"] = {"r_versions": list(spec["version_pin"])}
        return ModuleOutput(
            module="code_check",
            title="Code Check",
            section="results",
            table=code,
            summary_table=summary,
            paper=paper,
            prev_outputs=prev,
            extras=extras,
        )
    return ModuleOutput(
        module="data_check",
        title="Data Check",
        section="results",
        table=pd.DataFrame(),
        summary_table=summary,
        paper=paper,
        prev_outputs=prev,
        extras={"structure": structure},
    )


def _tables_dir(name: str, td: str, tables: Any) -> Any:
    """Save the scenario's upstream outputs as a prior build (R: ``rc_tables_dir()``)."""
    from pytacheck.module import ModuleOutput
    from pytacheck.papers.tables import paper_id
    from pytacheck.repro.tables import capture_module_tables

    x = rc_input(name)
    if tables == "none":
        return x.paper
    pid = next(iter(paper_id(x.paper)))
    chain = [
        ModuleOutput(module="code_check", title="Code Check", section="results", table=x.table),
        ModuleOutput(
            module="data_check",
            title="Data Check",
            section="results",
            extras={"structure": x.prev_outputs["data_check"]["structure"]},
        ),
        ModuleOutput(
            module="psychds_check",
            title="Psych-DS Check",
            section="results",
            table=x.prev_outputs["psychds_check"]["table"],
        ),
    ]
    capture_module_tables(chain, td, paper_id=pid)
    return x.paper


def _unroot(x: Any, root: str) -> Any:
    if isinstance(x, str):
        return x.replace(root, "<root>")
    if isinstance(x, list):
        return [_unroot(v, root) for v in x]
    return x


def _untime(mo: Any) -> None:
    """Zero the run times, which vary between runs (R: ``rc_untime()``)."""
    from pytacheck.report.blocks import ReportTable

    rr = mo.get("run_results")
    if not isinstance(rr, pd.DataFrame) or len(rr) == 0:
        return
    rr = rr.copy()
    rr["elapsed"] = 0.0
    mo.extras["run_results"] = rr

    def fix(x: Any) -> Any:
        if isinstance(x, ReportTable) and "Time (s)" in x.data.columns:
            data = x.data.copy()
            data["Time (s)"] = 0.0
            return ReportTable(data, x.colwidths, x.maxrows, x.escape, x.column, x.options)
        if isinstance(x, list):
            return [fix(v) for v in x]
        return x

    mo.report = fix(mo.report)


def rc_scrub(mo: Any) -> Any:
    """Replace the kept sandbox's path by ``"<root>"`` and delete it (R: ``rc_scrub()``)."""
    _untime(mo)
    root = mo.get("sandbox")
    if root is None:
        return mo
    mo.report = _unroot(mo.report, root)
    rr = mo.get("run_results")
    if isinstance(rr, pd.DataFrame):
        rr = rr.copy()
        for col in ("error", "stdout", "stderr"):
            rr[col] = pd.array([_unroot(v, root) for v in rr[col].tolist()], dtype=rr[col].dtype)
        rr["script_lines"] = pd.Series(
            [_unroot(list(v or []), root) for v in rr["script_lines"].tolist()], dtype=object
        )
        mo.extras["run_results"] = rr
    mods = mo.get("modifications")
    if isinstance(mods, pd.DataFrame) and len(mods):
        mods = mods.copy()
        mods["detail"] = pd.array(
            [_unroot(v, root) for v in mods["detail"].tolist()], dtype="string"
        )
        mo.extras["modifications"] = mods
    mo.extras["sandbox"] = "<root>"
    shutil.rmtree(root, ignore_errors=True)
    return mo


def rc_run(name: str, tables: Any = None, paper: Any = None, **kwargs: Any) -> Any:
    """``module_run(rc_input(name), "reproducibility_check", ...)``, scrubbed (R: ``rc_run()``)."""
    from pytacheck.module import module_run

    with contextlib.chdir(ROOT):
        if tables is not None:
            with tempfile.TemporaryDirectory(prefix="rc_tables_") as td:
                p = _tables_dir(name, td, tables)
                return rc_scrub(module_run(p, "reproducibility_check", tables_dir=td, **kwargs))
        return rc_scrub(module_run(rc_input(name, paper), "reproducibility_check", **kwargs))


# -- mocked external steps -----------------------------------------------------------


def fake_install(install_deps: pd.DataFrame, *args: Any, **kwargs: Any) -> pd.DataFrame:
    """A fixed install result (R: ``rc_fake_install()``)."""
    pkgs = [str(p) for p in install_deps["package"].tolist()]
    n = len(pkgs)
    return pd.DataFrame(
        {
            "package": pd.array([*pkgs, "oldpkg", "oddpkg", "fine"], dtype="string"),
            "source": pd.array(["cran"] * (n + 3), dtype="string"),
            "installed": pd.array([False] * n + [True, False, True], dtype="boolean"),
            "message": pd.array(
                ["package is not available for this version of R"] * n + ["", None, ""],
                dtype="string",
            ),
            "via_archive": pd.array([False] * n + [True, False, False], dtype="boolean"),
            "category": pd.array(
                ["cran_unavailable"] * n + [None, "mystery", None], dtype="string"
            ),
        }
    )


def rc_run_mocked(name: str, **kwargs: Any) -> Any:
    """:func:`rc_run` with installs and Docker mocked (R: ``rc_run_mocked()``)."""
    import warnings
    from unittest import mock

    from pytacheck.repro import core, docker

    def run_docker(
        run_tbl: Any,
        order: Any,
        sandbox_root: Any,
        lib_dir: Any = None,
        image: str = "",
        timeout: float = 600,
        skip: Any = (),
        parses: Any = None,
        failed_deps: Any = (),
    ) -> Any:
        return core.repro_run_scripts(
            run_tbl,
            order,
            lib_dir=lib_dir,
            timeout=timeout,
            skip=skip,
            parses=parses,
            failed_deps=failed_deps,
        )

    with (
        mock.patch.object(core, "repro_install_deps", fake_install),
        mock.patch.object(docker, "repro_install_deps_docker", fake_install),
        mock.patch.object(docker, "repro_docker_available", lambda: {"ok": True, "msg": ""}),
        mock.patch.object(docker, "repro_run_scripts_docker", run_docker),
        warnings.catch_warnings(),
    ):
        warnings.simplefilter("ignore")
        return rc_run(name, **kwargs)


def identity(x: Any) -> Any:
    """R ``base::identity()``."""
    return x
