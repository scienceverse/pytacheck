"""Port of the ref_consistency / ref_accuracy tests in tests/testthat/test-module-ref.R."""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.modules import ref_accuracy as ra
from pytacheck.report.blocks import ReportTable
from tests.mod_ref_accuracy.edits import ra_demo, ra_report_tables, ra_set, ra_xrefs

# ---------------------------------------------------------------------------
# ref_consistency


def test_ref_consistency(demo: pc.Paper) -> None:
    module = "ref_consistency"
    assert module in pc.module_list()["name"].tolist()

    out = pc.module_run(demo, module)
    assert out.traffic_light == "red"
    assert len(out.table) == 4
    assert out.module == module
    assert list(out.table.columns) == ["paper_id", "bib_id", "reference", "contents", "text"]
    # U115: n_missing is there without missing citations (metacheck drops the column)
    assert list(out.summary_table.columns) == [
        "paper_id",
        "n_bib",
        "n_xrefs",
        "n_missing",
        "n_extra",
    ]
    assert out.summary_table.loc[0, ["n_bib", "n_xrefs", "n_missing", "n_extra"]].tolist() == [
        5,
        1,
        0,
        4,
    ]


def test_ref_consistency_iteration(psychsci: pc.PaperList) -> None:
    module = "ref_consistency"
    papers = psychsci[[0, 2]]
    out1 = pc.module_run(papers[0], module)
    out2 = pc.module_run(papers[1], module)
    out3 = pc.module_run(papers, module)
    # the same rows (as sets: dplyr::setdiff() in R); a paper list's full join
    # lists every paper's bibliography rows before the unmatched citations
    t12 = pd.concat([out1.table, out2.table], ignore_index=True)
    assert len(t12) == len(out3.table)
    cols = list(t12.columns)
    pd.testing.assert_frame_equal(
        t12.sort_values(cols, ignore_index=True),
        out3.table.sort_values(cols, ignore_index=True),
        check_dtype=False,
    )
    assert out3.summary_table["paper_id"].tolist() == papers.names


def test_ref_consistency_green_and_na() -> None:
    cited = ra_xrefs(
        [0, 1, 3, 4], ["(DeBruine 2025)", "(Eagly and Wood 1999)", "(Lakens 2018)", "(Smith 2021)"]
    )
    out = pc.module_run(cited, "ref_consistency")
    assert out.traffic_light == "green"
    assert len(out.table) == 0
    assert out.summary_text.startswith("All cross-references were in the bibliography")
    # U83: metacheck says "likley"
    assert out.report == [
        "This module relies on Grobid correctly parsing the references. There are likely to "
        "be some false positives.",
        "",
    ]
    # U115: the count columns are there when nothing is flagged
    assert out.summary_table.loc[0, ["n_missing", "n_extra"]].tolist() == [0, 0]

    out = pc.module_run(pc.test_paper("No references."), "ref_consistency")
    assert out.traffic_light == "na"
    assert out.summary_text == "No bibliography entries were detected"
    assert out.summary_table.loc[0, ["n_bib", "n_xrefs"]].tolist() == [0, 0]


def test_ref_consistency_missing_xrefs() -> None:
    # a citation with no bibliography id is "missing"; U115: so is one with an
    # unknown id (99), which metacheck never lists
    paper = ra_xrefs([None, 99], ["(Nobody 2020)", "(Ghost 2021)"])
    out = pc.module_run(paper, "ref_consistency")
    assert out.traffic_light == "red"
    assert out.summary_table.loc[0, ["n_missing", "n_extra"]].tolist() == [2, 4]
    (tbl,) = ra_report_tables(out)
    assert tbl["type"].tolist() == ["extra"] * 4 + ["missing"] * 2
    assert tbl["bib_id"].tolist()[-1] == 99
    assert tbl["reference"].tolist()[-1] == out.table["text"].tolist()[-1]


def test_ref_consistency_duplicate_bib_match_rows() -> None:
    # U114: ref_table() repeats a reference for each bib_match row; metacheck then
    # counts 7 references (n_bib) and lists reference 3 twice for the demo's 5
    out = pc.module_run(ra_demo(dup_match=[2, 3]), "ref_consistency")
    assert out.summary_table.loc[0, ["n_bib", "n_xrefs", "n_missing", "n_extra"]].tolist() == [
        5,
        1,
        0,
        4,
    ]
    assert out.table["bib_id"].tolist() == [0, 1, 3, 4]


