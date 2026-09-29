"""Generate parity/cases/mod_repo_check.yaml (then regenerate the goldens).

python tests/mod_repo_check/make_cases.py
python -m parity generate --area mod_repo_check

Every case runs ``module_run(<paper>, "repo_check", ...)``. Network cases run
on recorded responses: this area's mocks (``tests/mod_repo_check/mocks``,
see ``make_mocks.py``) and metacheck's own recordings, with metacheck's
``page[size]`` redactor and ``online()`` mocked to ``TRUE`` (R: httptest2 and
``testthat::with_mocked_bindings()``; Python:
``tests/mod_repo_check/parity_support.py``). Local-folder cases use
``tests/mod_repo_check/fixtures`` (see ``make_fixtures.py``) and metacheck's
``fixtures/code_files``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "mod_repo_check.yaml"

APIS = "upstream/metacheck/tests/testthat/apis"
MOCKS = "tests/mod_repo_check/mocks"
FIX = "tests/mod_repo_check/fixtures/"
CODE_FILES = "upstream/metacheck/tests/testthat/fixtures/code_files"
PSY = "upstream/metacheck/tests/testthat/fixtures/psychsci/"
PSYCHSCI = [
    PSY + f for f in ("0956797613520608.json", "0956797614522816.json", "0956797614527830.json")
]
DEMO_JSON = "upstream/metacheck/inst/demos/to_err_is_human.json"
BIBR12 = "upstream/metacheck/tests/testthat/fixtures/bibr12/"

# the file_location column is never compared in these four cases: R raises an error
# (the nameless ones), or the row count differs (U121) and the comparison stops there
NO_TABLE_COMPARE = ["table.file_location"]


def rstr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def rvec(xs: list[str]) -> str:
    return "c(" + ", ".join(rstr(x) for x in xs) + ")" if xs else "character(0)"


def rval(x: Any) -> str:
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, str):
        return rstr(x)
    if isinstance(x, list):
        return rvec(x)
    if x is None:
        return "NULL"
    return repr(x)


def r_paper(spec: dict[str, Any]) -> str:
    parts = []
    if spec.get("read"):
        parts.append(f"read({rvec(spec['read'])})")
    for p in spec.get("papers", []):
        text = f", {rvec(p['text'])}" if p.get("text") else ""
        parts.append(f"tp({rvec(p.get('url', []))}, {rstr(p['id'])}{text})")
    if spec.get("paperlist") or len(parts) > 1:
        return "paperlist(" + ", ".join(parts) + ")"
    return parts[0]


def r_mock_dir(m: str) -> str:
    if m == "local":
        return MOCKS
    if m.startswith("tests/"):
        return m
    return f"upstream/metacheck/tests/testthat/{m}"


def r_expr(spec: dict[str, Any]) -> str:
    args = "".join(f", {k} = {rval(v)}" for k, v in (spec.get("args") or {}).items())
    run = f'module_run({r_paper(spec)}, "repo_check"{args})'
    if spec.get("tables"):
        run = (
            "(function(mo) { rep <- unlist(mo$report); "
            'm <- unlist(regmatches(rep, gregexpr("(?s)(?<=\\ntable <- ).*?(?=\\n\\n# display table)", '
            "rep, perl = TRUE))); "
            "lapply(m, function(s) as.data.frame(eval(parse(text = s)))) })"
            f"({run})"
        )
    elif spec.get("report"):
        run = (
            r'gsub("(?s)\n```\\{r\\}.*?\n```\n", "\n<R-CHUNK>\n", '
            f"module_report({run}), perl = TRUE)"
        )
    dirs = rvec([r_mock_dir(m) for m in (spec.get("mocks") or ["local", "apis"])])
    return (
        "(function() { .r <- httptest2::get_current_redactor(); "
        "httptest2::set_redactor(function(req) { req$url <- "
        'gsub("[?&]page(%5[Bb]size%5[Dd]|\\\\[size\\\\])=100", "", req$url); req }); '
        'Sys.setenv(TESTTHAT = "true"); '
        'on.exit({ httptest2::set_redactor(.r); Sys.unsetenv("TESTTHAT") }); '
        "tp <- function(url, id, text = LETTERS) { p <- test_paper(text, url); "
        "p$paper_id <- id; p }; "
        f"httptest2:::with_mock_path({dirs}, httptest2::with_mock_api("
        f"testthat::with_mocked_bindings({run}, online = function(...) TRUE, "
        '.package = "metacheck")), replace = TRUE) })()'
    )


def one(url: list[str] | str, pid: str, text: list[str] | None = None, **kw: Any) -> dict[str, Any]:
    p: dict[str, Any] = {"url": url if isinstance(url, list) else [url], "id": pid}
    if text:
        p["text"] = text
    return {"papers": [p], **kw}


def osf(*ids: str) -> list[str]:
    return [f"https://osf.io/{i}" for i in ids]


def local(
    path: str | list[str],
    pid: str = "p_local",
    url: list[str] | None = None,
    text: list[str] | None = None,
    **args: Any,
) -> dict[str, Any]:
    return {**one(url or [], pid, text), "args": {"local_path": path, **args}}


STUDIES = [
    "We ran three studies.",
    "In Study 1 participants rated faces.",
    "Study 2 replicated Study 1 online.",
    "Study 4 is reported in the supplement.",
]

CASES: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = [
    # --- traffic light "na": no repositories -----------------------------------------
    ("no_links", "no repository links at all", one([], "p_none", ["No repos"]), {}),
    ("no_links.paperlist", "a paper list without repository links", {"read": PSYCHSCI}, {}),
    (
        "local_only.no_path",
        "local_only = TRUE and no local_path: online links are skipped",
        one(osf("629bx"), "p_lo", args={"local_only": True}),
        {},
    ),
    (
        "local_only.many_types",
        "local_only = TRUE skips OSF, GitHub and ResearchBox links",
        one(
            ["osf.io/629bx", "github.com/scienceverse/demo", "https://researchbox.org/4377"],
            "p_lo2",
            args={"local_only": True},
        ),
        {},
    ),
    ("osf.not_linkable", "an OSF user page is not a repository", one(osf("4i578"), "p_user"), {}),
    ("osf.not_found", "an OSF guid that does not exist (404)", one(osf("xxxxx"), "p_404"), {}),
    ("osf.preprint", "an OSF preprint is not a repository", one(osf("xp5cy"), "p_pre"), {}),
    # --- local folders ------------------------------------------------------------------
    ("local.code_files", "metacheck's code_files fixture: green", local(CODE_FILES), {}),
    (
        "local.vector",
        "a vector of local file paths",
        local([f"{CODE_FILES}/analysis.R", f"{CODE_FILES}/README.md"]),
        {},
    ),
    ("local.tidy", "README, data and code: green", local(FIX + "tidy"), {}),
    (
        "local.messy",
        "spaces, special characters, unclassifiable files, E-Prime binaries, a .tar.gz, "
        "study folders and a manuscript naming other studies",
        local(FIX + "messy", text=STUDIES),
        {},
    ),
    (
        "local.roster_extra",
        "the repository separates out a study the manuscript does not name",
        local(
            FIX + "messy",
            "p_extra",
            text=["In Study 1 participants rated faces.", "Study 2 replicated it."],
        ),
        {},
    ),
    (
        "local.roster_missing",
        "the manuscript names studies with no matching files",
        local(
            FIX + "tidy",
            "p_missing",
            text=["Study 1 and Study 2 are reported here.", "Study 3 is in the supplement."],
        ),
        {},
    ),
    (
        "local.roster_missing_one",
        "the manuscript names one study with no matching files",
        local(FIX + "tidy", "p_missing1", text=["Study 1 and Study 2 are reported here."]),
        {},
    ),
    (
        "local.edat_ok",
        "E-Prime files with their .txt exports",
        local(FIX + "edat_ok"),
        {},
    ),
    ("local.zip", "local archives are not peeked (no URL)", local(FIX + "zip_local"), {}),
    ("local.rpkg", "an R package tree is excluded", local(FIX + "rpkg"), {}),
    (
        "local.desc_only",
        "DESCRIPTION without NAMESPACE is kept",
        local(FIX + "desc_only"),
        {},
    ),
    (
        "local.rpkg_root",
        "a root package that is the deposit is kept",
        local(FIX + "rpkg_root"),
        {},
    ),
    ("local.vendored", "a vendored package is excluded", local(FIX + "vendored"), {}),
    (
        "local.missing_path",
        "a local path that does not exist: an empty local repository",
        local(FIX + "does_not_exist"),
        {},
    ),
    (
        "local.paperlist",
        "a paper list with a local path: the local folder belongs to the first paper",
        {
            "papers": [{"url": [], "id": "p_a"}, {"url": [], "id": "p_b"}],
            "args": {"local_path": FIX + "tidy"},
        },
        {},
    ),
    # --- OSF (metacheck's recordings) ------------------------------------------------------
    ("osf.629bx", "OSF project: code, data and a zip, no README", one(osf("629bx"), "p_629bx"), {}),
    (
        "osf.no_files",
        "OSF project without files: an empty repository",
        one(osf("y6a34"), "p_y6a34"),
        {},
    ),
    ("osf.readme_zip", "README and code.zip", one(osf("m4nbv"), "p_m4nbv"), {}),
    (
        "osf.view_only",
        "an OSF view-only link",
        one(["https://osf.io/t9j8e/? view_only=f171281f212f4435917b16a9e581a73b"], "p_vo"),
        {},
    ),
    ("osf.green", "an OSF project with READMEs: green", one(osf("j3gcx"), "p_j3gcx"), {}),
    ("osf.large", "a large OSF project", one(osf("pngda"), "p_pngda"), {}),
    ("osf.components", "an OSF project with components", one(osf("mc45x"), "p_mc45x"), {}),
    ("osf.mjrpy", "OSF project mjrpy", one(osf("mjrpy"), "p_mjrpy"), {}),
    ("osf.msfcn", "OSF project with a naming problem", one(osf("msfcn"), "p_msfcn"), {}),
    ("osf.yt32c", "OSF data project", one(osf("yt32c"), "p_yt32c"), {}),
    ("osf.3cz2e", "OSF project without files", one(osf("3cz2e"), "p_3cz2e"), {}),
    ("osf.not_public", "an OSF node that is not public", one(osf("ybm3c"), "p_ybm3c"), {}),
    ("osf.file_guid", "a link to one OSF file", one(osf("75qgk"), "p_75qgk"), {}),
    ("osf.file_guid2", "a link to another OSF file", one(osf("k6gbt"), "p_k6gbt"), {}),
    ("osf.component", "an OSF data component", one(osf("6nt4v"), "p_6nt4v"), {}),
    (
        "osf.registration",
        "an OSF registration with its source project",
        one(osf("8c3kb"), "p_8c3kb"),
        {},
    ),
    (
        "osf.registration2",
        "an OSF registration whose rows are all removed",
        one(osf("jqkg7"), "p_jqkg7"),
        {},
    ),
    ("osf.invalid_id", "an invalid OSF id", one(osf("abc"), "p_abc"), {}),
    (
        "osf_github",
        "OSF and GitHub (the testthat case)",
        one(["osf.io/629bx", "github.com/scienceverse/demo"], "p_og"),
        {},
    ),
    (
        "osf.badge_excluded",
        "PsychScience badge links (tvyxz) are not repositories",
        one(["https://osf.io/tvyxz/wiki/home/"], "p_badge"),
        {},
    ),
    (
        "osf.local",
        "an OSF project and a local folder",
        local(CODE_FILES, "p_osf_local", url=osf("629bx")),
        {},
    ),
    (
        "osf.local_only",
        "local_only = TRUE with an OSF link and a local folder",
        local(CODE_FILES, "p_osf_lo", url=osf("629bx"), local_only=True),
        {},
    ),
    (
        "osf.no_peek",
        "peek_zips = FALSE",
        one(osf("629bx"), "p_nopeek", args={"peek_zips": False}),
        {},
    ),
    (
        "osf.paperlist",
        "a paper list: two papers share an OSF project, one has none",
        {
            "papers": [
                {"url": osf("629bx"), "id": "paper_a"},
                {"url": osf("629bx", "j3gcx"), "id": "paper_b"},
                {"url": [], "id": "paper_c"},
            ]
        },
        {},
    ),
    ("demo", "the demo paper (OSF and ResearchBox links)", {"read": [DEMO_JSON]}, {}),
    (
        "psychsci_demo",
        "the psychsci fixtures and the demo paper",
        {"read": [*PSYCHSCI, DEMO_JSON]},
        {},
    ),
    # --- bibr export schema 12.0 papers (read natively) ------------------------------------
    (
        "bibr12.preprint",
        "a bibr 12.0 paper with an OSF project",
        {"read": [BIBR12 + "preprint.json"]},
        {},
    ),
    (
        "bibr12.paperlist",
        "bibr 12.0 papers: an OSF project, an invalid OSF id, no links; and a local folder",
        {
            "read": [BIBR12 + f for f in ("preprint.json", "probe_docx.json", "full.json")],
            "args": {"local_path": FIX + "tidy"},
        },
        {},
    ),
    # --- Zenodo (metacheck's recordings) -----------------------------------------------------
    (
        "zenodo.zip",
        "a Zenodo record with one zip",
        one("https://zenodo.org/records/17754445", "p_zen"),
        {},
    ),
    (
        "zenodo.paperlist",
        "two papers, one Zenodo record not found",
        {
            "papers": [
                {"url": ["https://zenodo.org/records/17754445"], "id": "p_z1"},
                {"url": ["https://zenodo.org/records/123456789"], "id": "p_z2"},
            ]
        },
        {},
    ),
    # --- GitHub ------------------------------------------------------------------------------
    (
        "github.demo",
        "a GitHub repository listed by the contents API",
        one("github.com/scienceverse/demo", "p_gh"),
        {},
    ),
    (
        "github.invalid",
        "a GitHub repository that does not exist",
        one("https://github.com/scienceverse/norepo", "p_gh404"),
        {},
    ),
    # --- the report ------------------------------------------------------------------------------
    (
        "report.osf_github",
        "module_report(): OSF and GitHub",
        one(["osf.io/629bx", "github.com/scienceverse/demo"], "p_rep", report=True),
        {},
    ),
    (
        "report.messy",
        "module_report(): the messy local folder",
        local(
            FIX + "messy",
            "p_rep2",
            text=STUDIES,
        )
        | {"report": True},
        {},
    ),
    (
        "tables.osf_github",
        "report tables: OSF and GitHub",
        one(["osf.io/629bx", "github.com/scienceverse/demo"], "p_tab", tables=True),
        {},
    ),
    (
        "tables.messy",
        "report tables: the messy local folder",
        local(FIX + "messy", "p_tab2", text=STUDIES) | {"tables": True},
        {},
    ),
]


# --- platforms without metacheck recordings (tests/mod_repo_check/mocks) ----------------
DV = "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/"
PLATFORM: list[tuple[str, str, list[str]]] = [
    (
        "github.tree",
        "GitHub git trees API: README, licence, data, code, a .tar.gz",
        ["https://github.com/gzorg/treerepo"],
    ),
    (
        "github.truncated",
        "a truncated GitHub tree is not listed (gated)",
        ["https://github.com/gzorg/bigrepo"],
    ),
    ("github.empty", "an empty GitHub repository", ["https://github.com/gzorg/emptyrepo"]),
    ("gitlab.project", "a GitLab project in a subgroup", ["https://gitlab.com/gzorg/sub/glproj"]),
    ("gitlab.big", "a GitLab project with a paginated tree", ["https://gitlab.com/gzorg/bigproj"]),
    (
        "gitlab.missing",
        "a GitLab project that does not exist (gated)",
        ["https://gitlab.com/gzorg/missing"],
    ),
    (
        "zenodo.records",
        "two Zenodo records",
        ["https://zenodo.org/records/5550001", "https://zenodo.org/records/5550005"],
    ),
    (
        "zenodo.peek",
        "zip peeking: a listable zip, a non-zip .zip, a .tar.gz, an E-Prime file, a nameless file",
        ["https://zenodo.org/records/5559001"],
    ),
    ("dataverse", "a Dataverse dataset and one that does not exist", [DV + "ABC123", DV + "NOPE"]),
    (
        "figshare",
        "a Figshare article and project",
        [
            "https://doi.org/10.6084/m9.figshare.18093368.v1",
            "https://figshare.com/projects/PERICLES_-_Heritage_values/133332",
        ],
    ),
    (
        "figshare.share_link",
        "a private Figshare share link",
        ["https://figshare.com/s/5e01cc0cae4cf3e2e14f"],
    ),
    (
        "dryad",
        "a Dryad dataset and one that does not exist",
        ["https://doi.org/10.5061/dryad.j1fd7", "https://doi.org/10.5061/dryad.missing"],
    ),
    (
        "reshare",
        "ReShare deposits",
        ["https://doi.org/10.5255/UKDA-SN-854001", "https://doi.org/10.5255/UKDA-SN-854100"],
    ),
    ("researchdata4tu", "a 4TU.ResearchData dataset", ["https://doi.org/10.4121/16766929.v1"]),
    ("mendeley", "a Mendeley Data dataset", ["https://data.mendeley.com/datasets/vjtxybrc28"]),
    (
        "dataone",
        "a DataONE (KNB) dataset",
        ["https://knb.ecoinformatics.org/view/doi:10.5063/F1TIDY"],
    ),
    (
        "dataone.nameless",
        "a DataONE file without a name: the module fails (as in R)",
        ["https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86"],
    ),
    (
        "zenodo.nameless",
        "a Zenodo file without a name: the module fails (as in R)",
        ["https://zenodo.org/records/5559002"],
    ),
    (
        "dspace7",
        "a DSpace 7 item",
        ["https://repository.gatech.edu/entities/publication/bac086e5-c606-474b-af1e-4a6122694af5"],
    ),
    (
        "dspace.psycharchives",
        "a PsychArchives item (metacheck's recordings)",
        ["https://www.psycharchives.org/handle/20.500.12034/17526"],
    ),
    (
        "dspace.bonndoc",
        "a legacy DSpace item without public files",
        ["https://bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/1234"],
    ),
    (
        "dspace.restricted",
        "a legacy DSpace item with restricted access",
        ["https://bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/5678"],
    ),
    (
        "researchbox",
        "a ResearchBox box (downloaded and unzipped)",
        ["https://researchbox.org/4377"],
    ),
    (
        "osf.reg_closed_mirror",
        "a registration whose source project is closed; files from the registration",
        ["https://osf.io/regc1"],
    ),
    (
        "osf.reg_closed",
        "a registration whose source project is closed; no files anywhere",
        ["https://osf.io/regc2"],
    ),
    (
        "osf.reg_closed_plural",
        "two closed source projects of each kind",
        [
            "https://osf.io/regc1",
            "https://osf.io/regc2",
            "https://osf.io/regc5",
            "https://osf.io/regc6",
        ],
    ),
    (
        "osf.reg_public_parent",
        "a registration whose public source project holds the files",
        ["https://osf.io/regp3"],
    ),
    (
        "osf.reg_parent_linked",
        "a registration and its source project, both linked",
        ["https://osf.io/regp3", "https://osf.io/parp3"],
    ),
    ("osf.reg_orphan", "a registration without a source project", ["https://osf.io/regor"]),
    ("osf.private", "a private OSF project", ["https://osf.io/privn"]),
]
ALL_PLATFORMS = list(
    dict.fromkeys(
        u
        for cid, _, urls in PLATFORM
        if not cid.endswith(".nameless") and cid != "osf.reg_closed_plural"
        for u in urls
    )
)
PLATFORM_URLS = [urls for _, _, urls in PLATFORM] + [ALL_PLATFORMS]

CASES += [
    (
        cid,
        note,
        one(urls, "p_" + cid.replace(".", "_")),
        NO_TABLE_COMPARE if cid.endswith(".nameless") else {},
    )
    for cid, note, urls in PLATFORM
]
CASES += [
    (
        "all_platforms",
        "every platform in one paper",
        one(ALL_PLATFORMS, "p_all"),
        NO_TABLE_COMPARE,
    ),
    (
        "all_platforms.paperlist",
        "every platform, spread over a paper list",
        {"papers": [{"url": ALL_PLATFORMS[i::3], "id": f"p_all{i}"} for i in range(3)]},
        NO_TABLE_COMPARE,
    ),
    (
        "all_platforms.report",
        "module_report(): every platform",
        one(ALL_PLATFORMS, "p_allrep", report=True),
        {},
    ),
    (
        "all_platforms.tables",
        "report tables: every platform",
        one(ALL_PLATFORMS, "p_alltab", tables=True),
        {},
    ),
    (
        "zenodo.same_record_twice",
        "one Zenodo record linked as a URL and a DOI: duplicate files are dropped",
        one(
            ["https://zenodo.org/records/5550001", "https://doi.org/10.5281/zenodo.5550001"],
            "p_twice",
        ),
        {},
    ),
    (
        "github.shared",
        "a GitHub repository linked by two papers of a list",
        {
            "papers": [
                {"url": ["https://github.com/gzorg/treerepo"], "id": "p_gh_a"},
                {
                    "url": [
                        "https://github.com/gzorg/treerepo",
                        "https://gitlab.com/gzorg/sub/glproj",
                    ],
                    "id": "p_gh_b",
                },
            ]
        },
        {},
    ),
    (
        "osf_license",
        "osf_license = TRUE: one licence found, one lookup failing (NA)",
        one(osf("629bx", "j3gcx"), "p_lic", args={"osf_license": True}),
        {},
    ),
    (
        "cache",
        "cache = TRUE (listings stored in the repository-info cache)",
        one(
            [
                "https://github.com/gzorg/treerepo",
                "https://zenodo.org/records/5550001",
                "https://gitlab.com/gzorg/sub/glproj",
            ],
            "p_cache",
            args={"cache": True},
        ),
        {},
    ),
]

# --- DSpace items their host does not know (UPSTREAM_ISSUES U43) -------------------------
# Nothing is recorded for them, so every REST lookup fails on both sides (httptest2
# errors, the Python replay answers 404).
PA_HANDLE = "https://www.psycharchives.org/handle/20.500.12034/"
CASES += [
    (
        cid,
        note,
        one(urls, "p_" + cid.replace(".", "_")),
        {},
    )
    for cid, note, urls in (
        ("dspace.unfound", "a PsychArchives item that does not exist", [PA_HANDLE + "99991"]),
        (
            "dspace.unfound_two",
            "two PsychArchives items that do not exist",
            [PA_HANDLE + "99991", PA_HANDLE + "99992"],
        ),
        (
            "dspace.found_and_unfound",
            "a PsychArchives item and one that does not exist",
            [PA_HANDLE + "17526", PA_HANDLE + "99991"],
        ),
        (
            "dspace7.unfound",
            "a DSpace 7 item that does not exist",
            [
                "https://repository.gatech.edu/entities/publication/"
                "00000000-0000-4000-8000-000000000000"
            ],
        ),
    )
]


# cases whose repo_error R fills with its own crash text (a dplyr, match() or
# data.frame() error) where pytacheck says what went wrong: error texts are not
# compared, only whether each row has one (compare: presence, docs/PARITY.md)
REPO_ERROR_PRESENCE = {"osf.invalid_id", "figshare.share_link", "dspace.unfound_two"}


def case(cid: str, note: str, spec: dict[str, Any], ignore: Any) -> dict[str, Any]:
    fn = "run_repo"
    py_args = {
        k: v
        for k, v in spec.items()
        if k in ("papers", "read", "paperlist", "args", "mocks", "report", "tables")
    }
    out: dict[str, Any] = {
        "id": f"repo_check.{cid}",
        "note": note,
        "r": "identity",
        "py": f"tests.mod_repo_check.parity_support.{fn}",
        "args": {"x": {"$expr": {"r": r_expr(spec), "py": "None"}}},
        "py_drop": ["x"],
        "py_args": py_args,
    }
    if ignore:
        out["compare"] = {"ignore": list(ignore)} if isinstance(ignore, list) else dict(ignore)
    if spec.get("tables") or spec.get("report"):
        out.pop("compare", None)
    if cid in REPO_ERROR_PRESENCE:
        out.setdefault("compare", {})["presence"] = ["gated_repos.repo_error"]
    return out


class _Dumper(yaml.SafeDumper):
    """Double-quote every string (R's YAML reader would turn "yes"/"1.0" into values)."""


_Dumper.add_representer(str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s, style='"'))


def main() -> None:
    data = {
        "area": "mod_repo_check",
        "cases": [case(*c) for c in CASES],
    }
    header = (
        "# Parity cases for the repo_check module (inst/modules/repo_check.R).\n"
        "# Generated by tests/mod_repo_check/make_cases.py -- do not edit by hand.\n"
    )
    text = yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=10_000)
    OUT.write_text(header + text, encoding="utf-8")


if __name__ == "__main__":
    main()
