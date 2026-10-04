"""The README draft of a data package: the library, the module, the file it hands back and the command."""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import string
import sys
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from metacheck.cli import main
from metacheck.datapackage import check_package, open_package, package_selection, report_package
from metacheck.datapackage._docs_policy import load_readme_template
from metacheck.datapackage._docs_readme import find_placeholders
from metacheck.datapackage.docs import check_docs
from metacheck.datapackage.readme import (
    DRAFT_COLUMNS,
    FILE_NAME,
    VARIABLE_COLUMNS,
    ReadmeDraft,
    draft_readme,
    readme_report,
    summary_line,
)
from metacheck.module import module_files
from metacheck.packs.registry import refresh

MODULE = "datapackage::package_readme"

SURVEY = (
    "participant_id,age,gender,rt,date\n"
    "1,23,f,512.5,2024-01-05\n"
    "2,31,m,498.0,2024-01-06\n"
    "3,,m,630.2,2024-01-07\n"
)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PYTACHECK_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PYTACHECK_PRESET", raising=False)
    monkeypatch.chdir(tmp_path)
    refresh()
    yield
    refresh()


def _folder(tmp_path: Path, files: dict[str, str | bytes], name: str = "pkg") -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_bytes(content.encode("utf-8"))  # exactly as given, no line-end translation
    return root


def _draft(root: Path, **kwargs: Any) -> ReadmeDraft:
    with open_package(root) as pkg:
        return draft_readme(pkg, **kwargs)


