"""Test doubles for the power-module tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


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
