"""AsPredicted pre-registrations (port of ``R/archive-aspredicted.R``).

:func:`aspredicted_links` finds AsPredicted links in papers;
:func:`aspredicted_info` reads each pre-registration, through ResearchBox's API when a
key is set (:func:`metacheck.archives.api_keys.rbox_token`) and from its web page
otherwise, and splits it into its standard questions (``AP_title``, ``AP_authors``,
``AP_hypotheses``...).
Page text is extracted with a port of ``rvest::html_text2()``
(:func:`_html_text2`), so line breaks fall where they do in R.
"""

from __future__ import annotations

import re
import warnings
from typing import TYPE_CHECKING, Any, cast

import pandas as pd

from metacheck._r import gsub, trimws
from metacheck._values import is_missing

if TYPE_CHECKING:
    from lxml import html as lxml_html

__all__ = ["aspredicted_info", "aspredicted_links"]


def aspredicted_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-aspredicted.R::aspredicted_links(): AsPredicted links in papers.

    The rows of the paper's (or paper list's) ``url`` table whose ``href``
    mentions ``aspredicted.org``.
    """
    from metacheck._r import grepl
    from metacheck.archives import _paper_urls

    urls = _paper_urls(paper)
    if "href" not in urls.columns:
        # R: filter(grepl(..., href)) on the 0 x 0 url table of an empty paper
        # list sees `href <- NULL` and keeps the empty table
        return urls.copy()
    keep = pd.Series(
        [bool(v) for v in grepl(r"aspredicted\.org", urls["href"], ignore_case=True)],
        index=urls.index,
        dtype=bool,
    )
    return urls[keep].reset_index(drop=True)


def aspredicted_info(
    ap_url: Any, id_col: int | str = 1, wait: float = 1, cache: bool = False
) -> pd.DataFrame:
    """Port of R/archive-aspredicted.R::aspredicted_info(): AsPredicted preregistrations.

    *ap_url* is a URL, a sequence of URLs, or a table whose *id_col* (R-style
    1-based position, or a name) holds them, e.g. from
    :func:`aspredicted_links`. Returns the input rows with the preregistration's
    answers added.

    With a ResearchBox API key set (see :func:`metacheck.archives.api_keys.rbox_token`)
    each preregistration is read through ResearchBox's API, which serves
    AsPredicted's public preregistrations, with one request each. Without a key,
    or when the API fails, the preregistration's web page on AsPredicted is read
    instead (*wait* seconds after each page); AsPredicted asks for a captcha after
    several pages, and no page is read after that.

    Both routes give the same columns. Each answer is placed by the wording of its
    question, so both versions of AsPredicted's questions found so far (1.05 and
    2.00) are read: version 1.05 asks about secondary analyses (``AP_secondary``)
    where version 2.00 asks about outliers (``AP_outliers``). An answer to a
    question that is not recognised is added to ``AP_anything_else``, after its
    question. ``AP_created`` is the time of registration as AsPredicted shows it
    (``"2021/06/14 02:17 (PT)"``) and ``AP_registered`` its date. ``AP_number``,
    ``AP_status``, ``AP_authors_count`` and ``AP_bundle`` (the numbers of the
    preregistrations registered together with this one) come from the API only, and
    are ``NA`` when the web page was read; from the API ``AP_authors`` gives each
    author's name and university, without the e-mail address of the web page.

    A link that names no preregistration, such as AsPredicted's home page, is not
    looked up: its row has an ``error``. With *cache* a preregistration looked up
    before is not asked for again (see ``repo_info_cache_clear()``).
    """
    from metacheck import http, utils
    from metacheck._r import as_character, bind_rows
    from metacheck.archives import _message
    from metacheck.archives.api_keys import rbox_token
    from metacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from metacheck.utils import left_join

    # aspredicted.org is only needed when its pages are read
    if not rbox_token() and not utils.online("aspredicted.org"):
        raise RuntimeError("AsPredicted.org seems to be offline")

    if isinstance(ap_url, pd.DataFrame):
        table = ap_url
        if isinstance(id_col, int | float) and not isinstance(id_col, bool):
            id_col_name = str(table.columns[int(id_col) - 1])
        else:
            id_col_name = str(id_col)
        raw_urls = table[id_col_name].tolist()
    else:
        vals = [ap_url] if isinstance(ap_url, str) or ap_url is None else list(ap_url)
        uniq = list(dict.fromkeys(None if is_missing(v) else v for v in vals))
        raw_urls = [v for v in uniq if v is not None]
        id_col_name = "ap_url"
        table = pd.DataFrame(
            {"ap_url": pd.Series([as_character(v) for v in raw_urls], dtype="string")}
        )

    valid_ids = list(
        dict.fromkeys(cast("str", as_character(u)) for u in raw_urls if not is_missing(u))
    )
    if not valid_ids:
        _message("No valid AsPredicted links")
        return table

    n = len(valid_ids)
    _message(f"Starting AsPredicted retrieval for {n} file{'' if n == 1 else 's'}...")
    _warn_if_no_key()

    # After AsPredicted's captcha no further page is read, but the API is still asked for
    # the remaining preregistrations: this loop used to stop at the captcha, and every
    # later preregistration got no information.
    frames: list[pd.DataFrame] = []
    captcha = False
    for url in valid_ids:
        ap_id = _ap_id(url)
        cached = _repo_info_cache_get("aspredicted", ap_id) if cache is True and ap_id else None
        if cached is not None:
            # cached by identifier, so a different link to it gets its own url
            hit = cached.copy()
            hit["ap_url"] = pd.Series([url], dtype="string", index=hit.index)
            frames.append(hit)
            continue
        ap = _aspredicted_info(url, page=not captcha)
        if "error" in ap.columns and "captcha" in str(ap["error"].iloc[0] or ""):
            captcha = True
        if ap.attrs.get("route") == "page":
            http.sleep(wait)
        ap.attrs.pop("route", None)
        if cache is True and ap_id and _repo_info_ok(ap):
            _repo_info_cache_put("aspredicted", ap_id, ap)
        frames.append(ap)

    info = bind_rows(frames)
    ids = pd.DataFrame({"ap_url": pd.Series(valid_ids, dtype="string")})
    info = left_join(info, ids, by="ap_url")
    data = left_join(table, info, by={id_col_name: "ap_url"}, suffix=("", ".ap"))
    _message("...AsPredicted retrieval complete!")
    return data


def _warn_if_no_key() -> None:
    """A message, once per session, that without a ResearchBox API key the
    preregistrations are read from AsPredicted's web pages, which stop after
    AsPredicted's captcha (reset with ``options({"metacheck.aspredicted_key.notified": None})``)."""
    from metacheck.archives import _message
    from metacheck.archives.api_keys import KEY_SERVICES, rbox_token
    from metacheck.utils import get_option, options

    if get_option("metacheck.aspredicted_key.notified") is True or rbox_token():
        return
    _message(
        "No ResearchBox API key is set, so AsPredicted preregistrations are read "
        "from AsPredicted's web pages. AsPredicted asks for a captcha after "
        "several pages, after which no more are read. Set a key with "
        f"rbox_token() (make one at {KEY_SERVICES['researchbox'].url})."
    )
    options({"metacheck.aspredicted_key.notified": True})


