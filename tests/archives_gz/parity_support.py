"""Python side of the ``archives_gz`` parity cases that need a little setup.

The R side of these cases runs metacheck with ``online()`` mocked to ``TRUE``
(``testthat::with_mocked_bindings()``), as metacheck's own tests do, so no
DNS lookup is made; :func:`run` does the same for pytacheck and makes sure no
GitHub credentials are looked up. The case's ``mock_dir`` serves recorded
responses on both sides.
"""

from __future__ import annotations

import contextlib
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest import mock


@contextlib.contextmanager
def offline_safe() -> Iterator[None]:
    """``online()`` is ``True`` and no git credential helper is consulted."""
    from pytacheck.archives import github

    with (
        mock.patch("pytacheck.utils.online", lambda *a, **k: True),
        mock.patch.dict(github._TOKEN_CACHE, {"token": None}),
    ):
        yield


def run(x: Callable[[], Any]) -> Any:
    """Call *x* inside :func:`offline_safe`."""
    with offline_safe():
        return x()


def with_tmpdir(fn: Callable[[str], Any]) -> Any:
    """``fn(dir)`` with a fresh, existing temporary directory (R: ``tempfile(); dir.create()``)."""
    with tempfile.TemporaryDirectory() as tmp:
        return fn(tmp)


def verify_case() -> Any:
    """The ``.zenodo_verify_downloads.mixed`` case: files on disk checked against a table."""
    import hashlib

    import pandas as pd

    from pytacheck.archives.zenodo import _zenodo_verify_downloads

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "a.txt").write_bytes(b"abc\n")  # R: writeLines("abc", ...)
        (root / "b.txt").write_bytes(b"abc\n")
        (root / "sub").mkdir()
        a_md5 = hashlib.md5(b"abc\n", usedforsecurity=False).hexdigest()
        files = pd.DataFrame(
            {
                "path": pd.Series(
                    ["a.txt", "b.txt", "missing.txt", None, "sub", "a.txt"], dtype="string"
                ),
                "size": [4.0, 4.0, 3.0, 1.0, 0.0, 5.0],
                "checksum": pd.Series(
                    [f"md5:{a_md5}", "md5:" + "0" * 32, None, None, None, "sha1:x"],
                    dtype="string",
                ),
                "downloaded": [True, True, True, True, True, False],
                "extracted": pd.Series([None, None, None, 2, None, None], dtype="Int64"),
            }
        )
        return _zenodo_verify_downloads(files, d)


def with_option(name: str, fn: Callable[[], Any]) -> Any:
    """``fn()``, then the option *name* restored (R: ``on.exit(options(...))``)."""
    from pytacheck.utils import get_option, options

    old = get_option(name)
    try:
        return fn()
    finally:
        options({name: old})


def meta_folder() -> str:
    """A folder whose saved OSF metadata has odd shapes (kept for the session, as R's tempfile())."""
    d = Path(tempfile.mkdtemp())
    (d / "_osf_metadata").mkdir()
    (d / "_osf_metadata" / "metadata.json").write_text(
        '{"osf_id": "abcde", "tags": ["a", null, ["b", 1]], '
        '"contributors": {"x": {"name": "N"}, "y": {"family_name": "F", "given_name": "G"}}}\n',
        encoding="utf-8",
    )
    return str(d)
