"""Writes parity/cases/mod_codebook.yaml (run from the repo root).

    .venv/bin/python tests/mod_codebook/make_cases.py

Module cases chain codebook_check onto a stored data_check output
(``tests/mod_codebook/cbc_helpers.R`` / ``helpers.py``, fixtures from
``fixtures/make_fixtures.R``). Helper cases call the module's internal
functions: in R from the module file sourced into an environment whose
parent is the metacheck namespace (as upstream's testthat file does), in
Python from :mod:`pytacheck.modules._codebook`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

R_HELPERS = "source('tests/mod_codebook/cbc_helpers.R', local = TRUE)"
PY_HELPERS = "__import__('tests.mod_codebook.helpers', fromlist=['_'])"
R_MOD = (
    "e <- new.env(parent = asNamespace('metacheck')); "
    "sys.source(metacheck:::module_find('codebook_check'), envir = e)"
)
PY_MOD = "__import__('pytacheck.modules._codebook', fromlist=['_'])"

SCENARIOS = [
    "conflict",
    "merge",
    "unused",
    "misalign",
    "partial",
    "range",
    "norange",
    "missing_codes",
    "green",
    "downgrade",
    "nocb",
    "empty",
    "scope",
    "dup",
    "task",
    "taskonly",
    "task_medium",
    "orphan",
    "rulesonly",
    "panas_high",
    "scales_mixed",
    "loop",
    "qualtrics",
    "haven",
    "jasp_omv",
    "readme_text",
    "multifile",
    "pl_a",
    "pl_b",
]

TABLE_SCENARIOS = [
    "conflict",
    "unused",
    "misalign",
    "range",
    "missing_codes",
    "multifile",
    "qualtrics",
    "haven",
    "jasp_omv",
    "orphan",
    "panas_high",
    "scales_mixed",
    "loop",
    "task",
]

PSYCHSCI = [
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614522816.json",
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614527830.json",
]
BIBR12 = "upstream/metacheck/tests/testthat/fixtures/bibr12/preprint.json"


def module_case(cid: str, r_prev: str, py_prev: str, **extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "paper": {
            "$expr": {
                "r": f"local({{{R_HELPERS}; {r_prev}}})",
                "py": f"{PY_HELPERS}.{py_prev}",
            }
        }
    }
    args.update(extra)
    return {"id": cid, "module": "codebook_check", "args": args}


def helper_case(
    cid: str, r_call: str, py_call: str, py_base: str = PY_MOD, **spec: Any
) -> dict[str, Any]:
    case = {
        "id": cid,
        "r": "base::identity",
        "py": "tests.mod_codebook.helpers.identity",
        "args": {
            "x": {"$expr": {"r": f"local({{{R_MOD}; {r_call}}})", "py": f"{py_base}.{py_call}"}}
        },
    }
    case.update(spec)
    return case


def cases() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in SCENARIOS:
        out.append(module_case(f"codebook_check.{s}", f"cbc_prev('{s}')", f"cbc_prev('{s}')"))

    # the demo paper and the psychsci papers as the manuscripts
    out.append(
        module_case(
            "codebook_check.demo",
            "cbc_prev('panas_high', paper = demopaper())",
            "cbc_prev('panas_high', paper=__import__('pytacheck').demopaper())",
        )
    )
    ps_r = ", ".join(f"'{p}'" for p in PSYCHSCI)
    ps_py = ", ".join(f"'{p}'" for p in PSYCHSCI)
    out.append(
        module_case(
            "codebook_check.psychsci",
            f"cbc_prev_papers(read(c({ps_r})), c('task', 'pl_a', 'scales_mixed'))",
            f"cbc_prev_papers(__import__('pytacheck').read([{ps_py}]), ['task', 'pl_a', 'scales_mixed'])",
        )
    )
    # synthetic paper lists
    out.append(
        module_case(
            "codebook_check.paperlist",
            "cbc_prev_list(c('pl_a', 'pl_b'))",
            "cbc_prev_list(['pl_a', 'pl_b'])",
        )
    )
    out.append(
        module_case(
            "codebook_check.paperlist_scales",
            "cbc_prev_list(c('task', 'scales_mixed', 'conflict'))",
            "cbc_prev_list(['task', 'scales_mixed', 'conflict'])",
        )
    )
    # codebook_max_calls = 0: stage 1 stops after the first file with groups
    out.append(
        module_case(
            "codebook_check.max_calls_zero",
            "cbc_prev_list(c('scales_mixed', 'loop'))",
            "cbc_prev_list(['scales_mixed', 'loop'])",
            codebook_max_calls=0,
        )
    )
    # a bibr export schema 12.x paper as the manuscript
    out.append(
        module_case(
            "codebook_check.bibr12_task",
            f"cbc_prev('task', paper = read('{BIBR12}'))",
            f"cbc_prev('task', paper=__import__('pytacheck').read('{BIBR12}'))",
        )
    )
    out.append(
        module_case(
            "codebook_check.bibr12_scales",
            f"cbc_prev('scales_mixed', paper = read('{BIBR12}'))",
            f"cbc_prev('scales_mixed', paper=__import__('pytacheck').read('{BIBR12}'))",
        )
    )
    # data_check output with a zero-row structure table and no previews
    out.append(
        module_case(
            "codebook_check.no_previews",
            "cbc_prev('green', drop = 'previews')",
            "cbc_prev('green', drop=['previews'])",
        )
    )

    # the data of the report tables (prose comparison skips table widgets)
    for s in TABLE_SCENARIOS:
        out.append(
            {
                "id": f"codebook_check.tables.{s}",
                "r": "base::identity",
                "py": "tests.mod_codebook.helpers.identity",
                "args": {
                    "x": {
                        "$expr": {
                            "r": f"local({{{R_HELPERS}; cbc_run_tables('{s}')}})",
                            "py": f"{PY_HELPERS}.cbc_run_tables('{s}')",
                        }
                    }
                },
            }
        )

    out += llm_cases()
    out += helper_cases()
    return out


def expr_case(cid: str, r_expr: str, py_expr: str) -> dict[str, Any]:
    return {
        "id": cid,
        "r": "base::identity",
        "py": "tests.mod_codebook.helpers.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": f"local({{{R_HELPERS}; {r_expr}}})",
                    "py": f"{PY_HELPERS}.{py_expr}",
                }
            }
        },
    }


def llm_cases() -> list[dict[str, Any]]:
    """LLM tiers with llm_use(TRUE) and llm() mocked (fixtures/llm_mock.json)."""
    runs = [
        ("merge", "conflict", "merge", ""),
        ("merge_no", "conflict", "merge_no", ""),
        ("match", "fuzzy", "match", ""),
        ("parse", "readme_text", "parse", ""),
        ("parse_long", "readme_long", "parse", ""),
        ("gate", "readme_long", "parse", "codebook_max_calls = 1"),
        ("scales", "scales_mixed", "scales", ""),
        ("selfgen", "loop", "selfgen", ""),
        ("panas", "panas_high", "panas", ""),
        ("errors", "scales_mixed", "errors", ""),
        ("empty", "empty", "merge", ""),
        ("task", "task", "scales", ""),
        ("zero_calls", "scales_mixed", "scales", "codebook_max_calls = 0"),
    ]
    out = []
    for cid, scenario, spec, extra in runs:
        r_extra = f", {extra}" if extra else ""
        py_extra = f", {extra.replace(' = ', '=')}" if extra else ""
        out.append(
            expr_case(
                f"codebook_check.llm.{cid}",
                f"cbc_llm_run('{scenario}', '{spec}'{r_extra})",
                f"cbc_llm_run('{scenario}', '{spec}'{py_extra})",
            )
        )
    out.append(
        expr_case(
            "codebook_check.llm.paperlist",
            "cbc_llm_run(NULL, 'match', prev = cbc_prev_list(c('conflict', 'fuzzy', 'scales_mixed')))",
            "cbc_llm_run(None, 'match', prev=__import__('tests.mod_codebook.helpers', "
            "fromlist=['_']).cbc_prev_list(['conflict', 'fuzzy', 'scales_mixed']))",
        )
    )
    out.append(
        expr_case(
            "codebook_check.llm.bibr12",
            f"cbc_llm_run(NULL, 'scales', prev = cbc_prev('scales_mixed', paper = read('{BIBR12}')))",
            "cbc_llm_run(None, 'scales', prev=__import__('tests.mod_codebook.helpers', "
            f"fromlist=['_']).cbc_prev('scales_mixed', paper=__import__('pytacheck').read('{BIBR12}')))",
        )
    )
    out.append(
        expr_case(
            "codebook_check.llm.demo",
            "cbc_llm_run(NULL, 'panas', prev = cbc_prev('panas_high', paper = demopaper()))",
            "cbc_llm_run(None, 'panas', prev=__import__('tests.mod_codebook.helpers', "
            "fromlist=['_']).cbc_prev('panas_high', paper=__import__('pytacheck').demopaper()))",
        )
    )
    for cid, scenario, spec in (("match", "fuzzy", "match"), ("scales", "scales_mixed", "scales")):
        out.append(
            expr_case(
                f"codebook_check.llm.tables.{cid}",
                f"cbc_report_tables(cbc_llm_run('{scenario}', '{spec}'))",
                f"report_tables(__import__('tests.mod_codebook.helpers', fromlist=['_'])"
                f".cbc_llm_run('{scenario}', '{spec}'))",
            )
        )
    return out


def helper_cases() -> list[dict[str, Any]]:
    h: list[dict[str, Any]] = []
    # stems, alpha prefixes, loop bases, number runs
    for i, (a, b) in enumerate(
        [
            ("Q1.RR_P_1", "Q1.RR_P_2"),
            ("bfi_1", "bfi_2"),
            ("apple", "orange"),
            ("AQ01", "AQ10"),
            ("__x", "__y"),
            ("", "abc"),
            ("neighdev", "neighcur"),
        ]
    ):
        h.append(
            helper_case(
                f"scale_shared_stem.{i}",
                f"e$.scale_shared_stem('{a}', '{b}')",
                f"_scale_shared_stem('{a}', '{b}')",
            )
        )
        h.append(
            helper_case(
                f"scale_same_number_run.{i}",
                f"e$.scale_same_number_run('{a}', '{b}')",
                f"_scale_same_number_run('{a}', '{b}')",
            )
        )
    for i, x in enumerate(["AQ01", "AQ10", "CRS_EXP", "Q1.RR_P_1", "123", "x_ 9", "Émo1"]):
        h.append(
            helper_case(
                f"scale_alpha_prefix.{i}",
                f"e$.scale_alpha_prefix('{x}')",
                f"_scale_alpha_prefix('{x}')",
            )
        )
    for i, x in enumerate(["POWER.PP1", "POWER.PP170", "PANAS", "Q3_1", "a1", "stim-12", "x_y_z9"]):
        h.append(
            helper_case(
                f"scale_loop_base.{i}", f"e$.scale_loop_base('{x}')", f"_scale_loop_base('{x}')"
            )
        )

    # item / derived split
    # deterministic rating matrices: value[i, j] = ((i * 7 + j * 3) %% k) + 1
    def mat(nrow: int, ncol: int, k: int) -> str:
        return (
            f"as.data.frame(outer(1:{nrow}, 1:{ncol}, function(i, j) ((i * 7 + j * 3) %% {k}) + 1))"
        )

    split_cases = {
        "agg_name": (
            f"local({{cols <- c(sprintf('AQ%02d', 1:10), 'AQ_SUM'); df <- {mat(20, 11, 5)}; "
            "names(df) <- cols; df$AQ_SUM <- rowSums(df[, 1:10]); list(cols = cols, df = df)})"
        ),
        "mean_value": (
            f"local({{cols <- paste0('bfi_', 1:9); df <- {mat(20, 9, 5)}; names(df) <- cols; "
            "df$bfi_9 <- rowMeans(df[, 1:8]); list(cols = cols, df = df)})"
        ),
        "totals_only": (
            "local({cols <- c('IERQ_pos', 'IERQ_persp', 'IERQ_sooth', 'IERQ_model'); "
            "df <- as.data.frame(outer(1:20, 1:4, function(i, j) i * 1.5 + j * 2.25)); "
            "names(df) <- cols; list(cols = cols, df = df)})"
        ),
        "small_block": (
            "list(cols = c('x_1', 'x_2', 'x_sum'), "
            "df = data.frame(x_1 = 1:5, x_2 = 1:5, x_sum = (1:5) * 2))"
        ),
        "wide_range": (
            "list(cols = paste0('it', 1:6), df = data.frame(it1 = c(1, 2, 3), it2 = c(2, 3, 4), "
            "it3 = c(1, 3, 5), it4 = c(2, 2, 4), it5 = c(1, 5, 3), it6 = c(0, 40, 80)))"
        ),
        "placeholders": "list(cols = c('...1', '...2', 'V3', 'q_1', 'q_2', 'q_3'), df = NULL)",
        "no_df": "list(cols = c('pss_1', 'pss_2', 'pss_3', 'pss_total'), df = NULL)",
    }
    for name, setup in split_cases.items():
        h.append(
            helper_case(
                f"scale_split_items.{name}",
                f"{{a <- {setup}; e$.scale_split_items(a$cols, a$df)}}",
                f"_scale_split_items(*{PY_HELPERS}.r_split_args('{name}'))",
            )
        )

    # prefix groups
    group_frames = {
        "zero_pad": (
            f"local({{df <- {mat(20, 25, 5)}; names(df) <- c(sprintf('AQ%02d', 1:20), 'AQ_SUM', "
            "'CRS_EXP1', 'CRS_EXP2', 'CRS_EXP3', 'id'); df})"
        ),
        "paradata": (
            "data.frame(psqi_1_response_numeric = 1:5, psqi_1_response_time = 1:5, "
            "psqi_2_response_numeric = 1:5, psqi_2_response_time = 1:5, "
            "psqi_3_response_numeric = 1:5, psqi_3_response_time = 1:5, check.names = FALSE)"
        ),
        "loop": (
            f"local({{df <- {mat(10, 17, 7)}; names(df) <- c(sprintf('POWER.PP%d_%d', "
            "rep(1:3, each = 4), rep(1:4, 3)), sprintf('slider_%d', 1:5)); df})"
        ),
        "word_break": (
            "data.frame(matwarmth1 = 1:4, matwarmth2 = 1:4, matwarmth3 = 1:4, mataggr1 = 1:4, "
            "mataggr2 = 1:4, mataggr3 = 1:4, neighdev = 1:4, neighcur = 1:4, x1 = 1:4)"
        ),
        "too_few": "data.frame(a = 1:3, b = 1:3)",
        "dup_names": (
            "local({df <- data.frame(id = 1:3, a = 1:3, b = 1:3, c = 1:3, StartDate = 'x'); "
            "names(df) <- c('id', 'POWER', 'POWER', 'POWER', 'StartDate'); df})"
        ),
    }
    for name, frame in group_frames.items():
        h.append(
            helper_case(
                f"scale_prefix_groups.{name}",
                f"e$.scale_prefix_groups({frame})",
                f"_scale_prefix_groups({PY_HELPERS}.r_frame('{name}'))",
            )
        )
    h.append(
        helper_case(
            "scale_prefix_groups.qualtrics",
            "e$.scale_prefix_groups(data.frame(TIPI_1 = 1:3, TIPI_2 = 1:3, TIPI_3 = 1:3, "
            "Q5_1 = 1:3, Q5_2 = 1:3, Q7 = 1:3), qualtrics = TRUE)",
            f"_scale_prefix_groups({PY_HELPERS}.r_frame('qualtrics'), qualtrics=True)",
        )
    )
    h.append(
        helper_case(
            "scale_prefix_groups.scale_group",
            "e$.scale_prefix_groups(data.frame(Q1 = 1:3, Q2 = 1:3, Q3 = 1:3, zz_a = 1:3, zz_b = 1:3), "
            "scale_group = c(Q1 = 'SAT', Q2 = 'SAT', Q3 = 'SAT'))",
            f"_scale_prefix_groups({PY_HELPERS}.r_frame('scale_group'), "
            "scale_group=[('Q1', 'SAT'), ('Q2', 'SAT'), ('Q3', 'SAT')])",
        )
    )
    # paradata / machinery / duplicates
    h.append(
        helper_case(
            "paradata_col.psqi",
            f"e$.paradata_col({group_frames['paradata']})",
            f"_paradata_col({PY_HELPERS}.r_frame('paradata'))",
        )
    )
    h.append(
        helper_case(
            "paradata_col.qualtrics",
            "e$.paradata_col(data.frame(StartDate = 1, Q1_DO_order = 1, Q2_TEXT = 'x', "
            "eraitem16timing_First.Click = 1, Q8timing_Page.Submit = 1, sleep_time = 1, "
            "demo1time_Last.Click = 1, bfi_1 = 3, check.names = FALSE))",
            f"_paradata_col({PY_HELPERS}.r_frame('paradata_qualtrics'))",
        )
    )
    h.append(
        helper_case(
            "paradata_col.jspsych",
            "e$.paradata_col(data.frame(rt = c(500, 600), stimulus = c('a', 'b'), response = c('j', 'f'), "
            "trial_type = c('html-keyboard-response', 'html-keyboard-response'), trial_index = 0:1, "
            "time_elapsed = c(1000, 2000), internal_node_id = c('0.0-0.0', '0.0-1.0'), browser = 'x', "
            "correct = c(TRUE, FALSE)))",
            f"_paradata_col({PY_HELPERS}.r_frame('jspsych'))",
        )
    )
    h.append(
        helper_case(
            "paradata_platform_col.psychopy",
            "e$.paradata_platform_col(c('trials.thisN', 'key_resp.rt', 'key_resp.keys', 'frameRate', "
            "'image.started', 'loop.thisRepN', 'stim'), 'psychopy')",
            "_paradata_platform_col(['trials.thisN', 'key_resp.rt', 'key_resp.keys', 'frameRate', "
            "'image.started', 'loop.thisRepN', 'stim'], 'psychopy')",
        )
    )
    h.append(
        helper_case(
            "paradata_platform_col.inquisit",
            "e$.paradata_platform_col(c('date', 'stimulusnumber1', 'stimulusitem1', 'trialduration', "
            "'response'), 'inquisit')",
            "_paradata_platform_col(['date', 'stimulusnumber1', 'stimulusitem1', 'trialduration', "
            "'response'], 'inquisit')",
        )
    )
    h.append(
        helper_case(
            "paradata_platform_col.none",
            "e$.paradata_platform_col(c('date', 'browser'), NULL)",
            "_paradata_platform_col(['date', 'browser'], None)",
        )
    )
    h.append(
        helper_case(
            "codebook_label_machinery.basic",
            "e$.codebook_label_machinery(data.frame(source_file = 's.csv', "
            "column_name = c('StartDate', 'Q1_DO_order', 'Q2_TEXT', 'bfi_1'), label = NA_character_, "
            "codebook_variable = NA_character_, label_status = 'unlabelled', label_method = NA_character_), "
            "list(s.csv = data.frame(StartDate = '2024-01-01', Q1_DO_order = 1, Q2_TEXT = 'free text', "
            "bfi_1 = 3, check.names = FALSE)))",
            f"_codebook_label_machinery(*{PY_HELPERS}.r_machinery('basic'))",
        )
    )
    h.append(
        helper_case(
            "codebook_label_machinery.documented",
            "e$.codebook_label_machinery(data.frame(source_file = 's.csv', column_name = 'Q2_TEXT', "
            "label = 'Other, please specify', codebook_variable = 'Q2_TEXT', label_status = 'labelled', "
            "label_method = 'rules'), list(s.csv = data.frame(Q2_TEXT = 'x', check.names = FALSE)))",
            f"_codebook_label_machinery(*{PY_HELPERS}.r_machinery('documented'))",
        )
    )
    h.append(
        helper_case(
            "codebook_duplicate_name_warnings.mixed",
            "e$.codebook_duplicate_name_warnings(list(clean.csv = data.frame(a = 1:3, b = 1:3), "
            "loop.csv = local({d <- data.frame(1, 2, 3, 4, 5, 6, 7); names(d) <- c('B', 'a', 'B', 'a', "
            "'a', 'c', 'c'); d})))",
            f"_codebook_duplicate_name_warnings({PY_HELPERS}.r_dup_previews())",
        )
    )
    # propagation
    h.append(
        helper_case(
            "propagate_scale_by_prefix.siblings",
            "e$.propagate_scale_by_prefix(data.frame(source_file = 's.csv', "
            "column_name = c('bfi_1', 'bfi_2', 'bfi_3', 'other_1'), scale = c('Big Five Inventory', NA, NA, NA), "
            "scale_confidence = c('high', NA, NA, NA), scale_source = c('manuscript', NA, NA, NA)))",
            f"_propagate_scale_by_prefix({PY_HELPERS}.r_propagate('siblings'))",
        )
    )
    h.append(
        helper_case(
            "propagate_scale_by_prefix.no_overwrite",
            "e$.propagate_scale_by_prefix(data.frame(source_file = c('s.csv', 's.csv', 't.csv'), "
            "column_name = c('bfi_1', 'bfi_2', 'bfi_3'), scale = c('Big Five Inventory', 'Something Else', NA), "
            "scale_confidence = c('high', 'low', NA), scale_source = c('manuscript', 'self_generated', NA)))",
            f"_propagate_scale_by_prefix({PY_HELPERS}.r_propagate('no_overwrite'))",
        )
    )
    # text scanning
    for i, (pat_name, acr, text) in enumerate(
        [
            (
                "Raven's Advanced Progressive Matrices",
                None,
                "we used Raven's Advanced Progressive Matrices",
            ),
            (
                "Raven's Advanced Progressive Matrices",
                None,
                "we used Raven’s Advanced Progressive Matrices",
            ),
            ("Positive and Negative Affect Schedule", "PANAS", "the panas was given"),
            ("Big Five Inventory (BFI-2)", "BFI-2", "no mention"),
        ]
    ):
        r_acr = "NA" if acr is None else f"'{acr}'"
        py_acr = "None" if acr is None else f"'{acr}'"
        rq = pat_name.replace("'", "\\'")
        h.append(
            helper_case(
                f"scale_text_pattern.{i}",
                f"{{p <- e$.scale_text_pattern('{rq}', {r_acr}); list(p, grepl(p, '{text.replace(chr(39), chr(92) + chr(39))}', perl = TRUE, ignore.case = TRUE))}}",
                f"pattern_check({pat_name!r}, {py_acr}, {text!r})",
                py_base=PY_HELPERS,
            )
        )
    for i, (scale, sents) in enumerate(
        [
            ("Meaning in Everyday Scale (MES)", ["response times increased over trials"]),
            ("Meaning in Everyday Scale (MES)", ["the MES was administered first"]),
            (
                "Positive and Negative Affect Schedule",
                ["We used the positive and negative affect schedule."],
            ),
            ("", ["x"]),
            ("PANAS", []),
            ("Short (AB) scale (C-D1)", ["the c-d1 items", "nothing"]),
        ]
    ):
        r_sents = "character(0)" if not sents else "c(" + ", ".join(f"'{s}'" for s in sents) + ")"
        h.append(
            helper_case(
                f"scale_name_in_text.{i}",
                f"e$.scale_name_in_text('{scale}', {r_sents})",
                f"_scale_name_in_text({scale!r}, {sents!r})",
            )
        )
    for i, texts in enumerate(
        [
            ["Participants completed a Stroop task.", "Reaction times were recorded."],
            [],
            ["We used the Implicit Association Task and a flanker test."],
        ]
    ):
        r_txt = "character(0)" if not texts else "c(" + ", ".join(f"'{t}'" for t in texts) + ")"
        h.append(
            helper_case(
                f"scan_paper_for_tasks.{i}",
                f"e$.scan_paper_for_tasks(test_paper({r_txt}))",
                f"_scan_paper_for_tasks(__import__('pytacheck').test_paper({texts!r}))",
            )
        )
    h.append(
        helper_case(
            "scan_paper_for_scales.demo",
            "e$.scan_paper_for_scales(demopaper())",
            "_scan_paper_for_scales(__import__('pytacheck').demopaper())",
        )
    )
    h.append(
        helper_case(
            "scan_paper_for_scales.text",
            "e$.scan_paper_for_scales(test_paper(c('We used the PANAS and the Perceived Stress Scale.', "
            "'The Big Five Inventory was given too.')))",
            "_scan_paper_for_scales(__import__('pytacheck').test_paper(['We used the PANAS and the "
            "Perceived Stress Scale.', 'The Big Five Inventory was given too.']))",
        )
    )
    h.append(
        helper_case(
            "scan_paper_for_scales.empty",
            "e$.scan_paper_for_scales(test_paper(character(0)))",
            "_scan_paper_for_scales(__import__('pytacheck').test_paper([]))",
        )
    )
    # synonym merging
    h.append(
        helper_case(
            "selfgen_merge_synonyms.basic",
            "e$.selfgen_merge_synonyms(data.frame(source_file = c('a', 'a', 'a', 'a', 'b', 'b'), "
            "column_name = c('x1', 'x2', 'y1', 'z1', 'x1', 'y1'), scale = c('emotion recognition', "
            "'emotion recognition', 'emotion recognition empathic accuracy', 'Relationship Satisfaction', "
            "'trust scores', 'institutional trust'), confidence = 'medium', scale_source = 'self_generated'))",
            f"_selfgen_merge_synonyms({PY_HELPERS}.r_synonyms())",
        )
    )
    # the manuscript-scales report
    h.append(
        helper_case(
            "scale_text_report.basic",
            "e$.scale_text_report(data.frame(scale_name = c('Perceived Stress Scale', 'Big Five Inventory', "
            "'grit scale', ' ', NA), acronym = c('PSS', 'BFI', '', 'X', NA), n_items = c('10', '', '12', '', NA), "
            "administered = c('yes', 'unclear', 'no', 'yes', NA), confidence = 'high'), "
            "matched = c('Big Five Inventory (BFI)'))",
            f"_scale_text_report({PY_HELPERS}.r_text_scales(), matched=['Big Five Inventory (BFI)'])",
        )
    )
    h.append(
        helper_case(
            "scale_text_report.none",
            "e$.scale_text_report(NULL)",
            "_scale_text_report(None)",
        )
    )
    # likert options
    h.append(
        helper_case(
            "osd_likert_options.codebook",
            "e$.osd_likert_options(c('q1', 'q2'), 's.csv', NULL, data.frame(source_file = 's.csv', "
            "column_name = c('q1', 'q2'), value_labels = c(NA, "
            '\'{"1":"Low","2":"Mid","3":"High","9":"Refused"}\'), '
            'missing_values = c(NA, \'{"9":"Refused"}\')))',
            f"_osd_likert_options(['q1', 'q2'], 's.csv', None, {PY_HELPERS}.r_likert_labels())",
        )
    )
    h.append(
        helper_case(
            "osd_likert_options.observed",
            "e$.osd_likert_options(c('a', 'b', 'c'), 's.csv', data.frame(source_file = 's.csv', "
            "column_name = c('a', 'b', 'c', 'd'), min = c(1, 1, 0, 0), max = c(5, 5, 60, 7)), NULL)",
            f"_osd_likert_options(['a', 'b', 'c'], 's.csv', {PY_HELPERS}.r_likert_columns(), None)",
        )
    )
    h.append(
        helper_case(
            "osd_likert_options.wide",
            "e$.osd_likert_options(c('a', 'b', 'c'), 's.csv', data.frame(source_file = 's.csv', "
            "column_name = c('a', 'b', 'c'), min = c(0, 0, 0), max = c(100, 100, 100)), NULL)",
            f"_osd_likert_options(['a', 'b', 'c'], 's.csv', {PY_HELPERS}.r_likert_columns('wide'), None)",
        )
    )
    return h


def write_cases() -> None:
    header = (
        "# codebook_check parity cases (inst/modules/codebook_check.R), generated by\n"
        "# tests/mod_codebook/make_cases.py -- edit that script, not this file.\n"
        "# Module cases chain codebook_check onto a stored data_check output\n"
        "# (tests/mod_codebook/cbc_helpers.R / helpers.py); helper cases call the\n"
        "# module's internal functions (R: the module sourced into an environment).\n"
    )

    class Q(yaml.SafeDumper):
        pass

    def str_rep(dumper: yaml.SafeDumper, data: str) -> Any:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    Q.add_representer(str, str_rep)
    text = yaml.dump(
        {"area": "mod_codebook", "cases": cases()},
        Dumper=Q,
        sort_keys=False,
        allow_unicode=True,
        width=100000,
    )
    (ROOT / "parity" / "cases" / "mod_codebook.yaml").write_text(header + text, encoding="utf-8")


if __name__ == "__main__":
    write_cases()