def _snapshot(root: Path) -> dict[str, str]:
    """Every file and folder of *root* with a hash of its bytes: to see that nothing changed."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<folder>"
    return out


def _placeholder_lines(text: str, **kwargs: Any) -> list[str]:
    template = load_readme_template(kwargs.get("readme"))
    return [value for _line, value in find_placeholders(text, template)]


def _reported(result: Any) -> int:
    """How many places with template text the README check says it found."""
    detail = str(result.checklist.set_index("item").loc["readme_placeholders", "detail"])
    match = re.match(r"(\d+) places? with template text", detail)
    return int(match.group(1)) if match else 0


def _action(draft: ReadmeDraft, section: str) -> str:
    row = draft.sections.loc[draft.sections["id"] == section].iloc[0]
    return str(row["action"])


PACKAGE = {
    "data/raw/survey_2024-01-05.csv": SURVEY,
    "data/raw/survey_2024-01-06.csv": SURVEY,
    "code/analysis.R": "d <- read.csv('data/raw/survey_2024-01-05.csv')\n",
    "LICENSE": "Creative Commons Attribution 4.0 International Public License\n",
}


# -- a package without a README -----------------------------------------------------------


def test_without_a_readme_the_draft_follows_the_template(tmp_path: Path) -> None:
    draft = _draft(_folder(tmp_path, PACKAGE))
    assert draft.file_name == FILE_NAME == "README.md"
    assert draft.readme_path is None
    lines = draft.text.splitlines()
    assert lines[0].startswith("# <") and lines[0].endswith(">")  # the title is a placeholder
    template = load_readme_template(None)
    headings = [line for line in lines if line.startswith("## ")]
    assert headings == [f"## {s.title}" for s in template.sections]
    assert list(draft.sections.columns) == list(DRAFT_COLUMNS)
    assert list(draft.sections["id"]) == [s.id for s in template.sections]
    assert draft.text.endswith("\n") and not draft.text.endswith("\n\n")
    assert "\n\n\n" not in draft.text


def test_the_draft_has_the_files_formats_naming_variables_and_licence(tmp_path: Path) -> None:
    draft = _draft(_folder(tmp_path, PACKAGE))
    text = draft.text
    assert "The package has 4 files in 3 folders" in text
    for line in (
        "├── code/",
        "│   └── analysis.R",
        "│       ├── survey_2024-01-05.csv",
        "└── LICENSE",
    ):
        assert line in text
    assert "- `code/` (1 file): <Describe what is in this folder>" in text
    assert "- `data/` (2 files): <Describe what is in this folder>" in text
    assert "`.csv` (2 files)" in text and "`.R` (1 file)" in text
    assert "Dates in names are written as YYYY-MM-DD." in text
    assert "Licence: CC BY. The full text is in `LICENSE`." in text
    assert _action(draft, "licence") == "added from the package"
    # the two files have the same variables, so they share a table
    assert "have the same 5 variables" in text
    for name in ("participant_id", "age", "gender", "rt", "date"):
        assert f"| `{name}` |" in text
    assert "| `age` | integer |" in text and "| `rt` | decimal |" in text
    assert "| `date` | date |" in text and "| `gender` | text |" in text
    assert list(draft.variables.columns) == list(VARIABLE_COLUMNS)
    assert len(draft.variables) == 10  # 5 variables in each of the 2 files


def test_what_only_a_person_knows_is_a_placeholder(tmp_path: Path) -> None:
    draft = _draft(_folder(tmp_path, PACKAGE))
    for prompt in (
        "<Give the title of the study or dataset>",
        "<Give the contact e-mail address>",
        "<Describe how the data were collected and processed",
        "<Explain how to reproduce the results",
        "<Describe each variable",
    ):
        assert prompt in draft.text
    assert draft.placeholders == len(_placeholder_lines(draft.text))
    assert draft.placeholders >= 8
    counts = draft.counts
    assert counts["from_package"] == 3  # files, licence and variables
    assert counts["placeholders"] == 4  # description, contact, methods, how to reproduce
    assert counts["kept"] == 0


def test_a_package_without_data_files_has_no_variables_section(tmp_path: Path) -> None:
    draft = _draft(_folder(tmp_path, {"code/run.py": "x = 1\n", "notes.txt": "n\n"}))
    assert _action(draft, "variables") == "not needed"
    assert "## Variables and codebook" not in draft.text
    assert len(draft.variables) == 0
    # and no licence: the licence section is a placeholder that asks for one
    assert _action(draft, "licence") == "added as a placeholder"


def test_an_empty_package_still_gets_a_draft(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    draft = _draft(root)
    assert draft.text.startswith("# <")
    assert draft.placeholders > 0


# -- a package with a README --------------------------------------------------------------

AUTHORS_README = (
    "# Stroop study\n"
    "\n"
    "This dataset holds reaction times of 40 people in an online Stroop task.\n"
    "Two  spaces,\ttabs and   trailing spaces   \n"
    "\n"
    "## Contact\n"
    "\n"
    "Jane Roe, jane.roe@example.org\n"
)


def test_the_authors_text_is_kept_character_for_character(tmp_path: Path) -> None:
    root = _folder(tmp_path, {**PACKAGE, "README.md": AUTHORS_README})
    draft = _draft(root)
    assert draft.readme_path == "README.md"
    assert draft.text.startswith(AUTHORS_README)
    assert draft.text != AUTHORS_README  # the missing sections were added
    assert _action(draft, "description") == "kept"
    assert _action(draft, "contact") == "kept"
    assert _action(draft, "files") == "added from the package"
    assert _action(draft, "methods") == "added as a placeholder"
    # the heading style is the README's own: level-2 Markdown headings
    assert "\n## Files and folders\n" in draft.text
    # the added sections come in the order of the template, after the author's text
    added = [line for line in draft.text[len(AUTHORS_README) :].splitlines() if line[:3] == "## "]
    assert added == [
        "## Files and folders",
        "## Methods",
        "## How to reproduce",
        "## Licence and reuse",
        "## Variables and codebook",
    ]
    # nothing was changed in the package
    assert (root / "README.md").read_bytes() == AUTHORS_README.encode("utf-8")


def test_a_readme_with_every_section_comes_back_unchanged(tmp_path: Path) -> None:
    complete = (
        "# Stroop study\n\nThis package holds the reaction times of 40 people who did an online "
        "Stroop task in 2024, with the code that cleans and analyses them.\n\n"
        "## Contact\n\njane.roe@example.org\n\n"
        "## Files\n\n`data/` holds the data, `code/` the analysis.\n\n"
        "## Methods\n\nParticipants did the task online.\n\n"
        "## How to reproduce\n\nRun `analysis.R`.\n\n"
        "## Licence\n\nCC BY 4.0.\n\n"
        "## Variables\n\nSee the codebook.\n"
    )
    draft = _draft(_folder(tmp_path, {**PACKAGE, "README.md": complete}))
    assert draft.text == complete
    assert set(draft.sections["action"]) == {"kept"}
    assert draft.placeholders == 0
    assert "No placeholders left" in summary_line(draft)


def test_a_readme_without_a_final_newline_gets_one_only_if_something_is_added(
    tmp_path: Path,
) -> None:
    about = (
        "This dataset holds the reaction times of 40 people in an online task, collected in 2024."
    )
    complete = (
        f"# T\n\n{about}\n\n## Contact\n\nme@example.org\n\n## Files\n\nThe files.\n\n"
        "## Methods\n\nM.\n\n## Reproduce\n\nR.\n\n## Licence\n\nL.\n\n## Variables\n\nV."
    )
    same = _draft(_folder(tmp_path, {"README.md": complete, "data/a.csv": "a\n1\n"}))
    assert same.text == complete  # nothing to add: not even a newline
    partial = _draft(
        _folder(tmp_path, {"README.md": f"# T\n\n{about}", "data/a.csv": "a\n1\n"}, "p2")
    )
    assert partial.text.startswith(f"# T\n\n{about}\n\n## Contact and authors\n")
    assert partial.text.endswith("\n") and "\n\n\n" not in partial.text
    # a title with its text is a README with a description: the check agrees about the draft
    assert _action(partial, "description") == "kept"
    shipped = _folder(tmp_path, {"README.md": partial.text, "data/a.csv": "a\n1\n"}, "p3")
    with open_package(shipped) as pkg:
        sections = check_docs(pkg).sections.set_index("id")
    assert bool(sections.loc["description", "found"])


def test_empty_sections_are_filled_under_their_own_headings(tmp_path: Path) -> None:
    readme = (
        "# Stroop study\n\nA dataset of reaction times.\n\n## Files\n\n## Contact\n\n"
        "Jane Roe, jane.roe@example.org\n\n## Variables\n\n"
    )
    draft = _draft(_folder(tmp_path, {**PACKAGE, "README.md": readme}))
    assert _action(draft, "files") == "filled from the package"
    assert _action(draft, "variables") == "filled from the package"
    text = draft.text
    # the author's lines are all still there, in order
    positions = [
        text.index(line)
        for line in (
            "# Stroop study",
            "A dataset of reaction times.",
            "## Files",
            "## Contact",
            "Jane Roe, jane.roe@example.org",
            "## Variables",
        )
    ]
    assert positions == sorted(positions)
    # the file list is under "## Files", before "## Contact"
    assert text.index("The package has 4 files") < text.index("## Contact")
    assert text.index("| `age` |") > text.index("## Variables")
    assert "\n\n\n" not in text
    # and the README check now finds those sections filled
    with open_package(_folder(tmp_path, {**PACKAGE, "README.md": text}, "after")) as pkg:
        sections = check_docs(pkg).sections.set_index("id")
    assert not bool(sections.loc["files", "empty"])
    assert not bool(sections.loc["variables", "empty"])


def test_a_plain_text_readme_gets_headings_of_its_own_kind(tmp_path: Path) -> None:
    readme = (
        "GENERAL INFORMATION\n\n1. Title of Dataset: Reaction times\n2. Author: Jane Roe, "
        "jane.roe@example.org\n\nDATA AND FILE OVERVIEW\n\nThe data folder holds the results.\n"
    )
    root = _folder(tmp_path, {**PACKAGE, "README.txt": readme})
    draft = _draft(root)
    assert draft.readme_path == "README.txt"
    assert draft.text.startswith(readme)
    added = draft.text[len(readme) :]
    assert "# " not in added  # no Markdown headings, which would hide the author's own
    assert "METHODS" in added and "LICENCE AND REUSE" in added
    # the author's sections are all still found in the draft
    after = _folder(tmp_path, {"README.md": draft.text, "data/a.csv": "a\n1\n"}, "after")
    with open_package(after) as pkg:
        found = check_docs(pkg).sections.set_index("id")["found"]
    assert bool(found["description"]) and bool(found["contact"]) and bool(found["files"])
    assert bool(found["methods"]) and bool(found["licence"])


def test_a_readme_that_cannot_be_read_is_not_a_reason_to_fail(tmp_path: Path) -> None:
    draft = _draft(_folder(tmp_path, {**PACKAGE, "README.md": ""}))
    assert draft.readme_path is None
    assert any("is empty" in n for n in draft.notes)
    assert draft.text.startswith("# <")


# -- the template is data ------------------------------------------------------------------

CUSTOM = {
    "name": "Lab template",
    "sections": [
        {
            "id": "overview",
            "title": "Overview of the dataset",
            "prompt": "Summarise the dataset in one paragraph",
            "required": True,
            "match": ["^overview"],
            "fields": [{"id": "pi", "label": "Principal investigator", "match": ["investigator"]}],
        },
        {
            "id": "files",
            "title": "What is where",
            "prompt": "Say what is where",
            "required": True,
            "match": ["what is where"],
        },
        {
            "id": "provenance",
            "title": "Provenance",
            "prompt": "Say where the raw data came from",
            "required": False,
            "match": ["^provenance"],
        },
        {
            "id": "terms",
            "title": "Terms of use",
            "fill": "licence",
            "prompt": "State the terms of use",
            "required": False,
            "match": ["^terms"],
        },
    ],
    "prompts": {
        "title": "Name this dataset",
        "folder": "Say what this folder is for",
        "file": "Say what this file is for",
    },
}


def test_sections_headings_and_prompts_come_from_the_template(tmp_path: Path) -> None:
    root = _folder(tmp_path, {**PACKAGE, "notes.txt": "n\n"})
    draft = _draft(root, readme=CUSTOM)
    text = draft.text
    assert text.startswith("# <Name this dataset>\n")
    assert [line for line in text.splitlines() if line.startswith("## ")] == [
        "## Overview of the dataset",
        "## What is where",
        "## Provenance",
        "## Terms of use",
    ]
    assert "<Summarise the dataset in one paragraph>" in text
    assert "- Principal investigator: <Give the principal investigator>" in text
    assert "<Say where the raw data came from>" in text
    assert "- `code/` (1 file): <Say what this folder is for>" in text
    assert "- `notes.txt`: <Say what this file is for>" in text
    assert "Licence: CC BY. The full text is in `LICENSE`." in text
    assert "Methods" not in text and "Variables" not in text  # not in this template
    assert draft.template_name == "Lab template"
    assert draft.placeholders == len(_placeholder_lines(text, readme=CUSTOM))


def test_a_template_can_be_a_json_file(tmp_path: Path) -> None:
    path = tmp_path / "template.json"
    path.write_text(json.dumps(CUSTOM), encoding="utf-8")
    root = _folder(tmp_path, PACKAGE)
    assert _draft(root, readme=str(path)).text == _draft(root, readme=CUSTOM).text


def test_a_template_with_odd_prompts_is_refused_clearly(tmp_path: Path) -> None:
    root = _folder(tmp_path, PACKAGE)
    with pytest.raises(ValueError, match="prompts"):
        _draft(root, readme={**CUSTOM, "prompts": ["not", "a", "mapping"]})


def test_placeholders_use_the_syntax_the_readme_check_flags(tmp_path: Path) -> None:
    # a template whose prompts start with an HTML tag name would hide the placeholder from the
    # check; the draft writes a form that is flagged instead
    odd = {**CUSTOM, "sections": [{**CUSTOM["sections"][0], "prompt": "p of the study"}]}
    draft = _draft(_folder(tmp_path, PACKAGE), readme=odd)
    assert draft.placeholders == len(_placeholder_lines(draft.text, readme=odd))
    assert draft.placeholders > 0
    assert "<p of the study>" not in draft.text


# -- the licence -----------------------------------------------------------------------------


def test_a_licence_named_in_the_readme_but_without_a_file_asks_for_the_file(tmp_path: Path) -> None:
    readme = "# T\n\nAbout.\n\n## Contact\n\nme@example.org\n\n## Licence\n\nThe data are CC0.\n"
    draft = _draft(_folder(tmp_path, {"README.md": readme, "data/a.csv": "a\n1\n"}))
    assert _action(draft, "licence") == "kept"
    assert "CC0" in draft.text
    # an empty licence heading is filled with the licence the README names elsewhere, and the
    # draft asks for the file with its full text
    empty = _draft(
        _folder(
            tmp_path,
            {"README.md": "# T\n\nCC BY 4.0 data.\n\n## Licence\n\n", "data/a.csv": "a\n1\n"},
            "p2",
        )
    )
    assert _action(empty, "licence") == "filled from the package"
    assert (
        "Licence: CC BY 4.0. <Add a LICENSE file with the full text of the licence>" in empty.text
    )


def test_no_licence_is_a_placeholder_that_asks_for_a_file(tmp_path: Path) -> None:
    draft = _draft(_folder(tmp_path, {"data/a.csv": "a\n1\n"}))
    assert "<State the licence of the data and of the code" in draft.text
    assert _action(draft, "licence") == "added as a placeholder"


def test_an_unrecognised_licence_file_is_named_not_guessed(tmp_path: Path) -> None:
    draft = _draft(
        _folder(tmp_path, {"LICENSE": "All rights reserved by the lab.\n", "d/a.csv": "a\n1\n"})
    )
    assert "`LICENSE`" in draft.text
    assert "CC BY" not in draft.text and "MIT" not in draft.text


# -- tabular files ----------------------------------------------------------------------------


def test_a_header_only_file_is_listed_without_types(tmp_path: Path) -> None:
    draft = _draft(
        _folder(tmp_path, {"data/empty.csv": "a,b,c\n", "data/full.csv": "x,y\n1,2\n3,4\n"})
    )
    text = draft.text
    assert "`data/empty.csv` has no data rows, only a header with 3 variables." in text
    assert "| `a` | not known (no data rows) | 0 |" in text
    assert "`data/full.csv` has 2 rows and 2 variables." in text
    types = {r.variable: r.type for r in draft.variables.itertuples()}
    assert types["a"] == "unknown" and types["x"] == "integer"


def test_variable_types_and_empty_cells(tmp_path: Path) -> None:
    csv = (
        "id,when,score,flag,note,blank\n"
        "1,2024-01-05,1.5,true,hello,\n"
        "2,2024-02-06,2.5,false,,\n"
        "3,,3.0,true,again,\n"
    )
    draft = _draft(_folder(tmp_path, {"data/t.csv": csv}))
    rows = {r.variable: (r.type, r.empty_cells) for r in draft.variables.itertuples()}
    assert rows["id"] == ("integer", 0)
    assert rows["when"] == ("date", 1)
    assert rows["score"] == ("decimal", 0)
    assert rows["note"] == ("text", 1)
    assert rows["blank"][1] == 3
    assert "| `blank` | empty (no values) | 3 |" in draft.text


def test_other_formats_are_read_for_their_variables(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    pyreadstat = pytest.importorskip("pyreadstat")
    import pandas as pd

    root = _folder(tmp_path, {"data/tabs.tsv": "a\tb\n1\tx\n2\ty\n"})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Responses"
    ws.append(["subject", "score"])
    ws.append(["s1", 1.5])
    ws.append(["s2", 2.5])
    wb.create_sheet("Notes").append(["comment"])
    wb.save(root / "data" / "book.xlsx")
    frame = pd.DataFrame({"q1": [1, 2, 3], "q2": [4.5, 5.5, 6.5]})
    pyreadstat.write_sav(frame, str(root / "data" / "spss.sav"))
    pyreadstat.write_dta(frame, str(root / "data" / "stata.dta"))
    draft = _draft(root)
    by_file: dict[str, list[str]] = {}
    for r in draft.variables.itertuples():
        by_file.setdefault(str(r.path), []).append(str(r.variable))
    assert by_file["data/tabs.tsv"] == ["a", "b"]
    assert by_file["data/book.xlsx"] == ["subject", "score", "comment"]
    assert by_file["data/spss.sav"] == ["q1", "q2"]
    assert by_file["data/stata.dta"] == ["q1", "q2"]
    sheets = {str(r.sheet) for r in draft.variables.itertuples() if r.path == "data/book.xlsx"}
    assert sheets == {"Responses", "Notes"}
    assert "`data/book.xlsx` (sheet `Responses`)" in draft.text


def test_a_file_that_cannot_be_read_is_mentioned_not_fatal(tmp_path: Path) -> None:
    draft = _draft(
        _folder(tmp_path, {"data/bad.xlsx": b"this is not a workbook", "data/ok.csv": "a\n1\n"})
    )
    assert "`data/ok.csv`" in draft.text
    assert "The variables of `data/bad.xlsx` are not listed" in draft.text
    assert any("bad.xlsx" in n for n in draft.notes)


def test_the_limits_apply(tmp_path: Path) -> None:
    files = {f"data/f{i:02d}.csv": f"v{i},w\n1,2\n" for i in range(6)}
    draft = _draft(_folder(tmp_path, files), max_files=2)
    assert len(draft.variables) == 4  # 2 files of 2 variables
    assert any("2 of 6" in n or "not read" in n for n in draft.notes)
    rows = _draft(_folder(tmp_path, {"data/long.csv": "a\n" + "1\n" * 50}, "long"), max_rows=10)
    assert "more than 10 rows" in rows.text


# -- odd file names ---------------------------------------------------------------------------


ODD_NAMES = {
    "data/with space.csv": "a,b\n1,2\n",
    "data/ünïcode ✓.csv": "a,b\n1,2\n",
    "data/back`tick.txt": "t\n",
    "data/pipe|name.txt": "t\n",
    "data/[FILENAME].txt": "t\n",
    "data/<draft>.txt": "t\n",
    "TODO notes.txt": "t\n",
    "A.R": "x <- 1\n",
    "b.r": "x <- 2\n",
    "no_extension": "t\n",
    ".hidden": "t\n",
}


def _creatable(name: str) -> bool:
    """Whether a file can have this name here (Windows refuses ``<>:"|?*`` and control characters)."""
    return sys.platform != "win32" or not any(c in '<>:"|?*\n' for c in name)


def test_odd_file_names_are_shown_and_never_become_placeholders(tmp_path: Path) -> None:
    root = _folder(tmp_path, {k: v for k, v in ODD_NAMES.items() if _creatable(k)})
    draft = _draft(root)
    text = draft.text
    assert "ünïcode ✓.csv" in text and "with space.csv" in text
    assert "no extension" in text
    # a name that looks like template text (`[FILENAME].txt`, `<draft>.txt`, `TODO notes.txt`) is
    # not a gap: the only places the README check reports are the ones the draft asks for
    found = _placeholder_lines(text)
    assert len(found) == draft.placeholders
    assert all(re.match(r"<(Describe|Give|State|Explain) ", value) for value in found)
    assert "`TODO notes.txt`: <Describe what this file holds or does>" in text
    assert "[FILENAME].txt" in text
    assert "<draft>.txt" in text or not _creatable("<draft>.txt")
    # R and r are one format, shown with the more common spelling; hidden files are not listed
    assert "`.R` (2 files)" in text
    assert ".hidden" not in text


@pytest.mark.skipif(sys.platform == "win32", reason="Windows file names cannot hold a newline")
def test_a_name_with_a_newline_cannot_break_the_layout(tmp_path: Path) -> None:
    root = _folder(tmp_path, {"data/a\nb.txt": "t\n", "data/ok.csv": "a\n1\n"})
    draft = _draft(root)
    assert "a?b.txt" in draft.text
    assert "a\nb.txt" not in draft.text


def test_a_big_package_has_a_short_tree(tmp_path: Path) -> None:
    files = {f"data/run{i:03d}/trial_{j}.txt": "t\n" for i in range(40) for j in range(3)}
    draft = _draft(_folder(tmp_path, files), max_tree_lines=60)
    tree = draft.text.split("```text\n")[1].split("```")[0]
    assert len(tree.splitlines()) <= 60
    assert "The package has 120 files in 41 folders" in draft.text
    assert "trial_0.txt" not in tree  # the folders are shown with their counts of files
    assert "run000/  (3 files)" in tree


def test_naming_conventions_are_noted_only_when_evident(tmp_path: Path) -> None:
    few = _draft(_folder(tmp_path, {"a.csv": "a\n1\n", "b-c.csv": "a\n1\n"}))
    assert "Words in names" not in few.text and "Names are in" not in few.text
    many = {f"data/sub_{n:02d}_task_one.csv": "a\n1\n" for n in range(1, 8)}
    noted = _draft(_folder(tmp_path, many, "many"))
    assert "Words in names are separated with underscores (my_file)." in noted.text
    assert "Names are in lower case." in noted.text
    mixed = {f"data/Sub{n}-Task Two.csv": "a\n1\n" for n in range(1, 8)}
    unclear = _draft(_folder(tmp_path, mixed, "mixed"))
    assert "Words in names" not in unclear.text and "Names are in" not in unclear.text


# -- nothing is written, nothing leaks ---------------------------------------------------------


def test_the_package_is_not_changed(tmp_path: Path) -> None:
    root = _folder(
        tmp_path, {**PACKAGE, "README.md": AUTHORS_README, "data/odd name.csv": "a\n1\n"}
    )
    before = _snapshot(root)
    draft = _draft(root)
    check_package(root, modules=[MODULE])
    assert _snapshot(root) == before
    assert draft.text.startswith(AUTHORS_README)


SENTINELS = (
    "SENTINEL-4f9a",
    "zxqv-secret-1987",
    "Jan de Vries",
    "06-98765432",
    "5611 ZK",
    "2001:db8:85a3::8a2e:370:7334",
    "987654321",
    "31415926",
    "1987-06-05",
    "3.14159265",
)


def _everything(out: Any) -> str:
    """Every output of the module that can be shown as text."""
    parts = [str(out.summary_text), str(out.traffic_light), repr(out["report"]), out["readme_text"]]
    parts += [str(v) for v in out["files"].values()]
    for key in ("table", "variables", "summary_table"):
        parts.append(out[key].to_csv())
    parts.append(json.dumps(out["notes"]))
    return "\n".join(parts)


def test_no_cell_value_is_in_the_draft_or_any_other_output(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    pyreadstat = pytest.importorskip("pyreadstat")
    import pandas as pd

    csv = "participant_id,contact,phone,postcode,ip,count,when,score\n" + "".join(
        f"{i},{SENTINELS[2]},{SENTINELS[3]},{SENTINELS[4]},{SENTINELS[5]},{SENTINELS[6]},"
        f"{SENTINELS[8]},{SENTINELS[9]}\n"
        for i in range(1, 5)
    )
    root = _folder(
        tmp_path,
        {
            "data/survey.csv": csv,
            "data/tagged.tsv": f"id\tlabel\tvalue\n1\t{SENTINELS[0]}\t{SENTINELS[7]}\n"
            f"2\t{SENTINELS[1]}\t{SENTINELS[7]}\n",
            "README.md": AUTHORS_README,
        },
    )
    wb = openpyxl.Workbook()
    wb.active.append(["id", "who", "amount"])
    wb.active.append([1, SENTINELS[0], int(SENTINELS[7])])
    wb.active.append([2, SENTINELS[1], int(SENTINELS[7])])
    wb.save(root / "data" / "book.xlsx")
    frame = pd.DataFrame(
        {"id": [1, 2], "who": [SENTINELS[0], SENTINELS[1]], "n": [31415926, 31415926]}
    )
    pyreadstat.write_sav(frame, str(root / "data" / "spss.sav"))
    (out,) = check_package(root, modules=[MODULE])
    text = _everything(out)
    for sentinel in SENTINELS:
        assert sentinel not in text, sentinel
    # names, types and counts are there
    assert "| `contact` |" in text and "| `label` |" in text and "| `who` |" in text
    assert "survey.csv" in text


def test_no_value_is_in_the_reports_or_the_json_of_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path, {"data/t.csv": f"id,label\n1,{SENTINELS[0]}\n2,{SENTINELS[1]}\n"})
    for fmt in ("html", "md", "qmd"):
        out_file = tmp_path / f"report.{fmt}"
        report_package(root, out_file, fmt, modules=[MODULE])
        written = out_file.read_text(encoding="utf-8")
        assert "label" in written
        for sentinel in SENTINELS[:2]:
            assert sentinel not in written, (fmt, sentinel)
    assert (
        main(["package", str(root), "-m", MODULE, "--json", "--files-dir", str(tmp_path / "o")])
        == 0
    )
    captured = capsys.readouterr()
    for sentinel in SENTINELS[:2]:
        assert sentinel not in captured.out and sentinel not in captured.err
        assert sentinel not in (tmp_path / "o" / "README.md").read_text(encoding="utf-8")


def test_a_file_without_a_header_row_does_not_leak_its_first_row(tmp_path: Path) -> None:
    first = ["5612 PV", "2a02:aa::f1", "050 321 9876", "Jan de Vries", "2345671"]
    rows = [
        first,
        ["1013 TX", "2a02:aa::f2", "(070) 888 1234", "Anna Smit", "3456782"],
        ["5612 PV", "2a02:aa::f3", "06-11223344", "Piet van der Berg", "4567893"],
    ]
    root = _folder(tmp_path, {"data/raw.csv": "\n".join(",".join(r) for r in rows) + "\n"})
    draft = _draft(root)
    for value in first:
        assert value not in draft.text, value
    assert list(draft.variables["variable"][:3]) == ["column 1", "column 2", "column 3"]


_CELL = st.one_of(
    st.integers(min_value=10_000_000, max_value=99_999_999).map(str),
    st.text(alphabet=string.ascii_letters + " -", min_size=6, max_size=18).map(
        lambda s: "Q" + s.strip().replace("  ", " ") + "Z"
    ),
    st.dates().map(lambda d: d.isoformat()),
    st.floats(min_value=1000, max_value=9999, allow_nan=False).map(lambda f: f"{f:.4f}"),
    st.just(""),
)


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    cells=st.lists(st.lists(_CELL, min_size=3, max_size=3), min_size=1, max_size=6),
    sep=st.sampled_from([",", "\t", ";"]),
)
def test_property_no_cell_of_a_data_file_is_in_the_draft(cells: list[list[str]], sep: str) -> None:
    header = ["participant_id", "measure_a", "measure_b"]
    body = "\n".join(sep.join(row) for row in cells)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        (root / "data").mkdir(parents=True)
        ext = "tsv" if sep == "\t" else "csv"
        (root / "data" / f"values.{ext}").write_text(
            sep.join(header) + "\n" + body + "\n", encoding="utf-8"
        )
        draft = _draft(root)
        report = repr(readme_report(draft))
    shown = draft.text + "\n" + report + "\n" + draft.variables.to_csv() + draft.sections.to_csv()
    for row in cells:
        for value in row:
            if len(value) >= 6 and value not in ("participant_id", "measure_a", "measure_b"):
                assert value not in shown, value


