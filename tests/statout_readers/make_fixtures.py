"""Build the synthetic statistics-output fixtures used by the statout_readers tests.

metacheck ships no ``.spv``, ``.smcl`` or Mplus ``.out`` fixtures (its SPSS
corpus lives in an uncommitted repo cache), so these are generated here:

* ``.spv`` archives are encoded byte-for-byte following the grammar that
  ``R/spv.R`` decodes (PSPP's light-binary / legacy-binary grammars and the
  structure/detail/VizML XML dialects);
* ``.smcl`` and ``.out`` files are hand-written in Stata's and Mplus's own
  output conventions;
* a jamovi ``.omv`` and an SQLite-format ``.jasp`` mirror the archives
  metacheck's own testthat files build on the fly.

Run ``python tests/statout_readers/make_fixtures.py`` to regenerate; the
parity goldens (``parity/golden/statout_readers``) are produced from these
files by R, so regenerate them afterwards.
"""

from __future__ import annotations

import io
import json
import sqlite3
import struct
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
DBL_MAX = 1.7976931348623157e308
EPOCH = (1980, 1, 1, 0, 0, 0)


# ---------------------------------------------------------------------------
# primitive encoders
# ---------------------------------------------------------------------------


def i16(x: int) -> bytes:
    return struct.pack("<H", x & 0xFFFF)


def i32(x: int) -> bytes:
    return struct.pack("<I", x & 0xFFFFFFFF)


def i64(x: int) -> bytes:
    return struct.pack("<Q", x)


def dbl(x: float) -> bytes:
    return struct.pack("<d", x)


def flt(x: float) -> bytes:
    return struct.pack("<f", x)


def string(s: str) -> bytes:
    b = s.encode("utf-8")
    return i32(len(b)) + b


def count(inner: bytes) -> bytes:
    return i32(len(inner)) + inner


def boolean(x: bool) -> bytes:
    return b"\x01" if x else b"\x00"


# ---------------------------------------------------------------------------
# light-binary Values
# ---------------------------------------------------------------------------


def font_style() -> bytes:
    return (
        b"\x00\x01\x00\x00" + string("#000000") + string("#ffffff") + string("SansSerif") + b"\x09"
    )


def cell_style() -> bytes:
    return i32(1) + i32(2) + dbl(0.5) + i16(1) + i16(2) + i16(3) + i16(4)


def template_string(kind: str) -> bytes:
    if kind == "empty":
        return count(b"")
    if kind == "id":
        return count(count(i32(0) + b"\x31\x55") + b"\x31" + string("tmpl"))
    return count(count(i32(0) + b"\x58") + b"\x58")


def value_mod(version: int, refs: list[int] | None = None, style: str | None = None) -> bytes:
    """ValueMod: ``58`` (absent) or ``31`` + refs + subscripts + version tail."""
    if refs is None and style is None:
        return b"\x58"
    refs = refs or []
    out = b"\x31" + i32(len(refs)) + b"".join(i16(r) for r in refs) + i32(1) + string("sub")
    if version == 1:
        # 00 (i1|i2) 00? 00? int32 00? 00?  -- the int32 is chosen so its first
        # byte is not 00 (the optional 00 probes would otherwise swallow it).
        out += b"\x00" + i32(1) + i32(0x11)
    else:
        if style == "font":
            pair = b"\x31" + font_style() + b"\x58"
        elif style == "cell":
            pair = b"\x58\x31" + cell_style()
        else:
            pair = b"\x58\x58"
        out += count(template_string("id" if style else "empty") + pair)
    return out


FMT = 0x00050302  # bytes 02 03 05 00: first byte is not 00


def v_num(
    x: float, version: int = 3, refs: list[int] | None = None, style: str | None = None
) -> bytes:
    return b"\x01" + value_mod(version, refs, style) + i32(FMT) + dbl(x)


def v_numvar(x: float, var: str, label: str, version: int = 3) -> bytes:
    return b"\x02" + value_mod(version) + i32(FMT) + dbl(x) + string(var) + string(label) + b"\x01"


def v_text(s: str, version: int = 3, refs: list[int] | None = None, local: str = "") -> bytes:
    return (
        b"\x03"
        + string(local or s)
        + value_mod(version, refs)
        + string("id_" + s[:3])
        + string(s)
        + b"\x00"
    )


def v_string(s: str, version: int = 3) -> bytes:
    return (
        b"\x04"
        + value_mod(version)
        + i32(FMT)
        + string("lbl")
        + string("var")
        + b"\x02"
        + string(s)
    )


def v_var(name: str, label: str, version: int = 3) -> bytes:
    return b"\x05" + value_mod(version) + string(name) + string(label) + b"\x03"


def v_text6(s: str, version: int = 3) -> bytes:
    return b"\x06" + string(s) + value_mod(version) + string("id6") + string(s)


def v_template(tmpl: str, args: list[list[bytes]], version: int = 3, pad: int = 0) -> bytes:
    out = b"\x00" * pad + value_mod(version) + string(tmpl) + i32(len(args))
    for a in args:
        if len(a) == 1:
            out += i32(0) + a[0]
        else:
            out += i32(len(a)) + i32(0) + b"".join(a)
    return out


# ---------------------------------------------------------------------------
# light-binary tables
# ---------------------------------------------------------------------------


def leaf(name: bytes, idx: int) -> bytes:
    return name + b"\x00\x00\x00" + i32(2) + i32(idx) + i32(0)


def group(name: bytes, subs: list[bytes], merge: bool = False) -> bytes:
    return (
        name
        + boolean(merge)
        + b"\x00\x01"
        + i32(0)
        + i32(0xFFFFFFFF)
        + i32(len(subs))
        + b"".join(subs)
    )


