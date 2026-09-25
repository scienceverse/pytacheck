"""Tests for file categories, file types and file naming (port of test-file_category.R).

Ports ``tests/testthat/test-file_category.R`` and adds checks of the bundled
``metacheck::file_types`` table and of ``check_file_naming()`` (which has no
testthat file upstream; its behaviour is covered by the parity cases).
"""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck
from pytacheck.fileinfo import check_file_naming, file_category, file_types, filetype
from pytacheck.fileinfo.naming import FILE_NAMING_SEVERITY


def test_file_category_empty_vector_and_frame() -> None:
    # handle zero results and/or OSF down
    assert len(file_category(pd.DataFrame())) == 0
    assert len(file_category([])) == 0


def test_file_category_as_vector() -> None:
    contents = ["a.csv", "b.R", "codebook.xlsx", "readme.txt", "ambiguous", "file.json"]
    summary = file_category(contents)
    assert summary["file_category"].tolist() == ["data", "code", "codebook", "readme", pd.NA, pd.NA]
    assert list(summary.columns) == ["name", "filetype", "file_category"]
    assert summary["filetype"].tolist() == ["data", "code", "data", "text", "", "code;data"]


def test_file_category_as_data_frame() -> None:
    contents = pd.DataFrame(
        {
            "name": ["a.csv", "b.R", "codebook.xlsx", "readme.txt", "ambiguous", "file.json"],
            "category": ["code", "data", "data", "code", "code", None],
            "filetype": ["data", "code", "data", "text", "text", "code;data"],
        }
    )
    summary = file_category(contents)
    # not currently categorising from category
    assert summary["file_category"].tolist() == ["data", "code", "codebook", "readme", pd.NA, pd.NA]
    # the input is not modified
    assert "file_category" not in contents.columns


def test_data_classify_files_genomic_formats() -> None:
    from pytacheck.datacheck.files import data_classify_files

    contents = [
        "sample.fasta",
        "sample.fa",
        "sample.fq",
        "sample.fastq",
        "sample.fasta.gz",
        "sample.fa.gz",
        "sample.fq.gz",
        "sample.fastq.gz",
    ]
    assert data_classify_files(contents) == ["data"] * len(contents)
    assert data_classify_files("random_archive.tar.gz") == ["unknown"]
    assert data_classify_files("random.gz") == ["unknown"]
    assert data_classify_files("sample.dat.gz") == ["unknown"]


def test_add_filetype() -> None:
    files = {
        "datarelease.pdf": "text",  # pdf cannot be data or code
        "my_r_code.pdf": "text",
        "data.sas": "stats",  # sas is always code
        "codebook.sas": "stats",
        "codebook.pdf": "text",
    }
    ft = filetype(list(files))
    assert ft.tolist() == list(files.values())
    assert ft.index.tolist() == list(files)


def test_edge_case_summarise() -> None:
    contents = pd.DataFrame(
        {
            "name": [
                "datarelease.pdf",
                "data.pdf",
                "my_r_code.pdf",
                "readme.xls",
                "data.sas",
                "codebook.sas",
                "readme.sas",
                "codebook.pdf",
            ],
            "category": [None, "data", None, "project", None, None, None, None],
            "classify": [None, None, None, "readme", "code", "codebook", "readme", "codebook"],
        }
    )
    contents["filetype"] = filetype(contents["name"]).tolist()
    summary = file_category(contents)
    expected = [None if pd.isna(v) else v for v in contents["classify"]]
    assert [None if pd.isna(v) else v for v in summary["file_category"]] == expected


def test_filetype_quirks() -> None:
    # unknown extensions paste R's NA; the name's last "." part is the extension
    ft = filetype(["noext", "x.", "a.R.", "b.json", "c.JSON", "d.İNİ", None])
    assert ft.tolist() == ["NA", "NA", "code", "code;data", "code;data", "config", "NA"]
    with pytest.raises(IndexError):
        filetype([""])
    assert len(filetype([])) == 0


def test_file_category_compound_types() -> None:
    out = file_category(["x.jasp", "sample.fq.gz", "c.tar.gz", "x.json", "run.sh", "x.sps"])
    assert out["filetype"].tolist() == [
        "data;stats",
        "data;archive",
        "archive",
        "code;data",
        "code;exec",
        "stats",
    ]
    assert [None if v is pd.NA else v for v in out["file_category"]] == [
        "data",
        "data",
        None,
        None,
        None,
        "code",
    ]


def test_file_category_null_and_errors() -> None:
    assert file_category(None) == {"filetype": [], "file_category": []}
    with pytest.raises(ValueError, match="replacement has 0 rows"):
        file_category(pd.DataFrame({"x": ["code", "data"]}))


def test_file_types_table() -> None:
    ft = file_types()
    assert list(ft.columns) == ["ext", "type"]
    assert len(ft) == 404
    assert str(ft["ext"].dtype) == "string"
    assert ft.loc[ft["ext"] == "json", "type"].tolist() == ["code", "data"]
    assert set(ft["type"]) == {
        "3D",
        "archive",
        "audio",
        "book",
        "code",
        "config",
        "data",
        "exec",
        "font",
        "image",
        "slide",
        "stats",
        "text",
        "video",
        "web",
    }
    # a fresh copy each time
    ft.loc[0, "type"] = "changed"
    assert file_types().loc[0, "type"] != "changed"


def test_bundled_data_loader() -> None:
    from pytacheck.resources.data import load_data

    ft = load_data("file_types")
    assert ft.shape == (404, 2)
    assert ft["ext"].iloc[0] == "1.ada"


def test_top_level_exports() -> None:
    assert pytacheck.file_category is file_category
    assert pytacheck.filetype is filetype
    assert pytacheck.check_file_naming is check_file_naming


def test_check_file_naming_rules() -> None:
    out = check_file_naming(
        ["my file.csv", "data_2.csv", "data_11.csv", "résumé.txt", "x_20231301.csv", "ok.R"],
        data_type=["data", "data", "data", "documentation", "unknown", "code"],
    )
    assert list(out.columns) == ["file_name", "rule", "severity", "detail"]
    rules = list(zip(out["file_name"], out["rule"], strict=True))
    assert ("my file.csv", "spaces") in rules
    assert ("résumé.txt", "special-characters") in rules
    assert ("résumé.txt", "diacritics") in rules
    assert ("x_20231301.csv", "unclassifiable") in rules
    assert ("x_20231301.csv", "date-format") in rules
    assert ("data_2.csv", "zero-padding") in rules
    assert "ok.R" not in out["file_name"].tolist()
    assert set(out["severity"]) <= {"bad", "suggestion"}
    assert out.loc[out["rule"] == "zero-padding", "severity"].tolist() == ["suggestion"]
    assert FILE_NAMING_SEVERITY["path-length-255"] == "bad"


def test_check_file_naming_clean_and_empty() -> None:
    out = check_file_naming(["analysis.R", "data.csv"])
    assert len(out) == 0
    assert list(out.columns) == ["file_name", "rule", "severity", "detail"]
    assert len(check_file_naming([])) == 0


def test_check_file_naming_path_lengths() -> None:
    long_path = "d" * 101 + "/" + "f" * 160
    out = check_file_naming(["x.R"], file_path=[long_path])
    assert out["rule"].tolist() == [
        "path-length-255",
        "path-length-228",
        "directory-length-100",
        "filename-length-50",
    ]
    assert out["detail"].tolist()[0] == (
        "path-length-255 is 262 characters, over the 255-character budget"
    )
    with pytest.raises(ValueError, match="missing value"):
        check_file_naming(["a.R", None])