# -- the README check on the draft ---------------------------------------------------------------


def test_the_readme_check_reports_every_placeholder_of_the_draft(tmp_path: Path) -> None:
    root = _folder(tmp_path, {**PACKAGE, "README.md": AUTHORS_README})
    draft = _draft(root)
    shipped = _folder(tmp_path, {**PACKAGE, "README.md": draft.text}, "shipped")
    with open_package(shipped) as pkg:
        result = check_docs(pkg)
    findings = result.findings
    template_text = findings.loc[findings["rule"] == "template-text"]
    assert _reported(result) == draft.placeholders > 0
    # and each placeholder of the draft is in one of the findings (the check lists the first ones)
    details = " ".join(str(d) for d in template_text["detail"])
    for value in _placeholder_lines(draft.text)[: len(template_text) - 1]:
        assert value in details
    # the file list is covered, the required sections are there
    checklist = result.checklist.set_index("item")["status"]
    assert checklist["readme_file_list"] == "pass"
    assert checklist["readme_sections"] == "pass"


def test_a_complete_draft_has_no_placeholder_finding(tmp_path: Path) -> None:
    root = _folder(tmp_path, {**PACKAGE, "README.md": AUTHORS_README})
    draft = _draft(root)
    filled = draft.text
    for value in _placeholder_lines(filled):
        filled = filled.replace(value, "Written by a person")
    shipped = _folder(tmp_path, {**PACKAGE, "README.md": filled}, "shipped")
    with open_package(shipped) as pkg:
        findings = check_docs(pkg).findings
    assert "template-text" not in set(findings["rule"])


