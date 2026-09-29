"""Python sides of datacheck_checks parity cases whose R result is 1-based.

The block and header detectors return 0-based positions in Python (R's are
1-based); these wrappers add one so the results compare with R's goldens.
"""

from __future__ import annotations

from typing import Any

from metacheck.datacheck import checks as C


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


def _column_profile(name: str, x: Any, names: list[str]) -> dict[str, Any]:
    facets = C.data_col_facets(name, x)
    return {
        "facets": {k: facets[k] for k in list(facets)[:7]},
        "scale_values": C.data_check_scale_values(x),
        "outliers": C.data_check_outliers(x),
        "constant": C.data_check_constant(x),
        "empty": C.data_check_empty(x),
        "case_issues": C.data_check_case_issues(x),
        "whitespace": C.data_check_whitespace(x),
        "numeric_in_text": C.data_check_numeric_in_text(x),
        "colname": C.data_check_colname(name),
        "pii_values": C.data_check_pii_values(x),
        "pii_name": C.data_check_pii_name(name),
        "pii_geo": C.data_check_pii_geo(name, x, names),
        "pii_freetext": C.data_check_pii_freetext(x),
        "demographic": C.data_check_demographic(name, x),
        "design_name": C.data_check_design_name(name),
        "spss_filter": C.data_check_spss_filter(name, x),
        "likert": C._is_likert_item(x),
        "rt": C._looks_like_rt(x),
        "accuracy": C._looks_like_accuracy(x),
        "accuracy_item": C._is_accuracy_item(x),
    }


def profile_file(path: str) -> dict[str, Any] | None:
    """Every data_check helper on every column of a data file (``data_read_head()``).

    The Python side of the ``fixture_profile.*`` cases; the R side is the
    ``FIXTURE_PROFILE_R`` expression in ``gen_parity_cases.py``.
    """
    from metacheck.datacheck.files import data_read_head

    d = data_read_head(path, n_rows=float("inf"))
    if d is None:
        return None
    names = [str(c) for c in d.columns]
    cols = [d.iloc[:, j] for j in range(d.shape[1])]
    facets = [C.data_col_facets(n, x) for n, x in zip(names, cols, strict=True)]
    strip = C.data_strip_qualtrics_header(d)
    promo = C.data_promote_header_row(d)
    return {
        "names": names,
        "columns": [_column_profile(n, x, names) for n, x in zip(names, cols, strict=True)],
        "collisions": C.data_check_colname_collisions(names),
        "qualtrics": C.data_check_is_qualtrics(d),
        "qualtrics_tags": C._qualtrics_tag_cols(names),
        "display_order": C._qualtrics_is_display_order(names),
        "stems": [C._qualtrics_col_stem(n) for n in names],
        "strip_nrow": len(strip),
        "promoted": promo["promoted"],
        "promoted_names": [str(c) for c in promo["df"].columns],
        "behaverse": C.data_check_is_behaverse(d),
        "inquisit": C.data_check_is_inquisit(d),
        "jspsych": C.data_check_is_jspsych(d),
        "psychopy": C.data_check_is_psychopy(d),
        "task_columns": C._detect_task_columns(d),
        "scale_blocks": [[i + 1 for i in b] for b in C._detect_scale_blocks(d)],
        "accuracy_blocks": [[i + 1 for i in b] for b in C._detect_accuracy_blocks(d)],
        "task_data": C._is_task_data(d),
        "tabular_usable": C._tabular_usable(facets, d),
        "numeric_col_fraction": C._numeric_col_fraction(d),
    }


def identity(x: Any) -> Any:
    """``base::identity()``: the Python side of the ``datacheck_checks_review``
    battery cases, whose ``$expr`` argument does the work on each side."""
    return x
