"""Keys in text and files (metacheck #607). Port of ``tests/testthat/test-keys.R``.

The keys are made at random when the tests run, in the form of each provider's keys, so
that no file holds one: GitHub refuses a push with a key it recognises.
"""

from __future__ import annotations

import random

import pandas as pd

from metacheck import keys as K

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
    return any(k in text for k in keys.values())


def test_a_key_of_each_common_type_is_found_in_the_forms_code_and_files_write_it() -> None:
    k = fake_keys()
    for n, key in k.items():
        for s in (
            f"my key is {key} for the API.",
            f'api_key <- "{key}"',
            f'"Authorization": "Bearer {key}"',
            f'{{"key":"{key}","n":1}}',
        ):
            f = K._key_find(s)
            assert n in set(f["rule"]), (n, s[:30])
            assert key in set(f["key"]), n


def test_placeholders_examples_and_ordinary_text_are_not_keys() -> None:
    for s in (
        "sk-or-v1-" + "x" * 64,
        "ghp_" + "a" * 36,
        "api_key = 'YOUR_API_KEY'",
        "token: ${API_KEY}",
        "key = {{ secrets.KEY }}",
        "AKIAIOSFODNN7EXAMPLE",
        "The participants were asked to rate the stimuli.",
        "KeyError: 'response_time' in file data_cleaning.py",
        "",
    ):
        assert len(K._key_find(s)) == 0, s
    assert len(K._key_find(None)) == 0


def test_a_key_far_into_a_long_text_is_found_at_its_place() -> None:
    k = fake_keys(2)["openrouter-api-key"]
    s = "participant,condition,1.234\n" * 2000 + " key = " + k + " " + "x,y\n" * 3000
    f = K._key_find(s)
    assert list(f["key"]) == [k]
    start, end = int(f["start"][0]), int(f["end"][0])
    assert s[start - 1 : end] == k


def test_key_remove_replaces_each_key_in_a_result_in_tables_and_names_too() -> None:
    k = fake_keys(3)
    table = pd.DataFrame({k["groq-api-key"]: ["x", k["github-pat"]], "b": [1, 2]})
    res = {
        "report": ["text", "found " + k["openrouter-api-key"]],
        "table": table,
        "nested": [{"z": "key=" + k["aws-access-token"]}],
        "f": pd.Series(pd.Categorical(["a", k["gcp-api-key"]])),
    }
    out, n = K._key_remove(res)
    flat = " ".join(
        [
            *out["report"],
            *out["nested"][0].values(),
            *map(str, out["table"].columns),
            *out["table"].iloc[:, 0],
            *map(str, out["f"].cat.categories),
        ]
    )
    assert not has_any(k, flat)
    assert n == 5
    assert out["report"][1] == "found [key removed]"
    assert out["table"].iloc[1, 0] == "[key removed]"
    assert out["table"].columns[0] == "[key removed]"
    assert isinstance(out["table"], pd.DataFrame)
    assert out["table"]["b"].tolist() == [1, 2]
    assert out["nested"][0]["z"] == "key=[key removed]"
    # a result without a keyword is returned as it was
    plain = {"report": "nothing here", "table": pd.DataFrame({"a": [1, 2, 3]})}
    same, n0 = K._key_remove(plain)
    assert same is plain
    assert n0 == 0
    # the caller's table is not changed
    assert k["github-pat"] in table.iloc[:, 0].tolist()


def test_module_run_removes_a_key_from_any_modules_result() -> None:
    import metacheck as pc
    from metacheck.module import module, module_run

    k = fake_keys(4)["openrouter-api-key"]

    @module(title="Leaky", description="A module that copies a key.")
    def leaky(paper: object) -> dict[str, object]:
        key = k[:20] + k[20:]
        return {
            "table": pd.DataFrame({"text": ["Bearer " + key]}),
            "report": "key: " + key,
            "summary_text": key,
            "traffic_light": "info",
        }

    mo = module_run(pc.test_paper("x"), leaky)
    assert k not in " ".join([str(mo.table), str(mo.report), str(mo.summary_text)])
    assert mo.table["text"].tolist() == ["Bearer [key removed]"]
    assert "keys_removed" not in mo.extras


