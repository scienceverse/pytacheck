"""``read()``: load papers from bibr JSON, Grobid XML or source documents."""

from __future__ import annotations

import os
import warnings
from collections.abc import Sequence
from os import PathLike
from pathlib import Path
from typing import Any, cast

from metacheck.io._files import list_files
from metacheck.log import logger
from metacheck.papers.io import read_bibr
from metacheck.papers.model import Paper, PaperList

__all__ = ["SOURCE_EXTENSIONS", "read"]

# Documents bibr can extract (read() hands these to bibr when it is installed).
SOURCE_EXTENSIONS = (".pdf", ".docx", ".doc", ".html", ".htm", ".xhtml", ".epub", ".nxml")


def _read_xml(path: str, schema_version: str | None = "12.0") -> Paper:
    # R: read() calls .grobid_to_bibr(fp) (grobid_to_bibr() would save JSON)
    from metacheck.io.grobid import _grobid_to_bibr

    return _grobid_to_bibr(path, None, schema_version)


def _read_one(
    path: str, include_images: bool, bibr_options: dict[str, Any], schema_version: str | None
) -> Paper:
    # *path* is used as given (R keeps "./p.xml" or "dir//p.xml" as the XML
    # paper's file_name), so it is not normalised through pathlib here.
    # R: grepl("\\.json$", fp, ignore.case = TRUE), then "\\.xml$"
    base = os.path.basename(path)
    name = base.lower()
    if name.endswith(".json"):
        return read_bibr(path, include_images)
    if name.endswith(".xml"):
        return _read_xml(path, schema_version)
    if os.path.splitext(name)[1] in SOURCE_EXTENSIONS:
        from metacheck.io.bibr import chew

        return cast(Paper, chew(Path(path), include_images=include_images, **bibr_options))
    raise ValueError(f"Don't know how to read {base!r}")


def read(
    file_path: str | PathLike[str] | Sequence[str | PathLike[str]],
    include_images: bool = False,
    recursive: bool = False,
    *,
    schema_version: str | None = "12.0",
    **bibr_options: Any,
) -> Paper | PaperList:
    """Read paper(s) from bibr JSON or Grobid XML files, or a directory of them.

    Like ``metacheck::read()``: a directory is scanned for ``.json``/``.xml``
    files as ``list.files()`` does (lower-case extensions only, hidden files
    and directories skipped, full paths in R's sort order), a ``.xml`` is
    skipped when a ``.json`` of the same name is read and repeated paths are
    read once. A file that cannot be read is logged and skipped with a warning
    that names it and says why, and a single paper is returned unwrapped. A
    single file that cannot be read raises its error (a deliberate difference:
    metacheck warns and returns an empty list). A JSON file with a root ``schema_version`` is a bibr export:
    schema 12.x is read natively (:mod:`metacheck.io.bibr12`), any other
    version (bibr 11.x included) is refused; files without one (bibr v10.x and
    older) are read exactly as metacheck reads them. Source documents (PDF,
    DOCX, HTML, ...) are extracted with bibr when the ``metacheck[bibr]`` extra
    is installed; ``bibr_options`` are passed to :func:`metacheck.io.bibr.chew`.

    ``schema_version`` says how a Grobid TEI (``.xml``) file is converted:
    ``"12.0"`` (the default) gives a paper in bibr export schema 12.x form, as
    ``grobid_to_bibr(schema_version="12.0")`` does; ``None`` gives metacheck's
    older conversion, which is what ``metacheck::read()`` returns. Deliberate
    difference: metacheck's ``read()`` has no such argument and always converts
    TEI to its older format. JSON files are read as they are.
    """
    if schema_version is not None and (
        not isinstance(schema_version, str) or schema_version != "12.0"
    ):
        raise ValueError('schema_version must be None or "12.0"')
    from metacheck._r.regex import grep, gsub

    if isinstance(file_path, str | PathLike):
        names = [os.fspath(file_path)]
    else:
        names = [os.fspath(p) for p in file_path]

    # R: list.files(dir, "\\.(json|xml)$", recursive = recursive, full.names = TRUE)
    if len(names) == 1 and os.path.isdir(os.path.expanduser(names[0])):
        names = list_files(names[0], r"\.(json|xml)$", recursive=recursive)
    if not names:
        from metacheck.utils import message

        message("No JSON or XML files found.")  # metacheck's message(): stderr, verbose()
        return PaperList()

    # R: setdiff(file_path, gsub("\\.json$", ".xml", grep("\\.json$", file_path)))
    # drops the XML twin of a JSON file (names compared exactly, case-sensitive)
    # and repeated names
    xml_dupes = set(gsub(r"\.json$", ".xml", grep(r"\.json$", names, value=True)))
    kept: dict[str, None] = {}
    for name in names:
        if name not in xml_dupes:
            kept.setdefault(name)
    paths = list(kept)  # as given: R stores an XML's path verbatim as its file_name

    papers: list[Paper] = []
    for p in paths:
        try:
            papers.append(_read_one(p, include_images, bibr_options, schema_version))
        except Exception as exc:  # R: tryCatch(error = ...) around every file
            logger("read", {"file": p, "error": str(exc)})
            if len(paths) == 1:
                raise
            warnings.warn(f"Could not read {os.path.basename(p)}: {exc}", stacklevel=2)
    plist = PaperList(papers)
    if len(plist) > 1:
        # metacheck's paperlist() renames the papers that share an ID ("~2", ...; U208)
        from metacheck.papers.ids import resolve

        plist = resolve(plist)
    return plist[0] if len(plist) == 1 else plist