def dimension(name: bytes, cats: list[bytes], index: int) -> bytes:
    props = b"\x00\x01" + i32(2) + boolean(False) + boolean(False) + b"\x01" + i32(index)
    return name + props + i32(len(cats)) + b"".join(cats)


def area(i: int, version: int) -> bytes:
    out = bytes([i]) + b"\x31" + string("SansSerif") + flt(9.0) + i32(0) + boolean(False)
    out += i32(0) + i32(1) + string("#000000") + string("#ffffff") + boolean(False)
    out += string("#000000") + string("#ffffff")
    if version == 3:
        out += i32(8) + i32(11) + i32(1) + i32(3)
    return out


def light_table(
    version: int,
    title: bytes,
    user_title: bytes,
    dims: list[bytes],
    axes: tuple[list[int], list[int], list[int]],
    cells: list[tuple[int, bytes]],
    footnotes: list[tuple[bytes, bytes | None]] = (),  # type: ignore[assignment]
    corner: bytes | None = None,
    caption: bytes | None = None,
    areas_00: bool = False,
) -> bytes:
    out = b"\x01\x00" + i32(version) + b"\x01\x00\x00\x01\x00"
    out += i32(1) + i32(2) + i32(3) + i32(4) + i32(5) + i64(0x1234)
    # Titles
    out += title + b"\x01" + v_text("subtype", version) + b"\x01" + b"\x31" + user_title + b"\x01"
    out += (b"\x31" + corner) if corner is not None else b"\x58"
    out += (b"\x31" + caption) if caption is not None else b"\x58"
    # Footnotes
    out += i32(len(footnotes))
    for text, marker in footnotes:
        out += text + ((b"\x31" + marker) if marker is not None else b"\x58") + i32(1)
    # Areas, borders, print settings, table settings
    out += (b"\x00" if areas_00 else b"") + b"".join(area(k, version) for k in range(1, 9))
    out += count(b"\x00\x00\x00\x01" + struct.pack(">I", 0) + b"\x01\x00\x00\x00")
    out += count(b"\x00\x00\x00\x01" + b"\x01\x00")
    out += count(b"\x00\x00\x00\x01" + i32(0) * 3)
    # Formats
    out += i32(2) + i32(50) + i32(60) + string("en_US.UTF-8") + i32(0)
    out += b"\x00\x01\x00" + i32(1970) + b"." + b","
    out += i32(1) + string("-,$,,")
    out += count(count(string("dataset")) + count(b""))
    # Dimensions, axes, cells
    out += i32(len(dims)) + b"".join(dims)
    layers, rows, cols = axes
    out += i32(len(layers)) + i32(len(rows)) + i32(len(cols))
    out += b"".join(i32(x) for x in layers + rows + cols)
    out += i32(len(cells))
    for idx, val in cells:
        out += i64(idx) + (b"\x00" if version == 1 else b"") + val
    return out


def descriptives_table() -> bytes:
    """A v3 table: nested row groups, a layer, footnotes, every Value variant."""
    v = 3
    stats = dimension(
        v_text("Statistics", v),
        [leaf(v_text("N", v), 0), leaf(v_text("Mean", v), 1), leaf(v_text("Std. Deviation", v), 2)],
        0,
    )
    rows = dimension(
        v_text("Variables", v),
        [
            group(
                v_text("Scales", v),
                [leaf(v_var("age", "Age (years)", v), 0), leaf(v_text("Score", v), 1)],
            ),
            leaf(v_text6("Valid N (listwise)", v), 2),
        ],
        1,
    )
    layer = dimension(v_text("Gender", v), [leaf(v_numvar(1.0, "sex", "Male", v), 0)], 2)
    cells = [
        (0 * 9 + 0 * 3 + 0, v_num(120.0, v)),
        (0 * 9 + 0 * 3 + 1, v_num(34.56789012345678, v, refs=[0])),
        (0 * 9 + 0 * 3 + 2, v_num(1 / 3, v, style="font")),
        (0 * 9 + 1 * 3 + 0, v_numvar(118.0, "score", "Score", v)),
        (0 * 9 + 1 * 3 + 1, v_num(-0.0, v, style="cell")),
        (0 * 9 + 1 * 3 + 2, v_num(DBL_MAX, v)),
        (0 * 9 + 2 * 3 + 0, v_num(1e20, v)),
        (0 * 9 + 2 * 3 + 1, v_string(".", v)),
        (
            0 * 9 + 2 * 3 + 2,
            v_template("^1 of ^2", [[v_text("a", v)], [v_num(2.5, v), v_text("b", v)]], v),
        ),
    ]
    # the layer dimension comes first in dims order (layer, cols, rows)
    return light_table(
        v,
        title=v_text("Descriptive Statistics", v),
        user_title=v_text("Descriptive Statistics", v),
        dims=[layer, stats, rows],
        axes=([0], [2], [1]),
        cells=cells,
        footnotes=[
            (
                v_template("Based on ^1 cases and ^12 others", [[v_num(118.0, v)]], v),
                v_text("a", v),
            ),
            (v_text("Listwise deletion.", v), None),
            (
                v_template(
                    "Path ^1 [^2]", [[v_text(r"C:\\dir\\1x \\U", v)], [v_text("&<b>", v)]], v
                ),
                v_text("b", v),
            ),
        ],
        corner=v_text("corner", v),
        caption=v_text("Table caption", v),
        areas_00=True,
    )


