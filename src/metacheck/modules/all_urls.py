"""List All URLs: the links bibr extracted, plus URLs written as plain text."""

from __future__ import annotations

import re
from functools import cache
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

import pandas as pd

from metacheck._r.frames import count
from metacheck.module import module

# what a URL's comparison key leaves out: the scheme, "www." and trailing punctuation
_SCHEME = re.compile(r"^(?:doi:|[a-z][a-z0-9+.-]*://)", re.IGNORECASE)
_TRAILING = ".,;:!?'\")]}>"
# a link's visible text that is a URL (a link to a local file stands for it)
_LOOKS_LIKE_URL = re.compile(
    r"^(?:https?://|doi:|www\.)|^[\w-]+(?:\.[\w-]+)*\.[a-z]{2,}(?:/|$)", re.IGNORECASE
)
# the columns all_urls() adds to extract_urls()'s, last in its table
_ADDED = ["link_text", "url", "domain", "source"]


@cache
def _tlds() -> frozenset[str]:
    """The top-level domains a host name written without a scheme may end in."""
    raw = resources.files("metacheck.resources").joinpath("data/tlds.txt").read_text("utf-8")
    return frozenset(t for t in raw.split() if not t.startswith("#") and t.isalnum())


def _strip_trailing(url: str) -> str:
    """*url* without trailing sentence punctuation; a closing bracket stays when it is paired."""
    while url and url[-1] in _TRAILING:
        last = url[-1]
        opening = {")": "(", "]": "[", "}": "{", ">": "<"}.get(last)
        if opening is not None and url.count(opening) >= url.count(last):
            break
        url = url[:-1]
    return url


def _cut(url: str) -> str:
    """*url* as written in the text: up to a closing bracket it did not open, without
    trailing punctuation (``https://osf.io/abc).foot_2`` is ``https://osf.io/abc``)."""
    depth = 0
    for i, ch in enumerate(url):
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                url = url[:i]
                break
            depth -= 1
    return _strip_trailing(url)


def _key(url: str) -> str:
    """A comparison key: no scheme, no ``www.``, no trailing slash or punctuation, lower case."""
    k = _SCHEME.sub("", _strip_trailing(url.strip())).lower()
    k = k.removeprefix("www.")
    return k.rstrip("/")


def _absolute(url: str) -> str:
    """*url* as a link: ``doi:`` as a doi.org link, ``https://`` added when there is no scheme."""
    url = _strip_trailing(url.strip())
    if url[:4].lower() == "doi:":
        return "https://doi.org/" + url[4:].lstrip()
    if re.match(r"^[a-z][a-z0-9+.-]*:", url, re.IGNORECASE) and "://" in url[:12]:
        return url
    if re.match(r"^(?:mailto|tel|urn|info):", url, re.IGNORECASE):
        return url
    return "https://" + url


