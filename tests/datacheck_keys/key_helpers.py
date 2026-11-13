"""Python side of the key scanner's parity cases (twin of ``key_helpers.R``).

The keys are made when a case runs, by the same generator as in R, so that no file
in the repository holds one; no result holds one either: the helpers return where a
key was found, which rule found it and whether it is the key made.
"""

from __future__ import annotations

import contextlib
import functools
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from metacheck import keys as k

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "tests" / "datacheck_keys"


@functools.cache
def spec() -> dict[str, Any]:
    return json.loads((HERE / "key_spec.json").read_text(encoding="utf-8"))


def kc_gen(type_: str) -> str:
    """Park-Miller random numbers in the form of a provider's key (the same as in R)."""
    sp = spec()
    i = list(sp["types"]).index(type_)
    x = 7919 + 104729 * i
    out = ""
    for part in sp["types"][type_]:
        if part[0] == "lit":
            out += part[1]
        else:
            chars = sp["alphabets"][part[2]]
            for _ in range(part[1]):
                x = (x * 48271) % 2147483647
                out += chars[(x // 1024) % len(chars)]
    return out


def kc_fill(text: str) -> str:
    return re.sub(r"@@KEY:([a-z0-9-]+)@@", lambda m: kc_gen(m[1]), text)


def kc_dir(*parts: str) -> Path:
    d = ROOT / "parity" / "_out" / "dc_keys" / Path(*parts)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    return d


def kc_write(d: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(kc_fill(content).encode("utf-8"))
    return d


def kc_hits(found: list[k.KeyHit], key: str) -> pd.DataFrame:
    """Where, by which rule, and whether it is the key made (never the key)."""
    return pd.DataFrame(
        {
            "n": [len(found)],
            "start": pd.Series([found[0].start if found else None], dtype="Int64"),
            "end": pd.Series([found[0].end if found else None], dtype="Int64"),
            "rules": [",".join(sorted({h.rule for h in found}))],
            "same_key": [any(h.key == key for h in found)],
        }
    )


def kc_rules() -> dict[str, Any]:
    x = k._key_rules()
    p = x.patterns
    return {
        "n": len(p),
        "name": [r.name for r in p],
        "confidence": [r.confidence for r in p],
        "group": [r.group for r in p],
        "path": [r.path for r in p],
        "entropy": [r.entropy for r in p],
        "entropy_strict": [r.entropy_strict if r.entropy is not None else None for r in p],
        "n_keywords": [len(r.keywords) for r in p],
        "n_drop_match": [len(r.drop_match) for r in p],
        "n_need_match": [len(r.need_match) for r in p],
        "n_drop_contains": [len(r.drop_contains) for r in p],
        "ignore_patterns": list(x.ignore_patterns),
        "ignore_texts": list(x.ignore_texts),
        "n_keywords_all": len(x.keywords),
        "licence": x.source["licence"],
    }


def kc_find_types() -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for type_ in spec()["types"]:
        key = kc_gen(type_)
        for ctx in ("sentence", "r", "json_bearer", "json"):
            s = {
                "sentence": f"my key is {key} for the API.",
                "r": f'api_key <- "{key}"',
                "json_bearer": f'"Authorization": "Bearer {key}"',
                "json": f'{{"key":"{key}","n":1}}',
            }[ctx]
            rows.append(kc_hits(k._key_find(s), key).assign(type=type_, context=ctx))
    out = pd.concat(rows, ignore_index=True)
    return out[["type", "context", *[c for c in out.columns if c not in ("type", "context")]]]


def kc_find_negatives() -> pd.DataFrame:
    s = [
        "sk-or-v1-" + "x" * 64,
        "ghp_" + "a" * 36,
        "api_key = 'YOUR_API_KEY'",
        "token: ${API_KEY}",
        "key = {{ secrets.KEY }}",
        "AKIAIOSFODNN7EXAMPLE",
        "The participants were asked to rate the stimuli.",
        "KeyError: 'response_time' in file data_cleaning.py",
        "",
        None,
    ]
    return pd.DataFrame({"id": range(1, len(s) + 1), "n": [len(k._key_find(z)) for z in s]}).astype(
        "int64"
    )


def kc_find_long() -> pd.DataFrame:
    key = kc_gen("openrouter-api-key")
    s = "participant,condition,1.234\n" * 2000 + " key = " + key + " " + "x,y\n" * 3000
    return kc_hits(k._key_find(s), key)


def kc_find_encoded() -> dict[str, Any]:
    import itertools

    key = kc_gen("github-pat")
    pool = [*"ABCDEFGHIJKLMNOPQRSTUVWXYZ", *"abcdefghijklmnopqrstuvwxyz", *"0123456789", "+", "/"]
    b64 = "".join(itertools.islice(itertools.cycle(pool), 3000))
    alnum = "".join(itertools.islice(itertools.cycle(pool[:62]), 30))
    b64 = b64[:1000] + "/BSA" + alnum + "/" + b64[1000:]
    page = f'<p>Results</p>\n<img src="data:image/png;base64,{b64}">\n<p>token {key}</p>'
    out, n = k._key_remove({"report": page})
    return {
        "found": kc_hits(k._key_find(page), key),
        "image_kept": b64 in out["report"],
        "key_removed": key not in out["report"],
        "n_removed": n,
        "report_length": len(out["report"]),
    }


def kc_find_path_rule() -> dict[str, Any]:
    line = 'password = "x7Kq2-Lm9Pz"'
    d = kc_dir("path_rule")
    (d / "main.tf").write_text(line + "\n")
    (d / "Test.java").write_text(line + "\n")
    names = ["main.tf", "Test.java"]
    f, _ = k._key_scan_files([str(d / n) for n in names], names=names)
    return {
        "files": f["file"].tolist(),
        "rules": f["rule"].tolist(),
        "no_path": [h.rule for h in k._key_find(line)],
    }


def kc_remove() -> dict[str, Any]:
    ks = [
        kc_gen(t)
        for t in (
            "openrouter-api-key",
            "github-pat",
            "aws-access-token",
            "gcp-api-key",
            "groq-api-key",
        )
    ]
    table = pd.DataFrame({"a": ["x", ks[1]], "b": pd.Series([1, 2], dtype="int64")})
    table = table.rename(columns={"a": ks[4]})
    res = {
        "report": ["text", "found " + ks[0]],
        "table": table,
        "nested": [{"z": "key=" + ks[2]}],
        "note": "nothing here",
    }
    out, n = k._key_remove(res)
    plain = {"report": "nothing here", "table": pd.DataFrame({"a": [1, 2, 3]})}
    p, _ = k._key_remove(plain)
    flat = " ".join(
        [*out["report"], *out["table"].columns, *out["table"].iloc[:, 0], out["nested"][0]["z"]]
    )
    return {
        "out": out,
        "n": n,
        "plain_same": p is plain,
        "no_whole_key": not any(z in flat for z in ks),
        "replaced": k._key_replace(
            "aaa SECRETKEY bbb", [k.KeyHit(4, 13, "r", "k"), k.KeyHit(7, 16, "r", "k")]
        ),
        "shown": [k._key_shown(z) for z in ("abcdefghij", "x" * 36, "y" * 200)],
    }


def kc_scan_files() -> dict[str, Any]:
    ks = [kc_gen(t) for t in ("github-pat", "openrouter-api-key", "aws-access-token")]
    d = kc_dir("scan_files")
    (d / "a.R").write_bytes(("x <- 1\r\n# caf\xe9\r\ntoken <- '" + ks[0] + "'").encode("latin-1"))
    (d / "blob.bin").write_bytes(bytes([0, 1, 2, 0, 255]))
    (d / "two.txt").write_text(f"a\nb\nk1 = {ks[1]} ; k2 = {ks[2]}\n")
    nm = ["a.R", "blob.bin", "two.txt", "missing.txt"]
    f, skipped = k._key_scan_files([str(d / n) for n in nm], names=nm)
    flat = " ".join(f.astype(str).to_numpy().ravel())
    return {
        "found": f,
        "n_skipped": len(skipped),
        "no_whole_key": not any(z in flat for z in ks),
    }


def kc_scan_tables() -> dict[str, Any]:
    key = kc_gen("openrouter-api-key")
    responses = pd.DataFrame(
        {
            "StartDate": [f"2026-01-0{i}" for i in range(1, 10)],
            "Q1": "How\nworried?",
            "OpenRouterAPIKey": "Bearer " + key,
        }
    )
    late = pd.DataFrame({"id": range(1, 501), "answer": ["ok"] * 498 + ["my key " + key, "ok"]})
    hdr = pd.DataFrame({key: [1]})
    f = k._key_scan_tables({"responses.csv": responses, "late.csv": late}, seen=k.KeySeen())
    h = k._key_scan_tables({"h.csv": hdr})
    return {
        "found": f,
        "header_row": h["row"].tolist(),
        "no_whole_key": key not in " ".join(f.astype(str).to_numpy().ravel()),
    }


def kc_scan_repo(name: str) -> dict[str, Any]:
    d = kc_write(kc_dir("scan_repo", name), spec()["repos"][name])
    rel = sorted(
        p.relative_to(d).as_posix()
        for p in d.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(d).parts)
    )
    files = pd.DataFrame(
        {
            "paper_id": "p1",
            "repo_url": str(d),
            "file_name": [Path(r).name for r in rel],
            "file_path": rel,
            "file_location": [str(d / r) for r in rel],
            "data_type": ["data" if r.endswith(".csv") else "code" for r in rel],
        }
    )
    f, skipped = k._key_scan_repo_files(files, cache=False)
    f = f.assign(repo_url=f["repo_url"].astype("string").str.replace(str(d), "<repo>", regex=False))
    return {"found": f, "n_skipped": len(skipped)}


def kc_run_repo(name: str) -> Any:
    """``module_run(<test paper p1>, "data_check", local_path = <the repository>)``."""
    import metacheck as pc
    from metacheck.module import module_run
    from metacheck.utils import local_options
    from tests.mod_data_check.helpers import norm_paths

    d = kc_write(kc_dir("run", name), spec()["repos"][name])
    p = pc.test_paper(["Some text."])
    p.paper_id = "p1"
    rel = d.relative_to(ROOT).as_posix()
    with (
        contextlib.chdir(ROOT),
        local_options({"metacheck.careless": False, "metacheck.llm.use": False}),
    ):
        out = module_run(p, "data_check", local_path=rel, local_only=True)
    return norm_paths(out)