# ---------------------------------------------------------------------------
# Links broken by a space (metacheck 0.4.4.2, #411, #458, #459, #634)
# ---------------------------------------------------------------------------
#
# Reading a PDF (bibr and Grobid alike) sometimes turns the end of a line inside a link into a
# space: "https://github.com/ owner/repo", "10.1016/ j.cognition.2020.104", "doi. org/". The
# link is then not found whole, so a repository is not checked. The repair removes such a space
# only where a fixed part of a link comes before it. Removing the space after every link is not
# safe: most links are followed by an ordinary word. Repositories found by many website names
# (Dataverse, DataONE, DSpace) are left out: no one fixed name comes before the space.

# The text after a space looks like part of a link: it starts with a letter or digit, and before
# the next space it has a "/", a "_", a digit, or a capital letter after a small one
# ("seantuck12/MODISTools", "RoboDoig"); an ordinary word ("with", "Harvard", "review?") does not.
_LINK_PART = r"(?=[A-Za-z0-9])(?=\S*?(?:[/_0-9]|[a-z][A-Z]))"

# Repositories whose links begin with one fixed website name
_LINK_HOSTS = "|".join(
    [
        r"github\.com",
        r"gitlab\.com",
        r"figshare\.com",
        r"bitbucket\.org",
        r"datadryad\.org",
        r"aspredicted\.org",
        r"zenodo\.org",
        r"researchbox\.org",
    ]
)

# The full website names of the repositories repo_check reads, and of the link services before
# them (doi.org, hdl.handle.net)
_LINK_NAMES = [
    "doi.org",
    "dx.doi.org",
    "hdl.handle.net",
    "osf.io",
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "zenodo.org",
    "figshare.com",
    "datadryad.org",
    "researchbox.org",
    "aspredicted.org",
    "data.mendeley.com",
    "data.4tu.nl",
    "psycharchives.org",
    "reshare.ukdataservice.ac.uk",
    "ldbase.org",
    "archive.softwareheritage.org",
]


def _link_names_rx() -> Any:
    from metacheck._r.regex import compile_r

    names = sorted(_LINK_NAMES, key=lambda n: -len(n))  # order(-nchar()), stable
    parts = [r"\s*\.\s*".join(n.split(".")) for n in names]
    return compile_r(r"(?i)\b(?:" + "|".join(parts) + r")(?=\s*/)", perl=True)


def _repair_link_names(x: list[Any]) -> list[Any]:
    """Port of ``.repair_link_names()``: join a website's name that reading the PDF split at a dot.

    A space after a dot ("doi. org/", "hdl. handle. net/") or before one ("doi .org/", "osf
    .io/") in one of the repositories' names is removed when a "/" follows the name, as in every
    link to a deposit. Letters are kept as written ("OSF. IO/" -> "OSF.IO/").
    """
    import re

    rx = _link_names_rx()
    ws = re.compile(r"\s+")
    return [None if s is None else rx.sub(lambda m: ws.sub("", m.group(0)), s) for s in x]


