"""Port of metacheck 0.4.4.3's tests/testthat/test-repo-link-versions.R."""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives import link_versions as lv


def _paper(text: str | list[str], url: pd.DataFrame | None = None) -> pc.Paper:
    p = pc.test_paper(text)
    p.paper_id = "p"
    if url is not None:
        p.url = url
    return p


def _frame(urls: list[str], repo_type: str | list[str] = "x") -> pd.DataFrame:
    types = [repo_type] * len(urls) if isinstance(repo_type, str) else repo_type
    return pd.DataFrame(
        {
            "paper_id": pd.Series(["p"] * len(urls), dtype="string"),
            "repo_type": pd.Series(types, dtype="string"),
            "repo_url": pd.Series(urls, dtype="string"),
        }
    )


def test_repo_link_joined_gives_the_joined_form_only_before_a_links_part() -> None:
    j = lv._repo_link_joined
    assert j("10.5061/dryad.4mw6m", ["Data are at 10.5061/dryad.4mw6m 906h."]) == [
        "10.5061/dryad.4mw6m906h"
    ]
    assert j(
        "https://github.com/kun-ecolo", ["Code: https://github.com/kun-ecolo gy/SER and more."]
    ) == ["https://github.com/kun-ecology/SER"]
    # an ordinary word after a repository's link gives no other form
    assert j("https://github.com/x/y", ["see https://github.com/x/y for The code"]) == []
    # after an account, any word is tried, as a space can fall anywhere in its name
    assert j("https://github.com/J0vid/", ["see https://github.com/J0vid/ Color images"]) == [
        "https://github.com/J0vid/Color"
    ]
    assert j("github.com/crs", ["Aust (2022). https://github.com/crs h/papaja"]) == [
        "github.com/crsh/papaja"
    ]
    # up to three words, the longest form first
    assert j("github.com/keras", ["Keras Tuner. https://github.com/keras -team/keras -tuner"]) == [
        "github.com/keras-team/keras-tuner",
        "github.com/keras-team/keras",
    ]
    assert j(
        "github.com/matthewmoscou",
        ["Available from: https://github.com/matthewmoscou/ QKphylogeny"],
    ) == ["github.com/matthewmoscou/QKphylogeny"]
    # a link found without the "/" that ends it in the text
    assert j("github.com/zhoupc", ["Analyses: https://github.com/zhoupc/ OASIS_matlab here."]) == [
        "github.com/zhoupc/OASIS_matlab"
    ]
    # after a hyphen, the form without it is tried first
    assert j(
        "https://github.com/group/ionic-", ["at https://github.com/group/ionic- glass-formers."]
    ) == [
        "https://github.com/group/ionicglass-formers",
        "https://github.com/group/ionic-glass-formers",
    ]


