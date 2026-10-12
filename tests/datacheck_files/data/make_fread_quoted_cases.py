"""Write fread_quoted_cases.json: quote-heavy delimited files for test_delim.py.

Run from the repository root, then record R's answers with
``Rscript tests/datacheck_files/data/make_fread_ref.R fread_quoted`` (see that
script). The files look like what data_check meets in practice -- every field
quoted (Qualtrics, ``csv.QUOTE_ALL``), strings quoted and numbers / ``NA`` not
(``write.csv()``) -- with separators and doubled quotes inside quoted fields,
quoted numbers and ``"NA"``, CRLF line ends, and now and then a line that
quote rule 0 cannot read simply (blanks around quotes, a quoted field running
on to the next line, a stray quote, a short row), so the emulator's fast
row splitter hands over to the general tokenizer part-way through a file.
"""

from __future__ import annotations

import base64
import json
import random
from pathlib import Path

OUT = Path(__file__).parent / "fread_quoted_cases.json"

_TEXT = ["abc", "de f", "x y z", "café", "", "NA", "TRUE", "2020-01-02", " pad "]
_NUM = ["1", "2", "-3", "4.5", "0.25", "1e5", "007", "NA", ""]


def _quoted(r: random.Random, sep: str) -> str:
    k = r.random()
    if k < 0.15:
        inner = f"a{sep}b"
    elif k < 0.25:
        inner = r.choice(['say ""hi""', '""', 'q""', '""q'])
    elif k < 0.45:
        inner = r.choice(_NUM)
    else:
        inner = r.choice(_TEXT)
    return f'"{inner}"'


def _nasty(r: random.Random, sep: str) -> str:
    return r.choice(
        [' "padded"', '"trail" ', 'mid"quote', '"a"b', '"multi\nline"', '"""', '"', f'"x{sep}']
    )


def _file(r: random.Random) -> tuple[str, str]:
    sep = r.choice([",", ",", ",", ";", "\t", "|"])
    ncol = r.randint(2, 6)
    style = r.choice(["all", "all", "rstyle", "mixed"])
    kinds = [r.choice(["num", "text"]) for _ in range(ncol)]
    lines = [sep.join(f'"v{j}"' if style != "mixed" or r.random() < 0.5 else f"v{j}"
                      for j in range(ncol))]  # fmt: skip
    for _ in range(r.randint(15, 60)):
        fields = []
        for kind in kinds:
            if style == "all":
                f = _quoted(r, sep)
            elif style == "rstyle":
                v = r.choice(_NUM) if kind == "num" else None
                if v is not None:
                    f = "NA" if v in ("NA", "") else v
                else:
                    f = "NA" if r.random() < 0.1 else _quoted(r, sep)
            else:
                f = _quoted(r, sep) if r.random() < 0.5 else r.choice([*_NUM, "abc", "NA"])
            fields.append(f)
        if r.random() < 0.02:
            fields[r.randrange(ncol)] = _nasty(r, sep)
        if r.random() < 0.01:
            fields = fields[:-1]
        lines.append(sep.join(fields))
    eol = r.choice(["\n", "\n", "\r\n"])
    return eol.join(lines) + (eol if r.random() < 0.85 else ""), sep


def main() -> None:
    r = random.Random(20260925)
    cases = []
    for i in range(80):
        text, sep = _file(r)
        nrows = None if r.random() < 0.8 else r.choice([1, 5, 30])
        cases.append({
            "name": f"q_{i:03d}.csv",
            "b64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "sep": sep,
            "header": r.random() < 0.8,
            "nrows": nrows,
        })  # fmt: skip
    OUT.write_text(json.dumps(cases, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
