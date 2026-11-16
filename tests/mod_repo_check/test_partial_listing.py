"""repo_check names an OSF project whose listing stopped at ``osf_max_requests`` (metacheck 0.4.4.2)."""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

import metacheck as pc
from metacheck.llm.cap_prompt import _cap_report_partial
from tests.mod_repo_check.parity_support import mocked

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _text(mo: pc.ModuleOutput) -> str:
    return "\n".join(x for x in mo.report if isinstance(x, str))


def _run(**kwargs: object) -> pc.ModuleOutput:
    with mocked():
        return pc.module_run(pc.test_paper(url="https://osf.io/j3gcx"), "repo_check", **kwargs)


def test_a_cut_listing_is_named_in_the_output_the_report_and_the_summary() -> None:
    mo = _run(osf_max_requests=8)
    partial = mo.partial_repos
    assert partial["repo_url"].tolist() == ["https://osf.io/j3gcx"]
    assert partial["paper_id"].tolist() == [mo.table["paper_id"].iloc[0]]
    n = int(partial["files_listed"].iloc[0])
    assert n == int((mo.table["repo_url"] == "https://osf.io/j3gcx").sum()) == 5
    assert "We listed only part of 1 OSF project (the request limit was reached)." in (
        mo.summary_text
    )
    report = _text(mo)
    assert "#### Partly Listed Repositories" in report
    assert "https://osf.io/j3gcx" in report.split("#### Partly Listed Repositories")[1]
    assert "`osf_max_requests`" in report


def test_a_complete_listing_has_no_partial_repos() -> None:
    mo = _run()
    assert len(mo.partial_repos) == 0
    assert list(mo.partial_repos.columns) == ["paper_id", "repo_url", "files_listed"]
    assert "Partly Listed" not in _text(mo)
    assert "only part" not in mo.summary_text


def test_the_download_names_a_repository_that_was_listed_only_in_part(
    capsys: pytest.CaptureFixture[str],
) -> None:
    partial = pd.DataFrame(
        {
            "paper_id": ["p1", "p2"],
            "repo_url": ["https://osf.io/aaaaa", "https://osf.io/bbbbb"],
            "files_listed": pd.Series([1, 12], dtype="Int64"),
        }
    )
    with pytest.warns(UserWarning, match=r"aaaaa was listed only in part \(1 file listed\)"):
        _cap_report_partial(partial, ["https://osf.io/aaaaa"])
    err = capsys.readouterr().err
    assert "bbbbb" not in err
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _cap_report_partial(partial, ["https://osf.io/ccccc"])
        _cap_report_partial(None, ["https://osf.io/aaaaa"])
        _cap_report_partial(partial.iloc[0:0], ["https://osf.io/aaaaa"])
