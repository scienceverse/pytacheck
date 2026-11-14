"""Generate parity/cases/archives_repo_tables.yaml (R and Python snippets side by side).

The cases of metacheck 0.4.4.1's repository survey (#636, #637): the EPrints, Invenio and CKAN
host lists and link finders, the record-type check (R/repo-kind.R), and the readers of another
EPrints / Invenio / CKAN installation's record. Run from the repository root:
``python -m tests.archives_d2.make_repo_table_cases``, then
``python -m parity generate --area archives_repo_tables``. DataCite's answers are stood in for on
both sides (``.repo_doi_records`` mocked); the portals' answers replay
``tests/archives_d2/mocks`` (see ``make_mocks.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.archives_d2.make_parity_cases import Dumper, py_list, r_chr, tp_py, tp_r

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "archives_repo_tables.yaml"
MOCK = "tests/archives_d2/mocks"
RUN = "tests.archives_d2.parity_support.run"
IGNORE_PID = {"ignore": ["paper_id"]}
cases: list[dict[str, Any]] = []


def fn_case(id_: str, r: str, py: str, args: dict[str, Any]) -> None:
    cases.append({"id": id_, "r": r, "py": py, "args": args})


def expr_case(
    id_: str, r: str, py: str, mock: str | None = None, compare: dict[str, Any] | None = None
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


def datacite_r(call: str, records: dict[str, tuple[str, str]]) -> str:
    """*call* with ``.repo_doi_records()`` answering from *records* (lower-cased DOI -> url, type)."""
    dois = r_chr(list(records))
    urls = r_chr([u for u, _ in records.values()])
    types = r_chr([t for _, t in records.values()])
    return (
        f"testthat::with_mocked_bindings({call}, .repo_doi_records = function(doi) "
        f"{{ i <- match(tolower(doi), {dois}); data.frame(doi = tolower(doi), url = {urls}[i], "
        f'resource_type = {types}[i], in_datacite = TRUE) }}, .package = "metacheck")'
    )


def datacite_py(call: str, records: dict[str, tuple[str, str]]) -> str:
    items = ", ".join(f'"{d}": ("{u}", "{t}")' for d, (u, t) in records.items())
    return f"m.datacite({{{items}}}, lambda: {call})"


PKG = "metacheck.archives"

# ---------------------------------------------------------------- allowlists
for r, py in [
    ("metacheck:::.eprints_hosts", f"{PKG}.reshare._eprints_hosts"),
    ("metacheck:::.eprints_host_regex", f"{PKG}.reshare._eprints_host_regex"),
    ("metacheck:::.eprints_doi_prefixes", f"{PKG}.reshare._eprints_doi_prefixes"),
    ("metacheck:::.invenio_hosts", f"{PKG}.zenodo._invenio_hosts"),
    ("metacheck:::.invenio_host_regex", f"{PKG}.zenodo._invenio_host_regex"),
    ("metacheck:::.ckan_hosts", f"{PKG}.ckan._ckan_hosts"),
    ("metacheck:::.ckan_doi_prefixes_all", f"{PKG}.ckan._ckan_doi_prefixes_all"),
    ("metacheck:::.dspace7_doi_prefixes", f"{PKG}.dspace7._dspace7_doi_prefixes"),
]:
    fn_case(r.split(":::")[1], r, py, {})
fn_case(
    ".ckan_api_base.hosts",
    "metacheck:::.ckan_api_base",
    f"{PKG}.ckan._ckan_api_base",
    {
        "host": {
            "$expr": {
                "r": r_chr(["open.canada.ca", "www.open.canada.ca", "data.cnra.ca.gov"]),
                "py": py_list(["open.canada.ca", "www.open.canada.ca", "data.cnra.ca.gov"]),
            }
        }
    },
)

# ---------------------------------------------------------------- ids
RESHARE = [
    "https://datacompass.lshtm.ac.uk/id/eprint/4333",
    "https://datacompass.lshtm.ac.uk/4333/",
    "http://researchdata.gla.ac.uk/2235",
    "datacompass.lshtm.ac.uk/4333",
    "https://www.example.org/id/eprint/4333",
    "https://datacompass.lshtm.ac.uk/view/divisions/",
    "https://reshare.ukdataservice.ac.uk/id/eprint/854001",
    "https://reshare.ukdataservice.ac.uk/854243/",
    "10.5255/UKDA-SN-854001",
    None,
]
fn_case(
    ".reshare_id.eprints_hosts",
    "metacheck:::.reshare_id",
    f"{PKG}.reshare._reshare_id",
    {"reshare_url": {"$expr": {"r": r_chr(RESHARE), "py": py_list(RESHARE)}}},
)
ZENODO = [
    "https://data.caltech.edu/records/j4k4w-p8331",
    "https://data.caltech.edu/api/records/j4k4w-p8331/files",
    "https://www.fdr.uni-hamburg.de/record/14137",
    "data.caltech.edu/j4k4w-p8331",
    "https://zenodo.org/records/2669586",
    "10.5281/zenodo.2669586",
    "https://www.example.org/records/123",
    None,
]
fn_case(
    ".zenodo_id.invenio_hosts",
    "metacheck:::.zenodo_id",
    f"{PKG}.zenodo._zenodo_id",
    {"zenodo_url": {"$expr": {"r": r_chr(ZENODO), "py": py_list(ZENODO)}}},
)
CKAN = [
    "https://data.cnra.ca.gov/dataset/statewide-crop-mapping",
    "https://www.envidat.ch/#/metadata/chelsa-climatologies",
    "http://data.nhm.ac.uk/dataset/8c67d782-c01d-4807-8913-f57d0daabf35",
    "https://open.canada.ca/data/en/dataset/0c0a6e7b-4c1d-4b6f-9b1e-1a2b3c4d5e6f",
    "https://data.cnra.ca.gov/dataset/6c3d65e3/resource/1",
    "https://data.cnra.ca.gov/organization/cdfa",
    "https://www.example.org/dataset/x",
    None,
]
fn_case(
    ".ckan_id.shapes",
    "metacheck:::.ckan_id",
    f"{PKG}.ckan._ckan_id",
    {"url": {"$expr": {"r": r_chr(CKAN), "py": py_list(CKAN)}}},
)
fn_case(".ckan_id.empty", "metacheck:::.ckan_id", f"{PKG}.ckan._ckan_id", {"url": {"$chr": []}})

# ---------------------------------------------------------------- record types
TYPES = [
    "data_collection", "dataset", "Dataset", "Software", "artefact", "Data paper",
    "article", "thesis", "book_section", "Doctoral thesis", "Journal Article", "Text",
    "JournalArticle", "conference_item", "Working paper", "publication",
    "publication-article", "poster", "Presentation", "Preprint", "Report", "image",
    "video", "Other", "software code", "", None,
]  # fmt: skip
fn_case(
    ".repo_publication_type.shapes",
    "metacheck:::.repo_publication_type",
    f"{PKG}.repo_kind._repo_publication_type",
    {"type": {"$expr": {"r": r_chr(TYPES), "py": py_list(TYPES)}}},
)

# ---------------------------------------------------------------- Invenio files
R_FILES = (
    'c(list(list(id = "a", key = "x.csv", size = 10, checksum = "md5:1", links = list(self = '
    '"https://zenodo.org/api/records/1/files/x.csv/content"))), '
    'list(list(id = "b", key = "y.R", size = 20, checksum = "md5:2", links = list(self = '
    '"https://h/api/records/2/files/y.R", content = "https://h/api/records/2/files/y.R/content"))), '
    'list(list(file_id = "c", filename = "z.txt", filesize = 30, checksum = "3", links = list('
    'download = "https://h/record/3/files/z.txt", self = "https://h/api/files/c"))))'
)
PY_FILES = (
    '[{"id": "a", "key": "x.csv", "size": 10, "checksum": "md5:1", "links": {"self": '
    '"https://zenodo.org/api/records/1/files/x.csv/content"}}, '
    '{"id": "b", "key": "y.R", "size": 20, "checksum": "md5:2", "links": {"self": '
    '"https://h/api/records/2/files/y.R", "content": "https://h/api/records/2/files/y.R/content"}}, '
    '{"file_id": "c", "filename": "z.txt", "filesize": 30, "checksum": "3", "links": {'
    '"download": "https://h/record/3/files/z.txt", "self": "https://h/api/files/c"}}]'
)
expr_case(
    ".invenio_files.formats",
    f"metacheck:::.invenio_files({R_FILES})",
    f"lambda m: m.zenodo._invenio_files({PY_FILES})",
)
expr_case(
    ".invenio_files.rdm",
    'metacheck:::.invenio_files(list(enabled = TRUE, count = 1, entries = list(`y.R` = list(id = "b", '
    'key = "y.R", size = 20, checksum = "md5:2", links = list(self = "https://h/a/y.R", '
    'content = "https://h/a/y.R/content")))))',
    'lambda m: m.zenodo._invenio_files({"enabled": True, "count": 1, "entries": {"y.R": {"id": "b", '
    '"key": "y.R", "size": 20, "checksum": "md5:2", "links": {"self": "https://h/a/y.R", '
    '"content": "https://h/a/y.R/content"}}}})',
)
expr_case(
    ".invenio_files.restricted",
    "metacheck:::.invenio_files(list(enabled = TRUE, entries = list()))",
    'lambda m: m.zenodo._invenio_files({"enabled": True, "entries": {}})',
)

# ---------------------------------------------------------------- link finders (no DOI lookups)
TEXT = [
    "Data are at researchdata.gla.ac.uk/2235/ and on https://datacompass.lshtm.ac.uk/id/eprint/4333.",
    "Code is at data.caltech.edu/records/mzrjq-6wc02 and reshare.ukdataservice.ac.uk/854243.",
    "Crops were mapped from data.cnra.ca.gov/dataset/statewide-crop-mapping.",
    "Methods follow 10.1016/j.cognition.2020.104 and 10.5255/UKDA-SN-854001.",
    "See 10.5281/zenodo.2669586 and www.fdr.uni-hamburg.de/record/14137.",
]
URLS = [
    "https://datacompass.lshtm.ac.uk/id/eprint/4333",
    "https://www.fdr.uni-hamburg.de/record/14137",
    "https://www.envidat.ch/#/metadata/chelsa-climatologies",
    "https://osf.io/abcde",
]
for name, module in [
    ("eprints_links", "reshare"),
    ("invenio_links", "zenodo"),
    ("ckan_links", "ckan"),
]:
    expr_case(
        f"{name}.mixed",
        f"{name}({tp_r(TEXT, URLS)})",
        f"lambda m: m.{module}.{name}({tp_py(TEXT, URLS)})",
        compare=IGNORE_PID,
    )
    expr_case(
        f"{name}.none",
        f"{name}({tp_r(['Nothing to see here.'], ['https://osf.io/abcde'])})",
        f"lambda m: m.{module}.{name}({tp_py(['Nothing to see here.'], ['https://osf.io/abcde'])})",
        compare=IGNORE_PID,
    )
    fn_case(f"{name}.demo", name, f"{PKG}.{module}.{name}", {"paper": {"$paper": "demo"}})
    fn_case(
        f"{name}.psychsci",
        name,
        f"{PKG}.{module}.{name}",
        {"paper": {"$read": ["upstream/metacheck/tests/testthat/fixtures/psychsci"]}},
    )

# DOIs under a repository's prefix are looked up once at DataCite: a dataset on the repository is
# kept, a thesis left out, a DOI under any other prefix never asked
DOI_TEXT = [
    "Data are at doi:10.17037/DATA.00001411 and 10.5255/UKDA-SN-854001.",
    "Methods follow 10.1016/j.cognition.2020.104.",
    "Our earlier thesis is 10.17037/THESIS.77.",
    "Climate data: doi:10.16904/envidat.228. Code: doi:10.25592/uhhfdm.139",
]
RECORDS = {
    "10.17037/data.00001411": ("https://datacompass.lshtm.ac.uk/id/eprint/1411", "Dataset"),
    "10.17037/thesis.77": ("https://datacompass.lshtm.ac.uk/id/eprint/77", "Dissertation"),
    "10.16904/envidat.228": ("https://www.envidat.ch/#/metadata/chelsa-climatologies", "Dataset"),
    "10.25592/uhhfdm.139": ("https://www.fdr.uni-hamburg.de/record/139", "Dataset"),
}
for name, module in [
    ("eprints_links", "reshare"),
    ("invenio_links", "zenodo"),
    ("ckan_links", "ckan"),
]:
    expr_case(
        f"{name}.doi",
        datacite_r(f"{name}({tp_r(DOI_TEXT)})", RECORDS),
        "lambda m: " + datacite_py(f"m.{module}.{name}({tp_py(DOI_TEXT)})", RECORDS),
        compare=IGNORE_PID,
    )

# ---------------------------------------------------------------- readers of one record
for cid, rid in [
    ("other_host", "datacompass.lshtm.ac.uk/4333"),
    ("other_host_thesis", "datacompass.lshtm.ac.uk/1411"),
    ("other_host_unfound", "datacompass.lshtm.ac.uk/9999"),
]:
    expr_case(
        f".reshare_info.mock.{cid}",
        f'metacheck:::.reshare_info("{rid}")',
        f'lambda m: m.reshare._reshare_info("{rid}")',
        mock=MOCK,
    )
expr_case(
    "reshare_info.mock.other_host_table",
    online('reshare_info(eprints_links(test_paper(text = "see datacompass.lshtm.ac.uk/4333")))'),
    'lambda m: m.reshare.reshare_info(m.reshare.eprints_links(m.pc.test_paper(["see datacompass.lshtm.ac.uk/4333"])))',
    mock=MOCK,
    compare=IGNORE_PID,
)
for cid, rid in [("other_host", "data.caltech.edu/j4k4w-p8331")]:
    expr_case(
        f".zenodo_info.mock.{cid}",
        f'metacheck:::.zenodo_info("{rid}")',
        f'lambda m: m.zenodo._zenodo_info("{rid}")',
        mock=MOCK,
    )
for cid, url in [
    ("files", "https://data.cnra.ca.gov/dataset/statewide-crop-mapping"),
    ("unfound", "https://data.cnra.ca.gov/dataset/none"),
    ("not_ckan", "https://www.example.org/dataset/x"),
]:
    expr_case(
        f"ckan_info.mock.{cid}",
        online(f'ckan_info("{url}")'),
        f'lambda m: m.ckan.ckan_info("{url}")',
        mock=MOCK,
    )
expr_case(
    "ckan_info.mock.empty",
    "ckan_info(character(0))",
    "lambda m: m.ckan.ckan_info([])",
)


def main() -> None:
    header = (
        "# Parity cases for the EPrints, Invenio and CKAN repositories of metacheck 0.4.4.1\n"
        "# (R/repo-kind.R, R/repo-tables.R, R/archive-ckan.R and the eprints_links() /\n"
        "# invenio_links() parts of R/archive-reshare.R and R/archive-zenodo.R). Generated by\n"
        "# tests/archives_d2/make_repo_table_cases.py; mocked HTTP lives in\n"
        "# tests/archives_d2/mocks (httptest2 format, replayed on both sides).\n"
    )
    OUT.write_text(
        header
        + yaml.dump(
            {"area": "archives_repo_tables", "cases": cases},
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
