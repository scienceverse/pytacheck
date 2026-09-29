"""Python side of the report parity cases (twin of parity_helpers.R)."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODULES = ROOT / "tests" / "report" / "modules"


def identity(x: Any) -> Any:
    """R ``identity()``."""
    return x


def rp_mod(m: str) -> str:
    f = MODULES / f"{m}.py"
    return str(f) if f.is_file() else m


def rp_stem(x: str) -> str:
    return re.sub(r"\.(R|py)$", "", os.path.basename(str(x)))


def rp_mask(x: str) -> str:
    x = re.sub(r"\n```\{r\}.*?\n```\n", "\n<R-CHUNK>\n", x, flags=re.S)
    return re.sub(r"(?m)^(\s*(?:version|report-created): ).*$", r"\1<masked>", x)


def rp_norm_output(mo: Any) -> dict[str, Any]:
    from dataclasses import replace

    out = {}
    for name, op in mo.items():
        new = replace(op, module=rp_stem(op.module))
        if op.traffic_light == "fail":
            new = replace(new, title=rp_stem(op.title))
        out[rp_stem(name)] = new
    return out


def _mods(modules: Any) -> list[str]:
    modules = [modules] if isinstance(modules, str) else list(modules)
    return [rp_mod(m) for m in modules]


def _args(args: Any) -> dict[str, Any]:
    return {rp_mod(k): v for k, v in (args or {}).items()}


def rp_module_report(paper: Any, module: str, header: Any = 3, **kwargs: Any) -> str:
    from metacheck.module import module_run
    from metacheck.report.report import module_report

    op = module_run(paper, rp_mod(module), **kwargs)
    return rp_mask(module_report(op, header=header))


def rp_report_module_run(paper: Any, modules: Any, args: Any = None) -> dict[str, Any]:
    from metacheck.report.report import report_module_run

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mo = report_module_run(paper, _mods(modules), _args(args))
    return rp_norm_output(mo)


def _unpath(text: str) -> str:
    # the report writes a module's anchor from its lower-cased path
    return re.sub(re.escape(str(MODULES)) + r"/([a-z_]+)\.py", r"\1", text, flags=re.I)


def rp_report_qmd(paper: Any, modules: Any, args: Any = None, qmd_paper: Any = "same") -> str:
    from metacheck.report.report import report_module_run, report_qmd

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mo = report_module_run(paper, _mods(modules), _args(args))
    txt = report_qmd(mo, paper if isinstance(qmd_paper, str) and qmd_paper == "same" else qmd_paper)
    return rp_mask(_unpath(txt))


def rp_report(
    paper: Any, modules: Any, output_format: str = "qmd", args: Any = None
) -> dict[str, Any]:
    from metacheck.report.report import report

    fd, f = tempfile.mkstemp(suffix=f".{output_format}")
    os.close(fd)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = report(paper, _mods(modules), f, output_format, _args(args))
        txt = Path(f).read_text(encoding="utf-8")
        txt = txt[:-1] if txt.endswith("\n") else txt  # paste(readLines(f), collapse = "\n")
    finally:
        os.unlink(f)
    return {
        "output": rp_norm_output(res),
        "save_path_ok": res.save_path == f,
        "file": rp_mask(_unpath(txt)),
    }


def rp_html_export(fixture: str) -> Any:
    from metacheck.report.html_output import _html_export_r_source

    d = tempfile.mkdtemp()
    try:
        shutil.copy(ROOT / "tests" / "report" / "fixtures" / "html" / fixture, d)
        p = _html_export_r_source(os.path.join(d, fixture))
        if p is None:
            return None
        lines = Path(p).read_text(encoding="utf-8").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return {"file": p.replace(d + "/", "", 1), "lines": lines}
    finally:
        shutil.rmtree(d, ignore_errors=True)


def fixture(name: str) -> str:
    return str(ROOT / "tests" / "report" / "fixtures" / "html" / name)


def rp_report_repository(folder: str, modules: Any, args: Any = None) -> dict[str, Any]:
    from metacheck.report.report import report_repository

    d = tempfile.mkdtemp()
    fd, f = tempfile.mkstemp(suffix=".qmd")
    os.close(fd)
    try:
        os.makedirs(os.path.join(d, folder))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = report_repository(
                os.path.join(d, folder),
                output_file=f,
                output_format="qmd",
                modules=_mods(modules),
                args=_args(args),
            )
        txt = Path(f).read_text(encoding="utf-8")
        txt = txt[:-1] if txt.endswith("\n") else txt
    finally:
        shutil.rmtree(d, ignore_errors=True)
        os.unlink(f)
    out = rp_norm_output(res)
    for op in out.values():
        if op.summary_table is not None and "paper_id" in op.summary_table.columns:
            st = op.summary_table.copy()
            st["paper_id"] = "test_paper"
            op.summary_table = st
    return {"output": out, "file": rp_mask(_unpath(txt))}


def rp_report_repository_dir(path: str, modules: Any = None, args: Any = None) -> dict[str, Any]:
    """``report_repository()`` on a repository folder of the checkout (repo-relative *path*)."""
    from metacheck.report.report import report_repository

    fd, f = tempfile.mkstemp(suffix=".qmd")
    os.close(fd)
    extra: dict[str, Any] = {} if modules is None else {"modules": _mods(modules)}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = report_repository(
                str(ROOT / path), output_file=f, output_format="qmd", args=args or {}, **extra
            )
        txt = Path(f).read_text(encoding="utf-8")
        txt = txt[:-1] if txt.endswith("\n") else txt
    finally:
        os.unlink(f)
    out = rp_norm_output(res)
    for op in out.values():
        if op.summary_table is not None and "paper_id" in op.summary_table.columns:
            st = op.summary_table.copy()
            st["paper_id"] = "test_paper"
            op.summary_table = st
    return {"output": out, "file": rp_mask(txt)}


def rp_validate(gt: Any, module: str) -> Any:
    from metacheck.validate import validate

    return validate(gt, rp_mod(module))
