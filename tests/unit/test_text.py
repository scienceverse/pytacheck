from copy import deepcopy
from typing import Any

from pytacheck.text import (
    extract_apa_tests,
    extract_equations,
    extract_p_values,
    search_rows,
)


def _row(text: str, **extra: Any) -> dict[str, Any]:
    return {
        "text": text,
        "paper_id": "paper",
        "text_id": 7,
        "paragraph_id": 3,
        "section_id": 2,
        "header": "Results",
        "section_type": "results",
        **extra,
    }


def test_search_rows_returns_copied_full_rows_case_insensitively() -> None:
    rows = (_row("Alpha beta", metadata={"tags": ["source"]}),)

    result = search_rows(rows, "alpha")

    assert result == rows
    assert result is not rows
    assert result[0] is not rows[0]
    result[0]["metadata"]["tags"].append("result")
    assert rows[0]["metadata"] == {"tags": ["source"]}


def test_search_rows_match_mode_returns_each_match_without_aliasing() -> None:
    rows = (_row("Alpha and alpha", metadata={"tags": []}),)

    result = search_rows(rows, r"alpha", return_matches=True)

    assert [match["text"] for match in result] == ["Alpha", "alpha"]
    assert all(match["text_id"] == 7 for match in result)
    result[0]["metadata"]["tags"].append("first")
    assert result[1]["metadata"] == {"tags": []}
    assert rows[0]["metadata"] == {"tags": []}


def test_search_rows_excludes_references_and_can_be_case_sensitive() -> None:
    rows = (
        _row("Needle", section_type="references"),
        _row("Needle", text_id=8),
        _row("needle", text_id=9),
    )

    result = search_rows(rows, "Needle", ignore_case=False)

    assert [match["text_id"] for match in result] == [8]


def test_extract_p_values_preserves_token_expansion_and_location() -> None:
    tokens = [
        "p=.05",
        "p < .05",
        "p <= .05",
        "p ≥ .05",
        "p ≠ .05",
        "p-value = 0.05",
        "pvalue = .05",
        "p = n.s.",
        "p = ns",
        "p = 5.0x10^-2",
        "p = 5.0 e -2",
    ]
    sentence = "; ".join(tokens)

    result = extract_p_values((_row(sentence, page_number=4),))

    assert [match["text"] for match in result] == tokens
    assert [match["p_comp"] for match in result] == [
        "=",
        "<",
        "<=",
        "≥",
        "≠",
        "=",
        "=",
        "=",
        "=",
        "=",
        "=",
    ]
    assert [match["p_value"] for match in result] == [
        0.05,
        0.05,
        0.05,
        0.05,
        0.05,
        0.05,
        0.05,
        None,
        None,
        0.05,
        0.05,
    ]
    assert all(match["expanded"] == sentence for match in result)
    assert all(match["page_number"] == 4 for match in result)


def test_extract_p_values_rejects_non_upstream_forms() -> None:
    text = "up = 0.05; p = stuff; p = -0.05; p less than 0.05; p = 12.05; P = .05"

    assert extract_p_values((_row(text),)) == ()


def test_extract_equations_groups_matches_by_sentence() -> None:
    rows = (
        _row("t(10) = 2.23, p = 0.005.", text_id=1),
        _row("F(1, 23) = 9.23, p = .023; Cohen's d = .63.", text_id=2),
    )

    result = extract_equations(rows)

    assert [(row["lhs"], row["df"], row["comp"], row["rhs"]) for row in result] == [
        ("t", "(10)", "=", "2.23"),
        ("p", None, "=", "0.005"),
        ("F", "(1, 23)", "=", "9.23"),
        ("p", None, "=", ".023"),
        ("Cohen's d", None, "=", ".63"),
    ]
    assert [row["grp_id"] for row in result] == [1, 1, 2, 2, 2]
    assert all(row["expanded"] == rows[row["grp_id"] - 1]["text"] for row in result)


def test_extract_equations_handles_complex_effect_size_names() -> None:
    text = "Mantel-Cox χ2 (1, N = 60) = 6.99, partial eta squared = .095, 95% CI = [2, 4]."

    result = extract_equations((_row(text),))

    assert [(row["lhs"], row["df"], row["rhs"]) for row in result] == [
        ("χ2", "(1, N = 60)", "6.99"),
        ("partial eta squared", None, ".095"),
        ("95% CI", None, "[2, 4]"),
    ]


def test_extract_equations_covers_upstream_effect_size_lhs_in_source_order() -> None:
    text = (
        "d = .34; dz = .56; d_rm = .79; Hedges's g = .32; Cohen's f = .37; "
        "η² = .15; ηp² = .10; omega_p2 = .00; ωp² = .00; ξ = .10; β = .20; "
        "b = .30; r = .40."
    )

    result = extract_equations((_row(text),))

    assert [row["lhs"] for row in result] == [
        "d",
        "dz",
        "d_rm",
        "Hedges's g",
        "Cohen's f",
        "η²",
        "ηp²",
        "omega_p2",
        "ωp²",
        "ξ",
        "β",
        "b",
        "r",
    ]
    assert all(row["expanded"] == text for row in result)
    assert all(row["grp_id"] == 1 for row in result)


def test_extract_equations_covers_canonical_one_token_prefix_superscript_and_ns_forms() -> None:
    text = (
        "generalized_eta_squared = .095; f² = .095; Cohen's h = .63; "
        "d = n.s.; partial.eta.squared = .095."
    )

    result = extract_equations((_row(text),))

    assert [(row["lhs"], row["rhs"]) for row in result] == [
        ("generalized_eta_squared", ".095"),
        ("f²", ".095"),
        ("Cohen's h", ".63"),
        ("d", "n.s."),
        ("partial.eta.squared", ".095"),
    ]
    assert all(row["expanded"] == text for row in result)


def test_extract_equations_preserves_original_test_label_case_for_module_labeling() -> None:
    text = "T(20) = 2.00; f(1, 20) = 4.00; d = .50."

    result = extract_equations((_row(text),))

    assert [(row["lhs"], row["df"]) for row in result] == [
        ("T", "(20)"),
        ("f", "(1, 20)"),
        ("d", None),
    ]


def test_extract_equations_discards_single_numeric_lhs_after_broadening() -> None:
    result = extract_equations((_row("2 = 3; d = .50."),))

    assert [(row["lhs"], row["rhs"]) for row in result] == [("d", ".50")]


def test_extract_apa_tests_exposes_normalized_t_and_f_fields() -> None:
    rows = (
        _row("t(97.2) = -1.96, p = 0.152", text_id=1),
        _row("F(1, 38) = 4.00, p < .050", text_id=2),
    )

    result = extract_apa_tests(rows)

    assert result[0]["raw"] == "t(97.2) = -1.96, p = 0.152"
    assert result[0]["test_type"] == "t"
    assert result[0]["statistic"] == -1.96
    assert result[0]["df"] == [97.2]
    assert result[0]["df1"] == 97.2
    assert result[0]["df2"] is None
    assert result[0]["p_comp"] == "="
    assert result[0]["p_value"] == 0.152
    assert result[1]["test_type"] == "F"
    assert result[1]["df"] == [1, 38]
    assert result[1]["df1"] == 1
    assert result[1]["df2"] == 38
    assert result[1]["p_comp"] == "<"
    assert result[1]["p_value"] == 0.05


def test_extractors_do_not_mutate_or_alias_source_rows() -> None:
    source = (_row("t(18) = 2.10, p = .050", metadata={"values": [1]}),)
    original = deepcopy(source)

    extracted = extract_p_values(source)
    extracted[0]["metadata"]["values"].append(2)

    assert source == original
