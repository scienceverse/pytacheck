"""Write the recorded PubPeer responses for the synthetic ref_pubpeer papers.

Run from the repository root::

    .venv/bin/python -m tests.mod_ref_pubpeer_summary.make_mocks

The responses are hand-made (in PubPeer's v3 ``publications`` format) for the
DOI sets in :data:`tests.mod_ref_pubpeer_summary.helpers.PP_DOIS` and the
psychsci fixture papers; the files are named the way httptest2 names them
(``build_mock_url()``: the POST body's hash), so metacheck (via
``httptest2::with_mock_dir()``) and pytacheck (via ``tests.httpmock.replay()``)
read the same responses.
"""

from __future__ import annotations

import json
import shutil
from typing import Any

import httpx

from tests.httpmock import mock_path
from tests.mod_ref_pubpeer_summary import helpers as H

_URL = "https://pubpeer.com/v3/publications?devkey=PubPeerZotero"


def _fb(doi: str, n: int, users: Any, url: str | None = "auto") -> dict[str, Any]:
    fb: dict[str, Any] = {"id": doi, "title": f"A paper ({doi})", "total_comments": n}
    if url == "auto":
        fb["url"] = "https://pubpeer.com/publications/" + doi.split("/")[-1].upper()
    elif url is not None:
        fb["url"] = url
    fb["users"] = users
    return fb


def _module_dois(paper: Any) -> list[str]:
    """The DOIs ref_pubpeer sends to pubpeer_comments() for *paper*."""
    from pytacheck.modules.ref_pubpeer import _refs_with_doi

    return _refs_with_doi(paper)["doi"].tolist()


def _path(dois: list[str]) -> str:
    from pytacheck.db.pubpeer import _request_body

    body = _request_body([d.lower() for d in dois])
    request = httpx.Request(
        "POST",
        _URL,
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json;charset=UTF-8"},
    )
    return mock_path(request)


def _write_json(dois: list[str], feedbacks: list[dict[str, Any]]) -> None:
    f = H.MOCK_DIR / f"{_path(dois)}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    body = {"status": "good", "feedbacks": feedbacks}
    f.write_text(json.dumps(body, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_status(dois: list[str], status: int) -> None:
    f = H.MOCK_DIR / f"{_path(dois)}.R"
    f.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"status": "bad", "error": "Server Error"}).replace('"', '\\"')
    f.write_text(
        'structure(list(method = "POST", url = "'
        + _URL
        + f'", \n    status_code = {status}L, headers = structure(list(`content-type` = '
        + '"application/json"), class = "httr2_headers"), \n    body = charToRaw("'
        + body
        + '"), \n    cache = new.env(parent = emptyenv())), class = "httr2_response")\n',
        encoding="utf-8",
    )


def main() -> None:
    import pytacheck as pc

    if H.MOCK_DIR.exists():
        shutil.rmtree(H.MOCK_DIR)

    _write_json(
        _module_dois(H.pp_single()),
        [_fb("10.9999/pp.one", 2, "Jane Doe, John Roe")],
    )
    _write_json(
        _module_dois(H.pp_statcheck()),
        [_fb("10.9999/pp.stat", 1, "Statcheck ")],
    )
    _write_status(_module_dois(H.pp_fail()), 500)
    _write_json(
        _module_dois(H.pp_list()),
        [
            _fb("10.9999/pp.upper", 4, "A. Person"),
            _fb("10.9999/pp.zero", 0, "Nobody"),
            _fb("10.9999/pp.dup", 1, "Dup Checker"),
            _fb("10.9999/pp.nourl", 3, "No Url", url=None),
            _fb("10.9999/pp.stat", 1, "Statcheck "),
            _fb("10.9999/pp.many", 12, ["Alpha", " Beta ", "Gamma"]),
        ],
    )
    _write_json(
        _module_dois(H.pp_mixed()),
        [
            _fb("10.9999/pp.one", 2, "Jane Doe, John Roe"),
            _fb("10.9999/pp.stat", 1, "Statcheck "),
            _fb(
                "10.1177/0956797614520714",
                3,
                "Statcheck , Aioliops Novaeguineae, Hoya Camphorifolia",
                url="https://pubpeer.com/publications/3FA648ECECB88454C91804F09E2E56",
            ),
        ],
    )
    # the psychsci fixture papers, together and one at a time
    ps = H.psychsci3()
    dois = _module_dois(ps)
    _write_json(
        dois,
        [
            _fb(dois[0], 2, "Reviewer One"),
            _fb(dois[3], 1, "Statcheck "),
            _fb(dois[-1], 5, "Reviewer Two, Reviewer Three"),
        ],
    )
    for p in ps:
        own = _module_dois(pc.PaperList([p]))
        hits = [d for d in (dois[0], dois[3], dois[-1]) if d in own]
        _write_json(own, [_fb(d, 2, "Reviewer One") for d in hits])

    # ---- review scenarios (mod_ref_pubpeer_summary_review) ----
    # empty users, a null total_comments, Statcheck as a one-element list, an
    # empty URL, lower-case "statcheck", and a DOI that was not asked for
    null_tc = _fb("10.9999/pp.nulltc", 0, "Someone")
    null_tc["total_comments"] = None
    _write_json(
        _module_dois(H.pp_odd()),
        [
            _fb("10.9999/pp.emptyusers", 2, []),
            null_tc,
            _fb("10.9999/pp.statlist", 4, ["Statcheck"]),
            _fb("10.9999/pp.emptyurl", 1, "Empty Url", url=""),
            _fb("10.9999/pp.statcase", 1, "statcheck"),
            _fb("10.9999/pp.unrequested", 9, "Not Asked"),
        ],
    )
    # the only commented reference has no URL (no url column at all) ...
    _write_json(_module_dois(H.pp_nourl_only()), [_fb("10.9999/pp.nourl", 3, "No Url", url=None)])
    # ... or an NA URL (another feedback has one)
    _write_json(
        _module_dois(H.pp_nourl_some()),
        [_fb("10.9999/pp.nourl", 3, "No Url", url=None), _fb("10.9999/pp.zero", 0, "Nobody")],
    )
    # SICI and non-ASCII DOIs: PubPeer answers with the lower-cased ids
    _write_json(
        _module_dois(H.pp_sici()),
        [
            _fb("10.1002/(sici)1099-0720(199908)13:4<333::aid-acp588>3.0.co;2-z", 3, "Sici Fan"),
            _fb("10.9999/pp.ä", 1, "Umlaut"),
            _fb("10.9999/ß.x", 2, "Eszett"),
        ],
    )
    # bibr export schema 12.0 fixture papers (DOIs from ref_table(), bib_match included)
    for name in ("preprint", "PMC4383902"):
        dois = _module_dois(H.bibr12(name))
        _write_json(
            dois,
            [
                _fb(dois[1], 4, "Reviewer One"),
                _fb(dois[-1], 1, "Statcheck "),
                _fb(dois[len(dois) // 2], 2, ["Reviewer Two", "Statcheck"]),
            ],
        )
    # mixed-case paper ids with a bib_match table (DOIs in ref_table()'s C-locale order)
    _write_json(
        _module_dois(H.pp_case_list()),
        [
            _fb("10.9999/pp.one", 2, "Jane Doe, John Roe"),
            _fb("10.9999/pp.many", 12, ["Alpha", " Beta ", "Gamma"]),
            _fb("10.9999/pp.stat", 1, "Statcheck "),
            _fb("10.9999/pp.dup", 1, "Dup Checker"),
            _fb("10.9999/pp.zero", 0, "Nobody"),
        ],
    )


if __name__ == "__main__":
    main()
