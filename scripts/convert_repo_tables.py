"""Convert metacheck's ``R/repo-tables.R`` into ``src/metacheck/archives/_repo_tables.py``.

metacheck writes the repository survey's tables (hosts and DOI prefixes of the DSpace,
Dataverse, EPrints, Invenio and CKAN installations it reads, #635 to #637) into
``R/repo-tables.R``, generated from ``data-raw/repo_tables/``. pytacheck reads the same
lists, so they are converted rather than copied by hand::

    uv run python scripts/convert_repo_tables.py            # write the module
    uv run python scripts/convert_repo_tables.py --check    # fail if it is out of date

The R file is parsed as data: every ``.name <- function() { c(...) }`` or ``list(...)`` body
is made of string literals (and ``"host" = c("prefix", ...)`` pairs).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "upstream" / "metacheck" / "R" / "repo-tables.R"
TARGET = ROOT / "src" / "metacheck" / "archives" / "_repo_tables.py"

_FUNC = re.compile(r"^(\.[A-Za-z0-9_]+) <- function\(\) \{\n(.*?)\n\}\n", re.S | re.M)
_STR = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _body_without_comments(body: str) -> str:
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))


def _parse(source: str) -> dict[str, object]:
    tables: dict[str, object] = {}
    for m in _FUNC.finditer(source):
        name, body = m.group(1), _body_without_comments(m.group(2)).strip()
        if body.startswith("list("):
            pairs: dict[str, list[str]] = {}
            for pm in re.finditer(r'"([^"]+)"\s*=\s*c\(([^)]*)\)', body):
                pairs[pm.group(1)] = _STR.findall(pm.group(2))
            tables[name] = pairs
        else:
            tables[name] = _STR.findall(body)
    return tables


def render(source: str) -> str:
    tables = _parse(source)
    out = [
        '"""The repository survey\'s host and DOI-prefix tables (generated; do not edit).',
        "",
        "Converted from metacheck's ``R/repo-tables.R`` by ``scripts/convert_repo_tables.py``; change",
        "the pin and run the script again. Each name is the R function it comes from, without its dot.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]
    for name, value in tables.items():
        py = name.lstrip(".")
        if isinstance(value, dict):
            out.append(f"{py}: dict[str, tuple[str, ...]] = {{")
            for host, pre in value.items():
                items = ", ".join(json.dumps(x) for x in pre) + ("," if len(pre) == 1 else "")
                out.append(f"    {json.dumps(host)}: ({items}),")
            out.append("}")
        else:
            assert isinstance(value, list)
            out.append(f"{py}: tuple[str, ...] = (")
            out.extend(f"    {json.dumps(v)}," for v in value)
            out.append(")")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _formatted(text: str) -> str:
    """The module as ruff formats it, so a regenerated file has no diff to the committed one."""
    run = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", str(TARGET), "-"],
        input=text,
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    )
    return run.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    text = _formatted(render(SOURCE.read_text(encoding="utf-8")))
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != text:
            print(f"{TARGET.relative_to(ROOT)} is out of date; run scripts/convert_repo_tables.py")
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
