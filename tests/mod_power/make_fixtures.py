"""Writes tests/mod_power/mocks/ and parity/cases/mod_power.yaml (run from the repo root):

    .venv/bin/python tests/mod_power/make_fixtures.py

The mocks are hand-made Groq replies (``tests/mod_power/scenarios.py``) stored
under the httptest2 path of the request metacheck's ``llm()`` makes for each
paragraph; the path is computed by running the Python port with a recording
router (its request bodies, hence the paths, are identical to R's: a wrong
path makes the R golden fail). Then regenerate the goldens with
``python -m parity generate --area mod_power``.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.mod_power import scenarios as S

MOCKS = ROOT / "tests" / "mod_power" / "mocks"
H = "__import__('tests.mod_power.parity_helpers', fromlist=['_'])"
GURL = "https://api.groq.com/openai/v1/chat/completions"
FIX = "upstream/metacheck/tests/testthat/fixtures"
PSYCHSCI = [
    f"{FIX}/psychsci/0956797613520608.json",
    f"{FIX}/psychsci/0956797614522816.json",
    f"{FIX}/psychsci/0956797614527830.json",
    f"{FIX}/problems/0956797617737129.xml",
    f"{FIX}/problems/0956797615569889.xml",
    "upstream/metacheck/inst/demos/to_err_is_human.json",
]
FORMATS = [
    f"{FIX}/formats/preprint.pdf.tei.xml",
    f"{FIX}/formats/published.pdf.tei.xml",
    f"{FIX}/formats/published.cermine.xml",
]


# -- R / Python paper constructors ---------------------------------------------


def rq(s: str) -> str:
    """An R string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def rvec(texts: list[str]) -> str:
    return (texts and ("c(" + ", ".join(rq(t) for t in texts) + ")")) or "character(0)"


class P:
    """A paper expression on both sides."""

    def __init__(self, r: str, py: str) -> None:
        self.r, self.py = r, py


def _r_test_paper(texts: list[str], pid: str, ids: list[int] | None = None) -> str:
    """R: a test paper with a fixed id (test_paper() ids come from the clock)."""
    code = f"p <- test_paper({rvec(texts)}); "
    if ids is not None:
        code += "p$text$paragraph_id <- c(" + ", ".join(f"{i}L" for i in ids) + "); "
    return f"local({{{code}p$paper_id <- {rq(pid)}; p}})"


def tp(*texts: str, pid: str = "p1") -> P:
    return P(_r_test_paper(list(texts), pid), f"{H}.tp({list(texts)!r}, {pid!r})")


def paras(texts: list[str], ids: list[int], pid: str = "p1") -> P:
    return P(_r_test_paper(texts, pid, ids), f"{H}.paragraphs({texts!r}, {ids!r}, {pid!r})")


def plist(*texts: str) -> P:
    """``paperlist()`` of one-sentence test papers ``p1``, ``p2``, ..."""
    return P(
        "paperlist("
        + ", ".join(_r_test_paper([t], f"p{i}") for i, t in enumerate(texts, start=1))
        + ")",
        f"{H}.papers(*{list(texts)!r})",
    )


def read(files: list[str]) -> P:
    rfiles = "c(" + ", ".join(rq(f) for f in files) + ")"
    return P(f"read({rfiles})", f"{H}.read({files!r})")


DEMO = P("demopaper()", "pc.demopaper()")

R_TABLES = (
    "local({{mo <- {run}; chunks <- Filter(function(s) grepl('```{{r}}', s, fixed = TRUE), "
    "mo$report); lapply(chunks, function(s) {{code <- sub('{{r}}', '', "
    "strsplit(s, '```', fixed = TRUE)[[1]][2], fixed = TRUE); e <- new.env(); "
    "eval(parse(text = code)[[1]], e); as.data.frame(e$table)}})}})"
)


def r_llm(expr: str) -> str:
    """R: *expr* with the LLM options and a fake Groq key."""
    from tests.mod_power.parity_helpers import LLM_ENV_R, LLM_OPTIONS_R

    return f"withr::with_options({LLM_OPTIONS_R}, withr::with_envvar({LLM_ENV_R}, {expr}))"


def args_r(kwargs: dict[str, Any]) -> str:
    return "".join(f", {k} = {v!r}" for k, v in kwargs.items())


def args_py(kwargs: dict[str, Any]) -> str:
    return "".join(f", {k}={v!r}" for k, v in kwargs.items())


# -- cases -------------------------------------------------------------------

cases: list[dict[str, Any]] = []


def module_case(id: str, paper: Any, **kw: Any) -> None:
    cases.append({"id": id, "module": "power", "args": {"paper": paper}, **kw})


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


def tables_case(id: str, paper: P, llm: bool = False, **kw: Any) -> None:
    run_r = f"module_run({paper.r}, 'power')"
    if llm:
        run_r = r_llm(run_r)
        py = f"{H}.run_llm_tables({paper.py})"
    else:
        py = f"{H}.report_tables(pc.module_run({paper.py}, 'power'))"
    expr_case(id, R_TABLES.format(run=run_r), py, **kw)


def llm_case(id: str, paper: P, kwargs: dict[str, Any] | None = None, **kw: Any) -> None:
    kwargs = kwargs or {}
    expr_case(
        id,
        r_llm(f"module_run({paper.r}, 'power'{args_r(kwargs)})"),
        f"{H}.run_llm({paper.py}{args_py(kwargs)})",
        mock_dir="tests/mod_power/mocks",
        **kw,
    )


