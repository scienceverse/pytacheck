"""Python side of the ``mod_reg`` parity cases (and shared by the tests).

The R side of each case runs ``module_run(<paper>, "reg_check")`` (standalone,
or chained after ``prereg_check``) inside ``httptest2::with_mock_dir()`` on
metacheck's recorded OSF/AsPredicted responses (with metacheck's test
redactor), and with the RegCheck server replaced by a fake via
``testthat::with_mocked_bindings()``. :func:`run_reg` does the same for
pytacheck with ``unittest.mock.patch``; the fakes here are line-by-line
twins of the R fakes written by ``make_cases.py``:

``table``    ``regcheck_compare()`` returns the testthat mock table
``odd``      ... a table with odd judgements, ``NA`` cells and quote refs
``empty``    ... a zero-row table (every comparison "succeeds" with nothing)
``error``    ``regcheck_compare()`` fails ("RegCheck server unreachable")
``partial``  the first comparison succeeds, the others fail
``echo``     the real ``regcheck_compare()`` runs; only the server calls
             (``.regcheck_submit()`` / ``.regcheck_poll()``) are faked, and
             the fake result echoes what was sent (paper and prereg text,
             client, URL, token, dimensions)
``connfail`` the RegCheck server cannot be reached (``req_perform()`` fails)
``unauthorized`` the RegCheck server answers 401
``real``     nothing is faked (for errors raised before any request)
``nodim`` / ``noinfo`` / ``noquotes``  the testthat table without its
             ``dimension`` / ``deviation_information`` / ``paper_quotes`` column
``fulltext`` like ``echo``, but the result holds the complete paper and
             preregistration texts that were sent
``http:<name>`` the real RegCheck client runs against faked HTTP responses
             (:data:`HTTP_SCENARIOS`; R: ``httr2::with_mocked_responses()``)
``refused``  the real client connects to a closed local port (no mocks)
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest import mock as umock

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
# a table: an R chunk in R, a raw HTML block in pytacheck (D75)
_R_CHUNK = re.compile(r'\n```(?:\{r\}|\{=html\}\n<div class="datatables).*?\n```\n', re.S)

#: the table ``regcheck_compare()`` returns in metacheck's testthat tests
MOCK_TABLE: dict[str, list[Any]] = {
    "dimension": ["Sample size", "Hypotheses", "Exclusion criteria"],
    "deviation_judgement": ["yes", "no", "missing"],
    "paper_summary": ["120 participants", "H1: A > B", "not described"],
    "prereg_summary": ["100 participants", "H1: A > B", "exclude RT < 200 ms"],
    "deviation_information": [
        "Paper sample (120) exceeds preregistered sample (100).",
        "Consistent.",
        "Cannot be assessed from the paper.",
    ],
    "paper_quotes": ["[PAPER_0001] quote"] * 3,
    "prereg_quotes": ["[REG_0001] quote"] * 3,
}

#: odd judgements (case, spaces, unknown labels, NA) and bracketed references
ODD_TABLE: dict[str, list[Any]] = {
    "dimension": ["Sample size", "Hypotheses", "Exclusion criteria", "Other"],
    "deviation_judgement": [" YES ", None, "Partially", "missing"],
    "paper_summary": ["120 [PAPER_0001]", None, "x [PAPER_0001, REG_0002]", "y\nz"],
    "prereg_summary": ["p"] * 4,
    "deviation_information": ["a [REG_1]", "b", "c [lower]", None],
    "paper_quotes": [None] * 4,
    "prereg_quotes": ["[REG_0001] quote"] * 4,
}

TIDY_COLUMNS = list(MOCK_TABLE)

#: RegCheck fakes that return the testthat table without some columns
DROP_COLUMNS: dict[str, str] = {
    "nodim": "dimension",
    "noinfo": "deviation_information",
    "noquotes": "paper_quotes",
}

_TASK = '{"task_id": "t-1", "status_url": "/api/v1/comparisons/t-1"}'
_RUNNING = '{"state": "running", "processed_dimensions": 1, "total_dimensions": 6}'

#: a RegCheck result with R-vs-Python case-mapping traps, JSON nulls, missing
#: fields, non-breaking spaces and quote references (ASCII JSON, \u escapes)
SUCCESS_ITEMS: list[dict[str, Any]] = [
    {
        "dimension": "Sample size",
        "deviation_judgement": "M\u0130SS\u0130NG",
        "paper_content_summary": "N = 120 [PAPER_0001]",
        "registration_content_summary": None,
        "deviation_information": "The paper reports 120.\u00a0[REG_0001]",
        "paper_content_quotes": '"120 participants" [PAPER_0001]',
        "registration_content_quotes": "",
    },
    {
        "dimension": "Hypotheses",
        "deviation_judgement": "\tNO\n",
        "deviation_information": "Same [REG_0002, PAPER_0003]",
    },
    {"dimension": None, "deviation_judgement": "", "paper_content_summary": "x"},
    {
        "dimension": "\u03a3 test",
        "deviation_judgement": "\u0391\u03a3",
        "paper_content_summary": "\u03b1 = .05\n[PAPER_0004]",
        "registration_content_summary": "\u2265 2",
        "deviation_information": "Yes\u00a0",
    },
    {
        "dimension": "Analysis",
        "deviation_judgement": "Yes\u00a0",
        "deviation_information": "a judgement ending in a non-breaking space",
    },
    {"dimension": "Outliers", "deviation_judgement": " yes ", "deviation_information": "ok"},
]


def _json(x: Any) -> str:
    import json

    return json.dumps(x)


#: faked RegCheck HTTP exchanges: endpoint kind -> responses (status, JSON body
#: or None) in call order, the last one repeating; unlisted kinds answer 404.
#: Kinds: ``text`` (POST .../comparisons/text), ``multipart`` (POST
#: .../comparisons), ``poll`` (GET .../comparisons/<task>).
HTTP_SCENARIOS: dict[str, dict[str, list[tuple[int, str | None]]]] = {
    "http404": {"text": [(404, None)], "multipart": [(404, None)]},
    "http500": {"text": [(500, None)]},
    "http422": {"text": [(422, None)]},
    "http401": {"text": [(401, None)]},
    "pollfail": {
        "text": [(200, _TASK)],
        "poll": [(200, '{"state": "failure", "status": "worker crashed"}')],
    },
    "pollfail_nostatus": {"text": [(200, _TASK)], "poll": [(200, '{"state": "failure"}')]},
    "poll503": {"text": [(200, _TASK)], "poll": [(503, None)]},
    "success_null": {
        "text": [(200, _TASK)],
        "poll": [(200, '{"state": "success", "result": null}')],
    },
    "success": {
        "text": [(200, _TASK)],
        "poll": [
            (200, _RUNNING),
            (200, _json({"state": "success", "result": {"items": SUCCESS_ITEMS}})),
        ],
    },
    "fallback": {
        "text": [(405, None)],
        "multipart": [(200, _TASK)],
        "poll": [(200, _json({"state": "success", "result": {"items": SUCCESS_ITEMS[:2]}}))],
    },
}


def http_kind(url: str) -> str:
    """Which RegCheck endpoint *url* is (see :data:`HTTP_SCENARIOS`)."""
    if url.endswith("/api/v1/comparisons/text"):
        return "text"
    if url.endswith("/api/v1/comparisons"):
        return "multipart"
    return "poll"


def frame(data: Mapping[str, Sequence[Any]]) -> pd.DataFrame:
    """A character data frame like the R fakes' ``data.frame()``."""
    return pd.DataFrame({k: pd.Series(list(v), dtype="string") for k, v in data.items()})


