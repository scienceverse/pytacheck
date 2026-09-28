"""data_read_head() -- port of upstream tests/testthat/test-data-read.R.

All offline. R-written fixtures (.RData, .rds, .ods) live in ``data/`` (see
``data/make_fixtures.R``); the rest are written by the tests themselves.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import random
import time
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.datacheck import files as F

DATA = Path(__file__).parent / "data"
INF = math.inf


def _write_csv(path: Path, cols: dict[str, list[object]]) -> None:
    """utils::write.csv(data.frame(...), row.names = FALSE)."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC, lineterminator="\n")
        w.writerow(list(cols))
        w.writerows(zip(*cols.values(), strict=True))


def _has_checks() -> bool:
    if importlib.util.find_spec("pytacheck.datacheck.checks") is None:
        return False
    mod = importlib.import_module("pytacheck.datacheck.checks")
    return hasattr(mod, "data_check_is_qualtrics")


def test_reads_delimited_text_and_detects_delimiter(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    _write_csv(p, {"a": [1, 2, 3], "b": ["x", "y", "z"]})
    df = F.data_read_head(p, n_rows=INF)
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["a", "b"]
    assert len(df) == 3

    tsv = tmp_path / "a.tsv"
    tsv.write_text("id\tval\n1\t10\n2\t20\n", encoding="utf-8")
    dft = F.data_read_head(tsv, n_rows=INF)
    assert list(dft.columns) == ["id", "val"]
    assert len(dft) == 2


def test_honours_n_rows(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    _write_csv(p, {"a": list(range(1, 101))})
    assert len(F.data_read_head(p, n_rows=5)) == 5
    assert len(F.data_read_head(p, n_rows=INF)) == 100
    assert len(F.data_read_head(p)) == 5  # default n_rows = 5


def test_returns_none_for_unsupported_formats(tmp_path: Path) -> None:
    p = tmp_path / "a.xyz"
    p.write_text("nothing\n", encoding="utf-8")
    assert F.data_read_head(p) is None


def test_missing_file_warns_and_returns_none(tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="Could not read nope.csv"):
        assert F.data_read_head(tmp_path / "nope.csv") is None


def test_recovers_data_frame_from_rdata_workspace() -> None:
    # study_data (a data frame) saved alongside a fitted lm model
    df = F.data_read_head(DATA / "workspace.RData", n_rows=INF)
    assert isinstance(df, pd.DataFrame)
    assert list(df.columns) == ["id", "score", "f", "s", "d", "l"]
    assert len(df) == 5


def test_rdata_without_data_frame_returns_none() -> None:
    # only a model and a vector -> no reusable tabular data
    assert F.data_read_head(DATA / "noframe.RData", n_rows=INF) is None


def test_rdata_first_frame_is_load_order() -> None:
    # as.list(new.env()) order (hash buckets), not the save() order
    df = F.data_read_head(DATA / "twoframes.rda", n_rows=INF)
    assert list(df.columns) == ["a"]


def test_sanitises_invalid_utf8_column_names() -> None:
    # names(d) <- c("\xefPeronalData_fullname", "ok") saved to .rds
    df = F.data_read_head(DATA / "badname.rds", n_rows=INF)
    assert isinstance(df, pd.DataFrame)
    for name in df.columns:
        name.encode("utf-8")  # no surrogate escapes left
    assert df.columns[0] == "ïPeronalData_fullname"
    assert df.columns[1] == "ok"


def test_repairs_invalid_utf8_values(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    p.write_bytes(b"id,sentence\n1,ok\n2,ok\n3,She\xead like\n")
    df = F.data_read_head(p, n_rows=INF)
    assert isinstance(df, pd.DataFrame)
    for v in df["sentence"]:
        v.encode("utf-8")
    # the Latin-1 byte is kept as its UTF-8 equivalent (0xEA -> U+00EA)
    assert df["sentence"].iloc[2] == "Sheêd like"
    assert df.attrs["utf8_repaired"] == {"sentence": 1}


def test_reads_csv_with_latin1_bytes_in_first_lines(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    p.write_bytes(b"id,caf\xe9\n1,caf\xe9 study\n2,plain\n")
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        df = F.data_read_head(p, n_rows=INF)
    assert len(df) == 2
    assert df.columns[1] == "café"
    assert df.iloc[0, 1] == "café study"


def test_latin1_marked_rds_strings_are_repaired() -> None:
    df = F.data_read_head(DATA / "latin1.rds", n_rows=INF)
    assert df["txt"].tolist() == ["café", "plain"]
    assert df.attrs["utf8_repaired"] == {"txt": 1}


def _write_qualtrics(path: Path, n: int = 6) -> None:
    def q(*xs: object) -> str:
        return ",".join(f'"{x}"' for x in xs)

    hdr = q("StartDate", "EndDate", "Status", "IPAddress", "Progress",
            "Duration (in seconds)", "Finished", "RecordedDate", "ResponseId")  # fmt: skip
    qtxt = q("Start Date", "End Date", "Response Type", "IP Address", "Progress",
             "Duration (in seconds)", "Finished", "Recorded Date", "Response ID")  # fmt: skip
    ids = ["startDate", "endDate", "status", "ipAddress", "progress", "duration", "finished",
           "recordedDate", "_recordId"]  # fmt: skip
    imp = ",".join('"{""ImportId"":""' + i + '""}"' for i in ids)
    rng = random.Random(1)
    rows = [
        q(f"2021-05-{i:02d} 10:00:00", f"2021-05-{i:02d} 10:05:00", 0, f"192.168.0.{i}", 100,
          300, 1, f"2021-05-{i:02d} 10:05:00",
          "R_" + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(8)))
        for i in range(1, n + 1)
    ]  # fmt: skip
    path.write_text("\n".join([hdr, qtxt, imp, *rows]) + "\n", encoding="utf-8")


@pytest.mark.skipif(not _has_checks(), reason="needs pytacheck.datacheck.checks (Qualtrics)")
def test_strips_qualtrics_header_rows(tmp_path: Path) -> None:
    p = tmp_path / "q.csv"
    _write_qualtrics(p)
    df = F.data_read_head(p, n_rows=INF)
    assert len(df) == 6
    assert pd.api.types.is_numeric_dtype(df["Duration (in seconds)"])
    assert pd.api.types.is_numeric_dtype(df["Progress"])
    assert df["Duration (in seconds)"].tolist() == [300] * 6


def test_leaves_non_qualtrics_csv_untouched(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    _write_csv(p, {"id": [1, 2, 3, 4, 5],
                   "StartDate": [f"2024-01-0{i}" for i in range(1, 6)],
                   "score": [0.1, 0.2, 0.3, 0.4, 0.5]})  # fmt: skip
    assert len(F.data_read_head(p, n_rows=INF)) == 5


def test_skips_single_big_field_blob_quickly(tmp_path: Path) -> None:
    blob = tmp_path / "blob.csv"
    body = ",".join(f'""k{i}"":{i}' for i in range(1, 5001))
    blob.write_text('studyRunData\n"{' + body + '}"\n', encoding="utf-8")
    t0 = time.perf_counter()
    df = F.data_read_head(blob, n_rows=INF)
    assert df is None
    assert time.perf_counter() - t0 < 2

    ok = tmp_path / "ok.csv"
    rng = random.Random(2)
    ok.write_text(
        "score\n" + "".join(f"{round(rng.gauss(0, 1), 3)}\n" for _ in range(20)),
        encoding="utf-8",
    )
    assert len(F.data_read_head(ok, n_rows=INF)) == 20


def test_reads_table_with_large_quoted_cells_fast(tmp_path: Path) -> None:
    p = tmp_path / "a.csv"
    cell = '"[' + ",".join(["1.0"] * 5000) + ']"'
    p.write_text("id,label,arr\n" + "".join(f"{i},r{i},{cell}\n" for i in range(1, 6)),
                 encoding="utf-8")  # fmt: skip
    t0 = time.perf_counter()
    df = F.data_read_head(p, n_rows=INF)
    assert df is not None
    assert len(df) == 5
    assert list(df.columns) == ["id", "label", "arr"]
    assert time.perf_counter() - t0 < 5


def test_reads_ods_like_xlsx() -> None:
    # readODS::write_ods(data.frame(id = 1:4, grp = c("a","b","a","b"), val = ...))
    df = F.data_read_head(DATA / "written.ods", n_rows=3)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 3
    assert list(df.columns) == ["id", "grp", "val"]


def test_ods_is_tabular() -> None:
    assert F.data_format(["ods"]) == ["tabular"]
    assert F.data_format(["fods"]) == ["tabular"]


# -- beyond the upstream tests: column conventions -----------------------------


def test_column_dtypes_follow_r_types() -> None:
    df = F.data_read_head(DATA / "study.rds", n_rows=INF)
    assert str(df["id"].dtype) == "Int64"
    assert str(df["score"].dtype) == "float64"
    assert isinstance(df["f"].dtype, pd.CategoricalDtype)
    assert str(df["s"].dtype) == "string"
    assert str(df["l"].dtype) == "boolean"
    import datetime as dt

    assert df["d"].iloc[0] == dt.date(2020, 1, 1)


def test_haven_labels_and_formats() -> None:
    pytest.importorskip("pyreadstat")  # the optional "data" extra
    df = F.data_read_head(DATA / "labelled.sav", n_rows=INF)
    attrs = df.attrs["col_attrs"]
    assert attrs["gender"]["label"] == "Respondent gender"
    assert attrs["gender"]["labels"] == [("Male", 1.0), ("Female", 2.0), ("Refused", 9.0)]
    assert attrs["gender"]["class"] == ["haven_labelled", "vctrs_vctr", "double"]
    assert attrs["score"] == {"label": "Total score", "format.spss": "F8.2"}
    # user-missing 99 is read as NA (haven's user_na = FALSE)
    assert pd.isna(df["likert"].iloc[3])
    assert str(df["stamp"].dt.tz) == "UTC"


def test_rds_and_rdata_keep_column_attributes() -> None:
    # U61: labels and classes are kept as stored, for any n_rows (R's head()
    # drops the label of unclassed and factor columns, haven labels survive
    # only when vctrs is loaded, and the .RData child calls head() only for a
    # finite n_rows)
    attrs = F.data_read_head(DATA / "labs.rds", n_rows=INF).attrs["col_attrs"]
    assert attrs["plain"] == {"label": "Plain label"}
    assert attrs["f"] == {"label": "F label", "class": "factor"}
    assert attrs["lab"]["label"] == "Agreed?"
    assert attrs["when"] == {"class": ["POSIXct", "POSIXt"], "tzone": "Europe/Amsterdam"}
    head = F.data_read_head(DATA / "labs.rds", n_rows=2).attrs["col_attrs"]
    assert head["plain"] == attrs["plain"] and head["f"] == attrs["f"]
    full = F.data_read_head(DATA / "labs_ws.RData", n_rows=INF).attrs["col_attrs"]
    part = F.data_read_head(DATA / "labs_ws.RData", n_rows=2).attrs["col_attrs"]
    assert full["plain"] == part["plain"] == {"label": "Plain label"}
    assert part["lab"]["label"] == full["lab"]["label"]
    assert part["lab"]["class"] == ["haven_labelled", "vctrs_vctr", "double"]


def test_integer64_is_kept_as_int64() -> None:
    df = F.data_read_head(DATA / "odd.rds", n_rows=INF)
    assert str(df["big"].dtype) == "Int64"
    assert df["big"].tolist()[0] == 12345678901
    assert df["nested"].tolist() == ["x=1; y=u", "x=2; y=v", "x=3; y=w"]
    assert df["lst"].tolist() == ["1; 2", "a", ""]


def test_fread_date_classes() -> None:
    df = F.data_read_head(DATA / "dates.csv", n_rows=INF)
    classes = {k: v.get("class") for k, v in df.attrs.get("col_attrs", {}).items()}
    assert ["IDate", "Date"] in classes.values()


def test_classify_ref_json_is_valid() -> None:
    # keeps the R-written reference used by test_classify.py loadable
    assert json.loads((DATA / "classify_ref.json").read_text(encoding="utf-8"))["names"]
