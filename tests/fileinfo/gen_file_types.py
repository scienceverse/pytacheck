"""Regenerate the ``metacheck::file_types`` table embedded in pytacheck.fileinfo.types.

Usage (from the repository root, with R + metacheck at the pinned commit)::

    PYTACHECK_RSCRIPT=/path/to/Rscript .venv/bin/python tests/fileinfo/gen_file_types.py

Runs R, dumps ``metacheck::file_types`` (``data/file_types.rda``) row by row as
``ext:type`` tokens and rewrites the block between the ``BEGIN GENERATED`` /
``END GENERATED`` markers of ``src/pytacheck/fileinfo/types.py``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "src" / "pytacheck" / "fileinfo" / "types.py"

R_CODE = r"""
ft <- metacheck::file_types
stopifnot(identical(names(ft), c("ext", "type")), !anyNA(ft$ext), !anyNA(ft$type))
stopifnot(!any(grepl("[[:space:]]", ft$ext)), !any(grepl("[[:space:]:]", ft$type)))
writeLines(paste(ft$ext, ft$type, sep = ":"))
"""


def r_file_types(rscript: str) -> list[str]:
    """``ext:type`` tokens of ``metacheck::file_types``, in row order."""
    env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    out = subprocess.run(
        [rscript, "-e", R_CODE], check=True, capture_output=True, text=True, env=env
    ).stdout
    return [line for line in out.splitlines() if line]


def render(tokens: list[str]) -> str:
    body = textwrap.fill(" ".join(tokens), width=95, break_long_words=False, break_on_hyphens=False)
    return f'_FILE_TYPES = """\n{body}\n"""\n'


def main() -> int:
    rscript = os.environ.get("PYTACHECK_RSCRIPT", "Rscript")
    tokens = r_file_types(rscript)
    src = TARGET.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(# BEGIN GENERATED[^\n]*\n)(.*?)(# END GENERATED)", flags=re.DOTALL
    )
    if not pattern.search(src):
        print(f"no GENERATED block in {TARGET}", file=sys.stderr)
        return 1
    new = pattern.sub(lambda m: m.group(1) + render(tokens) + m.group(3), src)
    TARGET.write_text(new, encoding="utf-8")
    print(f"wrote {len(tokens)} rows of metacheck::file_types to {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
