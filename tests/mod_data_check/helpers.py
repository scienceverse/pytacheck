"""Python side of the data_check parity cases and tests.

data_check reads the repository file listing of an earlier ``repo_check``
through ``get_prev_outputs()``. The scenarios in
``tests/mod_data_check/scenarios.json`` describe such listings (the fixture
repositories under ``tests/mod_data_check/fixtures/repos`` plus explicit
rows); :func:`dc_prev` builds the ``repo_check`` output (a
:class:`~pytacheck.module.ModuleOutput` whose paper is a test paper or paper
list with fixed ids), so ``module_run()`` chains data_check onto it exactly as
in a report pipeline. The R twin is ``tests/mod_data_check/dc_helpers.R``.

Scenario keys:

``papers``         paper ids (default ``["p1"]``); several make a paper list
``text``           the test papers' text
``read``           paper files to ``read()`` instead (their ids are used)
``repos``          ``{dir, files, repo_url, [paper_id], [repo_name], [file_url], [local]}``:
                   one row per file of ``fixtures/repos/<dir>``; ``local: false``
                   leaves ``file_location`` NA; ``file_url`` is prefixed to the path
``copy``           copy the fixture directories to a temporary directory first
                   (archives are extracted beside themselves)
``rows``           explicit extra rows (JSON ``null`` is NA)
``drop``           columns to remove from the listing
``gated_repos``    / ``naming_issues``: repo_check's other outputs
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tests" / "mod_data_check"
FIXTURES = HERE / "fixtures"
REPOS = FIXTURES / "repos"

BASE_COLS = (
    "paper_id",
    "repo_name",
    "repo_url",
    "file_name",
    "file_path",
    "file_url",
    "file_location",
    "file_size",
)


@functools.cache
def _scenarios() -> dict[str, Any]:
    return json.loads((HERE / "scenarios.json").read_text(encoding="utf-8"))


def scenario(name: str) -> dict[str, Any]:
    return _scenarios()[name]


def _copy(src: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="dcrepo_"))
    dest = tmp / src.name
    shutil.copytree(src, dest)
    return dest


def _repo_rows(repo: dict[str, Any], default_pid: str, copy: bool) -> list[dict[str, Any]]:
    src = REPOS / repo["dir"]
    base = _copy(src) if copy else src
    local = repo.get("local", True)
    rows = []
    for rel in repo["files"]:
        rows.append(
            {
                "paper_id": repo.get("paper_id", default_pid),
                "repo_name": repo.get("repo_name", os.path.basename(repo["dir"])),
                "repo_url": repo.get("repo_url"),
                "file_name": os.path.basename(rel),
                "file_path": rel,
                "file_url": (
                    "file://" + (src / rel).resolve().as_posix()
                    if repo.get("file_url") == "local"
                    else (repo["file_url"] + rel)
                    if repo.get("file_url")
                    else None
                ),
                "file_location": (base / rel).as_posix() if local else None,
                "file_size": float(os.path.getsize(src / rel)),
            }
        )
    return rows


def _frame(rows: list[dict[str, Any]], drop: list[str]) -> pd.DataFrame:
    cols = list(BASE_COLS)
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    data: dict[str, pd.Series] = {}
    for c in cols:
        values = [r.get(c) for r in rows]
        numeric = c == "file_size" or (
            any(v is not None for v in values)
            and all(
                isinstance(v, int | float) and not isinstance(v, bool)
                for v in values
                if v is not None
            )
        )
        if numeric:
            data[c] = pd.Series(
                [float("nan") if v is None else float(v) for v in values], dtype="float64"
            )
        else:
            data[c] = pd.Series(values, dtype="string")
    df = pd.DataFrame(data, columns=cols)
    return df.drop(columns=[c for c in drop if c in df.columns])


def dc_table(name: str) -> pd.DataFrame:
    """repo_check's file table of scenario *name*."""
    sc = scenario(name)
    pids = sc.get("papers", ["p1"])
    rows: list[dict[str, Any]] = []
    for repo in sc.get("repos", []):
        rows.extend(_repo_rows(repo, pids[0], bool(sc.get("copy"))))
    rows.extend(sc.get("rows", []))
    return _frame(rows, sc.get("drop", []))


def dc_paper(name: str) -> Any:
    """The scenario's paper (a test paper, a paper list, or papers read from files)."""
    import pytacheck as pc

    sc = scenario(name)
    if sc.get("read"):
        files = [ROOT / f for f in sc["read"]]
        return pc.read(files if len(files) > 1 else files[0])
    pids = sc.get("papers", ["p1"])
    text = sc.get("text", ["Some text."])
    papers = []
    for pid in pids:
        p = pc.test_paper(text)
        p.paper_id = pid
        papers.append(p)
    return papers[0] if len(papers) == 1 else pc.PaperList(papers)


