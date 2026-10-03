"""Generated search chains: the core and the façade against the frozen ``text_search()``.

Every chain of :mod:`tests.core.chains` runs on ``tests/_legacy/search.py`` (the
façade before the indexed-document core), on today's ``text_search()`` and as
one :class:`~metacheck.core.hits.Hits` chain; the three tables are equal
(columns, dtypes, values and order), or all three calls raise.

The default budget is fixed (seed and number of chains), so a failure is
reproducible. A nightly run raises it with ``METACHECK_CHAIN_EXAMPLES`` (and
picks a seed with ``METACHECK_CHAIN_SEED``).
"""

from __future__ import annotations

import os

from tests.core.chains import differences

DEFAULT_CHAINS = 600
CHAINS = int(os.environ.get("METACHECK_CHAIN_EXAMPLES") or DEFAULT_CHAINS)
SEED = int(os.environ.get("METACHECK_CHAIN_SEED") or 1)


def test_generated_chains_match_the_frozen_text_search() -> None:
    fails = differences(CHAINS, SEED)
    assert not fails, f"{len(fails)} of {CHAINS} chains differ (seed {SEED}); first: {fails[:3]}"
