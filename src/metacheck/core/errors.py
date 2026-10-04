"""Warnings and errors of the core (docs/design/ARCHITECTURE.md §2.12)."""

from __future__ import annotations

__all__ = ["PytacheckWarning", "StaleDocumentError"]


class PytacheckWarning(UserWarning):
    """A warning about the input that changes what a run does, such as repeated paper ids.

    Never raised for what metacheck would also warn about: those keep their own
    messages.
    """


class StaleDocumentError(RuntimeError):
    """A paper's text or section table was edited in place after its Doc was built.

    Raised only while ``METACHECK_CHECK_MUTATION=1`` and inside a trusted scope
    (``run_modules()``, ``report()``), which read each paper's tables once and
    so would go on using the Doc of the old values. A module must copy a paper
    before it changes it.
    """