def notes_table_v1() -> bytes:
    """A version-1 table (v1 ValueMods, ``00`` cell prefixes), repeated dimension names."""
    v = 1
    d0 = dimension(
        v_text("Info", v), [leaf(v_text("Output Created", v), 0), leaf(v_text("Comments", v), 1)], 0
    )
    d1 = dimension(v_text("Info", v), [leaf(v_text("Value", v, refs=[1]), 0)], 1)
    d2 = dimension(v_text("", v), [leaf(v_text("x", v), 0)], 2)
    cells = [
        (0, v_text("24-SEP-2026 10:00:00", v, refs=[0, 1])),
        (1, v_template("^1", [[v_num(3.0, v)]], v, pad=2)),
    ]
    return light_table(
        v,
        title=v_text("Notes", v),
        user_title=v_template("Notes for ^1", [[v_var("DataSet1", "", v)]], v),
        dims=[d0, d1, d2],
        axes=([], [0], [1, 2]),
        cells=cells,
    )


def ttest_table() -> bytes:
    """A small v3 table whose row leaves skip an index (cells with no leaf -> NA)."""
    v = 3
    cols = dimension(
        v_text("Statistic", v),
        [leaf(v_text("t", v), 0), leaf(v_text("df", v), 1), leaf(v_text("Sig. (2-tailed)", v), 2)],
        0,
    )
    rows = dimension(
        v_text("Test Value = 0", v), [leaf(v_text("score", v), 0), leaf(v_text("age", v), 3)], 1
    )
    cells = [
        (0, v_num(12.345678, v)),
        (2, v_num(117.0, v)),
        (4, v_num(4.7e-108, v)),
        (1, v_num(0.0004, v)),
        (5, v_num(-2.5, v)),
    ]
    return light_table(
        v,
        title=v_text("One-Sample Test", v),
        user_title=v_text("One-Sample Test", v),
        dims=[cols, rows],
        axes=([], [1], [0]),
        cells=cells,
    )


def empty_cells_table() -> bytes:
    v = 3
    d = dimension(v_text("A", v), [leaf(v_text("a", v), 0)], 0)
    return light_table(v, v_text("Empty", v), v_text("Empty", v), [d], ([], [], [0]), [])


# ---------------------------------------------------------------------------
# legacy binary case data
# ---------------------------------------------------------------------------


def fixed(s: str, n: int) -> bytes:
    b = s.encode("utf-8")[:n]
    return b + b"\x00" * (n - len(b))


def legacy_data(
    sources: list[tuple[str, list[tuple[str, list[float]]]]],
    strings: tuple[list[tuple[str, list[tuple[str, list[tuple[int, int]]]]]], list[str]]
    | None = None,
) -> bytes:
    header = b"\x00" + b"\xb0" + i16(len(sources)) + i32(0)
    meta_size = 80 * len(sources)
    offset = len(header) + meta_size
    metas = b""
    blocks = b""
    for name, variables in sources:
        n_values = len(variables[0][1]) if variables else 0
        nb = name.encode("utf-8")
        metas += i32(n_values) + i32(len(variables)) + i32(offset + len(blocks))
        metas += fixed(name, 28) + (
            fixed(nb[28:].decode("utf-8"), 36) if len(nb) >= 28 else b"\x00" * 36
        )
        metas += i32(0)
        for vname, vals in variables:
            blocks += fixed(vname, 288) + b"".join(dbl(x) for x in vals)
    out = header + metas + blocks
    if strings is not None:
        maps, labels = strings
        out += i32(len(maps))
        for sname, vmaps in maps:
            out += string(sname) + i32(len(vmaps))
            for vname, data in vmaps:
                out += string(vname) + i32(len(data)) + b"".join(i32(a) + i32(b) for a, b in data)
        out += i32(len(labels)) + b"".join(i32(1) + string(lab) for lab in labels)
    return out


LEGACY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="http://xml.spss.com/visualization" lang="en" name="One-Sample Statistics"
    style="visualizationStyle" type="table" version="2.9">
  <extension xmlns:tbl="http://xml.spss.com/spss/table"/>
  <userSource missingValueColor="#000000"/>
  <sourceVariable categorical="true" dependsOn="dimension1categories" id="dimension0categories"
      label="Statistics" labelVariable="dimension0labels" source="tableData"
      sourceName="dimension0categories"/>
  <sourceVariable categorical="true" id="dimension0labels" source="tableData"
      sourceName="dimension0labels"/>
  <derivedVariable categorical="true" id="dimension0group0" value="constant(0)"/>
  <sourceVariable categorical="true" id="dimension1categories" labelVariable="dimension1labels"
      source="tableData" sourceName="dimension1categories"/>
  <sourceVariable categorical="true" id="dimension1labels" source="tableData"
      sourceName="dimension1labels"/>
  <derivedVariable categorical="true" id="unsupported" value="map(x)"/>
  <sourceVariable categorical="true" id="cell" source="tableData" sourceName="cell"/>
  <graph cellStyle="cellStyle" style="graphStyle">
    <location part="height" method="sizeToContent"/>
    <faceting>
      <cross>
        <nest>
          <variableReference ref="dimension0categories"/>
          <variableReference ref="dimension0group0"/>
        </nest>
        <nest>
          <variableReference ref="dimension1categories"/>
        </nest>
      </cross>
    </faceting>
    <facetLayout><tableLayout fitCells="ticks"/></facetLayout>
    <interval style="intervalStyle">
      <labeling style="labelingStyle" variable="cell"/>
    </interval>
  </graph>
