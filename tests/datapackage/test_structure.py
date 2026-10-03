"""The structure check: folder tree, depth, layout and empty folders."""

from __future__ import annotations

import json
import os
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from metacheck.datapackage import open_package
from metacheck.datapackage.structure import (
    DEFAULT_SEVERITY,
    check_empty_folders,
    check_folder_depth,
    check_structure,
    default_layouts,
    folder_tree,
    load_layouts,
    load_severity,
    match_layouts,
    normalise_name,
    structure_report,
)
from metacheck.module import module_run
from metacheck.report.render import render_blocks

LAYOUT_A = [
    "README.txt",
    "1 Scientific documents/Protocols & methodologies.txt",
    "2 Administrative documents/Data Management Plan.pdf",
    "3 Code & Script/R-Scripts/License_Script.txt",
    "3 Code & Script/R-Scripts/R_script_analysis-20201126-v0.6-Smith.R",
    "4 Data/Processed data/Processed_data-20201126-v0.3-Smith.csv",
    "4 Data/Raw data/Raw_data-20201126-v0.2-Smith.csv",
]
LAYOUT_B = [
    *[f"Fig.{i}/{name}" for i in range(1, 8) for name in ("data.csv", "plot.png")],
    "PDF_Manuscript.docx",
    "README.txt",
]


def make(root: Path, files: Iterable[str] = (), dirs: Iterable[str] = ()) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel in files:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * 100)
    for rel in dirs:
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def run(root: Path, **options: Any) -> Any:
    with open_package(root) as pkg:
        return check_structure(pkg, **options)


def rules(res: Any, check: str | None = None) -> list[str]:
    f = res.findings
    if check is not None:
        f = f[f["check"] == check]
    return f["rule"].tolist()


def status(res: Any, item: str) -> str:
    return str(res.checklist.set_index("item").loc[item, "status"])


def zip_of(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return path


# -- names ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("4 Data", "data"),
        ("3 Code & Script", "code and script"),
        ("1 Scientific documents", "scientific documents"),
        ("01_data", "data"),
        ("1.", "1"),
        ("2-Raw_Data", "raw data"),
        ("3) Code", "code"),
        ("1.1 Raw data", "raw data"),
        ("Fig.1", "fig 1"),
        ("fig_02", "fig 02"),
        ("Fig-3", "fig 3"),
        ("Raw  data", "raw data"),
        ("2024-03", "2024 03"),
        ("3d models", "3d models"),
        ("Données", "données"),
        ("Data&Code", "data and code"),
    ],
)
def test_normalise_name(name: str, expected: str) -> None:
    assert normalise_name(name) == expected


# -- the two real-world layouts -------------------------------------------------------


def test_layout_a_matches_the_generic_layout_through_normalisation(tmp_path: Path) -> None:
    res = run(make(tmp_path / "pkg", LAYOUT_A))
    assert res.layout_id == "data-code-docs"
    assert res.traffic_light == "green"
    assert res.checklist["item"].tolist() == [
        "folder_tree",
        "folder_depth",
        "folder_layout",
        "empty_folders",
    ]
    assert set(res.checklist["status"]) == {"pass"}
    assert set(res.findings["severity"]) == {"info"}
    parts = res.parts.set_index("path")
    assert parts.loc["4 Data", "part"] == "data"
    assert parts.loc["4 Data/Raw data", "part"] == "data/raw"
    assert parts.loc["4 Data/Processed data", "part"] == "data/processed"
    assert parts.loc["3 Code & Script", "part"] == "code"
    assert parts.loc["1 Scientific documents", "part"] == "docs"
    assert parts.loc["2 Administrative documents", "part"] == "docs"
    assert parts.loc["README.txt", "kind"] == "file"
    assert parts["recognised"].all()
    assert set(parts["layout"]) == {"data-code-docs"}


