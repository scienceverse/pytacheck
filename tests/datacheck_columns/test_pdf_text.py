"""PDF codebook gate (``.pdf_codebook_lines()``) and rich-text extraction.

``pdftools::pdf_text()`` is poppler; the Python port runs poppler's
``pdftotext -layout`` when it can find it and falls back to ``pypdf``. The
narrative-paper case depends on the engine's layout, so it is checked here
(against R's result, ``character(0)``) only when poppler is available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pytacheck.datacheck import _columns_codebook as cbmod
from pytacheck.datacheck.columns import _extract_rich_text, _pdf_codebook_lines, parse_codebook

FIX = Path(__file__).parent / "fixtures"


def _has_poppler() -> bool:
    return cbmod._pdftotext() is not None


@pytest.mark.skipif(not _has_poppler(), reason="poppler's pdftotext not available")
def test_pdf_gate_rejects_a_narrative_paper(fixtures_dir: Path) -> None:
    # R: length(.pdf_codebook_lines("debruine-child.pdf")) == 0
    assert _pdf_codebook_lines(fixtures_dir / "debruine" / "debruine-child.pdf") == []


def test_pdf_gate_accepts_a_definition_list() -> None:
    lines = _pdf_codebook_lines(FIX / "codebook.pdf")
    assert len(lines) == 7  # R: length(.pdf_codebook_lines(...)) == 7
    assert _pdf_codebook_lines(FIX / "codebook.pdf", max_pages=0) == []
    assert _pdf_codebook_lines(FIX / "prose.pdf") == []


def test_pdf_gate_without_poppler_uses_pypdf(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("pypdf")
    monkeypatch.setattr(cbmod, "_pdftotext", lambda: None)
    assert len(_pdf_codebook_lines(FIX / "codebook.pdf")) == 7
    assert _pdf_codebook_lines(FIX / "prose.pdf") == []


def test_parse_codebook_pdf_returns_lines_or_empty() -> None:
    assert parse_codebook(FIX / "prose.pdf") == []  # deliberately skipped, not readLines()
    assert len(parse_codebook(FIX / "codebook.pdf")) == 7


def test_extract_rich_text_formats() -> None:
    assert "participant" in _extract_rich_text(FIX / "codebook.docx", "docx").lower()
    assert _extract_rich_text(FIX / "codebook.rtf", "rtf") != ""
    assert _extract_rich_text(FIX / "codebook.xlsx", "docx") == ""  # a failure is ""
    assert _extract_rich_text(FIX / "readme.txt", "txt") == ""  # unknown extension