def _domain(url: str) -> str | None:
    """The host of *url* (an absolute link), lower case, without ``www.``."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.removeprefix("www.") if host else None


def _is_bare_host(text: str) -> bool:
    """Whether a text match is a host name with nothing that marks it as a link."""
    lower = text.lower()
    return not (
        lower.startswith(("http://", "https://", "doi:", "www.")) or "/" in lower.rstrip("/")
    )


def _repair(text: str, sentence: str) -> str | None:
    """A text match as the URL it is, or ``None`` when it is code or a dotted name.

    A host name that starts with a letter the regular expression does not read
    (``naïve.org``) gets it back, as does a scheme it does not read (``ftp://``), and the match is cut at a closing bracket it did
    not open. A match with a scheme, ``doi:``, ``www.`` or a path is kept. A bare
    host name (``osf.io``, ``pwr.t.test``, ``is.na``) is kept only when it ends in a
    top-level domain, is not called as a function (``is.na(x)``) and is not the
    end of a path or DOI (``xyz.abc`` of ``doi:10.5555/xyz.abc``); an IPv4 address
    only when each part is at most 255.
    """
    at = sentence.find(text)
    before = sentence[:at] if at > 0 else ""
    if before and (before[-1].isalnum() or before[-1] in "_-"):
        start = len(before)
        while start > 0 and (before[start - 1].isalnum() or before[start - 1] in "_-."):
            start -= 1
        text = before[start:] + text
        before = before[:start]
    scheme = re.search(r"\b[a-z][a-z0-9+.-]*://$", before, re.IGNORECASE)
    if scheme and not _SCHEME.match(text):  # a scheme the regular expression does not read (ftp)
        text = scheme.group(0) + text
        before = before[: scheme.start()]
    after = sentence[at + len(text) :][:1] if at >= 0 else ""
    text = _cut(text)
    if not _is_bare_host(text):
        return text
    host = text.rstrip("/").split(":", 1)[0]
    if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host):
        return text if all(int(n) <= 255 for n in host.split(".")) else None
    if host.rsplit(".", 1)[-1].lower() not in _tlds() or after == "(" or before.endswith("/"):
        return None
    return text


def _link_target(href: str, link_text: Any) -> str | None:
    """The URL a link of bibr's ``url`` table points to, or ``None`` to leave it out.

    Line-wrap whitespace and a trailing period are removed, as bibr's own export
    does. An http(s) link needs a dot in its host (``http://pss`` is a cut-off
    URL). A link to a local file (a converter's temporary path) stands for its
    visible text when that text is a URL. E-mail links are left out, as e-mail
    addresses in the text are.
    """
    url = "".join(href.split()).rstrip(".")
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme in ("http", "https", "ftp"):
        return url if "." in parts.netloc else None
    if scheme == "mailto":
        return None
    if scheme in ("", "file"):
        shown = "".join(link_text.split()) if isinstance(link_text, str) else ""
        return _absolute(shown) if shown and _LOOKS_LIKE_URL.match(shown) else None
    return url


def _link_rows(paper: Any, sentences: pd.DataFrame) -> pd.DataFrame:
    """bibr's links (the paper's ``url`` table) on the main-text sentences that hold them."""
    from metacheck.papers.tables import paper_table

    links = paper_table(paper, "url")
    if not len(links) or "href" not in links.columns or not len(sentences):
        return pd.DataFrame()
    links = links.loc[links["href"].notna() & links["text_id"].notna()]
    for col in ("link_text", "start"):
        if col not in links.columns:
            links = links.assign(**{col: pd.Series(pd.NA, index=links.index, dtype="object")})
    url = [
        _link_target(str(h), t)
        for h, t in zip(links["href"], links["link_text"].astype("object"), strict=True)
    ]
    links = links.assign(url=pd.Series(url, index=links.index, dtype="object")).loc[
        [u is not None for u in url]
    ]
    keep = ["paper_id", "text_id", "url", "link_text", "start"]
    rows = links.loc[:, keep].merge(sentences, on=["paper_id", "text_id"], how="inner")
    return rows.assign(text=rows["url"], source="link")


def _merge(links: pd.DataFrame, found: pd.DataFrame) -> pd.DataFrame:
    """bibr's links, and the text matches that are not one of them.

    A text match is the same URL as a link in its sentence when their keys are
    equal, or when one starts with the other (a URL cut by a line break or a
    space), except that a bare host name (``AsPredicted.org``) is not the same
    URL as a link to a page on it. A link whose URL is printed takes the printed
    form as its ``text``; one printed longer than bibr read it (a line break
    inside the URL) also takes the longer URL.
    """
    keep: list[int] = []
    text = links["text"].astype("object").tolist()
    url = links["url"].astype("object").tolist()
    shown = links["link_text"].astype("object").tolist()
    by_sentence: dict[tuple[Any, Any], list[int]] = {}
    for i, (pid, tid) in enumerate(zip(links["paper_id"], links["text_id"], strict=True)):
        by_sentence.setdefault((pid, tid), []).append(i)
    for j, row in enumerate(found.itertuples(index=False)):
        written = str(row.text)
        k = _key(written)
        same = None
        for i in by_sentence.get((row.paper_id, row.text_id), []):
            lk = _key(url[i])
            if k == lk or k.startswith(lk) or (lk.startswith(k) and not _is_bare_host(written)):
                same = i
                break
        if same is None:
            keep.append(j)
        elif isinstance(shown[same], str) and _key(shown[same]) != k:
            continue  # the link's visible text is not this URL
        elif len(k) > len(_key(url[same])):
            text[same] = written
            url[same] = _absolute(written)
        elif k == _key(url[same]):
            text[same] = written
    links = links.assign(
        text=pd.Series(text, index=links.index, dtype="string"),
        url=pd.Series(url, index=links.index, dtype="string"),
    )
    extra = found.iloc[keep]
    return pd.concat([x for x in (links, extra) if len(x)], ignore_index=True)


@module(
    title="List All URLs",
    description="List all the URLs in the main text.",
    details="""
        Lists the links bibr extracted from the document (including links whose
        visible text is not the URL), and URLs written as plain text that are not
        links, found with a regular expression. Dotted names that are not URLs
        (`pwr.t.test`, `is.na(x)`) are left out.
    """,
    keywords=["general"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object"},
)
def all_urls(paper: Any) -> dict[str, Any]:
    """List every URL in the main text of *paper* (a paper or paper list).

    One row per URL, in document order: the links in bibr's ``url`` table
    (``source`` ``"link"``, with their ``link_text``), then the URLs written as
    plain text that are not links (``source`` ``"text"``, found by
    :func:`~metacheck.text.extract.extract_urls`, without dotted names whose
    last part is not a top-level domain or that are called as functions).
    ``text`` is the URL as written, ``url`` the link it stands for (``doi:``
    as a doi.org link, ``https://`` added when there is no scheme) and
    ``domain`` its host. The summary table counts the URLs per paper; the
    traffic light is ``"info"`` when any URL was found and ``"na"`` otherwise.
    """
    from metacheck.text.extract import extract_urls
    from metacheck.text.search import text_search

    sentences = text_search(paper)
    context = (
        sentences.drop_duplicates(["paper_id", "text_id"])
        .set_index(["paper_id", "text_id"])["text"]
        .to_dict()
    )
    found = extract_urls(paper)
    if len(found):
        repaired = [
            _repair(str(t), str(context.get((pid, tid), "")))
            for t, pid, tid in zip(found["text"], found["paper_id"], found["text_id"], strict=True)
        ]
        found = found.assign(text=pd.Series(repaired, index=found.index, dtype="string"))
        found = found.loc[[t is not None for t in repaired]].reset_index(drop=True)

    links = _link_rows(paper, sentences)
    found = found.assign(source="text")
    table = _merge(links, found) if len(links) else found
    if "url" not in table.columns:
        table = table.assign(url=pd.Series(pd.NA, index=table.index, dtype="object"))
    if "link_text" not in table.columns:
        table = table.assign(link_text=pd.Series(pd.NA, index=table.index, dtype="object"))
    if "start" not in table.columns:
        table = table.assign(start=pd.Series(pd.NA, index=table.index, dtype="object"))

    # document order: paper, sentence, then where the URL starts in the sentence
    order = {pid: i for i, pid in enumerate(dict.fromkeys(sentences["paper_id"]))}

    def _at(r: Any) -> int:
        if not pd.isna(r.start):
            return int(r.start)
        s = str(context.get((r.paper_id, r.text_id), ""))
        at = s.find(str(r.link_text if isinstance(r.link_text, str) else r.text))
        return at if at >= 0 else len(s)

    table = table.assign(
        _p=[order.get(p, len(order)) for p in table["paper_id"]],
        _at=[_at(r) for r in table.itertuples(index=False)],
    )
    table = table.sort_values(["_p", "text_id", "_at"], kind="stable").reset_index(drop=True)
    urls = [
        _absolute(str(t)) if pd.isna(u) else str(u)
        for t, u in zip(table["text"], table["url"], strict=True)
    ]
    table = table.assign(
        text=table["text"].astype("string"),
        link_text=table["link_text"].astype("string"),
        url=pd.Series(urls, dtype="string"),
        domain=pd.Series([_domain(u) for u in urls], dtype="string"),
        source=table["source"].astype("string"),
    )
    columns = [c for c in found.columns if c not in _ADDED] + _ADDED
    table = table.loc[:, columns]

    return {
        "table": table,
        "summary_table": count(table, "paper_id", name="urls"),
        "na_replace": 0,
        "traffic_light": "info" if len(table) else "na",
    }