def test_drafting_again_from_a_draft_changes_nothing(tmp_path: Path) -> None:
    first = _draft(_folder(tmp_path, PACKAGE))
    again = _draft(_folder(tmp_path, {**PACKAGE, "README.md": first.text}, "second"))
    assert again.text == first.text
    assert again.placeholders == first.placeholders


# -- the module and the file it hands back ---------------------------------------------------------


def test_the_module_is_not_in_the_default_preset_but_can_be_selected() -> None:
    assert "datapackage::package_readme" not in package_selection().modules
    assert package_selection(modules=[MODULE]).modules == [MODULE]
    assert package_selection(modules=["package_readme"]).modules == ["package_readme"]


def test_the_module_hands_back_the_draft_as_a_file(tmp_path: Path) -> None:
    root = _folder(tmp_path, {**PACKAGE, "README.md": AUTHORS_README})
    chain = check_package(root, modules=[MODULE])
    (out,) = chain
    assert out.title == "Data Package README Draft"
    assert out.traffic_light == "info"
    assert out["files"] == {"README.md": out["readme_text"]}
    assert chain.files == {"README.md": out["readme_text"]}
    assert out["readme_path"] == "README.md"
    assert out["readme_text"].startswith(AUTHORS_README)
    assert list(out.table.columns) == list(DRAFT_COLUMNS)
    assert out["placeholders"] == len(_placeholder_lines(out["readme_text"]))
    assert "Drafted README.md from README.md" in out.summary_text
    row = out.summary_table  # no paper ids for a stand-in paper: only the shape is checked
    assert {"sections_kept", "placeholders"} <= set(row.columns)


