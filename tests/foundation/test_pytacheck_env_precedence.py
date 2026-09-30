"""An option or an argument comes before both names of a variable.

The order at a call site that also has an option or an argument is: the argument or
option, then the variables in table order, then the default. Each case here sets both
names of the variable to something else and shows that the option or argument still wins.

The file name is excluded by the rename guard (``tests/*test_pytacheck_*.py``): the options
of the language model and of the R version still carry the old spelling, which this file
may name. (Their new spelling, with the old one as an alias, comes with the option commit.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from metacheck import _env, utils
from metacheck._env import ENV_VARS


@pytest.fixture
def option(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Set options on an empty store; every name of the table is unset first."""
    _env._reset_warned()
    for var in ENV_VARS.values():
        for name in var.names:
            monkeypatch.delenv(name, raising=False)
    store: dict[str, Any] = {}
    monkeypatch.setattr(utils, "_options", store)
    return store.update


def _both(monkeypatch: pytest.MonkeyPatch, key: str, first: str, second: str) -> None:
    new, old = ENV_VARS[key].names
    monkeypatch.setenv(new, first)
    monkeypatch.setenv(old, second)


def test_the_cache_option_comes_before_the_cache_variable(
    option: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from metacheck.archives.cache import _metacheck_cache_root

    _both(monkeypatch, "CACHE_DIR", str(tmp_path / "new"), str(tmp_path / "old"))
    assert _metacheck_cache_root() == str(tmp_path / "new")
    option({"metacheck.cache.dir": str(tmp_path / "option")})
    assert _metacheck_cache_root() == str(tmp_path / "option")


def test_the_rscript_argument_comes_before_the_rscript_variable(
    option: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.statout.r_capture import _rscript

    _both(monkeypatch, "RSCRIPT", "/new/Rscript", "/old/Rscript")
    assert _rscript() == "/new/Rscript"
    assert _rscript("/given/Rscript") == "/given/Rscript"


@pytest.mark.parametrize("engine", ["python", "r"])
def test_the_engine_argument_comes_before_the_parser_variable(
    option: Any, monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    from metacheck.codecheck import _rparse

    monkeypatch.setattr(_rparse, "rscript", lambda search_path=True: "/fake/Rscript")
    monkeypatch.setattr(_rparse, "_parse_errors_r", lambda texts, script: ["from R"])
    other = "python" if engine == "r" else "r"
    _both(monkeypatch, "R_PARSER", other, other)
    wanted = ["from R"] if engine == "r" else [_rparse._parse_error_py(["x <- "])]
    assert _rparse.parse_errors([["x <- "]], engine=engine) == wanted
    assert _rparse.parse_errors([["x <- "]]) != wanted  # the variable alone picks the other


def test_the_serialize_version_option_comes_before_the_variable(
    option: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.llm._rds import _r_version_int

    _both(monkeypatch, "R_SERIALIZE_VERSION", "4.4.1", "4.4.2")
    assert _r_version_int() == 4 * 65536 + 4 * 256 + 1
    option({"pytacheck.r_serialize_version": "4.3.0"})
    assert _r_version_int() == 4 * 65536 + 3 * 256


def test_the_workers_option_comes_before_the_workers_variable(
    option: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.llm.core import _llm_workers

    _both(monkeypatch, "LLM_WORKERS", "4", "5")
    assert _llm_workers() == 4
    option({"pytacheck.llm.workers": 3})
    assert _llm_workers() == 3