def empty_frame() -> pd.DataFrame:
    """``regcheck_tidy(list(items = list()))``: the tidy columns, no rows."""
    return frame({c: [] for c in TIDY_COLUMNS})


class _Echo:
    """Fake ``.regcheck_submit()`` / ``.regcheck_poll()`` echoing their input."""

    def __init__(self, full: bool = False) -> None:
        self.n = 0
        self.full = full
        self.sent: dict[str, Any] = {}

    def submit(
        self,
        url: str,
        api_token: str,
        paper_text: str,
        prereg_text: str | None = None,
        registration_id: str | None = None,
        client: str = "ollama",
        dimensions: pd.DataFrame | None = None,
        reasoning_effort: str = "medium",
    ) -> dict[str, Any]:
        self.n += 1
        dims = "default" if dimensions is None else "|".join(dimensions["dimension"].tolist())
        self.sent = {
            "paper_text": paper_text,
            "prereg_text": prereg_text,
            "client": client,
            "dims": dims,
        }
        return {"task_id": f"task-{self.n}"}

    def poll(
        self,
        url: str,
        api_token: str,
        task_id: str,
        poll_interval: float = 5,
        timeout: float = 3600,
    ) -> dict[str, Any]:
        s = self.sent
        if self.full:
            return {
                "items": [
                    {
                        "dimension": "Full text",
                        "deviation_judgement": "no",
                        "paper_content_summary": s["paper_text"],
                        "registration_content_summary": s["prereg_text"],
                    }
                ]
            }
        info = (
            f"client: {s['client']} url: {url} task: {task_id} token: {api_token} "
            f"dims: {s['dims']} [PAPER_0001, REG_0002]"
        )
        return {
            "items": [
                {
                    "dimension": "Paper text",
                    "deviation_judgement": " Yes ",
                    "paper_content_summary": s["paper_text"][:300],
                    "registration_content_summary": str(len(s["paper_text"])),
                    "deviation_information": info,
                    "paper_content_quotes": "[PAPER_0001] first; [PAPER_0002] second",
                    "registration_content_quotes": "[REG_0001] q",
                },
                {
                    "dimension": "Preregistration text",
                    "deviation_judgement": "Partially",
                    "paper_content_summary": "p [PAPER_0003]",
                    "registration_content_summary": s["prereg_text"],
                    "deviation_information": "Details [REG_0001; REG_0002] and [not a ref].",
                },
                {
                    "dimension": "Exclusions",
                    "deviation_judgement": "MISSING",
                    "paper_content_summary": "none",
                    "registration_content_summary": "none",
                },
            ]
        }


