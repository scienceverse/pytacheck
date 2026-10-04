"""Compare two runs of ``scripts/llm_power_before_after.py``: what the change did to ``power``.

    uv run python scripts/llm_power_compare.py before.jsonl after.jsonl
    uv run python scripts/llm_power_compare.py before.jsonl before2.jsonl   # the noise floor

Prints Markdown: the two runs, how many papers kept their traffic light and their rows, how often
each extracted field agrees, and every difference (``--max-diffs`` of them). A model that answers
differently from one call to the next shows up here too, so compare two runs of the *same* code
first: a difference between before and after matters only when it is larger than that.

Rows are matched by the paragraph they came from (``text_id``) and their order within it. A
number agrees when it is within ``--tolerance`` of the other, a string when it is equal, a missing
value (``null``) only with another one. ``--strict`` makes the exit status 1 when anything
differs, which is for comparing a run with itself or with a recorded one.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

#: row keys that say where a row came from, not what the model answered
_ROW_ID = frozenset({"text_id", "text_head"})


def load(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The header and the paper records (by paper id) of one run's file."""
    header: dict[str, Any] = {}
    papers: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as f:
        for n, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}, line {n}: not JSON ({e})") from e
            if rec.get("kind") == "run":
                header = rec
            elif rec.get("kind") == "paper":
                papers[str(rec["paper_id"])] = rec
    if not header:
        raise SystemExit(f"{path}: no header line; is it a file from llm_power_before_after.py?")
    return header, papers


def same(a: Any, b: Any, tolerance: float) -> bool:
    """Whether two cell values agree: equal strings, numbers within a tolerance, both missing."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tolerance
    return bool(a == b)


def match_rows(
    before: list[dict[str, Any]], after: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair rows by (paragraph, order within it); the rest are only in one run."""

    def keyed(rows: list[dict[str, Any]]) -> dict[tuple[Any, int], dict[str, Any]]:
        seen: Counter[Any] = Counter()
        out = {}
        for row in rows:
            tid = row.get("text_id")
            out[(tid, seen[tid])] = row
            seen[tid] += 1
        return out

    kb, ka = keyed(before), keyed(after)
    pairs = [(kb[k], ka[k]) for k in kb if k in ka]
    return pairs, [kb[k] for k in kb if k not in ka], [ka[k] for k in ka if k not in kb]


def compare(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]], tolerance: float
) -> dict[str, Any]:
    """The differences between two runs, as plain data."""
    out: dict[str, Any] = {
        "only_before": sorted(set(before) - set(after)),
        "only_after": sorted(set(after) - set(before)),
        "papers": [],
        "fields": {},
        "diffs": [],
    }
    fields: dict[str, list[int]] = {}  # field -> [compared, same]
    for pid in sorted(set(before) & set(after)):
        b, a = before[pid], after[pid]
        pairs, lost, gained = match_rows(b.get("rows", []), a.get("rows", []))
        differing: set[str] = set()
        identical = 0
        for rb, ra in pairs:
            row_same = True
            for col in sorted((set(rb) | set(ra)) - _ROW_ID):
                x, y = rb.get(col), ra.get(col)
                f = fields.setdefault(col, [0, 0])
                f[0] += 1
                if same(x, y, tolerance):
                    f[1] += 1
                else:
                    row_same = False
                    differing.add(col)
                    out["diffs"].append(
                        {
                            "paper_id": pid,
                            "text_id": rb.get("text_id"),
                            "field": col,
                            "before": x,
                            "after": y,
                        }
                    )
            identical += row_same
        for side, rows in (("before", lost), ("after", gained)):
            for row in rows:
                out["diffs"].append(
                    {
                        "paper_id": pid,
                        "text_id": row.get("text_id"),
                        "field": "(row)",
                        "before": "present" if side == "before" else None,
                        "after": "present" if side == "after" else None,
                    }
                )
        out["papers"].append(
            {
                "paper_id": pid,
                "light": (b.get("traffic_light"), a.get("traffic_light")),
                "path": (b.get("path"), a.get("path")),
                "rows": (len(b.get("rows", [])), len(a.get("rows", []))),
                "matched": len(pairs),
                "identical": identical,
                "lost": len(lost),
                "gained": len(gained),
                "fields": sorted(differing),
                "error": (b.get("error"), a.get("error")),
                "warnings": (len(b.get("warnings", [])), len(a.get("warnings", []))),
            }
        )
    out["fields"] = {k: tuple(v) for k, v in sorted(fields.items())}
    return out


def _cell(v: Any) -> str:
    text = "(missing)" if v is None else str(v)
    return text.replace("|", "\\|").replace("\n", " ")[:60]


