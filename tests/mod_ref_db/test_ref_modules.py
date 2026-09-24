"""Tests for the ref_retraction, ref_replication and ref_miscitation modules.

Ports the corresponding blocks of metacheck's
``tests/testthat/test-module-ref.R`` and adds checks of the branches the
parity cases (``parity/cases/mod_ref_db.yaml``) compare with R.
"""

from __future__ import annotations

import warnings

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import module_list, module_run
from pytacheck.report.blocks import ReportTable
from tests.mod_ref_db.helpers import demo_no_dois, demo_no_refs, ref_paper, report_tbl

REF_MODULES = ["ref_retraction", "ref_replication", "ref_miscitation"]


@pytest.mark.parametrize("name", REF_MODULES)
def test_module_listed(name: str) -> None:
    assert name in module_list()["name"].tolist()
    info = pc.module_info(name)
    assert info.keywords == ("reference",)
    assert info.requires == ()
    assert info.section == "reference"


# -- ref_miscitation (test-module-ref.R: "ref_miscitation") -------------------------


def test_ref_miscitation_custom_db(demo: pc.Paper) -> None:
    test_doi = "10.1037/0003-066x.54.6.408"
    db = pd.DataFrame(
        {
            "doi": [test_doi],
            "reftext": ["The full reference (this is a test)"],
            "warning": ["Lorem ipsum this is a test..."],
        }
    )
    out = module_run(demo, "ref_miscitation", db=db)
    assert out.table["doi"].iloc[0] == test_doi
    assert out.summary_table[f"miscite_{test_doi}"].tolist() == [1]
    assert out.traffic_light == "yellow"


def test_ref_miscitation_all_matched_papers_reported(demo: pc.Paper) -> None:
    dois = demo.bib["doi"].dropna().unique().tolist()[:2]
    db = pd.DataFrame(
        {
            "doi": dois,
            "reftext": ["Reference A", "Reference B"],
            "warning": ["Warning A", "Warning B"],
        }
    )
    out = module_run(demo, "ref_miscitation", db=db)
    assert len(out.report) == 2


def test_ref_miscitation_default_db(demo: pc.Paper) -> None:
    out = module_run(demo, "ref_miscitation")
    assert out.traffic_light == "green"
    assert out.report == "We detected no miscited papers"
    assert list(out.table.columns) == [
        "paper_id",
        "bib_id",
        "doi",
        "citation",
        "reftext",
        "warning",
    ]
    assert len(out.table) == 0
    assert list(out.summary_table.columns) == ["paper_id"]


def test_ref_miscitation_instances() -> None:
    paper = ref_paper(
        ["10.1525/collabra.33267", "10.1016/j.jml.2012.11.001", "10.1371/journal.pone.0090779"],
        cites=[0, 0, [0, 1], 0, 0, 0],
    )
    out = module_run(paper, "ref_miscitation")
    assert out.traffic_light == "yellow"
    assert out.summary_text == "We found 8 citations to papers that are commonly miscited."
    first, second, third = out.report
    assert "*5 of 6 Instances:*" in first
    assert first.count("\n> ") == 5
    assert "*1 Instance:*\n\n> Body sentence 3 cites 0 and 1." in second
    # a miscited paper that is never cited in the text: right_join keeps it
    assert third.endswith("*1 Instance:*\n\n> NA")
    assert out.summary_table.columns.tolist() == [
        "paper_id",
        "miscite_10.1525/collabra.33267",
        "miscite_10.1016/j.jml.2012.11.001",
        "miscite_10.1371/journal.pone.0090779",
    ]


def test_ref_miscitation_duplicate_doi_list_columns() -> None:
    paper = ref_paper(["10.1525/collabra.33267"] * 2, cites=[0, 1])
    with pytest.warns(UserWarning, match="not uniquely identified"):
        out = module_run(paper, "ref_miscitation")
    assert out.summary_table["miscite_10.1525/collabra.33267"].tolist() == [[0, 1]]


def test_ref_miscitation_paperlist() -> None:
    papers = pc.PaperList(
        [
            ref_paper(["10.1525/collabra.33267"], id="p2", cites=[0]),
            ref_paper(["10.1000/x"], id="p3"),
            ref_paper(
                ["10.1016/j.jml.2012.11.001", "10.1525/collabra.33267"], id="p1", cites=[[0, 1]]
            ),
        ]
    )
    out = module_run(papers, "ref_miscitation")
    st = out.summary_table
    assert st["paper_id"].tolist() == ["p2", "p3", "p1"]
    assert st["miscite_10.1016/j.jml.2012.11.001"].tolist() == [pd.NA, pd.NA, 0]
    assert st["miscite_10.1525/collabra.33267"].tolist() == [0, pd.NA, 1]
    assert out.table["paper_id"].tolist() == ["p2", "p1", "p1"]


