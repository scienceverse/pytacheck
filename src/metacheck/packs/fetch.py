"""Fetching pack sources: commit tarballs over HTTPS, hardened git, local folders.

Sources (``index.json`` and pins use the same shapes):

* ``{"github": "owner/repo", "subdir": ...}``, ``{"gitlab": "group/repo"}`` and
  ``{"codeberg": "owner/repo"}``: the commit's tarball is downloaded over HTTPS
  (no git binary needed), falling back to git. With a GitHub token in the
  environment (:mod:`metacheck.packs.auth`), GitHub tarballs come from the API
  first, so private repositories work; git uses the user's own credentials;
* ``{"git": "<https, ssh or local url>"}``: hardened git;
* ``{"path": "<folder>"}``: a local folder (local and test stores).

Extraction is defensive: absolute paths, ``..``, symlinks, hardlinks,
device files and compiled extension modules (``*.so``, ``*.pyd``) are
refused, the extracted pack is capped at 50 MB and 5000 files, the single
top-level folder is stripped and ``subdir`` selected. Whatever the tree hash
leaves out (``.git/``, ``__pycache__/``, ``*.pyc``, an install record) is
never written, so the files on disk are exactly the hashed, recorded and
reviewed ones. Nothing fetched here is imported or run.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

from metacheck.packs.auth import AUTH_HELP, DownloadError, github_token, redact
from metacheck.packs.manifest import PackError
from metacheck.packs.tree import INSTALL_RECORD

__all__ = [
    "HOSTS",
    "MAX_BYTES",
    "MAX_DOWNLOAD",
    "MAX_FILES",
    "DownloadError",
    "GitMissing",
    "api_tarball_url",
    "clone_url",
    "copy_tree",
    "describe_source",
    "extract_tarball",
    "fetch_source",
    "git_fetch",
    "git_read_file",
    "git_rev",
    "resolve_rev",
    "tarball_url",
]

MAX_BYTES = 50 * 1024 * 1024  # extracted pack size
MAX_FILES = 5000  # extracted pack files
MAX_DOWNLOAD = 100 * 1024 * 1024  # compressed tarball (a whole store repo)
HOSTS = {"github": "github.com", "gitlab": "gitlab.com", "codeberg": "codeberg.org"}
_SHA = re.compile(r"^[0-9a-f]{40}$")
_SKIP_DIRS = frozenset({".git", "__pycache__"})
#: compiled Python extension modules: importable, but not reviewable
_NATIVE_SUFFIXES = (".so", ".pyd")


def _skipped(parts: tuple[str, ...] | list[str]) -> bool:
    """Whether a pack file is left out of the tree hash (and so is never installed)."""
    name = parts[-1]
    return any(p in _SKIP_DIRS for p in parts) or name.endswith(".pyc") or name == INSTALL_RECORD


def _refuse_native(rel: str) -> None:
    if rel.lower().endswith(_NATIVE_SUFFIXES):
        raise PackError(
            f"Refusing to install: {rel} is a compiled extension module "
            "(packs may only contain Python source)"
        )


def _slug(source: Mapping[str, Any], host: str) -> str:
    slug = str(source.get(host) or "").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)+", slug) or ".." in slug.split("/"):
        raise PackError(f"Invalid {host} source {redact(slug)!r}: expected 'owner/repo'")
    return slug


def _host(source: Mapping[str, Any]) -> str | None:
    return next((h for h in HOSTS if h in source), None)


def _subdir(source: Mapping[str, Any]) -> str:
    sub = str(source.get("subdir") or "").strip().strip("/")
    if not sub:
        return ""
    parts = PurePosixPath(sub).parts
    if "\\" in sub or any(p in ("..", ".") for p in parts) or PurePosixPath(sub).is_absolute():
        raise PackError(f"Invalid subdir {sub!r} in the pack source")
    return "/".join(parts)


def describe_source(source: Mapping[str, Any]) -> str:
    """A one-line description, e.g. ``github janedoe/psych (packs/psych)``.

    Credentials in a URL (from an older pin) are shown redacted.
    """
    host = _host(source)
    if host is not None:
        text = f"{host} {source[host]}"
    elif "git" in source:
        text = f"git {source['git']}"
    elif "path" in source:
        text = f"path {source['path']}"
    elif "dist" in source:
        text = f"dist {source['dist']}"
    else:
        text = str(dict(source))
    sub = source.get("subdir")
    return redact(f"{text} ({sub})" if sub else text)


def tarball_url(source: Mapping[str, Any], rev: str) -> str | None:
    """The HTTPS tarball URL of a commit on GitHub, GitLab or Codeberg."""
    host = _host(source)
    if host is None:
        return None
    slug = _slug(source, host)
    if host == "github":
        return f"https://codeload.github.com/{slug}/tar.gz/{rev}"
    repo = slug.rsplit("/", 1)[-1]
    if host == "gitlab":
        return f"https://gitlab.com/{slug}/-/archive/{rev}/{repo}-{rev}.tar.gz"
    return f"https://codeberg.org/{slug}/archive/{rev}.tar.gz"


def api_tarball_url(source: Mapping[str, Any], rev: str) -> str | None:
    """GitHub's API tarball URL of a commit (it redirects to a short-lived download URL)."""
    if _host(source) != "github":
        return None
    return f"https://api.github.com/repos/{_slug(source, 'github')}/tarball/{rev}"


