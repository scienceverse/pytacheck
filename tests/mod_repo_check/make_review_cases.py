"""Generate parity/cases/mod_repo_check_review.yaml (then regenerate the goldens).

python tests/mod_repo_check/make_review_fixtures.py
python tests/mod_repo_check/make_review_cases.py
python -m parity generate --area mod_repo_check_review

Review cases for ``repo_check``: branches the main cases (``make_cases.py``)
do not reach. They run exactly like the main cases (``module_run()`` on
recorded responses, see ``make_cases.py``), plus direct checks of the
report's file-size labels against ``utils:::format.object_size()``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))

from tests.mod_repo_check.make_cases import (  # noqa: E402
    CODE_FILES,
    FIX,
    LOCAL_IGNORE,
    _Dumper,
    case,
    local,
    one,
    osf,
)

OUT = ROOT / "parity" / "cases" / "mod_repo_check_review.yaml"
REV = FIX + "review/"

SIZES = [0, 1, 999, 1000, 1049, 1050, 1150, 1250, 1350, 12345, 99950, 999949, 999950]
SIZES += [1e6, 1.25e6, 1.5e9, 123456789012, 1e15, 2.5e21, 1e27, 1e30, 3e33]

SIZE_R = (
    "vapply(c({}, NA, -1, Inf, NaN), function(x) {{ "
    'if (is.na(x) || !is.finite(x) || x < 0) return("\\u2014"); '
    'utils:::format.object_size(x, units = "auto", standard = "SI", digits = 1) }}, '
    "character(1))"
).format(", ".join(repr(float(s)) for s in SIZES))
SIZE_PY = (
    "[__import__('pytacheck.modules._repo_check', fromlist=['size_label']).size_label(x) "
    "for x in [{}, None, -1.0, float('inf'), float('nan')]]"
).format(", ".join(repr(float(s)) for s in SIZES))


CASES: list[tuple[str, str, dict[str, Any], Any]] = [
    # --- local folders --------------------------------------------------------------------
    (
        "local.sizes",
        "file sizes at SI unit boundaries and R round() halves",
        local(REV + "sizes"),
        LOCAL_IGNORE,
    ),
    (
        "local.sizes.tables",
        "report tables: the size labels",
        {**local(REV + "sizes"), "tables": True},
        None,
    ),
    (
        "local.edat_many",
        "ten E-Prime files, nine without a .txt export (plural, head(8))",
        local(REV + "edat_many"),
        LOCAL_IGNORE,
    ),
    (
        "local.edat_many.report",
        "module_report(): ten E-Prime files",
        {**local(REV + "edat_many"), "report": True},
        None,
    ),
    (
        "local.edat_one",
        "one E-Prime file without its export (singular)",
        {**local(REV + "edat_one"), "report": True},
        None,
    ),
    (
        "local.unknown_many",
        "twelve unclassifiable files (head(10))",
        local(REV + "unknown_many"),
        LOCAL_IGNORE,
    ),
    (
        "local.unknown_many.report",
        "module_report(): twelve unclassifiable files",
        {**local(REV + "unknown_many"), "report": True},
        None,
    ),
    (
        "local.suggest_only",
        "naming suggestions without naming problems",
        local(REV + "suggest_only"),
        LOCAL_IGNORE,
    ),
    (
        "local.suggest_only.report",
        "module_report(): naming suggestions only",
        {**local(REV + "suggest_only"), "report": True},
        None,
    ),
    (
        "local.readme_variants",
        "README spellings (Read_Me, read me, READ-ME)",
        local(REV + "readme_variants"),
        LOCAL_IGNORE,
    ),
    (
        "local.two_folders",
        "two folders with the same file names: R drops the second folder's copies",
        local([REV + "dup_a", REV + "dup_b"]),
        LOCAL_IGNORE,
    ),
    (
        "local.two_folders.report",
        "module_report(): two folders with the same file names",
        {**local([REV + "dup_a", REV + "dup_b"]), "report": True},
        None,
    ),
    (
        "local.single_file",
        "a local path that is one file",
        local(CODE_FILES + "/analysis.R"),
        LOCAL_IGNORE,
    ),
    (
        "local.trailing_slash",
        "a local folder given with a trailing slash",
        local(FIX + "tidy/"),
        LOCAL_IGNORE,
    ),
    (
        "local.study_readme",
        "READMEs in study folders are grouped, root README and LICENSE are not",
        local(REV + "study_readme", text=["Study 1 and Study 2 are reported here."]),
        LOCAL_IGNORE,
    ),
    (
        "local.only_docs",
        "only a root README and LICENSE: nothing to group",
        local(REV + "only_docs"),
        LOCAL_IGNORE,
    ),
    (
        "local.vendored_only",
        "a deposit that is only a vendored R package",
        local(REV + "vendored_only"),
        LOCAL_IGNORE,
    ),
    (
        "local.hidden",
        "hidden files and folders are not listed",
        local(REV + "hidden"),
        LOCAL_IGNORE,
    ),
    (
        "local.unicode",
        "non-ASCII file and folder names",
        local(REV + "unicode"),
        LOCAL_IGNORE,
    ),
    (
        "local.unicode.tables",
        "report tables: non-ASCII file and folder names",
        {**local(REV + "unicode"), "tables": True},
        None,
    ),
    (
        "local.several_folders.paperlist",
        "a paper list with several local folders",
        {
            "papers": [
                {"url": [], "id": "zeta"},
                {"url": [], "id": "Alpha"},
            ],
            "args": {"local_path": [REV + "suggest_only", REV + "edat_one"]},
        },
        LOCAL_IGNORE,
    ),
    # --- online repositories ----------------------------------------------------------------
    (
        "osf.paperlist.order",
        "paper ids that sort differently from their order (naming issues per paper)",
        {
            "papers": [
                {"url": osf("msfcn"), "id": "zeta"},
                {"url": osf("629bx"), "id": "Alpha"},
                {"url": osf("j3gcx", "629bx"), "id": "alpha"},
            ]
        },
        {"ignore": ["table.file_location"]},
    ),
    (
        "gated.mixed",
        "an OSF project beside gated GitHub/GitLab repositories and a private OSF project",
        one(
            [
                *osf("629bx"),
                "https://github.com/gzorg/bigrepo",
                "https://gitlab.com/gzorg/missing",
                "https://osf.io/privn",
            ],
            "p_gated",
        ),
        {},
    ),
    (
        "gated.mixed.tables",
        "report tables: an Error column with missing values",
        one(
            [*osf("629bx"), "https://github.com/gzorg/bigrepo"],
            "p_gated_tab",
            tables=True,
        ),
        None,
    ),
    (
        "zenodo.no_peek",
        "peek_zips = FALSE: archive rows stay, the non-zip archive is reported",
        one(["https://zenodo.org/records/5559001"], "p_nopeek_z", args={"peek_zips": False}),
        {},
    ),
    (
        "zenodo.no_peek.report",
        "module_report(): peek_zips = FALSE",
        one(
            ["https://zenodo.org/records/5559001"],
            "p_nopeek_zr",
            args={"peek_zips": False},
            report=True,
        ),
        None,
    ),
    (
        "figshare.share_link_and_article",
        "a private share link beside a Figshare article",
        one(
            [
                "https://figshare.com/s/5e01cc0cae4cf3e2e14f",
                "https://doi.org/10.6084/m9.figshare.18093368.v1",
            ],
            "p_fs_mix",
        ),
        {},
    ),
    (
        "osf_license.invalid",
        "osf_license = TRUE with an invalid OSF id beside a project",
        one([*osf("629bx"), "https://osf.io/abc"], "p_lic_bad", args={"osf_license": True}),
        {},
    ),
    (
        "osf.local.paperlist",
        "a paper list with OSF links and a local folder (the folder belongs to the first paper)",
        {
            "papers": [
                {"url": osf("629bx"), "id": "p_first"},
                {"url": osf("629bx"), "id": "p_second"},
            ],
            "args": {"local_path": REV + "suggest_only"},
        },
        LOCAL_IGNORE,
    ),
]


def size_case() -> dict[str, Any]:
    return {
        "id": "repo_check.size_labels",
        "note": "the report's file-size labels: utils:::format.object_size(SI, digits = 1)",
        "r": "identity",
        "py": "tests.mod_repo_check.parity_support.identity",
        "args": {"x": {"$expr": {"r": SIZE_R, "py": SIZE_PY}}},
    }


def main() -> None:
    cases = [size_case()]
    for cid, note, spec, ignore in CASES:
        c = case(cid, note, spec, ignore)
        cases.append(c)
    data = {"area": "mod_repo_check_review", "cases": cases}
    header = (
        "# Review parity cases for the repo_check module (inst/modules/repo_check.R).\n"
        "# Generated by tests/mod_repo_check/make_review_cases.py -- do not edit by hand.\n"
    )
    text = yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=10_000)
    OUT.write_text(header + text, encoding="utf-8")


if __name__ == "__main__":
    main()