_MAY_HAVE_LINK = None


def _repair_links(x: Any) -> Any:
    """Port of ``.repair_links()``: join links that reading the PDF split with a space.

    *x* is a list of sentences (``None`` stays ``None``). Returns *x* with those spaces
    removed, and GROBID's temporary folder and Check Point security wrappers taken out of
    links; characters are only removed, never added or changed. A single string gives a string.
    """
    from metacheck._r.regex import compile_r

    global _MAY_HAVE_LINK
    scalar = isinstance(x, str)
    items = [x] if scalar else list(x)
    if not items:
        return x
    if _MAY_HAVE_LINK is None:
        # every rule needs a "/", "_ ", "http" or a website's ending after a full stop and a
        # space; most sentences have none of these, so only the others go through the rules
        _MAY_HAVE_LINK = compile_r(
            r"/|_\s|https?\s*:|\.\s+(?:org|com|io)\b", ignore_case=True, perl=True
        )
    may = [i for i, s in enumerate(items) if isinstance(s, str) and _MAY_HAVE_LINK.search(s)]
    if may:
        fixed = _repair_link_rules([items[i] for i in may])
        for i, s in zip(may, fixed, strict=True):
            items[i] = s
    return items[0] if scalar else items


def _repair_link_rules(x: list[Any]) -> list[Any]:
    from metacheck._r.regex import gsub

    # every rule with perl = TRUE, as metacheck
    # GROBID's own temporary folder, written before a link it could not resolve
    # ("file://localhost/opt/grobid/grobid-home/tmp/10.5281/zenodo.15527338"; #634)
    x = gsub(r"file://localhost/\S*?grobid-home/tmp/", "", x, perl=True)
    # a Check Point e-mail security wrapper around a link
    # ("https://protect.checkpoint.com/v2/___https://osf.io/qby5j___.YzJ1..."); wrappers that
    # encode the link (Outlook, Proofpoint) are undone when repo_check gathers links, as
    # decoding changes characters
    x = gsub(
        r"https?://protect\.checkpoint\.com/v2/___(\S+?)___\.[A-Za-z0-9+/=_-]+",
        r"\1",
        x,
        ignore_case=True,
        perl=True,
    )
    # "https ://", "https: //", and "https:// doi.org": only a website's name follows "https://"
    x = gsub(r"\b(https?)\s*:\s*//", r"\1://", x, ignore_case=True, perl=True)
    x = gsub(r"\b(https?://)\s+(?=[A-Za-z0-9])", r"\1", x, ignore_case=True, perl=True)
    # a website's name split at a dot ("doi. org/", "osf .io/", "hdl. handle. net/")
    x = _repair_link_names(x)
    # a space after the "/" of a DOI link or an OSF link
    x = gsub(r"\b(doi\.org/)\s+", r"\1", x, ignore_case=True, perl=True)
    x = gsub(r"\b(osf\.io/)\s+", r"\1", x, ignore_case=True, perl=True)
    # and of another repository, when the text after it looks like part of a link:
    # "zenodo.org/ with doi:" stays as it is
    x = gsub(rf"(?i:\b({_LINK_HOSTS})/)\s+{_LINK_PART}", r"\1/", x, perl=True)
    # a space after a DOI's prefix, which is never followed by one ("10.1016/ j.cognition"), or
    # inside a repository's DOI or record
    x = gsub(r"(\b10\.\d{4,9}/)\s+", r"\1", x, perl=True)
    x = gsub(r"\b(records?/)\s+(?=[0-9])", r"\1", x, ignore_case=True, perl=True)
    x = gsub(r"(/dryad\.|/zenodo\.)\s+([a-z0-9])", r"\1\2", x, ignore_case=True, perl=True)
    x = gsub(r"(/m9\.figshare\.)\s+([0-9])", r"\1\2", x, ignore_case=True, perl=True)

    # a link that ends in "_" goes on after the space: a link never ends in "_"
    # ("github.com/nardus/zoonotic_ rank/tree/main"); a link can be split more than once
    cut = rf"((?i:https?://|www\.|\b(?:osf\.io|{_LINK_HOSTS})/)\S*_)\s+(?=[A-Za-z0-9])"
    for _ in range(5):
        joined = gsub(cut, r"\1", x, perl=True)
        if joined == x:
            break
        x = joined
    return x


