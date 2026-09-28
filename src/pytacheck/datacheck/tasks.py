"""The behavioural and cognitive task dictionary (port of ``R/tasks.R`` / ``data/tasks.rda``).

metacheck ships ``tasks``, a dictionary of behavioural and cognitive tasks
(Stroop, Implicit Association Task, n-back, Raven's Progressive Matrices)
harvested from the Cognitive Atlas task ontology -- the task counterpart of
:func:`pytacheck.datacheck.scales.scales`. Entries that OpenScales already
defines are dropped.

The data are converted from ``data/tasks.rda`` by
``scripts/convert_datacheck_data.R`` into
``pytacheck/resources/data/tasks.json.gz``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytacheck.datacheck.scales import _bundled

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["tasks"]


def tasks() -> pd.DataFrame:
    """Behavioural and Cognitive Task Dictionary (metacheck's ``tasks`` data set).

    Port of ``R/tasks.R`` (``data/tasks.rda``): one row per task with columns
    ``code`` (OSD slug), ``name``, ``acronym``, ``atlas_id`` (Cognitive Atlas
    id), ``description``, ``citation``, ``pmid``, ``url``, ``n_conditions``,
    ``n_contrasts``, ``n_computable`` (integers), ``indicators``
    (comma-separated normalised indicators) and ``text_ok`` (logical).
    Returns a fresh copy.
    """
    return _bundled("tasks").copy()
