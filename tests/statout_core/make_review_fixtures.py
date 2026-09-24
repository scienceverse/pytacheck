"""Regenerate the review fixtures under ``tests/statout_core/data/review_*``.

Run with ``python -m tests.statout_core.make_review_fixtures``. The archives
are committed; this script documents how they were built (hand-crafted
protobuf blobs, odd encodings, duplicate JSON keys, ...).
"""

from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
SAMPLE = Path(__file__).resolve().parents[2] / "upstream/metacheck/tests/testthat/fixtures/formats"


# -- minimal protobuf writer ---------------------------------------------------


def varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def key(field: int, wire: int) -> bytes:
    return varint((field << 3) | wire)


def ld(field: int, payload: bytes | str) -> bytes:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return key(field, 2) + varint(len(payload)) + payload


def vi(field: int, n: int) -> bytes:
    return key(field, 0) + varint(n)


def f64(field: int, x: float) -> bytes:
    return key(field, 1) + struct.pack("<d", x)


def f32(field: int, x: float) -> bytes:
    return key(field, 5) + struct.pack("<f", x)


def cell_d(x: float) -> bytes:
    return f64(2, x)


def cell_i(n: int) -> bytes:
    return vi(1, n)


def cell_s(s: bytes | str) -> bytes:
    return ld(3, s)


def cell_o() -> bytes:
    return vi(4, 1)


def column(name: str, cells: list[bytes], title: str = "", typ: str = "", fmt: str = "") -> bytes:
    out = ld(1, name) if name else b""
    if title:
        out += ld(2, title)
    if typ:
        out += ld(3, typ)
    if fmt:
        out += ld(4, fmt)
    for c in cells:
        out += ld(7, c)
    return out


def table(cols: list[bytes]) -> bytes:
    return b"".join(ld(1, c) for c in cols)


def element(
    title: str = "",
    tbl: bytes | None = None,
    group: list[bytes] | None = None,
    array: list[bytes] | None = None,
) -> bytes:
    out = ld(1, "el")
    if title:
        out += ld(2, title)
    if tbl is not None:
        out += ld(6, tbl)
    if group is not None:
        out += ld(8, b"".join(ld(1, e) for e in group))
    if array is not None:
        out += ld(9, b"".join(ld(1, e) for e in array))
    return out


def response(name: str, results: bytes, aid: int | bytes | None = None) -> bytes:
    out = b""
    if isinstance(aid, int):
        out += vi(2, aid)
    elif isinstance(aid, bytes):
        out += ld(2, aid)
    if name:
        out += ld(3, name)
    return out + ld(7, results)


