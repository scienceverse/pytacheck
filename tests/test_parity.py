"""Every parity case must match its R golden (see parity/ and docs/PARITY.md)."""

from __future__ import annotations

import pytest

from parity.__main__ import check_case
from parity.cases import load_cases
from parity.compare import summarize

CASES = load_cases()


@pytest.mark.parity
@pytest.mark.parametrize("case", CASES, ids=[c.key for c in CASES])
def test_parity(case) -> None:
    status, problems, _ = check_case(case)
    if status == "missing":
        pytest.fail(f"no golden for {case.key}: run `python -m parity generate --area {case.area}`")
    if status == "xfail":
        pytest.xfail(case.spec["known_divergence"])
    assert status == "pass", f"{case.key} differs from R:\n{summarize(problems)}"
