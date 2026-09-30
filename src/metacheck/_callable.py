"""Submodules that share a name with the function they define.

``metacheck.module``, ``metacheck.stats`` and ``metacheck.validate`` are
submodules *and* top-level exports (``pc.module``, ``pc.stats(paper)``,
``pc.validate(...)``). Once Python imports such a submodule it sets it as an
attribute of the package, shadowing the lazily exported function. Making the
submodule callable keeps ``pc.stats(paper)`` working either way, while
``import metacheck.stats`` and attribute access still get the module.
"""

from __future__ import annotations

import sys
import types
from typing import Any

__all__ = ["callable_module"]


def callable_module(module_name: str, func_name: str) -> None:
    """Make ``sys.modules[module_name]`` callable as its attribute *func_name*."""
    mod = sys.modules[module_name]
    base = type(mod) if type(mod) is not types.ModuleType else types.ModuleType

    class CallableModule(base):  # type: ignore[misc, valid-type]
        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return getattr(self, func_name)(*args, **kwargs)

    CallableModule.__name__ = f"CallableModule[{func_name}]"
    mod.__class__ = CallableModule
