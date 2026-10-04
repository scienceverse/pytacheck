"""scripts/llm_power_before_after.py and scripts/llm_power_compare.py: the power before/after measurement.

The runner is tried with a fake back end (no key, no network), the comparison on small files.
"""

from __future__ import annotations

import functools
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import metacheck as pc

REPO = Path(__file__).resolve().parents[2]

SIMPLE = (
    "An a priori power analysis indicated 64 participants per group, d = 0.5, alpha = .05, "
    "power = 80%, using an unpaired t-test, run with pwr."
)
ANALYSIS = {
    "power_type": "apriori",
    "statistical_test": "unpaired t-test",
    "statistical_test_other": None,
    "sample_size": 128,
    "alpha_level": 0.05,
    "power": 0.8,
    "effect_size": 0.5,
    "effect_size_metric": "Cohen's d",
    "effect_size_metric_other": None,
    "software": "pwr",
}


#: what run() sets for the process it is run as; the tests give it back afterwards
RUN_OPTIONS = {"metacheck.llm.use": False, "metacheck.llm.model": None, "metacheck.llm.cache": True}


@functools.cache
def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner() -> ModuleType:
    return _script("llm_power_before_after")


@pytest.fixture
def compare() -> ModuleType:
    return _script("llm_power_compare")


