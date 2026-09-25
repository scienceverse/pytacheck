"""Text fixtures for parity/cases/datacheck_columns_review.yaml.

Run from the repository root:
    .venv/bin/python tests/datacheck_columns/fixtures/review/make_review_fixtures.py
(binary fixtures -- sav/xlsx/ods -- are written by make_review_fixtures.R)
"""

from __future__ import annotations

import json
from pathlib import Path

D = Path(__file__).resolve().parent
BOM = b"\xef\xbb\xbf"


def write(name: str, data: str | bytes) -> None:
    (D / name).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


# JSON with a non-standard NaN literal: jsonlite refuses to parse it
write("nan_literal.json", '[{"name": "a", "label": "A"}, {"name": "b", "label": "B", "n": NaN}]\n')
write("nan_literal.csv", '[{"name": "a", "label": "A"}, {"name": "b", "label": "B", "n": NaN}]\n')

# a BOM before JSON shipped with a .csv extension
write("bom_json.csv", BOM + b'[{"name": "a", "label": "A"}, {"name": "b", "label": "B"}]\n')

# a BOM before a markdown pipe table on the very first line
write("bom_table.md", BOM + b"| variable | description |\n|---|---|\n| a | Alpha |\n| b | Beta |\n")

# several tables: prose table (no codebook header), then a ragged codebook table
write(
    "multi_table.md",
    "# Data\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nText between.\n\n"
    "|Variable Name | | Label | Values |\n|:---|---|---:|---|\n"
    "| x1 | skip | First item | 1 = yes; 2 = no |\n"
    "| x2 | | Second item |\n"
    "| x1 | | | |\n"
    "| x3 | | Third | 1 (low) to 5 (high) | extra |\n"
    "|  | | orphan label | |\n"
    "not a table line\n| y | z |\n",
)

# declared-missing column shapes, a question column, coding instructions
write(
    "missing_cols.csv",
    "variable,label,values,missing values,question text,derivation\n"
    'a,Alpha,"1 = yes; 2 = no; 99 = refused",-9 = not answered,What is a?,mean of a1-a3\n'
    'b,Beta,"Male = 1, Female = 2",-99; -98,,\n'
    "c,Gamma,,N/A,  ,recoded\n"
    'd,Delta,"M = Male; F = Female","-7, x, -8",Delta?,\n'
    "a,,,,,\n"
    'e,Eps,"1 = Strongly disagree | 5 = Strongly agree",refused = -1,,\n',
)

# numeric variable names and a label column of numbers
write("numeric_vars.csv", "code,description\n1,10\n2,20\n2.50,30\n007,\n")

# semicolon delimiter with a header on row 3 and a stats column
write(
    "title_semicolon.csv",
    "Codebook v2;;\n;;\nName;Label;Mean\nage;Age in years;31.5\nrt;Reaction time;512\n",
)

# JSON codebook value-label shapes
write(
    "values_shapes.json",
    json.dumps(
        {
            "Variables": [
                {
                    "Name": "a",
                    "Label": "Alpha",
                    "Values": [
                        {"value": 1, "label": "yes"},
                        {"value": 2, "label": ""},
                        {"label": "no code"},
                        None,
                        "3 = maybe",
                    ],
                },
                {
                    "name": "b",
                    "question": "What is b?",
                    "levels": {"1": "low", "2": None, "3": "high"},
                },
                {"name": "c", "description": "Gamma", "categories": ["1 = x; 2 = y", 5, True]},
                {
                    "name": "d",
                    "title": "  ",
                    "item_text": "Item d",
                    "value_labels": {"1": ["a", "b"]},
                },
                {
                    "id": 7,
                    "label": 3.25,
                    "values": [{"code": "9", "meaning": "Refused"}, {"code": "1", "text": "One"}],
                },
                {"name": "", "label": "no name"},
                ["not", "an", "entry"],
            ]
        }
    ),
)

# Qualtrics edge cases
qsf = {
    "SurveyEntry": {"SurveyName": "edge"},
    "SurveyElements": [
        None,
        {"Element": "BL", "Payload": []},
        {
            "Element": "SQ",
            "Payload": {
                "DataExportTag": "M1",
                "QuestionType": ["Matrix"],
                "Selector": "Likert",
                "QuestionText": [None, "ignored"],
                "Choices": {
                    "1": {"Display": "Item <b>one</b>"},
                    "2": {"DisplayLogic": {"0": {"x": 1, "y": "z"}}},
                    "": {"Display": "blank key"},
                    "3": {"Display": {"a": [True, 2]}},
                },
                "Answers": {"1": {"Display": "Disagree"}, "2": {"Display": "Agree &amp;lt;3"}},
                "ChoiceDataExportTags": {"1": "M1_a", "2": 5, "3": ["x"]},
            },
        },
        {
            "Element": "SQ",
            "Payload": {
                "DataExportTag": 42,
                "QuestionType": "MC",
                "Selector": "MAVR",
                "QuestionText": [["nested", 1]],
                "Choices": {"1": {"Display": "A"}, "4": {"display": "lower-case display"}},
                "ChoiceDataExportTags": False,
            },
        },
        {
            "Element": "SQ",
            "Payload": {
                "DataExportTag": ["Q7"],
                "QuestionType": "MC",
                "Selector": "SAVR",
                "QuestionText": {"t": "Object text"},
                "Choices": [{"Display": "array choice"}],
            },
        },
        {
            "Element": "SQ",
            "Payload": {
                "DataExportTag": None,
                "QuestionID": "QID9",
                "QuestionType": "Matrix",
                "QuestionText": "Matrix with array choices",
                "Choices": [{"Display": "x"}],
            },
        },
        {
            "Element": "SQ",
            "Payload": {
                "DataExportTag": "TE",
                "QuestionType": "TE",
                "QuestionText": 12.5,
                "Choices": {},
            },
        },
    ],
}
write("edge.qsf", json.dumps(qsf))