# llm_use(FALSE): regex classification ----
module_case("power.demo", {"$paper": "demo"})
module_case("power.psychsci", {"$read": PSYCHSCI})
module_case("power.formats", {"$read": FORMATS})
classify = paras([*S.CLASSIFY, S.CLASSIFY[7]], list(range(len(S.CLASSIFY) + 1)))
for cid, paper in [
    ("power.none", tp("I love to power pose.")),
    ("power.one_paragraph", tp(*S.POWER_TEXT)),
    ("power.false_positive", tp(S.MOTH)),
    ("power.no_number", tp("A power analysis was run in 2019 (see Smith, 2020).")),
    ("power.two_paragraphs", paras(S.POWER_TEXT, [0, 1])),
    ("power.paperlist", plist(*S.POWER_TEXT)),
    ("power.classify", classify),
]:
    expr_case(cid, f"module_run({paper.r}, 'power')", f"pc.module_run({paper.py}, 'power')")
tables_case("power.demo.tables", DEMO)
tables_case("power.classify.tables", classify)
tables_case("power.one_paragraph.tables", tp(*S.POWER_TEXT))
tables_case("power.psychsci.tables", read(PSYCHSCI))

# llm_use(TRUE): structured extraction against recorded Groq replies ----
llm_case("power.llm.no_potential", tp("I love to power pose."))
llm_case("power.llm.complete", tp(S.COMPLETE))
llm_case("power.llm.seed", tp(S.COMPLETE), {"seed": 1})
llm_case("power.llm.two_in_one", tp(S.TWO_IN_ONE))
llm_case("power.llm.empty", tp(S.MOTH))
llm_case(
    "power.llm.partial_failure",
    paras([S.COMPLETE, S.ALWAYS_FAILS], [0, 1]),
)
llm_case("power.llm.incomplete", tp(*S.INCOMPLETE_TEXT))
llm_case("power.llm.paperlist", plist(*S.INCOMPLETE_TEXT))
llm_case("power.llm.posthoc", tp(S.POSTHOC))
llm_case("power.llm.omitted_key", tp(S.OMITTED))
llm_case("power.llm.other", tp(S.OTHER))
llm_case("power.llm.demo", DEMO)
llm_case(
    "power.llm.mixed_paperlist",
    plist(S.MOTH, S.COMPLETE, S.POSTHOC, "I love to power pose."),
)
for cid, paper in [
    ("power.llm.two_in_one.tables", tp(S.TWO_IN_ONE)),
    ("power.llm.demo.tables", DEMO),
    ("power.llm.complete.tables", tp(S.COMPLETE)),
    ("power.llm.paperlist.tables", plist(*S.INCOMPLETE_TEXT)),
]:
    tables_case(cid, paper, llm=True, mock_dir="tests/mod_power/mocks")


# -- mocks -----------------------------------------------------------------------


def groq(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1760000000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "logprobs": None,
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
    }


GROQ_400 = {
    "error": {
        "message": "Failed to validate JSON. Please adjust your prompt. See 'failed_generation' "
        "for more details.",
        "type": "invalid_request_error",
        "code": "json_validate_failed",
    }
}


def r_error_response(status: int, obj: Any) -> str:
    raw = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()
    hexes = ", ".join(f"0x{b:02x}" for b in raw)
    return (
        f'structure(list(method = "POST", url = "{GURL}", \n'
        f"    status_code = {status}L, headers = structure(list(`content-type` = "
        f'"application/json"), class = "httr2_headers"), \n'
        f"    body = as.raw(c({hexes})), \n"
        f'    cache = new.env(parent = emptyenv())), class = "httr2_response")\n'
    )


def record_mocks() -> dict[str, tuple[int, Any]]:
    """Run every LLM case in Python, answering (and recording) each Groq request."""
    import httpx
    import respx

    import metacheck as pc
    from tests.httpmock import mock_path

    recorded: dict[str, tuple[int, Any]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        user = body["messages"][-1]["content"]
        text = user[0]["text"] if isinstance(user, list) else user
        reply = S.reply_for(text)
        path = mock_path(request)
        if isinstance(reply, int):
            recorded[path] = (reply, GROQ_400)
            return httpx.Response(reply, json=GROQ_400)
        payload = groq(json.dumps({"power_analyses": reply}, ensure_ascii=False))
        recorded[path] = (200, payload)
        return httpx.Response(200, json=payload)

    ns = {"pc": pc, "__builtins__": __builtins__}
    for case in cases:
        if case.get("mock_dir") != "tests/mod_power/mocks":
            continue
        code = case["args"]["x"]["$expr"]["py"]
        with respx.mock(assert_all_called=False) as router, warnings.catch_warnings():
            warnings.simplefilter("ignore")
            router.route().mock(side_effect=handler)
            eval(code, ns)
    return recorded


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
    header = (
        "# Power module parity cases (inst/modules/power.R), generated by\n"
        "# tests/mod_power/make_fixtures.py -- edit that script, not this file.\n"
        "# LLM cases run with llm_use(TRUE) inside withr::with_options() (Python:\n"
        "# tests/mod_power/parity_helpers.run_llm) and replay tests/mod_power/mocks.\n"
    )

    class Q(yaml.SafeDumper):
        pass

    def str_rep(dumper: yaml.SafeDumper, data: str) -> Any:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    Q.add_representer(str, str_rep)
    text = yaml.dump(
        {"area": "mod_power", "cases": cases},
        Dumper=Q,
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    )
    (ROOT / "parity" / "cases" / "mod_power.yaml").write_text(header + text, encoding="utf-8")


if __name__ == "__main__":
    write_cases()
    write_mocks(record_mocks())
