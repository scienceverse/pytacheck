"""Write parity/cases/archives_gz_review.yaml (every string double-quoted).

Review cases for the GitHub/GitLab/Zenodo port: branches the first case set
(``make_cases.py``) did not reach. Regenerate with
``python tests/archives_gz/make_review_cases.py`` and then
``python -m parity generate --area archives_gz_review``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MK = "tests/archives_gz/mocks_review"
PS = "tests.archives_gz.parity_support.run"
GH = "metacheck.archives.github"
GL = "metacheck.archives.gitlab"
ZE = "metacheck.archives.zenodo"
ZU = "metacheck.archives.zenodo_upload"
IGN_PID = {"ignore": ["paper_id"]}

cases: list[dict] = []


def case(cid, r, py, args=None, mock=None, compare=None, **extra):
    c = {"id": cid, "r": r, "py": py, "args": args or {}}
    if mock:
        c["mock_dir"] = mock
    if compare:
        c["compare"] = compare
    c.update(extra)
    cases.append(c)


def wrapped(cid, r_code, py_code, mock=None, compare=None):
    """A call evaluated with online() mocked to TRUE on both sides."""
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
# links in papers
# ---------------------------------------------------------------------------
case(
    "github_links.review.tricky",
    "github_links",
    f"{GH}.github_links",
    {"paper": {"$test_paper": {
        "text": [
            "Code on GitHub (my-org/my.repo_v2) is public.",
            "github foo/bar/baz and 1/2.",
            "Die Daten auf GitHub (über/unter) sind da.",
            "See github.com/a/b and c/d.",
            "Our site user.github.io has x/y.",
            "a1/b1 one two three four five six seven eight nine ten eleven github",
            "GITHUB: Lab/Tool.git; Other/Thing",
            "In github:x/y",
        ],
        "url": [
            "https://github.com/x/y/tree/main",
            "https://GitHub.com/Upper/Case",
            "https://gist.github.com/u/abc",
            "https://github.com/onlyorg",
        ],
    }}},
    compare=IGN_PID,
)  # fmt: skip
case(
    "gitlab_links.review.tricky",
    "gitlab_links",
    f"{GL}.gitlab_links",
    {"paper": {"$test_paper": {
        "text": [
            "Code on GitLab (my-org/my.repo_v2) is public.",
            "gitlab foo/bar/baz and 1/2.",
            "See gitlab.com/a/b and c/d.",
            "Pages at user.gitlab.io have x/y.",
            "GITLAB.COM: Lab/Tool.git",
        ],
        "url": [
            "https://gitlab.com/group/sub/project",
            "https://GitLab.com/Upper/Case.git",
            "https://gitlab.example.org/a/b",
        ],
    }}},
    compare=IGN_PID,
)  # fmt: skip
# an empty paper list (UPSTREAM_ISSUES U79): its url table has no columns
for _fn, _mod in (("github_links", GH), ("gitlab_links", GL), ("zenodo_links", ZE)):
    wrapped(
        f"{_fn}.review.empty_paperlist",
        f'{_fn}(paperlist(test_paper("a"))[0])',
        f'{imp(_mod)}.{_fn}(__import__("metacheck").PaperList([]))',
    )
case(
    "zenodo_links.review.tricky",
    "zenodo_links",
    f"{ZE}.zenodo_links",
    {"paper": {"$test_paper": {
        "text": [
            "doi:10.5281/zenodo.123456. And DOI 10.5281/ZENODO.7!",
            "Files at https://zenodo.org/records/99/files/x.csv?download=1",
            "zenodo.org/record/5/ and zenodo.org/records/5",
            "badge: https://zenodo.org/badge/DOI/10.5281/zenodo.321.svg",
            "https://sandbox.zenodo.org/records/1",
        ],
        "url": [
            "http://ZENODO.org/record/3/",
            "https://zenodo.org/records/99/files/x.csv",
            "https://zenodo.org/communities/abc",
            "https://example.org/?ref=zenodo.org",
        ],
    }}},
    compare=IGN_PID,
)  # fmt: skip

# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
case(".zenodo_id.review", ".zenodo_id", f"{ZE}._zenodo_id", {"zenodo_url": {"$chr": [
    "  123  ",
    "10.5281/ZENODO.44",
    "https://sandbox.zenodo.org/records/1",
    "zenodo.org/badge/DOI/10.5281/zenodo.321.svg",
    "zenodo.99x",
    "zenodo.org/uploads/",
    "0123",
]}})  # fmt: skip
case(".zenodo_id.review.numbers", ".zenodo_id", f"{ZE}._zenodo_id",
     {"zenodo_url": {"$dbl": [12, 1234567890]}})  # fmt: skip
case(".zenodo_license_id.review.vector", ".zenodo_license_id", f"{ZU}._zenodo_license_id",
     {"osf_license": {"$chr": ["MIT License", "x"]}})  # fmt: skip
case(".zenodo_license_id.review.list", ".zenodo_license_id", f"{ZU}._zenodo_license_id",
     {"osf_license": {"$list": ["MIT License"]}})  # fmt: skip
for cid, files, folder in [
    ("three_way", ["p/f.txt", "p/x/f.txt", "p/y/x/f.txt"], "p"),
    ("dup_input", ["p/a/f.txt", "p/a/f.txt", "p/.hidden", "p/b/.hidden", "p/c/noext",
                   "p/d/noext", "p/d/noext"], "p"),
    ("trailing_slash", ["p//a.txt", "p/b/a.txt", "p/c.d/e.txt"], "p/"),
    ("numbered_clash", ["p/a/f.txt", "p/a__f.txt", "p/b/a/f.txt"], "p"),
]:  # fmt: skip
    case(f".zenodo_flat_names.review.{cid}", ".zenodo_flat_names", f"{ZU}._zenodo_flat_names",
         {"files": {"$chr": files}, "folder": folder})  # fmt: skip

# ---------------------------------------------------------------------------
# GitHub (mocked)
# ---------------------------------------------------------------------------
for cid, repo in [
    ("error_body", "rv/langerr"),
    ("mixed", "rv/langmix"),
    ("none", "rv/langnone"),
]:
    case(f"github_languages.review.{cid}", "github_languages", f"{GH}.github_languages",
         {"repo": repo}, mock=MK)  # fmt: skip
case("github_languages.review.vector", "github_languages", f"{GH}.github_languages",
     {"repo": {"$chr": ["rv/langmix", "rv/nope", "rv/langnone", "rv/langerr"]}}, mock=MK)  # fmt: skip
case("github_files.review.tricky", "github_files", f"{GH}.github_files",
     {"repo": "https://github.com/rv/files"}, mock=MK)  # fmt: skip
case("github_files.review.tricky_rec", "github_files", f"{GH}.github_files",
     {"repo": "rv/files", "recursive": True}, mock=MK)  # fmt: skip
case("github_files.review.dir_arg", "github_files", f"{GH}.github_files",
     {"repo": "rv/files", "dir": "my dir"}, mock=MK)  # fmt: skip
case("github_files.review.vector_rec", "github_files", f"{GH}.github_files",
     {"repo": {"$chr": ["rv/files", "rv/nope"]}, "recursive": True}, mock=MK)  # fmt: skip
case("github_tree_files.review.null_branch", "github_tree_files", f"{GH}.github_tree_files",
     {"repo": "rv/tree3"}, mock=MK)  # fmt: skip
case("github_tree_files.review.slash_branch", "github_tree_files", f"{GH}.github_tree_files",
     {"repo": "https://github.com/rv/tree4.git"}, mock=MK)  # fmt: skip
case("github_info.review.recursive", "github_info", f"{GH}.github_info",
     {"repo": "https://github.com/rv/files/", "recursive": True}, mock=MK)  # fmt: skip
case("github_readme.review.unicode", "github_readme", f"{GH}.github_readme",
     {"repo": "rv/files"}, mock=MK)  # fmt: skip

# ---------------------------------------------------------------------------
# GitLab (mocked)
# ---------------------------------------------------------------------------
case("gitlab_tree_files.review.branch_space", "gitlab_tree_files", f"{GL}.gitlab_tree_files",
     {"repo": "https://gitlab.com/rv/glp.git"}, mock=MK)  # fmt: skip

# ---------------------------------------------------------------------------
# Zenodo records (mocked)
# ---------------------------------------------------------------------------
for cid, zid in [
    ("title_licence", "5559001"),
    ("empty_id_licence", "5559002"),
    ("no_metadata", "5559003"),
    ("gone", "5559004"),
    ("null_body", "5559005"),
    ("html_body", "5559006"),
    ("title_string", "5559007"),
]:
    case(f".zenodo_info.review.{cid}", ".zenodo_info", f"{ZE}._zenodo_info",
         {"zenodo_id": zid}, mock=MK)  # fmt: skip

# bodies jsonlite reads differently from json.loads() (make_review_mocks.ZENODO_JSON_QUIRKS)
for cid, zid in [
    ("json_comments", "5559201"),
    ("json_repeated_key", "5559202"),
    ("json_nul_escape", "5559203"),
    ("json_bom", "5559204"),
    ("json_declared_latin1", "5559205"),
    ("json_nan", "5559206"),
    ("json_invalid_utf8", "5559207"),
    ("json_nul_byte_bigint", "5559208"),
    ("json_vnd_suffix", "5559209"),
    ("json_empty_body", "5559210"),
]:
    case(f".zenodo_info.review.{cid}", ".zenodo_info", f"{ZE}._zenodo_info",
         {"zenodo_id": zid}, mock=MK)  # fmt: skip

# several IDs (never passed by zenodo_info()): the first record's fields are
# recycled over the IDs; an unreadable first record or no ID is R's error
for cid, r_ids, py_ids in [
    ("vector", 'c("5559001", "5559007")', '["5559001", "5559007"]'),
    (
        "vector_urls",
        'c("5559007", "5559001", "https://zenodo.org/records/5559002")',
        '["5559007", "5559001", "https://zenodo.org/records/5559002"]',
    ),
    ("vector_unfound_error", 'c("5559004", "5559001")', '["5559004", "5559001"]'),
    ("vector_parse_error", 'c("5559006", "5559001")', '["5559006", "5559001"]'),
    ("vector_empty_error", "character(0)", "[]"),
]:
    case(f".zenodo_info.review.{cid}", ".zenodo_info", f"{ZE}._zenodo_info",
         {"zenodo_id": {"$expr": {"r": r_ids, "py": py_ids}}}, mock=MK)  # fmt: skip

zi = f"{imp(ZE)}.zenodo_info"
wrapped(
    "zenodo_info.review.mixed",
    'metacheck::zenodo_info(c("5559001", "5559004", "5559005", "5559003", '
    '"https://zenodo.org/records/5559001", NA))',
    f'{zi}(["5559001", "5559004", "5559005", "5559003", '
    '"https://zenodo.org/records/5559001", None])',
    mock=MK,
)
wrapped(
    "zenodo_info.review.table",
    'metacheck::zenodo_info(data.frame(link = c("https://zenodo.org/records/5559007", '
    '"5559007", "zenodo.5559004", "none", "5559007"), title = letters[1:5], '
    'zenodo_id = "old"), id_col = "link")',
    f'{zi}(pd.DataFrame({{"link": ["https://zenodo.org/records/5559007", "5559007", '
    '"zenodo.5559004", "none", "5559007"], "title": list("abcde"), "zenodo_id": "old"}), '
    'id_col="link")',
    mock=MK,
)
wrapped(
    "zenodo_info.review.factor_na",
    'metacheck::zenodo_info(data.frame(u = factor(c("5559007", NA, "5559001"))))',
    f'{zi}(pd.DataFrame({{"u": pd.Categorical(["5559007", None, "5559001"])}}))',
    mock=MK,
)
wrapped(
    "zenodo_info.review.numeric",
    "metacheck::zenodo_info(c(5559007, 5559007))",
    f"{zi}([5559007.0, 5559007.0])",
    mock=MK,
)

# ---------------------------------------------------------------------------
# Zenodo downloads (mocked)
# ---------------------------------------------------------------------------
# `files$downloaded %in% TRUE` on a character or numeric column: only "TRUE" and
# 1 match; with no `downloaded` column R's assignment fails
CODE_FILES = {"$file": "upstream/metacheck/tests/testthat/fixtures/code_files"}
for cid, dl_r, dl_py in [
    (
        "character_downloaded",
        ', downloaded = c("TRUE", "FALSE", "T", "true")',
        ', "downloaded": pd.array(["TRUE", "FALSE", "T", "true"], dtype="string")',
    ),
    ("numeric_downloaded", ", downloaded = c(1, 2, 0, NA)", ', "downloaded": [1, 2, 0, None]'),
    ("no_downloaded_error", "", ""),
]:
    case(
        f".zenodo_verify_downloads.review.{cid}",
        ".zenodo_verify_downloads",
        f"{ZE}._zenodo_verify_downloads",
        {
            "files": {
                "$expr": {
                    "r": f'data.frame(path = rep("analysis.R", 4), size = 166{dl_r})',
                    "py": f'pd.DataFrame({{"path": ["analysis.R"] * 4, "size": [166.0] * 4{dl_py}}})',
                }
            },
            "download_to": CODE_FILES,
        },
    )

R_TMP = "{ d <- tempfile(); dir.create(d); d }"
zfd = f"{imp(ZE)}.zenodo_file_download"
wt = f"{imp('tests.archives_gz.parity_support')}.with_tmpdir"
for cid, r_args, py_args in [
    ("odd_files", '"5559101"', '"5559101"'),
    ("ties", '"5559102", max_file_size = NULL, max_download_size = 10',
     '"5559102", max_file_size=None, max_download_size=10'),
    ("all_too_big", '"5559102", max_file_size = 0.5', '"5559102", max_file_size=0.5'),
    ("no_caps", '"5559102", max_file_size = 0, max_download_size = -1',
     '"5559102", max_file_size=0, max_download_size=-1'),
    ("na_caps", '"5559102", max_file_size = NA, max_download_size = NA',
     '"5559102", max_file_size=float("nan"), max_download_size=float("nan")'),
]:  # fmt: skip
    wrapped(
        f"zenodo_file_download.review.{cid}",
        f"metacheck::zenodo_file_download({r_args}, download_to = {R_TMP})",
        f"{wt}(lambda d: {zfd}({py_args}, download_to=d))",
        mock=MK,
    )


# ---------------------------------------------------------------------------
# second review round
# ---------------------------------------------------------------------------
for cid, repo in [("array", "rv/langarr"), ("scalar", "rv/langscalar")]:
    case(f"github_languages.review.{cid}", "github_languages", f"{GH}.github_languages",
         {"repo": repo}, mock=MK)  # fmt: skip
case("github_readme.review.nul", "github_readme", f"{GH}.github_readme",
     {"repo": "rv/readmenul"}, mock=MK)  # fmt: skip
case("github_tree_files.review.untyped", "github_tree_files", f"{GH}.github_tree_files",
     {"repo": "rv/tree5"}, mock=MK)  # fmt: skip
case("github_tree_files.review.string_size", "github_tree_files", f"{GH}.github_tree_files",
     {"repo": "rv/tree6"}, mock=MK)  # fmt: skip
case("github_tree_files.review.vector", "github_tree_files", f"{GH}.github_tree_files",
     {"repo": {"$chr": ["rv/files", "rv/nope"]}}, mock=MK)  # fmt: skip
case("gitlab_tree_files.review.untyped", "gitlab_tree_files", f"{GL}.gitlab_tree_files",
     {"repo": "rv/glq"}, mock=MK)  # fmt: skip
case("gitlab_tree_files.review.vector", "gitlab_tree_files", f"{GL}.gitlab_tree_files",
     {"repo": {"$chr": ["rv/glp", "https://gitlab.com/rv/glp"]}}, mock=MK)  # fmt: skip
case("gitlab_tree_files.review.vector_missing", "gitlab_tree_files", f"{GL}.gitlab_tree_files",
     {"repo": {"$chr": ["rv/glp", "rv/nope"]}}, mock=MK)  # fmt: skip

WO = f"{imp('tests.archives_gz.parity_support')}.with_option"
for fn, opt, mod in [
    ("gitlab_pat", "metacheck.gitlab.pat", GL),
    ("zenodo_pat", "metacheck.zenodo.pat.sandbox", ZU),
]:
    case(
        f"{fn}.review.length_one_vector",
        "identity",
        PS,
        {"x": {"$expr": {
            "r": f'local({{ old <- getOption("{opt}"); on.exit(options({opt} = old)); '
                 f'c(metacheck::{fn}(c("tok")), metacheck::{fn}()) }})',
            "py": f'lambda: {WO}("{opt}", lambda: [{imp(mod)}.{fn}(["tok"]), {imp(mod)}.{fn}()])',
        }}},
    )  # fmt: skip

# zenodo_info(): duplicated URLs switch off the NA row-name error; id_col positions
wrapped(
    "zenodo_info.review.na_with_duplicates",
    'metacheck::zenodo_info(data.frame(u = c("5559007", NA, "5559007")))',
    f'{zi}(pd.DataFrame({{"u": ["5559007", None, "5559007"]}}))',
    mock=MK,
)
wrapped(
    "zenodo_info.review.two_na",
    'metacheck::zenodo_info(data.frame(u = c(NA, "5559007", NA)))',
    f'{zi}(pd.DataFrame({{"u": [None, "5559007", None]}}))',
    mock=MK,
)
wrapped(
    "zenodo_info.review.id_col_zero",
    'metacheck::zenodo_info(data.frame(u = "5559007", v = "x"), id_col = 0)',
    f'{zi}(pd.DataFrame({{"u": ["5559007"], "v": ["x"]}}), id_col=0)',
    mock=MK,
)
wrapped(
    "zenodo_info.review.id_col_negative",
    'metacheck::zenodo_info(data.frame(v = "x", u = "5559007"), id_col = -1)',
    f'{zi}(pd.DataFrame({{"v": ["x"], "u": ["5559007"]}}), id_col=-1)',
    mock=MK,
)
wrapped(
    "zenodo_info.review.id_col_too_big",
    'metacheck::zenodo_info(data.frame(u = "5559007"), id_col = 3)',
    f'{zi}(pd.DataFrame({{"u": ["5559007"]}}), id_col=3)',
    mock=MK,
)

# .zenodo_build_metadata(): NA tags are kept; a "" creators field has length 1
case(".zenodo_build_metadata.review.na_tag", ".zenodo_build_metadata",
     f"{ZU}._zenodo_build_metadata",
     {"meta": {"title": "T", "tags": {"$expr": {"r": 'c("a", NA)', "py": '["a", None]'}},
               "creators": ""},
      "folder": "/tmp/f"})  # fmt: skip
case(".zenodo_meta_from_folder.review.odd", ".zenodo_meta_from_folder",
     f"{ZU}._zenodo_meta_from_folder",
     {"folder": {"$expr": {
         "r": 'local({ d <- tempfile(); dir.create(file.path(d, "_osf_metadata"), recursive = TRUE); '
              'writeLines(\'{"osf_id": "abcde", "tags": ["a", null, ["b", 1]], '
              '"contributors": {"x": {"name": "N"}, "y": {"family_name": "F", "given_name": "G"}}}\', '
              'file.path(d, "_osf_metadata", "metadata.json")); d })',
         "py": f'{imp("tests.archives_gz.parity_support")}.meta_folder()',
     }}})  # fmt: skip

# zenodo_upload(): user metadata with doubles and an NA, sent as jsonlite writes them
case(
    "zenodo_upload.review.numeric_metadata",
    "zenodo_upload",
    f"{ZU}.zenodo_upload",
    {
        "folders": {"$file": "tests/archives_gz/fixtures/upload/proj_plain"},
        "zenodo_pat": "fake-token",
        "publish": True,
        "metadata": {"$expr": {
            "r": 'list(version = 2, weight = 0.1, count = 3L, tiny = 1e-5, keywords = list("a", NA))',
            "py": '{"version": 2.0, "weight": 0.1, "count": 3, "tiny": 1e-5, "keywords": ["a", None]}',
        }},
        "as_zip": False,
        "ask": False,
    },
    mock=MK,
)  # fmt: skip

# github_info() on several repositories passes github_repo()'s vector (or, with a
# missing one, list) on to every function
case("github_info.review.vector_dup", "github_info", f"{GH}.github_info",
     {"repo": {"$chr": ["rv/files", "https://github.com/rv/files"]}}, mock=MK)  # fmt: skip
case("github_info.review.vector_missing", "github_info", f"{GH}.github_info",
     {"repo": {"$chr": ["rv/files", "rv/nope"]}}, mock=MK)  # fmt: skip


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
out = {Q("area"): Q("archives_gz_review"), Q("cases"): quote_all(cases)}
text = yaml.dump(out, sort_keys=False, allow_unicode=True, width=100)
(ROOT / "parity/cases/archives_gz_review.yaml").write_text(text, encoding="utf-8")
print(len(cases), "cases")
