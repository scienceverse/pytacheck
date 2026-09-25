"""Track metacheck upstream: detect new commits and prepare a porting brief.

Used by .github/workflows/upstream-sync.yml, and runnable by hand:

    uv run python scripts/upstream_sync.py status
    uv run python scripts/upstream_sync.py prepare            # to the tracked head
    uv run python scripts/upstream_sync.py prepare --to <sha>
    uv run python scripts/upstream_sync.py prepare --drop-pr  # stop tracking the pull request

pytacheck follows metacheck's ``dev`` branch. The pin (parity/UPSTREAM.toml and
pytacheck._version.UPSTREAM) can also name an open pull request that pytacheck
targets before it is merged (``pull_request``, with ``base_commit``, the dev
commit it is built on). Then the tracked head is:

* dev's head, once the pull request is merged into dev: its head is in dev, or
  dev has its squashed commit ("... (#N)") or all of its rebased commits. The
  pin goes back to plain dev (``pull_request`` and ``base_commit`` are dropped);
* otherwise the pull request's head (``refs/pull/<N>/head``), so a new push to
  the pull request is synced. The status and the brief list the dev commits the
  pull request does not contain: the pinned reference (and every golden) lacks
  them until the pull request is merged or rebased, so the sync warns about them.

``prepare`` moves the upstream/metacheck submodule to the target commit,
updates the pin, and writes .upstream-sync/brief.md: what is tracked, the commit
log, the changed files, the R diff, the Python files mapped to each changed R
file (porting/map/*.toml and porting/symbols.json), and added/removed/changed R
functions; .upstream-sync/meta.json has the pull request's title and summary.
The workflow then regenerates the goldens in R and appends which ones changed.

Around the porting agent, the workflow also calls:

    uv run python scripts/upstream_sync.py brief-parity --check C.json --accuracy A.json
    uv run python scripts/upstream_sync.py tier1-marks --out .upstream-sync/tier1-marks.json
    uv run python scripts/upstream_sync.py review --marks .upstream-sync/tier1-marks.json \
        --accuracy A.json

``brief-parity`` appends to the brief the parity check and the accuracy report
run after the goldens were regenerated, before porting: the marked cases whose R
golden changed (``r_changed``) or that now match R (``xpass``: upstream may have
fixed the bug), the failing cases and the accuracy differences no entry of
parity/accuracy/expected.yaml explains. ``tier1-marks`` records the marks of the
tier-1 (realistic) cases before porting; ``review`` says, after porting, whether
the pull request needs human review: tier-1 marks, parity/accuracy/expected.yaml
or D-entries of docs/UPSTREAM_ISSUES.md changed, or the accuracy report warns
or fails.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SUB = ROOT / "upstream" / "metacheck"
UPSTREAM_TOML = ROOT / "parity" / "UPSTREAM.toml"
VERSION_PY = ROOT / "src" / "pytacheck" / "_version.py"
BRIEF_DIR = ROOT / ".upstream-sync"
DEF = re.compile(r"^`?([A-Za-z0-9._]+)`?\s*(?:<-|=)\s*function\b")
MAX_DIFF_CHARS = 400_000
MAX_LISTED_COMMITS = 200


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd or SUB, check=True, capture_output=True, text=True
    ).stdout


def git_ok(*args: str) -> bool:
    """Whether a git command succeeds (for yes/no questions such as ``--is-ancestor``)."""
    return subprocess.run(["git", *args], cwd=SUB, capture_output=True).returncode == 0


def pinned() -> dict[str, Any]:
    return tomllib.loads(UPSTREAM_TOML.read_text(encoding="utf-8"))["upstream"]


# --- git history -----------------------------------------------------------------


def ensure_history() -> None:
    """Unshallow the submodule: ancestry and merge-base need the history."""
    if git("rev-parse", "--is-shallow-repository").strip() == "true":
        git("fetch", "--quiet", "--unshallow", "--filter=blob:none", "origin")


def fetch(ref: str) -> str:
    """Fetch a branch (``refs/heads/dev``), a pull request ref or a commit; its sha."""
    git("fetch", "--quiet", "--filter=blob:none", "origin", ref)
    return git("rev-parse", "FETCH_HEAD^{commit}").strip()


def resolve(rev: str) -> str:
    """The full sha of a commit, fetched first unless it is already here."""
    if not git_ok("cat-file", "-e", f"{rev}^{{commit}}"):
        git("fetch", "--quiet", "--filter=blob:none", "origin", rev)
    return git("rev-parse", f"{rev}^{{commit}}").strip()


def remote_head(branch: str) -> str:
    return fetch(f"refs/heads/{branch}")


def pr_head(number: int) -> str:
    return fetch(f"refs/pull/{number}/head")


def merge_base(a: str, b: str) -> str:
    return git("merge-base", a, b).strip()


def commits(rev_range: str) -> list[str]:
    """``git log --oneline`` of a range, newest first."""
    return [line for line in git("log", "--format=%h %s", rev_range).splitlines() if line.strip()]


def pr_merged(number: int, head: str, branch_head: str, base: str | None = None) -> str | None:
    """How pull request *number* (at *head*) is in *branch_head*, or None if it is not.

    A merge commit (or a fast-forward) contains the head itself; a squash merge
    adds one commit whose subject ends in ``(#N)`` (GitHub's default); a rebase
    merge adds commits with the same patches (``git cherry``).
    """
    if git_ok("merge-base", "--is-ancestor", head, branch_head):
        return "merge"
    base = base or merge_base(head, branch_head)
    squash = re.compile(rf"\(#{number}\)\s*$")
    for line in git("log", "--format=%H %s", f"{base}..{branch_head}").splitlines():
        sha, _, subject = line.partition(" ")
        if squash.search(subject):
            return f"squash {sha[:10]}"
    cherry = [line for line in git("cherry", branch_head, head, base).splitlines() if line]
    if cherry and all(line.startswith("-") for line in cherry):
        return "rebase"
    return None


def plan(pin: dict[str, Any], to: str | None = None, drop_pr: bool = False) -> dict[str, Any]:
    """What to sync to, and the pin that results.

    ``mode`` is ``"branch"`` (no pull request tracked), ``"merged"`` (the
    tracked pull request is in the branch: back to the branch head) or
    ``"pull_request"`` (still open: its head). ``not_in_pr`` lists the branch
    commits the target lacks (only in ``"pull_request"`` mode).
    """
    ensure_history()
    branch = pin["branch"]
    head = remote_head(branch)
    number = pin.get("pull_request")
    out: dict[str, Any] = {
        "branch": branch,
        "branch_head": head,
        "pull_request": None,
        "base_commit": None,
        "pr_head": None,
        "pr_merged": None,
        "not_in_pr": [],
    }
    if to:
        to = resolve(to)
    if number is None or drop_pr:
        out.update(mode="branch", target=to or head)
        return out
    number = int(number)
    prh = pr_head(number)
    merged = pr_merged(number, prh, head, pin.get("base_commit"))
    out.update(pr_head=prh, pr_merged=merged)
    if merged:
        out.update(mode="merged", target=to or head)
        return out
    target = to or prh
    out.update(
        mode="pull_request",
        target=target,
        pull_request=number,
        base_commit=merge_base(target, head),
        not_in_pr=commits(f"{target}..{head}"),
    )
    return out


def needs_sync(pin: dict[str, Any], p: dict[str, Any]) -> bool:
    """Whether the commit or the pull request part of the pin changes."""
    return (
        p["target"] != pin["commit"]
        or p["pull_request"] != pin.get("pull_request")
        or p["base_commit"] != pin.get("base_commit")
    )


# --- the pin -----------------------------------------------------------------------


def _edit_pin(
    text: str,
    values: dict[str, Any],
    line_re: Callable[[str], re.Pattern[str]],
    render: Callable[[str, Any], str],
    comment: Callable[[int], list[str]],
) -> str:
    """Set ``branch``/``commit``/``version`` and add, update or drop the pull request.

    Lines are edited in place, so other lines and comments stay. A new pull
    request is inserted after ``commit`` with *comment* above it; dropping it
    also drops the comment lines directly above ``pull_request``.
    """
    lines = text.splitlines()

    def find(key: str) -> int | None:
        pat = line_re(key)
        return next((i for i, line in enumerate(lines) if pat.match(line)), None)

    for key in ("branch", "commit", "version"):
        i = find(key)
        if i is None:
            raise ValueError(f"the pin has no {key!r} entry")
        lines[i] = render(key, values[key])

    number, base = values.get("pull_request"), values.get("base_commit")
    old = find("pull_request")
    if old is not None and (number is None or not re.search(rf"\b{number}\b", lines[old])):
        # dropped, or another pull request: remove it with its comment
        start = old
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        del lines[start : old + 1]
        old = None
    i = find("base_commit")
    if i is not None:
        del lines[i]
    if number is None:
        return "\n".join(lines) + "\n"
    if old is None:
        at = find("commit")
        assert at is not None
        new = [*comment(int(number)), render("pull_request", int(number))]
        lines[at + 1 : at + 1] = new
        old = at + len(new)
    lines.insert(old + 1, render("base_commit", base))
    return "\n".join(lines) + "\n"


def _toml_line(key: str, value: Any) -> str:
    return f"{key} = {value}" if isinstance(value, int) else f'{key} = "{value}"'


def _py_line(key: str, value: Any) -> str:
    return f'    "{key}": {value},' if isinstance(value, int) else f'    "{key}": "{value}",'


def write_pin(
    commit: str,
    version: str,
    branch: str,
    pull_request: int | None = None,
    base_commit: str | None = None,
) -> None:
    """Write the pin to parity/UPSTREAM.toml and pytacheck._version.UPSTREAM.

    Both always get the same entries: ``pull_request`` and ``base_commit``
    together (a tracked pull request), or neither.
    """
    if (pull_request is None) != (base_commit is None):
        raise ValueError("pull_request and base_commit go together")
    values = {
        "branch": branch,
        "commit": commit,
        "version": version,
        "pull_request": pull_request,
        "base_commit": base_commit,
    }
    UPSTREAM_TOML.write_text(
        _edit_pin(
            UPSTREAM_TOML.read_text(encoding="utf-8"),
            values,
            lambda key: re.compile(rf"^{key}\s*="),
            _toml_line,
            lambda n: [
                f"# {branch} plus pull request #{n}, which pytacheck targets ahead of its",
                f"# merge; the upstream-sync workflow moves back to {branch} once it is merged.",
            ],
        ),
        encoding="utf-8",
    )
    VERSION_PY.write_text(
        _edit_pin(
            VERSION_PY.read_text(encoding="utf-8"),
            values,
            lambda key: re.compile(rf'^\s*"{key}"\s*:'),
            _py_line,
            lambda n: [f"    # {branch} plus scienceverse/metacheck#{n}, not yet merged"],
        ),
        encoding="utf-8",
    )


# --- metacheck sources ------------------------------------------------------------


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
    return {r_file: list(dict.fromkeys(py)) for r_file, py in targets.items()}


# --- commands ------------------------------------------------------------------------


def cmd_status(ns: argparse.Namespace) -> int:
    pin = pinned()
    p = plan(pin, ns.to, ns.drop_pr)
    behind = git("rev-list", "--count", f"{pin['commit']}..{p['target']}").strip()
    print(
        json.dumps(
            {
                "pinned": pin["commit"],
                "head": p["target"],
                "behind": int(behind),
                "sync": needs_sync(pin, p),
                "mode": p["mode"],
                "branch_head": p["branch_head"],
                "pull_request": pin.get("pull_request"),
                "pr_head": p["pr_head"],
                "pr_merged": p["pr_merged"],
                "not_in_pr": len(p["not_in_pr"]),
            }
        )
    )
    return 0


def _tracking(pin: dict[str, Any], p: dict[str, Any], old: str, new: str) -> list[str]:
    """The brief's section on what is tracked (and what the pinned tree lacks)."""
    branch, number = p["branch"], pin.get("pull_request")
    if p["mode"] == "branch":
        return [f"pytacheck tracks metacheck `{branch}` ({new[:10]})."]
    if p["mode"] == "merged":
        return [
            f"Pull request #{number} was merged into `{branch}` ({p['pr_merged']}), so "
            f"pytacheck tracks `{branch}` again: the pin moves to `{branch}` ({new[:10]}) and "
            "drops `pull_request` and `base_commit`. The diff below is the tree diff from the "
            f"pull request's head ({old[:10]}), so it holds the `{branch}` commits the pull "
            "request lacked (and any change made while merging), not the pull request's own "
            "changes, which are already ported.",
        ]
    out = [
        f"pytacheck tracks metacheck `{branch}` plus pull request #{number}, which is not "
        f"merged yet: the pin moves to the pull request's head ({new[:10]}), built on "
        f"`{branch}` at {p['base_commit'][:10]}.",
    ]
    if p["not_in_pr"]:
        shown = p["not_in_pr"][:MAX_LISTED_COMMITS]
        more = len(p["not_in_pr"]) - len(shown)
        out += [
            "",
            f"### `{branch}` commits not in pull request #{number}",
            "",
            f"`{branch}` has {len(p['not_in_pr'])} commit(s) that the pull request does not "
            "contain. They are not in the pinned reference, so the goldens do not show them: "
            "**do not port them in this sync**. List them under a warning in "
            f"`.upstream-sync/notes.md`; they arrive once #{number} is merged or rebased.",
            "",
            *(f"- {line}" for line in shown),
            *([f"- ... and {more} more"] if more > 0 else []),
        ]
    return out


def _meta(pin: dict[str, Any], p: dict[str, Any], old: str, new: str, version: str) -> dict:
    branch, number = p["branch"], pin.get("pull_request")
    if p["mode"] == "pull_request":
        title = f"Sync with metacheck {branch} + #{number} {new[:10]} ({version})"
        summary = (
            f"Automated port of metacheck `{branch}` + pull request #{number} "
            f"→ `{new[:10]}` (metacheck {version}; not merged yet)."
        )
    elif p["mode"] == "merged":
        title = f"Sync with metacheck {branch} {new[:10]} ({version}; #{number} merged)"
        summary = (
            f"Automated port of metacheck `{branch}` → `{new[:10]}` (metacheck {version}). "
            f"Pull request #{number} was merged ({p['pr_merged']}): the pin tracks "
            f"`{branch}` again."
        )
    else:
        title = f"Sync with metacheck {branch} {new[:10]} ({version})"
        summary = f"Automated port of metacheck `{branch}` → `{new[:10]}` (metacheck {version})."
    warning = ""
    if p["not_in_pr"]:
        warning = (
            f"> [!WARNING]\n> `{branch}` has {len(p['not_in_pr'])} commit(s) that pull request "
            f"#{number} does not contain; they are not ported yet:\n"
            + "".join(f">\n> - {line}" for line in p["not_in_pr"][:50])
        )
    return {
        "from": old,
        "to": new,
        "version": version,
        "mode": p["mode"],
        "branch": branch,
        "pull_request": p["pull_request"],
        "base_commit": p["base_commit"],
        "pr_merged": p["pr_merged"],
        "not_in_pr": p["not_in_pr"],
        "title": title,
        "summary": summary,
        "warning": warning,
    }


def cmd_prepare(ns: argparse.Namespace) -> int:
    pin = pinned()
    old = pin["commit"]
    p = plan(pin, ns.to, ns.drop_pr)
    new = p["target"]
    if not needs_sync(pin, p):
        print("already at", old)
        return 0
    git("checkout", "--quiet", new)
    version = description_version(new)
    write_pin(new, version, p["branch"], p["pull_request"], p["base_commit"])

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
        "## Tracked upstream",
        "",
        *_tracking(pin, p, old, new),
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
    meta = _meta(pin, p, old, new, version)
    (BRIEF_DIR / "meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(
        json.dumps(
            {
                "from": old,
                "to": new,
                "version": version,
                "mode": p["mode"],
                "files": len(changed),
                "not_in_pr": len(p["not_in_pr"]),
            }
        )
    )
    return 0


# --- parity and accuracy in the brief; what needs human review -------------------


def _json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _cases_table(rows: list[dict[str, Any]], limit: int = 80) -> list[str]:
    out = ["| case | tier | mark | first problem |", "|---|---|---|---|"]
    for r in rows[:limit]:
        mark = " ".join(x for x in (r.get("kind"), r.get("ref")) if x) or "-"
        first = (r.get("problems") or [""])[0].replace("|", "\\|").replace("\n", " ")[:200]
        out.append(f"| `{r['case']}` | {r['tier']} | {mark} | {first} |")
    if len(rows) > limit:
        out.append(f"| ... and {len(rows) - limit} more | | | |")
    return out


def parity_brief(check: list[dict[str, Any]], accuracy: dict[str, Any] | None) -> list[str]:
    """The brief's sections on parity and accuracy with the new goldens, before porting."""
    counts: dict[str, int] = {}
    for r in check:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    out = [
        "## Parity after regeneration (before porting)",
        "",
        f"{len(check)} cases: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())),
        "",
    ]
    sections = (
        ("r_changed", "Marked cases whose R golden changed: check that each mark still holds"),
        ("xpass", "Marked cases that now match R: upstream may have fixed the bug, drop the mark"),
    )
    for status, title in sections:
        rows = [r for r in check if r["status"] == status]
        if rows:
            out += [f"### {title} ({len(rows)})", "", *_cases_table(rows), ""]
    failing = [r for r in check if r.get("failing") and r["status"] not in ("r_changed", "xpass")]
    if failing:
        out += [f"### Failing cases ({len(failing)})", "", *_cases_table(failing), ""]
    if accuracy is not None:
        out += ["## Accuracy before porting (new metacheck, old pytacheck)", ""]
        unexplained = [d for d in accuracy["differences"] if d["explained_by"] is None]
        out.append(
            f"{accuracy['outputs']} outputs, {len(accuracy['differences'])} differences, "
            f"{len(unexplained)} not explained by parity/accuracy/expected.yaml; stale "
            f"entries: {len(accuracy['stale'])}."
        )
        out += [""] + [f"- warning: {w}" for w in accuracy["warnings"]]
        if unexplained:
            out += ["", "| module | input | field | level | detail |", "|---|---|---|---|---|"]
            for d in unexplained[:150]:
                detail = d["detail"].replace("|", "\\|").replace("\n", " ")[:200]
                out.append(
                    f"| {d['module']} | `{d['input']}` | {d['field']} | {d['level']} | {detail} |"
                )
        out += [f"- stale: {e}" for e in accuracy["stale"]]
        out.append("")
    return out


def cmd_brief_parity(ns: argparse.Namespace) -> int:
    accuracy = _json(ns.accuracy) if ns.accuracy and Path(ns.accuracy).exists() else None
    if Path(ns.check).exists():
        lines = parity_brief(_json(ns.check), accuracy)
    else:
        lines = ["## Parity after regeneration (before porting)", "", "`parity check` failed."]
    brief = BRIEF_DIR / "brief.md"
    with brief.open("a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(lines) + "\n")
    return 0


def tier1_marks() -> dict[str, Any]:
    """The marks of the tier-1 (realistic) parity cases, by case key."""
    sys.path.insert(0, str(ROOT))
    from parity.cases import load_cases

    return {
        c.key: c.spec["known_divergence"]
        for c in load_cases()
        if c.tier == 1 and c.spec.get("known_divergence")
    }


def cmd_tier1_marks(ns: argparse.Namespace) -> int:
    Path(ns.out).write_text(json.dumps(tier1_marks(), indent=1, sort_keys=True), encoding="utf-8")
    return 0


_D_ROW = re.compile(r"^[+-]\| D\d+ ")


def review_reasons(before: dict[str, Any], accuracy: dict[str, Any] | None) -> list[str]:
    """Why the pull request needs human review (empty: it does not)."""
    reasons = []
    after = tier1_marks()
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if changed:
        shown = ", ".join(changed[:20]) + (" ..." if len(changed) > 20 else "")
        reasons.append(
            f"{len(changed)} tier-1 (realistic) marks added, removed or changed: {shown}"
        )
    if git("status", "--porcelain", "--", "parity/accuracy/expected.yaml", cwd=ROOT).strip():
        reasons.append("parity/accuracy/expected.yaml changed")
    diff = git("diff", "HEAD", "--", "docs/UPSTREAM_ISSUES.md", cwd=ROOT)
    d_rows = [line for line in diff.splitlines() if _D_ROW.match(line)]
    if d_rows:
        reasons.append(f"D-entries of docs/UPSTREAM_ISSUES.md changed ({len(d_rows)} lines)")
    if accuracy is None:
        reasons.append("the accuracy report did not run")
    else:
        if not accuracy["passed"]:
            reasons.append("the accuracy gate fails")
        reasons += [f"accuracy: {w}" for w in accuracy["warnings"]]
    return reasons


def cmd_review(ns: argparse.Namespace) -> int:
    accuracy = _json(ns.accuracy) if Path(ns.accuracy).exists() else None
    reasons = review_reasons(_json(ns.marks), accuracy)
    print(json.dumps({"needs_review": bool(reasons), "reasons": reasons}, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, func in (("status", cmd_status), ("prepare", cmd_prepare)):
        p = sub.add_parser(name)
        p.add_argument(
            "--to",
            help="target commit (default: the pull request's head while it is open, "
            "else the head of the tracked branch)",
        )
        p.add_argument(
            "--drop-pr",
            action="store_true",
            help="stop tracking the pinned pull request (e.g. closed without merging): "
            "sync to the branch and drop pull_request/base_commit from the pin",
        )
        p.set_defaults(func=func)
    b = sub.add_parser("brief-parity", help="append parity and accuracy to the brief")
    b.add_argument("--check", required=True, help="the report of `python -m parity check`")
    b.add_argument("--accuracy", help="the report of `python -m parity accuracy`")
    b.set_defaults(func=cmd_brief_parity)
    t = sub.add_parser("tier1-marks", help="record the tier-1 cases' marks")
    t.add_argument("--out", required=True)
    t.set_defaults(func=cmd_tier1_marks)
    r = sub.add_parser("review", help="whether the pull request needs human review (JSON)")
    r.add_argument("--marks", required=True, help="the tier-1 marks recorded before porting")
    r.add_argument("--accuracy", required=True, help="the accuracy report after porting")
    r.set_defaults(func=cmd_review)
    ns = parser.parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":
    sys.exit(main())
