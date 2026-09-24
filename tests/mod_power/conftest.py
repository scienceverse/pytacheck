"""Fixtures for the power-module tests: LLM options on, fake keys, cache off."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def llm_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """``llm_use(TRUE)``, ``llm_model("groq/llama-3.3-70b-versatile")``, cache off."""
    from pytacheck.llm._rds import RInt
    from pytacheck.utils import local_options

    monkeypatch.setenv("METACHECK_LLM_CACHE_DIR", str(tmp_path / "llmcache"))
    monkeypatch.delenv("PYTACHECK_LLM_CACHE_DIR", raising=False)
    monkeypatch.delenv("PYTACHECK_LLM_WORKERS", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with local_options(
        {
            "metacheck.llm.use": True,
            "metacheck.llm.cache": False,
            "metacheck.llm.model": "groq/llama-3.3-70b-versatile",
            "metacheck.llm_reasoning": None,
            "metacheck.llm_max_tokens": None,
            "metacheck.llm_max_calls": RInt(30),
        }
    ):
        yield


@pytest.fixture
def llm_off() -> Iterator[None]:
    """``llm_use(FALSE)``."""
    from pytacheck.utils import local_options

    with local_options({"metacheck.llm.use": False}):
        yield


@pytest.fixture
def mock_chat(monkeypatch: pytest.MonkeyPatch) -> Callable[[Any], None]:
    """``local_mocked_bindings(chat = ..., .package = "ellmer")``."""
    from pytacheck.llm import providers

    def install(fake: Any) -> None:
        monkeypatch.setattr(providers, "chat", lambda *a, **k: fake)

    return install
