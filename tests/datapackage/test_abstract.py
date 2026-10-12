"""Ethics from the paper's abstract: the opt-in lookup of ``package_docs``.

No test reaches the internet. The two services are mocked with respx, which also
says how many requests were made and what they held; a test that must make none
replaces ``metacheck.http.request`` with a function that fails the test (it fails
with pytest's own exception, which the code under test cannot swallow).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

import metacheck as pc
from metacheck.cli import main
from metacheck.datapackage import check_package, package_selection, report_package
from metacheck.datapackage.abstract import (
    TRIGGER_CHARS,
    abstract_text,
    clean_doi,
    decide_from_abstract,
    lookup_asked,
    readme_doi,
)
from metacheck.datapackage.docs import load_readme_template
from metacheck.module import module_run
from metacheck.report import module_report

DOI = "10.1234/abc.def-1"
OTHER = "10.5678/zzz"
CROSSREF = "api.crossref.org"
OPENALEX = "api.openalex.org"

YES = (
    "<jats:p>Background: sleep is poorly understood. Participants were recruited from a panel "
    "(N = 120) and completed a survey.</jats:p>"
)
NO = (
    "<jats:p>We propose a new estimator for panel data and prove its consistency. "
    "Quasi-experimental widgets outperform their rivals in simulations.</jats:p>"
)
UNRELATED = "Quasi-experimental widgets outperform their rivals in simulations."
SECRET = "SECRET-README-MARKER-9137"

README = f"""# Study

Project or Paper Title : https://doi.org/{DOI}
Notes : {SECRET}