def test_a_report_has_the_files_too(tmp_path: Path) -> None:
    root = _folder(tmp_path, PACKAGE)
    report = report_package(root, tmp_path / "r.md", "md", modules=[MODULE])
    assert report.files["README.md"].startswith("# <")
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert "README draft" in text and "The package has 4 files" in text
    assert "<Describe" in text  # the draft is shown in a code block, so not read as a tag


def test_the_module_in_an_archive(tmp_path: Path) -> None:
    archive = tmp_path / "study.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("study/data/survey.csv", SURVEY)
        zf.writestr("study/README.md", AUTHORS_README)
    (out,) = check_package(archive, modules=[MODULE], offline=True)
    assert out["readme_path"] == "README.md"
    assert "survey.csv" in out["files"]["README.md"]


def test_the_module_without_a_package(tmp_path: Path) -> None:
    from metacheck.module import module_run
    from metacheck.papers.model import Paper

    del Paper  # a paper is not needed: no local_path gives the standard "no package" output
    out = module_run(None, "datapackage::package_readme")
    assert out.traffic_light == "na"
    assert module_files([out]) == {}


def test_module_options(tmp_path: Path) -> None:
    root = _folder(tmp_path, PACKAGE)
    (out,) = check_package(root, modules=[MODULE], args={MODULE: {"readme": CUSTOM}})
    assert out["readme_text"].startswith("# <Name this dataset>")
    (small,) = check_package(root, modules=[MODULE], args={MODULE: {"max_files": 1}})
    assert len(small["variables"]) == 5