def test_repo_links_tidy_repairs_damage_from_reading_the_pdf() -> None:
    urls = [
        # security wrappers
        "https://nam04.safelinks.protection.outlook.com/?url=https%3A%2F%2Fdatadryad.org%2Fstash%2Fshare%2Fabc&data=05",
        "https://urldefense.proofpoint.com/v2/url?u=https-3A__osf.io_ab12c&d=DwM",
        "https://protect.checkpoint.com/v2/___https://researchbox.org/3218___.YzJ1OnN0",
        # two links run together
        "https://doi.org/10.48329/zrvz-6a47.org/10.1186/s12916-025-04514-z",
        "http://doi.org/10.1525/collabra.48.pr10.1525/collabra.48.pr",
        "github.com/gabehassler/PhylogeneticFactorAnalysis.https",
        # home pages and a DOI's prefix alone
        "https://zenodo.org/",
        "http://osf.io",
        "doi.org/10.5281",
        # a link cut inside a name beside its longer form; an account beside its repository
        "10.6084/m9",
        "10.6084/m9.figshare.27262470",
        "github.com/XiaoyangSu",
        "https://github.com/XiaoyangSu/AccuCor",
        # a digit after it may be another record: both stay
        "10.5281/zenodo.12345",
        "10.5281/zenodo.123456",
    ]
    paper = _paper("No text needed here.")
    out = lv._repo_links_tidy(_frame(urls), paper)["repo_url"].tolist()
    assert out == [
        "https://datadryad.org/stash/share/abc",
        "https://osf.io/ab12c",
        "https://researchbox.org/3218",
        "https://doi.org/10.48329/zrvz-6a47",
        "http://doi.org/10.1525/collabra.48.pr",
        "github.com/gabehassler/PhylogeneticFactorAnalysis",
        # a shorter link that may be whole is left to _repo_links_joined()
        "10.6084/m9",
        "10.6084/m9.figshare.27262470",
        "https://github.com/XiaoyangSu/AccuCor",
        "10.5281/zenodo.12345",
        "10.5281/zenodo.123456",
    ]
    # names that share a beginning are two repositories
    gh = [
        "https://github.com/mastodon-sc/mastodon",
        "https://github.com/mastodon-sc/mastodon-averoflab",
        "https://github.com/x/Raina-",
        "https://github.com/x/Raina-lab",
    ]
    out = lv._repo_links_tidy(_frame(gh, "github"), paper)["repo_url"].tolist()
    assert out == [gh[0], gh[1], gh[3]]
    # Proofpoint's newer wrapper writes "https:/"
    assert (
        lv._repo_link_unwrapped(
            "https://urldefense.com/v3/__https:/github.com/marhabaie/dmel-nanos-targets__;!!KGKeukY!zR8"
        )
        == "https://github.com/marhabaie/dmel-nanos-targets"
    )
    # a DOI written right after the link in the text, where the link stopped
    paper = _paper(
        "Life Sci. 2008. http://dx.doi.org/10.5061/dryad.w0vt4b8sh10.1007/s00018-008-8182-3"
    )
    out = lv._repo_links_tidy(_frame(["doi.org/10.5061/dryad.w0vt4b8sh10"], "dryad"), paper)
    assert out["repo_url"].tolist() == ["doi.org/10.5061/dryad.w0vt4b8sh"]
    # a link with nothing wrong is kept as it is
    same = _frame(["https://osf.io/ab12c"], "osf")
    pd.testing.assert_frame_equal(lv._repo_links_tidy(same, paper), same)
    # an OSF identifier written twice or run into the next; a DataONE identifier without "/";
    # the rest of a separator after a cut
    urls = [
        "https://osf.io/3bh67",
        "http://osf.io/3bh673bh67",
        "http://osf.io/3bh67c9pag",
        "https://osf.io/c9pag",
        "knb.341.1",
        "https://osf.io/j7kt2/;%20%20https://rr.peercommunityin.org/x",
    ]
    out = lv._repo_links_tidy(_frame(urls, ["osf", "osf", "osf", "osf", "dataone", "osf"]), paper)
    # ("http://osf.io/3bh67", what is left of the second, is the same address as
    # "https://osf.io/3bh67" and is not kept twice, #648)
    assert out["repo_url"].tolist() == [
        "https://osf.io/3bh67",
        "https://osf.io/c9pag",
        "knb.341.1",
        "https://osf.io/j7kt2/",
    ]
    # an OSF link cut inside its identifier, beside its whole form
    out = lv._repo_links_tidy(
        _frame(
            ["https://doi.org/10.17605/osf.io/zry5", "https://doi.org/10.17605/osf.io/zry5b"], "osf"
        ),
        paper,
    )
    assert out["repo_url"].tolist() == ["https://doi.org/10.17605/osf.io/zry5b"]
    # a longer form that repeats the link's end is the reader writing it twice
    two = ["https://github.com/o/Vaccine-Policy", "https://github.com/o/Vaccine-PolicyPolicy"]
    assert lv._repo_links_tidy(_frame(two, "github"), paper)["repo_url"].tolist() == two


def test_repo_links_tidy_keeps_one_row_for_an_address_written_in_two_ways() -> None:
    """Port of ".repo_links_tidy keeps one row for an address written in two ways (#648)"."""
    repos = pd.DataFrame(
        {
            "paper_id": pd.Series(["p"] * 8, dtype="string"),
            "repo_type": pd.Series(
                ["github", "github", "osf", "osf", "zenodo", "zenodo", "github", "github"],
                dtype="string",
            ),
            "repo_url": pd.Series(
                [
                    "github.com/OpenSAFELY-Core",
                    "www.github.com/OpenSAFELY-Core",
                    "osf.io/abcde/",
                    "https://osf.io/abcde",
                    "https://doi.org/10.5281/ZENODO.123",
                    "10.5281/zenodo.123",
                    # capitals in a name may matter: both stay
                    "github.com/A/Repo",
                    "github.com/A/repo",
                ],
                dtype="string",
            ),
        }
    )
    paper = _paper("No text needed here.")
    assert lv._repo_links_tidy(repos, paper)["repo_url"].tolist() == [
        "github.com/OpenSAFELY-Core",
        "https://osf.io/abcde",
        "https://doi.org/10.5281/ZENODO.123",
        "github.com/A/Repo",
        "github.com/A/repo",
    ]
    # the same address on two platforms' lists, or in two papers, is not merged
    repos = pd.DataFrame(
        {
            "paper_id": pd.Series(["p", "p", "q"], dtype="string"),
            "repo_type": pd.Series(["zenodo", "dataverse", "zenodo"], dtype="string"),
            "repo_url": pd.Series(
                ["10.5281/zenodo.1", "https://doi.org/10.5281/zenodo.1", "10.5281/zenodo.1"],
                dtype="string",
            ),
        }
    )
    q = pc.test_paper("Nothing.")
    q.paper_id = "q"
    papers = pc.PaperList([paper, q])
    assert len(lv._repo_links_tidy(repos, papers)) == 3