def _run_line(name: str, header: dict[str, Any]) -> str:
    v = header.get("versions") or {}
    sdks = ", ".join(f"{k} {v[k]}" for k in ("openai", "anthropic", "google-genai") if v.get(k))
    code = header.get("commit", "")[:10] + (" (changes)" if header.get("dirty") else "")
    code = code.strip() or f"metacheck {v.get('metacheck')}"
    return (
        f"- **{name}** `{header.get('label')}`: {code}; model `{header.get('model')}`, "
        f"seed {header.get('seed')}, {header.get('papers')} papers, {header.get('started')}"
        + (f"; {sdks}" if sdks else "")
    )


def render(
    head_b: dict[str, Any], head_a: dict[str, Any], result: dict[str, Any], max_diffs: int
) -> str:
    papers = result["papers"]
    lines = ["## power with the LLM on: before and after", ""]
    lines += [_run_line("before", head_b), _run_line("after", head_a), ""]

    kept = sum(1 for p in papers if p["light"][0] == p["light"][1])
    same_path = sum(1 for p in papers if p["path"][0] == p["path"][1])
    rows_b = sum(p["rows"][0] for p in papers)
    rows_a = sum(p["rows"][1] for p in papers)
    matched = sum(p["matched"] for p in papers)
    identical = sum(p["identical"] for p in papers)
    failed = [sum(1 for p in papers if p["path"][side] == "failed") for side in (0, 1)]
    lines += [
        f"- papers in both runs: {len(papers)} "
        f"(only before: {len(result['only_before'])}, only after: {len(result['only_after'])})",
        f"- traffic light unchanged: {kept} of {len(papers)}",
        f"- same route (structured request or prompt fallback): {same_path} of {len(papers)}",
        f"- papers whose LLM check failed to run: {failed[0]} before, {failed[1]} after",
        f"- power analyses found: {rows_b} before, {rows_a} after; {matched} matched by paragraph, "
        f"{identical} of them with every value equal",
        "",
    ]

    if result["fields"]:
        lines += [
            "### Agreement by field (matched rows)",
            "",
            "| field | compared | equal | share |",
        ]
        lines += ["|---|---:|---:|---:|"]
        for col, (n, k) in result["fields"].items():
            lines.append(f"| {col} | {n} | {k} | {k / n:.0%} |")
        lines.append("")

    changed = [
        p
        for p in papers
        if p["light"][0] != p["light"][1]
        or p["path"][0] != p["path"][1]
        or p["fields"]
        or p["lost"]
        or p["gained"]
        or p["error"][0] != p["error"][1]
    ]
    if changed:
        lines += ["### Papers that differ", ""]
        lines += ["| paper | light | route | rows | differing fields |", "|---|---|---|---|---|"]
        for p in changed:
            lines.append(
                f"| {p['paper_id']} | {_cell(p['light'][0])} -> {_cell(p['light'][1])} "
                f"| {_cell(p['path'][0])} -> {_cell(p['path'][1])} "
                f"| {p['rows'][0]} -> {p['rows'][1]} | {', '.join(p['fields']) or '-'} |"
            )
        lines.append("")
    else:
        lines += ["No paper differs in traffic light, route or extracted values.", ""]

    if failed[0] or failed[1]:
        lines += [
            "A paper whose LLM check failed to run says nothing about the model: read its "
            "`warnings` in the run's file (a missing key, a refused request) before trusting "
            "the rest.",
            "",
        ]

    errors = [p for p in papers if p["error"][0] or p["error"][1]]
    if errors:
        lines += ["### Papers where the module stopped with an error", ""]
        for p in errors:
            lines.append(
                f"- {p['paper_id']}: before {_cell(p['error'][0])}; after {_cell(p['error'][1])}"
            )
        lines.append("")

    diffs = result["diffs"]
    if diffs:
        lines += [f"### Differences ({min(len(diffs), max_diffs)} of {len(diffs)})", ""]
        lines += ["| paper | paragraph | field | before | after |", "|---|---|---|---|---|"]
        for d in diffs[:max_diffs]:
            lines.append(
                f"| {d['paper_id']} | {_cell(d['text_id'])} | {d['field']} "
                f"| {_cell(d['before'])} | {_cell(d['after'])} |"
            )
        lines.append("")
    return "\n".join(lines)


def differs(result: dict[str, Any]) -> bool:
    """Whether anything at all differs between the two runs."""
    return bool(
        result["diffs"]
        or result["only_before"]
        or result["only_after"]
        or any(
            p["light"][0] != p["light"][1] or p["path"][0] != p["path"][1] for p in result["papers"]
        )
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n", maxsplit=1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("before", type=Path)
    p.add_argument("after", type=Path)
    p.add_argument("--max-diffs", type=int, default=60, help="differences to list (default 60)")
    p.add_argument("--tolerance", type=float, default=1e-9, help="numbers closer than this agree")
    p.add_argument("--strict", action="store_true", help="exit with status 1 if anything differs")
    args = p.parse_args(argv)

    head_b, before = load(args.before)
    head_a, after = load(args.after)
    result = compare(before, after, args.tolerance)
    sys.stdout.write(render(head_b, head_a, result, args.max_diffs) + "\n")
    return 1 if args.strict and differs(result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
