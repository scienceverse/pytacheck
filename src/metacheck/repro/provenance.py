"""Provenance of a reproducibility check, kept for a TRACE Research Object (TRO).

Port of ``R/provenance.R`` (metacheck 0.4.4.20, #404). What a TRO declaration
needs is kept in the output of ``reproducibility_check()``, so that one can be
written later from a stored result, without running the code again:

- per script run (``run_results``): when it started and ended (``started_at``,
  ``ended_at``, UTC) and the exit status of the process (``exit_status``);
- per file (``artifacts``): its SHA-256 hash, size and type at three moments
  (``deposit``: as downloaded from the repository; ``before_run``: the run folder
  just before the first script ran, after metacheck's own changes; ``after_run``:
  the files the scripts made, changed or removed);
- per change metacheck made to a script (``modifications``): the script's hash
  before and after the change;
- the system that ran the code (``environment``): the version, the sandbox, the
  Docker image (its ID and digest), the Python version, the ``docker run``
  arguments (network, read-only file system, capabilities), the time limits and
  whether the internet was on.

A TRO maps these onto trov:ResearchArtifact (hash, type), trov:hasArrangement (the
files at each moment), trov:TransparentResearchPerformance (the runs, with their
times and the arrangements they read and wrote) and trov:TransparentResearchSystem
(the environment, whose docker arguments back capabilities such as network
isolation). See https://transparency-certified.github.io/trace-specification/
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import sysconfig
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import r_sorted

__all__ = [
    "_repro_add_run_clock",
    "_repro_artifacts",
    "_repro_artifacts_after",
    "_repro_deposit_artifacts",
    "_repro_environment",
    "_repro_exit_status",
    "_repro_iso_utc",
    "_repro_modification_hashes",
    "_repro_prov_finish",
    "_repro_prov_note_before_run",
    "_repro_prov_note_docker",
    "_repro_prov_note_materialised",
    "_repro_prov_reset",
    "_repro_prov_scripts",
    "_repro_prov_state",
    "_sha256_file",
]


def _sha256_file(path: Sequence[str | os.PathLike[str] | None] | str | None) -> list[str | None]:
    """SHA-256 hashes of files (``.sha256_file()``).

    Streams each file, so a large file is never read into memory whole.

    Returns:
        A list as long as *path*: 64 lowercase hex characters, or ``None`` for a
        path that is missing, a folder, or cannot be read.
    """
    paths = [path] if isinstance(path, str | os.PathLike) or path is None else list(path)
    out: list[str | None] = []
    for p in paths:
        if p is None or (isinstance(p, float) and p != p):
            out.append(None)
            continue
        try:
            if not os.path.isfile(p):
                out.append(None)
                continue
            with open(p, "rb") as fh:
                out.append(hashlib.file_digest(fh, "sha256").hexdigest())
        except OSError:
            out.append(None)
    return out


def _repro_iso_utc(t: Any) -> str | None:
    """A time as ISO 8601 in UTC with milliseconds (``2026-11-18T21:05:03.123Z``).

    *t* is a ``datetime`` (naive: UTC) or seconds since the epoch; ``None`` for a
    missing time.
    """
    if t is None or t is pd.NaT or (isinstance(t, float) and t != t):
        return None
    if isinstance(t, int | float):
        dt = _dt.datetime.fromtimestamp(t, tz=_dt.UTC)
    elif isinstance(t, pd.Timestamp):
        dt = t.to_pydatetime()
    else:
        dt = t
    dt = dt.replace(tzinfo=_dt.UTC) if dt.tzinfo is None else dt.astimezone(_dt.UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


def _repro_add_run_clock(
    df: pd.DataFrame | None, clocks: Mapping[str, Mapping[str, Any]] | None = None
) -> pd.DataFrame | None:
    """Add ``started_at``, ``ended_at`` and ``exit_status`` to rows of ``run_results``.

    ``.repro_add_run_clock()``. *clocks* maps a script's ``file_name`` to what its
    runner noted while the script ran (``started``, ``ended``, ``status``); without
    it, or for a script that did not run (skipped, not parsed), the columns are
    ``NA``, so rows of every kind and of every pass bind together. A column that
    is already there is kept.
    """
    if df is None:
        return df
    out = df.copy()
    names = out["file_name"].tolist() if "file_name" in out.columns else [None] * len(out)

    def clock(i: int, key: str) -> Any:
        c = (clocks or {}).get(str(names[i])) if names[i] is not None else None
        return None if c is None else c.get(key)

    if "started_at" not in out.columns:
        out["started_at"] = pd.array(
            [_repro_iso_utc(clock(i, "started")) for i in range(len(out))], dtype="string"
        )
    if "ended_at" not in out.columns:
        out["ended_at"] = pd.array(
            [_repro_iso_utc(clock(i, "ended")) for i in range(len(out))], dtype="string"
        )
    if "exit_status" not in out.columns:
        out["exit_status"] = pd.array([clock(i, "status") for i in range(len(out))], dtype="Int64")
    return out


def _repro_exit_status(res: Any) -> int | None:
    """The exit status of a finished process run.

    ``.repro_exit_status()``: a ``subprocess.CompletedProcess`` or any object with a
    ``returncode``/``status``; ``None`` when the process did not report one (docker
    itself failing) or was stopped at its time limit, where the status is that of
    the stopped ``docker`` command, not the script's.
    """
    if res is None:
        return None
    if isinstance(res, Mapping):
        if res.get("timeout"):
            return None
        s = res.get("status", res.get("returncode"))
    else:
        if getattr(res, "timeout", False) is True:
            return None
        s = getattr(res, "status", getattr(res, "returncode", None))
    if isinstance(s, bool) or not isinstance(s, int | float) or s != s:
        return None
    return int(s)


# Files metacheck's runs leave in a run folder that are neither the authors' files nor
# made by their code (``.repro_prov_files()``).
_OWN_LIBS = re.compile(r"^(_r?lib|_pylib2?)/")
_OWN_RUNNER = re.compile(r"(^|/)\.runner_[^/]*$")
_OWN_STAT_OUTPUT = re.compile(
    r"^statistical_output/(results_long\.csv|[^/]+\.statistical_output\.json)$"
)


def _repro_prov_files(root: str | os.PathLike[str] | None) -> list[str]:
    """The files in a run folder, relative to it, with forward slashes; hidden files included.

    Left out, as they are neither the authors' files nor made by their code: the
    package libraries metacheck installs there, metacheck's own short-lived files (a
    script's runner, its captured results, what a container received over the
    network) and the statistical output metacheck reads from the runs and writes
    there. The packages are installed in another folder, so an install.R of the
    authors' is kept.
    """
    if root is None or not os.path.isdir(root):
        return []
    root_s = os.fspath(root)
    files: list[str] = []
    for dirpath, _dirs, names in os.walk(root_s):
        for n in names:
            full = os.path.join(dirpath, n)
            if os.path.isdir(full):  # a link to a folder
                continue
            files.append(os.path.relpath(full, root_s).replace("\\", "/"))
    # ``list.files()`` sorts in the locale's collation, not by bytes
    files = list(r_sorted(files))
    return [
        f
        for f in files
        if not _OWN_LIBS.search(f)
        and not _OWN_RUNNER.search(f)
        and f not in (".capture.rds", ".repro_received")
        and not _OWN_STAT_OUTPUT.search(f)
    ]


def _repro_prov_utf8(x: Sequence[str | None] | str | None) -> list[str | None]:
    """A path as valid UTF-8 text.

    A name in another encoding (an old zip's CP437 name, a Latin-1 name) keeps its
    bytes as ``<xx>``, so the result can be written as JSON and the original bytes
    can still be read from it (``iconv(x, "UTF-8", "UTF-8", sub = "byte")``).
    """
    vals = [x] if isinstance(x, str) or x is None else list(x)
    out: list[str | None] = []
    for v in vals:
        if v is None:
            out.append(None)
            continue
        raw = v.encode("utf-8", "surrogateescape")
        try:
            out.append(raw.decode("utf-8"))
            continue
        except UnicodeDecodeError:
            pass
        pieces: list[str] = []
        pos = 0
        while pos < len(raw):
            try:
                pieces.append(raw[pos:].decode("utf-8"))
                break
            except UnicodeDecodeError as err:
                pieces.append(raw[pos : pos + err.start].decode("utf-8"))
                pieces.extend(f"<{b:02x}>" for b in raw[pos + err.start : pos + err.end])
                pos += err.end
        out.append("".join(pieces))
    return out


def _repro_file_mime(paths: Sequence[str | None]) -> list[str | None]:
    """The MIME type of a file, by its extension, from metacheck's registry of file types.

    ``None`` when it has none (``.repro_file_mime()``).
    """
    from metacheck.datacheck._files_registry import EXT_REGISTRY

    mime: dict[str, str | None] = {}
    for ext, *_rest, kind in EXT_REGISTRY:
        mime.setdefault(ext, kind)
    out: list[str | None] = []
    for p in paths:
        m = re.match(r"^.*\.([A-Za-z0-9]+)$", p or "")
        out.append(mime.get(m.group(1).lower()) if m else None)
    return out


_ARTIFACT_KINDS = {
    "stage": "string",
    "path": "string",
    "size": "float64",
    "sha256": "string",
    "mime_type": "string",
    "source": "string",
    "status": "string",
}


def _repro_artifact_cols() -> pd.DataFrame:
    """An empty ``artifacts`` table."""
    return pd.DataFrame({c: pd.Series([], dtype=t) for c, t in _ARTIFACT_KINDS.items()})  # type: ignore[call-overload]


def _artifact_frame(cols: Mapping[str, Sequence[Any]]) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(list(cols[c]), dtype=t) for c, t in _ARTIFACT_KINDS.items()})  # type: ignore[call-overload]


def _file_size(p: str) -> float | None:
    try:
        return float(os.path.getsize(p))
    except OSError:
        return None


def _repro_artifacts(root: str | os.PathLike[str] | None, stage: str) -> pd.DataFrame:
    """One row per file in a run folder at one moment.

    ``stage``, ``path`` (relative to the folder), ``size`` (bytes), ``sha256``,
    ``mime_type``, ``source`` (``NA`` here) and ``status`` (``NA`` here; see
    :func:`_repro_artifacts_after`). A file that vanishes while it is listed gets
    ``NA`` for size and hash.
    """
    f = _repro_prov_files(root)
    if not f:
        return _repro_artifact_cols()
    full = [os.path.join(os.fspath(root), x) for x in f]  # type: ignore[arg-type]
    n = len(f)
    return _artifact_frame(
        {
            "stage": [stage] * n,
            "path": _repro_prov_utf8(f),
            "size": [_file_size(p) for p in full],
            "sha256": _sha256_file(full),
            "mime_type": _repro_file_mime(f),
            "source": [None] * n,
            "status": [None] * n,
        }
    )


def _repro_deposit_artifacts(
    materialised: pd.DataFrame | None, structure_df: pd.DataFrame | None = None
) -> pd.DataFrame:
    """The files as downloaded from the repository (stage ``deposit``).

    One row per file metacheck copied into the run folder (the ``materialised`` table of
    ``repro_materialize_layout()``: ``target_path``, ``source``, ``ok``), hashed from
    the downloaded file itself, so a later change by metacheck or by the code shows as
    a different hash in the next stage. ``source``: the file's address in the repository
    (``file_url``, else ``repo_url``, from *structure_df*), else ``NA``.
    """
    if (
        materialised is None
        or not isinstance(materialised, pd.DataFrame)
        or len(materialised) == 0
        or not {"target_path", "source"} <= set(materialised.columns)
    ):
        return _repro_artifact_cols()
    m = materialised
    keep = [s is not None and s is not pd.NA and str(s) != "" for s in m["source"].tolist()]
    m = m.loc[keep]
    if "ok" in m.columns:
        m = m.loc[[v is True or (v is not pd.NA and v is not None and bool(v)) for v in m["ok"]]]
    if len(m) == 0:
        return _repro_artifact_cols()
    n = len(m)
    address: list[str | None] = [None] * n
    if isinstance(structure_df, pd.DataFrame) and "file_location" in structure_df.columns:

        def col(name: str) -> list[str | None]:
            if name not in structure_df.columns:
                return [None] * len(structure_df)
            return [None if pd.isna(v) else str(v) for v in structure_df[name].tolist()]

        url, repo = col("file_url"), col("repo_url")
        url = [u if u is not None and u != "" else r for u, r in zip(url, repo, strict=True)]
        first: dict[str, int] = {}
        for i, loc in enumerate(structure_df["file_location"].tolist()):
            if not pd.isna(loc):
                first.setdefault(str(loc), i)
        address = [url[first[str(s)]] if str(s) in first else None for s in m["source"].tolist()]
    path = [re.sub(r"^(\./|/)+", "", str(p).replace("\\", "/")) for p in m["target_path"].tolist()]
    src = [str(s) for s in m["source"].tolist()]
    return _artifact_frame(
        {
            "stage": ["deposit"] * n,
            "path": _repro_prov_utf8(path),
            "size": [_file_size(p) for p in src],
            "sha256": _sha256_file(src),
            "mime_type": _repro_file_mime(path),
            "source": address,
            "status": [None] * n,
        }
    )


def _repro_artifacts_after(
    before: pd.DataFrame | None, root: str | os.PathLike[str] | None
) -> pd.DataFrame:
    """The run folder after the scripts ran (stage ``after_run``).

    Compared with the rows of the ``before_run`` stage: only the files that are new,
    changed (another hash) or removed are listed, with that status, since an unchanged
    file is the one already listed before the run. A removed file keeps its before-run
    hash and size.
    """
    now = _repro_artifacts(root, "after_run")
    if before is None or len(before) == 0:
        if len(now):
            now["status"] = pd.array(["new"] * len(now), dtype="string")
        return now
    old = dict(zip(before["path"].tolist(), before["sha256"].tolist(), strict=True))
    status: list[str] = []
    for p, h in zip(now["path"].tolist(), now["sha256"].tolist(), strict=True):
        if p not in old:
            status.append("new")
        elif pd.isna(old[p]) or pd.isna(h) or old[p] != h:
            status.append("changed")
        else:
            status.append("unchanged")
    now["status"] = pd.array(status, dtype="string")
    here = set(now["path"].tolist())
    gone = before.loc[[p not in here for p in before["path"].tolist()]].copy()
    if len(gone):
        gone["stage"] = pd.array(["after_run"] * len(gone), dtype="string")
        gone["status"] = pd.array(["removed"] * len(gone), dtype="string")
    changed = now.loc[now["status"] != "unchanged"]
    out = pd.concat([changed, gone], ignore_index=True)
    return out.astype(_ARTIFACT_KINDS)  # type: ignore[arg-type]


# -- The system that ran the code (environment) -------------------------------------
# The docker run arguments and images actually used are noted by the script runners and
# installers while they run (:func:`_repro_prov_note_docker`), once per kind of run, and
# read back by :func:`_repro_environment`. Reset at the start of each check
# (:func:`_repro_prov_reset`); a batch of papers runs each paper in its own process.
_PROV: dict[str, dict[str, dict[str, Any]]] = {"docker": {}}


def _repro_prov_reset() -> None:
    """Forget the Docker runs noted by an earlier check."""
    _PROV.clear()
    _PROV["docker"] = {}


def _repro_prov_note_docker(kind: str, args: Sequence[str], image: str | None) -> None:
    """Note a ``docker run``: ``kind`` is ``r_run``, ``r_install``, ``python_run``, ``python_install``.

    Noted once per kind and image (Python 2 scripts run in another image than
    Python 3 ones). The container's random name is left out; the rest is kept as given.
    """
    docker = _PROV.setdefault("docker", {})
    img = str(image) if image is not None else ""
    key = f"{kind}|{img}"
    if key in docker:
        return
    a = [str(x) for x in args]
    if "--name" in a:
        i = a.index("--name")
        del a[i : i + 2]
    docker[key] = {"kind": kind, "image": img, "args": a}


def _repro_docker_image_info(image: str | None) -> dict[str, Any]:
    """What ``docker image inspect`` says about an image.

    Its ID, digests (as pulled from a registry), creation time, architecture, and the R
    and Python versions its environment declares (``R_VERSION`` in the rocker images and
    those built on them, ``PYTHON_VERSION`` in the official Python images), read without
    starting a container. ``None`` fields when Docker cannot tell.
    """
    info: dict[str, Any] = {
        "image": image,
        "id": None,
        "repo_digests": [],
        "created": None,
        "architecture": None,
        "r_version": None,
        "python_version": None,
    }
    if not image:
        return info
    try:
        res = subprocess.run(  # noqa: S603 - the docker command
            ["docker", "image", "inspect", image],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return info
    if res.returncode != 0:
        return info
    try:
        j = json.loads(res.stdout)[0]
    except (ValueError, IndexError, TypeError):
        return info
    env = [e for e in (j.get("Config") or {}).get("Env") or [] if isinstance(e, str)]

    def env_of(name: str) -> str | None:
        hit = [e for e in env if e.startswith(name + "=")]
        return hit[0][len(name) + 1 :] if hit else None

    info["id"] = j.get("Id")
    info["repo_digests"] = [str(d) for d in j.get("RepoDigests") or []]
    info["created"] = j.get("Created")
    info["architecture"] = j.get("Architecture")
    info["r_version"] = env_of("R_VERSION")
    info["python_version"] = env_of("PYTHON_VERSION")
    return info


def _repro_python_version(binary: str | None) -> str | None:
    """The version of a Python executable on this computer (``Python 3.12.4``); ``None`` when it cannot be run."""
    if not binary:
        return None
    try:
        res = subprocess.run(  # noqa: S603 - the Python used for the run
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    v = f"{res.stdout} {res.stderr}".strip()
    return v or None


def _pytacheck_version() -> str | None:
    try:
        from metacheck._version import __version__

        return str(__version__)
    except Exception:
        return None


def _repro_environment(
    sandbox: str,
    started: Any,
    ended: Any,
    timeout: Any = None,
    total_timeout: Any = None,
    internet: bool = False,
    python_bin: str | None = None,
) -> dict[str, Any]:
    """The ``environment`` element of ``reproducibility_check()``'s output.

    *sandbox*: ``"docker"`` or ``"process"``; *internet*: whether the code could use the
    internet; *timeout*: seconds per script; *total_timeout*: seconds for all scripts, or a
    clock time; *python_bin*: the Python run outside Docker, when Python ran there.
    """
    docker = _PROV.get("docker", {})
    images = list(dict.fromkeys(d["image"] for d in docker.values() if d["image"]))
    inspected = [_repro_docker_image_info(i) for i in images]
    runs = [d for d in docker.values() if str(d.get("kind", "")).endswith("_run")]
    isolated = (
        sandbox == "docker"
        and not internet
        and len(runs) > 0
        and all(
            "--network" in d["args"]
            and "none"
            in d["args"][d["args"].index("--network") + 1 : d["args"].index("--network") + 2]
            for d in runs
        )
    )
    tl = _repro_iso_utc(total_timeout) if isinstance(total_timeout, _dt.datetime) else total_timeout
    return {
        "metacheck_version": _pytacheck_version(),
        "started_at": _repro_iso_utc(started),
        "ended_at": _repro_iso_utc(ended),
        "host": {
            "python_version": sys.version.split()[0],
            "platform": sysconfig.get_platform(),
            "os": f"{platform.system()} {platform.release()}",
        },
        "sandbox": sandbox,
        # whether the code was run with the network switched off by the sandbox itself
        # (docker's --network none); the process sandbox does not stop it
        "network_isolated": bool(isolated),
        "internet": bool(internet),
        "timeout_per_script": timeout,
        "total_timeout": tl,
        "python_local": (
            {"executable": python_bin, "version": _repro_python_version(python_bin)}
            if python_bin
            else None
        ),
        "docker": (
            {"runs": [dict(d) for d in docker.values()], "images": inspected} if docker else None
        ),
    }


def _repro_modification_hashes(
    modifications: pd.DataFrame | None, scripts: pd.DataFrame | None, artifacts: pd.DataFrame | None
) -> pd.DataFrame | None:
    """Add ``sha256_before`` and ``sha256_after`` to the modifications table.

    The hash of the script as downloaded (stage ``deposit``) and as it ran (stage
    ``before_run``), matched by the script's path in the run folder (*scripts*:
    ``file_name`` and ``path``). ``NA`` where a stage has no file at that path, e.g. a
    script made from an R Markdown file.
    """
    if modifications is None or not isinstance(modifications, pd.DataFrame):
        return modifications
    out = modifications.copy()
    n = len(out)
    before: list[str | None] = [None] * n
    after: list[str | None] = [None] * n
    if n and scripts is not None and len(scripts) and artifacts is not None and len(artifacts):
        script_path: dict[Any, Any] = {}
        for fn, p in zip(scripts["file_name"].tolist(), scripts["path"].tolist(), strict=True):
            script_path.setdefault(fn, p)

        def lookup(stage: str) -> dict[Any, Any]:
            sub = artifacts.loc[artifacts["stage"] == stage]
            d: dict[Any, Any] = {}
            for p, h in zip(sub["path"].tolist(), sub["sha256"].tolist(), strict=True):
                d.setdefault(p, h)
            return d

        dep, bef = lookup("deposit"), lookup("before_run")
        paths = [script_path.get(f) for f in out["file_name"].tolist()]
        before = [None if pd.isna(p) else dep.get(p) for p in paths]
        after = [None if pd.isna(p) else bef.get(p) for p in paths]
    out["sha256_before"] = pd.array([None if pd.isna(v) else v for v in before], dtype="string")
    out["sha256_after"] = pd.array([None if pd.isna(v) else v for v in after], dtype="string")
    return out


def _repro_prov_scripts(
    run_tbl: pd.DataFrame | None, root: str | os.PathLike[str] | None
) -> pd.DataFrame:
    """The scripts of a run table as ``file_name`` and ``path`` relative to the run folder."""
    empty = pd.DataFrame(
        {"file_name": pd.Series([], dtype="string"), "path": pd.Series([], dtype="string")}
    )
    if run_tbl is None or len(run_tbl) == 0 or "script_path" not in run_tbl.columns or root is None:
        return empty
    r = os.path.abspath(os.fspath(root)).replace("\\", "/")
    rel: list[str | None] = []
    for p in run_tbl["script_path"].tolist():
        if p is None or p is pd.NA:
            rel.append(None)
            continue
        q = os.path.abspath(os.fspath(p)).replace("\\", "/")
        rel.append(q[len(r) + 1 :] if q.startswith(r + "/") else None)
    return pd.DataFrame(
        {
            "file_name": pd.Series(run_tbl["file_name"].tolist(), dtype="string"),
            "path": pd.Series(_repro_prov_utf8(rel), dtype="string"),
        }
    )


# ---------------------------------------------------------------------------
# The state one check keeps while it runs (the locals ``prov_*`` of R's
# ``reproducibility_check()``)
# ---------------------------------------------------------------------------


def _repro_prov_state() -> dict[str, Any]:
    """A fresh provenance state for one check, and the Docker runs of an earlier one forgotten.

    ``started``: when the check started; ``materialised``: the ``materialised`` tables of the
    ``repro_materialize_layout()`` calls; ``before``: the run folder before the first script
    ran (``None`` until then); ``scripts``: where each script was written; ``python_bin``:
    the Python that ran scripts outside Docker (``None`` when none did).
    """
    _repro_prov_reset()
    return {
        "started": _dt.datetime.now(_dt.UTC),
        "materialised": [],
        "before": None,
        "scripts": [],
        "python_bin": None,
    }


def _repro_prov_note_materialised(state: Mapping[str, Any] | None, ml: Any) -> None:
    """Keep the ``materialised`` table that ``repro_materialize_layout()`` returned."""
    if state is None:
        return
    m = getattr(ml, "attrs", {}).get("materialised")
    if isinstance(m, pd.DataFrame) and len(m):
        state["materialised"].append(m)


def _repro_prov_note_before_run(
    state: MutableMapping[str, Any] | None,
    run_tbl: pd.DataFrame | None,
    root: str | os.PathLike[str],
) -> None:
    """Hash the run folder before the first script runs, and note where the scripts are.

    The folder is hashed once, before the first script of either language; the scripts of
    every run table are noted. Done before the scripts' time starts, so hashing does not
    take from it.
    """
    if state is None:
        return
    if state["before"] is None:
        state["before"] = _repro_artifacts(root, "before_run")
    state["scripts"].append(_repro_prov_scripts(run_tbl, root))


def _repro_prov_finish(
    state: Mapping[str, Any] | None,
    modifications: pd.DataFrame | None,
    structure_df: pd.DataFrame | None,
    sandbox_root: str | os.PathLike[str] | None,
    *,
    sandbox: str,
    timeout: Any,
    total_timeout: Any,
    internet: bool,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None, dict[str, Any] | None]:
    """``(artifacts, modifications, environment)`` for the output of a check that ran code.

    ``(None, modifications, None)`` when no script was started (nothing was hashed).
    """
    if state is None or state["before"] is None:
        return None, modifications, None
    mats = state["materialised"]
    deposit = None
    if mats:
        m = pd.concat(mats, ignore_index=True)
        deposit = _repro_deposit_artifacts(
            m.loc[~m["target_path"].duplicated()].reset_index(drop=True), structure_df
        )
    parts = [
        deposit if deposit is not None else _repro_artifact_cols(),
        state["before"],
        _repro_artifacts_after(state["before"], sandbox_root)
        if sandbox_root is not None and os.path.isdir(sandbox_root)
        else _repro_artifact_cols(),
    ]
    artifacts = pd.concat(parts, ignore_index=True)
    scripts = [s for s in state["scripts"] if len(s)]
    scripts_df = (
        pd.concat(scripts, ignore_index=True) if scripts else _repro_prov_scripts(None, None)
    )
    modifications = _repro_modification_hashes(modifications, scripts_df, artifacts)
    environment = _repro_environment(
        sandbox,
        started=state["started"],
        ended=_dt.datetime.now(_dt.UTC),
        timeout=timeout,
        total_timeout=total_timeout,
        internet=internet,
        # the Python run outside Docker, when Python scripts ran there
        python_bin=state.get("python_bin") if sandbox == "process" else None,
    )
    return artifacts, modifications, environment
