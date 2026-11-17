"""Generate ``tests/datacheck_keys/key_spec.json`` and ``parity/cases/datacheck_keys.yaml``.

Run from the repository root::

    .venv/bin/python tests/datacheck_keys/gen_parity_cases.py

The keys the cases use are made when a case runs, in the form of each provider's
keys, by the same small generator in R (``key_helpers.R``) and Python
(``key_helpers.py``), so that no committed file holds one: GitHub refuses a push
with a key it recognises. ``key_spec.json`` holds the keys' forms and the files of
the repositories the module cases run on (``@@KEY:<type>@@`` is replaced by a key).
A case's result never holds a whole key (the module's, and ``kc_*``'s, results show
only where and what kind).
"""

from __future__ import annotations

import csv as csv_mod
import io
import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tests" / "datacheck_keys"
R_SRC = "source('tests/datacheck_keys/key_helpers.R', local = TRUE)"
PY_H = "__import__('tests.datacheck_keys.key_helpers', fromlist=['_'])"

ALPHABETS = {
    "hex": "0123456789abcdef",
    "an": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    "an_": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_",
    "an_-": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-",
    "up27": "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567",
    "digits": "0123456789",
    "letters": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
}

# each key: literal text and random runs (length, alphabet) in the form of the provider's keys
TYPES: dict[str, list[Any]] = {
    "openrouter-api-key": [["lit", "sk-or-v1-"], ["rand", 64, "hex"]],
    "github-pat": [["lit", "ghp_"], ["rand", 36, "an"]],
    "github-fine-grained-pat": [["lit", "github_pat_"], ["rand", 82, "an_"]],
    "aws-access-token": [["lit", "AKIA"], ["rand", 16, "up27"]],
    "slack-bot-token": [
        ["lit", "xoxb-"],
        ["rand", 12, "digits"],
        ["lit", "-"],
        ["rand", 12, "digits"],
        ["lit", "-"],
        ["rand", 24, "an"],
    ],
    "stripe-access-token": [["lit", "sk_live_"], ["rand", 24, "an"]],
    "anthropic-api-key": [["lit", "sk-ant-api03-"], ["rand", 93, "an_-"], ["lit", "AA"]],
    "gcp-api-key": [["lit", "AIza"], ["rand", 35, "an_-"]],
    "huggingface-access-token": [["lit", "hf_"], ["rand", 34, "letters"]],
    "openai-api-key": [
        ["lit", "sk-proj-"],
        ["rand", 58, "an_-"],
        ["lit", "T3BlbkFJ"],
        ["rand", 58, "an_-"],
    ],
    "gitlab-pat": [["lit", "glpat-"], ["rand", 20, "an"]],
    "groq-api-key": [["lit", "gsk_"], ["rand", 52, "an"]],
}


def k(t: str) -> str:
    return f"@@KEY:{t}@@"


def qsf(js: str) -> str:
    survey = {
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
                    "QuestionJS": js,
                },
            }
        ],
    }
    return json.dumps(survey)


def csv(rows: list[list[Any]]) -> str:
    return "".join(",".join(f'"{v}"' for v in r) + "\n" for r in rows)


def qualtrics_csv(extra_col: str, value: str) -> str:
    """The Qualtrics export of the data_check fixtures, with one more column of embedded data."""
    src = (ROOT / "tests/mod_data_check/fixtures/repos/qualtrics/data/survey.csv").read_text()
    rows = list(csv_mod.reader(io.StringIO(src)))
    imp = '{"ImportId":"ED"}'
    rows[0].append(extra_col)
    rows[1].append(extra_col)
    rows[2].append(imp)
    for r in rows[3:]:
        r.append(value)
    out = io.StringIO()
    csv_mod.writer(out, quoting=csv_mod.QUOTE_ALL, lineterminator="\n").writerows(rows)
    return out.getvalue()


def plain_csv(n: int = 20) -> str:
    return csv([["id", "worry"]]) + "".join(f'"{i}","{1 + i % 2}"\n' for i in range(1, n + 1))