def test_ref_consistency_paperlist_fills_zero() -> None:
    papers = pc.PaperList([pc.demopaper(), ra_demo(empty=["bib"], paper_id="nobib")])
    out = pc.module_run(papers, "ref_consistency")
    st = out.summary_table.set_index("paper_id")
    # U115: its citation points at a reference that is not in the (empty) bibliography
    assert st.loc["nobib", ["n_bib", "n_xrefs", "n_missing", "n_extra"]].tolist() == [0, 1, 1, 0]


# ---------------------------------------------------------------------------
# ref_accuracy


def test_ref_accuracy_listed_and_offline() -> None:
    assert "ref_accuracy" in pc.module_list()["name"].tolist()
    info = pc.module_info("ref_accuracy")
    assert info.section == "reference"
    assert info.requires == ()  # uses the stored bib_match table: no network
    assert info.arg_defaults == {
        "paper": None,
        "max_authors": 6,
        "title_similarity": 0.7,
        "min_mismatches": 1,
        "year_tolerance": 1,
        "suggest_score": 70,
    }


def test_ref_accuracy_no_references() -> None:
    paper = ra_demo(empty=["bib"], drop=["bib_match"])
    out = pc.module_run(paper, "ref_accuracy")
    assert out.traffic_light == "na"
    assert out.table is None
    assert out.summary_text == "We found no references"


def test_ref_accuracy_no_bib_match() -> None:
    paper = ra_demo(drop=["bib_match"])
    out = pc.module_run(paper, "ref_accuracy")
    # U30: metacheck returns "error", which is not one of its traffic lights
    assert out.traffic_light == "fail"
    assert out.table is None
    assert "add_bib_match" in out.summary_text


def test_ref_accuracy_demo(demo: pc.Paper) -> None:
    out = pc.module_run(demo, "ref_accuracy")
    assert len(out.table) == len(demo["bib"])

    # references that supplied a DOI are checked; the demo paper includes two
    # references with a DOI that does not resolve, which are flagged as incoherent
    st = out.summary_table
    assert st["refs_checked"].tolist() == [4]
    assert st["no_doi"].tolist() == [0]
    assert st["incoherent"].tolist() == [2]
    assert out.traffic_light == "yellow"

    # those two are the "unresolved" tier: a cited DOI CrossRef could not find
    unresolved = out.table[out.table["tier"] == "unresolved"]
    assert len(unresolved) == 2
    assert unresolved["incoherent"].all()
    # an unresolved DOI has no record: its DOI comparison is NA (as in R)
    assert unresolved["doi_mismatch"].isna().all()

    # U83: the guidance without metacheck's typos ("Such an incoherent", "ar an AI")
    prose = " ".join(b for b in out.report if isinstance(b, str))
    assert "Such an incoherence is most often an error in reading the reference" in prose
    assert "it could be a mistake, or an AI generated reference." in prose
    assert "incoherent is most" not in prose and " ar an " not in prose


def _checked_id(demo: pc.Paper) -> int:
    out = pc.module_run(demo, "ref_accuracy")
    return int(out.table["bib_id"][out.table["tier"] == "provided"].iloc[0])


def test_ref_accuracy_journal_mismatch(demo: pc.Paper) -> None:
    # a checked reference whose cited journal differs from its DOI's record is incoherent
    checked_id = _checked_id(demo)
    bad = ra_set(
        pc.demopaper(), "bib", checked_id, "container", "A Completely Different Journal Name"
    )
    out = pc.module_run(bad, "ref_accuracy")
    assert out.traffic_light == "yellow"
    assert out.summary_table["incoherent"].tolist() == [3]


def test_ref_accuracy_min_mismatches(demo: pc.Paper) -> None:
    # a lone author mismatch flags at the default (1) but not when set to 2
    checked_id = _checked_id(demo)
    auth = ra_set(pc.demopaper(), "bib", checked_id, "authors", "Nonexistent, A; Madeup, B")
    loose = pc.module_run(auth, "ref_accuracy")
    strict = pc.module_run(auth, "ref_accuracy", min_mismatches=2)
    assert loose.summary_table["incoherent"].tolist() == [3]
    assert strict.summary_table["incoherent"].tolist() == [2]


def test_ref_accuracy_green_lists_references_without_doi() -> None:
    paper = ra_demo([("bib", 0, "doi", ""), ("bib", 4, "doi", ""), ("bib_match", 3, "title", "")])
    out = pc.module_run(paper, "ref_accuracy")
    assert out.traffic_light == "green"
    assert out.summary_table["no_doi"].tolist() == [3]
    assert out.report[0] == ""
    assert out.report[1] == "### References without a DOI\n"
    assert "Adding a DOI to every reference" in out.summary_text
    (tbl,) = ra_report_tables(out)
    assert tbl["Suggested DOI"].tolist() == ["", "", ""]

    # the CrossRef match of bib 3 scored 61.8: suggested only when suggest_score allows
    out = pc.module_run(paper, "ref_accuracy", suggest_score=60)
    (tbl,) = ra_report_tables(out)
    assert tbl["Suggested DOI"].tolist()[1] == (
        "<a href='https://doi.org/10.1177/2515245918770963' target='_blank'>"
        "10.1177/2515245918770963</a>"
    )


