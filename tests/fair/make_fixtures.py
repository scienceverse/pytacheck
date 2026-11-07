"""Write the fixture folders of the FAIR_repo parity cases to ``tests/fair/fixtures/<name>/``.

Run ``python tests/fair/make_fixtures.py`` from the repository root. The folders are real files
in git (small), and the generator is deterministic: running it twice gives the same bytes, so a
change shows up in a diff. Each folder is a small research folder like ``fair_fixture()`` of
metacheck's ``test-fair-repo.R`` (``basic``) or ``basic`` plus the files one scenario of that
test file needs. The R and Python parity helpers (``parity_helpers.R``, ``parity_helpers.py``)
copy a fixture to a fresh temporary folder named like it and run ``FAIR_repo()`` on the copy, so
R and Python read the same files.

Not here: ``empty`` (git cannot hold an empty folder: the helpers create it) and the demo
repository (``demofile("repo")``).

``.xlsx``, ``.zip``, ``.rds``, ``.dta`` and ``.sav`` files are written without R: ``openpyxl``,
``zipfile`` (fixed time stamps), ``tests/fair/rds_writer.py``, pandas and ``pyreadstat``, each
with the time stamps in the file header fixed.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import re
import shutil
import struct
import sys
import zipfile
import zlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.fair import rds_writer

FIXTURES = ROOT / "tests" / "fair" / "fixtures"
BOM = b"\xef\xbb\xbf"
STAMP = (2024, 1, 1, 0, 0, 0)

# -- writing files ------------------------------------------------------------------------


def put(path: Path, content: str | bytes) -> Path:
    """Write a file (text as UTF-8, bytes as they are), making its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def lines(path: Path, rows: Sequence[str]) -> Path:
    """``writeLines(rows, path)``: UTF-8, ``\\n`` after every line."""
    return put(path, "".join(f"{row}\n" for row in rows))


def csv(path: Path, columns: Mapping[str, Sequence[Any]]) -> Path:
    """``write.csv(data.frame(...), path, row.names = FALSE)``: quoted header, plain numbers."""

    def cell(v: Any) -> str:
        if v is None:
            return "NA"
        if isinstance(v, str):
            return '"' + v.replace('"', '""') + '"'
        if isinstance(v, float) and v == int(v):
            return str(int(v))
        return str(v)

    names = list(columns)
    n = len(columns[names[0]])
    rows = [",".join(f'"{c}"' for c in names)]
    rows += [",".join(cell(columns[c][i]) for c in names) for i in range(n)]
    return lines(path, rows)


def _fixed_zip(data: bytes, fixes: Callable[[str, bytes], bytes] | None = None) -> bytes:
    """A zip archive rewritten with fixed time stamps (and, per entry, fixed content)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            content = src.read(info.filename)
            if fixes is not None:
                content = fixes(info.filename, content)
            new = zipfile.ZipInfo(info.filename, date_time=STAMP)
            new.compress_type = zipfile.ZIP_DEFLATED
            new.create_system = 3
            new.external_attr = info.external_attr
            dst.writestr(new, content)
    return out.getvalue()


def _core_props(name: str, content: bytes) -> bytes:
    if name != "docProps/core.xml":
        return content
    text = content.decode("utf-8")
    text = re.sub(
        r"(<dcterms:(?:created|modified)[^>]*>)[^<]*",
        r"\g<1>2024-01-01T00:00:00Z",
        text,
    )
    return text.encode("utf-8")


def xlsx(path: Path, sheets: Mapping[str, Mapping[str, Sequence[Any]]]) -> Path:
    """An Excel workbook, one sheet per entry (``{sheet: {column: values}}``), header in row 1."""
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, columns in sheets.items():
        ws = wb.create_sheet(name)
        names = list(columns)
        ws.append(names)
        for i in range(len(columns[names[0]])):
            ws.append([columns[c][i] for c in names])
    wb.properties.creator = "fixtures"
    wb.properties.created = dt.datetime(2024, 1, 1)
    wb.properties.modified = dt.datetime(2024, 1, 1)
    buf = io.BytesIO()
    wb.save(buf)
    return put(path, _fixed_zip(buf.getvalue(), _core_props))


def zip_folder(zip_path: Path, folder: Path) -> Path:
    """``zip::zip(zip_path, files = <folder name>, root = <its parent>)`` with fixed time stamps."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        top = zipfile.ZipInfo(f"{folder.name}/", date_time=STAMP)
        top.create_system = 3
        top.external_attr = (0o40755 << 16) | 0x10
        zf.writestr(top, b"")
        for p in sorted(folder.rglob("*")):
            rel = f"{folder.name}/{p.relative_to(folder).as_posix()}"
            info = zipfile.ZipInfo(rel + ("/" if p.is_dir() else ""), date_time=STAMP)
            info.create_system = 3
            if p.is_dir():
                info.external_attr = (0o40755 << 16) | 0x10
                zf.writestr(info, b"")
            else:
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zf.writestr(info, p.read_bytes())
    return put(zip_path, out.getvalue())


