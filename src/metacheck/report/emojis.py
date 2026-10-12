"""Emojis used in reports (port of ``data/emojis.rda``, built by ``data-raw/emojis.R``).

``emojis`` maps the names metacheck uses (``emojis$tl_red`` ...) to the
emoji strings. The traffic-light entries (``tl_*``) head every module in a
report; ``dot_*`` and the hearts are available to module authors.

* General: ``check``, ``star``, ``warning``, ``stop``, ``x``, ``no``,
  ``thumbs_up``, ``thumbs_down``, ``info``, ``question``
* Traffic lights: ``tl_green``, ``tl_yellow``, ``tl_red``, ``tl_info``,
  ``tl_na``, ``tl_fail``
* Dots: ``dot_green``, ``dot_yellow``, ``dot_red``, ``dot_info``, ``dot_na``,
  ``dot_fail``
* Hearts: ``red``, ``orange``, ``yellow``, ``green``, ``blue``, ``purple``,
  ``brown``, ``black``, ``white``, ``pink``
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["emojis"]

# Same order and code points as metacheck::emojis (R/emojis.R, data-raw/emojis.R).
_EMOJIS: dict[str, str] = {
    "check": "✅",
    "star": "⭐",
    "warning": "⚠️",
    "stop": "\U0001f6d1",
    "x": "❌",
    "no": "\U0001f6ab",
    "thumbs_up": "\U0001f44d",
    "thumbs_down": "\U0001f44e",
    "info": "ℹ️",
    "question": "❓",
    "tl_green": "✅️",
    "tl_yellow": "\U0001f50d",
    "tl_red": "⚠️",
    "tl_info": "ℹ️",
    "tl_na": "⬜",
    "tl_fail": "☠️",
    "dot_green": "\U0001f7e2",
    "dot_yellow": "\U0001f7e1",
    "dot_red": "\U0001f534",
    "dot_info": "\U0001f535",
    "dot_na": "⚪️",
    "dot_fail": "⚫️",
    "red": "❤️",
    "orange": "\U0001f9e1",
    "yellow": "\U0001f49b",
    "green": "\U0001f49a",
    "blue": "\U0001f499",
    "purple": "\U0001f49c",
    "brown": "\U0001f90e",
    "black": "\U0001f5a4",
    "white": "\U0001f90d",
    "pink": "\U0001f496",
}

#: Useful emojis (metacheck's ``emojis`` data set); read-only.
emojis: MappingProxyType[str, str] = MappingProxyType(_EMOJIS)
