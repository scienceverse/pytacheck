"""Run the ``power`` module with the LLM on over a set of papers; write one JSON line per paper.

``power`` is the one module that calls an LLM and has been validated against the R package, so a
change to the LLM back end is measured on it: run this script once on the code before the change
and once on the code after it, then compare the two files with ``scripts/llm_power_compare.py``.
Every run calls the model for real (the cache is off), which costs a little money and needs the
provider's key in the environment (``GEMINI_API_KEY``, ``OPENAI_API_KEY`` ...).

    # the current checkout, in the current environment
    uv run --extra llm python scripts/llm_power_before_after.py \\
        --model google_gemini/gemini-2.5-flash --out after.jsonl PAPERS...

    # a commit, in a throwaway checkout with its own environment (``uv`` builds it)
    uv run python scripts/llm_power_before_after.py --commit origin/main \\
        --model google_gemini/gemini-2.5-flash --out before.jsonl PAPERS...

    # any interpreter that has metacheck installed (a released version, an older venv)
    uv run python scripts/llm_power_before_after.py --python /path/to/venv/bin/python \\
        --model google_gemini/gemini-2.5-flash --out before.jsonl PAPERS...

    uv run python scripts/llm_power_compare.py before.jsonl after.jsonl

``PAPERS`` are anything ``metacheck.read()`` takes: paper ``.json`` files, bibr or Grobid ``.xml``
files (a PDF needs the ``bibr`` extra), or folders of them. ``--demo`` adds the bundled demo paper.
The set should hold papers with power analyses in them and a few without; a paper with no
paragraph that looks like a power analysis costs no LLM call.

Run the *before* side twice (``--out before.jsonl`` and ``--out before2.jsonl``) and compare those
two as well: that is how much the model differs from itself, and a difference between before and
after is worth reading only when it is larger than that.

The file holds a first line that describes the run (code version, SDK versions, model, seed) and
then one line per paper: the traffic light, the extracted rows (one per power analysis, with the
columns the model filled), whether the structured request or the prompt fallback gave them, and
the warnings the run raised (a failed request is a warning, and the paper's ``path`` then says
``prompt`` or ``failed``). Nothing here is run by the tests with a live model.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import warnings
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parent.parent

#: R's power() default; the seed is sent with every request
DEFAULT_SEED = 8675309
#: the columns of the text frame; every other column of the table is something the model filled
_TEXT_COLUMNS = frozenset(
    {
        "text",
        "text_id",
        "paragraph_id",
        "section_id",
        "page_number",
        "formatted",
        "paper_id",
        "header",
        "section_type",
        "power_id",
    }
)
#: the packages whose versions the header records
_PACKAGES = ("metacheck", "openai", "anthropic", "google-genai", "httpx", "pandas")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n", maxsplit=1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("papers", nargs="*", help="paper files or folders, as metacheck.read() takes")
    p.add_argument("--demo", action="store_true", help="also run the bundled demo paper")
    p.add_argument(
        "--model", required=True, help="the model, as 'provider/model' (llm_model() sets it)"
    )
    p.add_argument("--out", required=True, type=Path, help="the JSON-lines file to write")
    p.add_argument("--label", help="a name for this run in the header (default: the commit)")
    p.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help="power()'s seed (default: %(default)s)"
    )
    p.add_argument("--max-calls", type=int, help="llm_max_calls() (metacheck's default is 30)")
    p.add_argument("--timeout", type=float, help="llm_timeout() in seconds (default 180)")
    p.add_argument("--limit", type=int, help="run the first N papers only")
    where = p.add_argument_group("where to run (default: the current environment)")
    where.add_argument("--commit", help="a git revision: run in a throwaway checkout of it")
    where.add_argument("--python", help="an interpreter with metacheck installed")
    where.add_argument(
        "--extra",
        action="append",
        default=[],
        help="with --commit: an extra of that checkout to install (repeatable; its 'llm' extra "
        "is added when it has one)",
    )
    where.add_argument("--keep", action="store_true", help="with --commit: keep the checkout")
    return p.parse_args(argv)


# ---------------------------------------------------------------- run in another environment


def _forward(args: argparse.Namespace, papers: list[str]) -> list[str]:
    """The arguments of this script without where-to-run, for the run in the other environment."""
    out = [*papers, "--model", args.model, "--out", str(args.out), "--seed", str(args.seed)]
    for flag, value in (
        ("--label", args.label),
        ("--max-calls", args.max_calls),
        ("--timeout", args.timeout),
        ("--limit", args.limit),
    ):
        if value is not None:
            out += [flag, str(value)]
    if args.demo:
        out.append("--demo")
    return out


def _extras_of(tree: Path, wanted: list[str]) -> list[str]:
    """``--extra`` flags for ``uv run``: those asked for, and ``llm`` when the checkout has it."""
    extras = list(wanted)
    with (tree / "pyproject.toml").open("rb") as f:
        defined = tomllib.load(f).get("project", {}).get("optional-dependencies", {})
    if "llm" in defined and "llm" not in extras:
        extras.append("llm")
    return [x for e in extras for x in ("--extra", e)]


def _run_elsewhere(args: argparse.Namespace) -> int:
    papers = [str(Path(p).resolve()) for p in args.papers]
    args.out = args.out.resolve()
    forward = _forward(args, papers)
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    if args.python:
        return subprocess.run([args.python, str(SCRIPT), *forward], env=env, check=False).returncode
    tree = Path(tempfile.mkdtemp(prefix="llm-power-")) / "checkout"
    subprocess.run(
        ["git", "-C", str(ROOT), "worktree", "add", "--detach", str(tree), args.commit], check=True
    )
    try:
        if args.label is None:
            forward += ["--label", args.commit]
        cmd = ["uv", "run", "--project", str(tree), *_extras_of(tree, args.extra)]
        cmd += ["python", str(SCRIPT), *forward]
        return subprocess.run(cmd, cwd=tree, env=env, check=False).returncode
    finally:
        if args.keep:
            print(f"kept the checkout at {tree}", file=sys.stderr)
        else:
            subprocess.run(
                ["git", "-C", str(ROOT), "worktree", "remove", "--force", str(tree)], check=False
            )
            shutil.rmtree(tree.parent, ignore_errors=True)


# ---------------------------------------------------------------- run here


def _git(tree: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(tree), *args], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return done.stdout.strip()


def _describe_code() -> dict[str, Any]:
    """Which code ran: the commit (and whether the tree has changes), or the installed version."""
    import metacheck

    where = Path(metacheck.__file__).resolve().parent
    info: dict[str, Any] = {"metacheck_path": str(where)}
    commit = _git(where, "rev-parse", "HEAD")
    if commit:
        info["commit"] = commit
        info["dirty"] = bool(_git(where, "status", "--porcelain", "--untracked-files=no"))
    return info


def _versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in _PACKAGES:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
    return out


def _plain(value: Any) -> Any:
    """A JSON value for a table cell: missing values are ``null``, numpy scalars Python ones."""
    import pandas as pd

    if value is None or value is pd.NA:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _rows(table: Any) -> list[dict[str, Any]]:
    """One dict per power analysis: ``text_id``, the start of the text, and what the model filled."""
    if table is None or len(table) == 0:
        return []
    filled = [c for c in table.columns if c not in _TEXT_COLUMNS]
    rows = []
    for i in range(len(table)):
        row: dict[str, Any] = {}
        if "text_id" in table.columns:
            row["text_id"] = _plain(table["text_id"].iloc[i])
        if "text" in table.columns:
            row["text_head"] = (_plain(table["text"].iloc[i]) or "")[:120]
        for c in filled:
            row[c] = _plain(table[c].iloc[i])
        rows.append(row)
    return rows


def _report_facts(mo: Any) -> dict[str, Any]:
    """What the module's report says about the LLM: the model, the paragraphs, the path taken."""
    import importlib

    texts = importlib.import_module("metacheck.modules.power")
    report = mo.report if isinstance(mo.report, list) else [mo.report]
    text = "\n".join(str(x) for x in report) + "\n" + str(mo.summary_text)
    model = re.search(r"We used the LLM model '([^']+)'", text)
    n = re.search(r"contents of (\d+) paragraph", text)
    return {
        "model": model.group(1) if model else None,
        "n_candidates": int(n.group(1)) if n else None,
        "llm_failed": texts._LLM_FAILED_TEXT in text,
        "llm_off": texts._NO_LLM_TEXT in text,
        "prompt_fallback": texts._NOT_STRUCTURED_TEXT in text,
    }


