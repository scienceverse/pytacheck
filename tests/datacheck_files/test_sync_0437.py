"""data_check helpers synchronised with metacheck 0.4.3.7 (R/data_check_helpers.R at 4a61888).

The expected values follow the R source; the parity cases in ``parity/cases/datacheck_*.yaml``
hold the values R produced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from metacheck.datacheck import files as F
from metacheck.datacheck._columns_codebook import _strip_rtf
from metacheck.datacheck._files_readers import read_stat_file
from metacheck.datacheck.checks import data_check_scale_values

# ---------------------------------------------------------------------------
# .strip_rtf()
# ---------------------------------------------------------------------------


def test_strip_rtf_keeps_lines_and_drops_the_font_table() -> None:
    rtf = (
        r"{\rtf1\ansi{\fonttbl{\f0 Arial;}}{\colortbl;\red0\green0\blue0;}"
        r"\f0 age:\tab Age in years\par sex: 1 = male, 2 = female\par}"
    )
    assert _strip_rtf(rtf) == "age: Age in years\nsex: 1 = male, 2 = female"


def test_strip_rtf_decodes_escapes() -> None:
    rtf = r"{\rtf1 caf\'e9 \u233? \u-10179?\u-8704? brace \{ and \} and \\ x\~y\-z\par next}"
    out = _strip_rtf(rtf)
    assert out.startswith("café é ")
    assert "brace { and } and \\ x yz\nnext" in out


def test_strip_rtf_undefined_cp1252_byte_is_dropped() -> None:
    assert _strip_rtf(r"{\rtf1 a\'81b}") == "ab"


def test_strip_rtf_hidden_group_without_end_drops_the_rest() -> None:
    assert _strip_rtf(r"{\rtf1 text {\*\generator open") == "text"


# ---------------------------------------------------------------------------
# manifest_merge(): null and precision
# ---------------------------------------------------------------------------


def test_manifest_merge_writes_a_nested_null_as_null(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    F.manifest_merge(path, {"x": {"a": F.R_NULL, "b": []}, "small": 0.000012345})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"x": {"a": None, "b": []}, "small": 0.000012345}
    F.manifest_merge(path, {"y": 1})
    assert json.loads(path.read_text(encoding="utf-8"))["x"]["a"] is None


# ---------------------------------------------------------------------------
# scale values without inference
# ---------------------------------------------------------------------------


def test_scale_values_infer_false_flags_nothing_without_a_documented_range() -> None:
    x = [float(v) for v in list(range(1, 8)) * 7 + [99, 33]]
    assert data_check_scale_values(x)["problem"] is True
    assert data_check_scale_values(x, infer=False)["problem"] is False
    # documented codes explaining less than half of the values: skipped, not guessed
    assert (
        data_check_scale_values(
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0], valid_values=[1, 2], infer=False
        )["problem"]
        is False
    )
    # a documented range that fits is still checked
    sv = data_check_scale_values([1.0, 2.0, 3.0, 4.0, 6.0], valid_values=[1, 5], infer=False)
    assert sv["problem"] is True
    assert sv["values"] == [6.0]


# ---------------------------------------------------------------------------
# .sav files in another encoding
# ---------------------------------------------------------------------------


def test_sav_with_invalid_text_is_read_again_as_cp1252(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pyreadstat = pytest.importorskip("pyreadstat")
    import pandas as pd

    calls: list[dict[str, Any]] = []
    real = pyreadstat.read_sav

    def fake(path: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        if "encoding" not in kwargs:
            raise pyreadstat.PyreadstatError(
                "Unable to convert string to the requested encoding (invalid byte sequence)"
            )
        return real(path, **{k: v for k, v in kwargs.items() if k != "encoding"})

    sav = tmp_path / "tiny.sav"
    pyreadstat.write_sav(pd.DataFrame({"a": [1.0, 2.0]}), str(sav))
    monkeypatch.setattr(pyreadstat, "read_sav", fake)
    df = read_stat_file(str(sav), "sav", 5)
    assert list(df.columns) == ["a"]
    assert calls[-1]["encoding"] == "CP1252"

    # an error of another kind is not retried
    def other(path: str, **kwargs: Any) -> Any:
        raise pyreadstat.PyreadstatError("Unable to read from file")

    monkeypatch.setattr(pyreadstat, "read_sav", other)
    with pytest.raises(pyreadstat.PyreadstatError):
        read_stat_file(str(sav), "sav", 5)
