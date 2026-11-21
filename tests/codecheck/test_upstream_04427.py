"""code_check synchronised with metacheck 0.4.4.27 (tests/testthat/test-code_check.R, #695)."""

from __future__ import annotations

import pandas as pd

from metacheck.codecheck.core import _code_missing_files


def test_a_file_read_from_a_web_address_is_not_missing_from_the_repository() -> None:
    cf = pd.DataFrame(
        {
            "paper_id": ["p"],
            "repo_url": ["r"],
            "file_name": ["a.R"],
            "loaded_refs": ["http://journal.sjdm.org/16/1234/study1.sav\u001fgone.csv"],
            "files_written": [""],
        }
    )
    out = _code_missing_files(cf, pd.DataFrame({"repo_url": ["r"], "file_name": ["raw.csv"]}))
    assert out["loaded_files_missing_names"].tolist() == ["gone.csv"]
    assert out["loaded_files_missing"].tolist() == [1]