def _aspredicted_info(ap_url: str, page: bool = True) -> pd.DataFrame:
    """Port of R/archive-aspredicted.R::.aspredicted_info(): one preregistration.

    Uses ResearchBox's API when a key is set, and reads the preregistration's web
    page otherwise or when the API fails. ``page=False`` does not read the page
    when the API fails (after AsPredicted asked for a captcha). The attribute
    ``attrs["route"]`` is ``"api"`` or ``"page"`` when that was asked.
    """
    from metacheck.archives import _message
    from metacheck.archives.api_keys import rbox_token

    _message(f"* Retrieving info from {ap_url}...")
    path = _ap_path(ap_url)
    if path is None or not path:
        error = "the link names no AsPredicted preregistration"
        warnings.warn(f"{ap_url} error: {error}", stacklevel=2)
        return _error_row(ap_url, error)

    api_error: str | None = None
    if rbox_token():
        info = _aspredicted_info_api(ap_url)
        if "error" not in info.columns:
            info.attrs["route"] = "api"
            return info
        api_error = str(info["error"].iloc[0])
        _message(
            f"- ResearchBox API: {api_error}", "; reading the web page instead" if page else ""
        )

    if not page:
        if api_error == "unfound":
            api_error = "ResearchBox's API does not have it, or it is not public"
        prefix = f"{api_error}; " if api_error is not None else ""
        return _error_row(ap_url, f"{prefix}web page not read: AsPredicted asked for a captcha")

    info = _aspredicted_info_page(ap_url)
    # Both failed: the API's reason first, so a refusal a later run will get past keeps
    # its "API rate limit exhausted: " start. "unfound" from the API may be a
    # preregistration that is not public, which the page tells.
    if api_error is not None and api_error != "unfound" and "error" in info.columns:
        info["error"] = pd.Series(
            [f"{api_error}; web page: {info['error'].iloc[0]}"], dtype="string", index=info.index
        )
    info.attrs["route"] = "page"
    return info


