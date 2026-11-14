"""EPrints, Invenio and CKAN repositories beyond ReShare and Zenodo (metacheck 0.4.4.1, #636, #637).

Ports of the upstream tests in ``test-archive-reshare.R``, ``test-archive-zenodo.R`` and
``test-archive-ckan.R``; the portals' answers and DataCite's records are stood in for.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any

import httpx
import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives import _repo_tables, ckan, reshare, zenodo
from metacheck.archives.ckan import _ckan_id, ckan_info, ckan_links
from metacheck.archives.reshare import _reshare_id, _reshare_info, eprints_links, reshare_links
from metacheck.archives.zenodo import (
    _invenio_files,
    _zenodo_id,
    _zenodo_info,
    invenio_links,
    zenodo_links,
)

Serve = Callable[[dict[str, httpx.Response]], list[httpx.Request]]


def _datacite(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[str], tuple[str, str]]
) -> list[str]:
    """Stand in for ``.repo_doi_records()``: *answer* gives ``(url, resource_type)`` of a DOI."""
    from metacheck.archives import repo_kind

    asked: list[str] = []

    def records(doi: Any) -> list[dict[str, Any]]:
        asked.extend(doi)
        out = []
        for d in doi:
            url, rtype = answer(str(d).lower())
            out.append(
                {"doi": str(d).lower(), "url": url, "resource_type": rtype, "in_datacite": True}
            )
        return out

    monkeypatch.setattr(repo_kind, "_repo_doi_records", records)
    return asked


# -- tables ---------------------------------------------------------------------------


def test_survey_tables_have_the_sizes_the_survey_found() -> None:
    assert len(_repo_tables.dspace7_hosts_survey) == 100
    assert len(_repo_tables.dspace_legacy_hosts_survey) == 9
    assert len(_repo_tables.dspace7_doi_prefixes) == 254
    assert len(_repo_tables.dataverse_hosts_survey) == 48
    assert len(_repo_tables.eprints_hosts_survey) == 67
    assert len(_repo_tables.invenio_hosts_survey) == 38
    assert len(_repo_tables.ckan_hosts_survey) == 42
    # 250 -> 557 repository installations
    total = (
        len(set(reshare.EPRINTS_HOSTS)) + len(set(zenodo.INVENIO_HOSTS)) + len(set(ckan.CKAN_HOSTS))
    )
    assert total == 69 + 40 + 43


# -- EPrints --------------------------------------------------------------------------


def test_a_deposit_on_another_eprints_repository_is_host_slash_id() -> None:
    for h in ("datacompass.lshtm.ac.uk", "researchdata.gla.ac.uk", "uclandata.uclan.ac.uk"):
        assert h in reshare.EPRINTS_HOSTS
    ids = _reshare_id(
        [
            "https://datacompass.lshtm.ac.uk/id/eprint/4333",
            "https://datacompass.lshtm.ac.uk/4333/",
            "http://researchdata.gla.ac.uk/2235",
            "datacompass.lshtm.ac.uk/4333",
            "https://www.example.org/id/eprint/4333",
            "https://datacompass.lshtm.ac.uk/view/divisions/",
        ]
    )
    assert ids == [
        "datacompass.lshtm.ac.uk/4333",
        "datacompass.lshtm.ac.uk/4333",
        "researchdata.gla.ac.uk/2235",
        "datacompass.lshtm.ac.uk/4333",
        None,
        None,
    ]
    # ReShare's own deposits keep their plain id
    assert _reshare_id("https://reshare.ukdataservice.ac.uk/id/eprint/854001") == "854001"


def test_reshare_info_asks_the_deposits_own_eprints_host(serve: Serve) -> None:
    requests = serve({})
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        x = _reshare_info("datacompass.lshtm.ac.uk/4333")
        _reshare_info("854001")
    assert [str(r.url) for r in requests] == [
        "https://datacompass.lshtm.ac.uk/id/eprint/4333",
        "https://reshare.ukdataservice.ac.uk/id/eprint/854001",
    ]
    assert x["error"].iloc[0] == "unfound"
    assert any("could not be found on datacompass.lshtm.ac.uk" in str(w.message) for w in caught)


def test_eprints_links_finds_links_mentions_and_dois(monkeypatch: pytest.MonkeyPatch) -> None:
    # DataCite's records: a dataset, and a thesis on the same repository
    asked = _datacite(
        monkeypatch,
        lambda d: (
            ("https://datacompass.lshtm.ac.uk/id/eprint/1411", "Dataset")
            if "00001411" in d
            else ("https://datacompass.lshtm.ac.uk/id/eprint/77", "Dissertation")
        ),
    )
    paper = pc.test_paper(
        [
            "Data are at researchdata.gla.ac.uk/2235/ and doi:10.17037/DATA.00001411.",
            "Methods follow 10.1016/j.cognition.2020.104 and 10.5255/UKDA-SN-854001.",
            "Our earlier thesis is 10.17037/THESIS.77.",
        ],
        ["https://datacompass.lshtm.ac.uk/id/eprint/4333"],
    )
    links = eprints_links(paper)
    # the thesis is left out before anything is read
    assert set(links["reshare_id"]) == {
        "datacompass.lshtm.ac.uk/4333",
        "researchdata.gla.ac.uk/2235",
        "datacompass.lshtm.ac.uk/1411",
        "854001",
    }
    # only DOIs under an EPrints repository's prefix are looked up
    assert {a.lower() for a in asked} == {"10.17037/data.00001411", "10.17037/thesis.77"}
    # ReShare's own links are found as before
    assert reshare_links(paper)["reshare_id"].tolist() == ["854001"]


# -- Invenio --------------------------------------------------------------------------


def test_a_record_on_another_invenio_installation_is_host_slash_id() -> None:
    for h in ("zenodo.org", "fdr.uni-hamburg.de", "data.caltech.edu"):
        assert h in zenodo.INVENIO_HOSTS
    ids = _zenodo_id(
        [
            "https://data.caltech.edu/records/j4k4w-p8331",
            "https://data.caltech.edu/api/records/j4k4w-p8331/files",
            "https://www.fdr.uni-hamburg.de/record/14137",
            "data.caltech.edu/j4k4w-p8331",
            "https://zenodo.org/records/2669586",
            "10.5281/zenodo.2669586",
            "https://www.example.org/records/123",
        ]
    )
    # the host as the link writes it: Hamburg answers only with "www."
    assert ids == [
        "data.caltech.edu/j4k4w-p8331",
        "data.caltech.edu/j4k4w-p8331",
        "www.fdr.uni-hamburg.de/14137",
        "data.caltech.edu/j4k4w-p8331",
        "2669586",
        "2669586",
        None,
    ]


def test_invenio_files_reads_zenodos_inveniordms_and_an_older_invenios_files_alike() -> None:
    zen = [
        {
            "id": "a",
            "key": "x.csv",
            "size": 10,
            "checksum": "md5:1",
            "links": {"self": "https://zenodo.org/api/records/1/files/x.csv/content"},
        }
    ]
    rdm = {
        "enabled": True,
        "count": 1,
        "entries": {
            "y.R": {
                "id": "b",
                "key": "y.R",
                "size": 20,
                "checksum": "md5:2",
                "links": {
                    "self": "https://h/api/records/2/files/y.R",
                    "content": "https://h/api/records/2/files/y.R/content",
                },
            }
        },
    }
    old = [
        {
            "file_id": "c",
            "filename": "z.txt",
            "filesize": 30,
            "checksum": "3",
            "links": {
                "download": "https://h/record/3/files/z.txt",
                "self": "https://h/api/files/c",
            },
        }
    ]
    f = [*_invenio_files(zen), *_invenio_files(rdm), *_invenio_files(old)]
    assert [x["key"] for x in f] == ["x.csv", "y.R", "z.txt"]
    assert [float(x["size"]) for x in f] == [10, 20, 30]
    # the file's bytes, never InvenioRDM's description of the file
    assert [x["links"]["self"] for x in f] == [
        "https://zenodo.org/api/records/1/files/x.csv/content",
        "https://h/api/records/2/files/y.R/content",
        "https://h/record/3/files/z.txt",
    ]
    # a restricted record lists none
    assert _invenio_files({"enabled": True, "entries": {}}) == []


def test_zenodo_info_asks_the_records_own_invenio_installation(serve: Serve) -> None:
    requests = serve({})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _zenodo_info("data.caltech.edu/j4k4w-p8331")
        _zenodo_info("2669586")
    assert [str(r.url) for r in requests] == [
        "https://data.caltech.edu/api/records/j4k4w-p8331",
        "https://zenodo.org/api/records/2669586",
    ]


def test_invenio_links_finds_zenodo_and_other_invenio_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _datacite(
        monkeypatch, lambda d: ("https://www.fdr.uni-hamburg.de/record/139", "Dataset")
    )
    paper = pc.test_paper(
        [
            "Code is at data.caltech.edu/records/mzrjq-6wc02 and data at doi:10.25592/uhhfdm.139.",
            "See 10.5281/zenodo.2669586 and 10.1016/j.cognition.2020.104.",
        ],
        ["https://www.fdr.uni-hamburg.de/record/14137"],
    )
    links = invenio_links(paper)
    assert set(links["zenodo_id"]) == {
        "2669586",
        "data.caltech.edu/mzrjq-6wc02",
        "www.fdr.uni-hamburg.de/14137",
        "www.fdr.uni-hamburg.de/139",
    }
    # only the DOI under another installation's prefix is looked up
    assert [a.lower() for a in asked] == ["10.25592/uhhfdm.139"]
    # Zenodo's own links are found as before
    assert zenodo_links(paper)["zenodo_id"].tolist() == ["2669586"]
    # a paper with another installation's link and none to Zenodo
    only = invenio_links(pc.test_paper(["Data: https://www.fdr.uni-hamburg.de/record/14137 ."]))
    assert only["zenodo_id"].tolist() == ["www.fdr.uni-hamburg.de/14137"]


# -- CKAN -----------------------------------------------------------------------------


def test_ckan_id_reads_every_ckan_dataset_address() -> None:
    for h in ("data.cnra.ca.gov", "envidat.ch", "data.nhm.ac.uk", "open.canada.ca"):
        assert h in ckan.CKAN_HOSTS
    ids = _ckan_id(
        [
            "https://data.cnra.ca.gov/dataset/statewide-crop-mapping",
            "https://www.envidat.ch/#/metadata/chelsa-climatologies",
            "http://data.nhm.ac.uk/dataset/8c67d782-c01d-4807-8913-f57d0daabf35",
            "https://open.canada.ca/data/en/dataset/0c0a6e7b-4c1d-4b6f-9b1e-1a2b3c4d5e6f",
            "https://data.cnra.ca.gov/dataset/6c3d65e3/resource/1",
            "https://data.cnra.ca.gov/organization/cdfa",
            "https://www.example.org/dataset/x",
        ]
    )
    assert ids == [
        "data.cnra.ca.gov/statewide-crop-mapping",
        "www.envidat.ch/chelsa-climatologies",
        "data.nhm.ac.uk/8c67d782-c01d-4807-8913-f57d0daabf35",
        "open.canada.ca/0c0a6e7b-4c1d-4b6f-9b1e-1a2b3c4d5e6f",
        "data.cnra.ca.gov/6c3d65e3",
        None,
        None,
    ]


def test_ckan_info_lists_the_portals_own_files_not_links_to_other_websites(serve: Serve) -> None:
    body = {
        "result": {
            "title": "Statewide Crop Mapping",
            "license_title": "Creative Commons Attribution",
            "doi": "10.1/x",
            "author": "California DWR",
            "resources": [
                {
                    "name": "Crop map 2024",
                    "format": "ZIP",
                    "size": 176115282,
                    "url_type": "upload",
                    "url": "https://data.cnra.ca.gov/dataset/6c3d/resource/1/download/i15_crop_2024.zip",
                },
                {
                    "name": "Map service",
                    "format": "Data",
                    "url_type": "",
                    "url": "https://www.arcgis.com/home/item.html?id=39b6",
                },
                {
                    "name": "Readme",
                    "format": "PDF",
                    "size": 2000,
                    "url_type": "",
                    "url": "https://data.cnra.ca.gov/dataset/6c3d/resource/2/download",
                },
            ],
        }
    }
    url = "https://data.cnra.ca.gov/api/3/action/package_show?id=statewide-crop-mapping"
    requests = serve({url: httpx.Response(200, json=body)})
    x = ckan_info("https://data.cnra.ca.gov/dataset/statewide-crop-mapping")
    assert [str(r.url) for r in requests] == [url]
    assert x["title"].iloc[0] == "Statewide Crop Mapping"
    assert x["license"].iloc[0] == "Creative Commons Attribution"
    assert pd.isna(x["error"].iloc[0])
    f = x["files"].iloc[0]
    # the ArcGIS page is left out; a file without a name in its address is named after the
    # resource and its format
    assert f["file_name"].tolist() == ["i15_crop_2024.zip", "Readme.pdf"]
    assert f["file_size"].tolist() == [176115282, 2000]


def test_ckan_info_marks_a_dataset_the_portal_does_not_have(serve: Serve) -> None:
    serve({})
    x = ckan_info("https://data.cnra.ca.gov/dataset/none")
    assert x["error"].iloc[0] == "unfound"


def test_ckan_links_finds_dataset_links_and_dois(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _datacite(
        monkeypatch,
        lambda d: ("https://www.envidat.ch/#/metadata/chelsa-climatologies", "Dataset"),
    )
    paper = pc.test_paper(
        [
            "Crops were mapped from data.cnra.ca.gov/dataset/statewide-crop-mapping.",
            "Climate data: doi:10.16904/envidat.228 and 10.1016/j.cognition.2020.104.",
        ]
    )
    links = ckan_links(paper)
    assert set(links["ckan_id"]) == {
        "data.cnra.ca.gov/statewide-crop-mapping",
        "www.envidat.ch/chelsa-climatologies",
    }
    assert [a.lower() for a in asked] == ["10.16904/envidat.228"]
    # a paper without CKAN links gives an empty table with the columns repo_check takes
    none = ckan_links(pc.test_paper(["No data."]))
    assert len(none) == 0
    assert {"paper_id", "href"} <= set(none.columns)


def test_converted_tables_match_the_generator() -> None:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    out = subprocess.run(
        [sys.executable, str(root / "scripts" / "convert_repo_tables.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stdout + out.stderr