# References whose DOI a space split ----
#
# The rules above need a fixed part of a link before the space. In the reference list a DOI is
# often split anywhere, also inside its number ("10.5281/zenodo.76187 49", "10. 5281/ ZENODO.
# 13986711"), and no rule can tell those spaces from the ones after a link. But the reader
# already found each reference's DOI whole (GROBID's <idno type="DOI">, bibr's own), with the
# sentence it is in. Where that DOI is in its sentence only with spaces inside, the spaces are
# removed, so the text holds the DOI the reader found; no character is added or changed (#459).
# Two exceptions. The reader's DOI sometimes took in the word after it, the next reference's
# author or a word of the text, while the sentence has the space: a space before a capitalised
# word is therefore kept. And a DOI that holds a second DOI is left out, as joining it would
# break the copy written right.


def _text_key(x: Any) -> Any:
    """A ``text_id`` as a dict key (``match()`` compares numbers, not types)."""
    from metacheck._values import is_missing

    if is_missing(x):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return str(x)


def _repair_bib_dois(
    text: list[Any], text_id: list[Any], bib_doi: list[Any], bib_text_id: list[Any]
) -> list[Any]:
    """Port of ``.repair_bib_dois()``: join each reference's DOI in its sentence.

    *text* and *text_id* are the sentences of a paper and their ids; *bib_doi* and *bib_text_id*
    the ``doi`` and ``text_id`` columns of its bib table. Returns *text* with spaces inside the
    references' DOIs removed.
    """
    from metacheck._r.regex import compile_r, gsub
    from metacheck._values import is_missing

    if not bib_doi:
        return text
    from metacheck.archives.zenodo_upload import _zenodo_regex_escape

    text = list(text)
    first: dict[Any, int] = {}
    for i, t in enumerate(text_id):
        first.setdefault(_text_key(t), i)
    second_doi = compile_r(r".10\.\d{4,9}/", perl=True)
    for doi, tid in zip(bib_doi, bib_text_id, strict=True):
        if is_missing(doi) or str(doi) == "" or is_missing(tid) or second_doi.search(str(doi)):
            continue
        r = first.get(_text_key(tid))
        if r is None:
            continue
        s = text[r]
        d = str(doi)
        if is_missing(s) or d.lower() in str(s).lower():
            continue
        pat = r"\s*".join(_zenodo_regex_escape(list(d)))
        m = compile_r(pat, ignore_case=True, perl=True).search(s)
        if m is None:
            continue
        joined = gsub(r"\s+(?![A-Z][a-z])", "", s[m.start() : m.end()], perl=True)
        text[r] = s[: m.start()] + joined + s[m.end() :]
    return text


# Line numbers ----
#
# A manuscript with numbered lines keeps some of its line numbers when GROBID or bibr reads it,
# as stand-alone numbers inside sentences ("t(118) = 7.35, p < 538 .001"). Port of
# ``.repair_line_numbers()`` (metacheck 0.4.4.4, #648): the numbers of a paper with a sure sign
# of line numbers (a run of 5 or more numbers adding one from 30 up, or a chain of stand-alone
# numbers rising at the pace of a printed line) are removed, and in a paper without either sign
# nothing is.
_LINE_NO_LABEL = (
    r"(?i)(?:^|\W)(?:tables?|figures?|figs?\.?|stud(?:y|ies)|experiments?|sections?|chapters?|"
    r"appendix|items?|waves?|sessions?|steps?|models?|hypothes[ie]s|pages?|pp?\.|vol\.?|no\.?|"
    r"versions?|grades?|days?|weeks?|years?|months?|et al\.?|refs?\.?|n|k|h|s|t)\s*$"
)
_LINE_NO_TOKEN = r"(?<![\w.,\-\u2212\u2013])\d{1,4}(?![\w,]|\.\d)"  # noqa: S105
_LINE_NO_PREV = frozenset(["[", "(", ",", ";", "\u2013", "-", "/", ":"])
_LINE_NO_NEXT = frozenset(["]", ")", ",", ";", "\u2013", "-", "/", "%", ":"])


