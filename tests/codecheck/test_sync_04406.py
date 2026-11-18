"""code_check synchronised with metacheck 0.4.4.4 (tests/testthat/test-code_check.R, #648)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from metacheck.codecheck import core
from metacheck.codecheck.core import (
    _code_missing_files,
    _code_name_match,
    code_abs_path,
    code_extract_py,
    code_extract_qmd_py,
    code_file_refs,
)


def _nb(*cells: str) -> str:
    return json.dumps(
        {"cells": [{"cell_type": "code", "source": c} for c in cells]},
    )


def test_files_a_script_writes_or_downloads() -> None:
    assert code_file_refs(
        [
            'save "out/clean.dta", replace',
            "export delimited using results.csv, replace",
            "use raw.dta",
        ],
        "Stata",
        writes_only=True,
    ) == ["out/clean.dta", "results.csv"]
    assert code_file_refs(
        ['writetable(T, "tab.csv");', 'fid = fopen("log.txt", "w");', 'x = load("in.mat");'],
        "MATLAB",
        writes_only=True,
    ) == ["tab.csv", "log.txt"]
    assert "data.csv" in code_file_refs(
        'download.file("https://osf.io/x/data.csv", "data.csv")', "R", writes_only=True
    )
    assert code_file_refs(
        [
            'df.to_csv(f"vzv_{COMPARATOR}_model.csv")',
            'with open("o.txt", "w") as f:',
            'd = pd.read_csv("in.csv")',
        ],
        "Python",
        writes_only=True,
    ) == ["vzv_{COMPARATOR}_model.csv", "o.txt"]
    assert code_file_refs(
        'proc export data=a outfile="x.csv" dbms=csv;', "SAS", writes_only=True
    ) == ["x.csv"]
    # a language without write calls has no written files
    assert code_file_refs('GET FILE="in.sav".', "SPSS", writes_only=True) == []


def test_a_downloaded_file_is_made_by_the_script() -> None:
    from metacheck.repro.core import repro_file_io

    io = repro_file_io(
        {"a.R": ['download.file("https://x/y.csv", "y.csv")', 'd <- read.csv("y.csv")']}
    )
    assert "y.csv" in io["writes"].iloc[0]


def test_names_with_placeholders_on_either_side() -> None:
    assert _code_name_match(
        ["vzv_aou_ac_%s_model.csv", "vzv_aou_ac_pneumo_model.csv", "other.csv", "X.CSV"],
        ["vzv_aou_ac_{COMPARATOR}_model.csv", "x.csv"],
    ) == [True, True, False, True]
    # a pattern with little text of its own matches nothing
    assert _code_name_match(["education_tests.csv"], ["{name}.csv"]) == [False]
    assert _code_name_match(["x.dta"], ["`weekly'"]) == [False]
    assert _code_name_match(["resnet50_embeddings.npy"], ["{backbone}_embeddings.npy"]) == [True]


def test_missing_files_of_each_code_file_once_all_are_read() -> None:
    cf = pd.DataFrame(
        {
            "paper_id": ["p", "p", "p"],
            "repo_url": ["r", "r", "r"],
            "file_name": ["a.R", "b.R", "c.do"],
            "loaded_refs": [
                "\u001f".join(
                    [
                        "raw.csv",
                        "clean.csv",
                        "res_pneumo.csv",
                        "_part.csv",
                        "s{seed}.json",
                        "gone.csv",
                    ]
                ),
                "",
                "",
            ],
            "files_written": ["", "clean.csv", "res_%s.csv"],
        }
    )
    out = _code_missing_files(cf, pd.DataFrame({"repo_url": ["r"], "file_name": ["raw.csv"]}))
    assert out["loaded_files_missing_names"].iloc[0] == "gone.csv"
    assert out["loaded_files_missing"].tolist() == [1, 0, 0]
    assert "loaded_refs" not in out.columns


def _ap(x: str) -> list[str]:
    return code_abs_path(x)["abs_path"].tolist()


def test_text_that_is_not_a_path_is_not_an_absolute_path() -> None:
    # pieces the code joins to a folder of its own
    assert _ap('d <- read_csv(paste0(input_dir, "/behaviour_data/ages.csv"))') == []
    assert _ap("emfd = read_csv(fileDir+'/dictionaries/emfd_scoring.csv')") == []
    assert _ap("p = strcat(userpath,'/MVPA-Light/startup');") == []
    assert _ap("sbjdir = [rawdir 'sub-' nr '/ses-meg/meg/'];") == []
    assert _ap(r"save(strcat(DATA_DIR,'\\All\Results'),'x');") == []
    assert _ap('ggsave(file.path(out, "/figs/a.pdf"))') == []
    # a path that comes first, or starts with a drive or ~, stays
    assert _ap('d <- read.csv(file.path("/Users/me/data", "x.csv"))') == ["/Users/me/data"]
    assert _ap('x <- c("/Users/me/a.csv", "/Users/me/b.csv")') == [
        "/Users/me/a.csv",
        "/Users/me/b.csv",
    ]
    assert _ap('files = ["/home/me/a.csv"]') == ["/home/me/a.csv"]
    assert _ap(r'm = dir("C:\Users\me\Data\S5*.mat")') == [r"C:\Users\me\Data\S5*.mat"]
    assert _ap('d <- read.csv("/Users/me/Dropbox (Personal)/x.csv")') == [
        "/Users/me/Dropbox (Personal)/x.csv"
    ]
    # patterns, web routes and queries, devices, keys inside files, placeholders
    assert _ap(r'dicom = meta.str.extract(r"/([^/]+)\.dcm$")') == []
    assert _ap('@app.route("/users/<int:user_id>/reset")') == []
    assert _ap('r = client.get("/api/v1/audit?limit=50")') == []
    assert _ap('system("cmd > /dev/null/x")') == []
    assert _ap('mag = f["/Data/MAG"][:]') == []
    assert _ap('x <- h5read(file, "/Data/MAG")') == []
    assert _ap('annot <- "/path/to/variants.tsv"') == []
    assert _ap('snp <- "/YOUR PATH HERE/v10/files/"') == []
    assert _ap('x <- "/EXAMPLEDIRECTORY/data/a.csv"') == []
    # a message, and an R regular expression, are not Windows paths
    assert _ap(r'"C:\\Users\\me\\launcher.py:36: RuntimeWarning: More than 20 figures"') == []
    assert _ap(r'india <- str_detect(x, "\\bindia\\b")') == []


def test_pip_and_continued_commands_leave_a_notebook_cell() -> None:
    assert code_extract_py(text=[_nb("pip install pandas\nx = 1")]) == ["x = 1", ""]
    assert code_extract_py(
        text=[_nb("%run train.py \\\n    --epochs 5 \\\n    --lr 0.1\nx = 1")]
    ) == ["x = 1", ""]
    # pip as a name stays
    assert code_extract_py(text=[_nb("pip = 2\nx = pip")]) == ["pip = 2", "x = pip", ""]
    # IPython commands in a Quarto Python chunk go too
    assert code_extract_qmd_py(
        text=["```{python}", "%matplotlib inline", "import numpy as np", "```"]
    ) == ["import numpy as np", ""]


def test_code_is_not_recovered_from_a_knitted_html_whose_rmd_is_in_the_deposit(
    tmp_path: Path,
) -> None:
    h = [
        '<html><head><meta name="generator" content="pandoc" /></head><body>',
        '<pre class="r"><code>x &lt;- rnorm(10)',
        "mean(x)</code></pre>",
        "<pre><code>## [1] 0.1</code></pre></body></html>",
    ]
    (tmp_path / "analysis.html").write_text("\n".join(h), encoding="utf-8")
    (tmp_path / "other.html").write_text("\n".join(h), encoding="utf-8")
    (tmp_path / "analysis.Rmd").write_text("```{r}\nx <- rnorm(10)\n```", encoding="utf-8")
    names = ["analysis.html", "other.html", "analysis.Rmd"]
    af = pd.DataFrame(
        {
            "file_name": names,
            "file_path": ["out/analysis.html", "other.html", "analysis.Rmd"],
            "file_location": [str(tmp_path / n) for n in names],
            "file_url": [None, None, None],
            "repo_url": ["local"] * 3,
            "file_size": [1, 1, 1],
        }
    )
    out = core._code_expand_html(af, float("inf"), float("inf"), cache=False)
    # only other.html, which has no source in the deposit, gives a code file
    assert sorted(set(out["file_name"]) - set(af["file_name"])) == ["other.R"]
