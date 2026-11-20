"""Changes of metacheck 0.4.4.19 to 0.4.4.27 in the text readers and the report helpers.

Ports blocks of ``test-text-extractors.R``, ``test-module-stat.R``, ``test-module-ref.R`` and
``test-report-helpers.R``.
"""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc
from metacheck.db.replications import FLoRA
from metacheck.module import module_run
from metacheck.report.blocks import _report_file_tabs
from metacheck.text import extract as ex
from tests.mod_ref_db.helpers import ref_paper

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def test_a_p_with_the_sides_of_its_test_is_read() -> None:
    """#695: "p one-sided = .014" gave no row, and "one-sided" was taken for a statistic."""
    txt = [
        "the effect was significant, p one-sided = .014.",
        "t(20) = 2.1, p (two-tailed) = .048",
        "t(20) = 2.1, p (one-sided) = .024",
        "t(20) = 2.1, p one-tailed < .05",
        "t(20) = 2.1, p 2-tailed = .03",
    ]
    pv = ex.extract_p_values(pc.test_paper(txt))
    assert pv["text_id"].tolist() == [1, 2, 3, 4, 5]
    assert pv["p_value"].tolist() == [0.014, 0.048, 0.024, 0.05, 0.03]
    eq = ex.extract_eq(pd.DataFrame({"text": [txt[0]], "text_id": [1]}))
    assert eq["lhs"].tolist() == ["p one-sided"]


def test_stat_check_reads_a_test_as_one_sided_when_its_sentence_says_so() -> None:
    # p = .041 is half the two-sided p of t(28) = 1.80
    mo = module_run(
        pc.test_paper("The effect was positive, t(28) = 1.80, p = .041 (one-tailed)."),
        "stat_check",
    )
    assert mo["traffic_light"] == "green"
    assert not mo["table"]["error"].any()
    mo = module_run(pc.test_paper("The effect was positive, t(28) = 1.80, p = .041."), "stat_check")
    assert mo["traffic_light"] == "red"
    assert mo["table"]["error"].all()


def test_ref_replication_does_not_suggest_the_citing_paper_itself() -> None:
    fl = FLoRA()
    ok = fl["doi_r"].notna() & (fl["doi_r"] != "") & fl["doi_o"].notna() & (fl["doi_o"] != "")
    row = fl[ok].iloc[0]
    n_rep = int((fl["doi_o"].str.lower() == row["doi_o"].lower()).sum())
    pa = ref_paper([row["doi_o"]], id="A", cites=[0])
    pa.info = pd.DataFrame({"paper_id": ["A"], "doi": ["https://doi.org/" + row["doi_r"].upper()]})
    pb = ref_paper([row["doi_o"]], id="B", cites=[0])
    mo = module_run(pc.PaperList([pa, pb]), "ref_replication")
    tab = mo["table"]
    reps_a = tab.loc[tab["paper_id"] == "A", "replication_doi"].str.lower().tolist()
    reps_b = tab.loc[tab["paper_id"] == "B", "replication_doi"].str.lower().tolist()
    assert row["doi_r"].lower() not in reps_a
    assert len(reps_a) == n_rep - 1
    # another paper citing the same original is still told about it
    assert row["doi_r"].lower() in reps_b


def test_report_file_tabs_keeps_a_reports_per_file_tables_to_a_limit() -> None:
    from metacheck.utils import options

    few, note = _report_file_tabs(["a.csv", "b.csv"])
    assert few == ["a.csv", "b.csv"] and note is None
    many, note = _report_file_tabs([f"f{i:04d}.csv" for i in range(1, 1191)])
    assert len(many) == 20 and many[:2] == ["f0001.csv", "f0002.csv"]
    assert note == "*Showing the first 20 of 1190 files.*"
    exactly, note = _report_file_tabs([f"f{i:02d}.csv" for i in range(1, 21)])
    assert len(exactly) == 20 and note is None
    try:
        options({"metacheck.report_max_file_tabs": 3})
        shown, note = _report_file_tabs(list("abcdefghijklmnopqrstuvwxyz"))
        assert len(shown) == 3 and note == "*Showing the first 3 of 26 files.*"
        # an unusable setting falls back to 20
        for bad in (0, -1, None, "x", [1, 2]):
            options({"metacheck.report_max_file_tabs": bad})
            assert len(_report_file_tabs([f"f{i:02d}" for i in range(30)])[0]) == 20
    finally:
        options({"metacheck.report_max_file_tabs": None})
    assert _report_file_tabs([]) == ([], None)
