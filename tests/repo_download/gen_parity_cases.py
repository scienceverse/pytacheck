"""Write the generated part of ``parity/cases/repo_download.yaml``.

The file-name / file-type cases at the top of that file are written by hand;
everything after the ``# BEGIN GENERATED`` marker (zip peeking, archive
expansion, rate-limit helpers, download_repo_files) is produced here::

    .venv/bin/python tests/repo_download/gen_parity_cases.py

Cases that need several calls use the helpers in
``tests/repo_download/parity_helpers.R`` (R) and
``tests/repo_download/_parity_helpers.py`` (Python) through ``do.call()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "parity" / "cases" / "repo_download.yaml"
MARKER = "# BEGIN GENERATED"

HELPERS_PY = "__import__('tests.repo_download._parity_helpers', fromlist=['x'])"
MOCKS = "tests/repo_download/mocks"
MOCK_URL = "https://mock.example.org/zips/"


def r_helper(name: str) -> str:
    return (
        "local({source(file.path(root, 'tests/repo_download/parity_helpers.R'), "
        f"local = TRUE); {name}}})"
    )


def helper_call(
    case_id: str, r_name: str, py_name: str, args: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    """A case running ``do.call(<helper>, args)`` on both sides."""
    case: dict[str, Any] = {
        "id": case_id,
        "r": "do.call",
        "py": "tests.repo_download._parity_helpers.do_call",
        "args": {
            "what": {"$expr": {"r": r_helper(r_name), "py": f"{HELPERS_PY}.{py_name}"}},
            "args": args,
        },
    }
    case.update(extra)
    return case


def hex_of(
    case_id: str, r_fn: str, py_fn: str, args: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    """``as.character(<raw result>)``: raw vectors compared as hex strings."""
    case: dict[str, Any] = {
        "id": case_id,
        "r": "as.character",
        "py": "tests.repo_download._parity_helpers.hexbytes",
        "args": {"x": {"$call": {"r": r_fn, "py": py_fn, "args": args}}},
    }
    case.update(extra)
    return case


def fn_case(
    case_id: str, r_fn: str, py_fn: str, args: dict[str, Any], **extra: Any
) -> dict[str, Any]:
    case: dict[str, Any] = {"id": case_id, "r": r_fn, "py": py_fn, "args": args}
    case.update(extra)
    return case


ZP = "metacheck.archives.zip_peek."
DL = "metacheck.archives.download."


def zip_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    # archive-format classification
    names = {
        "$chr": [
            "a.zip",
            "B.ZIP",
            "x.zip.gz",
            "t.tar.gz",
            "t.TGZ",
            "t.tbz",
            "t.tbz2",
            "t.tar.bz2",
            "t.tar.xz",
            "t.txz",
            "t.tar",
            "r.csv.gz",
            "r.bz2",
            "r.XZ",
            "x.7z",
            "x.rar",
            "zip",
            ".zip",
            "a.tar.gz.zip",
            "dir/x.Tar.Gz",
            None,
            "",
        ]
    }
    for fn in ("is_zip", "is_tar_archive", "is_single_compress", "is_readable_archive"):
        cases.append(fn_case(f"{fn}.names", f".{fn}", ZP + f"_{fn}", {"name": names}))

    # zip_peek() over recorded HTTP answers
    for name in (
        "mixed.zip",
        "big.zip",
        "stored.zip",
        "comment.zip",
        "unicode.zip",
        "zip64.zip",
        "notzip.zip",
        "ignored-range.zip",
        "refused.zip",
        "nolength.zip",
    ):
        cases.append(
            fn_case(
                f"zip_peek.{name}",
                "zip_peek",
                ZP + "zip_peek",
                {"url": MOCK_URL + name},
                mock_dir=MOCKS,
            )
        )
    cases.append(
        fn_case(
            "zip_peek.tail_retry",
            "zip_peek",
            ZP + "zip_peek",
            {"url": MOCK_URL + "tail-retry.zip", "tail_bytes": 50},
            mock_dir=MOCKS,
        )
    )
    cases.append(
        hex_of(
            "http_range_tail.ignored_range",
            "metacheck:::.http_range_tail",
            ZP + "_http_range_tail",
            {"url": MOCK_URL + "ignored-range.zip", "n": 40},
            mock_dir=MOCKS,
        )
    )
    cases.append(
        hex_of(
            "http_range_tail.partial",
            "metacheck:::.http_range_tail",
            ZP + "_http_range_tail",
            {"url": MOCK_URL + "stimuli.zip", "n": 30},
            mock_dir=MOCKS,
        )
    )
    cases.append(
        hex_of(
            "http_range_tail.known_total",
            "metacheck:::.http_range_tail",
            ZP + "_http_range_tail",
            {"url": MOCK_URL + "stimuli.zip", "n": 10, "total": 0},
            mock_dir=MOCKS,
        )
    )
    cases.append(
        hex_of(
            "http_range_bytes.whole_body_is_not_a_range",
            "metacheck:::.http_range_bytes",
            ZP + "_http_range_bytes",
            {"url": MOCK_URL + "stimuli.zip", "from": 0, "to": 9},
            mock_dir=MOCKS,
        )
    )
    cases.append(
        hex_of(
            "http_range_bytes.exact_length",
            "metacheck:::.http_range_bytes",
            ZP + "_http_range_bytes",
            {"url": MOCK_URL + "stimuli.zip", "from": 10, "to": 457},
            mock_dir=MOCKS,
        )
    )
    cases.append(
        hex_of(
            "http_range_bytes.bad_range",
            "metacheck:::.http_range_bytes",
            ZP + "_http_range_bytes",
            {"url": MOCK_URL + "stimuli.zip", "from": 9, "to": 2},
            mock_dir=MOCKS,
        )
    )
    for fid, url, skip in (
        ("data", "mixed.zip", None),
        ("stimuli", "stimuli.zip", None),
        ("unreadable", "notzip.zip", None),
        (
            "skip_everything",
            "mixed.zip",
            {"$chr": ["materials", "data", "code", "documentation", "unknown"]},
        ),
        ("skip_nothing", "stimuli.zip", {"$chr": []}),
        ("skip_data", "stored.zip", "data"),
        ("unicode", "unicode.zip", "materials"),
        ("folder_entries", "comment.zip", "materials"),
    ):
        args: dict[str, Any] = {"url": MOCK_URL + url}
        if skip is not None:
            args["skip_types"] = skip
        cases.append(
            fn_case(
                f"zip_decision.{fid}", "zip_decision", ZP + "zip_decision", args, mock_dir=MOCKS
            )
        )

    # single members
    for fid, url, names_arg in (
        ("all", "stored.zip", None),
        ("subset", "stored.zip", ["notes.txt", "empty.txt"]),
        ("none_wanted", "stored.zip", "nothing.csv"),
        ("zip64", "zip64.zip", None),
        ("unreadable", "notzip.zip", None),
        ("deflated", "mixed.zip", ["study.csv", "missing.csv"]),
    ):
        args = {"url": MOCK_URL + url}
        if names_arg is not None:
            args["names"] = names_arg
        cases.append(
            helper_call(
                f"zip_fetch_members.{fid}",
                "rd_fetch_members",
                "fetch_members",
                args,
                mock_dir=MOCKS,
            )
        )
    # expanding downloaded archives
    for fid, fn, fixture, skip, minimal in (
        ("zip_mixed", "_expand_zip", "mixed.zip", "materials", False),
        ("zip_skip_code", "_expand_zip", "mixed.zip", ["materials", "code"], False),
        ("zip_minimal_row", "_expand_zip", "mixed.zip", "materials", True),
        ("zip_stimuli", "_expand_zip", "stimuli.zip", "materials", False),
        ("zip_comment", "_expand_zip", "comment.zip", "materials", False),
        ("zip_unicode", "_expand_zip", "unicode.zip", "materials", False),
        ("zip_empty", "_expand_zip", "empty.zip", "materials", False),
        ("zip_not_a_zip", "_expand_zip", "notzip.bin", "materials", False),
        ("zip_missing", "_expand_zip", "no-such.zip", "materials", False),
        ("tar", "_expand_tar", "archive.tar.gz", "materials", False),
        ("tar_not_a_tar", "_expand_tar", "notzip.bin", "materials", False),
        ("gz", "_expand_compressed", "results.csv.gz", "materials", False),
        ("bz2", "_expand_compressed", "results.csv.bz2", "materials", False),
        ("xz", "_expand_compressed", "results.csv.xz", "materials", False),
        ("gz_uncompressed", "_expand_compressed", "plain.csv.gz", "materials", False),
        ("gz_materials", "_expand_compressed", "stimuli.png.gz", "materials", False),
        ("gz_wrong_ext", "_expand_compressed", "mixed.zip", "materials", False),
        ("gz_minimal_row", "_expand_compressed", "results.csv.gz", "materials", True),
    ):
        cases.append(
            helper_call(
                f"expand.{fid}",
                "rd_expand",
                "expand",
                {"fn": fn, "fixture": fixture, "skip_types": skip, "minimal": minimal},
            )
        )
    return cases


def download_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    # pure helpers
    rel_urls = [
        "https://osf.io/abc",
        "http://github.com/o/r",
        "",
        "___",
        "osf.io/ab c/?x=1",
        "https://zenodo.org/records/123#files",
        "https://doi.org/10.5061/dryad.x1",
        "https://exämple.org/répo",
        "::::",
    ]
    for i, url in enumerate(rel_urls):
        cases.append(
            fn_case(
                f"repo_cache_rel.url{i}",
                ".repo_cache_rel",
                DL + "_repo_cache_rel",
                {
                    "repo_url": url,
                    "file_path": {
                        "$chr": [
                            "data/a.csv",
                            "\\\\win\\\\b.R",
                            "//abs/c.txt",
                            "",
                            None,
                            "déjà/vu.txt",
                        ]
                    },
                },
            )
        )
    cases.append(
        fn_case(
            "repo_cache_rel.null_repo",
            ".repo_cache_rel",
            DL + "_repo_cache_rel",
            {"repo_url": {"$null": True}, "file_path": "x.csv"},
        )
    )
    cases.append(
        fn_case(
            "repo_cache_rel.na_repo",
            ".repo_cache_rel",
            DL + "_repo_cache_rel",
            {"repo_url": {"$chr": [None]}, "file_path": "x.csv"},
        )
    )

    for cid, args in (
        ("na", {"timeout_s": 120, "expected_bytes": {"$dbl": [None]}}),
        ("zero", {"timeout_s": 120, "expected_bytes": 0}),
        ("negative", {"timeout_s": 120, "expected_bytes": -5}),
        ("small", {"timeout_s": 10, "expected_bytes": 1024}),
        ("large", {"timeout_s": 10, "expected_bytes": 524288000}),
        ("custom_rate", {"timeout_s": 10, "expected_bytes": 524288000, "min_bytes_per_s": 1048576}),
        ("default", {"timeout_s": 60}),
    ):
        cases.append(
            fn_case(
                f"zip_timeout_for_size.{cid}",
                ".zip_timeout_for_size",
                DL + "_zip_timeout_for_size",
                args,
            )
        )
    for s in (0.2, 5, 47.2, 59.99, 60, 740, 3599, 3600, 5040, 86400):
        sid = str(s).replace(".", "_")
        cases.append(
            fn_case(
                f"format_wait_duration.s{sid}",
                ".format_wait_duration",
                "metacheck.http._format_wait_duration",
                {"seconds": float(s)},
            )
        )
    statuses = [200, 206, 301, 403, 404, 429, 500, 501, 502, 503, 504]
    cases.append(
        {
            "id": "storage_is_transient.statuses",
            "r": "identity",
            "py": "tests.repo_download._parity_helpers.identity",
            "args": {
                "x": {
                    "$expr": {
                        "r": "vapply(c(" + ", ".join(map(str, statuses)) + "), function(s) "
                        "metacheck:::.storage_is_transient(httr2::response(status_code = s)), logical(1))",
                        "py": "[__import__('metacheck.archives.download', fromlist=['x'])._storage_is_transient("
                        "__import__('httpx').Response(s)) for s in ["
                        + ", ".join(map(str, statuses))
                        + "]]",
                    }
                }
            },
        }
    )
    cases.extend(_new_helper_cases())
    for cid, headers, offset in (
        ("no_headers", {}, None),
        ("not_exhausted", {"ratelimit-remaining": "5", "ratelimit-reset": "99999999999"}, None),
        ("bad_reset", {"ratelimit-remaining": "0", "ratelimit-reset": "not-a-number"}, None),
        ("past_reset", {"ratelimit-remaining": "0"}, -60),
        ("x_prefix_past", {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1000"}, None),
        ("remaining_float_zero", {"ratelimit-remaining": "0.0", "ratelimit-reset": "5"}, None),
        ("remaining_only", {"ratelimit-remaining": "0"}, None),
        ("mixed_prefix", {"x-ratelimit-remaining": "0", "ratelimit-reset": "10"}, None),
    ):
        args: dict[str, Any] = {"status": 429, "headers": headers or {"x-other": "1"}}
        if offset is not None:
            args["reset_offset"] = offset
        cases.append(
            {
                "id": f"rate_limit_wait.{cid}",
                "r": ".rate_limit_wait",
                "py": DL + "_rate_limit_wait",
                "args": {
                    "resp": {
                        "$call": {
                            "r": "do.call",
                            "py": "tests.repo_download._parity_helpers.do_call",
                            "args": {
                                "what": {
                                    "$expr": {
                                        "r": r_helper("rd_rate_limit_response"),
                                        "py": f"{HELPERS_PY}.rate_limit_response",
                                    }
                                },
                                "args": args,
                            },
                        }
                    }
                },
            }
        )
    for fixture in ("login.html", "page.html", "binary.dat", "mixed.zip", "no-such-file.html"):
        cases.append(
            fn_case(
                f"is_login_page.{fixture}",
                ".is_login_page",
                DL + "_is_login_page",
                {"path": {"$file": f"tests/repo_download/data/{fixture}"}},
            )
        )
    # nolength.zip: a HEAD without Content-Length, then the one-byte ranged GET
    # (its mock answers 206 with no Content-Range: no size). It replaces the
    # case of .remote_content_length(), which metacheck removed; the review
    # cases (size.*) serve real Content-Range answers.
    for fixture in ("mixed.zip", "stimuli.zip", "nolength.zip"):
        cases.append(
            fn_case(
                f"remote_size.{fixture}",
                ".remote_size",
                DL + "_remote_size",
                {"url": MOCK_URL + fixture},
                mock_dir=MOCKS,
            )
        )

    # download_repo_files() over local file:// URLs
    def dl(cid: str, files: dict[str, Any], **kw: Any) -> None:
        cases.append(
            helper_call(
                f"download_repo_files.{cid}",
                "rd_download",
                "download",
                {"files": {"$df": files}, **kw},
            )
        )

    repo_a = "https://example.org/repo-a"
    dl(
        "basic",
        {
            "repo_url": [repo_a, repo_a],
            "file_name": ["results.csv.gz", "page.html"],
            "file_path": ["data/results.csv.gz", "page.html"],
            "file_url": ["x", "x"],
            "file_size": [714, 40],
        },
        file_url=["data/results.csv.gz", "data/page.html"],
        max_file_size=10,
        max_download_size=100,
    )
    dl(
        "failures",
        {
            "repo_url": [repo_a] * 3,
            "file_name": ["results.csv.xz", "login.html", "missing.csv"],
            "file_path": ["results.csv.xz", "sub/login.html", "missing.csv"],
            "file_url": ["x"] * 3,
            "file_size": [452, 98, 10],
            "paper_id": ["p1", "p1", "p2"],
        },
        file_url=["data/results.csv.xz", "data/login.html", "data/missing.csv"],
        max_file_size=10,
        max_download_size=100,
    )
    dl(
        "oversize_and_budget",
        {
            "repo_url": [repo_a] * 5,
            "file_name": ["a.csv", "b.csv", "c.csv", "d.csv", "e.csv"],
            "file_path": ["a.csv", "b.csv", "c.csv", "d.csv", "e.csv"],
            "file_url": ["x"] * 5,
            "file_size": [714, 557, 452, 1440, 5000000],
        },
        file_url=[
            "data/results.csv.gz",
            "data/results.csv.bz2",
            "data/results.csv.xz",
            "data/plain.csv.gz",
            "data/stored.zip",
        ],
        max_file_size=1,
        max_download_size=0.0012,
    )
    dl(
        "file_count_gate",
        {
            "repo_url": [repo_a, repo_a, "https://example.org/repo-b"],
            "file_name": ["a.csv", "b.csv", "c.csv"],
            "file_path": ["a.csv", "b.csv", "c.csv"],
            "file_url": ["x"] * 3,
            "file_size": [714, 557, 452],
        },
        file_url=["data/results.csv.gz", "data/results.csv.bz2", "data/results.csv.xz"],
        max_files_per_repo=1,
        repo_file_counts={"https://example.org/repo-b": 1},
    )
    dl(
        "true_file_counts",
        {
            "repo_url": [repo_a],
            "file_name": ["a.csv"],
            "file_path": ["a.csv"],
            "file_url": ["x"],
            "file_size": [714],
        },
        file_url=["data/results.csv.gz"],
        max_files_per_repo=3,
        repo_file_counts={repo_a: 4000},
    )
    dl(
        "unknown_sizes_probed",
        {
            "repo_url": [repo_a] * 3,
            "file_name": ["a.csv", "b.csv", "c.csv"],
            "file_path": ["a.csv", "b.csv", "c.csv"],
            "file_url": ["x"] * 3,
            "file_size": [None, None, None],
        },
        file_url=["data/results.csv.gz", "data/results.csv.bz2", "data/missing.csv"],
        max_file_size=10,
        max_download_size=100,
    )
    dl(
        "reuse_cache",
        {
            "repo_url": [repo_a, repo_a],
            "file_name": ["a.csv", "b.csv"],
            "file_path": ["a.csv", "b/b.csv"],
            "file_url": ["x", "x"],
            "file_size": [714, 557],
        },
        file_url=["data/results.csv.gz", "data/results.csv.bz2"],
        twice=True,
    )
    dl(
        "no_file_path",
        {
            "repo_url": [repo_a, None],
            "file_name": ["a.csv", "b.csv"],
            "file_url": ["x", "x"],
            "file_size": [714, 557],
        },
        file_url=["data/results.csv.gz", "data/results.csv.bz2"],
    )
    dl(
        "missing_urls",
        {
            "repo_url": [repo_a, repo_a, repo_a],
            "file_name": ["a.csv", "b.csv", "c.csv"],
            "file_path": ["a.csv", None, "c.csv"],
            "file_url": ["x", "x", "x"],
            "file_size": [714, 557, 452],
            "file_location": [None, None, "/somewhere/c.csv"],
        },
        file_url={"$chr": ["data/results.csv.gz", "", None]},
    )
    dl(
        "no_file_size_column",
        {"repo_url": [repo_a], "file_name": ["a.csv"], "file_path": ["a.csv"], "file_url": ["x"]},
        file_url=["data/results.csv.gz"],
    )
    dl(
        "infinite_caps",
        {
            "repo_url": [repo_a, repo_a],
            "file_name": ["a.csv", "b.csv"],
            "file_path": ["a.csv", "b.csv"],
            "file_url": ["x", "x"],
            "file_size": [714, 557],
        },
        file_url=["data/results.csv.gz", "data/results.csv.bz2"],
        max_file_size={"$expr": {"r": "Inf", "py": "float('inf')"}},
        max_download_size={"$expr": {"r": "Inf", "py": "float('inf')"}},
    )
    cases.append(
        helper_call(
            "download_repo_files.archive_members",
            "rd_download",
            "download",
            {
                "files": {
                    "$df": {
                        "repo_url": ["https://example.org/repo-z"] * 4,
                        "file_name": ["data.csv", "notes.txt", "empty.txt", "big.csv"],
                        "file_path": [
                            "stored.zip/data.csv",
                            "stored.zip/notes.txt",
                            "stored.zip/empty.txt",
                            "stored.zip/big.csv",
                        ],
                        "file_url": [None, None, None, None],
                        "file_size": [1440, 6, 0, 5e8],
                        "archive_url": [MOCK_URL + "stored.zip"] * 4,
                        "archive_member": ["data.csv", "notes.txt", "empty.txt", "big.csv"],
                    }
                },
                "max_file_size": 100,
                "max_download_size": 0.001,
            },
            mock_dir=MOCKS,
        )
    )
    return cases


class _Dumper(yaml.SafeDumper):
    """Quote every string (PARITY.md), leave numbers, booleans and nulls plain; no aliases."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')


