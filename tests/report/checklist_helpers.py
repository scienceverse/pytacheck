"""Python side of the checklist report and file tree parity cases (twin of checklist_helpers.R)."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Any

import pandas as pd

from tests.report.parity_helpers import (
    ROOT,
    _args,
    _mods,
    _unpath,
    rp_mask,
    rp_norm_output,
    rp_stem,
)

LOGO = str(ROOT / "src" / "metacheck" / "report" / "assets" / "logo.png")


def rc_mo(module: str, title: str, traffic_light: str = "info", **kw: Any) -> Any:
    """A module output, as module_run() returns it."""
    from metacheck.module import ModuleOutput

    own = {k: kw.pop(k) for k in ("summary_text", "report") if k in kw}
    return ModuleOutput(
        module=module, title=title, section="general", traffic_light=traffic_light, extras=kw, **own
    )


def rc_mask(x: str) -> str:
    """Mask what legitimately differs: images and the font as data: URIs, the date, the versions."""
    x = re.sub(r"data:[A-Za-z/+.-]+;base64,[A-Za-z0-9+/=]+", "data:<masked>", x)
    x = re.sub(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "<date>", x)
    x = re.sub(r"Metacheck [0-9][0-9.]*( \(pytacheck [^)]*\))?", "Metacheck <version>", x)
    # a module's own description is its module's to compare, not the report's
    x = re.sub(
        r"(<details><summary>How [^<\n]* works</summary>\n\n::: \{\.fold\}\n\n).*?(\n\n:::\n\n</details>)",
        r"\1<HOW-IT-WORKS>\2",
        x,
        flags=re.S,
    )
    return rp_mask(x)


def _df(**cols: Any) -> pd.DataFrame:
    return pd.DataFrame({k: pd.Series(v, dtype="object") for k, v in cols.items()})


def rc_scenario(name: str) -> list[Any]:
    if name == "checks":
        return [
            rc_mo(
                "my_check",
                "My Check",
                "red",
                checks=_df(
                    status=["fix", "fine", "improve"],
                    title=["A", "B", "C"],
                    found=["Found A.", "Found B.", None],
                    todo=["Do A.", "", "Do C."],
                ),
            )
        ]
    if name == "checks_unknown":
        return [
            rc_mo(
                "my_check",
                "My Check",
                "red",
                checks=_df(
                    status=["fix", "great", "improve"],
                    title=["A", "B", "C"],
                    found=["Found A.", "Found B.", "Found C."],
                ),
            )
        ]
    if name == "single":
        return [
            rc_mo("a", "Red", "red", summary_text="Something to fix.", report="The details."),
            rc_mo("b", "Yellow", "yellow", summary_text="Something.", report="Something."),
            rc_mo("c", "Green", "green", summary_text="Fine."),
            rc_mo("d", "Not applicable", "na", summary_text="Nothing to check."),
            rc_mo(
                "e", "Failed", "fail", summary_text="This module failed to run", report="an error"
            ),
            rc_mo(
                "f",
                "Blue",
                "blue",
                summary_text=["Part one.", "Part two."],
                report=["Part one.", "Part two."],
            ),
        ]
    if name == "empty":
        return []
    if name == "all_statuses":
        return [
            rc_mo(
                "my_check",
                "All <results> & more",
                "red",
                checks=_df(
                    status=["fix", "improve", "decide", "info", "fine", "na", "fix", "fine"],
                    title=[
                        "No junk",
                        "Size",
                        "Pick one",
                        "Note",
                        "Names",
                        "ORCIDs",
                        "Bare",
                        "Plain",
                    ],
                    section=["2 Files", "", "1.1", "", "", "", "", ""],
                    found=[
                        "2 junk files.",
                        "1 MB.",
                        "Two options.",
                        "FYI.",
                        "All fine.",
                        "No ORCID.",
                        "",
                        "Ok.",
                    ],
                    todo=["Remove them.", "Shrink it.", "Choose.", "", "", "", "", ""],
                    why=["Junk confuses.", "", "", "", "", "", "", ""],
                    note=["", "A note.", "", "", "", "Not needed.", "", ""],
                    details=[
                        "The junk table.",
                        "",
                        "Options.",
                        "FYI details.",
                        "The size table.",
                        "",
                        "",
                        "",
                    ],
                    details_title=["Show the files", "", "", "", "", "", "", ""],
                ),
            )
        ]
    if name == "full":
        return [
            rc_mo(
                "my_check",
                'My "Check"',
                "red",
                intro="This report checks a package.",
                logo=LOGO,
                checks=_df(
                    status=["fix", "fine", "na"],
                    title=["No junk", "Size", "ORCIDs"],
                    section=["2 Files", "1.1", ""],
                    found=["2 junk files.", "1 MB.", "No ORCID."],
                    todo=["Remove them.", "", ""],
                    details=["The junk table.", "The size table.", ""],
                    details_title=["Show the files", "", ""],
                ),
            )
        ]
    if name == "multi":
        return [
            rc_mo(
                "my_check",
                "My Check",
                "red",
                checks=_df(
                    status=["fix"],
                    title=["No junk"],
                    found=["2 junk files."],
                    todo=["Remove them."],
                ),
            ),
            rc_mo("other", "Other", "green", summary_text="Fine."),
        ]
    repro = rc_mo(
        "reproducibility_check",
        "Reproducibility Check",
        "green",
        summary_text="The code ran.",
        report="Ran.",
        checklist_section="The scripts.",
    )
    other = rc_mo("other", "Other", "green", summary_text="Fine.", report="Other details.")
    if name == "tree":
        return [
            rc_mo(
                "data_check",
                "Data Check",
                "yellow",
                summary_text="Some data files.",
                report=["The data files.", "#### Data Tree", "Where the files sit.", "TREE-LINES"],
            ),
            repro,
            other,
        ]
    if name == "no_tree":
        return [repro, other]
    if name == "brief_keep":
        return [
            rc_mo(
                "kept",
                "Kept",
                "green",
                summary_text="The code ran.",
                report="Ran fine.",
                brief=True,
            ),
            rc_mo("green", "Plain green", "green", summary_text="Nothing to see."),
            rc_mo(
                "red",
                "Red",
                "red",
                summary_text="Fix this.",
                report="Details of the problem.",
            ),
        ]
    if name == "section_note":
        return [
            rc_mo(
                "sec",
                "Sectioned",
                "red",
                summary_text="Look below.",
                report=["Long", "report"],
                checklist_section=["Paragraph one.", "Paragraph two."],
            )
        ]
    if name == "fold":
        from metacheck.report.blocks import collapse_section

        return [
            rc_mo(
                "fold",
                "Folding",
                "yellow",
                summary_text="Short.",
                report=[
                    "Details first.",
                    collapse_section(["One.", "Two."], "Why this", callout="note"),
                ],
            )
        ]
    raise ValueError(f"no scenario {name}")


def rc_paper(kind: str) -> Any:
    from metacheck.papers.io import test_paper

    if kind == "none":
        return None
    p = test_paper()
    info = p.info.copy()
    if kind in ("titled", "doi"):
        info["title"] = pd.Series(["my_package"], dtype="string")
    if kind == "doi":
        info["doi"] = pd.Series(["10.1234/abc"], dtype="string")
    p.info = info
    return p


def rc_checks(name: str) -> dict[str, Any]:
    from metacheck.report.checklist import checklist_checks

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        checks = checklist_checks(rc_scenario(name))
    out: dict[str, Any] = {c: checks[c].tolist() for c in checks.columns}
    out["warnings"] = pd.Series([str(w.message) for w in caught], dtype="string")
    return out


def rc_qmd(name: str, paper: str = "plain") -> str:
    from metacheck.report.checklist import checklist_qmd

    return rc_mask(checklist_qmd(rc_scenario(name), rc_paper(paper)))


def rc_report_qmd(name: str, paper: str = "titled", type: str = "checklist") -> str:
    from metacheck.report.report import report_qmd

    return rc_mask(report_qmd(rc_scenario(name), rc_paper(paper), report_type=type))


def _rc_output(res: Any) -> dict[str, Any]:
    # the module outputs are compared by the cases of the full report; here what
    # the report type could change: a module's report is its text, which R's
    # collapse_section() folds into a <details> box when the module runs under the
    # checklist type, while a pytacheck module's output is the same under every type
    # and folded when the report is written (the text of the file is compared)
    out = rp_norm_output(res)
    return {
        k: {
            "module": v.module,
            "title": v.title,
            "traffic_light": v.traffic_light,
            "summary_text": v.summary_text,
            "n_report": len(v.report) if isinstance(v.report, list) else 1,
        }
        for k, v in out.items()
    }


def rc_report(
    paper: Any, modules: Any, type: str = "checklist", args: Any = None
) -> dict[str, Any]:
    from metacheck.report.blocks import report_type
    from metacheck.report.report import report

    fd, f = tempfile.mkstemp(suffix=".qmd")
    os.close(fd)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = report(paper, _mods(modules), f, "qmd", _args(args), report_type=type)
        txt = Path(f).read_text(encoding="utf-8")
        txt = txt[:-1] if txt.endswith("\n") else txt
    finally:
        os.unlink(f)
    return {
        "output": _rc_output(res),
        "save_path_ok": res.save_path == f,
        "type_after": report_type(),
        "file": rc_mask(_unpath(txt)),
    }


def rc_report_repository(
    folder: str, modules: Any, args: Any = None, type: str = "checklist"
) -> dict[str, Any]:
    from metacheck.report.report import report_repository

    d = tempfile.mkdtemp()
    fd, f = tempfile.mkstemp(suffix=".qmd")
    os.close(fd)
    try:
        os.makedirs(os.path.join(d, folder))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = report_repository(
                os.path.join(d, folder),
                output_file=f,
                output_format="qmd",
                modules=_mods(modules),
                args=_args(args),
                report_type=type,
            )
        txt = Path(f).read_text(encoding="utf-8")
        txt = txt[:-1] if txt.endswith("\n") else txt
    finally:
        shutil.rmtree(d, ignore_errors=True)
        os.unlink(f)
    # (the module outputs are compared by the report_repository cases of the full report)
    return {"modules": [rp_stem(k) for k in res], "file": rc_mask(_unpath(txt))}


# ---- file tree -----------------------------------------------------------------


def rc_files(kind: str) -> dict[str, Any]:
    na = None
    if kind == "study":
        return {
            "files": _df(
                repo_url=["my_study"] * 3,
                file_name=["raw.csv", "README.md", "analysis.R"],
                file_path=["data/raw.csv", "README.md", "code/analysis.R"],
                data_type=["data", "documentation", "code"],
                doc_role=[na, "readme", na],
            ),
            "naming": _df(file_name=["raw.csv"], detail=["no date"]),
            "useless": None,
        }
    if kind == "link":
        return {
            "files": _df(
                repo_url=["https://osf.io/abcde"],
                file_name=["raw.csv"],
                file_path=["data/raw.csv"],
                data_type=["data"],
            ),
            "naming": None,
            "useless": _df(
                repo_url=["https://osf.io/abcde"] * 4,
                file_path=[".DS_Store", "__MACOSX/._raw.csv", "__MACOSX/._x.csv", ".git/config"],
                kind=["operating system"] * 3 + ["version control"],
                severity=["problem", "problem", "problem", "suggestion"],
            ),
        }
    if kind == "server":
        return {
            "files": _df(
                repo_url=["/data/storage/jobs/1a2b3c/repo/my_study"],
                file_name=["raw.csv"],
                file_path=["data/raw.csv"],
                data_type=["data"],
                doc_role=[na],
            ),
            "naming": None,
            "useless": None,
        }
    if kind == "link_slash":
        return {
            "files": _df(
                repo_url=["https://osf.io/abcde/"],
                file_name=["raw.csv"],
                file_path=["data/raw.csv"],
                data_type=["data"],
                doc_role=[na],
            ),
            "naming": None,
            "useless": None,
        }
    if kind == "no_files":
        return {"files": None, "naming": None, "useless": None}
    if kind == "zero_rows":
        return {
            "files": _df(repo_url=[], file_name=[], file_path=[], data_type=[]),
            "naming": None,
            "useless": None,
        }
    if kind == "no_paths":
        return {"files": _df(file_name=["a.csv"]), "naming": None, "useless": None}
    raise ValueError(f"no files {kind}")


def rc_ft_rows(paths: Any, is_dir: Any = None) -> dict[str, Any]:
    from metacheck.report.file_tree import file_tree_rows

    rows = file_tree_rows(list(paths), None if is_dir is None else list(is_dir))
    # R's indices are 1-based
    return {
        "text": pd.Series(rows["text"].tolist(), dtype="string"),
        "leaf_idx": pd.Series(
            [None if pd.isna(i) else int(i) + 1 for i in rows["leaf_idx"].tolist()], dtype="Int64"
        ),
    }


def rc_ft_block(kind: str) -> Any:
    from metacheck.report.file_tree import file_tree_block

    x = rc_files(kind)
    block = file_tree_block(x["files"], x["naming"], x["useless"])
    return None if block is None else pd.Series(block, dtype="string")


def rc_ft_local() -> dict[str, Any]:
    from metacheck.report.file_tree import file_tree_block

    d = tempfile.mkdtemp(prefix="ft")
    try:
        for x in ("data/raw", "code/__pycache__", ".git/objects/ab", "docs/empty"):
            os.makedirs(os.path.join(d, x))

        def write(path: str, text: str) -> None:
            with open(os.path.join(d, path), "w", encoding="utf-8") as fh:
                fh.write(text + "\n")

        write("data/raw/raw data.csv", "a,b\n1,2")
        write("code/analysis.R", "x <- 1")
        write("code/__pycache__/u.pyc", "b")
        for i in (1, 2, 3):
            write(f"{'.git/objects/ab'}/obj{i}", "o")
        write(".DS_Store", "junk")
        write(".gitignore", "*.html")
        write("README.md", "# Study")
        files = _df(
            repo_url=[d] * 3,
            file_name=["raw data.csv", "analysis.R", "README.md"],
            file_path=["data/raw/raw data.csv", "code/analysis.R", "README.md"],
            data_type=["data", "code", "documentation"],
            doc_role=[None, None, "readme"],
        )
        block = file_tree_block(files)
        assert block is not None
        return {
            "head": block[0],
            "legend": block[1],
            "label": block[2].replace(os.path.basename(d), "<dir>"),
            "lines": block[3].split("\n"),
        }
    finally:
        shutil.rmtree(d, ignore_errors=True)


def rc_ft_report(kind: str) -> Any:
    from metacheck.report.file_tree import file_tree_drop_from_report, file_tree_from_report

    report = ["Intro", "#### Data Tree", "legend", "tree", "#### Data Files", "x"]
    if kind == "from":
        return file_tree_from_report(report)
    if kind == "from_none":
        return [
            file_tree_from_report(["Intro", "#### Data Files"]) is None,
            file_tree_from_report(None) is None,
        ]
    if kind == "drop":
        return file_tree_drop_from_report(report)
    if kind == "drop_end":
        return [
            file_tree_drop_from_report(["Intro", "#### Data Tree", "tree"]),
            file_tree_drop_from_report(["Intro", "#### Data Tree"]),
        ]
    if kind == "drop_none":
        return [
            file_tree_drop_from_report(["Intro", "#### Data Files"]),
            file_tree_drop_from_report(None),
        ]
    raise ValueError(f"no report {kind}")


def rc_ft_pick(kind: str) -> Any:
    from metacheck.report.file_tree import file_tree

    report = ["Intro", "#### Data Tree", "legend", "tree", "#### Data Files", "x"]
    if kind == "data_check":
        return file_tree({"data_check": {"report": report}})
    if kind == "repo_check":
        files = rc_files("study")["files"].iloc[[0]]
        useless = _df(
            repo_url=["my_study"],
            file_path=[".DS_Store"],
            kind=["operating system"],
            severity=["problem"],
        )
        return file_tree(
            {
                "data_check": {"report": "nothing"},
                "repo_check": {"table": files, "useless_files": useless},
            }
        )
    if kind == "none":
        return file_tree({}) is None
    raise ValueError(f"no pick {kind}")
