"""Helpers of the FAIR_repo tests: the fixture folder, small file writers and readers.

Ports of the helpers at the top of ``tests/testthat/test-fair-repo.R`` (``fair_fixture()``,
``crate_ids()``, ``fair_paper()``, ``rmd_page()``) and of the R functions the tests use to build
files (``write.csv``, ``openxlsx``, ``zip::zip``, ``tools::md5sum``).
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

BOM = b"\xef\xbb\xbf"


# -- writing files ------------------------------------------------------------------------


def write_lines(path: Path, lines: Sequence[str]) -> Path:
    """``writeLines(lines, path)``: UTF-8, ``\\n`` after every line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes("".join(f"{line}\n" for line in lines).encode("utf-8"))
    return path


def _cell(v: Any) -> str:
    if v is None:
        return "NA"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return f"{v:.15g}"
    if isinstance(v, str):
        return '"' + v.replace('"', '""') + '"'
    return str(v)


def write_csv(path: Path, columns: Mapping[str, Sequence[Any]]) -> Path:
    """``utils::write.csv(data.frame(...), path, row.names = FALSE)``: quoted header and strings."""
    names = list(columns)
    n = len(columns[names[0]]) if names else 0
    lines = [",".join(f'"{c}"' for c in names)]
    lines += [",".join(_cell(columns[c][i]) for c in names) for i in range(n)]
    return write_lines(path, lines)


def write_xlsx(path: Path, sheets: Mapping[str, Mapping[str, Sequence[Any]]]) -> Path:
    """An Excel workbook with one sheet per entry: ``{sheet name: {column: values}}``."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, columns in sheets.items():
        ws = wb.create_sheet(name)
        names = list(columns)
        ws.append(names)
        for i in range(len(columns[names[0]])):
            ws.append([columns[c][i] for c in names])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def zip_folder(zip_path: Path, folder: str, root: Path) -> Path:
    """``zip::zip(zip_path, files = folder, root = root)``: the folder and everything in it."""
    top = root / folder
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(top, arcname=f"{folder}/")
        for p in sorted(top.rglob("*")):
            rel = f"{folder}/{p.relative_to(top).as_posix()}"
            zf.write(p, arcname=rel + "/" if p.is_dir() else rel)
    return zip_path


def rmd_page(path: Path) -> Path:
    """A rendered R Markdown page: code_check recovers its code as a script."""
    return write_lines(
        path,
        [
            '<html><head><meta name="generator" content="pandoc" /></head><body>',
            '<pre class="r"><code>x &lt;- read.csv(&quot;data/scores.csv&quot;)',
            "summary(x)</code></pre>",
            "</body></html>",
        ],
    )


def fair_fixture(d: Path) -> Path:
    """``fair_fixture()``: a small folder (data, code, a README, a codebook) written to *d*."""
    (d / "data").mkdir(parents=True)
    (d / "Code").mkdir()
    write_csv(
        d / "data" / "scores.csv",
        {"id": [1, 2, 3, 4, 5], "score": [2.1, 3.4, 1.9, 4.0, 2.8]},
    )
    # a byte order mark, which the copy drops
    (d / "data" / "bom.csv").write_bytes(BOM + b"id,x\n1,2\n")
    write_lines(d / "README.txt", ["A readme."])
    write_lines(
        d / "Code" / "analysis.R",
        [
            'setwd("C:/Users/me/project")',
            'd <- read.csv("../data/scores.csv")',
            'e <- read.csv("old_scores.csv")',
            "library(dplyr)",
        ],
    )
    write_lines(
        d / "codebook.csv",
        ["var,description", "id,participant id", "score,outcome measure"],
    )
    return d


def fair_paper() -> Any:
    """``fair_paper()``: a test paper with a title, a DOI and two authors with CRediT roles."""
    from metacheck.papers.io import test_paper

    p = test_paper(["x"])
    p.info["title"] = pd.Series(["A test paper"], dtype="string")
    p.info["doi"] = pd.Series(["10.1234/abc"], dtype="string")
    author = pd.DataFrame(
        {
            "author_id": pd.Series([1, 2], dtype="Int64"),
            "given": pd.Series(["Ann", "Bob"], dtype="string"),
            "family": pd.Series(["Smith", "Jones"], dtype="string"),
            "email": pd.Series(["ann@x.org", None], dtype="string"),
            "corresponding": pd.Series([True, False], dtype="boolean"),
            "orcid": pd.Series(["0000-0001-2345-6789", None], dtype="string"),
            "affiliation": pd.Series(["TU/e", None], dtype="string"),
        }
    )
    author["credit_roles"] = pd.Series(
        [["Conceptualization", "Writing - original draft"], []], dtype=object
    )
    p.author = author
    return p


# -- reading files ------------------------------------------------------------------------


def read_lines(path: Path) -> list[str]:
    """``readLines(path)``."""
    return path.read_text(encoding="utf-8").splitlines()


def read_json(path: Path) -> Any:
    """``jsonlite::fromJSON(path, simplifyVector = FALSE)``."""
    return json.loads(path.read_text(encoding="utf-8"))


def md5(path: Path) -> str:
    """``tools::md5sum(path)``."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def md5_tree(d: Path) -> dict[str, str]:
    """``tools::md5sum(list.files(d, recursive = TRUE, full.names = TRUE))``, by relative path."""
    return {p.relative_to(d).as_posix(): md5(p) for p in sorted(d.rglob("*")) if p.is_file()}


