"""File classification: data_classify_files(), .data_doc_role(), .is_r_package_file().

``file_category()``/``filetype()`` belong to metacheck.fileinfo (ported
separately); these tests stub them with values recorded from R
(``data/make_classify_ref.R``) and compare the classification layered on top
of them to metacheck's.  Ports of the classification tests in
test-data-checks.R and test-file_category.R.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metacheck.datacheck import files as F

DATA = Path(__file__).parent / "data"
REF = json.loads((DATA / "classify_ref.json").read_text(encoding="utf-8"))


@pytest.fixture
def stub_category(monkeypatch: pytest.MonkeyPatch) -> None:
    cats = dict(zip(REF["names"], REF["file_category"], strict=True))
    types = dict(zip(REF["names"], REF["filetype"], strict=True))
    monkeypatch.setattr(F, "_file_category", lambda names: [cats[n] for n in names])
    monkeypatch.setattr(F, "_filetype", lambda names: [types[n] for n in names])


def test_classify_matches_r(stub_category: None) -> None:
    assert F.data_classify_files(REF["names"]) == REF["classify"]


def test_classify_with_paths_matches_r(stub_category: None) -> None:
    got = F.data_classify_files(REF["path_names"], REF["paths"])
    assert got == REF["classify_paths"]


def test_doc_role_matches_r(stub_category: None) -> None:
    assert F._data_doc_role(REF["names"]) == REF["doc_role"]


@pytest.mark.parametrize("which", ["root_with_study", "root_only"])
def test_is_r_package_file_matches_r(stub_category: None, which: str) -> None:
    got = F._is_r_package_file(REF["pkg_sets"][which])
    assert got == REF["pkg"][which]


def test_classify_empty() -> None:
    assert F.data_classify_files([]) == []


def test_fixed_extensions_win(stub_category: None) -> None:
    # genomic / eye-tracking formats are data regardless of the name (the
    # compressed .fasta.gz too); formats outside the registry stay unknown
    got = F.data_classify_files(
        ["sample.fasta", "sample.fasta.gz", "trial.edf", "reads.fastq", "seq.bam"]
    )
    assert got == ["data", "data", "data", "data", "unknown"]


def test_registry_shape() -> None:
    reg = F.ext_registry()
    assert len(reg) == len(set(reg["ext"]))
    assert {"ext", "data_type", "readable", "code_lang", "mime"} <= set(reg.columns)
    assert set(reg.loc[reg["readable"], "ext"]) == set(F._READABLE_EXTENSIONS)


# -- upstream test-data-checks.R / test-file_category.R ----------------------------


def test_classifies_by_name_and_extension(stub_category: None) -> None:
    files = ["data.csv", "analysis.R", "README.md", "codebook.xlsx", "photo.png",
             "experiment.psyexp", "notes.pdf", "archive.zip"]  # fmt: skip
    assert F.data_classify_files(files) == [
        "data",
        "code",
        "documentation",  # readme folds into documentation
        "documentation",  # codebook folds into documentation
        "materials",  # asset folds into materials
        "materials",  # software (.psyexp) folds into materials
        "documentation",  # supplemental (PDF) folds into documentation
        "unknown",  # a never-opened archive has unknown content
    ]
    roles = F._data_doc_role(files)
    assert roles[2] == "readme"
    assert roles[3] == "codebook"
    assert roles[6] == "supplemental"
    assert roles[0] is None
    assert roles[4] is None
    assert F._data_doc_role(["ro-crate-metadata.json"]) == ["readme"]


def test_genomic_formats_compressed_or_not(stub_category: None) -> None:
    contents = ["sample.fasta", "sample.fa", "sample.fq", "sample.fastq",
                "sample.fasta.gz", "sample.fa.gz", "sample.fq.gz", "sample.fastq.gz"]  # fmt: skip
    assert F.data_classify_files(contents) == ["data"] * len(contents)
    assert F.data_classify_files(["random_archive.tar.gz"]) == ["unknown"]
    assert F.data_classify_files(["random.gz"]) == ["unknown"]
    assert F.data_classify_files(["sample.dat.gz"]) == ["unknown"]


# -- upstream test-data-checks.R: domain data formats by extension alone (issue #441) --


def test_domain_formats_are_data_by_extension_alone() -> None:
    # GIS vector data, phylogenetic trees, mass spectrometry, 3D scans and plain-text
    # tables sit at the root of an archive with no data-named folder to hint at them
    files = ["shoreline.shp", "shoreline.dbf", "shoreline.shx", "shoreline.prj",
             "shoreline.sbn", "shoreline.sbx", "shoreline.cpg", "map.gpkg",
             "tree.nex", "tree.nwk", "tree.tre", "tree.phy",
             "spectrum.mzxml", "spectrum.mztab", "scan.ply",
             "results.tab", "results.table"]  # fmt: skip
    assert F.data_classify_files(files) == ["data"] * len(files)
    assert F.data_classify_files([f.upper() for f in files]) == ["data"] * len(files)
    # .stl stays "materials" (a 3D-printable model), not folded in with .ply
    assert F.data_classify_files(["model.stl"]) == ["materials"]


def test_data_format_of_plain_text_tables_and_domain_formats() -> None:
    # .tab/.table are delimited text, read like .csv/.txt/.tsv/.dat
    assert F.data_format(["tab", "table"]) == ["tabular", "tabular"]
    # Shapefile/GIS and phylogenetic-tree formats have no reader
    assert F.data_format(["shp", "nwk"]) == ["raw", "raw"]


@pytest.mark.parametrize("name", ["results.tab", "results.table", "comma.tab"])
def test_data_read_head_reads_tab_and_table(name: str) -> None:
    df = F.data_read_head(DATA / name, n_rows=float("inf"))
    assert df is not None
    assert len(df) in (3, 4)
    assert df.shape[1] == 3
    assert df.columns[0] in ("subject", "id")
