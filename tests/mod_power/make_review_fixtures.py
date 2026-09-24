"""Writes tests/mod_power/mocks_review/ and parity/cases/mod_power_review.yaml.

Run from the repo root::

    .venv/bin/python tests/mod_power/make_review_fixtures.py
    .venv/bin/python -m parity generate --area mod_power_review

These cases cover what ``mod_power`` leaves out, in particular the
prompt-based fallback of ``.power_llm_extract()``. That path downloads the
JSON schema with base R ``readLines(url)``, which httptest2 does not
intercept, so every LLM case here runs with ``base::readLines`` traced to
read a local copy of the schema instead (R), while Python replays the same
file as the recorded response of that URL. Both sides therefore build the
same system prompt, offline and deterministically.

Provider replies are chosen by a substring of the paragraph (see ``REPLIES``):
``structured`` answers a structured-output request (an ``int`` is an HTTP
error status), ``fallback`` answers the prompt-based one (the reply text,
or an ``int``). Mock paths are computed by running the Python port with a
recording router, as ``make_fixtures.py`` does; a request whose body differs
from R's makes the R golden fail (check that every golden is ``ok``).
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.mod_power import scenarios as S
from tests.mod_power.make_fixtures import (
    GROQ_400,
    R_TABLES,
    H,
    P,
    groq,
    paras,
    plist,
    r_error_response,
    tp,
)

AREA = "mod_power_review"
MOCK_DIR = "tests/mod_power/mocks_review"
MOCKS = ROOT / MOCK_DIR
SCHEMA_URL = "https://scienceverse.org/schema/power.json"
SCHEMA_FILE = f"{MOCK_DIR}/scienceverse.org/schema/power.json.json"

# -- texts ----------------------------------------------------------------------

FB_COMPLETE = (
    "An a priori power analysis in G*Power showed that 50 participants per group give 80% "
    "power (alpha = .05) for d = 0.5 in an unpaired t-test."
)
FB_NONE = "Power was not analysed a priori; the sample of 120 was a convenience sample."
FB_FAIL = "A sensitivity power analysis for 80 participants always fails in both extraction modes."
FB_OMIT = (
    "A sensitivity power analysis indicated that 40 participants give 90% power for d = 0.8 "
    "with pwr."
)
FB_GARBLED = "The post hoc power for the 25 participants was reported as 0.30 in the supplement."
FB_TEXTKEY = (
    "Two power analyses were run: an a priori analysis (n = 60, 80% power, r = 0.6) and a "
    "sensitivity analysis (n = 60, alpha = .05, 90% power) for our correlation."
)
FB_OBJECT = (
    "A compromise power analysis with 30 participants balanced alpha and beta errors at 0.10."
)
FB_EMPTY = "Our 20 volunteers had the power to detect nothing at all."
FB_POSTHOC = "The observed power was 0.45 for the effect in 36 participants (post-hoc)."
DUP = (
    "An a priori power analysis for the 2 x 2 design required 200 participants for 80% power "
    "and f = 0.2, while a sensitivity power analysis showed 90% power for f = 0.25."
)
NULL_TYPE = (
    "A power analysis (see Table 2) was reported in 2 earlier studies, and our a priori power "
    "analysis required 90 participants for 95% power at alpha = .01 (d = 0.8, G*Power, "
    "paired t-test)."
)
NULL_ONLY = "Power analyses for 3 pilot studies are reported in the supplement."
# structured replies a non-validating provider could send
OUT_OF_ENUM = (
    "An a priori power analysis (ANCOVA, 150 participants, 80% power, alpha = .05, f = 0.25) "
    "was run in R."
)
OUT_OF_ENUM_NONE = "No power analysis was run; 48 participants were tested for 2 weeks."
EXTRA_KEYS = (
    "A sensitivity power analysis showed that 70 participants give 80% power for d = 0.6 "
    "(alpha = .05, one-sample t-test, Superpower)."
)
NO_WRAPPER = "The a priori power analysis targeted 95% power with 44 participants."
BARE_ARRAY = "The achieved power was 0.62 for the 18 participants of the pilot."
STRING_NUMBERS = (
    "An a-priori power analysis gave 34 participants for 85% power (alpha = 0.05, d = 0.9, "
    "PASS, correlation)."
)

# control characters: llm() strips them from the text it sends, then joins the
# replies back on the unstripped paragraph text
CONTROL = (
    "A sensitivity power\x02 analysis with the CONTROL sample of 64 showed 80% power for "
    "d = 0.5 (alpha = .05, unpaired t-test, pwr)."
)
FB_CONTROL = (
    "An a priori power\x01 analysis for the FALLBACK CONTROL sample gave 52 participants for "
    "80% power."
)

# regex classification: case, Unicode and number-format variants
UNICODE = [
    "The POWERED design had 80% POWER for Post Hoc tests.",
    "G*Power 3.1 was used (power = .80).",
    "A powerful design had 90% power to detect small effects.",
    "ΣΑΣ power analysis: 40 participants, a-priori, 0,80.",
    "Statistical power was high (N = 1999) in the RETROSPECTIVE analysis.",
    "A Sensitivity and a priori power analysis used 12 participants.",
]

# -- provider replies --------------------------------------------------------------


def fenced(x: Any) -> str:
    """An unstructured reply: JSON bracketed by ```json and ```."""
    return "```json\n" + json.dumps(x, indent=2, ensure_ascii=False) + "\n```"


FULL = S.pa(
    power_type="apriori",
    statistical_test="unpaired t-test",
    sample_size=100,
    alpha_level=0.05,
    power=0.8,
    effect_size=0.5,
    effect_size_metric="Cohen's d",
    software="G*Power",
)

REPLIES: dict[str, dict[str, Any]] = {
    FB_COMPLETE[:40]: {"structured": 400, "fallback": fenced([FULL])},
    FB_NONE[:40]: {"structured": 400, "fallback": fenced([S.pa(power_type="none")])},
    FB_FAIL[:40]: {"structured": 400, "fallback": 400},
    FB_OMIT[:40]: {
        "structured": 400,
        "fallback": fenced(
            [
                S.pa(
                    omit=("alpha_level",),
                    power_type="sensitivity",
                    statistical_test="unpaired t-test",
                    sample_size=40,
                    power=0.9,
                    effect_size=0.8,
                    effect_size_metric="Cohen's d",
                    software="pwr",
                )
            ]
        ),
    },
    FB_GARBLED[:40]: {"structured": 400, "fallback": "Sorry, I cannot help with that."},
    FB_TEXTKEY[:40]: {
        "structured": 400,
        "fallback": fenced(
            [
                {
                    "text": "an a priori analysis (n = 60, 80% power, r = 0.6)",
                    **S.pa(
                        power_type="apriori",
                        statistical_test="correlation",
                        sample_size=60,
                        power=0.8,
                        effect_size=0.6,
                        effect_size_metric="other",
                        effect_size_metric_other="Pearson's r",
                        software="null",
                    ),
                },
                {
                    "text": "a sensitivity analysis (n = 60, alpha = .05, 90% power)",
                    **S.pa(
                        power_type="sensitivity",
                        statistical_test="correlation",
                        sample_size=60,
                        alpha_level=0.05,
                        power=0.9,
                    ),
                },
            ]
        ),
    },
    FB_OBJECT[:40]: {
        "structured": 400,
        "fallback": fenced(
            S.pa(power_type="unknown", sample_size=30, alpha_level=0.1, software="PASS")
        ),
    },
    FB_EMPTY[:40]: {"structured": 400, "fallback": fenced([])},
    FB_POSTHOC[:40]: {
        "structured": 400,
        "fallback": fenced(
            [
                S.pa(
                    power_type="posthoc",
                    statistical_test="regression",
                    sample_size=36,
                    alpha_level=0.05,
                    power=0.45,
                    effect_size=0.3,
                    effect_size_metric="eta squared",
                    software="other",
                )
            ]
        ),
    },
    DUP[:40]: {
        "structured": [
            S.pa(
                power_type="apriori",
                statistical_test="2-way ANOVA",
                sample_size=200,
                power=0.8,
                effect_size=0.2,
                effect_size_metric="Cohen's f",
            ),
            S.pa(
                power_type="sensitivity",
                statistical_test="2-way ANOVA",
                sample_size=200,
                power=0.9,
                effect_size=0.25,
                effect_size_metric="Cohen's f",
            ),
        ]
    },
    NULL_TYPE[:40]: {
        "structured": [
            S.pa(power_type=None, sample_size=2),
            S.pa(
                power_type="apriori",
                statistical_test="paired t-test",
                sample_size=90,
                alpha_level=0.01,
                power=0.95,
                effect_size=0.8,
                effect_size_metric="Cohen's d",
                software="G*Power",
            ),
        ]
    },
    NULL_ONLY[:40]: {"structured": [S.pa(power_type=None, sample_size=3)]},
    "the CONTROL sample of 64": {
        "structured": [
            S.pa(
                power_type="sensitivity",
                statistical_test="unpaired t-test",
                sample_size=64,
                alpha_level=0.05,
                power=0.8,
                effect_size=0.5,
                effect_size_metric="Cohen's d",
                software="pwr",
            )
        ]
    },
    "FALLBACK CONTROL sample": {"structured": 400, "fallback": fenced([FULL])},
    OUT_OF_ENUM[:40]: {
        "structured": [
            S.pa(
                power_type="apriori",
                statistical_test="ANCOVA",
                sample_size=150,
                alpha_level=0.05,
                power=0.8,
                effect_size=0.25,
                effect_size_metric="Cohen's f",
                software="R",
            )
        ]
    },
    OUT_OF_ENUM_NONE[:40]: {"structured": [S.pa(power_type="none", sample_size=48)]},
    EXTRA_KEYS[:40]: {
        "structured": [
            {
                "text": "70 participants give 80% power for d = 0.6",
                **S.pa(
                    power_type="sensitivity",
                    statistical_test="one-sample t-test",
                    sample_size=70,
                    alpha_level=0.05,
                    power=0.8,
                    effect_size=0.6,
                    effect_size_metric="Cohen's d",
                    software="Superpower",
                ),
                "notes": "clear",
            }
        ]
    },
    NO_WRAPPER[:40]: {"structured_raw": {}},
    BARE_ARRAY[:40]: {"structured_raw": [S.pa(power_type="posthoc", power=0.62, sample_size=18)]},
    STRING_NUMBERS[:40]: {
        "structured": [
            S.pa(
                power_type="apriori",
                statistical_test="correlation",
                sample_size="34",
                alpha_level="0.05",
                power=0.85,
                effect_size=0.9,
                effect_size_metric="Cohen's d",
                software="PASS",
            )
        ]
    },
}


class RawReply:
    """A structured-output reply sent as is (not wrapped in ``power_analyses``)."""

    def __init__(self, value: Any) -> None:
        self.value = value


def reply_for(text: str, structured: bool) -> Any:
    """The reply to a request for *text*: this file's, else ``scenarios.py``'s."""
    for key, value in REPLIES.items():
        if key in text:
            if structured and "structured_raw" in value:
                return RawReply(value["structured_raw"])
            return value.get("structured" if structured else "fallback", fenced([]))
    return S.reply_for(text) if structured else fenced([])


# -- R / Python expressions ----------------------------------------------------------

R_OPTS = {
    "metacheck.llm.use": "TRUE",
    "metacheck.llm.cache": "FALSE",
    "metacheck.llm_reasoning": "NULL",
    "metacheck.llm_max_tokens": "NULL",
    "metacheck.llm_max_calls": "30L",
    "metacheck.llm.model": "'groq/test-model'",
}


def r_traced(expr: str) -> str:
    """R: *expr* with ``readLines()`` of the schema URL reading the local copy."""
    return (
        "local({invisible(suppressMessages(trace(base::readLines, tracer = quote("
        f"if (identical(con, '{SCHEMA_URL}')) con <- '{SCHEMA_FILE}'), print = FALSE, "
        f"where = baseenv()))); tryCatch({expr}, finally = invisible(suppressMessages("
        "untrace(base::readLines, where = baseenv()))))})"
    )


def r_llm(expr: str, options: dict[str, str] | None = None, key: str = "'test-key'") -> str:
    opts = {**R_OPTS, **(options or {})}
    olist = "list(" + ", ".join(f"{k} = {v}" for k, v in opts.items()) + ")"
    return r_traced(
        f"withr::with_options({olist}, withr::with_envvar(c(GROQ_API_KEY = {key}), {expr}))"
    )


cases: list[dict[str, Any]] = []


def expr_case(id: str, r: str, py: str, **kw: Any) -> None:
    cases.append(
        {
            "id": id,
            "r": "base::identity",
            "py": "tests.mod_power.parity_helpers.identity",
            "args": {"x": {"$expr": {"r": r, "py": py}}},
            **kw,
        }
    )


def llm_case(
    id: str,
    paper: P,
    kwargs: str = "",
    py_kwargs: str = "",
    r_options: dict[str, str] | None = None,
    py_options: str = "None",
    key: str = "'test-key'",
    py_env: str = "None",
    error: bool = False,
    tables: bool = False,
) -> None:
    """A module run with the LLM on (``error``: compare the error message)."""
    run_r = r_llm(f"module_run({paper.r}, 'power'{kwargs})", r_options, key)
    run_py = f"{H}.run_llm_with({paper.py}, {py_options}, {py_env}{py_kwargs})"
    if error:
        run_r = f"tryCatch({run_r}, error = function(e) conditionMessage(e))"
        run_py = f"{H}.catch(lambda: {run_py})"
    if tables:
        run_r = R_TABLES.format(run=run_r)
        run_py = f"{H}.report_tables({run_py})"
    expr_case(id, run_r, run_py, mock_dir=MOCK_DIR)


# llm_use(FALSE): regex path ----
unicode = paras(UNICODE, list(range(len(UNICODE))))
expr_case(
    "power.review.unicode",
    f"module_run({unicode.r}, 'power')",
    f"pc.module_run({unicode.py}, 'power')",
)
expr_case(
    "power.review.unicode.tables",
    R_TABLES.format(run=f"module_run({unicode.r}, 'power')"),
    f"{H}.report_tables(pc.module_run({unicode.py}, 'power'))",
)
empty = tp()
expr_case(
    "power.review.empty_paper",
    f"module_run({empty.r}, 'power')",
    f"pc.module_run({empty.py}, 'power')",
)
expr_case(
    "power.review.empty_paperlist",
    "tryCatch(module_run(paperlist(), 'power'), error = function(e) conditionMessage(e))",
    f"{H}.catch(lambda: pc.module_run(pc.PaperList([]), 'power'))",
)
nohits = plist("I love to power pose.", "The 12 moths had no power.")
expr_case(
    "power.review.paperlist_none",
    f"module_run({nohits.r}, 'power')",
    f"pc.module_run({nohits.py}, 'power')",
)

# llm_use(TRUE), structured output ----
llm_case("power.review.llm.dup_paperlist", plist(DUP, DUP, S.COMPLETE))
llm_case("power.review.llm.dup_paperlist.tables", plist(DUP, DUP, S.COMPLETE), tables=True)
llm_case("power.review.llm.dup_paragraphs", paras([DUP, DUP], [0, 1]))
llm_case("power.review.llm.null_type", tp(NULL_TYPE))
llm_case("power.review.llm.null_only", tp(NULL_ONLY))
llm_case("power.review.llm.out_of_enum", tp(OUT_OF_ENUM))
llm_case("power.review.llm.out_of_enum_none", tp(OUT_OF_ENUM_NONE))
llm_case("power.review.llm.extra_keys", tp(EXTRA_KEYS))
llm_case("power.review.llm.extra_keys.tables", tp(EXTRA_KEYS), tables=True)
llm_case("power.review.llm.no_wrapper", tp(NO_WRAPPER))
llm_case("power.review.llm.bare_array", tp(BARE_ARRAY))
llm_case("power.review.llm.string_numbers", tp(STRING_NUMBERS))
llm_case("power.review.llm.control_char", tp(CONTROL))
llm_case("power.review.llm.control_char_mixed", paras([CONTROL, S.COMPLETE], [0, 1]))
llm_case("power.review.fallback.control_char", tp(FB_CONTROL))
llm_case("power.review.fallback.control_char_mixed", paras([FB_CONTROL, FB_COMPLETE], [0, 1]))
llm_case("power.review.llm.seed_null", tp(S.COMPLETE), ", seed = NULL", ", seed=None")
llm_case(
    "power.review.llm.no_key",
    tp(S.COMPLETE),
    key="NA",
    py_env="{'GROQ_API_KEY': None}",
)
llm_case(
    "power.review.llm.no_model",
    tp(S.COMPLETE),
    r_options={"metacheck.llm.model": "NULL"},
    py_options="{'metacheck.llm.model': None}",
    error=True,
)
llm_case(
    "power.review.llm.max_calls",
    paras([S.COMPLETE, S.OTHER], [0, 1]),
    r_options={"metacheck.llm_max_calls": "1L"},
    py_options="{'metacheck.llm_max_calls': __import__('pytacheck.llm._rds', "
    "fromlist=['_']).RInt(1)}",
    error=True,
)

# llm_use(TRUE), structured output rejected: prompt-based fallback ----
llm_case("power.review.fallback.complete", tp(FB_COMPLETE))
llm_case("power.review.fallback.complete.tables", tp(FB_COMPLETE), tables=True)
llm_case("power.review.fallback.none", tp(FB_NONE))
llm_case("power.review.fallback.all_fail", tp(FB_FAIL))
llm_case("power.review.fallback.omitted_key", tp(FB_OMIT))
llm_case("power.review.fallback.garbled", tp(FB_GARBLED))
llm_case("power.review.fallback.garbled_mixed", paras([FB_COMPLETE, FB_GARBLED], [0, 1]))
llm_case("power.review.fallback.text_key", tp(FB_TEXTKEY))
llm_case("power.review.fallback.text_key.tables", tp(FB_TEXTKEY), tables=True)
llm_case("power.review.fallback.object", tp(FB_OBJECT))
llm_case("power.review.fallback.empty", tp(FB_EMPTY))
llm_case("power.review.fallback.empty_mixed", paras([FB_EMPTY, FB_OMIT], [0, 1]))
llm_case("power.review.fallback.paperlist", plist(FB_COMPLETE, FB_NONE, FB_POSTHOC, FB_OMIT))
llm_case(
    "power.review.fallback.paperlist.tables",
    plist(FB_COMPLETE, FB_NONE, FB_POSTHOC, FB_OMIT),
    tables=True,
)
llm_case(
    "power.review.fallback.partial_http_fail",
    paras([FB_COMPLETE, FB_FAIL], [0, 1]),
    error=True,
)


# -- mocks ---------------------------------------------------------------------------


def record_mocks() -> dict[str, tuple[int, Any]]:
    """Run every LLM case in Python, answering (and recording) each Groq request."""
    import httpx
    import respx

    import pytacheck as pc
    from tests.httpmock import mock_path

    recorded: dict[str, tuple[int, Any]] = {}
    schema = (ROOT / SCHEMA_FILE).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == SCHEMA_URL:
            return httpx.Response(200, headers={"content-type": "application/json"}, content=schema)
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        text = user[0]["text"] if isinstance(user, list) else user
        structured = "response_format" in body
        reply = reply_for(text, structured)
        path = mock_path(request)
        if isinstance(reply, int):
            recorded[path] = (reply, GROQ_400)
            return httpx.Response(reply, json=GROQ_400)
        if isinstance(reply, RawReply):
            content = json.dumps(reply.value, ensure_ascii=False)
        elif structured:
            content = json.dumps({"power_analyses": reply}, ensure_ascii=False)
        else:
            content = reply
        payload = groq(content)
        recorded[path] = (200, payload)
        return httpx.Response(200, json=payload)

    ns = {"pc": pc, "__builtins__": __builtins__}
    for case in cases:
        if case.get("mock_dir") != MOCK_DIR:
            continue
        code = case["args"]["x"]["$expr"]["py"]
        with respx.mock(assert_all_called=False) as router, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            router.route().mock(side_effect=handler)
            eval(code, ns)
    return recorded


def write_schema() -> None:
    from pytacheck.modules._power import SCHEMA

    f = ROOT / SCHEMA_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(SCHEMA + "\n", encoding="utf-8")


def write_mocks(recorded: dict[str, tuple[int, Any]]) -> None:
    for path, (status, payload) in sorted(recorded.items()):
        if status == 200:
            f = MOCKS / f"{path}.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        else:
            f = MOCKS / f"{path}.R"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(r_error_response(status, payload), encoding="utf-8")


def write_cases() -> None:
    import yaml

    header = (
        "# Power module review parity cases (inst/modules/power.R), generated by\n"
        "# tests/mod_power/make_review_fixtures.py -- edit that script, not this file.\n"
        "# LLM cases replay tests/mod_power/mocks_review; R reads the fallback's JSON\n"
        "# schema from the same directory (base::readLines is traced), Python replays it.\n"
    )

    class Q(yaml.SafeDumper):
        pass

    def str_rep(dumper: yaml.SafeDumper, data: str) -> Any:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    Q.add_representer(str, str_rep)
    text = yaml.dump(
        {"area": AREA, "cases": cases},
        Dumper=Q,
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    )
    (ROOT / "parity" / "cases" / f"{AREA}.yaml").write_text(header + text, encoding="utf-8")


if __name__ == "__main__":
    write_schema()
    write_cases()
    write_mocks(record_mocks())
