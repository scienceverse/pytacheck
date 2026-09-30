"""The METACHECK_* names that R metacheck reads are reviewed, and the table keeps to them.

R reads ``METACHECK_LLM_CACHE_DIR``, ``METACHECK_LLM_MODEL`` and ``METACHECK_LLM_MAX_CALLS``
with the same meaning as here, so those three are shared on purpose. Any other name R
starts to read fails this test until someone has looked at it: a new one could mean
something else in R than in a table row of the same name.
"""

from __future__ import annotations

import re
from pathlib import Path

from metacheck._env import ENV_VARS

GETENV = re.compile(r"""Sys\.getenv\(\s*["'](METACHECK_[A-Z0-9_]+)["']""")

#: read by R's package code or apps, and in the table (same meaning)
SHARED = {"METACHECK_LLM_CACHE_DIR", "METACHECK_LLM_MODEL", "METACHECK_LLM_MAX_CALLS"}
#: read by R, reviewed, and not in the table: the Shiny apps' usage folder
REVIEWED_OTHER = {"METACHECK_USAGE_DIR"}
#: read only by R's tests
R_TEST_ONLY = {"METACHECK_API_URL", "METACHECK_BIBR12_EXPORTS"}
#: the installer's variables: not the package's, and never read by it with another meaning
INSTALLER = {
    "METACHECK_HOME",
    "METACHECK_NO_LAUNCH",
    "METACHECK_UNINSTALL",
    "METACHECK_REF",
    "METACHECK_SPEC",
    "METACHECK_CONSTRAINTS",
}


def _names_read(root: Path, suffixes: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in suffixes:
            found.update(GETENV.findall(path.read_text(encoding="utf-8", errors="replace")))
    return found


def _table_names() -> set[str]:
    return {name for var in ENV_VARS.values() for name in var.names}


def test_the_names_shared_with_r_are_exactly_the_three_reviewed_ones(upstream_dir: Path) -> None:
    read_by_r = _names_read(upstream_dir / "R", (".R", ".r")) | _names_read(
        upstream_dir / "inst", (".R", ".r", ".Rmd")
    )
    assert read_by_r & _table_names() == SHARED
    flagged = {
        name
        for var in ENV_VARS.values()
        if var.shared_with_r
        for name in var.names
        if name.startswith("METACHECK_")
    }
    assert flagged == SHARED, "each name R reads is marked shared_with_r, and only those"
    assert read_by_r - SHARED == REVIEWED_OTHER, (
        "R reads a METACHECK_ name that is not reviewed: decide what it means here, "
        "then add it to REVIEWED_OTHER or to the table"
    )


def test_r_only_names_and_the_installers_are_not_in_the_table(upstream_dir: Path) -> None:
    in_tests = _names_read(upstream_dir / "tests" / "testthat", (".R", ".r"))
    read_by_r = _names_read(upstream_dir / "R", (".R", ".r")) | _names_read(
        upstream_dir / "inst", (".R", ".r", ".Rmd")
    )
    assert in_tests - read_by_r == R_TEST_ONLY
    reserved = REVIEWED_OTHER | R_TEST_ONLY | INSTALLER
    assert not reserved & _table_names()
