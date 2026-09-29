"""metacheck bugs pytacheck fixes in the statistics-output readers (docs/UPSTREAM_ISSUES.md)."""

from __future__ import annotations

import pandas as pd
from lxml import etree

from metacheck.statout.spv import _spv_table_html, _spvsx_walk_heading, spv_assemble_table


def test_nested_headings_keep_the_command_name() -> None:
    # U139: a sub-heading or table without commandName belongs to the
    # enclosing command (R resets it to NA: xml_attr() gives NA, not NULL)
    xml = (
        '<heading commandName="Frequencies">'
        "<container><label>Log</label><text type='log'><html>FREQ x.</html></text></container>"
        "<heading><container><label>T</label>"
        "<table subType='Statistics'><tableStructure><dataPath>a.bin</dataPath>"
        "</tableStructure></table></container></heading>"
        "</heading>"
    )
    root = etree.fromstring(f"<root>{xml}</root>")
    rows = _spvsx_walk_heading(root, None, None)["rows"]
    assert [r["command_name"] for r in rows] == ["Frequencies"]
    assert rows[0]["syntax"] == "FREQ x."


def test_assemble_table_renders_templates_and_finds_large_leaf_indices() -> None:
    # U148: a template cell shows its rendered text, and a leaf index of 1e5
    # or more is found (R: as.character(1e5) is "1e+05", not a leaf key)
    n = 100001
    dims = [{"name": "Case", "leaves": {str(i): [f"c{i}"] for i in range(n)}, "n_leaves": n}]
    cells = [
        {"index": 100000.0, "value": {"type": "numeric", "x": 1.5}},
        {
            "index": 3.0,
            "value": {
                "type": "template",
                "s": "^1 of ^2",
                "args": [
                    {"values": [{"type": "string", "s": "3"}]},
                    {"values": [{"type": "numeric", "x": 5.0}]},
                ],
            },
        },
    ]
    df = spv_assemble_table(dims, {"rows": [0]}, cells)
    assert df is not None
    assert df["Case"].tolist() == ["c100000", "c3"]
    assert df["value"].tolist() == ["1.5", "3 of 5"]


def test_pivot_keeps_empty_trailing_labels() -> None:
    # U148: an empty last label keeps its cell (R's strsplit() drops it: the
    # export fails, or a row loses its stub cell)
    df = pd.DataFrame(
        {
            "G": pd.array(["", "a"], dtype="string"),
            "S": pd.array(["s", ""], dtype="string"),
            "value": pd.array(["1", "2"], dtype="string"),
        }
    )
    df.attrs.update({"spv_row_dims": ["G"], "spv_col_dims": ["S"]})
    html = _spv_table_html(df)
    assert "<tr><th>G</th><th>s</th><th></th></tr>" in html
    assert "<tr><td></td><td>1</td><td></td></tr>" in html
    assert "<tr><td>a</td><td></td><td>2</td></tr>" in html
