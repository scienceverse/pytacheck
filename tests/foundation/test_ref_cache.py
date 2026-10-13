"""Reference joins are reused only while a runner freezes its paper."""

import copy

import pandas as pd
import pytest

import metacheck as pc
from metacheck.core.errors import StaleDocumentError
from metacheck.core.scope import counting, trusted_scope
from metacheck.papers.tables import ref_table


def test_reference_cache_reuses_joins_and_protects_results(demo):
    with trusted_scope(), counting() as counts:
        first = ref_table(demo)
        expected = first.copy()
        first.loc[0, "doi"] = "edited result"
        pd.testing.assert_frame_equal(ref_table(demo), expected)
        assert counts["ref_table_builds"] == 1
        demo.bib_match = demo.bib_match.assign(doi="changed DOI")
        assert "changed DOI" in ref_table(demo)["doi"].tolist()
        assert counts["ref_table_builds"] == 2


def test_reference_cache_never_hides_edits_outside_a_run(demo):
    with trusted_scope():
        ref_table(demo)
    demo.bib_match.loc[:, "doi"] = "changed DOI"
    assert "changed DOI" in ref_table(demo)["doi"].tolist()


def test_reference_cache_expires_between_runs(demo):
    with trusted_scope(), counting() as counts:
        ref_table(demo)
    with trusted_scope(), counting() as next_counts:
        ref_table(demo)
    assert counts["ref_table_builds"] == next_counts["ref_table_builds"] == 1


def test_reference_cache_on_copied_papers(demo):
    with trusted_scope():
        expected = ref_table(demo)
        pd.testing.assert_frame_equal(ref_table(copy.copy(demo)), expected)


def test_reference_cache_checks_in_place_mutation(demo, monkeypatch):
    monkeypatch.setenv("METACHECK_CHECK_MUTATION", "1")
    _ = demo.bib_match  # build the frame before the scope watches it
    with pytest.raises(StaleDocumentError), trusted_scope():
        ref_table(demo)
        demo.bib_match.loc[:, "doi"] = "changed DOI"
        ref_table(demo)


def test_reference_cache_keeps_paper_list_order(demo):
    other = copy.deepcopy(demo)
    other.paper_id = "other"
    with trusted_scope(), counting() as counts:
        ref_table(other)
        ref_table(demo)
        together = ref_table(pc.PaperList([demo, other]))
        pd.testing.assert_frame_equal(ref_table(pc.PaperList([demo, other])), together)
        assert counts["ref_table_builds"] == 3
    # Matched bibliographies sort by (paper_id, bib_id), as before caching.
    assert together["paper_id"].drop_duplicates().tolist() == ["other", demo.paper_id]
