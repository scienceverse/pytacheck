"""The ``datapackage`` pack: checks for a data package as a whole.

metacheck registers it as a pip-installed pack (the entry point in
pyproject.toml), so every installation has it. Each module takes
``local_path`` (the package's folder or archive) and reads the shared listing
with :func:`metacheck.datapackage.package_for`.
"""
