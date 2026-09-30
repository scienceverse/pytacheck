"""Lints on how the package names environment variables.

* No module but ``_env`` spells a ``PYTACHECK_*`` or ``METACHECK_*`` name as a string
  constant: a name is read through the table (``metacheck._env``), which is the one place
  that holds it. Only the warning codes of the bibr 12 converter look like names.
* Every old name of the table has its ``METACHECK_`` twin in the same entry.

The lints for ``getLogger(`` and for the documentation table come with the commits that add
the logger link and the documentation.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import metacheck
from metacheck._env import ENV_VARS

SRC = Path(metacheck.__file__).resolve().parent

_NAME = re.compile(r"^(PYTACHECK|METACHECK)_[A-Z0-9_]+$")

#: strings that look like variable names and are not: warning codes in produced files
WARNING_CODES = frozenset(
    {
        "METACHECK_DOI_NOT_VALID",
        "METACHECK_EQ_COMP_UNMAPPED",
        "METACHECK_FLOAT_SECTION_UNKNOWN",
        "METACHECK_MATCH_SCORE_NOT_0_1",
        "METACHECK_SOURCE_IS_TEI",
        "METACHECK_SOURCE_SHA256_UNAVAILABLE",
        "METACHECK_XREF_TYPE_UNMAPPED",
    }
)


def _sources() -> Iterator[tuple[str, ast.Module]]:
    """``(path below src/metacheck, parsed module)`` for every module but ``_env``."""
    for path in sorted(SRC.rglob("*.py")):
        module = path.relative_to(SRC).as_posix()
        if module != "_env.py":
            yield module, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _name_constants() -> list[tuple[str, int, str]]:
    """Every string constant whose whole value looks like a variable name."""
    found = []
    for module, tree in _sources():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _NAME.fullmatch(node.value)
            ):
                found.append((module, node.lineno, node.value))
    return found


def test_no_module_but_env_spells_a_variable_name() -> None:
    offenders = [
        f"{module}:{line} {value!r}"
        for module, line, value in _name_constants()
        if value not in WARNING_CODES
    ]
    assert not offenders, (
        "read settings through metacheck._env (add a row to ENV_VARS for a new one): "
        + ", ".join(offenders)
    )


def test_each_warning_code_still_occurs() -> None:
    # a ratchet: a code that is gone leaves the allow-list, so the list cannot grow stale
    assert {value for _, _, value in _name_constants()} & WARNING_CODES == WARNING_CODES


def test_every_old_name_has_its_new_twin_in_the_same_entry() -> None:
    for key, var in ENV_VARS.items():
        for name in var.names:
            if name.startswith("PYTACHECK_"):
                twin = "METACHECK_" + name.removeprefix("PYTACHECK_")
                assert twin in var.names, f"{key}: {name} has no {twin} in its entry"
