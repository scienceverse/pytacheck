"""Fixtures for the power-module tests: LLM options on, fake keys, cache off."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
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


class FakeChat:
    """Stands in for an ellmer ``Chat`` (the R tests mock ``ellmer::chat()``)."""

    def __init__(
        self,
        chat_structured: Callable[[str, Any], Any] | None = None,
        chat: Callable[[str], str] | None = None,
    ) -> None:
        self._structured = chat_structured
        self._chat = chat

    def chat_structured(self, text: str, type: Any) -> Any:
        if self._structured is None:
            raise RuntimeError("attempt to apply non-function")
        return self._structured(text, type)

    def chat(self, text: str) -> str:
        if self._chat is None:
            raise RuntimeError("attempt to apply non-function")
        return self._chat(text)

    def last_turn(self) -> None:
        return None


def structured_lookup(lookup: Mapping[str, Any]) -> FakeChat:
    """R ``.mock_structured_chat(lookup)``: reply by the first key found in the text."""

    def reply(text: str, type: Any) -> Any:
        for key, value in lookup.items():
            if key in text:
                return value
        return {"power_analyses": []}

    return FakeChat(chat_structured=reply)


@pytest.fixture
def mock_chat(monkeypatch: pytest.MonkeyPatch) -> Callable[[FakeChat], None]:
    """``local_mocked_bindings(chat = ..., .package = "ellmer")``."""
    from pytacheck.llm import providers

    def install(fake: FakeChat) -> None:
        monkeypatch.setattr(providers, "chat", lambda *a, **k: fake)

    return install
