"""Tests for metacheck.archives.ldbase (port of test-archive-ldbase.R, plus mocked listings).

LDbase has no API, so the pages are made by hand with the structure of
LDbase's own (checked 2026-10-15) and served by the ``serve`` fixture; nothing
here calls the live site, except the tests marked ``network``.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable

import httpx
import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives import ldbase as ld
from metacheck.archives.ldbase import (
    _ldbase_item_files,
    _ldbase_page_meta,
    _ldbase_page_url,
    _ldbase_project_items,
    _ldbase_type,
    _read_html,
    ldbase_info,
    ldbase_links,
)
from metacheck.archives.zip_peek import _WAF_CHALLENGE_WARNED, _waf_challenge_check

Serve = Callable[[dict[str, httpx.Response]], list[httpx.Request]]

PROJECT = "https://ldbase.org/projects/55d2face-9550-48e2-990f-c04bfe6dded5"
DATASET = "https://ldbase.org/datasets/ef328591-e249-4803-b673-76f237717d1d"
CODE = "https://ldbase.org/code/642b77cc-6e5c-4631-88e8-3bad7253cd12"
DOCUMENT = "https://ldbase.org/documents/99033578-33fc-4882-b7d1-a03d38a23486"
DOI = "10.33009/ldbase.1790013418.b8ca"

# --------------------------------------------------------------------------- testthat ports


def test_exists() -> None:
    assert callable(ldbase_links)
    assert callable(ldbase_info)


def test_links_finds_item_links_bare_text_links_and_dois() -> None:
    paper = pc.test_paper(
        [
            "Data are on LDbase (https://ldbase.org/projects/55d2face-9550-48e2-990f-c04bfe6dded5) "
            "and at doi:10.33009/ldbase.1627053448.d151."
        ],
        ["https://ldbase.org/datasets/e54f6aad-e0e2-4b40-9017-d4e347d9a497/"],
    )
    links = ldbase_links(paper)
    assert set(links["ldbase_url"]) == {
        "https://ldbase.org/datasets/e54f6aad-e0e2-4b40-9017-d4e347d9a497",
        "https://ldbase.org/projects/55d2face-9550-48e2-990f-c04bfe6dded5",
        "10.33009/ldbase.1627053448.d151",
    }
    assert set(links["ldbase_type"]) == {"datasets", "projects", "doi"}
    # each link's type is the type of its own URL
    for url, kind in zip(links["ldbase_url"], links["ldbase_type"], strict=True):
        assert _ldbase_type(url) == [kind]


def test_links_ignore_ldbases_own_doi_and_its_home_page() -> None:
    paper = pc.test_paper(
        ["We thank LDbase (10.33009/ldbase)."],
        ["https://ldbase.org/", "https://ldbase.org/browse-data"],
    )
    assert len(ldbase_links(paper)) == 0


def test_links_of_a_paper_without_any_link() -> None:
    links = ldbase_links(pc.test_paper(["Nothing to see."], []))
    assert len(links) == 0
    assert {"href", "ldbase_url", "ldbase_type"} <= set(links.columns)


def test_links_drop_trailing_slashes_and_full_stops_and_duplicates() -> None:
    paper = pc.test_paper(
        [f"See {PROJECT}/. Also {PROJECT}."],
        [PROJECT + "/", f"https://doi.org/{DOI}."],
    )
    links = ldbase_links(paper)
    assert sorted(links["ldbase_url"]) == sorted([PROJECT, f"https://doi.org/{DOI}"])


def test_type_and_page_url_read_every_link_form() -> None:
    urls = [
        PROJECT,
        "http://ldbase.org:443/code/642b77cc-6e5c-4631-88e8-3bad7253cd12/",
        "ldbase.org/documents/99033578-33fc-4882-b7d1-a03d38a23486",
        "https://doi.org/10.33009/ldbase.1627053448.d151",
        "https://osf.io/abcde",
        None,
    ]
    assert _ldbase_type(urls) == ["projects", "code", "documents", "doi", None, None]
    assert _ldbase_page_url(urls[1]) == CODE
    assert _ldbase_page_url(urls[2]) == DOCUMENT
    assert _ldbase_page_url(urls[3]) is None
    assert _ldbase_page_url(None) is None
    assert _ldbase_type([]) == []


# Hand-made pages with the structure of LDbase's own (checked 2026-10-15).
def project_html(title: str = "LAMP Project") -> str:
    return (
        "<html><head><script type='application/ld+json'>"
        '[{"@type":"BreadcrumbList","itemListElement":[]},'
        '{"@type":"DataCatalog","name":"' + title + '"}]'
        "</script></head><body><h1>Project: " + title + "</h1>"
        "<div class='field field--name-field-doi'>"
        "<a href='https://doi.org/10.33009/ldbase.1790013418.b8ca'>doi</a></div>"
        # 'Most recent' block: not used, the tree is
        "<a href='/datasets/00000000-0000-0000-0000-000000000009'>recent</a>"
        "<ul id='project-tree-view-list-root'>"
        "<li class='project-tree-view project-tree-view-item-current'>"
        "<a href='/projects/55d2face-9550-48e2-990f-c04bfe6dded5'>Project: LAMP Project</a>"
        "<ul><li><a href='/datasets/ef328591-e249-4803-b673-76f237717d1d'>Dataset: Set 1</a></li>"
        "<li><a href='/code/642b77cc-6e5c-4631-88e8-3bad7253cd12'>Code: prep</a></li>"
        "<li><a href='/projects/11111111-2222-3333-4444-555555555555'>Project: child</a>"
        "<ul><li><a href='/documents/99033578-33fc-4882-b7d1-a03d38a23486'>Document: folder</a>"
        "</li></ul></li>"
        "</ul></li></ul></body></html>"
    )


def dataset_html(
    name: str = "LAMP Set 1 Analytic Dataset",
    href: str = "/system/files/datasets/2026-09/set%201.csv",
    license_: str | None = "Open Data Commons Attribution License (ODC-By)",
) -> str:
    lic = f',"license":"{license_}"' if license_ else ""
    return (
        "<html><head><script type='application/ld+json'>"
        '[{"@type":"BreadcrumbList","itemListElement":[]},'
        '{"@type":"Dataset","name":"' + name + '"' + lic + "}]"
        "</script></head><body>"
        "<div class='field field--name-field-file-upload field--type-file'>"
        f"<a href='{href}'>file</a></div>"
        "<div class='views-element-container'>"
        "<a href='/system/files/datasets/2025-01/old.csv'>v1</a></div>"
        "</body></html>"
    )


def code_html() -> str:
    return (
        "<html><body><h1>Code: Data prep</h1>"
        "<div class='field field--name-field-code-file'>"
        "<a href='/system/files/code/2026-09/prep.R'>prep.R</a></div></body></html>"
    )


def document_html() -> str:
    return (
        "<html><body><h1>Document: Codebook</h1>"
        "<div class='field field--name-field-document-file'>"
        "<a href='https://ldbase.org/system/files/documents/2026-09/book.pdf'>book.pdf</a></div>"
        "</body></html>"
    )


def page(text: str) -> httpx.Response:
    return httpx.Response(200, content=text.encode(), headers={"content-type": "text/html"})


def test_project_items_list_the_whole_hierarchy_tree_child_projects_included() -> None:
    assert set(_ldbase_project_items(_read_html(project_html()))) == {DATASET, CODE, DOCUMENT}


def test_project_items_fall_back_to_every_link_without_the_tree() -> None:
    html = _read_html(
        "<html><body><a href='/datasets/ef328591-e249-4803-b673-76f237717d1d'>x</a>"
        "<a href='/about'>about</a></body></html>"
    )
    assert _ldbase_project_items(html) == [DATASET]


def test_page_meta_reads_the_title_doi_and_licence() -> None:
    meta = _ldbase_page_meta(_read_html(project_html()))
    assert meta["title"] == "LAMP Project"
    assert meta["doi"] == DOI
    assert meta["license"] is None
    assert (
        _ldbase_page_meta(_read_html(dataset_html()))["license"]
        == "Open Data Commons Attribution License (ODC-By)"
    )


def test_page_meta_title_falls_back_to_the_heading() -> None:
    meta = _ldbase_page_meta(_read_html(code_html()))
    assert meta == {"title": "Data prep", "doi": None, "license": None}


def test_item_files_take_only_the_current_file_of_an_item() -> None:
    f = _ldbase_item_files(_read_html(dataset_html()), DATASET)
    assert len(f) == 1
    assert f["file_name"].iloc[0] == "set 1.csv"
    assert f["file_url"].iloc[0] == "https://ldbase.org/system/files/datasets/2026-09/set%201.csv"
    assert f["item_type"].iloc[0] == "datasets"
    assert list(f.columns) == ["file_name", "file_path", "file_url", "item_type", "item_url"]

    external = _read_html("<html><body><p>Stored elsewhere</p></body></html>")
    assert len(_ldbase_item_files(external, DATASET)) == 0


def test_item_files_keep_an_invalid_percent_escape_as_written() -> None:
    f = _ldbase_item_files(_read_html(dataset_html(href="/system/files/datasets/50%.csv")), DATASET)
    assert f["file_name"].tolist() == ["50%.csv"]


def test_item_files_make_urls_absolute_and_https() -> None:
    html = dataset_html(href="http://ldbase.org/system/files/datasets/x.csv")
    f = _ldbase_item_files(_read_html(html), DATASET)
    assert f["file_url"].tolist() == ["https://ldbase.org/system/files/datasets/x.csv"]


# --------------------------------------------------------------------------- info (mocked pages)


def test_info_lists_a_project_with_a_folder_per_item(serve: Serve, online: None) -> None:
    requests = serve(
        {
            PROJECT: page(project_html()),
            DATASET: page(dataset_html()),
            CODE: page(code_html()),
            DOCUMENT: page(document_html()),
        }
    )
    info = ldbase_info(PROJECT)
    assert info.shape[0] == 1
    row = info.iloc[0]
    assert pd.isna(row["error"])
    assert row["ldbase_page"] == PROJECT
    assert row["ldbase_type"] == "projects"
    assert row["title"] == "LAMP Project"
    assert row["doi"] == DOI
    assert row["license"] == "Open Data Commons Attribution License (ODC-By)"
    files = row["files"]
    assert files["file_path"].tolist() == [
        "LAMP Set 1 Analytic Dataset/set 1.csv",
        "Data prep/prep.R",
        "Codebook/book.pdf",
    ]
    assert files["item_type"].tolist() == ["datasets", "code", "documents"]
    assert "license" not in files.columns
    # one request per page, none for the child project's own page
    assert sorted(str(r.url) for r in requests) == sorted([PROJECT, DATASET, CODE, DOCUMENT])


def test_info_of_a_single_item_uses_its_own_page(serve: Serve, online: None) -> None:
    requests = serve({DATASET: page(dataset_html())})
    info = ldbase_info([DATASET + "/", DATASET + "/", None, ""])
    assert len(requests) == 1  # the link is cleaned, looked up once
    row = info.iloc[0]
    assert row["ldbase_url"] == DATASET + "/"
    assert row["ldbase_page"] == DATASET
    assert row["files"]["file_path"].tolist() == ["set 1.csv"]
    assert row["files"]["file_name"].tolist() == ["set 1.csv"]
    assert row["title"] == "LAMP Set 1 Analytic Dataset"


def test_info_takes_the_links_from_a_table(serve: Serve, online: None) -> None:
    serve({DATASET: page(dataset_html())})
    links = pd.DataFrame({"href": [DATASET], "ldbase_url": [DATASET]})
    assert ldbase_info(links, "ldbase_url")["ldbase_page"].tolist() == [DATASET]
    assert ldbase_info(links, 2)["ldbase_page"].tolist() == [DATASET]


def test_info_resolves_a_doi_through_the_handle_api(serve: Serve, online: None) -> None:
    from metacheck.archives.dataverse import _url_encode_reserved
    from metacheck.archives.dspace7 import DOI_HANDLE_API

    handle = f"{DOI_HANDLE_API}{_url_encode_reserved(DOI)}?type=URL"
    body = {"responseCode": 1, "values": [{"type": "URL", "data": {"value": PROJECT}}]}
    serve(
        {
            handle: httpx.Response(200, json=body),
            PROJECT: page(project_html()),
            DATASET: page(dataset_html()),
            CODE: page(code_html()),
            DOCUMENT: page(document_html()),
        }
    )
    info = ldbase_info(f"https://doi.org/{DOI}")
    assert info["ldbase_page"].tolist() == [PROJECT]
    assert info["ldbase_type"].tolist() == ["projects"]
    assert info["error"].isna().all()


def test_info_doi_that_leads_elsewhere_is_unfound(serve: Serve, online: None) -> None:
    from metacheck.archives.dataverse import _url_encode_reserved
    from metacheck.archives.dspace7 import DOI_HANDLE_API

    handle = f"{DOI_HANDLE_API}{_url_encode_reserved(DOI)}?type=URL"
    body = {"responseCode": 1, "values": [{"type": "URL", "data": {"value": "https://x.org/a"}}]}
    serve({handle: httpx.Response(200, json=body)})
    assert ldbase_info(DOI)["error"].tolist() == ["unfound"]


def test_info_doi_that_cannot_be_resolved(online: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "metacheck.archives.dspace7._handle_registered_url", lambda *a, **k: (None, False)
    )
    info = ldbase_info(DOI)
    assert info["error"].tolist() == ["doi_unresolved"]
    assert pd.isna(info["ldbase_type"].iloc[0])  # set only once a page is known
    assert info["files"].iloc[0] is None


def test_info_missing_page_is_unfound_and_the_others_are_still_listed(
    serve: Serve, online: None
) -> None:
    serve({DATASET: page(dataset_html())})  # CODE answers 404
    info = ldbase_info([CODE, DATASET])
    assert info["error"].iloc[0] == "unfound"
    assert pd.isna(info["error"].iloc[1])
    assert info["ldbase_page"].tolist() == [CODE, DATASET]
    assert info["files"].iloc[0] is None
    assert len(info["files"].iloc[1]) == 1


def test_info_item_stored_elsewhere_has_no_files(serve: Serve, online: None) -> None:
    serve({DATASET: page("<html><body><h1>Dataset: Elsewhere</h1></body></html>")})
    info = ldbase_info(DATASET)
    assert info["error"].isna().all()
    assert info["files"].iloc[0] is None
    assert info["title"].tolist() == ["Elsewhere"]


def test_info_not_a_ldbase_link_is_unfound(online: None) -> None:
    info = ldbase_info("https://ldbase.org/browse-data")
    assert info["error"].tolist() == ["unfound"]
    assert pd.isna(info["ldbase_type"].iloc[0])


def test_info_firewall_check_is_the_links_error(serve: Serve, online: None) -> None:
    _WAF_CHALLENGE_WARNED.clear()
    serve({DATASET: httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})})
    with pytest.warns(UserWarning, match="firewall answered with a browser check"):
        info = ldbase_info(DATASET)
    assert info["error"].tolist() == ["firewall browser check (AWS WAF challenge); try again later"]
    assert info["ldbase_type"].tolist() == ["datasets"]
    _WAF_CHALLENGE_WARNED.clear()


def test_info_of_nothing_is_an_empty_table(online: None) -> None:
    for empty in ([], [None, ""], pd.DataFrame({"u": [None]})):
        info = ldbase_info(empty, 1)
        assert len(info) == 0
        assert list(info.columns) == [
            "ldbase_url",
            "ldbase_page",
            "ldbase_type",
            "title",
            "doi",
            "license",
            "error",
        ]


def test_info_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("metacheck.utils.online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match=r"ldbase\.org seems to be offline"):
        ldbase_info(DATASET)


def test_info_cache_keeps_only_listings_without_an_error(serve: Serve, online: None) -> None:
    requests = serve({DATASET: page(dataset_html())})
    first = ldbase_info([DATASET, CODE], cache=True)
    assert len(requests) == 2
    again = ldbase_info([DATASET, CODE], cache=True)
    # the good listing came from the cache; the failed one was looked up again
    assert [str(r.url) for r in requests[2:]] == [CODE]
    assert (
        again["files"].iloc[0]["file_name"].tolist() == first["files"].iloc[0]["file_name"].tolist()
    )


def test_info_a_project_whose_items_have_no_licence(serve: Serve, online: None) -> None:
    serve(
        {
            PROJECT: page(project_html()),
            DATASET: page(dataset_html(license_=None)),
            CODE: page(code_html()),
            DOCUMENT: page(document_html()),
        }
    )
    info = ldbase_info(PROJECT)
    assert info["license"].isna().all()
    assert len(info["files"].iloc[0]) == 3


def test_info_joins_the_distinct_licences_of_a_project(serve: Serve, online: None) -> None:
    other = dataset_html(license_="CC0")
    # the project's code page names a second licence, the document repeats the first
    code = code_html().replace(
        "<body>",
        '<body><script type="application/ld+json">{"name":"Prep","license":"CC0"}</script>',
    )
    serve(
        {
            PROJECT: page(project_html()),
            DATASET: page(dataset_html()),
            CODE: page(code),
            DOCUMENT: page(other),
        }
    )
    lic = ldbase_info(PROJECT)["license"].iloc[0]
    assert lic == "Open Data Commons Attribution License (ODC-By); CC0"


def test_ldbase_pages_are_asked_for_with_html_and_a_throttle(serve: Serve, online: None) -> None:
    requests = serve({DATASET: page(dataset_html())})
    ldbase_info(DATASET)
    assert requests[0].headers["accept"] == "text/html"
    assert ld._throttle().capacity == 1


# --------------------------------------------------------------------------- firewall check


def test_waf_challenge_recognised_and_warns_once_per_host() -> None:
    _WAF_CHALLENGE_WARNED.clear()
    url = "https://ldbase.org/system/files/x.csv"
    challenge = httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})
    with pytest.warns(UserWarning, match="firewall answered with a browser check"):
        assert _waf_challenge_check(challenge, url) is True
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert _waf_challenge_check(challenge, url) is True
    ok = httpx.Response(206)
    assert _waf_challenge_check(ok, url) is False
    _WAF_CHALLENGE_WARNED.clear()


# --------------------------------------------------------------------------- live (opt-in)


@pytest.mark.network
def test_info_lists_a_real_project_and_dataset() -> None:  # pragma: no cover - live site
    import httpx as _httpx

    home = _httpx.get("https://ldbase.org/", timeout=20, follow_redirects=True).text
    if "ldbase" not in home.lower():
        pytest.skip("LDbase is not serving its own pages")
    info = ldbase_info(
        [
            "https://ldbase.org/datasets/e54f6aad-e0e2-4b40-9017-d4e347d9a497",
            "10.33009/ldbase.1627053448.d151",
        ]
    )
    assert info["error"].isna().all()
    assert info["ldbase_type"].tolist() == ["datasets", "projects"]
    assert info["files"].iloc[0]["file_name"].tolist() == ["read_theta.csv"]
    assert "ODC-By" in info["license"].iloc[0]
    assert "read_theta.csv" in [p.split("/")[-1] for p in info["files"].iloc[1]["file_path"]]


def test_json_ld_helper_ignores_unparsable_blocks() -> None:
    html = _read_html(
        "<html><head><script type='application/ld+json'>{not json</script>"
        "<script type='application/ld+json'>"
        + json.dumps({"name": "T", "license": "L"})
        + "</script>"
        "</head><body></body></html>"
    )
    assert _ldbase_page_meta(html) == {"title": "T", "doi": None, "license": "L"}
