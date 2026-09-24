"""Database of commonly miscited papers (metacheck's ``inst/databases/miscite.Rds``).

metacheck reads it directly with
``readRDS(system.file("databases/miscite.Rds", package = "metacheck"))`` as
the default ``db`` of the ``ref_miscitation`` module; it is built by
``data-raw/miscite.R`` and is a proof of concept (three entries).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytacheck.db.databases import load_database

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["miscite"]


def miscite() -> pd.DataFrame:
    """The miscitation database: columns ``doi``, ``reftext`` and ``warning``."""
    return load_database("miscite")