@contextlib.contextmanager
def env(**values: str) -> Iterator[None]:
    """Set environment variables for the block (``withr::local_envvar()``)."""
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
def fake_regcheck(fake: str = "table") -> Iterator[list[int]]:
    """Replace the RegCheck server as the R cases do; yields a call counter."""
    calls = [0]

    def returning(data: Mapping[str, Sequence[Any]] | None) -> Callable[..., pd.DataFrame]:
        def fn(*args: Any, **kwargs: Any) -> pd.DataFrame:
            calls[0] += 1
            return empty_frame() if data is None else frame(data)

        return fn

    def failing(*args: Any, **kwargs: Any) -> pd.DataFrame:
        calls[0] += 1
        raise RuntimeError("RegCheck server unreachable")

    def partial(*args: Any, **kwargs: Any) -> pd.DataFrame:
        calls[0] += 1
        if calls[0] == 1:
            return frame(MOCK_TABLE)
        raise RuntimeError("second prereg failed")

    target = "metacheck.db.regcheck.regcheck_compare"
    if fake in ("real", "refused"):
        yield calls
    elif fake.startswith("http:"):
        import httpx

        scenario = HTTP_SCENARIOS[fake.removeprefix("http:")]
        counts: dict[str, int] = {}

        def respond(method: str, url: str, **kwargs: Any) -> httpx.Response:
            calls[0] += 1
            kind = http_kind(url)
            counts[kind] = counts.get(kind, 0) + 1
            responses = scenario.get(kind) or [(404, None)]
            status, body = responses[min(counts[kind], len(responses)) - 1]
            request = httpx.Request(method, url)
            if body is None:
                return httpx.Response(status, request=request)
            return httpx.Response(
                status,
                content=body.encode(),
                headers={"Content-Type": "application/json"},
                request=request,
            )

        with umock.patch("metacheck.http.request", respond):
            yield calls
    elif fake in ("connfail", "unauthorized"):
        # R: httr2::req_perform() raising httr2_failure / httr2_http_401;
        # metacheck.http.request() returns None / the 401 response instead
        import httpx

        def request(method: str, url: str, **kwargs: Any) -> httpx.Response | None:
            calls[0] += 1
            if fake == "connfail":
                return None
            return httpx.Response(401, request=httpx.Request(method, url))

        with umock.patch("metacheck.http.request", request):
            yield calls
    elif fake in ("echo", "fulltext"):
        echo = _Echo(full=fake == "fulltext")
        with (
            umock.patch("metacheck.db.regcheck._regcheck_submit", echo.submit),
            umock.patch("metacheck.db.regcheck._regcheck_poll", echo.poll),
        ):
            yield calls
    else:
        fn = {
            "table": returning(MOCK_TABLE),
            "odd": returning(ODD_TABLE),
            "empty": returning(None),
            "error": failing,
            "partial": partial,
            **{
                name: returning({k: v for k, v in MOCK_TABLE.items() if k != col})
                for name, col in DROP_COLUMNS.items()
            },
        }[fake]
        with umock.patch(target, fn):
            yield calls


def dimensions_frame(dims: Sequence[str]) -> pd.DataFrame:
    """The ``dimensions`` argument of the cases."""
    return pd.DataFrame(
        {
            "dimension": pd.Series(list(dims), dtype="string"),
            "definition": pd.Series([f"Definition of {d}" for d in dims], dtype="string"),
        }
    )


