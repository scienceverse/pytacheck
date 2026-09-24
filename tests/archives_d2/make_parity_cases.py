"""Generate parity/cases/archives_d2.yaml (R and Python snippets side by side).

Run from the repository root: ``python -m tests.archives_d2.make_parity_cases``,
then ``python -m parity generate --area archives_d2``. Cases whose id contains
``.mock.`` run on both sides against recorded responses (``tests/archives_d2/mocks``,
see ``make_mocks.py``, or metacheck's own ``apis`` recordings) with ``online()``
mocked; the Python side of those cases is ``tests.archives_d2.parity_support.run``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "archives_d2.yaml"


class Dumper(yaml.SafeDumper):
    pass


def _str(d: yaml.SafeDumper, s: str) -> yaml.Node:
    return d.represent_scalar("tag:yaml.org,2002:str", s, style='"')


Dumper.add_representer(str, _str)

MOCK = "tests/archives_d2/mocks"
APIS = "apis"
RUN = "tests.archives_d2.parity_support.run"
PKG = "pytacheck.archives"
cases: list[dict[str, Any]] = []
IGNORE_PID = {"ignore": ["paper_id"]}


def rq(s: str) -> str:
    """An R string literal."""
    return json.dumps(s, ensure_ascii=False)


def r_chr(values: list[str | None]) -> str:
    return "c(" + ", ".join("NA" if v is None else rq(v) for v in values) + ")"


def py_list(values: list[Any]) -> str:
    return "[" + ", ".join("None" if v is None else json.dumps(v, ensure_ascii=False) for v in values) + "]"


def fn_case(
    id_: str, r: str, py: str, args: dict[str, Any], compare: dict[str, Any] | None = None
) -> None:
    c: dict[str, Any] = {"id": id_, "r": r, "py": py, "args": args}
    if compare:
        c["compare"] = compare
    cases.append(c)


def expr_case(
    id_: str, r: str, py: str, mock: str | None = MOCK, compare: dict[str, Any] | None = None
) -> None:
    c: dict[str, Any] = {
        "id": id_,
        "r": "identity",
        "py": RUN,
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }
    if mock:
        c["mock_dir"] = mock
    if compare:
        c["compare"] = compare
    cases.append(c)


def online(r_call: str) -> str:
    return (
        f"testthat::with_mocked_bindings({r_call}, online = function(...) TRUE, "
        '.package = "metacheck")'
    )


def offline(r_call: str) -> str:
    return (
        f"testthat::with_mocked_bindings({r_call}, online = function(...) FALSE, "
        '.package = "metacheck")'
    )


def tmp_r(body: str) -> str:
    return (
        "(function(f) { d <- tempfile(); dir.create(d); on.exit(unlink(d, recursive = TRUE)); "
        f"f(d) }})(function(d) {{ {body} }})"
    )


def tmp_py(body: str) -> str:
    return f"lambda m: m.tmp(lambda d: {body})"


def tp_r(text: list[str] | None = None, url: list[str] | None = None) -> str:
    args = []
    if text is not None:
        args.append(f"text = {r_chr(text)}")
    if url is not None:
        args.append(f"url = {r_chr(url)}")
    return f"test_paper({', '.join(args)})"


def tp_py(text: list[str] | None = None, url: list[str] | None = None) -> str:
    t = py_list(text) if text is not None else "None"
    u = py_list(url) if url is not None else "[]"
    return f"m.pc.test_paper({t}, {u})"


def links_cases(name: str, module: str, text: list[str], url: list[str]) -> None:
    """``<name>(test_paper(text, url))``, a no-match paper, the demo paper and psychsci."""
    expr_case(
        f"{name}.mixed",
        f"{name}({tp_r(text, url)})",
        f"lambda m: m.{module}.{name}({tp_py(text, url)})",
        mock=None,
        compare=IGNORE_PID,
    )
    expr_case(
        f"{name}.none",
        f"{name}({tp_r(['Nothing to see here.'], ['https://osf.io/abcde'])})",
        f"lambda m: m.{module}.{name}({tp_py(['Nothing to see here.'], ['https://osf.io/abcde'])})",
        mock=None,
        compare=IGNORE_PID,
    )
    fn_case(f"{name}.demo", name, f"{PKG}.{module}.{name}", {"paper": {"$paper": "demo"}})
    fn_case(
        f"{name}.psychsci",
        name,
        f"{PKG}.{module}.{name}",
        {"paper": {"$read": ["upstream/metacheck/tests/testthat/fixtures/psychsci"]}},
    )


def vec_case(id_: str, r_fn: str, py_fn: str, values: list[str | None]) -> None:
    """``r_fn(c(...))`` / ``py_fn([...])``, plus a single-value and an empty-vector case."""
    fn_case(
        f"{id_}.shapes",
        r_fn,
        py_fn,
        {"x": {"$expr": {"r": r_chr(values), "py": py_list(values)}}},
    )
    cases[-1]["args"] = {ARG[r_fn]: cases[-1]["args"]["x"]}
    fn_case(f"{id_}.single", r_fn, py_fn, {ARG[r_fn]: next(v for v in values if v)})
    fn_case(f"{id_}.empty", r_fn, py_fn, {ARG[r_fn]: {"$chr": []}})
    fn_case(f"{id_}.null", r_fn, py_fn, {ARG[r_fn]: {"$null": True}})
    if not r_fn.endswith("_parse"):  # Python's None is R's NULL, not a length-1 NA
        fn_case(f"{id_}.na", r_fn, py_fn, {ARG[r_fn]: {"$NA": True}})


ARG = {
    "metacheck:::.dataone_host": "dataone_url",
    "metacheck:::.dataone_pid": "dataone_url",
    "metacheck:::.researchdata4tu_id": "researchdata4tu_url",
    "metacheck:::.reshare_id": "reshare_url",
    "metacheck:::.mendeley_id": "mendeley_url",
    "metacheck:::.dspace_legacy_parse": "url",
    "metacheck:::.dspace7_parse": "url",
}

# ---------------------------------------------------------------- allowlists
for r, py in [
    ("metacheck:::.dataone_hosts", f"{PKG}.dataone._dataone_hosts"),
    ("metacheck:::.dataone_host_regex", f"{PKG}.dataone._dataone_host_regex"),
    ("metacheck:::.dspace7_hosts", f"{PKG}.dspace7._dspace7_hosts"),
    ("metacheck:::.dspace7_host_regex", f"{PKG}.dspace7._dspace7_host_regex"),
    ("metacheck:::.dspace_legacy_hosts", f"{PKG}.psycharchives._dspace_legacy_hosts"),
    ("metacheck:::.dspace_legacy_host_regex", f"{PKG}.psycharchives._dspace_legacy_host_regex"),
    ("metacheck:::.rbox_headers", f"{PKG}.researchbox._rbox_headers"),
]:
    fn_case(r.split(":::")[1], r, py, {})

# ================================================================= DataONE
DATAONE_URLS = [
    "https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86",
    "https://doi.org/10.18739/A2GT5FG86",
    "10.5063/PG1Q4B",
    "https://knb.ecoinformatics.org/view/doi:10.18739/X1",
    "https://doi.org/10.6085/AA/marine_ltm.20.1",
    "https://metacat.tfri.gov.tw/metacat/d1/mn/v2/object/abc",
    "https://smithsonian.dataone.org/view/doi:10.1/x",
    "https://example.org/10.9999/x",
    "",
    None,
    "  10.5063/ABC  ",
    "ARCTICDATA.IO/view/doi:10.1/Y",
    "https://arcticdata.io/metacat/d1/mn/v2/view/doi:10.18739/A2GT5FG86/extra",
    "DOI:10.18739/abc",
    "https://doi.org/10.18739/A2GT5FG86.",
    "https://doi.org/10.18739/A2GT5FG86?x=1",
    "no doi here",
    "doi:10.5063/F1Z60M87 and 10.18739/second",
]
vec_case(".dataone_host", "metacheck:::.dataone_host", f"{PKG}.dataone._dataone_host", DATAONE_URLS)
vec_case(".dataone_pid", "metacheck:::.dataone_pid", f"{PKG}.dataone._dataone_pid", DATAONE_URLS)

links_cases(
    "dataone_links",
    "dataone",
    [
        "Data at https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86 and doi.org/10.5063/PG1Q4B.",
        "Also knb.ecoinformatics.org/view/doi:10.5063/F1Z60M87) and 10.6085/AA/marine_ltm.20.1 here.",
        "Unrelated 10.1234/abc and ARCTICDATA.IO/catalog/view/doi:10.18739/UPPER",
    ],
    [
        "https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86/",
        "https://doi.org/10.6085/AA/marine_ltm.20.1",
        "https://osf.io/abcde",
        "https://knb.ecoinformatics.org/view/doi:10.5063/F1Z60M87",
    ],
)
pisco = "uploaded to DataONE and are accessible here: https://doi.org/10.6085/AA/marine_ltm.20.1"
expr_case(
    "dataone_links.pisco",
    f"dataone_links({tp_r([pisco])})",
    f"lambda m: m.dataone.dataone_links({tp_py([pisco])})",
    mock=None,
    compare=IGNORE_PID,
)

D1_VEC = [
    "https://doi.org/10.18739/A2GT5FG86",
    "https://arcticdata.io/catalog/view/doi:10.18739/A2GT5FG86",
    "10.6085/AA/marine_ltm.20.1",
    "10.5063/NOTEML",
    "10.5063/BROKEN",
    "10.18739/MISSING",
    "https://example.org/x",
    None,
    "https://doi.org/10.18739/A2GT5FG86",
]
expr_case(
    "dataone_info.mock.vector",
    online(f"dataone_info({r_chr(D1_VEC)})"),
    f"lambda m: m.dataone.dataone_info({py_list(D1_VEC)})",
)
expr_case(
    "dataone_info.mock.links_table",
    online(
        "dataone_info(dataone_links("
        + tp_r(["see 10.18739/A2GT5FG86 and 10.5063/NOTEML"], ["https://doi.org/10.6085/AA/marine_ltm.20.1/"])
        + "))"
    ),
    "lambda m: m.dataone.dataone_info(m.dataone.dataone_links("
    + tp_py(["see 10.18739/A2GT5FG86 and 10.5063/NOTEML"], ["https://doi.org/10.6085/AA/marine_ltm.20.1/"])
    + "))",
    compare=IGNORE_PID,
)
expr_case(
    "dataone_info.mock.none_valid",
    online('dataone_info(c("https://example.org", NA, "https://example.org"))'),
    'lambda m: m.dataone.dataone_info(["https://example.org", None, "https://example.org"])',
)
expr_case(
    "dataone_info.mock.id_col_na_error",
    online('dataone_info(data.frame(u = c("10.18739/A2GT5FG86", NA)))'),
    'lambda m: m.dataone.dataone_info(m.pd.DataFrame({"u": ["10.18739/A2GT5FG86", None]}))',
)
expr_case(
    "dataone_info.mock.id_col_name",
    online(
        'dataone_info(data.frame(n = 1:3, u = c("10.18739/A2GT5FG86", "x", "10.18739/A2GT5FG86"), '
        'dataone_pid = "stale", dataone_url = "old"), id_col = "u")'
    ),
    'lambda m: m.dataone.dataone_info(m.pd.DataFrame({"n": [1, 2, 3], '
    '"u": ["10.18739/A2GT5FG86", "x", "10.18739/A2GT5FG86"], "dataone_pid": "stale", '
    '"dataone_url": "old"}), id_col="u")',
)
expr_case(
    "dataone_info.mock.offline_error",
    offline('dataone_info("10.18739/A2GT5FG86")'),
    'lambda m: m.offline(lambda: m.dataone.dataone_info("10.18739/A2GT5FG86"))',
)
fn_case(
    ".dataone_info.unknown_host",
    "metacheck:::.dataone_info",
    f"{PKG}.dataone._dataone_info",
    {"pid": "doi:10.1/x", "host": "example.org"},
)
for pid, host, label in [
    ("doi:10.18739/A2GT5FG86", "arcticdata.io", "eml"),
    ("doi:10.6085/AA/marine_ltm.20.1", "data.piscoweb.org", "minimal"),
    ("doi:10.5063/NOTEML", "knb.ecoinformatics.org", "not_eml"),
    ("doi:10.5063/BROKEN", "knb.ecoinformatics.org", "not_xml"),
    ("doi:10.18739/MISSING", "arcticdata.io", "unfound"),
]:
    expr_case(
        f".dataone_info.mock.{label}",
        f"metacheck:::.dataone_info({rq(pid)}, host = {rq(host)})",
        f"lambda m: m.dataone._dataone_info({rq(pid)}, host={rq(host)})",
    )

# ================================================================= DSpace 7
GT = "bac086e5-c606-474b-af1e-4a6122694af5"
UM = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DS7_URLS = [
    "https://repository.gatech.edu/entities/publication/BAC086E5-C606-474B-AF1E-4A6122694AF5",
    "https://repository.gatech.edu/handle/1853/67239.",
    "http://hdl.handle.net/1853/67239",
    "https://www.research-collection.ethz.ch/handle/20.500.11850/12345;",
    "https://open.uni-marburg.de/handle/1/2/3",
    "https://tam-datahub.online.uni-marburg.de/items/x",
    "https://XDL.GI.DE/foo",
    "",
    None,
    "https://example.org/handle/123/456",
    "https://dspace.ut.ee/handle/10062/1.2.3/x",
]
vec_case(".dspace7_parse", "metacheck:::.dspace7_parse", f"{PKG}.dspace7._dspace7_parse", DS7_URLS)
links_cases(
    "dspace7_links",
    "dspace7",
    [
        "Data: repository.gatech.edu/handle/1853/67239/ and www.dl.gi.de/items/abc.",
        "See https://SCHOLARWORKS.UMASS.EDU/entities/x and open.fau.de",
    ],
    [
        f"https://repository.gatech.edu/entities/publication/{GT}/",
        "https://open.fau.de/handle/1/2",
        "https://osf.io/abcde",
    ],
)
for label, args_r, args_py in [
    ("uuid", f"uuid = {rq(GT)}", f"uuid={rq(GT)}"),
    ("handle", 'handle = "1853/67239"', 'handle="1853/67239"'),
    ("uuid_and_handle", f'uuid = {rq(GT)}, handle = "1853/404"', f'uuid={rq(GT)}, handle="1853/404"'),
    ("unfound_handle", 'handle = "1853/404"', 'handle="1853/404"'),
    ("nothing", "", ""),
]:
    sep_r = ", " if args_r else ""
    sep_py = ", " if args_py else ""
    expr_case(
        f".dspace7_info.mock.{label}",
        f'metacheck:::.dspace7_info("repository.gatech.edu"{sep_r}{args_r})',
        f'lambda m: m.dspace7._dspace7_info("repository.gatech.edu"{sep_py}{args_py})',
    )
expr_case(
    ".dspace7_info.mock.no_original",
    f'metacheck:::.dspace7_info("scholarworks.umass.edu", uuid = {rq(UM)})',
    f'lambda m: m.dspace7._dspace7_info("scholarworks.umass.edu", uuid={rq(UM)})',
)
gt_url = f"https://repository.gatech.edu/entities/publication/{GT}"
expr_case(
    "dspace7_file_download.mock.one",
    f"dspace7_file_download({rq(gt_url)})",
    f"lambda m: m.dspace7.dspace7_file_download({rq(gt_url)})",
)
DS7_DL = [
    gt_url,
    "https://repository.gatech.edu/handle/1853/67239",
    f"https://scholarworks.umass.edu/items/{UM}",
    "https://example.org/x",
    None,
    gt_url,
]
expr_case(
    "dspace7_file_download.mock.vector",
    f"dspace7_file_download({r_chr(DS7_DL)})",
    f"lambda m: m.dspace7.dspace7_file_download({py_list(DS7_DL)})",
)
for label, url in [
    ("unknown_host", "https://example.org/x"),
    ("no_files", f"https://scholarworks.umass.edu/items/{UM}"),
    ("na", None),
]:
    expr_case(
        f"dspace7_file_download.mock.{label}",
        f"dspace7_file_download({'NA' if url is None else rq(url)})",
        f"lambda m: m.dspace7.dspace7_file_download({'None' if url is None else rq(url)})",
    )
expr_case(
    "dspace7_file_download.mock.all_null_error",
    'dspace7_file_download(c("https://example.org/a", "https://example.org/b"))',
    'lambda m: m.dspace7.dspace7_file_download(["https://example.org/a", "https://example.org/b"])',
)

# ================================================================= 4TU.ResearchData
FOURTU_URLS = [
    "16766929",
    "https://doi.org/10.4121/16766929.v1",
    "10.4121/uuid:7F866E02-EB39-4A2A-8F7D-2D053EE6CDE9",
    "https://doi.org/10.4121/ce413614-1c82-4e81-90c0-323aa7d2fabd",
    "https://doi.org/10.4121/7f866e02-eb39-4a2a-8f7d-2d053ee6cde9",
    "https://data.4tu.nl/datasets/7f866e02-eb39-4a2a-8f7d-2d053ee6cde9",
    "https://data.4tu.nl/articles/dataset/Wind_tunnel/16766929/1",
    "https://data.4tu.nl/articles/16766929",
    "https://data.4tu.nl/articles/dataset/x",
    "",
    None,
    " 123 ",
]
vec_case(
    ".researchdata4tu_id",
    "metacheck:::.researchdata4tu_id",
    f"{PKG}.fourtu._researchdata4tu_id",
    FOURTU_URLS,
)
links_cases(
    "researchdata4tu_links",
    "fourtu",
    [
        "Data: 10.4121/16766929.v2 and https://data.4tu.nl/articles/dataset/Wind/16766929/1/.",
        "Also doi.org/10.4121/uuid:7f866e02-eb39-4a2a-8f7d-2d053ee6cde9 and www.data.4tu.nl/datasets/",
    ],
    ["https://doi.org/10.4121/16766929.v1", "https://data.4tu.nl/articles/16766929/", "https://osf.io/x"],
)
T4_VEC = [
    "https://doi.org/10.4121/16766929.v1",
    "10.4121/ce413614-1c82-4e81-90c0-323aa7d2fabd",
    "99999999",
    "nope",
    "16766929",
    "https://doi.org/10.4121/16766929.v1",
]
expr_case(
    "researchdata4tu_info.mock.vector",
    online(f"researchdata4tu_info({r_chr(T4_VEC)})"),
    f"lambda m: m.fourtu.researchdata4tu_info({py_list(T4_VEC)})",
)
expr_case(
    "researchdata4tu_info.mock.links_table",
    online(
        "researchdata4tu_info(researchdata4tu_links("
        + tp_r(["see 10.4121/16766929"], ["https://data.4tu.nl/articles/16766929/"])
        + "))"
    ),
    "lambda m: m.fourtu.researchdata4tu_info(m.fourtu.researchdata4tu_links("
    + tp_py(["see 10.4121/16766929"], ["https://data.4tu.nl/articles/16766929/"])
    + "))",
)
expr_case(
    "researchdata4tu_info.mock.id_col",
    online('researchdata4tu_info(data.frame(a = 1:2, b = c("16766929", "x")), id_col = "b")'),
    'lambda m: m.fourtu.researchdata4tu_info(m.pd.DataFrame({"a": [1, 2], "b": ["16766929", "x"]}), id_col="b")',
)
expr_case(
    "researchdata4tu_info.mock.na_error",
    online('researchdata4tu_info(c("16766929", NA))'),
    'lambda m: m.fourtu.researchdata4tu_info(["16766929", None])',
)
expr_case(
    "researchdata4tu_info.mock.none_valid",
    online('researchdata4tu_info(c("nope", "nope"))'),
    'lambda m: m.fourtu.researchdata4tu_info(["nope", "nope"])',
)
expr_case(
    "researchdata4tu_info.mock.offline_error",
    offline('researchdata4tu_info("16766929")'),
    'lambda m: m.offline(lambda: m.fourtu.researchdata4tu_info("16766929"))',
)
fn_case("researchdata4tu_pat.get", "researchdata4tu_pat", f"{PKG}.fourtu.researchdata4tu_pat", {})
fn_case(
    "researchdata4tu_pat.bad_error",
    "researchdata4tu_pat",
    f"{PKG}.fourtu.researchdata4tu_pat",
    {"pat": 1},
)
expr_case(
    "researchdata4tu_file_download.mock.one",
    tmp_r('researchdata4tu_file_download("https://doi.org/10.4121/16766929.v1", download_to = d)'),
    tmp_py('m.fourtu.researchdata4tu_file_download("https://doi.org/10.4121/16766929.v1", download_to=d)'),
)
expr_case(
    "researchdata4tu_file_download.mock.uuid_only",
    tmp_r('researchdata4tu_file_download("10.4121/ce413614-1c82-4e81-90c0-323aa7d2fabd", download_to = d)'),
    tmp_py('m.fourtu.researchdata4tu_file_download("10.4121/ce413614-1c82-4e81-90c0-323aa7d2fabd", download_to=d)'),
)
fn_case(
    "researchdata4tu_file_download.none",
    "researchdata4tu_file_download",
    f"{PKG}.fourtu.researchdata4tu_file_download",
    {"researchdata4tu_id": "nope"},
)

# ================================================================= PsychArchives / legacy DSpace
PA = "https://hdl.handle.net/20.500.12034/17526"
PA_URLS = [
    PA,
    "https://www.psycharchives.org/jspui/handle/20.500.12034/17526",
    "20.500.12034/17526",
    "https://bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/1234;",
    "https://DSPACE.UT.EE/handle/10062/12345.",
    "20.500.12034/17526;",
    "https://osf.io/abcde",
    "",
    None,
    "see 12.34/abc and 20.500.12034/1",
    "https://qsardb.org/repository/handle/10967/106",
    "123456/7",
]
vec_case(
    ".dspace_legacy_parse",
    "metacheck:::.dspace_legacy_parse",
    f"{PKG}.psycharchives._dspace_legacy_parse",
    PA_URLS,
)
for i, u in enumerate(
    [
        PA,
        "https://www.psycharchives.org/jspui/handle/20.500.12034/17526",
        "20.500.12034/17526",
        "https://osf.io/abcde",
    ]
):
    fn_case(
        f".psycharchives_handle.{i + 1}",
        "metacheck:::.psycharchives_handle",
        f"{PKG}.psycharchives._psycharchives_handle",
        {"url": u},
    )
fn_case(
    ".psycharchives_handle.vector",
    "metacheck:::.psycharchives_handle",
    f"{PKG}.psycharchives._psycharchives_handle",
    {"url": {"$chr": [None, "20.500.12034/1"]}},
)
fn_case(
    ".psycharchives_handle.empty_error",
    "metacheck:::.psycharchives_handle",
    f"{PKG}.psycharchives._psycharchives_handle",
    {"url": {"$chr": []}},
)
PA_TEXT = [
    "Materials: https://www.psycharchives.org/en/item/abc-123/ and hdl.handle.net/20.500.12034/17526.",
    "Also 20.500.12034/999 and bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/1234 and qsardb.org/x",
]
PA_LINKS = [
    PA,
    "https://www.psycharchives.org/jspui/handle/20.500.12034/17526/",
    "https://dspace.ut.ee/handle/10062/1",
    "https://osf.io/abcde",
]
links_cases("psycharchives_links", "psycharchives", PA_TEXT, PA_LINKS)
links_cases("dspace_links", "psycharchives", PA_TEXT, PA_LINKS)
for i, u in enumerate([PA, "https://www.psycharchives.org/jspui/handle/20.500.12034/17526"]):
    expr_case(
        f"psycharchives_links.test{i + 1}",
        f"psycharchives_links({tp_r(url=[u])})",
        f"lambda m: m.psycharchives.psycharchives_links({tp_py(url=[u])})",
        mock=None,
        compare=IGNORE_PID,
    )
expr_case(
    ".psycharchives_info.mock.apis",
    f"metacheck:::.psycharchives_info({rq(PA)})",
    f"lambda m: m.psycharchives._psycharchives_info({rq(PA)})",
    mock=APIS,
)
fn_case(
    ".psycharchives_info.invalid",
    "metacheck:::.psycharchives_info",
    f"{PKG}.psycharchives._psycharchives_info",
    {"pa_url": "https://osf.io/abcde"},
)
BONN = "https://bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/1234"
for label, u in [
    ("bonndoc", BONN),
    ("no_uuid", "https://qsardb.org/repository/handle/10967/106"),
    ("unrecorded", "https://dspace.ut.ee/handle/10062/1"),
    ("unknown_host", "https://example.org/handle/1/2"),
]:
    expr_case(
        f".psycharchives_info.mock.{label}",
        f"metacheck:::.psycharchives_info({rq(u)})",
        f"lambda m: m.psycharchives._psycharchives_info({rq(u)})",
    )
PA_VEC = [PA, None, PA, "https://osf.io/x"]
expr_case(
    "psycharchives_info.mock.vector",
    online(f"psycharchives_info({r_chr(PA_VEC)})"),
    f"lambda m: m.psycharchives.psycharchives_info({py_list(PA_VEC)})",
    mock=APIS,
)
expr_case(
    "psycharchives_info.mock.stop_on_error",
    online(f'psycharchives_info(c("https://osf.io/x", {rq(PA)}))'),
    f'lambda m: m.psycharchives.psycharchives_info(["https://osf.io/x", {rq(PA)}])',
    mock=APIS,
)
expr_case(
    "psycharchives_info.mock.table",
    online(f'psycharchives_info(psycharchives_links({tp_r(url=[PA, PA + "/"])}), "href")'),
    f'lambda m: m.psycharchives.psycharchives_info(m.psycharchives.psycharchives_links({tp_py(url=[PA, PA + "/"])}), "href")',
    mock=APIS,
    compare=IGNORE_PID,
)
expr_case(
    "psycharchives_info.mock.none_valid",
    online("psycharchives_info(data.frame(u = c(NA, NA), n = 1:2))"),
    'lambda m: m.psycharchives.psycharchives_info(m.pd.DataFrame({"u": [None, None], "n": [1, 2]}))',
    mock=APIS,
)
expr_case(
    "psycharchives_info.mock.offline_error",
    offline(f"psycharchives_info({rq(PA)})"),
    f"lambda m: m.offline(lambda: m.psycharchives.psycharchives_info({rq(PA)}))",
    mock=APIS,
)
expr_case(
    "psycharchives_file_download.mock.one",
    f"psycharchives_file_download({rq(PA)})",
    f"lambda m: m.psycharchives.psycharchives_file_download({rq(PA)})",
    mock=APIS,
)
expr_case(
    "psycharchives_file_download.mock.rights",
    f'attr(psycharchives_file_download({rq(PA)}), "rights")',
    f'lambda m: m.pd.Series(m.psycharchives.psycharchives_file_download({rq(PA)}).attrs["rights"])',
    mock=APIS,
)
expr_case(
    "psycharchives_file_download.mock.vector",
    f'psycharchives_file_download(c({rq(PA)}, NA, {rq(PA)}, "https://osf.io/x"))',
    f'lambda m: m.psycharchives.psycharchives_file_download([{rq(PA)}, None, {rq(PA)}, "https://osf.io/x"])',
    mock=APIS,
)
expr_case(
    "psycharchives_file_download.mock.vector_attrs",
    f'(function(x) list(attr(x, "rights"), attr(x, "doi")))(psycharchives_file_download(c({rq(PA)}, {rq(BONN)})))',
    f'lambda m: (lambda x: [m.pd.Series(x.attrs["rights"]), m.pd.Series(x.attrs["doi"])])(m.psycharchives.psycharchives_file_download([{rq(PA)}, {rq(BONN)}]))',
    mock=MOCK,
)
expr_case(
    "psycharchives_file_download.mock.restricted",
    f"(function(x) list(x, attr(x, 'rights'), attr(x, 'doi')))(psycharchives_file_download({rq(BONN)}))",
    f'lambda m: (lambda x: [x, m.pd.Series(x.attrs["rights"]), m.pd.Series(x.attrs["doi"])])(m.psycharchives.psycharchives_file_download({rq(BONN)}))',
)
expr_case(
    "psycharchives_file_download.mock.unfound",
    'psycharchives_file_download("https://osf.io/x")',
    'lambda m: m.psycharchives.psycharchives_file_download("https://osf.io/x")',
)
expr_case(
    "psycharchives_file_download.mock.all_null_error",
    'psycharchives_file_download(c("https://osf.io/x", "https://osf.io/y"))',
    'lambda m: m.psycharchives.psycharchives_file_download(["https://osf.io/x", "https://osf.io/y"])',
)
expr_case(
    "psycharchives_file_download.mock.empty_error",
    "psycharchives_file_download(character(0))",
    "lambda m: m.psycharchives.psycharchives_file_download([])",
)

# ================================================================= ResearchBox
RB_LINKS = [
    "https://researchbox.org/4377",
    "https://researchbox.org/6018/",
    "https://researchbox.org/2257/72",
    "https://ResearchBox.org/2257.9",
    "https://researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS",
    "https://osf.io/abcde",
]
links_cases(
    "rbox_links",
    "researchbox",
    ["See researchbox.org/801/ and https://researchbox.org/2257.8 for files.", "RESEARCHBOX.ORG/12"],
    RB_LINKS,
)
expr_case(
    "rbox_links.test",
    f"rbox_links({tp_r(url=['https://researchbox.org/801'])})",
    f"lambda m: m.researchbox.rbox_links({tp_py(url=['https://researchbox.org/801'])})",
    mock=None,
    compare=IGNORE_PID,
)
for label, u in [
    ("801", "https://researchbox.org/801"),
    ("peer_review", "https://researchbox.org/1150&PEER_REVIEW_passcode=MJUAAS"),
    ("4377", "https://researchbox.org/4377"),
    ("6018", "https://researchbox.org/6018"),
]:
    expr_case(
        f".rbox_info.mock.{label}",
        f"metacheck:::.rbox_info({rq(u)})",
        f"lambda m: m.researchbox._rbox_info({rq(u)})",
        mock=APIS,
    )
for label, u in [("synthetic", "https://researchbox.org/99"), ("unfound", "https://researchbox.org/404")]:
    expr_case(
        f".rbox_info.mock.{label}",
        f"metacheck:::.rbox_info({rq(u)})",
        f"lambda m: m.researchbox._rbox_info({rq(u)})",
    )
RB_TP = ["https://researchbox.org/4377", "https://researchbox.org/6018"]
expr_case(
    "rbox_info.mock.links",
    online(f'rbox_info(rbox_links({tp_r(url=RB_TP)}), "href")'),
    f'lambda m: m.researchbox.rbox_info(m.researchbox.rbox_links({tp_py(url=RB_TP)}), "href")',
    mock=APIS,
    compare=IGNORE_PID,
)
RB_VEC = ["https://researchbox.org/801", None, "https://researchbox.org/801"]
expr_case(
    "rbox_info.mock.vector",
    online(f"rbox_info({r_chr(RB_VEC)})"),
    f"lambda m: m.researchbox.rbox_info({py_list(RB_VEC)})",
    mock=APIS,
)
expr_case(
    "rbox_info.mock.stop_on_error",
    online('rbox_info(c("https://researchbox.org/404", "https://researchbox.org/99"))'),
    'lambda m: m.researchbox.rbox_info(["https://researchbox.org/404", "https://researchbox.org/99"])',
)
expr_case(
    "rbox_info.mock.none_valid",
    online("rbox_info(character(0))"),
    "lambda m: m.researchbox.rbox_info([])",
)
expr_case(
    "rbox_info.mock.offline_error",
    offline('rbox_info("https://researchbox.org/801")'),
    'lambda m: m.offline(lambda: m.researchbox.rbox_info("https://researchbox.org/801"))',
    mock=APIS,
)
expr_case(
    "rbox_file_download.mock.no_ids",
    'rbox_file_download("https://researchbox.org/99")',
    'lambda m: m.researchbox.rbox_file_download("https://researchbox.org/99")',
)
expr_case(
    "rbox_file_download.mock.unfound",
    'rbox_file_download("https://researchbox.org/404")',
    'lambda m: m.researchbox.rbox_file_download("https://researchbox.org/404")',
)
expr_case(
    "rbox_file_download.mock.all_null_error",
    'rbox_file_download(c("https://researchbox.org/99", "https://researchbox.org/404"))',
    'lambda m: m.researchbox.rbox_file_download(["https://researchbox.org/99", "https://researchbox.org/404"])',
)
RB_CACHE = {
    "researchbox.org_801/unzipped/data/x.csv": "a,b",
    "researchbox.org_801/unzipped/README.md": "hi",
    "researchbox.org_801/unzipped/run.sh": "echo",
    "researchbox.org_801/unzipped/Zeta.R": "1",
    "researchbox.org_801/unzipped/noext": "",
}
rb_writes = "; ".join(
    f"p <- file.path(d, {rq(k)}); dir.create(dirname(p), recursive = TRUE, showWarnings = FALSE); "
    f"writeLines({rq(v)}, p)"
    for k, v in RB_CACHE.items()
)
expr_case(
    "rbox_file_download.mock.cached",
    tmp_r(
        f"{rb_writes}; old <- options(metacheck.repo_cache.dir = d); on.exit(options(old), add = TRUE); "
        'res <- rbox_file_download(c("https://researchbox.org/801", NA, "https://researchbox.org/801")); '
        'res$file_location <- sub(".*/unzipped/", "", res$file_location); res'
    ),
    "lambda m: m.repo_cache("
    + json.dumps(RB_CACHE)
    + ', lambda d: (lambda res: res.assign(file_location=[None if m.pd.isna(v) else str(v).split("/unzipped/", 1)[-1] for v in res["file_location"]]))('
    'm.researchbox.rbox_file_download(["https://researchbox.org/801", None, "https://researchbox.org/801"])))',
)

# ================================================================= ReShare
RS_URLS = [
    "854001",
    "https://doi.org/10.5255/UKDA-SN-854001",
    "10.5255/ukda-sn-854243-2",
    "https://reshare.ukdataservice.ac.uk/854243/",
    "http://dx.doi.org/10.5255/UKDA-SN-1",
    "https://reshare.ukdataservice.ac.uk/id/eprint/854001",
    "",
    None,
    " 42 ",
    "nope",
]
vec_case(".reshare_id", "metacheck:::.reshare_id", f"{PKG}.reshare._reshare_id", RS_URLS)
links_cases(
    "reshare_links",
    "reshare",
    [
        "Deposited at 10.5255/UKDA-SN-854001 and https://reshare.ukdataservice.ac.uk/854243/.",
        "Also dx.doi.org/10.5255/ukda-sn-854100 (lower case) and www.reshare.ukdataservice.ac.uk/1",
    ],
    ["https://doi.org/10.5255/UKDA-SN-854001", "https://reshare.ukdataservice.ac.uk/854001/", "https://osf.io/x"],
)
for label, rid in [
    ("full", "854001"),
    ("parse_error", "854243"),
    ("unfound", "999999"),
    ("empty", "854100"),
]:
    expr_case(
        f".reshare_info.mock.{label}",
        f"metacheck:::.reshare_info({rq(rid)})",
        f"lambda m: m.reshare._reshare_info({rq(rid)})",
    )
RS_VEC = [
    "https://doi.org/10.5255/UKDA-SN-854001",
    "854001",
    "https://reshare.ukdataservice.ac.uk/854243/",
    "999999",
    "nope",
    None,
]
expr_case(
    "reshare_info.mock.vector",
    online(f"reshare_info({r_chr(RS_VEC)})"),
    f"lambda m: m.reshare.reshare_info({py_list(RS_VEC)})",
)
expr_case(
    "reshare_info.mock.links_table",
    online(
        "reshare_info(reshare_links("
        + tp_r(["see 10.5255/UKDA-SN-854100"], ["https://reshare.ukdataservice.ac.uk/854001/"])
        + "))"
    ),
    "lambda m: m.reshare.reshare_info(m.reshare.reshare_links("
    + tp_py(["see 10.5255/UKDA-SN-854100"], ["https://reshare.ukdataservice.ac.uk/854001/"])
    + "))",
    compare=IGNORE_PID,
)
expr_case(
    "reshare_info.mock.none_valid",
    online('reshare_info(c("nope", NA))'),
    'lambda m: m.reshare.reshare_info(["nope", None])',
)
expr_case(
    "reshare_info.mock.offline_error",
    offline('reshare_info("854001")'),
    'lambda m: m.offline(lambda: m.reshare.reshare_info("854001"))',
)
expr_case(
    "reshare_file_download.mock.one",
    tmp_r('reshare_file_download("https://doi.org/10.5255/UKDA-SN-854001", download_to = d)'),
    tmp_py('m.reshare.reshare_file_download("https://doi.org/10.5255/UKDA-SN-854001", download_to=d)'),
)
expr_case(
    "reshare_file_download.mock.no_caps",
    tmp_r('reshare_file_download("854001", download_to = d, max_file_size = NULL, max_download_size = Inf)'),
    tmp_py('m.reshare.reshare_file_download("854001", download_to=d, max_file_size=None, max_download_size=float("inf"))'),
)
expr_case(
    "reshare_file_download.mock.total_cap",
    tmp_r('reshare_file_download("854001", download_to = d, max_file_size = 100, max_download_size = 5)'),
    tmp_py('m.reshare.reshare_file_download("854001", download_to=d, max_file_size=100, max_download_size=5)'),
)
expr_case(
    "reshare_file_download.mock.multi",
    tmp_r('reshare_file_download(c("854001", "854100", "999999", "854001"), download_to = d)'),
    tmp_py('m.reshare.reshare_file_download(["854001", "854100", "999999", "854001"], download_to=d)'),
)
expr_case(
    "reshare_file_download.mock.no_files",
    tmp_r('reshare_file_download("854100", download_to = d)'),
    tmp_py('m.reshare.reshare_file_download("854100", download_to=d)'),
)
fn_case(
    "reshare_file_download.none",
    "reshare_file_download",
    f"{PKG}.reshare.reshare_file_download",
    {"reshare_id": "nope"},
)
expr_case(
    ".reshare_verify_downloads.table",
    tmp_r(
        'writeLines("abc", file.path(d, "a.txt")); dir.create(file.path(d, "sub")); '
        'files <- data.frame(path = c("a.txt", "a.txt", "missing.txt", NA, "sub", "a.txt"), '
        "size = c(4, 5, 5, NA, 1, NA), "
        'checksum = c("0BEE89B07A248E27C83FC3D5951213C1", NA, NA, NA, NA, "00000000000000000000000000000000"), '
        'checksum_type = c("md5", "md5", "md5", NA, "md5", "md5"), '
        "downloaded = c(TRUE, TRUE, TRUE, FALSE, TRUE, TRUE), extracted = c(NA, NA, NA, 2L, NA, NA)); "
        "metacheck:::.reshare_verify_downloads(files, d)"
    ),
    "lambda m: m.tmp(lambda d: (open(d + '/a.txt', 'w').write('abc\\n'), __import__('os').mkdir(d + '/sub'), "
    "m.reshare._reshare_verify_downloads(m.pd.DataFrame({"
    "'path': m.pd.Series(['a.txt', 'a.txt', 'missing.txt', None, 'sub', 'a.txt'], dtype='string'), "
    "'size': [4.0, 5.0, 5.0, None, 1.0, None], "
    "'checksum': m.pd.Series(['0BEE89B07A248E27C83FC3D5951213C1', None, None, None, None, '00000000000000000000000000000000'], dtype='string'), "
    "'checksum_type': m.pd.Series(['md5', 'md5', 'md5', None, 'md5', 'md5'], dtype='string'), "
    "'downloaded': [True, True, True, False, True, True], "
    "'extracted': m.pd.Series([None, None, None, 2, None, None], dtype='Int64')}), d))[-1])",
    mock=None,
)
expr_case(
    ".reshare_verify_downloads.empty",
    "metacheck:::.reshare_verify_downloads(data.frame(path = character(0)), tempdir())",
    "lambda m: m.reshare._reshare_verify_downloads(m.pd.DataFrame({'path': m.pd.Series([], dtype='string')}), '/tmp')",
    mock=None,
)
expr_case(
    ".reshare_verify_downloads.no_path",
    'metacheck:::.reshare_verify_downloads(data.frame(size = 1, downloaded = TRUE), tempdir())',
    "lambda m: m.reshare._reshare_verify_downloads(m.pd.DataFrame({'size': [1.0], 'downloaded': [True]}), '/tmp')",
    mock=None,
)

# ================================================================= Mendeley Data
MD_URLS = [
    "vjtxybrc28",
    "https://doi.org/10.17632/vjtxybrc28.1",
    "https://data.mendeley.com/datasets/vjtxybrc28/2",
    "10.17632/ABC.3",
    "https://data.mendeley.com/drafts/x",
    "",
    None,
    "has space",
    " abc ",
]
vec_case(".mendeley_id", "metacheck:::.mendeley_id", f"{PKG}.mendeley._mendeley_id", MD_URLS)
links_cases(
    "mendeley_links",
    "mendeley",
    [
        "Data: https://data.mendeley.com/datasets/vjtxybrc28/1 and 10.17632/abc123.2.",
        "Also doi.org/10.17632/xyz and DATA.MENDELEY.COM/datasets/Q9",
    ],
    ["https://doi.org/10.17632/vjtxybrc28.1", "https://data.mendeley.com/datasets/vjtxybrc28/1/", "https://osf.io/x"],
)
for label, mid in [
    ("full", "vjtxybrc28"),
    ("short_licence", "short1"),
    ("unfound", "zzz999"),
    ("parse_error", "notjson"),
    ("doi_string_error", "strdoi"),
]:
    expr_case(
        f".mendeley_info.mock.{label}",
        f"metacheck:::.mendeley_info({rq(mid)})",
        f"lambda m: m.mendeley._mendeley_info({rq(mid)})",
    )
MD_VEC = [
    "https://doi.org/10.17632/vjtxybrc28.1",
    "https://data.mendeley.com/datasets/vjtxybrc28/1",
    "short1",
    "zzz999",
    "10.17632/notjson",
    "http://x.org/a b",
    None,
]
expr_case(
    "mendeley_info.mock.vector",
    online(f"mendeley_info({r_chr(MD_VEC)})"),
    f"lambda m: m.mendeley.mendeley_info({py_list(MD_VEC)})",
)
expr_case(
    "mendeley_info.mock.links_table",
    online(
        "mendeley_info(mendeley_links("
        + tp_r(["see 10.17632/short1.1"], ["https://data.mendeley.com/datasets/vjtxybrc28/1/"])
        + "))"
    ),
    "lambda m: m.mendeley.mendeley_info(m.mendeley.mendeley_links("
    + tp_py(["see 10.17632/short1.1"], ["https://data.mendeley.com/datasets/vjtxybrc28/1/"])
    + "))",
    compare=IGNORE_PID,
)
expr_case(
    "mendeley_info.mock.id_col_position",
    online('mendeley_info(data.frame(n = 1:2, u = c("short1", "http://x.org/a b"), mendeley_id = "stale"), id_col = 2)'),
    'lambda m: m.mendeley.mendeley_info(m.pd.DataFrame({"n": [1, 2], "u": ["short1", "http://x.org/a b"], "mendeley_id": "stale"}), id_col=2)',
)
expr_case(
    "mendeley_info.mock.error",
    online('mendeley_info("strdoi")'),
    'lambda m: m.mendeley.mendeley_info("strdoi")',
)
expr_case(
    "mendeley_info.mock.offline_error",
    offline('mendeley_info("short1")'),
    'lambda m: m.offline(lambda: m.mendeley.mendeley_info("short1"))',
)

# ================================================================= FSD
FSD_IDS = [
    "https://services.fsd.tuni.fi/catalogue/FSD2653?lang=en",
    "https://doi.org/10.60686/t-fsd2653",
    "urn.fi/urn:nbn:fi:fsd:T-FSD3136",
    "FSD12",
    "fsd_1234567",
    "FSD 2653",
    "",
    None,
    "no study",
    "fsd:0042",
]
expr_case(
    ".fsd_study_id.shapes",
    f"vapply({r_chr(FSD_IDS)}, metacheck:::.fsd_study_id, character(1), USE.NAMES = FALSE)",
    f"lambda m: [m.fsd._fsd_study_id(u) for u in {py_list(FSD_IDS)}]",
    mock=None,
)
fn_case(
    ".fsd_study_id.vector_one",
    "metacheck:::.fsd_study_id",
    f"{PKG}.fsd._fsd_study_id",
    {"url": {"$chr": ["x", "FSD1234"]}},
)
fn_case(
    ".fsd_study_id.vector_error",
    "metacheck:::.fsd_study_id",
    f"{PKG}.fsd._fsd_study_id",
    {"url": {"$chr": ["FSD1234", "FSD5678"]}},
)
fn_case(".fsd_study_id.empty", "metacheck:::.fsd_study_id", f"{PKG}.fsd._fsd_study_id", {"url": {"$chr": []}})
links_cases(
    "fsd_links",
    "fsd",
    [
        "The data (FSD2653; fsd3136) are at services.fsd.tuni.fi/catalogue/FSD2653/ and urn.fi/urn:nbn:fi:fsd:T-FSD3136.",
        "Also FSD12 and FSD1234567.",
    ],
    [
        "https://services.fsd.tuni.fi/catalogue/FSD2653?lang=en",
        "https://doi.org/10.60686/t-fsd2653",
        "http://urn.fi/urn:nbn:fi:fsd:T-FSD3136",
        "https://www.fsd.tuni.fi/en/",
        "https://osf.io/x",
    ],
)
for label, u in [
    ("full", "https://doi.org/10.60686/t-fsd2653"),
    ("sparse", "FSD1111"),
    ("unfound", "FSD9999"),
]:
    expr_case(
        f".fsd_info.mock.{label}",
        f"metacheck:::.fsd_info({rq(u)})",
        f"lambda m: m.fsd._fsd_info({rq(u)})",
    )
fn_case(".fsd_info.invalid", "metacheck:::.fsd_info", f"{PKG}.fsd._fsd_info", {"fsd_url": "https://example.org"})
FSD_VEC = ["FSD2653", "https://doi.org/10.60686/t-fsd1111", None, "FSD2653"]
expr_case(
    "fsd_info.mock.vector",
    online(f"fsd_info({r_chr(FSD_VEC)})"),
    f"lambda m: m.fsd.fsd_info({py_list(FSD_VEC)})",
)
expr_case(
    "fsd_info.mock.stop_on_error",
    online('fsd_info(c("nope", "FSD2653"))'),
    'lambda m: m.fsd.fsd_info(["nope", "FSD2653"])',
)
expr_case(
    "fsd_info.mock.table",
    online(f'fsd_info(fsd_links({tp_r(["See FSD2653."], ["https://services.fsd.tuni.fi/catalogue/FSD1111/"])}), "href")'),
    f'lambda m: m.fsd.fsd_info(m.fsd.fsd_links({tp_py(["See FSD2653."], ["https://services.fsd.tuni.fi/catalogue/FSD1111/"])}), "href")',
    compare=IGNORE_PID,
)
expr_case(
    "fsd_info.mock.offline_error",
    offline('fsd_info("FSD2653")'),
    'lambda m: m.offline(lambda: m.fsd.fsd_info("FSD2653"))',
)


def main() -> None:
    header = (
        "# Parity cases for R/archive-{dataone,dspace7,4tu,psycharchives,researchbox,reshare,\n"
        "# mendeley,fsd}.R. Generated by tests/archives_d2/make_parity_cases.py; mocked HTTP\n"
        "# lives in tests/archives_d2/mocks (httptest2 format, replayed on both sides) and in\n"
        "# metacheck's own tests/testthat/apis recordings.\n"
    )
    OUT.write_text(
        header
        + yaml.dump(
            {"area": "archives_d2", "cases": cases},
            Dumper=Dumper,
            sort_keys=False,
            allow_unicode=True,
            width=10_000,
        ),
        encoding="utf-8",
    )
    print(f"wrote {len(cases)} cases to {OUT}")


if __name__ == "__main__":
    main()
