"""Generate ``parity/cases/keys.yaml`` (R/keys.R, metacheck #607).

Run ``python tests/keys/make_parity_cases.py``, then ``python -m parity generate --area keys``.

The keys are made here from a fixed seed and written into the cases in pieces of 8
characters (``paste0("ghp_", "kJ3mQ9xL", ...)``), so that no file of the repository holds one
whole: GitHub refuses a push with a key it recognises. The goldens hold only the type of
key and the beginning of it.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "keys.yaml"
HELPERS_R = 'source(file.path(root, "tests/keys/helpers.R")); '
SUPPORT = "__import__('tests.keys.parity_support', fromlist=['x'])"

HEX = "0123456789abcdef"
AN = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


class Dumper(yaml.SafeDumper):
    pass


def _str(d: yaml.SafeDumper, s: str) -> yaml.Node:
    return d.represent_scalar("tag:yaml.org,2002:str", s, style='"')


Dumper.add_representer(str, _str)

CASES: list[dict[str, Any]] = []


def make_keys(seed: int = 1) -> dict[str, str]:
    rng = random.Random(seed)

    def r(n: int, chars: str) -> str:
        return "".join(rng.choice(chars) for _ in range(n))

    return {
        "openrouter-api-key": "sk-or-v1-" + r(64, HEX),
        "github-pat": "ghp_" + r(36, AN),
        "github-fine-grained-pat": "github_pat_" + r(82, AN + "_"),
        "aws-access-token": "AKIA" + r(16, "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"),
        "slack-bot-token": f"xoxb-{r(12, '0123456789')}-{r(12, '0123456789')}-{r(24, AN)}",
        "stripe-access-token": "sk_live_" + r(24, AN),
        "anthropic-api-key": "sk-ant-api03-" + r(93, AN + "_-") + "AA",
        "gcp-api-key": "AIza" + r(35, AN + "_-"),
        "huggingface-access-token": "hf_"
        + r(34, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"),
        "openai-api-key": "sk-proj-" + r(58, AN + "_-") + "T3BlbkFJ" + r(58, AN + "_-"),
        "gitlab-pat": "glpat-" + r(20, AN),
        "groq-api-key": "gsk_" + r(52, AN),
    }


KEYS = make_keys()


def pieces(s: str, n: int = 8) -> list[str]:
    return [s[i : i + n] for i in range(0, len(s), n)]


class T:
    """A text with keys in it, written to R and to Python in pieces."""

    def __init__(self, *parts: str | tuple[str]) -> None:
        self.parts = parts  # a str is text, a 1-tuple is the name of a key

    def r(self) -> str:
        out = []
        for p in self.parts:
            if isinstance(p, tuple):
                out += [json.dumps(x) for x in pieces(KEYS[p[0]])]
            else:
                out.append(json.dumps(p))
        return "paste0(" + ", ".join(out) + ")"

    def py(self) -> str:
        out = []
        for p in self.parts:
            if isinstance(p, tuple):
                out += [repr(x) for x in pieces(KEYS[p[0]])]
            else:
                out.append(repr(p))
        return "(" + " + ".join(out) + ")"


class Raw:
    """A line written as R and Python code (a placeholder that is built, not spelled out)."""

    def __init__(self, r: str, py: str) -> None:
        self._r, self._py = r, py

    def r(self) -> str:
        return self._r

    def py(self) -> str:
        return self._py


def k(name: str) -> tuple[str]:
    return (name,)


def r_lines(lines: list[Any]) -> str:
    return "c(" + ", ".join(x.r() if isinstance(x, T | Raw) else json.dumps(x) for x in lines) + ")"


def py_lines(lines: list[Any]) -> str:
    return "[" + ", ".join(x.py() if isinstance(x, T | Raw) else repr(x) for x in lines) + "]"


def r_files(files: dict[str, list[Any]]) -> str:
    return "list(" + ", ".join(f"{json.dumps(n)} = {r_lines(ls)}" for n, ls in files.items()) + ")"


def py_files(files: dict[str, list[Any]]) -> str:
    return "{" + ", ".join(f"{n!r}: {py_lines(ls)}" for n, ls in files.items()) + "}"


def case(
    id_: str,
    r_expr: str,
    py_expr: str,
    note: str | None = None,
    ignore: list[str] | None = None,
) -> None:
    c: dict[str, Any] = {"id": id_}
    if note:
        c["note"] = note
    c.update(
        {
            "r": "base::identity",
            "py": "tests.keys.parity_support.identity",
            "args": {"x": {"$expr": {"r": HELPERS_R + r_expr, "py": f"{SUPPORT}.{py_expr}"}}},
        }
    )
    if ignore:
        c["compare"] = {"ignore": ignore}
    CASES.append(c)


def scan_case(id_: str, files: dict[str, list[Any]], note: str | None = None) -> None:
    case(id_, f"kp_scan({r_files(files)})", f"kp_scan({py_files(files)})", note)


# one key of each common type, in the forms code and files write it
for name in KEYS:
    forms = {
        "plain.txt": [T("my key is ", k(name), " for the API.")],
        "code.R": [T('api_key <- "', k(name), '"')],
        "header.json": [T('"Authorization": "Bearer ', k(name), '"')],
        "obj.json": [T('{"key":"', k(name), '","n":1}')],
    }
    scan_case(f"keys.scan_files.type.{name}", forms)

scan_case(
    "keys.scan_files.two_keys_one_line",
    {"two.txt": ["a", "b", T("k1 = ", k("openrouter-api-key"), " ; k2 = ", k("aws-access-token"))]},
    "two keys on one line; the same key in two files has one number",
)
scan_case(
    "keys.scan_files.same_key_two_files",
    {
        "a.R": [T("token <- '", k("github-pat"), "'")],
        "b/config.yml": ["x: 1", T("token: ", k("github-pat"))],
        "c.txt": [T("other ", k("gitlab-pat"))],
    },
)
scan_case(
    "keys.scan_files.placeholders",
    {
        "p.txt": [
            Raw('paste0("sk-or-v1-", strrep("x", 64))', '("sk-or-v1-" + "x" * 64)'),
            Raw('paste0("ghp_", strrep("a", 36))', '("ghp_" + "a" * 36)'),
            "api_key = 'YOUR_API_KEY'",
            "token: ${API_KEY}",
            "key = {{ secrets.KEY }}",
            "AKIAIOSFODNN7EXAMPLE",
            "The participants were asked to rate the stimuli.",
            "KeyError: 'response_time' in file data_cleaning.py",
        ]
    },
)
scan_case(
    "keys.scan_files.terraform_rule_by_path",
    {"main.tf": ['password = "x7Kq2-Lm9Pz"'], "Test.java": ['password = "x7Kq2-Lm9Pz"']},
    "a rule made for some files only is tried on those files only",
)
scan_case(
    "keys.scan_files.long_file",
    {
        "long.csv": ["participant,condition,1.234"] * 2000
        + [T("key = ", k("openrouter-api-key"))]
        + ["x,y"] * 3000
    },
    "a key far into a long text, on its line",
)
scan_case(
    "keys.scan_files.hidden_name",
    {".env": [T("GITHUB_TOKEN=", k("github-pat")), "MODE=test"]},
)

# .key_find: positions and types only
case(
    "keys.find.positions",
    "kp_find(" + T("a key ", k("github-pat"), " and ", k("gitlab-pat"), " here").r() + ")",
    "kp_find(" + T("a key ", k("github-pat"), " and ", k("gitlab-pat"), " here").py() + ")",
)
case(
    "keys.find.path_rule_with_path",
    "kp_find('password = \"x7Kq2-Lm9Pz\"', path = 'main.tf')",
    "kp_find('password = \"x7Kq2-Lm9Pz\"', path='main.tf')",
)
case(
    "keys.find.path_rule_without_path",
    "kp_find('password = \"x7Kq2-Lm9Pz\"')",
    "kp_find('password = \"x7Kq2-Lm9Pz\"')",
)
case("keys.find.na", "kp_find(NA_character_)", "kp_find(None)")
case("keys.find.empty", 'kp_find("")', 'kp_find("")')
case(
    "keys.find.encoded_run_masked",
    "kp_find(paste0('<img src=\"data:image/png;base64,', strrep('Ab3/', 300), '/BSA', strrep('xY7q', 8), '/', "
    "strrep('Cd9+', 300), '\">  token ', " + T(k("github-pat")).r() + "))",
    "kp_find('<img src=\"data:image/png;base64,' + 'Ab3/' * 300 + '/BSA' + 'xY7q' * 8 + '/' + "
    "'Cd9+' * 300 + '\">  token ' + " + T(k("github-pat")).py() + ")",
    "an unbroken run of 1,000 base64 characters or more is not searched",
)

# .key_remove
case(
    "keys.remove.vector",
    "kp_remove(c('text', " + T("found ", k("openrouter-api-key")).r() + "))",
    "kp_remove(['text', " + T("found ", k("openrouter-api-key")).py() + "])",
)
case(
    "keys.remove.data_frame",
    "kp_remove(data.frame(a = c('x', "
    + T(k("github-pat")).r()
    + "), b = 1:2, stringsAsFactors = FALSE))",
    "kp_remove(__import__('pandas').DataFrame({'a': ['x', "
    + T(k("github-pat")).py()
    + "], 'b': [1, 2]}))",
)
case(
    "keys.remove.two_keys_one_text",
    "kp_remove(" + T("k1=", k("aws-access-token"), " k2=", k("gcp-api-key")).r() + ")",
    "kp_remove(" + T("k1=", k("aws-access-token"), " k2=", k("gcp-api-key")).py() + ")",
)
case(
    "keys.remove.none",
    "kp_remove(list(report = 'nothing here', n = 1L))",
    "kp_remove({'report': 'nothing here', 'n': 1})",
)

# .key_scan_tables
case(
    "keys.scan_tables.survey_key_in_every_response",
    "kp_tables(list(responses.csv = data.frame(StartDate = sprintf('2026-01-0%d', 1:9), Q1 = 'How worried?', "
    "OpenRouterAPIKey = paste('Bearer', "
    + T(k("openrouter-api-key")).r()
    + "), stringsAsFactors = FALSE), "
    "late.csv = data.frame(id = 1:500, answer = c(rep('ok', 498), paste('my key', "
    + T(k("openrouter-api-key")).r()
    + "), 'ok'), stringsAsFactors = FALSE)))",
    "kp_tables({'responses.csv': __import__('pandas').DataFrame({'StartDate': [f'2026-01-0{i}' for i in range(1, 10)], "
    "'Q1': 'How worried?', 'OpenRouterAPIKey': 'Bearer ' + "
    + T(k("openrouter-api-key")).py()
    + "}), 'late.csv': __import__('pandas').DataFrame({'id': list(range(1, 501)), 'answer': ['ok'] * 498 + ['my key ' + "
    + T(k("openrouter-api-key")).py()
    + ", 'ok']})})",
    "a key in the first 3 rows is found; one far down a table is not searched for",
)

# data_check on a local folder
dc_files = {
    "code/analysis.R": ["library(httr)", T('key <- "', k("openrouter-api-key"), '"')],
    ".env": [T("GITHUB_TOKEN=", k("github-pat")), "MODE=test"],
    "notes.txt": ["no keys here"],
}
dc_data = "list('data.csv' = data.frame(id = 1:20, worry = rep(1:2, 10)))"
dc_data_py = (
    "{'data.csv': __import__('pandas').DataFrame({'id': list(range(1, 21)), 'worry': [1, 2] * 10})}"
)
case(
    "keys.data_check.keys_in_files",
    f"kp_data_check({r_files(dc_files)}, {dc_data})",
    f"kp_data_check({py_files(dc_files)}, {dc_data_py})",
    "data_check turns red and names the keys by their beginning only",
    ignore=["keys.repo_url"],  # the temporary folder
)
case(
    "keys.data_check.no_keys",
    "kp_data_check(list('a.R' = 'x <- read.csv(\"data.csv\")'), "
    "list('data.csv' = data.frame(id = 1:5, x = 1:5)))",
    "kp_data_check({'a.R': ['x <- read.csv(\"data.csv\")']}, "
    "{'data.csv': __import__('pandas').DataFrame({'id': list(range(1, 6)), 'x': list(range(1, 6))})})",
)

# the rules themselves
case("keys.rules.names", "kp_rules()", "kp_rules()")

doc = {"area": "keys", "cases": CASES}
header = (
    "# Parity cases for R/keys.R and data_check's key search (metacheck #607).\n"
    "# Generated by tests/keys/make_parity_cases.py -- do not edit by hand.\n"
    "# The keys are cut into pieces of 8 characters; the goldens hold only each key's type\n"
    "# and beginning.\n"
)
OUT.write_text(
    header + yaml.dump(doc, Dumper=Dumper, sort_keys=False, width=100000), encoding="utf-8"
)
print(f"{len(CASES)} cases -> {OUT}")
