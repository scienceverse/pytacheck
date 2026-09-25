"""Write the local repository folders used by the mod_repo_check tests and parity cases.

python tests/mod_repo_check/make_fixtures.py

Each folder under ``tests/mod_repo_check/fixtures`` is a small "deposit" that
exercises one part of ``repo_check(local_path = ...)``: R package trees (the
exclusion rule), a tidy deposit (green), a messy one (spaces, special
characters, unclassifiable files, E-Prime binaries, a non-zip archive, study
folders), E-Prime files with their text exports, and local archives.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"

FILES: dict[str, dict[str, str | bytes]] = {
    # an R package tree beside the study's own files (from test-module-repo_check.R)
    "rpkg": {
        "DESCRIPTION": "Package: mypkg\nVersion: 0.1.0\n",
        "NAMESPACE": "export(foo)\n",
        "R/foo.R": "foo <- function() 1\n",
        "man/foo.Rd": "\\name{foo}\n",
        "analysis.R": "x <- 1\n",
        "data.csv": "a,b\n1,2\n",
    },
    # a DESCRIPTION without a NAMESPACE is not a package
    "desc_only": {
        "DESCRIPTION": "Package: mypkg\nVersion: 0.1.0\n",
        "analysis.R": "x <- 1\n",
    },
    # a root-level package that IS the deposit
    "rpkg_root": {
        "DESCRIPTION": "Package: mypkg\nVersion: 0.1.0\n",
        "NAMESPACE": "export(foo)\n",
        "R/foo.R": "foo <- function() 1\n",
        "man/foo.Rd": "\\name{foo}\n",
        "data/study_data.rda": b"RDX3\n",
        ".Rbuildignore": "\n",
        "LICENSE": "MIT\n",
        "README.md": "# mypkg\n",
    },
    # a vendored package in a subfolder beside the study's files
    "vendored": {
        "vendored_pkg/DESCRIPTION": "Package: vendoredpkg\nVersion: 0.1.0\n",
        "vendored_pkg/NAMESPACE": "export(bar)\n",
        "vendored_pkg/R/bar.R": "bar <- function() 1\n",
        "analysis.R": "x <- 1\n",
        "data.csv": "a,b\n1,2\n",
    },
    # README, data and code: green
    "tidy": {
        "README.md": "# Tidy deposit\n\nData and code for the study.\n",
        "data/scores.csv": "id,score\n1,10\n2,12\n",
        "code/analysis.R": 'd <- read.csv("../data/scores.csv")\nmean(d$score)\n',
    },
    # everything the report warns about
    "messy": {
        "Study 1/raw data.csv": "id,rt\n1,300\n",
        "study2/analysis.R": 'x <- read.csv("raw data.csv")\n',
        "study3/stimuli.edat2": "EDAT\n",
        "materials/task.edat": "EDAT\n",
        "materials/task.txt": "Subject\tRT\n1\t300\n",
        "merged.emrg2": "EMRG\n",
        "results.tar.gz": "TAR\n",
        "weird.xyz123": "blob\n",
        "fig1.png": "fig\n",
        "fig2.png": "fig\n",
        "fig10.png": "fig\n",
        "notes (final)!.docx": "notes\n",
        "docs/codebook.csv": "variable,label\nrt,reaction time\n",
        "LICENSE.txt": "CC-BY 4.0\n",
    },
    # E-Prime files that all have a plain-text export
    "edat_ok": {
        "Stroop.edat3": "EDAT\n",
        "stroop.edat2": "EDAT\n",
        "Stroop.txt": "Subject\tRT\n",
        "stroop.TXT": "Subject\tRT\n",
        "README.txt": "# readme\n",
    },
    # local archives are never peeked (no URL): a .zip and a .7z
    "zip_local": {
        "README.md": "# zip deposit\n",
        "archive.7z": b"7z\xbc\xaf\x27\x1c",
    },
}


def main() -> None:
    for folder, files in FILES.items():
        for rel, content in files.items():
            path = FIXTURES / folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8") if isinstance(content, str) else content
            path.write_bytes(data)
    # a real zip, written with a fixed timestamp so it is byte-identical on every run
    with zipfile.ZipFile(FIXTURES / "zip_local" / "bundle.zip", "w") as z:
        for name, text in (("data/study.csv", "a,b\n1,2\n"), ("code/run.R", "x <- 1\n")):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            z.writestr(info, text)


if __name__ == "__main__":
    main()
