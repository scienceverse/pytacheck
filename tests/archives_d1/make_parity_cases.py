"""Generate parity/cases/archives_d1.yaml (R and Python snippets side by side).

Run from the repository root: ``python -m tests.archives_d1.make_parity_cases``,
then ``python -m parity generate --area archives_d1``. Cases whose id contains
``.mock.`` run on both sides against ``tests/archives_d1/mocks`` (see
``make_mocks.py``) with ``online()`` mocked; the Python side of those cases is
``tests.archives_d1.parity_support.run``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "archives_d1.yaml"


class Dumper(yaml.SafeDumper):
    pass


def _str(d: yaml.SafeDumper, s: str) -> yaml.Node:
    return d.represent_scalar("tag:yaml.org,2002:str", s, style='"')


Dumper.add_representer(str, _str)

MOCK = "tests/archives_d1/mocks"
RUN = "tests.archives_d1.parity_support.run"
cases: list[dict[str, Any]] = []


def rq(s: str) -> str:
    """An R string literal."""
    return json.dumps(s)


def r_chr(values: list[str | None]) -> str:
    return "c(" + ", ".join("NA" if v is None else rq(v) for v in values) + ")"


def py_list(values: list[Any]) -> str:
    return "[" + ", ".join("None" if v is None else json.dumps(v) for v in values) + "]"


def fn_case(
    id_: str, r: str, py: str, args: dict[str, Any], compare: dict[str, Any] | None = None
) -> None:
    c: dict[str, Any] = {"id": id_, "r": r, "py": py, "args": args}
    if compare:
        c["compare"] = compare
    cases.append(c)


def expr_case(
    id_: str, r: str, py: str, mock: bool = True, compare: dict[str, Any] | None = None
) -> None:
    c: dict[str, Any] = {
        "id": id_,
        "r": "identity",
        "py": RUN,
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }
    if mock:
        c["mock_dir"] = MOCK
    if compare:
        c["compare"] = compare
    cases.append(c)


def online(r_call: str) -> str:
    return (
        f"testthat::with_mocked_bindings({r_call}, online = function(...) TRUE, "
        '.package = "metacheck")'
    )


def tmp_r(body: str) -> str:
    return (
        "(function(f) { d <- tempfile(); dir.create(d); on.exit(unlink(d, recursive = TRUE)); "
        f"f(d) }})(function(d) {{ {body} }})"
    )


def tmp_py(body: str) -> str:
    return f"lambda m: m.tmp(lambda d: {body})"


IGNORE_PID = {"ignore": ["paper_id"]}

# ---------------------------------------------------------------- allowlists
for r, py in [
    ("metacheck:::.dryad_doi_prefixes", "metacheck.archives.dryad._dryad_doi_prefixes"),
    ("metacheck:::.figshare_vanity_hosts", "metacheck.archives.figshare._figshare_vanity_hosts"),
    (
        "metacheck:::.figshare_doi_prefix_hosts",
        "metacheck.archives.figshare._figshare_doi_prefix_hosts",
    ),
    ("metacheck:::.figshare_host_regex", "metacheck.archives.figshare._figshare_host_regex"),
    ("metacheck:::.dataverse_hosts", "metacheck.archives.dataverse._dataverse_hosts"),
    ("metacheck:::.dataverse_host_regex", "metacheck.archives.dataverse._dataverse_host_regex"),
    (
        "metacheck:::.dataverse_doi_prefix_hosts",
        "metacheck.archives.dataverse._dataverse_doi_prefix_hosts",
    ),
    (
        "metacheck:::.dataverse_doi_prefix_regex",
        "metacheck.archives.dataverse._dataverse_doi_prefix_regex",
    ),
]:
    fn_case(r.split(":::")[1], r, py, {})

# ---------------------------------------------------------------- .dryad_doi
DRYAD_URLS = [
    "https://doi.org/10.5061/dryad.j1fd7",
    "http://datadryad.org/stash/dataset/doi:10.5061/dryad.j1fd7",
    "https://datadryad.org/stash/dataset/doi%3A10.5061%2Fdryad.J1FD7",
    "10.5061/DRYAD.ABC",
    "  10.25338/B8N33J  ",
    "https://doi.org/10.5068/D14671",
    "https://doi.org/10.6075/j0w37ttw",
    "https://doi.org/10.7941/d18907",
    "doi:10.15146/R3XQ19",
    "https://doi.org/10.9999/unrelated123",
    "",
    None,
    "10.5061/dryad",
    "datadryad.org/dataset/doi:10.5061/dryad.x1y2z3/",
    "10.253380/xyz",
    "see abc%4gdef 10.5061/dryad.q1",
    "10.5061/dryad.q2%zz",
    "https://doi.org/10.25349/D9WP4K and 10.5061/dryad.second",
]
fn_case(
    ".dryad_doi.shapes",
    "metacheck:::.dryad_doi",
    "metacheck.archives.dryad._dryad_doi",
    {"dryad_url": {"$expr": {"r": r_chr(DRYAD_URLS), "py": py_list(DRYAD_URLS)}}},
)
fn_case(
    ".dryad_doi.single",
    "metacheck:::.dryad_doi",
    "metacheck.archives.dryad._dryad_doi",
    {"dryad_url": "https://doi.org/10.5061/dryad.j1fd7"},
)
fn_case(
    ".dryad_doi.empty",
    "metacheck:::.dryad_doi",
    "metacheck.archives.dryad._dryad_doi",
    {"dryad_url": {"$chr": []}},
)
fn_case(
    ".dryad_doi.null",
    "metacheck:::.dryad_doi",
    "metacheck.archives.dryad._dryad_doi",
    {"dryad_url": {"$null": True}},
)
fn_case(
    ".dryad_doi.embedded_nul_error",
    "metacheck:::.dryad_doi",
    "metacheck.archives.dryad._dryad_doi",
    {"dryad_url": "10.5061/dryad.a%zzb"},
)

# ---------------------------------------------------------------- .figshare_id
FS_URLS = [
    "18093368",
    "https://figshare.com/articles/dataset/some_title/18093368",
    "https://figshare.com/articles/18093368",
    "https://doi.org/10.6084/m9.figshare.18093368",
    "10.6084/m9.figshare.18093368.v1",
    "https://ndownloader.figshare.com/files/12345",
    "https://figshare.com/projects/some_project/133332",
    "https://figshare.com/s/5e01cc0cae4cf3e2e14f",
    "not-a-figshare-url",
    "",
    "https://figshare.com/articles/PxW_dataset/6934484",
    "https://doi.org/10.26180/19095317.v1",
    "https://doi.org/10.26188/14122688",
    "https://doi.org/10.25375/uct.14618526.v1",
    "https://tandf.figshare.com/articles/journal_contribution/Title/12345678",
    "https://figshare.le.ac.uk/articles/dataset/x/21234567",
    "https://figsh.com/articles/123",
    "https://doi.org/10.6084/m9.figshare.c.1234567",
    "https://doi.org/10.26180/19095317.v1.",
    "https://doi.org/10.17633/rd.brunel.1234567",
    " 18093368 ",
    "https://FIGSHARE.COM/ARTICLES/DATASET/X/123",
    "https://figshare.com/articles/dataset/some_title/18093368/2",
    "https://ndownloader.figshare.com/files/12345?private_link=abc",
    "https://sub.figshare.shef.ac.uk/articles/media/Name/99",
    "https://doi.org/10.25375/19095317x",
    None,
]
fn_case(
    ".figshare_id.shapes",
    "metacheck:::.figshare_id",
    "metacheck.archives.figshare._figshare_id",
    {"figshare_url": {"$expr": {"r": r_chr(FS_URLS), "py": py_list(FS_URLS)}}},
)
fn_case(
    ".figshare_id.numeric",
    "metacheck:::.figshare_id",
    "metacheck.archives.figshare._figshare_id",
    {"figshare_url": {"$dbl": [18093368]}},
)
fn_case(
    ".figshare_id.null",
    "metacheck:::.figshare_id",
    "metacheck.archives.figshare._figshare_id",
    {"figshare_url": {"$null": True}},
)
PROJ_URLS = [
    "https://figshare.com/projects/PERICLES_-_Heritage_values/133332",
    "https://figshare.com/projects/A_Project_With-Punctuation.In.It/999",
    "https://figshare.com/articles/dataset/some_title/18093368",
    "not-a-figshare-url",
    "",
    " https://figshare.com/projects/x/1/ ",
    "https://figshare.com/projects/x/y/1",
    "https://figshare.le.ac.uk/projects/Leicester/77",
    "https://FIGSHARE.com/Projects/Name/5",
    None,
]
fn_case(
    ".figshare_project_id.shapes",
    "metacheck:::.figshare_project_id",
    "metacheck.archives.figshare._figshare_project_id",
    {"figshare_url": {"$expr": {"r": r_chr(PROJ_URLS), "py": py_list(PROJ_URLS)}}},
)

# ---------------------------------------------------------------- .dataverse_parse
DV_TEST = [
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123",
    "https://dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/XYZ999",
    "https://dataverse.harvard.edu/dataset.xhtml?id=12345",
    "https://notdataverse.com/foo",
    None,
]
fn_case(
    ".dataverse_parse.testthat",
    "metacheck:::.dataverse_parse",
    "metacheck.archives.dataverse._dataverse_parse",
    {"url": {"$expr": {"r": r_chr(DV_TEST), "py": py_list(DV_TEST)}}},
)
DV_SHIFT = [
    "https://doi.org/10.18167/DVN1/T0DMFJ",
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/X",
    None,
    "",
    "https://dataverse.nl/x",
]
fn_case(
    ".dataverse_parse.realignment_quirk",
    "metacheck:::.dataverse_parse",
    "metacheck.archives.dataverse._dataverse_parse",
    {"url": {"$expr": {"r": r_chr(DV_SHIFT), "py": py_list(DV_SHIFT)}}},
)
DV_MORE = [
    "https://doi.org/10.18167/DVN1/T0DMFJ.",
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123&version=1.0",
    "https://dataverse.harvard.edu/citation?persistentId=doi:10.7910/DVN/X%2FY",
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi%3A10.7910%2FDVN%2FZ",
    "HTTPS://DATAVERSE.HARVARD.EDU/dataset.xhtml?PERSISTENTID=DOI:10.7910/DVN/UP",
    "https://dataverse.northwestern.edu/x",
    "https://doi.org/10.17026/dans-abc",
    "https://doi.org/10.9999/x",
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/OE9BNP.",
    "https://borealisdata.ca/dataset.xhtml?persistentId=hdl:1880/1234",
    "https://www.sodha.be/dataset.xhtml?persistentId=doi:10.34934/DVN/ABC",
    "https://doi.org/10.6084/m9.figshare.123",
]
fn_case(
    ".dataverse_parse.shapes",
    "metacheck:::.dataverse_parse",
    "metacheck.archives.dataverse._dataverse_parse",
    {"url": {"$expr": {"r": r_chr(DV_MORE), "py": py_list(DV_MORE)}}},
)
fn_case(
    ".dataverse_parse.empty",
    "metacheck:::.dataverse_parse",
    "metacheck.archives.dataverse._dataverse_parse",
    {"url": {"$chr": []}},
)
fn_case(
    ".dataverse_parse.all_missing",
    "metacheck:::.dataverse_parse",
    "metacheck.archives.dataverse._dataverse_parse",
    {"url": {"$expr": {"r": 'c(NA, "")', "py": '[None, ""]'}}},
)
HOST_DOIS = [
    "10.18167/DVN1/T0DMFJ",
    "10.7910/DVN/ABC123",
    "10.9999/unrelated123",
    "10.6084/m9.figshare.1",
    "10.17026/x",
    "10.48733/y",
    "10.58032/z",
    None,
    "",
    "doi:10.7910/DVN/X",
    "10.26300/abc",
]
fn_case(
    ".dataverse_host_from_doi.shapes",
    "metacheck:::.dataverse_host_from_doi",
    "metacheck.archives.dataverse._dataverse_host_from_doi",
    {"doi": {"$expr": {"r": r_chr(HOST_DOIS), "py": py_list(HOST_DOIS)}}},
)

# ---------------------------------------------------------------- *_links
PSYCHSCI = {"$read": ["upstream/metacheck/tests/testthat/fixtures/psychsci"]}
LINK_FNS = [
    ("dryad_links", "dryad"),
    ("figshare_links", "figshare"),
    ("dataverse_links", "dataverse"),
]
for name, mod in LINK_FNS:
    py = f"metacheck.archives.{mod}.{name}"
    fn_case(f"{name}.demo", name, py, {"paper": {"$paper": "demo"}})
    fn_case(f"{name}.psychsci", name, py, {"paper": PSYCHSCI})
    fn_case(
        f"{name}.none",
        name,
        py,
        {
            "paper": {
                "$test_paper": {
                    "text": ["No links here", "osf.io/abcde"],
                    "url": ["https://osf.io/abcde"],
                }
            }
        },
        IGNORE_PID,
    )

PROBLEM = "upstream/metacheck/tests/testthat/fixtures/problems/203020.json"
for name, mod in LINK_FNS:
    py = f"metacheck.archives.{mod}.{name}"
    fn_case(f"{name}.problem_203020", name, py, {"paper": {"$paper": PROBLEM}})
    fn_case(
        f"{name}.paperlist",
        name,
        py,
        {
            "paper": {
                "$read": [
                    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
                    PROBLEM,
                ]
            }
        },
    )

fn_case(
    "dryad_links.test_paper",
    "dryad_links",
    "metacheck.archives.dryad.dryad_links",
    {
        "paper": {
            "$test_paper": {
                "text": [
                    "Data are at https://doi.org/10.5061/dryad.j1fd7 and "
                    "datadryad.org/stash/dataset/doi:10.5061/dryad.xyz/.",
                    "Also doi:10.25338/B8N33J, 10.7941/D18907 and 10.9999/unrelated.",
                    "Dryad: https://datadryad.org/dataset/doi%3A10.5061%2Fdryad.enc and "
                    "www.datadryad.org/datasets/doi:10.15146/R3.",
                    "No DOI 10.5061/dryad here, but 10.5061/DRYAD.UPPER is one.",
                    "Twice: 10.5061/dryad.j1fd7 10.5061/dryad.j1fd7",
                ],
                "url": [
                    "https://doi.org/10.5061/dryad.j1fd7",
                    "https://doi.org/10.5061/dryad.j1fd7/",
                    "https://datadryad.org/stash/dataset/doi:10.5061/dryad.abc12",
                    "https://doi.org/10.6075/J0W37TTW//",
                    "https://doi.org/10.9999/unrelated123",
                    "https://example.org/datadryad.org",
                ],
            }
        },
    },
    IGNORE_PID,
)
ALT = [
    "10.15146", "10.25338", "10.25349", "10.5068", "10.6071", "10.6075", "10.6076",
    "10.6078", "10.6086", "10.7272", "10.7280", "10.7291", "10.7941",
]  # fmt: skip
fn_case(
    "dryad_links.all_prefixes",
    "dryad_links",
    "metacheck.archives.dryad.dryad_links",
    {
        "paper": {
            "$test_paper": {
                "url": ["https://doi.org/10.5061/dryad.j1fd7"]
                + [f"https://doi.org/{a}/ABC123" for a in ALT]
                + ["https://doi.org/10.9999/unrelated123"]
            }
        },
    },
    IGNORE_PID,
)
fn_case(
    "figshare_links.test_paper",
    "figshare_links",
    "metacheck.archives.figshare.figshare_links",
    {
        "paper": {
            "$test_paper": {
                "text": [
                    "Data from: ... Monash University. Dataset, https://doi.org/10.26180/19095317.v1.",
                    "See figshare.com/articles/dataset/Title_here/18093368 and "
                    "10.6084/m9.figshare.6934484.v2 too.",
                    "Share link https://figshare.com/s/5e01cc0cae4cf3e2e14f and project "
                    "figshare.com/projects/P/133332.",
                    "Vanity https://figshare.le.ac.uk/articles/dataset/x/21234567/1 and "
                    "tandf.figshare.com/ndownloader/files/1.",
                    "Institutional 10.25375/uct.14618526.v1 and 10.17633/rd.brunel.1234567 "
                    "plus 10.60842/abc.",
                ],
                "url": [
                    "https://figshare.com/articles/dataset/some_title/18093368",
                    "https://figshare.com/projects/PERICLES_-_Heritage_values/133332",
                    "https://figshare.com/s/5e01cc0cae4cf3e2e14f",
                    "https://osf.io/abcde",
                    "https://doi.org/10.6084/m9.figshare.18093368.v1/",
                    "https://doi.org/10.60847/unrelated",
                    "https://ndownloader.figshare.com/files/12345",
                ],
            }
        },
    },
    IGNORE_PID,
)
fn_case(
    "dataverse_links.test_paper",
    "dataverse_links",
    "metacheck.archives.dataverse.dataverse_links",
    {
        "paper": {
            "$test_paper": {
                "text": [
                    "Data are available on the CIRAD Dataverse: https://doi.org/10.18167/DVN1/T0DMFJ.",
                    "Also see https://doi.org/10.9999/unrelated123 for something unrelated.",
                    "dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123 is bare.",
                    "www.dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/XYZ999/ and "
                    "10.7910/DVN/ABC123",
                    "Figshare DOIs look like 10.6084/m9.figshare.123 (listed for dataverse.no).",
                ],
                "url": [
                    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123",
                    "https://dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/XYZ999",
                    "https://osf.io/abcde",
                    "https://DATAVERSE.UNC.EDU/dataset.xhtml?persistentId=doi:10.15139/S3/X./",
                    "https://dataverse.harvard.edu/dataset.xhtml?id=12345",
                ],
            }
        },
    },
    IGNORE_PID,
)

# ---------------------------------------------------------------- *_info (mocked)
DRYAD_INFO_IN = [
    "10.5061/dryad.j1fd7",
    "https://doi.org/10.5061/dryad.nofiles",
    "10.5061/dryad.notjson",
    "10.5061/dryad.missing",
    "nope",
    None,
    "10.5061/dryad.j1fd7",
]
expr_case(
    "dryad_info.mock.vector",
    online(f"dryad_info({r_chr(DRYAD_INFO_IN)})"),
    f"lambda m: m.dryad.dryad_info({py_list(DRYAD_INFO_IN)})",
)
expr_case(
    "dryad_info.mock.links_table",
    online(
        'dryad_info(dryad_links(test_paper(text = "see 10.5061/dryad.j1fd7 and 10.5061/dryad.missing", '
        'url = c("https://doi.org/10.5061/dryad.j1fd7/", "https://doi.org/10.25338/B8N33J"))))'
    ),
    'lambda m: m.dryad.dryad_info(m.dryad.dryad_links(__import__("metacheck").test_paper('
    '["see 10.5061/dryad.j1fd7 and 10.5061/dryad.missing"], '
    '["https://doi.org/10.5061/dryad.j1fd7/", "https://doi.org/10.25338/B8N33J"])))',
    compare=IGNORE_PID,
)
expr_case(
    "dryad_info.mock.id_col_name",
    online(
        'dryad_info(data.frame(n = 1:3, u = c("10.5061/dryad.j1fd7", NA, "x"), '
        'dryad_doi = "stale"), id_col = "u")'
    ),
    'lambda m: m.dryad.dryad_info(__import__("pandas").DataFrame({"n": [1, 2, 3], '
    '"u": ["10.5061/dryad.j1fd7", None, "x"], "dryad_doi": "stale"}), id_col="u")',
)
expr_case(
    "dryad_info.mock.id_col_position",
    online(
        'dryad_info(data.frame(n = 1:3, u = c("10.5061/dryad.j1fd7", "x", "10.5061/dryad.j1fd7")), '
        "id_col = 2)"
    ),
    'lambda m: m.dryad.dryad_info(pd.DataFrame({"n": [1, 2, 3], '
    '"u": ["10.5061/dryad.j1fd7", "x", "10.5061/dryad.j1fd7"]}), id_col=2)',
)
expr_case(
    "figshare_info.mock.id_col_na_error",
    online('figshare_info(data.frame(u = c("18093368", NA)))'),
    'lambda m: m.figshare.figshare_info(pd.DataFrame({"u": ["18093368", None]}))',
)
expr_case(
    "figshare_info.mock.id_col_na_duplicated",
    online('figshare_info(data.frame(u = c(NA, "18093368", NA)))'),
    'lambda m: m.figshare.figshare_info(pd.DataFrame({"u": [None, "18093368", None]}))',
)
expr_case(
    "dataverse_info.mock.id_col_na",
    online(
        "dataverse_info(data.frame(u = c("
        '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123", NA), '
        'dataverse_host = "stale"))'
    ),
    'lambda m: m.dataverse.dataverse_info(pd.DataFrame({"u": ['
    '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123", None], '
    '"dataverse_host": "stale"}))',
)
expr_case(
    "dryad_info.mock.no_valid",
    online('dryad_info(c("https://osf.io/abcde", "nope"))'),
    'lambda m: m.dryad.dryad_info(["https://osf.io/abcde", "nope"])',
)
expr_case(
    "dryad_info.mock.empty", online("dryad_info(character(0))"), "lambda m: m.dryad.dryad_info([])"
)

FS_INFO_IN = [
    "https://figshare.com/projects/PERICLES_-_Heritage_values/133332",
    "https://doi.org/10.6084/m9.figshare.18093368.v1",
    "1234",
    "https://figshare.com/s/5e01cc0cae4cf3e2e14f",
    "https://figshare.com/projects/Empty/999",
    "https://figshare.com/projects/Gone/404404",
    "https://doi.org/10.26180/19095317.v1",
    None,
    "18093368",
]
expr_case(
    "figshare_info.mock.vector",
    online(f"figshare_info({r_chr(FS_INFO_IN)})"),
    f"lambda m: m.figshare.figshare_info({py_list(FS_INFO_IN)})",
)
expr_case(
    "figshare_info.mock.project",
    online('figshare_info("https://figshare.com/projects/PERICLES_-_Heritage_values/133332")'),
    "lambda m: m.figshare.figshare_info("
    '"https://figshare.com/projects/PERICLES_-_Heritage_values/133332")',
)
expr_case(
    "figshare_info.mock.links_table",
    online(
        "figshare_info(figshare_links(test_paper(url = c("
        '"https://figshare.com/articles/dataset/some_title/18093368", '
        '"https://figshare.com/projects/PERICLES_-_Heritage_values/133332", '
        '"https://figshare.com/s/5e01cc0cae4cf3e2e14f"))))'
    ),
    'lambda m: m.figshare.figshare_info(m.figshare.figshare_links(__import__("metacheck").test_paper('
    'None, ["https://figshare.com/articles/dataset/some_title/18093368", '
    '"https://figshare.com/projects/PERICLES_-_Heritage_values/133332", '
    '"https://figshare.com/s/5e01cc0cae4cf3e2e14f"])))',
    compare=IGNORE_PID,
)
expr_case(
    "figshare_info.mock.no_valid",
    online('figshare_info(c("https://figshare.com/s/abc", "nope"))'),
    'lambda m: m.figshare.figshare_info(["https://figshare.com/s/abc", "nope"])',
)
expr_case(
    "figshare_info.mock.numeric",
    online("figshare_info(6934484)"),
    "lambda m: m.figshare.figshare_info(6934484.0)",
)

DV_INFO_IN = [
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123",
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/NOPE",
    "https://dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/XYZ999",
    "https://doi.org/10.18167/DVN1/T0DMFJ",
    "https://dataverse.harvard.edu/dataset.xhtml?id=12345",
    "https://osf.io/abcde",
    None,
]
expr_case(
    "dataverse_info.mock.vector",
    online(f"dataverse_info({r_chr(DV_INFO_IN)})"),
    f"lambda m: m.dataverse.dataverse_info({py_list(DV_INFO_IN)})",
)
expr_case(
    "dataverse_info.mock.links_table",
    online(
        'dataverse_info(dataverse_links(test_paper(text = "see https://doi.org/10.18167/DVN1/T0DMFJ.", '
        "url = c("
        '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123", '
        '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/NOPE"))))'
    ),
    "lambda m: m.dataverse.dataverse_info(m.dataverse.dataverse_links("
    '__import__("metacheck").test_paper('
    '["see https://doi.org/10.18167/DVN1/T0DMFJ."], '
    '["https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123", '
    '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/NOPE"])))',
    compare=IGNORE_PID,
)
expr_case(
    "dataverse_info.mock.no_valid",
    online('dataverse_info(c("https://dataverse.harvard.edu/dataset.xhtml?id=1", "x"))'),
    "lambda m: m.dataverse.dataverse_info("
    '["https://dataverse.harvard.edu/dataset.xhtml?id=1", "x"])',
)
expr_case(
    "dataverse_info.mock.string_license_error",
    online('dataverse_info("https://dataverse.no/dataset.xhtml?persistentId=doi:10.18710/OLDLIC")'),
    "lambda m: m.dataverse.dataverse_info("
    '"https://dataverse.no/dataset.xhtml?persistentId=doi:10.18710/OLDLIC")',
)
expr_case(
    "dataverse_info.mock.duplicate_dataset",
    online(
        "dataverse_info(c("
        '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123", '
        '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123."))'
    ),
    "lambda m: m.dataverse.dataverse_info(["
    '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123", '
    '"https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/ABC123."])',
)


# ---------------------------------------------------------------- *_file_download (mocked)
def dl_case(id_: str, r_body: str, py_body: str) -> None:
    expr_case(id_, tmp_r(r_body), tmp_py(py_body))


dl_case(
    "dryad_file_download.mock.default",
    'dryad_file_download("https://doi.org/10.5061/dryad.j1fd7", download_to = d)',
    'm.dryad.dryad_file_download("https://doi.org/10.5061/dryad.j1fd7", download_to=d)',
)
dl_case(
    "dryad_file_download.mock.no_caps",
    'dryad_file_download("10.5061/dryad.j1fd7", download_to = d, max_file_size = NULL, '
    "max_download_size = Inf)",
    'm.dryad.dryad_file_download("10.5061/dryad.j1fd7", download_to=d, max_file_size=None, '
    'max_download_size=float("inf"))',
)
dl_case(
    "dryad_file_download.mock.download_cap",
    'dryad_file_download("10.5061/dryad.j1fd7", download_to = d, max_download_size = 0.00001)',
    'm.dryad.dryad_file_download("10.5061/dryad.j1fd7", download_to=d, max_download_size=0.00001)',
)
dl_case(
    "dryad_file_download.mock.all_omitted",
    'dryad_file_download("10.5061/dryad.j1fd7", download_to = d, max_file_size = 0.000001)',
    'm.dryad.dryad_file_download("10.5061/dryad.j1fd7", download_to=d, max_file_size=0.000001)',
)
dl_case(
    "dryad_file_download.mock.no_files",
    'dryad_file_download("10.5061/dryad.nofiles", download_to = d)',
    'm.dryad.dryad_file_download("10.5061/dryad.nofiles", download_to=d)',
)
dl_case(
    "dryad_file_download.mock.missing",
    'dryad_file_download("10.5061/dryad.missing", download_to = d)',
    'm.dryad.dryad_file_download("10.5061/dryad.missing", download_to=d)',
)
dl_case(
    "dryad_file_download.mock.invalid",
    'dryad_file_download(c("nope", NA), download_to = d)',
    'm.dryad.dryad_file_download(["nope", None], download_to=d)',
)
dl_case(
    "dryad_file_download.mock.multi",
    'dryad_file_download(c("10.5061/dryad.j1fd7", "https://doi.org/10.25338/B8N33J", '
    '"10.5061/dryad.missing"), download_to = d)',
    'm.dryad.dryad_file_download(["10.5061/dryad.j1fd7", "https://doi.org/10.25338/B8N33J", '
    '"10.5061/dryad.missing"], download_to=d)',
)
dl_case(
    "dryad_file_download.mock.unzip_types",
    'dryad_file_download("10.25338/B8N33J", download_to = d, unzip_types = "data")',
    'm.dryad.dryad_file_download("10.25338/B8N33J", download_to=d, unzip_types="data")',
)
dl_case(
    "dryad_file_download.mock.folder_exists",
    'dir.create(file.path(d, "10.5061_dryad.j1fd7")); '
    'x <- dryad_file_download("10.5061/dryad.j1fd7", download_to = d); '
    "list(x, sort(list.files(d)))",
    '(__import__("os").mkdir(d + "/10.5061_dryad.j1fd7"), '
    '[m.dryad.dryad_file_download("10.5061/dryad.j1fd7", download_to=d), '
    'sorted(__import__("os").listdir(d))])[1]',
)
dl_case(
    "dryad_file_download.mock.new_folder",
    'dryad_file_download("10.5061/dryad.j1fd7", download_to = file.path(d, "newdir"))',
    'm.dryad.dryad_file_download("10.5061/dryad.j1fd7", download_to=d + "/newdir")',
)

dl_case(
    "figshare_file_download.mock.default",
    'figshare_file_download("https://doi.org/10.6084/m9.figshare.18093368.v1", download_to = d)',
    'm.figshare.figshare_file_download("https://doi.org/10.6084/m9.figshare.18093368.v1", '
    "download_to=d)",
)
dl_case(
    "figshare_file_download.mock.no_caps",
    "figshare_file_download(18093368, download_to = d, max_file_size = Inf, "
    "max_download_size = NULL)",
    'm.figshare.figshare_file_download(18093368.0, download_to=d, max_file_size=float("inf"), '
    "max_download_size=None)",
)
dl_case(
    "figshare_file_download.mock.folder_exists_quirk",
    'dir.create(file.path(d, "figshare_18093368")); '
    'x <- figshare_file_download("18093368", download_to = d); '
    "list(x, sort(list.files(d)))",
    '(__import__("os").mkdir(d + "/figshare_18093368"), '
    '[m.figshare.figshare_file_download("18093368", download_to=d), '
    'sorted(__import__("os").listdir(d))])[1]',
)
dl_case(
    "figshare_file_download.mock.no_files",
    'figshare_file_download("https://figshare.com/articles/PxW_dataset/6934484", download_to = d)',
    'm.figshare.figshare_file_download("https://figshare.com/articles/PxW_dataset/6934484", '
    "download_to=d)",
)
dl_case(
    "figshare_file_download.mock.unfound_and_project",
    'figshare_file_download(c("1234", "https://figshare.com/projects/P/133332"), download_to = d)',
    'm.figshare.figshare_file_download(["1234", "https://figshare.com/projects/P/133332"], '
    "download_to=d)",
)
dl_case(
    "figshare_file_download.mock.multi",
    'figshare_file_download(c("18093368", "19095317", "6934484"), download_to = d)',
    'm.figshare.figshare_file_download(["18093368", "19095317", "6934484"], download_to=d)',
)

dl_case(
    "dataverse_file_download.mock.default",
    'dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/ABC123", download_to = d)',
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/ABC123", '
    "download_to=d)",
)
dl_case(
    "dataverse_file_download.mock.vectorised",
    'dataverse_file_download(c("dataverse.harvard.edu", "dataverse.nl", "dataverse.harvard.edu"), '
    'c("10.7910/DVN/ABC123", "10.34894/XYZ999", "10.7910/DVN/ABC123"), download_to = d)',
    "m.dataverse.dataverse_file_download("
    '["dataverse.harvard.edu", "dataverse.nl", "dataverse.harvard.edu"], '
    '["10.7910/DVN/ABC123", "10.34894/XYZ999", "10.7910/DVN/ABC123"], download_to=d)',
)
dl_case(
    "dataverse_file_download.mock.recycled_host",
    'dataverse_file_download("dataverse.harvard.edu", c("10.7910/DVN/ABC123", "10.7910/DVN/NOPE"), '
    "download_to = d, max_download_size = 0.00001)",
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", '
    '["10.7910/DVN/ABC123", "10.7910/DVN/NOPE"], download_to=d, max_download_size=0.00001)',
)
dl_case(
    "dataverse_file_download.mock.missing_values",
    'list(dataverse_file_download(NA_character_, NA_character_), dataverse_file_download("", "x"), '
    'dataverse_file_download(character(0), "x"))',
    "[m.dataverse.dataverse_file_download([None], [None]), "
    'm.dataverse.dataverse_file_download("", "x"), m.dataverse.dataverse_file_download([], "x")]',
)
dl_case(
    "dataverse_file_download.mock.unfound",
    'dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/NOPE", download_to = d)',
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/NOPE", '
    "download_to=d)",
)
dl_case(
    "dataverse_file_download.mock.string_license_error",
    'dataverse_file_download("dataverse.no", "10.18710/OLDLIC", download_to = d)',
    'm.dataverse.dataverse_file_download("dataverse.no", "10.18710/OLDLIC", download_to=d)',
)
dl_case(
    "dataverse_file_download.mock.error_in_vector",
    'dataverse_file_download(c("dataverse.no", "dataverse.harvard.edu"), '
    'c("10.18710/OLDLIC", "10.7910/DVN/ABC123"), download_to = d)',
    'm.dataverse.dataverse_file_download(["dataverse.no", "dataverse.harvard.edu"], '
    '["10.18710/OLDLIC", "10.7910/DVN/ABC123"], download_to=d)',
)

# ---------------------------------------------------------------- *_verify_downloads
VERIFY_R = (
    'writeLines("x,y", file.path(d, "a.csv")); writeLines("hello", file.path(d, "b.txt")); '
    'dir.create(file.path(d, "sub")); '
    "files <- data.frame(id = as.character(1:9), "
    'key = c("a.csv", "a.csv", "b.txt", "none", "sub", "zip.zip", "b.txt", "a.csv", "b.txt"), '
    'path = c("a.csv", "a.csv", "b.txt", NA, "sub", "zip.zip", "b.txt", "a.csv", "b.txt"), '
    "size = c(4, 4, 99, 1, NA, 100, 6, NA, 6), "
    'checksum = c("043212bb9834e334677e9c9659294bd4", "0123456789abcdef0123456789abcdef", NA, NA, '
    'NA, NA, "b1946ac92492d2347c6235b4d2611184", "043212BB9834E334677E9C9659294BD4", "xyz"), '
    'checksum_type = c("md5", "md5", "md5", NA, NA, "md5", "sha-1", "md5", "md5"), '
    "downloaded = c(TRUE, TRUE, TRUE, TRUE, TRUE, FALSE, TRUE, FALSE, TRUE), "
    "extracted = c(NA, NA, NA, NA, NA, 2L, NA, NA, NA)); "
)
VERIFY_PY = (
    '(m.write_files(d, {"a.csv": "x,y", "b.txt": "hello"}), __import__("os").mkdir(d + "/sub"), '
    'pd.DataFrame({"id": pd.array([str(i) for i in range(1, 10)], dtype="string"), '
    '"key": pd.array(["a.csv", "a.csv", "b.txt", "none", "sub", "zip.zip", "b.txt", "a.csv", '
    '"b.txt"], dtype="string"), '
    '"path": pd.array(["a.csv", "a.csv", "b.txt", None, "sub", "zip.zip", "b.txt", "a.csv", '
    '"b.txt"], dtype="string"), '
    '"size": [4.0, 4.0, 99.0, 1.0, float("nan"), 100.0, 6.0, float("nan"), 6.0], '
    '"checksum": pd.array(["043212bb9834e334677e9c9659294bd4", "0123456789abcdef0123456789abcdef", '
    'None, None, None, None, "b1946ac92492d2347c6235b4d2611184", '
    '"043212BB9834E334677E9C9659294BD4", "xyz"], dtype="string"), '
    '"checksum_type": pd.array(["md5", "md5", "md5", None, None, "md5", "sha-1", "md5", "md5"], '
    'dtype="string"), '
    '"downloaded": pd.array([True, True, True, True, True, False, True, False, True], '
    'dtype="boolean"), '
    '"extracted": pd.array([None, None, None, None, None, 2, None, None, None], dtype="Int64")}))[2]'
)
for mod, fn in [
    ("dryad", ".dryad_verify_downloads"),
    ("figshare", ".figshare_verify_downloads"),
    ("dataverse", ".dataverse_verify_downloads"),
]:
    pyfn = "_" + fn[1:]
    expr_case(
        f"{fn}.table",
        tmp_r(VERIFY_R + f"metacheck:::{fn}(files, d)"),
        tmp_py(f"m.{mod}.{pyfn}({VERIFY_PY}, d)"),
        mock=False,
    )
    expr_case(
        f"{fn}.no_path",
        tmp_r(f'metacheck:::{fn}(data.frame(id = "1", size = 3, downloaded = TRUE), d)'),
        tmp_py(
            f'm.{mod}.{pyfn}(pd.DataFrame({{"id": ["1"], "size": [3.0], "downloaded": [True]}}), d)'
        ),
        mock=False,
    )
    expr_case(
        f"{fn}.empty",
        tmp_r(f"metacheck:::{fn}(data.frame(id = character(0), path = character(0)), d)"),
        tmp_py(
            f'm.{mod}.{pyfn}(pd.DataFrame({{"id": pd.array([], dtype="string"), '
            f'"path": pd.array([], dtype="string")}}), d)'
        ),
        mock=False,
    )

# ---------------------------------------------------------------- tokens
fn_case(
    "dataverse_pat.set_error",
    "dataverse_pat",
    "metacheck.archives.dataverse.dataverse_pat",
    {"host": "dataverse.harvard.edu", "pat": {"$dbl": [123]}},
)
fn_case(
    "figshare_pat.set_error",
    "figshare_pat",
    "metacheck.archives.figshare.figshare_pat",
    {"pat": {"$lgl": [True]}},
)
fn_case(
    "dryad_pat.set_error",
    "dryad_pat",
    "metacheck.archives.dryad.dryad_pat",
    {"pat": {"$dbl": [1]}},
)
fn_case(
    "dryad_auth.no_secret_error",
    "dryad_auth",
    "metacheck.archives.dryad.dryad_auth",
    {"client_id": "abc"},
)
expr_case(
    "dryad_auth.unset",
    '(function() { withr::local_envvar(DRYAD_CLIENT_ID = "", DRYAD_CLIENT_SECRET = ""); '
    "withr::local_options(metacheck.dryad.client_id = NULL, metacheck.dryad.client_secret = NULL); "
    "dryad_auth() })()",
    "lambda m: m.dryad.dryad_auth() if not __import__('os').environ.get('DRYAD_CLIENT_ID') else None",
    mock=False,
)


def main() -> None:
    doc = {"area": "archives_d1", "cases": cases}
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(
            "# Parity cases for R/archive-dryad.R, R/archive-figshare.R, R/archive-dataverse.R.\n"
            "# Generated by tests/archives_d1/make_parity_cases.py; mocked HTTP lives in\n"
            "# tests/archives_d1/mocks (httptest2 format, replayed on both sides).\n"
        )
        yaml.dump(doc, fh, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=10_000)
    print(len(cases), "cases")


if __name__ == "__main__":
    main()
