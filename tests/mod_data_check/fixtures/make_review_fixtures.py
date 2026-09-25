"""Build the review fixture repositories of the data_check parity cases.

    .venv/bin/python tests/mod_data_check/fixtures/make_review_fixtures.py [OUT_DIR]

Writes ``fixtures/repos/review_*`` (or under OUT_DIR):

* ``review_types``: the column types R's readers produce (integer64,
  POSIXct at midnight, IDate, logical, scientific-notation doubles), a
  120-row file (preview truncation), duplicated and non-ASCII column names,
  a header-only file;
* ``review_qualtrics``: a one-header-row Qualtrics export (the reader types
  the dates), no Duration column, text statuses, PII fields;
* ``review_int64``: a survey whose participant ids are integer64, two
  straightliners;
* ``review_broken``: an unreadable workbook;
* ``review_stats`` / ``review_text`` / ``review_sheets``: copies of reader
  fixtures of ``tests/datacheck_files/data``.
"""

from __future__ import annotations

import random
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATACHECK = HERE.parents[1] / "datacheck_files" / "data"

TYPES_CSV = """id,score,big,small,flag,when,day,mixed,txt,neg
1,100000.5,100000,0.0001,TRUE,2020-01-01 00:00:00,2020-01-01,1,Apple ,-0.5
2,1e5,200000,0.00012345,FALSE,2020-01-02 10:30:00,2020-01-02,a,apple,-1
3,0.1,300000,1e-10,TRUE,2020-01-03 11:00:00,2020-01-03,3,banana,0
4,0.333333333333333333,123456789012,5,NA,,2020-01-04,,Banana,
5,2,1,2,TRUE,2020-01-05 12:00:00,,5,cherry,3
6,3,2,3,FALSE,2020-01-06 00:00:00,2020-01-06,6,cherry,4
"""

COPIES = {
    "review_stats": [
        "labelled.sav", "labelled.dta", "portable.por", "iris.sas7bdat", "labs.rds",
        "tibble.rds", "study.rds", "twoframes.rda", "workspace.RData",
    ],
    "review_text": [
        "dates.csv", "big_int.csv", "bom.csv", "na_strings.csv", "semicolon.csv",
        "single_col.csv", "tabbed.tsv", "whitespace.csv", "title_line.csv",
        "eprime_utf8.txt", "utf16_table.txt", "pipe.txt", "table.txt",
    ],
    "review_sheets": [
        "xl_dates.xlsx", "xl_bools.xlsx", "xl_mixed.xlsx", "xl_gaps.xlsx", "xl_sheets.xlsx",
        "xl_title.xlsx", "xl_numhdr.xlsx", "xl_header_only.xlsx", "typed.ods", "title.ods",
        "written.fods", "readxl_type-me.xls",
    ],
}  # fmt: skip


def _write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_bytes(data.encode("utf-8"))


def main(root: Path) -> None:
    types = root / "review_types" / "data"
    _write(types / "types.csv", TYPES_CSV)
    rows = ["id,cond,value"] + [f"{i},{'A' if i % 2 else 'B'},{i * 0.5}" for i in range(1, 121)]
    _write(types / "long.csv", "\n".join(rows) + "\n")
    _write(
        types / "Upper.csv",
        "B,a,_x,Z1,été\n1,2,,4,5\n2,,,5,6\n3,4,1,6,7\n,5,2,7,8\n5,6,3,8,9\n"
        "6,7,4,9,10\n7,8,5,10,11\n8,9,6,11,12\n",
    )
    _write(types / "dupnames.csv", "x,x,y\n1,2,3\n4,5,6\n7,8,9\n")
    _write(types / "headeronly.csv", "a,b,c\n")
    _write(
        root / "review_dupclass" / "data" / "dupclass.csv",
        "d,d,n\n" + "".join(f"2020-01-{i:02d},{i},{i * 3}\n" for i in range(1, 13)),
    )

    random.seed(7)
    hdr = [
        "StartDate", "EndDate", "Status", "Progress", "Finished", "RecordedDate", "ResponseId",
        "RecipientEmail", "ExternalReference", "RecipientFirstName",
        "q_1", "q_2", "q_3", "q_4", "q_5", "q_6",
    ]  # fmt: skip
    lines = [",".join(hdr)]
    secs_of = [30, 45, 600, 610, 620, 630, 640, 650, 660, 670, 680, 690, 700, 710]
    for i in range(1, 15):
        day = (i % 9) + 1
        start = f"2022-03-{day:02d} 00:00:00" if i in (2, 9) else f"2022-03-{day:02d} 09:{i:02d}:00"
        h, rem = divmod(9 * 3600 + i * 60 + secs_of[i - 1], 3600)
        m, s = divmod(rem, 60)
        end = f"2022-03-{day:02d} {h:02d}:{m:02d}:{s:02d}"
        if i == 2:
            end = f"2022-03-{day:02d} 00:05:00"
        if i == 5:  # ends before it starts
            end = f"2022-03-{day:02d} 08:00:00"
        status = {3: "Survey Preview", 7: "Spam"}.get(i, "IP Address")
        fin = "False" if i in (4, 11) else "True"
        prog = "" if i == 12 else ("75" if fin == "False" else "100")
        items = [str(random.randint(1, 5)) for _ in range(6)]
        rid, email, ref, name = (
            f"R_{i * 104729:010d}",
            f"p{i}@example.org",
            f"PANEL{i:03d}",
            f"Name{i}",
        )
        lines.append(
            ",".join([start, end, status, prog, fin, start, rid, email, ref, name, *items])
        )
    _write(root / "review_qualtrics" / "survey.csv", "\n".join(lines) + "\n")

    lines = ["participant,age,item_1,item_2,item_3,item_4,item_5,item_6"]
    for i in range(1, 36):
        pid = 100000000000 * (1 + i % 4) + (0 if i % 3 == 0 else i)
        if i in (4, 20):
            items = ["3"] * 6
        else:
            items = [str(random.randint(1, 5)) for _ in range(6)]
        lines.append(",".join([str(pid), str(18 + i), *items]))
    _write(root / "review_int64" / "survey.csv", "\n".join(lines) + "\n")
    _write(root / "review_broken" / "broken.xlsx", "this is not a workbook\n")

    for d, files in COPIES.items():
        (root / d).mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copyfile(DATACHECK / f, root / d / f)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "repos")
