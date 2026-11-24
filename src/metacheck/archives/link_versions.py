"""Repository links that a space or a hyphen split (port of ``R/repo-link-versions.R``).

Reading a paper joins the parts of a split link only where the joined form is certain
(:func:`metacheck.io.read._repair_links`). Other splits need the repository itself to say which
form exists: a Dryad DOI cut in the middle (``10.5061/dryad.n2z34 tn2d`` for
``dryad.n2z34tn2d``; ``dryad.n2z34`` is also the shape of a real, older Dryad DOI), a cut after a
deeper "/" (``figshare.com/articles/Figure_data/ 8079713``, while ``github.com/J0vid/ Color`` is
an account and a word), a cut inside a name (``github.com/kun-ecolo gy/SER``), and a hyphen a
line break added (``github.com/Betha-nyL`` for ``BethanyL``).

Only links that may be split are checked, with at most one request for the link as written and
one per other form. No key is used: GitHub and GitLab answer on their websites, Dryad and Zenodo
through their DOIs at doi.org, Figshare through its public API. OSF is not checked: its website
answers for any address, and its API allows 100 requests an hour without a key.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import unquote

import pandas as pd

from metacheck._values import is_missing

__all__: list[str] = []

_TYPES = ("github", "gitlab", "dryad", "zenodo", "figshare")
_TRAIL = re.compile(r"[\].,;:)]+$")
_DOI = re.compile(r"10\.[0-9]{4,9}/\S+")


def _repo_link_exists(url: str, repo_type: str) -> bool | None:
    """Port of ``.repo_link_exists()``: whether a repository link names an item that exists.

    ``True``; ``False`` when it does not, or names no item at all (an account, or a site's own
    page); ``None`` (``NA``) when the host is not one checked here, or the check itself failed. A
    link of another repository that holds a DOI is checked through doi.org's handle service,
    which answers for every registration agency.
    """
    from metacheck.archives.dryad import _dryad_doi
    from metacheck.archives.figshare import _figshare_id, _figshare_project_id
    from metacheck.archives.github import _github_account_name, _github_repo_name, github_repo
    from metacheck.archives.gitlab import _gitlab_repo_path, gitlab_repo
    from metacheck.archives.zenodo import _zenodo_id
    from metacheck.db.doi import doi_resolves

    url = _TRAIL.sub("", url)
    m = _DOI.search(url)
    doi = m.group(0) if m else None
    try:
        if repo_type == "github":
            if _github_account_name(url) is not None or _github_repo_name(url) is None:
                return False
            return github_repo(url) is not None
        if repo_type == "gitlab":
            if _gitlab_repo_path(url) is None:
                return False
            return gitlab_repo(url) is not None
        if repo_type == "dryad":
            d = _dryad_doi(url)
            return False if d is None else _as_flag(doi_resolves(d))
        if repo_type == "zenodo":
            z = _zenodo_id(url)
            return False if z is None else _as_flag(doi_resolves(f"10.5281/zenodo.{z}"))
        if repo_type == "figshare":
            art = _figshare_id(url)
            proj = _figshare_project_id(url)
            if art is not None:
                return _figshare_item_exists("articles", art)
            if proj is not None:
                return _figshare_item_exists("projects", proj)
            return _as_flag(doi_resolves(doi)) if doi else False
        return _as_flag(doi_resolves(doi)) if doi else None
    except Exception:
        return None


def _as_flag(x: Any) -> bool | None:
    """A single check result as ``True``, ``False`` or ``None`` (``NA``)."""
    if isinstance(x, list | tuple):
        x = x[0] if x else None
    return None if x is None or is_missing(x) else bool(x)


def _figshare_item_exists(kind: str, id: Any) -> bool | None:
    """Port of ``.figshare_item_exists()``: whether Figshare's public API knows an article or project.

    ``True`` (200), ``False`` (404), ``None`` otherwise.
    """
    from metacheck import http

    try:
        resp = http.request("GET", f"https://api.figshare.com/v2/{kind}/{id}")
    except Exception:
        return None
    if resp is None:
        return None
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    return None


def _repo_link_without_hyphen(url: str) -> list[str]:
    """Port of ``.repo_link_without_hyphen()``: the forms of a link without one of its hyphens.

    One per hyphen, in the order they occur. Only the owner and repository part of a GitHub or
    GitLab link is changed (``github.com/Betha-nyL/DeepKoopman`` to
    ``github.com/BethanyL/DeepKoopman``), never the host.
    """
    host = re.search(r"git(?:hub|lab)\.com/", url, re.IGNORECASE)
    if host is None:
        return []
    return [url[:p] + url[p + 1 :] for p, ch in enumerate(url) if ch == "-" and p >= host.end()]


def _repo_link_joined(url: str, sentences: Sequence[Any], max_words: int = 3) -> list[str]:
    """Port of ``.repo_link_joined()``: the link's other forms, from the paper's own text.

    The link joined with up to three words that follow it after a space in the same sentence. A
    word is joined when it has the shape of a link's part (a "/", ".", "_", "=", "-" or digit, or
    a capital letter after its first letter); an ordinary word ("Further", "The") has none of
    these, so a sentence that goes on after a link gives no other form. While the link so far is
    a GitHub or GitLab account, with no repository yet, any word is joined: a space can fall
    anywhere in an account's name. After a link that ends in a hyphen, the form without the
    hyphen is tried first, as a line break added it. A link found without the "/" that ends it
    in the text is joined with the "/". The longest form comes first, so the caller keeps the
    longest form that exists.
    """
    pat = re.compile(re.escape(url) + rf"(/?)((?:\s+\S+){{1,{max_words}}})", re.IGNORECASE)
    matches = [m for s in sentences if isinstance(s, str) and (m := pat.search(s)) is not None]
    shaped = re.compile(r"[/._=-]|[0-9]|.[A-Z]")
    account = re.compile(r"git(?:hub|lab)\.com/[^/]+/?$", re.IGNORECASE)
    word_ok = re.compile(r"^[A-Za-z0-9._/~=?&#-]+$")
    out: list[str] = []
    n_words: list[int] = []
    for m in matches:
        words = m.group(2).strip().split()
        form = url + m.group(1)
        for k, word in enumerate(words, start=1):
            w = _TRAIL.sub("", word)
            if not word_ok.search(w) or len(w) > 60:
                break
            if not shaped.search(w) and not account.search(form):
                break
            if k == 1 and url.endswith("-") and not m.group(1):
                out.append(url[:-1] + w)
                n_words.append(k)
            form += w
            out.append(form)
            n_words.append(k)
            if w != word:
                break
    # most words first; with as many words, in the order made
    order = sorted(range(len(out)), key=lambda i: -n_words[i])
    return list(dict.fromkeys(out[i] for i in order))


def _paper_link_hrefs(paper: Any) -> list[str]:
    """Port of ``.paper_link_hrefs()``: the links of a paper's own link list, without a closing "/"."""
    u = getattr(paper, "url", None)
    if not isinstance(u, pd.DataFrame) or len(u) == 0 or "href" not in u.columns:
        return []
    return [re.sub(r"/+$", "", str(h)) for h in u["href"].tolist() if not is_missing(h)]