# a list-valued DataExportTag: R errors inside parse_qsf
qsf_err = {
    "SurveyElements": [
        {"Element": "SQ", "Payload": {"DataExportTag": ["a", "b"], "QuestionText": "x"}}
    ]
}
write("tag_list.qsf", json.dumps(qsf_err))
# an empty-array DataExportTag: `FALSE || logical(0)` is NA inside if()
write(
    "tag_empty.qsf",
    json.dumps(
        {
            "SurveyElements": [
                {"Element": "SQ", "Payload": {"DataExportTag": [], "QuestionText": "x"}}
            ]
        }
    ),
)


# -- Word documents built from the officer-written codebook.docx: officer's
#    docx_summary() orders a run's contents by their document-wide index as
#    TEXT ("10" < "9"), and joins runs outside any paragraph (an inline
#    content control) to the table cells tidyr::complete() invents.
def _docx(name: str, body: str) -> None:
    import zipfile

    src = D.parent / "codebook.docx"
    with zipfile.ZipFile(src) as zin:
        doc = zin.read("word/document.xml").decode("utf-8")
        head = doc[: doc.index("<w:body>") + len("<w:body>")]
        tail = doc[doc.index("<w:sectPr") :]
        with zipfile.ZipFile(D / name, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "word/document.xml":
                    data = (head + body + tail).encode("utf-8")
                zout.writestr(item, data)


def _p(*runs: str) -> str:
    return "<w:p>" + "".join(runs) + "</w:p>"


def _r(t: str) -> str:
    return f'<w:r><w:t xml:space="preserve">{t}</w:t></w:r>'


def _tc(*paras: str, pr: str = "") -> str:
    return f"<w:tc>{pr}" + "".join(paras) + "</w:tc>"


_docx(
    "run_order.docx",
    "".join(_p(_r(f"p{i}")) for i in range(1, 8))
    + _p("<w:r><w:t>a</w:t><w:tab/><w:t>b</w:t><w:br/><w:t>c</w:t></w:r>")
    + _p("<w:r><w:rPr><w:b/></w:rPr><w:t>x</w:t></w:r>"),
)
_docx(
    "tricky.docx",
    "".join(
        [
            _p(_r("Title line")),
            _p(
                _r("var1"),
                "<w:r><w:tab/></w:r>",
                _r("First variable"),
                "<w:r><w:br/></w:r>",
                _r("cont"),
            ),
            _p(
                '<w:hyperlink r:id="rId999"><w:r><w:t>linked text</w:t></w:r></w:hyperlink>',
                _r(" after link"),
            ),
            _p(
                '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="separate"/></w:r>',
                _r("7"),
                '<w:r><w:fldChar w:fldCharType="end"/></w:r>',
                _r(" field done"),
            ),
            _p(
                '<w:ins w:id="1" w:author="a"><w:r><w:t>inserted</w:t></w:r></w:ins>',
                '<w:del w:id="2" w:author="a"><w:r><w:delText>deleted</w:delText></w:r></w:del>',
                _r(" tracked"),
            ),
            _p(
                "<w:r><w:t>soft</w:t><w:softHyphen/><w:t>hy</w:t><w:noBreakHyphen/><w:t>phen</w:t></w:r>",
                '<w:r><w:sym w:font="Symbol" w:char="F0B7"/></w:r>',
            ),
            _p("<w:sdt><w:sdtContent><w:r><w:t>in sdt</w:t></w:r></w:sdtContent></w:sdt>"),
            _p("<w:smartTag><w:r><w:t>smart</w:t></w:r></w:smartTag>", _r(" tag")),
            "<w:tbl><w:tblPr/><w:tblGrid><w:gridCol/><w:gridCol/></w:tblGrid>"
            "<w:tr>"
            + _tc(_p(_r("code")), _p(_r("second para")))
            + _tc(_p(_r("meaning")))
            + "</w:tr>"
            "<w:tr>"
            + _tc(_p(_r("merged across")), pr='<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>')
            + "</w:tr>"
            "<w:tr>"
            + _tc(_p(_r("top")), pr='<w:tcPr><w:vMerge w:val="restart"/></w:tcPr>')
            + _tc(_p(_r("r3c2")))
            + "</w:tr>"
            "<w:tr>"
            + _tc(_p(), pr="<w:tcPr><w:vMerge/></w:tcPr>")
            + _tc(
                _p(_r("r4c2")),
                "<w:tbl><w:tr>" + _tc(_p(_r("nested"))) + "</w:tr></w:tbl>",
                _p(_r("after nested")),
            )
            + "</w:tr>"
            "<w:tr>" + _tc(_p()) + _tc(_p(_r("   "))) + "</w:tr>"
            "</w:tbl>",
            _p(_r("Closing paragraph")),
            _p("<w:r><w:t>  </w:t></w:r>"),
            _p("<w:r><w:cr/><w:t>after cr</w:t></w:r>"),
        ]
    ),
)
_docx(
    "orphan_no_table.docx",
    _p(_r("age: participant age"))
    + _p(
        "<w:sdt><w:sdtContent><w:r><w:t>orphan run</w:t></w:r></w:sdtContent></w:sdt>", _r(" tail")
    )
    + _p(_r("sex: participant sex")),
)

# RTF: control words with numeric args, escaped braces/backslashes, hex and
# unicode escapes, destinations, CRLF line ends
write(
    "escapes.rtf",
    b"{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Times;}}{\\*\\generator Riched20;}\r\n"
    b"\\pard\\f0\\fs24 age:\\tab Age in years\\par\r\n"
    b"sex: 1 = m\\'e9le; 2 = f\\u233?male\\par\r\n"
    b"brace \\{ and \\} and \\\\ backslash\\line next\r\n"
    b"\\b bold\\b0  text\\-hyphen\\~nbsp}\r\n",
)


# OpenDocument spreadsheet written by hand: a blank first sheet, then a
# sheet with a leading blank row, repeated columns, multi-paragraph cells,
# <text:s/> runs, a float cell with display text, and trailing blanks.
def _ods(name: str, tables: str) -> None:
    import zipfile

    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" office:version="1.2">'
        "<office:body><office:spreadsheet>" + tables + "</office:spreadsheet></office:body>"
        "</office:document-content>"
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    )
    with zipfile.ZipFile(D / name, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("META-INF/manifest.xml", manifest, zipfile.ZIP_DEFLATED)
        z.writestr("content.xml", content, zipfile.ZIP_DEFLATED)


def _cell(text: str = "", extra: str = "") -> str:
    if not text:
        return f"<table:table-cell{extra}/>"
    return f'<table:table-cell office:value-type="string"{extra}><text:p>{text}</text:p></table:table-cell>'


_ods(
    "handmade.ods",
    '<table:table table:name="Blank"><table:table-row><table:table-cell/></table:table-row></table:table>'
    '<table:table table:name="Codebook">'
    '<table:table-row table:number-rows-repeated="1">'
    + _cell(extra=' table:number-columns-repeated="3"')
    + "</table:table-row>"
    "<table:table-row>"
    + _cell("Variable<text:s/>Name")
    + _cell()
    + _cell("Label")
    + _cell("Values")
    + "</table:table-row>"
    "<table:table-row>" + _cell("q1") + _cell() + '<table:table-cell office:value-type="string">'
    "<text:p>First line</text:p><text:p>second line</text:p></table:table-cell>"
    + _cell('1 = no;<text:s text:c="2"/>2 = yes')
    + "</table:table-row>"
    "<table:table-row>"
    + _cell("q2")
    + _cell("x", ' table:number-columns-repeated="1"')
    + '<table:table-cell office:value-type="float" office:value="0.5"><text:p>50%</text:p>'
    "</table:table-cell>" + _cell() + "</table:table-row>"
    '<table:table-row table:number-rows-repeated="2">' + _cell() + "</table:table-row>"
    "<table:table-row>" + _cell("q3") + _cell() + _cell("Third") + "</table:table-row>"
    "</table:table>",
)

# RTF with raw cp1252 bytes (invalid UTF-8): R's gsub() fails on it
write("latin1.rtf", b"{\\rtf1\\ansi age:\\tab Alter in Jahren\\par caf\xe9 = coffee shop\\par}\r\n")
# markdown with an invalid UTF-8 byte inside the codebook table
write("latin1_table.md", b"| name | label |\n|---|---|\n| a | caf\xe9 au lait |\n| b | B\xff |\n")
# JSON codebook / QSF with a raw latin1 byte inside a string
write("latin1.json", b'[{"name": "a", "label": "caf\xe9"}, {"name": "b", "label": "B"}]\n')
write(
    "latin1.qsf",
    b'{"SurveyElements": [{"Element": "SQ", "Payload": {"DataExportTag": "Q1", '
    b'"QuestionText": "caf\xe9?"}}]}',
)
