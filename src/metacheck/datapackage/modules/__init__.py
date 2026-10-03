"""The ``datapackage`` pack: checks for a data package as a whole.

Registered through the ``pytacheck.modules`` entry point, so every
installation has it. Each module takes ``local_path`` (the package's folder or
archive) and reads the shared listing with
:func:`metacheck.datapackage.package_for`.
"""