REPOS: dict[str, dict[str, str]] = {
    # code, a survey definition and a settings file with keys; a key in a hidden file and in the
    # config of the repository's own .git folder; a data file and a README without any
    "local": {
        "code/analysis.R": f'library(httr)\nkey <- "{k("openrouter-api-key")}"\nx <- read.csv("data.csv")\n',
        "survey.qsf": qsf(
            'fetch(u, {headers: {"Authorization": "Bearer ' + k("anthropic-api-key") + '"}})'
        ),
        ".env": f"GITHUB_TOKEN={k('github-pat')}\nMODE=test\n",
        ".git/config": f'[remote "origin"]\n\turl = https://{k("github-pat")}@github.com/lab/study.git\n',
        ".git/HEAD": "ref: refs/heads/main\n",
        "data.csv": plain_csv(),
        "README.md": "# Study\n\nRun analysis.R. No keys here.\n",
    },
    # a Qualtrics survey whose key as embedded data is in every response (6qf9p_v3), and the
    # same key in its survey definition
    "survey": {
        "data/responses.csv": qualtrics_csv(
            "OpenRouterAPIKey", "Bearer " + k("openrouter-api-key")
        ),
        "survey.qsf": qsf(f'var key = "{k("openrouter-api-key")}";'),
        "README.md": "# Survey\n",
    },
    # a key typed by one participant far down a table is not searched for
    "late": {
        "data/answers.csv": csv([["id", "answer"]])
        + "".join(f'"{i}","ok"\n' for i in range(1, 60))
        + f'"60","my key {k("groq-api-key")}"\n',
        "code/clean.R": "d <- read.csv('data/answers.csv')\n",
    },
    # a rule made for some files only: a Terraform password, in .tf and in .java files
    "tf": {
        "infra/main.tf": 'password = "x7Kq2-Lm9Pz"\n',
        "src/Test.java": 'password = "x7Kq2-Lm9Pz"\n',
        "data.csv": plain_csv(5),
    },
    # no key anywhere: the report and the light are as before
    "clean": {
        "analysis.R": "d <- read.csv('data.csv')\nsummary(lm(worry ~ id, d))\n",
        "data.csv": plain_csv(),
        "notes.txt": "KeyError: 'response_time' in file data_cleaning.py; api_key = 'YOUR_API_KEY'\n",
    },
}

SPEC = {"alphabets": ALPHABETS, "types": TYPES, "repos": REPOS}


def call(cid: str, fn: str, **extra: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": cid,
        "r": "base::identity",
        "py": "tests.mod_data_check.helpers.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": f"local({{{R_SRC}; {fn}}})",
                    "py": f"{PY_H}.{fn}",
                }
            }
        },
    }
    case.update(extra)
    return case


def cases() -> list[dict[str, Any]]:
    out = [
        call("keys.rules", "kc_rules()"),
        call("keys.find.types", "kc_find_types()"),
        call("keys.find.negatives", "kc_find_negatives()"),
        call("keys.find.long", "kc_find_long()"),
        call("keys.find.encoded", "kc_find_encoded()"),
        call("keys.find.path_rule", "kc_find_path_rule()"),
        call("keys.remove", "kc_remove()"),
        call("keys.scan_files", "kc_scan_files()"),
        call("keys.scan_tables", "kc_scan_tables()"),
    ]
    for name in REPOS:
        out.append(call(f"keys.scan_repo.{name}", f"kc_scan_repo({name!r})"))
        out.append(call(f"data_check.keys.{name}", f"kc_run_repo({name!r})"))
    return out


def main() -> None:
    (HERE / "key_spec.json").write_text(
        json.dumps(SPEC, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    header = (
        "# Key scanner parity cases (R/keys.R, inst/modules/data_check.R, #607), generated by\n"
        "# tests/datacheck_keys/gen_parity_cases.py -- edit that script, not this file.\n"
        "# Keys are made when a case runs (key_helpers.R / key_helpers.py): none is in a file here.\n"
    )
    body = yaml.safe_dump(
        {"area": "datacheck_keys", "cases": cases()},
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_style='"',
    )
    (ROOT / "parity" / "cases" / "datacheck_keys.yaml").write_text(header + body, encoding="utf-8")


if __name__ == "__main__":
    main()
