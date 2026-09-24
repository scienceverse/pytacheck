"""GitHub repositories (port of ``R/archive-github.R``).

:func:`github_links` finds GitHub links in papers (real hyperlinks and bare
``owner/repo`` mentions near the word "github"); :func:`github_repo` checks a
repository exists; :func:`github_readme`, :func:`github_languages`,
:func:`github_files` and :func:`github_tree_files` query the GitHub API;
:func:`github_info` bundles them.

All requests go through :mod:`pytacheck.http`. As in metacheck, a request is
tried once (httr2 without ``req_retry()``), an HTTP error status is returned
rather than raised, and a connection failure raises.
"""

from __future__ import annotations

import base64
import functools
import os
import subprocess
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from pytacheck._r import is_na

if TYPE_CHECKING:
    import httpx

__all__ = [
    "github_files",
    "github_info",
    "github_languages",
    "github_links",
    "github_readme",
    "github_repo",
    "github_tree_files",
]

#: R: ``github_regex`` in github_links()
_GITHUB_REGEX = r"(?:https?://)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*"
#: R: the regexec() pattern in github_repo()
_REPO_REGEX = r"(?<=^|/)([a-z0-9-])+/([a-z0-9\._-])+(?=\.git|/|$)"


# ---------------------------------------------------------------------------
# shared helpers (also used by the GitLab and Zenodo ports)
# ---------------------------------------------------------------------------


def _perform(method: str, url: str, **kwargs: Any) -> httpx.Response:
    """``httr2::req_perform()`` with ``req_error(is_error = \\(resp) FALSE)``.

    One try (no ``req_retry()``); any status is returned; a connection-level
    failure raises, as httr2 does.
    """
    from pytacheck import http

    kwargs.setdefault("max_tries", 1)
    resp = http.request(method, url, **kwargs)
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request: {method} {url}")
    return resp


def _dollar(x: Any, name: str) -> Any:
    """R ``x$name`` on parsed JSON (``simplifyVector = FALSE``).

    ``NULL`` for ``NULL`` or an unnamed list (a JSON array), the exact or
    unique partial match on a named list, and R's error on an atomic value.
    """
    from collections.abc import Mapping

    from pytacheck.db._utils import r_dollar

    if x is None or isinstance(x, list | tuple):
        return None
    if isinstance(x, Mapping):
        return r_dollar(x, name)
    raise TypeError("$ operator is invalid for atomic vectors")


def _body_json(resp: httpx.Response) -> Any:
    """``httr2::resp_body_json()`` (content type checked, errors raise)."""
    from pytacheck.archives.osf_helpers import _resp_body_json

    return _resp_body_json(resp)


def _is_vector(x: Any) -> bool:
    """Is *x* an R vector of several values (not a single string)?"""
    return not isinstance(x, str) and isinstance(x, Sequence | pd.Series | pd.Index)


def _as_list(x: Any) -> list[Any]:
    """An R vector argument as a Python list (``None`` -> empty)."""
    if x is None:
        return []
    if isinstance(x, pd.Series | pd.Index):
        return [None if is_na(v) else v for v in x.tolist()]
    if _is_vector(x):
        return [None if is_na(v) else v for v in x]
    return [x]


_INT_MAX = 2**31 - 1


def _r_value_type(v: Any) -> str:
    """The R type jsonlite gives a JSON scalar (``int`` past 32 bits is a double)."""
    if isinstance(v, bool):
        return "logical"
    if isinstance(v, int):
        return "integer" if -_INT_MAX <= v <= _INT_MAX else "double"
    if isinstance(v, float):
        return "double"
    return "character"


_TYPE_ORDER = {"logical": 0, "integer": 1, "double": 2, "character": 3}
_TYPE_DTYPE = {"logical": "boolean", "integer": "Int64", "double": "float64", "character": "string"}


def _r_unlist(values: Sequence[Any]) -> pd.Series:
    """R ``unlist()`` of parsed JSON values: nested lists flattened, ``NULL`` dropped.

    The result has the highest R type present (logical < integer < double <
    character), as ``unlist()`` coerces.
    """
    from pytacheck._r import as_character

    flat: list[Any] = []

    def walk(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list | tuple):
            for x in v:
                walk(x)
        else:
            flat.append(v)

    for v in values:
        walk(v)
    if not flat:
        return pd.Series([], dtype="object")
    rtype = max((_r_value_type(v) for v in flat), key=_TYPE_ORDER.__getitem__)
    if rtype == "character":
        out = [("TRUE" if v else "FALSE") if isinstance(v, bool) else as_character(v) for v in flat]
        return pd.Series(out, dtype="string")
    if rtype == "double":
        return pd.Series([float(v) for v in flat], dtype="float64")
    return pd.Series(flat, dtype=_TYPE_DTYPE[rtype])


