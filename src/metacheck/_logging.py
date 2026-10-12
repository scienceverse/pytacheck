"""The package's logger, and the link that lets handlers on ``metacheck`` receive its records.

Records are still emitted under the old logger names, so every existing handler,
level and ``caplog`` filter keeps working. The old root logger is made a child of
``metacheck`` once, at import, so a handler or level set on ``metacheck`` applies
too. (``logging.config.dictConfig`` with ``disable_existing_loggers=True`` that
names only ``metacheck`` still disables the old loggers: they are not name-children
of it.) The emitters switch names when the old names are retired.
"""

from __future__ import annotations

import logging

__all__ = ["get_logger"]

logging.getLogger("pytacheck").parent = logging.getLogger("metacheck")


def get_logger(sub: str = "") -> logging.Logger:
    """The package's logger (records also reach handlers on ``metacheck``)."""
    root = logging.getLogger("pytacheck")
    return root.getChild(sub) if sub else root
