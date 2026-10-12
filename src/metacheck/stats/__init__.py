"""Statistics checks: ``stats()`` (metacheck's statcheck wrapper) and helpers.

* :func:`stats` -- port of ``R/stats.R``;
* :mod:`metacheck.stats.statcheck` -- port of the statcheck R package (1.5.0);
* :mod:`metacheck.stats.helpers` -- port of ``R/stat_helpers.R``.
"""

from __future__ import annotations

from metacheck.stats.core import stats
from metacheck.stats.statcheck import statcheck

__all__ = ["statcheck", "stats"]

from metacheck._callable import callable_module

callable_module(__name__, "stats")  # pc.stats(paper) even once this package is imported
