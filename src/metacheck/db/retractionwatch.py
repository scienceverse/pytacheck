"""RetractionWatch database (port of ``R/db-retractionwatch.R``).

DOIs and the nature of their notices (retraction, correction, expression of
concern) from the RetractionWatch database, as distributed by Crossref.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from metacheck.db.databases import (
    _newest_database,
    database_date,
    user_database_path,
    write_database,
)

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["retractionwatch", "rw", "rw_date", "rw_update"]

_NAME = "retractionwatch"
_URL = "https://api.labs.crossref.org/data/retractionwatch?"


def retractionwatch() -> pd.DataFrame:
    """RetractionWatch data (port of ``retractionwatch()``).

    A data frame with columns ``doi`` and ``retractionwatch`` (the nature of
    the note(s), ``;``-separated). Use :func:`rw_date` to find the date it
    was downloaded and :func:`rw_update` to update it; an updated copy in the
    user data directory is used when it is newer than the bundled one.
    Source: https://api.labs.crossref.org/data/retractionwatch

    Rows without a DOI (``""``) are left out: joined on ``doi`` they would
    match every reference without one (metacheck keeps them; U15).
    """
    return _indexed().table.copy(deep=False)


class _Index(NamedTuple):
    source: pd.DataFrame  # the newest database frame
    table: pd.DataFrame  # it without blank DOIs
    dois: list[str]  # each row's DOI in lower case
    rows: dict[str, list[int]]  # the rows of each lower-case DOI


# made once per database file rather than once per call; replaced as a whole, so
# a thread never sees the table of one file with the rows of another
_INDEX: _Index | None = None


def _indexed() -> _Index:
    global _INDEX
    source = _newest_database(_NAME)
    index = _INDEX
    if index is None or index.source is not source:
        table = source
        blank = (table["doi"].isna() | (table["doi"].str.strip() == "")).to_numpy(dtype=bool)
        if blank.any():
            table = table.loc[~blank].reset_index(drop=True)
        dois = [str(d).lower() for d in table["doi"].tolist()]
        rows: dict[str, list[int]] = {}
        for i, doi in enumerate(dois):
            rows.setdefault(doi, []).append(i)
        index = _INDEX = _Index(source, table, dois, rows)
    return index


def rw_rows(dois: Iterable[Any]) -> pd.DataFrame:
    """The RetractionWatch rows of *dois*, compared ignoring case, in database order,
    with each ``doi`` in lower case (missing DOIs match nothing)."""
    import pandas as pd

    index = _indexed()
    wanted = {d.lower() for d in dois if isinstance(d, str)}
    hits = sorted(i for d in wanted for i in index.rows.get(d, ()))
    out = index.table.take(hits).reset_index(drop=True)
    return out.assign(doi=pd.Series([index.dois[i] for i in hits], dtype="string"))


#: Alias of :func:`retractionwatch` (R ``rw``).
rw = retractionwatch


def rw_date() -> dt.date | None:
    """The date the RetractionWatch data was downloaded (port of ``rw_date()``)."""
    return database_date(_NAME)


def summarise_retractionwatch(csv_path: str | Path) -> pd.DataFrame:
    """Reduce the full RetractionWatch CSV to ``doi`` / ``retractionwatch``.

    The data step of ``rw_update()`` (and ``data-raw/retractionwatch.R``):
    ``read.csv()``, keep ``OriginalPaperDOI`` and ``RetractionNature``, drop
    ``"unavailable"`` and blank DOIs and empty natures, and join each DOI's distinct
    natures with ``;`` (DOIs in order of first appearance).
    """
    import pandas as pd

    raw = pd.read_csv(
        csv_path,
        dtype=str,
        keep_default_na=False,
        na_values=["NA"],
        usecols=["OriginalPaperDOI", "RetractionNature"],
        encoding="utf-8",
        encoding_errors="replace",
    )
    # surrounding spaces would stop a DOI from matching
    doi = raw["OriginalPaperDOI"].astype("string").str.strip()
    nature = raw["RetractionNature"].astype("string")
    # a blank DOI would match every reference without one (U15)
    keep = ((doi != "unavailable") & (doi.str.strip() != "") & (nature != "")).fillna(False)
    keep = keep.astype(bool)
    pairs = pd.DataFrame({"doi": doi[keep], "retractionwatch": nature[keep]})
    pairs = pairs.drop_duplicates()
    out = (
        pairs.groupby("doi", sort=False, dropna=False)["retractionwatch"]
        .agg(";".join)
        .reset_index()
    )
    out["doi"] = out["doi"].astype("string")
    out["retractionwatch"] = out["retractionwatch"].astype("string")
    return out


def rw_update() -> Path:
    """Download the newest RetractionWatch data (port of ``rw_update()``).

    The download is >50MB; it is summarised into the small ``doi`` /
    ``retractionwatch`` table, dated today, and saved in the user data
    directory, where :func:`retractionwatch` picks it up. Returns the path.
    """
    import tempfile

    from metacheck import http
    from metacheck.db._utils import default_email

    url = _URL + default_email()
    resp = http.request("GET", url, timeout=300, max_tries=1)  # plain req_perform()
    if resp is None:
        raise RuntimeError(f"Could not download the RetractionWatch data from {url}")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "retractionwatch.csv"
        path.write_bytes(resp.content)
        try:
            table = summarise_retractionwatch(path)
        except ValueError as exc:  # missing columns: the download was not the CSV
            raise RuntimeError(
                f"The RetractionWatch download (HTTP {resp.status_code}) is not the expected CSV: {exc}"
            ) from exc
    return write_database(table, user_database_path(_NAME), _NAME, dt.date.today())
