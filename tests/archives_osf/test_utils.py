"""Port of metacheck's tests/testthat/test-utils.R and test-svutils-utils.R
(the parts in pytacheck.utils; ``.batch_query``/``email``/``verbose`` live in
the foundation and are tested there)."""

from __future__ import annotations

import socket
import warnings

import pandas as pd
import pytest

from pytacheck import utils
from pytacheck.utils import (
    _col_chr,
    _safe_write_path,
    get_option,
    left_join,
    local_options,
    match_arg,
    online,
    options,
    path_sanitize,
    rep_if,
)


def test_path_sanitize() -> None:
    path = " has/ spaces/\\backslashes/><|?chars/.dot.is.ok "
    assert path_sanitize(path) == "has/_spaces/_backslashes/_chars/.dot.is.ok"
    assert path_sanitize(path, replacement="~") == "has/~spaces/~backslashes/~chars/.dot.is.ok"
    assert (
        path_sanitize(path, remove_whitespace=False) == "has/ spaces/_backslashes/_chars/.dot.is.ok"
    )
    assert path_sanitize(path, keep_sep=False) == "has_spaces_backslashes_chars_.dot.is.ok"
    assert path_sanitize(["a b", None]) == ["a_b", None]
    assert path_sanitize("/My Files/x><y.pdf") == "/My_Files/x_y.pdf"


def test_rep_if() -> None:
    assert rep_if([], 1) == []
    assert rep_if([None, 2], 1) == [1, 2]
    assert rep_if([float("nan"), 2], 1, float("nan")) == [1, 2]
    assert rep_if(["", 2], 1, "") == [1, 2]
    assert rep_if(["", 2, None], 1, ["", None]) == [1, 2, 1]
    assert rep_if([1, None, 3, None, None], list(range(1, 6))) == [1, 2, 3, 4, 5]
    assert rep_if(["", "B", "X"], ["A", "B", "C"], ["", "X"]) == ["A", "B", "C"]


def test_online(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_lookup(host: str, *args: object) -> list[object]:
        seen.append(host)
        if host.endswith("google.com"):
            return [object()]
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", fake_lookup)
    assert online() is True
    for url in [
        "google.com",
        "http://google.com",
        "https://google.com",
        "https://www.google.com",
        "https://google.com/images",
    ]:
        assert online(url) is True
    assert seen[-1] == "google.com"
    seen.clear()
    assert online("notasite", tries=3, wait=0) is False
    assert seen == ["notasite"] * 3


def test_options() -> None:
    assert get_option("metacheck.osf.api") == "https://api.osf.io/v2"
    assert get_option("metacheck.osf.delay") == 0
    assert get_option("no.such.option", "dflt") == "dflt"
    old = options({"metacheck.test": 1})
    assert old == {"metacheck.test": None}
    assert get_option("metacheck.test") == 1
    with local_options({"metacheck.test": 2, "metacheck.other": "x"}):
        assert get_option("metacheck.test") == 2
        assert get_option("metacheck.other") == "x"
    assert get_option("metacheck.test") == 1
    assert get_option("metacheck.other") is None
    options(metacheck_test=None)
    assert get_option("metacheck.test") is None


def test_match_arg() -> None:
    assert match_arg(None, ["stop", "warn"]) == "stop"
    assert match_arg("w", ["stop", "warn"]) == "warn"
    with pytest.raises(ValueError, match="'arg' should be one of “stop”, “warn”"):
        match_arg("x", ["stop", "warn"])


def test_col_chr() -> None:
    assert _col_chr(pd.DataFrame({"a": [1.5, 2.0]}), "a") == ["1.5", "2"]
    assert _col_chr(pd.DataFrame({"a": [1, 2]}), "b") == [None, None]
    assert _col_chr(pd.DataFrame({"a": [1]}), "b", default="x") == ["x"]
    assert _col_chr(pd.DataFrame({"a": pd.Series([], dtype="string")}), "a") == []
    df = pd.DataFrame({"a": [None, "x", ["y", "z"], [], 3, ("w",)]})
    assert _col_chr(df, "a") == [None, "x", None, None, "3", "w"]


def test_safe_write_path() -> None:
    assert _safe_write_path("some/dir/file.csv") == "some/dir/file.csv"
    assert _safe_write_path("") == ""
    assert _safe_write_path(None) is None
    long = "dir/" + "a" * 300 + ".txt"
    with pytest.warns(UserWarning, match="shortened"):
        short = _safe_write_path(long)
    assert short is not None
    leaf = short.split("/")[-1]
    assert len(leaf) == 255 and leaf.endswith(".txt")
    # deterministic, and unique per original name
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert _safe_write_path(long) == short
        assert _safe_write_path("dir/" + "b" * 300 + ".txt") != short


def test_left_join() -> None:
    x = pd.DataFrame({"id": [100], "osf_id": ["pngda"], "type": ["a"]})
    y = pd.DataFrame({"osf_id": ["pngda"], "name": ["P"], "osf_url": ["pngda"], "type": ["b"]})
    out = left_join(x, y, by={"osf_id": "osf_url"}, suffix=("", ".osf"))
    assert list(out.columns) == ["id", "osf_id", "type", "osf_id.osf", "name", "type.osf"]
    # NA keys match NA keys; many-to-many rows are repeated in x's order
    a = pd.DataFrame({"a": ["x", None, "x"]})
    b = pd.DataFrame({"a": ["x", "x", None], "b": [1, 2, 3]})
    out = left_join(a, b, by="a")
    assert out["b"].tolist() == [1, 2, 3, 1, 2]
    # unmatched rows get NA with the column's type kept
    out = left_join(pd.DataFrame({"k": [1, 2]}), pd.DataFrame({"k": [1.0], "v": [True]}), by="k")
    assert out["v"].tolist()[0] is True and pd.isna(out["v"].iloc[1])


def test_utils_module_exports() -> None:
    assert set(utils.__all__) >= {"path_sanitize", "rep_if", "online", "get_option", "options"}