def write_zip(path: Path, members: list[tuple[str, bytes | str]]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            z.writestr(name, data)


def main() -> None:
    # 1. jamovi archive: odd folder numbering, every cell kind, nested elements.
    t_basic = table(
        [
            column("", [cell_s("a")], title="Group"),
            column("stat", [cell_d(2.5), cell_i(-3), cell_i(100000), cell_o(), b""], typ="number"),
            column(
                "p",
                [cell_d(0.000632), cell_d(float("inf")), cell_s(b"x\x00"), cell_s(b"\x00\x00")],
                fmt="pvalue",
            ),
            column("p", [cell_s("< .001")], typ="text"),
            column("", [cell_s("dropped")]),
        ]
    )
    t_wide = table(
        [
            column("stat[n]", [cell_s("N")]),
            column("v1[n]", [cell_i(10)]),
            column("v1[mean]", [cell_d(1.25)]),
            column("v1[sd]", [cell_d(0.5)]),
            column("v2[n]", [cell_i(12)]),
            column("v2[mean]", [cell_d(2.5)]),
            column("v2[sd]", [cell_d(0.75)]),
        ]
    )
    t_f32 = table([column("x", [f32(2, 1.5)]), column("y", [cell_d(-0.0)])])
    r1 = element(
        title="Outer",
        group=[element(title="Inner", tbl=t_basic), element(array=[element(tbl=t_wide)])],
    )
    r2 = element(title="", tbl=t_f32)
    write_zip(
        DATA / "review_order.omv",
        [
            ("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n"),
            ("10 later/analysis", response("later", r2, aid=10)),
            ("9 ttest/analysis", response("ttest", r1, aid=b"\x09")),
            ("abc/analysis", response("", r2)),
            (" 2 first/analysis", response("first", r2, aid=100000)),
            ("007 seven/analysis", response("seven", element(tbl=t_basic), aid=b"ab")),
            ("08 broken/analysis", b"\x0a\x05ab"),
            ("empty/analysis", b""),
        ],
    )

    # 2. rendered HTML without a charset declaration (UTF-8 bytes).
    html = (
        "<html><body><h2>Tést α</h2><table>"
        '<tr><th colspan="3">Café table</th></tr>'
        "<tr><th></th><th>éffect</th><th>p</th></tr>"
        "<tr><td>−x</td><td>−0.5</td><td>.03</td></tr>"
        "</table></body></html>"
    )
    write_zip(DATA / "review_nometa.jasp", [("index.html", html.encode("utf-8"))])
    latin = (
        b'<html><head><meta charset="iso-8859-1"></head><body><h2>T\xe9st</h2><table>'
        b"<tr><th>a</th><th>b\xe9</th></tr><tr><td>1</td><td>2</td></tr></table></body></html>"
    )
    write_zip(DATA / "review_latin.jasp", [("index.html", latin)])

    # 3. analyses.json with a UTF-8 BOM (jsonlite accepts it with a warning).
    with zipfile.ZipFile(SAMPLE / "sample.jasp") as z:
        aj = z.read("analyses.json")
        idx = z.read("index.html")
    write_zip(
        DATA / "review_bom.jasp", [("analyses.json", b"\xef\xbb\xbf" + aj), ("index.html", idx)]
    )

    # 4. duplicate JSON keys: R keeps both, `$`/`[[` return the FIRST.
    dup = (
        '{"analyses": [{"id": 3, "id": 4, "title": "First", "title": "Second", "name": "X",'
        ' "results": {"t": {"title": "T1", "title": "T2", "schema": {"fields": ['
        '{"name": "t", "name": "u"}, {"name": "p"}]}, "data": [{"t": 1.5, "t": 2.5, "u": 9,'
        ' "p": 0.04}]}}}]}'
    )
    write_zip(DATA / "review_dupkeys.jasp", [("analyses.json", dup), ("index.html", idx)])

    write_zip(
        DATA / "review_tables.jasp", [("index.html", (DATA / "review_tables.html").read_bytes())]
    )

    # 5. zips without any extractable file: R's unzip() returns NULL.
    write_zip(DATA / "review_empty.jasp", [])
    with zipfile.ZipFile(DATA / "review_dirs_only.omv", "w") as z:
        z.writestr("05 ttestOneS/", "")

    # 6. notebooks: BOM, duplicate keys, non-character text arrays.
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": 1,
                "outputs": [
                    {
                        "output_type": "stream",
                        "name": "stdout",
                        "text": ["TtestResult(statistic=np.float64(2.5), pvalue=0.03, df=10)\n"],
                    }
                ],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    (DATA / "review_bom.ipynb").write_bytes(b"\xef\xbb\xbf" + json.dumps(nb).encode("utf-8"))
    dup_nb = (
        '{"cells": [{"cell_type": "code", "execution_count": 2, "execution_count": 3,'
        ' "outputs": [{"output_type": "stream", "name": "stdout", "text": "t = 1.5, p = 0.2\\n"}],'
        ' "outputs": [{"output_type": "stream", "name": "stderr", "text": "z = 9, p = 0.9\\n"}]}],'
        ' "nbformat": 4}'
    )
    (DATA / "review_dupkeys.ipynb").write_text(dup_nb, encoding="utf-8")
    mixed = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": [7],
                "outputs": [
                    {"output_type": "stream", "text": ["t = ", 2.5, ", p = ", 0.04, "\n"]},
                    {"output_type": "stream", "name": "stdout", "text": [1, True, 2.5]},
                    {
                        "output_type": "execute_result",
                        "data": {"text/plain": [100000, "\n", 1e-20]},
                    },
                    {
                        "output_type": "display_data",
                        "data": {"text/html": ["x > y"], "text/plain": ["F = 3.2, p = .01"]},
                    },
                ],
            },
            {"cell_type": "markdown", "source": "t = 1"},
            {
                "cell_type": "code",
                "execution_count": None,
                "outputs": [
                    {
                        "output_type": "execute_result",
                        "data": {
                            "text/html": "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td></td></tr>"
                            "<tr><td></td></tr></table>"
                        },
                    }
                ],
            },
        ],
    }
    (DATA / "review_mixed_types.ipynb").write_text(json.dumps(mixed), encoding="utf-8")

    # 7. stat_output_validate() inputs.
    (DATA / "review_validate_null.json").write_text("null", encoding="utf-8")
    doc = {
        "schema": "s",
        "schema_version": "1",
        "paper_id": "p",
        "source_file": "f",
        "source_format": "R",
        "analyses": [
            {"analysis": "a", "results": [{"result_id": "r1", "values": {"t": {"value": 1}}}]}
        ],
    }
    (DATA / "review_validate_bom.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps(doc).encode("utf-8")
    )
    partial = (
        '{"schema": "s", "schema_version": "1", "paper_id": "p", "source_file": "f",'
        ' "source_format": "R", "analysesX": [{"analysis_id": 1, "resultsZ": [{"result_idx": "r1",'
        ' "valuesQ": {"t": {"values": 1}, "p": {"val": 2}, "d": {"value": 3, "value": null}}}]}]}'
    )
    (DATA / "review_validate_partial.json").write_text(partial, encoding="utf-8")
    dup_doc = (
        '{"schema": "s", "schema_version": "1", "paper_id": "p", "source_file": "f",'
        ' "source_format": "R", "analyses": [{"analysis": "a", "results": [{"result_id": "r1",'
        ' "values": {"t": {"x": 1}, "t": {"value": 2}, "p": {"value": 1}, "p": {"x": 2}}}]}],'
        ' "analyses": []}'
    )
    (DATA / "review_validate_dupkeys.json").write_text(dup_doc, encoding="utf-8")


if __name__ == "__main__":
    main()