# -- the command -----------------------------------------------------------------------------------


def test_the_command_writes_the_draft_into_a_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path, {**PACKAGE, "README.md": AUTHORS_README})
    before = _snapshot(root)
    target = tmp_path / "drafts" / "one"
    assert main(["package", str(root), "-m", MODULE, "--files-dir", str(target)]) == 0
    written = (target / "README.md").read_text(encoding="utf-8")
    assert written.startswith(AUTHORS_README) and "## Methods" in written
    assert "README.md" in capsys.readouterr().err  # the path of the file is printed
    assert [p.name for p in target.iterdir()] == ["README.md"]
    assert _snapshot(root) == before


def test_the_command_refuses_the_package_folder_and_what_is_inside_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path, PACKAGE)
    before = _snapshot(root)
    for where in (root, root / "data", root / "new_folder", root / "data" / ".." / "x"):
        code = main(["package", str(root), "-m", MODULE, "--files-dir", str(where)])
        assert code == 2, where
        err = capsys.readouterr().err
        assert "never changed" in err
        # it stopped before anything ran: no module was announced
        assert "README Draft" not in err
    assert _snapshot(root) == before


def test_without_a_files_dir_nothing_is_written(tmp_path: Path) -> None:
    root = _folder(tmp_path, PACKAGE)
    assert main(["package", str(root), "-m", MODULE]) == 0
    assert sorted(p.name for p in tmp_path.iterdir() if p.name != "config.json") == ["pkg"]
    assert (tmp_path / "pkg").exists()


