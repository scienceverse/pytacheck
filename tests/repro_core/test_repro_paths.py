"""How reproducibility_check rewrites the paths in the authors' scripts before a run.

Port of ``test-repro-paths.R``: the tiny deposit in ``fixtures/repro_paths/`` (see its
README.md): a line marked KEEP must stay as written, a line marked RESOLVES must lead to its
file from the folder the script runs in, a line marked NAMES names the file as the deposit
spells it (#648).
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Any

import pandas as pd

from metacheck.repro.core import _repro_norm_rel
from metacheck.repro.layout import _repro_rewrite_preview

_MARK = re.compile(r"#\s*(KEEP|RESOLVES|NAMES)\s+(\S+)\s*(\S*)\s*$")


def _marks(pv: pd.DataFrame) -> list[dict[str, Any]]:
    """One row per marker: id, kind, the line as rewritten, and whether it passes."""
    rows: list[dict[str, Any]] = []
    for i in range(len(pv)):
        orig = str(pv["original"].iloc[i]).split("\n")
        new = str(pv["rewritten"].iloc[i]).split("\n")
        run_dir = str(pv["run_dir"].iloc[i])
        for line in orig:
            m = _MARK.search(line)
            if m is None:
                continue
            marker = m.group(0).strip()
            kind, mid, target = m.group(1), m.group(2), m.group(3)
            orig_line = next((x for x in orig if marker in x), "")
            new_line = next((x for x in new if marker in x), None)
            if new_line is None:
                ok = False
            elif kind == "KEEP":
                ok = new_line.strip() == orig_line.strip()
            elif kind == "NAMES":
                # the file named as in the deposit, and the rest of the line as written
                ok = (
                    re.search(f"[\"']{re.escape(target)}[\"']", new_line) is not None
                    and new_line.strip().lower() == orig_line.strip().lower()
                )
            else:
                quoted = [q[1:-1] for q in re.findall(r"""["'][^"']+["']""", new_line)]
                quoted = [
                    q
                    for q in quoted
                    if posixpath.basename(q.rstrip("/")) == posixpath.basename(target)
                ]
                ok = any(
                    _repro_norm_rel(q if run_dir in (".", "") else posixpath.join(run_dir, q))
                    == target
                    for q in quoted
                )
            rows.append(
                {
                    "file": pv["file_name"].iloc[i],
                    "id": mid,
                    "kind": kind,
                    "ok": ok,
                    "original": orig_line.strip(),
                    "rewritten": None if new_line is None else new_line.strip(),
                }
            )
    return rows


def test_paths_in_the_fixture_deposit_are_kept_or_pointed_to_their_file(
    area_fixtures: Path,
) -> None:
    """paths in the fixture deposit are kept or pointed to their file (#648)"""
    pv = _repro_rewrite_preview(area_fixtures / "repro_paths")
    marks = _marks(pv)
    assert len(marks) > 50
    bad = [m for m in marks if not m["ok"]]
    assert not bad, f"{len(bad)} path line(s) handled wrongly:\n" + "\n".join(
        f"{m['file']} {m['id']} ({m['kind']}): {m['original']}  ->  {m['rewritten']}" for m in bad
    )
