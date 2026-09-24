"""Port of metacheck's tests/testthat/test-stat-tables.R (plus structured readers)."""

from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.statout.stat_tables import (
    _ipynb_is_noise,
    _ipynb_stat_kv,
    _ipynb_stat_line,
    _ipynb_stat_table,
    _jasp_clean_colname,
    _pb_fields,
    _pb_varint,
    _stat_num_to_chr,
    read_stat_tables,
)


@pytest.fixture
def notebooks(fixtures_dir: Path) -> Path:
    return fixtures_dir / "notebooks"


def test_read_stat_tables_reads_jupyter_notebook_outputs(notebooks: Path) -> None:
    tabs = read_stat_tables(notebooks / "notebook_python.ipynb")
    assert isinstance(tabs, list)
    assert len(tabs) > 0
    for tb in tabs:
        assert {"analysis", "title", "data", "table_index"} <= set(tb)
        assert isinstance(tb["data"], pd.DataFrame)
    assert [tb["table_index"] for tb in tabs] == list(range(1, len(tabs) + 1))

    html_tab = [x for x in tabs if "t" in x["data"].columns]
    assert len(html_tab) == 1
    assert list(html_tab[0]["data"].columns) == ["cond", "t", "df", "p"]
    assert html_tab[0]["data"]["t"].tolist() == ["3.41", "2.07"]
    assert html_tab[0]["analysis"].startswith("Cell ")

    stat_tab = [x for x in tabs if "statistic" in x["data"].columns]
    assert len(stat_tab) == 1
    assert stat_tab[0]["call_fn"] == "TtestResult"
    assert stat_tab[0]["data"]["statistic"].tolist() == ["4.8977"]
    assert stat_tab[0]["data"]["pvalue"].tolist() == ["0.00007"]
    assert stat_tab[0]["data"]["df"].tolist() == ["22"]


def test_notebook_call_fn_types_statistic(notebooks: Path, stato: None) -> None:
    from pytacheck.statout.stat_output import stat_results_long

    tabs = read_stat_tables(notebooks / "notebook_python.ipynb")
    long = stat_results_long(tabs, source_file="notebook_python.ipynb")
    stat_row = long[long["statistic"] == "statistic"]
    assert len(stat_row) == 1
    assert stat_row["stato_iri"].tolist() == ["http://purl.obolibrary.org/obo/STATO_0000176"]


LINREG = (
    "LinregressResult(slope=np.float64(11.474329080951929), "
    "intercept=np.float64(46.43030811550318), rvalue=np.float64(0.4052714777933583), "
    "pvalue=np.float64(0.004269632365840751), stderr=np.float64(3.8162))"
)
TTEST_IND = (
    "Ttest_indResult(statistic=np.float64(5.324149292705737), "
    "pvalue=np.float64(3.6340850924488865e-07))"
)


def _scipy_results() -> list[dict]:
    out = []
    for line in [
        LINREG,
        TTEST_IND,
        "MannwhitneyuResult(statistic=1949.0, pvalue=0.0013180093601592962)",
        "KruskalResult(statistic=4.552258064516124, pvalue=0.10268091290330437)",
        "WilcoxonResult(statistic=0.0, pvalue=0.001953125)",
        "KstestResult(statistic=1.0, pvalue=0.0, statistic_location=1683.95, statistic_sign=-1)",
    ]:
        res = _ipynb_stat_line(line)
        assert res is not None
        out.extend(res)
    return out


def test_ipynb_stat_line_parses_scipy_result_classes() -> None:
    linreg = _ipynb_stat_line(LINREG)
    assert linreg is not None and len(linreg) == 1
    assert linreg[0]["call_fn"] == "LinregressResult"
    assert linreg[0]["data"]["slope"].tolist() == ["11.474329080951929"]
    assert linreg[0]["data"]["stderr"].tolist() == ["3.8162"]

    ttest_ind = _ipynb_stat_line(TTEST_IND)
    assert ttest_ind is not None and len(ttest_ind) == 1
    assert ttest_ind[0]["call_fn"] == "Ttest_indResult"

    for line in [
        "MannwhitneyuResult(statistic=1949.0, pvalue=0.0013180093601592962)",
        "KruskalResult(statistic=4.552258064516124, pvalue=0.10268091290330437)",
        "WilcoxonResult(statistic=0.0, pvalue=0.001953125)",
        "KstestResult(statistic=1.0, pvalue=0.0, statistic_location=1683.95, statistic_sign=-1)",
    ]:
        r = _ipynb_stat_line(line)
        assert r is not None and len(r) == 1
        assert "statistic" in r[0]["data"].columns


