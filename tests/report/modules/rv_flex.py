"""Twin of rv_flex.R (report review parity tests)."""

from __future__ import annotations

from typing import Any

from pytacheck.module import module


@module(
    title="Flexible  Module",
    description="Returns what it is given (report review tests).",
    details=(
        "First details. <validation>One.</validation> Middle text.\n\n"
        "<validation>\nTwo.\n</validation>\n\nEnd text."
    ),
    keywords=["intro"],
    author=["A One (\\email{a@b.c})", "B Two", "C Three", "D Four"],
)
def rv_flex(
    paper: Any, summary_text: Any = None, report: Any = None, traffic_light: str = "info"
) -> dict[str, Any]:
    return {"traffic_light": traffic_light, "summary_text": summary_text, "report": report}