# -- ref_replication (test-module-ref.R: "ref_replication") -------------------------


def test_ref_replication_no_references() -> None:
    out = module_run(demo_no_refs(), "ref_replication")
    assert out.traffic_light == "na"
    assert out.table is None
    assert out.report == "We found no references with DOIs"


def test_ref_replication_no_dois() -> None:
    out = module_run(demo_no_dois(), "ref_replication")
    assert out.traffic_light == "na"
    assert out.table is None


def test_ref_replication_demo(demo: pc.Paper) -> None:
    out = module_run(demo, "ref_replication")
    assert out.traffic_light == "info"
    assert len(out.table) == 1
    assert out.summary_text == "We found 1 replication for 1 original you cited."
    assert out.summary_table["replications"].tolist() == [1]

    out = module_run(demo, "ref_replication", show_outcomes=True)
    assert out.table["replication_outcome"].tolist() == ["failed"]
    assert out.table["replication_type"].tolist() == ["replication"]
    tbl = report_tbl(out)
    assert tbl["table"].columns.tolist() == ["Reference", "Replication", "Outcome", "Type"]
    assert tbl["colwidths"] == [0.3, 0.4, 0.15, 0.15]
    assert (
        tbl["table"]["Replication"]
        .iloc[0]
        .endswith(
            "<a href='https://doi.org/10.1177/1474704919852921' target='_blank'>"
            "doi:10.1177/1474704919852921</a>"
        )
    )


def test_ref_replication_both_types_and_links() -> None:
    paper = ref_paper(["10.1126/science.1239918", "10.1093/qje/qjac034"])
    out = module_run(paper, "ref_replication")
    assert (
        out.summary_text == "We found 6 replications and 1 reproduction for 2 originals you cited."
    )
    col = report_tbl(out)["table"]["Replication/Reproduction"].tolist()
    assert all(c.startswith(("<b>[Replication]</b> ", "<b>[Reproduction]</b> ")) for c in col)
    # a replication without a DOI links to its URL
    url_only = out.table["replication_doi"].isna().to_numpy()
    assert url_only.any()
    for c, url in zip(
        [c for c, u in zip(col, url_only, strict=True) if u],
        out.table.loc[url_only, "replication_url"],
        strict=True,
    ):
        assert f"<a href='{url}' target='_blank'>" in c


def test_ref_replication_reproductions_only() -> None:
    out = module_run(ref_paper(["10.1038/s41562-023-01655-0"]), "ref_replication")
    assert out.summary_text == "We found 1 reproduction for 1 original you cited."
    assert report_tbl(out)["table"].columns.tolist() == ["Reference", "Reproduction"]


def test_ref_replication_trailing_url_removed() -> None:
    out = module_run(ref_paper(["10.1016/j.biopsycho.2018.08.007"]), "ref_replication")
    ref = out.table["replication_ref"].iloc[0]
    assert ref.endswith("Retrieved from")
    assert "https://purl.utwente.nl/essays/100571" in report_tbl(out)["table"].iloc[0, 1]


def test_ref_replication_already_cited() -> None:
    out = module_run(
        ref_paper(["10.1037/0003-066x.54.6.408", "10.1177/1474704919852921"]), "ref_replication"
    )
    assert out.traffic_light == "na"
    assert len(out.table) == 0
    assert out.report == (
        "We checked 2 references with DOIs. "
        "No citations to articles in the FLoRA database were found."
    )
    # R compares with every paper's references, also across a paper list
    papers = pc.PaperList(
        [
            ref_paper(["10.1037/0003-066x.54.6.408"], id="orig"),
            ref_paper(["10.1177/1474704919852921"], id="rep"),
        ]
    )
    out = module_run(papers, "ref_replication")
    assert out.traffic_light == "na"
    assert out.summary_table["replications"].tolist() == [0, 0]


# -- ref_retraction (test-module-ref.R: "ref_retraction") ---------------------------


def test_ref_retraction_no_references() -> None:
    out = module_run(demo_no_refs(), "ref_retraction")
    assert out.traffic_light == "na"
    assert out.table is None


def test_ref_retraction_no_dois() -> None:
    out = module_run(demo_no_dois(), "ref_retraction")
    assert out.traffic_light == "na"
    assert out.table is None


def test_ref_retraction_demo(demo: pc.Paper) -> None:
    out = module_run(demo, "ref_retraction")
    assert out.traffic_light == "info"
    assert len(out.table) == 1
    assert out.table["retractionwatch"].tolist() == ["Retraction"]
    assert out.table.columns.tolist() == ["paper_id", "bib_id", "doi", "text", "retractionwatch"]
    assert out.summary_table["retractionwatch"].tolist() == [1]
    assert out.na_replace == 0
    text, table = out.report
    assert text.startswith("We checked 5 references with DOIs. You cited 1 article in the")
    assert isinstance(table, ReportTable)
    assert table.data.columns.tolist() == ["Reference", "RW Type"]