def _write_paper(folder: Path) -> Path:
    paper = pc.test_paper(SIMPLE)
    return Path(str(pc.paper_write(paper, "paper.json", save_path=folder)))


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_runner_writes_a_header_and_a_line_per_paper(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.llm import _backend
    from metacheck.utils import local_options

    seen: list[Any] = []

    def complete(
        model: str,
        system: str,
        user: str,
        type: Any = None,
        params: Any = None,
        api_args: Any = None,
    ) -> Any:
        seen.append((model, params))
        return {"power_analyses": [ANALYSIS]}

    monkeypatch.setattr(_backend, "complete", complete)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("METACHECK_LLM_CACHE_DIR", str(tmp_path / "cache"))
    paper = _write_paper(tmp_path)
    out = tmp_path / "out" / "after.jsonl"
    args = runner.parse_args(
        ["--model", "groq/llama-3.3-70b-versatile", "--out", str(out), "--seed", "7", str(paper)]
    )
    with local_options(RUN_OPTIONS):
        assert runner.run(args) == 0

    header, record = _lines(out)
    assert header["kind"] == "run"
    assert header["model"] == "groq/llama-3.3-70b-versatile"
    assert header["seed"] == 7
    assert header["versions"]["metacheck"] is not None
    assert header["label"]

    assert record["kind"] == "paper"
    assert record["error"] is None
    assert record["path"] == "structured"
    assert record["traffic_light"] == "green"
    assert record["model"] == "groq/llama-3.3-70b-versatile"
    assert record["n_candidates"] == 1
    (row,) = record["rows"]
    assert row["power_type"] == "apriori"
    assert row["sample_size"] == 128
    assert row["alpha_level"] == 0.05
    assert row["statistical_test_other"] is None
    assert row["text_head"].startswith("An a priori power analysis")
    assert "paper_id" not in row  # what the model did not say stays out of the row
    assert seen[0][0] == "groq/llama-3.3-70b-versatile"
    assert seen[0][1]["seed"] == 7


def test_runner_records_a_failed_llm_check(
    runner: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.llm import _backend
    from metacheck.utils import local_options

    def complete(*args: Any, **kwargs: Any) -> Any:
        raise _backend.LLMError("HTTP 401 Unauthorized.", status=401)

    monkeypatch.setattr(_backend, "complete", complete)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("METACHECK_LLM_CACHE_DIR", str(tmp_path / "cache"))
    paper = _write_paper(tmp_path)
    out = tmp_path / "failed.jsonl"
    args = runner.parse_args(
        ["--model", "groq/llama-3.3-70b-versatile", "--out", str(out), str(paper)]
    )
    with local_options(RUN_OPTIONS):
        assert runner.run(args) == 0
    _, record = _lines(out)
    assert record["path"] == "failed"
    assert record["traffic_light"] == "na"
    assert record["rows"] == []
    assert record["warnings"]  # the failed request is a warning


def test_runner_without_papers_stops(runner: ModuleType, tmp_path: Path) -> None:
    from metacheck.utils import local_options

    args = runner.parse_args(["--model", "groq/x", "--out", str(tmp_path / "o.jsonl")])
    with local_options(RUN_OPTIONS):
        assert runner.run(args) == 2


def test_runner_forwards_what_the_other_environment_needs(
    runner: ModuleType, tmp_path: Path
) -> None:
    args = runner.parse_args(
        ["--model", "m/x", "--out", "o.jsonl", "--seed", "3", "--limit", "2", "--demo", "p"]
    )
    forward = runner._forward(args, ["/abs/p"])
    assert forward[0] == "/abs/p"
    assert forward[forward.index("--seed") + 1] == "3"
    assert forward[forward.index("--limit") + 1] == "2"
    assert "--demo" in forward
    assert "--commit" not in forward and "--python" not in forward


def test_runner_adds_the_llm_extra_only_when_the_checkout_has_one(
    runner: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[project.optional-dependencies]\nllm = ["openai"]\n',
        encoding="utf-8",
    )
    assert runner._extras_of(tmp_path, ["bibr"]) == ["--extra", "bibr", "--extra", "llm"]
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
    assert runner._extras_of(tmp_path, []) == []


def _run_file(path: Path, label: str, papers: list[dict[str, Any]]) -> Path:
    header = {
        "kind": "run",
        "label": label,
        "model": "google_gemini/m",
        "seed": 1,
        "papers": len(papers),
        "versions": {"metacheck": "0.4.0a1"},
    }
    path.write_text(
        "\n".join(json.dumps(r) for r in [header, *({"kind": "paper", **p} for p in papers)])
        + "\n",
        encoding="utf-8",
    )
    return path


def _paper(
    pid: str, rows: list[dict[str, Any]], light: str = "green", path: str = "structured"
) -> dict[str, Any]:
    return {
        "paper_id": pid,
        "traffic_light": light,
        "path": path,
        "rows": rows,
        "warnings": [],
        "error": None,
    }


def _row(text_id: int, **values: Any) -> dict[str, Any]:
    return {
        "text_id": text_id,
        "text_head": "t",
        "power_type": "apriori",
        "sample_size": 64,
        **values,
    }


def test_compare_of_identical_runs_finds_nothing(
    compare: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    papers = [_paper("a", [_row(1), _row(2, sample_size=10)]), _paper("b", [])]
    one = _run_file(tmp_path / "one.jsonl", "one", papers)
    two = _run_file(tmp_path / "two.jsonl", "two", papers)
    assert compare.main([str(one), str(two), "--strict"]) == 0
    shown = capsys.readouterr().out
    assert "traffic light unchanged: 2 of 2" in shown
    assert "LLM check failed to run: 0 before, 0 after" in shown
    assert "2 matched by paragraph, 2 of them with every value equal" in shown
    assert "No paper differs" in shown


def test_compare_lists_every_kind_of_difference(
    compare: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _run_file(
        tmp_path / "before.jsonl",
        "before",
        [
            _paper("a", [_row(1, power=0.8), _row(2)]),
            _paper("b", [_row(1)], light="red"),
            _paper("gone", []),
        ],
    )
    after = _run_file(
        tmp_path / "after.jsonl",
        "after",
        [
            _paper("a", [_row(1, power=0.8000000000001), _row(2, sample_size=65), _row(2)]),
            _paper("b", [_row(1, power_type=None)], light="green", path="prompt"),
            _paper("new", []),
        ],
    )
    assert compare.main([str(before), str(after), "--strict"]) == 1
    shown = capsys.readouterr().out
    assert "papers in both runs: 2 (only before: 1, only after: 1)" in shown
    assert "traffic light unchanged: 1 of 2" in shown
    assert "| b | red -> green | structured -> prompt | 1 -> 1 | power_type |" in shown
    # the power difference is below the tolerance; the sample size and the missing value are not
    assert "| a | 2 | sample_size | 64 | 65 |" in shown
    assert "| b | 1 | power_type | apriori | (missing) |" in shown
    assert "| a | 2 | (row) | (missing) | present |" in shown
    assert "| power |" in shown and "| power | 1 | 1 | 100% |" in shown


def test_compare_says_when_the_llm_check_did_not_run(
    compare: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = _paper("a", [], light="na", path="failed")
    one = _run_file(tmp_path / "one.jsonl", "one", [failed])
    two = _run_file(tmp_path / "two.jsonl", "two", [failed])
    assert compare.main([str(one), str(two)]) == 0
    shown = capsys.readouterr().out
    assert "LLM check failed to run: 1 before, 1 after" in shown
    assert "says nothing about the model" in shown


def test_compare_numbers_use_the_tolerance(compare: ModuleType) -> None:
    assert compare.same(0.8, 0.8 + 1e-12, 1e-9)
    assert not compare.same(0.8, 0.81, 1e-9)
    assert not compare.same(None, 0, 1e-9)
    assert compare.same(None, None, 1e-9)
    assert not compare.same(True, 1, 1e-9)
    assert compare.same("a", "a", 1e-9)


def test_compare_refuses_a_file_without_a_header(compare: ModuleType, tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"kind": "paper", "paper_id": "a"}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="no header line"):
        compare.load(bad)
