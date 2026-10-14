"""A hosted run's process that runs R (imported by the spawned process of the test)."""

from __future__ import annotations

from metacheck import rengine


def run_r(paper: str) -> object:
    return rengine.run(paper, ["marginal"])
