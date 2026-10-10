"""Python side of the ``mod_repo_check`` parity cases (and shared by the tests).

The R side of those cases runs ``module_run(<paper>, "repo_check", ...)`` inside
httptest2 on recorded API responses -- metacheck's own recordings
(``upstream/.../testthat/apis``, with metacheck's test redactor that strips
``page[size]=100`` from OSF URLs) and synthetic ones for the platforms
metacheck records nothing for (``tests/mod_repo_check/mocks``, written by
``make_mocks.py``) -- with ``online()`` mocked to ``TRUE``. :func:`mocked`
sets up the same thing for pytacheck: the same recordings and redaction; an
unrecorded request fails the way it does under httptest2 (an error that is
not an HTTP failure, see :class:`UnexpectedRequest`); no OSF session cache or
token, no sleeping, every host online.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest import mock as umock

import httpx
import respx

from tests.httpmock import UPSTREAM_TESTS, fixture_response, r_digest

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
MOCKS = HERE / "mocks"
FIXTURES = HERE / "fixtures"
_REDACT = re.compile(r"[?&]page(%5[Bb]size%5[Dd]|\[size\])=100")
_FILE_REF = re.compile(r'find_mock_file\("([^"]+)"\)')
# a table: an R chunk in R, a raw HTML block in pytacheck (D75)
_R_CHUNK = re.compile(r'\n```(?:\{r\}|\{=html\}\n<div class="datatables).*?\n```\n', re.S)


def mock_path(request: httpx.Request) -> str:
    """``httptest2::build_mock_url()`` after metacheck's ``page[size]`` redactor."""
    url = _REDACT.sub("", str(request.url))
    url = re.sub(r"^.*?://", "", url, count=1)
    parts = url.split("?")
    if len(parts) > 1 and parts[-1] == "":
        parts.pop()  # strsplit() drops a trailing empty piece
    path = re.sub(r"/$", "", parts[0]).replace(":", "-")
    if len(parts) > 1:
        path += "-" + r_digest(parts[1])[:6]
    body = request.content
    if body:
        path += "-" + r_digest(body.decode("utf-8", "replace"), native=True)[:6]
    if request.method != "GET":
        path += "-" + request.method
    return path


def _response(root: Path, path: str) -> httpx.Response | None:
    resp = fixture_response(root, path)
    if resp is None:
        return None
    r_file = root / f"{path}.R"
    if r_file.exists():
        ref = _FILE_REF.search(r_file.read_text(encoding="utf-8"))
        if ref and (root / ref.group(1)).exists():
            return httpx.Response(
                resp.status_code, headers=resp.headers, content=(root / ref.group(1)).read_bytes()
            )
    return resp


class UnexpectedRequest(RuntimeError):
    """httptest2's error for an unrecorded request (class ``httptest2_request``).

    It is not an HTTP failure: like in R, it propagates through the HTTP
    layer (``.batch_query()`` / :func:`metacheck.http.request` only absorb
    connection failures) and is caught only where metacheck catches any
    error.
    """

    def __init__(self, request: httpx.Request, path: str) -> None:
        body = request.content.decode("utf-8", "replace")
        shown = f"{request.method} {request.url}" + (f" {body}" if body else "")
        super().__init__(f"An unexpected request was made:\n{shown}\nExpected mock file: {path}.*")


