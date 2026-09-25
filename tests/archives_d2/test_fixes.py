"""metacheck bugs fixed in the DataONE / DSpace / 4TU / PsychArchives / ResearchBox ports.

See docs/UPSTREAM_ISSUES.md; the parity cases of these fixes are marked
``known_divergence`` and these tests pin the fixed behaviour.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pytacheck.archives.fourtu import researchdata4tu_file_download
from pytacheck.archives.psycharchives import _psycharchives_info, psycharchives_file_download
from pytacheck.archives.researchbox import _rbox_info, rbox_file_download

Serve = Callable[[dict[str, httpx.Response]], list[httpx.Request]]


def test_4tu_download_by_uuid(serve: Serve, tmp_path: Path) -> None:
    # U41: metacheck re-resolves 4TU ids as Figshare ids, so an article known
    # only by its uuid downloads nothing
    uuid = "7f866e02-eb39-4a2a-8f7d-2d053ee6cde9"
    body = b"a,b\n1,2\n"
    serve(
        {
            f"https://data.4tu.nl/v2/articles/{uuid}": httpx.Response(
                200,
                json={
                    "id": 501,
                    "title": "Only a uuid",
                    "files": [
                        {
                            "id": 7,
                            "name": "data.csv",
                            "size": len(body),
                            "download_url": "https://data.4tu.nl/file/x/7",
                            "computed_md5": hashlib.md5(body).hexdigest(),
                        }
                    ],
                },
            ),
            "https://data.4tu.nl/file/x/7": httpx.Response(200, content=body),
        }
    )
    dl = researchdata4tu_file_download(f"https://doi.org/10.4121/{uuid}", download_to=str(tmp_path))
    assert dl is not None
    assert dl["researchdata4tu_id"].tolist() == [uuid]
    assert dl["downloaded"].tolist() == [True]
    assert (tmp_path / f"figshare_{uuid}" / "data.csv").read_bytes() == body


def test_psycharchives_handle_on_an_unknown_host(serve: Serve) -> None:
    # U42: metacheck's `%||%` never falls back from NA and requests https://NA/rest/...
    seen = serve({})
    with pytest.warns(UserWarning, match="could not be found"):
        info = _psycharchives_info("https://hdl.handle.net/20.500.11780/3456")
    assert info["error"].tolist() == ["unfound"]
    assert [str(r.url) for r in seen] == [
        "https://www.psycharchives.org/rest/handle/20.500.11780/3456"
    ]


def test_several_urls_that_all_fail_give_none(serve: Serve) -> None:
    # U43: metacheck's join on the empty result errors
    serve({})
    with pytest.warns(UserWarning):
        assert (
            psycharchives_file_download(
                ["https://hdl.handle.net/20.500.12034/1", "https://hdl.handle.net/20.500.12034/2"]
            )
            is None
        )
    with pytest.warns(UserWarning):
        assert (
            rbox_file_download(["https://researchbox.org/1", "https://researchbox.org/2"]) is None
        )


def test_rbox_empty_page_is_unfound(serve: Serve) -> None:
    # U45: metacheck reads the body before the status check, so an empty answer
    # is an error rather than "unfound"
    serve({"https://researchbox.org/77": httpx.Response(200, content=b"")})
    with pytest.warns(UserWarning, match="could not be found"):
        info = _rbox_info("https://researchbox.org/77")
    assert info["error"].tolist() == ["unfound"]
    serve({"https://researchbox.org/78": httpx.Response(404, content=b"")})
    with pytest.warns(UserWarning, match="could not be found"):
        assert _rbox_info("https://researchbox.org/78")["error"].tolist() == ["unfound"]
