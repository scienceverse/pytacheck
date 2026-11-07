"""The repository file tree (port of metacheck's test-file-tree.R, 4a61888)."""

from __future__ import annotations

import re

import pandas as pd
import pytest

from metacheck.report.file_tree import (
    brief_file_tree,
    brief_insert_after_summary,
    file_tree,
    file_tree_block,
    file_tree_drop_from_report,
    file_tree_from_report,
    file_tree_rows,
    tree_type_icon,
    tree_type_label,
    useless_file_kind,
    useless_file_severity,
)

REPORT = ["Intro", "#### Data Tree", "legend", "tree", "#### Data Files", "x"]


def frame(**cols):
    return pd.DataFrame({k: pd.Series(v, dtype="object") for k, v in cols.items()})


def test_file_tree_rows() -> None:
    rows = file_tree_rows(["data/raw.csv", "README.md", "data/clean.csv", None, ""])
    # folders first, then files, each alphabetically
    assert rows["text"].tolist() == [
        "├── data/",
        "│   ├── clean.csv",
        "│   └── raw.csv",
        "└── README.md",
    ]
    # each file row points back at its input path (0-based here); a folder row has none
    assert [None if pd.isna(i) else int(i) for i in rows["leaf_idx"]] == [None, 2, 0, 1]

    empty = file_tree_rows([])
    assert len(empty) == 0
    assert list(empty.columns) == ["text", "leaf_idx"]

    # a folder given as an input is drawn and sorted as a folder, even when
    # nothing lies below it, and its row points at it
    rows = file_tree_rows(["b.csv", "a_empty", "z_data/x.csv"], [False, True, False])
    assert rows["text"].tolist() == ["├── a_empty/", "├── z_data/", "│   └── x.csv", "└── b.csv"]
    assert [None if pd.isna(i) else int(i) for i in rows["leaf_idx"]] == [1, None, 2, 0]


def test_file_tree_block() -> None:
    files = frame(
        repo_url=["my_study"] * 3,
        file_name=["raw.csv", "README.md", "analysis.R"],
        file_path=["data/raw.csv", "README.md", "code/analysis.R"],
        data_type=["data", "documentation", "code"],
        doc_role=[None, "readme", None],
    )
    naming = frame(file_name=["raw.csv"], detail=["no date"])
    block = file_tree_block(files, naming)
    assert block is not None

    assert block[0] == "#### Data Tree"
    assert "and any file-naming issue found by repo_check" in block[1]
    assert "junk files, hidden files and empty folders are marked" in block[1]
    assert block[2] == "**Repository: my_study**"
    tree = block[3]
    # the tree sits in a scroll box
    assert re.match(r'^::: \{style="max-height: 40em; overflow: auto;"\}\n```\n', tree)
    assert tree.endswith("\n```\n:::")
    assert "data/" in tree
    assert re.search("raw.csv\\s+\U0001f4ca data\\s+no date", tree)
    assert re.search("README.md\\s+\U0001f4c4 readme", tree)

    assert file_tree_block(None) is None
    assert file_tree_block(files.iloc[0:0]) is None
    assert file_tree_block(frame(file_name=["a.csv"])) is None


def test_file_tree_block_marks_junk_from_repo_check_for_a_repository_link() -> None:
    files = frame(
        repo_url=["https://osf.io/abcde"],
        file_name=["raw.csv"],
        file_path=["data/raw.csv"],
        data_type=["data"],
    )
    useless = frame(
        repo_url=["https://osf.io/abcde"] * 4,
        file_path=[".DS_Store", "__MACOSX/._raw.csv", "__MACOSX/._x.csv", ".git/config"],
        kind=["operating system"] * 3 + ["version control"],
        severity=["problem", "problem", "problem", "suggestion"],
    )
    block = file_tree_block(files, None, useless)
    assert block is not None
    tree = block[3]
    assert re.search(r"\.DS_Store\s+junk: remove", tree)
    # a junk folder is one line with the number of files inside
    assert re.search(r"__MACOSX/\s+junk: remove \(2 files\)", tree)
    assert re.search(r"\.git/\s+junk: keep only on purpose \(1 file\)", tree)
    assert "._raw.csv" not in tree