def test_files_dir_works_for_an_archive(tmp_path: Path) -> None:
    archive = tmp_path / "study.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("study/data/survey.csv", SURVEY)
    assert main(["package", str(archive), "-m", MODULE, "--files-dir", str(tmp_path / "out")]) == 0
    assert "survey.csv" in (tmp_path / "out" / "README.md").read_text(encoding="utf-8")
    # next to the archive is fine, and the draft is not written into the archive's folder by accident
    assert not list(tmp_path.glob("README*"))


# -- the summary and the report -----------------------------------------------------------------------


def test_the_summary_line_says_what_was_kept_and_what_is_left(tmp_path: Path) -> None:
    draft = _draft(_folder(tmp_path, {**PACKAGE, "README.md": AUTHORS_README}))
    line = summary_line(draft)
    assert line.startswith("Drafted README.md from README.md.")
    assert "2 kept" in line and "placeholder" in line
    assert f"{draft.placeholders} places still to fill in" in line
    fresh = summary_line(_draft(_folder(tmp_path, PACKAGE, "fresh")))
    assert "from the template" in fresh


def test_the_report_shows_the_sections_and_the_draft(tmp_path: Path) -> None:
    draft = _draft(_folder(tmp_path, {**PACKAGE, "README.md": AUTHORS_README}))
    blocks = readme_report(draft)
    text = "\n".join(str(b) for b in blocks)
    assert "README draft" in text and "kept as it is" in text
    assert "````markdown" in text  # a longer fence, as the draft has fences of its own
    assert AUTHORS_README.splitlines()[0] in text