</visualization>
"""

LEGACY_XML_BAD = LEGACY_XML.replace(
    "</cross>", '<nest><variableReference ref="cell"/></nest></cross>'
)


def legacy_table_data() -> bytes:
    cats0 = [0.0, 1.0, 2.0, 0.0, 1.0, 2.0]
    cats1 = [0.0, 0.0, 0.0, 1.0, 1.0, 1.0]
    cell = [118.0, 34.5, float("nan"), 120.0, -DBL_MAX, 0.25]
    zero = [0.0] * 6
    long_name = "a_source_name_that_is_exactly_28"[:28] + "_and_continues"
    return legacy_data(
        [
            (
                "tableData",
                [
                    ("dimension0categories", cats0),
                    ("dimension0labels", zero),
                    ("dimension1categories", cats1),
                    ("dimension1labels", zero),
                    ("cell", cell),
                ],
            ),
            (long_name, [("unused", [1.0])]),
        ],
        strings=(
            [
                (
                    "tableData",
                    [
                        ("dimension0labels", [(0, 0), (1, 1), (2, 2), (3, 0), (4, 1), (5, 2)]),
                        ("dimension1labels", [(0, 3), (1, 3), (2, 3), (3, 4), (4, 4), (5, 4)]),
                        ("cell", [(2, 5), (9, 5)]),
                    ],
                ),
                ("noSuchSource", [("x", [(0, 0)])]),
            ],
            ["N", "Mean", "Std. Error Mean", "age", "score", "."],
        ),
    )


# ---------------------------------------------------------------------------
# charts (VizML + legacy case data)
# ---------------------------------------------------------------------------

VIZ = "http://www.ibm.com/software/analytics/spss/xml/visualization"

POINT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="{VIZ}" lang="en">
  <sourceVariable id="source0_x" source="source0" sourceName="height" label="Height (cm)"/>
  <sourceVariable id="source0_y" source="source0" sourceName="weight" shortLabel="Wt"/>
  <graph>
    <point><x variable="source0_x"/><y variable="source0_y"/></point>
    <functionGuide name="Linear" value="0.5 * x + 3"/>
    <functionGuide value="0.01 * x^2 + 1"/>
    <functionGuide name="broken" value="x +"/>
  </graph>
  <axis id="axisy_1"><label><descriptions><text>Weight (kg)</text></descriptions></label></axis>
  <labelFrame><label><text>Weight by Height</text></label></labelFrame>
</visualization>
"""

INTERVAL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="{VIZ}">
  <sourceVariable id="bin" source="source0" sourceName="value"/>
  <sourceVariable id="cnt" source="source0" sourceName="count"/>
  <graph><interval summaryStatistic="sum"><x variable="bin"/><y variable="cnt"/></interval></graph>
  <axis id="axisx_2"><label><text>Score</text></label></axis>
</visualization>
"""

BOX_ES_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="{VIZ}">
  <embeddedSource id="es0"><names>Category;Label;Tooltips;Value</names>
    <row>0;;167,50;1,75</row><row>0;;x;2,5</row><row>1;;y;3</row><row>2;;z;4,25</row>
  </embeddedSource>
  <sourceVariable id="cat" source="es0" sourceName="Category">
    <format><relabel from="0" to="Control"/><relabel from="1" to="Treatment"/></format>
  </sourceVariable>
  <sourceVariable id="val" source="es0" sourceName="Value" label="Reaction time"/>
  <graph><schema><x variable="cat"/><y variable="val"/></schema></graph>
  <labelFrame><label><text>Box plot</text></label></labelFrame>
</visualization>
"""

BOX_BIN_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="{VIZ}">
  <sourceVariable id="grp" source="source0" sourceName="group">
    <relabel from="1" to="Low"/><relabel from="2" to="High"/>
  </sourceVariable>
  <sourceVariable id="rt" source="source0" sourceName="rt"/>
  <graph><schema><x variable="grp"/><y variable="rt"/></schema></graph>
</visualization>
"""

LINE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="{VIZ}">
  <sourceVariable id="a" source="source0" sourceName="height"/>
  <graph><line><x variable="a"/><y variable="a"/></line></graph>
</visualization>
"""


def chart_data() -> bytes:
    nan = float("nan")
    return legacy_data(
        [
            (
                "source0",
                [
                    ("height", [150.0, 160.0, 170.5, 180.0, nan, DBL_MAX]),
                    ("weight", [55.0, 60.25, 70.0, 82.0, 90.0, 95.0]),
                    ("value", [1.0, 2.0, 2.0, 3.0, 3.0, 3.0]),
                    ("count", [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]),
                    ("group", [1.0, 1.0, 2.0, 2.0, 3.0, nan]),
                    ("rt", [0.5, 0.75, 1.25, 1.5, 2.0, 2.5]),
                ],
            )
        ]
    )


# ---------------------------------------------------------------------------
# structure XML
# ---------------------------------------------------------------------------

NS = (
    'xmlns="http://xml.spss.com/spss/viewer/viewer-tree" '
    'xmlns:vtx="http://xml.spss.com/spss/viewer/viewer-text" '
    'xmlns:vtb="http://xml.spss.com/spss/viewer/viewer-table" '
    'xmlns:vgr="http://xml.spss.com/spss/viewer/viewer-graph"'
)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def log_container(syntax: str) -> str:
    html = (
        '<html><head><style type="text/css">p{color:0;font-family:Monospaced;font-size:13pt;}'
        "</style></head><BR><p>" + syntax + "</p></html>"
    )
    return (
        '<container visibility="visible"><label>Log</label>'
        f'<vtx:text type="log" commandName="Log"><vtx:html lang="en">{esc(html)}</vtx:html></vtx:text>'
        "</container>"
    )


def title_container(text: str) -> str:
    return (
        f'<container><label>Title</label><vtx:text type="title"><vtx:html lang="en">'
        f"{esc('<html><body><b>' + text + '</b></body></html>')}</vtx:html></vtx:text></container>"
    )


def table_container(
    label: str, data_path: str, command: str | None, subtype: str | None, path: str | None = None
) -> str:
    attrs = ""
    if command is not None:
        attrs += f' commandName="{command}"'
    if subtype is not None:
        attrs += f' subType="{subtype}"'
    xml_path = f"<vtb:path>{path}</vtb:path>" if path is not None else ""
    return (
        f'<container><label>{label}</label><vtb:table{attrs} type="table">'
        f"<vtb:tableStructure>{xml_path}<vtb:dataPath>{data_path}</vtb:dataPath></vtb:tableStructure>"
        "</vtb:table></container>"
    )