def test_layout_b_matches_the_figure_layout(tmp_path: Path) -> None:
    res = run(make(tmp_path / "pkg", LAYOUT_B))
    assert res.layout_id == "figure-folders"
    assert res.traffic_light == "green"
    parts = res.parts
    assert parts[parts["part"] == "figure"]["path"].tolist() == [f"Fig.{i}" for i in range(1, 8)]
    assert "manuscript" in parts["part"].tolist()
    assert res.summary_text.startswith("16 files in 7 folders; follows the")


def test_reports_read_well(tmp_path: Path) -> None:
    for files, layout in ((LAYOUT_A, "Data, code and documentation"), (LAYOUT_B, "One folder per")):
        res = run(make(tmp_path / layout[:4], files))
        text = render_blocks(structure_report(res), lambda t: f"[table of {len(t.data)} rows]")
        assert "[table of 4 rows]" in text
        assert f'The folders follow the "{layout}' in text
        assert "#### Folder structure" in text
        assert "```" in text
        assert "Recognised layouts" in text
        # a passing check has no section of its own
        assert "#### Folder depth" not in text
        assert "#### Empty folders" not in text


def test_psych_ds_folders_pick_the_psych_ds_layout(tmp_path: Path) -> None:
    files = [
        "dataset_description.json",
        "data/a.csv",
        "analysis/run.R",
        "materials/stim.png",
        "outputs/fig.png",
        "documentation/notes.md",
    ]
    res = run(make(tmp_path / "pkg", files))
    assert res.layout_id == "psych-ds"
    # without a required README, the generic layout fits too, but less closely
    assert (
        'also fits "Data, code and documentation"'
        in res.checklist.set_index("item").loc["folder_layout", "detail"]
    )


def test_a_layout_that_also_fits_is_mentioned(tmp_path: Path) -> None:
    res = run(make(tmp_path / "pkg", ["README.md", "data/a.csv", "analysis/run.R"]))
    detail = res.checklist.set_index("item").loc["folder_layout", "detail"]
    assert res.layout_id == "data-code-docs"
    assert "also fits" in detail
    assert "Psych-DS" in detail


# -- layout: no match -----------------------------------------------------------------


def test_no_layout_explains_what_is_missing_and_what_does_not_fit(tmp_path: Path) -> None:
    files = ["README.md", "stuff/a.csv", "misc/b.txt", "old/c.txt", "analysis/run.R"]
    res = run(make(tmp_path / "pkg", files))
    assert res.layout_id == ""
    assert status(res, "folder_layout") == "warn"
    assert res.traffic_light == "yellow"
    assert rules(res, "folder_layout") == [
        "layout-not-recognised",
        "layout-part-missing",
        "layout-folder-unmatched",
        "layout-folder-unmatched",
        "layout-folder-unmatched",
    ]
    f = res.findings.set_index("rule")
    assert f.loc["layout-part-missing", "detail"] == 'No "Data" folder at the top level.'
    assert set(f.loc["layout-folder-unmatched", "path"]) == {"stuff", "misc", "old"}
    detail = res.checklist.set_index("item").loc["folder_layout", "detail"]
    assert detail.startswith('No recognised layout. The closest is "')
    assert 'The package is missing a "Data" folder.' in detail
    assert "Only 1 of 4 top-level folders fit it." in detail
    assert "do not fit" in detail
    assert res.layout.best is not None
    assert not res.layout.best.matched
    # the folders that did fit are still given a part
    assert "code" in res.parts["part"].tolist()
    assert not res.parts["recognised"].any()


def test_a_missing_nested_part_names_its_folder(tmp_path: Path) -> None:
    layout = {
        "id": "x",
        "parts": [
            {
                "id": "data",
                "match": ["^data$"],
                "required": True,
                "parts": [{"id": "raw", "title": "Raw data", "match": ["^raw$"], "required": True}],
            }
        ],
    }
    res = run(make(tmp_path / "pkg", ["data/a.csv"]), layouts=layout)
    f = res.findings[res.findings["rule"] == "layout-part-missing"].iloc[0]
    assert (f["path"], f["kind"]) == ("data", "folder")
    assert f["detail"] == 'No "Raw data" folder inside it.'
    # an optional folder's required sub-part is only needed when the folder is there
    assert run(make(tmp_path / "other", ["code/a.R"]), layouts=layout).layout_id == ""
    layout["parts"][0]["required"] = False
    assert run(make(tmp_path / "other2", ["data/a.csv"]), layouts=layout).layout_id == ""
    assert run(make(tmp_path / "other3", ["code/a.R"]), layouts=layout).layout_id == ""


