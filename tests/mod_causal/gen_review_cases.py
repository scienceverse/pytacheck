"""Generate ``parity/cases/mod_causal_review.yaml`` (adversarial review cases).

Run ``python tests/mod_causal/gen_review_cases.py`` after changing the cases,
then ``python -m parity generate --area mod_causal_review``.

These add branches the first set of cases (``gen_parity_cases.py``) did not
reach: Unicode whitespace in randomization sentences (``text_search()``
normalises TRE ``\\s``, the PCRE patterns only see ASCII ``\\s``), pattern
quirks (word boundaries, the 60-character order/trial/block window), blank and
duplicated abstract sentences, abstract rows whose section is unknown, a paper
without a title column, empty papers inside a paper list, a one-paper list,
module pipelines, and a missing ``causal`` flag (``NA``) from the classifier.

``error.*`` cases return the error message (``conditionMessage()`` / ``str()``),
so the message is compared too, not only that both sides fail. ``report.*``
cases return ``module_run()$report`` with every table rendered as the R chunk
``scroll_table()`` returns (``parity_support.report_qmd``), so the report is
compared exactly, tables and empty elements included (the default ``prose``
comparison skips both).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.mod_causal.gen_parity_cases import (
    DEMO,
    PSYCHSCI,
    R_FAKE,
    R_MK,
    RANDOM_ONE,
    RANDOM_SEVERAL,
    QuotedDumper,
    S,
    mk,
    plist,
    read,
    test_paper,
    untitled,
)

# R twin of parity_support.fake_causal_relations_na()
R_FAKE_NA = (
    R_FAKE + ".fake_na <- function(sentence, ...) { out <- .fake(sentence); "
    'out$causal[grepl("maybe", tolower(out$sentence), fixed = TRUE)] <- NA; out }; '
)

# an empty paper with the ID "e"
EMPTY = (
    '(function() { p <- paper(); p$paper_id <- "e"; p })()',
    "(lambda p: (setattr(p, 'paper_id', 'e'), p)[1])(pc.paper())",
)

cases: list[dict[str, Any]] = []


def module_case(id: str, paper: tuple[str, str]) -> None:
    cases.append(
        {
            "id": f"causal_claims.review.{id}",
            "module": "causal_claims",
            "args": {"paper": {"$expr": {"r": f"{{ {R_MK}{paper[0]} }}", "py": paper[1]}}},
        }
    )


def fake_case(
    id: str, paper: tuple[str, str], na: bool = False, run: tuple[str, str] | None = None
) -> None:
    """A module run with the fake classifier; *run* wraps ``p`` (default: one module run)."""
    fake = ".fake_na" if na else ".fake"
    r_run, py_run = run or ('module_run(p, "causal_claims")', 'pc.module_run(p, "causal_claims")')
    r = (
        f"(function() {{ {R_FAKE_NA}{R_MK}p <- {paper[0]}; "
        f"testthat::with_mocked_bindings({r_run}, "
        f'causal_relations = {fake}, .package = "metacheck") }})()'
    )
    py = f"lambda: (lambda p: {py_run})({paper[1]})"
    runner = "run_fake_causal_na" if na else "run_fake_causal"
    cases.append(
        {
            "id": f"causal_claims.review.{'fake_na' if na else 'fake'}.{id}",
            "r": "identity",
            "py": f"tests.mod_causal.parity_support.{runner}",
            "args": {"x": {"$expr": {"r": r, "py": py}}},
        }
    )


def error_case(id: str, paper: tuple[str, str], fake: str | None = None) -> None:
    """``module_run()``, or its error message when it fails.

    *fake* is ``None`` (the real classifier; the paper must not reach the
    network), ``"fake"`` or ``"na"`` (see :func:`fake_case`).
    """
    r_run = 'tryCatch(module_run(p, "causal_claims"), error = function(e) conditionMessage(e))'
    py_run = f'{S}.error_message(lambda: pc.module_run(p, "causal_claims"))'
    if fake is not None:
        fake_case(f"error.{id}", paper, na=fake == "na", run=(r_run, py_run))
        return
    cases.append(
        {
            "id": f"causal_claims.review.error.{id}",
            "r": "identity",
            "py": "tests.mod_causal.parity_support.error_message",
            "args": {
                "x": {
                    "$expr": {
                        "r": f"(function() {{ {R_MK}p <- {paper[0]}; {r_run} }})()",
                        "py": f'lambda: (lambda p: pc.module_run(p, "causal_claims"))({paper[1]})',
                    }
                }
            },
        }
    )


def report_case(id: str, paper: tuple[str, str]) -> None:
    """The exact report (tables as R chunks), with the fake classifier."""
    fake_case(
        f"report.{id}",
        paper,
        run=(
            'module_run(p, "causal_claims")$report',
            f'{S}.report_qmd(pc.module_run(p, "causal_claims"))',
        ),
    )


def r_then(paper: tuple[str, str], r_code: str, py_fn: str) -> tuple[str, str]:
    """Modify a paper: R ``p <- paper; <r_code>; p``, Python ``S.<py_fn>(paper)``."""
    return (
        f"(function() {{ p <- {paper[0]}; {r_code}; p }})()",
        f"{S}.{py_fn}({paper[1]})",
    )


def section_ids(paper: tuple[str, str], ids: list[int | None]) -> tuple[str, str]:
    """R ``p$text$section_id <- c(...)`` (``None`` -> ``NA``)."""
    r_ids = ", ".join("NA" if i is None else str(i) for i in ids)
    return (
        f"(function() {{ p <- {paper[0]}; p$text$section_id <- c({r_ids}); p }})()",
        f"{S}.with_section_ids({paper[1]}, {ids!r})",
    )


def section_types(paper: tuple[str, str], types: list[str]) -> tuple[str, str]:
    """R ``p$section$section_type <- c(...)``."""
    r_types = ", ".join(f'"{t}"' for t in types)
    return (
        f"(function() {{ p <- {paper[0]}; p$section$section_type <- c({r_types}); p }})()",
        f"{S}.with_section_types({paper[1]}, {types!r})",
    )


def gap(k: int) -> str:
    """A sentence whose ``randomized`` and ``order`` are ``k + 2`` characters apart."""
    return f"Groups were randomized {'y' * k} order."


# ------------------------------------------------------- no-network (plain)

module_case(
    "unicode_space",
    untitled(
        [
            # U+00A0 / U+202F: not whitespace for TRE (glibc iswspace) nor PCRE
            "Participants were randomly assigned to groups.",
            "Participants were randomly assigned to the groups.",
            # U+2003 / tab: text_search() turns them into a plain space
            "Participants were randomly assigned to conditions.",
            "Participants were randomly\tassigned to arms.",
            "Pupils were randomly allocated to classes.",
        ],
        id="uni",
    ),
)
module_case(
    "pattern_quirks",
    untitled(
        [
            "Randomisation was done by coin flip.",
            "Participants were not randomized.",
            "Participants were randomly assigned and we modelled random intercepts.",
            "The order of blocks was randomized after participants were randomly assigned.",
            "éRandomized design was used.",
            "A _randomized design.",
            "The randomized_trial design.",
            "RANDOMIZATION WAS STRATIFIED.",
            "Participants were assigned randomly to two groups.",
            "We used stratified random assignment.",
            "Children were randomly divided into teams.",
            "Participants were randomly assigned, and random effects were modelled.",
            gap(58),
            gap(59),
            "Participants were randomly assigned , as described.",
        ],
        id="quirks",
    ),
)
module_case(
    "info_no_title",
    r_then(untitled(RANDOM_ONE, id="notitle"), "p$info$title <- NULL", "without_title"),
)
# a blank title: pytacheck classifies the titles of a paper list (U84); the
# fake.paperlist_empty_member_random case keeps test_paper()'s "Test Paper"
module_case("paperlist_empty_member_untitled", plist(EMPTY, untitled(RANDOM_SEVERAL, id="s")))
module_case(
    "duplicate_random_sentence",
    untitled(
        ["Participants were randomly assigned.", "Participants were randomly assigned."], id="dup"
    ),
)

# ------------------------------------------------ fake causal_relations()

fake_case("dup_same_paper", mk(["X causes Y.", "X causes Y.", "Nothing."], [], id="dup"))
fake_case(
    "blank_abstract",
    mk(
        ["   ", ""],
        ["Participants were randomly assigned."],
        title="Heat causes thirst",
        id="blank",
    ),
)
fake_case(
    "blank_mixed",
    mk(["", "Stress causes errors.", "  "], [], title="", id="bmix"),
)
fake_case(
    "section_na",
    section_ids(
        mk(
            ["Heat causes thirst.", "Cold causes shivers."],
            ["We randomly assigned people."],
            id="sna",
        ),
        [1, None, 2],
    ),
)
fake_case(
    "paperlist_single",
    plist(mk(["We measured things."], [], title="Heat causes thirst", id="solo")),
)
fake_case("paperlist_empty_member", plist(EMPTY, mk(["Heat causes thirst."], [], id="h")))
fake_case("paperlist_empty_member_random", plist(EMPTY, test_paper(RANDOM_SEVERAL, "s")))
fake_case(
    "chain_self",
    mk(["X causes Y."], ["Participants were randomly assigned."], title="A causes B", id="c1"),
    run=(
        'module_run(module_run(p, "causal_claims"), "causal_claims")',
        'pc.module_run(pc.module_run(p, "causal_claims"), "causal_claims")',
    ),
)
fake_case(
    "chain_marginal",
    mk(
        ["X causes Y.", "The effect was marginally significant."],
        [],
        title="",
        id="c2",
    ),
    run=(
        'module_run(module_run(p, "marginal"), "causal_claims")',
        'pc.module_run(pc.module_run(p, "marginal"), "causal_claims")',
    ),
)
fake_case(
    "abstract",
    mk(["X causes Y.", "Maybe noise causes Z.", "Nothing."], [], title="", id="na1"),
    na=True,
)
fake_case(
    "abstract_only_na",
    mk(["Maybe noise causes Z."], ["Participants were randomly assigned."], title="", id="na2"),
    na=True,
)
fake_case(
    "title",
    mk(["X causes Y."], [], title="Maybe heat causes thirst", id="na3"),
    na=True,
)
fake_case(
    "paperlist",
    plist(
        mk(["Maybe noise causes Z.", "X causes Y."], [], id="n1"),
        mk(["Maybe noise causes Z."], [], id="n2"),
    ),
    na=True,
)

# ------------------------------------------------------------ error messages

error_case("title_na", untitled(["Participants were randomly assigned."], title=None, id="na"))
error_case("empty_paperlist", ("paperlist()", "pc.PaperList([])"))
error_case(
    "no_text_column",
    r_then(untitled(RANDOM_ONE, id="nt"), "p$text$text <- NULL", "without_text"),
)
error_case(
    "fake_na_title", mk(["X causes Y."], [], title="Maybe heat causes thirst", id="e1"), fake="na"
)
error_case(
    "fake_ok",
    mk(["X causes Y."], ["We randomly assigned people."], title="Heat causes thirst", id="e2"),
    fake="fake",
)

# ------------------------------------------------- references are excluded

fake_case(
    "references_excluded",
    section_types(
        mk(["X causes Y."], ["Participants were randomly assigned."], title="", id="refs"),
        ["abstract", "references"],
    ),
)
fake_case(
    "references_abstract_only",
    section_types(
        mk(["X causes Y.", "We randomly assigned people."], ["Nothing."], title="", id="refs2"),
        ["references", "method"],
    ),
)

# ------------------------------------------------------------ exact reports

report_case("none", untitled(["Nothing here."], id="r0"))
report_case(
    "full",
    mk(
        ["X causes Y and more effect Z.", "Nothing."],
        [
            *RANDOM_SEVERAL,
            'Participants were "randomly assigned" to groups \\ with caf\u00e9 and na\u00efve.',
        ],
        title="Heat causes thirst and sweat",
        id="r1",
    ),
)
report_case("yellow", mk(["Stress causes errors."], ["We measured."], title="", id="r2"))
report_case("title_only", mk(["Nothing."], [], title="Sleep causes errors", id="r3"))
report_case("demo", ("demopaper()", "pc.demopaper()"))
report_case("psychsci_list", read(*PSYCHSCI, DEMO))


def main() -> None:
    path = Path(__file__).resolve().parents[2] / "parity/cases/mod_causal_review.yaml"
    out = {"area": "mod_causal_review", "cases": cases}
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Generated by tests/mod_causal/gen_review_cases.py -- do not edit by hand.\n")
        yaml.dump(out, fh, Dumper=QuotedDumper, sort_keys=False, allow_unicode=True, width=10000)


if __name__ == "__main__":
    main()
