"""Study grouping and batched LLM classification.

Ports of the data_group_llm / .llm_classify_batched / .strip_llm_wrapper tests in
upstream tests/testthat/test-codebook-helpers.R, plus the deterministic
grouping passes. ``files._llm`` is the hook that calls pytacheck.llm.core.llm();
tests replace it so nothing reaches a provider.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from pytacheck.datacheck import files as F


@pytest.fixture
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no LLM in tests")

    monkeypatch.setattr(F, "_llm", fail)
    monkeypatch.setattr(F, "_llm_model", lambda: "test-model")


def _llm_frame(cols: dict[str, list[Any]], model: str = "mock") -> pd.DataFrame:
    df = pd.DataFrame(cols)
    df.attrs["llm"] = {"model": model}
    return df


def test_falls_back_to_single_study_without_evidence(no_llm: None) -> None:
    f = pd.DataFrame({"file_name": ["fig1.png", "photo.jpg", "manual.pdf"],
                      "data_type": ["materials"] * 3})  # fmt: skip
    out = F.data_group_llm(f)
    assert out["group"].tolist() == ["ex1"] * 3


def test_returns_none_on_empty_input() -> None:
    assert F.data_group_llm(pd.DataFrame()) is None
    assert F.data_group_llm(None) is None


def test_llm_classify_batched_maps_by_index(monkeypatch: pytest.MonkeyPatch) -> None:
    batch_sizes: list[int] = []

    def fake_llm(text: pd.DataFrame, text_col: str = "text", **_k: Any) -> pd.DataFrame:
        lines = text[text_col].iloc[0].split("\n")
        batch_sizes.append(len(lines))
        idx = [int(ln.split(".", 1)[0]) for ln in lines]
        item = [ln.split(". ", 1)[1] for ln in lines]
        val = ["continuous" if "num" in it else "text" for it in item]
        # reversed on purpose: results must be aligned by index, not position
        return _llm_frame({"results.index": idx[::-1], "results.value": val[::-1]})

    monkeypatch.setattr(F, "_llm", fake_llm)
    items = [("num", "cat")[i % 2] for i in range(120)]
    res = F._llm_classify_batched(items, system_prompt="classify", value_desc="type",
                                  valid=["continuous", "text"], batch_size=50)  # fmt: skip
    assert len(res) == 120
    assert batch_sizes == [50, 50, 20]
    assert res.iloc[[0, 2, 4]].tolist() == ["continuous"] * 3
    assert res.iloc[[1, 3, 5]].tolist() == ["text"] * 3
    assert res.attrs["llm_model"] == "mock"


def test_llm_classify_batched_drops_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_llm(text: pd.DataFrame, text_col: str = "text", **_k: Any) -> pd.DataFrame:
        n = len(text[text_col].iloc[0].split("\n"))
        return _llm_frame({"results.index": list(range(1, n + 1)),
                           "results.value": ["banana"] * n})  # fmt: skip

    monkeypatch.setattr(F, "_llm", fake_llm)
    res = F._llm_classify_batched(["a", "b"], system_prompt="p", value_desc="v",
                                  valid=["continuous", "text"], batch_size=50)  # fmt: skip
    assert res.isna().all()


def test_llm_classify_batched_survives_llm_errors(no_llm: None) -> None:
    res = F._llm_classify_batched(["a", "b", "c"], system_prompt="p", value_desc="v")
    assert len(res) == 3
    assert res.isna().all()
    assert res.attrs["llm_model"] is None


def test_strip_llm_wrapper() -> None:
    df = pd.DataFrame({"assignments.index": [1, 2], "assignments.group": ["ex1", "ex2"]})
    out = F._strip_llm_wrapper(df, "assignments")
    assert list(out.columns) == ["index", "group"]
    bare = pd.DataFrame({"index": [1], "group": ["x"]})
    assert F._strip_llm_wrapper(bare, "assignments") is bare
    assert F._strip_llm_wrapper(None, "assignments") is None


def test_group_from_path_and_normalize() -> None:
    got = F._data_group_from_path(
        ["Experiment 1/data.csv", "study2a_data.csv", "pilot/p.csv", "a/b/c.csv", None]
    )
    assert got == ["ex1", "ex2a", "pilot1", None, None]
    assert F._data_group_normalize(["Experiment 1", "study 2a", "Pilot 3", None]) == [
        "ex1",
        "ex2a",
        "pilot3",
        None,
    ]
    # ordered by study number first, then prefix, then suffix (as in R)
    assert F._data_group_sort(["ex2", "pilot1", "ex1b", "ex10", "ex1", "ex1a", "pilot2"]) == [
        "pilot1", "ex1", "ex1a", "ex1b", "ex2", "pilot2", "ex10",
    ]  # fmt: skip


def test_group_llm_uses_mocked_assignments(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fake_llm(text: pd.DataFrame, text_col: str = "text", **_k: Any) -> pd.DataFrame:
        lines = text[text_col].iloc[0].split("\n")
        seen.extend(lines)
        groups = ["ex2" if "second" in ln else "ex1" for ln in lines]
        return _llm_frame({"assignments.index": list(range(1, len(lines) + 1)),
                           "assignments.group": groups}, model="mock-model")  # fmt: skip

    monkeypatch.setattr(F, "_llm", fake_llm)
    f = pd.DataFrame({
        "file_name": ["first_raw.csv", "second_raw.csv", "analysis.R"],
        "file_path": ["first_raw.csv", "second_raw.csv", "analysis.R"],
        "data_type": ["data", "data", "code"],
    })  # fmt: skip
    out = F.data_group_llm(f)
    assert seen  # the files without path evidence went to the (mock) LLM
    by_file = dict(zip(f["file_name"], out["group"], strict=True))
    assert by_file["first_raw.csv"] == "ex1"
    assert by_file["second_raw.csv"] == "ex2"
    assert out.attrs["model"] == "mock-model"


def test_study_roster_from_text() -> None:
    import pytacheck as pc

    paper = pc.test_paper(
        ["In Experiment 1 we tested x.", "Study 2a replicated it.", "A pilot 1 preceded both."]
    )
    # sorted by study number (pilot1 before ex2a), as in R
    assert F.data_study_roster(paper) == ["ex1", "pilot1", "ex2a"]
    assert F.data_study_roster("Experiment 1") == []


def test_roster_check() -> None:
    chk = F._data_group_check_roster(["ex1", "ex3"], ["ex1", "ex2"])
    assert chk["agrees"] is False
    assert chk["missing"] == ["ex2"]
    assert chk["extra"] == ["ex3"]
    empty = F._data_group_check_roster(["ex1"], [])
    assert empty["roster"] == [] and empty["extra"] == ["ex1"] and empty["agrees"] is False