def _line_step_ok(dv: int, chars: int) -> bool:
    """Is going *dv* line numbers up over *chars* characters a line's distance?

    40 to 170 characters per line on average; one line may be shorter, down to nothing, as the
    last line of a paragraph, a heading or an empty line can be.
    """
    return (40 <= chars / dv <= 170) or (dv == 1 and chars <= 170)


def _repair_line_numbers(text: list[Any]) -> list[Any]:
    """Port of ``.repair_line_numbers()``: the sentences of one paper without its line numbers."""
    import bisect
    import re
    import statistics

    from metacheck._r.regex import compile_r
    from metacheck._values import is_missing

    if not len(text):
        return text
    t = ["" if is_missing(x) else str(x) for x in text]
    tok_rx = compile_r(_LINE_NO_TOKEN, perl=True)
    label_rx = compile_r(_LINE_NO_LABEL, perl=True)
    sent: list[int] = []
    start: list[int] = []  # 1 = first character
    length: list[int] = []
    for i, x in enumerate(t):
        for m in tok_rx.finditer(x):
            sent.append(i)
            start.append(m.start() + 1)
            length.append(m.end() - m.start())
    n_tok = len(sent)
    if not n_tok:
        return text
    value = [int(t[sent[k]][start[k] - 1 : start[k] - 1 + length[k]]) for k in range(n_tok)]
    offsets = [0]
    for x in t[:-1]:
        offsets.append(offsets[-1] + len(x) + 1)
    pos = [offsets[sent[k]] + start[k] for k in range(n_tok)]
    ws = " \t\r\n"
    before = [t[sent[k]][: start[k] - 1].rstrip(ws) for k in range(n_tok)]
    after = [t[sent[k]][start[k] - 1 + length[k] :].lstrip(ws) for k in range(n_tok)]
    stand = [
        (not before[k] or before[k][-1] not in _LINE_NO_PREV)
        and (not after[k] or after[k][0] not in _LINE_NO_NEXT)
        and label_rx.search(before[k][max(0, len(before[k]) - 15) :]) is None
        for k in range(n_tok)
    ]
    remove = [False] * n_tok

    # runs of 5 or more numbers adding one each, from 30 up, in one sentence
    by_sent: dict[int, list[int]] = {}
    for k in range(n_tok):
        by_sent.setdefault(sent[k], []).append(k)
    for ks in by_sent.values():
        if len(ks) < 5:
            continue
        v = [value[k] for k in ks]
        step = [False] + [
            v[j + 1] - v[j] == 1 and start[ks[j + 1]] - (start[ks[j]] + length[ks[j]]) <= 2
            for j in range(len(ks) - 1)
        ]
        j = 0
        while j < len(step):  # rle(step)
            e = j
            while e + 1 < len(step) and step[e + 1] == step[j]:
                e += 1
            run = e - j + 1
            if step[j] and run >= 4:
                span = range(j - 1, e + 1)  # the number before the run, too
                years = all(1950 <= v[q] <= 2050 for q in span)
                if v[span[0]] >= 30 and not years:
                    for q in span:
                        remove[ks[q]] = True
            j = e + 1

    # the chain of stand-alone numbers, and how fast it advances
    cand = [k for k in range(n_tok) if stand[k] and not remove[k]]
    if len(cand) >= 15:
        v = [value[k] for k in cand]
        n = len(v)
        best = [1] * n
        prev_i = [-1] * n
        for i in range(1, n):
            w = range(max(0, i - 400), i)
            ok = [q for q in w if 1 <= v[i] - v[q] <= 10]
            if ok:
                j = max(ok, key=lambda q: (best[q], -q))
                if best[j] + 1 > best[i]:
                    best[i] = best[j] + 1
                    prev_i[i] = j
        i = best.index(max(best))
        ch: list[int] = []
        while i >= 0:
            ch.append(i)
            i = prev_i[i]
        ch.reverse()
        if len(ch) >= 15:
            dvs = [v[ch[q + 1]] - v[ch[q]] for q in range(len(ch) - 1)]
            dps = [pos[cand[ch[q + 1]]] - pos[cand[ch[q]]] for q in range(len(ch) - 1)]
            rate = [dp / dv for dp, dv in zip(dps, dvs, strict=True)]
            line_steps = sum(1 for r in rate if 50 <= r <= 150)
            other = sum(1 for r in rate if r >= 8)
            if (
                line_steps >= 10
                and line_steps >= 0.5 * other
                and statistics.median([v[q] for q in ch]) >= 50
            ):
                # only a number that is a line's distance from its neighbours in the chain, on
                # both sides, also at the chain's two ends
                ok_step = [_line_step_ok(dv, dp) for dv, dp in zip(dvs, dps, strict=True)]
                keep = [False, *ok_step]
                keep2 = [*ok_step, False]
                for q in range(len(ch)):
                    if keep[q] and keep2[q]:
                        remove[cand[ch[q]]] = True
    if not any(remove):
        return text

    # one pass through the whole text, across sentences, adds each stand-alone number that fits
    # the nearest line number before or after it (see the comments of the R function)
    amount_rx = re.compile(r"^(?:thousand|million|billion|trillion)", re.IGNORECASE)
    row_prev = compile_r(r"\d%?\)?$", perl=True)
    row_next = compile_r(r"^\(?\.?\d", perl=True)
    amount = []
    in_row = []
    for k in range(n_tok):
        prev_word = re.sub(r"^.*\s", "", before[k], flags=re.DOTALL)
        next_word = re.sub(r"\s.*$", "", after[k], flags=re.DOTALL)
        amount.append(amount_rx.search(next_word) is not None)
        in_row.append(
            row_prev.search(prev_word) is not None and row_next.search(next_word) is not None
        )

    def fits(dv: int, dpos: int) -> tuple[bool, bool]:
        near = 1 <= dv <= 10
        return near, near and _line_step_ok(dv, dpos)

    removed = sorted(k for k in range(n_tok) if remove[k])
    for _round in range(20):
        added = False
        for r in [
            k
            for k in range(n_tok)
            if stand[k] and not remove[k] and not amount[k] and not in_row[k]
        ]:
            at = bisect.bisect_left(removed, r)
            l_near, l_ok = (
                fits(value[r] - value[removed[at - 1]], pos[r] - pos[removed[at - 1]])
                if at > 0
                else (False, False)
            )
            a_near, a_ok = (
                fits(value[removed[at]] - value[r], pos[removed[at]] - pos[r])
                if at < len(removed)
                else (False, False)
            )
            if (l_ok or a_ok) and (not l_near or l_ok) and (not a_near or a_ok):
                remove[r] = True
                bisect.insort(removed, r)
                added = True
        if not added:
            break

    # remove each number, with the space after it (or before it, at the end)
    out = list(t)
    for i, ks in by_sent.items():
        gone = [k for k in reversed(ks) if remove[k]]
        if not gone:
            continue
        x = out[i]
        for k in gone:
            a = start[k] - 1
            b = a + length[k]
            if b < len(x) and x[b] == " ":
                b += 1
            elif a > 0 and x[a - 1] == " ":
                a -= 1
            x = x[:a] + x[b:]
        out[i] = x
    return [None if is_missing(orig) else o for orig, o in zip(text, out, strict=True)]


