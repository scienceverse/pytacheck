"""The ``save_path`` argument that has no default.

metacheck asks for the folder to write to (CRAN does not allow writing into the working
directory unless asked), so ``convert()``, ``convert_bibr()``, ``convert_grobid()``,
``grobid_to_bibr()`` and ``paper_write()`` have no default ``save_path``. Python needs a
default value to tell "not given" from ``None`` (a valid ``save_path`` for some of them).
"""

from __future__ import annotations

from typing import Any

__all__ = ["NO_SAVE_PATH", "require_save_path"]


class _NoSavePath:
    """The default of a ``save_path`` argument."""

    def __repr__(self) -> str:
        return "<save_path not given>"


#: ``save_path`` was not given
NO_SAVE_PATH: Any = _NoSavePath()


def require_save_path(save_path: Any) -> None:
    """R's ``force(save_path)``: stop when ``save_path`` was not given."""
    if save_path is NO_SAVE_PATH:
        raise TypeError('argument "save_path" is missing, with no default')
