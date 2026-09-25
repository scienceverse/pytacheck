"""Hand-built .xlsx edge cases for the spreadsheet inspector review cases.

    .venv/bin/python tests/mod_data_check/fixtures/make_review_xlsx.py

Writes ``review_xlsx/*.xlsx`` (zips of minimal OOXML parts, fixed timestamps):

* ``noname.xlsx``: the first ``<sheet>`` has no ``name`` attribute and a
  second worksheet has no ``<sheet>`` entry at all (its file stem is used);
  cells without an ``r`` reference; seven merged ranges; rgb, theme and
  black/white fills.
* ``gaps.xlsx``: blank rows between populated rows, a header with a gap, a
  column with a header and nothing below it, inline strings.
* ``edge.fods``: a nameless table, a table nested in a cell, repeated rows
  and columns (a sheet-limit padding run), covered cells, row and column
  spans, a coloured empty run, text-only cells and value-type-only cells.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent / "review_xlsx"
NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R_NS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="xml" ContentType="application/xml"/>
</Types>"""

STYLES = f"""<?xml version="1.0" encoding="UTF-8"?>
<styleSheet {NS}>
<fills count="6">
<fill><patternFill patternType="none"/></fill>
<fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFFF0000"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor theme="4"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="ffffffff"/></patternFill></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FF00B050"/></patternFill></fill>
</fills>
<cellXfs count="6">
<xf fillId="0"/><xf fillId="2"/><xf fillId="3"/><xf fillId="4"/><xf fillId="5"/><xf fillId="x"/>
</cellXfs>
</styleSheet>"""


def sheet(rows: list[str], merges: list[str] | None = None) -> str:
    m = ""
    if merges:
        m = (
            f'<mergeCells count="{len(merges)}">'
            + "".join(f'<mergeCell ref="{r}"/>' for r in merges)
            + "</mergeCells>"
        )
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<worksheet {NS}><sheetData>{"".join(rows)}</sheetData>{m}</worksheet>'


def write(name: str, parts: dict[str, str]) -> None:
    HERE.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(HERE / name, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, text in parts.items():
            info = zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text)


def main() -> None:
    wb = f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook {NS} {R_NS}><sheets><sheet sheetId="1" r:id="rId1"/></sheets></workbook>"""
    s1 = sheet(
        [
            '<row r="1"><c r="A1" t="inlineStr"><is><t>id</t></is></c>'
            '<c r="B1" s="1" t="inlineStr"><is><t>score</t></is></c>'
            '<c r="C1" s="2"><v>3</v></c><c r="D1" s="3"/></row>',
            '<row r="2"><c r="A2"><v>1</v></c><c r="B2" s="4"><v>2</v></c>'
            '<c s="1"><v>9</v></c></row>',
            '<row r="4"><c r="A4"><v>2</v></c><c r="B4" s="5"><v>5</v></c></row>',
        ],
        merges=["A1:B1", "C3:D3", "A5:A6", "B5:C5", "D5:E6", "F1:G1", "H2:H9"],
    )
    s2 = sheet(['<row r="1"><c r="A1"><v>1</v></c></row>'])
    write(
        "noname.xlsx",
        {
            "[Content_Types].xml": CONTENT_TYPES,
            "xl/workbook.xml": wb,
            "xl/styles.xml": STYLES,
            "xl/worksheets/sheet1.xml": s1,
            "xl/worksheets/sheet2.xml": s2,
        },
    )
    wb2 = f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook {NS} {R_NS}><sheets><sheet name="Data" sheetId="1" r:id="rId1"/>
<sheet name="Other" sheetId="2" r:id="rId2"/></sheets></workbook>"""
    g1 = sheet(
        [
            '<row r="2"><c r="A2" t="inlineStr"><is><t>a</t></is></c>'
            '<c r="C2" t="inlineStr"><is><t>c</t></is></c>'
            '<c r="D2" t="inlineStr"><is><t>d</t></is></c></row>',
            '<row r="3"><c r="A3"><v>1</v></c><c r="B3"><v>2</v></c><c r="C3"><v>3</v></c></row>',
            '<row r="4"/>',
            '<row r="7"><c r="A7"><v>4</v></c><c r="E7"/></row>',
        ]
    )
    g2 = sheet(['<row r="1"><c r="A1"><v>1</v></c></row>'])
    g10 = sheet(['<row r="3"><c r="B3"><v>7</v></c></row>'])
    write(
        "gaps.xlsx",
        {
            "[Content_Types].xml": CONTENT_TYPES,
            "xl/workbook.xml": wb2,
            "xl/worksheets/sheet1.xml": g1,
            "xl/worksheets/sheet10.xml": g10,
            "xl/worksheets/sheet2.xml": g2,
        },
    )
    write_fods()


FODS = """<?xml version="1.0" encoding="UTF-8"?>
<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
 office:version="1.3" office:mimetype="application/vnd.oasis.opendocument.spreadsheet">
<office:automatic-styles>
<style:style style:name="ce1" style:family="table-cell"><style:table-cell-properties fo:background-color="#ffff00"/></style:style>
<style:style style:name="ce2" style:family="table-cell"><style:table-cell-properties fo:background-color="#FFFFFF"/></style:style>
<style:style style:name="ce3" style:family="table-cell"><style:table-cell-properties fo:border="0.5pt solid #000000"/></style:style>
<style:style style:name="ce4" style:family="table-column"><style:table-cell-properties fo:background-color="#00ff00"/></style:style>
</office:automatic-styles>
<office:body><office:spreadsheet>
<table:table>
<table:table-row>
<table:table-cell office:value-type="string"><text:p>id</text:p></table:table-cell>
<table:table-cell table:number-columns-spanned="2" office:value-type="string"><text:p>score</text:p></table:table-cell>
<table:covered-table-cell/>
<table:table-cell table:style-name="ce1" table:number-columns-repeated="3"/>
<table:table-cell table:number-columns-repeated="16000"/>
</table:table-row>
<table:table-row table:number-rows-repeated="2">
<table:table-cell office:value-type="float" office:value="1"/>
<table:table-cell table:style-name="ce1" table:number-columns-repeated="2"><text:p>  x </text:p></table:table-cell>
<table:table-cell table:style-name="ce2"><text:p>w</text:p></table:table-cell>
</table:table-row>
<table:table-row table:number-rows-repeated="3"><table:table-cell table:number-columns-repeated="1024"/></table:table-row>
<table:table-row>
<table:table-cell table:number-rows-spanned="2" table:number-columns-spanned="3"><text:p>big</text:p>
<table:table table:name="Nested"><table:table-row><table:table-cell><text:p>in</text:p></table:table-cell></table:table-row></table:table>
</table:table-cell>
<table:table-cell table:style-name="ce3"><text:p> </text:p></table:table-cell>
</table:table-row>
<table:table-row table:number-rows-repeated="1048570"><table:table-cell table:number-columns-repeated="1024"/></table:table-row>
</table:table>
<table:table table:name="Second">
<table:table-row><table:table-cell table:number-columns-repeated="2" office:value-type="float" office:value="5"/></table:table-row>
</table:table>
<table:table table:name=""/>
</office:spreadsheet></office:body></office:document>
"""


def write_fods() -> None:
    (HERE / "edge.fods").write_text(FODS, encoding="utf-8")


if __name__ == "__main__":
    main()