def test_key_scan_files_gives_file_line_type_and_only_the_beginning_of_a_key(tmp_path) -> None:  # type: ignore[no-untyped-def]
    k = fake_keys(5)
    # Windows line endings, a Latin-1 character, a key on the last line without a line ending
    (tmp_path / "a.R").write_bytes(
        ("x <- 1\r\n# caf\xe9\r\ntoken <- '" + k["github-pat"] + "'").encode("latin-1")
    )
    (tmp_path / "blob.bin").write_bytes(bytes([0, 1, 2, 0, 255]))
    (tmp_path / "two.txt").write_text(
        f"a\nb\nk1 = {k['openrouter-api-key']} ; k2 = {k['aws-access-token']}\n"
    )
    names = ["a.R", "blob.bin", "two.txt", "missing.txt"]
    f, skipped = K._key_scan_files([str(tmp_path / n) for n in names], names=names)
    assert list(f["file"]) == ["a.R", "two.txt", "two.txt"]
    assert list(f["line"]) == [3, 3, 3]
    assert set(f["rule"]) == {"github-pat", "openrouter-api-key", "aws-access-token"}
    assert list(f["key"]) == [1, 2, 3]
    assert not has_any(k, " ".join(map(str, f.to_numpy().ravel())))
    assert all(s.endswith("...") for s in f["shown"])
    shown = f.loc[f["rule"] == "github-pat", "shown"].iloc[0]
    assert shown == k["github-pat"][:6] + "..."
    assert len(skipped) == 0


def test_of_a_data_table_only_the_column_names_and_first_rows_are_searched(tmp_path) -> None:  # type: ignore[no-untyped-def]
    k = fake_keys(8)["openrouter-api-key"]
    # a survey's key as embedded data, in every response (as in 6qf9p_v3); the question text
    # has a line break, as Qualtrics' do
    responses = pd.DataFrame(
        {
            "StartDate": [f"2026-01-0{i}" for i in range(1, 10)],
            "Q1": "How\nworried?",
            "OpenRouterAPIKey": "Bearer " + k,
        }
    )
    # a key typed by one participant far down a table is not searched for
    late = pd.DataFrame({"id": range(1, 501), "answer": ["ok"] * 498 + ["my key " + k, "ok"]})
    seen = K._KeySeen()
    f = K._key_scan_tables({"responses.csv": responses, "late.csv": late}, seen=seen)
    assert list(f["table"]) == ["responses.csv"] * 3
    assert list(f["row"]) == [1, 2, 3]
    assert list(f["key"]) == [1, 1, 1]
    assert k not in " ".join(map(str, f.to_numpy().ravel()))
    # a key in a column name is found as row 0
    hdr = pd.DataFrame({k: [1]})
    assert list(K._key_scan_tables({"h.csv": hdr})["row"]) == [0]
    # data files are left to _key_scan_tables(); other files are searched whole
    (tmp_path / "data.csv").write_text("id,answer\n" + "".join(f"{i},{k}\n" for i in range(1, 4)))
    (tmp_path / "late.R").write_text(
        "".join(f"# line {i}\n" for i in range(1, 499)) + f"key <- '{k}'\n"
    )
    files = pd.DataFrame(
        {
            "paper_id": "p",
            "repo_url": str(tmp_path),
            "file_name": ["data.csv", "late.R"],
            "file_path": ["data.csv", "late.R"],
            "file_location": [str(tmp_path / "data.csv"), str(tmp_path / "late.R")],
            "data_type": ["data", "code"],
        }
    )
    g, _ = K._key_scan_repo_files(files, seen=seen)
    assert list(g["file"]) == ["late.R"]
    assert list(g["line"]) == [499]
    # the same key as in the table: the same number
    assert list(g["key"]) == [1]


