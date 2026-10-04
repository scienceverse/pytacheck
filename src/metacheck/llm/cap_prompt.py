"""Cap reporting (port of ``R/cap-prompt.R``).

When a resource cap (download size, codebook LLM calls) refuses a unit of
work, metacheck does not prompt: it reports what was skipped -- inline, and
as a warning so ``warnings.catch_warnings()`` / manifests capture it -- and
continues. The message (built by ``cap_gate_*()``) names the parameter and
the value that would include the skipped unit on a later run.
"""

from __future__ import annotations

import math
import sys
import warnings

__all__ = ["cap_report"]


def cap_report(message: str) -> None:
    """Port of ``.cap_report()`` (R keeps it internal; here it stays public): show *message* now and raise it as a warning."""
    print(message, file=sys.stderr)
    warnings.warn(message, stacklevel=2)


def _cap_size_str(bytes: float | None) -> str:
    """Port of ``.cap_size_str()``: a byte count as a short string (``"5.4 GB"``)."""
    if bytes is None or (isinstance(bytes, float) and math.isnan(bytes)):
        return "unknown size"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 1 if bytes <= 0 else min(len(units), 1 + math.floor(math.log(bytes) / math.log(1024)))
    return f"{bytes / 1024 ** (i - 1):.1f} {units[i - 1]}"
