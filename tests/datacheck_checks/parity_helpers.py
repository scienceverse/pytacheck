"""Python sides of datacheck_checks parity cases whose R result is 1-based.

The block and header detectors return 0-based positions in Python (R's are
1-based); these wrappers add one so the results compare with R's goldens.
"""

from __future__ import annotations

from typing import Any

from pytacheck.datacheck import checks as C


def detect_header_row(rows: Any, max_scan: int = 4) -> dict[str, Any]:
    """``.detect_header_row()`` with R's 1-based ``header_row``."""
    det = C._detect_header_row(rows, max_scan=max_scan)
    return {**det, "header_row": det["header_row"] + 1}


def detect_scale_blocks(df: Any, min_items: int = C._SCALE_MIN_ITEMS) -> list[list[int]]:
    """``.detect_scale_blocks()`` with R's 1-based column positions."""
    return [[i + 1 for i in b] for b in C._detect_scale_blocks(df, min_items=min_items)]


def detect_accuracy_blocks(df: Any, min_items: int = C._TASK_ACC_MIN_ITEMS) -> list[list[int]]:
    """``.detect_accuracy_blocks()`` with R's 1-based column positions."""
    return [[i + 1 for i in b] for b in C._detect_accuracy_blocks(df, min_items=min_items)]