def test_a_random_package_always_gives_a_draft_the_check_accepts(tmp_path: Path) -> None:
    rng = random.Random(7)
    pool = ["data", "code", "docs", "materials", "output", "raw data", "Ünï", "x y"]
    ext = [".csv", ".R", ".py", ".txt", ".md", ".json", "", ".xlsx", ".pdf", ".tsv"]
    for n in range(6):
        files: dict[str, str | bytes] = {}
        for _ in range(rng.randint(0, 25)):
            depth = rng.randint(0, 3)
            parts = [rng.choice(pool) for _ in range(depth)]
            name = rng.choice(["a", "b", "run 1", "TODO", "[DATE]", "x_y-z"]) + rng.choice(ext)
            files["/".join([*parts, name])] = "a,b\n1,2\n" if name.endswith(".csv") else "text\n"
        root = _folder(tmp_path, files, f"rand{n}")
        before = _snapshot(root)
        draft = _draft(root)
        assert _snapshot(root) == before
        assert draft.text.endswith("\n") and "\n\n\n" not in draft.text
        assert draft.placeholders == len(_placeholder_lines(draft.text))
        with open_package(
            _folder(tmp_path, {**files, "README.md": draft.text}, f"randshipped{n}")
        ) as pkg:
            result = check_docs(pkg)
        assert _reported(result) == draft.placeholders


def test_the_copy_of_a_package_with_the_draft_in_place_still_checks(tmp_path: Path) -> None:
    root = _folder(tmp_path, PACKAGE)
    copy = tmp_path / "copy"
    shutil.copytree(root, copy)
    (out,) = check_package(root, modules=[MODULE])
    (copy / "README.md").write_text(out["files"]["README.md"], encoding="utf-8")
    (checked,) = check_package(copy, modules=["datapackage::package_docs"])
    assert "template-text" in set(checked.table["rule"])
