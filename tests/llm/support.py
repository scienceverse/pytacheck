"""Shared constants and helpers for the LLM tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
MOCKS = HERE / "mocks"

KEYS = (
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "VLLM_API_KEY",
)


def load_json(name: str) -> Any:
    """A JSON fixture written by ``fixtures/make_fixtures.R``."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeChat:
    """Stands in for an ellmer ``Chat`` (R tests mock ``ellmer::chat()``)."""

    def __init__(self, chat: Any = None, chat_structured: Any = None) -> None:
        self._chat = chat
        self._structured = chat_structured

    def chat(self, text: Any) -> str:
        return self._chat(text)  # type: ignore[no-any-return]

    def chat_structured(self, text: Any, type: Any) -> Any:
        return self._structured(text, type)

    def last_turn(self) -> None:
        return None