def _r_data_frame(cols: dict[str, Any]) -> pd.DataFrame:
    """``data.frame(...)`` of vectors: shorter ones recycled, as R does.

    Every length must divide the longest (and none be zero unless all are),
    else R's "arguments imply differing number of rows" error.
    """
    series = {k: v if isinstance(v, pd.Series) else pd.Series(v) for k, v in cols.items()}
    lengths = [len(s) for s in series.values()]
    nr = max(lengths, default=0)
    if any((n == 0 and nr > 0) or (n > 0 and nr % n) for n in lengths):
        raise ValueError(
            "arguments imply differing number of rows: " + ", ".join(str(n) for n in lengths)
        )
    out = {}
    for k, s in series.items():
        s = s.reset_index(drop=True)
        if 0 < len(s) < nr:
            s = pd.concat([s] * (nr // len(s)), ignore_index=True)
        out[k] = s
    return pd.DataFrame(out)


def _r_col_type(col: pd.Series) -> str:
    """The R type of a column: logical/integer/double/character/list (``unspecified`` = all-NA logical)."""
    dtype = col.dtype
    if pd.api.types.is_bool_dtype(dtype):
        return "unspecified" if bool(col.isna().all()) else "logical"
    if pd.api.types.is_integer_dtype(dtype):
        return "integer"
    if pd.api.types.is_float_dtype(dtype):
        return "double"
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_object_dtype(dtype):
        return "character"
    if isinstance(dtype, pd.CategoricalDtype):
        return "factor"
    return "list"


def _check_combine(frames: Sequence[pd.DataFrame | None]) -> None:
    """Raise as ``dplyr::bind_rows()`` (vctrs) does when column types cannot be combined.

    Logical, integer and double combine; character only with character; a
    list column only with list columns. An all-``NA`` logical column combines
    with anything. Factors are left to the caller.
    """
    numeric = {"logical", "integer", "double"}
    parts = [f for f in frames if f is not None]
    seen: dict[str, tuple[int, str]] = {}
    for i, f in enumerate(parts, start=1):
        for c in f.columns:
            t = _r_col_type(f[c])
            if t in ("unspecified", "factor"):
                continue
            if c not in seen:
                seen[c] = (i, t)
                continue
            j, prev = seen[c]
            if prev == t or (prev in numeric and t in numeric):
                if _TYPE_ORDER.get(t, -1) > _TYPE_ORDER.get(prev, -1):
                    seen[c] = (j, t)
                continue
            raise TypeError(f"Can't combine `..{j}${c}` <{prev}> and `..{i}${c}` <{t}>.")


def _r_basename(path: str) -> str:
    """R ``basename()`` (trailing separators are dropped first)."""
    stripped = path.rstrip("/")
    if not stripped:
        return ""
    return stripped.rsplit("/", 1)[-1]


def _file_ext(name: str) -> str:
    """``tools::file_ext()``: the alphanumeric extension after the last dot, or ``""``."""
    from pytacheck._r import regextract

    m = regextract(r"\.([[:alnum:]]+)$", name)
    return "" if m is None else m[1:]


def _url_encode(url: str, reserved: bool = False) -> str:
    """``utils::URLencode()``."""
    from pytacheck.db._utils import url_encode

    return url_encode(url, reserved=reserved)


# metacheck::file_types (data/file_types.rda): 404 rows of ext:type, in order.
# Several extensions appear twice (e.g. json is "code" and "data"), so a
# left_join() onto it repeats those files, as in metacheck.
_FILE_TYPES_TABLE = """
1.ada:code 2.ada:code 3dm:image 3ds:3D 3ds:image 3g2:video 3gp:video 3mf:3D 7z:archive
a:archive aac:audio aaf:video aar:archive abw:text ada:code adb:code ado:code ads:code
ai:image aif:audio aiff:audio amr:audio ape:audio apk:archive ar:archive arff:data arw:image
asf:video asm:code asp:code asp:web aspx:code aspx:web au:audio avchd:video avi:video
avif:image azw:book azw1:book azw3:book azw4:book azw6:book bak:config bas:code bash:code
bash:exec bat:code bat:exec bin:exec bmp:image br:archive bz2:archive c:code c++:code
cab:archive car:video cbl:code cbr:book cbz:book cc:code cfg:config class:code clj:code
cmake:config cmd:exec cob:code com:exec command:exec config:config cpio:archive cpp:code
cr2:image cr3:image crx:exec cs:code csh:code csh:exec css:web csv:data csv.sav:data
cxx:code d:code dart:code dat:data dav:video dds:image deb:archive dft:data diff:code
dll:code dmg:archive do:stats doc:text docx:text drc:video dss:code dta:data dwg:image
dxf:image e:code ebook:text egg:archive el:code env:config eot:font eps:image epub:book
exe:exec f:code f3d:3D f77:code f90:code fa.gz:data fasta.gz:data fastq.gz:data feather:data
fish:code fish:exec flac:audio flv:video for:code fq.gz:data fth:code ftn:code gcode:3D
gdt:data gdtb:data gen:data geojson:data gif:image git:config gitignore:config go:code
gpx:image gradle:code groovy:code gsm:audio gz:archive h:code heic:image heif:image
hevc:video hh:code hpp:code hs:code htm:code htm:web html:code html:web hxx:code ico:image
ics:data inc:code inc:web ini:config ipynb:code iso:archive it:audio jar:archive jasp:data
jasp:stats java:code jl:code jp2:image jpeg:image jpg:image js:code js:web json:code
json:data jsp:code jsp:web jsx:code jsx:web jxl:image kml:image kmz:image ksh:code ksh:exec
kt:code kts:code less:web lha:archive lhs:code lisp:code lock:config log:text lua:code
lz:archive lz4:archive lzma:archive lzo:archive lzop:archive m:code m2ts:video m2v:video
m3u:audio m4:code m4a:audio m4p:video m4v:video make:config mar:archive mat:data max:image
md:text mid:audio mk:config mka:audio mkv:video mng:video mobi:book mod:audio mov:video
mp2:video mp3:audio mp4:video mpa:audio mpe:video mpeg:video mpg:video mpv:video msg:text
msi:exec mts:video mxf:video ndjson:data nef:image nim:code nsv:video obj:3D odf:text
odg:text odp:slide ods:data odt:text ogg:audio ogm:video ogv:video ogx:video old:config
omv:data opus:audio orc:data org:text orig:config otf:font pages:text pak:archive
parquet:data patch:code pdf:text pea:archive pfb:font pfm:font php:code php:web php3:code
php3:web php4:code php4:web php5:code php5:web phtml:code phtml:web pl:code pls:audio
png:image po:code por:data por:stats pp:code ppt:slide pptx:slide prql:code ps:image
ps1:code ps1xml:code psb:image psc1:code psd:image psd1:code psm1:code psrc:code pssc:code
py:code qmd:code qt:video quarto:code r:code ra:audio rar:archive raw:image rb:code rd:code
rda:data rdata:data rds:code rds:data rm:video rmd:code rmvb:video rnw:code roq:video
rpm:archive rproj:config rs:code rst:text rtf:text rtx:text s:code s3m:audio s7z:archive
sas:stats sas7bdat:data sav:data sav.gz:data scad:3D scala:code scss:web sd7:data sh:code
sh:exec shar:archive sid:audio smt:3D sol:code spo:stats sps:stats spss:stats spv:stats
sql:code srt:video step:3D stl:3D stp:3D svelte:code svg:image svi:video swg:code swift:code
swp:config sz:archive tar:archive tbz2:archive tex:text tga:image tgz:archive thm:image
tif:image tiff:image tlz:archive tmp:config toml:config ts:web ts:code tsv:data tsx:web
ttf:font txt:text txz:archive v:code vb:code vcf:data vcxproj:code vob:video vue:code
war:archive wasm:web wav:audio webm:video webp:image wf1:data whl:archive wll:code wma:audio
wmv:video woff:font woff2:font wpd:text wps:text xba:video xcf:image xcodeproj:code xll:code
xls:data xlsx:data xm:audio xml:code xpi:archive xpt:data xz:archive yaml:config yml:config
yuv:image yuv:video z:archive zig:code zip:archive zipx:archive zsav:data zsh:code zsh:exec
zst:archive
"""


@functools.cache
def _file_types_fallback() -> pd.DataFrame:
    pairs = [item.split(":", 1) for item in _FILE_TYPES_TABLE.split()]
    return pd.DataFrame(
        {
            "ext": pd.Series([p[0] for p in pairs], dtype="string"),
            "type": pd.Series([p[1] for p in pairs], dtype="string"),
        }
    )


def _file_types() -> pd.DataFrame:
    """``metacheck::file_types``: the coarse type (``code``, ``data``...) of an extension.

    Taken from :mod:`pytacheck.fileinfo.types` when that port is present,
    else from the copy of the table embedded here.
    """
    try:
        from pytacheck.fileinfo import types as ft_module  # type: ignore[attr-defined]
    except ImportError:
        ft_module = None
    if ft_module is not None:
        obj = getattr(ft_module, "file_types", None)
        if callable(obj):
            obj = obj()
        if isinstance(obj, pd.DataFrame) and {"ext", "type"} <= set(obj.columns):
            return obj[["ext", "type"]]
    return _file_types_fallback()


def _add_file_types(files: pd.DataFrame, drop_ext: bool) -> pd.DataFrame:
    """``left_join(files, file_types, by = "ext")``, then fill ``type`` from ``ft``.

    Drops ``ft`` (and ``ext`` when *drop_ext*), as metacheck does after the join.
    """
    from pytacheck.utils import left_join

    out = left_join(files, _file_types(), by="ext")
    missing = out["type"].isna()
    out["type"] = out["type"].astype("string").mask(missing, out["ft"].astype("string"))
    drop = ["ft", "ext"] if drop_ext else ["ft"]
    return out.drop(columns=drop)


# ---------------------------------------------------------------------------
# links in papers
# ---------------------------------------------------------------------------


def _plusminus(host: str, texts: list[Any]) -> str:
    """metacheck's "+-10 words around the host name" pattern, as R's TRE applies it.

    R runs the TRE pattern
    ``(?:\\b\\w+\\b\\W+){0,10}\\b<host>(\\.com)?\\b(?:\\W+\\b\\w+\\b){0,10}``
    over every sentence at once. When any sentence it matches is non-ASCII, R
    switches TRE to its wide-character matcher. That matcher ignores the
    ``{0,10}`` upper bounds here (nested ``\\w+``/``\\W+`` inside a bounded
    group), so the match runs to the ends of the sentence, and so does the
    owner/repo search that follows. Checked against R on 3000 random
    sentences: wide mode equals the unbounded pattern in every case. So the
    unbounded pattern is used exactly when R would be in wide mode.
    """
    rep = "*" if _tre_wide(rf"\b{host}(\.com)?\b", texts) else "{0,10}"
    return rf"(?:\b\w+\b\W+){rep}\b{host}(\.com)?\b(?:\W+\b\w+\b){rep}"


def _tre_wide(match_pattern: str, texts: list[Any]) -> bool:
    """Would R's ``gregexpr()`` use TRE's wide-character mode on the matching *texts*?

    ``text_search()`` calls ``gregexpr()`` on the rows that match; R uses the
    wide-character matcher when any of them (NA aside) is not pure ASCII.
    """
    from pytacheck._r import grepl

    hits = grepl(match_pattern, texts, ignore_case=True)
    return any(
        bool(h) and isinstance(t, str) and not t.isascii() for t, h in zip(texts, hits, strict=True)
    )


def _host_links(paper: Any, host: str, host_regex: str) -> pd.DataFrame:
    """The shared body of github_links() / gitlab_links()."""
    from pytacheck._r import bind_rows
    from pytacheck.papers.tables import paper_table
    from pytacheck.text.search import text_search

    # strip punctuation off the end of sentences to avoid weird matches
    strip_text = text_search(paper, r".*[^\.$]", return_="match", perl=True)

    found = text_search(paper_table(paper, "url"), host_regex, perl=True)
    found = found[["href", "text_id", "paper_id"]]

    # repos referenced only by owner/repo near the host name (+-10 words)
    no_host_regex = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?"
    other = text_search(strip_text, host)
    other = text_search(other, host_regex, exclude=True, perl=True)
    other = text_search(other, f"{host}.io", exclude=True)
    other = text_search(other, _plusminus(host, other["text"].tolist()), return_="match")
    other = text_search(other, no_host_regex, return_="match", perl=True)
    other = other[["text", "text_id", "paper_id"]].rename(columns={"text": "href"})
    return bind_rows([found, other])


def github_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-github.R::github_links(): GitHub links in papers.

    Hyperlinks to ``github.com/owner/repo`` in the paper's ``url`` table, plus
    bare ``owner/repo`` mentions within ten words of "github" in sentences
    that contain no GitHub URL (and no ``github.io``). Returns ``href``,
    ``text_id`` and ``paper_id``.
    """
    return _host_links(paper, "github", _GITHUB_REGEX)


# ---------------------------------------------------------------------------
# repositories
# ---------------------------------------------------------------------------


def github_repo(repo: Any) -> Any:
    """Port of R/archive-github.R::github_repo(): the short ``owner/repo`` name.

    Accepts ``owner/repo`` or a GitHub URL (``.git`` and trailing paths are
    ignored) and checks the repository exists with a ``HEAD`` request;
    returns ``None`` when it does not. Several repositories give a list
    aligned with the input (R: a vector named by the input).
    """
    if repo is None:
        return None
    if _is_vector(repo):
        items = _as_list(repo)
        if not items:
            return None
        if len(items) > 1:
            return [github_repo(r) for r in items]
        repo = items[0]
    if is_na(repo):
        return None

    from pytacheck._r import as_character, regexec, sub

    text = repo if isinstance(repo, str) else as_character(repo)
    match = regexec(_REPO_REGEX, text, perl=True, ignore_case=True)
    if not match:
        return None
    simple_repo = sub(r"\.git$", "", match[0])

    resp = _perform("HEAD", f"https://github.com/{simple_repo}")
    if resp.status_code != 200:
        return None
    return simple_repo


# gitcreds::gitcreds_get() caches the token for the session (in an env var)
_TOKEN_CACHE: dict[str, str | None] = {}


def _github_token() -> str | None:
    """The GitHub token ``gitcreds::gitcreds_get()`` would find, or ``None``.

    ``GITHUB_PAT_GITHUB_COM`` first, then git's credential helpers for
    ``https://github.com`` (never prompting). Looked up once per session.
    """
    if "token" in _TOKEN_CACHE:
        return _TOKEN_CACHE["token"]
    token = os.environ.get("GITHUB_PAT_GITHUB_COM") or None
    if token is None:
        env = dict(os.environ)
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "GIT_ASKPASS": "",
                "SSH_ASKPASS": "",
            }
        )
        try:
            out = subprocess.run(
                ["git", "credential", "fill"],  # noqa: S607 - git on PATH, as gitcreds uses
                input="protocol=https\nhost=github.com\npath=\n\n",
                capture_output=True,
                text=True,
                timeout=10,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            out = None
        if out is not None and out.returncode == 0:
            for line in out.stdout.splitlines():
                key, _, value = line.partition("=")
                if key == "password" and value:
                    token = value
    _TOKEN_CACHE["token"] = token
    return token


def _github_config(headers: dict[str, str] | None = None) -> dict[str, str]:
    """Port of R/archive-github.R::.github_config(): GitHub API request headers.

    Adds the v3 ``Accept`` type, metacheck's ``User-Agent``, and an
    ``Authorization`` token when git has GitHub credentials. R modifies an
    httr2 request; here the headers are returned for :func:`pytacheck.http.request`.
    """
    out = dict(headers or {})
    out["Accept"] = "application/vnd.github.v3+json"
    out["User-Agent"] = "scienceverse/metacheck"
    token = _github_token()
    if token is not None:
        out["Authorization"] = f"token {token}"
    return out


def github_readme(repo: Any) -> Any:
    """Port of R/archive-github.R::github_readme(): a repository's README text.

    ``""`` when the repository or its README cannot be found. Several
    repositories give a list of READMEs aligned with the input.
    """
    if _is_vector(repo) and len(_as_list(repo)) > 1:
        return [github_readme(r) for r in _as_list(repo)]
    clean = github_repo(repo)
    if clean is None:
        return ""
    return _github_readme(clean)


def _github_readme(clean_repo: str) -> str:
    resp = _perform(
        "GET", f"https://api.github.com/repos/{clean_repo}/readme", headers=_github_config()
    )
    if resp.status_code != 200:
        return ""
    content = _body_json(resp)
    raw = base64.b64decode(str(_dollar(content, "content") or ""))
    if b"\x00" in raw:
        # R: rawToChar() refuses a NUL byte
        raise ValueError("embedded nul in string")
    return raw.decode("utf-8", errors="replace")


def github_languages(repo: Any) -> pd.DataFrame | None:
    """Port of R/archive-github.R::github_languages(): languages used in a repository.

    A table of ``repo``, ``language`` and ``bytes`` (one row of missing
    values when GitHub lists none); ``None`` for a repository that does not
    exist. Several repositories are row-bound (missing ones contribute nothing).
    """
    from pytacheck._r import bind_rows

    if _is_vector(repo) and len(_as_list(repo)) > 1:
        tables = [github_languages(r) for r in _as_list(repo)]
        _check_combine(tables)
        return bind_rows(tables)
    clean = github_repo(repo)
    if clean is None:
        return None
    return _github_languages(clean)


def _github_languages(clean_repo: str) -> pd.DataFrame:
    resp = _perform(
        "GET", f"https://api.github.com/repos/{clean_repo}/languages", headers=_github_config()
    )
    try:
        languages = _body_json(resp)
    except Exception:
        languages = []
    # metacheck does not check the status: an error body such as
    # {"message": "Not Found", ...} becomes "languages" named after its fields
    n = 0 if languages is None else len(languages) if isinstance(languages, list | dict) else 1
    if n:
        # names() of a JSON array or scalar is NULL: data.frame() counts it as
        # a zero-row column and stops ("differing number of rows: 1, 0, n")
        names = list(languages) if isinstance(languages, dict) else []
        if isinstance(languages, dict):
            values = list(languages.values())
        else:
            values = languages if isinstance(languages, list) else [languages]
        return _r_data_frame(
            {
                "repo": pd.Series([clean_repo], dtype="string"),
                "language": pd.Series(names, dtype="string"),
                "bytes": _r_unlist(values),
            }
        )
    return pd.DataFrame(
        {
            "repo": pd.Series([clean_repo], dtype="string"),
            "language": pd.Series([None], dtype="string"),
            "bytes": pd.Series([None], dtype="float64"),
        }
    )


# ---------------------------------------------------------------------------
# file listings
# ---------------------------------------------------------------------------


def _ext_from_name(name: str) -> str:
    """``strsplit(name, "\\\\.")``'s last piece when there are two or more, else ``""``."""
    from pytacheck._r import strsplit

    parts = strsplit(name, r"\.")
    return parts[-1].lower() if len(parts) >= 2 else ""


def github_files(repo: Any, dir: str = "", recursive: bool = False) -> pd.DataFrame | None:
    """Port of R/archive-github.R::github_files(): list a repository's files.

    One row per file or directory in *dir* (the repository root by default):
    ``repo`` (as given), ``clean_repo``, ``name``, ``path``, ``download_url``,
    ``size``, ``ext`` and ``type`` (from the extension, else ``file``/``dir``),
    sorted by path. With *recursive*, sub-directories' contents follow.
    ``None`` when the repository or directory cannot be listed. Several
    repositories are listed from their roots and joined onto the input.
    """
    if _is_vector(repo) and len(_as_list(repo)) > 1:
        from pytacheck._r import bind_rows
        from pytacheck.utils import left_join

        repos = _as_list(repo)
        unique_repos = [r for r in dict.fromkeys(repos) if r is not None]
        info = bind_rows([github_files(r, recursive=recursive) for r in unique_repos])
        if "repo" not in info.columns:
            raise ValueError(
                "Join columns in `y` must be present in the data.\nx Problem with `repo`."
            )
        orig = pd.DataFrame({"repo": pd.Series(repos, dtype="string")})
        return left_join(orig, info, by="repo")

    clean_repo = github_repo(repo)
    if clean_repo is None:
        return None
    single = _as_list(repo)[0] if _is_vector(repo) else repo
    return _github_files(single, clean_repo, dir, recursive)


def _github_files(repo: Any, clean_repo: str, dir: str, recursive: bool) -> pd.DataFrame | None:
    """github_files() for one repository already checked by github_repo()."""
    import datetime as dt

    from pytacheck._r import as_character, gsub, r_sort_key
    from pytacheck.archives import _message

    url = _url_encode(f"https://api.github.com/repos/{clean_repo}/contents/{dir}")
    resp = _perform("GET", url, headers=_github_config())
    try:
        contents = _body_json(resp)
    except Exception:
        contents = []

    if resp.status_code != 200:
        rl = resp.headers.get("x-ratelimit-remaining")
        if rl is not None and _as_int(rl) == 0:
            reset_at = _as_int(resp.headers.get("x-ratelimit-reset"))
            reset = (
                "NA"
                if reset_at is None
                else dt.datetime.fromtimestamp(reset_at).strftime("%Y-%m-%d %H:%M:%S")
            )
            _message("Rate limit exceeded, resetting at ", reset)
        else:
            _message(dir, ": ", _dollar(contents, "message") or "")
        # NULL rather than an error, so a rate limit at the end of a file
        # list still returns the files up to that point
        return None

    if isinstance(contents, dict):
        # R: lapply() over the fields of a single file, then `$` on a string
        raise TypeError("$ operator is invalid for atomic vectors")
    if not contents:
        # R: sort_by() on the NULL that rbind() of no rows gives
        raise TypeError("argument 1 is not a vector")

    repo_str = as_character(repo) if not isinstance(repo, str) else repo
    names = [f.get("name") for f in contents]
    names = gsub("^/|/$", "", gsub("/+", "/", names))
    paths = [f.get("path") for f in contents]
    sizes = [f.get("size") for f in contents]
    files = pd.DataFrame(
        {
            "repo": pd.Series([repo_str] * len(contents), dtype="string"),
            "clean_repo": pd.Series([clean_repo] * len(contents), dtype="string"),
            "name": pd.Series(names, dtype="string"),
            "path": pd.Series(paths, dtype="string"),
            "download_url": pd.Series([f.get("download_url") for f in contents], dtype="string"),
            "ft": pd.Series([f.get("type") for f in contents], dtype="string"),
            "size": pd.Series(
                sizes, dtype="float64" if any(isinstance(s, float) for s in sizes) else "Int64"
            ),
        }
    )
    # sort_by(files, files$path): a stable order in R's collation
    order = sorted(range(len(files)), key=lambda i: r_sort_key(paths[i]))
    files = files.iloc[order].reset_index(drop=True)
    files["ext"] = pd.Series([_ext_from_name(n) for n in files["name"]], dtype="string")
    files = _add_file_types(files, drop_ext=False)

    if recursive is True:
        is_dir = (files["type"] == "dir").fillna(False).tolist()
        subdirs = [p for p, d in zip(files["path"].tolist(), is_dir, strict=True) if d]
        if subdirs:
            from pytacheck._r import bind_rows

            dir_contents = [
                _github_files(repo, clean_repo, subdir, recursive=True) for subdir in subdirs
            ]
            files = bind_rows([files, *dir_contents])
    return files


def _as_int(x: Any) -> int | None:
    """R ``as.integer()`` of a header value (``None`` for NA)."""
    if x is None:
        return None
    try:
        return int(float(str(x).strip()))
    except ValueError:
        return None


def github_tree_files(repo: Any) -> dict[str, Any]:
    """Port of R/archive-github.R::github_tree_files(): a repository's whole file tree.

    Two requests (repository metadata, then the recursive Git tree) instead
    of one per directory. Returns a dict with ``gated`` (``True`` only when
    the repository cannot be listed: invalid/inaccessible, or a truncated
    tree), ``reason``, ``files`` (``repo``, ``clean_repo``, ``name``,
    ``path``, ``download_url``, ``size``, ``type``; ``None`` when not
    fetched), ``default_branch`` and ``license`` (the SPDX id GitHub
    detected). When the metadata or tree request fails, falls back to the
    recursive :func:`github_files` listing.
    """
    clean_repo = github_repo(repo)
    if clean_repo is None:
        return {
            "gated": True,
            "reason": "invalid or inaccessible GitHub repository",
            "files": None,
            "default_branch": None,
            "license": None,
        }

    def fallback(default_branch: str, license: str | None) -> dict[str, Any]:
        try:
            files_df = github_files(repo, recursive=True)
        except Exception:
            files_df = None
        return {
            "gated": False,
            "reason": None,
            "files": files_df,
            "default_branch": default_branch,
            "license": license,
        }

    if isinstance(clean_repo, list):
        # several repositories: R's request() refuses a vector of URLs, and
        # the error sends it to the github_files() fallback
        return fallback("main", None)

    # 1. repository metadata (default branch + detected licence)
    try:
        meta_resp = _perform(
            "GET", f"https://api.github.com/repos/{clean_repo}", headers=_github_config()
        )
    except Exception:
        meta_resp = None
    if meta_resp is None or meta_resp.status_code != 200:
        return fallback("main", None)

    meta = _body_json(meta_resp)
    default_branch = _dollar(meta, "default_branch")
    if default_branch is None:
        default_branch = "main"
    license_id = _empty_or(_dollar(_dollar(meta, "license"), "spdx_id"))

    # 2. the Git tree (recursive, one request)
    try:
        tree_resp = _perform(
            "GET",
            f"https://api.github.com/repos/{clean_repo}/git/trees/{default_branch}?recursive=1",
            headers=_github_config(),
        )
    except Exception:
        tree_resp = None
    if tree_resp is None or tree_resp.status_code != 200:
        return fallback(default_branch, license_id)

    tree = _body_json(tree_resp)
    if _dollar(tree, "truncated") is True:
        return {
            "gated": True,
            "reason": "GitHub repo tree truncated (>100 000 items); too large to list",
            "files": None,
            "default_branch": default_branch,
            "license": license_id,
        }

    blobs = _filter_blobs(_dollar(tree, "tree") or [])
    paths = [_empty_or(_dollar(x, "path"), "") for x in blobs]
    if not blobs:
        files_df = _empty_tree_files()
    else:
        repo_str = _as_list(repo)[0] if _is_vector(repo) else repo
        raw_base = f"https://raw.githubusercontent.com/{clean_repo}/{default_branch}/"
        names = [_r_basename(p) for p in paths]
        files_df = pd.DataFrame(
            {
                "repo": pd.Series([repo_str] * len(blobs), dtype="string"),
                "clean_repo": pd.Series([clean_repo] * len(blobs), dtype="string"),
                "name": pd.Series(names, dtype="string"),
                "path": pd.Series(paths, dtype="string"),
                "download_url": pd.Series([raw_base + p for p in paths], dtype="string"),
                "size": pd.Series(
                    [_vapply_num(_dollar(x, "size")) for x in blobs], dtype="float64"
                ),
                "ft": pd.Series(["file"] * len(blobs), dtype="string"),
            }
        )
        files_df["ext"] = pd.Series([_file_ext(n).lower() for n in names], dtype="string")
        files_df = _add_file_types(files_df, drop_ext=True)

    return {
        "gated": False,
        "reason": None,
        "files": files_df,
        "default_branch": default_branch,
        "license": license_id,
    }


def _filter_blobs(entries: Any) -> list[Any]:
    """``Filter(\\(x) x$type == "blob", entries)`` on parsed JSON tree entries.

    ``Filter()`` keeps ``x[which(unlist(lapply(x, f)))]``: an entry without a
    ``type`` gives ``logical(0)``, which ``unlist()`` drops, so the flags after
    it shift onto earlier entries, as in R.
    """
    if isinstance(entries, dict):
        entries = list(entries.values())
    flags = [t == "blob" for t in (_dollar(x, "type") for x in entries) if t is not None]
    return [entries[i] for i, keep in enumerate(flags) if keep is True]


def _empty_tree_files() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo": pd.Series([], dtype="string"),
            "clean_repo": pd.Series([], dtype="string"),
            "name": pd.Series([], dtype="string"),
            "path": pd.Series([], dtype="string"),
            "download_url": pd.Series([], dtype="string"),
            "size": pd.Series([], dtype="float64"),
            "type": pd.Series([], dtype="string"),
        }
    )


