"""Port of tests/testthat/test-import-convert.R.

metacheck's own tests fetch the online server list and talk to real
servers; here the server list is served locally and Grobid answers from
metacheck's recorded responses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pytacheck as pc
from pytacheck.io.convert import convert
from pytacheck.papers.validate import paper_validate
from tests.io.conftest import GROBID_URL, SERVERS_URL, api, json_response

GROBID_SERVERS = [{"id": "tue", "service": "grobid", "url": GROBID_URL}]


def test_convert_needs_documents(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        convert()  # type: ignore[call-arg]

    # no files
    empty = tmp_path / "emptydir"
    empty.mkdir()
    with pytest.raises(ValueError, match="No PDF, XML, DOC or DOCX files detected"):
        convert(empty)

    # no relevant files
    (empty / "hi.txt").write_text("hi\n")
    with pytest.raises(ValueError, match="No PDF, XML, DOC or DOCX files detected"):
        convert(empty)


def test_bad_method() -> None:
    with pytest.raises(ValueError, match="'arg' should be one of"):
        convert(pc.demofile("pdf"), method="pandoc")


def test_xml_needs_server_list_like_r(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R looks up the online server list even for XML input (no api_url given)."""
    import pytacheck.utils

    monkeypatch.setattr(pytacheck.utils, "online", lambda *a, **k: False)
    with pytest.raises(ConnectionError, match="online versions not available"):
        convert(pc.demofile("xml"), tmp_path)


@pytest.mark.parametrize("method", ["auto", "grobid"])
def test_xml(tmp_path: Path, online: None, method: str) -> None:
    with api("apis", routes={("GET", SERVERS_URL): json_response(GROBID_SERVERS)}):
        obs = convert(pc.demofile("xml"), tmp_path, method)
    assert obs.endswith(".json")
    assert Path(obs).exists()
    paper = pc.read(obs)
    assert paper_validate(paper)
    # no bib_match unless crossref_lookup = TRUE
    assert "bib_match" not in paper


def test_xml_null_save_path_with_api_url() -> None:
    paper = convert(pc.demofile("xml"), None, api_url="unused")
    assert isinstance(paper, pc.Paper)


def test_xml_crossref(tmp_path: Path, online: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytacheck.db.crossref as crossref

    def add_bib_match(p: pc.Paper) -> pc.Paper:
        p = p.copy()
        p["bib_match"] = p.bib[["bib_id"]].assign(service="crossref")
        return p

    monkeypatch.setattr(crossref, "add_bib_match", add_bib_match)
    with api("apis", routes={("GET", SERVERS_URL): json_response(GROBID_SERVERS)}):
        obs = convert(pc.demofile("xml"), tmp_path, crossref_lookup=True)
    paper = pc.read(obs)
    assert paper_validate(paper)
    assert "bib_match" in paper


def test_pdf_auto(tmp_path: Path, online: None) -> None:
    with api("apis", routes={("GET", SERVERS_URL): json_response(GROBID_SERVERS)}):
        obs = convert(pc.demofile("pdf"), tmp_path, api_url=GROBID_URL)
    assert obs.endswith(".json")
    assert Path(obs).exists()
    assert paper_validate(pc.read(obs))


@pytest.mark.parametrize("keep_xml", [True, False])
def test_pdf_grobid_keep_xml(tmp_path: Path, keep_xml: bool) -> None:
    with api("apis"):
        obs = convert(pc.demofile("pdf"), tmp_path, "grobid", api_url=GROBID_URL, keep_xml=keep_xml)
    assert obs.endswith(".json")
    assert Path(obs).exists()
    xml = obs[: -len(".json")] + ".xml"
    assert Path(xml).exists() is keep_xml
    if keep_xml:
        assert Path(obs).name == "to_err_is_human.json"
    else:
        # R converts a tempfile() XML, so the JSON is named after it
        assert Path(obs).name.startswith("file")
    assert paper_validate(pc.read(obs))


def test_auto_prefers_local_grobid(tmp_path: Path) -> None:
    import httpx

    local = "http://localhost:8070"
    xml = (pc.demofile("xml")).read_bytes()
    routes = {
        ("GET", f"{local}/api/isalive"): lambda _r: httpx.Response(200, text="true"),
        ("POST", f"{local}/api/processFulltextDocument"): lambda _r: httpx.Response(
            200, content=xml
        ),
    }
    with api("apis", routes=routes):
        obs = convert(pc.demofile("pdf"), tmp_path)
    assert Path(obs).name == "to_err_is_human.json"


def test_bibr_method_passes_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R hands convert_bibr() the argument *names*; pytacheck passes the values."""
    import functools

    import pytacheck.io.bibr_convert as bc

    seen = {}

    @functools.wraps(bc.convert_bibr)  # keeps the signature convert() filters on
    def fake_convert_bibr(**kwargs):
        seen.update(kwargs)
        return "out.json"

    monkeypatch.setattr(bc, "convert_bibr", fake_convert_bibr)
    doc = pc.demofile("docx")
    assert convert(doc, tmp_path, api_url="http://bibr.example", api_key="k") == "out.json"
    assert seen == {
        "api_url": "http://bibr.example",
        "api_key": "k",
        "file_path": doc,
        "save_path": tmp_path,
    }
