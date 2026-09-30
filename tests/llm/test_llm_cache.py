"""Port of tests/testthat/test-llm-cache.R, plus R <-> Python cache compatibility."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pandas as pd
import pytest

import metacheck.llm as L
from metacheck import _env
from metacheck._logging import get_logger
from metacheck.llm import cache as C
from metacheck.llm._rds import EllmerOutput, read_rds, to_python
from metacheck.utils import local_options
from tests.httpmock import replay
from tests.llm.support import FIXTURES, MOCKS

UPSTREAM_CACHE = (
    Path(__file__).resolve().parents[2]
    / "upstream"
    / "metacheck"
    / "tests"
    / "testthat"
    / ".metacheck_llm_cache"
)


@pytest.fixture
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "llmcache"
    d.mkdir()
    monkeypatch.setenv("METACHECK_LLM_CACHE_DIR", str(d))
    monkeypatch.delenv("PYTACHECK_LLM_CACHE_DIR", raising=False)
    return d


def test_llm_cache_toggles_and_validates() -> None:
    with local_options({"metacheck.llm.cache": True}):
        assert L.llm_cache() is True
        assert L.llm_cache(False) is False
        assert L.llm_cache() is False
        L.llm_cache(True)
        assert L.llm_cache() is True
        with pytest.raises(ValueError, match="TRUE or FALSE"):
            L.llm_cache("yes")  # type: ignore[arg-type]
    with local_options({"metacheck.llm.cache": None}):
        assert L.llm_cache() is True  # the default


def test_cache_key_is_stable_and_sensitive() -> None:
    k1 = C._llm_cache_key("hi", "sys", None, "groq/x", {"temperature": 0.0})
    k2 = C._llm_cache_key("hi", "sys", None, "groq/x", {"temperature": 0.0})
    assert k1 == k2
    assert k1 != C._llm_cache_key("bye", "sys", None, "groq/x", {"temperature": 0.0})
    assert k1 != C._llm_cache_key("hi", "other", None, "groq/x", {"temperature": 0.0})
    assert k1 != C._llm_cache_key("hi", "sys", None, "groq/y", {"temperature": 0.0})
    assert k1 != C._llm_cache_key("hi", "sys", None, "groq/x", {"temperature": 1.0})
    # param order does not matter
    assert C._llm_cache_key("hi", "s", None, "m", {"a": 1, "b": 2}) == C._llm_cache_key(
        "hi", "s", None, "m", {"b": 2, "a": 1}
    )


def test_cache_key_matches_r() -> None:
    # metacheck:::.llm_cache_key("hi", "sys", NULL, "groq/x", list(temperature = 0))
    assert (
        C._llm_cache_key("hi", "sys", None, "groq/x", {"temperature": 0.0})
        == "801e1462f98e73b5976e98d0c633a719"
    )


def test_cache_put_get_round_trips_and_misses(cache_dir: Path) -> None:
    key = C._llm_cache_key("hi", "sys", None, "m", {})
    df = pd.DataFrame(
        {
            "answer": pd.array(["yes"], dtype="string"),
            ".join_key.": pd.array(["hi"], dtype="string"),
        }
    )
    C._llm_cache_put(key, df, raw={"reasoning": "because"})
    hit = C._llm_cache_get(key)
    assert hit is not None
    pd.testing.assert_frame_equal(hit["df"], df)
    assert hit["raw"]["reasoning"] == "because"
    assert hit["version"] == 1
    assert C._llm_cache_get("no-such-key") is None
    assert Path(C._llm_cache_path(key)).parent == cache_dir


def test_llm_cache_clear_removes_entries_and_reports_count(cache_dir: Path) -> None:
    for i in range(1, 4):
        k = C._llm_cache_key(f"t{i}", "s", None, "m", {})
        C._llm_cache_put(k, pd.DataFrame({"x": [i]}))
    (cache_dir / "notes.txt").write_text("kept")
    assert L.llm_cache_clear() == 3
    assert L.llm_cache_clear() == 0
    assert (cache_dir / "notes.txt").exists()


def test_cache_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("METACHECK_LLM_CACHE_DIR", str(tmp_path / "m"))
    monkeypatch.delenv("PYTACHECK_LLM_CACHE_DIR", raising=False)
    assert Path(C._llm_cache_dir()) == (tmp_path / "m").resolve()
    monkeypatch.setenv("PYTACHECK_LLM_CACHE_DIR", str(tmp_path / "p"))
    assert Path(C._llm_cache_dir()) == (tmp_path / "p").resolve()
    monkeypatch.delenv("PYTACHECK_LLM_CACHE_DIR")
    monkeypatch.delenv("METACHECK_LLM_CACHE_DIR")
    with local_options({"metacheck.cache.dir": str(tmp_path / "root")}):
        assert Path(C._llm_cache_dir()) == (tmp_path / "root" / ".metacheck_llm_cache").resolve()


def test_cache_dir_env_with_both_names_set_logs_only_a_debug_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # this package's name wins; the other one is R's shared name, so a different setting
    # there is expected and is no warning
    _env._reset_warned()
    monkeypatch.setenv("METACHECK_LLM_CACHE_DIR", str(tmp_path / "m"))
    monkeypatch.setenv("PYTACHECK_LLM_CACHE_DIR", str(tmp_path / "p"))
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert Path(C._llm_cache_dir()) == (tmp_path / "p").resolve()
        assert Path(C._llm_cache_dir()) == (tmp_path / "p").resolve()
    records = [r for r in caplog.records if r.name == get_logger().name]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (
            logging.DEBUG,
            "PYTACHECK_LLM_CACHE_DIR is set, so METACHECK_LLM_CACHE_DIR is ignored",
        )
    ]
    assert str(tmp_path) not in caplog.text


def test_unreadable_cache_entry_is_a_miss(cache_dir: Path) -> None:
    (cache_dir / "deadbeef.rds").write_bytes(b"not an rds file")
    assert C._llm_cache_get("deadbeef") is None


# ---------------------------------------------------------------------------
# sharing a cache with metacheck
# ---------------------------------------------------------------------------

TS = L.type_object(
    variables=L.type_array(
        L.type_object(
            variable_name=L.type_string("Exact variable name/code in the data file"),
            label=L.type_string("Verbatim description text from the codebook", required=False),
            kind=L.type_enum(["a", "b", None], "Kind", required=False),
        )
    )
)


def test_python_replays_a_cache_written_by_r(llm_on: Path) -> None:
    """tests/llm/fixtures/r_cache was written by metacheck (make_r_cache.R)."""
    for f in (FIXTURES / "r_cache").glob("*.rds"):
        shutil.copy(f, llm_on / f.name)
    with local_options({"metacheck.llm.cache": True}), replay(FIXTURES / "no-such-mocks"):
        plain = L.llm(
            ["hello", "12"], "Is this a number? Answer TRUE or FALSE", model="groq/test-model"
        )
        structured = L.llm(
            ["two vars", "none"],
            "Extract variables",
            type=TS,
            model="groq/openai/gpt-oss-20b",
            capture_reasoning=True,
        )
    # every call was a cache hit: a request would have failed (no fixtures)
    assert "error" not in plain.columns
    assert plain["answer"].tolist() == ["FALSE", "TRUE"]
    assert ".error" not in structured.columns
    assert structured["variables.variable_name"].tolist()[:2] == ["dv", "iv"]
    assert structured[".reasoning"].tolist()[0] == "Found two variables: dv and iv."


def test_python_writes_the_keys_r_writes(llm_on: Path) -> None:
    with local_options({"metacheck.llm.cache": True}), replay(MOCKS):
        L.llm(["hello", "12"], "Is this a number? Answer TRUE or FALSE", model="groq/test-model")
        L.llm(
            ["two vars", "none"],
            "Extract variables",
            type=TS,
            model="groq/openai/gpt-oss-20b",
            capture_reasoning=True,
        )
    ours = sorted(p.name for p in llm_on.glob("*.rds"))
    theirs = sorted(p.name for p in (FIXTURES / "r_cache").glob("*.rds"))
    assert ours == theirs
    # the entries hold what R's hold
    for name in ours:
        a = to_python(read_rds(llm_on / name))
        b = to_python(read_rds(FIXTURES / "r_cache" / name))
        assert set(a) == set(b) == {"df", "raw", "thinking", "created", "version"}
        if isinstance(b["df"], dict):
            assert a["df"] == b["df"]
            assert isinstance(a["df"]["answer"], EllmerOutput)
        else:
            pd.testing.assert_frame_equal(a["df"], b["df"])
        assert a["thinking"] == b["thinking"]


def test_upstream_cache_fixtures_are_readable() -> None:
    files = sorted(UPSTREAM_CACHE.glob("*.rds"))
    if not files:
        pytest.skip("upstream cache fixtures not available")
    for f in files:
        entry = to_python(read_rds(f))
        assert entry["version"] == 1
        assert list(entry["df"].columns) == ["assignments.index", "assignments.group", ".join_key."]
        assert entry["df"]["assignments.index"].dtype == "Int64"