def clone_url(source: Mapping[str, Any]) -> str | None:
    """The URL git clones a source from."""
    host = _host(source)
    if host is not None:
        return f"https://{HOSTS[host]}/{_slug(source, host)}.git"
    if "git" in source:
        return str(source["git"])
    return None


# ---------------------------------------------------------------------------
# Tarballs
# ---------------------------------------------------------------------------


def download(url: str, *, limit: int = MAX_DOWNLOAD, token: bool = False) -> bytes:
    """GET *url*, refusing bodies over *limit* bytes.

    With ``token=True`` a GitHub token may be sent (only to GitHub's https
    hosts, see :mod:`metacheck.packs.auth`). Errors name *url*, never a
    redirect target.
    """
    from metacheck.packs.auth import get

    res = get(url, token=token, limit=limit, tries=2, timeout=60.0)
    if res.status != 200:
        raise DownloadError(f"Downloading {url} failed: {res.problem()}", status=res.status)
    return res.content


def _download_tarball(source: Mapping[str, Any], rev: str) -> bytes:
    """A commit's tarball: GitHub's API with a token first, then the public download URL."""
    urls: list[tuple[str, bool]] = []
    api = api_tarball_url(source, rev)
    if api is not None and github_token():
        urls.append((api, True))
    public = tarball_url(source, rev)
    if public is not None:
        urls.append((public, False))
    errors: list[DownloadError] = []
    for url, token in urls:
        try:
            return download(url, token=token)
        except DownloadError as exc:
            errors.append(exc)
    statuses = [e.status for e in errors if e.status is not None]
    denied = [st for st in statuses if st in (401, 403, 404)]
    status = denied[0] if denied else (statuses[-1] if statuses else None)
    raise DownloadError("; ".join(str(e) for e in errors), status=status)


def _one_line(text: str) -> str:
    return " ".join(redact(text).split())


def _auth_failure(
    source: Mapping[str, Any], rev: str, http: DownloadError, git: PackError | None
) -> DownloadError:
    """Why a source could not be fetched, and (for 401/403/404) how to authenticate."""
    text = f"Cannot download {describe_source(source)} at {rev[:12]}: {_one_line(str(http))}"
    if git is not None:
        text += f"; git: {_one_line(str(git))}"
    if http.status in (401, 403, 404):
        private = "If the repository is private, configure git credentials for it"
        text += f". {AUTH_HELP if _host(source) == 'github' else private}"
    return DownloadError(text, status=http.status)


def _unsafe(name: str, why: str) -> PackError:
    return PackError(f"Refusing to install: the archive member {name!r} {why}")


def _member_parts(name: str) -> tuple[str, ...]:
    if name.startswith("/") or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise _unsafe(name, "has an absolute path")
    parts = tuple(p for p in PurePosixPath(name).parts if p not in ("", "."))
    if ".." in parts:
        raise _unsafe(name, "points outside the archive ('..')")
    return parts