def _repo_link_undoubled(u: list[Any], hrefs: Sequence[str]) -> list[Any]:
    """Port of ``.repo_link_undoubled()``: a link whose ending reading the PDF wrote twice.

    ``macroeco_phylophylo`` or ``California-Vaccine-PolicyPolicy`` gets the single form, when the
    paper's own link list has that form; the other such links are left as written, as a name may
    repeat (``SampSamp``).
    """
    if not u or not hrefs:
        return u
    rx = re.compile(r"([A-Za-z0-9_-]{4,})\1/?$")
    known = {h.lower() for h in hrefs}
    u = list(u)
    for i, x in enumerate(u):
        if isinstance(x, str) and (m := rx.search(x)) is not None:
            single = rx.sub(r"\1", x, count=1)
            if m and single.lower() in known:
                u[i] = single
    return u


def _repo_link_unwrapped(url: Any) -> Any:
    """Port of ``.repo_link_unwrapped()``: the link inside an e-mail security wrapper, or *url*."""
    if is_missing(url):
        return url

    def dec(x: str) -> str:
        try:
            return unquote(x)
        except Exception:
            return x

    if re.search(r"safelinks\.protection\.outlook\.com/.*[?&]url=", url, re.IGNORECASE):
        return dec(re.sub(r"^.*[?&]url=([^&]+).*$", r"\1", url, flags=re.IGNORECASE | re.DOTALL))
    if re.search(r"urldefense\.proofpoint\.com/v2/url\?u=", url, re.IGNORECASE):
        x = re.sub(r"^.*\?u=([^&]+).*$", r"\1", url, flags=re.IGNORECASE | re.DOTALL)
        return dec(re.sub(r"-([0-9A-F]{2})", r"%\1", x.replace("_", "/")))
    # (this wrapper writes "https:/" with one slash)
    if re.search(r"urldefense\.com/v3/__", url, re.IGNORECASE):
        inner = re.sub(r"^.*/v3/__(.+?)__.*$", r"\1", url, count=1, flags=re.IGNORECASE | re.DOTALL)
        return re.sub(r"^(https?:)/(?!/)", r"\1//", inner, count=1)
    if re.search(r"protect\.checkpoint\.com/v2/___", url, re.IGNORECASE):
        return re.sub(
            r"^.*/v2/___(.+?)___.*$", r"\1", url, count=1, flags=re.IGNORECASE | re.DOTALL
        )
    return url