def _path_taken(facts: dict[str, Any], n_rows: int) -> str:
    """``structured`` or ``prompt`` (the fallback) gave the rows; ``none`` found no analysis."""
    if facts["llm_failed"]:
        return "failed"
    if facts["llm_off"]:
        return "regex"  # the LLM was not used at all
    if facts["prompt_fallback"]:
        return "prompt"
    return "structured" if n_rows else "none"


def _papers_of(args: argparse.Namespace) -> list[Any]:
    import metacheck as pc

    found: list[Any] = []
    if args.demo:
        found.append(pc.demopaper())
    for given in args.papers:
        got = pc.read(given)
        found.extend(list(got) if pc.is_paper_list(got) else [got])
    if args.limit is not None:
        found = found[: args.limit]
    return found


def run(args: argparse.Namespace) -> int:
    import metacheck as pc
    from metacheck import module_run
    from metacheck.llm import llm_cache, llm_max_calls, llm_model, llm_timeout, llm_use

    llm_use(True)
    llm_model(args.model)
    llm_cache(False)
    if args.max_calls is not None:
        llm_max_calls(args.max_calls)
    if args.timeout is not None:
        llm_timeout(args.timeout)

    papers = _papers_of(args)
    if not papers:
        print("no papers: pass files or folders, or --demo", file=sys.stderr)
        return 2

    header = {
        "kind": "run",
        "label": args.label,
        "model": args.model,
        "seed": args.seed,
        "papers": len(papers),
        "started": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "versions": _versions(),
        **_describe_code(),
    }
    if header["label"] is None:
        header["label"] = (header.get("commit") or header["versions"]["metacheck"] or "run")[:12]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    failed = 0
    with args.out.open("w", encoding="utf-8") as f:
        f.write(json.dumps(header) + "\n")
        for n, paper in enumerate(papers, start=1):
            paper_id = str(pc.paper_id(paper)[0])
            rec: dict[str, Any] = {"kind": "paper", "paper_id": paper_id, "error": None}
            began = time.monotonic()
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    mo = module_run(paper, "power", seed=args.seed)
                except Exception as e:
                    rec["error"] = f"{type(e).__name__}: {e}"[:500]
                    mo = None
            rec["seconds"] = round(time.monotonic() - began, 1)
            rec["warnings"] = sorted({str(w.message)[:300] for w in caught})
            if mo is not None:
                rows = _rows(mo.table)
                facts = _report_facts(mo)
                rec.update(
                    traffic_light=mo.traffic_light,
                    summary_text=str(mo.summary_text),
                    model=facts["model"],
                    n_candidates=facts["n_candidates"],
                    path=_path_taken(facts, len(rows)),
                    rows=rows,
                )
            else:
                failed += 1
                rec.update(traffic_light=None, rows=[], path="failed")
            f.write(json.dumps(rec) + "\n")
            f.flush()
            print(
                f"[{n}/{len(papers)}] {paper_id}: {rec['traffic_light']}, "
                f"{len(rec['rows'])} rows, {rec['seconds']} s",
                file=sys.stderr,
            )
    print(f"wrote {args.out} ({len(papers)} papers, {failed} that stopped with an error)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.commit and args.python:
        print("--commit and --python exclude each other", file=sys.stderr)
        return 2
    if args.commit or args.python:
        return _run_elsewhere(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
