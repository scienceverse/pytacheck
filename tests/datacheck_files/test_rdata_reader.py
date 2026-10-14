"""The readers of R's serialization format (``datacheck/_files_rdata.py``).

The C reader (``_rdata_c``) must build exactly the objects the Python reader
builds: the same types, values, attributes, strings (Latin-1 ones included) and
the same sharing of referenced objects. Whatever it does not read (ASCII streams,
damaged files) goes to the Python reader, which gives the result or its error.
The fixtures in ``data/robjects`` are written by ``make_robjects.R``.
"""

from __future__ import annotations

import datetime as dt
import gc
import math
import os
import struct
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from metacheck.datacheck import _files_rdata as rd
from metacheck.datacheck._files_readers import read_rds_head

DATA = Path(__file__).parent / "data"
ROBJECTS = DATA / "robjects"
WORKSPACE_MAGIC = (b"RDX2", b"RDX3", b"RDA2", b"RDA3", b"RDB2", b"RDB3")

needs_c = pytest.mark.skipif(rd._rdata_c is None, reason="the C reader is not built")

FILES = sorted(
    [
        *DATA.glob("*.rds"),
        *DATA.glob("*.RData"),
        *DATA.glob("*.rda"),
        *DATA.glob("review/*.rds"),
        *ROBJECTS.glob("*.rds"),
    ]
)
# read by the Python reader only: an ASCII stream, and a formula nested deeper
# than Python's recursion limit allows
PYTHON_ONLY = {"study_ascii.rds", "formula400.rds"}


def _stream(path: Path) -> tuple[bytes, int]:
    raw = rd._decompress(path.read_bytes())
    return raw, 5 if raw[:4] in WORKSPACE_MAGIC else 0


def _same(a: Any, b: Any, where: str, pairs: dict[int, Any]) -> None:
    """*a* and *b* are the same R value, down to types, bytes and sharing."""
    assert type(a) is type(b), f"{where}: {type(a).__name__} != {type(b).__name__}"
    if isinstance(a, rd.RObject):
        if id(a) in pairs:  # a referenced object: the same one on both sides
            assert pairs[id(a)] is b, f"{where}: shared differently"
            return
        pairs[id(a)] = b
        _same(a.type, b.type, f"{where}.type", pairs)
        _same(a.name, b.name, f"{where}.name", pairs)
        _same(a.attrs, b.attrs, f"{where}.attrs", pairs)
        _same(a.value, b.value, f"{where}.value", pairs)
    elif isinstance(a, np.ndarray):
        assert (a.dtype, a.shape) == (b.dtype, b.shape), where
        assert a.tobytes() == b.tobytes(), where  # NaN payloads (NA_real_) too
        assert a.flags.writeable and b.flags.writeable, where
    elif isinstance(a, list | tuple):
        assert len(a) == len(b), where
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            _same(x, y, f"{where}[{i}]", pairs)
    elif isinstance(a, dict):
        assert list(a) == list(b), where
        for k in a:
            _same(a[k], b[k], f"{where}[{k!r}]", pairs)
    elif isinstance(a, float):
        assert struct.pack("<d", a) == struct.pack("<d", b), where
    else:
        if isinstance(a, rd.Latin1Str):
            assert a.raw == b.raw, where
        assert a == b, where


@needs_c
@pytest.mark.parametrize(
    "path", [p for p in FILES if p.name not in PYTHON_ONLY], ids=lambda p: p.name
)
def test_c_reader_builds_the_objects_of_the_python_reader(path: Path) -> None:
    raw, offset = _stream(path)
    with rd._gc_paused():
        expected = rd._unserialize_py(raw, offset)
    _same(expected, rd._rdata_c.unserialize(raw, offset), path.name, {})


@pytest.mark.skipif(
    not os.environ.get("METACHECK_REQUIRE_C_READER"), reason="the C reader is optional here"
)
def test_c_reader_is_built_where_required() -> None:
    """CI builds the C reader (a failed build would only skip the tests above)."""
    assert rd._rdata_c is not None
    assert rd.rds_reader() == "c"


@needs_c
def test_ascii_stream_is_read_by_the_python_reader() -> None:
    raw, _ = _stream(DATA / "study_ascii.rds")
    with pytest.raises(ValueError, match="unsupported input format"):
        rd._rdata_c.unserialize(raw)
    df = rd.read_rds(DATA / "study_ascii.rds")
    _same(df, rd.read_rds(DATA / "study.rds"), "study", {})