def _df(x: dict[str, list[Any]] | None) -> pd.DataFrame | None:
    if x is None:
        return None
    return pd.DataFrame({k: pd.Series(v, dtype="string") for k, v in x.items()})


def dc_prev(name: str, paper: Any = None) -> Any:
    """The repo_check output data_check chains onto for scenario *name*."""
    import pytacheck as pc
    from pytacheck.module import ModuleOutput

    sc = scenario(name)
    if paper is None:
        paper = dc_paper(name)
    pids = list(paper.names) if isinstance(paper, pc.PaperList) else [paper.paper_id]
    rc = ModuleOutput(
        module="repo_check",
        title="Repository Check",
        section="general",
        table=dc_table(name),
        report="",
        traffic_light="info",
        summary_text=None,
        summary_table=pd.DataFrame({"paper_id": pd.Series(pids, dtype="string")}),
        paper=paper,
        prev_outputs={},
        extras={
            "gated_repos": _df(sc.get("gated_repos")),
            "naming_issues": _df(sc.get("naming_issues")),
        },
    )
    if sc.get("codebook") is None:
        return rc
    from dataclasses import replace

    return ModuleOutput(
        module="codebook_check",
        title="Codebook Check",
        section="results",
        table=_df(sc["codebook"]),
        report="",
        traffic_light="info",
        summary_text=None,
        summary_table=pd.DataFrame({"paper_id": pd.Series(pids, dtype="string")}),
        paper=paper,
        prev_outputs={"repo_check": replace(rc, paper=None, summary_table=None, prev_outputs={})},
    )


# -- LLM: a deterministic mock of llm() (twin of dc_helpers.R::dc_mock_llm) ------------


def mock_spec(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "llm_mock.json").read_text(encoding="utf-8"))[name]


def mock_llm(spec: dict[str, Any]) -> Any:
    """An ``llm()`` replacement answering each data_check phase by fixed rules."""
    from pytacheck._r import grepl, sub

    def llm(text: Any, system_prompt: Any = None, type: Any = None, text_col: str = "text",
            model: Any = None, params: Any = None, phase: str | None = None, **_: Any) -> Any:  # fmt: skip
        if phase in (spec.get("error_phases") or []):
            raise RuntimeError("mock LLM error")
        txt = str(text[text_col].iloc[0])
        lines = txt.split("\n")
        items = [ln for ln, g in zip(lines, grepl(r"^[0-9]+\. ", lines), strict=True) if g]
        idx = [int(sub(r"^([0-9]+)\. .*$", r"\1", s)) for s in items]
        body = [sub(r"^[0-9]+\. ", "", s) for s in items]
        if phase == "Classifying file types":
            names = [b.removeprefix("file_name: ") for b in body]
            ext = [sub(r"^.*\.", "", n) if "." in n else "" for n in names]
            vals = [spec.get("file_types", {}).get(e, "unknown") for e in ext]
            res = pd.DataFrame({"index": idx, "value": pd.Series(vals, dtype="string")})
        elif phase == "Classifying column concepts":
            names = [b.removeprefix("column_name: ") for b in body]
            vals = [spec.get("concepts", {}).get(n, "other") for n in names]
            res = pd.DataFrame({"index": idx, "value": pd.Series(vals, dtype="string")})
        elif phase == "Assigning study groups":
            res = pd.DataFrame(
                {
                    "index": idx,
                    "group": pd.Series([spec.get("group", "ex1")] * len(idx), dtype="string"),
                }
            )
        else:
            raise RuntimeError(f"unexpected phase {phase}")
        res.attrs["llm"] = {"model": "mock/dc"}
        return res

    return llm


@contextmanager
def _llm_mocked(spec: str | None) -> Iterator[None]:
    from pytacheck.datacheck import files
    from pytacheck.utils import local_options

    if spec is None:
        with local_options({"metacheck.llm.use": False}):
            yield
        return
    orig = files._llm
    files._llm = mock_llm(mock_spec(spec))
    try:
        with local_options({"metacheck.llm.use": True}):
            yield
    finally:
        files._llm = orig


def dc_run(
    name: str,
    careless: bool = False,
    llm: str | None = None,
    prev: Any = None,
    paper: Any = None,
    **kwargs: Any,
) -> Any:
    """``module_run(<repo_check output>, "data_check", ...)``.

    *careless* makes the careless-responding indices available (metacheck with
    the ``careless`` package installed); *llm* names a mock spec in
    ``fixtures/llm_mock.json`` (LLM on, ``llm()`` mocked).
    """
    from pytacheck.module import module_run
    from pytacheck.utils import local_options

    if prev is None:
        prev = dc_prev(name, paper=paper)
    with local_options({"pytacheck.careless": careless}), _llm_mocked(llm):
        out = module_run(prev, "data_check", **kwargs)
    return norm_paths(out)