## Contact
E-mail: a@example.org
"""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _package(tmp_path: Path, readme: str | None = README) -> Path:
    root = tmp_path / "pkg"
    root.mkdir(exist_ok=True)
    if readme is not None:
        (root / "README.md").write_text(readme, encoding="utf-8")
    (root / "secret_participant_list.csv").write_text("a\n1\n", encoding="utf-8")
    return root


def _crossref_body(abstract: str | None) -> dict[str, Any]:
    message: dict[str, Any] = {"DOI": DOI, "title": ["A title"]}
    if abstract is not None:
        message["abstract"] = abstract
    return {"status": "ok", "message-type": "work", "message": message}


def _inverted(text: str) -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    for position, word in enumerate(text.split()):
        index.setdefault(word, []).append(position)
    return index


def _openalex_body(text: str | None) -> dict[str, Any]:
    return {"abstract_inverted_index": None if text is None else _inverted(text)}


def _mock() -> respx.MockRouter:
    """A router that does not insist every route is called (a hit at Crossref asks no one else)."""
    return respx.mock(assert_all_called=False)


def _not_found() -> httpx.Response:
    return httpx.Response(404, text="Resource not found.", headers={"content-type": "text/plain"})


def _services(
    router: respx.MockRouter,
    crossref: httpx.Response | Exception | None = None,
    openalex: httpx.Response | Exception | None = None,
) -> tuple[respx.Route, respx.Route]:
    """Routes for the two services (a service that is not given answers 404)."""
    routes = []
    for host, answer in ((CROSSREF, crossref), (OPENALEX, openalex)):
        route = router.get(host=host)
        if isinstance(answer, Exception):
            route.mock(side_effect=answer)
        else:
            route.mock(return_value=answer if answer is not None else _not_found())
        routes.append(route)
    return routes[0], routes[1]


def _lookup(root: Path, **kwargs: Any) -> Any:
    return module_run(
        None, "datapackage::package_docs", local_path=str(root), abstract_lookup=True, **kwargs
    )


def _status(out: Any, part: str) -> str:
    return str(out.components.set_index("id").loc[part, "status"])


def _detail(out: Any, part: str) -> str:
    return str(out.components.set_index("id").loc[part, "detail"])


@pytest.fixture
def no_requests(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Any request through the shared HTTP layer fails the test."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        pytest.fail(f"a request was made: {args!r}")

    monkeypatch.setattr("metacheck.http.request", refuse)
    yield


# --------------------------------------------------------------------------
# the DOI
# --------------------------------------------------------------------------

JCLP = "10.1002/(SICI)1097-4679(199911)55:11<1339::AID-JCLP1>3.0.CO;2-L"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("10.1234/abc", "10.1234/abc"),
        ("https://doi.org/10.1234/abc", "10.1234/abc"),
        ("http://dx.doi.org/10.1234/abc", "10.1234/abc"),
        ("HTTPS://DOI.ORG/10.1234/ABC", "10.1234/ABC"),
        ("doi.org/10.1234/abc", "10.1234/abc"),
        ("doi:10.1234/abc", "10.1234/abc"),
        ("DOI: 10.1234/abc", "10.1234/abc"),
        ("  <https://doi.org/10.1234/abc>.  ", "10.1234/abc"),
        ("(doi:10.1234/abc),", "10.1234/abc"),
        (JCLP, JCLP),
    ],
)
def test_clean_doi_accepts_the_usual_forms(given: str, expected: str) -> None:
    assert clean_doi(given) == expected


@pytest.mark.parametrize(
    "bad",
    [
        None,
        42,
        "",
        "   ",
        "not a doi",
        "10.12/abc",  # too few digits in the prefix
        "10.1234/",
        "10.1234/ab c",
        "10.1234/abc?x=1",
        "10.1234/abc#frag",
        "10.1234/abc%2Fdef",
        "10.1234/abc?x=1%41",  # an escape elsewhere must not let the "?" through
        "10.1234/abc\n10.5678/def",
        "https://evil.example/10.1234/abc",
        "https://doi.org.evil.example/10.1234/abc",
        "ftp://doi.org/10.1234/abc",
        "../10.1234/abc",
        "10.1234/" + "a" * 300,
    ],
)
def test_clean_doi_refuses_everything_else(bad: Any) -> None:
    assert clean_doi(bad) is None


def test_lookup_asked_is_a_clear_yes_only() -> None:
    assert lookup_asked(True) and lookup_asked("true") and lookup_asked(" Yes ") and lookup_asked(1)
    assert not any(lookup_asked(v) for v in (None, False, 0, "", "false", "no", "maybe", "treu"))


# --------------------------------------------------------------------------
# markup
# --------------------------------------------------------------------------


def test_abstract_text_strips_jats_and_html() -> None:
    jats = (
        "<jats:title>Abstract</jats:title><jats:p>We tested <jats:italic>N</jats:italic> = 20 "
        "adults; p&lt;.05 &amp; more.</jats:p><jats:p>Second<jats:sup>1</jats:sup> paragraph.</jats:p>"
    )
    assert (
        abstract_text(jats) == "Abstract We tested N = 20 adults; p<.05 & more. Second1 paragraph."
    )
    assert abstract_text("<p>One.</p><p>Two <b>bold</b>.</p>") == "One. Two bold."
    # a "<" in running text is not a tag
    assert abstract_text("Effects with p < .05 and n<10 were found.") == (
        "Effects with p < .05 and n<10 were found."
    )
    assert abstract_text(None) == "" and abstract_text(12) == ""
    assert len(abstract_text("x " * 50_000)) <= 20_000


# --------------------------------------------------------------------------
# the DOI in the README
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Project or Paper Title : https://doi.org/{DOI}\n", DOI),
        ("DOI of the publication: 10.1234/abc.\n", "10.1234/abc."),
        ("- Related publication: [10.1234/abc](https://doi.org/10.1234/abc)\n", "10.1234/abc"),
        ("**DOI of the paper:** doi:10.1234/abc\n", "10.1234/abc"),
        ("Associated article (DOI) :\n\n  10.1234/abc\n", "10.1234/abc"),
        # not about the publication: the package's own DOI, or no label at all
        ("Dataset DOI: 10.1234/own\n", None),
        ("DOI: 10.1234/maybe-the-dataset\n", None),
        ("Version of the data: 10.1234/x\n", None),
        # a DOI in a sentence or a reference list is never taken
        ("This accompanies the paper at https://doi.org/10.1234/abc\n", None),
        ("References\n- Smith (2020). Title. https://doi.org/10.1234/abc\n", None),
        ("Project or Paper Title : <provide DOI of publication if applicable>\n", None),
        ("", None),
    ],
)
def test_readme_doi_takes_only_the_line_for_the_publication(
    text: str, expected: str | None
) -> None:
    assert readme_doi(text) == expected
    assert readme_doi(None) is None


def test_readme_doi_follows_a_field_of_the_template() -> None:
    template = load_readme_template(
        {
            "sections": [
                {
                    "id": "general",
                    "title": "General",
                    "fields": [
                        {"id": "doi", "label": "DOI", "match": ["^doi\\b"], "expect": "doi"}
                    ],
                }
            ]
        }
    )
    text = "DOI : 10.1234/declared\n"
    assert readme_doi(text) is None  # no template: a bare "DOI" says nothing
    assert readme_doi(text, template) == "10.1234/declared"
    assert readme_doi("Dataset DOI: 10.1234/x\n", template) is None


# --------------------------------------------------------------------------
# the lookup
# --------------------------------------------------------------------------


def test_a_crossref_hit_decides_that_people_were_involved(tmp_path: Path) -> None:
    root = _package(tmp_path)
    with _mock() as router:
        crossref, openalex = _services(router, httpx.Response(200, json=_crossref_body(YES)))
        out = _lookup(root, paper_doi=f"https://doi.org/{DOI}")
    assert crossref.call_count == 1 and openalex.call_count == 0
    assert out.human_participants is True
    assert out.human_participants_source == "abstract"
    assert out.abstract_decision == {
        "people": True,
        "source": "abstract",
        "outcome": "decided",
        "doi": DOI,
        "service": "Crossref",
        "trigger": "Participants were recruited from a panel (N = 120) and completed a survey.",
    }
    assert out.human_participants_note.startswith(f"Decided from the abstract of {DOI} (Crossref)")
    # what the answer changes: the parts that depend on it are now required, and missing
    assert _status(out, "ethics") == _status(out, "consent") == "fail"
    detail = _detail(out, "ethics")
    assert "The research involved people" in detail and f"abstract of {DOI} (Crossref)" in detail
    assert out.traffic_light == "red"


def test_the_request_is_the_doi_alone(tmp_path: Path) -> None:
    root = _package(tmp_path)
    with _mock() as router:
        crossref, _ = _services(router, httpx.Response(200, json=_crossref_body(YES)))
        _lookup(root, paper_doi=DOI)
    (call,) = crossref.calls
    request = call.request
    assert request.method == "GET"
    assert str(request.url) == "https://api.crossref.org/works/10.1234%2Fabc.def-1"
    assert request.content == b""
    seen = " ".join([str(request.url), *(f"{k}={v}" for k, v in request.headers.items())])
    for private in (SECRET, "secret_participant_list", "README", "pkg", str(root)):
        assert private not in seen


def test_an_openalex_hit_when_crossref_has_no_abstract(tmp_path: Path) -> None:
    root = _package(tmp_path)
    text = abstract_text(YES)
    with _mock() as router:
        crossref, openalex = _services(
            router,
            httpx.Response(200, json=_crossref_body(None)),
            httpx.Response(200, json=_openalex_body(text)),
        )
        out = _lookup(root, paper_doi=DOI)
    assert crossref.call_count == 1 and openalex.call_count == 1
    assert str(openalex.calls[0].request.url) == (
        "https://api.openalex.org/works/https://doi.org/10.1234%2Fabc.def-1"
        "?select=abstract_inverted_index"
    )
    assert out.human_participants is True
    assert out.human_participants_source == "abstract"
    assert out.abstract_decision["service"] == "OpenAlex"
    assert f"abstract of {DOI} (OpenAlex)" in out.human_participants_note


def test_openalex_when_crossref_has_no_record(tmp_path: Path) -> None:
    root = _package(tmp_path)
    with _mock() as router:
        _services(router, None, httpx.Response(200, json=_openalex_body(abstract_text(YES))))
        out = _lookup(root, paper_doi=DOI)
    assert out.human_participants is True
    assert out.abstract_decision["service"] == "OpenAlex"


def test_an_abstract_without_participants_proves_nothing(tmp_path: Path) -> None:
    root = _package(tmp_path)
    with _mock() as router:
        _services(router, httpx.Response(200, json=_crossref_body(NO)))
        out = _lookup(root, paper_doi=DOI)
    # open, and never False
    assert out.human_participants is None
    assert out.human_participants_source == "unknown"
    assert out.abstract_decision["outcome"] == "no-mention"
    assert out.abstract_decision["people"] is None and out.abstract_decision["trigger"] is None
    assert "does not mention participants" in out.human_participants_note
    assert _status(out, "ethics") == _status(out, "consent") == "manual"
    assert "does not mention participants" in _detail(out, "ethics")


def test_a_record_without_an_abstract_leaves_it_open(tmp_path: Path) -> None:
    root = _package(tmp_path)
    for crossref_body in (_crossref_body(None), _crossref_body("")):
        with _mock() as router:
            _services(
                router,
                httpx.Response(200, json=crossref_body),
                httpx.Response(200, json=_openalex_body(None)),
            )
            out = _lookup(root, paper_doi=DOI)
        assert out.human_participants is None
        assert out.abstract_decision["outcome"] == "no-abstract"
        assert "No abstract was found" in out.human_participants_note
        assert "Crossref has no abstract" in out.human_participants_note
        assert _status(out, "ethics") == "manual"
    # nobody has the record
    with _mock() as router:
        _services(router)
        out = _lookup(root, paper_doi=DOI)
    assert out.abstract_decision["outcome"] == "no-abstract"
    assert "Crossref has no record; OpenAlex has no record" in out.human_participants_note


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("no route to host"),
        httpx.ReadTimeout("too slow"),
        httpx.Response(500, text="oops"),
        httpx.Response(429, text="slow down"),
        httpx.Response(200, text="<html>not json</html>", headers={"content-type": "text/html"}),
        httpx.Response(200, content=b"", headers={"content-type": "application/json"}),
    ],
)
def test_a_failing_lookup_degrades_to_the_current_behaviour(
    tmp_path: Path, failure: httpx.Response | Exception
) -> None:
    root = _package(tmp_path)
    plain = module_run(None, "datapackage::package_docs", local_path=str(root))
    with _mock() as router:
        _services(router, failure, failure)
        out = _lookup(root, paper_doi=DOI)
    assert out.human_participants is None and out.human_participants_source == "unknown"
    assert out.abstract_decision["outcome"] == "unreachable"
    assert "could not be fetched" in out.human_participants_note
    assert _status(out, "ethics") == _status(out, "consent") == "manual"
    assert out.checklist["status"].tolist() == plain.checklist["status"].tolist()
    assert out.traffic_light == plain.traffic_light


def test_one_service_failing_does_not_stop_the_other(tmp_path: Path) -> None:
    root = _package(tmp_path)
    with _mock() as router:
        _services(
            router,
            httpx.ConnectError("down"),
            httpx.Response(200, json=_openalex_body(abstract_text(YES))),
        )
        out = _lookup(root, paper_doi=DOI)
    assert out.human_participants is True and out.abstract_decision["service"] == "OpenAlex"
    # Crossref has no record and OpenAlex does not answer: not "unreachable", but still open
    with _mock() as router:
        _services(router, None, httpx.ConnectError("down"))
        out = _lookup(root, paper_doi=DOI)
    assert out.abstract_decision["outcome"] == "no-abstract"
    assert "Crossref has no record; OpenAlex did not answer" in out.human_participants_note


@pytest.mark.parametrize(
    "malformed",
    [
        {"abstract_inverted_index": {"We": ["x"], "ran": [None], "it": "oops"}},
        {"abstract_inverted_index": ["not", "a", "mapping"]},
        {"abstract_inverted_index": 7},
        {"abstract_inverted_index": {}},
        ["a list instead of a work"],
        "just text",
        None,
    ],
)
def test_a_malformed_record_is_no_abstract_and_no_error(tmp_path: Path, malformed: Any) -> None:
    root = _package(tmp_path)
    with _mock() as router:
        _services(
            router,
            httpx.Response(200, json={"message": ["not", "a", "work"]}),
            httpx.Response(200, json=malformed),
        )
        out = _lookup(root, paper_doi=DOI)
    assert out.human_participants is None
    assert out.abstract_decision["outcome"] in ("no-abstract", "no-mention")
    assert _status(out, "ethics") == "manual"


def test_the_trigger_is_one_cut_sentence_and_the_rest_of_the_abstract_is_not_kept(
    tmp_path: Path,
) -> None:
    root = _package(tmp_path)
    long_sentence = "Participants were recruited " + "word " * 80 + "and paid."
    abstract = f"<jats:p>{UNRELATED} {long_sentence} Another sentence about nothing.</jats:p>"
    with _mock() as router:
        _services(router, httpx.Response(200, json=_crossref_body(abstract)))
        out = _lookup(root, paper_doi=DOI)
    trigger = out.abstract_decision["trigger"]
    assert trigger.startswith("Participants were recruited") and trigger.endswith("…")
    assert len(trigger) <= TRIGGER_CHARS
    assert _status(out, "ethics") == "fail"
    # nothing else of the abstract is in what the run produced
    produced = " ".join(
        [
            module_report(out),
            out.checklist.to_csv(),
            out.table.to_csv(),
            json.dumps(out.abstract_decision),
            out.human_participants_note,
            out.summary_text,
        ]
    )
    for other in (UNRELATED, "Another sentence about nothing", "Quasi-experimental"):
        assert other not in produced


# --------------------------------------------------------------------------
# which DOI
# --------------------------------------------------------------------------


def test_the_doi_comes_from_the_readme_when_none_is_given(tmp_path: Path) -> None:
    root = _package(tmp_path)
    with _mock() as router:
        crossref, _ = _services(router, httpx.Response(200, json=_crossref_body(YES)))
        out = _lookup(root)
    assert [str(c.request.url) for c in crossref.calls] == [
        "https://api.crossref.org/works/10.1234%2Fabc.def-1"
    ]
    assert out.human_participants is True
    assert out.abstract_decision["doi"] == DOI


def test_an_explicit_doi_wins_over_the_readme(tmp_path: Path) -> None:
    root = _package(tmp_path)
    with _mock() as router:
        crossref, _ = _services(router, httpx.Response(200, json=_crossref_body(YES)))
        out = _lookup(root, paper_doi=OTHER)
    assert [str(c.request.url) for c in crossref.calls] == [
        "https://api.crossref.org/works/10.5678%2Fzzz"
    ]
    assert out.abstract_decision["doi"] == OTHER
    # an explicit DOI that is no DOI is not replaced by the README's
    with _mock() as router:
        crossref, openalex = _services(router, httpx.Response(200, json=_crossref_body(YES)))
        out = _lookup(root, paper_doi="https://evil.example/x?y=1")
    assert crossref.call_count == openalex.call_count == 0
    assert out.abstract_decision["outcome"] == "bad-doi"


def test_a_readme_without_a_doi_for_the_publication_asks_nothing(
    tmp_path: Path, no_requests: None
) -> None:
    readmes = (None, "# Study\n\nDataset DOI: 10.1234/own\n", "Paper Title : <provide DOI>\n")
    for n, readme in enumerate(readmes):
        folder = tmp_path / f"p{n}"
        folder.mkdir()
        out = _lookup(_package(folder, readme))
        assert out.human_participants is None
        assert out.abstract_decision["outcome"] == "no-doi"
        assert "No DOI was given and the README has none" in out.human_participants_note
        assert _status(out, "ethics") == "manual"


def test_a_bad_doi_makes_no_request(tmp_path: Path, no_requests: None) -> None:
    root = _package(tmp_path)
    for bad in ("nonsense", "10.1234/abc?evil=1", "https://evil.example/", "10.1/x"):
        out = _lookup(root, paper_doi=bad)
        assert out.human_participants is None
        assert out.abstract_decision["outcome"] == "bad-doi"
        assert "is not a DOI" in out.human_participants_note
        assert _status(out, "ethics") == "manual"


# --------------------------------------------------------------------------
# off by default; already decided; offline
# --------------------------------------------------------------------------


def test_by_default_no_request_is_made_at_all(tmp_path: Path, no_requests: None) -> None:
    root = _package(tmp_path)  # a README with a DOI, which is not looked up
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        _services(router, httpx.Response(200, json=_crossref_body(YES)))
        out = module_run(None, "datapackage::package_docs", local_path=str(root))
        for flag in (False, None, "no", 0):
            module_run(
                None, "datapackage::package_docs", local_path=str(root), abstract_lookup=flag
            )
        check_package(root, modules=["package_docs"])
        report_package(root, tmp_path / "r.md", "md", modules=["package_docs"])
    assert router.calls.call_count == 0
    assert out.human_participants is None and out.human_participants_source == "unknown"
    assert out.human_participants_note is None and out.abstract_decision is None
    assert _status(out, "ethics") == _status(out, "consent") == "manual"
    assert _detail(out, "ethics") == "Needed if the research involved people. Check by hand."


def test_a_doi_without_the_switch_is_not_looked_up(tmp_path: Path, no_requests: None) -> None:
    root = _package(tmp_path)
    out = module_run(None, "datapackage::package_docs", local_path=str(root), paper_doi=DOI)
    assert out.human_participants is None and out.abstract_decision is None
    assert out.human_participants_note == (
        "paper_doi is set but abstract_lookup is off, so nothing was looked up."
    )
    # and the rows say nothing about it
    assert _detail(out, "ethics") == "Needed if the research involved people. Check by hand."


def test_an_answer_that_is_already_given_is_not_looked_up(
    tmp_path: Path, no_requests: None
) -> None:
    root = _package(tmp_path)
    for answer, expected in ((False, "na"), (True, "fail")):
        out = _lookup(root, human_participants=answer, paper_doi=DOI)
        assert out.human_participants is answer and out.human_participants_source == "given"
        assert out.abstract_decision is None
        assert "was not looked up: it was decided already (given)" in out.human_participants_note
        assert _status(out, "ethics") == expected
    # the paper's own text decides first
    paper = pc.test_paper(["Participants were recruited via Prolific."])
    out = module_run(
        paper,
        "datapackage::package_docs",
        local_path=str(root),
        abstract_lookup=True,
        paper_doi=DOI,
    )
    assert out.human_participants is True and out.human_participants_source == "paper"
    assert "decided already (paper)" in out.human_participants_note


def test_offline_makes_no_request_and_says_so(tmp_path: Path, no_requests: None) -> None:
    root = _package(tmp_path)
    # the setting pc.use(offline=True) gives ...
    with pc.use(offline=True):
        out = _lookup(root, paper_doi=DOI)
    assert out.human_participants is None
    assert out.abstract_decision["outcome"] == "offline"
    assert out.human_participants_note == f"Offline: the abstract of {DOI} was not looked up."
    assert _status(out, "ethics") == "manual"
    # ... and what the package run passes on when it is offline
    args = {"package_docs": {"abstract_lookup": True, "paper_doi": DOI}}
    (docs,) = check_package(root, modules=["package_docs"], args=args, offline=True)
    assert docs.abstract_decision["outcome"] == "offline"
    result = report_package(
        root, tmp_path / "r.md", "md", modules=["package_docs"], args=args, offline=True
    )
    assert "Offline: the abstract of" in (tmp_path / "r.md").read_text(encoding="utf-8")
    assert [o.module for o in result.values()] == ["package_docs"]


def test_the_module_is_not_dropped_by_offline() -> None:
    # the network use is optional, so it is not declared: --offline would drop the whole module
    info = pc.module_info("datapackage::package_docs")
    assert list(info.requires) == []
    assert {"abstract_lookup", "paper_doi"} <= set(info.params)
    assert "Only the DOI is sent" in info.details
    assert "datapackage::package_docs" in package_selection(offline=True).modules


# --------------------------------------------------------------------------
# through the package run and the command
# --------------------------------------------------------------------------


def test_check_package_and_report_package_carry_the_arguments(tmp_path: Path) -> None:
    root = _package(tmp_path)
    args = {"package_docs": {"abstract_lookup": True}}
    with _mock() as router:
        crossref, _ = _services(router, httpx.Response(200, json=_crossref_body(YES)))
        (docs,) = check_package(root, modules=["package_docs"], args=args)
        assert docs.human_participants is True
        assert crossref.call_count == 1
        report_package(root, tmp_path / "r.md", "md", modules=["package_docs"], args=args)
        assert crossref.call_count == 2
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    assert f"Decided from the abstract of {DOI} (Crossref)" in text


def _checklist(capsys: pytest.CaptureFixture[str]) -> dict[str, dict[str, Any]]:
    return {r["item"]: r for r in json.loads(capsys.readouterr().out)[0]["checklist"]}


def test_the_command_flags(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _package(tmp_path)
    base = ["package", str(root), "-m", "package_docs", "--json"]
    with _mock() as router:
        crossref, _ = _services(router, httpx.Response(200, json=_crossref_body(YES)))
        assert main(base) == 0
        assert crossref.call_count == 0  # off by default
        assert _checklist(capsys)["component:ethics"]["status"] == "manual"

        assert main([*base, "--abstract-lookup"]) == 0
        assert crossref.call_count == 1
        rows = _checklist(capsys)
        assert rows["component:ethics"]["status"] == "fail"
        assert f"abstract of {DOI} (Crossref)" in rows["component:ethics"]["detail"]

        # a DOI of one's own implies the switch
        assert main([*base, "--paper-doi", OTHER]) == 0
        assert str(crossref.calls.last.request.url).endswith("/works/10.5678%2Fzzz")
        capsys.readouterr()

        # -a MODULE.KEY beats the flag
        assert main([*base, "--abstract-lookup", "-a", "package_docs.abstract_lookup=false"]) == 0
        assert crossref.call_count == 2
        capsys.readouterr()

        # --offline: the module still runs, and nothing is asked
        assert main([*base, "--abstract-lookup", "--offline"]) == 0
        assert crossref.call_count == 2
        rows = _checklist(capsys)
        assert "Offline: the abstract of" in rows["component:ethics"]["detail"]
        assert rows["component:ethics"]["status"] == "manual"


def test_the_command_needs_the_docs_check_for_the_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], no_requests: None
) -> None:
    root = _package(tmp_path)
    assert main(["package", str(root), "-m", "package_files", "--abstract-lookup"]) == 2
    assert "need the package_docs check" in capsys.readouterr().err


def test_the_help_says_what_leaves_the_machine(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["package", "--help"])
    text = " ".join(capsys.readouterr().out.split())
    assert "--abstract-lookup" in text and "--paper-doi" in text
    assert "Only the DOI is sent" in text
    assert "no file name, README text or other content of the package leaves this machine" in text
    assert "Off by default" in text


def test_decide_from_abstract_without_a_package_needs_a_doi(no_requests: None) -> None:
    decision = decide_from_abstract(None, None)
    assert decision.outcome == "no-doi" and decision.people is None and decision.source == "unknown"
    assert decide_from_abstract(None, "x").outcome == "bad-doi"
    assert decide_from_abstract(None, DOI, offline=True).outcome == "offline"
