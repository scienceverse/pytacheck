"""Python side of the FAIR_repo parity cases (twin of ``parity_helpers.R``).

``ff_run()`` copies a fixture (``tests/fair/fixtures/<name>``, written by ``make_fixtures.py``,
or an absolute path such as ``demofile("repo")``) to ``<tmp>/<name>``, runs ``FAIR_repo()`` on the
copy with the language model off and returns a summary of the folder it wrote. R's helper returns
the same names and shapes. What is masked, and why:

* today's date -> ``<date>`` (CHANGES.md, the README, ``metacheck:generated``, ``dateCreated``);
* any temporary folder -> ``<tmp>``;
* the file the checks' results are saved in: metacheck writes an R data file
  (``logs/<folder>.rds``), pytacheck a JSON file (``logs/<folder>.json``, D138). Both are written
  ``<folder>.<results>`` wherever the name occurs (the file list, the RO-Crate, the README's
  tree), and the RO-Crate's ``encodingFormat`` of that one file is ``<results>``.

Nothing else is changed.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fair" / "fixtures"

Subs = list[tuple[str, str]]


def identity(x: Any) -> Any:
    """R ``identity()``: the parity cases call a helper that builds the value."""
    return x


def ff_fixture_dir(fixture: str | os.PathLike[str]) -> Path:
    """The folder of a fixture; an absolute path is used as it is."""
    p = Path(fixture)
    return p if p.is_absolute() else FIXTURES / p


def ff_tmp() -> Path:
    """A new empty folder in the temporary folder."""
    return Path(tempfile.mkdtemp(prefix="ff-"))


def ff_copy(fixture: str | os.PathLike[str], base: Path) -> Path:
    """Copy a fixture into *base* as ``<base>/<name>`` and return that path. ``"empty"`` is an
    empty folder (git cannot hold one)."""
    if str(fixture) == "empty":
        work = base / "empty"
        work.mkdir()
        return work
    src = ff_fixture_dir(fixture)
    work = base / src.name
    shutil.copytree(src, work)
    return work


def ff_lines(path: str | os.PathLike[str]) -> list[str]:
    """``readLines()``: UTF-8, any of LF, CRLF and CR ends a line."""
    text = Path(path).read_bytes().decode("utf-8", errors="replace")
    parts = re.split(r"\r\n|\n|\r", text)
    if parts and parts[-1] == "":
        parts.pop()
    return parts


def ff_json(path: str | os.PathLike[str]) -> Any:
    """A JSON file as nested dicts and lists, or ``None`` when it does not exist."""
    if not Path(path).is_file():
        return None
    return json.loads("\n".join(ff_lines(path)))


def ff_subs(base: Path, pid: str) -> Subs:
    """The substitutions of every string: temporary folders (longest first), today's date, and
    the name of the file the results are saved in (see the top of this file)."""
    roots = {
        os.path.realpath(base),
        str(base),
        os.path.realpath(tempfile.gettempdir()),
        tempfile.gettempdir(),
    }
    ordered = sorted((r for r in roots if r), key=len, reverse=True)
    subs: Subs = [(r, "<tmp>") for r in ordered]
    subs.append((time.strftime("%Y-%m-%d"), "<date>"))
    subs.append((f"{pid}.json", f"{pid}.<results>"))
    return subs


def ff_clean(x: Any, subs: Subs) -> Any:
    """Every string at any depth with the substitutions made (dict keys are kept)."""
    if isinstance(x, str):
        for old, new in subs:
            x = x.replace(old, new)
        return x
    if isinstance(x, dict):
        return {k: ff_clean(v, subs) for k, v in x.items()}
    if isinstance(x, list):
        return [ff_clean(v, subs) for v in x]
    return x


def ff_mask_results(crate: Any, pid: str) -> Any:
    """The RO-Crate entity of the results file has the format of that file in R
    (``application/x-r-rds``) and here (``application/json``): both ``<results>``."""
    graph = crate.get("@graph") if isinstance(crate, dict) else None
    if not isinstance(graph, list):
        return crate
    target = f"logs/{pid}.<results>"
    for entity in graph:
        if isinstance(entity, dict) and entity.get("@id") == target and "encodingFormat" in entity:
            entity["encodingFormat"] = "<results>"
    return crate


def _get(res: Any, name: str) -> Any:
    if isinstance(res, Mapping):
        return res.get(name)
    return getattr(res, name, None)


def _plan_columns(plan: Any) -> pd.DataFrame | None:
    if not isinstance(plan, pd.DataFrame) or not len(plan):
        return None
    cols = [
        c
        for c in (
            "file_name",
            "data_type",
            "group",
            "target_path",
            "written",
            "convert",
            "original_target",
            "same_as",
            "script_writes",
            "recovered_row",
            "recovered_source",
        )
        if c in plan.columns
    ]
    return plan[cols].reset_index(drop=True)


def _strings(x: Any) -> list[str]:
    return [] if x is None else [str(v) for v in x]


def ff_summary(res: Any, out: Path, pid: str, subs: Subs) -> dict[str, Any]:
    """What ``FAIR_repo()`` wrote into *out*, as plain values."""
    actual = [p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()]
    shown = {a: ff_clean(a, subs) for a in actual}
    actual.sort(key=lambda a: (shown[a], a))

    def skip(a: str) -> bool:
        return (
            a.startswith("logs/")
            or a
            in (
                "README.md",
                "CHANGES.md",
                "ro-crate-metadata.json",
                "dataset_description.json",
                "requirements.txt",
            )
            or re.fullmatch(r"study-[^/]+/(README\.md|CHANGES\.md|dataset_description\.json)", a)
            is not None
        )

    md5 = {a: hashlib.md5((out / a).read_bytes()).hexdigest() for a in actual if not skip(a)}
    study_descriptions = {
        a: ff_clean(ff_json(out / a), subs)
        for a in actual
        if re.fullmatch(r"study-[^/]+/dataset_description\.json", a)
    }
    texts = {
        a: ff_clean(ff_lines(out / a), subs)
        for a in actual
        if a in ("CHANGES.md", "README.md", "requirements.txt")
        or re.fullmatch(r"study-[^/]+/(README|CHANGES)\.md", a)
    }
    scripts = {
        a: ff_lines(out / a)
        for a in actual
        if re.search(r"\.(r|rmd|qmd|py|ipynb)$", a, flags=re.IGNORECASE)
    }
    rocrate = ff_json(out / "ro-crate-metadata.json")
    if rocrate is not None:
        rocrate = ff_mask_results(ff_clean(rocrate, subs), pid)

    result = {
        "n_files_copied": _get(res, "n_files_copied"),
        "n_studies": _get(res, "n_studies"),
        "empty_plan": bool(_get(res, "empty_plan")),
        "skipped": _strings(_get(res, "skipped")),
        "copy_failed": _strings(_get(res, "copy_failed")),
        "renamed": _get(res, "renamed"),
        "identical": _get(res, "identical"),
        "scripts": _get(res, "scripts"),
        "plan": _plan_columns(_get(res, "plan")),
        "existed": bool(_get(res, "existed")),
    }

    fl = ff_json(out / "logs" / f"{pid}.files.json")
    filelog = None
    if fl is not None:
        filelog = {
            "folder": fl.get("folder"),
            "n_files": fl.get("n_files"),
            "files": [
                {k: f[k] for k in ("file_path", "file_size", "data_type") if f.get(k) is not None}
                for f in fl.get("files") or []
            ],
            "llm": (fl.get("provenance") or {}).get("llm"),
        }

    return {
        "files": [shown[a] for a in actual],
        "md5": md5,
        "description": ff_clean(ff_json(out / "dataset_description.json"), subs),
        "study_descriptions": study_descriptions,
        "rocrate": rocrate,
        "texts": texts,
        "scripts": scripts,
        "result": result,
        "filelog": filelog,
    }


@contextlib.contextmanager
def _llm_off() -> Iterator[None]:
    """``options(metacheck.llm.use = FALSE)`` for a while."""
    from metacheck.llm.core import llm_use

    old = llm_use()
    llm_use(False)
    try:
        yield
    finally:
        llm_use(old)


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    """``suppressMessages()``: FAIR_repo's progress messages go nowhere."""
    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        yield


