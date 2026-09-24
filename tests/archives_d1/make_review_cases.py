"""Generate parity/cases/archives_d1_review.yaml: the adversarial-review cases.

Run from the repository root: ``python -m tests.archives_d1.make_review_cases``,
then ``python -m parity generate --area archives_d1_review``. Mocked HTTP comes
from ``tests/archives_d1/mocks`` (``make_mocks.py`` plus ``make_review_mocks.py``).

These target branches the first round of cases did not reach: a sample of
generated link shapes (the same generator agreed with R on ~7300 URLs and
their links), text-only and URL-only papers, ``bind_rows()`` column order when
the first lookup fails, column-name clashes in the joins, numeric ids, empty or
atomic JSON fields, files with no name and no id, empty names, missing sizes,
duplicate names, the total-size cap with ties and missing sizes, duplicated
Dataverse host/DOI pairs, and R's errors for malformed records and tables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.archives_d1.make_parity_cases import Dumper, py_list, r_chr, rq, tmp_py, tmp_r

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "archives_d1_review.yaml"
MOCK = "tests/archives_d1/mocks"
RUN = "tests.archives_d1.parity_support.run"
IGNORE_PID = {"ignore": ["paper_id"]}
cases: list[dict[str, Any]] = []

PLACEHOLDER_TEXT: list[str] = []
PLACEHOLDER_URL: list[str] = []


def fn_case(id_: str, r: str, py: str, args: dict[str, Any], compare: Any = None) -> None:
    c: dict[str, Any] = {"id": id_, "r": r, "py": py, "args": args}
    if compare:
        c["compare"] = compare
    cases.append(c)


def expr_case(id_: str, r: str, py: str, mock: bool = True, compare: Any = None) -> None:
    c: dict[str, Any] = {
        "id": id_,
        "r": "identity",
        "py": RUN,
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }
    if mock:
        c["mock_dir"] = MOCK
    if compare:
        c["compare"] = compare
    cases.append(c)


def online(r_call: str) -> str:
    return (
        f"testthat::with_mocked_bindings({r_call}, online = function(...) TRUE, "
        '.package = "metacheck")'
    )


def dl_case(id_: str, r_body: str, py_body: str) -> None:
    expr_case(id_, tmp_r(r_body), tmp_py(py_body))