def dc_run_local(d: str, **kwargs: Any) -> Any:
    """``module_run(<test paper p1>, "data_check", local_path = <fixture dir>)``.

    The real pipeline: ``repo_check`` lists the directory.
    """
    import pytacheck as pc
    from pytacheck.module import module_run
    from pytacheck.utils import local_options

    p = pc.test_paper(["Some text."])
    p.paper_id = "p1"
    import contextlib

    # R passes the relative path (it runs from the repository root), which
    # repo_check reports as the repository URL
    local = f"tests/mod_data_check/fixtures/repos/{d}"
    with (
        contextlib.chdir(ROOT),
        local_options({"pytacheck.careless": False, "metacheck.llm.use": False}),
    ):
        out = module_run(p, "data_check", local_path=local, local_only=True, **kwargs)
    return norm_paths(out)


def norm_paths(out: Any) -> Any:
    """Fixture locations as R's goldens hold them (twin of ``.dc_norm_paths()``).

    Paths under the repository become relative to it (R runs from the
    repository root) and a temporary copy's become ``<copy>/<dir>/...``.
    """
    import re

    st = out.get("structure")
    root = ROOT.as_posix() + "/"
    if isinstance(st, pd.DataFrame) and "file_location" in st.columns:
        locs = [
            None
            if v is None or v is pd.NA
            else re.sub(
                r"^.*/metacheck-repo-files/",
                "<cache>/",
                re.sub(r"^.*/dcrepo_[^/]*/", "<copy>/", str(v).removeprefix(root)),
            )
            for v in st["file_location"].tolist()
        ]
        st = st.copy()
        st["file_location"] = pd.Series(locs, index=st.index, dtype="string")
        out.extras["structure"] = st
    if isinstance(st, pd.DataFrame) and "file_url" in st.columns:
        urls = [
            None
            if v is None or v is pd.NA
            else ("file://<root>/" + str(v)[len("file://" + root) :])
            if str(v).startswith("file://" + root)
            else str(v)
            for v in st["file_url"].tolist()
        ]
        st = st.copy()
        st["file_url"] = pd.Series(urls, index=st.index, dtype="string")
        out.extras["structure"] = st
    return out


def report_tables(out: Any) -> list[pd.DataFrame]:
    """The data of the report's table blocks, in order."""
    from pytacheck.report import ReportTable

    return [b.data.reset_index(drop=True) for b in out.report if isinstance(b, ReportTable)]


def report_text(out: Any) -> str:
    """The report's text blocks joined by newlines (tables skipped)."""
    report = out.report if isinstance(out.report, list) else [out.report]
    return "\n".join(b for b in report if isinstance(b, str) and b)


def dc_run_tables(name: str, **kwargs: Any) -> list[pd.DataFrame]:
    """Parity: the tables of data_check's report for scenario *name*."""
    return report_tables(dc_run(name, **kwargs))


def dc_dv_report(name: str, **kwargs: Any) -> dict[str, Any]:
    """Parity: the prose and table data of the spreadsheet report of a run with
    no readable table (R: the ``dv_report`` of its ``empty()`` return;
    pytacheck ends the module's own report with it, U98)."""
    from pytacheck.report import ReportTable

    rep = list(dc_run(name, **kwargs).report or [])
    start = next((i for i, b in enumerate(rep) if b == "#### Spreadsheet Formatting"), len(rep))
    rep = rep[start:]
    return {
        "text": [b for b in rep if isinstance(b, str)],
        "tables": [b.data.reset_index(drop=True) for b in rep if isinstance(b, ReportTable)],
    }


def with_careless(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call *fn* with the careless indices available (R: the vendored package loaded)."""
    from pytacheck.utils import local_options

    with local_options({"pytacheck.careless": True}):
        return fn(*args, **kwargs)


def tree_rows_r(paths: list[Any]) -> pd.DataFrame:
    """``repo_tree_rows()`` with R's 1-based ``leaf_idx``."""
    from pytacheck.modules._data_check import repo_tree_rows

    out = repo_tree_rows(paths)
    out["leaf_idx"] = out["leaf_idx"] + 1
    return out


def q_datetime_fmt(x: list[Any]) -> list[str | None]:
    """``format(.dv_q_datetime(x), "%Y-%m-%d %H:%M:%S")``."""
    from pytacheck.modules._data_check import dv_q_datetime

    return [None if v is None else v.strftime("%Y-%m-%d %H:%M:%S") for v in dv_q_datetime(x)]


def identity(x: Any) -> Any:
    """R ``base::identity()``."""
    return x