def make_paper(
    papers: Sequence[Mapping[str, Any]] = (),
    demo: bool = False,
    read: Sequence[str] = (),
    paperlist: bool = False,
) -> Any:
    """The demo paper, papers read from *read* and/or test papers (a list if several)."""
    import metacheck as pc
    from tests.mod_prereg.parity_support import plist, tp

    items: list[Any] = []
    if demo:
        items.append(pc.demopaper())
    if read:
        got = pc.read([ROOT / r for r in read])
        items.extend(got if isinstance(got, pc.PaperList) else [got])
    items.extend(tp(p.get("url") or [], p["id"], p.get("text")) for p in papers)
    return plist(*items) if paperlist or len(items) > 1 else items[0]


def run_reg(
    papers: Sequence[Mapping[str, Any]] = (),
    demo: bool = False,
    read: Sequence[str] = (),
    paperlist: bool = False,
    pre: Sequence[str] = (),
    double: bool = False,
    na_paper_id: bool = False,
    fake: str = "table",
    args: Mapping[str, Any] | None = None,
    dims: Sequence[str] | None = None,
    envvars: Mapping[str, str] | None = None,
    tables: bool = False,
    report: bool = False,
    na_id: Sequence[int] = (),
    empty_table: bool = False,
    catch: bool = False,
    set_paper_id: str | None = None,
) -> Any:
    """``module_run(<paper>, "reg_check")`` on recorded responses (a parity case).

    *pre* are modules run first (chained: ``"prereg_check"`` makes
    ``reg_check`` reuse its table); *double* then repeats the prereg_check
    table (a paper linking the same preregistration twice), *na_paper_id*
    blanks its ``paper_id`` column (*set_paper_id* sets it), *na_id* blanks the ``id`` of those
    (0-based) rows and *empty_table* drops all its rows. *fake* picks the
    RegCheck fake (see the module docstring); *args* are extra module
    arguments and *dims* the ``dimension`` column of a ``dimensions``
    argument. *envvars* are set around the run (``REGCHECK_API_TOKEN`` and
    ``REGCHECK_BASE_URL`` default to empty). With *tables*, returns the
    report's tables; with *report*, ``module_report()`` with its R code chunks
    masked; with *catch*, the message of an error instead of raising it.
    """
    import metacheck as pc
    from tests.mod_prereg.parity_support import mocked

    paper = make_paper(papers, demo, read, paperlist)
    kwargs = dict(args or {})
    if dims is not None:
        kwargs["dimensions"] = dimensions_frame(dims)
    variables = {"REGCHECK_API_TOKEN": "", "REGCHECK_BASE_URL": "", **(envvars or {})}

    def run(x: Any) -> Any:
        try:
            return pc.module_run(x, "reg_check", **kwargs)
        except Exception as e:
            if catch:
                return str(e)
            raise

    with (
        umock.patch("metacheck.utils.online", return_value=True),
        env(**variables),
    ):
        with mocked():
            x: Any = paper
            for m in pre:
                x = pc.module_run(x, m)
            if double:
                x.table = pd.concat([x.table, x.table], ignore_index=True)
            if na_paper_id:
                x.table = x.table.assign(paper_id=pd.Series([pd.NA] * len(x.table), dtype="string"))
            if set_paper_id is not None:
                x.table = x.table.assign(
                    paper_id=pd.Series([set_paper_id] * len(x.table), dtype="string")
                )
            if na_id:
                table = x.table.copy()
                table.loc[table.index[list(na_id)], "id"] = pd.NA
                x.table = table
            if empty_table:
                x.table = x.table.iloc[0:0]
            if fake != "refused":
                with fake_regcheck(fake):
                    mo = run(x)
        if fake == "refused":
            # a real connection attempt, outside the recorded responses
            mo = run(x)
    if isinstance(mo, str):
        return mo
    if tables:
        return report_tables(mo)
    if report:
        from metacheck.report.report import module_report

        return _R_CHUNK.sub("\n<R-CHUNK>\n", module_report(mo))
    return mo


def identity(x: Any) -> Any:
    """R ``identity()`` (the Python side of ``$expr`` cases)."""
    return x


def report_tables(mo: Any) -> list[pd.DataFrame]:
    """The tables in a module output's report, in report order."""
    from metacheck.report.blocks import ReportTable

    out: list[pd.DataFrame] = []

    def walk(x: Any) -> None:
        if isinstance(x, ReportTable):
            out.append(x.data.reset_index(drop=True))
        elif isinstance(x, list | tuple):
            for item in x:
                walk(item)

    walk(mo["report"])
    return out