def _error_row(ap_url: str, error: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ap_url": pd.Series([ap_url], dtype="string"),
            "error": pd.Series([error], dtype="string"),
        }
    )


_AP_RX = re.compile(r"^(https?://)?(www\.)?aspredicted\.org(/|$)", re.IGNORECASE)


def _ap_path(ap_url: Any) -> str | None:
    """The part of an AsPredicted link after ``aspredicted.org/`` (``""`` for the home
    page), or ``None`` for a link that is not to aspredicted.org."""
    if is_missing(ap_url):
        return None
    x = str(ap_url).strip(" \t\r\n")
    if not _AP_RX.search(x):
        return None
    return re.sub(r"/+$", "", _AP_RX.sub("", x, count=1))


def _ap_id(ap_url: Any) -> str | None:
    """The identifier ResearchBox's API takes for an AsPredicted link, or ``None``.

    The links found in papers are a PDF's short name (``ve2qn.pdf``,
    ``3pc3-6tw7.pdf``), a share code (``Y2F_6B7``) or a ``blind.php?x=`` link; the
    API refuses the blind link itself but takes the code after ``x=``. Any other
    link is not sent, as each identifier the API cannot find counts toward a block
    of the key (see ``_AP_UNFOUND_LIMIT``).
    """
    path = _ap_path(ap_url)
    if path is None:
        return None
    path = re.sub(r"^blind\.php\?x=", "", path, flags=re.IGNORECASE)
    return path if re.fullmatch(r"[A-Za-z0-9_-]+(\.pdf)?", path) else None


#: ResearchBox blocks the key for an hour after 5 different AsPredicted identifiers
#: within 24 hours that it cannot find or that are not public, and for a day after 10.
#: Each such identifier is recorded for 24 hours in metacheck's state folder, so jobs with
#: the same key share the count. An identifier recorded is not sent again, and from this
#: many identifiers on, none is sent; the web page is read instead.
_AP_UNFOUND_LIMIT = 4


def _ap_unfound_path() -> str:
    from metacheck.archives.api_keys import state_dir

    return str(state_dir() / "aspredicted_unfound_ids.json")


