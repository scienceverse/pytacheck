"""Write ``parity/cases/repo_download_review.yaml`` (the review parity cases).

Run from the repository root::

    .venv/bin/python tests/repo_download/gen_review_cases.py
    .venv/bin/python -m parity generate --area repo_download_review

The cases run R and Python against the same in-process HTTP server
(``tests/repo_download/review_helpers.R`` / ``_review_helpers.py``), which
serves the fixtures of ``tests/repo_download/data/review`` (made by
``make_review_fixtures.py``) and honours Range requests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "repo_download_review.yaml"
SRV = "https://srv.example.org"
ZIP_FN = {"$expr": {"r": "'.expand_zip'", "py": "'_expand_zip'"}}
TAR_FN = {"$expr": {"r": "'.expand_tar'", "py": "'_expand_tar'"}}


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style='"')


_Dumper.add_representer(str, _str)


def helper(fn_r: str, fn_py: str) -> dict[str, Any]:
    return {
        "$expr": {
            "r": "local({source(file.path(root, 'tests/repo_download/review_helpers.R'), "
            f"local = TRUE); {fn_r}}})",
            "py": f"__import__('tests.repo_download._review_helpers', fromlist=['x']).{fn_py}",
        }
    }


def call(case_id: str, fn_r: str, fn_py: str, args: dict[str, Any], **extra: Any) -> dict[str, Any]:
    case: dict[str, Any] = {
        "id": case_id,
        "r": "do.call",
        "py": "tests.repo_download._parity_helpers.do_call",
        "args": {"what": helper(fn_r, fn_py), "args": args},
    }
    case.update(extra)
    return case


def route(name: str, **kw: Any) -> dict[str, Any]:
    return {"url": f"{SRV}/{name}", "fixture": f"review/{name}", **kw}


def fuzz_names() -> list[str]:
    """~800 file names: documentation/data/code words x separators x extensions."""
    import random

    from metacheck.fileinfo.types import file_types

    rng = random.Random(20260925)
    exts = file_types()["ext"].tolist()
    stems = [
        "readme", "READ_ME", "Read-Me", "read me", "readMe", "codebook", "Code Book", "code_book",
        "code-book", "Race_IAT.public.2025.codebook", "data dictionary", "Data_Dict", "datadict",
        "variable key", "Variable_Key", "var_list", "variables-description", "vardescript",
        "data-legend", "DataLegend", "coding manual", "Coding_Scheme", "coding-sheet", "script",
        "analysis", "analysis_code", "Study1_data", "meta_data", "encoding", "stimuli", "img_01",
        "r\u00e9sum\u00e9", "Donn\u00e9es", "\u00fcbersicht", "\u6570\u636e", "donn\u00e9es brutes",
        "20200101_data", "run-01", "run-2", "x", "a.b", "..hidden", "sub/dir/file",
        "C:\\win\\path", "WITH.variable.labels", "all40_variable key",
        "repetition_texting_tm_data-legend", "notes", "LICENSE", "Makefile", "Dockerfile",
        "renv.lock", "manuscript", "prereg", "materials", "data", "code", "DATA", "CODE", "Codes",
        "script_data", "data_code", "codebook_data", "readme_codebook", " ", "-", "_",
    ]  # fmt: skip
    names = set()
    for stem in stems:
        for _ in range(12):
            e = rng.choice(exts)
            mode = rng.random()
            if mode < 0.2:
                e = e.upper()
            elif mode < 0.3:
                e = e.capitalize()
            sep = rng.choice([".", ".", ".", "", "_", ". "])
            name = f"{stem}{sep}{e}"
            if rng.random() < 0.1:
                name += rng.choice([".gz", ".zip", ".bak", ".", "..", ".GZ"])
            names.add(name)
    return sorted(names)


def main() -> None:
    cases: list[dict[str, Any]] = []

    # -- .parse_zip_central_dir(): names with NULs, empty names ----------------
    for f in ("trailnul", "emptyname", "emptyname_last", "bigoffset", "allnul", "midnul"):
        cases.append(call(f"cd.{f}", "rv_cd", "cd", {"fixture": f"review/{f}.cd"}))

    # -- zip_peek() over the range server --------------------------------------
    for z in ("paths.zip", "cp437.zip", "crc.zip", "climb.zip"):
        cases.append(
            call(f"peek.{z}", "rv_peek", "peek", {"routes": [route(z)], "url": f"{SRV}/{z}"})
        )
    cases += [
        call(
            "peek.range_ignored",
            "rv_peek",
            "peek",
            {"routes": [route("crc.zip", range=False)], "url": f"{SRV}/crc.zip"},
        ),
        call(
            "peek.range_ignored_small_tail",
            "rv_peek",
            "peek",
            {
                "routes": [route("crc.zip", range=False)],
                "url": f"{SRV}/crc.zip",
                "tail_bytes": 100,
            },
        ),
        call(
            "peek.no_length",
            "rv_peek",
            "peek",
            {"routes": [route("crc.zip", length=False)], "url": f"{SRV}/crc.zip"},
        ),
        call(
            "peek.small_tail",
            "rv_peek",
            "peek",
            {"routes": [route("paths.zip")], "url": f"{SRV}/paths.zip", "tail_bytes": 40},
        ),
        call(
            "peek.status_404",
            "rv_peek",
            "peek",
            {"routes": [route("crc.zip", status=404)], "url": f"{SRV}/crc.zip"},
        ),
        call("peek.no_route", "rv_peek", "peek", {"routes": [], "url": f"{SRV}/none.zip"}),
    ]

    # -- zip_decision() --------------------------------------------------------
    for z in ("paths.zip", "crc.zip", "climb.zip", "cp437.zip"):
        cases.append(
            call(
                f"decision.{z}",
                "rv_decision",
                "decision",
                {"routes": [route(z)], "url": f"{SRV}/{z}"},
            )
        )
    cases.append(
        call(
            "decision.skip_data",
            "rv_decision",
            "decision",
            {"routes": [route("crc.zip")], "url": f"{SRV}/crc.zip", "skip_types": ["data"]},
        )
    )

    # -- hosts that refuse HEAD (S3: Dryad, Figshare, Harvard Dataverse) --------
    # A HEAD answered 403 (an error page's Content-Length must not count), the
    # tail then asked for by a suffix range whose Content-Range gives the size.
    crc = f"{SRV}/crc.zip"
    head_403 = {
        "url": crc,
        "method": "HEAD",
        "status": 403,
        "length": False,
        "headers": {"Content-Length": "243"},
    }
    cases += [
        call(
            "peek.s3_head_refused",
            "rv_peek",
            "peek",
            {"routes": [head_403, route("crc.zip", suffix="206")], "url": crc},
        ),
        call(
            "peek.s3_head_refused_small_tail",
            "rv_peek",
            "peek",
            {
                "routes": [head_403, route("crc.zip", suffix="206")],
                "url": crc,
                "tail_bytes": 100,
            },
        ),
        call(
            "peek.no_length_suffix",
            "rv_peek",
            "peek",
            {"routes": [route("crc.zip", length=False, suffix="206")], "url": crc},
        ),
        call(
            "peek.suffix_ignored",
            "rv_peek",
            "peek",
            {"routes": [head_403, route("crc.zip", suffix="ignore")], "url": crc},
        ),
        call(
            "peek.suffix_416",
            "rv_peek",
            "peek",
            {"routes": [head_403, route("crc.zip", suffix="416")], "url": crc},
        ),
        call(
            "peek.suffix_refused",
            "rv_peek",
            "peek",
            {"routes": [head_403, route("crc.zip", status=403)], "url": crc},
        ),
        call(
            "decision.s3_head_refused",
            "rv_decision",
            "decision",
            {"routes": [head_403, route("crc.zip", suffix="206")], "url": crc},
        ),
        call(
            "fetch.s3_head_refused",
            "rv_fetch",
            "fetch",
            {"routes": [head_403, route("crc.zip", suffix="206")], "url": crc},
        ),
        call(
            "fetch.s3_suffix_416",
            "rv_fetch",
            "fetch",
            {
                "routes": [
                    {**head_403, "url": f"{SRV}/paths.zip"},
                    route("paths.zip", suffix="416"),
                ],
                "url": f"{SRV}/paths.zip",
                "names": ["ok/data.csv", "dup.csv"],
            },
        ),
    ]

    # -- .remote_size(): HEAD, then a one-byte ranged GET ------------------------
    sz = f"{SRV}/sz"
    cases += [
        call("size.head", "rv_size", "size", {"routes": [route("crc.zip")], "url": crc}),
        call(
            "size.head_refused",
            "rv_size",
            "size",
            {"routes": [head_403, route("crc.zip")], "url": crc},
        ),
        call(
            "size.no_length",
            "rv_size",
            "size",
            {"routes": [route("crc.zip", length=False)], "url": crc},
        ),
        call(
            "size.range_ignored",
            "rv_size",
            "size",
            {"routes": [route("crc.zip", length=False, range=False)], "url": crc},
        ),
        call(
            "size.private",
            "rv_size",
            "size",
            {
                "routes": [{"url": f"{sz}/p.csv", "status": 403, "body": "denied"}],
                "url": f"{sz}/p.csv",
            },
        ),
        call(
            "size.head_404",
            "rv_size",
            "size",
            {
                "routes": [
                    {"url": f"{sz}/h.csv", "method": "HEAD", "status": 404},
                    {"url": f"{sz}/h.csv", "body": "abcdef"},
                ],
                "url": f"{sz}/h.csv",
            },
        ),
        call("size.no_route", "rv_size", "size", {"routes": [], "url": f"{sz}/none.csv"}),
    ]

    # -- zip_peek(cache = TRUE): the on-disk cache across sessions ---------------
    cases += [
        call(
            "peek_cache.reused",
            "rv_peek_cached",
            "peek_cached",
            {"first": [route("crc.zip")], "second": [], "url": crc},
        ),
        call(
            "peek_cache.failure_kept",
            "rv_peek_cached",
            "peek_cached",
            {"first": [route("crc.zip", range=False)], "second": [route("crc.zip")], "url": crc},
        ),
        call(
            "peek_cache.rate_limited_not_kept",
            "rv_peek_cached",
            "peek_cached",
            {
                "first": [route("crc.zip")],
                "second": [route("crc.zip")],
                "url": crc,
                "rate_limited": True,
            },
        ),
    ]

    # -- .zip_fetch_members() / .zip_member_fetch() ------------------------------
    cases += [
        call(
            "fetch.paths_all",
            "rv_fetch",
            "fetch",
            {"routes": [route("paths.zip")], "url": f"{SRV}/paths.zip"},
        ),
        call(
            "fetch.paths_subset",
            "rv_fetch",
            "fetch",
            {
                "routes": [route("paths.zip")],
                "url": f"{SRV}/paths.zip",
                "names": ["ok/data.csv", "dup.csv", "missing.csv", "sub\\win.csv", "/abs.csv"],
            },
        ),
        call(
            "fetch.paths_dir_first",
            "rv_fetch",
            "fetch",
            {
                "routes": [route("paths.zip")],
                "url": f"{SRV}/paths.zip",
                "names": ["a/b.csv", "a", "z/../../up.csv", "C:/drive.csv"],
            },
        ),
        call(
            "fetch.crc_verify",
            "rv_fetch",
            "fetch",
            {"routes": [route("crc.zip")], "url": f"{SRV}/crc.zip"},
        ),
        call(
            "fetch.crc_noverify",
            "rv_fetch",
            "fetch",
            {"routes": [route("crc.zip")], "url": f"{SRV}/crc.zip", "verify": False},
        ),
        call(
            "fetch.cp437_all",
            "rv_fetch",
            "fetch",
            {"routes": [route("cp437.zip")], "url": f"{SRV}/cp437.zip"},
        ),
        call(
            "fetch.cp437_valid_names",
            "rv_fetch",
            "fetch",
            {
                "routes": [route("cp437.zip")],
                "url": f"{SRV}/cp437.zip",
                "names": ["plain.csv", "nul.csv"],
            },
        ),
        call(
            "fetch.range_ignored",
            "rv_fetch",
            "fetch",
            {"routes": [route("crc.zip", range=False)], "url": f"{SRV}/crc.zip"},
        ),
        call(
            "fetch.bzip2",
            "rv_fetch",
            "fetch",
            {"routes": [route("bzcrc.zip")], "url": f"{SRV}/bzcrc.zip"},
        ),
    ]
    for name in ("good.csv", "badcrc.csv", "badsize.csv", "empty.csv", "stored.txt"):
        cases.append(
            call(
                f"member.{name}",
                "rv_member",
                "member",
                {"routes": [route("crc.zip")], "url": f"{SRV}/crc.zip", "name": name},
            )
        )
    cases.append(
        call(
            "member.badcrc_noverify",
            "rv_member",
            "member",
            {
                "routes": [route("crc.zip")],
                "url": f"{SRV}/crc.zip",
                "name": "badcrc.csv",
                "verify": False,
            },
        )
    )

    # -- .expand_zip() / .expand_tar(): what R's unzip / GNU tar extract ---------
    for z in (
        "paths.zip",
        "climb.zip",
        "dots.zip",
        "lzma.zip",
        "bzcrc.zip",
        "cp437.zip",
        "crc.zip",
    ):
        cases.append(
            call(
                f"expand.{z}",
                "rv_expand",
                "expand",
                {"fn": ZIP_FN, "fixture": f"review/{z}"},
            )
        )
    cases.append(
        call(
            "expand.climb.zip_skip_data",
            "rv_expand",
            "expand",
            {"fn": ZIP_FN, "fixture": "review/climb.zip", "skip_types": ["data"]},
        )
    )
    cases.append(
        call(
            "expand.hostile.tar.gz",
            "rv_expand",
            "expand",
            {"fn": TAR_FN, "fixture": "review/hostile.tar.gz"},
        )
    )
    cases.append(
        call(
            "expand.dirlink.tar.gz",
            "rv_expand",
            "expand",
            {"fn": TAR_FN, "fixture": "review/dirlink.tar.gz"},
        )
    )
    cases.append(
        call(
            "expand.abslink.tar.gz",
            "rv_expand",
            "expand",
            {"fn": TAR_FN, "fixture": "review/abslink.tar.gz"},
            # its mark (a link that leaves the folder is refused) is in
            # parity/divergences/archives.yaml
        )
    )

    # -- download_repo_files() over the range server -----------------------------
    arc_p = f"{SRV}/paths.zip"
    arc_c = f"{SRV}/cp437.zip"
    cases.append(
        call(
            "download.archive_members",
            "rv_download",
            "download",
            {
                "routes": [route("paths.zip"), route("cp437.zip")],
                "files": {
                    "$df": {
                        "repo_url": ["https://example.org/rp"] * 6 + ["https://example.org/rc"] * 2,
                        "file_name": [
                            "a",
                            "b.csv",
                            "data.csv",
                            "dup.csv",
                            "win.csv",
                            "evil.csv",
                            "plain.csv",
                            "nul.csv",
                        ],
                        "file_path": [
                            "p/a",
                            "p/a/b.csv",
                            "p/ok/data.csv",
                            "p/dup.csv",
                            "p/sub/win.csv",
                            "p/evil.csv",
                            "c/plain.csv",
                            "c/nul.csv",
                        ],
                        "file_url": [None] * 8,
                        "file_size": [13, 817, 817, 12, 4, 2, 817, 8],
                        "archive_url": [arc_p] * 6 + [arc_c] * 2,
                        "archive_member": [
                            "a",
                            "a/b.csv",
                            "ok/data.csv",
                            "dup.csv",
                            "sub\\win.csv",
                            "../evil.csv",
                            "plain.csv",
                            "nul.csv",
                        ],
                    }
                },
            },
        )
    )
    cases.append(
        call(
            "download.archive_members_budget",
            "rv_download",
            "download",
            {
                "routes": [route("paths.zip")],
                "files": {
                    "$df": {
                        "repo_url": ["https://example.org/rp"] * 4,
                        "file_name": ["data.csv", "dup.csv", "b.csv", "abs.csv"],
                        "file_path": ["ok/data.csv", "dup.csv", "a/b.csv", "abs.csv"],
                        "file_url": [None] * 4,
                        "file_size": [817, 12, None, 4],
                        "archive_url": [arc_p] * 4,
                        "archive_member": ["ok/data.csv", "dup.csv", "a/b.csv", "/abs.csv"],
                    }
                },
                "max_download_size": 0.0001,
            },
        )
    )
    err_url = f"{SRV}/files"
    cases.append(
        call(
            "download.sequential_errors",
            "rv_download",
            "download",
            {
                "routes": [
                    {"url": f"{err_url}/a.csv", "status": 404, "body": "gone"},
                    {"url": f"{err_url}/b.csv", "status": 413, "body": "too big"},
                    {"url": f"{err_url}/c.csv", "status": 431, "body": "x"},
                    {
                        "url": f"{err_url}/d.csv",
                        "status": 401,
                        "body": "{}",
                        "headers": {
                            "WWW-Authenticate": 'Bearer realm="Doorkeeper", error="invalid_token", '
                            'error_description="The access token is invalid because it has '
                            'expired or was revoked by the resource owner"'
                        },
                    },
                    {
                        "url": f"{err_url}/e.csv",
                        "status": 401,
                        "body": "{}",
                        "headers": {"WWW-Authenticate": 'Basic realm="files"'},
                    },
                    {
                        "url": f"{err_url}/f.csv",
                        "status": 400,
                        "body": "{}",
                        "headers": {"WWW-Authenticate": "Bearer"},
                    },
                ],
                "files": {
                    "$df": {
                        "repo_url": ["https://example.org/seq"] * 7,
                        "file_name": [
                            "a.csv",
                            "b.csv",
                            "c.csv",
                            "d.csv",
                            "e.csv",
                            "f.csv",
                            "g.csv",
                        ],
                        "file_path": [
                            "a.csv",
                            "b.csv",
                            "c.csv",
                            "d.csv",
                            "e.csv",
                            "f.csv",
                            "g.csv",
                        ],
                        "file_url": [f"{err_url}/{c}.csv" for c in "abcdefg"],
                        "file_size": [10, 10, 10, 10, 10, 10, 10],
                        "paper_id": ["p1", "p1", "p2", "p2", "p3", "p3", "p3"],
                    }
                },
            },
        )
    )
    par_url = f"{SRV}/par"
    login = (
        "<!DOCTYPE html><html><head><title>OSF | Sign in</title></head>"
        "<body>Please sign in</body></html>"
    )
    cases.append(
        call(
            "download.parallel",
            "rv_download",
            "download",
            {
                "routes": [
                    {"url": f"{par_url}/ok.csv", "body": "a,b\n1,2\n"},
                    {"url": f"{par_url}/empty.csv", "body": ""},
                    {"url": f"{par_url}/gone.csv", "status": 404, "body": "gone"},
                    {"url": f"{par_url}/big.csv", "status": 413, "body": "big"},
                    {"url": f"{par_url}/partial.csv", "status": 206, "body": "a,b"},
                    {"url": f"{par_url}/login.csv", "body": login},
                    {"url": f"{par_url}/short.csv", "body": "a,b\n"},
                    {"url": f"{par_url}/unsized.csv", "body": "x,y\n3,4\n"},
                    {"url": "https://zenodo.org.example/records/1/files/z.csv", "body": "z\n1\n"},
                    {"url": f"{par_url}/nolen.csv", "body": "q\n", "length": False},
                ],
                "files": {
                    "$df": {
                        "repo_url": ["https://example.org/par"] * 10,
                        "file_name": [
                            "ok.csv",
                            "empty.csv",
                            "gone.csv",
                            "big.csv",
                            "partial.csv",
                            "login.csv",
                            "short.csv",
                            "unsized.csv",
                            "z.csv",
                            "nolen.csv",
                        ],
                        "file_path": [
                            "ok.csv",
                            "empty.csv",
                            "gone.csv",
                            "big.csv",
                            "partial.csv",
                            "login.csv",
                            "short.csv",
                            "unsized.csv",
                            "z.csv",
                            "nolen.csv",
                        ],
                        "file_url": [
                            f"{par_url}/{n}"
                            for n in (
                                "ok.csv",
                                "empty.csv",
                                "gone.csv",
                                "big.csv",
                                "partial.csv",
                                "login.csv",
                                "short.csv",
                                "unsized.csv",
                            )
                        ]
                        + [
                            "https://zenodo.org.example/records/1/files/z.csv",
                            f"{par_url}/nolen.csv",
                        ],
                        "file_size": [8, 5, 4, 3, 3, len(login), 10, None, 4, None],
                        "provider": ["osfstorage"] * 8 + [None, "osfstorage"],
                    }
                },
                "disk": False,
            },
        )
    )
    ok_routes = [
        {"url": f"{par_url}/ok.csv", "body": "a,b\n1,2\n"},
        {"url": f"{par_url}/unsized.csv", "body": "x,y\n3,4\n"},
        {"url": f"{par_url}/short.csv", "body": "a,b\n"},
        {"url": f"{par_url}/gone.csv", "status": 404, "body": "gone"},
    ]
    stale = {
        "$df": {
            "repo_url": ["https://example.org/par2"] * 4,
            "file_name": ["ok.csv", "unsized.csv", "short.csv", "gone.csv"],
            "file_url": [
                f"{par_url}/{n}" for n in ("ok.csv", "unsized.csv", "short.csv", "gone.csv")
            ],
            "file_size": [8, None, 10, 4],
            "provider": ["osfstorage"] * 4,
            "file_location": ["stale/ok.csv", None, "stale/short.csv", "stale/gone.csv"],
        }
    }
    cases.append(
        call(
            "download.stale_locations",
            "rv_download",
            "download",
            {"routes": ok_routes, "files": stale},
        )
    )
    cases.append(
        call(
            "download.parallel_twice",
            "rv_download",
            "download",
            {"routes": ok_routes, "files": stale, "twice": True},
        )
    )
    cases.append(
        call(
            "download.rate_limit_skip",
            "rv_download",
            "download",
            {
                "routes": [
                    {
                        "url": f"{SRV}/rl/{n}",
                        "status": 429,
                        "body": "slow down",
                        "headers": {"RateLimit-Remaining": "0", "RateLimit-Reset": "4102444800"},
                    }
                    for n in ("seq.csv", "par.csv")
                ],
                "files": {
                    "$df": {
                        "repo_url": ["https://example.org/rl"] * 2,
                        "file_name": ["seq.csv", "par.csv"],
                        "file_url": [f"{SRV}/rl/seq.csv", f"{SRV}/rl/par.csv"],
                        "file_size": [4, 4],
                        "provider": [None, "osfstorage"],
                    }
                },
                "skip_on_api_limit": True,
            },
        )
    )
    cases.append(
        call(
            "download.head_sizes",
            "rv_download",
            "download",
            {
                "routes": [
                    {"url": f"{par_url}/h1.csv", "body": "1234567890"},
                    {"url": f"{par_url}/h2.csv", "body": "12345", "length": False},
                    {"url": f"{par_url}/h3.csv", "status": 404, "body": "missing file"},
                    {"url": f"{par_url}/h4.csv", "body": "x" * 3000},
                ],
                "files": {
                    "$df": {
                        "repo_url": ["https://example.org/heads"] * 4,
                        "file_name": ["h1.csv", "h2.csv", "h3.csv", "h4.csv"],
                        "file_url": [f"{par_url}/h{i}.csv" for i in range(1, 5)],
                        "file_size": [None, None, None, None],
                        "provider": ["osfstorage"] * 4,
                    }
                },
                "max_download_size": 0.002,
            },
        )
    )

    # -- GitHub's zipball: the size estimated from the listed files -------------
    # No whole-repository archive reports its size: an archive with no listed
    # sizes, or an estimate above 2x the budget, is not used, and one that grows
    # past 2x the budget while downloading is stopped (files then one by one).
    csv = 817  # bytes of the CSV each member holds

    def gh(case_id: str, name: str, sizes: list[Any], **kw: Any) -> dict[str, Any]:
        raw = f"https://raw.githubusercontent.com/owner/{name}/main"
        body = "id,x\n" + "".join(f"{i},{i * 3}\n" for i in range(120))
        return call(
            case_id,
            "rv_download_spy",
            "download_spy",
            {
                "routes": [
                    {"url": f"https://github.com/owner/{name}", "body": ""},
                    {
                        "url": f"https://api.github.com/repos/owner/{name}/zipball",
                        "fixture": "review/gh.zip",
                    },
                    {"url": f"{raw}/a.csv", "body": body},
                    {"url": f"{raw}/sub/b.csv", "body": body},
                ],
                "files": {
                    "$df": {
                        "repo_url": [f"https://github.com/owner/{name}"] * 2,
                        "file_name": ["a.csv", "b.csv"],
                        "file_path": ["a.csv", "sub/b.csv"],
                        "file_url": [f"{raw}/a.csv", f"{raw}/sub/b.csv"],
                        "file_size": sizes,
                    }
                },
                **kw,
            },
        )

    cases += [
        gh("download.github_archive", "repo-zip", [csv, csv]),
        gh("download.github_unsized", "repo-unsized", [None, None]),
        gh(
            "download.github_too_large",
            "repo-big",
            [csv, 5 * 1024 * 1024],
            max_file_size=1,
            max_download_size=1,
        ),
        gh("download.github_capped", "repo-cap", [1, 1], max_download_size=0.0001),
        gh(
            "download.github_no_budget",
            "repo-inf",
            [csv, csv],
            max_download_size={"$expr": {"r": "Inf", "py": "float('inf')"}},
        ),
    ]

    # -- file names that are not valid UTF-8 --------------------------------------
    bad_r = 'c("caf\\x82.csv", "ok.R", "READ\\x82ME.txt", "code\\x82book.csv", "x\\x82.R")'
    bad_py = '["caf\\udc82.csv", "ok.R", "READ\\udc82ME.txt", "code\\udc82book.csv", "x\\udc82.R"]'
    cases.append(
        call(
            "invalid.file_category",
            "rv_category",
            "category",
            {"x": {"$expr": {"r": bad_r, "py": bad_py}}},
        )
    )
    cases.append(
        call(
            "invalid.filetype",
            "rv_filetype",
            "filetype",
            {"x": {"$expr": {"r": bad_r, "py": bad_py}}},
        )
    )
    cases.append(
        {
            "id": "invalid.check_file_naming",
            "r": "check_file_naming",
            "py": "metacheck.fileinfo.naming.check_file_naming",
            "args": {"file_name": {"$expr": {"r": bad_r, "py": bad_py}}},
        }
    )
    cases.append(
        {
            "id": "invalid.check_file_naming_path",
            "r": "check_file_naming",
            "py": "metacheck.fileinfo.naming.check_file_naming",
            "args": {
                "file_name": "ok.csv",
                "file_path": {"$expr": {"r": '"d\\x82/ok.csv"', "py": '"d\\udc82/ok.csv"'}},
            },
        }
    )
    cases.append(
        {
            "id": "invalid.check_file_naming_dir_only",
            "r": "check_file_naming",
            "py": "metacheck.fileinfo.naming.check_file_naming",
            "args": {
                "file_name": {"$expr": {"r": '"d\\x82/ok.csv"', "py": '"d\\udc82/ok.csv"'}},
                "file_path": "d/ok.csv",
            },
        }
    )
    cases.append(
        {
            "id": "invalid.is_readable_archive",
            "r": "metacheck:::.is_readable_archive",
            "py": "metacheck.archives.zip_peek._is_readable_archive",
            "args": {
                "name": {
                    "$expr": {
                        "r": 'c("a\\x82.zip", "b.zip", "c\\x82.tar.gz", "d.gz")',
                        "py": '["a\\udc82.zip", "b.zip", "c\\udc82.tar.gz", "d.gz"]',
                    }
                }
            },
        }
    )

    # -- check_file_naming(): paths R's basename()/dirname() treat specially ------
    odd = [
        "~/data 1.csv",
        "dir/",
        "/",
        "",
        "a//b.csv",
        "C:\\x\\y 2.csv",
        "./x.csv",
        "~",
        "abc/def/",
        "x.tar.gz",
        "20200230.csv",
        "2020010120200102.csv",
        "a.20200101",
        "run 01.csv",
        "run 1.csv",
        "run1.csv",
    ]
    cases.append(
        {
            "id": "check_file_naming.odd_paths",
            "r": "check_file_naming",
            "py": "metacheck.fileinfo.naming.check_file_naming",
            "args": {"file_name": odd},
        }
    )
    cases.append(
        {
            "id": "check_file_naming.odd_paths_as_path",
            "r": "check_file_naming",
            "py": "metacheck.fileinfo.naming.check_file_naming",
            "args": {"file_name": [f"f{i}.csv" for i in range(len(odd))], "file_path": odd},
        }
    )

    # -- many generated file names, as repo listings name them ---------------------
    names = fuzz_names()
    cases.append(
        {
            "id": "file_category.generated_names",
            "r": "file_category",
            "py": "metacheck.fileinfo.category.file_category",
            "args": {"contents": names},
        }
    )
    cases.append(
        {
            "id": "filetype.generated_names",
            "r": "filetype",
            "py": "metacheck.fileinfo.category.filetype",
            "args": {"filename": names},
        }
    )
    cases.append(
        {
            "id": "check_file_naming.generated_names",
            "r": "check_file_naming",
            "py": "metacheck.fileinfo.naming.check_file_naming",
            "args": {
                "file_name": names,
                "data_type": {"$chr": ["unknown", "data", None, "code", "unknown"]},
            },
        }
    )

    doc = {"area": "repo_download_review", "cases": cases}
    header = (
        "# Review parity cases for repo_download (generated by\n"
        "# tests/repo_download/gen_review_cases.py; do not edit by hand).\n"
        "# R and Python are served the same bytes by an in-process HTTP server\n"
        "# (tests/repo_download/review_helpers.R, _review_helpers.py) that honours\n"
        "# Range requests; fixtures: tests/repo_download/data/review.\n"
    )
    OUT.write_text(
        header + yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
