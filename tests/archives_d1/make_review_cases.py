"""Generate parity/cases/archives_d1_review.yaml: the adversarial-review cases.

Run from the repository root: ``python -m tests.archives_d1.make_review_cases``,
then ``python -m parity generate --area archives_d1_review``. Mocked HTTP comes
from ``tests/archives_d1/mocks`` (``make_mocks.py`` plus ``make_review_mocks.py``).

These target branches the first round of cases did not reach: a sample of
generated link shapes (the same generator agreed with R on ~7300 URLs and
their links), text-only and URL-only papers, ``bind_rows()`` column order when
the first lookup fails, column-name clashes in the joins, numeric ids, empty or
atomic JSON fields, files with no name and no id, empty names, missing sizes,
duplicate names, the total-size cap with ties and missing sizes, duplicated
Dataverse host/DOI pairs, and R's errors for malformed records and tables.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tests.archives_d1.make_parity_cases import Dumper, py_list, r_chr, tmp_py, tmp_r

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "archives_d1_review.yaml"
MOCK = "tests/archives_d1/mocks"
RUN = "tests.archives_d1.parity_support.run"
IGNORE_PID = {"ignore": ["paper_id"]}
cases: list[dict[str, Any]] = []

#: sentences sampled from the review's link-shape generator (schemes x ids x trailers)
FUZZ_TEXT: list[str] = [
    "(http://figshare.com/articles/Dataset/Name_x/123456/2/)",
    "https://www.figshare.com/projects/My_project/133332/)",
    "(https://doi.org/10.18167/DVN1/T0DMFJ,)",
    "See https://www.figshare.com/articles/dataset/name/123456/// for data.",
    "See https://www. 123456 / for data.",
    "See http://figshare.community/articles/1 for data.",
    "Data: http://figshare.com/articles/Dataset/Name_x/123456/2); code at "
    "http://figshare.com/articles/Dataset/Name_x/123456/2,.",
    "(https://10.5068/D14671//)",
    "See https://www.figshare.com/articles/dataset/name/123456 for data.",
    "Data: https://doi.org/10.6084/M9.FIGSHARE.18093368); code at "
    "https://doi.org/10.6084/M9.FIGSHARE.18093368,.",
    "(HTTPS://110.5061/dryad.abc/)",
    "HTTPS://10.72721/abc,",
    "(https://www.10.5061/dryad.j1fd7;)",
    "The DOI is https://10.6084/M9.FIGSHARE.18093368\n.",
    "http://tandf.figshare.com/articles/journal_contribution/x/987)",
    "See https://doi.org/figshare.com/articles/figure/Fig_1/12/3, for data.",
    "See https://doi.org/dataverse.nl/dataset.xhtml?persistentId=doi%3A10.34894%2FABC  for data.",
    "Data: https://www.10.26180/19095317.v1.;; code at https://www.10.26180/19095317.v1.v2.",
    "(HTTPS://10.7910/)",
    "Data:  persistentId=doi:10.1/x y;; code at  "
    "rodbuk.pl/dataset.xhtml?persistentId=doi:10.58099/x.",
    "The DOI is http://doi.org/10.18167/DVN1/T0DMFJ.",
    "(doi:10.7910/DVN/ABC.v2)",
    "Data: 123456\n; code at 123456 .",
    " ndownloader.figshare.com/files/31415",
    "https://datadryad.org/stash/dataset/doi:10.5061/dryad.j1fd7",
    "https://www.10.58032/abc ",
    "The DOI is https://doi.org/10.5061/dryad.abc%E2%80%93x.",
    "(https://dx.doi.org/10.7910;)",
    "https://doi.org/figshare.com/projects/133332 ",
    "The DOI is  10.7941/D18907..",
    "https://doi.org/10.7910;",
    "(http://10.26180/abc.)",
    "(http://figshare.com/articles/dataset/name/123456 )",
    "Data: https://dx.doi.org/10.15146/R3XQ19); code at https://dx.doi.org/10.15146/R3XQ19,.",
    "The DOI is https://dx.doi.org/figshare.le.ac.uk/articles/dataset/x/555/.",
    "https://doi.org/dataverse.nl/dataset.xhtml?persistentId=doi:10.34894%2FABC&version=1.0.",
    "(https://doi.org/uj.rodbuk.pl/x )",
    "(HTTPS://10.26180/19095317.v1)",
    "See https://doi.org/figsh.com/articles/77/ for data.",
    " 10.26180/abc)",
    "The DOI is https://www.datadryad.org/dataset/doi:10.5061/dryad.",
    "The DOI is https://DataVerse.Harvard.edu/dataset.xhtml?persistentId=DOI:10.7910/DVN/X\n.",
    " dataverse.nlx.org\n",
    " dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/GGDUND..v2",
    " 10.6084/m9.figshare.18093368.v1.",
    "The DOI is HTTPS://123456.",
    "(https://www.uj.rodbuk.pl/x.)",
    "Data: HTTPS://tandf.figshare.com/articles/journal_contribution/x/987//; code at "
    "HTTPS://tandf.figshare.com/articles/journal_contribution/x/987;.",
    "https://doi.org/12a,",
    "Data:  10.58032/abc//; code at  10.58032/abc;.",
    "See https://tandf.figshare.com/articles/journal_contribution/x/987  for data.",
    "(http://figshare.com/projects/My_project/133332//)",
    "doi:dataverse.no/dataset.xhtml?persistentId=doi:10.18710/X)",
    "The DOI is http://dataverse.nl/dataset.xhtml?persistentId=doi%3A10.34894%2FABC.",
    "Data: https://doi.org/10.6084/m9.figshare.abc); code at "
    "https://doi.org/10.6084/m9.figshare.abc,.",
    "Data: https://www.dataverse.nl/dataset.xhtml?persistentId=doi%3A10.34894%2FABC/; code at "
    "https://www.dataverse.nl/dataset.xhtml?persistentId=doi%3A10.34894%2FABC//.",
    "(HTTPS://abc.dataverse.nl/x )",
    "https://10.7910/DVN/ABC\n",
    "See HTTPS://https://dataverse.harvard.edu/api/access/datafile/123\n for data.",
    "(https://doi.org/figshare.le.ac.uk/articles/dataset/x/555 )",
    "See doi:10.6078/; for data.",
    "See https://10.5061/dryad\n for data.",
    " 10.17633/rd.123/",
    "Data: https://dx.doi.org/10.25375/uct.14618526.v1); code at "
    "https://dx.doi.org/10.25375/uct.14618526.v1,.",
    "Data:  figshare.com/s/abc123def,; code at  figshare.com/s/abc123def..",
    "Data: http://figshare.com/articles/online_resource/x_y/42; code at "
    "http://figshare.com/articles/online_resource/x_y/42\n"
    ".",
    "See https://dx.doi.org/10.17026/dans-x1\n for data.",
    "The DOI is doi:10.26180/abc/.",
    "http://10.50610/dryad.x)",
    "(https://dataverse.no/dataset.xhtml?persistentId=doi:10.18710/X,)",
]

FUZZ_URL: list[str] = [
    "https://www.dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/GGDUND.,",
    " figshare.com/projects/My_project/133332/",
    "https://dx.doi.org/dataverse.harvard.edu/citation?persistentId=doi:10.7910/DVN/Q&x=1//",
    "dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/GGDUND.)",
    "doi:datadryad.org/stash/dataset/doi: ",
    "https://figshare.com/articles/dataset/name/123456?file=999.",
    "HTTPS://figshare.com/projects/My_project/133332/;",
    "https://10.6084/M9.FIGSHARE.18093368;",
    "doi:10.50610/dryad.x/",
    " 10.58032/abc.",
    " figsh.com/articles/77;",
    "https://doi.org/10.25375/UCT.14618526)",
    "https://figshare.com/projects/My_project/133332/.v2",
    "10.25375/uct.14618526.v1 ",
    "http://10.5061/DRYAD.J1FD7",
    "10.25375/uct.14618526.v1\n",
    "https://www.dataverse.no/dataset.xhtml?persistentId=doi:10.18710/X\n",
    "doi:10.5068/D14671)",
    "http://10.17633/rd.brunel.123.",
    " doi.org/10.18167/DVN1/T0DMFJ\n",
    " 10.48420/16744483;",
    "https://www.dataverse.no/dataset.xhtml?persistentId=doi:10.18710/X/",
    "https://dx.doi.org/10.6078/x_y;",
    "  123456 ,",
    "HTTPS://entrepot.recherche.data.gouv.fr/dataset.xhtml?persistentId=doi:10.57745/ABC ",
    "HTTPS://dataverse.no/dataset.xhtml?persistentId=doi:10.18710/X\n",
    " datadryad.org/datasets/doi%3A\n",
    "http://https://www.dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/Z.v2",
    "figshare.com/projects/My_project/133332 ",
    "https://dx.doi.org/dataverse.harvard.edu/dataset.xhtml?id=12345/",
    "http://10.48420/16744483.",
    "http://10.72721/abc;",
    " figshare.com/articles/dataset/name/123456 ",
    "https://doi.org/10.26180/19095317.v1.)",
    "https://doi.org/figshare.com/articles/figure/Fig_1/12/3\n",
    "HTTPS://datadryad.org/datasets/doi%3A//",
    "https://tandf.figshare.com/articles/journal_contribution/x/987.",
    "https://dx.doi.org/10.5061/dryad.j1fd7\n",
    "doi:10.18167/DVN1/T0DMFJ.//",
    "doi:figshare.com/articles/dataset/name/123456?file=999 ",
    " figshare.le.ac.uk/articles/dataset/x/555\n",
    "doi:DataVerse.Harvard.edu/dataset.xhtml?persistentId=DOI:10.7910/DVN/X;",
    "doi:figshare.com/articles/123456.",
    " 10.26180/abc.",
    "http://10.58032/abc",
    "10.5061/DRYAD.J1FD7.",
    "10.6084/m9.figshare.abc ",
    "datadryad.org/dataset/doi:.",
    "https://dx.doi.org/dataverse.harvard.edu/citation?persistentId=doi:10.7910/DVN/Q&x=1\n",
    "10.17026/dans-x1.",
]


def fn_case(id_: str, r: str, py: str, args: dict[str, Any], compare: Any = None) -> None:
    c: dict[str, Any] = {"id": id_, "r": r, "py": py, "args": args}
    if compare:
        c["compare"] = compare
    cases.append(c)


def expr_case(id_: str, r: str, py: str, mock: bool = True, compare: Any = None) -> None:
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


def dl_case(id_: str, r_body: str, py_body: str) -> None:
    expr_case(id_, tmp_r(r_body), tmp_py(py_body))


# ---------------------------------------------------------------- links
TP_R = f"test_paper({r_chr(FUZZ_TEXT)}, {r_chr(FUZZ_URL)})"
TP_PY = f'__import__("pytacheck").test_paper({py_list(FUZZ_TEXT)}, {py_list(FUZZ_URL)})'
TEXT_ONLY = (
    "see 10.5061/dryad.abc, figshare.com/articles/x/12 (10.26180/19095317.v1.) and "
    "dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/Q"
)
for fn, mod in [
    ("dryad_links", "dryad"),
    ("figshare_links", "figshare"),
    ("dataverse_links", "dataverse"),
]:
    expr_case(
        f"{fn}.review.generated_shapes",
        f"{fn}({TP_R})",
        f"lambda m: m.{mod}.{fn}({TP_PY})",
        mock=False,
        compare=IGNORE_PID,
    )
    fn_case(
        f"{fn}.review.text_only",
        fn,
        f"pytacheck.archives.{mod}.{fn}",
        {"paper": {"$test_paper": {"text": [TEXT_ONLY]}}},
        compare=IGNORE_PID,
    )
    fn_case(
        f"{fn}.review.url_only",
        fn,
        f"pytacheck.archives.{mod}.{fn}",
        {
            "paper": {
                "$test_paper": {
                    "text": [],
                    "url": [
                        "https://doi.org/10.5061/dryad.abc/",
                        "https://figshare.com/s/abc123",
                        "https://figshare.com/articles/x/12",
                        "https://doi.org/10.34894/Q",
                        "https://dataverse.nl/dataset.xhtml?persistentId=doi:10.34894/Q",
                    ],
                }
            }
        },
        compare=IGNORE_PID,
    )

# ---------------------------------------------------------------- *_info
expr_case(
    "dryad_info.review.first_unfound",
    online(
        'dryad_info(c("10.5061/dryad.missing", "10.5061/dryad.notjson", "10.5061/dryad.j1fd7"))'
    ),
    'lambda m: m.dryad.dryad_info(["10.5061/dryad.missing", "10.5061/dryad.notjson", '
    '"10.5061/dryad.j1fd7"])',
)
expr_case(
    "dryad_info.review.column_clash",
    online(
        'dryad_info(data.frame(u = c("10.5061/dryad.j1fd7", "10.5061/dryad.missing"), '
        'title = "mine", error = "old", files = "x"))'
    ),
    'lambda m: m.dryad.dryad_info(__import__("pandas").DataFrame({"u": ["10.5061/dryad.j1fd7", '
    '"10.5061/dryad.missing"], "title": "mine", "error": "old", "files": "x"}))',
)
expr_case(
    "dryad_info.review.named_authors",
    online('dryad_info("https://datadryad.org/dataset/doi:10.5061/dryad.rev1")'),
    'lambda m: m.dryad.dryad_info("https://datadryad.org/dataset/doi:10.5061/dryad.rev1")',
)
expr_case(
    "dryad_info.review.na",
    online("dryad_info(NA)"),
    "lambda m: m.dryad.dryad_info(float('nan'))",
)
expr_case(
    "dryad_info.review.missing_id_col_error",
    online('dryad_info(data.frame(u = "10.5061/dryad.j1fd7"), id_col = "nope")'),
    'lambda m: m.dryad.dryad_info(__import__("pandas").DataFrame({"u": ["10.5061/dryad.j1fd7"]}), '
    'id_col="nope")',
)
expr_case(
    "figshare_info.review.first_unfound",
    online(
        'figshare_info(c("1234", "https://figshare.com/articles/dataset/x/18093368", "19095317"))'
    ),
    'lambda m: m.figshare.figshare_info(["1234", "https://figshare.com/articles/dataset/x/18093368", '
    '"19095317"])',
)
expr_case(
    "figshare_info.review.numeric_vector",
    online("figshare_info(c(18093368, 1234, NA, 18093368))"),
    "lambda m: m.figshare.figshare_info([18093368.0, 1234.0, float('nan'), 18093368.0])",
)
expr_case(
    "figshare_info.review.string_license_error",
    online('figshare_info("700003")'),
    'lambda m: m.figshare.figshare_info("700003")',
)
expr_case(
    "figshare_info.review.empty_fields",
    online('figshare_info("https://figshare.com/articles/dataset/x/700004")'),
    'lambda m: m.figshare.figshare_info("https://figshare.com/articles/dataset/x/700004")',
)
expr_case(
    "figshare_info.review.project_mixed_ids",
    online(
        'figshare_info(data.frame(u = c("https://figshare.com/projects/Mixed/700500", '
        '"https://figshare.com/projects/Mixed/700500", "700004"), n = 1:3))'
    ),
    'lambda m: m.figshare.figshare_info(__import__("pandas").DataFrame({"u": '
    '["https://figshare.com/projects/Mixed/700500", "https://figshare.com/projects/Mixed/700500", '
    '"700004"], "n": [1, 2, 3]}))',
)
expr_case(
    "figshare_info.review.id_col_is_id",
    online(
        "figshare_info(figshare_links(test_paper(url = c("
        '"https://figshare.com/articles/dataset/x/18093368", "https://figshare.com/s/abc"))), '
        'id_col = "figshare_id")'
    ),
    'lambda m: m.figshare.figshare_info(m.figshare.figshare_links(__import__("pytacheck").test_paper('
    '[], ["https://figshare.com/articles/dataset/x/18093368", "https://figshare.com/s/abc"])), '
    'id_col="figshare_id")',
    compare=IGNORE_PID,
)
expr_case(
    "dryad_info.review.id_col_is_doi",
    online(
        "dryad_info(dryad_links(test_paper(url = c("
        '"https://doi.org/10.5061/dryad.j1fd7", "https://datadryad.org/stash/dataset/x"))), '
        'id_col = "dryad_doi")'
    ),
    'lambda m: m.dryad.dryad_info(m.dryad.dryad_links(__import__("pytacheck").test_paper('
    '[], ["https://doi.org/10.5061/dryad.j1fd7", "https://datadryad.org/stash/dataset/x"])), '
    'id_col="dryad_doi")',
    compare=IGNORE_PID,
)
expr_case(
    "dryad_info.review.id_col_is_doi_reordered",
    online(
        "(function(l) dryad_info(l[2:1, ], id_col = 6))(dryad_links(test_paper(url = c("
        '"https://doi.org/10.5061/dryad.j1fd7", "https://datadryad.org/stash/dataset/x"))))'
    ),
    "(lambda m: (lambda l: m.dryad.dryad_info(l.iloc[[1, 0]], id_col=6))(m.dryad.dryad_links("
    '__import__("pytacheck").test_paper([], ["https://doi.org/10.5061/dryad.j1fd7", '
    '"https://datadryad.org/stash/dataset/x"]))))',
    compare=IGNORE_PID,
)
DV = "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:"
expr_case(
    "dataverse_info.review.first_parse_error",
    online(
        f'dataverse_info(c("{DV}10.7910/DVN/HTML", "{DV}10.7910/DVN/NOVER", "{DV}10.7910/DVN/REV2"))'
    ),
    f'lambda m: m.dataverse.dataverse_info(["{DV}10.7910/DVN/HTML", "{DV}10.7910/DVN/NOVER", '
    f'"{DV}10.7910/DVN/REV2"])',
)
expr_case(
    "dataverse_info.review.string_data_error",
    online(f'dataverse_info("{DV}10.7910/DVN/STRDATA")'),
    f'lambda m: m.dataverse.dataverse_info("{DV}10.7910/DVN/STRDATA")',
)
expr_case(
    "dataverse_info.review.table_duplicates",
    online(
        f'dataverse_info(data.frame(u = c("{DV}10.7910/DVN/REV2", NA, "{DV}10.7910/DVN/REV2", '
        f'"https://doi.org/10.7910/DVN/REV2"), title = "clash"), id_col = 1)'
    ),
    f'lambda m: m.dataverse.dataverse_info(__import__("pandas").DataFrame({{"u": ["{DV}10.7910/DVN/REV2", '
    f'None, "{DV}10.7910/DVN/REV2", "https://doi.org/10.7910/DVN/REV2"], "title": "clash"}}), id_col=1)',
)

# ---------------------------------------------------------------- downloads
dl_case(
    "figshare_file_download.review.awkward_files",
    'figshare_file_download("https://figshare.com/articles/dataset/x/700001", download_to = d)',
    'm.figshare.figshare_file_download("https://figshare.com/articles/dataset/x/700001", '
    "download_to=d)",
)
dl_case(
    "figshare_file_download.review.total_cap_ties",
    'figshare_file_download("700002", download_to = d, max_file_size = Inf)',
    'm.figshare.figshare_file_download("700002", download_to=d, max_file_size=float("inf"))',
)
dl_case(
    "figshare_file_download.review.file_cap_missing_size",
    'figshare_file_download(c("700002", "https://figshare.com/projects/P/133332", NA), download_to = d)',
    'm.figshare.figshare_file_download(["700002", "https://figshare.com/projects/P/133332", None], '
    "download_to=d)",
)
dl_case(
    "figshare_file_download.review.nothing_valid",
    'figshare_file_download(c("https://figshare.com/s/abc", NA), download_to = d)',
    'm.figshare.figshare_file_download(["https://figshare.com/s/abc", None], download_to=d)',
)
dl_case(
    "dryad_file_download.review.awkward_files",
    'dryad_file_download("https://datadryad.org/dataset/doi:10.5061/dryad.rev1", download_to = d)',
    'm.dryad.dryad_file_download("https://datadryad.org/dataset/doi:10.5061/dryad.rev1", '
    "download_to=d)",
)
dl_case(
    "dataverse_file_download.review.label_fallbacks",
    'dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/REV2", download_to = d)',
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/REV2", '
    "download_to=d)",
)
dl_case(
    "dataverse_file_download.review.no_version",
    'dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/NOVER", download_to = d)',
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/NOVER", '
    "download_to=d)",
)
dl_case(
    "dataverse_file_download.review.duplicate_pairs",
    'dataverse_file_download("dataverse.harvard.edu", c("10.7910/DVN/REV2", "10.7910/DVN/REV2", '
    '"10.7910/DVN/STRDATA", "10.7910/DVN/NOVER"), download_to = d)',
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", ["10.7910/DVN/REV2", '
    '"10.7910/DVN/REV2", "10.7910/DVN/STRDATA", "10.7910/DVN/NOVER"], download_to=d)',
)
dl_case(
    "dataverse_file_download.review.string_data_error",
    'dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/STRDATA", download_to = d)',
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/STRDATA", '
    "download_to=d)",
)

# ---------------------------------------------------------------- verify
VERIFY_R = (
    'writeLines("x,y", file.path(d, "a.csv")); '
    'files <- data.frame(id = c("1", "2"), key = c("a.csv", "b"), path = c("a.csv", NA), '
    'size = c("4", "x"), checksum = c(NA, NA), checksum_type = c("md5", NA){extra}); '
    "metacheck:::.{fn}_verify_downloads(files, d)"
)
VERIFY_PY = (
    'm.{mod}._{fn}_verify_downloads((m.write_files(d, {{"a.csv": "x,y"}}), pd.DataFrame({{'
    '"id": pd.array(["1", "2"], dtype="string"), "key": pd.array(["a.csv", "b"], dtype="string"), '
    '"path": pd.array(["a.csv", None], dtype="string"), "size": pd.array(["4", "x"], dtype="string"), '
    '"checksum": pd.array([None, None], dtype="string"), '
    '"checksum_type": pd.array(["md5", None], dtype="string"){extra}}}))[1], d)'
)
for fn, mod in [("dryad", "dryad"), ("figshare", "figshare"), ("dataverse", "dataverse")]:
    dl_case(
        f".{fn}_verify_downloads.review.character_size",
        VERIFY_R.format(extra=", downloaded = c(TRUE, TRUE)", fn=fn),
        VERIFY_PY.format(
            extra=', "downloaded": pd.array([True, True], dtype="boolean")', fn=fn, mod=mod
        ),
    )
    dl_case(
        f".{fn}_verify_downloads.review.no_downloaded_error",
        VERIFY_R.format(extra="", fn=fn),
        VERIFY_PY.format(extra="", fn=fn, mod=mod),
    )


# ---------------------------------------------------------------- round 2
# URL escapes that decode to invalid UTF-8: R's regex functions refuse the
# decoded string (.dryad_doi() warns and finds nothing; .dataverse_parse()'s
# sub() is an error, which aborts dataverse_links()).
BAD_UTF8 = ["10.5061/dryad.abc%E2%80", "%FF10.5061/dryad.abc", "10.5061/dryad.ok"]
fn_case(
    ".dryad_doi.review.invalid_utf8",
    "metacheck:::.dryad_doi",
    "pytacheck.archives.dryad._dryad_doi",
    {"dryad_url": {"$expr": {"r": r_chr(BAD_UTF8), "py": py_list(BAD_UTF8)}}},
)
BAD_DV = [
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/Y",
    "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/X%FF",
]
fn_case(
    ".dataverse_parse.review.invalid_utf8_error",
    "metacheck:::.dataverse_parse",
    "pytacheck.archives.dataverse._dataverse_parse",
    {"url": {"$expr": {"r": r_chr(BAD_DV), "py": py_list(BAD_DV)}}},
)
fn_case(
    ".dataverse_parse.review.valid_escapes",
    "metacheck:::.dataverse_parse",
    "pytacheck.archives.dataverse._dataverse_parse",
    {
        "url": [
            "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/X%E2%80%93.",
            "https://dataverse.nl/dataset.xhtml?persistentId=doi%3A10.34894%2FA%C3%A9",
        ]
    },
)
BAD_TEXT = [
    "See datadryad.org/dataset/doi%3A10.5061%2Fdryad.abc%FF here.",
    "Also datadryad.org/dataset/doi%3A10.5061%2Fdryad.fine and 10.5061/dryad.plain.",
]
for fn, mod in [("dryad_links", "dryad"), ("figshare_links", "figshare")]:
    fn_case(
        f"{fn}.review.invalid_utf8",
        fn,
        f"pytacheck.archives.{mod}.{fn}",
        {"paper": {"$test_paper": {"text": BAD_TEXT}}},
        compare=IGNORE_PID,
    )
fn_case(
    "dataverse_links.review.invalid_utf8_error",
    "dataverse_links",
    "pytacheck.archives.dataverse.dataverse_links",
    {
        "paper": {
            "$test_paper": {
                "text": [
                    "See dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/X%FF."
                ]
            }
        }
    },
)
expr_case(
    "dryad_info.review.invalid_utf8",
    online('dryad_info(c("10.5061/dryad.abc%FF", NA))'),
    'lambda m: m.dryad.dryad_info(["10.5061/dryad.abc%FF", None])',
)

# An empty paper list: paper_table() gives a table without columns and
# text_search() one without `text`, so `href` names dryad_links()'s own NULL
# local variable -- R returns text_id, paper_id (logical) and then href.
for fn, mod in [
    ("dryad_links", "dryad"),
    ("figshare_links", "figshare"),
    ("dataverse_links", "dataverse"),
]:
    expr_case(
        f"{fn}.review.empty_paperlist",
        f'{fn}(paperlist(test_paper("a"))[0])',
        f'lambda m: m.{mod}.{fn}(__import__("pytacheck").PaperList([]))',
        mock=False,
    )

# A 200 answer with an empty body: httr2::resp_body_raw() refuses it outside
# the tryCatch, so the download aborts (a vectorised call warns and drops it).
dl_case(
    "figshare_file_download.review.empty_body_error",
    'figshare_file_download("700006", download_to = d)',
    'm.figshare.figshare_file_download("700006", download_to=d)',
)
dl_case(
    "figshare_file_download.review.empty_body_multi",
    'figshare_file_download(c("700006", "700001"), download_to = d)',
    'm.figshare.figshare_file_download(["700006", "700001"], download_to=d)',
)
dl_case(
    "dryad_file_download.review.empty_body_error",
    'dryad_file_download("10.5061/dryad.rev2", download_to = d)',
    'm.dryad.dryad_file_download("10.5061/dryad.rev2", download_to=d)',
)
dl_case(
    "dryad_file_download.review.empty_body_multi",
    'dryad_file_download(c("10.5061/dryad.rev2", "10.5061/dryad.rev1"), download_to = d)',
    'm.dryad.dryad_file_download(["10.5061/dryad.rev2", "10.5061/dryad.rev1"], download_to=d)',
)
dl_case(
    "dataverse_file_download.review.empty_body_error",
    'dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/EMPTY", download_to = d)',
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", "10.7910/DVN/EMPTY", '
    "download_to=d)",
)
dl_case(
    "dataverse_file_download.review.empty_body_multi",
    'dataverse_file_download("dataverse.harvard.edu", c("10.7910/DVN/EMPTY", "10.7910/DVN/REV2"), '
    "download_to = d)",
    'm.dataverse.dataverse_file_download("dataverse.harvard.edu", ["10.7910/DVN/EMPTY", '
    '"10.7910/DVN/REV2"], download_to=d)',
)


# Scalar fields given as one-element JSON arrays: list columns in R;
# two elements: "replacement has 2 rows, data has 1".
expr_case(
    "figshare_info.review.list_fields",
    online('figshare_info("700007")'),
    'lambda m: m.figshare.figshare_info("700007")',
)
expr_case(
    "figshare_info.review.long_field_error",
    online('figshare_info("700008")'),
    'lambda m: m.figshare.figshare_info("700008")',
)
expr_case(
    "dataverse_info.review.list_fields",
    online(
        'dataverse_info("https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/LISTS")'
    ),
    'lambda m: m.dataverse.dataverse_info("https://dataverse.harvard.edu/dataset.xhtml?'
    'persistentId=doi:10.7910/DVN/LISTS")',
)

expr_case(
    "figshare_info.review.object_files",
    online('figshare_info("700009")'),
    'lambda m: m.figshare.figshare_info("700009")',
)
dl_case(
    "figshare_file_download.review.object_files",
    'figshare_file_download("700009", download_to = d)',
    'm.figshare.figshare_file_download("700009", download_to=d)',
)


def main() -> None:
    doc = {"area": "archives_d1_review", "cases": cases}
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(
            "# Adversarial-review parity cases for R/archive-dryad.R, R/archive-figshare.R and\n"
            "# R/archive-dataverse.R. Generated by tests/archives_d1/make_review_cases.py; mocked\n"
            "# HTTP lives in tests/archives_d1/mocks (make_mocks.py + make_review_mocks.py).\n"
        )
        yaml.dump(doc, fh, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=10_000)
    print(len(cases), "cases")


if __name__ == "__main__":
    main()