def extract_tarball(data: bytes, dest: str | os.PathLike[str], subdir: str = "") -> int:
    """Safely extract a (gzipped) tarball's *subdir* into *dest*; returns the file count.

    The archive must have a single top-level folder (as forge tarballs do);
    it is stripped. Members outside *subdir* are skipped.
    """
    out = Path(dest)
    sub = _subdir({"subdir": subdir})
    top: str | None = None
    files = total = 0
    found = False
    out.mkdir(parents=True, exist_ok=True)
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")  # noqa: SIM115
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise PackError(f"The downloaded archive is not a valid tarball: {exc}") from exc
    with tar:
        try:
            for m in tar:
                parts = _member_parts(m.name)
                if not parts:
                    continue
                if top is None:
                    top = parts[0]
                elif parts[0] != top:
                    raise _unsafe(m.name, "is outside the archive's single top-level folder")
                rel = "/".join(parts[1:])
                if not rel:
                    continue
                inside = not sub or rel == sub or rel.startswith(sub + "/")
                if not inside:
                    continue
                if m.issym() or m.islnk():
                    raise _unsafe(m.name, "is a link")
                if m.ischr() or m.isblk() or m.isfifo() or m.isdev():
                    raise _unsafe(m.name, "is a device or fifo")
                found = True
                local = rel[len(sub) + 1 :] if sub else rel
                if not local:
                    continue
                if _skipped(local.split("/")):
                    continue  # bytecode, .git, an install record: never hashed, never written
                target = out.joinpath(*local.split("/"))
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not m.isfile():
                    raise _unsafe(m.name, "is not a regular file")
                _refuse_native(local)
                files += 1
                total += m.size
                if files > MAX_FILES:
                    raise PackError(f"Refusing to install: more than {MAX_FILES} files")
                if total > MAX_BYTES:
                    raise PackError(f"Refusing to install: more than {MAX_BYTES // 2**20} MB")
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(m)
                if src is None:  # pragma: no cover - regular files always have data
                    raise _unsafe(m.name, "has no data")
                with src, open(target, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                os.chmod(target, 0o644)
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise PackError(f"The downloaded archive is damaged: {exc}") from exc
    if sub and not found:
        raise PackError(f"The archive has no folder '{sub}'")
    if files == 0 and not found and not sub:
        raise PackError("The archive is empty")
    return files


# ---------------------------------------------------------------------------
# Local folders
# ---------------------------------------------------------------------------


def copy_tree(src: str | os.PathLike[str], dest: str | os.PathLike[str]) -> int:
    """Copy a pack folder's regular files (no ``.git``, no symlinks) into *dest*."""
    base = Path(src)
    if not base.is_dir():
        raise PackError(f"{base} is not a folder")
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)
    files = total = 0
    for folder, dirs, names in os.walk(base):
        for d in list(dirs):
            if d in _SKIP_DIRS:
                dirs.remove(d)
            elif os.path.islink(os.path.join(folder, d)):
                raise PackError(f"Refusing to install: {os.path.join(folder, d)} is a symlink")
        for name in names:
            full = Path(folder) / name
            rel = full.relative_to(base)
            if full.is_symlink():
                raise PackError(f"Refusing to install: {rel.as_posix()} is a symlink")
            if not full.is_file() or _skipped(rel.parts):
                continue
            _refuse_native(rel.as_posix())
            files += 1
            total += full.stat().st_size
            if files > MAX_FILES:
                raise PackError(f"Refusing to install: more than {MAX_FILES} files")
            if total > MAX_BYTES:
                raise PackError(f"Refusing to install: more than {MAX_BYTES // 2**20} MB")
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(full, target)
            os.chmod(target, 0o644)
    return files


# ---------------------------------------------------------------------------
# Hardened git (fallback, and git/ssh/file sources)
# ---------------------------------------------------------------------------

_GIT_CONFIG = (
    "-c", "protocol.ext.allow=never",
    "-c", f"core.hooksPath={os.devnull}",
    "-c", "submodule.recurse=false",
    "-c", "filter.lfs.smudge=",
    "-c", "filter.lfs.process=",
    "-c", "filter.lfs.required=false",
    "-c", "advice.detachedHead=false",
)  # fmt: skip