def test_ref_retraction_paperlist_order() -> None:
    papers = pc.PaperList(
        [
            ref_paper(["10.3390/nano14090769"], id="b"),
            ref_paper(["10.1000/x"], id="c"),
            ref_paper(["10.1371/journal.ppat.1000066", "10.1016/j.ejogrb.2018.03.041"], id="a"),
        ]
    )
    out = module_run(papers, "ref_retraction")
    assert out.summary_text == "You cited 3 articles in the RetractionWatch database."
    assert out.summary_table["paper_id"].tolist() == ["b", "c", "a"]
    assert out.summary_table["retractionwatch"].tolist() == [1, 0, 2]
    assert out.table["paper_id"].tolist() == ["b", "a", "a"]
    assert out.table["retractionwatch"].tolist() == [
        "Correction",
        "Retraction;Expression of concern",
        "Expression of concern",
    ]


def test_ref_retraction_no_hits() -> None:
    out = module_run(ref_paper(["10.1000/not.in.rw", None, ""]), "ref_retraction")
    assert out.traffic_light == "na"
    assert out.report == (
        "We checked 1 references with DOIs. "
        "No citations to articles in the RetractionWatch database were found."
    )


# -- all three -----------------------------------------------------------------------


@pytest.mark.parametrize("name", REF_MODULES)
def test_paperlist_equals_single_papers(name: str, psychsci: pc.PaperList) -> None:
    """Like ref_consistency's iteration test: a paper list gives the rows of its papers."""
    papers = pc.PaperList([*psychsci, pc.demopaper()])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        whole = module_run(papers, name).table
        parts = [module_run(p, name).table for p in papers]
    parts = [p for p in parts if p is not None]
    expected = pd.concat(parts, ignore_index=True) if parts else None
    if whole is None or expected is None:
        assert whole is None and expected is None
        return
    pd.testing.assert_frame_equal(
        whole.reset_index(drop=True), expected, check_dtype=False, check_index_type=False
    )


@pytest.mark.parametrize("name", REF_MODULES)
def test_does_not_mutate_paper(name: str) -> None:
    paper = ref_paper(
        ["10.1177/0956797614520714", "10.1037/0003-066x.54.6.408", "10.1525/collabra.33267"],
        cites=[0, [1, 2]],
    )
    before = paper.copy()
    module_run(paper, name)
    assert paper == before


@pytest.mark.parametrize("name", REF_MODULES)
def test_test_paper(name: str) -> None:
    out = module_run(pc.test_paper(["No references here."]), name)
    if name == "ref_miscitation":
        assert out.traffic_light == "green"
    else:
        assert out.traffic_light == "na"
        assert out.summary_text == "We found no references with DOIs"


# -- tools::toTitleCase() (used for the replication/reproduction labels) -------------

TITLE_CASE = [
    ("replication", "Replication"),
    ("reproduction", "Reproduction"),
    ("a replication of the thing", "a Replication of the Thing"),
    ("the art of war: a history", "The Art of War: a History"),
    ('"quoted words" in text', '"Quoted Words" in Text'),
    ("'single' quotes here", "'single' Quotes Here"),
    ("well-known self-test of the method", "Well-Known Self-Test of the Method"),
    ("HTML and LaTeX in U.S. data", "HTML and LaTeX in U.S. Data"),
    ("using ggplot2 for the analysis", "using ggplot2 for the Analysis"),
    ("mixed CASE words like iPhone and eBay", "Mixed CASE Words Like iPhone and eBay"),
    ("from here to there - and back", "From Here to There - and Back"),
    ("x", "x"),
    ("  leading and trailing  ", "  Leading and Trailing  "),
    ("one/two (three) four", "One/Two (Three) Four"),
    ("title: the subtitle", "Title: The Subtitle"),
    ("title - and more", "Title - and more"),
    ("it is what it is", "it is What it is"),
    (None, None),
    ("", ""),
]


def test_to_title_case_matches_r() -> None:
    """Outputs recorded from R 4.5.3 ``tools::toTitleCase()``."""
    from pytacheck.modules.ref_replication import _to_title_case

    inputs = [i for i, _ in TITLE_CASE]
    assert _to_title_case(inputs) == [o for _, o in TITLE_CASE]


# -- review: db key types, NA-DOI instances, R errors, scaling ------------------------


