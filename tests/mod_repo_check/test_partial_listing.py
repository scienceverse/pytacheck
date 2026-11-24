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
    assert list(mo.partial_repos.columns) == [
        "paper_id",
        "repo_url",
        "files_listed",
        "reason",
    ]
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


# -- a listing the OSF refused in part (metacheck 0.4.4.22, #681) ----------------------------


def _refused_listing(osf_url: str, *args: object, **kwargs: object) -> pd.DataFrame:
    if "fghij" in osf_url:
        return pd.DataFrame(
            {
                "osf_url": [osf_url],
                "osf_id": ["fghij"],
                "osf_type": ["error"],
                "error": ["the OSF API did not answer (HTTP 503)"],
            }
        )
    d = pd.DataFrame(
        {
            "osf_url": [osf_url] * 3,
            "osf_id": ["abcde", "f1", "f2"],
            "osf_type": ["nodes", "files", "files"],
            "parent": pd.Series([None] * 3, dtype="string"),
            "kind": pd.Series([None, "file", "file"], dtype="string"),
            "public": [True] * 3,
            "name": ["A study", "data.csv", "analysis.R"],
            "path": pd.Series([None, "/data.csv", "/analysis.R"], dtype="string"),
            "download_url": pd.Series(
                [None, "https://osf.io/download/f1", "https://osf.io/download/f2"], dtype="string"
            ),
            "size": [float("nan"), 10.0, 10.0],
            "filetype": pd.Series([None, "data", "code"], dtype="string"),
            "provider": pd.Series([None, "osfstorage", "osfstorage"], dtype="string"),
        }
    )
    d["listing_short"] = True
    return d


def test_a_listing_the_osf_refused_in_part_is_named_by_repo_check_code_check_and_data_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("metacheck.archives.osf.osf_info", _refused_listing)
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: True)
    paper = pc.test_paper(text="Data: https://osf.io/abcde and code: https://osf.io/fghij")

    with mocked():
        rc = pc.module_run(paper, "repo_check", peek_zips=False)
    assert rc.partial_repos["repo_url"].tolist() == ["https://osf.io/abcde"]
    assert rc.partial_repos["reason"].tolist() == ["refused by the OSF"]
    assert rc.partial_repos["files_listed"].tolist() == [2]
    assert (
        "The OSF refused part of the listing of https://osf.io/abcde (2 files listed)"
        in " ".join(x for x in rc.report if isinstance(x, str))
    )
    assert "because the OSF refused part of the listing; run the check again" in rc.summary_text
    assert rc.gated_repos["paper_id"].tolist() == list(pc.paper_id(paper))
    assert rc.gated_repos["repo_url"].tolist() == ["https://osf.io/fghij"]

    for name, args in (("code_check", {"download": False}), ("data_check", {"download": "none"})):
        with mocked():
            mo = pc.module_run(rc, name, **args)
        assert mo.traffic_light in ("yellow", "red"), name
        report = " ".join(str(x) for x in mo.report)
        assert "Repositories not listed in full" in report, name
        assert "https://osf.io/abcde: the OSF refused part of the listing" in report, name
        assert (
            "https://osf.io/fghij: could not be listed (the OSF API did not answer (HTTP 503))"
            in report
        ), name
        assert "We could not list every file of 2 repositories" in mo.summary_text, name


def test_repos_not_listed_text_names_partly_listed_and_unlisted_repositories_with_their_reason() -> (
    None
):
    from metacheck.archives.download import _repos_not_listed_text

    partial = pd.DataFrame(
        {
            "paper_id": ["p", "p"],
            "repo_url": ["https://osf.io/aaaaa", "https://osf.io/bbbbb"],
            "files_listed": pd.Series([7, 1], dtype="Int64"),
            "reason": ["refused by the OSF", "request limit"],
        }
    )
    gated = pd.DataFrame(
        {
            "paper_id": ["p"] * 3,
            "repo_url": ["https://osf.io/ccccc", "https://osf.io/ddddd", "https://osf.io/aaaaa"],
            "repo_type": ["osf"] * 3,
            "repo_error": ["the OSF API did not answer (HTTP 503)", "private", "x"],
        }
    )
    # ddddd has files, so it was listed; aaaaa is named once, as partly listed
    x = _repos_not_listed_text(partial, gated, ["https://osf.io/ddddd"])
    assert x["report"][0] == "#### Repositories not listed in full"
    assert len(x["report"]) == 5
    assert (
        "https://osf.io/aaaaa: the OSF refused part of the listing, also when asked again "
        "(7 files listed). Run the check again later."
    ) in x["report"][2]
    assert "the listing stopped at the request limit (1 file listed)" in x["report"][3]
    assert (
        "https://osf.io/ccccc: could not be listed (the OSF API did not answer (HTTP 503))"
        in x["report"][4]
    )
    assert x["summary"].startswith("We could not list every file of 3 repositories")
    # a table without `reason` (an older repo_check output) is a cut listing
    x = _repos_not_listed_text(partial.iloc[[1], :3], None, [])
    assert "this repository, because it could not" in x["report"][1]
    assert "request limit" in x["report"][2]
    assert _repos_not_listed_text(None, None, [])["report"] is None
    assert _repos_not_listed_text(partial.iloc[0:0], gated.iloc[0:0], [])["summary"] is None
    assert _repos_not_listed_text(None, gated.iloc[[1]], ["https://osf.io/ddddd"])["report"] is None


def test_the_download_words_a_refused_listing_apart_from_a_cut_one() -> None:
    partial = pd.DataFrame(
        {
            "paper_id": ["p", "p"],
            "repo_url": ["https://osf.io/abcde", "https://osf.io/zzzzz"],
            "files_listed": pd.Series([3, 1], dtype="Int64"),
            "reason": ["refused by the OSF", "request limit"],
        }
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _cap_report_partial(partial, list(partial["repo_url"]))
    w = [str(c.message) for c in caught]
    assert len(w) == 2
    assert "the OSF refused part of the listing, also when asked again" in w[0]
    assert "osf_max_requests" not in w[0]
    assert "osf_max_requests" in w[1]
