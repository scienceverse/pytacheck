"""Contracts every built-in module must satisfy (the checks live in pytacheck.packs.check)."""

from __future__ import annotations

import pytest

import pytacheck as pc
from parity.cases import load_cases
from pytacheck.module import _builtin_names
from pytacheck.packs.check import metadata_issues, run_issues
from tests.httpmock import no_network

BUILTINS = list(_builtin_names())
OFFLINE = [
    m
    for m in BUILTINS
    if not {*pc.module_info(m).keywords, *pc.module_info(m).requires} & {"network", "llm"}
]


@pytest.mark.parametrize("name", BUILTINS)
def test_module_metadata(name: str) -> None:
    issues = metadata_issues(pc.module_info(name), name)
    problems = [str(i) for i in issues if i.level == "error" and i.code in ("name", "metadata")]
    assert not problems, problems


@pytest.mark.parametrize("name", OFFLINE)
def test_module_does_not_mutate_paper(name: str, demo: pc.Paper) -> None:
    # offline modules run offline: codebook_check runs data_check and repo_check
    # when it has no data_check output, and they look up the demo paper's OSF and
    # ResearchBox links; refused, they find no files and the run goes on
    with no_network():
        issues = run_issues(pc.module_info(name), [("demopaper()", demo)])
    if any(i.code == "run" for i in issues):
        pytest.skip(f"{name} needs resources not available offline")
    problems = [str(i) for i in issues if i.code in ("mutation", "traffic_light")]
    assert not problems, problems


def test_every_module_has_parity_cases() -> None:
    covered = {c.spec.get("module") for c in load_cases()}
    missing = sorted(set(BUILTINS) - covered)
    assert not missing, f"modules without parity cases: {missing}"
