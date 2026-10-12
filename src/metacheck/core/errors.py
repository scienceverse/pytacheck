"""Warnings and errors of the core (docs/design/ARCHITECTURE.md §2.12)."""

from __future__ import annotations

__all__ = ["PytacheckWarning", "StaleDocumentError"]


class PytacheckWarning(UserWarning):
    """A warning about the input that changes what a run does, such as repeated paper ids.

    Never raised for what metacheck would also warn about: those keep their own
    messages.
    """


class StaleDocumentError(RuntimeError):
    """A paper's indexed text or reference tables were edited in place.

    Raised only while ``METACHECK_CHECK_MUTATION=1`` and inside a trusted scope
    (``run_modules()``, ``report()``), which read each paper's tables once and
    so would go on using cached values. A module must copy a paper
    before it changes it.
    """