def test_ipynb_stat_line_types_each_class(stato: None) -> None:
    from pytacheck.statout.stat_output import stat_results_long

    long = stat_results_long(_scipy_results(), source_file="test.ipynb")
    slope = long[(long["row_label"] == "") & (long["statistic"] == "slope")]
    assert slope["stato_iri"].tolist() == ["http://purl.obolibrary.org/obo/STATO_0000656"]
    for term in ["mannWhitneyU", "kruskalWallisH", "wilcoxonV", "kolmogorovSmirnovD"]:
        rows = long[(long["statistic"] == "statistic") & long["stato_iri"].str.contains(term)]
        assert len(rows) == 1, term
    assert ((long["statistic"] == "statistic_location") & (long["stato_iri"] == "")).any()


SUMMARY_TEXT = [
    "                            OLS Regression Results",
    "==============================================================================",
    "Dep. Variable:         Square Footage   R-squared:                       0.844",
    "Model:                            OLS   Adj. R-squared:                  0.834",
    "Method:                 Least Squares   F-statistic:                     84.18",
    "=============================================================================================",
    "                                coef    std err          t      P>|t|      [0.025      0.975]",
    "---------------------------------------------------------------------------------------------",
    "const                      -4.62e+04   4449.507    -10.384      0.000    -5.5e+04   -3.74e+04",
    "Number of Occupants        -188.2184     20.093     -9.367      0.000    -228.118    -148.318",
]


def test_ipynb_stat_table_parses_statsmodels_summary() -> None:
    tabs = _ipynb_stat_table(SUMMARY_TEXT)
    assert tabs is not None and len(tabs) == 2
    coef_tab = next(t for t in tabs if "coef" in t["data"].columns)
    kv_tab = next(t for t in tabs if "R-squared" in t["data"].columns)
    assert {"coef", "std err", "t", "P>|t|"} <= set(coef_tab["data"].columns)
    assert len(coef_tab["data"]) == 2
    assert coef_tab["data"]["coef"].tolist() == ["-4.62e+04", "-188.2184"]
    assert not {"Dep. Variable", "Model", "Method"} & set(kv_tab["data"].columns)
    assert kv_tab["data"]["R-squared"].tolist() == ["0.844"]
    assert _ipynb_stat_table(["This model fit the data reasonably well."]) is None


def test_ipynb_stat_table_long_typing(stato: None) -> None:
    from pytacheck.statout.stat_output import stat_results_long

    tabs = _ipynb_stat_table(SUMMARY_TEXT)
    long = stat_results_long(tabs, source_file="test.ipynb")
    const = long[long["row_label"] == "const"]
    iri = dict(zip(const["statistic"], const["stato_iri"], strict=True))
    assert iri["coef"] == "http://purl.obolibrary.org/obo/STATO_0000471"
    assert iri["std err"] == "http://purl.obolibrary.org/obo/STATO_0000037"
    assert iri["t"] == "http://purl.obolibrary.org/obo/STATO_0000176"
    assert iri["P>|t|"] == "http://purl.obolibrary.org/obo/STATO_0000700"
    kv = long[long["statistic"].isin(["R-squared", "Adj. R-squared", "F-statistic"])]
    assert (kv["row_label"] == "").all()
    kv_iri = dict(zip(kv["statistic"], kv["stato_iri"], strict=True))
    assert kv_iri["R-squared"] == "http://purl.obolibrary.org/obo/STATO_0000564"
    assert (
        kv_iri["Adj. R-squared"]
        == "https://scienceverse.org/schema/metacheck/statistics/adjustedRSquared"
    )
    assert kv_iri["F-statistic"] == "http://purl.obolibrary.org/obo/STATO_0000282"


KV_TEXT = [
    "Dep. Variable:         Square Footage   R-squared:                       0.844",
    "Model:                            OLS   Adj. R-squared:                  0.834",
    "Method:                 Least Squares   F-statistic:                     84.18",
    "Date:                Mon, 10 Mar 2025   Prob (F-statistic):           2.16e-35",
    "Time:                        01:17:53   Log-Likelihood:                -1000.9",
    "No. Observations:                 100   AIC:                             2016.",
    "Df Residuals:                      93   BIC:                             2034.",
    "Df Model:                           6",
    "Covariance Type:            nonrobust",
]


