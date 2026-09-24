"""RetractionWatch database (port of ``R/db-retractionwatch.R``).

DOIs and the nature of their notices (retraction, correction, expression of
concern) from the RetractionWatch database, as distributed by Crossref.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

from pytacheck.db.databases import database_date, load_database, user_database_path, write_database

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
    """
    return load_database(_NAME)


#: Alias of :func:`retractionwatch` (R ``rw``).
rw = retractionwatch


def rw_date() -> dt.date | None:
    """The date the RetractionWatch data was downloaded (port of ``rw_date()``)."""
    return database_date(_NAME)


def summarise_retractionwatch(csv_path: str | Path) -> pd.DataFrame:
    """Reduce the full RetractionWatch CSV to ``doi`` / ``retractionwatch``.

    The data step of ``rw_update()`` (and ``data-raw/retractionwatch.R``):
    ``read.csv()``, keep ``OriginalPaperDOI`` and ``RetractionNature``, drop
    ``"unavailable"`` DOIs and empty natures, and join each DOI's distinct
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
    doi = raw["OriginalPaperDOI"].astype("string")
    nature = raw["RetractionNature"].astype("string")
    keep = ((doi != "unavailable") & (nature != "")).fillna(False).astype(bool)
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

    from pytacheck import http
    from pytacheck.db._utils import default_email

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
