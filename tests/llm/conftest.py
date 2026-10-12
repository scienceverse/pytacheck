"""Fixtures for the LLM tests: options on, fake API keys, an isolated cache."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.llm.support import KEYS


@pytest.fixture
def llm_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """``llm_use(TRUE)``, cache off (in a temp dir), fake keys, default settings."""
    from metacheck.utils import local_options

    cache = tmp_path / "llmcache"
    cache.mkdir()
    monkeypatch.setenv("METACHECK_LLM_CACHE_DIR", str(cache))
    monkeypatch.delenv("PYTACHECK_LLM_CACHE_DIR", raising=False)
    for key in ("GOOGLE_API_KEY", "ANTHROPIC_BASE_URL", "OLLAMA_BASE_URL", "PYTACHECK_LLM_WORKERS"):
        monkeypatch.delenv(key, raising=False)
    for key in KEYS:
        monkeypatch.setenv(key, "test-key")
    with local_options(
        {
            "metacheck.llm.use": True,
            "metacheck.llm.cache": False,
            "metacheck.llm_reasoning": None,
            "metacheck.llm_max_tokens": None,
            "metacheck.llm_max_calls": 30,
            "metacheck.llm.vllm.base_url": None,
        }
    ):
        yield cache


@pytest.fixture
def restore_llm_options() -> Iterator[None]:
    """Restore every LLM option a test changes."""
    from metacheck.llm.core import _init_options
    from metacheck.utils import local_options

    _init_options()
    names = [
        "metacheck.llm.use",
        "metacheck.llm.model",
        "metacheck.llm_max_calls",
        "metacheck.llm_max_tokens",
        "metacheck.llm_timeout",
        "metacheck.llm_reasoning",
        "metacheck.llm.cache",
    ]
    from metacheck.utils import get_option

    with local_options({n: get_option(n) for n in names}):
        yield