def _ap_unfound_ids() -> list[dict[str, Any]]:
    """The identifiers recorded in the last 24 hours, with the time (epoch seconds) of each."""
    import json
    import time

    try:
        with open(_ap_unfound_path(), encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    now = time.time()
    return [
        r
        for r in rows
        if isinstance(r, dict)
        and isinstance(r.get("id"), str)
        and isinstance(r.get("time"), int | float)
        and now - r["time"] < 24 * 3600
    ]


def _ap_unfound_add(ap_id: str) -> None:
    import json
    import os
    import time

    from metacheck.archives._atomic import atomic_write

    path = _ap_unfound_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rows = [*_ap_unfound_ids(), {"id": ap_id, "time": time.time()}]
        with atomic_write(path) as fh:
            fh.write(json.dumps(rows).encode("utf-8"))
    except OSError:
        pass


#: Words that identify each question of AsPredicted's versions 1.05 and 2.00, in their
#: wording on the web page and in the API (lower case)
_AP_QUESTIONS: dict[str, str] = {
    "AP_data": "have any data been collected",
    "AP_hypotheses": "main question being asked",
    "AP_key_dv": "describe the key dependent variable",
    "AP_conditions": "how many and which conditions",
    "AP_analyses": "specify exactly which analyses",
    "AP_outliers": "describe exactly how outliers",
    "AP_secondary": "any secondary analyses",
    "AP_sample_size": "how many observations will be collected",
    "AP_anything_else": "anything else you would like to pre-register",
}


def _ap_api_time(x: Any) -> str | None:
    """``"06/14/2021 02:17 AM"`` from the API as the web page writes it,
    ``"2021/06/14 02:17 (PT)"``: the same time, in AsPredicted's time zone (Pacific time).
    Anything else is returned unchanged."""
    if not isinstance(x, str) or not x:
        return None
    m = re.fullmatch(r"([0-9]{2})/([0-9]{2})/([0-9]{4}) ([0-9]{1,2}):([0-9]{2}) ([AP]M)", x.strip())
    if m is None:
        return x
    hour = int(m.group(4)) % 12 + (12 if m.group(6) == "PM" else 0)
    return f"{m.group(3)}/{m.group(1)}/{m.group(2)} {hour:02d}:{m.group(5)} (PT)"


def _ap_iso_date(x: str | None) -> str | None:
    """``"2021/06/14 02:17 (PT)"`` as ``"2021-06-14"``, or ``None``."""
    m = re.match(r"([0-9]{4})/([0-9]{2})/([0-9]{2})", x or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _ap_anonymous_authors(n: int | None) -> str:
    """The authors line the web page shows for an anonymous preregistration."""
    count = "one author" if n == 1 else f"{'NA' if n is None else n} authors"
    return (
        "This pre-registration is currently anonymous to enable blind peer-review.\n"
        f"It has {count}."
    )


def _field(x: Any) -> str | None:
    """The API's value for a field as text; ``""`` and ``null`` are how it reports a
    field it does not have."""
    if x is None or isinstance(x, list | dict) or x == "":
        return None
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def _aspredicted_info_api(ap_url: str) -> pd.DataFrame:
    """A preregistration from ResearchBox's API (one request).

    Returns the same columns as :func:`_aspredicted_info_page`; on failure only
    ``ap_url`` and ``error``.
    """
    from metacheck._r import gsub
    from metacheck.archives.researchbox import _rbox_api, _rbox_plain_text

    ap_id = _ap_id(ap_url)
    if ap_id is None:
        return _error_row(ap_url, "unfound")
    # see _AP_UNFOUND_LIMIT
    unfound = list(dict.fromkeys(r["id"] for r in _ap_unfound_ids()))
    if ap_id in unfound:
        return _error_row(ap_url, "unfound")
    if len(unfound) >= _AP_UNFOUND_LIMIT:
        return _error_row(
            ap_url,
            f"not sent: ResearchBox could not find {len(unfound)} AsPredicted "
            "preregistrations asked for in the last 24 hours; it blocks the key after 5, so "
            "no other preregistration is sent to the API until then",
        )

    res = _rbox_api("get_aspredicted", id=ap_id, format="json")
    if not res["ok"]:
        if res["error"] == "unfound":
            _ap_unfound_add(ap_id)
        return _error_row(ap_url, str(res["error"]))

    b = res["body"] if isinstance(res["body"], dict) else {}
    row: dict[str, Any] = {"ap_url": ap_url, "AP_title": _field(b.get("title"))}
    count = b.get("authors_count")
    n_authors = (
        int(count) if isinstance(count, int | float) and not isinstance(count, bool) else None
    )
    authors = b.get("authors")
    if b.get("anonymous") is True:
        row["AP_authors"] = _ap_anonymous_authors(n_authors)
    elif isinstance(authors, list) and authors:
        lines = []
        for a in authors:
            a = a if isinstance(a, dict) else {}
            name = f"{a.get('first') or ''} {a.get('last') or ''}".strip(" \t\r\n")
            uni = a.get("uni") or ""
            lines.append(gsub(r"[ \t\r\n\f\v]+", " ", name + (f" ({uni})" if uni else "")))
        row["AP_authors"] = "\n".join(lines)
    else:
        row["AP_authors"] = None
    row["AP_created"] = _ap_api_time(b.get("registration_time"))

    # Each answer is placed by its question's wording; the numbers differ between versions
    # (question 6 is about outliers in 2.00 and secondary analyses in 1.05). A question not
    # recognised is kept with its answer.
    answers: dict[str, str | None] = dict.fromkeys(_AP_QUESTIONS)
    other: list[str] = []
    questions = b.get("questions")
    for qa in questions if isinstance(questions, list) else []:
        qa = qa if isinstance(qa, dict) else {}
        q = _rbox_plain_text(_field(qa.get("question"))) or ""
        a_raw = qa.get("answer")
        a = "" if a_raw is None else re.sub(r"\r\n?", "\n", str(a_raw)).strip(" \t\r\n")
        hit = [name for name, words in _AP_QUESTIONS.items() if words in q.lower()]
        if not hit:
            other.append(f"{q}\n{a}")
        elif answers[hit[0]] is None:
            answers[hit[0]] = a
        else:
            answers[hit[0]] = f"{answers[hit[0]]}\n\n{a}"
    if other:
        kept = [answers["AP_anything_else"]] if answers["AP_anything_else"] is not None else []
        answers["AP_anything_else"] = "\n\n".join([*kept, *other])
    row.update(answers)

    row["AP_version"] = _field(b.get("question_version"))
    row["AP_registered"] = _ap_iso_date(row["AP_created"])
    row["AP_anonymous"] = None if b.get("anonymous") is None else b.get("anonymous") is True
    row["AP_number"] = _field(b.get("a_id"))
    row["AP_status"] = _field(b.get("status"))
    row["AP_authors_count"] = n_authors
    bundle = b.get("bundle_a_ids")
    row["AP_bundle"] = (
        ", ".join(_field(i) or "NA" for i in bundle)
        if isinstance(bundle, list) and bundle
        else None
    )
    return _frame(row)


def _frame(row: dict[str, Any]) -> pd.DataFrame:
    """A one-row table: logical and integer columns typed, the rest character."""
    cols: dict[str, pd.Series] = {}
    for k, v in row.items():
        if k == "AP_anonymous":
            cols[k] = pd.Series([v], dtype="boolean")
        elif k == "AP_authors_count":
            cols[k] = pd.Series([v], dtype="Int64")
        else:
            cols[k] = pd.Series([v], dtype="string")
    return pd.DataFrame(cols)


#: The page's section headings, in the wording of version 2.00 and, where it differs, of
#: version 1.05 (question 6, and the examples under question 8)
_SECTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("AP_authors", ("Author(s)",)),
    ("AP_created", ("Pre-registered on",)),
    ("AP_data", ("1) Have any data been collected for this study already?",)),
    (
        "AP_hypotheses",
        ("2) What's the main question being asked or hypothesis being tested in this study?",),
    ),
    (
        "AP_key_dv",
        ("3) Describe the key dependent variable(s) specifying how they will be measured.",),
    ),
    ("AP_conditions", ("4) How many and which conditions will participants be assigned to?",)),
    (
        "AP_analyses",
        (
            "5) Specify exactly which analyses you will conduct to examine the main "
            "question/hypothesis.",
        ),
    ),
    (
        "AP_outliers",
        (
            "6) Describe exactly how outliers will be defined and handled, and your precise "
            "rule(s) for excluding observations.",
        ),
    ),
    ("AP_secondary", ("6) Any secondary analyses?",)),
    (
        "AP_sample_size",
        (
            "7) How many observations will be collected or what will determine sample size?\n"
            "No need to justify decision, but be precise about exactly how the number will be "
            "determined.",
        ),
    ),
    (
        "AP_anything_else",
        (
            "8) Anything else you would like to pre-register?\n(e.g., secondary analyses, "
            "variables collected for exploratory purposes, unusual analyses planned?)",
            "8) Anything else you would like to pre-register?\n(e.g., data exclusions, "
            "variables collected for exploratory purposes, unusual analyses planned?)",
        ),
    ),
    ("AP_version", ("Version of AsPredicted Questions: ",)),
    ("bug", ("Report a bug",)),
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36 scienceverse/metacheck"
)


def _aspredicted_info_page(ap_url: str) -> pd.DataFrame:
    """A preregistration read from its web page on AsPredicted.

    A failed connection or any status but 200 gives an ``error`` column (and a
    warning); a captcha page gives ``error = "captcha"``.
    """
    from metacheck import http

    resp = http.request(
        "GET",
        ap_url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,*/*;q=0.8",
            "User-Agent": _USER_AGENT,
        },
        max_tries=1,
    )
    if resp is None:
        error = "the connection failed"
        warnings.warn(f"{ap_url} error: {error}", stacklevel=2)
        return _error_row(ap_url, error)
    # any status but 200 (a 404 for a withdrawn page too) is this page's error;
    # metacheck used to raise on 4xx/5xx before its own check, aborting every URL (U54)
    if resp.status_code != 200:
        warnings.warn(f"{ap_url} error: {resp.reason_phrase}", stacklevel=2)
        return _error_row(ap_url, resp.reason_phrase)
    return _aspredicted_parse(resp.text, ap_url)


def _aspredicted_parse(html_text: str, ap_url: str) -> pd.DataFrame:
    """The parsing half of ``.aspredicted_info_page()``: page HTML -> one-row table."""
    from lxml import html as lxml_html

    doc = lxml_html.document_fromstring(html_text)
    bodies = cast("list[lxml_html.HtmlElement]", doc.xpath("//body"))
    body = _html_text2(bodies[0]) if bodies else ""
    if "CLICK after solving captcha" in body:
        warnings.warn("Log in to AsPredicted to bypass the CAPTCHA", stacklevel=3)
        return _error_row(ap_url, "captcha")

    row: dict[str, Any] = {"ap_url": ap_url}
    title = cast("list[lxml_html.HtmlElement]", doc.xpath("//h3//b//i"))
    row["AP_title"] = title[0].text_content() if title else None

    # Where each heading starts and ends (None when the page does not have it). Each
    # section is cut at the first later heading the page has: a section used to be cut only
    # at the next heading in the list, so on a version 1.05 page, which has no outlier
    # question, the analyses took in questions 6 to 8 and the page's scripts, and the
    # outliers were NA.
    found: dict[str, tuple[int, int] | None] = {}
    for name, alternatives in _SECTIONS:
        found[name] = None
        for heading in alternatives:
            pos = body.find(heading)
            if pos >= 0:
                found[name] = (pos, pos + len(heading))
                break
    starts = [f[0] for f in found.values() if f is not None]
    for name, _ in _SECTIONS:
        if name == "bug":
            continue
        span = found[name]
        if span is None:
            row[name] = None
            continue
        later = [s for s in starts if s > span[0]]
        end = min(later) if later else len(body)
        row[name] = trimws(body[span[1] : end])

    row["AP_registered"] = _ap_iso_date(row["AP_created"])
    row["AP_anonymous"] = (
        None if row["AP_authors"] is None else "currently anonymous" in row["AP_authors"]
    )
    row["AP_number"] = None
    row["AP_status"] = None
    row["AP_authors_count"] = None
    row["AP_bundle"] = None
    return _frame(row)


# ---------------------------------------------------------------------------
# rvest::html_text2()
# ---------------------------------------------------------------------------

_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "details", "dialog", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hgroup", "hr", "li", "main", "nav", "ol", "p", "pre",
        "section", "table", "ul", "caption",
    }
)  # fmt: skip
_TABLE_TAGS = frozenset({"tbody", "thead", "tfoot", "tr", "td", "th"})


def _collapse_whitespace(x: str, preserve_nbsp: bool = False) -> str:
    x = gsub(r"(^[ \t\n]+)|([ \t\n]+$)", "", x, perl=True)
    return cast("str", gsub("[\t\n ]+" if preserve_nbsp else "[\t\n  ]+", " ", x, perl=True))


class _PaddedText:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.lines = 0

    def add_margin(self, n: int) -> None:
        if not self.text:
            return
        self.lines = max(self.lines, n)

    def convert_breaks(self) -> None:
        if self.lines == 0:
            return
        self.text.append("\n" * self.lines)
        self.lines = 0

    def add_text(self, x: str) -> None:
        if x == "":
            return
        self.convert_breaks()
        self.text.append(x)

    def output(self) -> str:
        return "".join(self.text)


def _contents(node: Any) -> list[tuple[str, Any]]:
    """``xml2::xml_contents()``: child elements and text nodes, as (name, node|text)."""
    out: list[tuple[str, Any]] = []
    if node.text:
        out.append(("text", node.text))
    for child in node:
        tag = child.tag if isinstance(child.tag, str) else None
        if tag is None:  # comment / processing instruction
            name = "comment"
        else:
            name = tag.lower()
        out.append((name, child))
        if child.tail:
            out.append(("text", child.tail))
    return out


def _xml_text(item: tuple[str, Any]) -> str:
    name, node = item
    if name == "text":
        return str(node)
    if name == "comment":
        return str(node.text or "")
    return str(node.text_content())


def _is_inline(node: Any) -> bool:
    for child in node:
        if isinstance(child.tag, str) and child.tag.lower() in _BLOCK_TAGS | _TABLE_TAGS:
            return False
    return True


def _html_text_inline(node: Any, preserve_nbsp: bool) -> str:
    children = _contents(node)
    if not children:
        return ""
    texts = [_xml_text(c) for c in children]
    is_br = [c[0] == "br" for c in children]
    lines: list[str] = []
    has_br: list[bool] = []
    for i, (t, br) in enumerate(zip(texts, is_br, strict=True)):
        if i == 0 or is_br[i - 1]:
            lines.append(t)
            has_br.append(br)
        else:
            lines[-1] += t
            has_br[-1] = has_br[-1] or br
    if node.tag != "pre":
        lines = [_collapse_whitespace(line, preserve_nbsp) for line in lines]
    return "".join(line + ("\n" if br else "") for line, br in zip(lines, has_br, strict=True))


def _tag_margin(name: str) -> int:
    if name == "p":
        return 2
    return 1 if name in _BLOCK_TAGS else 0


def _html_text_block(item: tuple[str, Any], text: _PaddedText, preserve_nbsp: bool) -> None:
    name, node = item
    if name == "text":
        text.add_text(_collapse_whitespace(str(node), preserve_nbsp))
        return
    if name == "comment":
        return  # R: html_text_inline() of a comment has no contents
    if _is_inline(node):
        text.add_text(_html_text_inline(node, preserve_nbsp))
        return
    children = _contents(node)
    n = len(children)
    for i, child in enumerate(children, start=1):
        cname = child[0]
        margin = _tag_margin(cname)
        text.add_margin(margin)
        _html_text_block(child, text, preserve_nbsp)
        if cname == "tr" and i != n:
            text.add_text("\n")
        elif cname in ("th", "td") and i != n:
            text.add_text("\t")
        elif cname == "br":
            text.add_text("\n")
        text.add_margin(margin)


def _html_text2(x: str | lxml_html.HtmlElement | list[Any], preserve_nbsp: bool = False) -> Any:
    """Port of ``rvest::html_text2()``: an element's text as a browser would lay it out.

    Block elements start new lines (paragraphs get a blank line), ``<br>``
    breaks lines, table cells are tab-separated, and whitespace is collapsed.
    *x* is an element, a list of elements (gives a list), or an HTML
    document string (its ``<body>`` is used).
    """
    if isinstance(x, list):
        return [_html_text2(e, preserve_nbsp) for e in x]
    if isinstance(x, str):
        from lxml import html as lxml_html

        doc = lxml_html.document_fromstring(x)
        bodies = cast("list[lxml_html.HtmlElement]", doc.xpath(".//body"))
        if not bodies:
            return None
        x = bodies[0]
    text = _PaddedText()
    _html_text_block(
        (x.tag.lower() if isinstance(x.tag, str) else "comment", x), text, preserve_nbsp
    )
    return text.output()