def test_coverage_decides_when_nothing_is_missing(tmp_path: Path) -> None:
    files = ["README.md", "data/a.csv", "x1/a", "x2/a", "x3/a"]
    res = run(make(tmp_path / "pkg", files))
    assert res.layout_id == ""
    detail = res.checklist.set_index("item").loc["folder_layout", "detail"]
    assert "Only 1 of 4 top-level folders fit it." in detail
    assert run(make(tmp_path / "pkg"), min_coverage=0.2).layout_id == "data-code-docs"
    # unmatched folders of a recognised layout are only worth knowing
    res = run(make(tmp_path / "pkg"), min_coverage=0.2)
    assert rules(res, "folder_layout")[:1] == ["layout-recognised"]
    assert set(rules(res, "folder_layout")[1:]) == {"layout-extra-folder"}
    assert status(res, "folder_layout") == "pass"


def test_a_required_repeated_part_needs_its_minimum(tmp_path: Path) -> None:
    layout = {
        "id": "figs",
        "parts": [
            {
                "id": "figure",
                "title": "Figure",
                "match": [r"^fig(ure)? ?\d+$"],
                "repeat": True,
                "min": 3,
                "example": "Fig 1",
            }
        ],
    }
    res = run(make(tmp_path / "pkg", ["Fig1/a", "fig_2/b"]), layouts=layout)
    assert res.layout_id == ""
    f = res.findings[res.findings["rule"] == "layout-part-missing"].iloc[0]
    assert (
        f["detail"]
        == 'Needs at least 3 "Figure" folders (such as "Fig 1") (only 2 found) at the top level.'
    )
    assert (
        run(make(tmp_path / "ok", ["Fig1/a", "fig_2/b", "Figure 03/c"]), layouts=layout).layout_id
        == "figs"
    )


def test_a_missing_readme_does_not_stop_a_default_layout(tmp_path: Path) -> None:
    # the README is package_docs' concern; the default layouts are about folders
    res = run(
        make(tmp_path / "pkg", ["data/a.csv", "code/run.R", "docs/notes.md"]),
        layouts=default_layouts()[:1],
    )
    assert res.layout_id == "data-code-docs"


def test_a_required_root_file_is_what_is_wrong(tmp_path: Path) -> None:
    layout = {
        "id": "strict",
        "title": "Strict",
        "parts": [{"id": "data", "title": "Data", "match": ["^data$"], "required": True}],
        "root_files": [
            {"id": "readme", "title": "README", "match": ["^read ?me"], "required": True}
        ],
    }
    res = run(make(tmp_path / "pkg", ["data/a.csv"]), layouts=[layout])
    assert res.layout_id == ""
    f = res.findings[res.findings["rule"] == "layout-part-missing"]
    assert f["detail"].tolist() == ["No README at the top level."]


def test_a_flat_package_without_folders(tmp_path: Path) -> None:
    small = run(make(tmp_path / "small", ["README.txt", "data.csv", "run.R"]))
    assert small.layout.flat
    assert status(small, "folder_layout") == "pass"
    assert rules(small, "folder_layout") == ["flat-package"]
    assert (
        "no folder layout is needed"
        in small.checklist.set_index("item").loc["folder_layout", "detail"]
    )
    assert small.parts.empty
    assert small.layout_id == ""
    big = run(make(tmp_path / "big", [f"file{i}.csv" for i in range(11)]))
    assert not big.layout.flat
    assert status(big, "folder_layout") == "warn"
    detail = big.checklist.set_index("item").loc["folder_layout", "detail"]
    assert "It has no folders: all 11 files sit at the top level." in detail
    assert status(run(make(tmp_path / "big", []), flat_max_files=20), "folder_layout") == "pass"