def test_repo_link_without_hyphen_changes_the_owner_and_repository_only() -> None:
    w = lv._repo_link_without_hyphen
    assert w("github.com/Betha-nyL/DeepKoopman") == ["github.com/BethanyL/DeepKoopman"]
    assert w("https://gitlab.com/a-b/c-d") == [
        "https://gitlab.com/ab/c-d",
        "https://gitlab.com/a-b/cd",
    ]
    assert w("https://github.com/owner/repo") == []
    assert w("https://zenodo.org/records/1") == []


def test_repo_links_joined_keeps_a_link_that_exists_as_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # no request is made for a link without another form
    monkeypatch.setattr(lv, "_repo_link_exists", lambda *a: pytest.fail("a request was made"))
    repos = _frame(["https://github.com/x/y"], "github")
    paper = _paper("The code is at https://github.com/x/y for The analyses.")
    pd.testing.assert_frame_equal(lv._repo_links_joined(repos, paper), repos)


def test_repo_links_joined_replaces_a_link_that_exists_only_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = {
        "10.5061/dryad.4mw6m906h",
        "https://github.com/kun-ecology/SER",
        "10.6084/m9.figshare.27262470",
        "https://github.com/mastodon-sc/mastodon",
        "https://github.com/mastodon-sc/mastodon-averoflab",
    }
    monkeypatch.setattr(lv, "_repo_link_exists", lambda url, t: url in real)
    repos = _frame(["10.5061/dryad.4mw6m", "https://github.com/kun-ecolo"], ["dryad", "github"])
    paper = _paper(
        [
            "Data are at 10.5061/dryad.4mw6m 906h.",
            "Code: https://github.com/kun-ecolo gy/SER and more.",
        ]
    )
    obs = lv._repo_links_joined(repos, paper)
    assert obs["repo_url"].tolist() == [
        "10.5061/dryad.4mw6m906h",
        "https://github.com/kun-ecology/SER",
    ]
    # a link cut short beside its longer form that exists is left out; two repositories whose
    # names share a beginning both stay
    urls = [
        "10.6084/m9",
        "10.6084/m9.figshare.27262470",
        "https://github.com/mastodon-sc/mastodon",
        "https://github.com/mastodon-sc/mastodon-averoflab",
    ]
    repos = _frame(urls, ["figshare", "figshare", "github", "github"])
    obs = lv._repo_links_joined(repos, _paper("No split links here."))
    assert obs["repo_url"].tolist() == urls[1:]
    monkeypatch.setattr(
        lv, "_repo_link_exists", lambda *a: None
    )  # the hyphen search asks the listing itself
    found = lv._repo_link_unhyphenated(
        "github.com/Betha-nyL/DeepKoopman",
        lambda u: u if u == "github.com/BethanyL/DeepKoopman" else None,
    )
    assert found == "github.com/BethanyL/DeepKoopman"


def test_a_link_whose_ending_was_written_twice_gets_its_single_form() -> None:
    hrefs = ["https://github.com/wrshoemaker/macroeco_phylo", "https://example.org"]
    urls = [
        "https://github.com/wrshoemaker/macroeco_phylophylo",
        "https://github.com/HoverflyLab/SampSamp",
        "https://github.com/x/plain",
    ]
    assert lv._repo_link_undoubled(urls, hrefs) == [
        "https://github.com/wrshoemaker/macroeco_phylo",
        urls[1],
        urls[2],
    ]
    # without the paper's own link list nothing changes
    assert lv._repo_link_undoubled(["https://github.com/a/PolicyPolicy"], []) == [
        "https://github.com/a/PolicyPolicy"
    ]
    # in repo_check's tidying, from the paper's `url` table
    paper = _paper(
        "Code: https://github.com/wrshoemaker/macroeco_phylophylo",
        pd.DataFrame({"href": ["https://github.com/wrshoemaker/macroeco_phylo/"], "text_id": [1]}),
    )
    repos = _frame(["https://github.com/wrshoemaker/macroeco_phylophylo"], "github")
    out = lv._repo_links_tidy(repos, paper)
    assert out["repo_url"].tolist() == ["https://github.com/wrshoemaker/macroeco_phylo"]
