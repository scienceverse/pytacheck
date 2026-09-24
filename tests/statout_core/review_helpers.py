"""Python sides of the statout_core review parity cases (R idioms)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

DATA = Path(__file__).resolve().parent / "data"


def parse_html_tables(path: str | Path) -> list[Any]:
    """``lapply(xml_find_all(read_html(path), "//table"), .stat_table_parse)``."""
    from pytacheck.statout.stat_tables import _read_html_file, _stat_table_parse

    doc = _read_html_file(str(path))
    return [_stat_table_parse(tb) for tb in doc.xpath("//table")]


def chr_frame(**cols: list[Any]) -> pd.DataFrame:
    """``data.frame(..., stringsAsFactors = FALSE, check.names = FALSE)`` of text."""
    return pd.DataFrame({k: pd.array(v, dtype="string") for k, v in cols.items()})


def with_roles(df: pd.DataFrame, roles: dict[str, Any]) -> pd.DataFrame:
    """``structure(df, col_roles = roles)``."""
    df = df.copy()
    df.attrs["col_roles"] = roles
    return df
