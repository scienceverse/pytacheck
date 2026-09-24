"""The helpers of the prereg_check module (``pytacheck.modules._prereg``).

Expected values were produced by R 4.5 / jsonlite (``fromJSON(simplifyVector =
TRUE)``) and base R's ``unlist()``, ``c()``, ``paste()`` and ``as.character()``.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from pytacheck.modules import _prereg as pr
from pytacheck.modules._prereg import RList, RVec


def chr1(x: str) -> RVec:
    return RVec("character", (x,))


# -- as.character() of list elements (R: as.character(list(...))) ------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (RVec("integer", (1, 2)), "1:2"),
        (RVec("integer", (1, 3)), "c(1, 3)"),
        (RVec("integer", (5,)), "5"),
        (RVec("double", (1.5, 2.0)), "c(1.5, 2)"),
        (RVec("double", (1e-20,)), "1e-20"),
        (RVec("double", (123456789012.0,)), "123456789012"),
        (RVec("logical", (True, None)), "c(TRUE, NA)"),
        (RVec("character", ('a"b', "c\\d\n")), 'c("a\\"b", "c\\\\d\\n")'),
        (RVec("character", (None,)), "NA"),
        (RVec("character", (None, "x")), 'c(NA, "x")'),
        (None, "NULL"),
        (RList((chr1("x"), RVec("double", (1.0,))), ("a", "b-c")), 'list(a = "x", `b-c` = 1)'),
        (RVec("character", ()), "character(0)"),
        (RList(()), "list()"),
        (chr1("é"), "é"),
        (RVec("character", ("é", "ü")), 'c("é", "ü")'),
        (RVec("double", (1 / 3,)), "0.333333333333333"),
        (RVec("double", (1 / 3, 2.0)), "c(0.333333333333333, 2)"),
        (RVec("double", (100000.0,)), "1e+05"),
        (RVec("double", (100000.0, 1.0)), "c(1e+05, 1)"),
    ],
)
def test_as_character_of_list_elements(value: object, expected: str) -> None:
    assert pr.paste_collapse(RList((value,)), " ") == expected  # type: ignore[arg-type]


# -- jsonlite simplification + unlist() ----------------------------------------------

_JSON = json.loads(
    '{"a": [{"x": 1}, null, {"y": "b"}], "b": [[1, 2], [3]], "c": [[1, "a"], [2, "b"]],'
    ' "d": [1, null, 2], "e": [null, null], "f": [{"x": [1, 2]}, {"x": [3]}], "g": 3000000000,'
    ' "h": [1, 2.5], "i": ["a", 1, true], "j": [[], []], "m": [[1, 2], [3, 4]],'
    ' "n": [null, "z"], "o": [true, 2], "p": {"k": null, "l": ["q"]}, "q": []}'
)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("a", "1 NA NA NA NA b"),  # records -> data frame, walked column by column
        ("b", "1 2 3"),
        ("c", "1 2 a b"),  # a character matrix, column-major
        ("d", "1 NA 2"),
        ("e", "NA NA"),
        ("f", "1 2 3"),
        ("g", "3e+09"),
        ("h", "1 2.5"),
        ("i", "a 1 TRUE"),
        ("j", ""),
        ("m", "1 3 2 4"),
        ("n", "NA z"),
        ("o", "1 2"),
        ("p", "q"),
        ("q", ""),
    ],
)
def test_paste_unlist(key: str, expected: str) -> None:
    value = pr.simplify(_JSON[key])
    assert pr.paste_collapse(pr.unlist(value), " ") == expected


_FILES = json.loads(
    '{"a": [{"file_id": "x1", "n": 2, "u": {"h": "h1", "d": "d1"}},'
    ' {"file_id": "x2", "n": 3.5, "u": {"h": "h2", "d": "d2"}}],'
    ' "b": ["s1", "s2"], "c": "one", "h": {"k": null, "l": ["q"]}}'
)


def test_c_and_paste() -> None:
    s = {k: pr.simplify(v) for k, v in _FILES.items()}
    # paste(c(x$a, NULL), collapse = " ")
    assert pr.paste_collapse(pr.r_c([s["a"], None]), " ") == (
        'c("x1", "x2") c(2, 3.5) list(h = c("h1", "h2"), d = c("d1", "d2"))'
    )
    # paste(x$a, collapse = "\n\n")
    assert pr.paste_collapse(s["a"], "\n\n") == (
        'c("x1", "x2")\n\nc(2, 3.5)\n\nlist(h = c("h1", "h2"), d = c("d1", "d2"))'
    )
    assert pr.paste_collapse(pr.r_c([s["b"], s["c"]]), " ") == "s1 s2 one"
    assert pr.paste_collapse(pr.r_c([s["h"], s["c"]]), " ") == "NULL q one"
    assert pr.paste_collapse(pr.r_c([None, None]), " ") == ""
    assert pr.paste_collapse(None, "\n\n") == ""


def test_dollar_partial_matching() -> None:
    x = {"84-56": 1, "84-58": 2, "84-140": 3, "looked.question": 4}
    assert pr.dollar(x, "84-56") == 1
    assert pr.dollar(x, "84-5") is None  # ambiguous
    assert pr.dollar(x, "84-14") == 3  # unique prefix
    assert pr.dollar(x, "looked") == 4
    assert pr.dollar(x, "zzz") is None
    assert pr.dollar("not a list", "a") is None


# -- labels and fields ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "field"),
    [
        ("Sample Size", "sample_size"),
        (" Research Questions ", "research_questions"),
        ("Hypotheses", "research_questions"),
        ("analyses2", "additional_analyses"),
        ("My Custom Field: Über-weird!", "my_custom_field_ber_weird"),
        ("Numbers & Flags", "numbers_flags"),
        ("???", "field"),
        ("q2", "q2"),
    ],
)
def test_osf_label_to_field(label: str, field: str) -> None:
    assert pr.osf_label_to_field(label) == field


def test_osf_blocks_labels() -> None:
    blocks = [
        {"block_type": "page-heading", "display_text": "Overview"},
        {"block_type": "short-text-input"},
        {"block_type": "question-label", "display_text": "Hypotheses"},
        {"block_type": "long-text-input"},
        {"block_type": "question-label"},
        {"block_type": "file-input"},
        {"block_type": "paragraph", "display_text": "x"},
        {"display_text": "no type"},
        {"block_type": "contributors-input"},
    ]
    assert pr.osf_blocks_labels(blocks) == {"1": None, "3": "Hypotheses", "5": None, "8": None}
    assert pr.osf_blocks_labels([]) == {}


def test_osf_pages_labels() -> None:
    pages = [
        {
            "questions": [
                {"qid": "q1", "title": "Sample Size"},
                {"qid": "q2", "title": "x" * 41},
                {"qid": "q3", "title": "Why?"},
                {"qid": "q4"},
                {"title": "no qid"},
            ]
        },
        {"id": "no questions"},
    ]
    labels = pr.osf_pages_labels(pages)
    assert labels["q1"] == labels["q1.question"] == labels["q1.uploader"] == "Sample Size"
    assert labels["q2"] == "q2"
    assert labels["q3"] == "q3"
    assert labels["q4"] == "q4"
    assert len(labels) == 12


def test_osf_schema_labels_without_schema() -> None:
    info = {"relationships": {"registration_schema": {"data": {"id": "x"}}}}
    assert pr.osf_schema_labels(info, lambda url: pytest.fail("no request expected")) == {}
    href = {"links": {"related": {"href": "https://api.osf.io/v2/schemas/registrations/x/"}}}
    info = {"relationships": {"registration_schema": href}}
    assert pr.osf_schema_labels(info, lambda url: None) == {}
    body = {"data": {"attributes": {"schema": {"pages": [{"questions": [{"qid": "a"}]}]}}}}
    assert pr.osf_schema_labels(info, lambda url: body)["a"] == "a"


# -- extractors -------------------------------------------------------------------------


def _info(**attrs: object) -> dict[str, object]:
    return {
        "id": "abcde",
        "attributes": {"title": "T", "registration_supplement": "S", **attrs},
        "relationships": {
            "registration_schema": {
                "data": {"id": "s1"},
                "links": {"related": {"href": "https://api.osf.io/v2/schemas/registrations/s1/"}},
            }
        },
    }


def test_common_osf_and_withdrawn() -> None:
    info = _info(withdrawn=True, date_created="2020", embargo_end_date=None)
    flat = pr.flatten_schema(pr.osf_prereg_extract(info, lambda url: pytest.fail("no fetch")))
    assert flat == {
        "template_name": "S",
        "title": "T",
        "id": "abcde",
        "link": "https://osf.io/abcde",
        "date_created": "2020",
        "date_modified": "",
        "date_registered": "",
        "embargo_end_date": "",
        "ia_url": "",
        "description": "WITHDRAWN",
    }


def test_osf_pr_schema_blocks() -> None:
    blocks = [
        {"block_type": "question-label", "display_text": "Title"},
        {"block_type": "short-text-input"},
        {"block_type": "question-label", "display_text": "Hypothesis"},
        {"block_type": "long-text-input"},
        {"block_type": "question-label", "display_text": "Hypotheses"},
        {"block_type": "long-text-input"},
    ]
    body = {"data": {"attributes": {"schema": {"blocks": blocks}}}}
    info = _info(
        registration_responses={
            "9-1": "other title",
            "9-3": " H1 ",
            "9-5": ["H2", "H3"],
            "9-7": "x",
        }
    )
    assert pr.needs_schema(info) == "https://api.osf.io/v2/schemas/registrations/s1/"
    flat = pr.flatten_schema(pr.osf_prereg_extract(info, lambda url: body))
    assert flat["title"] == "T"
    assert flat["research_questions"] == "H1 H2 H3"
    assert list(flat)[-1] == "research_questions"


def test_prsp_dispatch() -> None:
    info = _info(registration_responses={"84-56": "a", "84-23": ["b", "c"]})
    info["relationships"]["registration_schema"]["data"]["id"] = "5730e99a9ad5a102c5745a8a"  # type: ignore[index]
    assert pr.needs_schema(info) is None
    flat = pr.flatten_schema(pr.osf_prereg_extract(info, lambda url: pytest.fail("no fetch")))
    assert flat["research_questions"] == "a"  # `$84-5` partially matches `84-56`
    assert flat["indices"] == "a"
    assert flat["design_independent_variables"] == "b c"
    assert flat["design_covariates_moderators"] == ""
    assert flat["additional_comments"] == ""


def test_ap_schema() -> None:
    table = pd.DataFrame(
        {
            "ap_url": pd.Series(
                [
                    "https://aspredicted.org/blind.php?x=nq4xa3",
                    "https://aspredicted.org/by8i8v.pdf",
                ],
                dtype="string",
            ),
            "AP_title": pd.Series(["One", None], dtype="string"),
            "AP_sample_size": pd.Series(["10", "20"], dtype="string"),
        }
    )
    out = pr.ap_schema(table)
    assert list(out.columns) == ["template_name", "id", "link", "title", "sample_size"]
    assert out["id"].tolist() == ["nq4xa3", "by8i8v"]
    assert pr.frame_schema(out)["title"] == "One\n\nNA"
    assert pr.ap_schema(table.iloc[0:0]).shape == (0, 0)


def test_prereg_schema_template() -> None:
    df = pr.prereg_schema()
    assert df.shape == (1, 69)
    assert list(df.columns[:3]) == ["id", "date_created", "template_name"]
    assert df.isna().all().all()
