"""Write the local folders of the ``mod_repo_check_review`` parity cases.

python tests/mod_repo_check/make_review_fixtures.py

Each folder under ``tests/mod_repo_check/fixtures/review`` targets one branch
of ``repo_check(local_path = ...)`` that the main cases do not reach: file
sizes around the SI unit boundaries and R's ``round()`` halves, the singular
and plural E-Prime sentences and their ``head(8)``, ``head(10)`` of the
unclassified files, naming suggestions without problems, README spellings,
the same relative path in two folders (R's de-duplication quirk), root-only
documentation (no study grouping), a deposit that is only a vendored R
package, hidden files and non-ASCII names.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
REVIEW = HERE / "fixtures" / "review"


def _sized(n: int) -> bytes:
    return (b"x" * (n - 1) + b"\n") if n > 0 else b""


FILES: dict[str, dict[str, str | bytes]] = {
    # sizes: 0 B, SI boundaries and R round() halves (1.05 kB, 1.15 kB, ...)
    "sizes": {
        "README.md": "# sizes\n",
        "empty.csv": _sized(0),
        "b999.csv": _sized(999),
        "k1000.csv": _sized(1000),
        "k1049.csv": _sized(1049),
        "k1050.csv": _sized(1050),
        "k1150.csv": _sized(1150),
        "k1250.csv": _sized(1250),
        "k12345.csv": _sized(12345),
        "k99950.csv": _sized(99950),
    },
    # ten E-Prime files, one with its .txt export: plural sentence, head(8)
    "edat_many": {
        "README.md": "# eprime\n",
        **{f"run{i:02d}.edat2": "EDAT\n" for i in range(1, 11)},
        "run01.txt": "Subject\tRT\n",
    },
    # one E-Prime file without its export: singular sentence
    "edat_one": {
        "README.md": "# eprime\n",
        "task.edat": "EDAT\n",
        "analysis.R": "x <- 1\n",
    },
    # twelve unclassifiable files: head(10) in the report
    "unknown_many": {
        "README.md": "# unknown\n",
        **{f"blob{i:02d}.qqz{i}": "blob\n" for i in range(1, 13)},
    },
    # naming suggestions (zero padding) and no naming problem
    "suggest_only": {
        "README.md": "# suggestions\n",
        "data_1.csv": "a\n1\n",
        "data_2.csv": "a\n2\n",
        "data_10.csv": "a\n10\n",
        "analysis.R": "x <- 1\n",
    },
    # README spellings: Read_Me, "read me" in a subfolder; READ-ME is not one
    "readme_variants": {
        "Read_Me.pdf": "%PDF\n",
        "sub/read me.txt": "readme\n",
        "READ-ME.md": "# not a readme by R's pattern\n",
        "data.csv": "a\n1\n",
    },
    # the same file names in two folders (a vector of paths is not recursive)
    "dup_a": {
        "README.md": "# a\n",
        "data.csv": "a\n1\n",
        "only_a.R": "a <- 1\n",
        "nested/deep.csv": "a\n1\n",
    },
    "dup_b": {
        "README.md": "# b\n",
        "data.csv": "b\n2\n",
    },
    # README and LICENSE in study folders, plus root README and LICENSE
    "study_readme": {
        "README.md": "# root\n",
        "LICENSE": "MIT\n",
        "study1/README.md": "# study 1\n",
        "study1/data.csv": "a\n1\n",
        "study2/readme.txt": "study 2\n",
        "study2/data.csv": "a\n2\n",
    },
    # only collection-level documentation: nothing to group
    "only_docs": {
        "README.md": "# docs\n",
        "LICENSE": "MIT\n",
    },
    # a deposit that is only a vendored R package
    "vendored_only": {
        "pkg/DESCRIPTION": "Package: pkg\nVersion: 0.1.0\n",
        "pkg/NAMESPACE": "export(a)\n",
        "pkg/R/a.R": "a <- function() 1\n",
    },
    # hidden files and folders are not listed
    "hidden": {
        ".hidden.csv": "a\n1\n",
        ".hiddendir/config": "[core]\n",
        "visible.csv": "a\n1\n",
        "README.md": "# hidden\n",
    },
    # non-ASCII file and folder names
    "unicode": {
        "README.md": "# unicode\n",
        "données.csv": "a\n1\n",
        "Übersicht.R": "x <- 1\n",
        "études/Étude 1.csv": "a\n1\n",
        "日本語.txt": "text\n",
    },
}


def main() -> None:
    for folder, files in FILES.items():
        for rel, content in files.items():
            path = REVIEW / folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8") if isinstance(content, str) else content
            path.write_bytes(data)


if __name__ == "__main__":
    main()
