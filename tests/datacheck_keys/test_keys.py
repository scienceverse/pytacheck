"""Keys in a repository's files (port of metacheck's ``tests/testthat/test-keys.R``, #607).

The keys are made at random when the tests run, in the form of each provider's
keys, so that no file holds one: GitHub refuses a push with a key it recognises.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck import keys as k
from metacheck.module import module, module_run
from metacheck.utils import local_options

_HEX = "0123456789abcdef"
_AN = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def fake_keys(seed: int = 1) -> dict[str, str]:
    rng = random.Random(seed)

    def r(n: int, chars: str) -> str:
        return "".join(rng.choice(chars) for _ in range(n))

    return {
        "openrouter-api-key": "sk-or-v1-" + r(64, _HEX),
        "github-pat": "ghp_" + r(36, _AN),
        "github-fine-grained-pat": "github_pat_" + r(82, _AN + "_"),
        "aws-access-token": "AKIA" + r(16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"),
        "slack-bot-token": f"xoxb-{r(12, '0123456789')}-{r(12, '0123456789')}-{r(24, _AN)}",
        "stripe-access-token": "sk_live_" + r(24, _AN),
        "anthropic-api-key": "sk-ant-api03-" + r(93, _AN + "_-") + "AA",
        "gcp-api-key": "AIza" + r(35, _AN + "_-"),
        "huggingface-access-token": "hf_"
        + r(34, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        "openai-api-key": "sk-proj-" + r(58, _AN + "_-") + "T3BlbkFJ" + r(58, _AN + "_-"),
        "gitlab-pat": "glpat-" + r(20, _AN),
        "groq-api-key": "gsk_" + r(52, _AN),
    }


def has_any(keys: dict[str, str], text: str) -> bool:
    return any(v in text for v in keys.values())


@pytest.mark.parametrize("name", list(fake_keys()))
def test_a_key_of_each_common_type_is_found_in_the_forms_code_and_files_write_it(name: str) -> None:
    key = fake_keys()[name]
    for s in (
        f"my key is {key} for the API.",
        f'api_key <- "{key}"',
        f'"Authorization": "Bearer {key}"',
        f'{{"key":"{key}","n":1}}',
    ):
        found = k._key_find(s)
        assert name in [h.rule for h in found], (name, s[:30])
        assert key in [h.key for h in found], name


@pytest.mark.parametrize(
    "text",
    [
        "sk-or-v1-" + "x" * 64,
        "ghp_" + "a" * 36,
        "api_key = 'YOUR_API_KEY'",
        "token: ${API_KEY}",
        "key = {{ secrets.KEY }}",
        "AKIAIOSFODNN7EXAMPLE",
        "The participants were asked to rate the stimuli.",
        "KeyError: 'response_time' in file data_cleaning.py",
        "",
    ],
)
def test_placeholders_examples_and_ordinary_text_are_not_keys(text: str) -> None:
    assert k._key_find(text) == []


def test_missing_text_has_no_keys() -> None:
    assert k._key_find(None) == []
    assert k._key_find(float("nan")) == []


def test_a_key_far_into_a_long_text_is_found_at_its_place() -> None:
    key = fake_keys(2)["openrouter-api-key"]
    s = "participant,condition,1.234\n" * 2000 + " key = " + key + " " + "x,y\n" * 3000
    found = k._key_find(s)
    assert [h.key for h in found] == [key]
    assert s[found[0].start : found[0].end] == key


def test_key_remove_replaces_each_key_in_a_result_in_tables_and_names_too() -> None:
    keys = fake_keys(3)
    table = pd.DataFrame({"a": ["x", keys["github-pat"]], "b": [1, 2]})
    table = table.rename(columns={"a": keys["groq-api-key"]})
    res: dict[str, Any] = {
        "report": ["text", "found " + keys["openrouter-api-key"]],
        "table": table,
        "nested": [{"z": "key=" + keys["aws-access-token"]}],
        "f": pd.Series(["a", keys["gcp-api-key"]], dtype="category"),
    }
    out, n = k._key_remove(res)
    assert n == 5
    flat = " ".join(
        [
            *out["report"],
            *out["table"].columns,
            *out["table"].iloc[:, 0],
            out["nested"][0]["z"],
            *map(str, out["f"].cat.categories),
        ]
    )
    assert not has_any(keys, flat)
    assert out["report"][1] == "found [key removed]"
    assert out["table"].iloc[1, 0] == "[key removed]"
    assert out["table"].columns[0] == "[key removed]"
    assert isinstance(out["table"], pd.DataFrame)
    assert pd.api.types.is_integer_dtype(out["table"]["b"])
    assert out["nested"][0]["z"] == "key=[key removed]"
    # the result in is not changed
    assert keys["github-pat"] in table.iloc[:, 0].tolist()
    # a result without a keyword is returned as it was
    plain = {"report": "nothing here", "table": pd.DataFrame({"a": [1, 2, 3]}), "x": None}
    out2, n2 = k._key_remove(plain)
    assert out2 is plain and n2 == 0


def test_module_run_removes_a_key_from_any_modules_result(tmp_path: Path) -> None:
    key = fake_keys(4)["openrouter-api-key"]

    @module(title="Leaky", description="A module that copies a key.")
    def leaky(paper: Any) -> dict[str, Any]:
        joined = key[:20] + key[20:]
        return {
            "table": pd.DataFrame({"text": ["Bearer " + joined]}),
            "report": "key: " + joined,
            "summary_text": joined,
            "traffic_light": "info",
        }

    mo = module_run(pc.test_paper("x"), leaky)
    assert key not in " ".join([mo.table["text"][0], str(mo.report), str(mo.summary_text)])
    assert mo.table["text"][0] == "Bearer [key removed]"


def test_key_scan_files_gives_file_line_type_and_only_the_beginning_of_a_key(
    tmp_path: Path,
) -> None:
    keys = fake_keys(5)
    # Windows line endings, a Latin-1 character, a key on the last line without a line ending
    (tmp_path / "a.R").write_bytes(
        ("x <- 1\r\n# caf\xe9\r\ntoken <- '" + keys["github-pat"] + "'").encode("latin-1")
    )
    (tmp_path / "blob.bin").write_bytes(bytes([0, 1, 2, 0, 255]))
    (tmp_path / "two.txt").write_text(
        "a\nb\nk1 = " + keys["openrouter-api-key"] + " ; k2 = " + keys["aws-access-token"] + "\n"
    )
    names = ["a.R", "blob.bin", "two.txt", "missing.txt"]
    found, skipped = k._key_scan_files([str(tmp_path / n) for n in names], names=names)
    assert found["file"].tolist() == ["a.R", "two.txt", "two.txt"]
    assert found["line"].tolist() == [3, 3, 3]
    assert set(found["rule"]) == {"github-pat", "openrouter-api-key", "aws-access-token"}
    assert found["key"].tolist() == [1, 2, 3]
    assert not has_any(keys, " ".join(found.astype(str).to_numpy().ravel()))
    assert all(s.endswith("...") for s in found["shown"])
    shown = found.loc[found["rule"] == "github-pat", "shown"].iloc[0]
    assert shown == keys["github-pat"][:6] + "..."
    assert len(skipped) == 0


def test_a_file_over_the_size_limit_is_not_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys = fake_keys(5)
    (tmp_path / "big.txt").write_text("token = " + keys["github-pat"])
    monkeypatch.setattr(k, "_KEY_MAX_BYTES", 10)
    found, skipped = k._key_scan_files([str(tmp_path / "big.txt")], names=["big.txt"])
    assert len(found) == 0
    assert skipped["file"].tolist() == ["big.txt"]
    assert skipped["reason"].tolist() == ["too large"]


def test_of_a_data_table_only_the_column_names_and_first_rows_are_searched(tmp_path: Path) -> None:
    key = fake_keys(8)["openrouter-api-key"]
    # a survey's key as embedded data, in every response (as in 6qf9p_v3); the
    # question text has a line break, as Qualtrics' do
    responses = pd.DataFrame(
        {
            "StartDate": [f"2026-01-0{i}" for i in range(1, 10)],
            "Q1": "How\nworried?",
            "OpenRouterAPIKey": "Bearer " + key,
        }
    )
    # a key typed by one participant far down a table is not searched for
    late = pd.DataFrame({"id": range(1, 501), "answer": ["ok"] * 498 + ["my key " + key, "ok"]})
    seen = k.KeySeen()
    f = k._key_scan_tables({"responses.csv": responses, "late.csv": late}, seen=seen)
    assert f["table"].tolist() == ["responses.csv"] * 3
    assert f["row"].tolist() == [1, 2, 3]
    assert f["key"].tolist() == [1, 1, 1]
    assert key not in " ".join(f.astype(str).to_numpy().ravel())
    # a key in a column name is found as row 0
    hdr = pd.DataFrame({key: [1]})
    assert k._key_scan_tables({"h.csv": hdr})["row"].tolist() == [0]
    # data files are left to the table search; other files are searched whole
    (tmp_path / "data.csv").write_text("id,answer\n" + "".join(f"{i},{key}\n" for i in (1, 2, 3)))
    (tmp_path / "late.R").write_text(
        "".join(f"# line {i}\n" for i in range(1, 499)) + f"key <- '{key}'\n"
    )
    files = pd.DataFrame(
        {
            "paper_id": ["p", "p"],
            "repo_url": [str(tmp_path)] * 2,
            "file_name": ["data.csv", "late.R"],
            "file_path": ["data.csv", "late.R"],
            "file_location": [str(tmp_path / "data.csv"), str(tmp_path / "late.R")],
            "data_type": ["data", "code"],
        }
    )
    g, _ = k._key_scan_repo_files(files, seen=seen)
    assert g["file"].tolist() == ["late.R"]
    assert g["line"].tolist() == [499]
    # the same key as in the table: the same number
    assert g["key"].tolist() == [1]


def test_encoded_data_is_not_searched_and_not_changed() -> None:
    import numpy as np

    key = fake_keys(9)["github-pat"]
    rng = np.random.default_rng(10)
    pool = list("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")
    b64 = "".join(rng.choice(pool, 3000))
    # a run that holds what looks like a Brave Search key, as in a corpus page
    alnum = "".join(rng.choice(pool[:62], 30))
    b64 = b64[:1000] + "/BSA" + alnum + "/" + b64[1000:]
    page = f'<p>Results</p>\n<img src="data:image/png;base64,{b64}">\n<p>token {key}</p>'
    found = k._key_find(page)
    assert [h.rule for h in found] == ["github-pat"]
    assert page[found[0].start : found[0].end] == key
    out, n = k._key_remove({"report": page})
    assert b64 in out["report"]
    assert key not in out["report"]
    assert n == 1


def test_encoded_data_in_a_file_keeps_the_lines(tmp_path: Path) -> None:
    key = fake_keys(9)["github-pat"]
    page = "<p>x</p>\n" + "A" * 1500 + f"\n<p>token {key}</p>"
    (tmp_path / "report.html").write_text(page)
    found, _ = k._key_scan_files([str(tmp_path / "report.html")])
    assert found["line"].tolist() == [3]


def test_a_rule_made_for_some_files_only_is_tried_on_those_files_only(tmp_path: Path) -> None:
    line = 'password = "x7Kq2-Lm9Pz"'
    (tmp_path / "main.tf").write_text(line)
    (tmp_path / "Test.java").write_text(line)
    names = ["main.tf", "Test.java"]
    f, _ = k._key_scan_files([str(tmp_path / n) for n in names], names=names)
    assert f.loc[f["rule"] == "hashicorp-tf-password", "file"].tolist() == ["main.tf"]
    # without a file, as in a module's result, such a rule is not tried
    assert "hashicorp-tf-password" not in [h.rule for h in k._key_find(line)]


def test_the_rules_are_betterleaks_rules_of_high_and_medium_confidence_all_readable() -> None:
    x = k._key_rules()
    assert len(x.patterns) > 400
    assert {p.confidence for p in x.patterns} == {"high", "medium"}
    assert x.source["licence"] == "MIT"
    for p in x.patterns:
        assert p.rx is not None
    assert all(len(p.keywords) > 0 for p in x.patterns)


def test_key_replace_takes_overlapping_keys_as_one() -> None:
    text = "aaa SECRETKEY bbb"
    found = [k.KeyHit(4, 13, "r", "SECRETKEY"), k.KeyHit(7, 16, "r", "RETKEY b")]
    assert k._key_replace(text, found) == "aaa [key removed]b"
    assert k._key_replace(text, []) == text


def test_key_shown_is_a_sixth_of_the_key_between_4_and_12_characters() -> None:
    assert k._key_shown("abcdefghij") == "abcd..."
    assert k._key_shown("x" * 36) == "xxxxxx..."
    assert k._key_shown("y" * 200) == "yyyyyyyyyyyy..."


def test_data_check_warns_about_keys_in_code_survey_and_env_files(tmp_path: Path) -> None:
    import json

    from tests.mod_data_check import test_data_check as dct

    keys = fake_keys(6)
    d = tmp_path / "repo"
    (d / "code").mkdir(parents=True)
    (d / "code" / "analysis.R").write_text(
        f'library(httr)\nkey <- "{keys["openrouter-api-key"]}"\n'
    )
    qsf = {
        "SurveyEntry": {"SurveyName": "Climate"},
        "SurveyElements": [
            {
                "Element": "SQ",
                "PrimaryAttribute": "QID1",
                "Payload": {
                    "QuestionText": "How worried are you?",
                    "DataExportTag": "worry",
                    "QuestionType": "MC",
                    "Choices": {"1": {"Display": "Not"}, "2": {"Display": "Very"}},
                    "QuestionJS": 'fetch(u, {headers: {"Authorization": "Bearer '
                    + keys["anthropic-api-key"]
                    + '"}})',
                },
            }
        ],
    }
    (d / "survey.qsf").write_text(json.dumps(qsf))
    (d / ".env").write_text(f"GITHUB_TOKEN={keys['github-pat']}\nMODE=test\n")
    pd.DataFrame({"id": range(1, 21), "worry": [1, 2] * 10}).to_csv(d / "data.csv", index=False)
    with local_options({"metacheck.llm.use": False}):
        mo = module_run(pc.test_paper("x"), "data_check", local_path=str(d), local_only=True)
    assert mo.traffic_light == "red"
    kd = mo.extras["keys"]
    assert set(kd["file"]) == {"code/analysis.R", "survey.qsf", ".env"}
    assert set(kd["rule"]) == {"openrouter-api-key", "anthropic-api-key", "github-pat"}
    assert int(mo.summary_table["key_n"].iloc[0]) == 3
    assert "We found 3 keys" in mo.summary_text
    rep = pc.module_report(mo)
    assert "Keys in the repository" in rep
    assert not has_any(keys, rep)
    assert not has_any(keys, str({k_: v for k_, v in mo.extras.items() if k_ != "previews"}))
    # a folder without keys: no warning
    d2 = tmp_path / "clean"
    d2.mkdir()
    pd.DataFrame({"id": range(1, 6), "x": range(1, 6)}).to_csv(d2 / "data.csv", index=False)
    (d2 / "a.R").write_text("x <- read.csv('data.csv')\n")
    with local_options({"metacheck.llm.use": False}):
        mo3 = module_run(pc.test_paper("x"), "data_check", local_path=str(d2), local_only=True)
    assert len(mo3.extras["keys"]) == 0
    assert int(mo3.summary_table["key_n"].iloc[0]) == 0
    assert "Keys in the repository" not in pc.module_report(mo3)
    del dct
