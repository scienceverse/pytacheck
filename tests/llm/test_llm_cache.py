"""The LLM reply cache: a folder of JSON files keyed by what was asked."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import metacheck.llm as L
from metacheck import _env
from metacheck._logging import get_logger
from metacheck.llm import _backend
from metacheck.llm import cache as C
from metacheck.utils import local_options
from tests.llm.support import Asked


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
    base = ("hi", "sys", None, "groq/x", {"temperature": 0.0})
    k1 = C._llm_cache_key(*base)
    assert k1 == C._llm_cache_key(*base)
    assert len(k1) == 64
    assert k1 != C._llm_cache_key("bye", "sys", None, "groq/x", {"temperature": 0.0})
    assert k1 != C._llm_cache_key("hi", "other", None, "groq/x", {"temperature": 0.0})
    assert k1 != C._llm_cache_key("hi", "sys", None, "groq/y", {"temperature": 0.0})
    assert k1 != C._llm_cache_key("hi", "sys", None, "groq/x", {"temperature": 1.0})
    assert k1 != C._llm_cache_key("hi", "sys", None, "groq/x", {"temperature": 0.0}, {"a": 1})
    assert k1 != C._llm_cache_key("hi", "sys", L.type_object(a=L.type_string()), "groq/x", {})
    # param order does not matter
    assert C._llm_cache_key("hi", "s", None, "m", {"a": 1, "b": 2}) == C._llm_cache_key(
        "hi", "s", None, "m", {"b": 2, "a": 1}
    )


def test_cache_put_get_round_trips_and_misses(cache_dir: Path) -> None:
    key = C._llm_cache_key("hi", "sys", None, "m", {})
    C._llm_cache_put(key, {"variables": [{"name": "x"}]})
    hit = C._llm_cache_get(key)
    assert hit is not None
    assert hit["reply"] == {"variables": [{"name": "x"}]}
    assert hit["version"] == 1
    assert C._llm_cache_get("no-such-key") is None
    assert C._llm_cache_path(key).parent == cache_dir / "json"


def test_llm_cache_clear_removes_entries_and_reports_count(cache_dir: Path) -> None:
    for i in range(1, 4):
        C._llm_cache_put(C._llm_cache_key(f"t{i}", "s", None, "m", {}), f"reply {i}")
    (cache_dir / "notes.txt").write_text("kept")
    (cache_dir / "metacheck-r-entry.rds").write_bytes(b"an R cache entry")  # never touched
    assert L.llm_cache_clear() == 3
    assert L.llm_cache_clear() == 0
    assert (cache_dir / "notes.txt").exists()
    assert (cache_dir / "metacheck-r-entry.rds").exists()


def test_unreadable_cache_entry_is_a_miss(cache_dir: Path) -> None:
    (cache_dir / "json").mkdir()
    (cache_dir / "json" / "deadbeef.json").write_text("{not json")
    assert C._llm_cache_get("deadbeef") is None
    (cache_dir / "json" / "cafe.json").write_text('{"no": "reply"}')
    assert C._llm_cache_get("cafe") is None


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


# ---------------------------------------------------------------------------
# llm() and the cache
# ---------------------------------------------------------------------------

TS = L.type_object(
    variables=L.type_array(
        L.type_object(
            variable_name=L.type_string("Exact variable name/code in the data file"),
            label=L.type_string("Verbatim description text from the codebook", required=False),
        )
    )
)


def test_the_second_call_is_answered_from_the_cache(
    llm_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = Asked(" TRUE ", "FALSE", "TRUE")
    monkeypatch.setattr(_backend, "complete", asked)
    with local_options({"metacheck.llm.cache": True}):
        first = L.llm(["12", "hello"], "A number?", model="groq/test-model")
        second = L.llm(["12", "hello"], "A number?", model="groq/test-model")
        other = L.llm(["12"], "A number? (another prompt)", model="groq/test-model")
    assert first["answer"].tolist() == ["TRUE", "FALSE"]
    assert second["answer"].tolist() == ["TRUE", "FALSE"]
    assert other["answer"].tolist() == ["TRUE"]
    assert len(asked.calls) == 3  # two for the first call, none for the second, one for `other`
    assert len(list((llm_on / "json").glob("*.json"))) == 3


def test_structured_replies_are_cached_as_json(
    llm_on: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = {"variables": [{"variable_name": "dv", "label": None}, {"variable_name": "iv"}]}
    asked = Asked(reply)
    monkeypatch.setattr(_backend, "complete", asked)
    with local_options({"metacheck.llm.cache": True}):
        a = L.llm("two vars", "Extract variables", type=TS, model="groq/test-model")
        b = L.llm("two vars", "Extract variables", type=TS, model="groq/test-model")
    assert len(asked.calls) == 1
    assert a["variables.variable_name"].tolist() == ["dv", "iv"]
    assert b.equals(a)
    (entry,) = list((llm_on / "json").glob("*.json"))
    assert '"variable_name":"dv"' in entry.read_text(encoding="utf-8").replace(" ", "")


def test_errors_are_never_cached(llm_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = Asked(_backend.LLMError("HTTP 500 Internal Server Error.", status=500), "ok")
    monkeypatch.setattr(_backend, "complete", asked)
    with local_options({"metacheck.llm.cache": True}):
        with pytest.warns(UserWarning, match="HTTP 500"):
            failed = L.llm("x", "p", model="groq/test-model")
        retried = L.llm("x", "p", model="groq/test-model")
    assert failed["error"].tolist() == [True]
    assert retried["answer"].tolist() == ["ok"]
    assert len(asked.calls) == 2


def test_a_cache_hit_needs_no_sdk_and_no_key(llm_on: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = C._llm_cache_key("hello", "A number?", None, "groq/test-model",
                           {"temperature": 0.0, "max_tokens": 4096}, {})  # fmt: skip
    C._llm_cache_put(key, "TRUE")
    monkeypatch.setattr(_backend, "complete", Asked())  # asked nothing: no reply to give
    with local_options({"metacheck.llm.cache": True}):
        out = L.llm("hello", "A number?", model="groq/test-model")
    assert out["answer"].tolist() == ["TRUE"]
