"""Run the checks on a data package, and write a report on it.

:func:`check_package` opens a package (a folder, or a zip or tar archive of one),
runs a selection of modules on it and returns their outputs.
:func:`report_package` does the same and writes the report.
Both run on this machine only: an archive is extracted to a private folder
that is removed when the run is over, and every module that takes
``local_path`` and ``local_only`` is given the package's folder and
``local_only=True``, so nothing is looked up online or uploaded.

The modules have no paper to read, so they get an empty stand-in paper titled
with the package's name (what :func:`metacheck.report.report_repository` does
for a repository folder). Which modules run is chosen like this: ``modules`` if
given, else ``preset``, else the pack's own ``datapackage::default``; the
preset the user configured for papers is never used.
"""

from __future__ import annotations

import inspect
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from metacheck.datapackage._open import OpenedPackage, open_package
from metacheck.datapackage._session import using_package

if TYPE_CHECKING:
    from metacheck.presets import Selection
    from metacheck.provenance import ModuleChain, RunRecord
    from metacheck.report.report import ReportOutput

__all__ = ["DEFAULT_PRESET", "check_package", "package_selection", "report_package"]

#: The preset that runs when neither modules nor a preset are given.
DEFAULT_PRESET = "datapackage::default"

_OUTPUT_FORMATS = ("html", "qmd", "md")


def package_selection(
    *,
    preset: str | None = None,
    modules: Any = None,
    args: Mapping[str, Mapping[str, Any]] | None = None,
    offline: bool | None = None,
) -> Selection:
    """The ordered ``[(ref, args)]`` a package run uses.

    *modules* (refs, in order; a ready :class:`~metacheck.presets.Selection` is
    used as it is, and *preset* and *args* are then ignored) beat *preset*,
    which beats :data:`DEFAULT_PRESET`. *args* maps module names to extra
    arguments, as for :func:`metacheck.report`. ``offline=True`` leaves out the
    modules that declare they need the network or an LLM.
    """
    from metacheck.presets import Selection, select

    if isinstance(modules, Selection):
        return modules
    chosen = preset
    if not modules and preset is None:
        chosen = DEFAULT_PRESET
    selection = select(
        modules=modules or None,
        preset=chosen,
        args=args,
        use_config=False,  # a paper preset the user configured is not a package preset
        offline=offline,
    )
    if chosen != preset:
        selection.source = "default"
    return selection


def check_package(
    path: str | os.PathLike[str],
    *,
    preset: str | None = None,
    modules: Any = None,
    args: Mapping[str, Mapping[str, Any]] | None = None,
    record: str | os.PathLike[str] | None = None,
    offline: bool | None = None,
) -> ModuleChain:
    """Run the checks on the data package at *path*.

    *path* is the package's folder, or a zip or tar archive of it. Raises
    :class:`~metacheck.datapackage.PackageError` for a path that cannot be
    opened. Which modules run: see :func:`package_selection`. Every selected
    module whose function takes ``local_path`` and ``local_only`` gets the
    package's folder and ``True`` (a value in *args* or in the preset wins).

    Returns the module outputs in run order (a
    :class:`~metacheck.provenance.ModuleChain`). They are complete when this
    returns: an archive's temporary folder is removed before. The run record
    (``chain.run_record``, also written to *record*) names the package, and the
    ``local_path`` of each module is the path that was given, not the temporary
    folder.

    A module can hand back files (the README draft of ``package_readme`` is
    one): ``chain.files`` is ``{file name: text or bytes}`` for all of them, and
    ``metacheck.module.write_module_files`` writes them to a folder. The package itself is
    never written to.
    """
    from metacheck.provenance import run_modules

    selection = package_selection(preset=preset, modules=modules, args=args, offline=offline)
    with open_package(path) as opened, using_package(opened):
        run = _give_package(selection, opened)
        chain = run_modules(_stand_in_paper(_name(opened)), run)
        rec = _record(list(chain), run, opened)
    chain.run_record = rec
    if record is not None:
        rec.write(record)
    return chain


