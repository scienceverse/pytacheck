"""Python side of the code_check parity cases and tests.

code_check reads the repository file listing of an earlier ``repo_check`` (or
``data_check``) run through ``get_prev_outputs()``. The scenarios in
``tests/mod_code/scenarios.json`` describe such listings (local fixture
directories and explicit rows); :func:`cc_prev` builds the ``repo_check``
output (a :class:`~pytacheck.module.ModuleOutput` whose paper is a test paper
or paper list with fixed ids), so ``module_run()`` chains code_check onto it
exactly as in a report pipeline -- as metacheck's own tests do. The R twin is
``tests/mod_code/cc_helpers.R``.

Scenario keys:

``papers``     paper ids (default ``["p1"]``); several make a paper list
``demo``       ``true``: the paper is ``demopaper()`` (``papers`` must name its id)
``read``       paper files to ``read()`` instead (``papers`` names their ids)
``repos``      ``{paper_id, repo_url, dir, files, [repo_name], [file_url], [local]}``:
               one row per file of *dir* (a path relative to the repository
               root); ``local: false`` leaves ``file_location`` NA; ``file_url``
               is prefixed to the file path (``"local"``: a ``file://`` URL of it)
``rows``       explicit extra rows (JSON ``null`` is NA)
``drop``       columns to remove from the listing
``structure``  extra rows that only data_check's ``structure`` has (the
               output is then data_check's, chained onto repo_check's)
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "tests" / "mod_code" / "scenarios.json"

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
    return json.loads(SCENARIOS.read_text(encoding="utf-8"))


def scenario(name: str) -> dict[str, Any]:
    return _scenarios()[name]


def _repo_rows(repo: dict[str, Any], default_pid: str) -> list[dict[str, Any]]:
    rows = []
    d = repo["dir"]
    for rel in repo["files"]:
        path = f"{d}/{rel}"
        local = repo.get("local", True)
        rows.append(
            {
                "paper_id": repo.get("paper_id", default_pid),
                "repo_name": repo.get("repo_name", os.path.basename(d)),
                "repo_url": repo.get("repo_url"),
                "file_name": os.path.basename(rel),
                "file_path": rel,
                "file_url": (
                    "file://" + str(ROOT / path)
                    if repo.get("file_url") == "local"
                    else (repo["file_url"] + rel)
                    if repo.get("file_url")
                    else None
                ),
                "file_location": path if local else None,
                "file_size": float(os.path.getsize(ROOT / path)),
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


def cc_table(name: str) -> pd.DataFrame:
    """repo_check's file table of scenario *name*."""
    sc = scenario(name)
    pids = sc.get("papers", ["p1"])
    rows: list[dict[str, Any]] = []
    for repo in sc.get("repos", []):
        rows.extend(_repo_rows(repo, pids[0]))
    rows.extend(sc.get("rows", []))
    return _frame(rows, sc.get("drop", []))


def cc_paper(name: str, text: list[str] | None = None) -> Any:
    """The scenario's test paper (or paper list), with fixed ids."""
    import pytacheck as pc

    sc = scenario(name)
    if sc.get("demo"):
        return pc.demopaper()
    if sc.get("read"):
        papers_read = pc.read([ROOT / f for f in sc["read"]])
        return papers_read if isinstance(papers_read, pc.PaperList) else pc.PaperList([papers_read])
    pids = sc.get("papers", ["p1"])
    papers = []
    for pid in pids:
        p = pc.test_paper(text or ["Some text."])
        p.paper_id = pid
        papers.append(p)
    return papers[0] if len(papers) == 1 else pc.PaperList(papers)


def _output(module: str, paper: Any, pids: list[str], prev: dict[str, Any], **items: Any) -> Any:
    from pytacheck.module import ModuleOutput

    table = items.pop("table", None)
    return ModuleOutput(
        module=module,
        title={"repo_check": "Repository Check", "data_check": "Data Check"}[module],
        section="general",
        table=table,
        report="",
        traffic_light="info",
        summary_text=None,
        summary_table=pd.DataFrame({"paper_id": pd.Series(pids, dtype="string")}),
        paper=paper,
        prev_outputs=prev,
        extras=items,
    )


def cc_prev(name: str, paper: Any = None) -> Any:
    """The repo_check (or data_check) output code_check chains onto for scenario *name*."""
    from dataclasses import replace

    sc = scenario(name)
    if paper is None:
        paper = cc_paper(name)
    pids = sc.get("papers", ["p1"])
    table = cc_table(name)
    repo = _output("repo_check", paper, pids, {}, table=table)
    if "structure" not in sc:
        return repo
    from pytacheck._r.frames import bind_rows

    structure = bind_rows([table, _frame(sc["structure"], sc.get("drop", []))])
    stripped = replace(repo, paper=None, summary_table=None, prev_outputs={})
    return _output(
        "data_check", paper, pids, {"repo_check": stripped}, table=None, structure=structure
    )


def cc_run(name: str, **kwargs: Any) -> Any:
    """``module_run(<repo_check output>, "code_check", ...)``."""
    from pytacheck.module import module_run

    return module_run(cc_prev(name), "code_check", **kwargs)


def report_tables(out: Any) -> list[pd.DataFrame]:
    """The data of the report's table blocks, in order."""
    from pytacheck.report import ReportTable

    return [b.data.reset_index(drop=True) for b in out.report if isinstance(b, ReportTable)]


def report_text(out: Any) -> str:
    """The report's text blocks joined by newlines (tables skipped)."""
    report = out.report if isinstance(out.report, list) else [out.report]
    return "\n".join(b for b in report if isinstance(b, str) and b)


def cc_report_tables(name: str, **kwargs: Any) -> list[pd.DataFrame]:
    """Parity: the tables of code_check's report for scenario *name*."""
    return report_tables(cc_run(name, **kwargs))


def identity(x: Any) -> Any:
    """R ``base::identity()``."""
    return x


def dir_listing(
    path: str | os.PathLike[str], pid: str = "p1", repo_url: str | None = None
) -> pd.DataFrame:
    """A repo_check-like table of every file under *path* (like a ``local_path`` listing)."""
    root = Path(path)
    files = sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else [root]
    base = root if root.is_dir() else root.parent
    rows = [
        {
            "paper_id": pid,
            "repo_name": base.name,
            "repo_url": repo_url or str(path),
            "file_name": f.name,
            "file_path": f.relative_to(base).as_posix(),
            "file_url": None,
            "file_location": str(f),
            "file_size": float(f.stat().st_size),
        }
        for f in files
    ]
    return _frame(rows, [])


def fake_repo_check(
    table: pd.DataFrame | None, paper: Any = None, pids: list[str] | None = None
) -> Any:
    """A repo_check output holding *table*, for ``module_run(<it>, "code_check")``."""
    import pytacheck as pc

    if paper is None:
        paper = pc.test_paper(["Some text."])
        paper.paper_id = (pids or ["p1"])[0]
    if pids is None:
        pids = [paper.paper_id] if not isinstance(paper, pc.PaperList) else list(paper.names)
    return _output("repo_check", paper, pids, {}, table=table)


def run_dir(path: str | os.PathLike[str], **kwargs: Any) -> Any:
    """code_check on every file under *path* (the ``local_path`` tests' listing)."""
    from pytacheck.module import module_run

    return module_run(fake_repo_check(dir_listing(path)), "code_check", **kwargs)
