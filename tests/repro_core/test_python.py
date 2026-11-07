"""The Python support of the reproducibility check (port of metacheck d3b8594's tests).

The static helpers run offline. The process backend runs this machine's ``python3`` on
the fixture scripts in ``fixtures/python`` (copies of metacheck's
``tests/testthat/fixtures/repro/*.py``, which the pinned submodule does not have yet);
the Docker backends run with ``docker`` itself replaced by fakes.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.codecheck.core import code_file_refs
from metacheck.repro import docker
from metacheck.repro import python as rpy
from metacheck.repro.core import (
    _repro_redirect_writes,
    repro_file_io,
    repro_rewrite_paths,
    repro_run_order,
    repro_write_scripts,
)
from metacheck.repro.docker import repro_install_deps_py_docker, repro_run_scripts_py_docker
from metacheck.repro.python import (
    _classify_py_error,
    _repro_py_defined_vars,
    _repro_py_manifest_pins,
    _repro_py_parses,
    repro_dependencies_py,
    repro_install_deps_py,
    repro_run_scripts_py,
)

HERE = Path(__file__).resolve().parent
PY_FIXTURES = HERE / "fixtures" / "python"
SCRIPTS = [
    "ok.py",
    "writes_then_reads.py",
    "reads_written.py",
    "missing_input.py",
    "bad_chdir.py",
    "errors.py",
    "undefined_var.py",
]


def _na(s: pd.Series) -> list[Any]:
    return [None if pd.isna(v) else v for v in s.tolist()]


def _code(name: str) -> list[str]:
    return (PY_FIXTURES / name).read_text(encoding="utf-8").split("\n")


@pytest.fixture(scope="module")
def python3() -> str:
    exe = rpy._python()
    if exe is None:
        pytest.skip("no python3 on PATH")
    return exe


# dependencies ------------------------------------------------------------------------------


def test_dependencies_from_imports() -> None:
    deps = repro_dependencies_py(
        [
            "import numpy as np",
            "import os, sys",
            "from sklearn.linear_model import LinearRegression",
            "import pandas.api.types",
            "from . import helpers",
            "from __future__ import annotations",
            "    import scipy.stats as st  # indented, inside a function",
            "# import commented_out",
        ]
    )
    # in the order of the imports; `from . import` and `__future__` are not packages; the
    # pip name is what is reported (`import sklearn` comes from `scikit-learn`)
    assert deps["package"].tolist() == ["numpy", "os", "sys", "scikit-learn", "pandas", "scipy"]
    base = dict(zip(deps["package"], deps["base"], strict=True))
    assert base["os"] and base["sys"]
    assert not base["numpy"] and not base["scikit-learn"]
    assert set(deps.loc[~deps["base"], "source"]) == {"pypi"}
    assert set(deps.loc[deps["base"], "source"]) == {"base"}


def test_dependencies_report_the_pip_name_once() -> None:
    deps = repro_dependencies_py(
        [
            "import yaml",
            "import PIL.Image",
            "import pywt",
            "import sklearn",
            "from sklearn import svm",
        ]
    )
    assert deps["package"].tolist() == ["PyYAML", "Pillow", "PyWavelets", "scikit-learn"]


def test_dependencies_python2_modules_are_base() -> None:
    # they cannot be installed: importing one shows that the code is Python 2
    deps = repro_dependencies_py(["import cPickle", "import urlparse", "import numpy"])
    base = dict(zip(deps["package"], deps["base"], strict=True))
    assert base == {"cPickle": True, "urlparse": True, "numpy": False}
    assert set(deps.loc[deps["base"], "source"]) == {"base"}


def test_dependencies_pooled_list_dedups() -> None:
    deps = repro_dependencies_py({"a.py": ["import numpy"], "b.py": ["import numpy", "import re"]})
    assert deps["package"].tolist() == ["numpy", "re"]


def test_dependencies_take_the_manifest_pin() -> None:
    # the pin is matched by the PEP 503 pip name, which is not the module's for scikit-learn
    deps = repro_dependencies_py(
        ["import numpy", "import sklearn", "import yaml", "import scipy"],
        manifest_text=["numpy==1.26.4", "scikit-learn>=1.3  # ML", "PYYAML==6.0", "Scipy_x"],
    )
    refs = dict(zip(deps["package"], _na(deps["ref"]), strict=True))
    assert refs == {
        "numpy": "numpy==1.26.4",
        "scikit-learn": "scikit-learn>=1.3",
        "PyYAML": "PYYAML==6.0",
        "scipy": None,
    }
    # an underscore is a hyphen, as pip reads names; metacheck matches lowercase only (D123)
    deps = repro_dependencies_py(["import sklearn"], manifest_text=["Scikit_Learn>=1.3"])
    assert _na(deps["ref"]) == ["Scikit_Learn>=1.3"]


def test_pooled_dependencies_prefer_the_pinned_row() -> None:
    deps = repro_dependencies_py(
        {"a.py": ["import numpy"], "b.py": ["import numpy", "import os"]},
        manifest_text=["numpy==1.26.4"],
    )
    assert deps["package"].tolist() == ["numpy", "os"]
    assert _na(deps["ref"]) == ["numpy==1.26.4", None]


def test_stdlib_list_covers_this_python() -> None:
    names = getattr(sys, "stdlib_module_names", None)
    if names is None:  # pragma: no cover - Python < 3.10
        pytest.skip("no sys.stdlib_module_names")
    public = {n for n in names if not n.startswith("_")} | {"__future__"}
    assert public - rpy._PY_STDLIB == set()


def test_requirement_uses_the_pypi_name() -> None:
    deps = repro_dependencies_py(["import sklearn", "import cv2", "import PIL", "import numpy"])
    assert rpy._requirements(deps) == ["scikit-learn", "opencv-python", "Pillow", "numpy"]


# manifests ---------------------------------------------------------------------------------


def test_manifest_requirements_txt() -> None:
    pins = _repro_py_manifest_pins(
        [
            "# analysis deps",
            "-r base.txt",
            "-e .",
            "--index-url https://example.org/simple",
            "numpy==1.26.4 --hash=sha256:abc",
            "pandas>=2 ; python_version >= '3.9'",
            "Scikit_Learn  # the ML bits",
            "git+https://example.org/x/y.git#egg=y",
            "",
            "numpy==1.26.4",
        ]
    )
    # lowercased; the name is the leading identifier of any line, as in metacheck
    assert pins["package"].tolist() == ["numpy", "pandas", "scikit_learn", "git"]
    assert pins["requirement"].tolist() == [
        "numpy==1.26.4",
        "pandas>=2 ; python_version >= '3.9'",
        "Scikit_Learn",
        "git+https://example.org/x/y.git#egg=y",
    ]


def test_manifest_pyproject() -> None:
    pins = _repro_py_manifest_pins(
        [
            "[project]",
            'name = "analysis"',
            'version = "0.1"',
            'dependencies = ["numpy>=1.24", "pandas"]',
            "[tool.poetry.dependencies]",
            'python = "^3.10"',
            'scipy = "^1.11"',
            'statsmodels = {version = "0.14.1"}',
            'pingouin = "~0.5.3"',
            'seaborn = "*"',
            "[tool.poetry.group.dev.dependencies]",
            'pytest = "^7.0"',
        ]
    )
    # the project's own name and version are not requirements, and Poetry's versions are
    # written as pip ranges
    assert pins["package"].tolist() == [
        "numpy",
        "pandas",
        "scipy",
        "statsmodels",
        "pingouin",
        "seaborn",
        "pytest",
    ]
    req = dict(zip(pins["package"], pins["requirement"], strict=True))
    assert req["numpy"] == "numpy>=1.24"
    assert req["scipy"] == "scipy>=1.11,<2.0"
    assert req["statsmodels"] == "statsmodels==0.14.1"
    assert req["pingouin"] == "pingouin>=0.5.3,<0.6"
    assert req["seaborn"] == "seaborn"
    assert req["pytest"] == "pytest>=7.0,<8.0"


def test_manifest_pyproject_array_over_lines() -> None:
    pins = _repro_py_manifest_pins(
        ["[project]", "dependencies = [", '  "numpy>=1",  # array', '  "pandas",', "]"]
    )
    assert pins["requirement"].tolist() == ["numpy>=1", "pandas"]


@pytest.mark.parametrize(
    ("spec", "pip"),
    [
        ("^1.26", ">=1.26,<2.0"),
        ("^0.3", ">=0.3,<0.4"),
        ("^0.0.3", ">=0.0.3,<0.0.4"),
        ("~1.11", ">=1.11,<1.12"),
        ("~1", ">=1,<2.0"),
        ("1.2.3", "==1.2.3"),
        ("*", ""),
        ("", ""),
        (">=1.0", ">=1.0"),
        ("~=1.2", "~=1.2"),
        ("^x", ""),
        ("abc", "==abc"),
    ],
)
def test_poetry_to_pip(spec: str, pip: str) -> None:
    assert rpy._poetry_to_pip(spec) == pip


def test_manifest_broken_pyproject_is_empty() -> None:
    assert len(_repro_py_manifest_pins(["[project", "dependencies = ["])) == 0
    assert len(_repro_py_manifest_pins(None)) == 0


# parsing and definitions -------------------------------------------------------------------


def test_parses() -> None:
    res = _repro_py_parses(
        {
            "ok.py": _code("ok.py"),
            "py2.py": ["print 'python 2'"],
            "bad.py": ["def f(:"],
            "empty.py": [],
        }
    )
    assert res.to_dict() == {"ok.py": True, "py2.py": False, "bad.py": False, "empty.py": True}
    # only a file that is Python 2 is marked, not any file that fails
    assert res.attrs["python2"] == [False, True, False, False]
    plain = _repro_py_parses([["x = 1"], ["def f(:"]])
    assert plain.tolist() == [True, False]
    assert "python2" not in plain.attrs


def test_parses_marks_the_python2_forms() -> None:
    res = _repro_py_parses(
        [
            ["exec 'x = 1'"],
            ["try:", "    pass", "except ValueError, e:", "    pass"],
            ["if 1:", "\tx = 1", "        y = 2"],
            ["print('fine')"],
        ]
    )
    assert res.tolist() == [False, False, False, True]
    assert res.attrs["python2"] == [True, True, True, False]


def test_defined_vars() -> None:
    out = _repro_py_defined_vars(
        {
            "a.py": [
                "x = 1",
                "y=2  # comment",
                "if x == 1:",
                "    inner = 3",
                "def f():",
                "    pass",
                "class C:",
                "    pass",
                "async def g():",
                "    pass",
            ],
            "b.py": ["print(x)"],
        }
    )
    assert out["file_name"].tolist() == ["a.py", "b.py"]
    assert out["defines"].iloc[0] == ["x", "y", "f", "C", "g"]
    assert out["defines"].iloc[1] == []


def test_classify_errors() -> None:
    tb = "Traceback (most recent call last):\n  File 'a.py', line 1\nNameError: name 'zz' is not defined\n"
    assert _classify_py_error(tb, []) == (
        "NameError: name 'zz' is not defined",
        "undefined_variable",
        "zz",
        False,
    )
    nomod = "Traceback ...\nModuleNotFoundError: No module named 'sklearn.linear_model'\n"
    # the pip name of the module is compared with the packages that failed to install
    assert _classify_py_error(nomod, ["scikit-learn"])[1:] == ("dependency_unavailable", None, True)
    assert _classify_py_error(nomod, ["sklearn"])[1] == "ModuleNotFoundError"
    assert _classify_py_error(nomod, [])[1] == "ModuleNotFoundError"
    # Python 2 writes the name without quotes
    py2 = "ImportError: No module named librosa"
    assert _classify_py_error(py2, ["librosa"])[1] == "dependency_unavailable"
    # a last line that is no `<Name>Error: ...` has no type (metacheck: NA), not "runtime"
    assert _classify_py_error("Segmentation fault\n", [])[1] is None
    assert _classify_py_error("ValueError\n", [])[1] is None
    assert _classify_py_error("RuntimeError: deliberate\n", [])[1] == "RuntimeError"


# file I/O, path rewrites, run order -------------------------------------------------------


def test_file_io_and_order() -> None:
    io = repro_file_io({n: _code(n) for n in SCRIPTS}, "Python")
    reads = dict(zip(io["file_name"], io["reads"], strict=True))
    writes = dict(zip(io["file_name"], io["writes"], strict=True))
    assert reads["ok.py"] == ["data.csv"]
    assert writes["writes_then_reads.py"] == ["intermediate.csv"]
    assert reads["reads_written.py"] == ["intermediate.csv"]
    assert writes["ok.py"] == []
    order = repro_run_order(io)
    o = dict(zip(order["file_name"], order["order"], strict=True))
    assert o["writes_then_reads.py"] < o["reads_written.py"]


@pytest.mark.parametrize(
    ("line", "written"),
    [
        ("f = open('out.txt', 'w')", True),
        ('with open("out.csv", mode="a", newline="") as f:', True),
        ("open('out.bin', 'wb')", True),
        ("open('state.txt', 'r+')", True),
        ("open('x.txt', 'x')", True),
        ("open('in.csv')", False),
        ("open('in.csv', 'r')", False),
        ("open('in.csv', 'rb')", False),
        # metacheck takes any quoted letters with a w, a or x in them for a write mode
        ("open('raw')", False),
        ("open(p, encoding='ascii')", False),
        ("df.to_csv('out.csv', index=False)", True),
        ("plt.savefig('fig.png')", True),
        ("np.save('arr.npy', a)", True),
        ("pickle.dump(obj, open('m.pkl', 'wb'))", True),
        ("pd.to_datetime('2020.01')", False),
    ],
)
def test_python_writes(line: str, written: bool) -> None:
    io = repro_file_io({"a.py": [line]}, "Python")
    assert (len(io["writes"].iloc[0]) > 0) is written


def test_code_file_refs_python_writes() -> None:
    code = ["import pandas as pd", "df = pd.read_csv('in.csv')", "df.to_parquet('out.parquet')"]
    refs = code_file_refs(code, "Python", include_writes=True)
    assert refs == ["in.csv", "out.parquet"]
    assert code_file_refs(code, "Python") == ["in.csv"]


def test_redirect_writes_python() -> None:
    wr = _repro_redirect_writes(
        [
            "out = os.path.join('results', 'table.csv')",
            "df.to_csv(out, index=False)",
            "fig.savefig('plots/fig1.png', dpi=300)",
            "x = a == b",
        ],
        lang="Python",
    )
    # a path held in a variable is redirected under the variable's name, as for R
    assert wr["redirected_name"].tolist() == ["out.csv", "fig1.png"]
    assert wr["replacement"].tolist()[1] == 'savefig("{{REPRO_OUTPUT}}fig1.png", dpi=300)'


def test_rewrite_paths_python() -> None:
    plan = pd.DataFrame({"file_name": ["raw/data.csv"], "target_path": ["data/raw/data.csv"]})
    rw = repro_rewrite_paths(["df = pd.read_csv('data.csv')"], "a.py", plan, "Python")
    assert rw["ref"].tolist() == ["data.csv"]
    assert rw["target"].tolist() == ["data/raw/data.csv"]
    assert rw["matched"].tolist() == [True]
    assert len(repro_rewrite_paths(["x"], "a.jl", plan, "Julia")) == 0


def test_write_scripts_python(tmp_path: Path) -> None:
    code = {
        "bad_chdir.py": _code("bad_chdir.py"),
        "plots.py": [
            "import os",
            "if True:",
            "    os.chdir(base)",
            "df.to_csv('summary.csv')",
            "plt.rcParams['font.family'] = 'Arial'",
        ],
    }
    rt = repro_write_scripts(
        code, None, None, tmp_path, lang="Python", output_dir="/sandbox/output/"
    )
    assert rt["setwd_removed"].tolist() == [1, 1]
    assert rt["setwd_paths"].tolist() == ["/Users/original_author/project", ""]
    written = (tmp_path / "plots.py").read_text(encoding="utf-8")
    # the block keeps a statement, so the script still compiles
    assert "    pass  # [reproducibility_check removed os.chdir] os.chdir(base)" in written
    compile(written, "plots.py", "exec")
    assert 'df.to_csv("/sandbox/output/summary.csv")' in written
    assert "'Arial'" in written  # no font substitution for Python
    assert rt["family_replaced"].tolist() == [0, 0]


# the process backend (this machine's python3) ---------------------------------------------


def _run_table(root: Path, names: list[str]) -> pd.DataFrame:
    shutil.copy(PY_FIXTURES / "data.csv", root / "data.csv")
    code = {n: _code(n) for n in names}
    return repro_write_scripts(code, None, None, root, lang="Python")


def test_run_scripts_py(python3: str, tmp_path: Path) -> None:
    tbl = _run_table(tmp_path, SCRIPTS)
    out = repro_run_scripts_py(
        tbl,
        [
            "bad_chdir.py",
            "ok.py",
            "writes_then_reads.py",
            "reads_written.py",
            "errors.py",
            "undefined_var.py",
        ],
        timeout=60,
        skip=["missing_input.py"],
    )
    got = dict(zip(out["file_name"], out["outcome"], strict=True))
    assert got == {
        "bad_chdir.py": "ran_ok",
        "ok.py": "ran_ok",
        "writes_then_reads.py": "ran_ok",
        "reads_written.py": "ran_ok",
        "errors.py": "errored",
        "undefined_var.py": "errored",
        "missing_input.py": "skipped_missing_inputs",
    }
    row = out.set_index("file_name")
    assert "mean_a = 2.0" in row.loc["ok.py", "stdout"]
    assert row.loc["errors.py", "error"] == "RuntimeError: deliberate failure for testing"
    assert row.loc["errors.py", "error_type"] == "RuntimeError"
    assert row.loc["undefined_var.py", "error_type"] == "undefined_variable"
    assert row.loc["undefined_var.py", "undefined_var"] == "some_var_no_script_defines"
    assert row.loc["ok.py", "script_lines"][0] == "import csv"
    assert out["captures"].isna().all()


def test_run_scripts_py_not_parsed_timeout_and_lib(python3: str, tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    (lib / "helperpkg").mkdir(parents=True)
    (lib / "helperpkg" / "__init__.py").write_text("VALUE = 42\n")
    root = tmp_path / "sb"
    root.mkdir()
    code = {
        "uses_lib.py": ["import helperpkg", "print(helperpkg.VALUE)"],
        "slow.py": ["import time", "time.sleep(30)"],
        "py2.py": ["print 'x'"],
        "nodep.py": ["import not_a_real_pkg_xyz"],
    }
    tbl = repro_write_scripts(code, None, None, root, lang="Python")
    out = repro_run_scripts_py(
        tbl,
        list(code),
        lib_dir=lib,
        timeout=2,
        parses={"py2.py": False},
        failed_deps=["not_a_real_pkg_xyz"],
    )
    row = out.set_index("file_name")
    assert row.loc["uses_lib.py", "outcome"] == "ran_ok"
    assert row.loc["uses_lib.py", "stdout"].strip() == "42"
    assert row.loc["slow.py", "outcome"] == "timed_out"
    # the process runner gives no type for a script that ran out of its own time (metacheck: NA)
    assert pd.isna(row.loc["slow.py", "error_type"])
    assert "timed out" in row.loc["slow.py", "error"]
    assert row.loc["slow.py", "stdout"] == "" and row.loc["slow.py", "script_lines"] == []
    assert row.loc["py2.py", "outcome"] == "not_parsed"
    assert row.loc["nodep.py", "outcome"] == "dependency_unavailable"


def test_run_scripts_py_deadline(python3: str, tmp_path: Path) -> None:
    root = tmp_path / "sb"
    root.mkdir()
    code = {"slow.py": ["import time", "time.sleep(30)"], "later.py": ["print(1)"]}
    tbl = repro_write_scripts(code, None, None, root, lang="Python")
    out = repro_run_scripts_py(tbl, list(code), timeout=60, deadline=time.time() + 2)
    row = out.set_index("file_name")
    # stopped by the time left for all scripts, not its own limit; the next is not started
    assert row.loc["slow.py", "outcome"] == "timed_out"
    assert row.loc["slow.py", "error_type"] == "deadline"
    assert row.loc["later.py", "outcome"] == "skipped_time_limit"
    assert (
        row.loc["later.py", "error"] == "not started: the time for all scripts together had run out"
    )
    gone = repro_run_scripts_py(tbl, list(code), deadline=time.time() - 1)
    assert gone["outcome"].tolist() == ["skipped_time_limit", "skipped_time_limit"]


def test_run_scripts_py_own_modules_and_environment(python3: str, tmp_path: Path) -> None:
    root = tmp_path / "sb"
    root.mkdir()
    lib = tmp_path / "lib"
    lib.mkdir()
    (root / "mods").mkdir()
    (root / "mods" / "mymod.py").write_text("VALUE = 7\n")
    code = {
        "run.py": [
            "import os, mymod",
            "print(mymod.VALUE)",
            "print(os.environ['MPLBACKEND'], os.environ['QT_QPA_PLATFORM'])",
            "print(os.environ['PYTHONPATH'].split(os.pathsep)[:2])",
        ]
    }
    tbl = repro_write_scripts(code, None, None, root, lang="Python")
    tbl["module_root"] = str(root)
    tbl["module_folders"] = "mods"
    out = repro_run_scripts_py(tbl, ["run.py"], lib_dir=lib, timeout=60)
    row = out.set_index("file_name")
    assert row.loc["run.py", "outcome"] == "ran_ok", row.loc["run.py", "stderr"]
    lines = row.loc["run.py", "stdout"].split("\n")
    assert lines[0] == "7"
    assert lines[1] == "Agg offscreen"
    # the repository's own modules first, then the library
    assert lines[2] == str([f"{root}/mods", str(lib)])


def test_run_scripts_py_needs_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(rpy.shutil, "which", lambda _: None)
    tbl = _run_table(tmp_path, ["ok.py"])
    with pytest.raises(RuntimeError, match="not found"):
        repro_run_scripts_py(tbl, ["ok.py"])
    assert len(repro_run_scripts_py(None, [])) == 0


def test_install_deps_py_runs_pip_into_the_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []

    class Done:
        def __init__(self, rc: int, err: str) -> None:
            self.returncode, self.stderr = rc, err

    def fake_run(args: list[str], **kw: Any) -> Done:
        calls.append(args)
        return Done(0, "") if args[-1] != "nopkg" else Done(1, "ERROR: No matching distribution")

    monkeypatch.setattr(rpy.subprocess, "run", fake_run)
    deps = repro_dependencies_py(["import sklearn", "import nopkg", "import os"])
    deps = deps[~deps["base"]].reset_index(drop=True)
    out = repro_install_deps_py(deps, tmp_path / "lib", python_bin="python3")
    calls = [c for c in calls if "pip" in c]
    assert out["package"].tolist() == ["scikit-learn", "nopkg"]
    assert out["installed"].tolist() == [True, False]
    assert out["message"].tolist() == ["", "ERROR: No matching distribution"]
    assert [c[-1] for c in calls] == ["scikit-learn", "nopkg"]
    assert all(c[c.index("--target") + 1] == str(tmp_path / "lib") for c in calls)
    assert all("--prefer-binary" in c for c in calls)
    assert len(repro_install_deps_py(deps.iloc[0:0], tmp_path / "lib")) == 0


def test_install_deps_py_retries_a_pin_with_the_bare_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    class Done:
        def __init__(self, rc: int, err: str) -> None:
            self.returncode, self.stderr = rc, err

    def fake_run(args: list[str], **kw: Any) -> Done:
        if "pip" not in args:
            return Done(0, "")
        calls.append(args[-1])
        return (
            Done(1, "ERROR: No matching distribution\n") if args[-1] == "numpy==99" else Done(0, "")
        )

    monkeypatch.setattr(rpy.subprocess, "run", fake_run)
    deps = repro_dependencies_py(["import numpy", "import pandas"], manifest_text=["numpy==99"])
    out = repro_install_deps_py(deps, tmp_path / "lib", python_bin="python3")
    # a pinned version that does not install is replaced by the current one
    assert calls == ["numpy==99", "numpy", "pandas"]
    assert out["installed"].tolist() == [True, True]
    assert out["message"].tolist() == ["", ""]


# the Docker backends (docker itself faked) ------------------------------------------------


def _mount(args: list[str], target: str) -> str:
    for i, a in enumerate(args):
        m = re.fullmatch(r"(.+):(/[^:]*)(?::ro)?", args[i + 1]) if a == "-v" else None
        if m and m.group(2) == target:
            return m.group(1)
    raise AssertionError(f"no mount for {target}: {args}")


def test_install_deps_py_docker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake(args: list[str], **kw: Any) -> dict[str, Any]:
        if args[0] == "stop":
            seen["stopped"] = True
            return {"status": 0, "timeout": False}
        seen["args"] = args
        sandbox = _mount(args, "/sandbox")
        seen["driver"] = Path(sandbox, "install.sh").read_text()
        # numpy finished; the timeout stopped scikit-learn
        Path(sandbox, ".install_results").write_text("1 ok\n")
        return {"status": None, "timeout": True}

    monkeypatch.setattr(docker, "_docker", fake)
    deps = repro_dependencies_py(["import numpy", "import sklearn"])
    out = repro_install_deps_py_docker(deps, tmp_path / "lib", timeout=9)
    assert out["package"].tolist() == ["numpy", "scikit-learn"]
    assert out["installed"].tolist() == [True, False]
    assert out["message"].tolist() == ["", "docker install timed out after 9s"]
    # one sh driver, one pip per requirement, each with a time limit of its own
    driver = seen["driver"]
    assert driver.startswith("#!/bin/sh\nset -u\n")
    pip = (
        "timeout 120 pip install --quiet --prefer-binary --no-input --prefix /tmp/pyt_{} "
        "--extra-index-url https://download.pytorch.org/whl/cpu '{}'"
    )
    assert pip.format(1, "numpy") in driver
    assert pip.format(2, "scikit-learn") in driver
    assert seen["stopped"]
    args = seen["args"]
    assert "--network" not in args  # PyPI must be reachable
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in args
    assert _mount(args, "/pylib") == os.path.normpath(tmp_path / "lib")
    assert args[-3:] == [
        "ghcr.io/scienceverse/metacheck_py_small:latest",
        "sh",
        "/sandbox/install.sh",
    ]


def test_install_deps_py_docker_not_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake(args: list[str], **kw: Any) -> Any:
        return {"status": 0, "timeout": False} if args[0] == "stop" else OSError("docker")

    monkeypatch.setattr(docker, "_docker", fake)
    deps = repro_dependencies_py(["import numpy"])
    out = repro_install_deps_py_docker(deps, tmp_path / "lib")
    assert out["message"].tolist() == ["docker run could not be started"]
    assert len(repro_install_deps_py_docker(None, tmp_path / "lib")) == 0


def test_run_scripts_py_docker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "sb"
    root.mkdir()
    lib = root / "_pylib"
    lib.mkdir()
    code = {n: [f"# {n}"] for n in ["ok.py", "undef.py", "slow.py", "gone.py", "skip.py"]}
    tbl = repro_write_scripts(code, None, None, root, lang="Python")
    calls: list[list[str]] = []

    def fake(
        args: list[str], stdout: str | None = None, stderr: str | None = None, **kw: Any
    ) -> Any:
        calls.append(args)
        if args[0] == "stop":
            return {"status": 0, "timeout": False}
        script = args[-1]
        if script.endswith("ok.py"):
            Path(stdout or "").write_text("t(10) = 2.5, p = .03\n")
            return {"status": 0, "timeout": False}
        if script.endswith("undef.py"):
            Path(stderr or "").write_text("Traceback\nNameError: name 'zz' is not defined\n")
            return {"status": 1, "timeout": False}
        if script.endswith("slow.py"):
            return {"status": None, "timeout": True}
        return OSError("docker vanished")

    monkeypatch.setattr(docker, "_docker", fake)
    out = repro_run_scripts_py_docker(
        tbl,
        ["ok.py", "undef.py", "slow.py", "gone.py"],
        sandbox_root=root,
        lib_dir=lib,
        timeout=7,
        skip=["skip.py"],
    )
    assert out["outcome"].tolist() == [
        "ran_ok",
        "errored",
        "timed_out",
        "errored",
        "skipped_missing_inputs",
    ]
    assert _na(out["error_type"]) == [None, "undefined_variable", "timeout", "runtime", None]
    assert out["undefined_var"].tolist()[1] == "zz"
    assert out["stdout"].tolist()[0] == "t(10) = 2.5, p = .03"
    run = calls[0]
    # the run phase gets the same sandbox as R code
    assert run[run.index("--network") + 1] == "none"
    assert "--read-only" in run
    # compiled code in /tmp may run (metacheck 0.4.3.7); nosuid and nodev stay
    assert run[run.index("--tmpfs") + 1] == "/tmp:exec,nosuid,nodev"
    assert run[run.index("--cap-drop") + 1] == "ALL"
    assert run[run.index("--pids-limit") + 1] == "512"
    assert "no-new-privileges" in run
    assert run[run.index("--user") + 1] != "0:0"
    assert run[run.index("-w") + 1] == "/sandbox"
    env = [run[k + 1] for k, a in enumerate(run) if a == "-e"]
    assert env == [
        "PWD=/sandbox",
        "HOME=/tmp",
        "NUMBA_CACHE_DIR=/tmp/numba",
        "MPLCONFIGDIR=/tmp/matplotlib",
        "XDG_CACHE_HOME=/tmp/.cache",
        "MPLBACKEND=Agg",
        "QT_QPA_PLATFORM=offscreen",
        "PYTHONPATH=/pylib",
    ]
    assert f"{os.path.realpath(lib)}:/pylib:ro" in run  # read-only, unlike metacheck's (#575)
    assert run[-3:] == [
        "ghcr.io/scienceverse/metacheck_py_small:latest",
        "python",
        "/sandbox/ok.py",
    ]
    assert next(c for c in calls if c[0] == "stop")[:3] == ["stop", "--time", "5"]


def test_run_scripts_py_docker_scrubs_links(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "sb"
    root.mkdir()
    tbl = repro_write_scripts({"a.py": ["x = 1"]}, None, None, root, lang="Python")

    def fake(args: list[str], **kw: Any) -> Any:
        os.symlink("/etc/passwd", root / "leak")
        return {"status": 0, "timeout": False}

    monkeypatch.setattr(docker, "_docker", fake)
    repro_run_scripts_py_docker(tbl, ["a.py"], sandbox_root=root)
    assert not os.path.lexists(root / "leak")


def test_run_scripts_py_docker_empty(tmp_path: Path) -> None:
    assert len(repro_run_scripts_py_docker(None, [], sandbox_root=tmp_path)) == 0