@needs_c
def test_damaged_stream_gives_the_python_readers_error() -> None:
    raw, _ = _stream(DATA / "study.rds")
    cut = raw[: len(raw) // 2]
    with pytest.raises(Exception) as from_python:
        rd._unserialize_py(cut)
    with pytest.raises(Exception) as from_either:
        rd._unserialize(cut)
    assert type(from_either.value) is type(from_python.value)
    assert str(from_either.value) == str(from_python.value)


@needs_c
def test_python_setting_turns_the_c_reader_off(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any) -> Any:
        raise AssertionError("the C reader was used")

    monkeypatch.setenv("METACHECK_RDS_READER", "python")
    monkeypatch.setattr(rd._rdata_c, "unserialize", refuse)
    assert rd.rds_reader() == "python"
    assert rd.is_data_frame(rd.read_rds(DATA / "study.rds"))


@pytest.mark.parametrize("reader", ["c", "python"])
def test_data_frames_read_the_same_with_either_reader(
    reader: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if reader == "c" and rd._rdata_c is None:
        pytest.skip("the C reader is not built")
    frames = {}
    for path in (DATA / "study.rds", DATA / "labs.rds", DATA / "latin1.rds", DATA / "tibble.rds"):
        monkeypatch.setenv("METACHECK_RDS_READER", reader)
        got = read_rds_head(str(path), math.inf)
        monkeypatch.setenv("METACHECK_RDS_READER", "python")
        frames[path.name] = (got, read_rds_head(str(path), math.inf))
    for name, (got, want) in frames.items():
        assert got is not None, name
        pd.testing.assert_frame_equal(got, want)
        assert got.attrs == want.attrs, name


def _objects() -> dict[str, Any]:
    obj = rd.read_rds(ROBJECTS / "objects.rds")
    return dict(zip(rd._strvec(obj.attr("names")), obj.value, strict=True))


def test_environments_written_by_reference() -> None:
    """The base namespace, a namespace and a package environment (R's codes 250,
    249 and 248), and an environment written as a persistent name (247)."""
    envs = rd.read_rds(ROBJECTS / "envs.rds")
    names = rd._strvec(envs.attr("names"))
    got = {k: (v.type, v.name) for k, v in zip(names, envs.value, strict=True) if k != "mean"}
    assert got == {
        "base": (rd.ENVSXP, "base"),
        "ns": (rd.ENVSXP, "stats:4.5.3"),
        "pkg": (rd.ENVSXP, "package:stats"),
        "global": (rd.ENVSXP, "globalenv"),
        "empty": (rd.ENVSXP, "emptyenv"),
        "baseenv": (rd.ENVSXP, "baseenv"),
    }
    closure = envs.value[names.index("mean")]
    assert closure.type == rd.CLOSXP
    assert closure.value[0] == ("base", closure.value[0][1])  # its environment's name
    persist = rd.read_rds(ROBJECTS / "persist.rds")
    assert persist.value[1].type == rd.PERSISTSXP
    assert persist.value[1].value == ["env::1"]


def test_complex_numbers_are_kept_exactly() -> None:
    z = _objects()["complex"].value
    assert z.dtype == np.complex128
    assert np.isinf(z[0].imag) and z[0].real == 1  # 1+Inf i: the real part stays 1
    assert math.copysign(1, z[1].real) == -1  # -0+1i keeps its sign
    assert z[2].real == math.inf and z[2].imag == -math.inf


def test_altrep_and_strings() -> None:
    objects = _objects()
    np.testing.assert_array_equal(objects["compact_int"].value, np.arange(1, 1001))
    assert objects["deferred"].value == [str(i) for i in range(1, 51)]
    assert objects["deferred_real"].value == ["1.5", "2", None, "1e+10"]
    assert rd._strvec(objects["wrapped"].attr("names")) == list("abcdefghij")
    strings = objects["strings"].value
    assert isinstance(strings[0], rd.Latin1Str)
    assert (strings[0], strings[0].raw) == ("café", b"caf\xe9")
    assert strings[1:4] == ["plain", None, "naïve"]


@needs_c
def test_deeply_nested_objects_are_read_by_the_c_reader() -> None:
    """A formula of 400 terms nests 400 calls: past Python's recursion limit, so
    only the C reader reads it (as R does)."""
    formula = rd.read_rds(ROBJECTS / "formula400.rds")
    assert formula.type == rd.LANGSXP
    assert formula.value[0][1].name == "~"


def test_data_frame_columns_convert_to_their_pandas_types() -> None:
    """Each column type of ``columns.rds`` (see ``make_robjects.R``), with its NA."""
    df = read_rds_head(str(ROBJECTS / "columns.rds"), math.inf)
    assert df is not None
    assert (df["int"].dtype, df["int"].tolist()) == ("Int64", [1, pd.NA, -5])
    assert (df["lgl"].dtype, df["lgl"].tolist()) == ("boolean", [True, pd.NA, False])
    assert df["num"].dtype == "float64" and df["num"].iloc[0] == 1.5
    assert df["num"].iloc[1:].isna().all()
    assert df["fct"].cat.categories.tolist() == ["a", "b"] and not df["fct"].cat.ordered
    assert df["fct"].cat.codes.tolist() == [1, -1, 0]
    assert df["ord"].cat.ordered and df["ord"].tolist() == ["lo", "hi", "lo"]
    # days: -0.5 is the day before 1970-01-01; an integer Date's NA is NA
    assert df["date"].tolist() == [dt.date(1969, 12, 31), None, dt.date(2020, 1, 1)]
    assert df["idate"].tolist() == [dt.date(1970, 1, 2), None, dt.date(1969, 12, 31)]
    assert (df["i64"].dtype, df["i64"].tolist()) == ("Int64", [5, pd.NA, -3])
    assert df["chr"].tolist() == ["caf\udce9", pd.NA, "x"]  # Latin-1 bytes, repaired later
    assert str(df["time"].dt.tz) == "Europe/Amsterdam"
    assert df["time"].iloc[0] == pd.Timestamp("2020-01-01 10:00", tz="Europe/Amsterdam")
    assert df["time"].isna().tolist() == [False, True, False]


def test_dates_out_of_range_keep_their_error() -> None:
    """A Date past year 9999 cannot be a ``datetime.date``: the error names it."""
    col = rd.RObject(rd.REALSXP, np.array([0.0, 2932897.0]), {"class": _chr("Date")})
    with pytest.raises(OverflowError, match="date value out of range"):
        rd._column_to_pandas(col, 2)
    ok = rd.RObject(rd.REALSXP, np.array([2932896.0, -719162.0]), {"class": _chr("Date")})
    assert rd._column_to_pandas(ok, 2).tolist() == [dt.date(9999, 12, 31), dt.date(1, 1, 1)]


def test_factor_codes_beyond_the_levels_are_an_error() -> None:
    """R calls such a factor malformed; reading it fails, as before."""
    col = rd.RObject(
        rd.INTSXP,
        np.array([1, 3], dtype=np.int32),
        {"levels": rd.RObject(rd.STRSXP, ["a", "b"]), "class": _chr("factor")},
    )
    with pytest.raises(IndexError):
        rd._column_to_pandas(col, 2)


def _chr(*values: str) -> rd.RObject:
    return rd.RObject(rd.STRSXP, list(values))


@pytest.mark.parametrize("enabled", [True, False])
def test_reading_leaves_the_collector_as_it_was(enabled: bool) -> None:
    """The collector is paused during a read and on or off after it as before."""
    was = gc.isenabled()
    try:
        (gc.enable if enabled else gc.disable)()
        rd.read_rds(DATA / "study.rds")
        assert gc.isenabled() is enabled
    finally:
        (gc.enable if was else gc.disable)()


@pytest.mark.parametrize(
    ("at_import", "frozen", "promote", "calls"),
    [
        (0, 0, True, ["freeze", "unfreeze"]),
        (375, 375, True, ["freeze", "unfreeze"]),  # Python 3.12 starts with 375
        (0, 5, True, []),
        (375, 380, True, []),
        (0, 0, False, []),
    ],
    ids=["promoted", "python-3.12", "program-froze", "program-froze-3.12", "free-threaded"],
)
def test_what_a_read_builds_goes_to_the_oldest_generation(
    at_import: int, frozen: int, promote: bool, calls: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``gc.freeze()`` then ``gc.unfreeze()`` after a read, unless the program froze
    objects itself (unfreezing would undo that) or the build is free-threaded."""
    seen: list[str] = []
    monkeypatch.setattr(gc, "get_freeze_count", lambda: frozen)
    monkeypatch.setattr(gc, "freeze", lambda: seen.append("freeze"))
    monkeypatch.setattr(gc, "unfreeze", lambda: seen.append("unfreeze"))
    monkeypatch.setattr(rd, "_PROMOTE", promote)
    monkeypatch.setattr(rd, "_frozen_at_import", at_import)
    rd.read_rds(DATA / "study.rds")
    assert seen == calls
    # once unfrozen, Python's own objects are in the oldest generation too
    assert rd._frozen_at_import == (0 if calls else at_import)
