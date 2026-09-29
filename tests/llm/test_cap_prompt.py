"""Port of R/cap-prompt.R (cap_report(), .cap_size_str())."""

from __future__ import annotations

import pytest

from metacheck.llm import cap_report
from metacheck.llm.cap_prompt import _cap_size_str


def test_cap_report_prints_and_warns(capsys: pytest.CaptureFixture[str]) -> None:
    msg = "The `codebook_max_calls` cap of 30 skipped cb.csv; set codebook_max_calls >= 71."
    with pytest.warns(UserWarning, match="codebook_max_calls >= 71"):
        assert cap_report(msg) is None
    assert msg in capsys.readouterr().err


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (None, "unknown size"),
        (float("nan"), "unknown size"),
        (0, "0.0 B"),
        (-5, "-5.0 B"),
        (512, "512.0 B"),
        (1023, "1023.0 B"),
        (1024, "1.0 KB"),
        (1536, "1.5 KB"),
        (1048576, "1.0 MB"),
        (5.4e9, "5.0 GB"),
        (1e13, "9.1 TB"),
        (1e18, "909494.7 TB"),
    ],
)
def test_cap_size_str(n: float | None, expected: str) -> None:
    assert _cap_size_str(n) == expected
