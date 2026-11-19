"""data_check synchronised with metacheck 0.4.4.8 to 0.4.4.18 (#648, #673).

Mirrors the module-level behaviour of tests/testthat/test-data-checks.R; the parity cases
(``parity/cases/mod_data_check.yaml``) compare the complete outputs with R.
"""

from __future__ import annotations

import warnings

import pandas as pd

from . import helpers as dc


def test_items_the_model_gave_no_usable_answer_for_are_reported_and_counted() -> None:
    # every request fails (#673): the rules classify, and the report and the summary table say
    # how many columns and files they classified alone
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mo = dc.dc_run("llm", llm="errors")
    st = mo.summary_table
    assert st["llm_cols_unanswered"].iloc[0] > 0
    cols = list(st.columns)
    k = cols.index("key_n")
    assert cols[k + 1 : k + 4] == ["files_failed", "llm_cols_unanswered", "llm_files_unanswered"]
    text = " ".join(b for b in mo.report if isinstance(b, str))
    assert "The model gave no usable answer for" in text
    assert "also when asked again in smaller batches; the rules alone classified" in text


def test_the_unanswered_counts_are_missing_without_the_model() -> None:
    mo = dc.dc_run("llm")
    st = mo.summary_table
    assert st["llm_cols_unanswered"].isna().all()
    assert st["llm_files_unanswered"].isna().all()
    assert pd.api.types.is_integer_dtype(st["files_failed"])


def test_a_model_that_answers_leaves_nothing_unanswered() -> None:
    mo = dc.dc_run("llm", llm="basic")
    st = mo.summary_table
    assert st["llm_cols_unanswered"].iloc[0] == 0
    assert st["llm_files_unanswered"].iloc[0] == 0
    text = " ".join(b for b in mo.report if isinstance(b, str))
    assert "no usable answer" not in text
