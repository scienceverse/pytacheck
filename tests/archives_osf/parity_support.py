"""Python side of the ``archives_osf_review`` parity cases.

The R side of those cases runs metacheck inside ``httptest2::with_mock_dir()``
with metacheck's test redactor (``page[size]=100`` stripped) and
``TESTTHAT=true`` (so ``osf_get_all_pages()`` fetches later pages one by one
through the mock). :func:`run_mocked` sets up the same thing for pytacheck:
recorded responses (unrecorded requests fail, as under httptest2), no session
listing cache, no token, no sleeping.

``filetype()`` (``R/file_category.R``) belongs to another work item; until
``metacheck.fileinfo.category`` exists a stub stands in and the cases ignore
the ``filetype`` column.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

UPSTREAM_APIS = (
    Path(__file__).resolve().parents[2] / "upstream" / "metacheck" / "tests" / "testthat" / "apis"
)


@contextlib.contextmanager
def _stub_modules(stubs: dict[str, dict[str, Any]]) -> Iterator[None]:
    """Install ``{module: {name: value}}`` for modules that cannot be imported yet."""
    missing: dict[str, dict[str, Any]] = {}
    for name, attrs in stubs.items():
        try:
            mod = __import__(name, fromlist=["_"])
        except ImportError:
            missing[name] = attrs
            continue
        if not all(hasattr(mod, a) for a in attrs):
            missing[name] = attrs
    if not missing:
        yield
        return
    saved: dict[str, Any] = {}
    for name, attrs in missing.items():
        parts = name.split(".")
        for i in range(2, len(parts)):
            parent = ".".join(parts[:i])
            try:
                __import__(parent)
            except ImportError:
                saved.setdefault(parent, sys.modules.get(parent))
                pkg = types.ModuleType(parent)
                pkg.__path__ = []  # type: ignore[attr-defined]
                sys.modules[parent] = pkg
        saved.setdefault(name, sys.modules.get(name))
        mod = types.ModuleType(name)
        for attr, value in attrs.items():
            setattr(mod, attr, value)
        sys.modules[name] = mod
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _stub_file_category(filename: Any) -> Any:
    import pandas as pd

    return pd.DataFrame({"file_category": pd.Series([None] * len(list(filename)), dtype="string")})


def stub_download_many_parallel(
    urls: list[str], dests: list[str], expected_size: Any = float("nan")
) -> list[str | None]:
    """Sequential stand-in for ``metacheck.archives.download._download_many_parallel``."""
    import math

    from metacheck import http

    sizes = list(expected_size) if isinstance(expected_size, list | tuple) else None
    if sizes is None:
        sizes = [expected_size] * len(urls)
    errs: list[str | None] = []
    for url, dest, exp in zip(urls, dests, sizes, strict=True):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        resp = http.request("GET", url, max_tries=1)
        if resp is None:
            errs.append("download failed")
            continue
        if resp.status_code != 200:
            errs.append(f"HTTP {resp.status_code}")
            continue
        with open(dest, "wb") as fh:
            fh.write(resp.content)
        if exp is not None and not (isinstance(exp, float) and math.isnan(exp)) and exp > 0:
            got = os.path.getsize(dest)
            if got != exp:
                os.remove(dest)
                errs.append(f"truncated ({got:.0f} of {exp:.0f} bytes)")
                continue
        errs.append(None)
    return errs


def _filetype_stub() -> contextlib.AbstractContextManager[None]:
    """Stand-ins for the file-category and download ports until they exist."""
    return _stub_modules(
        {
            "metacheck.fileinfo.category": {
                "filetype": lambda filename: [None for _ in filename],
                "file_category": _stub_file_category,
            },
            "metacheck.archives.download": {
                "_download_many_parallel": stub_download_many_parallel,
            },
        }
    )


@contextlib.contextmanager
def _env(**values: str) -> Iterator[None]:
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


#: Synthetic OSF responses for cases metacheck's recordings do not cover
#: (a user's node listing, projects the token cannot read).
LOCAL_MOCKS = Path(__file__).resolve().parent / "mocks"


def run_mocked_local(x: Callable[[], Any]) -> Any:
    """:func:`run_mocked` with :data:`LOCAL_MOCKS` searched before metacheck's recordings."""
    return run_mocked(x, mock_dirs=(LOCAL_MOCKS, "apis"))


