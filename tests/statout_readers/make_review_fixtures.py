"""Build the review fixtures for the statout_readers parity cases.

These target branches the first round of fixtures did not reach, found while
reviewing ``R/spv.R``, ``R/jasp.R``, ``R/omv.R``, ``R/stata.R`` and
``R/mplus.R`` against the Python port:

* ``.spv``: strings with trailing NUL bytes (``rawToChar()`` drops them),
  structure documents whose numbers tie or sort differently as text,
  headings without ``commandName``, R-parseable function guides that are not
  plain arithmetic, box-plot relabels keyed by ``""``, a legacy dimension
  whose ``id`` is ``""``;
* ``.omv``/``.jasp``: absolute and percent-encoded image paths, an empty
  ``index.html``, analysis folder names that sort differently under ICU,
  ``strings.bin`` edge cases, partial ``$`` matches, a ``\\(`` after a
  ``pkg::fn`` name, SQLite columns with no ``columnType``;
* ``.smcl``/``.out``: SMCL directives with odd arguments, Mplus label/value
  lines that exercise TRE's lazy matching, lower-case section headers, and a
  latin1-encoded log of each kind (R cannot read those: a known divergence).

Run ``python tests/statout_readers/make_review_fixtures.py``; the goldens
(``parity/golden/statout_readers_review``) are produced from these files by R.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from make_fixtures import (  # noqa: E402
    DBL_MAX,
    VIZ,
    dimension,
    doc,
    graph_container,
    group,
    leaf,
    legacy_data,
    light_table,
    log_container,
    table_container,
    title_container,
    v_num,
    v_template,
    v_text,
    write_zip,
)

OUT = HERE / "fixtures" / "review"


# ---------------------------------------------------------------------------
# .spv
# ---------------------------------------------------------------------------


def nul_strings_table() -> bytes:
    """A v3 table whose strings carry trailing NUL bytes (R drops them)."""
    v = 3
    cols = dimension(
        v_text("Statistic\x00", v),
        [leaf(v_text("Mean\x00\x00", v), 0), leaf(v_text("SD", v), 1)],
        0,
    )
    rows = dimension(
        v_text("Variable", v),
        [group(v_text("Group\x00", v), [leaf(v_text("x", v), 0), leaf(v_text("y", v), 1)])],
        1,
    )
    cells = [
        (0, v_num(1.5, v)),
        (1, v_text("n/a\x00", v)),
        (2, v_template("^1\x00", [[v_text("t\x00", v)]], v)),
        (3, v_num(0.25, v)),
    ]
    return light_table(
        v,
        title=v_text("Trailing NULs", v),
        user_title=v_text("Trailing NULs\x00", v),
        dims=[cols, rows],
        axes=([], [1], [0]),
        cells=cells,
        footnotes=[(v_text("note\x00", v), v_text("a\x00", v))],
    )


def embedded_nul_table() -> bytes:
    """A v3 table with an embedded NUL in a cell string (R cannot decode it)."""
    v = 3
    d = dimension(v_text("A", v), [leaf(v_text("a", v), 0)], 0)
    return light_table(
        v, v_text("Bad", v), v_text("Bad", v), [d], ([], [], [0]), [(0, v_text("x\x00y", v))]
    )


def simple_table(label: str, value: float) -> bytes:
    v = 3
    d = dimension(v_text("Statistic", v), [leaf(v_text(label, v), 0)], 0)
    return light_table(
        v, v_text(label, v), v_text(label, v), [d], ([], [], [0]), [(0, v_num(value, v))]
    )


LEGACY_EMPTY_ID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="http://xml.spss.com/visualization" lang="en">
  <sourceVariable categorical="true" id="" label="Statistics" source="tableData"
      sourceName="dimension0categories"/>
  <sourceVariable categorical="true" id="cell" source="tableData" sourceName="cell"/>
  <graph>
    <faceting><cross>
      <nest><variableReference ref=""/></nest>
      <nest></nest>
    </cross></faceting>
    <interval><labeling variable="cell"/></interval>
  </graph>
</visualization>
"""

LEGACY_OK_XML = LEGACY_EMPTY_ID_XML.replace('id=""', 'id="dim0"').replace('ref=""', 'ref="dim0"')


