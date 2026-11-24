"""Tests for the 0.4.4.18 services of repo_check: file-sharing links (#660), CRAN, ICPSR, sequence
archives and Handles (port of test-file-share.R, test-archive-cran.R, test-archive-icpsr.R,
test-archive-seq.R, test-handle-links.R).

The ownership parts of those R tests (``.own_needle``, ``.repo_ownership``, ``.own_org_match``)
and the ones that read the live records are not ported (D141, D149); the network tests use the
mocks of ``parity_support``.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import pytest

import metacheck as pc
from metacheck.archives import _useless as useless
from metacheck.archives import cran, file_share, handle, icpsr, seq
from metacheck.fileinfo import category
from tests.archives_services.parity_support import batch_mock, handle_mock, seq_mock
from tests.mod_repo_check.parity_support import mocked

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def tpaper(text: object = None, url: object = None) -> pc.Paper:
    return pc.test_paper(text=text, url=url) if url is not None else pc.test_paper(text=text)


# --- file-sharing services -------------------------------------------------------------


def test_file_share_links_finds_links_to_file_sharing_services() -> None:
    p = tpaper(
        [
            "Data are on https://drive.google.com/drive/folders/1AbC?usp=sharing.",
            "Code: www.dropbox.com/s/abc123/code.zip?dl=0 and https://1drv.ms/u/s!AbC",
            "The survey is at https://docs.google.com/forms/d/e/1FA/viewform",
            "Box: https://app.box.com/s/xyz9 ; SURF: https://surfdrive.surf.nl/files/index.php/s/AbCd",
            "We used Dropbox to share drafts among the authors.",
        ]
    )
    fs = file_share.file_share_links(p)
    assert fs["service"].tolist() == ["Google Drive", "Dropbox", "OneDrive", "Box", "SURFdrive"]
    # the full stop that ends the sentence is not part of the link
    assert fs["href"].iloc[0] == "https://drive.google.com/drive/folders/1AbC?usp=sharing"
    # a hyperlink without the address in the text
    h = file_share.file_share_links(tpaper(url="https://www.dropbox.com/s/abc123/data.csv?dl=0"))
    assert h["service"].tolist() == ["Dropbox"]
    # nothing to find
    assert len(file_share.file_share_links(tpaper("Data are at https://osf.io/abcde."))) == 0
    # an archived copy lasts: no warning
    a = tpaper(
        "See the list at http://web.archive.org/web/20220203164905/"
        "https://docs.google.com/spreadsheets/d/1LNF5/edit."
    )
    assert len(file_share.file_share_links(a)) == 0
    # one link in full and shortened is one link
    b = tpaper(
        "The set (https://nihcc.app.box.com/v/ChestXray-NIHCC) is public.",
        "app.box.com/v/ChestXray-NIHCC",
    )
    assert file_share.file_share_links(b)["href"].tolist() == [
        "https://nihcc.app.box.com/v/ChestXray-NIHCC"
    ]


def test_repo_check_warns_for_a_file_sharing_link_and_makes_the_light_yellow() -> None:
    p = tpaper(
        "All data and code are available at https://www.dropbox.com/s/abc123/project.zip?dl=0."
    )
    with mocked():
        mo = pc.module_run(p, "repo_check")
    assert mo.traffic_light == "yellow"
    assert mo.summary_table["file_share_n"].tolist() == [1]
    rep = "\n".join(str(x) for x in (mo.report if isinstance(mo.report, list) else [mo.report]))
    assert "File-sharing services" in rep
    assert "Dropbox: https://www.dropbox.com/s/abc123/project.zip?dl=0" in rep
    assert "Zenodo" in rep
    # without such a link, no warning
    with mocked():
        mq = pc.module_run(tpaper("We report all measures."), "repo_check")
    assert mq.traffic_light == "na"
    assert mq.summary_table["file_share_n"].tolist() == [0]


# --- CRAN --------------------------------------------------------------------------------


def test_cran_links_finds_cran_package_links_in_their_forms() -> None:
    p = tpaper(
        [
            "A stable version can be downloaded through CRAN at "
            "http://cran.r-project.org/web/packages/Reol/index.html.",
            "We used lme4 (https://CRAN.R-project.org/package=lme4) and its vignette "
            "https://cran.r-project.org/web/packages/lme4/vignettes/lmerperf.html.",
            "Cite it as 10.32614/CRAN.package.metafor.",
            "R is at https://cran.r-project.org/.",
        ]
    )
    links = cran.cran_links(p)
    assert links["package"].tolist() == ["Reol", "lme4", "metafor"]
    assert links["href"].iloc[0] == "https://CRAN.R-project.org/package=Reol"
    assert links["repo_type"].unique().tolist() == ["cran"]
    assert (
        cran._cran_people(
            "Roger Bivand [cre, aut] (<https://orcid.org/0000-0003-2392-6140>), "
            "Nicholas Lewin-Koh [aut]"
        )
        == "Roger Bivand; Nicholas Lewin-Koh"
    )
    assert len(cran.cran_links(tpaper("No CRAN link."))) == 0


def test_cran_info_reads_a_package_record_and_an_unknown_package() -> None:
    reol = json.dumps(
        {
            "Package": "Reol",
            "Title": "R   interface to the  Encyclopedia of Life",
            "Author": "Barb Banbury [aut, cre], Brian O'Meara [aut]",
            "Version": "0.6.4",
            "Date/Publication": "2013-10-16 01:00:00",
            "Description": "Interface   to EOL.",
        }
    )
    answers = {"https://crandb.r-pkg.org/Reol": reol}
    info = seq_mock(
        answers,
        lambda: cran.cran_info(
            [
                "https://CRAN.R-project.org/package=Reol",
                "https://CRAN.R-project.org/package=notapackagexyz",
            ]
        ),
    )
    assert info["creators"].iloc[0] == "Barb Banbury; Brian O'Meara"
    assert info["title"].iloc[0] == "R interface to the Encyclopedia of Life"
    assert info["publication_date"].iloc[0] == "2013-10-16"
    assert info["error"].iloc[1] == "unfound"
    assert info["resource_type"].tolist() == ["Software", "Software"]


# --- ICPSR -------------------------------------------------------------------------------


def test_icpsr_links_finds_openicpsr_and_icpsr_deposits_in_their_forms() -> None:
    p = tpaper(
        [
            "Data and code are available at https://doi.org/10.3886/E195681V1.",
            "See https://www.openicpsr.org/openicpsr/project/121688/version/V1/view and 10.3886/E121688.",
            "We used http://www.icpsr.umich.edu/icpsrweb/ICPSR/studies/04652 and 10.3886/ICPSR21600.v17.",
        ]
    )
    links = icpsr.icpsr_links(p)
    assert set(links["doi"]) == {
        "10.3886/E195681V1",
        "10.3886/E121688V1",
        "10.3886/ICPSR04652",
        "10.3886/ICPSR21600.v17",
    }
    assert (
        links.loc[links["doi"] == "10.3886/E195681V1", "href"].iloc[0]
        == "https://doi.org/10.3886/E195681V1"
    )
    assert icpsr._icpsr_doi_of(
        [
            "icpsr.umich.edu/web/ICPSR/studies/4652",
            "https://www.icpsr.umich.edu/sites/icpsr/view/studies/195681/versions/V1",
            "10.3886/e100462v3",
        ]
    ) == ["10.3886/ICPSR04652", "10.3886/E195681V1", "10.3886/E100462V3"]
    assert len(icpsr.icpsr_links(tpaper("No ICPSR link."))) == 0


def test_icpsr_info_reads_a_deposit_datacite_record() -> None:
    def rec(doi: str, creators: list[str], title: str) -> tuple[int, dict]:
        return 200, {
            "data": {
                "attributes": {
                    "doi": doi,
                    "titles": [{"title": title}],
                    "creators": [{"name": n} for n in creators],
                    "types": {"resourceTypeGeneral": "Dataset"},
                }
            }
        }

    answers = {
        "https://api.datacite.org/dois/10.3886/E195681V1": rec(
            "10.3886/E195681V1", ["Wu, Stephen", "Ge, Qi"], "A deposit"
        ),
        "https://api.datacite.org/dois/10.3886/ICPSR04652": rec(
            "10.3886/ICPSR04652", ["Ryff, Carol"], "Midlife in the United States (MIDUS 2)"
        ),
    }
    info = batch_mock(
        answers,
        lambda: icpsr.icpsr_info(
            ["https://doi.org/10.3886/E195681V1", "https://doi.org/10.3886/ICPSR04652"]
        ),
    )
    assert info["creators"].iloc[0] == "Stephen Wu; Qi Ge"
    assert "Midlife in the United States" in info["title"].iloc[1]
    assert info["error"].isna().all()


# --- sequence and expression archives ---------------------------------------------------


def test_seq_archive_links_finds_sequence_and_expression_archive_deposits() -> None:
    p = tpaper(
        [
            "RNA-seq data are deposited in GEO (GSE149523) and the SRA (PRJNA770129, SRR16287660).",
            "The single nucleus reads are deposited (SRR7416431 to SRR7416438).",
            "Data are in ArrayExpress under E-MTAB-6487; see E-ISSN 1234-5678.",
            "All reads were deposited in the DDBJ Sequence Read Archive (DRA007363).",
            "We analysed 20 participants.",
        ]
    )
    links = seq.seq_archive_links(p)
    assert set(links["accession"] + " " + links["repo_type"]) == {
        "GSE149523 geo",
        "PRJNA770129 ena",
        "SRR7416431 ena",
        "E-MTAB-6487 arrayexpress",
        "DRA007363 ena",
    }
    assert (
        links.loc[links["accession"] == "GSE149523", "href"].iloc[0]
        == "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE149523"
    )
    # a run in a sentence that names its study is not a deposit of its own
    assert "SRR16287660" not in set(links["accession"])
    assert len(seq.seq_archive_links(tpaper("No accession here."))) == 0
    # a paper naming many accessions keeps those in depositing sentences first
    many = tpaper(
        [
            "We reanalysed " + ", ".join(f"GSE{n}" for n in range(1001, 1031)) + ".",
            "Our data have been deposited in GEO (GSE999999).",
        ]
    )
    m = seq.seq_archive_links(many, max_n=5)
    assert len(m) == 5
    assert "GSE999999" in set(m["accession"])


def test_a_record_whose_archive_did_not_answer_is_unreachable_not_unfound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # R (#699): an archive that did not answer is not a deposit that does not exist
    import httpx

    from metacheck import http
    from metacheck.archives import seq

    geo = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE149523"
    ena = "https://www.ebi.ac.uk/ena/browser/view/PRJNA770129"
    ae = "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6487"
    status = {"code": 503}

    def request(method: str, url: str, **kw: Any) -> Any:
        if status["code"] == 0:
            return None  # no answer at all (no connection or a time-out)
        return httpx.Response(status["code"], text="", request=httpx.Request(method, url))

    monkeypatch.setattr(http, "request", request)
    got = seq._seq_fetch("https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE1")
    assert got["text"] is None and got["unreachable"] is True
    info = seq.seq_archive_info([geo, ena, ae])
    assert info["error"].tolist() == ["unreachable"] * 3

    status["code"] = 0
    assert seq._seq_fetch("https://www.ebi.ac.uk/x")["unreachable"] is True

    # the archive answered that it has no such record
    status["code"] = 404
    assert seq._seq_fetch("https://www.ebi.ac.uk/x")["unreachable"] is False
    assert seq.seq_archive_info(ae)["error"].tolist() == ["unfound"]


def test_a_sequence_archive_deposit_is_set_aside_and_the_light_is_na() -> None:
    # R lists the authors' own deposits and does not check them; without the ownership engine
    # (D141, D149) every GEO / SRA / ENA / ArrayExpress / CRAN / ICPSR link is set aside, so the
    # file table never holds one and the light stays "na" (as R does when none is the authors')
    p = tpaper("All RNA-seq raw data have been deposited to NCBI under BioProject ID PRJNA770129.")
    with mocked():
        mo = pc.module_run(p, "repo_check")
    assert mo.traffic_light == "na"
    assert mo.summary_table["repo_n"].tolist() == [0]
    assert (
        mo.table is None or not mo.table["repo_url"].astype(str).str.contains("PRJNA770129").any()
    )
    assert "without READMEs" not in mo.report


# --- Handles ------------------------------------------------------------------------------


def test_handle_links_without_a_handle_is_empty() -> None:
    assert len(handle.handle_links(tpaper("No Handle here."))) == 0


def test_handle_links_follows_a_handle_to_a_repository_repo_check_reads() -> None:
    p = tpaper(
        [
            "All data are available at http://hdl.handle.net/11667/195.",
            "See the thesis at http://hdl.handle.net/1721.1/114075.",
        ]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        h = handle_mock(
            {
                "11667/195": "https://datastorre.stir.ac.uk/handle/11667/195",
                "1721.1/114075": "https://dspace.mit.edu/handle/1721.1/114075",
            },
            lambda: handle.handle_links(p),
        )
    # the data deposit on Stirling's DataSTORRE (DSpace); the thesis is on a repository
    # metacheck does not read here, so it is left out
    assert h["repo_type"].tolist()[0] == "dspace"
    assert "datastorre.stir.ac.uk" in h["href"].iloc[0]
    assert h["handle"].iloc[0] == "http://hdl.handle.net/11667/195"


# --- file kinds and useless files (repo_check) -----------------------------------------


def test_repo_file_kind_counts_a_readme_and_an_archive_by_name() -> None:
    k = category._repo_file_kind(
        ["README.md", "readme.txt", "data.csv", "a.zip", "b.tar.gz", "c.7z", "d.rar", "m.joblib"],
        ["text", "text", "data", "archive", "archive", "archive", "archive", "unknown"],
        [10, 0, 5, 1, 1, 1, 1, 1],
    )
    # an empty README is not one (size 0)
    assert list(k) == [
        "readme",
        "text",
        "data",
        "archive",
        "archive",
        "archive",
        "archive",
        "unknown",
    ]


def test_useless_kind_names_the_r_history_and_its_temporary_files() -> None:
    assert useless.useless_file_kind([".Rapp.history", ".RDataTmp", ".RDataTmp12", "a.R"]) == [
        "R history",
        "temporary files",
        "temporary files",
        None,
    ]
