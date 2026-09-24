"""Statistics checks: ``stats()`` (metacheck's statcheck wrapper) and helpers.

* :func:`stats` -- port of ``R/stats.R``;
* :mod:`pytacheck.stats.statcheck` -- port of the statcheck R package (1.5.0);
* :mod:`pytacheck.stats.helpers` -- port of ``R/stat_helpers.R``.
"""

from __future__ import annotations

from pytacheck.stats.core import stats
from pytacheck.stats.statcheck import statcheck

__all__ = ["statcheck", "stats"]

from pytacheck._callable import callable_module

callable_module(__name__, "stats")  # pc.stats(paper) even once this package is imported
