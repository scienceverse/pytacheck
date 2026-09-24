"""The built-in presets must equal the module lists hard-coded in metacheck.

Parsed from the pinned upstream sources, so upstream-sync fails here when
metacheck changes ``report()``, ``report_repository()`` or the Shiny app's
``validated_modules``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pytacheck.packs.registry import builtin_pack


def _strings(block: str) -> list[str]:
    return re.findall(r'"([^"]*)"', block)


def _formals_modules(source: str, function: str) -> list[str]:
    """The default of ``modules = c(...)`` in ``function <- function(...)``."""
    m = re.search(
        rf"^{re.escape(function)} <- function\((.*?)\)\s*\{{",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert m, f"{function}() not found"
    modules = re.search(r"\bmodules\s*=\s*c\((.*?)\)", m.group(1), flags=re.DOTALL)
    assert modules, f"{function}() has no modules = c(...) default"
    return _strings(modules.group(1))


@pytest.fixture(scope="module")
def upstream_r(upstream_dir: Path) -> dict[str, str]:
    return {
        "report": (upstream_dir / "R" / "report.R").read_text(encoding="utf-8"),
        "app": (upstream_dir / "inst" / "app" / "report_app.R").read_text(encoding="utf-8"),
    }


def test_default_is_report_formals(upstream_r) -> None:
    expected = _formals_modules(upstream_r["report"], "report")
    assert len(expected) == 16
    assert builtin_pack().presets["default"]["modules"] == expected


def test_repository_is_report_repository_formals(upstream_r) -> None:
    expected = _formals_modules(upstream_r["report"], "report_repository")
    assert builtin_pack().presets["repository"]["modules"] == expected


def test_validated_is_the_apps_validated_modules(upstream_r) -> None:
    m = re.search(r"validated_modules\s*<-\s*c\((.*?)\)", upstream_r["app"], flags=re.DOTALL)
    assert m, "validated_modules not found in inst/app/report_app.R"
    assert builtin_pack().presets["validated"]["modules"] == _strings(m.group(1))


def test_builtin_presets_are_plain_module_lists() -> None:
    for name, body in builtin_pack().presets.items():
        assert body["modules"], name
        assert not (body["extends"] or body["exclude"] or body["replace"] or body["args"]), name


def test_metacheck_modules_lists_every_upstream_module(upstream_dir: Path) -> None:
    expected = sorted(p.stem for p in (upstream_dir / "inst" / "modules").glob("*.R"))
    assert builtin_pack().manifest["metacheck_modules"] == expected
    from pytacheck.module import _builtin_names

    assert set(_builtin_names()) <= set(expected), "a built-in module that metacheck lacks"


def test_pack_json_ships_in_the_package() -> None:
    import importlib.resources

    assert importlib.resources.files("pytacheck.modules").joinpath("pack.json").is_file()
