"""Sequence and expression archives (port of ``R/archive-seq.R``, metacheck #660).

NCBI's GEO, the INSDC archives (NCBI's BioProject and SRA, EBI's ENA, DDBJ), and EBI's
ArrayExpress. Papers name these deposits by accession number ("GSE149523", "PRJNA770129",
"SRR7416431", "E-MTAB-6487") more often than by link. The INSDC archives share their records, so
ENA's portal answers for all three, also for a run, experiment, sample or submission number,
which it resolves to its study.

What each record gives (the ownership step of metacheck, which pytacheck does not have, D141):

* GEO: title, contributors by name, submission date, and the PubMed ids of the papers it belongs
  to (turned into DOIs);
* ENA (BioProject, SRA, ENA, DDBJ): title, the submitting institute, first public date; no
  people;
* ArrayExpress (BioStudies): title, authors, release date, and the paper it belongs to (DOI or
  PubMed id).
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import date
from typing import Any

import pandas as pd

from metacheck._r import bind_rows
from metacheck._values import is_missing

__all__ = ["seq_archive_info", "seq_archive_links", "seq_archive_size"]

#: R ``.seq_acc_rx``: accession numbers: GEO series; INSDC projects and studies; INSDC runs,
#: experiments, samples and submissions (resolved to their study); ArrayExpress experiments.
_SEQ_ACC_RX: dict[str, str] = {
    "geo": r"\bGSE[0-9]{3,8}\b",
    "ena_study": r"\bPRJ(?:NA|EB|DB)[0-9]{3,9}\b|\b[SED]RP[0-9]{6,9}\b",
    "ena_other": r"\b[SED]R[RXSA][0-9]{6,9}\b|\bSAM(?:N|EA|D)[0-9]{6,11}\b",
    "arrayexpress": r"\bE-(?!ISSN)[A-Z]{4}-[0-9]{1,7}\b",
}

#: R ``.seq_deposit_words``: words of a sentence that deposits data rather than reuses it, to look
#: these sentences up first when a paper names many accessions (see :func:`seq_archive_links`)
_SEQ_DEPOSIT_WORDS = (
    r"(?i)deposit|submitted|generated (in|for|during) (this|the present|the current)"
    r"|have been (made )?available|our (data|sequenc)|we (deposited|submitted|uploaded)"
)


def _all_rx() -> str:
    return "(?:" + "|".join(_SEQ_ACC_RX.values()) + ")"


def _seq_url(acc: Any, type_: Any) -> Any:
    """Port of ``.seq_url()``: the address of an accession's record, by archive.

    Strings give a string, sequences a list.
    """
    if isinstance(acc, str):
        return _seq_url_one(acc, type_)
    return [_seq_url_one(a, t) for a, t in zip(acc, type_, strict=True)]


def _seq_url_one(acc: str, type_: str) -> str:
    if type_ == "geo":
        return f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}"
    if type_ == "arrayexpress":
        return f"https://www.ebi.ac.uk/biostudies/arrayexpress/studies/{acc}"
    return f"https://www.ebi.ac.uk/ena/browser/view/{acc}"


def _seq_acc_of(url: Any) -> Any:
    """Port of ``.seq_acc_of()``: the accession in a link made by :func:`_seq_url` (or written in a paper).

    ``None`` where there is none. A string gives a string, a sequence a list.
    """
    rx = re.compile(_all_rx())

    def one(u: Any) -> str | None:
        if is_missing(u):
            return None
        m = rx.search(str(u))
        return None if m is None else m.group(0)

    if isinstance(url, str) or not hasattr(url, "__iter__"):
        return one(url)
    return [one(u) for u in url]


def seq_archive_links(paper: Any, max_n: int = 25) -> pd.DataFrame:
    """Port of ``R/archive-seq.R::seq_archive_links()``: sequence and expression archive deposits in a paper.

    Finds deposits in NCBI's Gene Expression Omnibus (GEO series, ``GSE...``), the INSDC archives
    (BioProject ``PRJNA...``, ``PRJEB...``, ``PRJDB...``; SRA, ENA and DDBJ studies ``SRP...``,
    ``ERP...``, ``DRP...``; and runs, experiments, samples and submissions, which are looked up
    as their study), and EBI's ArrayExpress (``E-MTAB-...`` and the like), by accession number or
    link. A sentence that names a project or study is represented by those; a run or sample
    number counts only in a sentence that names no study, and then only the first one, as a
    sentence's runs belong to one study. A paper that names more than *max_n* accessions keeps
    the ones in sentences that deposit data first.

    The table has a row for each deposit: ``href`` (the record's address), ``accession``,
    ``repo_type`` (``geo``, ``ena`` or ``arrayexpress``), and ``text_id`` / ``paper_id``.
    """
    from metacheck._r.base import r_sort_key
    from metacheck.text.search import text_search

    cols = ["paper_id", "href", "accession", "repo_type", "text_id"]
    none = pd.DataFrame(
        {
            "paper_id": pd.Series([], dtype="string"),
            "href": pd.Series([], dtype="string"),
            "accession": pd.Series([], dtype="string"),
            "repo_type": pd.Series([], dtype="string"),
            "text_id": pd.Series([], dtype="Int64"),
        }
    )
    hits = text_search(paper, _all_rx(), return_="sentence", perl=True, ignore_case=False)
    if not isinstance(hits, pd.DataFrame) or len(hits) == 0:
        return none
    pids = hits["paper_id"].tolist() if "paper_id" in hits.columns else [None] * len(hits)
    tids = hits["text_id"].tolist() if "text_id" in hits.columns else [None] * len(hits)
    rows: list[dict[str, Any]] = []
    for pid, tid, s in zip(pids, tids, hits["text"].tolist(), strict=True):
        if not isinstance(s, str):
            continue

        def get(k: str, s: str = s) -> list[str]:
            return list(dict.fromkeys(re.findall(f"(?:{_SEQ_ACC_RX[k]})", s)))

        geo, study, ae = get("geo"), get("ena_study"), get("arrayexpress")
        acc = [*geo, *study, *ae]
        typ = ["geo"] * len(geo) + ["ena"] * len(study) + ["arrayexpress"] * len(ae)
        if not study:
            other = get("ena_other")
            if other:
                acc.append(other[0])
                typ.append("ena")
        if not acc:
            continue
        deposit = re.search(_SEQ_DEPOSIT_WORDS, s) is not None
        for a, t in zip(acc, typ, strict=True):
            rows.append(
                {
                    "paper_id": None if is_missing(pid) else pid,
                    "text_id": None if is_missing(tid) else tid,
                    "accession": a,
                    "repo_type": t,
                    "deposit": deposit,
                }
            )
    if not rows:
        return none
    # order(paper_id, !deposit), then one row per accession of a paper, at most max_n per paper
    # (split() by paper_id: the papers in sorted order)
    rows.sort(
        key=lambda r: (
            (1, ()) if r["paper_id"] is None else (0, r_sort_key(r["paper_id"])),
            not r["deposit"],
        )
    )
    seen: set[tuple[Any, str]] = set()
    kept: list[dict[str, Any]] = []
    for r in rows:
        k = (r["paper_id"], r["accession"])
        if k not in seen:
            seen.add(k)
            kept.append(r)
    per: dict[Any, list[dict[str, Any]]] = {}
    for r in kept:
        per.setdefault(r["paper_id"], []).append(r)
    final = [
        r
        for pid in sorted(per, key=lambda p: (p is None, r_sort_key(p) if p else ()))
        for r in per[pid][:max_n]
    ]
    out = pd.DataFrame(
        {
            "paper_id": pd.Series([r["paper_id"] for r in final], dtype="string"),
            "href": pd.Series(
                _seq_url([r["accession"] for r in final], [r["repo_type"] for r in final]),
                dtype="string",
            ),
            "accession": pd.Series([r["accession"] for r in final], dtype="string"),
            "repo_type": pd.Series([r["repo_type"] for r in final], dtype="string"),
            "text_id": pd.Series([r["text_id"] for r in final], dtype="Int64"),
        }
    )
    return out.loc[:, cols].reset_index(drop=True)


def _seq_fetch(url: str) -> dict[str, Any]:
    """Port of ``.seq_fetch()``: a text answer from an archive.

    ``text`` (``None`` when it fails) and ``unreachable``, true when the archive did not
    answer (no connection, a time-out, too many requests or a server error) rather than
    answering that it has no such record. A deposit whose archive did not answer was
    recorded as "unfound", which reads as a deposit that does not exist.
    """
    from metacheck import http

    try:
        r = http.request("GET", url, max_tries=3, timeout=60)
    except Exception:
        r = None
    if r is None:
        return {"text": None, "unreachable": True}
    status = int(r.status_code)
    if status != 200:
        return {"text": None, "unreachable": status in (408, 429) or status >= 500}
    return {"text": str(r.text), "unreachable": False}


def _seq_get(url: str) -> str | None:
    """Port of ``.seq_get()``: a text answer from an archive (``None`` when it fails)."""
    text: str | None = _seq_fetch(url)["text"]
    return text


def _seq_not_read(fetched: dict[str, Any]) -> str:
    """Port of ``.seq_not_read()``: the error of a record that could not be read.

    "unreachable" when the archive did not answer, else "unfound".
    """
    return "unreachable" if fetched.get("unreachable") is True else "unfound"


def _seq_pubmed_dois(pmids: Any) -> list[str]:
    """Port of ``.seq_pubmed_dois()``: PubMed ids -> DOIs (one request for all)."""
    ids = [
        p
        for p in dict.fromkeys(str(p) for p in pmids if not is_missing(p))
        if re.fullmatch(r"[0-9]+", p)
    ]
    if not ids:
        return []
    txt = _seq_get(
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id="
        + ",".join(ids)
    )
    try:
        result = json.loads(txt or "")["result"]
    except Exception:
        return []
    out: list[str] = []
    for i in ids:
        rec = result.get(i) if isinstance(result, dict) else None
        found = None
        for a in (rec or {}).get("articleids", []) if isinstance(rec, dict) else []:
            if isinstance(a, dict) and a.get("idtype") == "doi":
                found = str(a.get("value")).lower()
                break
        if found is not None:
            out.append(found)
    return out


_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def _seq_date(x: Any) -> Any:
    """Port of ``.seq_date()``: "Sep 24 2024" or "2024-09-24..." -> "2024-09-24" (``None`` for neither)."""

    def one(v: Any) -> str | None:
        if is_missing(v):
            return None
        s = str(v).strip()
        m = re.match(r"([A-Za-z]{3})[A-Za-z]*\s+([0-9]{1,2})\s+([0-9]{4})", s)
        if m and m.group(1).lower() in _MONTHS:
            try:
                return date(
                    int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))
                ).isoformat()
            except ValueError:
                pass
        m = re.match(r"([0-9]{4})-([0-9]{2})-([0-9]{2})", s[:10])
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                return None
        return None

    if isinstance(x, str) or not hasattr(x, "__iter__"):
        return one(x)
    return [one(v) for v in x]


def _info_row(
    url: str, acc: Any, repo_type: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "seq_url": url,
        "accession": acc,
        "repo_type": repo_type,
        "title": None,
        "creators": None,
        "organisation": None,
        "publication_date": None,
        "description": None,
        "resource_type": "Dataset",
        "related": None,
    }
    row.update(extra or {})
    row["error"] = None
    return row


def _frame(row: dict[str, Any]) -> pd.DataFrame:
    return pd.DataFrame({k: pd.Series([v], dtype="string") for k, v in row.items()})


def _seq_geo_info(url: str) -> pd.DataFrame:
    """Port of ``.seq_geo_info()``: one GEO series, one row."""
    acc = _seq_acc_of(url)
    obj = _info_row(url, acc, "geo")
    got = _seq_fetch(
        f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}&targ=self&form=text&view=brief"
    )
    txt = got["text"]
    if txt is None or "!Series_title" not in txt:
        obj["error"] = _seq_not_read(got)
        return _frame(obj)

    def tag(t: str) -> list[str]:
        found = re.findall(rf"(?m)^!{t} = (.*)$", txt)
        return list(dict.fromkeys(f.strip() for f in found))

    # "Manish,V,Bais" -> "Manish Bais"
    people_raw = list(dict.fromkeys([*tag("Series_contributor"), *tag("Series_contact_name")]))
    people = []
    for p in people_raw:
        parts = [x for x in p.split(",") if x]
        people.append(f"{parts[0]} {parts[-1]}" if parts else "NA")
    titles = tag("Series_title")
    obj["title"] = titles[0] if titles else None
    obj["creators"] = "; ".join(dict.fromkeys(people)) if people else None
    obj["organisation"] = "; ".join(tag("Series_contact_institute"))
    dates = tag("Series_submission_date")
    obj["publication_date"] = _seq_date(dates[0] if dates else None)
    obj["description"] = " ".join(tag("Series_summary"))[:2000]
    dois = _seq_pubmed_dois(tag("Series_pubmed_id"))
    if dois:
        obj["related"] = " | ".join(f"IsSupplementTo:{d}" for d in dois)
    return _frame(obj)


def _read_tsv(text: str | None, quote: bool = True) -> list[dict[str, str]] | None:
    """``utils::read.delim(text = t, colClasses = "character")`` as a list of rows (``None`` on failure)."""
    if text is None or text.strip() == "":
        return None
    try:
        reader = csv.reader(
            io.StringIO(text),
            delimiter="\t",
            quotechar='"' if quote else None,
            quoting=csv.QUOTE_MINIMAL if quote else csv.QUOTE_NONE,
        )
        lines = list(reader)
    except Exception:
        return None
    if not lines:
        return None
    header = lines[0]
    return [
        dict(zip(header, row + [""] * (len(header) - len(row)), strict=False)) for row in lines[1:]
    ]


def _seq_ena_info(url: str) -> pd.DataFrame:
    """Port of ``.seq_ena_info()``: one INSDC study (or a run, experiment, sample or submission of it), one row."""
    acc = _seq_acc_of(url)
    obj = _info_row(url, acc, "ena", {"study": None})
    portal = "https://www.ebi.ac.uk/ena/portal/api/"
    study = acc
    if not re.search(_SEQ_ACC_RX["ena_study"], str(acc)):
        # a run, experiment, sample or submission: its study
        if re.match(r"^[SED]RA", str(acc)):
            q = f"search?result=read_run&query=submission_accession=%22{acc}%22"
        else:
            q = f"filereport?accession={acc}&result=read_run"
        got = _seq_fetch(f"{portal}{q}&fields=study_accession&limit=1&format=tsv")
        tab = _read_tsv(got["text"])
        study = tab[0].get("study_accession") if tab else None
        if not study:
            obj["error"] = _seq_not_read(got)
            return _frame(obj)
    got = _seq_fetch(
        f"{portal}search?result=study&query=study_accession=%22{study}%22%20OR%20"
        f"secondary_study_accession=%22{study}%22&fields=study_accession,secondary_study_accession,"
        "study_title,study_description,center_name,first_public&format=tsv"
    )
    tab = _read_tsv(got["text"], quote=False)
    if not tab:
        obj["error"] = _seq_not_read(got)
        return _frame(obj)
    first = tab[0]
    obj["study"] = first.get("study_accession")
    obj["title"] = first.get("study_title")
    desc = first.get("study_description")
    obj["description"] = desc[:2000] if desc is not None else None
    obj["organisation"] = first.get("center_name")
    obj["publication_date"] = _seq_date(first.get("first_public"))
    return _frame(obj)


def _seq_arrayexpress_info(url: str) -> pd.DataFrame:
    """Port of ``.seq_arrayexpress_info()``: one ArrayExpress experiment (a BioStudies study), one row."""
    acc = _seq_acc_of(url)
    obj = _info_row(url, acc, "arrayexpress")
    got = _seq_fetch(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{acc}")
    try:
        j = json.loads(got["text"] or "")
    except Exception:
        j = None
    if not isinstance(j, dict):
        obj["error"] = _seq_not_read(got)
        return _frame(obj)

    # attributes are name/value pairs, on the study and in its subsections
    def val(a: Any, nm: str) -> list[str]:
        return [
            str(x.get("value") or "")
            for x in (a if isinstance(a, list) else [])
            if isinstance(x, dict) and str(x.get("name") or "") == nm
        ]

    sec: dict[str, Any] = j["section"] if isinstance(j.get("section"), dict) else {}
    flat: list[dict[str, Any]] = []

    def walk(s: Any) -> None:
        if s is None:
            return
        if isinstance(s, dict) and s.get("type") is not None:
            flat.append(s)
        elif isinstance(s, dict):
            for e in s.values():
                walk(e)
        elif isinstance(s, list):
            for e in s:
                walk(e)

    walk(sec.get("subsections"))
    type_of = [str(s.get("type") or "").lower() for s in flat]
    authors = [
        v
        for s, t in zip(flat, type_of, strict=True)
        if t == "author"
        for v in val(s.get("attributes"), "Name")
    ]
    orgs = [
        v
        for s, t in zip(flat, type_of, strict=True)
        if t in ("organization", "organisation")
        for v in val(s.get("attributes"), "Name")
    ]
    pubs = [s for s, t in zip(flat, type_of, strict=True) if t == "publication"]
    title = val(sec.get("attributes"), "Title") or val(j.get("attributes"), "Title")
    obj["title"] = title[0] if title else None
    desc = val(sec.get("attributes"), "Description")
    obj["description"] = desc[0][:2000] if desc else None
    obj["creators"] = "; ".join(dict.fromkeys(authors)) if authors else None
    obj["organisation"] = "; ".join(dict.fromkeys(orgs)) if orgs else None
    rel = val(j.get("attributes"), "ReleaseDate")
    obj["publication_date"] = _seq_date(rel[0] if rel else None)
    dois = [d.lower() for s in pubs for d in val(s.get("attributes"), "DOI")]
    dois += _seq_pubmed_dois([p for s in pubs for p in val(s.get("attributes"), "PubMed ID")])
    dois = list(dict.fromkeys(d for d in dois if d))
    if dois:
        obj["related"] = " | ".join(f"IsSupplementTo:{d}" for d in dois)
    return _frame(obj)


def seq_archive_info(
    seq_url: Any, id_col: int | str = 1, pb: Any = None, cache: bool = False
) -> pd.DataFrame:
    """Port of ``R/archive-seq.R::seq_archive_info()``: the records of sequence and expression archive deposits.

    Reads the record of each deposit :func:`seq_archive_links` finds: a GEO series from GEO, an
    INSDC study (BioProject, SRA, ENA, DDBJ) from ENA's portal, an ArrayExpress experiment from
    BioStudies. A run, experiment, sample or submission number is read as its study.

    *seq_url* is the records' addresses, as :func:`seq_archive_links` gives them in ``href``, or
    a table with them in column *id_col*. Returns a row per address: ``seq_url``, ``accession``,
    ``repo_type``, ``title``, ``creators`` (names, separated by "; "), ``organisation`` (the
    submitting institute), ``publication_date``, ``description``, ``resource_type``,
    ``related`` (``IsSupplementTo:<DOI>`` for each paper the record belongs to), and ``error``
    ("unfound" when the archive has no such record, "unreachable" when the archive did not
    answer: no connection, a time-out, too many requests or a server error). With *cache*, each answer is kept (see
    ``.repo_info_cache_get()``).
    """
    from metacheck.archives import _spinner, _tick
    from metacheck.archives.dataverse import _chr_values
    from metacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_put,
        _repo_info_ok,
    )
    from metacheck.archives.psycharchives import _column

    raw: Any = (
        seq_url[_column(seq_url, id_col)].tolist() if isinstance(seq_url, pd.DataFrame) else seq_url
    )
    values, _ = _chr_values(raw)
    urls = [u for u in dict.fromkeys(v for v in values if v is not None) if u != ""]
    cols = [
        "seq_url",
        "accession",
        "repo_type",
        "title",
        "creators",
        "organisation",
        "publication_date",
        "description",
        "resource_type",
        "related",
        "error",
    ]
    if not urls:
        return pd.DataFrame({c: pd.Series([], dtype="string") for c in cols})
    rows: list[pd.DataFrame] = []
    with _spinner(pb, "Retrieve") as bar:
        for u in urls:
            cached = _repo_info_cache_get("seq", u) if cache is True else None
            if cached is not None:
                rows.append(cached)
                continue
            _tick(bar, f"* Retrieving the archive record {_seq_acc_of(u)}...")
            try:
                if re.search(r"ncbi\.nlm\.nih\.gov/geo/", u):
                    res = _seq_geo_info(u)
                elif re.search(r"biostudies|arrayexpress", u):
                    res = _seq_arrayexpress_info(u)
                else:
                    res = _seq_ena_info(u)
            except Exception as e:
                res = pd.DataFrame(
                    {
                        "seq_url": pd.Series([u], dtype="string"),
                        "accession": pd.Series([_seq_acc_of(u)], dtype="string"),
                        "repo_type": pd.Series([None], dtype="string"),
                        "error": pd.Series([str(e)], dtype="string"),
                    }
                )
            if cache is True and _repo_info_ok(res):
                _repo_info_cache_put("seq", u, res)
            rows.append(res)
    return bind_rows(rows)


def _seq_geo_suppl_bytes(acc: str) -> float:
    """Port of ``.seq_geo_suppl_bytes()``: the size of a GEO series' supplementary files, from its folder listing.

    ("550K", "1.2G"), in bytes; NaN when the listing cannot be read.
    """
    num = re.sub(r"^GSE", "", acc)
    stub = f"GSE{num[:-3]}nnn" if len(num) > 3 else "GSEnnn"
    html = _seq_get(f"https://ftp.ncbi.nlm.nih.gov/geo/series/{stub}/{acc}/suppl/")
    if html is None:
        return float("nan")
    m = re.findall(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} +[0-9.]+[KMGT]?", html)
    if not m:
        return 0.0
    mult = {"K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}
    total = 0.0
    for x in m:
        s = re.sub(r"^.* +", "", x)
        unit = re.sub(r"^[0-9.]+", "", s)
        try:
            total += float(re.sub(r"[KMGT]$", "", s)) * (mult[unit] if unit else 1)
        except (ValueError, KeyError):
            continue
    return total


def seq_archive_size(info: Any) -> pd.DataFrame:
    """Port of ``R/archive-seq.R::seq_archive_size()``: the size of sequence and expression archive deposits.

    How much each deposit holds, without downloading it: an INSDC study's runs and their total
    size (ENA's file report), a GEO series' samples and the size of its supplementary files, an
    ArrayExpress experiment's files. *info* is a table from :func:`seq_archive_info`
    (``seq_url``, ``accession``, ``repo_type``, and ``study`` for INSDC records). Returns
    ``seq_url`` with ``units`` (the number of runs, samples or files), ``unit`` ("runs",
    "samples" or "files") and ``bytes`` (NaN when the archive does not say).
    """
    cols = ["seq_url", "units", "unit", "bytes"]
    if not isinstance(info, pd.DataFrame) or len(info) == 0:
        return pd.DataFrame(
            {
                "seq_url": pd.Series([], dtype="string"),
                "units": pd.Series([], dtype="Int64"),
                "unit": pd.Series([], dtype="string"),
                "bytes": pd.Series([], dtype="float64"),
            }
        )
    out_rows: list[dict[str, Any]] = []
    for i in range(len(info)):
        u = info["seq_url"].iloc[i]
        acc = info["accession"].iloc[i]
        type_ = info["repo_type"].iloc[i] if "repo_type" in info.columns else None
        row: dict[str, Any] = {"seq_url": u, "units": None, "unit": None, "bytes": float("nan")}
        if type_ == "ena":
            study_v = info["study"].iloc[i] if "study" in info.columns else None
            study = acc if is_missing(study_v) else study_v
            t = _seq_get(
                f"https://www.ebi.ac.uk/ena/portal/api/filereport?accession={study}"
                "&result=read_run&fields=run_accession,fastq_bytes,submitted_bytes&format=tsv"
            )
            tab = _read_tsv(t)
            if tab is not None:
                total = 0.0
                for r in tab:
                    b = r.get("fastq_bytes") or r.get("submitted_bytes") or ""
                    for part in b.split(";"):
                        try:
                            total += float(part)
                        except ValueError:
                            continue
                row["units"] = len(tab)
                row["unit"] = "runs"
                row["bytes"] = total
        elif type_ == "geo":
            t = _seq_get(
                f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={acc}&targ=self&form=text&view=brief"
            )
            if t is not None:
                row["units"] = len(re.findall(r"(?m)^!Series_sample_id", t))
                row["unit"] = "samples"
                row["bytes"] = _seq_geo_suppl_bytes(str(acc))
        elif type_ == "arrayexpress":
            try:
                j = json.loads(
                    _seq_get(f"https://www.ebi.ac.uk/biostudies/api/v1/studies/{acc}/info") or ""
                )
            except Exception:
                j = None
            if isinstance(j, dict) and j.get("files") is not None:
                try:
                    row["units"] = int(j["files"])
                    row["unit"] = "files"
                except (TypeError, ValueError):
                    pass
        out_rows.append(row)
    return pd.DataFrame(
        {
            "seq_url": pd.Series([r["seq_url"] for r in out_rows], dtype="string"),
            "units": pd.Series([r["units"] for r in out_rows], dtype="Int64"),
            "unit": pd.Series([r["unit"] for r in out_rows], dtype="string"),
            "bytes": pd.Series([r["bytes"] for r in out_rows], dtype="float64"),
        }
    ).loc[:, cols]
