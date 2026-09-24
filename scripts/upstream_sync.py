"""Track metacheck's dev branch: detect new commits and prepare a porting brief.

Used by .github/workflows/upstream-sync.yml, and runnable by hand:

    uv run python scripts/upstream_sync.py status
    uv run python scripts/upstream_sync.py prepare            # to origin/dev
    uv run python scripts/upstream_sync.py prepare --to <sha>

``prepare`` moves the upstream/metacheck submodule to the target commit,
updates the pin (parity/UPSTREAM.toml and pytacheck._version.UPSTREAM), and
writes .upstream-sync/brief.md: the commit log, the changed files, the R diff,
the Python files mapped to each changed R file (porting/map/*.toml and
porting/symbols.json), and added/removed/changed R functions. The workflow
then regenerates the goldens in R and appends which ones changed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUB = ROOT / "upstream" / "metacheck"
UPSTREAM_TOML = ROOT / "parity" / "UPSTREAM.toml"
VERSION_PY = ROOT / "src" / "pytacheck" / "_version.py"
BRIEF_DIR = ROOT / ".upstream-sync"
DEF = re.compile(r"^`?([A-Za-z0-9._]+)`?\s*(?:<-|=)\s*function\b")
MAX_DIFF_CHARS = 400_000


def git(*args: str, cwd: Path = SUB) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def pinned() -> dict[str, str]:
    return tomllib.loads(UPSTREAM_TOML.read_text(encoding="utf-8"))["upstream"]


def remote_head(branch: str) -> str:
    git("fetch", "--quiet", "--filter=blob:none", "origin", branch)
    return git("rev-parse", f"origin/{branch}").strip()


def description_version(commit: str) -> str:
    text = git("show", f"{commit}:DESCRIPTION")
    m = re.search(r"^Version:\s*(\S+)", text, re.M)
    return m.group(1) if m else "unknown"


def functions_at(commit: str, path: str) -> dict[str, str]:
    """Top-level R function name -> source text of its definition block."""
    try:
        text = git("show", f"{commit}:{path}")
    except subprocess.CalledProcessError:
        return {}
    lines = text.splitlines()
    starts = [(i, m.group(1)) for i, line in enumerate(lines) if (m := DEF.match(line))]
    out: dict[str, str] = {}
    for k, (i, name) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(lines)
        out[name] = "\n".join(lines[i:end]).strip()
    return out


def python_targets() -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {}
    for toml in sorted((ROOT / "porting" / "map").glob("*.toml")):
        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        for r_file, py_files in data.get("files", {}).items():
            targets.setdefault(r_file, []).extend(py_files)
    return targets


def write_pin(commit: str, version: str, branch: str) -> None:
    text = UPSTREAM_TOML.read_text(encoding="utf-8")
    text = re.sub(r'^commit = ".*"$', f'commit = "{commit}"', text, flags=re.M)
    text = re.sub(r'^version = ".*"$', f'version = "{version}"', text, flags=re.M)
    text = re.sub(r'^branch = ".*"$', f'branch = "{branch}"', text, flags=re.M)
    UPSTREAM_TOML.write_text(text, encoding="utf-8")
    code = VERSION_PY.read_text(encoding="utf-8")
    code = re.sub(r'"commit": "[0-9a-f]+"', f'"commit": "{commit}"', code)
    code = re.sub(r'"version": "[^"]+"', f'"version": "{version}"', code)
    VERSION_PY.write_text(code, encoding="utf-8")


def cmd_status(ns: argparse.Namespace) -> int:
    pin = pinned()
    head = remote_head(pin["branch"])
    behind = git("rev-list", "--count", f"{pin['commit']}..{head}").strip()
    print(json.dumps({"pinned": pin["commit"], "head": head, "behind": int(behind)}))
    return 0


def cmd_prepare(ns: argparse.Namespace) -> int:
    pin = pinned()
    old = pin["commit"]
    new = ns.to or remote_head(pin["branch"])
    if new == old:
        print("already at", old)
        return 0
    git("fetch", "--quiet", "--filter=blob:none", "origin", new)
    git("checkout", "--quiet", new)
    version = description_version(new)
    write_pin(new, version, pin["branch"])

    log = git("log", "--format=- %h %s (%an, %ad)", "--date=short", f"{old}..{new}")
    changed = [
        line.split("\t")
        for line in git("diff", "--name-status", f"{old}..{new}").splitlines()
        if line.strip()
    ]
    targets = python_targets()
    symbols = json.loads((ROOT / "porting" / "symbols.json").read_text(encoding="utf-8"))

    out = [
        f"# Upstream sync: metacheck {pin['version']} ({old[:10]}) -> {version} ({new[:10]})",
        "",
        "Port these upstream changes to pytacheck following docs/PORTING.md.",
        "",
        "## Commits",
        "",
        log.strip() or "(none)",
        "",
        "## Changed files",
        "",
        "| status | upstream file | pytacheck files |",
        "|---|---|---|",
    ]
    fn_changes: list[str] = []
    for parts in changed:
        status, path = parts[0], parts[-1]
        py = targets.get(path) or targets.get(str(Path(path).parent)) or []
        if not py and path.startswith("inst/modules/") and path.endswith(".R"):
            py = [f"src/pytacheck/modules/{Path(path).stem}.py"]
        out.append(f"| {status} | `{path}` | {', '.join(f'`{p}`' for p in py) or '—'} |")
        if path.startswith("R/") and path.endswith(".R"):
            before, after = functions_at(old, path), functions_at(new, path)
            for name in sorted(set(before) | set(after)):
                where = symbols.get(name, {}).get("python", "(new: add to porting/symbols.json)")
                if name not in before:
                    fn_changes.append(f"- **added** `{name}` in `{path}` -> `{where}`")
                elif name not in after:
                    fn_changes.append(f"- **removed** `{name}` from `{path}` (was `{where}`)")
                elif before[name] != after[name]:
                    fn_changes.append(f"- changed `{name}` (`{path}`) -> `{where}`")
    out += ["", "## Changed R functions", "", *(fn_changes or ["(none)"]), ""]

    diff = (
        git("diff", "--stat", f"{old}..{new}")
        + "\n"
        + git(
            "diff",
            f"{old}..{new}",
            "--",
            "R",
            "inst/modules",
            "inst/schema",
            "inst/templates",
            "DESCRIPTION",
            "NEWS.md",
            "tests/testthat/*.R",
        )
    )
    if len(diff) > MAX_DIFF_CHARS:
        diff = (
            diff[:MAX_DIFF_CHARS]
            + "\n... (diff truncated; inspect with git -C upstream/metacheck diff)\n"
        )
    out += ["## Diff", "", "```diff", diff, "```", ""]

    BRIEF_DIR.mkdir(exist_ok=True)
    (BRIEF_DIR / "brief.md").write_text("\n".join(out), encoding="utf-8")
    (BRIEF_DIR / "meta.json").write_text(
        json.dumps({"from": old, "to": new, "version": version}, indent=1), encoding="utf-8"
    )
    print(json.dumps({"from": old, "to": new, "version": version, "files": len(changed)}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    p = sub.add_parser("prepare")
    p.add_argument("--to", help="target commit (default: head of the tracked branch)")
    p.set_defaults(func=cmd_prepare)
    ns = parser.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":
    sys.exit(main())