def _empty_or(x: Any, y: Any = None) -> Any:
    """metacheck's ``x %empty_or% y``: *y* when *x* is ``NULL`` or zero-length."""
    if x is None or (isinstance(x, list | tuple | dict) and len(x) == 0):
        return y
    return x


def _vapply_num(x: Any) -> float | None:
    """``vapply(..., \\(x) x %empty_or% NA_real_, numeric(1))`` for one value."""
    x = _empty_or(x)
    if x is None:
        return None
    if isinstance(x, list | dict):
        raise ValueError("values must be length 1")
    if isinstance(x, str):
        raise TypeError("values must be type 'double', but FUN(X[[1]]) result is type 'character'")
    return float(x)


def _num(x: Any) -> float | None:
    if x is None or isinstance(x, bool):
        return None if x is None else float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def github_info(repo: Any, recursive: bool = False) -> dict[str, Any] | None:
    """Port of R/archive-github.R::github_info(): everything about a repository.

    A dict with ``repo`` (the short name), ``readme``, ``files`` (see
    :func:`github_files`) and ``languages`` (see :func:`github_languages`);
    ``None`` when the repository does not exist.
    """
    clean = github_repo(repo)
    if clean is None:
        return None
    if isinstance(clean, list):
        # several repositories: R passes the vector on to each function
        return {
            "repo": clean,
            "readme": github_readme(clean),
            "files": github_files(clean, recursive=recursive),
            "languages": github_languages(clean),
        }
    # `clean` was just checked, so the per-function existence checks
    # (a HEAD request each in metacheck) are skipped
    return {
        "repo": clean,
        "readme": _github_readme(clean),
        "files": _github_files(clean, clean, "", recursive),
        "languages": _github_languages(clean),
    }