def graph_container(data_path: str, path: str, command: str | None = "Graph") -> str:
    attrs = f' commandName="{command}"' if command is not None else ""
    return (
        f"<container><label>Graph</label><vgr:graph{attrs}>"
        f"<vgr:dataPath>{data_path}</vgr:dataPath><vgr:path>{path}</vgr:path></vgr:graph></container>"
    )


def doc(body: str, command: str | None = None) -> str:
    cmd = f' commandName="{command}"' if command is not None else ""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f"<heading {NS}{cmd}><label>Output</label>{body}</heading>\n"
    )


def write_zip(path: Path, members: list[tuple[str, bytes | str]]) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data.encode("utf-8") if isinstance(data, str) else data)
    path.write_bytes(buf.getvalue())


def build_spv() -> None:
    d = FIX / "spv"
    d.mkdir(parents=True, exist_ok=True)

    desc_syntax = "DESCRIPTIVES VARIABLES=age score   \n  /STATISTICS=MEAN STDDEV MIN MAX."
    ttest_syntax = "T-TEST\n  /TESTVAL=0\n  /VARIABLES=score."
    write_zip(
        d / "modern.spv",
        [
            ("META-INF/MANIFEST.MF", "allowPivoting=true"),
            ("outputViewer2.xml", doc(log_container(desc_syntax))),
            (
                "outputViewer3_heading.xml",
                doc(
                    title_container("Descriptives")
                    + table_container(
                        "Notes", "0000000004_lightTableData.bin", "Descriptives", "Notes"
                    )
                    + table_container(
                        "Descriptive Statistics",
                        "0000000005_lightTableData.bin",
                        "Descriptives",
                        "",
                    )
                    + "<container><label>Only a label</label></container>"
                    + '<container><label>Object</label><object type="x"/></container>'
                    + table_container(
                        "Broken", "0000000006_lightTableData.bin", "Descriptives", "Broken"
                    )
                    + table_container(
                        "Missing member", "0000000099_lightTableData.bin", "Descriptives", "Gone"
                    )
                    + table_container("No data path", "", "Descriptives", "Nothing"),
                    command="Descriptives",
                ),
            ),
            ("outputViewer10.xml", doc(log_container(ttest_syntax))),
            (
                "outputViewer11_heading.xml",
                doc(
                    "<heading><label>Nested, no command</label>"
                    + table_container(
                        "One-Sample Test",
                        "0000000012_lightTableData.bin",
                        "T-Test",
                        "One-Sample Test",
                    )
                    + table_container("No command", "0000000013_lightTableData.bin", None, None)
                    + "</heading>"
                    + table_container("Empty", "0000000014_lightTableData.bin", "T-Test", "Empty")
                    + table_container(
                        "Version 2", "0000000015_lightTableData.bin", "T-Test", "Version 2"
                    ),
                    command="T-Test",
                ),
            ),
            ("outputViewer16_heading.xml", "<heading><unclosed>"),
            ("0000000004_lightTableData.bin", notes_table_v1()),
            ("0000000005_lightTableData.bin", descriptives_table()),
            ("0000000006_lightTableData.bin", descriptives_table()[:300]),
            ("0000000012_lightTableData.bin", ttest_table()),
            ("0000000013_lightTableData.bin", ttest_table()),
            ("0000000014_lightTableData.bin", empty_cells_table()),
            ("0000000015_lightTableData.bin", b"\x01\x00" + i32(2) + b"\x00" * 40),
        ],
    )

    write_zip(
        d / "legacy.spv",
        [
            (
                "outputViewer0000000001.xml",
                doc(log_container("T-TEST /TESTVAL=0 /VARIABLES=age score.")),
            ),
            (
                "outputViewer0000000002_heading.xml",
                doc(
                    table_container(
                        "One-Sample Statistics",
                        "0000000003_tableData.bin",
                        "T-Test",
                        "One-Sample Statistics",
                        path="0000000003_table.xml",
                    )
                    + table_container(
                        "Bad nests",
                        "0000000003_tableData.bin",
                        "T-Test",
                        None,
                        path="0000000004_table.xml",
                    )
                    + table_container(
                        "Bad data",
                        "0000000005_tableData.bin",
                        "T-Test",
                        "Bad data",
                        path="0000000003_table.xml",
                    )
                    + table_container(
                        "No detail",
                        "0000000003_tableData.bin",
                        "T-Test",
                        "No detail",
                        path="0000000009_table.xml",
                    ),
                    command="T-Test",
                ),
            ),
            ("0000000003_tableData.bin", legacy_table_data()),
            ("0000000003_table.xml", LEGACY_XML),
            ("0000000004_table.xml", LEGACY_XML_BAD),
            ("0000000005_tableData.bin", b"\x01garbage"),
        ],
    )

    cd = chart_data()
    write_zip(
        d / "charts.spv",
        [
            (
                "outputViewer0000000001.xml",
                doc(log_container("GRAPH /SCATTERPLOT(BIVAR)=height WITH weight.")),
            ),
            (
                "outputViewer0000000002_heading.xml",
                doc(
                    graph_container("0000000003_chartData.bin", "0000000003_chart.xml")
                    + graph_container(
                        "0000000003_chartData.bin", "0000000004_chart.xml", command="Frequencies"
                    )
                    + graph_container(
                        "0000000003_chartData.bin", "0000000005_chart.xml", command=None
                    )
                    + graph_container(
                        "0000000003_chartData.bin", "0000000006_chart.xml", command="Examine"
                    )
                    + graph_container(
                        "0000000003_chartData.bin", "0000000007_chart.xml", command="Examine"
                    )
                    + graph_container(
                        "0000000003_chartData.bin", "0000000008_chart.xml", command="Examine"
                    ),
                    command="Graph",
                ),
            ),
            ("0000000003_chartData.bin", cd),
            ("0000000003_chart.xml", POINT_XML),
            ("0000000004_chart.xml", INTERVAL_XML),
            ("0000000005_chart.xml", BOX_ES_XML),
            ("0000000006_chart.xml", BOX_BIN_XML),
            ("0000000007_chart.xml", LINE_XML),
        ],
    )

    t = "0000000009_lightTableData.bin"
    write_zip(
        d / "logs_only.spv",
        [
            ("outputViewer0000000001.xml", doc(log_container("GET FILE='data.sav'."))),
            ("outputViewer0000000002_heading.xml", doc(table_container("T1", t, "X", "T1"))),
            ("outputViewer0000000003_heading.xml", doc(table_container("T2", t, "X", "T2"))),
            (
                "outputViewer0000000004.xml",
                doc(
                    '<container><label>Log</label><vtx:text type="log"/></container>'
                    + table_container("T3", t, "X", "T3")
                ),
            ),
            ("outputViewer0000000005.xml", doc(log_container("FREQUENCIES age."))),
            ("outputViewer0000000006_heading.xml", doc(table_container("T4", t, "X", "T4"))),
            ("outputViewer0000000007.xml", doc(log_container("   "))),
            ("outputViewer0000000008_heading.xml", doc(table_container("T5", t, "X", "T5"))),
        ],
    )
    write_zip(d / "empty.spv", [("META-INF/MANIFEST.MF", "x"), ("other.xml", "<a/>")])
    (d / "notzip.spv").write_text("not a zip\n")


