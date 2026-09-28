"""Tests of data_check's local concept classifier (``pytacheck.datacheck.concepts``).

The classifier itself is replaced by a stub that answers by column name; these
tests cover the input text it is given (checked against the training pipeline's
text for the same columns, ``fixtures/r_texts.json``) and the concept tiers.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.datacheck import concepts
from pytacheck.module import module_run
from pytacheck.utils import local_options
from tests.mod_data_check import helpers as dc

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


class StubClassifier:
    """Answers ``(label, confidence)`` by column name and records its inputs."""

    name = "stub/concepts"

    def __init__(
        self, answers: dict[str, str] | None = None, conf: dict[str, float] | None = None
    ) -> None:
        self.answers = answers or {}
        self.conf = conf or {}
        self.texts: list[str] = []

    def predict(self, texts: Sequence[str]) -> list[tuple[str, float]]:
        self.texts.extend(texts)
        names = [t.split(" | ", 1)[0].removeprefix("column: ") for t in texts]
        return [(self.answers.get(n, "other"), self.conf.get(n, 0.99)) for n in names]


@pytest.fixture
def stub(monkeypatch: pytest.MonkeyPatch) -> StubClassifier:
    s = StubClassifier()
    monkeypatch.setattr(concepts, "load_classifier", lambda source=None: s)
    monkeypatch.delenv("PYTACHECK_CONCEPTS")  # the default tier
    return s


def _concepts(mo: Any) -> dict[str, Any]:
    return {
        f"{f}:{c}": (None if pd.isna(v) else v)
        for f, c, v in zip(
            mo.table["source_file"], mo.table["column_name"], mo.table["concept"], strict=True
        )
    }


def _report(mo: Any) -> str:
    return "\n".join(b for b in mo.report if isinstance(b, str))


@contextlib.contextmanager
def _llm_recorded(spec: str) -> Iterator[list[str]]:
    """The mock LLM of ``tests/mod_data_check``; yields the concept items it was asked."""
    from pytacheck.datacheck import files

    asked: list[str] = []
    with dc._llm_mocked(spec):
        mock = files._llm

        def llm(text: Any, *args: Any, phase: str | None = None, **kwargs: Any) -> Any:
            if phase == "Classifying column concepts":
                txt = str(text[kwargs.get("text_col", "text")].iloc[0])
                asked.extend(
                    ln.split("column_name: ", 1)[1]
                    for ln in txt.split("\n")
                    if "column_name: " in ln
                )
            return mock(text, *args, phase=phase, **kwargs)

        files._llm = llm
        yield asked


# -----------------------------------------------------------------------------
# the input text
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "text"),
    [
        # jsonlite::toJSON(digits = 6) and back, then "%.4g" (checked with R 4.5, jsonlite 2.0.0)
        (0, "0"),
        (5, "5"),
        (1e-06, "1e-06"),
        (-1e-06, "-1e-06"),
        (0.000015, "1.5e-05"),
        (0.123457, "0.1235"),
        (0.1234564, "0.1235"),
        (2.958039891549808, "2.958"),
        (100000, "100000"),
        (12345.6789, "1.235e+04"),
        (2147483646.7, "2.147e+09"),
        (3000000000, "3000000000"),
        (-3000000000, "-3000000000"),
        (1e15, "1000000000000000"),
        (1e17, "1e+17"),
        (1.2345678912339999e18, "1.235e+18"),
        (None, "NA"),
        (float("nan"), "NA"),
        (float("inf"), "NA"),
        (pd.NA, "NA"),
        (True, "NA"),
    ],
)
def test_stat_text_prints_as_the_training_data(x: Any, text: str) -> None:
    assert concepts.stat_text(x) == text


@pytest.mark.parametrize(
    ("n", "md5"),
    [
        # R: idx <- unique(round(seq(1, n, length.out = 200))); md5 of paste(idx, collapse = ",")
        (201, "07f91777fc7e69727212e928b5259b37"),
        (250, "d9b251da544673defb534aea1e5e6220"),
        (300, "01c8ce3c8f40d10e34dda0e42a327685"),
        (399, "edb1aa7458bbedf3c8699a882cd34326"),
        (1000, "f8e141ec3487d7baec3b12cd6efdfde4"),
        (12345, "68541825ea8b2f0a6d1062da59fc56c0"),
        (99999, "7044cf458aa0ecec48d62e2a95807a2e"),
    ],
)
def test_sample_values_takes_rs_evenly_spaced_values(n: int, md5: str) -> None:
    idx = concepts.sample_values([str(i) for i in range(1, n + 1)])
    assert len(idx) == 200
    assert hashlib.md5(",".join(idx).encode(), usedforsecurity=False).hexdigest() == md5


def test_sample_values_drops_missing_values_and_cuts_long_ones() -> None:
    assert concepts.sample_values(["a", None, "b" * 250]) == ["a", "b" * 200]
    assert concepts.sample_values([]) == []


def test_concept_text() -> None:
    stats = {"n": 4, "n_missing": 1, "n_unique": 3, "min": 1, "max": 2.5, "mean": 1.75}
    text = concepts.concept_text(
        "rt",
        "data/exp1.csv",
        ["id", "cond"],
        {**stats, "sd": None, "representation": "numeric"},
        ["1", "2.5", "1", "x" * 50],
        "1 | 2.5",
    )
    assert text == (
        "column: rt | file: exp1.csv | neighbours: id, cond | n=4 missing=1 unique=3 min=1 "
        "max=2.5 mean=1.75 sd=NA type=numeric | values: 3 distinct in sample: 1 ; 2.5 ; " + "x" * 40
    )
    # values are told apart on their full text, then cut
    both = concepts.concept_text("c", None, [], {}, ["a" * 41, "a" * 42], "")
    assert both.endswith(f"values: 2 distinct in sample: {'a' * 40} ; {'a' * 40}")
    # without values (a file not read in full): the sample_values column
    assert concepts.concept_text("c", "f.csv", [], {}, None, "x | y").endswith("| values: x | y")


def test_the_text_is_the_training_pipelines() -> None:
    """R's texts for the fixture repositories (the training pipeline run on R's
    data_check) are, in order, among pytacheck's: pytacheck also classifies the columns
    of files the recording LLM stubs left unclassified, and the fixture leaves out
    columns whose name and values occur twice."""
    want = json.loads((FIXTURES / "r_texts.json").read_text(encoding="utf-8"))
    want.pop("_about")
    matched = 0
    for repo, texts in want.items():
        s = StubClassifier()
        p = pc.test_paper(["x"])
        p.paper_id = "p1"
        with (
            contextlib.chdir(ROOT),
            local_options(
                {
                    "pytacheck.careless": False,
                    "metacheck.llm.use": False,
                    "pytacheck.concepts": "classifier",
                }
            ),
            pytest.MonkeyPatch.context() as mp,
        ):
            mp.setattr(concepts, "load_classifier", lambda source=None, s=s: s)
            module_run(
                p,
                "data_check",
                local_path=f"tests/mod_data_check/fixtures/repos/{repo}",
                local_only=True,
            )
        wanted = set(texts)
        assert [t for t in s.texts if t in wanted] == texts, repo
        matched += len(texts)
    assert matched == 119


# -----------------------------------------------------------------------------
# settings
# -----------------------------------------------------------------------------


def test_concept_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTACHECK_CONCEPTS")
    assert concepts.concept_mode() == "classifier"
    monkeypatch.setenv("PYTACHECK_CONCEPTS", "Cascade ")
    assert concepts.concept_mode() == "cascade"
    with local_options({"pytacheck.concepts": "rules"}):
        assert concepts.concept_mode() == "rules"
        assert concepts.concept_mode("llm") == "llm"
    with pytest.raises(ValueError, match="`concepts` must be one of"):
        concepts.concept_mode("gpt")


def test_concept_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTACHECK_CONCEPT_THRESHOLD", raising=False)
    assert concepts.concept_threshold() == concepts.DEFAULT_THRESHOLD
    monkeypatch.setenv("PYTACHECK_CONCEPT_THRESHOLD", "0.5")
    assert concepts.concept_threshold() == 0.5
    with local_options({"pytacheck.concepts.threshold": 0.7}):
        assert concepts.concept_threshold() == 0.7
    monkeypatch.setenv("PYTACHECK_CONCEPT_THRESHOLD", "high")
    assert concepts.concept_threshold() == concepts.DEFAULT_THRESHOLD


def test_without_the_extra_there_is_no_classifier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(concepts, "classifier_available", lambda: False)
    monkeypatch.setattr(concepts, "_unavailable_notice", concepts._Once())
    assert concepts.load_classifier() is None
    assert concepts._unavailable_notice.done


def test_an_unloadable_model_is_no_classifier(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pytest.importorskip("onnxruntime")
    pytest.importorskip("tokenizers")
    monkeypatch.setattr(concepts, "_unavailable_notice", concepts._Once())
    assert concepts.load_classifier(str(tmp_path)) is None  # no concept_model.json


@pytest.mark.skipif(
    not os.environ.get("PYTACHECK_TEST_CONCEPT_MODEL"),
    reason="PYTACHECK_TEST_CONCEPT_MODEL (an exported model directory) is not set",
)
def test_the_exported_model() -> None:
    clf = concepts.load_classifier(os.environ["PYTACHECK_TEST_CONCEPT_MODEL"])
    assert clf is not None
    rts = (
        "512 ; 498 ; 610 ; 733 ; 312 ; 1204 ; 587 ; 645 ; 559 ; 702 ; 468 ; 691 ; 534 ; 823 ; "
        "577 ; 619 ; 490 ; 655 ; 701 ; 543"
    )
    texts = [
        "column: RT | file: stroop_data.csv | neighbours: subject, block, trial, stimulus, correct "
        "| n=400 missing=3 unique=287 min=312 max=1204 mean=598.2 sd=141.7 type=numeric | "
        f"values: 20 distinct in sample: {rts}",
        "column: age | file: demo.csv | neighbours: id, gender | n=3 missing=0 unique=3 "
        "min=23 max=41 mean=33 sd=9.165 type=numeric | values: 3 distinct in sample: 23 ; 35 ; 41",
        "column: sex | file: demo.csv | neighbours: id, age, education | n=40 missing=0 unique=2 "
        "type=text | values: 2 distinct in sample: female ; male",
        "column: condition | file: exp1.csv | neighbours: id, trial, rt | n=400 missing=0 "
        "unique=2 type=text | values: 2 distinct in sample: congruent ; incongruent",
    ]
    out = clf.predict(texts)
    assert [label for label, _ in out] == ["reaction_time", "age", "gender", "condition"]
    assert all(0 < p <= 1 for _, p in out)


# -----------------------------------------------------------------------------
# data_check's concept tiers
# -----------------------------------------------------------------------------


def test_the_classifier_fills_the_blank_concepts_by_default(stub: StubClassifier) -> None:
    rules = dc.dc_run("llm", concepts="rules")
    blank = [k for k, v in _concepts(rules).items() if v is None]
    stub.answers = {"q3": "reaction_time", "v7": "condition", "label": "measure", "pp": "other"}
    mo = dc.dc_run("llm")
    assert len(stub.texts) == len(blank)
    got = _concepts(mo)
    assert got["trials.csv:q3"] == "reaction_time"
    assert got["trials.csv:v7"] == "condition"
    # measure and other leave a concept blank, as they do from the LLM
    assert got["trials.csv:label"] is None
    assert "LLM" not in _report(mo)
    assert (
        "The local concept classifier ('stub/concepts') assigned concepts to 2 columns "
        "the rules left open."
    ) in _report(mo)
    # the rules' concepts stand
    assert {k: v for k, v in got.items() if k not in blank} == {
        k: v for k, v in _concepts(rules).items() if k not in blank
    }


def test_files_with_the_same_header_share_the_concepts(
    stub: StubClassifier, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    for k in (1, 2):
        pd.DataFrame({"q3": [512 + k, 498, 610], "v7": [1, 2, 3]}).to_csv(
            repo / "data" / f"s{k}.csv", index=False
        )
    stub.answers = {"q3": "reaction_time", "v7": "condition"}
    listing = dc._frame(
        dc._repo_rows(
            {
                "dir": str(repo),
                "repo_url": "https://osf.io/abcde",
                "files": ["data/s1.csv", "data/s2.csv"],
            },
            "p1",
            copy=False,
        ),
        [],
    )
    prev = dc.dc_prev("llm")
    from dataclasses import replace

    with local_options({"metacheck.llm.use": False}):
        mo = module_run(replace(prev, table=listing), "data_check")
    assert len(stub.texts) == 2  # the first file's columns only
    assert all("file: s1.csv" in t for t in stub.texts)
    got = _concepts(mo)
    assert got["s2.csv:q3"] == got["s1.csv:q3"] == "reaction_time"
    assert got["s2.csv:v7"] == got["s1.csv:v7"] == "condition"


def test_the_cascade_asks_the_llm_about_uncertain_columns(
    stub: StubClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTACHECK_CONCEPTS", "cascade")
    # the mock LLM answers q3 reaction_time, v7 condition, label measure, pp id
    stub.answers = {"q3": "accuracy", "v7": "likert", "label": "id", "pp": "age"}
    stub.conf = {"q3": 0.95, "v7": 0.5, "label": 0.5, "pp": 0.5}
    with _llm_recorded("basic") as asked:
        mo = module_run(dc.dc_prev("llm"), "data_check")
    classified = [t.split(" | ", 1)[0].removeprefix("column: ") for t in stub.texts]
    assert sorted(asked) == sorted(c for c in classified if stub.conf.get(c, 0.99) < 0.92)
    got = _concepts(mo)
    assert got["trials.csv:q3"] == "accuracy"  # confident: the classifier's
    assert got["trials.csv:v7"] == "condition"  # uncertain: the LLM's
    assert got["trials.csv:label"] is None  # the LLM says measure
    text = _report(mo)
    assert "LLM model 'mock/dc' reviewed ambiguous cases" in text
    assert "The local concept classifier ('stub/concepts') assigned concepts to 1 column " in text


def test_the_cascade_keeps_the_classifiers_answer_when_the_llm_gives_none(
    stub: StubClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTACHECK_CONCEPTS", "cascade")
    stub.answers = {"q3": "accuracy", "v7": "likert"}
    stub.conf = {"q3": 0.5, "v7": 0.5}
    with dc._llm_mocked("errors"):
        mo = module_run(dc.dc_prev("llm"), "data_check")
    got = _concepts(mo)
    assert got["trials.csv:q3"] == "accuracy"
    assert got["trials.csv:v7"] == "likert"


def test_without_the_classifier_the_llm_tier_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTACHECK_CONCEPTS")
    monkeypatch.setattr(concepts, "load_classifier", lambda source=None: None)
    mo = dc.dc_run("llm", llm="basic")
    assert _concepts(mo)["trials.csv:q3"] == "reaction_time"
    assert "local concept classifier" not in _report(mo)
    assert _concepts(mo) == _concepts(dc.dc_run("llm", llm="basic", concepts="llm"))


def test_rules_only(stub: StubClassifier) -> None:
    with _llm_recorded("basic") as asked:
        mo = module_run(dc.dc_prev("llm"), "data_check", concepts="rules")
    assert asked == []
    assert stub.texts == []
    assert _concepts(mo)["trials.csv:q3"] is None


def test_the_manifest_names_the_classifier(stub: StubClassifier, tmp_path: Path) -> None:
    stub.answers = {"q3": "reaction_time"}
    mo = dc.dc_run("llm", manifest=str(tmp_path / "on"))
    prov = json.loads(Path(mo.manifest_path[0]).read_text(encoding="utf-8"))["provenance"]
    assert prov["concept_classifier"] == {"used": True, "model": "stub/concepts"}
    off = dc.dc_run("llm", manifest=str(tmp_path / "off"), concepts="llm")
    prov = json.loads(Path(off.manifest_path[0]).read_text(encoding="utf-8"))["provenance"]
    assert "concept_classifier" not in prov
