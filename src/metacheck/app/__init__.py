"""The local demo app: ``metacheck-app`` opens a Gradio page in the browser.

Importing this package never imports gradio. The app needs the ``app`` extra
(``pip install 'metacheck[app]>=0.4.0a1'``). ``run`` holds the checking code and
needs no web server, ``security`` guards the local server, ``state`` lets a second
start reuse a running app, ``ui`` builds the page and ``launch`` starts it.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """The ``metacheck-app`` command (also ``pytacheck app``). Returns the exit code."""
    from metacheck.app.launch import main as launch

    return launch(argv)