def test_layouts_can_be_chosen_by_the_caller(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", LAYOUT_A)
    assert run(root, layouts=[]).layout.item["status"] == "na"
    only_figs = [spec for spec in default_layouts() if spec["id"] == "figure-folders"]
    assert run(root, layouts=only_figs).layout_id == ""
    with open_package(root) as pkg:
        ranked = match_layouts(pkg)
    assert ranked[0].id == "data-code-docs"
    assert [m.matched for m in ranked] == [True, False, False]


# -- custom layouts: dict, list, JSON path --------------------------------------------


CUSTOM = {
    "id": "lab",
    "title": "Lab layout",
    "description": "Raw, scripts and results.",
    "parts": [
        {"id": "raw", "title": "Raw", "match": ["^raw$"], "required": True},
        {"id": "scripts", "title": "Scripts", "match": ["^scripts$"], "required": True},
        {"id": "results", "title": "Results", "match": ["^results$"]},
    ],
}


def test_custom_layout_as_a_dict_list_or_json_path(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", ["Raw/a.csv", "scripts/run.py", "other/x.txt"])
    for layouts in (CUSTOM, [CUSTOM]):
        res = run(root, layouts=layouts)
        assert res.layout_id == "lab"
        assert res.layout.best is not None
        assert res.layout.best.unmatched == ("other",)
    path = tmp_path / "lab.json"
    path.write_text(json.dumps(CUSTOM))
    assert run(root, layouts=path).layout_id == "lab"
    assert run(root, layouts=str(path)).layout_id == "lab"
    many = tmp_path / "many.json"
    many.write_text(json.dumps({"layouts": [CUSTOM, {**CUSTOM, "id": "lab2"}]}))
    assert [s["id"] for s in load_layouts(many)] == ["lab", "lab2"]
    assert [s["id"] for s in load_layouts([path, {**CUSTOM, "id": "third"}])] == ["lab", "third"]
    # a layout of its own is the only one compared with
    assert run(make(tmp_path / "pkg2", LAYOUT_A), layouts=CUSTOM).layout_id == ""


def test_the_default_layouts_are_a_starting_point_for_a_pack() -> None:
    mine = {"id": "mine", "parts": [{"id": "d", "match": ["^d$"], "required": True}]}
    ids = [spec["id"] for spec in load_layouts([*default_layouts(), mine])]
    assert ids == ["data-code-docs", "psych-ds", "figure-folders", "mine"]
    # loading what is already loaded changes nothing
    assert load_layouts(default_layouts()) == default_layouts()


def test_bad_layouts_are_refused_with_the_place(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"unknown key.*colour"):
        load_layouts({"id": "x", "colour": "red"})
    with pytest.raises(ValueError, match="needs an 'id'"):
        load_layouts({"title": "no id"})
    with pytest.raises(ValueError, match=r"x/p: bad regular expression"):
        load_layouts({"id": "x", "parts": [{"id": "p", "match": ["("]}]})
    with pytest.raises(ValueError, match=r"x/p: 'match' must be a list"):
        load_layouts({"id": "x", "parts": [{"id": "p"}]})
    with pytest.raises(ValueError, match="part id 'p' is used twice"):
        load_layouts(
            {"id": "x", "parts": [{"id": "p", "match": ["a"]}, {"id": "p", "match": ["b"]}]}
        )
    with pytest.raises(ValueError, match="'min' must be a whole number"):
        load_layouts({"id": "x", "parts": [{"id": "p", "match": ["a"], "min": -1}]})
    with pytest.raises(ValueError, match="Layout id 'x' is used twice"):
        load_layouts([{"id": "x"}, {"id": "x"}])
    with pytest.raises(ValueError, match="unknown key"):
        load_layouts({"id": "x", "root_files": [{"id": "r", "match": ["a"], "repeat": True}]})
    with pytest.raises(ValueError, match="not int"):
        load_layouts(3)  # type: ignore[arg-type]
    (tmp_path / "bad.json").write_text("{nope")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_layouts(tmp_path / "bad.json")
    with pytest.raises(ValueError, match="Cannot read"):
        load_layouts(tmp_path / "missing.json")
    with pytest.raises(ValueError, match="not a JSON file of reasonable size"):
        load_layouts(tmp_path)
    (tmp_path / "huge.json").write_text(" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="reasonable size"):
        load_severity(tmp_path / "huge.json")


# -- folder depth ---------------------------------------------------------------------


def test_depth_is_counted_from_the_base_and_each_branch_is_reported_once(tmp_path: Path) -> None:
    files = [
        "4 Data/Raw data/x.csv",
        "a/b/c/ok.csv",
        "a/b/c/d/e/f/deep.csv",
        "a/b/c/d/e/g/deep.csv",
        "a/b/c/d/side.csv",
        "a/b/c/z/y.csv",
        "q/r/s/t/u.csv",
    ]
    res = run(make(tmp_path / "pkg", files))
    f = res.findings[res.findings["check"] == "folder_depth"]
    # one finding per branch: a/b/c/d (deepest: .../e/f), a/b/c/z and q/r/s/t
    assert sorted(f["path"]) == ["a/b/c/d/e/f", "a/b/c/z", "q/r/s/t"]
    assert set(f["rule"]) == {"too-deep"}
    assert set(f["severity"]) == {"suggestion"}
    assert set(f["kind"]) == {"folder"}
    assert f.set_index("path").loc["a/b/c/d/e/f", "detail"].startswith("6 folder levels down")
    assert status(res, "folder_depth") == "warn"
    assert (
        res.checklist.set_index("item").loc["folder_depth", "title"]
        == "No more than 3 folder levels"
    )
    detail = res.checklist.set_index("item").loc["folder_depth", "detail"]
    assert (
        detail == "3 branches go deeper than 3 folder levels (the deepest folder is 6 levels down)."
    )


def test_depth_limit_is_an_option(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", ["4 Data/Raw data/x.csv", "readme.md"])
    with open_package(root) as pkg:
        ok = check_folder_depth(pkg)
        one = check_folder_depth(pkg, 1)
    assert ok.item["status"] == "pass"
    assert ok.item["detail"] == "The deepest folder is 2 levels down."
    assert one.item["title"] == "No more than 1 folder level"
    assert [r["path"] for r in one.findings] == ["4 Data/Raw data"]
    assert one.item["detail"].startswith("1 branch goes deeper than 1 folder level")


def test_depth_ignores_hidden_folders_and_files_outside_the_base(tmp_path: Path) -> None:
    members = {
        "pkg/data/a.csv": "x",
        "pkg/.git/objects/ab/cd/ef/gh/obj": "x",
        "__MACOSX/pkg/a/b/c/d/e/._x": "x",
    }
    with open_package(zip_of(tmp_path / "pkg.zip", members)) as pkg:
        assert pkg.base == "pkg"
        assert check_folder_depth(pkg).findings == []


def test_no_folders_is_not_deep(tmp_path: Path) -> None:
    with open_package(make(tmp_path / "pkg", ["a.txt"])) as pkg:
        assert check_folder_depth(pkg).item["detail"] == "The package has no folders."


# -- empty folders --------------------------------------------------------------------


def test_empty_folders_are_reported_at_their_top(tmp_path: Path) -> None:
    root = make(
        tmp_path / "pkg",
        ["data/a.csv"],
        ["empty", "nest/inner/deepest", "data/none", ".cache/empty", "has files/sub"],
    )
    (root / "has files" / "a.txt").write_text("x")
    res = run(root)
    f = res.findings[res.findings["check"] == "empty_folders"].set_index("path")
    assert sorted(f.index) == ["data/none", "empty", "has files/sub", "nest"]
    assert f.loc["nest", "detail"] == (
        "Empty folder. Remove it, or add what belongs in it. It holds 2 more empty folders."
    )
    assert set(f["rule"]) == {"empty-folder"}
    assert status(res, "empty_folders") == "warn"
    assert (
        res.checklist.set_index("item").loc["empty_folders", "detail"] == "6 empty folders found."
    )
    assert "empty" in res.tree


def test_a_linked_folder_is_not_empty(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", ["a.txt"])
    target = make(tmp_path / "elsewhere", ["f.txt"])
    try:
        os.symlink(target, root / "linked", target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are not available")
    with open_package(root) as pkg:
        assert check_empty_folders(pkg).findings == []


# -- the tree -------------------------------------------------------------------------


def test_the_tree_lists_folders_first_then_files_with_sizes(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", ["b.txt", "A.txt", "z/inner.csv", "a/x.csv"], ["empty"])
    with open_package(root) as pkg:
        tree = folder_tree(pkg)
    lines = tree.splitlines()
    assert lines[0].startswith("pkg/")
    names = [re.sub(r"^[│├└─ ]+", "", line).split("  ")[0] for line in lines[1:]]
    assert names == ["a/", "x.csv", "empty/", "z/", "inner.csv", "A.txt", "b.txt"]
    assert any("empty/" in line and line.endswith("empty") for line in lines)
    assert any("A.txt" in line and line.endswith("100 B") for line in lines)
    assert lines[0].endswith("4 files, 400 B")
    assert lines[1].startswith("├── a/")
    assert lines[-1].startswith("└── b.txt")


def test_numbers_in_names_sort_in_order(tmp_path: Path) -> None:
    files = ["f10.csv", "f2.csv", "f1.csv", ".hidden", "Fig 9.png", "fig 10.png"]
    with open_package(make(tmp_path / "pkg", files)) as pkg:
        lines = folder_tree(pkg).splitlines()[1:]
    names = [re.sub(r"^[│├└─ ]+", "", line).split("  ")[0] for line in lines]
    assert names == ["f1.csv", "f2.csv", "f10.csv", "Fig 9.png", "fig 10.png", ".hidden"]


def test_big_folders_are_collapsed(tmp_path: Path) -> None:
    files = [f"data/a{i:03d}.csv" for i in range(185)] + [f"data/z{i:02d}.txt" for i in range(10)]
    with open_package(make(tmp_path / "pkg", files)) as pkg:
        lines = folder_tree(pkg).splitlines()
    # the package, the folder, its first 15 files and the summary line
    assert len(lines) == 1 + 1 + 15 + 1
    assert lines[-1].startswith("    └── … and 180 more (.csv ×170, .txt ×10)")
    assert lines[-1].endswith("17.6 KB")


def test_the_ext_summary_counts_every_collapsed_file(tmp_path: Path) -> None:
    files = [f"d/{i:03d}.csv" for i in range(20)] + [f"d/z{i}.txt" for i in range(5)] + ["d/zz"]
    with open_package(make(tmp_path / "pkg", files)) as pkg:
        lines = folder_tree(pkg, max_items=10).splitlines()
    assert "… and 16 more (.csv ×10, .txt ×5, no extension ×1)" in lines[-1]
    assert len(lines) == 1 + 1 + 10 + 1


def test_many_folders_are_collapsed_too(tmp_path: Path) -> None:
    files = [f"sub-{i:03d}/a.csv" for i in range(40)]
    with open_package(make(tmp_path / "pkg", files)) as pkg:
        lines = folder_tree(pkg).splitlines()
    assert "… and 25 more folders" in lines[-1]
    assert lines[-1].rstrip().endswith("25 files, 2.4 KB")


def test_the_tree_is_kept_to_the_line_cap(tmp_path: Path) -> None:
    files = [
        f"top{i:02d}/mid{j:02d}/f{k}.csv" for i in range(15) for j in range(15) for k in range(3)
    ]
    with open_package(make(tmp_path / "pkg", files)) as pkg:
        full = folder_tree(pkg, max_lines=10_000).splitlines()
        capped = folder_tree(pkg).splitlines()
        tiny = folder_tree(pkg, max_lines=40).splitlines()
        three = folder_tree(pkg, max_lines=3).splitlines()
    assert len(full) > 400
    assert len(capped) <= 400
    assert len(tiny) <= 40
    assert capped[0].startswith("pkg/")
    assert any("…" in line for line in capped)
    # nothing is lost from the counts at the top
    assert capped[0].rstrip().endswith("KB") and tiny[0].startswith("pkg/")
    assert "675 files" in capped[0]
    assert three[0].startswith("pkg/") and three[-1].startswith("└── … and 14 more folders")


def test_very_deep_folders_do_not_overflow(tmp_path: Path) -> None:
    rel = "/".join(f"d{i}" for i in range(60)) + "/f.txt"
    with open_package(make(tmp_path / "pkg", [rel])) as pkg:
        tree = folder_tree(pkg)
        res = check_structure(pkg)
    assert "…" in tree
    assert status(res, "folder_depth") == "warn"


def test_files_next_to_the_wrapper_folder_are_shown_separately(tmp_path: Path) -> None:
    members = {
        "mypkg/README.txt": "x",
        "mypkg/data/a.csv": "a,b\n1,2\n",
        "__MACOSX/mypkg/._README.txt": "x",
        ".DS_Store": "x",
    }
    with open_package(zip_of(tmp_path / "mypkg.zip", members)) as pkg:
        res = check_structure(pkg)
        tree = res.tree
    assert tree.startswith("mypkg/")
    inside, _, outside = tree.partition("\nOutside the package folder")
    assert "README.txt" in inside
    assert "__MACOSX" not in inside
    assert "__MACOSX/" in outside
    assert "._README.txt" in outside
    assert ".DS_Store" in outside
    detail = res.checklist.set_index("item").loc["folder_tree", "detail"]
    assert detail == "2 files in 1 folder (9 B). 2 more files outside the package folder."
    # the wrapper's name is not part of the structure: the layout is judged from inside it
    assert res.layout_id == "data-code-docs"
    assert res.n_files == 2


def test_an_archive_is_checked_like_a_folder(tmp_path: Path) -> None:
    members = {f"Smith 2020/{rel}": "x" for rel in LAYOUT_A} | {
        "__MACOSX/Smith 2020/._README.txt": "x"
    }
    with open_package(zip_of(tmp_path / "Smith 2020.zip", members)) as pkg:
        res = check_structure(pkg)
    assert res.layout_id == "data-code-docs"
    assert res.package == "Smith 2020"
    assert status(res, "folder_depth") == "pass"
    assert "Outside the package folder" in res.tree
    assert "" in res.dirs["rel"].tolist()


# -- the checklist as a whole ---------------------------------------------------------


def test_an_empty_package(tmp_path: Path) -> None:
    res = run(make(tmp_path / "pkg", [], ["only/empty"]))
    assert status(res, "folder_tree") == "fail"
    assert status(res, "folder_layout") == "na"
    assert status(res, "empty_folders") == "warn"
    assert res.traffic_light == "red"
    assert res.summary_text == "The package has no files."
    assert "no-files" in rules(res)
    text = render_blocks(structure_report(res), lambda t: "[table]")
    assert "0 files in 2 folders" in text


def test_severity_overrides_change_the_status(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", ["a/b/c/d/e.csv", "misc/x.txt", "stuff/y.txt"], ["empty"])
    base = run(root)
    assert status(base, "folder_depth") == "warn"
    assert status(base, "folder_layout") == "warn"
    assert base.traffic_light == "yellow"
    res = run(
        root,
        severity={
            "too-deep": "problem",
            "empty-folder": "info",
            "layout-not-recognised": "info",
            "layout-part-missing": "info",
            "layout-folder-unmatched": "info",
        },
    )
    assert status(res, "folder_depth") == "fail"
    assert status(res, "empty_folders") == "pass"
    assert status(res, "folder_layout") == "pass"
    assert res.traffic_light == "red"
    assert res.findings.set_index("rule").loc["too-deep", "severity"] == "problem"
    # a JSON file works the same, and rule ids of other checks are ignored
    path = tmp_path / "sev.json"
    path.write_text(json.dumps({"too-deep": "info", "some-other-check": "problem"}))
    assert status(run(root, severity=path), "folder_depth") == "pass"
    with pytest.raises(ValueError, match="must be one of"):
        run(root, severity={"too-deep": "serious"})
    with pytest.raises(ValueError, match="dict of rule id"):
        load_severity(["too-deep"])  # type: ignore[arg-type]
    assert load_severity(None) == {}


def test_options_out_of_range_are_refused(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", ["a/b.txt"])
    with pytest.raises(ValueError, match="between 0 and 1"):
        run(root, min_coverage=60)


def test_every_rule_has_a_default_severity() -> None:
    assert set(DEFAULT_SEVERITY.values()) <= {"problem", "suggestion", "info"}
    assert DEFAULT_SEVERITY["too-deep"] == "suggestion"
    assert DEFAULT_SEVERITY["empty-folder"] == "suggestion"


def test_a_big_messy_package_gives_a_readable_report(tmp_path: Path) -> None:
    files = [f"Results {i}/run/old/older/oldest/r.csv" for i in range(20)] + ["notes.txt"]
    res = run(make(tmp_path / "pkg", files, ["empty"]))
    text = render_blocks(structure_report(res), lambda t: "[table]")
    assert "#### Folder depth" in text
    assert "These branches go deeper than 3 levels:" in text
    assert "`Results 0/run/old/older/oldest`: 5 folder levels down" in text
    assert "#### Folder layout" in text
    assert "#### Empty folders" in text
    assert "- `empty`: Empty folder." in text
    assert "… and 5 more" in text  # 20 branches, the first 15 are listed


# -- the module -----------------------------------------------------------------------


def test_the_module_returns_findings_summary_and_extras(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", LAYOUT_A)
    out = module_run(None, "datapackage::package_structure", local_path=str(root))
    assert out.traffic_light == "green"
    assert out.summary_text.startswith("7 files in 7 folders; follows")
    assert out.table["check"].tolist() == ["folder_layout"]
    assert out["checklist"]["item"].tolist() == [
        "folder_tree",
        "folder_depth",
        "folder_layout",
        "empty_folders",
    ]
    assert out["tree"].startswith("pkg/")
    assert "n_files" in out["dirs"].columns
    assert "4 Data/Raw data" in out["parts"]["path"].tolist()
    assert isinstance(out.report, list)


def test_the_module_takes_the_options(tmp_path: Path) -> None:
    root = make(tmp_path / "pkg", ["a/b/c/d.csv", "README.md"])
    lay = tmp_path / "lay.json"
    lay.write_text(json.dumps(CUSTOM))
    out = module_run(
        None,
        "datapackage::package_structure",
        local_path=str(root),
        max_depth=2,
        layouts=str(lay),
        min_coverage=0.5,
        flat_max_files=3,
        severity={"too-deep": "problem"},
    )
    assert out.traffic_light == "red"
    assert out.table.set_index("rule").loc["too-deep", "severity"] == "problem"
    assert out.table.set_index("rule").loc["layout-part-missing"].shape[0] == 2


def test_no_package_is_not_applicable() -> None:
    out = module_run(None, "datapackage::package_structure")
    assert out.traffic_light == "na"
    assert "No data package" in out.summary_text


def test_the_module_checks_an_archive(tmp_path: Path) -> None:
    members = {f"pkg/{rel}": "x" for rel in LAYOUT_B}
    archive = zip_of(tmp_path / "pkg.zip", members)
    out = module_run(None, "datapackage::package_structure", local_path=str(archive))
    assert out.traffic_light == "green"
    assert out["tree"].startswith("pkg/")