def test_ref_miscitation_nan_db_is_r_logical_na() -> None:
    # an all-NaN (float64) doi column is R's logical NA column: it joins NA DOIs
    paper = ref_paper([None, "10.1000/x"], cites=[0, 1, 0])
    db = pd.DataFrame({"doi": [float("nan")], "reftext": ["r"], "warning": ["w"]})
    out = module_run(paper, "ref_miscitation", db=db)
    assert out.traffic_light == "yellow"
    assert out.report == ["**NA**\n\nr\n\nw\n\n*2 Instances:*\n\n> NA\n\n> NA"]
    assert out.summary_table.columns.tolist() == ["paper_id", "miscite_NA"]
    assert db["doi"].dtype == "float64"  # the input is not modified


@pytest.mark.parametrize(
    "doi", [pd.Series([], dtype="float64"), pd.Series([1.0]), pd.Series([1], dtype="Int64")]
)
def test_ref_miscitation_incompatible_db_doi(doi: pd.Series) -> None:
    # dplyr: "Can't join `x$doi` with `y$doi` due to incompatible types."
    paper = ref_paper(["10.1000/x"], cites=[0])
    db = pd.DataFrame({"doi": doi, "reftext": ["r"] * len(doi), "warning": ["w"] * len(doi)})
    with pytest.raises(pc.ModuleError, match="incompatible types"):
        module_run(paper, "ref_miscitation", db=db)


def test_ref_miscitation_factor_db() -> None:
    paper = ref_paper(["10.1000/y", "10.1000/x"], cites=[0, 1, 1])
    db = pd.DataFrame(
        {
            "doi": pd.Categorical(["10.1000/x", "10.1000/y"]),
            "reftext": ["a", "b"],
            "warning": ["1", "2"],
        }
    )
    out = module_run(paper, "ref_miscitation", db=db)
    assert out.table["doi"].tolist() == ["10.1000/y", "10.1000/x", "10.1000/x"]


def test_ref_miscitation_na_doi_rows_interleave_instances() -> None:
    # xrefs rows with an NA DOI are NA instances of every DOI (R's NA subsetting),
    # interleaved in row order and counted in "5 of N"
    paper = ref_paper([None, "10.1000/x", "10.1000/a"], cites=[1, 0, 1, 0, 2, 1, 1, 0, 1])
    db = pd.DataFrame(
        {
            "doi": ["10.1000/x", None, "10.1000/a"],
            "reftext": ["RX", "RN", "RA"],
            "warning": ["WX", "WN", "WA"],
        }
    )
    x, na, a = module_run(paper, "ref_miscitation", db=db).report
    assert x.endswith(
        "*5 of 8 Instances:*\n\n> Body sentence 1 cites 1.\n\n> NA\n\n"
        "> Body sentence 3 cites 1.\n\n> NA\n\n> Body sentence 6 cites 1."
    )
    assert na.endswith("*5 of 9 Instances:*\n\n" + "\n\n".join(["> NA"] * 5))
    assert a.endswith("*4 Instances:*\n\n> NA\n\n> NA\n\n> Body sentence 5 cites 2.\n\n> NA")


def _naive_instances(xrefs: pd.DataFrame, warn_doi: str) -> list[object]:
    """``xrefs$citation[xrefs$doi == warn_doi]``, element by element."""
    out: list[object] = []
    for d, c in zip(xrefs["doi"], xrefs["citation"], strict=True):
        if pd.isna(d):
            out.append(None)
        elif d == warn_doi:
            out.append(c)
    return out


def test_ref_miscitation_many_citations_is_fast_and_exact() -> None:
    import random
    import time

    rng = random.Random(1)
    dois = [f"10.1000/m{i}" for i in range(400)]
    cites = [rng.randrange(400) for _ in range(4000)]
    paper = ref_paper(dois, cites=cites)
    db = pd.DataFrame({"doi": dois[::2], "reftext": ["r"] * 200, "warning": ["w"] * 200})
    start = time.perf_counter()
    out = module_run(paper, "ref_miscitation", db=db)
    assert time.perf_counter() - start < 5
    assert len(out.report) == 200
    assert out.summary_table.shape == (1, 201)
    # compare the instance counts with a naive scan of the detailed table
    table = out.table
    for doi, rep in zip(dict.fromkeys(table["doi"]), out.report, strict=True):
        n = len(_naive_instances(table, doi))
        head = min(n, 5)
        label = f"{head} of {n}" if head < n else f"{head}"
        assert f"*{label} Instance" in rep


@pytest.mark.parametrize("name", ["ref_retraction", "ref_replication"])
def test_bib_without_text_id_errors_like_r(name: str, upstream_dir) -> None:
    # R's ref_table() cannot join a bib without text_id to the text table
    paper = pc.read(upstream_dir / "inst" / "demos" / "golden_bibr_10_2.json")
    assert "text_id" not in paper["bib"].columns
    with pytest.raises(pc.ModuleError, match="Join columns in `x` must be present"):
        module_run(paper, name)
