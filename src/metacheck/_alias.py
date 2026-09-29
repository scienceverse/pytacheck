"""The old import name ``pytacheck``, kept so that existing code keeps working.

``pytacheck/__init__.py`` calls :func:`install`. Submodules are not copies:
``import pytacheck.io.convert`` returns the ``metacheck.io.convert`` module
object, so module state, ``mock.patch`` targets, pickles and ``isinstance``
checks are the same under both names. The ``pytacheck`` top level is a small
module of its own: it reads its names from ``metacheck`` and writes them there.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import threading
import types
from collections.abc import Sequence
from typing import Any

__all__ = ["install"]

_MISSING: Any = object()
#: Writes through the alias remembered per name. A delete through the alias, or putting
#: back the value under the last write, pops one; plain assignments would pile up.
_KEEP = 64
#: Held while a guard (below) is armed or disarmed: first imports of two submodules of
#: one package can run in two threads at once.
_lock = threading.RLock()


class _Loader(importlib.abc.Loader):
    """Hands out the already imported ``metacheck.<sub>`` module.

    The questions that tools ask a loader about the module's file (runpy asks
    ``get_code`` for ``python -m pytacheck.cli``) go to the real loader, under
    the real name: the file loaders check the name they are given.
    """

    def __init__(self, target: str, real_loader: Any, name: str) -> None:
        self.target = target
        self.real_loader = real_loader
        self.name = name  # the old name being imported
        self.spec: importlib.machinery.ModuleSpec | None = None

    def create_module(self, _spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        module = importlib.import_module(self.target)
        self.spec = module.__spec__
        return module

    def exec_module(self, module: types.ModuleType) -> None:
        # module_from_spec() gave the shared module the alias spec: give its own back
        alias_spec, module.__spec__ = module.__spec__, self.spec
        # Next, the import system sets the module as an attribute of its parent under
        # the old name. For the real name that happens only when the module is first
        # loaded, which create_module() has done, so a package attribute that shares
        # the submodule's name (the function metacheck.io.read, the decorator
        # metacheck.module) keeps its value. The old name must not change it either,
        # nor count as a write through the alias. Only an import makes that assignment
        # (its spec is _initializing then); exec_module() called by hand, or by a
        # LazyLoader, must not arm a guard that nothing would disarm.
        parent, _, child = self.name.rpartition(".")
        if parent in sys.modules and getattr(alias_spec, "_initializing", False):
            _skip_next_assignment(sys.modules[parent], child, module)

    def get_code(self, _fullname: str) -> Any:
        return self.real_loader.get_code(self.target)

    def get_source(self, _fullname: str) -> Any:
        return self.real_loader.get_source(self.target)

    def is_package(self, _fullname: str) -> Any:
        return self.real_loader.is_package(self.target)

    def get_filename(self, _fullname: str) -> Any:
        return self.real_loader.get_filename(self.target)

    def get_resource_reader(self, _fullname: str) -> Any:
        return self.real_loader.get_resource_reader(self.target)


def _skip_next_assignment(parent: types.ModuleType, child: str, module: types.ModuleType) -> None:
    """Drop the next ``setattr(parent, child, module)`` in this thread, the import system's.

    The parent gets a subclass of its class for as long as an assignment is pending;
    only an assignment of that very module object under that name, by the importing
    thread, is dropped. The lock keeps one guard per parent while threads come and go.
    """
    with _lock:
        cls = type(parent)
        pending: list[tuple[str, types.ModuleType, int]] | None = vars(cls).get("_alias_pending")
        if pending is None:
            pending = []
            base, waiting = cls, pending

            def __setattr__(self: types.ModuleType, name: str, value: Any) -> None:
                me = threading.get_ident()
                with _lock:
                    for i, (c, m, t) in enumerate(waiting):
                        if c == name and m is value and t == me:
                            del waiting[i]
                            if not waiting:
                                object.__setattr__(self, "__class__", base)
                            return
                base.__setattr__(self, name, value)

            guard = type(
                cls.__name__,
                (cls,),
                {
                    "__setattr__": __setattr__,
                    "_alias_pending": pending,
                    "__module__": cls.__module__,
                    "__qualname__": cls.__qualname__,
                },
            )
            parent.__class__ = guard
        pending.append((child, module, threading.get_ident()))


class AliasFinder(importlib.abc.MetaPathFinder):
    """Finds ``<old>.<sub>`` as the module ``<new>.<sub>``."""

    def __init__(self, old: str, new: str, own: frozenset[str] = frozenset()) -> None:
        self.old, self.new, self.own = old, new, own

    def find_spec(
        self,
        fullname: str,
        _path: Sequence[str] | None = None,
        _target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if not fullname.startswith(self.old + ".") or fullname in self.own:
            return None
        real = self.new + fullname[len(self.old) :]
        found = importlib.util.find_spec(real)
        if found is None:
            return None
        spec = importlib.machinery.ModuleSpec(
            fullname, _Loader(real, found.loader, fullname), origin=found.origin
        )
        spec.submodule_search_locations = found.submodule_search_locations
        return spec


def install(old: str, new: str) -> None:
    """Make the package *old*, while it is being imported, an alias of *new*."""
    target = importlib.import_module(new)
    # A write through *old* goes to *new*, and a delete through *old* undoes the latest
    # such write that *new* still holds, so that monkeypatch and mock.patch restore
    # exactly what *new* held, even for a name that is also a submodule, and nested
    # patches unwind one at a time, also around direct changes to *new*.
    # writes[name] is a stack of (what *new* held, what the alias wrote over it).
    writes: dict[str, list[tuple[Any, Any]]] = {}

    def own(name: str) -> bool:
        return name.startswith("__") and name.endswith("__")

    class AliasModule(types.ModuleType):
        def __getattr__(self, name: str) -> Any:
            try:
                return getattr(target, name)
            except AttributeError:
                raise AttributeError(f"module {old!r} has no attribute {name!r}") from None

        def __setattr__(self, name: str, value: Any) -> None:
            if own(name):
                super().__setattr__(name, value)
                return
            stack = writes.setdefault(name, [])
            if stack and value is stack[-1][0]:
                # the value under the last write is put back (monkeypatch undo). Known
                # limit: an inner mock.patch(create=True) through *old* that sets the
                # original value looks the same, so its exit deletes the name from *new*
                stack.pop()
            else:
                stack.append((vars(target).get(name, _MISSING), value))
                del stack[:-_KEEP]
            if not stack:
                del writes[name]
            setattr(target, name, value)

        def __delattr__(self, name: str) -> None:
            if own(name):
                super().__delattr__(name)
                return
            # undo the latest write that *new* still holds; the ones above it were
            # overwritten by direct changes to *new* since
            stack = writes.pop(name, [])
            current = vars(target).get(name, _MISSING)
            while stack and stack[-1][1] is not current:
                stack.pop()
            if not stack:
                delattr(target, name)  # nothing of the alias's to undo: a plain delete
                return
            before, _ = stack.pop()
            if stack:
                writes[name] = stack
            if before is _MISSING:
                delattr(target, name)
            else:
                setattr(target, name, before)

        def __dir__(self) -> list[str]:
            return sorted(set(vars(self)) | set(dir(target)))

    alias = sys.modules[old]
    vars(alias).update(
        __all__=list(getattr(target, "__all__", ())),
        __version__=getattr(target, "__version__", None),
    )
    alias.__class__ = AliasModule
    if not any(getattr(f, "old", None) == old for f in sys.meta_path):
        sys.meta_path.insert(0, AliasFinder(old, new, own=frozenset({f"{old}.__main__"})))
