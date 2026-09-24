"""Generate ``parity/cases/mod_causal.yaml`` (every string quoted).

Run ``python tests/mod_causal/gen_parity_cases.py`` after changing the cases,
then ``python -m parity generate --area mod_causal``.

Two kinds of cases:

* plain ``module`` cases on papers whose title is blank/NULL and that have no
  abstract section, so ``causal_relations()`` gets no sentences and never
  touches the network (the randomization branches, empty papers, paper
  lists, the NA-title error);
* ``fake.*`` cases, where ``causal_relations()`` is replaced on both sides by
  the same deterministic fake (R: ``testthat::with_mocked_bindings()``;
  Python: ``tests.mod_causal.parity_support.run_fake_causal``), exercising
  every causal-claim branch and traffic light without the Hugging Face Space.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

S = '__import__("tests.mod_causal.parity_support", fromlist=["_"])'
ROOT_PY = '__import__("parity.cases", fromlist=["_"]).ROOT'
DEMO = "upstream/metacheck/inst/demos/to_err_is_human.json"
PSYCHSCI = [
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614522816.json",
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614527830.json",
]

# R twin of parity_support.fake_causal_relations()
R_FAKE = (
    ".fake <- function(sentence, ...) { "
    "out <- data.frame(sentence = character(0), causal = logical(0), "
    "cause = character(0), effect = character(0)); "
    'if (length(sentence) == 0 || all(trimws(sentence) == "")) return(out); '
    "for (s in sentence) { "
    'w <- strsplit(s, " ", fixed = TRUE)[[1]]; '
    'if (grepl("caus", tolower(s), fixed = TRUE) || grepl("effect", tolower(s), fixed = TRUE)) { '
    "out <- rbind(out, data.frame(sentence = s, causal = TRUE, cause = w[1], "
    "effect = w[length(w)])); "
    'if (grepl(" and ", s, fixed = TRUE)) out <- rbind(out, data.frame(sentence = s, '
    "causal = TRUE, cause = w[2], effect = w[length(w) - 1])) "
    "} else { "
    "out <- rbind(out, data.frame(sentence = s, causal = FALSE, cause = NA_character_, "
    "effect = NA_character_)) "
    "} }; out }; "
)

# R twins of parity_support.mk() and parity_support.untitled()
R_MK = (
    '.mk <- function(abstract = character(0), body = character(0), title = "", id = NULL) { '
    "p <- test_paper(c(abstract, body)); "
    "p$text$section_id <- c(rep(1, length(abstract)), rep(2, length(body))); "
    'p$section <- dplyr::tibble(section_id = c(1, 2), header = c("Abstract", "Methods"), '
    'parent_section_id = NA_integer_, section_type = c("abstract", "method"), '
    "classification_score = 0); "
    "p$info$title <- title; "
    "if (!is.null(id)) p$paper_id <- id; "
    "p }; "
    '.untitled <- function(text, title = "", id = NULL) { '
    "p <- test_paper(text); p$info$title <- title; "
    "if (!is.null(id)) p$paper_id <- id; p }; "
)

cases: list[dict[str, Any]] = []


def rchr(x: list[str] | tuple[str, ...]) -> str:
    return "c(" + ", ".join(json.dumps(s, ensure_ascii=False) for s in x) + ")"


def rs(x: str | None) -> str:
    return "NA" if x is None else json.dumps(x, ensure_ascii=False)


def pys(x: Any) -> str:
    """A Python literal."""
    return repr(x)


def mk(abstract=(), body=(), title: str | None = "", id: str | None = None) -> tuple[str, str]:
    r = f".mk({rchr(list(abstract)) if abstract else 'character(0)'}, "
    r += f"{rchr(list(body)) if body else 'character(0)'}, title = {rs(title)}"
    r += f", id = {rs(id)})" if id is not None else ")"
    py = f"{S}.mk({pys(list(abstract))}, {pys(list(body))}, title={pys(title)}, id={pys(id)})"
    return r, py


def untitled(text, title: str | None = "", id: str | None = None) -> tuple[str, str]:
    r = f".untitled({rchr(list(text))}, title = {rs(title)}"
    r += f", id = {rs(id)})" if id is not None else ")"
    py = f"{S}.untitled({pys(list(text))}, title={pys(title)}, id={pys(id)})"
    return r, py


def test_paper(text, id: str) -> tuple[str, str]:
    r = f"(function() {{ p <- test_paper({rchr(list(text))}); p$paper_id <- {rs(id)}; p }})()"
    py = f"(lambda p: (setattr(p, 'paper_id', {pys(id)}), p)[1])(pc.test_paper({pys(list(text))}))"
    return r, py


def plist(*papers: tuple[str, str]) -> tuple[str, str]:
    r = "paperlist(" + ", ".join(p[0] for p in papers) + ")"
    py = "pc.PaperList([" + ", ".join(p[1] for p in papers) + "])"
    return r, py


def read(*paths: str) -> tuple[str, str]:
    r = (
        f"metacheck::read({rchr(list(paths))})"
        if len(paths) > 1
        else f'metacheck::read("{paths[0]}")'
    )
    if len(paths) > 1:
        py = f"pc.PaperList(pc.read([{ROOT_PY} / p for p in {pys(list(paths))}]))"
    else:
        py = f"pc.read({ROOT_PY} / {pys(paths[0])})"
    return r, py


def module_case(id: str, paper: tuple[str, str], compare: dict | None = None) -> None:
    case: dict[str, Any] = {
        "id": f"causal_claims.{id}",
        "module": "causal_claims",
        "args": {"paper": {"$expr": {"r": f"{{ {R_MK}{paper[0]} }}", "py": paper[1]}}},
    }
    if compare:
        case["compare"] = compare
    cases.append(case)


def fake_case(id: str, paper: tuple[str, str], compare: dict | None = None) -> None:
    r = (
        f"(function() {{ {R_FAKE}{R_MK}p <- {paper[0]}; "
        'testthat::with_mocked_bindings(module_run(p, "causal_claims"), '
        'causal_relations = .fake, .package = "metacheck") })()'
    )
    py = f'lambda: pc.module_run({paper[1]}, "causal_claims")'
    case: dict[str, Any] = {
        "id": f"causal_claims.fake.{id}",
        "r": "identity",
        "py": "tests.mod_causal.parity_support.run_fake_causal",
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }
    if compare:
        case["compare"] = compare
    cases.append(case)


# ------------------------------------------------------------------ sentences

RANDOM_NONE = [
    "We included random effects for participants and items.",
    "Stimuli were presented in a random order.",
    "A random sample of 200 adults was drawn from the panel.",
    "Nothing else to report here.",
]
RANDOM_ONE = [
    "Participants were randomly assigned to the experimental or control condition.",
    "We fitted random intercepts for each participant.",
    "The data were analysed with t-tests.",
]
RANDOM_SEVERAL = [
    "Participants were randomly assigned to one of three conditions.",
    "Randomization was performed with a computer script.",
    "Subjects were assigned at random to the two groups.",
    "Trials were presented in random order after participants were randomized.",
    "Participants were RANDOMLY ALLOCATED to groups.",
    "Pseudorandomized sequences were used.",
    "The sample was randomly split into halves.",
]

# ------------------------------------------------------- no-network (plain)

module_case(
    "empty",
    (
        '(function() { p <- paper(); p$paper_id <- "empty"; p })()',
        "(lambda p: (setattr(p, 'paper_id', 'empty'), p)[1])(pc.paper())",
    ),
)
module_case("none", untitled(RANDOM_NONE, id="none"))
module_case("random_one", untitled(RANDOM_ONE, title="   ", id="one"))
module_case("random_several", untitled(RANDOM_SEVERAL, id="several"))
module_case("no_random_word", untitled(["No matching words.", "Just plain text."], id="plain"))
module_case("title_na", untitled(["Participants were randomly assigned."], title=None, id="na"))
module_case(
    "paperlist_no_abstract",
    plist(
        test_paper(RANDOM_ONE, "pl_one"),
        test_paper(RANDOM_NONE, "pl_none"),
        test_paper(RANDOM_SEVERAL, "pl_several"),
    ),
)
module_case(
    "paperlist_none",
    plist(test_paper(RANDOM_NONE, "pl_a"), test_paper(["Nothing random."], "pl_b")),
)

# ------------------------------------------------ fake causal_relations()

fake_case("demo", ("demopaper()", "pc.demopaper()"))
fake_case("psychsci", read(PSYCHSCI[0]))
fake_case("psychsci_list", read(*PSYCHSCI, DEMO))
fake_case("psychsci_only", read(*PSYCHSCI))
fake_case(
    "abstract_no_random",
    mk(
        ["Stress causes poor sleep.", "We surveyed 200 adults."],
        ["Participants completed a questionnaire.", "We included random effects."],
        title="A survey of sleep",
        id="abs_norand",
    ),
)
fake_case(
    "abstract_random",
    mk(
        ["Exercise causes better mood and higher energy.", "We ran an experiment."],
        ["Participants were randomly assigned to conditions."],
        title="",
        id="abs_rand",
    ),
)
fake_case(
    "title_only",
    mk(["We measured errors in a lab task."], [], title="Sleep deprivation causes errors", id="t1"),
)
fake_case(
    "title_and_abstract_random",
    mk(
        ["The effect of caffeine was large.", "Caffeine causes jitters.", "We tested adults."],
        [
            "Participants were randomly allocated to caffeine or placebo.",
            "Randomization used sealed envelopes.",
        ],
        title="Caffeine causes alertness and focus",
        id="t2",
    ),
)
fake_case(
    "title_random",
    mk([], ["Participants were randomly assigned."], title="Noise effects on memory", id="t3"),
)
fake_case(
    "no_causal",
    mk(
        ["We measured things.", "Results are reported."],
        ["Order was randomized."],
        title="Plain title",
        id="nc",
    ),
)
fake_case(
    "paperlist_duplicate",
    plist(
        mk(["X causes Y.", "Same sentence here."], [], title="Stress causes nothing", id="p1"),
        mk(["X causes Y."], ["Groups were randomly assigned."], id="p2"),
        untitled(["No abstract here."], id="p3"),
    ),
)
fake_case(
    "paperlist_info_id",
    plist(
        mk(["Nothing here."], [], title="Heat causes thirst", id="info"),
        mk(["Light causes growth and color."], [], id="other"),
    ),
)
fake_case(
    "paperlist_no_causal",
    plist(
        mk(["We measured things."], [], id="q1"),
        mk(["We measured more things."], ["We randomly assigned people."], id="q2"),
    ),
)


class QuotedDumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


QuotedDumper.add_representer(str, _str)


def main() -> None:
    path = Path(__file__).resolve().parents[2] / "parity/cases/mod_causal.yaml"
    out = {"area": "mod_causal", "cases": cases}
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Generated by tests/mod_causal/gen_parity_cases.py -- do not edit by hand.\n")
        yaml.dump(out, fh, Dumper=QuotedDumper, sort_keys=False, allow_unicode=True, width=10000)


if __name__ == "__main__":
    main()
