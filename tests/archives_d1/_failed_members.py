"""Python side of the ``*_file_download.*failed_member`` parity cases (metacheck #429).

The R side replaces a host's ``.<host>_zip_members()`` with a function that
writes ``a.csv`` into the target folder and reports ``b.csv`` as failed; this
does the same for the Python port and returns the download table with its
``failed`` table (R: ``list(value = x, failed = attr(x, "failed"))``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any
from unittest import mock

ERROR = "HTTP 403 (range not honoured)"


def _members(url: Any, dest: str, *args: Any, **kwargs: Any) -> Any:
    import pandas as pd

    path = os.path.join(dest, "a.csv")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("a,b\n")
    return pd.DataFrame(
        {
            "name": pd.Series(["a.csv", "b.csv"], dtype="string"),
            "path": pd.Series([path, None], dtype="string"),
            "size": [4.0, 9.0],
            "ok": pd.Series([True, False], dtype="boolean"),
            "error": pd.Series([None, ERROR], dtype="string"),
        }
    )


def run(module: Any, name: str, fn: Callable[[], Any]) -> dict[str, Any]:
    """Call *fn* with ``module.<name>`` (a zip-members function) replaced."""
    with mock.patch.object(module, name, _members):
        x = fn()
    failed = None if x is None else x.attrs.get("failed")
    return {"value": x, "failed": failed}
