"""Run pytacheck.db network functions on metacheck's recorded API responses.

The Python half of ``tests/db/replay.R``: parity cases in
``parity/cases/db.yaml`` call e.g. ::

    call("pytacheck.db.crossref.crossref_doi", ["10.1177/fake"])

which runs the function with requests served from
``upstream/metacheck/tests/testthat/apis`` (see :mod:`tests.httpmock`),
``online()`` mocked to ``True`` and the contact email set to metacheck's
test address, exactly as the R side does.
"""

from __future__ import annotations

import contextlib
import importlib
import os
from collections.abc import Iterator
from typing import Any


@contextlib.contextmanager
def replaying(mock_dir: str = "apis") -> Iterator[None]:
    """Serve HTTP from recorded fixtures, with ``online()`` true and the test email."""
    from pytacheck import config
    from pytacheck.db import _utils
    from tests.httpmock import replay

    old_online = _utils.online
    old_email = config._state.get("email")
    old_sleep = os.environ.get("PYTACHECK_NO_SLEEP")
    _utils.online = lambda *_a, **_k: True  # type: ignore[assignment]
    config._state["email"] = "metacheck@scienceverse.org"
    os.environ["PYTACHECK_NO_SLEEP"] = "1"
    try:
        with replay(mock_dir):
            yield
    finally:
        _utils.online = old_online  # type: ignore[assignment]
        if old_email is None:
            config._state.pop("email", None)
        else:
            config._state["email"] = old_email
        if old_sleep is None:
            os.environ.pop("PYTACHECK_NO_SLEEP", None)
        else:
            os.environ["PYTACHECK_NO_SLEEP"] = old_sleep


def call(fn: str, *args: Any, mock_dir: str = "apis", **kwargs: Any) -> Any:
    """Call the function at dotted path *fn* while :func:`replaying`."""
    module, _, name = fn.rpartition(".")
    func = getattr(importlib.import_module(module), name)
    with replaying(mock_dir):
        return func(*args, **kwargs)


def test_paper_with_bib(
    bib: dict[str, Any], text: str = "x", paper_id: str | None = None
) -> Any:
    """``p <- test_paper(text); p$bib <- data.frame(bib)`` (optionally with an ID)."""
    import pandas as pd

    import pytacheck as pc

    p = pc.test_paper(text)
    p.bib = pd.DataFrame(bib)
    if paper_id is not None:
        p.paper_id = paper_id
    return p


def paperlist(*papers: Any) -> Any:
    """``paperlist(...)``."""
    import pytacheck as pc

    return pc.PaperList(papers)
