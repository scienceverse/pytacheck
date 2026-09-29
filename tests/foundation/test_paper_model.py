from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc


def test_demopaper_tables(demo: pc.Paper) -> None:
    assert demo.paper_id == "to_err_is_human"
    assert len(demo.text) == 37
    assert str(demo.text["text_id"].dtype) == "Int64"
    assert demo.title == "To Err is Human: An Empirical Investigation"
    assert demo.funding is None  # schema table the paper lacks -> None (R NULL)


def test_lazy_tables_materialise_once(demo: pc.Paper) -> None:
    assert demo._raw_records("text") is not None
    first = demo.text
    assert demo._raw_records("text") is None
    assert demo.text is first


def test_paperlist_indexing(psychsci: pc.PaperList) -> None:
    assert len(psychsci) == 3
    assert psychsci[0].paper_id == psychsci.names[0]
    assert psychsci[psychsci.names[1]] is psychsci[1]
    assert isinstance(psychsci[0:2], pc.PaperList)


def test_paper_table_fast_path_matches_slow_path(psychsci: pc.PaperList) -> None:
    fast = pc.paper_table(psychsci, "text")
    for p in psychsci:
        _ = p.text  # materialise -> slow path
    slow = pc.paper_table(psychsci, "text")
    pd.testing.assert_frame_equal(fast, slow)


def test_round_trip(tmp_path, demo: pc.Paper) -> None:
    path = pc.paper_write(demo, save_path=tmp_path)
    again = pc.read(path)
    pd.testing.assert_frame_equal(again.text, demo.text)


def test_validate(demo: pc.Paper) -> None:
    assert pc.paper_validate(demo)
    broken = demo.copy()
    del broken["text"]
    with pytest.raises(pc.PaperValidationError):
        pc.paper_validate(broken)


def test_import_is_lazy() -> None:
    import subprocess
    import sys

    code = "import sys, metacheck; print('pandas' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False", "import metacheck must not import pandas"