def test_ref_accuracy_report_strikes_cited_values() -> None:
    paper = ra_demo(
        [
            ("bib", 2, "title", "Zzz"),
            ("bib", 2, "authors", "Nobody, X"),
            ("bib", 2, "container", "Journal of Nothing"),
            ("bib", 2, "year", 1990),
            ("bib_match", 2, "doi", "10.9999/other"),
        ]
    )
    out = pc.module_run(paper, "ref_accuracy")
    row = out.table[out.table["bib_id"] == 2].iloc[0]
    for flag in ("title", "author", "container", "year", "doi"):
        assert row[f"{flag}_mismatch"]
    (tbl,) = ra_report_tables(out)
    assert list(tbl.columns) == ["Reference", "What is wrong (cited → record)", "Record"]
    wrong = tbl["What is wrong (cited → record)"].tolist()[1]
    labels = [part.split(":</b>")[0] for part in wrong.split("<br>")]
    assert labels == ["<b>title", "<b>authors", "<b>journal", "<b>year", "<b>DOI"]
    assert "<b>year:</b> " + ra._STRIKE + "1990</span> &rarr; 2014" in wrong
    assert tbl["Record"].tolist()[1] == (
        "<a href='https://doi.org/10.9999/other' target='_blank'>10.9999/other</a>"
    )
    blocks = out.report
    assert isinstance(blocks, list) and isinstance(blocks[3], ReportTable)
    assert blocks[3].maxrows == 5 and blocks[3].colwidths == [0.45, 0.4, 0.15]


def test_ref_accuracy_paperlist(psychsci: pc.PaperList) -> None:
    papers = pc.PaperList([*psychsci, pc.demopaper()])
    out = pc.module_run(papers, "ref_accuracy")
    assert out.summary_table["paper_id"].tolist() == papers.names
    assert out.summary_table["incoherent"].tolist() == [0, 0, 0, 2]
    # the same rows as running each paper on its own
    single = [pc.module_run(p, "ref_accuracy").table for p in papers]
    pd.testing.assert_frame_equal(
        pd.concat(single, ignore_index=True), out.table, check_dtype=False
    )


def test_ref_accuracy_does_not_mutate(demo: pc.Paper) -> None:
    before = demo.copy()
    pc.module_run(demo, "ref_accuracy")
    pc.module_run(demo, "ref_consistency")
    assert demo == before


# ---------------------------------------------------------------------------
# helpers (the R closures inside the module)


def test_clean_and_deaccent() -> None:
    assert ra._clean(["<i>Gredebäck</i>, Æsop – “Quoted”.", None, "ΣΟΦΙΑ İnce"]) == [
        "gredeback, aesop 'quoted'",
        None,
        "σοφια ince",
    ]


def test_clean_folds_dashes_to_spaces() -> None:
    # U114: metacheck's clean() removes dashes ("retractedevil"), although its
    # comments say they fold to spaces
    assert ra._clean(["Retracted\u2014Evil", "Self-control: a well–studied topic"]) == [
        "retracted evil",
        "self control: a well studied topic",
    ]


def test_norm_title_drops_superscripts() -> None:
    # U114: metacheck's norm_title() looks for <sup> after clean() has removed the
    # tags, so footnote markers stayed in titles ("effectsof1sleep")
    titles = ["Effects of<sup>1</sup> sleep", "Effects of sleep", None]
    assert ra._norm_title(ra._clean(ra._drop_sup(titles))) == [
        "effectsofsleep",
        "effectsofsleep",
        None,
    ]


def test_ref_accuracy_duplicate_bib_match_rows() -> None:
    # U114: two bib_match rows for references 2 and 3; metacheck's three left joins
    # give 2 x 2 x 2 rows for each
    out = pc.module_run(
        ra_demo([("bib", 2, "container", "Psychological Review")], dup_match=[2, 3]),
        "ref_accuracy",
    )
    single = pc.module_run(
        ra_demo([("bib", 2, "container", "Psychological Review")]), "ref_accuracy"
    )
    assert out.table["bib_id"].tolist() == single.table["bib_id"].tolist()
    assert out.summary_table.to_dict("list") == single.summary_table.to_dict("list")


