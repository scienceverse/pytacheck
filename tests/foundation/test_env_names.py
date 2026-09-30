"""Lints on how the package names environment variables.

* No module but ``_env`` spells a ``PYTACHECK_*`` or ``METACHECK_*`` name as a string
  constant: a name is read through the table (``metacheck._env``), which is the one place
  that holds it. Only the warning codes of the bibr 12 converter look like names.
* Every old name of the table has its ``METACHECK_`` twin in the same entry.

* ``getLogger(`` is called only in ``_logging.py`` and in ``packs/auth.py``, and never with
  ``__name__``: every other module asks ``get_logger()`` for its logger.
* The table in ``docs/ENVIRONMENT.md`` lists exactly the settings of ``ENV_VARS``, in the same
  order, with the same names in the same rank, and marks the secret and the shared ones.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import metacheck
from metacheck._env import ENV_VARS

SRC = Path(metacheck.__file__).resolve().parent
ENVIRONMENT_MD = Path(__file__).resolve().parents[2] / "docs" / "ENVIRONMENT.md"

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


#: the only modules that call ``getLogger``: the package's logger, and the filter on the HTTP
#: libraries' loggers
GETLOGGER_MODULES = frozenset({"_logging.py", "packs/auth.py"})


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


def _getlogger_calls() -> list[tuple[str, int, bool]]:
    """``(module, line, argument is __name__)`` for every ``getLogger(...)`` call in the package."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        module = path.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "getLogger") or (
                isinstance(func, ast.Name) and func.id == "getLogger"
            ):
                dunder_name = any(
                    isinstance(arg, ast.Name) and arg.id == "__name__" for arg in node.args
                )
                found.append((module, node.lineno, dunder_name))
    return found


def test_getlogger_is_called_only_where_the_package_logger_lives() -> None:
    calls = _getlogger_calls()
    offenders = [f"{module}:{line}" for module, line, _ in calls if module not in GETLOGGER_MODULES]
    assert not offenders, "ask metacheck._logging.get_logger() instead: " + ", ".join(offenders)
    # a ratchet, as for the warning codes: a module that stops calling it leaves the list
    assert {module for module, _, _ in calls} == GETLOGGER_MODULES


def test_no_logger_is_named_after_its_module() -> None:
    # records would come out under metacheck.<module>, and not reach handlers set on pytacheck
    offenders = [f"{module}:{line}" for module, line, dunder in _getlogger_calls() if dunder]
    assert not offenders, "getLogger(__name__) is not allowed: " + ", ".join(offenders)


def _documented_table(text: str) -> list[tuple[str, list[str], str]]:
    """``(setting, names in the order listed, notes)`` for each row of the table of the doc.

    The table is the one under the heading ``## The table``; a row starts with the setting in
    backticks, and its second cell lists the variable names in backticks.
    """
    _, _, after = text.partition("\n## The table\n")
    table = after.split("\n#", 1)[0]  # up to the next heading
    rows = []
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        setting = re.fullmatch(r"`([A-Z0-9_]+)`", cells[0]) if len(cells) >= 4 else None
        if setting is not None:
            rows.append((setting[1], re.findall(r"`([A-Z0-9_]+)`", cells[1]), cells[3]))
    return rows


def test_the_documented_table_lists_the_settings_names_and_ranks_of_the_code() -> None:
    documented = _documented_table(ENVIRONMENT_MD.read_text(encoding="utf-8"))
    # the settings and their order, then each setting's names in lookup order
    assert [key for key, _, _ in documented] == list(ENV_VARS)
    for key, names, _ in documented:
        assert tuple(names) == ENV_VARS[key].names, f"{key}: names or their order differ"


def test_the_documented_table_marks_the_secret_and_the_shared_settings() -> None:
    for key, _, notes in _documented_table(ENVIRONMENT_MD.read_text(encoding="utf-8")):
        var = ENV_VARS[key]
        assert notes.startswith("secret") == var.secret, f"{key}: secret mark differs"
        assert ("shared with R" in notes) == var.shared_with_r, f"{key}: shared mark differs"
