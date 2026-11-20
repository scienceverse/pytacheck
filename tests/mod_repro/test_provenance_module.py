"""What a run of reproducibility_check keeps for a TRO (metacheck 0.4.4.20, #404).

The module-level cases of ``test-provenance.R``: a deposit with data, an R script that reads
it and writes a file (with a ``setwd()`` the module removes) and an R script that fails, run
for real with ``sandbox = "process"`` (skipped without Rscript); and the same check without
running code, which keeps no provenance. The helpers are tested in
``tests/repro_core/test_provenance.py``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
import warnings
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import ModuleOutput, module_run

pytestmark = pytest.mark.filterwarnings("ignore::metacheck.core.errors.PytacheckWarning")


def deposit(root: Path) -> Path:
    (root / "data").mkdir()
    (root / "code").mkdir()
    rows = "\n".join(
        f"{g},{y}" for g, y in zip("ab" * 10, [*range(1, 11), *range(3, 13)], strict=True)
    )
    (root / "data" / "scores.csv").write_text("g,y\n" + rows + "\n", encoding="utf-8")
    (root / "code" / "analysis.R").write_text(
        'setwd("C:/Users/someone/project")\n'
        'x <- read.csv("data/scores.csv")\n'
        'write.csv(aggregate(y ~ g, x, mean), "means.csv", row.names = FALSE)\n',
        encoding="utf-8",
    )
    (root / "fails.R").write_text('stop("this script fails on purpose")\n', encoding="utf-8")
    return root


def run(d: Path, **kw: Any) -> ModuleOutput:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return module_run(
            pc.test_paper(),
            "reproducibility_check",
            local_path=str(d),
            local_only=True,
            **kw,
        )


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_a_run_keeps_run_times_file_hashes_and_the_environment(
    tmp_path: Path, rscript: str
) -> None:
    d = deposit(tmp_path)
    original = sha(d / "code" / "analysis.R")
    t0 = time.time()
    mo = run(d, execute=True, sandbox="process", timeout=60)

    rr = mo.run_results
    assert {"started_at", "ended_at", "exit_status"} <= set(rr.columns)
    by = rr.set_index("file_name")
    assert by.loc["analysis.R", "exit_status"] == 0
    assert by.loc["fails.R", "outcome"] == "errored"
    st = [dt.datetime.fromisoformat(v) for v in rr["started_at"]]
    en = [dt.datetime.fromisoformat(v) for v in rr["ended_at"]]
    assert all(a <= b for a, b in zip(st, en, strict=True))
    assert all(a.timestamp() >= t0 - 1 for a in st)

    a = mo.artifacts
    dep = a[a["stage"] == "deposit"]
    assert set(dep["path"]) == {"data/scores.csv", "code/analysis.R", "fails.R"}
    assert dep.set_index("path").loc["code/analysis.R", "sha256"] == original
    bef = a[a["stage"] == "before_run"]
    h = {s: x.set_index("path")["sha256"] for s, x in (("dep", dep), ("bef", bef))}
    assert h["bef"]["data/scores.csv"] == h["dep"]["data/scores.csv"]
    assert h["bef"]["code/analysis.R"] != original  # setwd() removed
    aft = a[a["stage"] == "after_run"]
    new = aft[aft["status"] == "new"]
    assert any(p.endswith("means.csv") for p in new["path"])
    assert not any(p.startswith("statistical_output/") for p in a["path"])

    sw = mo.modifications[mo.modifications["change_type"] == "setwd_removed"]
    assert sw["sha256_before"].iloc[0] == original
    assert sw["sha256_after"].iloc[0] == h["bef"]["code/analysis.R"]

    e = mo.environment
    assert e["sandbox"] == "process"
    assert e["network_isolated"] is False
    assert e["host"]["python_version"]
    # the whole of it can be written as JSON, for a TRO
    json.dumps([a.to_dict("records"), e], default=str)


def test_without_running_code_there_is_no_provenance(tmp_path: Path) -> None:
    d = deposit(tmp_path)
    mo = run(d, execute=False)
    assert mo.get("artifacts") is None
    assert mo.get("environment") is None
    rr = mo.get("run_results")
    assert not (isinstance(rr, pd.DataFrame) and "started_at" in rr.columns)