def png(path: Path) -> Path:
    """A 1 x 1 pixel PNG image."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\x4c\x72\xa6", 9)
    data = (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixel) + chunk(b"IEND", b"")
    )
    return put(path, data)


def dta(path: Path) -> Path:
    """A Stata data file (``panel.dta``) with a fixed time stamp."""
    import pandas as pd

    df = pd.DataFrame({"id": [1, 2, 3, 4], "wave": [1, 1, 2, 2], "score": [3.5, 4.0, 2.5, 3.0]})
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_stata(path, write_index=False, version=118, time_stamp=dt.datetime(2024, 1, 1))
    return path


def sav(path: Path) -> Path:
    """An SPSS data file (``trial.sav``); the creation date and time of its header are fixed."""
    import pandas as pd
    import pyreadstat

    df = pd.DataFrame({"id": [1.0, 2.0, 3.0], "arm": [0.0, 1.0, 1.0], "outcome": [10.5, 12.0, 9.5]})
    path.parent.mkdir(parents=True, exist_ok=True)
    pyreadstat.write_sav(
        df,
        str(path),
        column_labels=["participant", "study arm", "outcome measure"],
    )
    data = bytearray(path.read_bytes())
    # record type (4) + product name (60) + five int32 + bias (8): the date (9) and time (8)
    data[92:101] = b"01 Jan 24"
    data[101:109] = b"00:00:00"
    path.write_bytes(bytes(data))
    return path


# -- the folders --------------------------------------------------------------------------

ANALYSIS_R = [
    'setwd("C:/Users/me/project")',
    'd <- read.csv("../data/scores.csv")',
    'e <- read.csv("old_scores.csv")',
    "library(dplyr)",
]


def basic(d: Path, readme: bool = True) -> Path:
    """``fair_fixture()``: data, code, a README and a codebook."""
    csv(d / "data" / "scores.csv", {"id": [1, 2, 3, 4, 5], "score": [2.1, 3.4, 1.9, 4.0, 2.8]})
    # a byte order mark, which the copy drops
    put(d / "data" / "bom.csv", BOM + b"id,x\n1,2\n")
    if readme:
        lines(d / "README.txt", ["A readme."])
    lines(d / "Code" / "analysis.R", ANALYSIS_R)
    lines(d / "codebook.csv", ["var,description", "id,participant id", "score,outcome measure"])
    return d


def harvest_readme_sheet() -> dict[str, list[Any]]:
    return {"about": ["Harvest data", "see sheet 2"]}


def plants_sheet() -> dict[str, list[Any]]:
    return {"pid": [1, 2, 3, 4, 5, 6], "weight": [2.5, 3.1, 2.8, 4, 3, 2]}


def soil_sheet() -> dict[str, list[Any]]:
    return {"sid": [1, 2, 3, 4, 5, 6], "ph": [6.5, 7.1, 6.8, 7, 6.2, 6.9]}


def f_basic(d: Path) -> None:
    basic(d)


def f_readme_title(d: Path) -> None:
    basic(d)
    lines(d / "README.txt", ["# Water quality at the festival", "", "Some text."])


def f_readme_latin1(d: Path) -> None:
    basic(d)
    # an older Mac encoding: "\xd4" and "\xd5" are curly quotes
    put(d / "README.txt", b"# Snow removal\nLabelled \xd4frosted stalks\xd5.\n")


def f_latin1_column(d: Path) -> None:
    basic(d)
    # "PPFD (µmol)" with the µ as the Latin-1 byte b5
    put(d / "data" / "light.csv", b"PPFD (\xb5mol),x\n1,2\n3,4\n")


def f_studies(d: Path) -> None:
    for s in ("study1", "study2"):
        csv(d / s / f"{s}_scores.csv", {"id": [1, 2, 3], "x": [1.5, 2.5, 3.5]})


def f_sheets(d: Path) -> None:
    basic(d)
    # the first sheet is a README; the data are on the second
    xlsx(
        d / "data" / "harvest.xlsx",
        {
            "README": harvest_readme_sheet(),
            "data sheet": {"plant": [1, 2, 3], "weight": [2.5, 3.1, 2.8]},
        },
    )


def f_sheets_two(d: Path) -> None:
    basic(d)
    xlsx(d / "data" / "harvest.xlsx", {"plants": plants_sheet(), "soil": soil_sheet()})


def f_sheets_top(d: Path) -> None:
    xlsx(d / "harvest.xlsx", {"README": harvest_readme_sheet(), "plants": plants_sheet()})


def f_xlsx_single(d: Path) -> None:
    basic(d)
    xlsx(d / "data" / "survey.xlsx", {"Sheet1": {"id": [1, 2, 3], "cond": ["a", "b", "a"]}})


def f_dup_names(d: Path) -> None:
    basic(d)
    # x.csv and a one-sheet x.xlsx in one folder both become study-x_data.csv
    lines(d / "data" / "x.csv", ["a,b", "1,2"])
    xlsx(d / "data" / "x.xlsx", {"Sheet1": {"a": [3], "b": [4]}})
    # files with one name in different folders stay apart in their folders
    csv(d / "extra" / "scores.csv", {"id": [9]})


def f_text_bagit(d: Path) -> None:
    basic(d)
    lines(d / "data" / "scores_tab.txt", ["id\tscore", "1\t2", "2\t3"])
    lines(d / "bagit.txt", ["BagIt-Version: 0.97"])
    lines(d / "manifest-md5.txt", ["0123abc  data/scores.csv"])


def f_rds_tables(d: Path) -> None:
    basic(d)
    # a list is no table (and no model)
    rds_writer.write_list_rds(d / "data" / "lookup.rds")
    # two different R tables with one name in one folder of the copy
    rds_writer.write_table_rds(d / "data" / "a" / "tab.rds", {"x": [1, 2, 3]})
    rds_writer.write_table_rds(d / "data" / "a" / "b" / "tab.rds", {"x": [4, 5, 6]})


def f_rds_model(d: Path) -> None:
    basic(d)
    lines(
        d / "Code" / "summarise.R",
        ['d <- read.csv("../data/scores.csv")', 'write.csv(summary(d), "results_table.csv")'],
    )
    # the results the script wrote, shared next to the code
    lines(d / "Code" / "results_table.csv", ["x,y", "1,2"])
    rds_writer.write_model_rds(d / "data" / "fit.rds")
    # an R table with a CSV of the same name next to it is not converted again
    rds_writer.write_table_rds(d / "data" / "scores.rds", {"id": [1, 2, 3, 4, 5]})


def f_zipped(d: Path) -> None:
    basic(d)
    # the folder holds a zip of its own data folder, next to that folder
    zip_folder(d / "data.zip", d / "data")


def f_readmes(d: Path) -> None:
    basic(d)
    lines(d / "nest-1" / "README", ["About the files in nest-1."])
    lines(d / "copy of top" / "README.txt", ["A readme."])


def f_scenarios(d: Path) -> None:
    basic(d)
    for s, v in (("scenario_lb", [1, 2]), ("scenario_ub", [3, 4])):
        csv(d / "data" / s / "forcing.csv", {"t": [1, 2], "v": v})
        # identical in both scenarios, and still kept in both
        lines(d / "data" / s / "soil.csv", ["a,b", "1,2"])


def f_code_only(d: Path) -> None:
    lines(d / "analysis.R", ["x <- 1"])


def f_py_requirements(d: Path) -> None:
    basic(d, readme=False)
    lines(
        d / "Code" / "model.py",
        [
            "# ruff: noqa",
            "import os",
            "import json",
            "import pandas as pd",
            "from sklearn.linear_model import LinearRegression",
        ],
    )


def f_own_requirements(d: Path) -> None:
    f_py_requirements(d)
    lines(d / "requirements.txt", ["pandas==2.2.0", "scikit-learn"])


def f_setwd_variants(d: Path) -> None:
    basic(d)
    lines(
        d / "Code" / "folders.R",
        [
            "old <- getwd()",
            "setwd(tempdir())",
            "setwd(old)",
            "setwd(dirname(rstudioapi::getActiveDocumentContext()$path))",
            'msg <- "a line that says',
            "setwd('here') in a text\"",
            '  setwd("results")',
        ],
    )


def f_write_read(d: Path) -> None:
    basic(d)
    csv(d / "data" / "clean.csv", {"id": [1, 2, 3], "y": [1.0, 2.0, 3.0]})
    lines(
        d / "Code" / "a_clean.R",
        [
            'x <- read.csv("data/scores.csv")',
            'write.csv(x, "data/clean.csv", row.names = FALSE)',
        ],
    )
    lines(d / "Code" / "b_model.R", ['y <- read.csv("data/clean.csv")'])


def f_readme_links(d: Path) -> None:
    basic(d, readme=False)
    png(d / "man" / "figures" / "fig1.png")
    lines(
        d / "README.md",
        [
            "# My study",
            "![a figure](man/figures/fig1.png)",
            '<img src="./man/figures/fig1.png" width="100%" />',
            "[the site](https://example.org/man/figures/fig1.png)",
            "[a part](#results)",
        ],
    )


def rmd_page(path: Path) -> Path:
    """A rendered R Markdown page: code_check recovers its code as a script."""
    return lines(
        path,
        [
            '<html><head><meta name="generator" content="pandoc" /></head><body>',
            '<pre class="r"><code>x &lt;- read.csv(&quot;data/scores.csv&quot;)',
            "summary(x)</code></pre>",
            "</body></html>",
        ],
    )


def f_rendered_page(d: Path) -> None:
    basic(d)
    rmd_page(d / "docs" / "report.html")


def f_rendered_page_with_rmd(d: Path) -> None:
    f_rendered_page(d)
    # not when the folder has the document the page was made from
    lines(d / "docs" / "report.Rmd", ["```{r}", "x <- 1", "```"])


def f_nested_top(d: Path) -> None:
    # a repository downloaded from GitHub sits in "<name>-<commit>/"
    basic(d / "repo-1a2b3c4")


def f_crlf_script(d: Path) -> None:
    basic(d)
    put(d / "Code" / "helper.R", b"x <- 1\r\ny <- 2\r\n")


def f_script_kinds(d: Path) -> None:
    basic(d)
    lines(
        d / "Code" / "report.Rmd",
        [
            "---",
            "title: An R Markdown report",
            "---",
            "",
            "```{r}",
            'scores <- read.csv("data/scores.csv")',
            "summary(scores)",
            "```",
        ],
    )
    lines(
        d / "Code" / "slides.qmd",
        [
            "---",
            "title: Quarto slides",
            "---",
            "",
            "```{r}",
            'scores <- read.csv("data/scores.csv")',
            "mean(scores$score)",
            "```",
        ],
    )
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "import os\n",
                    "import pandas as pd\n",
                    "df = pd.read_csv('data/scores.csv')\n",
                    "df.describe()",
                ],
            }
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    put(d / "Code" / "explore.ipynb", json.dumps(notebook, indent=1) + "\n")
    lines(
        d / "Code" / "model.py",
        [
            "import os",
            "",
            "import pandas as pd",
            "",
            'os.chdir("/home/me/project")',
            'df = pd.read_csv("data/scores.csv")',
            "print(df.describe())",
        ],
    )
    lines(
        d / "Code" / "prepare.do",
        [
            'cd "C:/Users/me/project"',
            'import delimited "data/scores.csv", clear',
            "summarize score",
        ],
    )
    lines(
        d / "Code" / "plot.m",
        [
            "cd('C:/Users/me/project');",
            "t = readtable('data/scores.csv');",
            "plot(t.id, t.score);",
        ],
    )


def f_multi_ext(d: Path) -> None:
    basic(d)
    xlsx(d / "data" / "survey.xlsx", {"Sheet1": {"id": [1, 2, 3], "cond": ["a", "b", "a"]}})
    dta(d / "data" / "panel.dta")
    sav(d / "data" / "trial.sav")


def f_licensed(d: Path) -> None:
    basic(d)
    lines(d / "LICENSE", ["MIT License", "", "Copyright (c) 2024 The authors"])


def f_review_files(d: Path) -> None:
    """Files the review cases read: a script, and READMEs with the different kinds of title."""
    lines(d / "a.R", ["x <- mutate(df, y = 1)"])
    lines(d / "README.md", ["Snow removal", "============", "", "Text."])
    lines(d / "README.Rmd", ["---", 'title: "A *quoted* title"', "---", "", "Text."])
    lines(d / "README.qmd", ["---", "title: 'Single quoted'", "---"])
    lines(d / "README", ["This readme.text file was generated on 2024-01-01 by me"])
    lines(d / "README.markdown", ["#  A heading  with spaces", "", "Text."])
    lines(d / "notes.txt", ["# not a README", "Text."])
    lines(d / "sub" / "README.md", ["# A subfolder title"])


FIXTURE_BUILDERS: dict[str, Callable[[Path], None]] = {
    "basic": f_basic,
    "readme_title": f_readme_title,
    "readme_latin1": f_readme_latin1,
    "latin1_column": f_latin1_column,
    "studies": f_studies,
    "sheets": f_sheets,
    "sheets_two": f_sheets_two,
    "sheets_top": f_sheets_top,
    "xlsx_single": f_xlsx_single,
    "dup_names": f_dup_names,
    "text_bagit": f_text_bagit,
    "rds_tables": f_rds_tables,
    "rds_model": f_rds_model,
    "zipped": f_zipped,
    "readmes": f_readmes,
    "scenarios": f_scenarios,
    "code_only": f_code_only,
    "py_requirements": f_py_requirements,
    "own_requirements": f_own_requirements,
    "setwd_variants": f_setwd_variants,
    "write_read": f_write_read,
    "readme_links": f_readme_links,
    "rendered_page": f_rendered_page,
    "rendered_page_with_rmd": f_rendered_page_with_rmd,
    "nested_top": f_nested_top,
    "crlf_script": f_crlf_script,
    "script_kinds": f_script_kinds,
    "multi_ext": f_multi_ext,
    "licensed": f_licensed,
    "review_files": f_review_files,
}


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, build in FIXTURE_BUILDERS.items():
        folder = FIXTURES / name
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        build(folder)
    stale = sorted(p.name for p in FIXTURES.iterdir() if p.name not in FIXTURE_BUILDERS)
    if stale:
        print("folders this generator does not write:", ", ".join(stale))
    print(f"wrote {len(FIXTURE_BUILDERS)} fixtures to {FIXTURES.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