_KEY_HEAD = re.compile(r"^(?:https?://)?(?:www\.|dx\.)?(?:doi\.org/)?")


def _link_key(urls: Sequence[Any]) -> list[str | None]:
    """The comparable form of a link: no scheme, ``www.``, ``doi.org/`` or closing ``/`` or ``.``."""
    return [
        None if is_missing(u) else re.sub(r"[/.]+$", "", _KEY_HEAD.sub("", str(u).lower()))
        for u in urls
    ]


def _text_by_paper(paper: Any) -> dict[str, list[Any]]:
    from metacheck.papers.tables import paper_table

    tx = paper_table(paper, "text", cols=["text"])
    by: dict[str, list[Any]] = {}
    if "paper_id" in tx.columns and "text" in tx.columns:
        for pid, t in zip(tx["paper_id"].tolist(), tx["text"].tolist(), strict=True):
            by.setdefault(str(pid), []).append(t)
    return by


def _paper_of(paper: Any, pid: Any) -> Any:
    """``paper[[pid]]`` of a paper list, or the paper itself (``None`` when it is not there)."""
    from metacheck.papers.model import is_paper_list

    if not is_paper_list(paper):
        return paper
    try:
        return paper[str(pid)]
    except (KeyError, IndexError, TypeError):
        return None


_SAME_PREFIX = re.compile(r"^(?:https?://)?(?:www\.|dx\.)?(?:doi\.org/)?", re.IGNORECASE)
_OSF_PROJECT = re.compile(
    r"^(?:10\.17605/)?(osf\.io/[a-z0-9]{5})(?:$|[/?#]).*$", re.IGNORECASE | re.DOTALL
)