def test_encoded_data_is_not_searched_and_not_changed() -> None:
    import string

    k = fake_keys(9)["github-pat"]
    rng = random.Random(10)
    alphabet = string.ascii_letters + string.digits + "+/"
    b64 = "".join(rng.choice(alphabet) for _ in range(3000))
    # a run that holds what looks like a Brave Search key, as in a corpus page
    b64 = (
        b64[:1000]
        + "/BSA"
        + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(30))
        + "/"
        + b64[1000:]
    )
    page = f'<p>Results</p>\n<img src="data:image/png;base64,{b64}">\n<p>token {k}</p>'
    f = K._key_find(page)
    assert list(f["rule"]) == ["github-pat"]
    assert page[int(f["start"][0]) - 1 : int(f["end"][0])] == k
    # the image passes _key_remove() as it was; the key next to it does not
    out, _ = K._key_remove({"report": page})
    assert b64 in out["report"]
    assert k not in out["report"]


def test_a_rule_made_for_some_files_only_is_tried_on_those_files_only(tmp_path) -> None:  # type: ignore[no-untyped-def]
    line = 'password = "x7Kq2-Lm9Pz"'
    (tmp_path / "main.tf").write_text(line + "\n")
    (tmp_path / "Test.java").write_text(line + "\n")
    names = ["main.tf", "Test.java"]
    f, _ = K._key_scan_files([str(tmp_path / n) for n in names], names=names)
    assert list(f.loc[f["rule"] == "hashicorp-tf-password", "file"]) == ["main.tf"]
    # without a file, as in a module's result, such a rule is not tried
    assert "hashicorp-tf-password" not in set(K._key_find(line)["rule"])


def test_the_rules_are_betterleaks_rules_of_high_and_medium_confidence() -> None:
    x = K._key_rules()
    assert len(x.patterns) > 400
    assert {p.confidence for p in x.patterns} == {"high", "medium"}
    assert x.source["licence"] == "MIT"
    for p in x.patterns:
        K._key_regex(p.regex)  # readable
        assert len(p.keywords) > 0


def test_data_check_warns_about_keys_in_an_r_file_a_qsf_file_and_a_env_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json

    import metacheck as pc
    from metacheck.module import module_run

    k = fake_keys(6)
    d = tmp_path / "repo"
    (d / "code").mkdir(parents=True)
    (d / "code" / "analysis.R").write_text(
        "library(httr)\n" + f'key <- "{k["openrouter-api-key"]}"\n'
    )
    # a Qualtrics survey that calls the service, with the key in its header
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
                    "QuestionJS": f'fetch(u, {{headers: {{"Authorization": "Bearer {k["anthropic-api-key"]}"}}}})',
                },
            }
        ],
    }
    (d / "survey.qsf").write_text(json.dumps(qsf))
    (d / ".env").write_text(f"GITHUB_TOKEN={k['github-pat']}\nMODE=test\n")
    pd.DataFrame({"id": range(1, 21), "worry": [1, 2] * 10}).to_csv(d / "data.csv", index=False)

    mo = module_run(pc.test_paper("x"), "data_check", local_path=str(d), local_only=True)
    assert mo.traffic_light == "red"
    keys = mo.extras["keys"]
    assert set(keys["file"]) == {"code/analysis.R", "survey.qsf", ".env"}
    assert set(keys["rule"]) == {"openrouter-api-key", "anthropic-api-key", "github-pat"}
    assert int(mo.summary_table["key_n"].iloc[0]) == 3
    assert "We found 3 keys" in str(mo.summary_text)
    rep = "\n".join(str(r) for r in (mo.report if isinstance(mo.report, list) else [mo.report]))
    assert "Keys in the repository" in rep
    assert not has_any(k, rep)

    # a folder without keys: no warning
    d2 = tmp_path / "clean"
    d2.mkdir()
    pd.DataFrame({"id": range(1, 6), "x": range(1, 6)}).to_csv(d2 / "data.csv", index=False)
    (d2 / "a.R").write_text("x <- read.csv('data.csv')\n")
    mo3 = module_run(pc.test_paper("x"), "data_check", local_path=str(d2), local_only=True)
    assert len(mo3.extras["keys"]) == 0
    assert int(mo3.summary_table["key_n"].iloc[0]) == 0
    assert "Keys in the repository" not in str(mo3.report)