def ff_run(
    fixture: str | os.PathLike[str],
    paper: Any = None,
    overwrite: bool = False,
    execute: bool = False,
    drop: Sequence[str] = (),
    existing: bool = False,
) -> dict[str, Any]:
    """``FAIR_repo()`` on a copy of a fixture, the language model off, as a summary.

    *drop*: paths (in the copy) to delete first; *existing*: make the output folder first,
    holding a file ``mine.txt``.
    """
    from metacheck.fair.repo import FAIR_repo

    base = ff_tmp()
    try:
        work = ff_copy(fixture, base)
        for f in drop:
            target = work / f
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        out = base / "out"
        if existing:
            out.mkdir()
            (out / "mine.txt").write_text("keep\n", encoding="utf-8")
        with _llm_off(), _quiet():
            res = FAIR_repo(
                str(work), output_dir=str(out), paper=paper, overwrite=overwrite, execute=execute
            )
        return ff_summary(res, out, work.name, ff_subs(base, work.name))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def ff_test_paper(info: Mapping[str, Any] | None = None) -> Any:
    """A test paper with some fields of ``paper.info`` set: *info* maps a name to a single value
    or, for a list, to a list cell (``{"keywords": ["a", "b"]}``)."""
    from metacheck.papers.io import test_paper

    p = test_paper(["x"])
    for name, value in (info or {}).items():
        if isinstance(value, list):
            p.info[name] = pd.Series([value], dtype=object)
        else:
            p.info[name] = pd.Series([value], dtype="string")
    return p


