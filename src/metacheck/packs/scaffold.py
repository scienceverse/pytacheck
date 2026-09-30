"""Scaffolding for authors: ``pack_new()`` and ``module_template()``.

Templates live in ``metacheck/resources/templates/``. The module template is
adapted from metacheck's ``inst/templates/_module.R`` (including its
``<validation>`` placeholder), and ``module_template()`` ports metacheck's
function of the same name, writing a ``.py`` file.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from metacheck.packs.manifest import PackError, validate_pack_name

__all__ = ["INSTALL_SPEC", "module_template", "pack_new"]

#: What CI installs to check a pack: the pip requirement the pytacheck-modules
#: store's workflow uses too (a PyPI pre-release, metacheck>=0.4.0a1, works too). One place to change.
INSTALL_SPEC = "metacheck @ git+https://github.com/scienceverse/pytacheck@main"

#: template file -> path inside a new pack ("{module}" is the example module's name)
PACK_FILES = {
    "pack/pack.json.tmpl": "pack.json",
    "module.py.tmpl": "{module}.py",
    "pack/test_module.py.tmpl": "tests/test_{module}.py",
    "pack/README.md.tmpl": "README.md",
    "pack/LICENSE.tmpl": "LICENSE",
    "pack/CITATION.cff.tmpl": "CITATION.cff",
    "pack/github-workflow.yml.tmpl": ".github/workflows/pytacheck.yml",
    "pack/gitignore.tmpl": ".gitignore",
}


def _template(name: str) -> str:
    return (
        resources.files("metacheck.resources")
        .joinpath("templates", *name.split("/"))
        .read_text(encoding="utf-8")
    )


def module_template(
    module_name: str, path: str | os.PathLike[str] = "./modules", *, overwrite: bool = False
) -> Path:
    """Port of ``module_template()``: write a module template to ``<path>/<module_name>.py``.

    Every ``module_name`` in the template is replaced, as in R. Unlike R, an
    existing file is not overwritten unless ``overwrite=True``.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_]+", str(module_name)):
        raise ValueError("The module_name must contain only letters, numbers, and _")
    if not re.match(r"[A-Za-z]", module_name):
        raise ValueError("The module_name must start with a letter (it is a Python function)")
    text = _template("module.py.tmpl").replace("module_name", module_name)
    folder = Path(path)
    folder.mkdir(parents=True, exist_ok=True)
    filepath = folder / f"{module_name}.py"
    if filepath.exists() and not overwrite:
        raise FileExistsError(f"{filepath} already exists (pass overwrite=True to replace it)")
    filepath.write_text(text, encoding="utf-8")
    if not filepath.exists():  # pragma: no cover - R's check
        raise OSError(f"The file {filepath} did not save")
    return filepath


def pack_new(
    name: str,
    path: str | os.PathLike[str] = ".",
    *,
    module: str | None = None,
    title: str | None = None,
) -> Path:
    """Create a new pack folder ``<path>/<name>/`` that passes ``pack check``.

    It holds ``pack.json`` (with a ``default`` and an ``only`` preset), an
    example module (``<name>_example`` unless *module* is given), a test,
    ``README.md``, ``LICENSE`` (MIT; change it to any OSI-approved licence),
    ``CITATION.cff`` and a GitHub workflow running ``pytacheck pack check``.
    """
    from metacheck._version import __version__

    validate_pack_name(name)
    module = module or f"{name.replace('-', '_')}_example"
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", module):
        raise PackError(f"Invalid module name {module!r}")
    root = Path(path) / name
    if root.exists() and any(root.iterdir()):
        raise PackError(f"{root} already exists and is not empty")
    now = datetime.now(UTC)
    base_version = re.match(r"^\d+(\.\d+)?", __version__)  # major.minor
    requires = base_version.group(0) if base_version else "0.3"
    if not re.fullmatch(r"\d+(\.\d+)*", __version__):
        # 0.4.0a1 and 0.5.0.dev1 are below 0.4 and 0.5 (PEP 440); .dev0 is below all of them: a pre-release or dev build must pass its own pack
        requires += ".0.dev0"
    values = {
        "{{name}}": name,
        "{{module}}": module,
        "{{title}}": title or f"{name} checks",
        "{{year}}": str(now.year),
        "{{date}}": now.strftime("%Y-%m-%d"),
        "{{pytacheck_version}}": requires,
        "{{install_spec}}": INSTALL_SPEC,
    }
    for template, target in PACK_FILES.items():
        text = _template(template)
        if template == "module.py.tmpl":
            text = text.replace("module_name", module)
        for key, value in values.items():
            text = text.replace(key, value)
        out = root / target.format(module=module)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return root
