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
    """Stands in for the model (R tests mock ``ellmer::chat()``)."""

    def __init__(self, chat: Any = None, chat_structured: Any = None) -> None:
        self._chat = chat
        self._structured = chat_structured

    def chat(self, text: Any) -> str:
        return self._chat(text)  # type: ignore[no-any-return]

    def chat_structured(self, text: Any, type: Any) -> Any:
        return self._structured(text, type)

    def complete(
        self,
        model: str,
        system: str,
        user: str,
        type: Any = None,
        params: Any = None,
        api_args: Any = None,
    ) -> Any:
        """The seam :func:`metacheck.llm._backend.complete`: the reply, as the model's JSON."""
        return self.chat(user) if type is None else self.chat_structured(user, type)


class Asked:
    """A stand-in for :func:`metacheck.llm._backend.complete` that records what it is asked.

    It answers with *replies* in order (an exception in the list is raised).
    ``calls`` holds ``(user, type)`` and ``asked`` the whole request of each call.
    """

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, Any]] = []
        self.asked: list[dict[str, Any]] = []

    def __call__(
        self,
        model: str,
        system: str,
        user: str,
        type: Any = None,
        params: Any = None,
        api_args: Any = None,
    ) -> Any:
        self.calls.append((user, type))
        self.asked.append(
            {"model": model, "system": system, "user": user, "type": type,
             "params": dict(params or {}), "api_args": dict(api_args or {})}
        )  # fmt: skip
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
