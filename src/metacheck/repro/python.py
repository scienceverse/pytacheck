"""The reproducibility-check helpers for Python code.

Port of ``R/reproducibility_check_python.R``: what has no shared, language-parametrised
home in :mod:`metacheck.repro.core` because the Python ecosystem differs from R's. The
dependencies come from ``import`` statements and from a ``requirements.txt`` or
``pyproject.toml`` (:func:`repro_dependencies_py`), the standard library takes the place
of R's base packages, and the tools are ``pip`` and ``python``, not
``install.packages()`` and ``Rscript``.

The execution helpers here (:func:`repro_install_deps_py`, :func:`repro_run_scripts_py`)
run on this machine, with no filesystem or network isolation: the module reaches them
only with ``sandbox="process"``, which must be asked for by name (D62). The default is
the Docker backend in :mod:`metacheck.repro.docker`. A script runs whole, as one
``python`` process: there is no Python counterpart of the R backend's capture of
statistical objects, so ``captures`` is always ``None``; the module reads the
statistics a script prints from its output instead.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r.base import as_character, r_sort_key, trimws
from metacheck._r.regex import grepl, regexec, regextract, strsplit, sub
from metacheck._values import as_str
from metacheck.repro.core import (
    _chr_list,
    _code_remove_comments,
    _code_text_items,
    _col,
    _deps_frame,
    _frame,
    _install_frame,
    _message,
    _ordered_names,
    _pre_run_outcome,
    _r_dirname,
    _read_cap,
    _run_frame,
    _run_row,
    _script_lines,
    _unique,
)

__all__ = [
    "repro_dependencies_py",
    "repro_install_deps_py",
    "repro_run_scripts_py",
]

#: The top-level modules of Python's standard library: a run never needs to install them.
#: metacheck's static list (``.repro_py_stdlib_modules()``, Python 3.10+, with modules
#: removed since, which old code still imports), plus the public names it misses;
#: ``tests/repro_core/test_python.py`` checks it covers ``sys.stdlib_module_names``.
_PY_STDLIB = frozenset(
    """
    __future__ _thread abc aifc annotationlib antigravity argparse array ast asynchat
    asyncio asyncore atexit audioop base64 bdb binascii binhex bisect builtins bz2
    cProfile calendar cgi cgitb chunk cmath cmd code codecs codeop collections colorsys
    compileall compression concurrent configparser contextlib contextvars copy copyreg
    crypt csv ctypes curses dataclasses datetime dbm decimal difflib dis distutils
    doctest email encodings ensurepip enum errno faulthandler fcntl filecmp fileinput
    fnmatch formatter fractions ftplib functools gc genericpath getopt getpass gettext
    glob graphlib grp gzip hashlib heapq hmac html http idlelib imaplib imghdr imp
    importlib inspect io ipaddress itertools json keyword lib2to3 linecache locale
    logging lzma macpath mailbox mailcap marshal math mimetypes mmap modulefinder msilib
    msvcrt multiprocessing netrc nis nntplib nt ntpath nturl2path numbers opcode
    operator optparse os ossaudiodev parser pathlib pdb pickle pickletools pipes pkgutil
    platform plistlib poplib posix posixpath pprint profile pstats pty pwd py_compile
    pyclbr pydoc pydoc_data pyexpat queue quopri random re readline reprlib resource
    rlcompleter runpy sched secrets select selectors shelve shlex shutil signal site
    smtpd smtplib sndhdr socket socketserver spwd sqlite3 sre_compile sre_constants
    sre_parse ssl stat statistics string stringprep struct subprocess sunau symbol
    symtable sys sysconfig syslog tabnanny tarfile telnetlib tempfile termios test
    textwrap this threading time timeit tkinter token tokenize tomllib trace traceback
    tracemalloc tty turtle turtledemo types typing unicodedata unittest urllib uu uuid
    venv warnings wave weakref webbrowser winreg winsound wsgiref xdrlib xml xmlrpc
    zipapp zipfile zipimport zlib zoneinfo
    """.split()  # noqa: SIM905 - a word list, kept readable
)

#: The PyPI distribution of a module whose import name is not its own (``import sklearn``
#: is ``pip install scikit-learn``; the ``sklearn`` distribution only fails). metacheck
#: installs the import name.
_PY_DIST_NAMES = {
    "Bio": "biopython",
    "Crypto": "pycryptodome",
    "MySQLdb": "mysqlclient",
    "OpenSSL": "pyOpenSSL",
    "PIL": "Pillow",
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python-headless",
    "dateutil": "python-dateutil",
    "docx": "python-docx",
    "dotenv": "python-dotenv",
    "fitz": "PyMuPDF",
    "jwt": "PyJWT",
    "magic": "python-magic",
    "pptx": "python-pptx",
    "serial": "pyserial",
    "skimage": "scikit-image",
    "sklearn": "scikit-learn",
    "umap": "umap-learn",
    "yaml": "PyYAML",
    "zmq": "pyzmq",
}

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"


def _canonical(name: str) -> str:
    """A PyPI name as pip compares it (PEP 503: case and ``-_.`` runs do not matter)."""
    from packaging.utils import canonicalize_name

    return str(canonicalize_name(name))


def _py_dist_name(module: str) -> str:
    """The distribution ``pip install`` needs for the top-level module *module*."""
    return _PY_DIST_NAMES.get(module, module)


def _python(python_bin: str | None = None) -> str | None:
    """The ``python`` to run: *python_bin* on ``PATH`` (or a path), else ``python3``, ``python``."""
    if python_bin is not None:
        return shutil.which(python_bin)
    return shutil.which("python3") or shutil.which("python")


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------


def _requirement_name(req: str) -> str | None:
    """The distribution a requirement line names (``None`` when it names none)."""
    from packaging.requirements import InvalidRequirement, Requirement

    try:
        return Requirement(req).name
    except InvalidRequirement:
        egg = regextract(r"#egg=([A-Za-z0-9][A-Za-z0-9._-]*)", req, perl=True)
        return None if egg is None else egg[len("#egg=") :]


def _poetry_requirement(name: str, spec: Any) -> str:
    """A Poetry dependency as a pip requirement: a plain version constraint keeps its pin.

    Poetry's caret, tilde and wildcard constraints are not pip's, so those install the
    name only (metacheck passes ``numpy^1.26``, which pip refuses).
    """
    version = spec.get("version") if isinstance(spec, Mapping) else spec
    v = trimws(version) if isinstance(version, str) else ""
    if grepl(r"^[0-9]+(\.[0-9]+)*$", v):
        return f"{name}=={v}"
    if grepl(r"^(==|>=|<=|!=|~=|>|<)\s*[0-9]", v) and not grepl(r"[*^]", v):
        return f"{name}{v}"
    return name


def _repro_py_manifest_pins(manifest_text: Any) -> pd.DataFrame:
    """Per-package requirements from a ``requirements.txt`` or ``pyproject.toml``.

    Port of ``.repro_py_manifest_pins()``: ``package`` (the canonical PyPI name) and
    ``requirement`` (the text to give ``pip install``). A ``requirements.txt`` line keeps
    its pin; comments, blank lines and option lines (``-r``, ``-e``, ``--hash``, ...) name
    no package. A ``pyproject.toml`` is read with :mod:`tomllib`: ``[project]
    dependencies`` and ``[tool.poetry.dependencies]`` (metacheck takes every quoted
    string of the file, so ``name = "analysis"`` became a requirement too).
    """
    rows: list[tuple[str, str]] = []

    def result() -> pd.DataFrame:
        out = list(dict.fromkeys(rows))
        return _frame(
            {
                "package": ("string", [r[0] for r in out]),
                "requirement": ("string", [r[1] for r in out]),
            }
        )

    lines = [x for x in _chr_list(manifest_text) if x is not None]
    if not lines:
        return result()
    stripped = [trimws(x) for x in lines]
    is_toml = any(grepl(r"^\[(project|tool\.poetry)|^dependencies\s*=", stripped))
    if is_toml:
        try:
            doc = tomllib.loads("\n".join(lines))
        except tomllib.TOMLDecodeError:
            return result()
        project = doc.get("project")
        deps = project.get("dependencies") if isinstance(project, Mapping) else None
        for req in deps if isinstance(deps, list) else []:
            name = _requirement_name(req) if isinstance(req, str) else None
            if name is not None:
                rows.append((_canonical(name), trimws(req)))
        tool = doc.get("tool")
        poetry = tool.get("poetry") if isinstance(tool, Mapping) else None
        pdeps = poetry.get("dependencies") if isinstance(poetry, Mapping) else None
        for name, spec in (pdeps or {}).items() if isinstance(pdeps, Mapping) else []:
            if _canonical(name) != "python":
                rows.append((_canonical(name), _poetry_requirement(name, spec)))
        return result()

    for line in stripped:
        if line == "" or line.startswith(("#", "-")):
            continue
        # an inline comment and per-requirement options (--hash=...) are not pip install's
        req = trimws(sub(r"\s+(#|--).*$", "", line, perl=True))
        name = _requirement_name(req)
        if name is not None:
            rows.append((_canonical(name), req))
    return result()


def repro_dependencies_py(code_text: Any, manifest_text: Any = None) -> pd.DataFrame:
    """Collect the package dependencies a set of Python files declare.

    Port of ``R/reproducibility_check_python.R::repro_dependencies_py()``. *code_text* is
    one file's code (a string or its lines) or a list (or mapping) of such, pooled across
    files; *manifest_text* the lines of the repository's ``requirements.txt`` or
    ``pyproject.toml``, if it has one. Returns ``package`` (the top-level module
    imported), ``source`` (``pypi``, or ``base`` for the standard library), ``ref`` (the
    manifest's requirement for that package, else NA) and ``base``, one row per package.
    A manifest entry matches an import by its PyPI name too (``scikit-learn`` is
    ``import sklearn``).
    """
    from metacheck.repro.core import _is_file_list

    empty = _deps_frame([], [], [], [])
    if code_text is None:
        return empty
    if _is_file_list(code_text):
        items = list(code_text.values()) if isinstance(code_text, Mapping) else list(code_text)
        parts = [repro_dependencies_py(ct, manifest_text=manifest_text) for ct in items]
        parts = [d for d in parts if len(d) > 0]
        if not parts:
            return empty
        rows = [r for d in parts for r in d.itertuples(index=False)]
        # a row with the manifest's pin wins over an import-only one of the same package
        rows.sort(key=lambda r: (r_sort_key(r.package), as_str(r.ref) is None))
        keep = []
        seen: set[Any] = set()
        for r in rows:
            if r.package not in seen:
                seen.add(r.package)
                keep.append(r)
        return _deps_frame(
            [r.package for r in keep],
            [r.source for r in keep],
            [as_str(r.ref) for r in keep],
            [bool(r.base) for r in keep],
        )

    from metacheck.codecheck.core import code_library_names

    names_df = code_library_names(code_text, "Python")
    pkgs = [p for p in _unique(_col(names_df, "package")) if p is not None] if len(names_df) else []
    if not pkgs:
        return empty
    pins = _repro_py_manifest_pins(manifest_text)
    pin_of = dict(zip(_col(pins, "package"), _col(pins, "requirement"), strict=True))
    base = [p in _PY_STDLIB for p in pkgs]
    ref: list[str | None] = []
    for p, b in zip(pkgs, base, strict=True):
        hit = None
        if not b:
            hit = pin_of.get(_canonical(_py_dist_name(p))) or pin_of.get(_canonical(p))
        ref.append(hit)
    return _deps_frame(pkgs, ["base" if b else "pypi" for b in base], ref, base)


def _py_requirement(package: str | None, ref: str | None) -> str:
    """What ``pip install`` gets for one dependency: the manifest's requirement, else its name."""
    if ref is not None and ref != "":
        return ref
    return _py_dist_name(package or "")


def _requirements(install_deps: pd.DataFrame) -> list[str]:
    refs = _chr_list(install_deps["ref"]) if "ref" in install_deps.columns else None
    pkgs = _chr_list(install_deps["package"])
    return [_py_requirement(p, refs[i] if refs else None) for i, p in enumerate(pkgs)]


def _install_rows(
    pkgs: Sequence[str | None], srcs: Sequence[str | None], ok: Sequence[bool], msgs: Sequence[str]
) -> pd.DataFrame:
    """The install result frame: ``via_archive`` is always false and ``category`` NA (pip has
    no archive to fall back on, and its messages are not classified)."""
    return _install_frame(
        [
            {
                "package": p,
                "source": s,
                "installed": o,
                "message": m,
                "via_archive": False,
                "category": None,
            }
            for p, s, o, m in zip(pkgs, srcs, ok, msgs, strict=True)
        ]
    )


def repro_install_deps_py(
    install_deps: pd.DataFrame | None,
    lib_dir: str | os.PathLike[str],
    python_bin: str | None = None,
    timeout: float = 300,
) -> pd.DataFrame:
    """Install a paper's Python dependencies into a throwaway directory, on this machine.

    Port of ``R/reproducibility_check_python.R::repro_install_deps_py()``: one
    ``python -m pip install --target <lib_dir>`` per dependency (the manifest's pinned
    requirement when there is one, else the PyPI name of the module), so the host's own
    packages are not touched. Installing runs a package's own build code, unisolated: the
    module calls this only with ``sandbox="process"``. Same columns as
    :func:`~metacheck.repro.core.repro_install_deps`.
    """
    if install_deps is None or len(install_deps) == 0:
        return _install_frame([])
    exe = _python(python_bin)
    if exe is None:
        raise RuntimeError(f"Python executable '{python_bin or 'python3'}' was not found on PATH.")
    lib = os.fspath(lib_dir)
    os.makedirs(lib, exist_ok=True)
    reqs = _requirements(install_deps)
    pip = [exe, "-m", "pip", "install", "--quiet", "--no-input", "--disable-pip-version-check"]
    ok: list[bool] = []
    msgs: list[str] = []
    for req in reqs:
        try:
            res = subprocess.run(  # noqa: S603 - pip of the configured python
                [*pip, "--no-cache-dir", "--target", lib, req],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
            good, msg = res.returncode == 0, trimws(res.stderr or "")
        except subprocess.TimeoutExpired:
            good, msg = False, f"pip install timed out after {as_character(timeout)}s"
        except OSError as exc:
            good, msg = False, str(exc)
        ok.append(good)
        msgs.append("" if good else msg)
    return _install_rows(
        _chr_list(install_deps["package"]), _chr_list(install_deps["source"]), ok, msgs
    )


# ---------------------------------------------------------------------------
# running the scripts
# ---------------------------------------------------------------------------

_PY_ERROR_PAT = r"^([A-Za-z_][A-Za-z0-9_.]*Error)(\s*:.*)?$"
_PY_NAME_PAT = r"""name ['"]([^'"]+)['"] is not defined"""
_PY_NOMOD_PAT = r"""No module named ['"]([^'"]+)['"]"""


def _classify_py_error(
    stderr: str, failed_deps: Sequence[str] | str
) -> tuple[str, str, str | None, bool]:
    """``(error, error_type, undefined_var, dependency_unavailable)`` of a failed script.

    The error is the traceback's last line (``NameError: name 'x' is not defined``). Its
    type is ``undefined_variable`` for a ``NameError``, ``dependency_unavailable`` when
    the module a ``No module named`` names (its top-level package) could not be installed,
    else the exception's class name, or ``runtime`` (metacheck: NA) when the last line
    names none.
    """
    lines = [x for x in stderr.split("\n") if trimws(x) != ""]
    last = lines[-1] if lines else stderr
    m = regextract(_PY_ERROR_PAT, last, perl=True)
    exc_type = sub(_PY_ERROR_PAT, r"\1", m, perl=True) if m is not None else None
    undef: str | None = None
    if exc_type == "NameError":
        hit = regextract(_PY_NAME_PAT, last, perl=True)
        undef = sub(_PY_NAME_PAT, r"\1", hit, perl=True) if hit is not None else None
    nomod = regextract(_PY_NOMOD_PAT, stderr, perl=True)
    missing = sub(_PY_NOMOD_PAT, r"\1", nomod, perl=True) if nomod is not None else None
    dep_unavailable = missing is not None and missing.split(".")[0] in _chr_list(failed_deps)
    if dep_unavailable:
        etype = "dependency_unavailable"
    elif undef is not None:
        etype = "undefined_variable"
    else:
        etype = exc_type or "runtime"
    return last, etype, undef, dep_unavailable


def _py_run_row(
    fn: str,
    returncode: int | None,
    timed_out: bool,
    so: str,
    se: str,
    elapsed: float,
    exec_lines: list[str],
    timeout: float,
    failed_deps: Sequence[str] | str,
) -> dict[str, Any]:
    """The run-result row of one script, from its exit status and output (both backends)."""
    if timed_out:
        msg = f"timed out after {as_character(timeout)}s"
        return _run_row(fn, "timed_out", msg, "timeout", None, so, se, elapsed, exec_lines)
    if returncode != 0:
        last, etype, undef, dep_unavailable = _classify_py_error(se, failed_deps)
        outcome = "dependency_unavailable" if dep_unavailable else "errored"
        return _run_row(fn, outcome, last, etype, undef, so, se, elapsed, exec_lines)
    return _run_row(fn, "ran_ok", "", None, None, so, se, elapsed, exec_lines)


def repro_run_scripts_py(
    run_tbl: pd.DataFrame | None,
    order: Any,
    lib_dir: str | os.PathLike[str] | None = None,
    python_bin: str | None = None,
    timeout: float = 600,
    skip: Sequence[str] | str = (),
    parses: Mapping[str, Any] | pd.Series | None = None,
    failed_deps: Sequence[str] | str = (),
) -> pd.DataFrame:
    """Run the paper's Python scripts, in order, each in a ``python`` subprocess on this machine.

    Port of ``R/reproducibility_check_python.R::repro_run_scripts_py()``. Each script of
    *run_tbl* (``file_name``, ``script_path``, ``run_dir``) runs whole from its
    ``run_dir``, with *lib_dir* first on ``PYTHONPATH`` and a per-script *timeout*. A
    subprocess isolates a crash, nothing else: the code can read, change and delete
    anything this process can, and use the network. The module calls this only with
    ``sandbox="process"``; :func:`~metacheck.repro.docker.repro_run_scripts_py_docker` is
    the sandbox. Same columns and outcomes as
    :func:`~metacheck.repro.core.repro_run_scripts`; ``captures`` is always ``None``.
    """
    if run_tbl is None or len(run_tbl) == 0:
        return _run_frame([])
    exe = _python(python_bin)
    if exe is None:
        raise RuntimeError(f"Python executable '{python_bin or 'python3'}' was not found on PATH.")
    from metacheck.utils import pb

    ordered = _ordered_names(run_tbl, order)
    names = _chr_list(run_tbl["file_name"])
    paths = _chr_list(run_tbl["script_path"])
    dirs = _chr_list(run_tbl["run_dir"])
    env = dict(os.environ)
    if lib_dir is not None and os.path.isdir(lib_dir):
        old = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join([os.fspath(lib_dir), *([old] if old else [])])

    bar = pb(len(ordered), ":what [:bar] :current/:total")
    rows: list[dict[str, Any]] = []
    try:
        bar.tick(0, {"what": ""})
        for fn in ordered:
            bar.tick(1, {"what": fn})
            i = names.index(fn)
            pre = _pre_run_outcome(fn, skip, parses)
            if pre is not None:
                rows.append(pre)
                continue
            exec_lines = _script_lines(paths[i])
            _message(
                "[repro/py]   -> running '", fn, "' (timeout ", as_character(timeout), "s) ..."
            )
            with tempfile.TemporaryDirectory(prefix="pytacheck_pyrun_") as tmp:
                out_file = os.path.join(tmp, "out.txt")
                err_file = os.path.join(tmp, "err.txt")
                t0 = time.monotonic()
                returncode: int | None = None
                timed_out = False
                with open(out_file, "wb") as out, open(err_file, "wb") as err:
                    try:
                        proc = subprocess.run(  # noqa: S603 - the configured python
                            [exe, paths[i] or ""],
                            cwd=dirs[i],
                            stdout=out,
                            stderr=err,
                            stdin=subprocess.DEVNULL,
                            timeout=timeout,
                            env=env,
                            check=False,
                        )
                        returncode = proc.returncode
                    except subprocess.TimeoutExpired:
                        timed_out = True
                elapsed = time.monotonic() - t0
                so, se = _read_cap(out_file), _read_cap(err_file)
            _message("[repro/py]   <- '", fn, "' done in ", as_character(round(elapsed, 1)), "s")
            rows.append(
                _py_run_row(
                    fn, returncode, timed_out, so, se, elapsed, exec_lines, timeout, failed_deps
                )
            )
    finally:
        bar.terminate()
    return _run_frame(rows)


# ---------------------------------------------------------------------------
# static checks of the scripts
# ---------------------------------------------------------------------------

_PY_ASSIGN_PAT = rf"^({_IDENT})\s*=(?!=)"
_PY_DEFCLASS_PAT = rf"^(?:async\s+def|def|class)\s+({_IDENT})"


def _repro_py_defined_vars(code_text_list: Any) -> pd.DataFrame:
    """The names each Python script defines at top level.

    Port of ``.repro_py_defined_vars()``: assignments (``x = ...``, not ``==``) and
    ``def``/``class`` statements at the start of a comment-free line, so an indented one
    (inside a function or a block) is not taken. Returns ``file_name`` and a list column
    ``defines``.
    """
    if code_text_list is None or len(code_text_list) == 0:
        return _frame({"file_name": ("string", [])})
    fnames, texts = _code_text_items(code_text_list)
    defines: list[list[str]] = []
    for ct in texts:
        nc = [x for x in _code_remove_comments(ct, "Python") if x is not None]
        found: list[str] = []
        for pat in (_PY_ASSIGN_PAT, _PY_DEFCLASS_PAT):
            found.extend(
                sub(pat + ".*", r"\1", m, perl=True)
                for m in regextract(pat, nc, perl=True)
                if m is not None
            )
        defines.append(_unique(v for v in found if v != ""))
    return _frame({"file_name": ("string", fnames), "defines": ("object", defines)})


def _repro_py_parses(code_text: Sequence[str | None]) -> bool:
    """Whether a Python script parses (compiled to a syntax tree only; nothing is run).

    metacheck has no static check for Python, so a syntax error (Python 2's ``print x``,
    say) only shows when the script is run, as an error. The check uses this
    interpreter's grammar.
    """
    src = "\n".join("" if x is None else x for x in code_text)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # invalid escape sequences and the like
        try:
            ast.parse(src)
        except (SyntaxError, ValueError):
            return False
        except (RecursionError, MemoryError):  # too deeply nested to check: let it run
            return True
    return True


# ---------------------------------------------------------------------------
# the interpreter, Python 2, the repository's own modules
# ---------------------------------------------------------------------------


def _repro_py2_modules() -> list[str]:
    """Port of ``R/reproducibility_check_python.R::.repro_py2_modules()``.

    Modules of Python 2's standard library, which have other names in Python 3 and cannot
    be installed: importing one shows that the code is Python 2 (#568, item 13).
    """
    return [
        "cPickle", "cStringIO", "StringIO", "Queue", "BaseHTTPServer", "SocketServer",
        "SimpleHTTPServer", "CGIHTTPServer", "Tkinter", "tkFileDialog", "tkMessageBox",
        "ttk", "urlparse", "urllib2", "httplib", "ConfigParser", "commands", "HTMLParser",
        "cookielib", "exceptions", "__builtin__", "sets", "thread", "dummy_thread", "anydbm",
        "dumbdbm", "gdbm", "whichdb", "copy_reg", "repr", "xmlrpclib", "htmlentitydefs",
    ]  # fmt: skip


def _repro_find_python(python_bin: str | None = "python") -> str:
    """Port of ``R/reproducibility_check_python.R::.repro_find_python()``.

    The Python interpreter to run: *python_bin* when it is on the path and works, else
    ``python3`` (the only name on many macOS and Linux machines). "Works" is checked by
    running it, because on Windows ``python3`` can be a placeholder that only opens the
    Microsoft Store. Returns ``""`` when neither works.

    Unlike :func:`_python`, which only looks a name up (``python3`` before ``python`` when
    no name is given, and no second candidate for a name that is given), this tries
    *python_bin* first and then ``python3``, each by running it.
    """
    for cand in _unique(c for c in (python_bin, "python3") if c is not None):
        exe = shutil.which(cand)
        if not exe:
            continue
        try:
            res = subprocess.run(  # noqa: S603 - a configured python, run to see that it works
                [exe, "-c", "import sys"],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0:
            return str(exe)
    return ""


def _repro_python_missing_reason() -> str:
    """Port of ``R/reproducibility_check_python.R::.repro_python_missing_reason()``.

    Why no Python interpreter can be used, for the report. R names a missing ``processx``
    package as the other possible reason; Python needs no such package.
    """
    return "no Python interpreter (`python` or `python3`) was found on this computer"


def _repro_py2_error(txt: Any) -> Any:
    """Port of ``R/reproducibility_check_python.R::.repro_py2_error()``.

    Whether a run's error output shows Python 2 code: a print or exec statement, the form
    ``except E, e``, or tabs mixed with spaces, which Python 2 accepted. The image only
    has Python 3, and converting the code could change results without a word (``/``
    between whole numbers divides differently), so these are only named. One value gives
    a bool, a sequence a list of bools; a missing value reads as no text.
    """
    pat = (
        r"SyntaxError: (Missing parentheses in call to|multiple exception types must be "
        r"parenthesized)|TabError:|No module named '(" + "|".join(_repro_py2_modules()) + ")'"
    )
    if txt is None or isinstance(txt, str):
        return bool(grepl(pat, txt or "", perl=True))
    return [bool(grepl(pat, t or "", perl=True)) for t in _chr_list(txt)]


def _repro_py_stdlib_modules() -> list[str]:
    """Port of ``R/reproducibility_check_python.R::.repro_py_stdlib_modules()``.

    The Python standard library's top-level module names (3.10+), analogous to
    ``.repro_base_packages()``: a run never needs to ``pip install`` these. A static list,
    as R's, because the standard library is not queryable without running an interpreter.
    A module missing from it is reported ``source = "pypi"`` and a ``pip install`` is
    attempted for it at run time, which fails harmlessly.

    This is metacheck's list exactly. ``_PY_STDLIB`` (used for the dependencies) holds
    these and more names of modules removed since, which suits a dependency check but
    would hide a repository's own ``parser.py`` here, so it is not used for this.
    """
    return """
        __future__ _thread abc aifc argparse array ast asyncio atexit base64 bdb binascii
        bisect builtins bz2 calendar cgi cgitb chunk cmath cmd code codecs codeop collections
        colorsys compileall concurrent configparser contextlib contextvars copy copyreg
        cProfile csv ctypes curses dataclasses datetime dbm decimal difflib dis distutils
        doctest email encodings ensurepip enum errno faulthandler fcntl filecmp fileinput
        fnmatch fractions ftplib functools gc getopt getpass gettext glob graphlib grp gzip
        hashlib heapq hmac html http idlelib imaplib imghdr imp importlib inspect io
        ipaddress itertools json keyword lib2to3 linecache locale logging lzma mailbox
        mailcap marshal math mimetypes mmap modulefinder msilib msvcrt multiprocessing netrc
        nis nntplib numbers operator optparse os ossaudiodev pathlib pdb pickle pickletools
        pipes pkgutil platform plistlib poplib posix posixpath pprint profile pstats pty pwd
        py_compile pyclbr pydoc queue quopri random re readline reprlib resource rlcompleter
        runpy sched secrets select selectors shelve shlex shutil signal site smtpd smtplib
        sndhdr socket socketserver spwd sqlite3 ssl stat statistics string stringprep struct
        subprocess sunau symtable sys sysconfig syslog tabnanny tarfile telnetlib tempfile
        termios test textwrap this threading time timeit tkinter token tokenize tomllib trace
        traceback tracemalloc tty turtle turtledemo types typing unicodedata unittest urllib
        uu uuid venv warnings wave weakref webbrowser winreg winsound wsgiref xdrlib xml
        xmlrpc zipapp zipfile zipimport zlib zoneinfo
    """.split()  # noqa: SIM905 - a word list, kept readable


def _repro_py_depth_shared(a: str, b: str) -> int:
    """How many leading folders two ``/``-separated paths share."""
    x, y = (a.split("/") if a else []), (b.split("/") if b else [])
    n = min(len(x), len(y))
    for i in range(n):
        if x[i] != y[i]:
            return i
    return n


def _repro_py_module_folders(
    code_text: Sequence[str | None] | str | None,
    script_path: str,
    files: Sequence[str | None] | str | None,
) -> list[str]:
    """Port of ``R/reproducibility_check_python.R::.repro_py_module_folders()``.

    The folders Python must look in to find the repository's own modules that a script
    imports. Python only looks beside the script, so ``from maad import sound`` stopped
    with "No module named 'maad'" while ``maad/`` is in the repository: 365 scripts in 23
    papers (#568, item 10). For each imported name that is no standard module, the folder
    holding a file or folder of that name, nearest to the script first. A folder is left
    out when it would hide a standard module the script uses, such as one with its own
    ``json.py``, since these folders come before the standard library. *files* are the
    paths in the sandbox from its top; returns folders from the top.
    """
    txt = [t for t in _chr_list(code_text) if t is not None and not grepl(r"^\s*#", t)]
    names1: list[str] = []
    for m in regexec(r"^\s*import\s+([A-Za-z0-9_., ]+)", txt):
        if len(m) > 1:
            names1.extend(trimws(sub(r"\s+as\s+.*$", "", part)) for part in strsplit(m[1], ","))
    names2 = [
        m[1]
        for m in regexec(r"^\s*from\s+([A-Za-z0-9_]+)[A-Za-z0-9_.]*\s+import\s", txt)
        if len(m) > 1
    ]
    tops = _unique(sub(r"\..*$", "", n) for n in [*names1, *names2])
    stdlib = set(_repro_py_stdlib_modules())
    own = [t for t in tops if t not in stdlib]
    file_list = [f.replace("\\", "/") for f in _chr_list(files) if f is not None]
    if not own or not file_list:
        return []
    script_dir = _r_dirname((script_path or "").replace("\\", "/")) or ""
    used_std = [t for t in tops if t in stdlib]

    def hides(folder: str) -> bool:
        prefix = "" if folder == "." else folder + "/"
        names = {f"{prefix}{s}.py" for s in used_std}
        return any(f in names for f in file_list) or any(
            f.startswith(f"{prefix}{s}/") for s in used_std for f in file_list
        )

    out: list[str] = []
    for nm in own:
        hit = [f for f in file_list if grepl(f"(^|/){nm}(\\.py$|/)", f)]
        if not hit:
            continue
        # the folder that holds the module, not one further down
        parents: list[str] = []
        for f in hit:
            parts = f.split("/")
            k = next(i for i, p in enumerate(parts) if p in (nm, f"{nm}.py"))
            parents.append("." if k < 1 else "/".join(parts[:k]))
        parents = sorted(_unique(parents), key=lambda p: -_repro_py_depth_shared(p, script_dir))
        parents = [p for p in parents if not hides(p)]
        if parents and parents[0] != script_dir:
            out.append(parents[0])
    return _unique(out)


def _repro_py_get_ipython(txt: Sequence[str | None] | str | None) -> list[str | None]:
    """Port of ``R/reproducibility_check_python.R::.repro_py_get_ipython()``.

    A stand-in for ``get_ipython()``, which only exists inside IPython: without it a
    notebook's ``if "google.colab" in str(get_ipython()):`` stops with a NameError. Its
    methods (``run_line_magic``, ``system``) do nothing, as the commands are left out too.
    Placed after leading comments (an encoding line must stay on top) and after ``from
    __future__`` imports (which must come first).
    """
    lines = _chr_list(txt)
    if not any(grepl(r"\bget_ipython\s*\(", lines)) or any(
        grepl(r"\bget_ipython\s*=|def\s+get_ipython\b", lines)
    ):
        return lines
    stub = (
        'get_ipython = lambda: type("ipython", (), {"__getattr__": lambda self, name: '
        "(lambda *a, **k: None)})()  # [reproducibility_check added]"
    )
    future = [i for i, g in enumerate(grepl(r"^\s*from\s+__future__\s+import", lines)) if g]
    if future:
        after = max(future) + 1
    else:
        # the leading lines that are blank or comments
        after = 0
        for g in grepl(r"^\s*(#.*)?$", lines):
            if not g:
                break
            after += 1
    return [*lines[:after], stub, *lines[after:]]


_REPRO_DOCKER_DEFAULT_IMAGE_PY2 = "python:2.7-slim"


def _repro_docker_image_py2() -> str | None:
    """Port of ``R/reproducibility_check_python_docker.R::.repro_docker_image_py2()``.

    The image that runs code written for Python 2 (#568): the official Python 2.7 image.
    pip there installs the last version of each package that supports Python 2. The
    option ``metacheck.docker_image_py2`` names another image; set it to ``False`` or
    ``""`` to not run Python 2 code (it is then only named). ``None`` when there is none.
    """
    from metacheck.utils import get_option

    img = get_option("metacheck.docker_image_py2", _REPRO_DOCKER_DEFAULT_IMAGE_PY2)
    first = img[0] if isinstance(img, list | tuple) and img else img
    if img is False or img is None or first is None or first is False or not str(first):
        return None
    return str(first)


def _repro_py_pip_names() -> dict[str, str]:
    """Port of ``R/reproducibility_check_python.R::.repro_py_pip_names()``.

    Import names whose pip package has a different name. Python imports are
    case-sensitive, so the keys are matched exactly. A name not listed here is installed
    under its import name.
    """
    return {
        "sklearn": "scikit-learn", "skimage": "scikit-image", "cv2": "opencv-python",
        "PIL": "Pillow", "yaml": "PyYAML", "bs4": "beautifulsoup4",
        "dateutil": "python-dateutil", "Bio": "biopython", "docx": "python-docx",
        "pptx": "python-pptx", "umap": "umap-learn", "mpl_toolkits": "matplotlib",
        "fitz": "PyMuPDF", "Crypto": "pycryptodome", "serial": "pyserial",
        "dotenv": "python-dotenv", "community": "python-louvain", "wx": "wxPython",
        "zmq": "pyzmq", "attr": "attrs", "jwt": "PyJWT", "OpenSSL": "pyOpenSSL",
        "pywt": "PyWavelets", "ruamel": "ruamel.yaml", "osgeo": "GDAL",
        "pkg_resources": "setuptools", "pylab": "matplotlib", "skbio": "scikit-bio",
        "gi": "PyGObject", "git": "GitPython", "MySQLdb": "mysqlclient",
        "psycopg2": "psycopg2-binary", "argon2": "argon2-cffi", "elftools": "pyelftools",
        "paho": "paho-mqtt", "past": "future", "vcf": "PyVCF3",
        "rapidjson": "python-rapidjson", "gitlab": "python-gitlab", "primer3": "primer3-py",
        "hydra": "hydra-core", "skbuild": "scikit-build", "pytorch_grad_cam": "grad-cam",
        "rest_framework": "djangorestframework",
        "rest_framework_api_key": "djangorestframework-api-key",
        "corsheaders": "django-cors-headers", "django_filters": "django-filter",
        "allauth": "django-allauth", "dirtyfields": "django-dirtyfields",
        "silk": "django-silk", "crum": "django-crum", "django_auth_ldap": "django-auth-ldap",
        "logstash_async": "python-logstash-async",
    }  # fmt: skip


def _repro_py_pip_name(module: Sequence[str | None] | str | None) -> Any:
    """Port of ``R/reproducibility_check_python.R::.repro_py_pip_name()``.

    The pip name for each import name (only the top-level module counts, so
    ``sklearn.linear_model`` gives ``scikit-learn``). A string gives a string, a sequence a
    list.
    """
    mapping = _repro_py_pip_names()

    def one(m: str | None) -> str | None:
        if m is None:
            return None
        top = str(sub(r"\..*$", "", m))
        return mapping.get(top, top)

    if module is None or isinstance(module, str):
        return one(module)
    return [one(m) for m in _chr_list(module)]
