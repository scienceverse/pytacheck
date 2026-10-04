"""Decide "the research involved people" from the abstract of the paper (opt-in).

A data package usually comes without the paper, so ``package_docs`` cannot tell
whether the ethical approval and the informed consent form are needed. When
asked to (``abstract_lookup``), :func:`decide_from_abstract` takes the paper's
DOI (given, or the DOI the README names for the publication), fetches **only the
abstract** of that DOI from Crossref and, when Crossref has none, from OpenAlex,
and runs the live-data detection that ``ethics_check`` uses
(:func:`metacheck.text.extract._detect_live_data`: sentences about recruiting
participants, informed consent, online recruitment platforms and the like) on
it.

* The answer is ``True`` or open, never ``False``: an abstract that does not
  mention participants proves nothing, so the question stays with a person.
* **Only the DOI is sent.** No file name, README text or any other content of
  the package leaves the machine. The request is an ordinary GET of one record,
  through the shared HTTP layer (:mod:`metacheck.http`), so it also carries what
  every request of this tool carries: the User-Agent with the tool's name and
  version and, if you have set one with ``metacheck.email()``, your contact
  address.
* Nothing here fails the run. Offline, a timeout (10 seconds, two tries), a
  record that is missing or has no abstract, a malformed record or a DOI that
  is not a DOI give an open answer with a one-sentence reason.
* The abstract is not kept: the result holds the reason and, for a hit, the
  one sentence that matched (cut to :data:`TRIGGER_CHARS` characters).
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "TRIGGER_CHARS",
    "AbstractDecision",
    "abstract_text",
    "clean_doi",
    "decide_from_abstract",
    "lookup_asked",
    "readme_doi",
]

#: The longest quotation of the matching sentence that a result holds.
TRIGGER_CHARS = 160

#: The services asked for the abstract, in this order: the first with one wins.
_SERVICES = ("Crossref", "OpenAlex")
_TIMEOUT = 10.0  # seconds, for each try
_TRIES = 2
#: More than this of an abstract is not read (a real abstract is a few thousand characters).
_MAX_CHARS = 20_000


@dataclass(frozen=True)
class AbstractDecision:
    """What :func:`decide_from_abstract` found.

    ``people`` is ``True`` when the abstract mentions people and ``None`` in
    every other case, never ``False``. ``source`` is ``"abstract"`` for a hit
    and ``"unknown"`` otherwise. ``note`` is one sentence that says where the
    answer comes from or why it is open. ``doi`` and ``service`` say which
    record was read (``None`` when none was); ``trigger`` is the matching
    sentence of a hit, cut to :data:`TRIGGER_CHARS` characters; ``outcome`` is
    one of ``decided``, ``no-mention``, ``no-abstract``, ``unreachable``,
    ``offline``, ``bad-doi`` and ``no-doi``.
    """

    people: bool | None
    source: str
    note: str
    outcome: str
    doi: str | None = None
    service: str | None = None
    trigger: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """The decision as a plain dict (``people``, ``source``, ``outcome``, ``doi``, ``service``, ``trigger``)."""
        return {
            "people": self.people,
            "source": self.source,
            "outcome": self.outcome,
            "doi": self.doi,
            "service": self.service,
            "trigger": self.trigger,
        }


_TRUE = frozenset({"true", "yes", "y", "1"})


def lookup_asked(value: Any) -> bool:
    """Whether the ``abstract_lookup`` option is on: ``True``, or text such as ``"yes"``.

    Anything else, ``None`` and text that is not clearly a yes included, is off:
    a request goes out only when someone asked for it.
    """
    if isinstance(value, str):
        return value.strip().lower() in _TRUE
    return value is not None and bool(value)


# --------------------------------------------------------------------------
# the DOI
# --------------------------------------------------------------------------

_PREFIX = re.compile(r"^(?:(?:https?://)?(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_EDGES = "<([{\"'`"
_TRAILING = ".,;:)]}>\"'`"
_MAX_DOI = 200


def clean_doi(value: Any) -> str | None:
    """The DOI in *value* in its plain ``10.xxxx/...`` form, or ``None`` when it is not one.

    A leading ``https://doi.org/``, ``http://dx.doi.org/`` or ``doi:``, spaces,
    brackets or quotes around it and sentence punctuation after it are taken
    off. What is left must be a DOI in the usual form (``10.`` and 3 to 9
    digits, a slash, and letters, digits and ``-._;()/:<>``, ending in a letter
    or digit): anything else, such as a URL of another site or text with a
    ``?``, ``#``, ``%`` or a space in it, gives ``None``, so that nothing but a
    DOI is ever put in a request.
    """
    from metacheck.db.doi import doi_valid_format

    if not isinstance(value, str):
        return None
    text = value.strip().lstrip(_EDGES).strip()
    text = _PREFIX.sub("", text, count=1).strip().rstrip(_TRAILING)
    if not text or len(text) > _MAX_DOI or not doi_valid_format(text):
        return None
    return text


# the DOI-shaped run in a line of a README (it stops at brackets, quotes and spaces)
_DOI_IN_TEXT = re.compile(r"\b10\.\d{3,9}/[-._;()/:A-Za-z0-9]+")
_PAPER_LABEL = re.compile(
    r"\b(?:publications?|papers?|articles?|manuscripts?|journals?|preprints?|theses|thesis"
    r"|dissertations?)\b",
    re.IGNORECASE,
)
_MAX_LABEL_WORDS = 8
_OWN_LABEL = re.compile(
    r"\b(?:data ?sets?|data|packages?|repositor(?:y|ies)|software|code|versions?)\b",
    re.IGNORECASE,
)


def readme_doi(text: str | None, template: Any = None) -> str | None:
    """The DOI the README gives for the paper, as written, or ``None``.

    Only a labelled line counts (``Label : value``, also in a list or in bold,
    with the value on the next line when the label stands alone), and only when
    the label says the line is about the publication (``DOI of the publication``,
    ``Related paper``, ``Project or Paper Title``, not ``Dataset DOI``), or a
    field of the README *template* with ``"expect": "doi"`` matches the line. A
    DOI in a sentence, in a list of references or in a link elsewhere is never
    taken, because it may be another work's, or the package's own. The first
    such line wins. The DOI is returned as it stands; :func:`clean_doi` says
    whether it is usable.
    """
    from metacheck.datapackage._docs_readme import _INLINE

    if not text:
        return None
    declared = [
        pattern
        for section in getattr(template, "sections", ())
        for field in section.fields
        if field.expect == "doi"
        for pattern in field.match
    ]
    lines = text.splitlines()
    for at, line in enumerate(lines):
        pair = _INLINE.match(line)
        if pair is None:
            continue
        label, value = pair.group(1), pair.group(2)
        if value.startswith("//") or len(label.split()) > _MAX_LABEL_WORDS:
            continue  # the "https:" of a link in a sentence, not a label
        declared_here = any(p.search(line) for p in declared)
        about_paper = bool(_PAPER_LABEL.search(label)) and not _OWN_LABEL.search(label)
        if not (declared_here or about_paper):
            continue
        if not value.strip():  # the label stands alone: the value is on the next filled line
            value = next((x for x in lines[at + 1 : at + 4] if x.strip()), "")
        found = _DOI_IN_TEXT.search(value)
        if found:
            return found.group(0)
    return None


# --------------------------------------------------------------------------
# the abstract
# --------------------------------------------------------------------------

# block elements of JATS and HTML: a break between sentences
_BLOCK_TAG = re.compile(
    r"</?(?:[A-Za-z]+:)?(?:p|title|sec|list|list-item|label|break|br|li|ul|ol|div|h[1-6])"
    r"(?:\s[^<>]*)?/?>",
    re.IGNORECASE,
)
# any other tag (jats:italic, <sup>, ...); a "<" in running text ("p < .05") is not one
_TAG = re.compile(r"</?[A-Za-z][\w:.-]*(?:\s[^<>]*)?/?>")


def abstract_text(raw: Any) -> str:
    """The plain text of an abstract that may hold JATS or HTML markup.

    Block elements (paragraphs, titles, list items) become spaces, other tags
    are dropped, character references are decoded and white space is collapsed.
    """
    if not isinstance(raw, str):
        return ""
    text = _BLOCK_TAG.sub(" ", raw[:_MAX_CHARS])
    text = _TAG.sub("", text)
    return " ".join(html.unescape(text).split())


def _get(url: str) -> tuple[str, Any]:
    """One short GET of a JSON record: ``(state, data)``.

    The state is ``"ok"``, ``"missing"`` (the service has no such record) or
    ``"failed"`` (no answer, an error status, a body that is not JSON).
    """
    from metacheck import http

    try:
        # a rate limit seen earlier must not make this wait
        with http.skip_on_api_limit():
            resp = http.request(
                "GET",
                url,
                headers={"Accept": "application/json"},
                timeout=_TIMEOUT,
                max_tries=_TRIES,
            )
        if resp is None:
            return "failed", None
        if resp.status_code in (404, 410):
            return "missing", None
        if resp.status_code >= 400:
            return "failed", None
        return "ok", http.resp_json(resp)
    except Exception:  # an odd answer must not stop the check
        return "failed", None


def _crossref_abstract(encoded: str) -> tuple[str, str]:
    state, data = _get(f"https://api.crossref.org/works/{encoded}")
    message = data.get("message") if isinstance(data, Mapping) else None
    raw = message.get("abstract") if isinstance(message, Mapping) else None
    return state, abstract_text(raw)


def _openalex_abstract(encoded: str) -> tuple[str, str]:
    from metacheck.db.crossref import _openalex_add_abstract

    state, data = _get(
        f"https://api.openalex.org/works/https://doi.org/{encoded}?select=abstract_inverted_index"
    )
    if state != "ok" or not isinstance(data, Mapping):
        return state, ""
    try:
        work = _openalex_add_abstract(data)
    except Exception:  # a malformed index: the work has no usable abstract
        return "ok", ""
    return "ok", abstract_text(work.get("abstract") if isinstance(work, Mapping) else None)


_WHAT = {"ok": "has no abstract", "missing": "has no record", "failed": "did not answer"}


def _fetch(doi: str) -> tuple[str, str, str]:
    """``(abstract, service, why not)``: the first abstract found, or what each service said."""
    from metacheck.db._utils import url_encode

    encoded = url_encode(
        doi, reserved=True
    )  # a validated DOI has no "%", so this encodes all of it
    said: list[str] = []
    answered = False
    for service, ask in zip(_SERVICES, (_crossref_abstract, _openalex_abstract), strict=True):
        state, text = ask(encoded)
        if text:
            return text, service, ""
        answered = answered or state != "failed"
        said.append(f"{service} {_WHAT[state]}")
    return "", "", ("; ".join(said) if answered else "")


def _hits(text: str) -> str | None:
    """The first sentence of *text* about recruiting people, as ``ethics_check`` finds them."""
    from metacheck.io.grobid import _tokenize_sentences
    from metacheck.papers.io import test_paper
    from metacheck.text.extract import _detect_live_data

    sentences = [s for s in _tokenize_sentences(text) if s]
    if not sentences:
        return None
    found = _detect_live_data(test_paper(sentences))
    return str(found["text"].iloc[0]) if len(found) else None


def _cut(sentence: str, limit: int = TRIGGER_CHARS) -> str:
    sentence = " ".join(sentence.split())
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------
# the decision
# --------------------------------------------------------------------------


def _open(outcome: str, note: str, doi: str | None = None) -> AbstractDecision:
    return AbstractDecision(None, "unknown", note, outcome, doi)


def decide_from_abstract(
    package: Any = None,
    doi: Any = None,
    *,
    readme: Any = None,
    offline: bool = False,
) -> AbstractDecision:
    """Whether the research involved people, from the abstract of the paper's DOI.

    *doi* is the paper's DOI; an explicit one beats the DOI in the README of
    *package* (see :func:`readme_doi`, with *readme* the README template). With
    *offline* nothing is looked up. See the module docstring for what is sent
    and what the answer can be. Never raises for a lookup that goes wrong.
    """
    from metacheck.datapackage.docs import load_readme_template, package_readme_text

    given = doi.strip() if isinstance(doi, str) else (str(doi) if doi is not None else "")
    if given:
        found = given
        where = "The DOI given"
    else:
        where = "The DOI in the README"
        found = None
        if package is not None:
            try:
                found = readme_doi(package_readme_text(package), load_readme_template(readme))
            except Exception:  # a README that cannot be read gives no DOI
                found = None
        if not found:
            return _open(
                "no-doi",
                "No DOI was given and the README has none for the publication, "
                "so the abstract was not looked up.",
            )
    clean = clean_doi(found)
    if clean is None:
        return _open(
            "bad-doi", f"{where} is not a DOI (10.xxxx/...), so the abstract was not looked up."
        )
    if offline:
        return _open("offline", f"Offline: the abstract of {clean} was not looked up.", clean)

    text, service, why = _fetch(clean)
    if not text:
        if not why:  # neither service answered
            return _open(
                "unreachable",
                f"The abstract of {clean} could not be fetched: Crossref and OpenAlex did not answer.",
                clean,
            )
        return _open("no-abstract", f"No abstract was found for {clean} ({why}).", clean)
    sentence = _hits(text)
    if sentence is None:
        return AbstractDecision(
            None,
            "unknown",
            f"The abstract of {clean} ({service}) does not mention participants, "
            "which proves nothing either way.",
            "no-mention",
            clean,
            service,
        )
    trigger = _cut(sentence)
    return AbstractDecision(
        True,
        "abstract",
        f"Decided from the abstract of {clean} ({service}): “{trigger}”",
        "decided",
        clean,
        service,
        trigger,
    )
