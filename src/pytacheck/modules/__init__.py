"""Built-in modules.

Each ``<name>.py`` file in this package defines one module, decorated with
:func:`pytacheck.module.module`, whose function has the same name as the
file. Files starting with ``_`` are helpers, not modules. Modules are
imported lazily by :func:`pytacheck.module.module_find`, so heavy
dependencies should be imported inside the module function.
"""
