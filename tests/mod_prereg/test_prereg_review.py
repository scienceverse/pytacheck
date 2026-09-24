"""Review checks of prereg_check branches found by the adversarial review.

The same scenarios are parity cases (parity/cases/mod_prereg_review.yaml);
these assert the R behaviour directly, on the synthetic recordings of
tests/mod_prereg/make_review_mocks.py and metacheck's recordings.
"""

from __future__ import annotations

import pytest

from tests.mod_prereg.parity_support import run_prereg, run_prereg_tables


def test_view_only_link_keeps_its_token_and_does_not_join() -> None:
    url = "https://osf.io/vwonl/?view_only=abc123"
    mo = run_prereg(papers=[{"url": [url], "id": "p_vwonl"}], mock="local")
    assert mo.table["id"].tolist() == ["vwonl"]
    assert mo.table["link"].tolist() == ["https://osf.io/vwonl"]
    # the link in the paper has the token, so the row has no paper_id (R's join)
    assert mo.table["paper_id"].isna().all()
    assert mo.summary_table["preregistration"].tolist() == [0]
    assert mo.summary_text == "We found 1 preregistration."


def test_registration_without_data_errors_as_in_r() -> None:
    # no row and no osf_error: R falls through to dplyr::count(), which fails
    with pytest.raises(Exception, match="paper_id"):
        run_prereg(papers=[{"url": ["https://osf.io/emptd"], "id": "p"}], mock="local")


def test_registration_without_data_next_to_a_readable_one() -> None:
    urls = ["https://osf.io/emptd", "https://osf.io/blkss"]
    mo = run_prereg(papers=[{"url": urls, "id": "p"}], mock="local")
    assert mo.table["id"].tolist() == ["blkss"]
    assert mo.summary_text == "We found 1 preregistration."


def test_field_slugged_to_paper_id_errors_as_in_r() -> None:
    # left_join() makes paper_id.x / paper_id.y, so count(paper_id) fails
    with pytest.raises(Exception, match="paper_id"):
        run_prereg(papers=[{"url": ["https://osf.io/pidsl"], "id": "p"}], mock="local")


def test_label_case_mapping() -> None:
    mo = run_prereg(papers=[{"url": ["https://osf.io/unicd"], "id": "p"}], mock="local")
    assert "istanbul_study" in mo.table.columns
    assert mo.table["sample_size"].tolist() == ["answer 4 answer 5"]


def test_same_aspredicted_link_in_two_papers_joins_to_both() -> None:
    ap = "https://aspredicted.org/by8i8v.pdf"
    mo = run_prereg(
        papers=[
            {"url": [ap, "https://osf.io/48ncu"], "id": "paper_a"},
            {"url": [ap], "id": "paper_b"},
        ]
    )
    assert mo.table["paper_id"].tolist() == ["paper_a", "paper_a", "paper_b"]
    assert mo.summary_table["preregistration"].tolist() == [2, 1]
    assert mo.summary_text == "We found 3 preregistrations."


def test_inaccessible_link_twice_is_named_twice() -> None:
    papers = [{"url": ["https://osf.io/3cz2e"], "id": "p", "text": ["See osf.io/3cz2e."]}]
    mo = run_prereg(papers=papers)
    assert mo.traffic_light == "na"
    assert "2 registration links could not be accessed" in mo.summary_text


def test_report_tables() -> None:
    tables = run_prereg_tables(demo=True)
    assert [list(t.columns) for t in tables] == [
        ["id", "title", "template"],
        ["id", "sample_size"],
        ["Field", "Preregistration 1", "Preregistration 2"],
    ]
    assert tables[0]["id"].str.startswith("<a href='https://").all()
    # line breaks in cells are shown as <br>
    assert not tables[2].iloc[:, 1:].apply(lambda s: s.str.contains("\n").any()).any()


# --- second review pass ---------------------------------------------------------------


def test_report_keeps_the_empty_block_of_a_missing_sample_size_table() -> None:
    # c(..., scroll_table(NULL), ...) keeps scroll_table()'s "" (NULLs vanish)
    mo = run_prereg(papers=[{"url": ["https://osf.io/5xysn"], "id": "p_oer"}])
    assert "sample_size" not in mo.table.columns
    assert mo.report[2].startswith("Meta-scientific research")
    assert mo.report[3] == ""  # where the sample size table would be


def test_no_paper_ids_errors_as_r_data_frame() -> None:
    import pytacheck as pc
    from pytacheck.modules.prereg_check import _no_prereg_summary

    with pytest.raises(ValueError, match="differing number of rows: 0, 1"):
        _no_prereg_summary(pc.PaperList([]))
    with pytest.raises(Exception):  # noqa: B017 (R errors too; the message differs)
        run_prereg(paperlist=True)


def test_deparse_str_uses_r_escapes() -> None:
    from pytacheck.modules._prereg import _deparse_str

    # expected values from R 4.5 deparse() in a UTF-8 locale
    assert _deparse_str("a\ab\bc\fd\ve\x01f\x7fg") == r'"a\ab\bc\fd\ve\001f\177g"'
    assert _deparse_str("h\x85i\u2028j\u2029k") == r'"h\u0085i\u2028j\u2029k"'
    assert _deparse_str("\u0378\ufffe\U000e01f0") == r'"\u0378\ufffe\U{0e01f0}"'
    # format, private-use, emoji and Unicode 15.1 characters are written as is
    kept = "\u200b\u00ad\ue000\U0001f600\u2ffc\U0002ebf0\u00a0"
    assert _deparse_str(kept) == f'"{kept}"'
    assert _deparse_str('q"b\\s\n\t\r') == r'"q\"b\\s\n\t\r"'


def test_non_string_schema_labels_are_coerced_like_r() -> None:
    from pytacheck.modules._prereg import osf_blocks_labels, osf_pages_labels

    blocks = []
    for text in (2024, True, 1.5, 100000.0, 3000000000, None, False):
        blocks += [
            {"block_type": "question-label", "display_text": text},
            {"block_type": "short-text-input"},
        ]
    assert osf_blocks_labels(blocks) == {
        "1": "2024",
        "3": "TRUE",
        "5": "1.5",
        "7": "1e+05",
        "9": "3e+09",
        "11": None,
        "13": "FALSE",
    }
    pages = [{"questions": [{"qid": "a", "title": 5}, {"qid": "b", "title": 1e49}]}]
    labels = osf_pages_labels(pages)
    assert labels["a"] == labels["a.uploader"] == "5"
    assert labels["b"] == "1e+49"


def test_scalar_labels_end_up_as_slugged_fields() -> None:
    mo = run_prereg(papers=[{"url": ["https://osf.io/lblsc"], "id": "p"}], mock="local")
    assert {"2024", "true", "1_5", "1e_05", "3e_09", "false", "sample_size"} <= set(
        mo.table.columns
    )


def test_label_with_ffff_errors_as_r_tolower() -> None:
    from pytacheck.modules._prereg import osf_label_to_field

    # R's utf8towcs() rejects U+FFFE/U+FFFF; other noncharacters are lowered as is
    with pytest.raises(ValueError, match="in 'utf8towcs'"):
        osf_label_to_field("Sample " + chr(0xFFFF))
    assert osf_label_to_field("Notes " + chr(0x1FFFF)) == "notes"
    assert osf_label_to_field("Ab" + chr(0xFDD0) + "C") == "ab_c"
    with pytest.raises(Exception, match="utf8towcs"):
        run_prereg(papers=[{"url": ["https://osf.io/lblnc"], "id": "p"}], mock="local")