_Dumper.add_representer(str, _str)


def _expr_case(case_id: str, r: str, py: str) -> dict[str, Any]:
    """A case comparing one R expression with one Python expression."""
    return {
        "id": case_id,
        "r": "identity",
        "py": "tests.repo_download._parity_helpers.identity",
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }


def _new_helper_cases() -> list[dict[str, Any]]:
    """Helpers added in metacheck 0.4.3.x: host matching, guestbooks, downloads for a run."""
    dl = "__import__('metacheck.archives.download', fromlist=['x'])"
    urls = [
        "https://osf.io/download/abc/",
        "https://files.de-1.osf.io/v1/x",
        "https://web.archive.org/web/2020/https://osf.io/abc/",
        "https://example.com/?u=osf.io",
        "https://osf.io.example.com/x",
        "https://notosf.io/x",
        "https://OSF.IO/x",
        "not a url",
    ]
    r_urls = "c(" + ", ".join('"' + u + '"' for u in urls) + ")"
    py_urls = "[" + ", ".join('"' + u + '"' for u in urls) + "]"
    answers = [
        '{"status":"ERROR","message":"You may not download this file without the required Guestbook response for guestbookID 270."}',
        '{"status":"ERROR","message":"Bad request"}',
        "Guestbook response",
        "guestbook response",
    ]
    r_answers = "c(" + ", ".join("'" + a + "'" for a in answers) + ")"
    py_answers = "[" + ", ".join(repr(a) for a in answers) + "]"
    names = ["a.R", "x.csv", "model.stan", "fig.png", "song.wav", "paper.pdf", "fig.png", "lib.R"]
    paths = [*names[:7], "renv/library/lib.R"]
    repos = ["https://zenodo.org/records/1"] * 6 + [
        "https://github.com/a/b",
        "https://zenodo.org/records/1",
    ]

    def r_chr(v: list[str]) -> str:
        return "c(" + ", ".join('"' + x + '"' for x in v) + ")"

    def py_chr(v: list[str]) -> str:
        return "[" + ", ".join('"' + x + '"' for x in v) + "]"

    return [
        _expr_case(
            "url_host_is.osf",
            f'vapply({r_urls}, function(u) metacheck:::.url_host_is(u, "osf.io"), logical(1), USE.NAMES = FALSE)',
            f'[{dl}._url_host_is(u, "osf.io") for u in {py_urls}]',
        ),
        _expr_case(
            "url_host_is.several_domains",
            'vapply(c("https://dataverse.nl/api/x", "https://x.dataverse.nl/y", "https://nodataverse.nl/"), '
            'function(u) metacheck:::.url_host_is(u, c("dataverse.harvard.edu", "dataverse.nl")), '
            "logical(1), USE.NAMES = FALSE)",
            f'[{dl}._url_host_is(u, ["dataverse.harvard.edu", "dataverse.nl"]) for u in '
            '["https://dataverse.nl/api/x", "https://x.dataverse.nl/y", "https://nodataverse.nl/"]]',
        ),
        _expr_case(
            "dataverse_guestbook_hint.answers",
            f"vapply({r_answers}, function(a) metacheck:::.dataverse_guestbook_hint("
            "httr2::response(status_code = 400, body = charToRaw(a))), character(1), USE.NAMES = FALSE)",
            f"[{dl}._dataverse_guestbook_hint(__import__('httpx').Response(400, content=a.encode())) "
            f"for a in {py_answers}]",
        ),
        _expr_case(
            "dataverse_guestbook_hint.empty_body",
            "metacheck:::.dataverse_guestbook_hint(httr2::response(status_code = 400))",
            f"{dl}._dataverse_guestbook_hint(__import__('httpx').Response(400))",
        ),
        _expr_case(
            "run_download_want.media",
            f"metacheck:::.run_download_want(data.frame(file_name = {r_chr(names)}, "
            f"file_path = {r_chr(paths)}, repo_url = {r_chr(repos)}))",
            f"{dl}._run_download_want(__import__('pandas').DataFrame("
            f"{{'file_name': {py_chr(names)}, 'file_path': {py_chr(paths)}, "
            f"'repo_url': {py_chr(repos)}}}))",
        ),
        _expr_case(
            "run_download_want.no_path_column",
            'metacheck:::.run_download_want(data.frame(file_name = c("a.R", "b.png", "renv/x.R"), '
            'repo_url = c("https://zenodo.org/records/1", "https://zenodo.org/records/1", '
            '"https://gitlab.com/a/b")))',
            f"{dl}._run_download_want(__import__('pandas').DataFrame("
            "{'file_name': ['a.R', 'b.png', 'renv/x.R'], "
            "'repo_url': ['https://zenodo.org/records/1', 'https://zenodo.org/records/1', "
            "'https://gitlab.com/a/b']}))",
        ),
        _expr_case(
            "run_download_want.empty",
            "metacheck:::.run_download_want(data.frame(file_name = character(0), repo_url = character(0)))",
            f"{dl}._run_download_want(__import__('pandas').DataFrame("
            "{'file_name': [], 'repo_url': []}))",
        ),
    ]


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    head = text.split(MARKER)[0].rstrip() + "\n\n"
    cases = zip_cases() + download_cases()
    body = yaml.dump(cases, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=100)
    TARGET.write_text(
        head + MARKER + " by tests/repo_download/gen_parity_cases.py -- do not edit below\n" + body,
        encoding="utf-8",
    )
    print(f"wrote {len(cases)} generated cases to {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