def test_ipynb_stat_kv_extracts_full_header_block() -> None:
    kv = _ipynb_stat_kv(KV_TEXT)
    assert kv is not None
    assert kv["title"] == "Model fit statistics"
    d = kv["data"]
    expected = {
        "R-squared": "0.844",
        "Adj. R-squared": "0.834",
        "F-statistic": "84.18",
        "Prob (F-statistic)": "2.16e-35",
        "Log-Likelihood": "-1000.9",
        "No. Observations": "100",
        "AIC": "2016.",
        "Df Residuals": "93",
        "BIC": "2034.",
    }
    for k, v in expected.items():
        assert d[k].tolist() == [v]
    assert not {"Dep. Variable", "Model", "Method", "Date", "Time", "Covariance Type"} & set(
        d.columns
    )
    assert "Df Model" in d.columns
    assert _ipynb_stat_kv(["This model fit the data reasonably well."]) is None
    assert _ipynb_stat_kv(["Dep. Variable: Square Footage   R-squared: 0.844"]) is None


def test_ipynb_stat_kv_long_typing(stato: None) -> None:
    from pytacheck.statout.stat_output import stat_results_long

    long = stat_results_long([_ipynb_stat_kv(KV_TEXT)], source_file="test.ipynb")
    iri = dict(zip(long["statistic"], long["stato_iri"], strict=True))
    assert iri["Log-Likelihood"] == "http://purl.obolibrary.org/obo/STATO_0000550"
    assert iri["No. Observations"] == "http://purl.obolibrary.org/obo/STATO_0000088"
    assert iri["AIC"] == "http://purl.obolibrary.org/obo/STATO_0000325"
    assert iri["BIC"] == "http://purl.obolibrary.org/obo/STATO_0000327"
    assert iri["Df Residuals"] == "http://purl.obolibrary.org/obo/STATO_0000069"
    assert iri["Df Model"] == ""


def test_read_stat_tables_filters_notebook_noise(notebooks: Path) -> None:
    tabs = read_stat_tables(notebooks / "notebook_python.ipynb")
    all_text = [
        v for x in tabs if list(x["data"].columns) == ["text"] for v in x["data"]["text"].tolist()
    ]
    assert not any("Figure size" in t for t in all_text)


def test_ipynb_is_noise_drops_machinery_keeps_statistics() -> None:
    assert _ipynb_is_noise(["<Figure size 432x288 with 1 Axes>"])
    assert _ipynb_is_noise(["plot without title"])
    assert _ipynb_is_noise(["<AxesSubplot: xlabel='cond'>"])
    assert _ipynb_is_noise(["0%|          | 0/200 [00:00<?, ?it/s]"])
    assert _ipynb_is_noise(
        [
            "/opt/py/scipy/stats/_continuous_distns.py:6832: RuntimeWarning: overflow",
            "  return np.exp(x)",
        ]
    )
    assert _ipynb_is_noise([""])
    assert not _ipynb_is_noise(
        ["TtestResult(statistic=4.897732993778993, pvalue=6.750826167315465e-05, df=22)"]
    )
    assert not _ipynb_is_noise(
        ["       T  dof alternative     p-val", "  4.898   22   two-sided  0.00007"]
    )
    assert not _ipynb_is_noise(
        ["Generalized linear mixed model fit by maximum likelihood (Laplace)"]
    )


def test_read_stat_tables_handles_notebooks_without_usable_output(
    notebooks: Path, tmp_path: Path
) -> None:
    r_tabs = read_stat_tables(notebooks / "notebook_r.ipynb")
    assert len(r_tabs) > 0
    r_stat = [x for x in r_tabs if "t" in x["data"].columns]
    assert len(r_stat) == 1
    assert r_stat[0]["data"]["t"].tolist() == ["2.8134"]
    assert r_stat[0]["data"]["df"].tolist() == ["47.2"]
    assert r_stat[0]["data"]["p-value"].tolist() == ["0.007123"]

    bad = tmp_path / "bad.ipynb"
    bad.write_text('{"foo": 1}\n')
    assert read_stat_tables(bad) == []

    stripped = tmp_path / "stripped.ipynb"
    stripped.write_text(
        json.dumps(
            {
                "cells": [{"cell_type": "code", "source": ["x = 1"], "outputs": []}],
                "nbformat": 4,
            }
        )
    )
    assert read_stat_tables(stripped) == []

    with pytest.raises(FileNotFoundError, match="File not found"):
        read_stat_tables("no_such_file.ipynb")


# ---------------------------------------------------------------------------
# Structured JASP / jamovi readers and the HTML fallback
# ---------------------------------------------------------------------------


