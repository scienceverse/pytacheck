"""Generate parity/cases/archives_repo35.yaml (metacheck 0.4.4.30 to 0.4.4.35, repository parts).

python tests/repo_download/make_repo35_cases.py
python -m parity generate --area archives_repo35

The R and Python halves of each case are written side by side as ``identity`` /
``tests.archives_services.parity_support.run`` cases, as in ``archives_services.yaml``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "archives_repo35.yaml"

DL = "__import__('importlib').import_module('metacheck.archives.download')"
IC = "__import__('importlib').import_module('metacheck.archives.info_cache')"
GH = "__import__('importlib').import_module('metacheck.archives.github')"


def case(cid: str, r: str, py: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": cid,
        "r": "identity",
        "py": "tests.archives_services.parity_support.run",
        "args": {"x": {"$expr": {"r": r, "py": f"lambda m: {py}"}}},
        **extra,
    }


REL = [
    "names/case/Data.csv",
    "names/case/data.csv",
    "names/case/DATA.CSV",
    "a.R",
    "Dir/x",
    "dir/x",
]
R_REL = "c(" + ", ".join(f'"{p}"' for p in REL) + ")"
PY_REL = repr(REL)

cases: list[dict[str, Any]] = [
    case(
        ".case_clash_rename.names",
        f'metacheck:::.case_clash_rename(rep("r", 6), {R_REL})',
        f'{DL}._case_clash_rename(["r"] * 6, {PY_REL})',
    ),
    case(
        ".case_clash_rename.listing",
        f'metacheck:::.case_clash_rename("r", "names/case/data.csv", data.frame(repo_url = "r", '
        f"file_path = {R_REL}, file_name = basename({R_REL})))",
        f"{DL}._case_clash_rename(['r'], ['names/case/data.csv'], "
        f"m.pd.DataFrame({{'repo_url': 'r', 'file_path': {PY_REL}, "
        f"'file_name': [p.split('/')[-1] for p in {PY_REL}]}}))",
    ),
    case(
        ".case_clash_rename.repos",
        'metacheck:::.case_clash_rename(c("r", "s"), c("a.csv", "A.csv"))',
        f"{DL}._case_clash_rename(['r', 's'], ['a.csv', 'A.csv'])",
    ),
    case(
        ".case_clash_rename.windows_separator",
        'metacheck:::.case_clash_rename(rep("r", 3), c("d\\\\X.txt", "d/x.txt", "d/y.txt"))',
        f"{DL}._case_clash_rename(['r'] * 3, ['d\\\\X.txt', 'd/x.txt', 'd/y.txt'])",
    ),
    case(
        ".case_clash_rename.empty",
        "metacheck:::.case_clash_rename(character(0), character(0))",
        f"{DL}._case_clash_rename([], [])",
    ),
    case(
        ".repo_links.broken",
        'metacheck:::.repo_links(c("links/to_small.csv", "links/broken.R", "top.R", "links/up.R"), '
        'all_paths = c("data/small.csv", "links", "data"), raw_urls = NA, '
        'targets = c("../data/small.csv", "missing.R", NA, "../../x.R"))',
        f"{GH}._repo_links(['links/to_small.csv', 'links/broken.R', 'top.R', 'links/up.R'], "
        "['data/small.csv', 'links', 'data'], [None] * 4, "
        "targets=['../data/small.csv', 'missing.R', None, '../../x.R'])",
    ),
    case(
        ".repo_links.absolute_and_dot",
        'metacheck:::.repo_links(c("a/l1", "a/l2", "l3"), all_paths = c("a", "a/l1", "b/c.R", "c.R"), '
        'raw_urls = NA, targets = c("./../b/c.R", "/etc/passwd", "c.R"))',
        f"{GH}._repo_links(['a/l1', 'a/l2', 'l3'], ['a', 'a/l1', 'b/c.R', 'c.R'], [None] * 3, "
        "targets=['./../b/c.R', '/etc/passwd', 'c.R'])",
    ),
]

# .repo_info_ok: a listing without a title, a DOI and any file is not kept (#711)
BLANK_R = 'blank <- data.frame(figshare_id = "1", title = NA_character_, doi = NA_character_); blank$files <- list(data.frame()); '
BLANK_PY = "m.pd.DataFrame({'figshare_id': ['1'], 'title': [None], 'doi': [None], 'files': [[]]})"
cases += [
    case(
        ".repo_info_ok.blank_record",
        f"(function() {{ {BLANK_R}metacheck:::.repo_info_ok(blank) }})()",
        f"{IC}._repo_info_ok({BLANK_PY})",
    ),
    case(
        ".repo_info_ok.record_with_title",
        f'(function() {{ {BLANK_R}blank$title <- "A data set"; metacheck:::.repo_info_ok(blank) }})()',
        f"{IC}._repo_info_ok({BLANK_PY}.assign(title='A data set'))",
    ),
    case(
        ".repo_info_ok.record_with_files",
        f'(function() {{ {BLANK_R}blank$files <- list(data.frame(name = "a.csv")); '
        "metacheck:::.repo_info_ok(blank) })()",
        f"{IC}._repo_info_ok({BLANK_PY}.assign(files=[m.pd.DataFrame({{'name': ['a.csv']}})]))",
    ),
    case(
        ".repo_info_ok.error_column",
        'metacheck:::.repo_info_ok(data.frame(title = "x", error = "unfound"))',
        f"{IC}._repo_info_ok(m.pd.DataFrame({{'title': ['x'], 'error': ['unfound']}}))",
    ),
    case(
        ".repo_info_ok.other_frame",
        "metacheck:::.repo_info_ok(data.frame(id = 1))",
        f"{IC}._repo_info_ok(m.pd.DataFrame({{'id': [1]}}))",
    ),
]

# aspredicted_links() also reads the links written in the text (#695, point 4)
cases += [
    {
        "id": "aspredicted_links.text_bare_and_marked",
        "r": "aspredicted_links",
        "py": "metacheck.archives.aspredicted.aspredicted_links",
        "args": {
            "paper": {
                "$test_paper": {
                    "text": [
                        "See aspredicted.org/abc_de and www.aspredicted.org/ABC_DE/ and more."
                    ],
                    "url": ["https://aspredicted.org/abc_de"],
                }
            }
        },
        "compare": {"ignore": ["paper_id"]},
    },
]

HEADER = (
    "# Parity cases for the repository parts of metacheck 0.4.4.30 to 0.4.4.35 (made by\n"
    "# tests/repo_download/make_repo35_cases.py): the case-clash names on disk and the symbolic\n"
    "# links of a GitHub listing (#700), a record that came back without its fields not being\n"
    "# cached (#711), and the AsPredicted links written in the text (#695).\n"
)

if __name__ == "__main__":
    OUT.write_text(
        HEADER + yaml.safe_dump({"area": "archives_repo35", "cases": cases}, width=100000)
    )