def test_file_tree_block_lists_a_local_folder_in_full(tmp_path) -> None:
    d = tmp_path / "study"
    for x in ("data/raw", "code/__pycache__", ".git/objects/ab", "docs/empty"):
        (d / x).mkdir(parents=True)
    (d / "data/raw/raw data.csv").write_text("a,b\n1,2\n")
    (d / "code/analysis.R").write_text("x <- 1\n")
    (d / "code/__pycache__/u.pyc").write_text("b\n")
    for i in (1, 2, 3):
        (d / f".git/objects/ab/obj{i}").write_text("o\n")
    (d / ".DS_Store").write_text("junk\n")
    (d / ".gitignore").write_text("*.html\n")
    (d / "README.md").write_text("# Study\n")
    files = frame(
        repo_url=[str(d)] * 3,
        file_name=["raw data.csv", "analysis.R", "README.md"],
        file_path=["data/raw/raw data.csv", "code/analysis.R", "README.md"],
        data_type=["data", "code", "documentation"],
        doc_role=[None, None, "readme"],
    )
    block = file_tree_block(files)
    assert block is not None
    assert block[2] == "**Repository: study**"
    lines = [x for x in block[3].split("\n") if not re.match(r"^(:::|```)", x)]
    # junk folders as one line with their number of files, nothing below them
    assert any(
        re.search(r"^├── \.git/\s+junk: keep only on purpose \(3 files\)$", x) for x in lines
    )
    assert any(re.search(r"__pycache__/\s+junk: remove \(1 file\)$", x) for x in lines)
    assert not any(re.search(r"obj1|u\.pyc", x) for x in lines)
    # junk files, hidden files and empty folders are marked
    assert any(re.search(r"\.DS_Store\s+junk: remove$", x) for x in lines)
    assert any(re.search(r"\.gitignore\s+hidden file$", x) for x in lines)
    assert any(re.search(r"empty/\s+empty folder$", x) for x in lines)
    # classified files keep their classification
    assert any(re.search("raw data\\.csv\\s+\U0001f4ca data$", x) for x in lines)
    # folders (empty ones included) before files, alphabetically
    top = [re.sub(r"^[├└]── ", "", x) for x in lines if re.match(r"^[├└]── ", x)]
    top = [re.sub(r"\s.*$", "", x) for x in top]
    assert top == [".git/", "code/", "data/", "docs/", ".DS_Store", ".gitignore", "README.md"]


def test_file_tree_from_report_and_file_tree() -> None:
    assert file_tree_from_report(REPORT) == ["legend", "tree"]
    assert file_tree_from_report(["Intro", "#### Data Files"]) is None
    assert file_tree_from_report(None) is None

    # data_check's tree first
    assert file_tree({"data_check": {"report": REPORT}}) == ["legend", "tree"]

    # else one drawn from repo_check's files and junk, without its heading
    files = frame(
        repo_url=["my_study"], file_name=["raw.csv"], file_path=["data/raw.csv"], data_type=["data"]
    )
    useless = frame(
        repo_url=["my_study"],
        file_path=[".DS_Store"],
        kind=["operating system"],
        severity=["problem"],
    )
    tree = file_tree(
        {
            "data_check": {"report": "nothing"},
            "repo_check": {"table": files, "useless_files": useless},
        }
    )
    assert tree is not None
    assert "#### Data Tree" not in tree
    assert tree[1] == "**Repository: my_study**"
    assert re.search(r"\.DS_Store\s+junk: remove", tree[2])

    assert file_tree({}) is None


def test_file_tree_drop_from_report() -> None:
    assert file_tree_drop_from_report(REPORT) == ["Intro", "#### Data Files", "x"]
    # a tree at the end, and a heading with nothing under it
    assert file_tree_drop_from_report(["Intro", "#### Data Tree", "tree"]) == ["Intro"]
    assert file_tree_drop_from_report(["Intro", "#### Data Tree"]) == ["Intro"]
    # nothing to drop
    assert file_tree_drop_from_report(["Intro", "#### Data Files"]) == ["Intro", "#### Data Files"]
    assert file_tree_drop_from_report(None) is None


def test_file_tree_block_names_a_local_folder_by_its_own_name() -> None:
    files = frame(
        repo_url=["/data/metacheck_storage/metacheck-api-jobs/1a2b3c/repo/my_study"],
        file_name=["raw.csv"],
        file_path=["data/raw.csv"],
        data_type=["data"],
        doc_role=[None],
    )
    block = file_tree_block(files)
    assert block is not None
    assert block[2] == "**Repository: my_study**"
    assert not any("metacheck_storage" in b for b in block)
    # a link stays a link
    files["repo_url"] = ["https://osf.io/abcde/"]
    block = file_tree_block(files)
    assert block is not None and block[2] == "**Repository: https://osf.io/abcde/**"


def test_junk_kinds_and_type_labels() -> None:
    kinds = useless_file_kind([".DS_Store", "__MACOSX/._a", ".git/config", "data/raw.csv", None])
    assert kinds[:3] == ["operating system", "operating system", "version control"]
    assert kinds[3] is None and kinds[4] is None
    assert useless_file_severity(["operating system", "version control", None]) == [
        "problem",
        "suggestion",
        None,
    ]
    assert tree_type_icon("data", None) == "\U0001f4ca"
    assert tree_type_label("documentation", "readme") == "readme"


def test_brief_file_tree_and_insert() -> None:
    callout = brief_file_tree({"data_check": {"report": REPORT}})
    assert callout == (
        '::: {.callout-note title="Files in the repository"}\n\nlegend\n\ntree\n\n:::'
    )
    assert brief_file_tree({}) is None

    text = "## Summary\n\nrows\n\n## Validation\n\ntable\n"
    out = brief_insert_after_summary(text, "SECTION")
    assert out.index("SECTION") < out.index("## Validation")
    assert out.index("SECTION") > out.index("rows")
    # without validation: before the feedback sentence
    text = "## Summary\n\nrows\n\nIf there are things we can improve, please email us"
    out = brief_insert_after_summary(text, "SECTION")
    assert out.index("SECTION") < out.index("If there are things")
    # neither marker: at the end
    assert brief_insert_after_summary("text", "SECTION") == "text\n\nSECTION"


@pytest.mark.parametrize("path", ["", None])
def test_blank_paths_are_left_out(path) -> None:
    assert len(file_tree_rows([path])) == 0