def test_read_stat_tables_jasp_structured(fixtures_dir: Path) -> None:
    tabs = read_stat_tables(fixtures_dir / "formats" / "sample.jasp")
    assert [t["title"] for t in tabs] == ["Descriptives", "Paired Samples T-Test"]
    assert all(t["analysis"] == "Paired Samples T-Test" for t in tabs)
    assert all(t["analysis_id"] == "0" for t in tabs)
    ttest = tabs[1]["data"]
    assert ttest["t"].tolist() == ["6.89966035949175", "5.04714614482635"]
    assert ttest["p"].tolist() == ["0.000124571109365704", "0.000992766799151074"]
    assert ttest["df"].tolist() == ["8", "8"]


def test_read_stat_tables_omv_structured(fixtures_dir: Path, stato: None) -> None:
    tabs = read_stat_tables(fixtures_dir / "formats" / "sample.omv")
    assert len(tabs) == 3
    first = tabs[0]
    assert first["analysis"] == "ttestOneS"
    assert first["analysis_id"] == "5"
    assert first["data"]["stat[stud]"].tolist() == ["3.74627189026507"]
    roles = first["data"].attrs["col_roles"]
    assert roles["p[stud]"] == {"type": "number", "format": "zto,pvalue"}
    assert roles["var[stud]"]["type"] == "text"


def test_read_stat_tables_html_fallback(data_dir: Path) -> None:
    tabs = read_stat_tables(data_dir / "html_only.jasp")
    assert len(tabs) == 2
    assert all("table_index" in t for t in tabs)
    custom = read_stat_tables(data_dir / "custom_html.jasp")
    first = custom[0]
    assert first["analysis"] == "Main   result"  # trimws() only, inner spaces kept
    assert first["title"] == "Independent Samples T-Test"
    assert list(first["data"].columns) == ["V1", "t", "df", "p", "Lower", "Upper", "Cohen's d"]
    # the footnote rows are dropped
    assert first["data"]["V1"].tolist() == ["score", "time on task"]
    # table_index counts every <table>, including ones without a header
    assert [t["table_index"] for t in custom] == [1, 2, 5, 7]


def test_read_stat_tables_not_a_zip(data_dir: Path) -> None:
    # utils::unzip() returns NULL for a non-archive and basename(NULL) errors
    with pytest.raises(TypeError, match="character vector"):
        read_stat_tables(data_dir / "not_zip.jasp")


def test_read_stat_tables_empty_zip(tmp_path: Path) -> None:
    # utils::unzip() returns NULL for an archive without entries, and
    # read_stat_tables() then fails in basename(NULL), as for a non-zip file.
    empty = tmp_path / "empty.jasp"
    with zipfile.ZipFile(empty, "w"):
        pass
    with pytest.raises(TypeError, match="character vector argument expected"):
        read_stat_tables(empty)
    # A zip holding only directory entries opens fine and yields nothing.
    dirs = tmp_path / "dirs.omv"
    with zipfile.ZipFile(dirs, "w") as z:
        z.writestr("05 ttestOneS/", "")
    assert read_stat_tables(dirs) == []


def test_protobuf_reader() -> None:
    assert _pb_varint(bytes([0x96, 0x01]), 1) == {"value": 150.0, "pos": 3}
    msg = bytes([0x08, 0x96, 0x01, 0x12, 0x02]) + b"hi" + bytes([0x19]) + struct.pack("<d", 2.5)
    fields = _pb_fields(msg)
    assert fields == [
        {"field": 1, "wire": 0, "value": 150.0},
        {"field": 2, "wire": 2, "value": b"hi"},
        {"field": 3, "wire": 1, "value": 2.5},
    ]
    assert _pb_fields(bytes([0x00])) is None
    assert _pb_fields(bytes([0x12, 0x05, 0x61])) is None
    assert _pb_fields(b"") == []


def test_stat_num_to_chr_and_colnames() -> None:
    assert _stat_num_to_chr(113.0) == "113"
    assert _stat_num_to_chr(6.58e-72) == "6.58e-72"
    assert _stat_num_to_chr(1 / 3) == "0.333333333333333"
    assert _stat_num_to_chr(1e15) == "1e+15"
    assert _stat_num_to_chr(-0.0) == "0"
    assert _stat_num_to_chr(float("inf")) == "Inf"
    assert _stat_num_to_chr(float("nan")) == "NaN"
    assert _stat_num_to_chr(None) is None
    assert _jasp_clean_colname("JaspColumn_.21._Encoded_pearson_p.value") == "pearson_p.value"
    assert _jasp_clean_colname("JaspColumn_.3._Encoded_") == "JaspColumn_.3._Encoded_"
    assert _jasp_clean_colname(None) == ""
