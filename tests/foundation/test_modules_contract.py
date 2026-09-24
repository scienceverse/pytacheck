"""Contracts every built-in module must satisfy."""

from __future__ import annotations

import copy

import pandas as pd
import pytest
from parity.cases import load_cases

import pytacheck as pc
from pytacheck.module import TRAFFIC_LIGHTS, _builtin_names

BUILTINS = list(_builtin_names())
OFFLINE = [m for m in BUILTINS if not set(pc.module_info(m).keywords) & {"network", "llm"}]


def _fingerprint(paper: pc.Paper) -> dict:
    return {
        k: (
            v.to_json(orient="split", default_handler=str)
            if isinstance(v, pd.DataFrame)
            else copy.deepcopy(v)
        )
        for k, v in paper.items()
    }


@pytest.mark.parametrize("name", BUILTINS)
def test_module_metadata(name: str) -> None:
    spec = pc.module_info(name)
    assert spec.name == name, "module function must be named like its file"
    assert spec.title and spec.description


@pytest.mark.parametrize("name", OFFLINE)
def test_module_does_not_mutate_paper(name: str, demo: pc.Paper) -> None:
    before = _fingerprint(demo)
    try:
        out = pc.module_run(demo, name)
    except pc.ModuleError:
        pytest.skip(f"{name} needs resources not available offline")
    assert out.traffic_light in TRAFFIC_LIGHTS
    assert _fingerprint(demo) == before, f"{name} mutated its input paper"


def test_every_module_has_parity_cases() -> None:
    covered = {c.spec.get("module") for c in load_cases()}
    missing = sorted(set(BUILTINS) - covered)
    assert not missing, f"modules without parity cases: {missing}"
