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
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx
import respx

from tests.httpmock import UPSTREAM_TESTS, fixture_response, r_digest

ROOT = Path(__file__).resolve().parents[2]
_R_CHUNK = re.compile(r"\n```\{r\}.*?\n```\n", re.S)
_REDACT = re.compile(r"[?&]page(%5[Bb]size%5[Dd]|\[size\])=100")


def mock_path(request: httpx.Request) -> str:
    """``httptest2::build_mock_url()`` after metacheck's ``page[size]`` redactor.

    httptest2 splits the URL with ``strsplit(url, "?", fixed = TRUE)`` and
    hashes only the second piece, so a URL with two ``?`` (an OSF id carrying
    ``?view_only=`` inside ``.../guids/<id>/?resolve=false``) is hashed up to
    its second ``?``.
    """
    url = _REDACT.sub("", str(request.url))
    url = re.sub(r"^.*?://", "", url, count=1)
    parts = url.split("?")
    if len(parts) > 1 and parts[-1] == "":
        parts.pop()  # strsplit() drops a trailing empty piece
    path = re.sub(r"/$", "", parts[0]).replace(":", "-")
    if len(parts) > 1:
        path += "-" + r_digest(parts[1])[:6]
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
    from metacheck import utils

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


#: Synthetic OSF recordings for what metacheck's recordings do not cover
#: (withdrawn registrations, schema edge cases, prsp partial matching).
LOCAL_MOCKS = Path(__file__).resolve().parent / "mocks"


def run_prereg(
    papers: Sequence[Mapping[str, Any]] = (),
    demo: bool = False,
    read: Sequence[str] = (),
    paperlist: bool = False,
    mock: str = "apis",
    report: bool = False,
) -> Any:
    """``module_run(<paper>, "prereg_check")`` on recorded responses (a parity case).

    The paper is the demo paper, papers read from *read* (repository-relative
    paths), or test papers (``{"url": [...], "id": ..., "text": [...]}``);
    several papers, or *paperlist*, make a paper list. *mock* is ``"apis"``
    (metacheck's recordings) or ``"local"`` (:data:`LOCAL_MOCKS`). The DNS
    check of ``aspredicted_info()`` is skipped (the goldens were made online).
    With *report*, returns ``module_report()`` of the output with its R code
    chunks masked (they deparse tables, which the report area checks).
    """
    from unittest import mock as umock

    import metacheck as pc

    items: list[Any] = []
    if demo:
        items.append(pc.demopaper())
    if read:
        got = pc.read([ROOT / r for r in read])
        items.extend(got if isinstance(got, pc.PaperList) else [got])
    items.extend(tp(p.get("url") or [], p["id"], p.get("text")) for p in papers)
    paper = plist(*items) if paperlist or len(items) > 1 else items[0]
    dirs = (LOCAL_MOCKS,) if mock == "local" else (mock,)
    with mocked(*dirs), umock.patch("metacheck.utils.online", return_value=True):
        mo = pc.module_run(paper, "prereg_check")
    if not report:
        return mo
    from metacheck.report.report import module_report

    return _R_CHUNK.sub("\n<R-CHUNK>\n", module_report(mo))


def run_prereg_tables(**kwargs: Any) -> list[Any]:
    """The tables in the report of :func:`run_prereg` (a parity case).

    R deparses each ``scroll_table()`` into the report's code chunks, which the
    report comparison skips; the R side of these cases evaluates that code
    back into data frames, and this returns the same tables (in report order)
    so their cells are compared.
    """
    from metacheck.report.blocks import ReportTable

    mo = run_prereg(**kwargs)
    tables: list[Any] = []

    def walk(x: Any) -> None:
        if isinstance(x, ReportTable):
            tables.append(x.data.reset_index(drop=True))
        elif isinstance(x, list | tuple):
            for item in x:
                walk(item)

    walk(mo["report"])
    return tables


def tp(url: Sequence[str] | str, paper_id: str, text: Sequence[str] | None = None) -> Any:
    """``test_paper(text, url)`` with a fixed ``paper_id`` (R: ``p$paper_id <- id``)."""
    import metacheck as pc

    p = pc.test_paper(text, [url] if isinstance(url, str) else list(url))
    p.paper_id = paper_id
    return p


def plist(*papers: Any) -> Any:
    """``paperlist(...)``."""
    import metacheck as pc

    return pc.PaperList(list(papers))
