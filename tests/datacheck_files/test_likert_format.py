"""Likert detection, data formats and manifest-as-data detection.

Ports of the ``.detect_likert_scale``, ``data_format`` and ``data_is_manifest``
tests in upstream tests/testthat/test-data-checks.R.  The random vectors come
from R's RNG in the upstream test's order (``data/make_likert_ref.R``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from metacheck.datacheck import files as F

V = json.loads((Path(__file__).parent / "data" / "likert_ref.json").read_text(encoding="utf-8"))
f = F._detect_likert_scale


def _range(x: list[float]) -> tuple[int, int]:
    r = f(x)
    assert r is not None
    return r["lo"], r["hi"]


def test_clean_scales() -> None:
    assert _range(V["s1_5"]) == (1, 5)
    assert _range(V["s1_7"]) == (1, 7)
    assert _range(V["s0_10"]) == (0, 10)
    assert _range(V["bip5"]) == (-5, 5)
    assert _range(V["bip11"]) == (-11, 11)


def test_unobserved_floor_is_inferred() -> None:
    r = f(V["s2_5"])
    assert (r["lo"], r["hi"]) == (1, 5)
    assert r["floor_inferred"] == [1]
    assert f(V["s3_5"])["floor_inferred"] == [1, 2]
    assert _range(V["zero"]) == (0, 5)


def test_interior_gaps_are_bridged() -> None:
    assert _range(V["gap"]) == (1, 7)


def test_contaminants_become_suspects() -> None:
    assert f(V["sus99"])["suspects"] == [99]
    assert f(V["sus33"])["suspects"] == [33]
    assert f(V["sus8"])["suspects"] == [8]
    assert f(V["neg99"])["suspects"] == [-99]


def test_adjacent_overshoot_extends_scale() -> None:
    r = f(V["adj6"])
    assert (r["lo"], r["hi"]) == (1, 6)
    assert r["suspects"] == []


@pytest.mark.parametrize("name", ["age", "rt", "coded", "count", "nonint", "few"])
def test_non_scales_are_rejected(name: str) -> None:
    assert f(V[name]) is None


def test_likert_ignores_missing_and_infinite() -> None:
    x = [*V["s1_5"], None, float("nan"), float("inf"), float("-inf")]
    assert _range(x) == (1, 5)


def test_data_format() -> None:
    assert F.data_format(["csv"]) == ["tabular"]
    assert F.data_format(["sav"]) == ["tabular"]
    assert F.data_format(["edf"]) == ["raw"]
    assert F.data_format(["mp4"]) == ["raw"]
    assert F.data_format(["RData", "", None]) == ["tabular", "raw", "raw"]
    assert F.data_format([]) == []


def test_data_is_manifest_detects_file_listing() -> None:
    repo = ["Study 1.r", "Study 1.csv", "notes.txt"]
    manifest = pd.DataFrame({"type": ["code", "data", "doc"],
                             "file": ["Study 1.r", "Study 1.csv", "notes.txt"]})  # fmt: skip
    assert F.data_is_manifest(manifest, repo) is True
    real = pd.DataFrame({"id": [1, 2, 3], "score": [1.1, 2.2, 3.3]})
    assert F.data_is_manifest(real, repo) is False


def test_data_is_manifest_numeric_column_names_numeric_files() -> None:
    # numeric columns are skipped only while no repository file is named like a number
    repo = ["1.5", "2.25", "notes.txt"]
    numbers = pd.DataFrame({"file": [1.5, 2.25, 1.5]})
    assert F.data_is_manifest(numbers, repo) is True
    numbers_as_objects = pd.DataFrame({"file": pd.Series([1.5, 2.25, None], dtype=object)})
    assert F.data_is_manifest(numbers_as_objects, repo) is True
    assert F.data_is_manifest(numbers, ["a.csv", "b.r"]) is False


def test_data_is_manifest_mixed_object_column() -> None:
    repo = ["Study 1.r", "Study 1.csv", "notes.txt"]
    mixed = pd.DataFrame(
        {
            "file": pd.Series(
                ["Study 1.r", " Study 1.csv ", 3, "notes.txt", "Study 1.r"], dtype=object
            )
        }
    )
    assert F.data_is_manifest(mixed, repo) is True
    too_few = pd.DataFrame({"file": ["Study 1.r", "x", "y", "Study 1.csv"]})
    assert F.data_is_manifest(too_few, repo) is False
