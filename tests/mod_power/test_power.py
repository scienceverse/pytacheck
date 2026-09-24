"""Port of metacheck's tests/testthat/test-module-power.R (plus R-checked edge cases).

The R tests mock ``ellmer::chat()``; here ``pytacheck.llm.providers.chat`` is
replaced by a :class:`FakeChat`. Expected values not in the R tests were
produced by running the same mocks through metacheck (R 4.5.3, pinned commit).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

import pytacheck as pc
from pytacheck.module import ModuleError, module_list, module_run
from pytacheck.modules import _power
from tests.mod_power.parity_helpers import paragraphs
from tests.mod_power.support import FakeChat, structured_lookup

POWER_TEXT = [
    "An a priori power analysis for an independent samples t-test, conducted using the "
    "pwr.t.test function from pwr (Champely, 2020), indicated that for a Cohen's d = 0.5, an "
    "alpha level of 0.05, and a desired power level of 80% required at least 64 participants "
    "in each group.",
    "A sensitivity power analysis for an independent samples t-test, conducted using the "
    "pwr.t.test function from pwr (Champely, 2020), indicated that with 64 participants in "
    "each group, and an alpha level of 0.05, a desired power level of 80% was reached for an "
    "effect size of d = 0.5.",
]
MOTH = "Our 12 participants have a lot of power to detect a moth."
SIMPLE = (
    "An a priori power analysis indicated 64 participants per group, d = 0.5, alpha = .05, "
    "power = 80%, using an unpaired t-test, run with pwr."
)
COMPLETE_ANALYSIS = {
    "power_type": "apriori",
    "statistical_test": "unpaired t-test",
    "sample_size": 128,
    "alpha_level": 0.05,
    "power": 0.8,
    "effect_size": 0.5,
    "effect_size_metric": "Cohen's d",
    "software": "pwr",
}
FENCED = (
    '```json\n[{"power_type":"apriori","statistical_test":"unpaired t-test",'
    '"statistical_test_other":null,"sample_size":128,"alpha_level":0.05,'
    '"power":0.8,"effect_size":0.5,"effect_size_metric":"Cohen\'s d",'
    '"effect_size_metric_other":null,"software":"pwr"}]\n```'
)

MockChat = Callable[[Any], None]


def na(x: Any) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def values(s: pd.Series) -> list[Any]:
    return [None if na(v) else v for v in s.tolist()]


def rejects_structured(text: str, type: Any) -> Any:
    raise RuntimeError("HTTP 400: json_validate_failed")


@pytest.fixture
def schema_served() -> Any:
    """The fallback's schema download, served locally."""
    with respx.mock(assert_all_called=False) as router:
        router.get(_power.SCHEMA_URL).mock(
            return_value=httpx.Response(200, text=_power.SCHEMA + "\n")
        )
        yield router


# -- no LLM ----------------------------------------------------------------------


def test_power_no_llm(llm_off: None) -> None:
    assert "power" in module_list()["name"].tolist()

    # no relevant text
    mo = module_run(pc.test_paper(["I love to power pose."]), "power")
    assert mo.traffic_light == "na"
    assert len(mo.table) == 0
    assert len(mo.summary_table) == 1
    assert mo.summary_table["power_n"].tolist() == [0]
    assert na(mo.summary_table["power_complete"].iloc[0])

    # several power sentences in one paragraph
    paper = pc.test_paper(POWER_TEXT)
    mo = module_run(paper, "power")
    assert mo.traffic_light == "yellow"
    assert len(mo.table) == 1
    assert mo.table["power_type"].tolist() == ["apriori"]
    assert len(mo.summary_table) == 1
    assert mo.summary_table["power_n"].tolist() == [1]
    assert na(mo.summary_table["power_complete"].iloc[0])

    # multiple paragraphs
    mo = module_run(paragraphs(POWER_TEXT, [0, 1]), "power")
    assert mo.traffic_light == "yellow"
    assert len(mo.table) == 2
    assert mo.table["power_type"].tolist() == ["apriori", "sensitivity"]
    assert mo.summary_table["power_n"].tolist() == [2]
    assert na(mo.summary_table["power_complete"].iloc[0])

    # multiple papers
    papers = pc.PaperList([pc.test_paper([POWER_TEXT[0]]), pc.test_paper([POWER_TEXT[1]])])
    mo = module_run(papers, "power")
    assert mo.traffic_light == "yellow"
    assert len(mo.table) == 2
    assert mo.table["power_type"].tolist() == ["apriori", "sensitivity"]
    assert len(mo.summary_table) == 2
    assert mo.summary_table["power_n"].tolist() == [1, 1]
    assert all(na(v) for v in mo.summary_table["power_complete"])

    # only false positives
    mo = module_run(pc.test_paper([MOTH]), "power")
    assert mo.traffic_light == "yellow"
    assert len(mo.table) == 1
    assert mo.summary_table["power_n"].tolist() == [1]