def _check_git_url(url: str) -> str:
    if not url or url.startswith("-"):
        raise PackError(f"Invalid git URL {redact(url)!r}")
    if re.match(r"^[\w.-]+@[\w.-]+:", url):  # scp-like ssh (git@host:owner/repo)
        return url
    scheme = urlsplit(url).scheme.lower()
    if scheme in ("https", "ssh", "file"):
        return url
    if not scheme and Path(url).expanduser().is_absolute():
        return url
    raise PackError(
        f"Refusing the git URL {redact(url)!r}: only https://, ssh:// (or git@host:...), file:// and "
        "absolute local paths are allowed"
    )


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ASKPASS": "",
            "SSH_ASKPASS": "",
        }
    )
    return env


def _git_bin() -> str:
    git = shutil.which("git")
    if git is None:
        raise PackError("git is not installed, and this source needs it (install git and retry)")
    return git


def _run_git(
    *args: str, cwd: str | os.PathLike[str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed git binary, validated URL, no shell
        [_git_bin(), *_GIT_CONFIG, *args],
        cwd=cwd,
        env=_git_env(),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def _run_git_bytes(
    *args: str, cwd: str | os.PathLike[str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(  # noqa: S603 - fixed git binary, validated URL, no shell
        [_git_bin(), *_GIT_CONFIG, *args],
        cwd=cwd,
        env=_git_env(),
        capture_output=True,
        timeout=600,
        check=False,
    )


class GitMissing(PackError):
    """git read the repository, and it has no such file (so access is not the problem)."""


def git_read_file(url: str, ref: str, path: str, *, limit: int) -> bytes:
    """One file of a git repository at *ref* (a branch, tag, commit or ``HEAD``), nothing else.

    A shallow, blob-less fetch (``--depth 1 --filter=blob:none``: commits and
    trees only, where the server allows it) into a bare temporary repository,
    then ``git cat-file`` of that file alone (git fetches just that blob).
    Nothing is checked out, so the repository's other files (symlinks, large
    or binary files) do not matter. Refuses files over *limit* bytes. Uses
    the user's git credentials; errors are redacted and name no temporary
    path. :class:`GitMissing` means git read the repository but it has no
    *path* (the file is missing, not the access).
    """
    _check_git_url(url)
    if not ref or ref.startswith("-") or not re.fullmatch(r"[\w./+-]+", ref):
        raise PackError(f"Invalid git ref {ref!r}")
    rel = str(PurePosixPath(path))
    if not path or rel.startswith(("/", "-")) or ".." in PurePosixPath(rel).parts:
        raise PackError(f"Invalid path {path!r}")
    shown = redact(url)

    def failed(what: str, res: subprocess.CompletedProcess[Any]) -> DownloadError:
        err = res.stderr.decode("utf-8", "replace") if isinstance(res.stderr, bytes) else res.stderr
        detail = " ".join(redact(err or "").replace(str(work), "<repo>").split())
        return DownloadError(f"git could not {what} of {shown}: {detail or 'failed'}")

    with tempfile.TemporaryDirectory(prefix="pytacheck-git-") as tmp:
        work = Path(tmp) / "repo.git"
        res = _run_git("init", "-q", "--bare", str(work))
        if res.returncode != 0:
            raise PackError("git init failed")
        res = _run_git("remote", "add", "origin", url, cwd=work)
        if res.returncode != 0:
            raise failed("add the remote", res)
        res = _run_git(
            "fetch", "-q", "--depth", "1", "--filter=blob:none", "--no-tags",
            "--no-recurse-submodules", "origin", ref, cwd=work,
        )  # fmt: skip
        if res.returncode != 0:
            if "couldn't find remote ref" in res.stderr:
                raise GitMissing(f"the repository {shown} has no branch or tag {ref!r}")
            raise failed(f"fetch {ref}", res)
        commit = _run_git("rev-parse", "--verify", "-q", "FETCH_HEAD^{commit}", cwd=work)
        sha = commit.stdout.strip()
        where = f"{ref} ({sha[:12]})" if sha and ref != sha else ref
        # "<mode> <type> <object>\t<path>" of that one entry (trees only: no blob is read)
        entry = _run_git("ls-tree", "--full-tree", "FETCH_HEAD", "--", rel, cwd=work)
        if entry.returncode != 0:
            raise failed(f"read {rel}", entry)
        fields = entry.stdout.split("\t", 1)[0].split()
        if not fields:
            raise GitMissing(f"the repository {shown} has no {rel} at {where}")
        if len(fields) != 3 or fields[1] != "blob" or fields[0] not in ("100644", "100755"):
            raise GitMissing(f"{rel} in the repository {shown} is not a regular file at {where}")
        obj = fields[2]
        size = _run_git("cat-file", "-s", obj, cwd=work)  # fetches this blob alone
        if size.returncode != 0:
            raise failed(f"read {rel}", size)
        try:
            n = int(size.stdout.strip())
        except ValueError:
            n = 0
        if n > limit:
            raise PackError(f"{rel} of {shown} is too large ({n} bytes; limit {limit})")
        blob = _run_git_bytes("cat-file", "blob", obj, cwd=work)
        if blob.returncode != 0:
            raise failed(f"read {rel}", blob)
        if len(blob.stdout) > limit:
            raise PackError(f"{rel} of {shown} is too large (limit {limit} bytes)")
        return blob.stdout


def git_fetch(url: str, rev: str, dest: str | os.PathLike[str], subdir: str = "") -> int:
    """Check out commit *rev* of *url* with hardened git and copy *subdir* to *dest*.

    Fetches the single commit (``--depth 1``), falling back to a full clone,
    and verifies that ``rev-parse HEAD`` equals the pin.
    """
    _check_git_url(url)
    if not _SHA.match(rev or ""):
        raise PackError(f"git sources need a full 40-character commit SHA, not {rev!r}")
    sub = _subdir({"subdir": subdir})
    with tempfile.TemporaryDirectory(prefix="pytacheck-git-") as tmp:
        work = Path(tmp) / "repo"
        res = _run_git("init", "-q", str(work))
        if res.returncode != 0:
            raise PackError(f"git init failed: {res.stderr.strip()}")
        res = _run_git(
            "fetch", "-q", "--depth", "1", "--no-tags", "--no-recurse-submodules", url, rev,
            cwd=work,
        )  # fmt: skip
        if res.returncode == 0:
            res = _run_git("checkout", "-q", "--detach", "FETCH_HEAD", cwd=work)
        if res.returncode != 0:
            shutil.rmtree(work, ignore_errors=True)
            res = _run_git(
                "clone", "-q", "--no-checkout", "--no-recurse-submodules", "--", url, str(work)
            )
            if res.returncode != 0:
                raise DownloadError(
                    f"git could not fetch {redact(url)}: {redact(res.stderr.strip())}"
                )
            res = _run_git("checkout", "-q", "--detach", rev, cwd=work)
            if res.returncode != 0:
                raise PackError(
                    f"git could not check out {rev} of {redact(url)}: {redact(res.stderr.strip())}"
                )
        head = _run_git("rev-parse", "HEAD", cwd=work).stdout.strip()
        if head != rev:
            raise PackError(f"git checked out {head or 'nothing'}, not the pinned {rev}")
        src = work.joinpath(*sub.split("/")) if sub else work
        if not src.is_dir():
            raise PackError(f"The repository has no folder '{sub}' at {rev[:12]}")
        return copy_tree(src, dest)


def _ls_remote(url: str, ref: str | None) -> str:
    _check_git_url(url)
    res = _run_git("ls-remote", "--", url, ref or "HEAD")
    if res.returncode != 0:
        raise DownloadError(f"git ls-remote {redact(url)} failed: {redact(res.stderr.strip())}")
    rows = [line.split("\t", 1) for line in res.stdout.splitlines() if "\t" in line]
    want = ref or "HEAD"
    names = (want, f"refs/heads/{want}", f"refs/tags/{want}")
    peeled = {n.removesuffix("^{}"): sha for sha, n in rows if n.endswith("^{}")}
    for n in names:
        for sha, name in rows:
            if name == n:
                return peeled.get(name, sha)
    raise PackError(f"The ref {want!r} does not exist in {redact(url)}")


def git_rev(url: str, ref: str | None = None) -> str:
    """The commit a branch or tag (default: the default branch) of a git remote points at."""
    if ref and _SHA.match(ref.lower()):
        return ref.lower()
    return _ls_remote(url, ref)


def resolve_rev(source: Mapping[str, Any], ref: str | None = None) -> str:
    """Resolve a tag, branch or short SHA (default: the default branch) to a commit SHA, once.

    GitHub sources ask the GitHub API (with a token when one is set, see
    :mod:`metacheck.packs.auth`); everything else, and GitHub when the API
    cannot be reached or does not show the repository (private, no token), uses
    ``git ls-remote`` with the user's git credentials.
    """
    if ref and _SHA.match(ref.lower()):
        return ref.lower()
    if "path" in source:
        raise PackError("Local folders have no commits to resolve")
    denied: str | None = None  # the API's 401/403/404: private, or no access
    if "github" in source:
        from metacheck.packs.auth import get

        slug = _slug(source, "github")
        url = f"https://api.github.com/repos/{slug}/commits/{quote(ref or 'HEAD', safe='')}"
        try:
            res = get(url, token=True, accept="application/vnd.github.sha", limit=4096)
        except DownloadError:
            res = None
        if res is not None and res.status == 200:
            sha = res.text.strip().lower()
            if _SHA.match(sha):
                return sha
        if res is not None and res.status == 422:
            raise PackError(f"{ref or 'HEAD'} is not a branch, tag or commit of github {slug}")
        if res is not None and res.denied:
            denied = f"github {slug}: {res.problem()}"
    remote = clone_url(source)
    if remote is None:
        raise PackError(f"Cannot resolve a revision for the source {redact(dict(source))}")
    short = bool(ref and re.match(r"^[0-9a-f]{4,39}$", ref.lower()))
    try:
        return _ls_remote(remote, ref)
    except PackError as exc:
        if denied is not None and isinstance(exc, DownloadError):
            raise DownloadError(
                f"Cannot read {denied}; git: {_one_line(str(exc))}. {AUTH_HELP}"
            ) from None
        if short:
            raise PackError(
                f"Cannot resolve the short commit id {ref!r} without the GitHub API; "
                "give the full 40-character SHA"
            ) from None
        raise


def fetch_source(
    source: Mapping[str, Any],
    rev: str | None,
    dest: str | os.PathLike[str],
    *,
    base: str | os.PathLike[str] | None = None,
) -> int:
    """Put the pack folder of *source* at commit *rev* into *dest*; returns the file count.

    ``base`` resolves a relative ``{"path": ...}`` source (a local store's folder);
    with a *base*, the folder must be inside it.
    """
    sub = _subdir(source)
    if "path" in source:
        folder = Path(str(source["path"])).expanduser()
        if not folder.is_absolute():
            if base is None:
                raise PackError(
                    f"The source {redact(dict(source))} is a path relative to its store, and this "
                    "store is not a local folder (its CI regenerates the index with commits)"
                )
            folder = Path(base) / folder
        if base is not None and not folder.resolve().is_relative_to(Path(base).resolve()):
            raise PackError(f"The source folder {folder} is outside its store ({base})")
        if sub:
            folder = folder.joinpath(*sub.split("/"))
        return copy_tree(folder, dest)
    if not rev or not _SHA.match(rev):
        raise PackError(f"A pack source needs a full 40-character commit SHA, not {rev!r}")
    if tarball_url(source, rev) is not None:
        try:
            data = _download_tarball(source, rev)
        except DownloadError as exc:
            if shutil.which("git") is None:
                raise _auth_failure(source, rev, exc, None) from None
            try:
                return git_fetch(str(clone_url(source)), rev, dest, sub)
            except DownloadError as git_exc:
                raise _auth_failure(source, rev, exc, git_exc) from None
        return extract_tarball(data, dest, sub)
    if "git" in source:
        return git_fetch(str(source["git"]), rev, dest, sub)
    raise PackError(f"Unsupported pack source {redact(dict(source))}")