@contextlib.contextmanager
def replay(*mock_dirs: str | Path) -> Iterator[respx.MockRouter]:
    """Serve recorded responses; an unrecorded request raises :class:`UnexpectedRequest`."""
    roots = [
        Path(d) if Path(d).is_absolute() else UPSTREAM_TESTS / d for d in mock_dirs or ("apis",)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = mock_path(request)
        for root in roots:
            resp = _response(root, path)
            if resp is not None:
                return resp
        raise UnexpectedRequest(request, path)

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
    """metacheck's test setup around :func:`replay` (see the module docstring)."""
    from metacheck import utils
    from metacheck.archives import osf

    dirs = mock_dirs or (MOCKS, "apis")
    with (
        tempfile.TemporaryDirectory() as tmp,
        _env(OSF_PAT="", PYTACHECK_NO_SLEEP="1", GITHUB_PAT="", GITHUB_TOKEN="", GITLAB_PAT=""),
        utils.local_options(
            {
                "metacheck.osf.cache": False,
                "metacheck.osf.pat": None,
                "metacheck.osf.delay": 0,
                "metacheck.cache.dir": tmp,
            }
        ),
        umock.patch("metacheck.utils.online", return_value=True),
        replay(*dirs) as router,
    ):
        osf.osf_cache_clear()
        yield router


@contextlib.contextmanager
def in_root() -> Iterator[None]:
    """Run with the repository root as working directory (relative local paths)."""
    old = os.getcwd()
    os.chdir(ROOT)
    try:
        yield
    finally:
        os.chdir(old)


def tp(url: Sequence[str] | str, paper_id: str, text: Sequence[str] | None = None) -> Any:
    """``test_paper(text, url)`` with a fixed ``paper_id`` (R: ``p$paper_id <- id``)."""
    import metacheck as pc

    p = pc.test_paper(text, [url] if isinstance(url, str) else list(url))
    p.paper_id = paper_id
    return p


def make_paper(papers: Sequence[Mapping[str, Any]], paperlist: bool = False) -> Any:
    """Test papers (``{"url": [...], "id": ..., "text": [...]}``), a list when several."""
    import metacheck as pc

    items = [tp(p.get("url") or [], p["id"], p.get("text")) for p in papers]
    if paperlist or len(items) > 1:
        return pc.PaperList(items)
    return items[0]


def run_repo(
    papers: Sequence[Mapping[str, Any]] = (),
    read: Sequence[str] = (),
    paperlist: bool = False,
    args: Mapping[str, Any] | None = None,
    mocks: Sequence[str] | None = None,
    report: bool = False,
    tables: bool = False,
) -> Any:
    """``module_run(<paper>, "repo_check", ...)`` on recorded responses (a parity case).

    The paper is made of test papers (*papers*) or read from *read*
    (repository-relative paths). *args* are passed to the module; a
    ``local_path`` is relative to the repository root. *mocks* are the mock
    directories searched (default: this area's synthetic mocks, then
    metacheck's ``apis``). With *tables*, returns the data of the report's
    tables (see :func:`report_tables`).
    """
    import metacheck as pc

    if read:
        got = pc.read([ROOT / r for r in read])
        items = list(got) if isinstance(got, pc.PaperList) else [got]
        items.extend(make_paper(papers, paperlist=True) if papers else [])
        paper = pc.PaperList(items) if (paperlist or len(items) > 1) else items[0]
    else:
        paper = make_paper(papers, paperlist=paperlist)
    dirs = [
        MOCKS if m == "local" else (ROOT / m if m.startswith("tests/") else m)
        for m in (mocks or ["local", "apis"])
    ]
    with in_root(), mocked(*dirs):
        mo = pc.module_run(paper, "repo_check", **dict(args or {}))
    if tables:
        return report_tables(mo)
    if report:
        from metacheck.report.report import module_report

        return _R_CHUNK.sub("\n<R-CHUNK>\n", module_report(mo))
    return mo


def report_tables(mo: Any) -> list[Any]:
    """The data of the tables in a module output's report, in report order."""
    from metacheck.report.blocks import ReportTable

    tables: list[Any] = []

    def walk(x: Any) -> None:
        if isinstance(x, ReportTable):
            tables.append(x.data.reset_index(drop=True))
        elif isinstance(x, list | tuple):
            for item in x:
                walk(item)

    walk(mo["report"])
    return tables


def identity(x: Any) -> Any:
    """The Python side of an ``identity`` case whose value is built by ``$expr``."""
    return x