def report_package(
    path: str | os.PathLike[str],
    output_file: str | os.PathLike[str] | None = None,
    output_format: str = "html",
    *,
    preset: str | None = None,
    modules: Any = None,
    args: Mapping[str, Mapping[str, Any]] | None = None,
    record: str | os.PathLike[str] | None = None,
    offline: bool | None = None,
) -> ReportOutput:
    """Run the checks on the data package at *path* and write a report.

    The modules run as in :func:`check_package`. The report is titled with the
    package's name and written to *output_file* (default
    ``<name>_report.<format>`` in the current folder); *output_format* is
    ``"html"``, ``"qmd"`` or ``"md"``. The report is built outside the package
    and moved into place, so a report written inside the package's own folder
    is not one of the files that are checked. Returns the report's module
    outputs (:class:`~metacheck.report.report.ReportOutput`, with the file's
    path in ``save_path``, and the files that modules hand back in ``files``).
    """
    from metacheck.module import run_session
    from metacheck.presets import label
    from metacheck.report.report import report

    fmt = str(output_format).lower()
    if fmt not in _OUTPUT_FORMATS:
        raise ValueError("The output_format must be either 'html', 'qmd' or 'md'.")
    selection = package_selection(preset=preset, modules=modules, args=args, offline=offline)
    with open_package(path) as opened, using_package(opened), run_session():
        name = _name(opened)
        dest = Path(output_file if output_file is not None else f"{name}_report.{fmt}")
        if not dest.parent.is_dir() or not os.access(dest.parent, os.W_OK):
            raise ValueError("The output_file is not a valid path.")
        run = _give_package(selection, opened)
        by_ref = {(ref if isinstance(ref, str) else label(ref)): a for ref, a in run if a}
        with tempfile.TemporaryDirectory(prefix="metacheck-report-") as tmp:
            result = cast(  # one paper gives one report
                "ReportOutput",
                report(
                    _stand_in_paper(name),
                    modules=[ref for ref, _ in run],
                    output_file=str(Path(tmp) / dest.name),
                    output_format=fmt,
                    args=by_ref or None,
                ),
            )
            # a report that could not be rendered is saved as .qmd instead
            saved = Path(str(result.save_path))
            final = dest.with_name(saved.name)
            shutil.move(str(saved), final)
            result.save_path = str(final)
        outputs = _in_run_order(result, run)
        rec = _record(outputs, run, opened)
    if record is not None:
        rec.write(record)
    return result


# -- helpers --------------------------------------------------------------------------


def _name(opened: OpenedPackage) -> str:
    """The package's name; a folder given as ``.`` is named by the folder it is."""
    name = opened.name
    if name in ("", ".", ".."):
        name = opened.base_dir.name
    return name or "package"


def _stand_in_paper(name: str) -> Any:
    """A paper with no content, titled *name*: what modules run on instead of a paper."""
    import pandas as pd

    from metacheck.papers.io import test_paper

    paper = test_paper()
    info = paper.info.copy()
    info["title"] = pd.Series([name], dtype="string")
    paper.info = info
    return paper


def _own_parameters(ref: Any) -> frozenset[str]:
    """The named parameters of a module's function (none if it cannot be found)."""
    from metacheck.module import ModuleError, module_find

    try:
        parameters = inspect.signature(module_find(ref).func).parameters
    except (ModuleError, TypeError, ValueError):
        return frozenset()
    keyword = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    return frozenset(name for name, p in parameters.items() if p.kind in keyword)


def _give_package(selection: Selection, opened: OpenedPackage) -> Selection:
    """*selection* with ``local_path`` and ``local_only=True`` for each module that takes them.

    A value the caller or a preset set for a module is kept.
    """
    from metacheck.presets import Selection

    given: dict[str, Any] = {"local_path": str(opened.base_dir), "local_only": True}
    entries = []
    for ref, args in selection:
        takes = _own_parameters(ref)
        entries.append((ref, {**{k: v for k, v in given.items() if k in takes}, **args}))
    return Selection(
        entries,
        preset=selection.preset,
        source=selection.source,
        dropped=selection.dropped,
        offline=selection.offline,
    )


def _in_run_order(result: ReportOutput, selection: Selection) -> list[Any]:
    """A report's outputs (sorted by section) in the order the modules ran."""
    from metacheck.presets import label

    by_label = dict(result)
    ordered = [by_label.pop(label(ref)) for ref, _ in selection if label(ref) in by_label]
    return ordered + list(by_label.values())


def _record(outputs: Sequence[Any], selection: Selection, opened: OpenedPackage) -> RunRecord:
    """The run record of a package run: it names the package instead of a paper.

    An archive was extracted to a temporary folder; the record (and each
    output's ``run_provenance``) gives the archive's own path wherever that
    folder was an argument.
    """
    from metacheck.provenance import RunRecord

    base = str(opened.base_dir)
    source = os.path.abspath(os.path.expanduser(opened.source))
    for out in outputs:
        args = (getattr(out, "run_provenance", None) or {}).get("args")
        if isinstance(args, dict):
            for key, value in args.items():
                if isinstance(value, str) and value == base:
                    args[key] = source
    rec = RunRecord.build(outputs, selection=selection, papers=[])
    rec.package = {"name": _name(opened), "source": source, "archive": opened.archive}
    return rec