def _repo_link_same_key(urls: Sequence[Any]) -> list[str]:
    """Port of ``.repo_link_same_key()``: the form of a link by which two ways of writing one address are found.

    Without "https://", "www.", "dx.", "doi.org/" and a closing "/", the website's name in small
    letters, and a DOI all in small letters (DOIs do not depend on case). An OSF project is one
    address however it is written: its DOI ("10.17605/OSF.IO/W4Q6J"), its page
    ("osf.io/bt2xy/overview"), with a view-only key ("osf.io/qhwds/?view_only=..."), or a file in
    it ("osf.io/pcjnb/files/qkx9f"), and its five-character identifier does not depend on case.
    Each such pair was two repositories (metacheck #648, K8c: 23 of 132 preprints).
    """
    out: list[str] = []
    for url in urls:
        b = _SAME_PREFIX.sub("", str(url), count=1)
        b = re.sub(r"/+$", "", b)
        host = re.sub(r"/.*$", "", b, flags=re.DOTALL)
        b = host.lower() + b[len(host) :]
        b = b.lower() if re.match(r"10\.\d{4,9}/", b) else b
        m = _OSF_PROJECT.match(b)
        out.append(m.group(1).lower() if m else b)
    return out


def _repo_links_tidy(repos: pd.DataFrame, paper: Any) -> pd.DataFrame:
    """Port of ``.repo_links_tidy()``: repo_check()'s gathered links, before they are checked.

    Tidied for damage that reading the PDF did to them: a link inside an e-mail security wrapper
    (Outlook, Proofpoint, Check Point) is replaced by the link it wraps; a link ends where another
    begins inside it (a DOI written straight after it, a DOI link whose ``https://doi`` was lost,
    or ``https`` run into it); a website's home page alone, or a DOI's prefix alone, is not a
    repository and is left out; a link that the paper also gives longer is left out when it
    cannot be whole (an account beside one of its repositories, a link ending in "-", "_" or ".",
    an OSF identifier of fewer than 5 characters); an OSF identifier of 10 characters is two run
    together, and the first is kept; an ending written twice gets its single form when the paper's
    own link list has it.
    """
    if len(repos) == 0:
        return repos
    repos = repos.copy()
    u: list[Any] = [_repo_link_unwrapped(x) for x in repos["repo_url"].tolist()]

    def sub_all(pattern: str, repl: str, flags: int = 0) -> None:
        rx = re.compile(pattern, flags)
        for i, x in enumerate(u):
            if isinstance(x, str):
                u[i] = rx.sub(repl, x, count=1)

    # a link ends where another begins inside it
    sub_all(r"(?<!doi)(?<!DOI)\.org/10\.[0-9]{4,9}/.*$", "", re.DOTALL)
    first_rx = re.compile(r"10\.[0-9]{4,9}/")
    glued_rx = re.compile(r"(?<=[A-Za-z0-9])10\.[0-9]{4,9}/")
    for i, x in enumerate(u):
        if not isinstance(x, str):
            continue
        f = first_rx.search(x)
        cuts = [m.start() for m in glued_rx.finditer(x) if f is None or m.start() > f.start()]
        if cuts:
            u[i] = x[: cuts[0]]
    sub_all(r"(?<=[A-Za-z0-9.])https?://.*$", "", re.DOTALL)
    sub_all(r"[.]https?$", "", re.IGNORECASE)
    # what is left of the space or punctuation between the two links
    sub_all(r"(?:%20|[;,\s])+$", "")
    sub_all(r"[.,;:]+$", "")
    # an OSF identifier has five characters: ten are two run together
    sub_all(r"(osf\.io/[a-z0-9]{5})[a-z0-9]{5}(?=$|[/?#])", r"\1", re.IGNORECASE)
    # an ending written twice, when the paper's own link list has it once
    pids = repos["paper_id"].tolist()
    for pid in dict.fromkeys(pids):
        idx = [i for i, p in enumerate(pids) if p == pid]
        p = _paper_of(paper, pid)
        if p is not None:
            fixed = _repo_link_undoubled([u[i] for i in idx], _paper_link_hrefs(p))
            for i, x in zip(idx, fixed, strict=True):
                u[i] = x
    # a DOI written right after the link in the text, where the link's pattern stopped at the
    # DOI's first ".": "dryad.w0vt4b8sh10.1007/s00018-..."
    by_paper = _text_by_paper(paper)
    for i, x in enumerate(u):
        if isinstance(x, str) and x.endswith("10"):
            rx = re.compile(re.escape(x) + r"\.[0-9]{4,9}/", re.IGNORECASE)
            if any(isinstance(s, str) and rx.search(s) for s in by_paper.get(str(pids[i]), [])):
                u[i] = x[:-2]
    repos["repo_url"] = pd.Series(u, index=repos.index, dtype="string")
    # a website's home page alone, or a DOI's prefix alone ("doi.org/10.5281"); a name without
    # "/" that is not a website's ("knb.341.1", a DataONE identifier) stays
    home = []
    for x in u:
        if not isinstance(x, str):
            home.append(False)
            continue
        bare = re.sub(r"/+$", "", re.sub(r"^(?:https?://)?(?:www\.|dx\.)?", "", x.lower()))
        home.append(
            bool(re.search(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}$", bare))
            or bool(re.search(r"^(?:doi\.org/)?10\.[0-9]{4,9}$", bare))
        )
    repos = repos.loc[[not h for h in home]].drop_duplicates().reset_index(drop=True)
    # one address written in two ways ("https://osf.io/abcde" and "osf.io/abcde/", "github.com/x"
    # and "www.github.com/x") is one repository: it was checked, listed and counted twice (#648).
    # Compared without "https://", "www.", "dx.", "doi.org/" and a closing "/", with the
    # website's name in small letters (and a DOI, which is the same in either case); the form
    # written in full is kept.
    same = _repo_link_same_key(repos["repo_url"].tolist())
    urls_now = repos["repo_url"].tolist()
    pids_now = repos["paper_id"].tolist()
    types_now = repos["repo_type"].tolist()

    # the form with a view-only key first, as only it opens a private project; then the project's
    # own address before a file in it
    def _has(i: int, pattern: str) -> bool:
        return isinstance(urls_now[i], str) and re.search(pattern, urls_now[i], re.I) is not None

    order = sorted(
        range(len(repos)),
        key=lambda i: (
            not _has(i, r"view_only="),
            _has(i, r"/files/"),
            not _has(i, r"^https?://"),
            i,
        ),
    )
    seen: set[tuple[Any, ...]] = set()
    dup = [False] * len(repos)
    for i in order:
        dkey = (str(pids_now[i]), str(types_now[i]), same[i])
        if dkey in seen:
            dup[i] = True
        seen.add(dkey)
    repos = repos.loc[[not d for d in dup]].reset_index(drop=True)
    # a link cut inside a name, beside its longer form; an account beside one of its repositories
    key = _link_key(repos["repo_url"].tolist())
    pids = repos["paper_id"].tolist()
    drop: list[bool] = []
    for i, k in enumerate(key):
        if k is None:
            drop.append(False)
            continue
        others = [o for o, p in zip(key, pids, strict=True) if p == pids[i] and o and o != k]
        longer = [o for o in others if o.startswith(k)]
        # a longer form that only repeats the link's last part is the reader writing it twice
        # ("...Policy" beside "...PolicyPolicy"), not more of it
        longer = [o for o in longer if not (len(o) - len(k) >= 3 and k.endswith(o[len(k) :]))]
        if not longer:
            drop.append(False)
            continue
        nxt = [o[len(k) : len(k) + 1] for o in longer]
        # Only a link that cannot be whole is left out here: an account beside one of its
        # repositories, a link ending in "-", "_" or ".", an OSF identifier of fewer than 5
        # characters. Names often share a beginning: those are decided by _repo_links_joined(),
        # which asks which form exists.
        drop.append(
            (bool(re.search(r"^git(?:hub|lab)\.com/[^/]+$", k)) and "/" in nxt)
            or bool(re.search(r"[-_.]$", k))
            or bool(re.search(r"^(?:10\.17605/)?osf\.io/[a-z0-9]{1,4}$", k))
        )
    return repos.loc[[not d for d in drop]].reset_index(drop=True)


def _repo_links_cut_short(repos: pd.DataFrame) -> list[bool]:
    """Port of ``.repo_links_cut_short()``: the rows to drop from the links of ``_repo_links_joined()``.

    A link that does not exist, when the paper also gives a longer form of it that does
    (``10.6084/m9`` beside ``10.6084/m9.figshare.27262470``).
    """
    key = _link_key(repos["repo_url"].tolist())
    pids = repos["paper_id"].tolist()
    urls = repos["repo_url"].tolist()
    types = repos["repo_type"].tolist()
    out: list[bool] = []
    for i, k in enumerate(key):
        others = [
            j
            for j, o in enumerate(key)
            if k is not None and o is not None and pids[j] == pids[i] and o != k and o.startswith(k)
        ]
        if not others or _repo_link_exists(urls[i], types[i]) is not False:
            out.append(False)
            continue
        out.append(any(_repo_link_exists(urls[j], types[j]) is True for j in others))
    return out


def _repo_links_joined(repos: pd.DataFrame, paper: Any) -> pd.DataFrame:
    """Port of ``.repo_links_joined()``: repo_check()'s table of links, with split links joined.

    Each link that does not exist as written, but exists joined with the rest the text gives it,
    is replaced by that joined form. *paper* is the paper or paper list the links came from.
    """
    # GitHub and GitLab on their websites; the others through their DOI
    repos = repos.copy()
    urls = repos["repo_url"].tolist()
    types = repos["repo_type"].tolist()
    pids = repos["paper_id"].tolist()
    rows = [
        i
        for i, (x, t) in enumerate(zip(urls, types, strict=True))
        if not is_missing(x) and (t in _TYPES or re.search(r"10\.[0-9]{4,9}/", str(x)))
    ]
    if not rows:
        return repos
    by_paper = _text_by_paper(paper)
    for i in rows:
        url = str(urls[i])
        low = url.lower()
        s = [t for t in by_paper.get(str(pids[i]), []) if isinstance(t, str) and low in t.lower()]
        alt = _repo_link_joined(url, s)
        if not alt:
            continue
        if _repo_link_exists(url, types[i]) is not False:
            continue
        for a in alt:
            if _repo_link_exists(a, types[i]) is True:
                urls[i] = a
                break
    repos["repo_url"] = pd.Series(urls, index=repos.index, dtype="string")
    repos = repos.drop_duplicates().reset_index(drop=True)
    # a link cut short, beside its longer form in the same paper
    checkable = [
        t in _TYPES or bool(re.search(r"10\.[0-9]{4,9}/", str(x)))
        for x, t in zip(repos["repo_url"].tolist(), repos["repo_type"].tolist(), strict=True)
    ]
    if any(checkable):
        cut = _repo_links_cut_short(repos.loc[checkable])
        flags = [False] * len(repos)
        for pos, c in zip([i for i, ok in enumerate(checkable) if ok], cut, strict=True):
            flags[pos] = c
        repos = repos.loc[[not f for f in flags]].reset_index(drop=True)
    return repos


def _repo_link_unhyphenated(url: str, exists: Callable[[str], Any]) -> str | None:
    """Port of ``.repo_link_unhyphenated()``: a GitHub or GitLab link the listing could not find.

    The first form without one of its hyphens that *exists* (``github_repo()`` or
    ``gitlab_repo()``) finds, or ``None``. The website is asked, not the API, so a link that is
    not found costs none of the API's hourly requests.
    """
    for a in _repo_link_without_hyphen(url):
        try:
            ok = exists(a) is not None
        except Exception:
            ok = False
        if ok:
            return a
    return None