# ---------------------------------------------------------------------------
# jamovi / JASP
# ---------------------------------------------------------------------------


def build_omv_jasp() -> None:
    d = FIX / "archives"
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "dataSet": {
            "rowCount": 3,
            "fields": [
                {
                    "name": "grp",
                    "dataType": "Integer",
                    "measureType": "Nominal",
                    "columnType": "Data",
                    "labels": [[1, "Control"], [2, "Treatment"]],
                },
                {
                    "name": "score",
                    "dataType": "Decimal",
                    "measureType": "Continuous",
                    "columnType": "Data",
                    "description": "Total score",
                },
                {
                    "name": "note",
                    "dataType": "Text",
                    "measureType": "Nominal",
                    "columnType": "Data",
                },
                {"name": "code", "measureType": "Ordinal", "title": "Response code"},
            ],
        }
    }
    xdata = {
        "code": {"labels": [[1, "low", "1"], ["2", "high", "2"], ["x", "bad", "x"], [3, "", "3"]]}
    }
    data = (
        struct.pack("<3i", 1, 2, 1)
        + struct.pack("<3d", 1.5, 2.5, float("nan"))
        + struct.pack("<3i", 0, 1, -2147483648)
        + struct.pack("<3i", 1, -2147483648, 2)
    )
    strings = b"yes\x00no\x00"
    blob = b"\x52\x01" + b"jmv::ttestIS(vars = vars(score), group = grp, students = TRUE)\x12\x00"
    blob2 = b"\x0a\x05\x00junk\x52\x03" + b"jmv::descriptives(data = data, vars = vars(score, grp)"
    write_zip(
        d / "fixture.omv",
        [
            ("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n"),
            ("metadata.json", json.dumps(meta)),
            ("xdata.json", json.dumps(xdata)),
            ("data.bin", data),
            ("strings.bin", strings),
            ("02 descriptives/analysis", blob2),
            ("01 ttestIS/analysis", blob),
            ("10 empty/analysis", b"\x00\x01\x02"),
            (
                "index.html",
                '<html><body><img src="01%20ttestIS/resources/plot.png">'
                '<img src="01%20ttestIS/resources/plot.png"><img src="missing.png">'
                '<img src="https://example.org/x.png"><img SRC="data:image/png;base64,AAAA">'
                '<img src="02 descriptives/resources/p.JPEG"></body></html>',
            ),
            ("01 ttestIS/resources/plot.png", b"\x89PNG\r\n\x1a\nfakepng"),
            ("02 descriptives/resources/p.JPEG", b"\xff\xd8\xff\xe0fakejpeg"),
        ],
    )

    # A modern SQLite-format .jasp (the schema metacheck's own test builds).
    sq = d / "internal.sqlite"
    if sq.exists():
        sq.unlink()
    con = sqlite3.connect(sq)
    con.execute("CREATE TABLE Columns (id INT, name TEXT, columnType TEXT, colIdx INT, title TEXT)")
    con.execute(
        "INSERT INTO Columns VALUES (1,'grp','nominal',0,'grp'),(2,'score','scale',1,'Total'),"
        "(3,'rating','ordinal',2,NULL),(4,'weight','scale',3,'')"
    )
    con.execute(
        "CREATE TABLE DataSet_1 (rowNumber INT, Column_1_INT INT, Column_2_DBL REAL, "
        "Column_3_INT INT, Column_4_INT INT)"
    )
    con.execute(
        "INSERT INTO DataSet_1 VALUES (0,1,1.5,3,70),(1,2,2.5,-2147483648,NULL),(2,1,NULL,5,80)"
    )
    con.execute("CREATE TABLE Labels (columnId INT, value INT, ordering INT, label TEXT)")
    con.execute(
        "INSERT INTO Labels VALUES (1,2,1,'Treatment'),(1,1,0,'Control'),(3,3,0,''),(3,NULL,1,'none')"
    )
    con.execute("CREATE TABLE DataSets (dataFilePath TEXT)")
    con.execute("INSERT INTO DataSets VALUES ('orig.csv')")
    con.commit()
    con.close()
    write_zip(d / "sqlite.jasp", [("internal.sqlite", sq.read_bytes())])
    sq.unlink()

    # A binary .jasp without analyses.json and with an index.html to export.
    jmeta = {
        "dataFilePath": "C:/data/study.csv",
        "dataSet": {
            "rowCount": 2,
            "fields": [
                {"name": "id", "measureType": "Nominal"},
                {"name": "rt", "measureType": "Continuous", "title": "Reaction time"},
                {
                    "name": "cond",
                    "measureType": "Ordinal",
                    "labels": [[1, "A", True], [2, "B", True]],
                },
            ],
        },
    }
    jdata = (
        struct.pack("<2i", 7, 8) + struct.pack("<2d", 0.25, float("nan")) + struct.pack("<2i", 2, 1)
    )
    write_zip(
        d / "binary.jasp",
        [
            ("metadata.json", json.dumps(jmeta)),
            ("data.bin", jdata),
            ("index.html", '<p>no images</p><img src="resources/1/p.gif">'),
            ("resources/1/p.gif", b"GIF89afake"),
        ],
    )
    (d / "notzip.jasp").write_text("not a zip\n")
    (d / "notzip.omv").write_text("not a zip\n")
    write_zip(d / "nodata.jasp", [("metadata.json", "{}")])
    write_zip(d / "nodata.omv", [("metadata.json", "{}")])


