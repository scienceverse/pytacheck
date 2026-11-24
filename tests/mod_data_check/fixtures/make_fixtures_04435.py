"""Fixtures of the data_check cases for metacheck 0.4.4.30 to 0.4.4.35 (#700, #702, #703).

    .venv/bin/python tests/mod_data_check/fixtures/make_fixtures_04435.py

Writes ``repos/lfs_nested`` (a Git LFS pointer and a zip inside a zip), ``repos/unopened`` (a zip
cut off before its central directory) and ``repos/many`` (six files, over a cap of three).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

REPOS = Path(__file__).resolve().parent / "repos"
STAMP = (2026, 1, 1, 0, 0, 0)


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(zipfile.ZipInfo(name, STAMP), data, zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def main() -> None:
    d = REPOS / "lfs_nested"
    d.mkdir(parents=True, exist_ok=True)
    (d / "large_data.csv").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 734003200\n",
        encoding="utf-8",
    )
    inner = _zip({"level2.csv": b"a,b\n1,2\n3,4\n5,6\n"})
    (d / "nested.zip").write_bytes(
        _zip({"level1.csv": b"c,d\n3,4\n5,6\n7,8\n", "level1.zip": inner})
    )
    (d / "README.md").write_text("# Nested\n", encoding="utf-8")

    d = REPOS / "unopened"
    d.mkdir(parents=True, exist_ok=True)
    full = _zip({"a.csv": (",".join(str(i) for i in range(1, 5001)) + "\n").encode()})
    (d / "data and scripts.zip").write_bytes(full[:300])

    d = REPOS / "many"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(1, 6):
        (d / f"d{i}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (d / "a.R").write_text("x <- 1\n", encoding="utf-8")


if __name__ == "__main__":
    main()