@pytest.mark.parametrize(
    ("a", "b", "coherent"),
    [
        ("J. Pers. Soc. Psychol.", "Journal of Personality and Social Psychology", True),
        ("Am. Psychol.", "American Psychologist", True),
        ("Psychological Review", "Psychological Science", False),
        ("!!!", "Psychological Science", True),
        ("Psychologist American", "American Psychologist", False),
    ],
)
def test_journal_coherent(a: str, b: str, coherent: bool) -> None:
    ta, tb = ra._journal_tokens([a, b])
    assert ra._journal_coherent(ta, tb) is coherent


def test_adist_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    pairs = [("kitten", "sitting"), ("", "abc"), ("abc", "abc"), ("flaw", "lawn")]
    fast = [ra._adist(a, b) for a, b in pairs]
    ra._levenshtein.cache_clear()
    monkeypatch.setattr(ra, "_levenshtein", lambda: None)
    assert [ra._adist(a, b) for a, b in pairs] == fast == [3, 3, 0, 2]


def test_last_names_cells() -> None:
    frame = pd.DataFrame({"given": ["A", "B"], "family": ["Eagly", None]})
    assert ra._last_names(frame) == ["Eagly", None]
    assert ra._last_names([{"given": "A"}]) == []
    assert ra._last_names("Eagly, A; Wood, W") == ["Eagly", "Wood"]
    assert ra._last_names("; Wood") == [None]  # an error in R, caught as NA
    assert ra._last_names("") == []
    assert ra._last_names(None) == [None]


def test_as_numeric() -> None:
    assert [ra._as_numeric(x) for x in [2014, " 2014 ", "2014a", None, "0x10", 1.5]] == [
        2014.0,
        2014.0,
        None,
        None,
        16.0,
        1.5,
    ]


def test_as_numeric_follows_r_string2real() -> None:
    # R's as.numeric(): C isspace() padding, ASCII digits only, C99 hex floats
    values = ["１９９０", "1990 ", " 2014", "٢٠١٤", "\f2018\v", "0x1p3", "0x1.f8p10"]
    assert [ra._as_numeric(x) for x in values] == [None, None, None, None, 2018.0, 8.0, 2016.0]
    assert [ra._as_numeric(x) for x in ["-0x10", "+0X7de", "1_000", "0x", "Inf", "NaN"]] == [
        -16.0,
        2014.0,
        None,
        None,
        float("inf"),
        None,
    ]


def test_incompatible_join_keys_error_like_dplyr() -> None:
    from pytacheck.module import ModuleError
    from tests.mod_ref_accuracy.edits import ra_setcol

    # a character xref_id cannot be joined with the integer bib_id (dplyr refuses)
    p = ra_setcol(ra_demo(), "xref", "xref_id", ["2", "0", "0", None, "1"], "string")
    with pytest.raises(ModuleError, match="Can't join `x\\$bib_id` with `y\\$bib_id`"):
        pc.module_run(p, "ref_consistency")
    # so is a character text_id, even though every joined row has an NA text_id
    p = ra_setcol(ra_demo(), "xref", "text_id", ["4", "8", "17", "25", "25"], "string")
    with pytest.raises(ModuleError, match="Can't join `x\\$text_id` with `y\\$text_id`"):
        pc.module_run(p, "ref_consistency")
    # integer and double keys join
    p = ra_setcol(ra_demo(), "xref", "xref_id", [2.0, 0.0, 0.0, None, 1.0], "float64")
    assert pc.module_run(p, "ref_consistency").traffic_light == "red"


def test_key_kind() -> None:
    from pytacheck.modules.ref_consistency import _key_kind

    assert _key_kind(pd.Series([1, 2], dtype="Int64")) == "num"
    assert _key_kind(pd.Series([], dtype="string")) == "chr"  # vctrs checks empty keys too
    assert _key_kind(pd.Series([None, None], dtype="string")) == "chr"
    assert _key_kind(pd.Series([float("nan")])) == "unspecified"  # R's logical NA
    assert _key_kind(pd.Series([None, "b0"], dtype=object)) == "chr"
    assert _key_kind(pd.Series([None, 3], dtype=object)) == "num"
    assert _key_kind(pd.Series([True, None], dtype="boolean")) == "lgl"


def test_param_help_keeps_roxygen_line_breaks() -> None:
    # module_info() keeps the line breaks of a multi-line @param, as R does
    params = pc.module_info("ref_accuracy").params
    assert params["max_authors"].startswith(
        "how many of the leading authors to compare against the\nretrieved record."
    )
    assert "\n" not in params["paper"]


def test_help_text_without_metacheck_typos() -> None:
    # U83: metacheck's details say "..., of mistakes in citations. You will need check ..."
    details = pc.module_info("ref_accuracy").details
    assert "or mistakes in citations. You will need to check the original source." in details
    assert "will need check" not in details
