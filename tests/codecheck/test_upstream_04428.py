"""code_check synchronised with metacheck 0.4.4.28 (tests/testthat/test-code_check.R and
test-module-code_check.R, the download limits of #699)."""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives import download as dlm
from metacheck.codecheck.core import _code_fetch_capped
from metacheck.module import module_run
from tests.mod_code.helpers import fake_repo_check


def test_code_fetch_capped_reads_a_web_file_only_up_to_max_file_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # not a web address: returned as it is
    assert _code_fetch_capped("C:/a/b.R", "b.R") == "C:/a/b.R"
    # no address at all (a file inside an archive): said at once, no request
    with pytest.raises(OSError, match="no web address of its own"):
        _code_fetch_capped(None, "b.R")

    limit: list[float] = []

    def fake(url: str, dest: str, max_bytes: float = math.inf, **kw: Any) -> Any:
        limit.append(max_bytes)
        Path(dest).write_text("x <- 1\n")
        return None, False, 0

    monkeypatch.setattr(dlm, "_download_capped", fake)
    p = _code_fetch_capped("https://x.org/a.Rmd", "a.Rmd", max_file_size=2)
    try:
        assert limit[-1] == 2 * 1024 * 1024
        assert p.endswith(".Rmd")  # the readers of .Rmd files need it
        assert Path(p).read_text() == "x <- 1\n"
    finally:
        os.unlink(p)
    os.unlink(_code_fetch_capped("https://x.org/a.R", "a.R", max_file_size=math.inf))
    assert limit[-1] == math.inf

    monkeypatch.setattr(dlm, "_download_capped", lambda *a, **k: ("over the byte limit", True, 3e6))
    with pytest.raises(OSError) as e:
        _code_fetch_capped("https://x.org/a.R", "a.R", max_file_size=2)
    assert "larger than the 2 MB per-file limit (max_file_size)" in str(e.value)

    # an error from the web reads as code_read()'s
    monkeypatch.setattr(dlm, "_download_capped", lambda *a, **k: ("HTTP 404", False, 0))
    with pytest.raises(OSError) as e:
        _code_fetch_capped("https://x.org/a.R", "a.R")
    assert "cannot open URL 'https://x.org/a.R': HTTP status was 404" in str(e.value)

    # an empty file is no error
    monkeypatch.setattr(dlm, "_download_capped", lambda *a, **k: ("empty response", False, 0))
    p = _code_fetch_capped("https://x.org/a.R", "a.R")
    try:
        assert os.path.getsize(p) == 0
    finally:
        os.unlink(p)


def test_code_check_does_not_read_from_the_web_a_file_left_out_by_a_download_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import metacheck.archives.download as dl_mod
    import metacheck.codecheck.core as core
    import metacheck.modules._code_check as cc

    paper = pc.test_paper(["Some text."])
    paper.paper_id = "p1"
    tbl = pd.DataFrame(
        {
            "paper_id": "p1",
            "repo_url": "https://osf.io/abcde",
            "file_name": ["small.R", "big.R"],
            "file_url": ["https://osf.io/download/1", "https://osf.io/download/2"],
            "file_location": pd.Series([None, None], dtype="string"),
            "file_size": [100.0, 5e8],
        }
    )
    why = (
        "it was not downloaded, because it is larger than the 100 MB per-file limit (max_file_size)"
    )
    fetched: list[str] = []

    def fake_download(files: pd.DataFrame, **kw: Any) -> pd.DataFrame:
        out = files.copy()
        out.attrs["limit_refused"] = pd.Series(
            [why if n == "big.R" else None for n in files["file_name"]], dtype="string"
        )
        return out

    def fake_fetch(url: Any, file_name: Any, **kw: Any) -> str:
        fetched.append(str(file_name))
        p = Path(os.environ.get("TMPDIR", "/tmp")) / f"limit_{file_name}"
        p.write_text("x <- 1\n")
        return str(p)

    monkeypatch.setattr(dl_mod, "download_repo_files", fake_download)
    monkeypatch.setattr(core, "_code_fetch_capped", fake_fetch)
    monkeypatch.setattr(cc, "_code_fetch_capped", fake_fetch)
    mo = module_run(fake_repo_check(tbl, paper=paper), "code_check")
    # only the file within the limits is fetched from the web
    assert set(fetched) == {"small.R"}
    errors = dict(zip(mo.table["file_name"], mo.table["error"], strict=True))
    assert errors["big.R"] == why
    assert pd.isna(errors["small.R"])


def test_reproducibility_check_passes_the_size_limits_to_code_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R (#699): code_check used its own defaults whatever reproducibility_check was given
    import metacheck.module as mod
    from metacheck.modules._reproducibility import run_missing

    seen: dict[str, dict[str, Any]] = {}

    def fake(paper: Any, module: str, **kw: Any) -> Any:
        seen[module] = kw
        return None

    monkeypatch.setattr(mod, "module_run", fake)
    for m in ("code_check", "data_check", "psychds_check"):
        run_missing(
            None,
            m,
            local_path=None,
            local_only=False,
            model=None,
            params=None,
            cache=False,
            skip_on_api_limit=False,
            download="data",
            skip_types=None,
            peek_zips=True,
            max_file_size=7,
            max_download_size=70,
        )
    assert (
        seen["code_check"]["max_file_size"] == 7 and seen["code_check"]["max_download_size"] == 70
    )
    assert "download" not in seen["code_check"]
    assert seen["data_check"]["max_file_size"] == 7 and seen["data_check"]["download"] == "data"
    assert "max_file_size" not in seen["psychds_check"]