def legacy_small_data() -> bytes:
    return legacy_data(
        [("tableData", [("dimension0categories", [0.0, 1.0]), ("cell", [3.5, 1e5])])]
    )


FIT_EXPRS = [
    ("Linear", "0.5 * x + 3"),
    ("logical", "TRUE"),
    ("unknown symbol", "a * x"),
    ("two exprs", "1; 2"),
    ("newline", "x\n+1"),
    ("if", "if (x > 0) 1 else 2"),
    ("modulo", "x %% 2"),
    ("underscore", "1_000"),
    ("neg square", "-x^2"),
    ("exp", "exp(x) / 10"),
    ("empty", ""),
    ("blank", "   "),
    ("comment only", "# nothing"),
    ("string", "'abc'"),
    ("backtick", "`x` + 1"),
    ("hex", "0x1F * x"),
    ("int", "2L * x"),
    ("function", "function(x) x"),
    ("assign", "y <- x"),
    ("python only", "x // 2"),
    ("python cmp", "1 if x else 2"),
    ("dollar", "x$y"),
    ("brackets", "x[1]"),
    ("braces", "{x; 1}"),
    ("unary not", "!x"),
    ("sci", "1.5e-3 * x + .5"),
    ("unicode minus", "\u2212x"),
    ("gt", "x > 1"),
    ("tab", "x\t* 2"),
    ("semicolon first", "; x"),
    ("keyword", "NULL"),
    ("inf", "Inf * x"),
    ("na", "NA_real_"),
    ("complex", "1i"),
    ("dots", "..."),
    ("formula", "~ x"),
    ("pipe", "x |> exp()"),
    ("lambda", "\\(z) z"),
    ("raw string", "r\"(abc)\""),
    ("utf8 name", "\u00e9 + x"),
    ("trailing op", "x +"),
    ("double star", "x ** 2"),
]


