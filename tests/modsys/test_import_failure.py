"""A module whose package is missing says so (port of metacheck #620 and #223, in Python terms).

metacheck's ``module_run()`` tells "the R package is not installed" from "installed but
cannot be loaded" (``.pkg_problem()``) and, for a package only on GitHub (``@import
owner/pkg``), how to install it from there. A pack's Python modules import their own
packages, and a pack declares them as PEP 508 ``dependencies``: a package only on GitHub is
``pkg @ git+https://github.com/owner/pkg``.
"""

from __future__ import annotations

import pytest

from metacheck.module import module_run
from metacheck.utils import _import_failure, _pkg_github_hint, _pkg_name, _pkg_problem
from tests.modsys.helpers import mod_src


def test_pkg_name_is_the_distribution_of_a_requirement() -> None:
    assert _pkg_name("scipy>=1.10") == "scipy"
    assert _pkg_name("my-pkg[extra]==2") == "my-pkg"
    assert _pkg_name("notapkg @ git+https://github.com/someone/notapkg") == "notapkg"


def test_pkg_problem_tells_not_installed_from_cannot_load(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _pkg_problem("json") is None
    assert _pkg_problem("notapkgzzz") == "the Python package 'notapkgzzz' is not installed"

    # a package that is installed but stops while loading
    broken = tmp_path / "brokenpkgzzz"
    broken.mkdir()
    (broken / "__init__.py").write_text(
        'raise ImportError("libzzz.so: cannot open shared object file")\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    problem = _pkg_problem("brokenpkgzzz")
    assert problem is not None
    assert "the Python package 'brokenpkgzzz' is installed" in problem
    assert "but cannot be loaded on this computer" in problem
    assert "cannot open shared object file" in problem
    assert "built for another version of the operating system" in problem


def test_github_hint_for_a_direct_reference() -> None:
    dep = "notapkgzzz @ git+https://github.com/someone/notapkgzzz"
    assert _pkg_github_hint(dep) == f"; install it from GitHub with pip install '{dep}'"
    assert _pkg_github_hint("notapkgzzz>=1") == ""
    # an installed package needs no hint
    assert _pkg_github_hint("pandas @ git+https://github.com/pandas-dev/pandas") == ""


def test_import_failure_is_only_for_a_missing_package() -> None:
    assert _import_failure("m", ValueError("x")) is None
    err = ModuleNotFoundError("No module named 'notapkgzzz'", name="notapkgzzz")
    assert (
        _import_failure("m", err)
        == "The module 'm' cannot run: the Python package 'notapkgzzz' is not installed."
    )
    gh = "notapkgzzz @ git+https://github.com/someone/notapkgzzz"
    assert _import_failure("m", err, [gh]) == (
        "The module 'm' cannot run: the Python package 'notapkgzzz' is not installed; "
        f"install it from GitHub with pip install '{gh}'."
    )
    assert "pip install 'notapkgzzz>=2'" in (_import_failure("m", err, ["notapkgzzz>=2"]) or "")


def test_a_pack_module_that_needs_a_missing_package(ms, paper) -> None:
    src = mod_src("needs_pkg", header="import notapkgzzz")
    ms.install("demo", {"needs_pkg": src}, dependencies=["notapkgzzz>=2"])
    with pytest.raises(
        Exception,
        match=r"cannot run: the Python package 'notapkgzzz' is not installed; install it with "
        r"pip install 'notapkgzzz>=2'",
    ):
        module_run(paper, "demo::needs_pkg")


def test_a_module_file_that_needs_a_missing_package(tmp_path, paper) -> None:
    path = tmp_path / "needs_missing.py"
    path.write_text(mod_src("needs_missing", header="import notapkgzzz"))
    with pytest.raises(
        Exception, match="cannot run: the Python package 'notapkgzzz' is not installed"
    ):
        module_run(paper, str(path))


def test_other_errors_keep_their_message(ms, paper) -> None:
    src = mod_src("boom", header="raise ValueError('boom in the module')")
    ms.install("demo", {"boom": src})
    with pytest.raises(Exception, match="has errors: boom in the module"):
        module_run(paper, "demo::boom")
