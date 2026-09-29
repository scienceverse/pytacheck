"""R serialization: cache keys (ASCII) and .rds files (XDR, compressed)."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
from pathlib import Path

import pandas as pd
import pytest

from metacheck.llm._rds import (
    RInt,
    RList,
    RVec,
    read_rds,
    serialize,
    to_python,
    unserialize,
    write_rds,
)
from tests.llm.support import FIXTURES

RDS = FIXTURES / "rds"


def test_ascii_serialization_matches_r() -> None:
    payload = {
        "text": "hi",
        "system_prompt": "sys",
        "model": "groq/x",
        "type": None,
        "params": {"temperature": 0.0},
    }
    raw = serialize(payload, ascii=True)
    assert raw.startswith(b"A\n3\n263427\n197888\n5\nUTF-8\n531\n5\n16\n1\n262153\n2\nhi\n")
    assert hashlib.md5(raw).hexdigest() == "801e1462f98e73b5976e98d0c633a719"


def test_ascii_escapes_and_special_values() -> None:
    x = [
        "a b\n\"é\\'?\t\x01~",
        RVec("chr", [None]),
        RVec("lgl", [True, False, None]),
        RVec("int", [1, None, -3]),
        RVec(
            "dbl", [0.1, 1 / 3, None, math.nan, math.inf, -math.inf, 1e-300, 123456789012345678.0]
        ),
    ]
    text = serialize(x, ascii=True).decode()
    lines = text.split("\n")
    assert "a\\040b\\n\\\"\\303\\251\\\\\\'\\?\\t\\001~" in lines
    assert "0.3333333333333333" in lines
    assert "1.234567890123457e+17" in lines
    assert lines[lines.index("NaN") - 1] == "NA"


def test_r_version_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTACHECK_R_SERIALIZE_VERSION", "4.4.1")
    assert serialize(None, ascii=True).split(b"\n")[2] == str(4 * 65536 + 4 * 256 + 1).encode()


def test_round_trip() -> None:
    obj = RList(
        [RVec("int", [1, None]), RVec("chr", ["é", None]), None, RList([RVec("lgl", [True])])],
        {"names": RVec("chr", ["a", "b", "c", "d"])},
    )
    for ascii_ in (True, False):
        assert unserialize(serialize(obj, ascii=ascii_)) == obj


def test_data_frame_round_trip(tmp_path: Path) -> None:
    df = pd.DataFrame(
        {
            "s": pd.array(["a", None], dtype="string"),
            "i": pd.array([1, None], dtype="Int64"),
            "d": [1.5, math.nan],
            "l": pd.array([True, None], dtype="boolean"),
            "f": pd.Categorical(["y", None], categories=["y", "x"]),
        }
    )
    path = tmp_path / "x.rds"
    write_rds({"df": df, "n": RInt(1), "when": dt.datetime(2020, 1, 1, tzinfo=dt.UTC)}, path)
    back = to_python(read_rds(path))
    pd.testing.assert_frame_equal(back["df"], df)
    assert back["n"] == 1
    assert back["when"] == dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


def test_reads_r_written_files() -> None:
    assert to_python(read_rds(RDS / "intseq.rds")) == list(range(1, 11))  # ALTREP compact_intseq
    frame = to_python(read_rds(RDS / "frame.rds"))
    assert frame.columns.tolist() == ["i", "d", "s", "l", "f"]
    assert frame["i"].dtype == "Int64"
    assert frame["s"].tolist()[2] == "é"
    assert pd.isna(frame["d"].tolist()[1])
    assert frame["f"].cat.categories.tolist() == ["y", "x"]
    assert frame["f"].tolist()[:2] == ["x", "y"]
    nested = to_python(read_rds(RDS / "nested.rds"))
    assert nested["a"] == 1
    assert nested["b"] == {"c": "x", "d": None}
    assert nested["t"] == dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    v = nested["v"]
    assert math.isnan(v[0]) and v[1] == math.inf and v[2] == -math.inf and v[3] is None
    tib = to_python(read_rds(RDS / "tibble.rds"))
    assert tib["x"].tolist() == ["p", "q"]
    assert to_python(read_rds(RDS / "named_vec.rds")) == {"a": 1.0, "b": 2.0}
    assert to_python(read_rds(RDS / "bzip2.rds")) == {"x": "bz"}
    assert to_python(read_rds(RDS / "xz.rds")) == {"x": "xz"}


def test_python_scalars_map_like_r_literals() -> None:
    # 4096 in R is a double; 4096L an integer
    assert unserialize(serialize(4096)) == RVec("dbl", [4096.0])
    assert unserialize(serialize(RInt(4096))) == RVec("int", [4096])
    assert unserialize(serialize(True)) == RVec("lgl", [True])
    with pytest.raises(TypeError):
        serialize(object())
