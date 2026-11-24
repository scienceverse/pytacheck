"""metacheck 0.4.4.37 (#723): a Windows drive written "C://" is a path, not a web address."""

from __future__ import annotations

import pandas as pd

from metacheck.archives.zip_peek import _is_local_file
from metacheck.codecheck.core import _code_missing_files
from metacheck.repro.layout import _repro_ref_checkable
from metacheck.utils import _is_web_address


def test_a_web_address_needs_a_scheme_of_two_letters_or_more():
    assert (
        _is_web_address(["https://site/x.csv", "ftp://h/x", "s3://bucket/x", "file:///C:/x.csv"])
        == [True] * 4
    )
    assert _is_web_address('url("https://site/x.csv")') is True
    assert (
        _is_web_address(["C://folder/x.csv", "C:/folder/x.csv", "data/x.csv", None]) == [False] * 4
    )


def test_a_file_on_a_windows_drive_is_counted_missing_but_a_web_file_is_not():
    code = pd.DataFrame(
        {
            "paper_id": ["p"],
            "repo_url": ["r"],
            "file_name": ["a.R"],
            "loaded_refs": ["C://folder/x.csv\x1fhttps://site/y.csv"],
            "files_written": [""],
        }
    )
    out = _code_missing_files(code, pd.DataFrame({"repo_url": ["r"], "file_name": ["raw.csv"]}))
    assert out["loaded_files_missing_names"].tolist() == ["x.csv"]
    assert out["loaded_files_missing"].tolist() == [1]


def test_the_reproducibility_prediction_keeps_a_windows_drive():
    d = pd.DataFrame({"ref": ["C://Lakens/files/example.csv"], "basename": ["example.csv"]})
    assert _repro_ref_checkable(d) == [True]


def test_a_local_zip_on_a_windows_drive_is_not_a_url(tmp_path):
    assert _is_local_file(str(tmp_path / "missing.zip")) is False
    assert _is_local_file("https://site/x.zip") is False
