"""Preregistration Check (port of ``inst/modules/prereg_check.R``)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any

import pandas as pd

from metacheck._r import plural
from metacheck.module import module
from metacheck.report import collapse_section, format_ref, link, scroll_table

# R: format_ref(vandenAkker2024) / format_ref(Lakens2024), rendered by R's bibentry html style.
_VAN_DEN_AKKER_2024 = (
    "van den Akker O, Bakker M, van Assen M, Pennington C, Verweij L, Elsherif M, Claesen A, "
    "Gaillard S, Yeung S, Frankenberger J, Krautter K, Cockcroft J, Kreuer K, Evans T, "
    "Heppel F, Schoch S, Korbmacher M, Yamada Y, Albayrak-Aydemir N, Wicherts J (2024). "
    "&ldquo;The potential of preregistration in psychology: Assessing preregistration "
    "producibility and preregistration-study consistency.&rdquo; "
    "<em>Psychological Methods</em>. "
    '<a href="https://doi.org/10.1037/met0000687">doi:10.1037/met0000687</a>.'
)
_LAKENS_2024 = (
    "Lakens, Daniël (2024). &ldquo;When and How to Deviate From a Preregistration.&rdquo; "
    "<em>Collabra: Psychology</em>, <b>10</b>(1), 117094. "
    '<a href="https://doi.org/10.1525/collabra.117094">doi:10.1525/collabra.117094</a>.'
)

#: at most this many OSF requests in flight at once (R makes them one by one)
_MAX_WORKERS = 8

_REG_API = "https://api.osf.io/v2/registrations/%s"


def _parallel_map(fn: Callable[[Any], Any], items: Sequence[Any]) -> list[Any]:
    """``lapply(items, fn)`` with the calls made concurrently (results in order)."""
    items = list(items)
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(items))) as pool:
        futures = [pool.submit(copy_context().run, fn, x) for x in items]
        return [f.result() for f in futures]


def _unique(values: Iterable[Any]) -> list[Any]:
    """``unique()``: first appearance order, ``NA`` kept once."""
    return list(dict.fromkeys(values))


def _no_prereg_summary(paper: Any) -> pd.DataFrame:
    """``data.frame(paper_id = paper_id(paper), preregistration = 0)``.

    An empty paper list gives a table without rows; metacheck's
    ``data.frame()`` stops there ("arguments imply differing number of rows:
    0, 1", U79).
    """
    from metacheck.papers.tables import paper_id

    ids = paper_id(paper)
    return pd.DataFrame(
        {"paper_id": pd.Series(ids, dtype="string"), "preregistration": [0.0] * len(ids)}
    )


def _rows_frame(rows: Sequence[Mapping[str, str | None]]) -> pd.DataFrame:
    """``dplyr::bind_rows()`` of one-row named lists (empty lists are skipped)."""
    rows = [r for r in rows if r]
    columns = list(dict.fromkeys(c for r in rows for c in r))  # first-appearance order
    return pd.DataFrame(
        {c: pd.Series([r.get(c) for r in rows], dtype="string") for c in columns},
        index=pd.RangeIndex(len(rows)),
    )


@module(
    title="Preregistration Check",
    description="""
        Retrieve information from preregistrations in a standardised way,
        and make them easier to check.
    """,
    details="""
        The Preregistration Check module identifies preregistrations on the OSF and AsPredicted based on links in the manuscript, retrieves the preregistration text, and organizes the information into a template. The module then uses regular expressions to identify text from AsPredicted, and the API to retrieve text from the OSF. The information in the preregistration is returned.

        The module can’t extract information from non-structured preregistration templates (i.e., where the preregistration is uploaded in a single text field) and it can’t retrieve information in preregistrations that are stored as text documents on the OSF.

        If you want to extend the package to be able to download information from other preregistration sites, reach out to the Metacheck development team.
    """,
    keywords=["method"],
    requires=["network"],
    author=[
        "Daniel Lakens <D.Lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
    ],
)
def prereg_check(paper: Any) -> dict[str, Any]:
    """Port of inst/modules/prereg_check.R::prereg_check().

    Finds OSF and AsPredicted links, keeps the OSF links that are
    registrations, retrieves every registration (OSF API) and AsPredicted
    page, and returns their contents in standard fields (one row per
    registration). OSF links that cannot be read (private, embargoed,
    withdrawn, deleted) are reported by name.
    """
    from metacheck.archives.aspredicted import aspredicted_info, aspredicted_links
    from metacheck.archives.osf import osf_check_id, osf_get_all_pages, osf_links, osf_type
    from metacheck.modules import _prereg
    from metacheck.utils import suppress_messages

    # table ----
    links_ap = aspredicted_links(paper)
    links_osf = osf_links(paper)

    ## no links ----
    if len(links_ap) == 0 and len(links_osf) == 0:
        return {
            "traffic_light": "na",
            "summary_text": "No preregistration links were found.",
            "summary_table": _no_prereg_summary(paper),
        }

    ## AsPredicted preregs ----
    # only with AsPredicted links: metacheck always calls aspredicted_info(),
    # which checks that aspredicted.org is online, so OSF-only papers failed
    # offline (U109)
    table_ap = pd.DataFrame()
    if len(links_ap):
        with suppress_messages():  # R: suppressMessages(aspredicted_info(...))
            table_ap = aspredicted_info(links_ap["href"].tolist())
    ap_schema_table = _prereg.ap_schema(table_ap)

    ## OSF prereg ----
    osf_hrefs = links_osf["href"].tolist()
    checked = osf_check_id(osf_hrefs) if osf_hrefs else []
    osf_ids = _unique(checked)
    # no OSF ids, no lookups: metacheck's osf_type(character(0)) stops the module
    # for an AsPredicted-only paper (U109)
    link_types = _parallel_map(osf_type, osf_ids)
    reg_ids = [i for i, t in zip(osf_ids, link_types, strict=True) if t == "registrations"]
    inaccessible_ids = {i for i, t in zip(osf_ids, link_types, strict=True) if t == "inaccessible"}

    ## no registrations ----
    n_osf = len(links_osf)
    if not reg_ids and not inaccessible_ids and len(table_ap) == 0:
        return {
            "traffic_light": "na",
            "summary_text": f"We found {n_osf} OSF link{plural(n_osf)}, but no registrations.",
            "na_replace": 0,
            "summary_table": _no_prereg_summary(paper),
        }

    ## get reg info from OSF ----
    urls = [_REG_API % rid for rid in reg_ids]
    inaccessible_regs = [
        href
        for href, cid in zip(osf_hrefs, checked, strict=True)
        if cid is not None and cid in inaccessible_ids
    ]
    reg_infos = _parallel_map(osf_get_all_pages, urls)

    # registration schemas, fetched once per schema (R fetches one per registration)
    schema_urls = _unique(
        u
        for info in reg_infos
        if len(info) != 0 and isinstance(info, dict)
        for u in [_prereg.needs_schema(info)]
        if u is not None
    )
    schema_bodies = dict(
        zip(schema_urls, _parallel_map(_prereg.fetch_schema_json, schema_urls), strict=True)
    )

    def fetch_schema(url: str) -> Any:
        if url not in schema_bodies:
            schema_bodies[url] = _prereg.fetch_schema_json(url)
        return schema_bodies[url]

    ps: list[dict[str, str]] = []
    # which papers link each registration (U110): by OSF id, not by link text
    papers_of: list[list[Any]] = []
    osf_papers: dict[Any, list[Any]] = {}
    for pid, cid in zip(links_osf["paper_id"].tolist(), checked, strict=True):
        if cid is not None and pid not in osf_papers.setdefault(cid, []):
            osf_papers[cid].append(pid)
    for rid, reg_info in zip(reg_ids, reg_infos, strict=True):
        if len(reg_info) == 0:
            if getattr(reg_info, "osf_error", None) is not None:
                # the paper's own links, not the API URL metacheck reports (U110)
                inaccessible_regs.extend(
                    href for href, cid in zip(osf_hrefs, checked, strict=True) if cid == rid
                )
            continue
        extracted = _prereg.osf_prereg_extract(reg_info, fetch_schema)
        ps.append(_prereg.flatten_schema(extracted))
        papers_of.append(osf_papers.get(rid, []))

    # one row per AsPredicted preregistration: metacheck pastes every AsPredicted
    # prereg of the call (of all papers) into one row, with "NA" for missing
    # fields, whose joined link then matches no paper (U109)
    ap_rows = _prereg.schema_rows(ap_schema_table)
    ap_papers: dict[Any, list[Any]] = {}
    for pid, href in zip(links_ap["paper_id"].tolist(), links_ap["href"].tolist(), strict=True):
        if pid not in ap_papers.setdefault(href, []):
            ap_papers[href].append(pid)
    papers_of.extend(ap_papers.get(row.get("link"), []) for row in ap_rows)

    # make sure all items are not lists
    prereg_info = _rows_frame([*ps, *ap_rows])
    n_inacc = len(inaccessible_regs)
    inaccessible_text = (
        "The following registration link"
        f"{plural(n_inacc)} could not be accessed and may be private, embargoed, or "
        f"withdrawn: {', '.join(inaccessible_regs)}."
    )

    ## every registration inaccessible, nothing else found ----
    if len(prereg_info) == 0 and n_inacc > 0:
        return {
            "traffic_light": "na",
            "summary_text": (
                f"We found {n_osf} OSF link{plural(n_osf)}, but no accessible registrations. "
                f"{n_inacc} registration link{plural(n_inacc)} could not be accessed "
                "(private, embargoed, or withdrawn)."
            ),
            "na_replace": 0,
            "summary_table": _no_prereg_summary(paper),
            "report": inaccessible_text,
        }

    if len(prereg_info):
        # one row per registration and paper that links it: metacheck joins by
        # link text, so http://, upper-case or view_only links matched no paper
        # and a link given twice duplicated the registration (U110)
        rows = [(i, pid) for i, pids in enumerate(papers_of) for pid in (pids or [None])]
        prereg_info = prereg_info.iloc[[i for i, _ in rows]].reset_index(drop=True)
        if "paper_id" in prereg_info.columns:
            # a registration field called "paper_id" keeps its value (dplyr's suffix)
            prereg_info = prereg_info.rename(columns={"paper_id": "paper_id.x"})
        prereg_info["paper_id"] = pd.Series([pid for _, pid in rows], dtype="string")

    # traffic light ----
    tl = "info"

    # summary_text ----
    n = len(prereg_info)
    summary_text = f"We found {n} preregistration{plural(n)}."
    if n_inacc > 0:
        summary_text += (
            f" {n_inacc} registration link{plural(n_inacc)} could not be accessed "
            "(private, embargoed, or withdrawn)."
        )

    # report ----
    has_sample_size = "sample_size" in prereg_info.columns
    report_text = None
    if n > 0:
        report_text = (
            "Meta-scientific research has shown that deviations from preregistrations are often "
            "not reported or checked, and that the most common deviations concern the sample "
            "size. We recommend manually checking the full preregistration at the "
            f"link{plural(n)} above"
            f"{', and have provided the preregistered sample size' if has_sample_size else ''}."
        )

    link_cols: dict[str, Any] = {
        "id": pd.Series(link(_col(prereg_info, "link"), _col(prereg_info, "id")), dtype="string")
    }
    if "title" in prereg_info.columns:
        link_cols["title"] = prereg_info["title"].reset_index(drop=True)
    if "template_name" in prereg_info.columns:
        link_cols["template"] = prereg_info["template_name"].reset_index(drop=True)
    prereg_link_table = pd.DataFrame(link_cols)

    samplesize_table = prereg_info.loc[:, ["id", "sample_size"]] if has_sample_size else None

    ## summary output for paperlists ----
    if "paper_id" not in prereg_info.columns:
        # R: dplyr::count() on a table without paper_id
        raise ValueError(
            "Must group by variables found in `.data`.\n✖ Column `paper_id` is not found."
        )
    from metacheck._r import count

    summary_table = count(prereg_info, "paper_id", name="preregistration")

    ## prereg table ----
    keep = [c for c in prereg_info.columns if prereg_info[c].notna().any()]
    # t(): one row per field, one column per registration (built in one go:
    # inserting a column per registration fragments the frame on large lists)
    cells = prereg_info.loc[:, keep].to_numpy(dtype=object).T
    prereg_table = pd.DataFrame(
        {
            "Field": pd.Series(keep, dtype="string"),
            **{
                f"Preregistration {i + 1}": pd.Series(cells[:, i], dtype="string") for i in range(n)
            },
        }
    )

    ## guidance ----
    guidance = [
        "For metascientific articles demonstrating the rate of deviations from "
        "preregistrations, see:",
        format_ref(_VAN_DEN_AKKER_2024),
        "For educational material on how to report deviations from preregistrations, see:",
        format_ref(_LAKENS_2024),
    ]

    full = collapse_section(scroll_table(prereg_table, maxrows=5), "Full Preregistration")
    blocks: list[Any] = [
        summary_text,
        scroll_table(prereg_link_table),
        report_text,
        inaccessible_text if n_inacc > 0 else None,
        scroll_table(samplesize_table),
        *(full if isinstance(full, list) else [full]),
        collapse_section(guidance),
    ]
    # c(): NULL blocks vanish, but scroll_table(NULL) is "" and stays (R's report
    # then has an empty paragraph where the sample size table would be)
    report = [b for b in blocks if b is not None]

    return {
        "table": prereg_info,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }


def _col(df: pd.DataFrame, name: str) -> list[Any] | None:
    """``df$name`` as a list (``None`` when the column is missing)."""
    if name not in df.columns:
        return None
    return [None if pd.isna(v) else v for v in df[name].tolist()]
