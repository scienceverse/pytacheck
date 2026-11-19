"""Generate parity/cases/archives_services.yaml (R and Python snippets side by side).

The services of metacheck 0.4.4.18 (#660, #478, #648, #667): sequence and expression archives
(R/archive-seq.R), CRAN (R/archive-cran.R), ICPSR and openICPSR (R/archive-icpsr.R), Handles
(R/handle-links.R), file-sharing services (R/file-share.R), one OSF project as one repository
(R/repo-link-versions.R), what a file is for repo_check's counts (R/file_category.R) and the two
files R leaves behind (R/file-naming.R). Run from the repository root:
``python -m tests.archives_services.make_cases``, then
``python -m parity generate --area archives_services``. What a host would answer is stood in for
on both sides (``.seq_get()``, ``.batch_query()`` and ``.handle_resolve()`` mocked).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from tests.archives_d2.make_parity_cases import Dumper, py_list, r_chr, tp_py, tp_r

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "archives_services.yaml"
RUN = "tests.archives_services.parity_support.run"
IGNORE_PID = {"ignore": ["paper_id"]}
cases: list[dict[str, Any]] = []


def expr_case(id_: str, r: str, py: str, compare: dict[str, Any] | None = None) -> None:
    c: dict[str, Any] = {
        "id": id_,
        "r": "identity",
        "py": RUN,
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }
    if compare:
        c["compare"] = compare
    cases.append(c)


def rq(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def r_named_list(d: dict[str, str]) -> str:
    return "list(" + ", ".join(f"{rq(k)} = {rq(v)}" for k, v in d.items()) + ")"


def py_dict(d: dict[str, Any]) -> str:
    return "{" + ", ".join(f"{k!r}: {v!r}" for k, v in d.items()) + "}"


def seq_mock_r(call: str, answers: dict[str, str]) -> str:
    return (
        f"testthat::with_mocked_bindings({call}, .seq_get = function(url) "
        f'{r_named_list(answers)}[[url]], .package = "metacheck")'
    )


def seq_mock_py(call: str, answers: dict[str, str]) -> str:
    return f"m.seq_mock({py_dict(answers)}, lambda: {call})"


# ---------------------------------------------------------------- file-sharing services
FS_TEXT = [
    "Data are on https://drive.google.com/drive/folders/1AbC?usp=sharing.",
    "Code: www.dropbox.com/s/abc123/code.zip?dl=0 and https://1drv.ms/u/s!AbC",
    "The survey is at https://docs.google.com/forms/d/e/1FA/viewform",
    "Box: https://app.box.com/s/xyz9 ; SURF: https://surfdrive.surf.nl/files/index.php/s/AbCd",
    "We used Dropbox to share drafts among the authors.",
]
for cid, text, url in [
    ("mixed", FS_TEXT, None),
    ("url_only", ["No link in the text."], ["https://www.dropbox.com/s/abc123/data.csv?dl=0"]),
    ("none", ["Data are at https://osf.io/abcde."], None),
    (
        "archived",
        [
            "See the list at http://web.archive.org/web/20220203164905/"
            "https://docs.google.com/spreadsheets/d/1LNF5/edit."
        ],
        None,
    ),
    (
        "shortened",
        ["The set (https://nihcc.app.box.com/v/ChestXray-NIHCC) is public."],
        ["app.box.com/v/ChestXray-NIHCC"],
    ),
    (
        "others",
        [
            "Files at https://we.tl/t-abc, https://mega.nz/folder/abc#key, "
            "https://www.icloud.com/iclouddrive/0abc and https://pan.baidu.com/s/1abc.",
            "Also https://univ.sharepoint.com/:f:/s/team/abc and https://wetransfer.com/downloads/abc.",
        ],
        None,
    ),
]:
    expr_case(
        f"file_share_links.{cid}",
        f"file_share_links({tp_r(text, url)})",
        f"lambda m: m.file_share.file_share_links({tp_py(text, url)})",
        IGNORE_PID,
    )
fn_urls = ["https://drive.google.com/x", "dropbox.com/s/a", "https://example.org/", "box.com/s/x"]
expr_case(
    ".file_share_service.vector",
    f"metacheck:::.file_share_service({r_chr(fn_urls)})",
    f"lambda m: m.file_share._file_share_service({py_list(fn_urls)})",
)

# ---------------------------------------------------------------- CRAN
CRAN_TEXT = [
    "A stable version can be downloaded through CRAN at "
    "http://cran.r-project.org/web/packages/Reol/index.html.",
    "We used lme4 (https://CRAN.R-project.org/package=lme4) and its vignette "
    "https://cran.r-project.org/web/packages/lme4/vignettes/lmerperf.html.",
    "Cite it as 10.32614/CRAN.package.metafor.",
    "R is at https://cran.r-project.org/.",
]
expr_case(
    "cran_links.mixed",
    f"cran_links({tp_r(CRAN_TEXT)})",
    f"lambda m: m.cran.cran_links({tp_py(CRAN_TEXT)})",
    IGNORE_PID,
)
expr_case(
    "cran_links.none",
    f"cran_links({tp_r(['No package here.'])})",
    f"lambda m: m.cran.cran_links({tp_py(['No package here.'])})",
    IGNORE_PID,
)
CRAN_URLS = [
    "https://CRAN.R-project.org/package=Reol",
    "cran.r-project.org/web/packages/lme4/vignettes/lmerperf.html",
    "10.32614/CRAN.package.metafor",
    "https://example.org/",
]
expr_case(
    ".cran_pkg_of.vector",
    f"metacheck:::.cran_pkg_of({r_chr(CRAN_URLS)})",
    f"lambda m: m.cran._cran_pkg_of({py_list(CRAN_URLS)})",
)
PEOPLE = (
    "Roger Bivand [cre, aut] (<https://orcid.org/0000-0003-2392-6140>), Nicholas Lewin-Koh [aut]"
)
expr_case(
    ".cran_people.orcid",
    f"metacheck:::.cran_people({rq(PEOPLE)})",
    f"lambda m: m.cran._cran_people({json.dumps(PEOPLE)})",
)
expr_case(
    ".cran_people.and",
    'metacheck:::.cran_people("A. Author and B. Writer\\nC. Third")',
    'lambda m: m.cran._cran_people("A. Author and B. Writer\\nC. Third")',
)
CRANDB = {
    "https://crandb.r-pkg.org/Reol": json.dumps(
        {
            "Package": "Reol",
            "Title": "Programmatic   Interface to the\nEncyclopedia of Life",
            "Author": "Barb Banbury [aut, cre],\n  Brian O'Meara [aut]",
            "Version": "0.1.2",
            "Date/Publication": "2013-05-11 12:00:00",
            "Description": "Programmatic\n interface to EOL.",
        }
    ),
}
URLS = [
    "https://CRAN.R-project.org/package=Reol",
    "https://CRAN.R-project.org/package=notapackagexyz",
]
expr_case(
    "cran_info.mock.records",
    seq_mock_r(f"cran_info({r_chr(URLS)})", CRANDB),
    f"lambda m: {seq_mock_py(f'm.cran.cran_info({py_list(URLS)})', CRANDB)}",
)

# ---------------------------------------------------------------- ICPSR
ICPSR_TEXT = [
    "Data and code are available at https://doi.org/10.3886/E195681V1.",
    "See https://www.openicpsr.org/openicpsr/project/121688/version/V1/view and 10.3886/E121688.",
    "We used http://www.icpsr.umich.edu/icpsrweb/ICPSR/studies/04652 and 10.3886/ICPSR21600.v17.",
]
expr_case(
    "icpsr_links.mixed",
    f"icpsr_links({tp_r(ICPSR_TEXT)})",
    f"lambda m: m.icpsr.icpsr_links({tp_py(ICPSR_TEXT)})",
    IGNORE_PID,
)
expr_case(
    "icpsr_links.none",
    f"icpsr_links({tp_r(['No ICPSR link.'])})",
    f"lambda m: m.icpsr.icpsr_links({tp_py(['No ICPSR link.'])})",
    IGNORE_PID,
)
ICPSR_LINKS = [
    "icpsr.umich.edu/web/ICPSR/studies/4652",
    "https://www.icpsr.umich.edu/sites/icpsr/view/studies/195681/versions/V1",
    "10.3886/e100462v3",
    "https://example.org/",
]
expr_case(
    ".icpsr_doi_of.vector",
    f"metacheck:::.icpsr_doi_of({r_chr(ICPSR_LINKS)})",
    f"lambda m: m.icpsr._icpsr_doi_of({py_list(ICPSR_LINKS)})",
)
DC = "https://api.datacite.org/dois/"
DATACITE = {
    DC + "10.3886/E195681V1": (
        200,
        {
            "data": {
                "attributes": {
                    "creators": [{"name": "Wu, Stephen"}, {"name": "Ge, Qi"}],
                    "titles": [{"title": "Replication data for: A paper"}],
                    "publicationYear": 2021,
                    "dates": [{"date": "2021-03-04", "dateType": "Issued"}],
                    "descriptions": [{"description": "Data and code."}],
                    "types": {"resourceTypeGeneral": "Dataset"},
                    "relatedIdentifiers": [
                        {"relationType": "IsSupplementTo", "relatedIdentifier": "10.1257/aer.1"}
                    ],
                }
            }
        },
    ),
    DC + "10.3886/ICPSR99999": (404, None),
}
ICPSR_INFO = ["https://doi.org/10.3886/E195681V1", "https://doi.org/10.3886/ICPSR99999"]
batch_r = (
    "testthat::with_mocked_bindings(icpsr_info(" + r_chr(ICPSR_INFO) + "), .batch_query = "
    "function(urls, ...) lapply(urls, function(u) if (grepl('E195681V1', u)) "
    "httr2::response_json(status_code = 200L, body = list(data = list(attributes = list("
    "creators = list(list(name = 'Wu, Stephen'), list(name = 'Ge, Qi')), "
    "titles = list(list(title = 'Replication data for: A paper')), publicationYear = 2021L, "
    "dates = list(list(date = '2021-03-04', dateType = 'Issued')), "
    "descriptions = list(list(description = 'Data and code.')), "
    "types = list(resourceTypeGeneral = 'Dataset'), "
    "relatedIdentifiers = list(list(relationType = 'IsSupplementTo', "
    "relatedIdentifier = '10.1257/aer.1')))))) "
    "else httr2::response(status_code = 404L)), .package = 'metacheck')"
)
expr_case(
    "icpsr_info.mock.records",
    batch_r,
    f"lambda m: m.batch_mock({py_dict({k: list(v) for k, v in DATACITE.items()})}, "
    f"lambda: m.icpsr.icpsr_info({py_list(ICPSR_INFO)}))",
)

# ---------------------------------------------------------------- sequence archives
SEQ_TEXT = [
    "RNA-seq data are deposited in GEO (GSE149523) and the SRA (PRJNA770129, SRR16287660).",
    "The single nucleus reads are deposited (SRR7416431 to SRR7416438).",
    "Data are in ArrayExpress under E-MTAB-6487; see E-ISSN 1234-5678.",
    "All reads were deposited in the DDBJ Sequence Read Archive (DRA007363).",
    "We analysed 20 participants.",
]
expr_case(
    "seq_archive_links.mixed",
    f"seq_archive_links({tp_r(SEQ_TEXT)})",
    f"lambda m: m.seq.seq_archive_links({tp_py(SEQ_TEXT)})",
    IGNORE_PID,
)
expr_case(
    "seq_archive_links.none",
    f"seq_archive_links({tp_r(['No accession here.'])})",
    f"lambda m: m.seq.seq_archive_links({tp_py(['No accession here.'])})",
    IGNORE_PID,
)
MANY = [
    "We reanalysed " + ", ".join(f"GSE{i}" for i in range(1001, 1031)) + ".",
    "Our data have been deposited in GEO (GSE999999).",
]
expr_case(
    "seq_archive_links.many",
    f"seq_archive_links({tp_r(MANY)}, max_n = 5)",
    f"lambda m: m.seq.seq_archive_links({tp_py(MANY)}, max_n=5)",
    IGNORE_PID,
)
ACCS = [
    "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE149523",
    "https://www.ebi.ac.uk/ena/browser/view/SRR7416431",
    "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6487",
    "https://example.org/none",
]
expr_case(
    ".seq_acc_of.vector",
    f"metacheck:::.seq_acc_of({r_chr(ACCS)})",
    f"lambda m: m.seq._seq_acc_of({py_list(ACCS)})",
)
expr_case(
    ".seq_url.types",
    'metacheck:::.seq_url(c("GSE1", "PRJNA2", "E-MTAB-3"), c("geo", "ena", "arrayexpress"))',
    'lambda m: m.seq._seq_url(["GSE1", "PRJNA2", "E-MTAB-3"], ["geo", "ena", "arrayexpress"])',
)
DATES = ["Sep 24 2024", "2024-09-24T10:00:00Z", "not a date", "May 3 2020"]
expr_case(
    ".seq_date.forms",
    f"metacheck:::.seq_date({r_chr(DATES)})",
    f"lambda m: m.seq._seq_date({py_list(DATES)})",
)
GEO = "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE149523"
ENA = "https://www.ebi.ac.uk/ena/browser/view/PRJNA770129"
SRA_RUN = "https://www.ebi.ac.uk/ena/browser/view/SRR7416431"
AE = "https://www.ebi.ac.uk/biostudies/arrayexpress/studies/E-MTAB-6487"
PORTAL = "https://www.ebi.ac.uk/ena/portal/api/"
FIELDS = (
    "&fields=study_accession,secondary_study_accession,study_title,study_description,"
    "center_name,first_public&format=tsv"
)


def study_url(acc: str) -> str:
    return (
        f"{PORTAL}search?result=study&query=study_accession=%22{acc}%22%20OR%20"
        f"secondary_study_accession=%22{acc}%22{FIELDS}"
    )


STUDY_TSV = (
    "study_accession\tsecondary_study_accession\tstudy_title\tstudy_description\tcenter_name\t"
    "first_public\nPRJNA{n}\tSRP1\tA study\tWhat it holds.\tNational Institute of Biological "
    "Sciences\t2021-10-05\n"
)
SEQ_ANSWERS = {
    f"{GEO.split('?')[0]}?acc=GSE149523&targ=self&form=text&view=brief": (
        "^SERIES = GSE149523\n!Series_title = A GEO series\n!Series_summary = One.\n"
        "!Series_summary = Two.\n!Series_submission_date = Sep 24 2024\n"
        "!Series_pubmed_id = 12345\n!Series_contributor = Manish,V,Bais\n"
        "!Series_contributor = Jane,Doe\n!Series_contact_name = Jane,,Doe\n"
        "!Series_contact_institute = Some Institute\n!Series_sample_id = GSM1\n"
        "!Series_sample_id = GSM2\n"
    ),
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id=12345": json.dumps(
        {
            "result": {
                "uids": ["12345"],
                "12345": {
                    "articleids": [
                        {"idtype": "pubmed", "value": "12345"},
                        {"idtype": "doi", "value": "10.1371/Journal.pbio.1"},
                    ]
                },
            }
        }
    ),
    study_url("PRJNA770129"): STUDY_TSV.format(n="770129"),
    f"{PORTAL}filereport?accession=SRR7416431&result=read_run&fields=study_accession&limit=1&format=tsv": (
        "study_accession\nPRJNA477348\n"
    ),
    study_url("PRJNA477348"): STUDY_TSV.format(n="477348"),
    "https://www.ebi.ac.uk/biostudies/api/v1/studies/E-MTAB-6487": json.dumps(
        {
            "accno": "E-MTAB-6487",
            "attributes": [{"name": "ReleaseDate", "value": "2019-01-01"}],
            "section": {
                "type": "Study",
                "attributes": [
                    {"name": "Title", "value": "An experiment"},
                    {"name": "Description", "value": "Some text."},
                ],
                "subsections": [
                    {"type": "Author", "attributes": [{"name": "Name", "value": "Luigi Naldini"}]},
                    {"type": "Organization", "attributes": [{"name": "Name", "value": "SR-Tiget"}]},
                    {"type": "Publication", "attributes": [{"name": "DOI", "value": "10.1/ABC"}]},
                ],
            },
        }
    ),
}
SEQ_URLS = [GEO, ENA, SRA_RUN, AE, "https://www.ebi.ac.uk/ena/browser/view/PRJEB00000"]
expr_case(
    "seq_archive_info.mock.records",
    seq_mock_r(f"seq_archive_info({r_chr(SEQ_URLS)})", SEQ_ANSWERS),
    f"lambda m: {seq_mock_py(f'm.seq.seq_archive_info({py_list(SEQ_URLS)})', SEQ_ANSWERS)}",
)
SIZE_ANSWERS = {
    f"{PORTAL}filereport?accession=PRJNA770129&result=read_run&fields=run_accession,fastq_bytes,submitted_bytes&format=tsv": (
        "run_accession\tfastq_bytes\tsubmitted_bytes\nSRR1\t100;200\t\nSRR2\t\t300\nSRR3\t\t\n"
    ),
    "https://www.ebi.ac.uk/biostudies/api/v1/studies/E-MTAB-6487/info": json.dumps({"files": 7}),
    f"{GEO.split('?')[0]}?acc=GSE149523&targ=self&form=text&view=brief": SEQ_ANSWERS[
        f"{GEO.split('?')[0]}?acc=GSE149523&targ=self&form=text&view=brief"
    ],
    "https://ftp.ncbi.nlm.nih.gov/geo/series/GSE149nnn/GSE149523/suppl/": (
        "<a>x</a> 2020-04-01 10:11  550K\n<a>y</a> 2020-04-01 10:11  1.2M\n"
    ),
}
SIZE_INFO_R = (
    "data.frame(seq_url = c("
    + ", ".join(rq(u) for u in (ENA, GEO, AE))
    + "), accession = c('PRJNA770129', 'GSE149523', 'E-MTAB-6487'), "
    "repo_type = c('ena', 'geo', 'arrayexpress'), stringsAsFactors = FALSE)"
)
SIZE_INFO_PY = (
    "m.pd.DataFrame({'seq_url': "
    + py_list([ENA, GEO, AE])
    + ", 'accession': ['PRJNA770129', 'GSE149523', 'E-MTAB-6487'], "
    "'repo_type': ['ena', 'geo', 'arrayexpress']})"
)
expr_case(
    "seq_archive_size.mock.sizes",
    seq_mock_r(f"seq_archive_size({SIZE_INFO_R})", SIZE_ANSWERS),
    f"lambda m: {seq_mock_py(f'm.seq.seq_archive_size({SIZE_INFO_PY})', SIZE_ANSWERS)}",
)

# ---------------------------------------------------------------- Handles
HANDLE_TEXT = [
    "All data are available at http://hdl.handle.net/11667/195.",
    "See the thesis at http://hdl.handle.net/1721.1/114075.",
]
HANDLES = {
    "11667/195": "https://datastorre.stir.ac.uk/handle/11667/195",
    "1721.1/114075": "https://example.org/thesis/114075",
}
expr_case(
    "handle_links.none",
    f"handle_links({tp_r(['No Handle here.'])})",
    f"lambda m: m.handle.handle_links({tp_py(['No Handle here.'])})",
    IGNORE_PID,
)
expr_case(
    "handle_links.mock.resolved",
    "testthat::with_mocked_bindings(handle_links("
    + tp_r(HANDLE_TEXT)
    + "), .handle_resolve = function(handle) "
    + r_named_list(HANDLES)
    + '[[handle]], .package = "metacheck")',
    f"lambda m: m.handle_mock({py_dict(HANDLES)}, lambda: m.handle.handle_links({tp_py(HANDLE_TEXT)}))",
    IGNORE_PID,
)

# ---------------------------------------------------------------- one OSF project
OSF_FORMS = [
    "https://doi.org/10.17605/OSF.IO/W4Q6J",
    "osf.io/w4q6j",
    "https://osf.io/qhwds/overview?view_only=abc",
    "osf.io/pcjnb/files/qkx9f",
    "https://github.com/Owner/Repo/",
]
expr_case(
    ".repo_link_same_key.osf",
    f"metacheck:::.repo_link_same_key({r_chr(OSF_FORMS)})",
    f"lambda m: m.link_versions._repo_link_same_key({py_list(OSF_FORMS)})",
)
OSF_REPOS = [
    "osf.io/qhwds",
    "https://osf.io/qhwds/overview?view_only=abc",
    "https://osf.io/pcjnb/files/qkx9f",
    "osf.io/pcjnb",
    "https://osf.io/zzzzz/files/osfstorage/1",
]
expr_case(
    ".repo_links_tidy.osf_forms",
    "(function() { repos <- data.frame(paper_id = 'p', repo_type = 'osf', repo_url = "
    + r_chr(OSF_REPOS)
    + "); paper <- test_paper('No text needed here.'); paper$paper_id <- 'p'; "
    "metacheck:::.repo_links_tidy(repos, paper) })()",
    "lambda m: (lambda p: m.link_versions._repo_links_tidy(m.pd.DataFrame({'paper_id': 'p', "
    "'repo_type': 'osf', 'repo_url': "
    + py_list(OSF_REPOS)
    + "}), p))(m.pc.test_paper(['No text needed here.']))",
)

# ---------------------------------------------------------------- file kinds and naming
KIND_NAMES = [
    "data.nc",
    "README.md",
    "run.sh",
    "scan.nii.gz",
    "paper.zip",
    "backup.tar.gz",
    "readme.txt",
    "x.7z",
]
KIND_TYPES = [
    "data",
    "documentation",
    "code",
    "data",
    "unknown",
    "unknown",
    "documentation",
    "unknown",
]
KIND_SIZES = [10, 0, 5, 7, None, 3, None, 1]
expr_case(
    ".repo_file_kind.mixed",
    f"metacheck:::.repo_file_kind({r_chr(KIND_NAMES)}, {r_chr(KIND_TYPES)}, "
    "c(10, 0, 5, 7, NA, 3, NA, 1))",
    f"lambda m: m.category._repo_file_kind({py_list(KIND_NAMES)}, {py_list(KIND_TYPES)}, "
    "[10, 0, 5, 7, None, 3, None, 1])",
)
expr_case(
    ".repo_file_kind.no_size",
    f"metacheck:::.repo_file_kind({r_chr(KIND_NAMES)}, {r_chr(KIND_TYPES)})",
    f"lambda m: m.category._repo_file_kind({py_list(KIND_NAMES)}, {py_list(KIND_TYPES)})",
)
USELESS = [
    ".Rhistory",
    ".Rapp.history",
    "code/.RDataTmp",
    ".RDataTmp1",
    "x/.RDataTmp12",
    ".RData",
    "notes.tmp",
    "a/.Rapp.history.bak",
]
expr_case(
    ".useless_file_kind.r_leftovers",
    f"metacheck:::.useless_file_kind({r_chr(USELESS)})",
    f"lambda m: m.file_tree.useless_file_kind({py_list(USELESS)})",
)
expr_case(
    ".useless_file_kind.r_leftovers.repo_check",
    f"metacheck:::.useless_file_kind({r_chr(USELESS)})",
    f"lambda m: m.useless.useless_file_kind({py_list(USELESS)})",
)


def main() -> None:
    header = (
        "# Parity cases for the services of metacheck 0.4.4.18: sequence and expression archives,\n"
        "# CRAN, ICPSR, Handles, file-sharing services (#660, #478), one OSF project as one\n"
        "# repository, file kinds and naming (#648). Generated by\n"
        "# tests/archives_services/make_cases.py; what a host answers is mocked on both sides.\n"
    )
    OUT.write_text(
        header
        + yaml.dump(
            {"area": "archives_services", "cases": cases},
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
