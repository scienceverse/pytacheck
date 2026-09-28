"""Fixtures for the GitHub / GitLab / Zenodo archive tests.

As in metacheck's tests, ``online()`` is mocked to ``TRUE`` (no DNS lookups),
no GitHub credentials are looked up, and no Zenodo/GitLab tokens leak in from
the environment. Every HTTP request is served by recorded responses
(:func:`tests.httpmock.replay`); an unrecorded one gets a 404, never the
network.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MOCKS = HERE / "mocks"
MOCKS_FAIL = HERE / "mocks_fail"
UPLOAD = HERE / "fixtures" / "upload"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from pytacheck import utils
    from pytacheck.archives import github

    monkeypatch.setattr("pytacheck.utils.online", lambda *a, **k: True)
    monkeypatch.setitem(github._TOKEN_CACHE, "token", None)
    for var in ("ZENODO_PAT", "ZENODO_SANDBOX_PAT", "GITLAB_PAT", "OSF_PAT"):
        monkeypatch.delenv(var, raising=False)
    with utils.local_options(
        {
            "metacheck.zenodo.pat": None,
            "metacheck.zenodo.pat.sandbox": None,
            "metacheck.gitlab.pat": None,
            "metacheck.osf.pat": None,
        }
    ):
        yield


@pytest.fixture
def apis() -> Iterator[object]:
    """metacheck's recorded API responses."""
    from tests.httpmock import replay

    with replay("apis") as router:
        yield router


@pytest.fixture
def mocks() -> Iterator[object]:
    """This work item's hand-written responses (see ``make_mocks.py``)."""
    from tests.httpmock import replay

    with replay(MOCKS) as router:
        yield router


@pytest.fixture
def upload_dir() -> Path:
    return UPLOAD
