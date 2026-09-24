"""Python side of the ``mod_prereg`` parity cases (and shared by the tests).

The R side of those cases runs ``module_run(paper, "prereg_check")`` inside
``httptest2::with_mock_dir()`` on metacheck's recorded API responses, with
metacheck's test redactor (``page[size]=100`` stripped from OSF URLs, as the
recordings predate that parameter). :func:`run_mocked` sets up the same thing
for pytacheck: the same recordings and redaction, an unrecorded request fails
the way it does under httptest2 (a connection error, not a 404), no session
OSF listing cache, no token and no sleeping.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import respx

from tests.httpmock import UPSTREAM_TESTS, fixture_response, r_digest

_REDACT = re.compile(r"[?&]page(%5[Bb]size%5[Dd]|\[size\])=100")


def mock_path(request: httpx.Request) -> str:
    """``httptest2::build_mock_url()`` after metacheck's ``page[size]`` redactor."""
    url = _REDACT.sub("", str(request.url))
    url = re.sub(r"^.*?://", "", url, count=1)
    base, _, query = url.partition("?")
    path = re.sub(r"/$", "", base).replace(":", "-")
    if query:
        path += "-" + r_digest(query)[:6]
    if request.method != "GET":
        path += "-" + request.method
    return path


@contextlib.contextmanager
def replay(*mock_dirs: str | Path) -> Iterator[respx.MockRouter]:
    """Serve metacheck's recordings; unrecorded requests raise a connection error."""
    roots = [
        Path(d) if Path(d).is_absolute() else UPSTREAM_TESTS / d for d in mock_dirs or ("apis",)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = mock_path(request)
        for root in roots:
            resp = fixture_response(root, path)
            if resp is not None:
                return resp
        raise httpx.ConnectError(f"no recorded fixture for {path}", request=request)

    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=handler)
        yield router


@contextlib.contextmanager
def _env(**values: str) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def mocked(*mock_dirs: str | Path) -> Iterator[respx.MockRouter]:
    """metacheck's test setup around :func:`replay`."""
    from pytacheck import utils

    with (
        tempfile.TemporaryDirectory() as tmp,
        _env(OSF_PAT="", PYTACHECK_NO_SLEEP="1"),
        utils.local_options(
            {
                "metacheck.osf.cache": False,
                "metacheck.osf.pat": None,
                "metacheck.osf.delay": 0,
                "metacheck.cache.dir": tmp,
            }
        ),
        replay(*mock_dirs) as router,
    ):
        yield router


def run_mocked(x: Callable[[], Any]) -> Any:
    """Call *x* against metacheck's recorded API responses (see module docstring)."""
    with mocked():
        return x()


def tp(url: Sequence[str] | str, paper_id: str, text: Sequence[str] | None = None) -> Any:
    """``test_paper(text, url)`` with a fixed ``paper_id`` (R: ``p$paper_id <- id``)."""
    import pytacheck as pc

    p = pc.test_paper(text, [url] if isinstance(url, str) else list(url))
    p.paper_id = paper_id
    return p


def plist(*papers: Any) -> Any:
    """``paperlist(...)``."""
    import pytacheck as pc

    return pc.PaperList(list(papers))
