"""Record the built-in pattern triples, for test_required_literals.py.

The built-in triples are the ``(pattern, icase, perl)`` combinations the offline
paper modules of the accuracy matrix (``parity/accuracy/matrix.toml``) search for
on its 21 papers: every pattern given to ``_r.regex.detector()``, through which
``grepl()`` and ``text_search()`` both detect, except fixed ones. Re-record when
modules change their patterns:

    python tests/foundation/record_builtin_patterns.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).with_name("data") / "builtin_patterns.json"


def record() -> list[tuple[str, bool, bool]]:
    from metacheck._r import regex as rx
    from parity import accuracy as A

    seen: dict[tuple[str, bool, bool], None] = {}
    init = rx.Detector.__init__

    def noted(self: Any, pattern: str, ignore_case: bool, perl: bool, fixed: bool) -> None:
        if not fixed and isinstance(pattern, str):
            seen.setdefault((pattern, bool(ignore_case), bool(perl)))
        init(self, pattern, ignore_case, perl, fixed)

    rx.detector.cache_clear()
    rx.Detector.__init__ = noted  # type: ignore[method-assign]
    try:
        A.run_python([o for o in A.load_matrix() if o.kind == "paper"])
    finally:
        rx.Detector.__init__ = init  # type: ignore[method-assign]
        rx.detector.cache_clear()
    return sorted(seen)


def save(triples: list[tuple[str, bool, bool]], path: Path = DATA) -> None:
    text = json.dumps({"triples": [list(t) for t in triples]}, ensure_ascii=False, indent=0)
    path.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT))
    triples = record()
    save(triples)
    print(f"{len(triples)} triples -> {DATA.relative_to(ROOT)}")