def run_mocked(x: Callable[[], Any], mock_dirs: tuple[str | Path, ...] = ("apis",)) -> Any:
    """Call *x* against metacheck's recorded API responses (see module docstring)."""
    from metacheck import utils
    from tests.archives_osf.osfmock import replay_osf

    with (
        tempfile.TemporaryDirectory() as tmp,
        _env(OSF_PAT="", PYTACHECK_NO_SLEEP="1"),
        utils.local_options(
            {
                "metacheck.osf.cache": False,
                "metacheck.osf.pat": None,
                "metacheck.osf.delay": 0,
                "metacheck.cache.dir": tmp,
            }
        ),
        _filetype_stub(),
        replay_osf(*mock_dirs, missing="error"),
    ):
        return x()


def run(x: Callable[[], Any]) -> Any:
    """Call *x* with the ``filetype()`` stub in place (no HTTP)."""
    with _filetype_stub():
        return x()


def fixture(path: str) -> Any:
    """The recorded httr2 response ``apis/<path>`` (R: ``source(<path>.R)$value``)."""
    from tests.httpmock import fixture_response

    return fixture_response(UPSTREAM_APIS, path)


def with_tmpdir(fn: Callable[[str], Any]) -> Any:
    """Call ``fn(dir)`` with a fresh temporary directory (R: ``tempfile(); dir.create()``)."""
    with tempfile.TemporaryDirectory() as tmp:
        return fn(tmp)


def read_lines(folder: str, name: str) -> list[str]:
    """R ``readLines(file.path(folder, name))``."""
    text = (Path(folder) / name).read_text(encoding="utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line.removesuffix("\r") for line in lines]


def list_files(folder: str) -> list[str]:
    """R ``sort(list.files(folder))``."""
    from metacheck._r import r_sorted

    return r_sorted([p.name for p in Path(folder).iterdir() if not p.name.startswith(".")])


def download_summary(x: Any, folder: str) -> dict[str, Any]:
    """An ``osf_file_download()`` result with ``download_path`` reduced to its
    basename (temporary folders differ between runs), plus the files on disk."""
    import pandas as pd

    if isinstance(x, pd.DataFrame) and "download_path" in x.columns:
        x = x.copy()
        x["download_path"] = [
            None if v is None or v != v else Path(str(v)).name for v in x["download_path"]
        ]
    return {"value": x, "files": list_files_recursive(folder)}


def list_files_recursive(folder: str) -> list[str]:
    """R ``sort(list.files(folder, recursive = TRUE))``."""
    from metacheck._r import r_sorted

    out = []
    for dirpath, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        rel = os.path.relpath(dirpath, folder)
        out.extend(f if rel == "." else f"{rel}/{f}" for f in files if not f.startswith("."))
    return r_sorted(out)


def online_true(fn: Callable[[], Any]) -> Any:
    """Call *fn* with ``metacheck.utils.online()`` reporting every host as reachable."""
    from metacheck import utils

    saved = utils.online
    utils.online = lambda *args, **kwargs: True  # type: ignore[assignment]
    try:
        return fn()
    finally:
        utils.online = saved  # type: ignore[assignment]


def _fake_download_many_parallel(
    urls: list[str], dests: list[str], expected_size: Any = float("nan")
) -> list[str | None]:
    """Write each destination's base name as its content (R: ``writeLines(basename(d), d)``)."""
    for d in dests:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        Path(d).write_text(os.path.basename(d) + "\n", encoding="utf-8")
    return [None] * len(urls)


def fake_downloads(fn: Callable[[], Any]) -> Any:
    """Call *fn* with file downloads replaced by :func:`_fake_download_many_parallel`."""
    with _stub_modules(
        {"metacheck.archives.download": {"_download_many_parallel": stub_download_many_parallel}}
    ):
        mod = __import__("metacheck.archives.download", fromlist=["_"])
        saved = mod._download_many_parallel
        mod._download_many_parallel = _fake_download_many_parallel
        try:
            return fn()
        finally:
            mod._download_many_parallel = saved