def tree_state(d: Path) -> dict[str, tuple[int, int, str | None]]:
    """Every path in *d* (hidden ones and folders too): size, modification time, checksum."""
    out: dict[str, tuple[int, int, str | None]] = {}
    for p in sorted(d.rglob("*")):
        st = p.stat()
        out[p.relative_to(d).as_posix()] = (
            st.st_size,
            st.st_mtime_ns,
            None if p.is_dir() else md5(p),
        )
    return out


def files_in(d: Path) -> list[str]:
    """``list.files(d, recursive = TRUE)``: the relative paths of the files."""
    return sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file())


def names_in(d: Path) -> list[str]:
    """``list.files(d)``: the names in one folder."""
    return sorted(os.listdir(d))


def rds_column(path: Path, name: str) -> list[Any]:
    """``readRDS(path)[[name]]`` for a data frame (or list) written by ``tests.fair.rds_writer``."""
    from metacheck.datacheck._files_rdata import read_rds

    obj = read_rds(path)
    names = obj.attrs["names"].value
    return list(obj.value[names.index(name)].value.tolist())


# -- the RO-Crate -------------------------------------------------------------------------


def graph(crate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``crate[["@graph"]]``."""
    return list(crate["@graph"])


def by_id(g: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """``setNames(g, vapply(g, `[[`, character(1), "@id"))``: the entities by their ``@id``."""
    return {e["@id"]: dict(e) for e in g}


def _ids(x: Any) -> list[str]:
    """Every value under an ``@id`` key, anywhere in a nested structure."""
    found: list[str] = []
    if isinstance(x, Mapping):
        for k, v in x.items():
            if k == "@id" and isinstance(v, str):
                found.append(v)
            else:
                found.extend(_ids(v))
    elif isinstance(x, list):
        for v in x:
            found.extend(_ids(v))
    return found


def crate_ids(crate: Mapping[str, Any]) -> dict[str, list[str]]:
    """``crate_ids()``: every ``@id`` in the graph (``described``), and the ones it refers to."""
    g = graph(crate)
    referenced = list(dict.fromkeys(_ids(g)))
    return {"described": [e["@id"] for e in g], "referenced": referenced}


def leaves(x: Any) -> list[Any]:
    """``unlist(x)``: the scalar values of a nested structure."""
    if isinstance(x, Mapping):
        return [leaf for v in x.values() for leaf in leaves(v)]
    if isinstance(x, list):
        return [leaf for v in x for leaf in leaves(v)]
    return [x]


def of_type(g: Sequence[Mapping[str, Any]], type_: str) -> list[Mapping[str, Any]]:
    """``Filter(function(e) identical(e[["@type"]], type_), g)``."""
    return [e for e in g if e.get("@type") == type_]


def cell(row: Any, column: str) -> Any:
    """One value of a one-row result: a dict, a Series or a one-row DataFrame."""
    if isinstance(row, pd.DataFrame):
        return row[column].iloc[0]
    return row[column]