def _removed_positions(old: str, new: str) -> list[int]:
    """Port of ``.removed_positions()``: where *old* has characters that *new* does not.

    The positions (1 = first character) in *old* of the characters that are not in *new*, where
    *new* is *old* with some characters removed (spaces, or a line number).
    """
    gone: list[int] = []
    j = 0
    for i, ch in enumerate(old):
        if j < len(new) and ch == new[j]:
            j += 1
        else:
            gone.append(i + 1)
    return gone


class _Column:
    """One column of a paper's table, read and written as a list (raw JSON records or a frame)."""

    def __init__(self, paper: Paper, table: str) -> None:
        import pandas as pd

        self.paper, self.table = paper, table
        raw = paper._raw_records(table) if table in paper._tables else None
        self.records = raw[0] if raw is not None else None
        self.frame = None
        if self.records is None and table in paper._tables:
            value = paper[table]
            self.frame = value if isinstance(value, pd.DataFrame) else None

    @property
    def n(self) -> int:
        if self.records is not None:
            return len(self.records)
        return 0 if self.frame is None else len(self.frame)

    def has(self, *cols: str) -> bool:
        if self.records is not None:
            names = {k for r in self.records if isinstance(r, dict) for k in r}
            return all(c in names for c in cols)
        return self.frame is not None and all(c in self.frame.columns for c in cols)

    def get(self, col: str) -> list[Any]:
        from metacheck._values import is_missing

        if self.records is not None:
            return [r.get(col) if isinstance(r, dict) else None for r in self.records]
        assert self.frame is not None
        return [None if is_missing(v) else v for v in self.frame[col].tolist()]

    def set(self, col: str, values: list[Any]) -> None:
        import pandas as pd

        self.paper._touch()
        if self.records is not None:
            from metacheck.papers.model import _Lazy

            for r, v in zip(self.records, values, strict=True):
                r[col] = v
            # what the paper derived from these records (typed columns, the sentence table) is
            # keyed by this token: a new one drops it, so a search sees the repaired text
            if self.table in self.paper._lazy:
                self.paper._lazy[self.table] = _Lazy()
            return
        assert self.frame is not None
        self.frame[col] = pd.Series(values, index=self.frame.index, dtype=self.frame[col].dtype)


