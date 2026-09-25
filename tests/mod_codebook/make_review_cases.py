"""Writes parity/cases/mod_codebook_review.yaml (run from the repo root).

    .venv/bin/python tests/mod_codebook/make_review_cases.py

Adversarial-review cases for the codebook_check port: branches the first
round of cases (``make_cases.py``) did not reach. Module cases chain
codebook_check onto stored data_check output of the ``rv_*`` scenarios
(``fixtures/make_review_fixtures.R``); helper cases call the module's internal
functions on inputs built identically by ``review_helpers.R`` /
``review_helpers.py``. Several cases also compare the attributes R keeps on
the OSD objects (``write``, ``code``, ...), which the canonical encoding
of a value does not include.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

R_PRE = "source('tests/mod_codebook/review_helpers.R', local = TRUE)"
PY_RH = "__import__('tests.mod_codebook.review_helpers', fromlist=['_'])"
PY_H = "__import__('tests.mod_codebook.helpers', fromlist=['_'])"
PY_CB = "__import__('pytacheck.modules._codebook', fromlist=['_'])"

SCENARIOS = [
    "rv_violations",
    "rv_sort",
    "rv_round_low",
    "rv_round_mid",
    "rv_dupscale",
    "rv_maxitem",
    "rv_onlycb",
    "rv_qblock",
    "rv_task_none",
]
# labelled data files whose embedded labels the module harvests
HAVEN_FILES = [
    "tests/datacheck_files/data/labelled.dta",
    "tests/datacheck_files/data/review/zero_rows.dta",
    "tests/datacheck_files/data/review/all_na_dates.sav",
    "tests/datacheck_files/data/review/stata_int_na.dta",
    "tests/datacheck_files/data/review/zero_rows.sav",
    "tests/datacheck_files/data/iris.sas7bdat",
    "tests/datacheck_files/data/labelled.sav",
    "tests/datacheck_columns/fixtures/review/dup_labels.sav",
]
# paper lists: empty-data summaries per paper, and two papers sharing a data file name
PAPER_LISTS = {
    "pl_empty": ["empty", "empty"],
    "pl_onlycb": ["rv_onlycb", "rv_onlycb"],
    "pl_onlycb_green": ["rv_onlycb", "green"],
    "pl_samefile": ["green", "scope"],
    "pl_scales": ["rulesonly", "panas_high"],
}


def case(cid: str, r: str, py: str) -> dict[str, Any]:
    py = py.replace("RH.", PY_RH + ".").replace("H.", PY_H + ".").replace("CB.", PY_CB + ".")
    return {
        "id": cid,
        "r": "base::identity",
        "py": "tests.mod_codebook.helpers.identity",
        "args": {"x": {"$expr": {"r": f"local({{{R_PRE}; {r}}})", "py": py}}},
    }


def module_cases() -> list[dict[str, Any]]:
    out = []
    for s in SCENARIOS:
        # module_run(<stored data_check output>, "codebook_check")
        out.append(
            {
                "id": f"codebook_check.{s}",
                "module": "codebook_check",
                "args": {
                    "paper": {
                        "$expr": {
                            "r": f"local({{{R_PRE}; cbc_prev('{s}')}})",
                            "py": f"{PY_H}.cbc_prev('{s}')",
                        }
                    }
                },
            }
        )
        out.append(
            case(f"codebook_check.tables.{s}", f"cbc_run_tables('{s}')", f"H.cbc_run_tables('{s}')")
        )
    for name, scen in PAPER_LISTS.items():
        vec_r = ", ".join(f"'{x}'" for x in scen)
        out.append(
            case(
                f"codebook_check.{name}",
                f"module_run(cbc_prev_list(c({vec_r})), 'codebook_check')",
                f"__import__('pytacheck.module', fromlist=['_']).module_run("
                f"H.cbc_prev_list([{vec_r}]), 'codebook_check')",
            )
        )
    out.append(
        case(
            "codebook_check.llm.pl_scales",
            "cbc_llm_run(NULL, 'scales', prev = cbc_prev_list(c('rulesonly', 'scales_mixed')))",
            "H.cbc_llm_run(None, 'scales', prev=H.cbc_prev_list(['rulesonly', 'scales_mixed']))",
        )
    )
    for spec in ["rv_parse_ctx", "rv_parse_noctx"]:
        out.append(
            case(
                f"codebook_check.llm.readme_text.{spec}",
                f"cbc_llm_run('readme_text', '{spec}')",
                f"H.cbc_llm_run('readme_text', '{spec}')",
            )
        )
    for s in ["rv_dupscale", "rv_maxitem", "orphan", "loop", "scales_mixed", "panas_high"]:
        out.append(
            case(
                f"codebook_check.osd_attrs.{s}",
                f"rv_run_osd_attrs(cbc_run('{s}'))",
                f"RH.rv_run_osd_attrs(H.cbc_run('{s}'))",
            )
        )
    # LLM tiers (deterministic mock, fixtures/llm_mock.json)
    for s in ["rv_dupscale", "rv_maxitem"]:
        out.append(
            case(
                f"codebook_check.llm.{s}",
                f"cbc_llm_run('{s}', 'rv_names')",
                f"H.cbc_llm_run('{s}', 'rv_names')",
            )
        )
        out.append(
            case(
                f"codebook_check.llm.tables.{s}",
                f"cbc_report_tables(cbc_llm_run('{s}', 'rv_names'))",
                f"H.report_tables(H.cbc_llm_run('{s}', 'rv_names'))",
            )
        )
        out.append(
            case(
                f"codebook_check.llm.osd_attrs.{s}",
                f"rv_run_osd_attrs(cbc_llm_run('{s}', 'rv_names'))",
                f"RH.rv_run_osd_attrs(H.cbc_llm_run('{s}', 'rv_names'))",
            )
        )
    for s, spec in [("scales_mixed", "scales"), ("loop", "selfgen"), ("panas_high", "panas")]:
        out.append(
            case(
                f"codebook_check.llm.osd_attrs.{s}",
                f"rv_run_osd_attrs(cbc_llm_run('{s}', '{spec}'))",
                f"RH.rv_run_osd_attrs(H.cbc_llm_run('{s}', '{spec}'))",
            )
        )
    return out


def helper_cases() -> list[dict[str, Any]]:
    h = []
    h.append(
        case(
            "propagate_scale_by_prefix.empty_prefix",
            "rv_env()$.propagate_scale_by_prefix(rv_propagate_df())",
            "CB._propagate_scale_by_prefix(RH.rv_propagate_df())",
        )
    )
    h.append(
        case(
            "codebook_duplicate_name_warnings.collation",
            "rv_env()$.codebook_duplicate_name_warnings(list(f.csv = rv_named_frame(rv_dup_names()), "
            "g.csv = rv_named_frame(c('a', 'b')), h.csv = rv_named_frame(character(0))))",
            "CB._codebook_duplicate_name_warnings({'f.csv': RH.rv_named_frame(RH.rv_dup_names()), "
            "'g.csv': RH.rv_named_frame(['a', 'b']), 'h.csv': RH.rv_named_frame([])})",
        )
    )
    for cid, matched_r, matched_py, cols_r, cols_py in [
        (
            "na_fields",
            "c('Personality (BFI)', 'life orientation test')",
            "['Personality (BFI)', 'life orientation test']",
            "",
            "",
        ),
        ("no_match", "character(0)", "[]", "", ""),
        ("name_only", "c('x')", "['x']", "'scale_name'", "['scale_name']"),
        (
            "no_admin",
            "c('grit scale')",
            "['grit scale']",
            "c('scale_name', 'acronym', 'n_items')",
            "['scale_name', 'acronym', 'n_items']",
        ),
    ]:
        h.append(
            case(
                f"scale_text_report.{cid}",
                f"rv_env()$.scale_text_report(rv_text_scales({cols_r}), matched = {matched_r})",
                f"CB._scale_text_report(RH.rv_text_scales({cols_py}), matched={matched_py})",
            )
        )
    h.append(
        case(
            "scale_split_items.values",
            "{df <- rv_split_df(); rv_env()$.scale_split_items(names(df), df)}",
            "CB._scale_split_items(list(RH.rv_split_df().columns), RH.rv_split_df())",
        )
    )
    h.append(
        case(
            "scale_split_items.values_min2",
            "{df <- rv_split_df(); rv_env()$.scale_split_items(names(df), df, min_items = 2L)}",
            "CB._scale_split_items(list(RH.rv_split_df().columns), RH.rv_split_df(), min_items=2)",
        )
    )
    h.append(
        case(
            "scale_prefix_groups.mixed",
            "rv_env()$.scale_prefix_groups(rv_named_frame(rv_prefix_names()))",
            "CB._scale_prefix_groups(RH.rv_named_frame(RH.rv_prefix_names()))",
        )
    )
    h.append(
        case(
            "scale_prefix_groups.mixed_qualtrics",
            "rv_env()$.scale_prefix_groups(rv_named_frame(rv_prefix_names()), qualtrics = TRUE)",
            "CB._scale_prefix_groups(RH.rv_named_frame(RH.rv_prefix_names()), qualtrics=True)",
        )
    )
    h.append(
        case(
            "scale_prefix_groups.min_chars",
            "rv_env()$.scale_prefix_groups(rv_named_frame(c('abcd1', 'abcd2', 'abce3', 'abcf4', "
            "'xy_1', 'xy_2', 'xy_3', 'xy_4a')), min_cols = 2L, min_chars = 4L)",
            "CB._scale_prefix_groups(RH.rv_named_frame(['abcd1', 'abcd2', 'abce3', 'abcf4', "
            "'xy_1', 'xy_2', 'xy_3', 'xy_4a']), min_cols=2, min_chars=4)",
        )
    )
    for cid, cols in [("codes", "'a1', 'a2'"), ("drop_missing", "'b1', 'b2'")]:
        h.append(
            case(
                f"osd_likert_options.{cid}",
                f"rv_env()$.osd_likert_options(c({cols}), 's.csv', NULL, rv_likert_labels())",
                f"CB._osd_likert_options([{cols}], 's.csv', None, RH.rv_likert_labels())",
            )
        )
    for cid, cols in [("observed_na", "'c1', 'c2', 'c3'"), ("half", "'d1', 'd2'")]:
        h.append(
            case(
                f"osd_likert_options.{cid}",
                f"rv_env()$.osd_likert_options(c({cols}), 's.csv', rv_likert_columns(), "
                "rv_likert_labels())",
                f"CB._osd_likert_options([{cols}], 's.csv', RH.rv_likert_columns(), "
                "RH.rv_likert_labels())",
            )
        )
    h.append(
        case(
            "selfgen_merge_synonyms.files",
            "rv_env()$.selfgen_merge_synonyms(rv_synonyms())",
            "CB._selfgen_merge_synonyms(RH.rv_synonyms())",
        )
    )
    h.append(case("scales_to_osd.mixed", "rv_scales_to_osd()", "RH.rv_scales_to_osd()"))
    h.append(
        case(
            "scale_paper_context.basic",
            "rv_env()$.scale_paper_context(rv_text_paper(), c('bfi', 'q.x', 'ab', 'item', NA, ''), "
            "c('I feel enthusiastic', 'Determined and enthusiastic', NA, '', 'strongly agree'), "
            "max_sent = 4L)",
            "CB._scale_paper_context(RH.rv_text_paper(), ['bfi', 'q.x', 'ab', 'item', None, ''], "
            "['I feel enthusiastic', 'Determined and enthusiastic', None, '', 'strongly agree'], "
            "max_sent=4)",
        )
    )
    h.append(
        case(
            "scale_prefix_sentences.basic",
            "rv_env()$.scale_prefix_sentences(rv_text_paper(), c('BFI', 'q.x', '', 'C++', 'pss', "
            "'BFI'), max_sent = 3L)",
            "CB._scale_prefix_sentences(RH.rv_text_paper(), ['BFI', 'q.x', '', 'C++', 'pss', "
            "'BFI'], max_sent=3)",
        )
    )
    h.append(
        case(
            "scale_description_sentences.basic",
            "rv_env()$.scale_description_sentences(rv_text_paper(), max_sent = 2L)",
            "CB._scale_description_sentences(RH.rv_text_paper(), max_sent=2)",
        )
    )
    h.append(
        case(
            "scan_paper_for_tasks.review",
            "rv_env()$.scan_paper_for_tasks(rv_text_paper())",
            "CB._scan_paper_for_tasks(RH.rv_text_paper())",
        )
    )
    for i, f in enumerate(HAVEN_FILES):
        h.append(case(f"haven_labels.{i}", f"rv_haven_labels('{f}')", f"RH.rv_haven_labels('{f}')"))
    names = [
        "Q8timing_First.Click",
        "demo1time_First.Click",
        "sleep_time",
        "time_spent",
        "RESPONSE_TIME",
        "trial_index",
        "response_numeric",
        "item_response_time_numeric",
        "response_numeric_timing",
        "Timer_1",
        "page submit",
        "clickcount",
        "q_timing",
    ]
    nm_r = ", ".join(f"'{n}'" for n in names)
    nm_py = ", ".join(f"'{n}'" for n in names)
    h.append(
        case(
            "paradata_name.misc",
            f"rv_env()$.paradata_name(c({nm_r}))",
            f"CB._paradata_name([{nm_py}])",
        )
    )
    pairs = [
        ("a1", "a1"),
        ("ab", "abc"),
        ("x.1", "x.2"),
        ("q1_a", "q1_b"),
        ("é1", "é2"),
        ("a-b_1", "a-b_2"),
        ("", "a"),
        ("abc_", "abc-"),
    ]
    for i, (a, b) in enumerate(pairs):
        h.append(
            case(
                f"scale_stem_pair.{i}",
                f"{{e <- rv_env(); list(stem = e$.scale_shared_stem('{a}', '{b}'), "
                f"run = e$.scale_same_number_run('{a}', '{b}'))}}",
                f"{{'stem': CB._scale_shared_stem('{a}', '{b}'), "
                f"'run': CB._scale_same_number_run('{a}', '{b}')}}",
            )
        )
    words = ["PP", "p1", "power.PP170", "Q3_1_", "ab12cd34", "", "ÄB1", "_a1", "123", "x_y_12"]
    wr = ", ".join(f"'{w}'" for w in words)
    h.append(
        case(
            "scale_word_helpers.misc",
            f"{{e <- rv_env(); w <- c({wr}); list(vapply(w, e$.scale_loop_base, ''), "
            "vapply(w, e$.scale_alpha_prefix, ''))}",
            f"[[CB._scale_loop_base(w) for w in [{wr}]], [CB._scale_alpha_prefix(w) for w in [{wr}]]]",
        )
    )
    return h


def write_cases() -> None:
    header = (
        "# codebook_check review parity cases (inst/modules/codebook_check.R), generated\n"
        "# by tests/mod_codebook/make_review_cases.py -- edit that script, not this file.\n"
    )

    class Q(yaml.SafeDumper):
        pass

    def str_rep(dumper: yaml.SafeDumper, data: str) -> Any:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    Q.add_representer(str, str_rep)
    text = yaml.dump(
        {"area": "mod_codebook_review", "cases": module_cases() + helper_cases()},
        Dumper=Q,
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    )
    (ROOT / "parity" / "cases" / "mod_codebook_review.yaml").write_text(
        header + text, encoding="utf-8"
    )


if __name__ == "__main__":
    write_cases()
