"""Write parity/cases/archives_gz.yaml (every string double-quoted).

The cases are defined here in Python because many of them wrap an R and a
Python expression; regenerate with ``python tests/archives_gz/make_cases.py``
and then ``python -m parity generate --area archives_gz``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MK = "tests/archives_gz/mocks"
MKF = "tests/archives_gz/mocks_fail"
UP = "tests/archives_gz/fixtures/upload"
PS = "tests.archives_gz.parity_support.run"
GH = "metacheck.archives.github"
GL = "metacheck.archives.gitlab"
ZE = "metacheck.archives.zenodo"
ZU = "metacheck.archives.zenodo_upload"
IGN_PID = {"ignore": ["paper_id"]}
PSYCH = {"$read": ["upstream/metacheck/tests/testthat/fixtures/psychsci"]}

cases: list[dict] = []


def case(cid, r, py, args=None, mock=None, compare=None, **extra):
    c = {"id": cid, "r": r, "py": py, "args": args or {}}
    if mock:
        c["mock_dir"] = mock
    if compare:
        c["compare"] = compare
    c.update(extra)
    cases.append(c)


def wrapped(cid, r_code, py_code, mock=None, compare=None, online=True):
    """A call evaluated inside online() mocking (R) / parity_support.run (Python)."""
    if online:
        r_code = (
            f"testthat::with_mocked_bindings({r_code}, "
            'online = function(...) TRUE, .package = "metacheck")'
        )
    case(
        cid,
        "identity",
        PS,
        {"x": {"$expr": {"r": r_code, "py": f"lambda: {py_code}"}}},
        mock=mock,
        compare=compare,
    )


def imp(mod):
    return f'__import__("{mod}", fromlist=["_"])'


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
gh_urls = [
    "github.com/a/b1",
    "http://github.com/a/b2",
    "https://github.com/a/b3",
    "https://github.com/a/b4.git",
    "https://github.com/a/b5/file",
    "https://github.com/a/b6",
    "https://github.com/a/b7",
    "https://github.com/a/b8",
]
case(
    "github_links.test_paper.urls",
    "github_links",
    f"{GH}.github_links",
    {"paper": {"$test_paper": {"text": ["The github repo is a/b9"], "url": gh_urls}}},
    compare=IGN_PID,
)
case(
    "github_links.test_paper.bare",
    "github_links",
    f"{GH}.github_links",
    {
        "paper": {
            "$test_paper": {
                "text": [
                    "See our github repo at scienceverse/metacheck.",
                    "Code is on GitHub (https://github.com/a/b) and elsewhere at c/d.",
                    "Docs at user.github.io/project and org/repo on github.",
                    "Nothing to see here.",
                    "Our GitHub: lab/tool.git, data/set and more.",
                    "The github",
                    "github x/y z/w q/r one two three four five six seven eight nine ten m/n",
                    "Visit GITHUB.COM for lab_1/tool-2.v3",
                ],
                "url": ["https://osf.io/abcde", "https://github.com/org/repo/tree/main/sub"],
            }
        }
    },
    compare=IGN_PID,
)
case(
    "github_links.test_paper.none",
    "github_links",
    f"{GH}.github_links",
    {"paper": {"$test_paper": {"text": ["No links here."]}}},
    compare=IGN_PID,
)
case("github_links.demo", "github_links", f"{GH}.github_links", {"paper": {"$paper": "demo"}})
case("github_links.psychsci", "github_links", f"{GH}.github_links", {"paper": PSYCH})

for cid, repo in [
    ("clean", "scienceverse/metacheck"),
    ("url", "https://github.com/scienceverse/metacheck"),
    ("git", "http://github.com/scienceverse/metacheck.git"),
    ("subpath", "https://github.com/scienceverse/metacheck/index"),
    ("unclean", "https://scienceverse/metacheck"),
    ("not_found", "scienceverse/norepo"),
    ("no_match", "nothing"),
]:
    case(f"github_repo.{cid}", "github_repo", f"{GH}.github_repo", {"repo": repo}, mock="apis")
case(
    "github_repo.vector",
    "github_repo",
    f"{GH}.github_repo",
    {
        "repo": {
            "$chr": ["scienceverse/metacheck", "scienceverse/faux", "scienceverse/metacheck.git"]
        }
    },
    mock="apis",
)
case("github_repo.empty", "github_repo", f"{GH}.github_repo", {"repo": {"$chr": []}}, mock="apis")
case("github_repo.na", "github_repo", f"{GH}.github_repo", {"repo": {"$NA": True}}, mock="apis")

three = {"$chr": ["scienceverse/metacheck", "scienceverse/typo", "scienceverse/faux"]}
case("github_readme.metacheck", "github_readme", f"{GH}.github_readme",
     {"repo": "scienceverse/metacheck"}, mock="apis")  # fmt: skip
case("github_readme.not_found", "github_readme", f"{GH}.github_readme",
     {"repo": "scienceverse/norepo"}, mock="apis")  # fmt: skip
case("github_readme.vector", "github_readme", f"{GH}.github_readme", {"repo": three}, mock="apis")
case("github_languages.metacheck", "github_languages", f"{GH}.github_languages",
     {"repo": "https://github.com/scienceverse/metacheck"}, mock="apis")  # fmt: skip
case("github_languages.not_found", "github_languages", f"{GH}.github_languages",
     {"repo": "scienceverse/norepo"}, mock="apis")  # fmt: skip
case("github_languages.vector", "github_languages", f"{GH}.github_languages",
     {"repo": three}, mock="apis")  # fmt: skip

for cid, args in [
    ("metacheck", {"repo": "scienceverse/metacheck"}),
    ("unclean", {"repo": "https://scienceverse/metacheck"}),
    ("dir", {"repo": "scienceverse/metacheck", "dir": "tests"}),
    ("dir_nonrec", {"repo": "scienceverse/metacheck", "dir": ".github", "recursive": False}),
    ("dir_rec", {"repo": "scienceverse/metacheck", "dir": ".github", "recursive": True}),
    ("demo_rec", {"repo": "https://github.com/scienceverse/demo", "recursive": True}),
    ("vector", {"repo": {"$chr": ["scienceverse/metacheck", "scienceverse/demo"]}}),
    ("vector_na", {"repo": {"$expr": {
        "r": 'c("scienceverse/demo", NA, "scienceverse/norepo", "scienceverse/demo")',
        "py": '["scienceverse/demo", None, "scienceverse/norepo", "scienceverse/demo"]'}}}),
    ("not_found", {"repo": "scienceverse/norepo"}),
]:  # fmt: skip
    case(f"github_files.{cid}", "github_files", f"{GH}.github_files", args, mock="apis")
case("github_files.rate_limited", "github_files", f"{GH}.github_files",
     {"repo": "gzorg/limited"}, mock=MK)  # fmt: skip
case("github_files.html_json_types", "github_files", f"{GH}.github_files",
     {"repo": "https://github.com/gzorg/fallrepo"}, mock=MK)  # fmt: skip
case("github_files.empty_repo", "github_files", f"{GH}.github_files",
     {"repo": "gzorg/notree"}, mock=MK)  # fmt: skip

case("github_info.metacheck", "github_info", f"{GH}.github_info",
     {"repo": "scienceverse/metacheck"}, mock="apis")  # fmt: skip
case("github_info.not_found", "github_info", f"{GH}.github_info",
     {"repo": "scienceverse/norepo"}, mock="apis")  # fmt: skip

for cid, repo in [
    ("tree", "gzorg/treerepo"),
    ("truncated", "https://github.com/gzorg/bigrepo"),
    ("no_blobs", "gzorg/emptyrepo"),
    ("meta_fails", "gzorg/fallrepo"),
    ("tree_fails", "gzorg/notree"),
    ("invalid", "nothing"),
]:
    case(f"github_tree_files.{cid}", "github_tree_files", f"{GH}.github_tree_files",
         {"repo": repo}, mock=MK)  # fmt: skip

# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------
case(
    "gitlab_links.test_paper",
    "gitlab_links",
    f"{GL}.gitlab_links",
    {
        "paper": {
            "$test_paper": {
                "text": [
                    "Code on gitlab at lab/project and more.",
                    "Mirror at gitlab.com/x/y is here.",
                    "See our GitLab repo group/sub/tool.git today",
                    "Pages at user.gitlab.io/site with gitlab a/b",
                    "github at c/d",
                ],
                "url": [
                    "https://gitlab.com/gzorg/sub/glproj",
                    "https://gitlab.com/a",
                    "http://www.gitlab.com/b/c",
                    "https://github.com/x/y",
                ],
            }
        }
    },
    compare=IGN_PID,
)
case("gitlab_links.demo", "gitlab_links", f"{GL}.gitlab_links", {"paper": {"$paper": "demo"}})
case("gitlab_links.psychsci", "gitlab_links", f"{GL}.gitlab_links", {"paper": PSYCH})
for cid, repo in [
    ("url_git", "https://gitlab.com/gzorg/sub/glproj.git/"),
    ("www_upper", "HTTPS://WWW.GitLab.com/gzorg/bigproj/"),
    ("bare", "gzorg/bigproj"),
    ("missing", "gzorg/missing"),
    ("one_part", "justonepart"),
    ("space", "a b/c"),
    ("empty_string", ""),
    ("other_host", "https://github.com/gzorg/bigproj"),
]:
    case(f"gitlab_repo.{cid}", "gitlab_repo", f"{GL}.gitlab_repo", {"repo": repo}, mock=MK)
case("gitlab_repo.vector", "gitlab_repo", f"{GL}.gitlab_repo",
     {"repo": {"$chr": ["https://gitlab.com/gzorg/sub/glproj", "gzorg/bigproj.git"]}}, mock=MK)  # fmt: skip
case("gitlab_repo.na", "gitlab_repo", f"{GL}.gitlab_repo", {"repo": {"$NA": True}}, mock=MK)
for cid, repo in [
    ("subgroup_paged", "https://gitlab.com/gzorg/sub/glproj"),
    ("graphql_batches", "gzorg/bigproj"),
    ("meta_fails", "gzorg/nometa"),
    ("tree_fails", "gzorg/notree"),
    ("empty", "gzorg/emptyproj"),
    ("missing", "gzorg/missing"),
    ("invalid", "nothing"),
]:
    case(f"gitlab_tree_files.{cid}", "gitlab_tree_files", f"{GL}.gitlab_tree_files",
         {"repo": repo}, mock=MK)  # fmt: skip
case(".gitlab_project_id.subgroup", ".gitlab_project_id", f"{GL}._gitlab_project_id",
     {"path": "gzorg/sub/glproj"})  # fmt: skip
case(".gitlab_project_id.special", ".gitlab_project_id", f"{GL}._gitlab_project_id",
     {"path": "a b/c+d~e_f.g"})  # fmt: skip
case(".gitlab_blob_sizes.empty", ".gitlab_blob_sizes", f"{GL}._gitlab_blob_sizes",
     {"clean_repo": "gzorg/bigproj", "paths": {"$chr": []}})  # fmt: skip
case(".gitlab_blob_sizes.subgroup", ".gitlab_blob_sizes", f"{GL}._gitlab_blob_sizes",
     {"clean_repo": "gzorg/sub/glproj",
      "paths": {"$chr": ["README.md", "data/d1.csv", "src/run.py", "x.json"]}}, mock=MK)  # fmt: skip

# ---------------------------------------------------------------------------
# Zenodo: links, IDs, info
# ---------------------------------------------------------------------------
case(
    "zenodo_links.test_paper.urls",
    "zenodo_links",
    f"{ZE}.zenodo_links",
    {"paper": {"$test_paper": {"url": [
        "https://zenodo.org/records/12345",
        "https://doi.org/10.5281/zenodo.98765",
        "https://osf.io/abcde",
    ]}}},
    compare=IGN_PID,
)  # fmt: skip
case(
    "zenodo_links.test_paper.text",
    "zenodo_links",
    f"{ZE}.zenodo_links",
    {
        "paper": {
            "$test_paper": {
                "text": [
                    "Data: 10.5281/zenodo.12345 and https://zenodo.org/record/777/.",
                    "doi.org/10.5281/zenodo.12345 again",
                    "zenodo.org/records/abc is not a record",
                    "https://zenodo.org/records/12345/",
                    "Nothing here",
                ],
                "url": [
                    "https://zenodo.org/records/12345/",
                    "https://zenodo.org/communities/x",
                    "HTTPS://DOI.ORG/10.5281/ZENODO.5",
                    "https://zenodo.org/records/12345/",
                ],
            }
        }
    },
    compare=IGN_PID,
)
case("zenodo_links.none", "zenodo_links", f"{ZE}.zenodo_links",
     {"paper": {"$test_paper": {"text": ["No links"]}}}, compare=IGN_PID)  # fmt: skip
case("zenodo_links.demo", "zenodo_links", f"{ZE}.zenodo_links", {"paper": {"$paper": "demo"}})
case("zenodo_links.psychsci", "zenodo_links", f"{ZE}.zenodo_links", {"paper": PSYCH})

case(".zenodo_id.vector", ".zenodo_id", f"{ZE}._zenodo_id", {"zenodo_url": {"$chr": [
    "12345",
    "https://zenodo.org/records/12345",
    "https://zenodo.org/record/12345",
    "https://doi.org/10.5281/zenodo.12345",
    "zenodo.12345",
    "https://zenodo.org/records/12345 zenodo.98765",
    "not-a-zenodo-id",
    "",
    "  https://zenodo.org/uploads/42  ",
    "ZENODO.ORG/RECORDS/7",
]}})  # fmt: skip
case(
    ".zenodo_id.vector_na",
    ".zenodo_id",
    f"{ZE}._zenodo_id",
    {
        "zenodo_url": {
            "$expr": {
                "r": 'c("zenodo.1", NA, "  ", "10.5281/zenodo.2")',
                "py": '["zenodo.1", None, "  ", "10.5281/zenodo.2"]',
            }
        }
    },
)
case(".zenodo_id.null", ".zenodo_id", f"{ZE}._zenodo_id", {"zenodo_url": {"$null": True}})
case(".zenodo_id.single", ".zenodo_id", f"{ZE}._zenodo_id",
     {"zenodo_url": "https://doi.org/10.5281/zenodo.2669586"})  # fmt: skip
case(".zenodo_id.number", ".zenodo_id", f"{ZE}._zenodo_id", {"zenodo_url": {"$dbl": [12345]}})
case(
    ".zenodo_id.na",
    ".zenodo_id",
    f"{ZE}._zenodo_id",
    {"zenodo_url": {"$expr": {"r": "NA_character_", "py": "pd.NA"}}},
)

case(".zenodo_info.found", ".zenodo_info", f"{ZE}._zenodo_info",
     {"zenodo_id": "10.5281/zenodo.2669586"}, mock="apis")  # fmt: skip
case(".zenodo_info.unfound", ".zenodo_info", f"{ZE}._zenodo_info",
     {"zenodo_id": "00000000"}, mock="apis")  # fmt: skip
case(".zenodo_info.custom", ".zenodo_info", f"{ZE}._zenodo_info",
     {"zenodo_id": "https://zenodo.org/records/5550002"}, mock=MK)  # fmt: skip
case(".zenodo_unread", ".zenodo_unread", f"{ZE}._zenodo_unread",
     {"zenodo_id": "5498371", "error": "unfound"})  # fmt: skip

zi = f"{imp(ZE)}.zenodo_info"
wrapped(
    "zenodo_info.vector",
    'metacheck::zenodo_info(c("https://doi.org/10.5281/zenodo.17754445", '
    '"https://zenodo.org/records/123456789", "https://doi.org/10.5281/zenodo.17754445", NA))',
    f'{zi}(["https://doi.org/10.5281/zenodo.17754445", "https://zenodo.org/records/123456789", '
    '"https://doi.org/10.5281/zenodo.17754445", None])',
    mock="apis",
)
wrapped(
    "zenodo_info.table",
    'metacheck::zenodo_info(data.frame(id = 1:3, href = c("https://doi.org/10.5281/zenodo.17754445", '
    '"https://zenodo.org/records/123456789", "not-a-zenodo-id")), "href")',
    f'{zi}(pd.DataFrame({{"id": [1, 2, 3], "href": ["https://doi.org/10.5281/zenodo.17754445", '
    '"https://zenodo.org/records/123456789", "not-a-zenodo-id"]}), "href")',
    mock="apis",
)
wrapped(
    "zenodo_info.id_col_position.na",
    'metacheck::zenodo_info(data.frame(x = c("a", "b"), url = c("10.5281/zenodo.2669586", '
    'NA), zenodo_url = c("keep", "me")), 2)',
    f'{zi}(pd.DataFrame({{"x": ["a", "b"], "url": ["10.5281/zenodo.2669586", None], '
    '"zenodo_url": ["keep", "me"]}), 2)',
    mock="apis",
)
wrapped(
    "zenodo_info.id_col_position",
    'metacheck::zenodo_info(data.frame(x = c("a", "b", "c"), url = c("10.5281/zenodo.2669586", '
    '"nope", "10.5281/zenodo.2669586"), zenodo_url = c("keep", "me", "x")), 2)',
    f'{zi}(pd.DataFrame({{"x": ["a", "b", "c"], "url": ["10.5281/zenodo.2669586", "nope", '
    '"10.5281/zenodo.2669586"], "zenodo_url": ["keep", "me", "x"]}), 2)',
    mock="apis",
)
wrapped(
    "zenodo_info.links_unfound",
    "metacheck::zenodo_info(metacheck::zenodo_links(metacheck::test_paper(url = "
    'c("https://doi.org/10.5281/zenodo.00000000", "https://zenodo.org/records/2669586/"))))',
    f'{zi}({imp(ZE)}.zenodo_links(pc.test_paper(url=["https://doi.org/10.5281/zenodo.00000000", '
    '"https://zenodo.org/records/2669586/"])))',
    mock="apis",
    compare=IGN_PID,
)
wrapped(
    "zenodo_info.no_valid",
    'metacheck::zenodo_info(c("not-a-zenodo-id", NA, "osf.io/abcde"))',
    f'{zi}(["not-a-zenodo-id", None, "osf.io/abcde"])',
    mock="apis",
)
wrapped(
    "zenodo_info.empty",
    "metacheck::zenodo_info(character(0))",
    f"{zi}([])",
    mock="apis",
)
wrapped(
    "zenodo_info.custom_records",
    'metacheck::zenodo_info(c("5550001", "https://zenodo.org/records/5550003"))',
    f'{zi}(["5550001", "https://zenodo.org/records/5550003"])',
    mock=MK,
)

# ---------------------------------------------------------------------------
# Zenodo: downloads
# ---------------------------------------------------------------------------
R_TMP = "{ d <- tempfile(); dir.create(d); d }"
zfd = f"{imp(ZE)}.zenodo_file_download"
wt = f"{imp('tests.archives_gz.parity_support')}.with_tmpdir"
for cid, r_args, py_args in [
    ("all_good", '"5550001"', '"5550001"'),
    ("mixed", '"https://doi.org/10.5281/zenodo.5550002", max_file_size = 10',
     '"https://doi.org/10.5281/zenodo.5550002", max_file_size=10'),
    ("no_limits", '"5550002", max_file_size = NULL, max_download_size = Inf',
     '"5550002", max_file_size=None, max_download_size=float("inf")'),
    ("no_files", '"5550003"', '"5550003"'),
    ("total_cap", '"5550004", max_file_size = NULL, max_download_size = 10',
     '"5550004", max_file_size=None, max_download_size=10'),
    ("multi", 'c("5550001", "5550003", "not-an-id", "5550001")',
     '["5550001", "5550003", "not-an-id", "5550001"]'),
    ("unzip_fallback", '"5550005", unzip_types = "data"', '"5550005", unzip_types="data"'),
    ("zip_no_unzip", '"5550005", max_file_size = 200', '"5550005", max_file_size=200'),
]:  # fmt: skip
    wrapped(
        f"zenodo_file_download.{cid}",
        f"metacheck::zenodo_file_download({r_args}, download_to = {R_TMP})",
        f"{wt}(lambda d: {zfd}({py_args}, download_to=d))",
        mock=MK,
    )
case("zenodo_file_download.invalid", "zenodo_file_download", f"{ZE}.zenodo_file_download",
     {"zenodo_id": "not-an-id"})  # fmt: skip
case("zenodo_file_download.null", "zenodo_file_download", f"{ZE}.zenodo_file_download",
     {"zenodo_id": {"$chr": []}})  # fmt: skip
wrapped(
    "zenodo_file_download.existing_target",
    '{ d <- tempfile(); dir.create(file.path(d, "5550001"), recursive = TRUE); '
    'dir.create(file.path(d, "5550001_1")); '
    'metacheck::zenodo_file_download("5550001", download_to = d)$folder }',
    f'{wt}(lambda d: (__import__("os").makedirs(d + "/5550001"), '
    f'__import__("os").makedirs(d + "/5550001_1"), '
    f'{zfd}("5550001", download_to=d))[-1]["folder"])',
    mock=MK,
)

vd = f"{imp('tests.archives_gz.parity_support')}.verify_case"
case(
    ".zenodo_verify_downloads.mixed",
    "identity",
    "tests.archives_gz.parity_support.run",
    {"x": {"$expr": {
        "r": (
            "local({ d <- tempfile(); dir.create(d); "
            'writeLines("abc", file.path(d, "a.txt")); writeLines("abc", file.path(d, "b.txt")); '
            'dir.create(file.path(d, "sub")); '
            "files <- data.frame(path = c(\"a.txt\", \"b.txt\", \"missing.txt\", NA, \"sub\", \"a.txt\"), "
            "size = c(4, 4, 3, 1, 0, 5), "
            'checksum = c(paste0("md5:", unname(tools::md5sum(file.path(d, "a.txt")))), '
            '"md5:00000000000000000000000000000000", NA, NA, NA, "sha1:x"), '
            "downloaded = c(TRUE, TRUE, TRUE, TRUE, TRUE, FALSE), "
            "extracted = c(NA, NA, NA, 2L, NA, NA)); "
            "metacheck:::.zenodo_verify_downloads(files, d) })"
        ),
        "py": f"lambda: {vd}()",
    }}},
)  # fmt: skip
case(
    ".zenodo_verify_downloads.no_path",
    "identity",
    PS,
    {"x": {"$expr": {
        "r": "metacheck:::.zenodo_verify_downloads(data.frame(size = c(1, 2), downloaded = TRUE), tempdir())",
        "py": (
            f'lambda: {imp(ZE)}._zenodo_verify_downloads(pd.DataFrame({{"size": [1.0, 2.0], '
            '"downloaded": [True, True]}), "/tmp")'
        ),
    }}},
)  # fmt: skip
case(
    ".zenodo_verify_downloads.empty",
    ".zenodo_verify_downloads",
    f"{ZE}._zenodo_verify_downloads",
    {"files": {"$expr": {"r": "data.frame(path = character(0))",
                         "py": 'pd.DataFrame({"path": pd.Series([], dtype="string")})'}},
     "download_to": "."},
)  # fmt: skip

# ---------------------------------------------------------------------------
# Zenodo: uploads
# ---------------------------------------------------------------------------
case(".zenodo_api.sandbox", ".zenodo_api", f"{ZU}._zenodo_api", {"sandbox": True})
case(".zenodo_api.real", ".zenodo_api", f"{ZU}._zenodo_api", {"sandbox": False})
case(".zenodo_api.default", ".zenodo_api", f"{ZU}._zenodo_api", {})
for cid, value in [
    ("cc_by", "CC-By Attribution 4.0 International"),
    ("cc_by_nc_sa", "CC-BY Attribution-NonCommercial-ShareAlike 4.0 International"),
    ("cc0", "CC0 1.0 Universal"),
    ("bsd3", 'BSD 3-Clause "New"/"Revised" License'),
    ("gpl2", "GNU General Public License (GPL) 2.0"),
    ("lgpl21", "GNU Lesser General Public License (LGPL) 2.1"),
    ("afl", "Academic Free License (AFL) 3.0"),
    ("spacing", "mit   LICENSE"),
    ("no_license", "No license"),
    ("other", "Other"),
    ("made_up", "Some Made Up License"),
    ("empty", ""),
]:
    case(f".zenodo_license_id.{cid}", ".zenodo_license_id", f"{ZU}._zenodo_license_id",
         {"osf_license": value})  # fmt: skip
case(".zenodo_license_id.na", ".zenodo_license_id", f"{ZU}._zenodo_license_id",
     {"osf_license": {"$expr": {"r": "NA_character_", "py": "pd.NA"}}})  # fmt: skip
case(".zenodo_license_id.zero_length", ".zenodo_license_id", f"{ZU}._zenodo_license_id",
     {"osf_license": {"$chr": []}})  # fmt: skip
case(".zenodo_license_id.null", ".zenodo_license_id", f"{ZU}._zenodo_license_id",
     {"osf_license": {"$null": True}})  # fmt: skip

case(".zenodo_regex_escape", ".zenodo_regex_escape", f"{ZU}._zenodo_regex_escape",
     {"x": {"$chr": ["/a/proj (old)/v1.0+[x]{y}^$*?|z", "C:\\x\\y", "plain"]}})  # fmt: skip
for cid, files, folder in [
    ("unique", ["/p/a.R", "/p/sub/b.csv", "/p//c.txt"], "/p"),
    ("clash", ["/p/a.R", "/p/github/README.md", "/p/data/README.md"], "/p"),
    ("deep", ["/p/a/x/f.txt", "/p/b/x/f.txt", "/p/f.txt"], "/p"),
    ("leftover", ["/p/a.csv", "/p/x/a.csv", "/p/x__a.csv"], "/p"),
    ("leftover_noext", ["/p/README", "/p/d/README", "/p/d__README"], "/p"),
    ("regex_folder", ["/a/proj (v1.0)/d.csv", "/a/proj (v1.0)/s/d.csv"], "/a/proj (v1.0)"),
    ("backslash", ["C:\\p\\a\\f.txt", "C:\\p\\b\\f.txt"], "C:\\p"),
    ("other_folder", ["/tmp/zips/proj.zip", "/tmp/zips/proj_materials.zip"], "/data/proj"),
]:
    case(f".zenodo_flat_names.{cid}", ".zenodo_flat_names", f"{ZU}._zenodo_flat_names",
         {"files": {"$chr": files}, "folder": folder})  # fmt: skip
case(".zenodo_flat_names.empty", ".zenodo_flat_names", f"{ZU}._zenodo_flat_names",
     {"files": {"$chr": []}, "folder": "/p"})  # fmt: skip

case(".zenodo_classify", ".zenodo_classify", f"{ZU}._zenodo_classify", {"files": {"$chr": [
    "/x/proj/data/raw.csv", "/x/proj/code/analysis.R", "/x/proj/stimuli/img.png",
    "/x/proj/README.md", "/x/proj/output/fig.pdf", "/x/proj/Materials/list.docx",
    "/x/proj/weird.xyz",
]}})  # fmt: skip
case(".zenodo_classify.empty", ".zenodo_classify", f"{ZU}._zenodo_classify",
     {"files": {"$chr": []}})  # fmt: skip

case(".zenodo_meta_from_folder.saved", ".zenodo_meta_from_folder",
     f"{ZU}._zenodo_meta_from_folder", {"folder": {"$file": f"{UP}/proj_osf"}})  # fmt: skip
case(".zenodo_meta_from_folder.none", ".zenodo_meta_from_folder",
     f"{ZU}._zenodo_meta_from_folder", {"folder": {"$file": f"{UP}/proj_plain"}})  # fmt: skip

osf_meta = {
    "osf_id": "6nt4v",
    "title": "My Project",
    "description": "A description",
    "tags": {"$chr": ["open", "data"]},
    "license": "CC-By Attribution 4.0 International",
    "creators": {"$list": [{"name": "DeBruine, Lisa"}]},
}
case(".zenodo_build_metadata.osf", ".zenodo_build_metadata", f"{ZU}._zenodo_build_metadata",
     {"meta": osf_meta, "folder": "/tmp/6nt4v"})  # fmt: skip
case(".zenodo_build_metadata.no_license", ".zenodo_build_metadata",
     f"{ZU}._zenodo_build_metadata",
     {"meta": {"osf_id": "6nt4v", "title": "T", "description": "D", "license": {"$NA": True},
               "creators": {"$list": [{"name": "A, B"}]}},
      "folder": "/tmp/6nt4v", "license": "cc0-1.0", "upload_type": "software"})  # fmt: skip
case(".zenodo_build_metadata.null", ".zenodo_build_metadata", f"{ZU}._zenodo_build_metadata",
     {"meta": {"$null": True}, "folder": "/tmp/myfolder"})  # fmt: skip
case(".zenodo_build_metadata.blank", ".zenodo_build_metadata", f"{ZU}._zenodo_build_metadata",
     {"meta": {"osf_id": "abcde", "title": "", "description": ""}, "folder": "/tmp/f/"})  # fmt: skip
case(".zenodo_build_metadata.one_tag", ".zenodo_build_metadata",
     f"{ZU}._zenodo_build_metadata",
     {"meta": {"title": "T", "tags": "solo", "license": "Other",
               "creators": {"$list": []}}, "folder": "/tmp/f"})  # fmt: skip

case(".zenodo_check_token.ok", ".zenodo_check_token", f"{ZU}._zenodo_check_token",
     {"api": "https://sandbox.zenodo.org/api", "token": "fake-token"}, mock=MK)  # fmt: skip
case(".zenodo_check_token.refused", ".zenodo_check_token", f"{ZU}._zenodo_check_token",
     {"api": "https://zenodo.org/api", "token": "fake-token", "sandbox": False}, mock=MK)  # fmt: skip
resp_ok_r = (
    'httr2::response(status_code = 201L, headers = list(`content-type` = "application/json"), '
    'body = charToRaw(\'{"id": 5, "links": {"html": "u"}}\'))'
)
resp_ok_py = (
    '__import__("httpx").Response(201, headers={"content-type": "application/json"}, '
    'content=b\'{"id": 5, "links": {"html": "u"}}\')'
)
case(".zenodo_check_resp.ok", ".zenodo_check_resp", f"{ZU}._zenodo_check_resp",
     {"resp": {"$expr": {"r": resp_ok_r, "py": resp_ok_py}}, "what": "Creating"})  # fmt: skip
case(".zenodo_check_resp.not_json", ".zenodo_check_resp", f"{ZU}._zenodo_check_resp",
     {"resp": {"$expr": {"r": "httr2::response(status_code = 202L, body = charToRaw('x'))",
                         "py": '__import__("httpx").Response(202, content=b"x")'}},
      "what": "Creating"})  # fmt: skip
case(".zenodo_check_resp.error", ".zenodo_check_resp", f"{ZU}._zenodo_check_resp",
     {"resp": {"$expr": {
         "r": "httr2::response(status_code = 400L, body = charToRaw('{}'))",
         "py": '__import__("httpx").Response(400, content=b"{}")'}},
      "what": "Uploading x"})  # fmt: skip

case("zenodo_pat.set", "zenodo_pat", f"{ZU}.zenodo_pat",
     {"pat": "sandbox-token-for-tests", "sandbox": True})  # fmt: skip
case("zenodo_pat.not_a_string", "zenodo_pat", f"{ZU}.zenodo_pat", {"pat": 123})
case("zenodo_pat.two_strings", "zenodo_pat", f"{ZU}.zenodo_pat", {"pat": {"$chr": ["a", "b"]}})

case(".osf_zenodo_metadata", ".osf_zenodo_metadata", f"{ZU}._osf_zenodo_metadata",
     {"osf_id": {"$expr": {"r": 'c("gzosf", "gzgone", NA, "gzosf")',
                           "py": '["gzosf", "gzgone", None, "gzosf"]'}}}, mock=MK)  # fmt: skip
case(".osf_zenodo_metadata.empty", ".osf_zenodo_metadata", f"{ZU}._osf_zenodo_metadata",
     {"osf_id": {"$expr": {"r": "NA_character_", "py": "[None]"}}})  # fmt: skip

zu = f"{ZU}.zenodo_upload"
case(
    "zenodo_upload.individual_files",
    "zenodo_upload",
    zu,
    {
        "folders": {"$list": [{"$file": f"{UP}/proj_osf"}, {"$file": f"{UP}/proj_plain"}]},
        "zenodo_pat": "fake-token",
        "as_zip": False,
        "ask": False,
    },
    mock=MK,
)
case(
    "zenodo_upload.publish_override",
    "zenodo_upload",
    zu,
    {
        "folders": {"$file": f"{UP}/proj_plain"},
        "zenodo_pat": "fake-token",
        "publish": True,
        "upload_type": "software",
        "metadata": {"title": "Override", "version": "1.0"},
        "as_zip": False,
        "ask": False,
    },
    mock=MK,
)
case(
    "zenodo_upload.from_download_table",
    "zenodo_upload",
    zu,
    {
        "folders": {"$expr": {
            "r": (
                'data.frame(download_path = c(file.path(root, "tests/archives_gz/fixtures/upload/'
                'proj_fromosf"), file.path(root, "tests/archives_gz/fixtures/upload/proj_fromosf"), '
                'file.path(root, "tests/archives_gz/fixtures/upload/proj_plain"), NA, '
                '"/no/such/folder"), osf_project = c("gzosf", "gzosf", "gzgone", "zzzzz", "yyyyy"))'
            ),
            "py": (
                'pd.DataFrame({"download_path": [str(__import__("parity.cases", fromlist=["_"]).ROOT'
                ' / "tests/archives_gz/fixtures/upload" / f) for f in ("proj_fromosf", '
                '"proj_fromosf", "proj_plain")] + [None, "/no/such/folder"], "osf_project": '
                '["gzosf", "gzosf", "gzgone", "zzzzz", "yyyyy"]})'
            ),
        }},
        "zenodo_pat": "fake-token",
        "as_zip": False,
        "ask": False,
    },
    mock=MK,
)  # fmt: skip
case(
    "zenodo_upload.create_fails",
    "zenodo_upload",
    zu,
    {"folders": {"$file": f"{UP}/proj_plain"}, "zenodo_pat": "fake-token", "as_zip": False,
     "ask": False},
    mock=MKF,
)  # fmt: skip
case(
    "zenodo_upload.token_refused",
    "zenodo_upload",
    zu,
    {"folders": {"$file": f"{UP}/proj_plain"}, "sandbox": False,
     "zenodo_pat": "fake-real-token", "ask": False},
    mock=MK,
)  # fmt: skip
case(
    "zenodo_upload.no_download_path",
    "zenodo_upload",
    zu,
    {"folders": {"$df": {"x": [1]}}, "ask": False},
)
case(
    "zenodo_upload.missing_folder",
    "zenodo_upload",
    zu,
    {"folders": "/no/such/folder", "zenodo_pat": "fake-token", "ask": False},
)
case(
    "zenodo_upload.empty_folder",
    "zenodo_upload",
    zu,
    {"folders": {"$expr": {"r": R_TMP, "py": '__import__("tempfile").mkdtemp()'}},
     "zenodo_pat": "fake-token", "ask": False},
)  # fmt: skip
case(
    "zenodo_upload.all_too_big",
    "zenodo_upload",
    zu,
    {"folders": {"$file": f"{UP}/proj_plain"}, "zenodo_pat": "fake-token",
     "max_file_size": 1e-9, "ask": False},
)  # fmt: skip
case(
    "zenodo_upload.metadata_only_skipped",
    "zenodo_upload",
    zu,
    {"folders": {"$file": f"{UP}/proj_osf"}, "zenodo_pat": "fake-token",
     "upload_osf_metadata": False, "max_file_size": 1e-9, "ask": False},
)  # fmt: skip


class Q(str):
    pass


def q_repr(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


def quote_all(x):
    if isinstance(x, str):
        return Q(x)
    if isinstance(x, dict):
        return {Q(k): quote_all(v) for k, v in x.items()}
    if isinstance(x, list):
        return [quote_all(v) for v in x]
    return x


yaml.add_representer(Q, q_repr)
out = {Q("area"): Q("archives_gz"), Q("cases"): quote_all(cases)}
text = yaml.dump(out, sort_keys=False, allow_unicode=True, width=100)
(ROOT / "parity/cases/archives_gz.yaml").write_text(text, encoding="utf-8")
print(len(cases), "cases")
