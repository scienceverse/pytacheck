"""FORRT Replication Database, FLoRA (port of ``R/db-replications.R``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

from pytacheck.db.databases import database_date, load_database, user_database_path, write_database

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["FLoRA", "FLoRA_date", "FLoRA_update"]

_NAME = "FLoRA"
_OSF_ID = "t4j8f"
_COLUMNS = ["doi_o", "apa_ref_o", "doi_r", "apa_ref_r", "url_r", "outcome", "outcome_quote", "type"]


def FLoRA() -> pd.DataFrame:  # noqa: N802 - R name
    """FORRT Replication Database (port of ``FLoRA()``).

    DOIs of original studies and their replications, with columns ``doi_o``,
    ``apa_ref_o``, ``doi_r`` (may be NA when ``url_r`` is given),
    ``apa_ref_r``, ``url_r``, ``outcome``, ``outcome_quote`` and ``type``
    (replication or reproduction). Use :func:`FLoRA_date` for the download
    date and :func:`FLoRA_update` to update it. Source: https://osf.io/9r62x/files/t4j8f
    """
    return load_database(_NAME)


def FLoRA_date() -> dt.date | None:  # noqa: N802 - R name
    """The date the FLoRA data was downloaded (port of ``FLoRA_date()``)."""
    return database_date(_NAME)


def summarise_flora(csv_path: str | Path) -> pd.DataFrame:
    """Reduce the FLoRA CSV to the 8 columns metacheck keeps.

    The data step of ``FLoRA_update()``: ``readr::read_csv()`` semantics
    (fields trimmed of spaces/tabs, ``""`` and ``"NA"`` are missing), keep
    rows with an original DOI and a replication DOI or URL, keep 8 columns,
    and drop duplicate rows.
    """
    import pandas as pd

    raw = pd.read_csv(
        csv_path, dtype=str, keep_default_na=False, na_values=[], encoding="utf-8"
    )
    missing = [c for c in _COLUMNS if c not in raw.columns]
    if missing:
        raise ValueError(f"Can't subset columns that don't exist: {', '.join(missing)}")
    data = {}
    for c in _COLUMNS:
        s = raw[c].astype("string").str.strip(" \t")
        data[c] = s.mask(s.isin(["", "NA"]))
    flora = pd.DataFrame(data)

    def has(col: str) -> pd.Series:
        return (flora[col].notna() & (flora[col] != "")).fillna(False).astype(bool)

    rows = has("doi_o") & (has("doi_r") | has("url_r"))
    out = flora[rows].drop_duplicates().reset_index(drop=True)
    return out


def _download_csv(dest: Path) -> Path:
    """Download the single OSF file t4j8f (flora.csv) into *dest*."""
    from pytacheck import http
    from pytacheck.db._utils import resp_body_json

    name = "flora.csv"
    url = f"https://osf.io/download/{_OSF_ID}/"
    meta = http.request(
        "GET", f"https://api.osf.io/v2/files/{_OSF_ID}/", headers={"Accept": "application/json"}
    )
    if meta is not None and meta.status_code < 400:
        try:
            info = resp_body_json(meta)["data"]
            name = info["attributes"]["name"] or name
            url = info["links"]["download"] or url
        except (KeyError, TypeError, ValueError):
            pass
    if not name.lower().endswith(".csv"):
        raise RuntimeError(f"The file at osf.io/{_OSF_ID} is missing")
    resp = http.request("GET", url, timeout=300)
    if resp is None or resp.status_code >= 400:
        raise RuntimeError(f"The file at osf.io/{_OSF_ID} is missing")
    path = dest / Path(name).name
    path.write_bytes(resp.content)
    return path


def FLoRA_update() -> Path:  # noqa: N802 - R name
    """Download the newest FLoRA data (port of ``FLoRA_update()``).

    Downloads flora.csv from the OSF (file ``t4j8f``), keeps the rows and
    columns metacheck uses, dates it today and saves it in the user data
    directory, where :func:`FLoRA` picks it up. Returns the path.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        csv = _download_csv(Path(tmp))
        table = summarise_flora(csv)
    return write_database(table, user_database_path(_NAME), _NAME, dt.date.today())