def xml_attr(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    return s.replace("\n", "&#10;").replace("\t", "&#9;")


def point_xml() -> str:
    guides = "".join(
        f'<functionGuide name="{xml_attr(n)}" value="{xml_attr(e)}"/>' for n, e in FIT_EXPRS
    )
    guides += '<functionGuide value="x / 2"/>'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="{VIZ}" lang="en">
  <sourceVariable id="source0_x" source="source0" sourceName="height" label=""/>
  <sourceVariable id="source0_y" source="source0" sourceName="weight" label="" shortLabel=""/>
  <graph>
    <point><x variable="source0_x"/><y variable="source0_y"/></point>
    {guides}
  </graph>
  <axis id="myaxisx"><label><text></text></label></axis>
</visualization>
"""


BOX_ES_REVIEW_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<visualization xmlns="{VIZ}">
  <embeddedSource id="es0"><names>Value;Category;Label;Tooltips;Value</names>
    <row>1,5;;;a;9</row><row>2;0;;b;9</row><row>x;1;;c;9</row><row>4;NA;;d;9</row>
  </embeddedSource>
  <sourceVariable id="cat" source="es0" sourceName="Category">
    <format><relabel from="" to="Empty"/><relabel from="0" to="Zero"/>
    <relabel from="0" to="Duplicate"/><relabel from="1"/><relabel to="orphan"/></format>
  </sourceVariable>
  <sourceVariable id="val" source="es0" sourceName="Value"/>
  <graph><schema><x variable="cat"/><y variable="val"/></schema></graph>
</visualization>
"""


def chart_data() -> bytes:
    return legacy_data(
        [
            (
                "source0",
                [
                    ("height", [150.0, 160.0, 170.0]),
                    ("weight", [55.0, 60.0, -DBL_MAX]),
                ],
            )
        ]
    )


def build_spv() -> None:
    NUL_LOG = "COMPUTE x = 1."
    write_zip(
        OUT / "spv_strings.spv",
        [
            ("outputViewer1.xml", doc(log_container("LOG ONE."))),
            (
                "outputViewer1_heading.xml",
                doc(
                    table_container("T1", "t1_lightTableData.bin", "Tie", None),
                    command="Tied",
                ),
            ),
            ("outputViewer9.xml", doc(log_container(NUL_LOG))),
            (
                "outputViewer10_heading.xml",
                doc(
                    title_container("Ten")
                    + "<heading><label>Sub</label>"
                    + table_container("T2", "t2_lightTableData.bin", None, "Nul Strings")
                    + table_container("T3", "t3_lightTableData.bin", "Bad", "Embedded")
                    + "</heading>"
                    + table_container("T4", "t4_lightTableData.bin", "After", "")
                    + '<container><label>No structure</label><vtb:table commandName="X"/>'
                    + "</container>",
                    command="Outer",
                ),
            ),
            ("outputViewer2_heading.xml", doc(log_container("   "))),
            (
                "outputViewer3_heading.xml",
                doc(
                    '<container><label>Log</label><vtx:text type="log" commandName="Log">'
                    "</vtx:text></container>"
                    + table_container("T5", "t5_lightTableData.bin", "NoLog", "Five")
                ),
            ),
            ("t1_lightTableData.bin", simple_table("one", 1.0)),
            ("t2_lightTableData.bin", nul_strings_table()),
            ("t3_lightTableData.bin", embedded_nul_table()),
            ("t4_lightTableData.bin", simple_table("four", 4.0)),
            ("t5_lightTableData.bin", simple_table("five", 5.0)),
        ],
    )

    write_zip(
        OUT / "spv_charts.spv",
        [
            (
                "outputViewer1_heading.xml",
                doc(
                    graph_container("c1_chartData.bin", "c1_chart.xml")
                    + graph_container("c2_chartData.bin", "c2_chart.xml", command=None)
                    + table_container("L1", "l1_tableData.bin", "Legacy", "EmptyId", "l1.xml")
                    + table_container("L2", "l2_tableData.bin", "Legacy", "", "l2.xml"),
                    command="Charts",
                ),
            ),
            ("c1_chartData.bin", chart_data()),
            ("c1_chart.xml", point_xml()),
            ("c2_chartData.bin", chart_data()),
            ("c2_chart.xml", BOX_ES_REVIEW_XML),
            ("l1_tableData.bin", legacy_small_data()),
            ("l1.xml", LEGACY_EMPTY_ID_XML),
            ("l2_tableData.bin", legacy_small_data()),
            ("l2.xml", LEGACY_OK_XML),
        ],
    )


# ---------------------------------------------------------------------------
# .omv / .jasp
# ---------------------------------------------------------------------------


def build_omv_jasp() -> None:
    meta = {
        "dataSet": {
            "rowCount": 4,
            "fields": [
                {
                    "name": "a",
                    "dataType": "Integer",
                    "measureType": "Nominal",
                    "labels": [],
                    "descriptionLong": "partial match",
                },
                {
                    "name": "t",
                    "dataType": "Text",
                    "description": "",
                    "title": "not used",
                },
                {"name": "d", "dataType": "Decimal", "measureType": "Continuous", "title": "d"},
                {"name": "n", "description": 5, "labels": [[1, "one"], [1, "uno"], [2, "two"]]},
            ],
        }
    }
    xdata = {"a": {"labels": [[0, "zero"], [1.5, "one and a half"], ["1e1", "ten"]]}}
    data = (
        struct.pack("<4i", 0, 1, 10, -2147483648)
        + struct.pack("<4i", -1, 0, 5, 2)
        + struct.pack("<4d", 0.5, float("inf"), -0.0, 1e-300)
        + struct.pack("<4i", 1, 2, 3, 2)
    )
    strings = b"x\x00\x00z"
    analyses = {
        "10 b": b"R jmv::late(x = 1)",
        "1 a": b"R jmv::fn\\(data = x)",
        "2 c": b"R jmv::nested(vars(a, b), f(g(1)))",
        "_u": b"R pkg.x::fn\t(a)",
        "B x": b"R pkg::fn  (a) pkg2::other(b)",
        "a x": b"R pkg::unbalanced(a",
    }
    members: list[tuple[str, bytes | str]] = [
        ("metadata.json", json.dumps(meta)),
        ("xdata.json", json.dumps(xdata)),
        ("data.bin", data),
        ("strings.bin", strings),
    ]
    members += [(f"{k}/analysis", v) for k, v in analyses.items()]
    members += [
        (
            "index.html",
            '<img src="/01 a/resources/p.png"><img src="01%20a/resources/p.png">'
            '<img src="01%20a/resources/p.png"><img src="01 a/resources/p.png">'
            '<img src="01%20a/resources/p.png%">'
            '<IMG Src="01 a/resources/Q.GIF"><img src="//cdn/x.png"><img src="HTTP://x/y.png">',
        ),
        ("01 a/resources/p.png", b"\x89PNGreview"),
        ("01 a/resources/Q.GIF", b"GIF89areview"),
    ]
    write_zip(OUT / "review.omv", members)
    write_zip(
        OUT / "empty_index.omv",
        [("metadata.json", json.dumps({"dataSet": {"fields": []}})), ("data.bin", b""), ("index.html", "")],
    )
    write_zip(OUT / "badurl.omv", [("index.html", '<p><img src="a%zz.png"></p>')])
    write_zip(
        OUT / "trailing_pct.omv",
        [("index.html", '<img src="p.png%"><img src="q.png%2">'), ("p.png", b"P"), ("q.png", b"Q")],
    )

    # binary .jasp: short data.bin (rowCount larger than the data), xdata
    # fallback when a field's own labels are empty, a title equal to the name.
    jmeta = {
        "dataSet": {
            "rowCount": 2,
            "fields": [
                {"name": "g", "measureType": "Nominal", "labels": [], "title": "g"},
                {"name": "s", "measureType": "Continuous", "title": 7},
            ],
        }
    }
    jx = {"g": {"labels": [[1, "A", True], [2, "", True], [3, "C", False]]}}
    write_zip(
        OUT / "review.jasp",
        [
            ("metadata.json", json.dumps(jmeta)),
            ("xdata.json", json.dumps(jx)),
            ("analyses.json", json.dumps({"analyses": [{"name": "t", "module": 3}, "x", 2.5]})),
            ("data.bin", struct.pack("<2i", 1, 3) + struct.pack("<2d", 1.25, float("nan"))),
            ("index.html", ""),
        ],
    )
    write_zip(
        OUT / "short.jasp",
        [
            ("metadata.json", json.dumps({"dataSet": {"rowCount": 4, "fields": jmeta["dataSet"]["fields"]}})),
            ("data.bin", struct.pack("<4i", 1, 2, 3, 1) + struct.pack("<2d", 1.0, 2.0)),
        ],
    )

    def sqlite_jasp(name: str, ctype: str | None) -> None:
        sq = OUT / "internal.sqlite"
        if sq.exists():
            sq.unlink()
        con = sqlite3.connect(sq)
        con.execute(
            "CREATE TABLE Columns (id INT, name TEXT, columnType TEXT, colIdx INT, title TEXT)"
        )
        con.execute(
            "INSERT INTO Columns VALUES (1,'grp',?,0,5),(2,'score','scale',1,'score'),"
            "(3,'only_dbl','nominal',2,'x'),(4,'missing','scale',3,NULL)",
            (ctype,),
        )
        con.execute(
            "CREATE TABLE DataSet_2 (rowNumber INT, Column_1_INT INT, Column_2_DBL REAL, "
            "Column_3_DBL REAL)"
        )
        con.execute("CREATE TABLE DataSet_1 (rowNumber INT, Column_1_INT INT)")
        con.execute(
            "INSERT INTO DataSet_2 VALUES (1,2,2.5,7.5),(0,1,1.5,-2147483648),(2,1,NULL,3)"
        )
        con.execute("CREATE TABLE Labels (columnId INT, value INT, ordering INT, label TEXT)")
        con.execute(
            "INSERT INTO Labels VALUES (1,2,1,'Two'),(1,1,0,NULL),(3,7,0,'seven')"
        )
        con.commit()
        con.close()
        write_zip(OUT / name, [("internal.sqlite", sq.read_bytes())])
        sq.unlink()

    sqlite_jasp("sqlite_review.jasp", "nominal")
    sqlite_jasp("sqlite_nulltype.jasp", None)


# ---------------------------------------------------------------------------
# .smcl / .out
# ---------------------------------------------------------------------------

SMCL = r"""{smcl}
{txt}{sf}{ul off}{.-}
      name:  {res}<unnamed>
{com}. display {res:{it:x}} and {ralign 8:abc}|{center 7:ab}|{rcenter:ab}|{lalign 5:toolongtext}|
{txt}{dup 3:-}{space 0}{space -2}{col 0}x{hline 0}{hline -3}{char 65}{char 300}{c 0x7b}{abc
{}{:x}}lone{c -(}brace{c )-} {c TT}{c 0x41}

{com}. summarize x y
  2. , detail
> nothing

{txt}    Variable {c |}        Obs        Mean    Std. dev.
{hline 13}{c +}{hline 44}
           x {c |}{res}         74    6165.257    2949.496
{txt}           y {c |}{res}         74     21.2973    5.785503
{txt}{hline 13}{c BT}{hline 44}

{com}. tabulate g
{txt}
            {c |}   Freq.  Freq.
{hline 12}{c +}{hline 20}
          a {c |}{res}       1      1
{txt}          b {c |}{res}       2      2
{txt}{hline 12}{c +}{hline 20}
      Total {c |}{res}       3      3

{com}. list
{txt}   Name   Kind
{hline 20}
   alpha  beta
{hline 20}

{com}. logit y x
{txt}Iteration 0:{space 3}log likelihood = {res}-45.03321
{txt}Iteration 1:{space 3}log likelihood = {res}-27.914626
{txt}Number of obs = {res}74{txt}  LR chi2(1) = {res}34.24{txt}  Prob > chi2 = {res}0.0000
{txt}t = 1.5, t = 2.5, t = 3.5
{com}.
{txt}end of do-file
{com}.
"""

MPLUS = """Mplus VERSION 8.6
MUTHEN & MUTHEN

INPUT INSTRUCTIONS

  TITLE: review;
  DATA: FILE = data.dat;

*** WARNING in MODEL command
  Something odd happened.

   summary of analysis

Number of groups                                                 1
Number of observations  2  45
  P-Value 0.05
  Loglikelihood  H0 Value   -1234.5
  Akaike (AIC)                    2468.9
  Estimate  0.100D-05
  90 Percent C.I.   0.000  0.123
  RMSEA (Root Mean Square Error Of Approximation)   0.05
  x_y  5
  Tab\tSeparated\t\t5
          Value                              5.887*
  Trailing spaces   3.5
  Hyphen-ated / slash.x%  -2

MODEL RESULTS

                                                    Two-Tailed
                    Estimate       S.E.  Est./S.E.    P-Value

Latent Class 1

 F1       BY
    Y1                 1.000      0.000    999.000    999.000
    Y2                 0.900      0.050     18.000      0.000

 Group G1
    Y3                 0.500      0.010     50.000      0.000

Within Level

 Residual Variances
    Y1                 0.300      0.020     15.000      0.000


                    Estimate       S.E.  Est./S.E.    P-Value
    Y4                 0.100      0.010     10.000      0.000

UNIVARIATE HIGHER-ORDER MOMENT DESCRIPTIVE STATISTICS

     Variable/         Mean/     Skewness/   Minimum/ % with                Percentiles
    Sample Size      Variance    Kurtosis    Maximum  Min/Max      20%/60%    40%/80%    Median

     Y1                   0.100      -0.200     -2.000    1.00%      -0.500      0.000      0.100
                1000.000       1.000       0.300      3.000    1.00%       0.200      0.700

SAMPLE STATISTICS

     Means
              Y1            Y2
              ________      ________
                0.100         0.200

     Covariances
              Y1            Y2
              ________      ________
 Y1             1.000
 Y2             0.500         1.000
"""


def build_text() -> None:
    (OUT / "tricky.smcl").write_text(SMCL, encoding="utf-8")
    (OUT / "tricky.out").write_text(MPLUS, encoding="utf-8")
    (OUT / "latin1.smcl").write_bytes(
        b"{smcl}\n{com}. summarize caf\xe9\n{txt}\n{hline 13}{c +}{hline 20}\n"
        b"    Variable {c |}        Obs\n{hline 13}{c +}{hline 20}\n"
        b"        caf\xe9 {c |}         74\n"
    )
    (OUT / "latin1.out").write_bytes(
        b"Mplus VERSION 8\nINPUT INSTRUCTIONS\n  DATA: FILE = C:\\Jos\xe9\\d.dat;\n\n"
        b"MODEL FIT INFORMATION\n\nNumber of Free Parameters   5\n"
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    build_spv()
    build_omv_jasp()
    build_text()


if __name__ == "__main__":
    main()
