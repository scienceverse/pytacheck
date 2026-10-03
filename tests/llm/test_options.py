"""Package option defaults (R/zzz.R ``.onLoad()``) and ``verbose()`` (R/svutils-utils.R)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from typing import Any

import pytest


def test_llm_option_defaults_exist_before_llm_is_imported() -> None:
    # R: library(metacheck) sets metacheck.llm_max_calls = 30L and
    # metacheck.llm.use = FALSE, so getOption() sees them before any LLM code runs
    from metacheck.llm import core

    # no provider keys: with one set, metacheck.llm.core sets the default
    # metacheck.llm.model when it loads (as .onLoad() does), changing the memo key
    keys = {env for _, env in core._API_KEY_ENV}
    env = {k: v for k, v in os.environ.items() if k not in keys}
    code = (
        "import json, sys\n"
        "import metacheck.utils as u\n"
        "from metacheck.module import _memo_options\n"
        "n = u.get_option('metacheck.llm_max_calls')\n"
        "before = _memo_options()\n"
        "loaded = 'metacheck.llm.core' in sys.modules\n"
        "import metacheck.llm.core\n"
        "print(json.dumps([n, type(n).__name__, u.get_option('metacheck.llm.use'), loaded,"
        " before == _memo_options()]))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env=env
    ).stdout
    n, cls, use, loaded, same = json.loads(out.strip().splitlines()[-1])
    assert (n, cls, use) == (30, "RInt", False)  # 30L: an R integer
    assert not loaded
    assert same  # the module memo key does not change when metacheck.llm loads


@pytest.fixture
def restore_verbose() -> Iterator[None]:
    from metacheck import config

    saved = config._state.get("verbose", None)
    try:
        yield
    finally:
        if saved is None:
            config._state.pop("verbose", None)
        else:
            config._state["verbose"] = saved


# R 4.5 / metacheck: verbose(x) sets as.logical(x); x whose as.logical() is NA stops
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("FALSE", False),
        ("false", False),
        ("F", False),
        ("False", False),
        ("TRUE", True),
        ("true", True),
        ("T", True),
        ("True", True),
        (0, False),
        (2, True),
        (0.5, True),
        (0.0, False),
        (True, True),
        (False, False),
        ([True], True),  # verbose(list(TRUE))
        (("F",), False),
    ],
)
def test_verbose_converts_as_r(value: Any, expected: bool, restore_verbose: None) -> None:
    from metacheck.config import verbose

    verbose(not expected)
    assert verbose(value) is expected
    assert verbose() is expected


@pytest.mark.parametrize("value", [" TRUE", "yes", "0", "1", "", float("nan"), [None]])
def test_verbose_refuses_what_r_refuses(value: Any, restore_verbose: None) -> None:
    from metacheck.config import verbose

    verbose(True)
    with pytest.raises(ValueError, match=r"^set verbose with TRUE or FALSE$"):
        verbose(value)
    assert verbose() is True  # unchanged


def test_verbose_vector_lengths(restore_verbose: None) -> None:
    from metacheck.config import verbose

    with pytest.raises(ValueError, match=r"^the condition has length > 1$"):
        verbose([True, False])
    with pytest.raises(ValueError, match=r"^argument is of length zero$"):
        verbose([])


def test_verbose_numpy_scalars(restore_verbose: None) -> None:
    import numpy as np

    from metacheck.config import verbose

    assert verbose(np.bool_(False)) is False
    assert verbose(np.int64(3)) is True
    assert verbose(np.float64(0.0)) is False


def test_verbose_complex_and_arrays(restore_verbose: None) -> None:
    # R 4.5: verbose(1i) is TRUE, verbose(0i) FALSE, verbose(complex(real = NaN))
    # stops; verbose(matrix(TRUE)) is TRUE; c(TRUE, FALSE) and numeric(0) fail in if ()
    import numpy as np
    import pandas as pd

    from metacheck.config import verbose

    assert verbose(1j) is True
    assert verbose(0j) is False
    with pytest.raises(ValueError, match=r"^set verbose with TRUE or FALSE$"):
        verbose(complex(float("nan"), 0))
    assert verbose(np.array([[True]])) is True
    assert verbose(pd.Series([False])) is False
    with pytest.raises(ValueError, match=r"^the condition has length > 1$"):
        verbose(np.array([True, False]))
    with pytest.raises(ValueError, match=r"^argument is of length zero$"):
        verbose(np.array([]))


def test_github_pat_is_not_a_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # U20: metacheck's .onLoad() picks the defunct "github" provider when only
    # GITHUB_PAT is set, so every LLM call fails
    from metacheck.llm import core

    for _, env in core._API_KEY_ENV:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GITHUB_PAT", "ghp_x")
    assert core._default_model_from_env() is None
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert core._default_model_from_env() == "groq"


def test_llm_timeout_bounds_hosted_providers() -> None:
    # U20: ellmer's providers waited 300 s whatever llm_timeout() said
    from metacheck import utils
    from metacheck.llm import core, providers

    old_timeout = core.llm_timeout()
    old_opt = utils.get_option("ellmer_timeout_s")
    try:
        core.llm_timeout(42)
        utils.options({"ellmer_timeout_s": None})
        assert providers._timeout_default() == 42.0
        utils.options({"ellmer_timeout_s": 7})
        assert providers._timeout_default() == 7.0
    finally:
        core.llm_timeout(old_timeout)
        utils.options({"ellmer_timeout_s": old_opt})
