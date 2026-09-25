"""Regression tests for divergences found reviewing the GitHub/GitLab/Zenodo port.

Each test pins a branch where pytacheck used to differ from metacheck; the
matching ``archives_gz_review`` parity cases check the same behaviour against
R. Requests are served by ``mocks_review`` (see ``make_review_mocks.py``).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
MOCKS_REVIEW = HERE / "mocks_review"


@pytest.fixture
def review() -> Iterator[object]:
    from tests.httpmock import replay

    with replay(MOCKS_REVIEW) as router:
        yield router


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def test_github_readme_with_nul_stops(review: object) -> None:
    from pytacheck.archives.github import github_readme

    # R: rawToChar() refuses an embedded NUL
    with pytest.raises(ValueError, match="embedded nul"):
        github_readme("rv/readmenul")


@pytest.mark.parametrize("repo", ["rv/langarr", "rv/langscalar"])
def test_github_languages_unnamed_body_stops(review: object, repo: str) -> None:
    from pytacheck.archives.github import github_languages

    # names() of a JSON array/scalar is NULL, a zero-row column for data.frame()
    with pytest.raises(ValueError, match="differing number of rows"):
        github_languages(repo)


def test_github_tree_files_filter_realigns_on_untyped_entry(review: object) -> None:
    from pytacheck.archives.github import github_tree_files

    out = github_tree_files("rv/tree5")
    # Filter() drops the untyped entry's logical(0), shifting later flags:
    # "d" (a tree) is kept and "e.txt" (a blob) is lost, as in metacheck
    assert out["files"]["path"].tolist() == ["a.txt", "d", "c.txt"]
    assert out["files"]["size"].tolist()[0] == 1.0
    assert pd.isna(out["files"]["size"].tolist()[1])


def test_github_tree_files_string_size_stops(review: object) -> None:
    from pytacheck.archives.github import github_tree_files

    with pytest.raises(TypeError, match="must be type 'double'"):
        github_tree_files("rv/tree6")


def test_github_tree_files_vector_falls_back_to_contents(review: object) -> None:
    from pytacheck.archives.github import github_tree_files

    out = github_tree_files(["rv/files", "rv/nope"])
    assert out["gated"] is False
    assert out["default_branch"] == "main"
    assert out["license"] is None
    files = out["files"]
    # the vectorised github_files(): every input row kept, rv/nope unmatched
    assert set(files["repo"].tolist()) == {"rv/files", "rv/nope"}
    assert files.loc[files["repo"] == "rv/nope", "path"].isna().all()


def test_github_info_vector_with_missing_repo_stops(review: object) -> None:
    from pytacheck.archives.github import github_info

    with pytest.raises(ValueError, match="differing number of rows: 1, 0"):
        github_info(["rv/files", "rv/nope"])


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------


def test_gitlab_tree_files_untyped_pathless_and_object_nodes(review: object) -> None:
    from pytacheck.archives.gitlab import gitlab_tree_files

    out = gitlab_tree_files("rv/glq")
    files = out["files"]
    # entries 1, 3, 4 and 5 of the six (the untyped one shifts the flags, so
    # the tree "d" is kept and the last blob, from the page given as a JSON
    # object, is lost)
    assert files["path"].tolist() == ["a.txt", "d", "c.txt", ""]
    sizes = files["size"].tolist()
    assert sizes[0] == 1.0
    assert sizes[1] == 2.5
    # c.txt has no node; "" never matches a name in R, though a node has path ""
    assert pd.isna(sizes[2])
    assert pd.isna(sizes[3])


def test_gitlab_tree_files_vector(review: object) -> None:
    from pytacheck.archives.gitlab import gitlab_tree_files

    out = gitlab_tree_files(["rv/glp", "https://gitlab.com/rv/glp"])
    assert out["gated"] is True
    assert out["reason"] == "invalid or inaccessible GitLab repository"
    with pytest.raises(ValueError, match="missing value"):
        gitlab_tree_files(["rv/glp", "rv/nope"])


@pytest.mark.parametrize("module", ["gitlab", "zenodo_upload"])
def test_pat_setters_accept_length_one_vector(module: str) -> None:
    import importlib

    mod = importlib.import_module(f"pytacheck.archives.{module}")
    fn = mod.gitlab_pat if module == "gitlab" else mod.zenodo_pat
    assert fn(["tok"]) == "tok"
    assert fn() == "tok"
    with pytest.raises(ValueError, match="single string"):
        fn(["a", "b"])


# ---------------------------------------------------------------------------
# Zenodo records
# ---------------------------------------------------------------------------


def test_zenodo_info_incompatible_licence_types_stop(review: object) -> None:
    from pytacheck.archives.zenodo import zenodo_info

    # 5559001's licence is a JSON object (a list column), 5559007's a string
    with pytest.raises(TypeError, match="Can't combine"):
        zenodo_info(["5559007", "5559001"])


def test_zenodo_info_na_in_a_table(review: object) -> None:
    from pytacheck.archives.zenodo import zenodo_info

    # U33: metacheck fails on the NA ("row names contain missing values")
    out = zenodo_info(pd.DataFrame({"u": ["5559007", None]}))
    assert out["title"].tolist()[0] == "T7"
    out = zenodo_info(pd.DataFrame({"u": ["5559007", None, "5559007"]}))
    assert out["title"].tolist()[0] == "T7"
    out = zenodo_info(pd.DataFrame({"u": [None, "5559007", None]}))
    assert len(out) == 3


def test_zenodo_info_id_col_positions(review: object) -> None:
    from pytacheck.archives.zenodo import zenodo_info

    with pytest.raises(IndexError):
        zenodo_info(pd.DataFrame({"u": ["5559007"], "v": ["x"]}), id_col=0)
    with pytest.raises(IndexError):
        zenodo_info(pd.DataFrame({"u": ["5559007"]}), id_col=3)
    # [[-1]] of a two-column table is the other column
    out = zenodo_info(pd.DataFrame({"v": ["x"], "u": ["5559007"]}), id_col=-1)
    assert out["zenodo_id"].tolist() == ["5559007"]


# ---------------------------------------------------------------------------
# Zenodo uploads
# ---------------------------------------------------------------------------


def test_zenodo_license_id_longer_vector_stops() -> None:
    from pytacheck.archives.zenodo_upload import _zenodo_license_id

    with pytest.raises(ValueError, match="length = 2"):
        _zenodo_license_id(["MIT License", "x"])
    assert _zenodo_license_id(["MIT License"]) == "mit"


def test_jsonlite_body_numbers() -> None:
    from pytacheck.archives.zenodo_upload import _json_body

    body = _json_body(
        {
            "a": 2.0,
            "b": 0.1,
            "c": 3,
            "d": 1e-5,
            "e": [None, "é/\u0001"],
            "f": {},
            "g": [],
            "h": float("nan"),
            "i": True,
        }
    )
    assert body.decode() == (
        '{"a":2,"b":0.10000000000000001,"c":3,"d":1.0000000000000001e-05,'
        '"e":[null,"é/\\u0001"],"f":{},"g":[],"h":"NA","i":true}'
    )


def test_build_metadata_keeps_na_tags_and_blank_creators() -> None:
    from pytacheck.archives.zenodo_upload import _zenodo_build_metadata

    md = _zenodo_build_metadata({"title": "T", "tags": ["a", None], "creators": ""}, "/tmp/f")
    assert md["keywords"] == ["a", None]
    assert md["creators"] == ""


def test_meta_from_folder_odd_shapes(tmp_path: Path) -> None:
    from pytacheck.archives.zenodo_upload import _zenodo_meta_from_folder

    meta_dir = tmp_path / "_osf_metadata"
    meta_dir.mkdir()
    (meta_dir / "metadata.json").write_text(
        json.dumps(
            {
                "osf_id": "abcde",
                "tags": ["a", None, ["b", 1]],
                "contributors": {
                    "x": {"name": "N"},
                    "y": {"family_name": "F", "given_name": "G"},
                },
            }
        ),
        encoding="utf-8",
    )
    meta = _zenodo_meta_from_folder(str(tmp_path))
    assert meta is not None
    # unlist(): flattened, NULL dropped, coerced to character
    assert meta["tags"] == ["a", "b", "1"]
    # lapply() over a JSON object keeps its names
    assert meta["creators"] == {"x": {"name": "N"}, "y": {"name": "F, G"}}
    (meta_dir / "metadata.json").write_text('"just a string"', encoding="utf-8")
    with pytest.raises(TypeError):
        _zenodo_meta_from_folder(str(tmp_path))


def test_zenodo_upload_sends_jsonlite_numbers(review: object, upload_dir: Path) -> None:
    import warnings

    from pytacheck.archives.zenodo_upload import zenodo_upload

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = zenodo_upload(
            str(upload_dir / "proj_plain"),
            zenodo_pat="fake-token",
            publish=True,
            metadata={
                "version": 2.0,
                "weight": 0.1,
                "count": 3,
                "tiny": 1e-5,
                "keywords": ["a", None],
            },
            as_zip=False,
            ask=False,
        )
    assert out is not None
    # the metadata PUT only matches the recorded body if it is byte-identical
    # to httr2's; otherwise it warns and the deposition is not published
    assert not [w for w in caught if "Adding metadata" in str(w.message)]
    assert out["files_uploaded"].tolist() == [3]
    assert out["published"].tolist() == [True]
    assert out["doi"].tolist() == ["10.5072/zenodo.123.pub"]