def _paper_repair_text(paper: Any) -> Any:
    """Port of ``.paper_repair_text()``: clean a paper's sentences of what reading the PDF left wrong.

    Repairs the text table with :func:`_repair_line_numbers` (a manuscript's line numbers),
    :func:`_repair_links` (links split with a space) and :func:`_repair_bib_dois` (references'
    DOIs). The url, xref and eq tables give where each item is in its sentence (``start``,
    counted from 0, and ``end``, just after it); these move back by the number of characters
    removed before them, so a paper written with ``paper_write()`` stays right. Applies to a
    paper or a paper list.
    """
    from metacheck._values import is_missing
    from metacheck.papers.model import PaperList, is_paper_list

    if is_paper_list(paper):
        return PaperList([_paper_repair_text(p) for p in paper])
    if not hasattr(paper, "_tables"):  # not a paper (a list of other objects)
        return paper
    text = _Column(paper, "text")
    if text.n == 0 or not text.has("text"):
        return paper
    old = text.get("text")
    if not all(is_missing(s) or isinstance(s, str) for s in old):
        return paper
    # line numbers first: the link rules would join a line number after "osf.io/ " to the link
    new = _repair_links(_repair_line_numbers(old))
    has_id = text.has("text_id")
    tids = text.get("text_id") if has_id else []
    if has_id:
        bib = _Column(paper, "bib")
        if bib.n and bib.has("doi", "text_id"):
            new = _repair_bib_dois(new, tids, bib.get("doi"), bib.get("text_id"))
    changed = [
        i for i, (a, b) in enumerate(zip(old, new, strict=True)) if not is_missing(a) and a != b
    ]
    if not changed:
        return paper
    text.set("text", new)
    if not has_id:
        return paper
    for tbl in ("url", "xref", "eq"):
        df = _Column(paper, tbl)
        if df.n == 0 or not df.has("text_id", "start", "end"):
            continue
        by_id: dict[Any, list[int]] = {}
        for k, t in enumerate(df.get("text_id")):
            by_id.setdefault(_text_key(t), []).append(k)
        cols = {c: df.get(c) for c in ("start", "end")}
        for r in changed:
            rows = by_id.get(_text_key(tids[r]), [])
            if not rows:
                continue
            gone = _removed_positions(old[r], new[r])
            for vals in cols.values():
                for k in rows:
                    v = vals[k]
                    if not is_missing(v):
                        vals[k] = v - sum(1 for g in gone if g <= v)
        for col, vals in cols.items():
            df.set(col, vals)
    return paper
