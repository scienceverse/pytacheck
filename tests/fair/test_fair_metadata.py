"""The metadata FAIR_repo() writes: dataset_description.json, the RO-Crate, README, logs.

Port of the second half of ``tests/testthat/test-fair-repo.R``. Every ``test_that()`` block is
one function, with R's title in a comment.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.fair.repo import FAIR_repo
from tests.fair.fair_helpers import (
    by_id,
    crate_ids,
    fair_paper,
    graph,
    leaves,
    of_type,
    read_json,
    read_lines,
    write_csv,
    write_lines,
    write_xlsx,
)

FairRun = Callable[..., dict[str, Any]]

CRATE = "ro-crate-metadata.json"
DESCRIPTION = "dataset_description.json"


def variables(out: Path) -> list[dict[str, Any]]:
    """``fromJSON(dataset_description.json)$variableMeasured``, as a list of entities."""
    return list(read_json(out / DESCRIPTION)["variableMeasured"])


# FAIR_repo writes the metadata files
def test_fair_repo_writes_the_metadata_files(proj: Path, fair_run: FairRun) -> None:
    res = fair_run(proj)
    out = Path(res["output_dir"])

    vars_ = variables(out)
    names = [v["name"] for v in vars_]
    assert {"id", "score"} <= set(names)
    # the codebook's label is the variable's description
    assert vars_[names.index("score")]["description"] == "outcome measure"

    # R packages are not put in requirements.txt, which pip reads
    assert not (out / "requirements.txt").exists()
    assert (out / "logs" / "log.md").exists()
    assert not (out / "logs" / "README.md").exists()
    # the paper's full text is not copied
    assert not (out / "documentation").is_dir() or not any(
        "fulltext" in p.name for p in (out / "documentation").iterdir()
    )
    logs = sorted(p.name for p in (out / "logs").iterdir())
    assert len([n for n in logs if n.endswith("files.json")]) == 1
    # R writes the checks' results as <paper>.rds; the Python port writes <paper>.json (D172)
    assert [n for n in logs if n.endswith(".json") and not n.endswith("files.json")] == [
        "proj.json"
    ]

    # CHANGES.md tells the user about the removed setwd()
    changes = read_lines(out / "CHANGES.md")
    assert any("setwd" in line for line in changes)
    assert any("C:/Users/me/project" in line for line in changes)

    # the RO-Crate describes every file it refers to, once
    ids = crate_ids(read_json(out / CRATE))
    assert len(ids["described"]) == len(set(ids["described"]))
    local = [i for i in ids["referenced"] if not i.startswith(("http://", "https://"))]
    assert set(local) - set(ids["described"]) == set()
    assert {"data/study-scores_data.csv", "analysis/analysis.R", "CHANGES.md"} <= set(
        ids["described"]
    )


# FAIR_repo's metadata holds no path on the computer that ran it
def test_fair_repo_metadata_holds_no_path_on_the_computer_that_ran_it(
    proj: Path, fair_run: FairRun
) -> None:
    res = fair_run(proj)
    out = Path(res["output_dir"])
    # the name of the folder holding the folder (the folder's own name is its name, and may be
    # shown)
    top = proj.resolve().parent.name
    files = [out / CRATE, out / DESCRIPTION, *sorted((out / "logs").glob("*json"))]
    for f in files:
        assert top not in f.read_text(encoding="utf-8"), f.name
    # R also reads the checks' .rds back; here that file is the logs' <paper>.json, read above
    assert (out / "logs" / "proj.json") in files


# FAIR_repo records which files each script reads
def test_fair_repo_records_which_files_each_script_reads(proj: Path, fair_run: FairRun) -> None:
    res = fair_run(proj)
    crate = read_json(Path(res["output_dir"]) / CRATE)
    runs = [e for e in graph(crate) if e.get("eventType") == "script run"]
    assert len(runs) == 1
    assert runs[0]["instrument"]["@id"] == "analysis/analysis.R"
    assert runs[0]["object"][0]["@id"] == "data/study-scores_data.csv"


# FAIR_repo gives each study its own folder and metadata
def test_fair_repo_gives_each_study_its_own_folder_and_metadata(
    tmp_path: Path, fair_run: FairRun
) -> None:
    d = tmp_path / "multi"
    for s in ("study1", "study2"):
        write_csv(d / s / f"{s}_scores.csv", {"id": [1, 2, 3], "x": [1.5, 2.5, 3.5]})
    res = fair_run(d, paper=fair_paper())
    out = Path(res["output_dir"])
    assert res["n_studies"] == 2
    for s in ("study-ex1", "study-ex2"):
        for f in (DESCRIPTION, "README.md", "CHANGES.md"):
            assert (out / s / f).exists(), f"{s}/{f}"

    g = graph(read_json(out / CRATE))
    ids = crate_ids(read_json(out / CRATE))
    assert {"study-ex1/", "study-ex2/", "#study-design-ex1", "#study-design-ex2"} <= set(
        ids["described"]
    )
    # CRediT roles are one entry per author, not a set per study
    assert len(of_type(g, "Role")) == 2
    assert len(of_type(g, "Person")) == 2
    assert len(ids["described"]) == len(set(ids["described"]))


# FAIR_repo writes the metadata for a folder with no files
def test_fair_repo_writes_the_metadata_for_a_folder_with_no_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    out = tmp_path / "out"
    res = FAIR_repo(str(d), output_dir=str(out))
    assert "No files" in capsys.readouterr().err
    assert res["empty_plan"] is True
    assert res["n_files_copied"] == 0
    assert (out / CRATE).exists()
    assert not (out / DESCRIPTION).exists()


# the RO-Crate has the paper, authors, CRediT roles and contact, and few unknowns
def test_the_rocrate_has_the_paper_authors_roles_and_contact_and_few_unknowns(
    proj: Path, fair_run: FairRun
) -> None:
    res = fair_run(proj, paper=fair_paper())
    g = graph(read_json(Path(res["output_dir"]) / CRATE))
    entities = by_id(g)
    root = entities["./"]
    # the paper is cited, not given as the dataset's own identifier
    assert root["citation"]["@id"] == "https://doi.org/10.1234/abc"
    assert root.get("identifier") is None
    assert root["name"] == "A test paper"
    ann = entities["https://orcid.org/0000-0001-2345-6789"]
    assert ann["email"] == "ann@x.org"
    assert entities["mailto:ann@x.org"]["contactType"] == "corresponding author"
    roles = of_type(g, "Role")
    assert len(roles) == 2
    assert [r["@id"] for r in roles[0]["roleName"]] == [
        "https://credit.niso.org/contributor-roles/conceptualization/",
        "https://credit.niso.org/contributor-roles/writing-original-draft/",
    ]
    assert roles[1]["roleName"] == "unknown"
    # unknown only for the licence, Bob's roles, and when and how the data were collected and
    # processed
    assert sum(1 for leaf in leaves(g) if leaf == "unknown") == 5
    # the language and package the code uses
    assert {"#language-r", "#package-r-dplyr"} <= set(entities)
    assert entities["#package-r-dplyr"]["programmingLanguage"]["@id"] == "#language-r"


# without a paper, the copy is named after the folder and the README asks for the paper
def test_without_a_paper_the_copy_is_named_after_the_folder(proj: Path, fair_run: FairRun) -> None:
    (proj / "README.txt").unlink()
    res = fair_run(proj)
    out = Path(res["output_dir"])
    g = graph(read_json(out / CRATE))
    assert g[1]["name"] == "proj"
    assert not any(e.get("@type") == "Person" for e in g)
    # the authors are a field to fill in
    assert g[1]["author"] == "unknown"
    readme = read_lines(out / "README.md")
    assert readme[0] == "# proj"
    assert {"- The title", "- The authors", "- The paper that uses these files"} <= set(readme)


# without a paper, the title comes from the folder's own README
def test_without_a_paper_the_title_comes_from_the_folders_own_readme(
    proj: Path, fair_run: FairRun
) -> None:
    write_lines(proj / "README.txt", ["# Water quality at the festival", "", "Some text."])
    res = fair_run(proj)
    out = Path(res["output_dir"])
    g = graph(read_json(out / CRATE))
    assert g[1]["name"] == "Water quality at the festival"
    assert read_json(out / DESCRIPTION)["name"] == "Water quality at the festival"
    # (the plain first line that is no title: test_fair_functions.py)


# a README in another encoding does not stop the conversion
def test_a_readme_in_another_encoding_does_not_stop_the_conversion(
    proj: Path, fair_run: FairRun
) -> None:
    # an older Mac encoding: "\xd4" and "\xd5" are curly quotes
    (proj / "README.txt").write_bytes(
        b"# Snow removal\nLabelled " + b"\xd4" + b"frosted stalks" + b"\xd5" + b".\n"
    )
    res = fair_run(proj)
    g = graph(read_json(Path(res["output_dir"]) / CRATE))
    assert g[1]["name"] == "Snow removal"


# a column name in another encoding gives valid JSON
def test_a_column_name_in_another_encoding_gives_valid_json(proj: Path, fair_run: FairRun) -> None:
    # "PPFD (µmol)" with the µ as the Latin-1 byte b5
    (proj / "data" / "light.csv").write_bytes(b"PPFD (" + b"\xb5" + b"mol),x\n1,2\n3,4\n")
    res = fair_run(proj)
    out = Path(res["output_dir"])
    for f in (DESCRIPTION, CRATE):
        raw = (out / f).read_bytes()
        raw.decode("utf-8")  # valid UTF-8 (raises otherwise)
        read_json(out / f)
    assert "PPFD (µmol)" in [v["name"] for v in variables(out)]


# FAIR_repo writes one CSV per Excel sheet, and describes the columns of every sheet
def test_fair_repo_writes_one_csv_per_excel_sheet(proj: Path, fair_run: FairRun) -> None:
    # the first sheet is a README; the data are on the second
    write_xlsx(
        proj / "data" / "harvest.xlsx",
        {
            "README": {"about": ["Harvest data", "see sheet 2"]},
            "data sheet": {"plant": [1, 2, 3], "weight": [2.5, 3.1, 2.8]},
        },
    )
    res = fair_run(proj)
    out = Path(res["output_dir"])
    data_dir = out / "data"
    assert (data_dir / "harvest.xlsx").exists()
    back = pd.read_csv(data_dir / "study-harvest_description-datasheet_data.csv")
    assert list(back.columns) == ["plant", "weight"]
    assert len(back) == 3
    # the README sheet is documentation, not data
    assert (out / "documentation" / "study-harvest_description-README_data.csv").exists()
    assert not (data_dir / "study-harvest_description-README_data.csv").exists()
    # every column of every data CSV is described, and the README sheet's are not
    names = [v["name"] for v in variables(out)]
    assert {"plant", "weight"} <= set(names)
    assert "about" not in names
    # and the RO-Crate lists both sheets' files
    ids = crate_ids(read_json(out / CRATE))
    assert {
        "data/study-harvest_description-datasheet_data.csv",
        "documentation/study-harvest_description-README_data.csv",
    } <= set(ids["described"])


# FAIR_repo writes a README from the RO-Crate when the folder has none
def test_fair_repo_writes_a_readme_from_the_rocrate_when_the_folder_has_none(
    proj: Path, fair_run: FairRun
) -> None:
    (proj / "README.txt").unlink()
    res = fair_run(proj, paper=fair_paper())
    out = Path(res["output_dir"])
    readme = read_lines(out / "README.md")
    assert readme[0] == "# A test paper"
    for h in (
        "## Authors", "## Paper", "## Licence", "## Files", "## File formats", "## Software",
        "## Scripts", "## Variables", "## Still to be filled in",
    ):  # fmt: skip
        assert h in readme, h
    assert any("Contact: Ann Smith <ann@x.org>" in line for line in readme)
    assert any("The CRediT roles of Bob Jones" in line for line in readme)
    assert any("| score | outcome measure |" in line for line in readme)
    # the README is listed in the RO-Crate
    assert "README.md" in crate_ids(read_json(out / CRATE))["described"]


# requirements.txt lists the Python packages pip can install, nothing else
def test_requirements_lists_the_python_packages_pip_can_install(
    proj: Path, fair_run: FairRun
) -> None:
    write_lines(
        proj / "Code" / "model.py",
        [
            "import os",
            "import json",
            "import pandas as pd",
            "from sklearn.linear_model import LinearRegression",
        ],
    )
    (proj / "README.txt").unlink()
    res = fair_run(proj)
    out = Path(res["output_dir"])
    req = read_lines(out / "requirements.txt")
    pkgs = [line for line in req if not line.startswith("#")]
    assert set(pkgs) == {"pandas", "scikit-learn"}
    # the R packages are named in the README instead
    readme = read_lines(out / "README.md")
    assert any("R packages: dplyr" in line for line in readme)
    assert any("Python packages: pandas, scikit-learn" in line for line in readme)


# FAIR_repo leaves the folder's own README alone
def test_fair_repo_leaves_the_folders_own_readme_alone(proj: Path, fair_run: FairRun) -> None:
    res = fair_run(proj)
    out = Path(res["output_dir"])
    assert read_lines(out / "README.txt") == ["A readme."]
    assert not (out / "README.md").exists()


# FAIR_repo writes the funding it found into the RO-Crate
def test_fair_repo_writes_the_funding_it_found_into_the_rocrate(
    proj: Path, fair_run: FairRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "metacheck.fair.metadata._FAIR_repo_funding",
        lambda paper, ops: {
            "statement": "Funded by NWO grant 123.",
            "funders": ["NWO"],
            "awards": ["123"],
        },
    )
    res = fair_run(proj, paper=fair_paper())
    entities = by_id(graph(read_json(Path(res["output_dir"]) / CRATE)))
    assert entities["./"]["funding"]["@id"] == "#funding"
    assert entities["#funding"]["description"] == "Funded by NWO grant 123."
    assert entities["#funder-1"]["name"] == "NWO"


# a folder whose only data is a spreadsheet starting with a README sheet is described (#555)
def test_a_folder_with_only_a_spreadsheet_starting_with_a_readme_sheet_is_described(
    tmp_path: Path, fair_run: FairRun
) -> None:
    d = tmp_path / "sheets"
    write_xlsx(
        d / "harvest.xlsx",
        {
            "README": {"about": ["Harvest data", "see sheet 2"]},
            "plants": {"pid": [1, 2, 3, 4, 5, 6], "weight": [2.5, 3.1, 2.8, 4, 3, 2]},
        },
    )
    res = fair_run(d)
    out = Path(res["output_dir"])
    assert (out / DESCRIPTION).exists()
    vm = variables(out)
    assert {v["name"] for v in vm} == {"pid", "weight"}
    assert {v["metacheck:sourceFile"] for v in vm if "metacheck:sourceFile" in v} == {
        "data/study-harvest_description-plants_data.csv"
    }


# each variable of a spreadsheet names the CSV of its own sheet (#555)
def test_each_variable_of_a_spreadsheet_names_the_csv_of_its_own_sheet(
    proj: Path, fair_run: FairRun
) -> None:
    write_xlsx(
        proj / "data" / "harvest.xlsx",
        {
            "plants": {"pid": [1, 2, 3, 4, 5, 6], "weight": [2.5, 3.1, 2.8, 4, 3, 2]},
            "soil": {"sid": [1, 2, 3, 4, 5, 6], "ph": [6.5, 7.1, 6.8, 7, 6.2, 6.9]},
        },
    )
    res = fair_run(proj)
    vm = variables(Path(res["output_dir"]))

    def src(nm: str) -> list[str]:
        return [
            v["metacheck:sourceFile"] for v in vm if v["name"] == nm and "metacheck:sourceFile" in v
        ]

    assert src("weight") == ["data/study-harvest_description-plants_data.csv"]
    assert src("ph") == ["data/study-harvest_description-soil_data.csv"]
    # from data_check, so with what it found, not only the name
    assert any(v["name"] == "ph" and v.get("maxValue") is not None for v in vm)


# each variable names its file as it is in the copy
def test_each_variable_names_its_file_as_it_is_in_the_copy(proj: Path, fair_run: FairRun) -> None:
    res = fair_run(proj)
    vm = variables(Path(res["output_dir"]))
    src = {v.get("metacheck:sourceFile") for v in vm}
    # R: `src %in% plan$written`, where a variable with no file is NA and so is a file not written
    plan_written = {None if pd.isna(w) else w for w in res["plan"]["written"]}
    assert src <= plan_written