def test_power_no_llm_output_shape(llm_off: None) -> None:
    mo = module_run(pc.demopaper(), "power")
    assert list(mo.table.columns)[-3:] == ["power_type", "complete", "power_id"]
    assert mo.table["power_type"].tolist() == ["unknown", "sensitivity", "unknown"]
    assert mo.table["power_id"].tolist() == [1, 2, 3]
    assert mo.summary_text == "We detected 3 potential power analyses."
    assert list(mo.summary_table.columns) == ["paper_id", "power_n", "power_complete"]
    assert mo.report[0].startswith("You chose to not use an LLM")
    info, text = pc.report.blocks.ReportTable, None
    tables = [b.data for b in mo.report if isinstance(b, info)]
    assert list(tables[0].columns) == ["power_id", "power_type"]
    text = tables[1]
    assert (
        text["text"]
        .iloc[1]
        .startswith("We conducted a <strong>sensitivity</strong> <strong>power</strong> analysis")
    )


def test_power_regex_classification(llm_off: None) -> None:
    texts = [
        "An a-priori power analysis showed that 50 participants give 80% power.",
        "A sensitivity power analysis showed that 50 participants give 80% power for d = 0.4.",
        "A compromise power analysis balanced alpha and beta for 50 participants.",
        "A post-hoc power analysis showed 45% power.",
        "The retrospective power was 30% for the observed effect size.",
        "An A Posteriori power analysis gave 20% power.",
        "The observed power of the test was 0.25 (small effect).",
        "Power analysis: with 2019 participants we achieve power.",  # a year is not a number
    ]
    mo = module_run(paragraphs(texts, list(range(len(texts)))), "power")
    # rows come in text_search() order (by the first power word each paragraph matched)
    by_paragraph = dict(zip(mo.table["paragraph_id"], mo.table["power_type"], strict=True))
    assert by_paragraph == {
        0: "apriori",
        1: "sensitivity",
        2: "compromise",
        3: "posthoc",
        4: "posthoc",
        5: "posthoc",
        6: "unknown",
    }
    assert any("classified as 'post-hoc'" in b for b in mo.report if isinstance(b, str))
    tables = [b.data for b in mo.report if isinstance(b, pc.report.blocks.ReportTable)]
    highlighted = tables[1]["text"].tolist()
    assert "The <strong>observed power</strong> of the test was 0.25 (small effect)." in highlighted
    assert (
        "The <strong>retrospective power</strong> was 30% for the observed effect size."
        in highlighted
    )
    assert (
        "An <strong>A Posteriori</strong> <strong>power</strong> analysis gave 20% "
        "<strong>power</strong>." in highlighted
    )


def test_power_duplicate_paragraph_text(llm_off: None) -> None:
    text = "We had 95% power to detect our effect."
    mo = module_run(paragraphs([text, text], [0, 1]), "power")
    tables = [b.data for b in mo.report if isinstance(b, pc.report.blocks.ReportTable)]
    assert tables[0]["power_id"].tolist() == [1, 2]
    assert tables[1]["power_id"].tolist() == ["1;2"]


def test_power_does_not_call_llm_without_candidates(llm_on: None, mock_chat: MockChat) -> None:
    mock_chat(FakeChat())  # any call would fail
    mo = module_run(pc.test_paper(["I love to power pose."]), "power")
    assert mo.traffic_light == "na"
    assert mo.summary_text == "No power analyses were detected."


# -- with LLM (structured) ------------------------------------------------------------


