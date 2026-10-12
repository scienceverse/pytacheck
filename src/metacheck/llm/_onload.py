"""The default model metacheck's ``.onLoad()`` picks (R/zzz.R).

Stdlib-only, so :mod:`metacheck.utils` can apply it when it loads without
importing the LLM code.
"""

from __future__ import annotations

import os

#: Environment variables .onLoad() checks to pick the default model provider.
_API_KEY_ENV = (
    ("ollama", "OLLAMA_BASE_URL"),
    ("groq", "GROQ_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("google_gemini", "GEMINI_API_KEY"),
    ("google_gemini", "GOOGLE_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("cloudflare", "CLOUDFLARE_API_KEY"),
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("huggingface", "HUGGINGFACE_API_KEY"),
    ("mistral", "MISTRAL_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("perplexity", "PERPLEXITY_API_KEY"),
    ("portkey", "PORTKEY_API_KEY"),
    ("azure_openai", "AZURE_OPENAI_ENDPOINT"),
    ("databricks", "DATABRICKS_HOST"),
    # not ("github", "GITHUB_PAT"): ellmer's chat_github() is defunct, and
    # GITHUB_PAT is set for many other reasons; metacheck still picks it as the
    # default model, so every LLM call then fails (U20)
)


def _default_model_from_env() -> str | None:
    for name, env in _API_KEY_ENV:
        if os.environ.get(env, ""):
            return name
    return None