# ---------------------------------------------------------------------------
# Stata .smcl and Mplus .out
# ---------------------------------------------------------------------------

SMCL = r"""{smcl}
{com}{sf}{ul off}{txt}{.-}
      name:  {res}<unnamed>
       {txt}log:  {res}/Users/me/project/analysis.smcl
  {txt}log type:  {res}smcl
 {txt}opened on:  {res}24 Sep 2026, 10:15:32
{txt}
{com}. summarize price mpg weight

{txt}    Variable {c |}        Obs        Mean    Std. dev.       Min        Max
{hline 13}{c +}{hline 57}
{space 7}price {c |}{res}{col 20}     74    6165.257    2949.496       3291      15906
{txt}{space 9}mpg {c |}{res}{col 20}     74     21.2973    5.785503         12         41
{txt}{space 6}weight {c |}{res}{col 20}     74    3019.459    777.1936       1760       4840

{com}. regress price mpg weight

{txt}      Source {c |}       SS           df       MS      Number of obs   ={res}        74
{txt}{hline 13}{c +}{hline 34}   F(2, 71)        = {res}    14.74
{txt}       Model {c |} {res} 186321280         2  93160639.9   {txt}Prob > F        ={res}    0.0000
{txt}    Residual {c |} {res} 448744116        71  6320339.67   {txt}R-squared       ={res}    0.2934
{txt}{hline 13}{c +}{hline 34}   Adj R-squared   ={res}    0.2735
{txt}       Total {c |} {res} 634065396        73  8685827.34   {txt}Root MSE        =   {res} 2514

{txt}{hline 13}{c TT}{hline 64}
{col 1}       price{col 14}{c |} Coefficient{col 26}  Std. err.{col 37}      t{col 45}   P>|t|{col 53}     [95% con{col 66}f. interval]
{hline 13}{c +}{hline 64}
{space 9}mpg {c |}{col 14}{res}{space 2}-49.51222{col 26}{space 2} 86.15604{col 37}{space 1}   -0.57{col 45}{space 3}0.567{col 53}{space 4}-221.3025{col 66}{space 3} 122.2781
{txt}{space 6}weight {c |}{col 14}{res}{space 2} 1.746559{col 26}{space 2} .6413538{col 37}{space 1}    2.72{col 45}{space 3}0.008{col 53}{space 4} .4677522{col 66}{space 3} 3.025366
{txt}{space 7}_cons {c |}{col 14}{res}{space 2} 1946.069{col 26}{space 2}  3597.05{col 37}{space 1}    0.54{col 45}{space 3}0.590{col 53}{space 4}-5226.245{col 66}{space 3} 9118.382
{txt}{hline 13}{c BT}{hline 64}
{res}{txt}
{com}. foreach v of varlist price mpg {c -(}
{txt}  2{com}.   quietly summarize `v'
{txt}  3{com}. {c )-}

{com}. logit foreign mpg, nolog

{txt}Iteration 0:{space 2}log likelihood = {res}-45.03321
{txt}Iteration 1:{space 2}log likelihood = {res}-39.380959
{txt}Logistic regression{col 49}Number of obs{col 67}= {res}        74
{txt}{col 49}LR chi2({res}1{txt}){col 67}= {res}     11.30
{txt}Log likelihood = {res}-39.380959{txt}{col 49}Pseudo R2{col 67}= {res}    0.1254

{com}. tabulate rep78

{txt}     Repair {c |}
record 1978 {c |}      Freq.     Percent        Cum.
{hline 12}{c +}{hline 35}
          1 {c |}{res}          2        2.90        2.90
{txt}          2 {c |}{res}          8       11.59       14.49
{txt}{hline 12}{c +}{hline 35}
      Total {c |}{res}         69      100.00
{txt}
{com}. display "{dup 3:ab} {char 65}{c 0x41} {ralign 8:xy}|{center 7:ab}|{lalign 5:z}|{res:-1.5}{it:x}{* note}"
{txt}{hline 3}{c +}{hline 3}
{col 5}{ralign 12:label} {rcenter 9:mid}{bf}
{res}ababab AA       xy|  ab   |z    |-1.5x

{com}. display "only text, no table"
{txt}Some words here {hline}

{com}. graph export "figure1.png", replace
{txt}(file figure1.png written in PNG format)

{com}. log close
      {txt}name:  {res}<unnamed>
       {txt}log:  {res}/Users/me/project/analysis.smcl
  {txt}log type:  {res}smcl
 {txt}closed on:  {res}24 Sep 2026, 10:16:01
{txt}{.-}
{smcl}
{txt}{sf}{ul off}
"""