def test_power_with_llm_structured(llm_on: None, mock_chat: MockChat) -> None:
    # only false positives
    mock_chat(structured_lookup({}))
    mo = module_run(pc.test_paper([MOTH]), "power")
    assert mo.traffic_light == "na"
    assert len(mo.table) == 0
    assert mo.summary_table["power_n"].tolist() == [0]

    # only some info
    paper = pc.test_paper(
        [
            "The a priori power analysis determined a sample size of 15 in each group for 80% "
            "power with a medium effect size."
        ]
    )
    mock_chat(
        structured_lookup(
            {
                "sample size of 15": {
                    "power_analyses": [{"power_type": "apriori", "sample_size": 30, "power": 0.8}]
                }
            }
        )
    )
    mo = module_run(paper, "power")
    assert mo.traffic_light == "red"
    assert len(mo.table) == 1
    assert values(mo.table["sample_size"]) == [30]
    assert values(mo.table["power"]) == [0.8]
    assert values(mo.table["effect_size"]) == [None]
    assert values(mo.table["alpha_level"]) == [None]
    assert mo.table["complete"].tolist() == [False]

    # the example from the prompt: two power analyses in one paragraph
    paper = pc.test_paper(
        [
            "An a priori power analysis was conducted to estimate the sample size required to "
            "achieve 80% power to detect a Cohen's d of 0.2 using an unpaired t-test at an alpha "
            "level of 0.05. This required a total sample size of 300 participants. A second a "
            "priori power analysis was conducted to estimate the required sample size for a "
            "secondary outcome. To achieve 80% power to detect a Cohen's f of 0.1 using a "
            "one-way ANOVA, a sample size of 350 was required. The a priori power analyses were "
            "conducted with G*Power."
        ]
    )
    mock_chat(
        structured_lookup(
            {
                "A second a priori power analysis": {
                    "power_analyses": [
                        {
                            "power_type": "apriori",
                            "statistical_test": "unpaired t-test",
                            "sample_size": 300,
                            "alpha_level": 0.05,
                            "power": 0.8,
                            "effect_size": 0.2,
                            "effect_size_metric": "Cohen's d",
                            "software": "G*Power",
                        },
                        {
                            "power_type": "apriori",
                            "statistical_test": "1-way ANOVA",
                            "sample_size": 350,
                            "power": 0.8,
                            "effect_size": 0.1,
                            "effect_size_metric": "Cohen's f",
                            "software": "G*Power",
                        },
                    ]
                }
            }
        )
    )
    mo = module_run(paper, "power")
    assert mo.traffic_light == "red"
    assert len(mo.table) == 2
    assert mo.table["statistical_test"].tolist() == ["unpaired t-test", "1-way ANOVA"]
    assert values(mo.table["sample_size"]) == [300, 350]
    assert values(mo.table["alpha_level"]) == [0.05, None]
    assert values(mo.table["power"]) == [0.8, 0.8]
    assert values(mo.table["effect_size"]) == [0.2, 0.1]
    assert mo.table["effect_size_metric"].tolist() == ["Cohen's d", "Cohen's f"]
    assert mo.table["software"].tolist() == ["G*Power", "G*Power"]
    assert mo.table["complete"].tolist() == [True, False]

    # no relevant text
    mo = module_run(pc.test_paper(["I love to power pose."]), "power")
    assert mo.traffic_light == "na"
    assert len(mo.table) == 0
    assert mo.summary_table["power_n"].tolist() == [0]
    assert na(mo.summary_table["power_complete"].iloc[0])

    # several power sentences in one paragraph: one call, two analyses
    power_text = [
        POWER_TEXT[0],
        "A sensitivity power analysis for an independent samples t-test, conducted using "
        "G*Power, indicated that with 64 participants in each group, and an alpha level of 0.05, "
        "power of 0.91 was reached for an effect size of d = 0.5.",
    ]
    mock_chat(
        structured_lookup(
            {
                "pwr.t.test function from pwr": {
                    "power_analyses": [
                        {**COMPLETE_ANALYSIS},
                        {
                            **COMPLETE_ANALYSIS,
                            "power_type": "sensitivity",
                            "power": 0.91,
                            "software": "G*Power",
                        },
                    ]
                }
            }
        )
    )
    mo = module_run(pc.test_paper(power_text), "power")
    assert mo.traffic_light == "green"
    assert len(mo.table) == 2
    assert mo.table["power_type"].tolist() == ["apriori", "sensitivity"]
    assert mo.table["statistical_test"].tolist() == ["unpaired t-test", "unpaired t-test"]
    assert values(mo.table["sample_size"]) == [128, 128]
    assert values(mo.table["alpha_level"]) == [0.05, 0.05]
    assert values(mo.table["power"]) == [0.8, 0.91]
    assert values(mo.table["effect_size"]) == [0.5, 0.5]
    assert mo.table["effect_size_metric"].tolist() == ["Cohen's d", "Cohen's d"]
    assert mo.table["software"].tolist() == ["pwr", "G*Power"]
    assert mo.summary_table["power_n"].tolist() == [2]
    assert mo.summary_table["power_complete"].tolist() == [2]
    assert "All essential information could be detected." in mo.report

    # incomplete power
    power_text = [
        "An a priori power analysis for an independent samples t-test, conducted using the "
        "pwr.t.test function from pwr (Champely, 2020), indicated that for a Cohen's d = 0.5, "
        "and a desired power level of 80% required at least 64 participants in each group.",
        "A sensitivity power analysis for a paired samples t-test, conducted using G-Power, "
        "indicated that with 64 participants, an adequate power was reached for an effect size "
        "of d = 0.5.",
    ]
    apriori_incomplete = {
        "power_type": "apriori",
        "statistical_test": "unpaired t-test",
        "sample_size": 128,
        "power": 0.8,
        "effect_size": 0.5,
        "effect_size_metric": "Cohen's d",
        "software": "pwr",
    }
    sensitivity_incomplete = {
        "power_type": "sensitivity",
        "statistical_test": "paired t-test",
        "sample_size": 64,
        "effect_size": 0.5,
        "effect_size_metric": "Cohen's d",
        "software": "G*Power",
    }
    mock_chat(
        structured_lookup(
            {
                "for a Cohen's d = 0.5, and a desired power": {
                    "power_analyses": [apriori_incomplete, sensitivity_incomplete]
                }
            }
        )
    )
    mo = module_run(pc.test_paper(power_text), "power")

    def check_incomplete(mo: Any, n_papers: int) -> None:
        assert mo.traffic_light == "red"
        assert len(mo.table) == 2
        assert mo.table["power_type"].tolist() == ["apriori", "sensitivity"]
        assert mo.table["statistical_test"].tolist() == ["unpaired t-test", "paired t-test"]
        assert values(mo.table["sample_size"]) == [128, 64]
        assert values(mo.table["alpha_level"]) == [None, None]
        assert values(mo.table["power"]) == [0.8, None]
        assert values(mo.table["effect_size"]) == [0.5, 0.5]
        assert mo.table["effect_size_metric"].tolist() == ["Cohen's d", "Cohen's d"]
        assert mo.table["software"].tolist() == ["pwr", "G*Power"]
        assert len(mo.summary_table) == n_papers
        assert mo.summary_table["power_n"].tolist() == [2 // n_papers] * n_papers
        assert mo.summary_table["power_complete"].tolist() == [0] * n_papers

    check_incomplete(mo, 1)

    # multiple papers: each paper's text is its own call
    papers = pc.PaperList([pc.test_paper([power_text[0]]), pc.test_paper([power_text[1]])])
    mock_chat(
        structured_lookup(
            {
                "for a Cohen's d = 0.5, and a desired power": {
                    "power_analyses": [apriori_incomplete]
                },
                "paired samples t-test": {"power_analyses": [sensitivity_incomplete]},
            }
        )
    )
    check_incomplete(module_run(papers, "power"), 2)


def test_power_model_attribution_and_no_fallback_notice(llm_on: None, mock_chat: MockChat) -> None:
    from pytacheck.llm import llm_model
    from pytacheck.utils import local_options

    test_model = "groq/llama-3.3-70b-versatile"
    with local_options({"metacheck.llm.model": test_model}):
        assert llm_model() == test_model
        mock_chat(
            structured_lookup(
                {"64 participants per group": {"power_analyses": [COMPLETE_ANALYSIS]}}
            )
        )
        mo = module_run(pc.test_paper([SIMPLE]), "power")
    assert len(mo.table) == 1
    prose = [b for b in mo.report if isinstance(b, str)]
    assert any(test_model in b for b in prose)
    assert not any("prompt-based extraction" in b for b in prose)
    assert prose[0] == (
        f"We used the LLM model '{test_model}' to check the contents of 1 paragraph that "
        "contained words suggesting they might contain power analyses."
    )


def test_power_falls_back_to_prompt_fenced_extraction(
    llm_on: None, mock_chat: MockChat, schema_served: Any
) -> None:
    mock_chat(FakeChat(chat_structured=rejects_structured, chat=lambda text: FENCED))
    with pytest.warns(UserWarning):
        mo = module_run(pc.test_paper([SIMPLE]), "power")
    assert len(mo.table) == 1
    assert mo.table["power_type"].tolist() == ["apriori"]
    assert mo.table["statistical_test"].tolist() == ["unpaired t-test"]
    assert values(mo.table["sample_size"]) == [128]
    assert values(mo.table["power"]) == [0.8]
    assert any("prompt-based extraction" in b for b in mo.report if isinstance(b, str))
    # R: the table keeps llm()'s answer column; everything was extracted -> green
    assert "answer" in mo.table.columns
    assert mo.traffic_light == "green"
    assert mo.table["complete"].tolist() == [True]
    assert schema_served.calls.call_count == 1


def test_power_fallback_prompt_includes_schema(
    llm_on: None, monkeypatch: pytest.MonkeyPatch, schema_served: Any
) -> None:
    from pytacheck.llm import providers

    prompts: list[str] = []

    def chat(*args: Any, **kwargs: Any) -> FakeChat:
        prompts.append(kwargs.get("system_prompt", ""))
        return FakeChat(chat_structured=rejects_structured, chat=lambda text: FENCED)

    monkeypatch.setattr(providers, "chat", chat)
    with pytest.warns(UserWarning):
        module_run(pc.test_paper([SIMPLE]), "power")
    assert prompts[0] == _power._STRUCTURED_PROMPT
    assert prompts[-1] == _power._PREFACE + "\n\n" + _power.SCHEMA


def test_power_fallback_json_expand_details(
    llm_on: None, mock_chat: MockChat, schema_served: Any
) -> None:
    # R (mocked ellmer::chat): "none" rows and unparsable answers are dropped; a JSON
    # "text" key collides with the paragraph text and becomes "text.power"
    def chat(text: str) -> str:
        if "a priori" in text:
            return (
                '```json\n[{"text": "x", "power_type":"apriori","sample_size":128, '
                '"power": 0.8}, {"power_type":"posthoc","sample_size":null}]\n```'
            )
        if "sensitivity" in text:
            return "not json at all"
        return '```json\n[{"power_type":"none"}]\n```'

    paper = paragraphs(
        [
            "An a priori power analysis indicated 64 participants per group, d = 0.5, "
            "alpha = .05, power = 80%.",
            "A sensitivity power analysis showed 80% power for d = 0.4.",
            "We did not run a power analysis but had 90% power in 2 studies.",
        ],
        [0, 1, 2],
    )
    mock_chat(FakeChat(chat_structured=rejects_structured, chat=chat))
    with pytest.warns(UserWarning):
        mo = module_run(paper, "power")
    assert list(mo.table.columns)[8:] == [
        "answer",
        "text.power",
        "power_type",
        "sample_size",
        "power",
        "error",
        "complete",
        "power_id",
    ]
    assert mo.table["power_type"].tolist() == ["apriori", "posthoc"]
    assert values(mo.table["text.power"]) == ["x", None]
    assert mo.table["complete"].tolist() == [True, False]
    assert mo.traffic_light == "red"
    assert mo.summary_text == "We detected 2 potential power analyses."
    assert list(mo.summary_table.columns) == [
        "paper_id",
        "power_n",
        "power_complete",
        "power_sample_size",
        "power_power",
    ]
    assert mo.summary_table.iloc[0, 1:].tolist() == [2, 1, 1, 1]
    prose = [b for b in mo.report if isinstance(b, str)]
    assert prose[0].startswith(
        "We used the LLM model 'groq/llama-3.3-70b-versatile' to check the contents of 3 paragraphs"
    )
    assert "Some essential information could not be detected: sample_size, power" in prose
    assert any("classified as 'post-hoc'" in b for b in prose)


def test_power_reports_failed_llm_check(
    llm_on: None, mock_chat: MockChat, schema_served: Any
) -> None:
    def refused(text: str) -> str:
        raise RuntimeError("connection refused")

    mock_chat(FakeChat(chat_structured=rejects_structured, chat=refused))
    paper = pc.test_paper(
        [
            "An a priori power analysis determined a sample size of 30 for 80% power with a "
            "medium effect size, using an unpaired t-test, run with pwr."
        ]
    )
    with pytest.warns(UserWarning):
        mo = module_run(paper, "power")
    assert len(mo.table) == 0
    assert mo.traffic_light == "na"
    assert "failed to run" in mo.summary_text
    assert "No power analyses were detected" not in mo.summary_text
    # R: the empty table keeps llm()'s and json_expand()'s error columns
    assert list(mo.table.columns)[-5:] == [
        "answer",
        "error",
        "error_msg",
        "error.power",
        "complete",
    ]
    assert mo.summary_table["power_n"].tolist() == [0]


def test_power_fallback_mixed_errors_propagate_llm_error(
    llm_on: None, mock_chat: MockChat, schema_served: Any
) -> None:
    # R's llm() cannot bind an errored row (answer = NA) with an ellmer_output answer
    # ("Can't combine `..1` <vctrs:::common_class_fallback> and `..2` <ellmer_output>."),
    # so a fallback where only some calls fail stops the module, as in metacheck.
    def chat(text: str) -> str:
        if "a priori" in text:
            raise RuntimeError("boom")
        return '```json\n[{"power_type":"unknown"}]\n```'

    paper = paragraphs(
        [
            "An a priori power analysis indicated 64 participants per group, d = 0.5.",
            "A sensitivity power analysis showed 80% power for d = 0.4.",
        ],
        [0, 1],
    )
    mock_chat(FakeChat(chat_structured=rejects_structured, chat=chat))
    with pytest.warns(UserWarning), pytest.raises(ModuleError, match="Can't combine"):
        module_run(paper, "power")


def test_power_fallback_schema_unreachable(llm_on: None, mock_chat: MockChat) -> None:
    # R: readLines() of the schema URL errors, so the module errors
    mock_chat(FakeChat(chat_structured=rejects_structured, chat=lambda text: FENCED))
    with respx.mock() as router:
        router.get(_power.SCHEMA_URL).mock(return_value=httpx.Response(404))
        with (
            pytest.warns(UserWarning),
            pytest.raises(
                ModuleError,
                match=r"^Running the module 'power' produced errors: cannot open the connection "
                r"to 'https://scienceverse\.org/schema/power\.json'$",
            ),
        ):
            module_run(pc.test_paper([SIMPLE]), "power")
    with respx.mock() as router:
        router.get(_power.SCHEMA_URL).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.warns(UserWarning), pytest.raises(ModuleError, match="cannot open"):
            module_run(pc.test_paper([SIMPLE]), "power")


def test_power_genuine_negative_result(llm_on: None, mock_chat: MockChat) -> None:
    mock_chat(FakeChat(chat_structured=lambda text, type: {"power_analyses": []}))
    mo = module_run(pc.test_paper([MOTH]), "power")
    assert len(mo.table) == 0
    assert mo.summary_text == "No power analyses were detected."
    assert mo.traffic_light == "na"
    assert mo.summary_table["power_n"].tolist() == [0]


def test_power_empty_result_across_all_paragraphs(llm_on: None, mock_chat: MockChat) -> None:
    mock_chat(FakeChat(chat_structured=lambda text, type: {"power_analyses": []}))
    paper = paragraphs(
        [
            "An a priori power analysis indicated 64 participants per group, d = 0.5.",
            "A sensitivity power analysis showed 80% power for d = 0.4.",
        ],
        [0, 1],
    )
    mo = module_run(paper, "power")
    assert mo.traffic_light == "na"
    assert len(mo.table) == 0
    assert mo.summary_table["power_n"].tolist() == [0]
    # R: every llm_cols column is added to the empty table
    assert list(mo.table.columns)[-9:] == [*_power.LLM_COLS, "complete"]


def test_power_structured_tibble_shape(llm_on: None, mock_chat: MockChat) -> None:
    tibble_result = {
        "power_analyses": pd.DataFrame(
            {
                "power_type": pd.Categorical(
                    ["apriori"], categories=["apriori", "sensitivity", "posthoc", "unknown"]
                ),
                "statistical_test": pd.Categorical(
                    ["unpaired t-test"], categories=["paired t-test", "unpaired t-test"]
                ),
                "statistical_test_other": pd.array([None], dtype="string"),
                "sample_size": [64.0],
                "alpha_level": [0.05],
                "power": [0.8],
                "effect_size": [0.5],
                "effect_size_metric": pd.Categorical(
                    ["Cohen's d"], categories=["Cohen's d", "other"]
                ),
                "effect_size_metric_other": pd.array([None], dtype="string"),
                "software": pd.Categorical(["G*Power"], categories=["G*Power", "pwr"]),
            }
        )
    }
    mock_chat(FakeChat(chat_structured=lambda text, type: tibble_result))
    paper = pc.test_paper(
        [
            "An a priori power analysis using G*Power indicated 64 participants per group were "
            "needed to detect a Cohen's d = 0.5 with 80% power at an alpha level of .05, using an "
            "independent samples t-test."
        ]
    )
    mo = module_run(paper, "power")
    assert len(mo.table) == 1
    assert mo.table["power_type"].tolist() == ["apriori"]
    assert mo.table["statistical_test"].tolist() == ["unpaired t-test"]
    assert values(mo.table["sample_size"]) == [64]
    assert values(mo.table["alpha_level"]) == [0.05]
    assert values(mo.table["power"]) == [0.8]
    assert values(mo.table["effect_size"]) == [0.5]
    assert mo.table["effect_size_metric"].tolist() == ["Cohen's d"]
    assert mo.table["software"].tolist() == ["G*Power"]
    assert mo.table["complete"].tolist() == [True]
    # enum-typed columns come back as plain character, not factor
    for col in ("power_type", "statistical_test", "effect_size_metric", "software"):
        assert isinstance(mo.table[col].dtype, pd.StringDtype)
    assert mo.traffic_light == "green"


def test_power_structured_omitted_key(llm_on: None, mock_chat: MockChat) -> None:
    analysis = {k: v for k, v in COMPLETE_ANALYSIS.items() if k != "alpha_level"}
    analysis["sample_size"] = 30
    mock_chat(FakeChat(chat_structured=lambda text, type: {"power_analyses": [analysis]}))
    paper = pc.test_paper(
        [
            "An a priori power analysis determined a sample size of 30 for 80% power with a "
            "medium effect size, using an unpaired t-test, run with pwr."
        ]
    )
    mo = module_run(paper, "power")
    assert "alpha_level" in mo.table.columns
    assert na(mo.table["alpha_level"].iloc[0])
    assert mo.table["complete"].tolist() == [False]
    assert mo.traffic_light == "red"


def test_power_structured_only_some_keys(llm_on: None, mock_chat: MockChat) -> None:
    # R: missing llm_cols are appended in llm_cols order; the report lists them in that order
    mock_chat(
        FakeChat(
            chat_structured=lambda text, type: {
                "power_analyses": [{"power_type": "apriori", "sample_size": 30}]
            }
        )
    )
    mo = module_run(pc.test_paper([SIMPLE]), "power")
    assert list(mo.table.columns)[8:] == [
        "power_type",
        "sample_size",
        "statistical_test",
        "alpha_level",
        "power",
        "effect_size",
        "effect_size_metric",
        "software",
        "complete",
        "power_id",
    ]
    assert (
        "Some essential information could not be detected: statistical_test, alpha_level, "
        "power, effect_size, effect_size_metric, software"
    ) in mo.report
    assert mo.summary_table.iloc[0, 1:].tolist() == [1, 0, 0, 1, 0, 0, 0, 0, 0]


def test_power_structured_partial_failure(llm_on: None, mock_chat: MockChat) -> None:
    def reply(text: str, type: Any) -> Any:
        if "64 participants per group" in text:
            return {"power_analyses": [COMPLETE_ANALYSIS]}
        raise RuntimeError("HTTP 400: json_validate_failed")

    mock_chat(FakeChat(chat_structured=reply))
    paper = paragraphs(
        [SIMPLE, "A different paragraph that always errors during structured extraction."],
        [0, 1],
    )
    # (as in R, the second paragraph has no number, so it is never sent)
    mo = module_run(paper, "power")
    assert len(mo.table) == 1
    assert mo.table["power_type"].tolist() == ["apriori"]
    assert not any("prompt-based extraction" in b for b in mo.report if isinstance(b, str))

    # a failing paragraph that is sent: its row is dropped, the others are kept
    paper = paragraphs(
        [
            SIMPLE,
            "A sensitivity power analysis showed 80% power for d = 0.4 with 100 participants, "
            "but this paragraph always errors during structured extraction.",
        ],
        [0, 1],
    )
    with pytest.warns(UserWarning, match="1 of 2 LLM extractions failed"):
        mo = module_run(paper, "power")
    assert len(mo.table) == 1
    assert mo.table["power_type"].tolist() == ["apriori"]
    assert ".error" not in mo.table.columns
    assert mo.traffic_light == "green"
    prose = [b for b in mo.report if isinstance(b, str)]
    assert "2 paragraphs" in prose[0]
    assert not any("prompt-based extraction" in b for b in prose)


def test_power_with_ollama_structured(llm_on: None, mock_chat: MockChat) -> None:
    from tests.httpmock import replay

    power_text = [
        "An a priori power analysis for an independent samples t-test, conducted using the "
        "pwr.t.test function from pwr (Champely, 2020), indicated that for a Cohen's d = 0.5, "
        "and a desired power level of 80% required at least 64 participants in each group.",
        "A sensitivity power analysis for a paired samples t-test, conducted using G-Power, "
        "indicated that with 64 participants, an adequate power was reached for an effect size "
        "of d = 0.5.",
    ]
    papers = pc.PaperList([pc.test_paper([power_text[0]]), pc.test_paper([power_text[1]])])
    mock_chat(
        structured_lookup(
            {
                "for a Cohen's d = 0.5, and a desired power": {
                    "power_analyses": [
                        {
                            "power_type": "apriori",
                            "statistical_test": "unpaired t-test",
                            "sample_size": 128,
                            "power": 0.8,
                            "effect_size": 0.5,
                            "effect_size_metric": "Cohen's d",
                            "software": "pwr",
                        }
                    ]
                },
                "paired samples t-test": {
                    "power_analyses": [
                        {
                            "power_type": "sensitivity",
                            "statistical_test": "paired t-test",
                            "sample_size": 64,
                            "effect_size": 0.5,
                            "effect_size_metric": "Cohen's d",
                            "software": "G*Power",
                        }
                    ]
                },
            }
        )
    )
    from pytacheck.utils import local_options

    # the "apis" fixtures answer llm()'s ollama_up / model-exists pre-checks
    with replay("apis"), local_options({"metacheck.llm.model": "ollama/qwen2.5:3b"}):
        mo = module_run(papers, "power")
    assert mo.traffic_light == "red"
    assert len(mo.table) == 2
    assert mo.table["power_type"].tolist() == ["apriori", "sensitivity"]
    assert mo.table["statistical_test"].tolist() == ["unpaired t-test", "paired t-test"]
    assert values(mo.table["sample_size"]) == [128, 64]
    assert values(mo.table["alpha_level"]) == [None, None]
    assert values(mo.table["power"]) == [0.8, None]
    assert values(mo.table["effect_size"]) == [0.5, 0.5]
    assert mo.table["effect_size_metric"].tolist() == ["Cohen's d", "Cohen's d"]
    assert mo.table["software"].tolist() == ["pwr", "G*Power"]
    assert len(mo.summary_table) == 2
    assert mo.summary_table["power_n"].tolist() == [1, 1]
    assert mo.summary_table["power_complete"].tolist() == [0, 0]


def test_power_seed_is_sent(llm_on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from pytacheck.llm import providers

    seen: list[Any] = []

    def chat(*args: Any, **kwargs: Any) -> FakeChat:
        seen.append(kwargs.get("params"))
        return FakeChat(chat_structured=lambda text, type: {"power_analyses": []})

    monkeypatch.setattr(providers, "chat", chat)
    module_run(pc.test_paper([SIMPLE]), "power", seed=42)
    assert "42" in repr(seen[0])


def test_power_does_not_mutate_input(llm_off: None) -> None:
    paper = pc.demopaper()
    before = paper.text.copy()
    module_run(paper, "power")
    pd.testing.assert_frame_equal(paper.text, before)


def test_read_lines_url(monkeypatch: pytest.MonkeyPatch) -> None:
    with respx.mock() as router:
        router.get("https://example.org/x.json").mock(
            return_value=httpx.Response(200, content=b"a\r\nb\rc\n\nd\n")
        )
        assert _power._read_lines_url("https://example.org/x.json") == "a\nb\nc\n\nd"


def test_power_empty_paperlist_errors(llm_off: None) -> None:
    # R: `summary_table$power_n <- 0` on the 0-row summary table of an empty
    # paper list errors (reproduced; parity case power.review.empty_paperlist)
    with pytest.raises(ModuleError, match="replacement has 1 row, data has 0"):
        module_run(pc.PaperList([]), "power")


def test_power_empty_paper(llm_off: None) -> None:
    paper = pc.test_paper([])
    paper.paper_id = "p1"
    mo = module_run(paper, "power")
    assert mo.traffic_light == "na"
    assert len(mo.table) == 0
    assert mo.summary_table["paper_id"].tolist() == ["p1"]
    assert mo.summary_table["power_n"].tolist() == [0]


# R: grepl(p, x, ignore.case = TRUE) (TRE) -- an ASCII letter matches only its ASCII
# other case, never U+0130/U+0131 (i/I), U+212A (Kelvin sign) or U+017F (long s)
@pytest.mark.parametrize(
    ("pattern", "text", "expected"),
    [
        ("i", "İ", False),
        ("I", "ı", False),
        ("k", "K", False),
        ("s", "ſ", False),
        ("[a-z]", "İ", False),
        ("[a-z]", "K", False),
        ("[A-Z]", "k", True),
        ("[[:lower:]]", "A", True),
        ("sensitivity", "SENSITIVITY", True),
        ("sensitivity", "SENSİTİVİTY", False),
        ("a[- ]?priori", "A PRİORİ", False),
        ("post[- ]?hoc", "POſT HOC", False),
    ],
)
def test_tre_ignore_case_is_ascii_for_ascii_letters(
    pattern: str, text: str, expected: bool
) -> None:
    from pytacheck._r import grepl

    assert grepl(pattern, [text], ignore_case=True) == [expected]


def test_tre_ignore_case_negated_bracket() -> None:
    # R: regmatches(x, gregexpr("[^a-z]+", x, ignore.case = TRUE))
    from pytacheck._r import gsub, regextract_all

    x = "T+ participants: M = 0.22 s"
    assert regextract_all("[^a-z]+", [x], ignore_case=True) == [["+ ", ": ", " = 0.22 "]]
    assert gsub("[^]a-]+", "<\\0>", ["a]b-c]]--K"], ignore_case=True) == ["a]<0>-<0>]]--<0>"]


def test_power_highlight_is_tre_case_insensitive(llm_off: None) -> None:
    # parity case power.review.tricky.tables: R does not highlight "A PRİORİ"
    paper = paragraphs(
        ["An A PRİORİ power analysis required 50 participants.", SIMPLE], [1, 2], "p1"
    )
    mo = module_run(paper, "power")
    texts = [b.data for b in mo.report if hasattr(b, "data")][1]["text"].tolist()
    assert texts[0] == "An A PRİORİ <strong>power</strong> analysis required 50 participants."
    assert texts[1].startswith("An <strong>a priori</strong> <strong>power</strong> analysis")