def ff_paper(links: bool = False, funding: bool = False) -> Any:
    """The paper of ``fair_paper()`` in test-fair-repo.R: a title, a DOI and two authors with an
    email, an ORCID, an affiliation and CRediT roles. *links*: the text names a repository on OSF
    and on GitHub; *funding*: a funding statement, a funder and an award."""
    p = ff_test_paper({"title": "A test paper", "doi": "10.1234/abc"})
    author = pd.DataFrame(
        {
            "author_id": pd.Series([1, 2], dtype="Int64"),
            "given": pd.Series(["Ann", "Bob"], dtype="string"),
            "family": pd.Series(["Smith", "Jones"], dtype="string"),
            "email": pd.Series(["ann@x.org", None], dtype="string"),
            "corresponding": pd.Series([True, False], dtype="boolean"),
            "orcid": pd.Series(["0000-0001-2345-6789", None], dtype="string"),
            "affiliation": pd.Series(["TU/e", None], dtype="string"),
        }
    )
    author["credit_roles"] = pd.Series(
        [["Conceptualization", "Writing - original draft"], []], dtype=object
    )
    p.author = author
    if links:
        p.text["text"] = pd.Series(
            ["Data and code are at https://osf.io/abcde and https://github.com/a/b."],
            dtype="string",
        )
    if funding:
        p.info["funding_statement"] = pd.Series(["Supported by ERC."], dtype="string")
        fu = pd.DataFrame(
            {
                "funding_id": pd.Series([1.0], dtype="float64"),
                "funder": pd.Series(["ERC"], dtype="string"),
            }
        )
        fu["award_ids"] = pd.Series([["A1"]], dtype=object)
        p.funding = fu
    return p


def ff_error(kind: str) -> dict[str, bool]:
    """Whether ``FAIR_repo()`` fails for a wrong argument, on a copy of the fixture ``basic``:
    ``{"error": True}`` (it failed) or ``{"error": False}``. The messages are never compared."""
    from metacheck.fair.repo import FAIR_repo

    base = ff_tmp()
    try:
        d = ff_copy("basic", base)
        out = str(base / "out")
        with _llm_off(), _quiet():
            try:
                if kind == "no_output_dir":
                    FAIR_repo(str(d))  # type: ignore[call-arg]
                elif kind == "not_a_folder":
                    FAIR_repo(str(base / "no_such_folder"), output_dir=out)
                elif kind == "two_paths":
                    FAIR_repo(["a", "b"], output_dir=out)  # type: ignore[arg-type]
                elif kind == "a_file":
                    FAIR_repo(str(d / "README.txt"), output_dir=out)
                elif kind == "inside":
                    FAIR_repo(str(d), output_dir=str(d / "copy"))
                elif kind == "same_folder":
                    FAIR_repo(str(d), output_dir=str(d))
                elif kind == "bad_paper":
                    FAIR_repo(str(d), output_dir=out, paper=[1])
                else:
                    raise ValueError("unknown kind")
            except Exception:
                return {"error": True}
        return {"error": False}
    finally:
        shutil.rmtree(base, ignore_errors=True)