SMCL_NOCMD = """{smcl}
{txt}{sf}{ul off}{.-}
      name:  {res}<unnamed>
{txt}Nothing was run in this log.
"""

MPLUS = """Mplus VERSION 8.4
MUTHEN & MUTHEN
09/24/2026   10:15 AM

INPUT INSTRUCTIONS

  TITLE: Two-level regression;
  DATA: FILE = data.dat;
  VARIABLE: NAMES = clus y x w;
            CLUSTER = clus;
            WITHIN = x;
            BETWEEN = w;
  ANALYSIS: TYPE = TWOLEVEL;
  MODEL:
    %WITHIN%
    y ON x;
    %BETWEEN%
    y ON w;

*** WARNING in MODEL command
  Variable is uncorrelated with all other variables: W

   1 WARNING(S) FOUND IN THE INPUT INSTRUCTIONS



Two-level regression;

SUMMARY OF ANALYSIS

Number of groups                                                 1
Number of observations                                         500

Number of dependent variables                                    1
Number of independent variables                                  2
Number of continuous latent variables                            0

Estimator                                                      MLR
Information matrix                                        OBSERVED
Maximum number of iterations                                   100
Convergence criterion                                    0.100D-05


UNIVARIATE SAMPLE STATISTICS


     UNIVARIATE HIGHER-ORDER MOMENT DESCRIPTIVE STATISTICS

         Variable/         Mean/     Skewness/   Minimum/ % with                Percentiles
        Sample Size      Variance    Kurtosis    Maximum  Min/Max      20%/60%    40%/80%    Median

     Y                     0.012      -0.041      -3.101    0.20%      -0.851     -0.259      0.010
             500.000       1.021      -0.112       3.214    0.20%       0.282      0.862
     X                    -0.023       0.066      -2.852    0.20%      -0.840     -0.271     -0.011
             500.000       0.976       0.031       3.120    0.20%       0.228      0.815


THE MODEL ESTIMATION TERMINATED NORMALLY



MODEL FIT INFORMATION

Number of Free Parameters                        5

Loglikelihood

          H0 Value                       -1423.568
          H0 Scaling Correction Factor      1.0123
            for MLR
          H1 Value                       -1423.568

Information Criteria

          Akaike (AIC)                    2857.136
          Bayesian (BIC)                  2878.209
          Sample-Size Adjusted BIC        2862.338
            (n* = (n + 2) / 24)

Chi-Square Test of Model Fit

          Value                              0.000*
          Degrees of Freedom                     0
          P-Value                           1.0000



MODEL RESULTS

                                                    Two-Tailed
                    Estimate       S.E.  Est./S.E.    P-Value

Within Level

 Y          ON
    X                  0.498      0.044     11.318      0.000

 Residual Variances
    Y                  0.742      0.049     15.143      0.000

Between Level

 Y          ON
    W                  0.203      0.071      2.859      0.004

 Intercepts
    Y                  0.011      0.052      0.212      0.832

 Residual Variances
    Y                  0.231      0.041      5.634      0.000


STANDARDIZED MODEL RESULTS


STDYX Standardization

                                                    Two-Tailed
                    Estimate       S.E.  Est./S.E.    P-Value

Within Level

 Y          ON
    X                  0.483      0.038     12.711      0.000


R-SQUARE

Within Level

    Observed                                        Two-Tailed
    Variable        Estimate       S.E.  Est./S.E.    P-Value

    Y                  0.233      0.037      6.356      0.000


SAMPLE STATISTICS


     ESTIMATED SAMPLE STATISTICS FOR WITHIN


           Means
              Y             X
              ________      ________
                0.000         0.000


           Covariances
              Y             X
              ________      ________
 Y              0.966
 X              0.487         0.978


TECHNICAL 1 OUTPUT


     PARAMETER SPECIFICATION FOR WITHIN


           NU
              Y             X
              ________      ________
                  0             0


     Beginning Time:  10:15:32
        Ending Time:  10:15:33
       Elapsed Time:  00:00:01



MUTHEN & MUTHEN
3463 Stoner Ave.
Los Angeles, CA  90066

Tel: (310) 391-9971
Fax: (310) 391-8971
Web: www.StatModel.com
Support: Support@StatModel.com

Copyright (c) 1998-2019 Muthen & Muthen
MODEL MODIFICATION INDICES
"""

MPLUS_ADJACENT = """Mplus VERSION 7.4
MUTHEN & MUTHEN

INPUT INSTRUCTIONS
  TITLE: adjacent headers;
SUMMARY OF ANALYSIS
Number of observations      12
MODEL RESULTS
R-SQUARE
"""

MPLUS_NOSECTIONS = """Mplus VERSION 8.0
MUTHEN & MUTHEN
Nothing recognisable here.
"""

NOT_MPLUS = """Compiler output
gcc -O2 main.c
"""


def build_text() -> None:
    d = FIX / "text"
    d.mkdir(parents=True, exist_ok=True)
    (d / "analysis.smcl").write_text(SMCL, encoding="utf-8")
    (d / "nocommands.smcl").write_text(SMCL_NOCMD, encoding="utf-8")
    (d / "twolevel.out").write_bytes(MPLUS.replace("\n", "\r\n").encode("utf-8"))
    (d / "adjacent.out").write_text(MPLUS_ADJACENT, encoding="utf-8")
    (d / "nosections.out").write_text(MPLUS_NOSECTIONS, encoding="utf-8")
    (d / "compiler.out").write_text(NOT_MPLUS, encoding="utf-8")


def main() -> None:
    build_spv()
    build_omv_jasp()
    build_text()


if __name__ == "__main__":
    main()
