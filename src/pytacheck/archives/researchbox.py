"""ResearchBox (port of ``R/archive-researchbox.R``).

ResearchBox has no API: :func:`rbox_info` scrapes a box's page for its file
names and the "Box public since" / "Box creators" / ... sections, and
:func:`rbox_file_download` asks the site to build a zip of the box's files
(the same POST the page's download button makes), then unzips it into the
repository file cache.
"""

from __future__ import annotations

import os
import warnings
from typing import Any

import pandas as pd

from pytacheck._r import is_na

__all__ = ["rbox_file_download", "rbox_info", "rbox_links"]


def _rbox_headers() -> dict[str, str]:
    """Port of R/archive-researchbox.R::.rbox_headers(): browser-like request headers.

    ResearchBox sits behind Cloudflare, which rejects httr2's default
    User-Agent.
    """
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
        "image/webp,*/*;q=0.8",
        "User-Agent": " ".join(
            [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "AppleWebKit/537.36 (KHTML, like Gecko)",
                "Chrome/137.0.0.0 Safari/537.36",
                "scienceverse/metacheck",
            ]
        ),
    }


def rbox_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::rbox_links(): ResearchBox links in papers.

    Hyperlinks from the paper's (or paper list's) ``url`` table mentioning
    ``researchbox.org``, plus bare ``researchbox.org/<id>`` mentions in the
    text. Trailing slashes are stripped, links to a file inside a box
    (``researchbox.org/2257.8``, ``researchbox.org/2257/72``) are cut back to
    the box, and duplicate rows dropped.
    """
    from pytacheck._r import sub
    from pytacheck.archives.dataone import _scan_links
    from pytacheck.archives.dataverse import _collect_links, _url_rows

    found_href = _url_rows(paper, r"researchbox\.org")
    other = _scan_links(paper, r"(?:https?://)?researchbox\.org/[0-9]+/?", ["researchbox.org/"])
    links = _collect_links([found_href, other])
    links["href"] = pd.Series(
        sub(
            r"(researchbox\.org/[0-9]+)(\.[0-9]+)?(/.*)?$",
            r"\1",
            links["href"].tolist(),
            ignore_case=True,
        ),
        dtype="string",
        index=links.index,
    )
    return links.drop_duplicates().reset_index(drop=True)


def rbox_info(rb_url: Any, id_col: int | str = 1, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::rbox_info(): look up ResearchBox boxes.

    *rb_url* is a box URL, a sequence of them, or a table whose *id_col*
    (1-based position or name) holds them (e.g. from :func:`rbox_links`).
    Each distinct URL is scraped in turn, stopping at the first that fails;
    the input rows are returned with ``files``, ``box_id``, ``reference``,
    ``RB_target``, ``RB_license``, ``RB_public``, ``RB_authors`` and
    ``RB_abstract`` (or ``error``) added.
    """
    from pytacheck.archives.psycharchives import _info_loop
    from pytacheck.utils import online

    if not online("researchbox.org"):
        raise ConnectionError("ResearchBox.org seems to be offline")
    return _info_loop(
        rb_url,
        id_col,
        pb,
        False,
        url_col="rb_url",
        label="ResearchBox",
        noun="file",
        cache_ns=None,
        one=_rbox_info,
        suffix=".rb",
    )


#: The page's section headings, in order (R: ``sections``).
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("RB_target", "SUPPLEMENTARY FILES FOR"),
    ("RB_license", "LICENSE FOR USE"),
    ("RB_public", "BOX PUBLIC SINCE"),
    ("RB_authors", "BOX CREATORS"),
    ("RB_abstract", "ABSTRACT"),
    ("done", "$('.file_number')"),
)

_REDIRECT = r"(?<=window\.location\.replace\(')https://researchbox.org/\d+(?='\))"


def _curl_url(url: str) -> str:
    """The URL libcurl requests for *url*: one without a scheme gets ``http://``.

    ``rbox_links()`` returns bare text mentions such as ``researchbox.org/801``,
    which httr2 hands to libcurl as they are; libcurl guesses the scheme.
    """
    from pytacheck._r import grepl

    if grepl("^[A-Za-z][A-Za-z0-9+.-]*://", url):
        return url
    return "http://" + url


def _get(url: str) -> Any:
    """``httr2::request(url) |> req_headers(<rbox headers>) |> req_error(FALSE) |> req_perform()``.

    A request that cannot be sent is an error, as in R.
    """
    from pytacheck import http

    resp = http.request("GET", _curl_url(url), headers=_rbox_headers(), max_tries=1)
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request: {url}")
    return resp


def _body_string(resp: Any) -> str | None:
    """``httr2::resp_body_string()`` (see :func:`pytacheck.archives.dataone._resp_body_string`)."""
    from pytacheck.archives.dataone import _resp_body_string

    return _resp_body_string(resp)


def _read_html(text: str | None) -> Any:
    """``xml2::read_html(text, encoding = "UTF-8")`` of a page body.

    libxml2's HTML parser with xml2's options (``RECOVER``, ``NOERROR``,
    ``NOBLANKS``, ``HUGE``), reading the string as UTF-8. ``NA`` is an error,
    and a string without ``<`` or ``>`` is taken as a file path by xml2: an
    error here.
    """
    from lxml import etree
    from lxml import html as lxml_html

    if text is None:
        raise ValueError("`x` must be a single string, not a character `NA`.")
    if "<" not in text and ">" not in text:
        raise FileNotFoundError(f"'{text}' does not exist in current working directory.")
    parser = lxml_html.HTMLParser(
        encoding="utf-8", recover=True, remove_blank_text=True, huge_tree=True, no_network=True
    )
    root = etree.fromstring(text.encode("utf-8", "surrogateescape"), parser)
    if root is None:
        raise ValueError("Document is empty")
    return root


def _rbox_info(rb_url: Any, pb: Any = None) -> pd.DataFrame:
    """Port of R/archive-researchbox.R::.rbox_info(): scrape one ResearchBox page.

    Follows the page's JavaScript redirect (peer-review links), then returns
    a one-row table: ``rb_url`` and either ``error`` (``"unfound"``, with a
    warning) or ``files`` (``name``, ``file_id``), ``box_id``,
    ``reference`` (what the zip download needs) and the ``RB_*`` sections of
    the page.
    """
    from pytacheck._r import regextract_all, strsplit, trimws
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.aspredicted import _html_text2
    from pytacheck.archives.dataverse import _cell, _paste
    from pytacheck.archives.psycharchives import _obj_cell

    with _spinner(pb) as bar:
        _tick(bar, f"* Retrieving info from {_paste(rb_url)}...")
        obj: dict[str, pd.Series] = {"rb_url": _cell(rb_url)}

        resp = _get(_paste(rb_url))
        body_text = _body_string(resp)
        # R: grepl(pattern, NA) is FALSE
        redirect = [] if body_text is None else regextract_all(_REDIRECT, body_text, perl=True)
        if redirect:
            if len(redirect) > 1:
                raise ValueError("`base_url` must be a single string, not a character vector.")
            resp = _get(redirect[0])

        if resp.status_code != 200:
            warnings.warn(f"{_paste(rb_url)} could not be found", stacklevel=2)
            obj["error"] = _cell("unfound")
            return pd.DataFrame(obj)

        doc = _read_html(_body_string(resp))

        file_names = [str(p.xpath("string()")) for p in doc.xpath("//p [@class='file_name']")]
        file_ids = [
            el.get("value")
            for el in doc.xpath("//input[@type='checkbox'][starts-with(@name, 'file')]")
        ]
        n = len(file_names)
        if len(file_ids) == n:
            ids: list[str | None] = file_ids
        elif n == 0:
            raise ValueError("arguments imply differing number of rows: 0, 1")
        else:
            ids = [None] * n
        obj["files"] = _obj_cell(
            pd.DataFrame(
                {
                    "name": pd.Series(file_names, dtype="string"),
                    "file_id": pd.Series(ids, dtype="string"),
                }
            )
        )

        def first_value(xpath: str) -> str | None:
            hits = doc.xpath(xpath)
            return hits[0].get("value") if hits else None

        obj["box_id"] = _cell(first_value("//input[@id='box_id']"))
        obj["reference"] = _cell(first_value("//input[@id='reference']"))

        bodies = [_html_text2(b) for b in doc.xpath("//body")]
        for i in range(5):
            name, start = _SECTIONS[i]
            end = _SECTIONS[i + 1][1]
            value: str | None = None
            if bodies:
                pieces = strsplit(bodies[0], start, fixed=True)
                if len(pieces) >= 2:
                    rest = strsplit(pieces[1], end, fixed=True)
                    if rest:
                        value = trimws(rest[0])
            obj[name] = _cell(value)
        return pd.DataFrame(obj)


def _cache_subdir(rb_url: str) -> str:
    """``.repo_cache_subdir(rb_url)`` (R/repo-download.R)."""
    try:
        from pytacheck.archives.download import _repo_cache_subdir
    except ImportError:  # the repo-download port may not be available yet
        _repo_cache_subdir = None
    if _repo_cache_subdir is not None:
        return str(_repo_cache_subdir(rb_url))
    from pytacheck._r import gsub
    from pytacheck.archives.cache import _metacheck_cache_subdir
    from pytacheck.utils import get_option

    key = gsub("^https?://", "", rb_url)
    key = gsub("[^A-Za-z0-9._-]+", "_", key)
    key = gsub("^_+|_+$", "", key)
    if not key:
        key = "unknown"
    root = _metacheck_cache_subdir(
        ".metacheck_repo_cache", override=get_option("metacheck.repo_cache.dir")
    )
    return f"{root}/{key}"


def _list_files(root: str) -> list[str]:
    """``list.files(root, recursive = TRUE)``: relative paths of files, sorted as R does
    (locale collation, which R does with ICU).

    As in R (``all.files = FALSE``), files and directories whose names start
    with ``.`` are left out (a zip made on a Mac holds ``.DS_Store`` and
    ``__MACOSX/._*`` entries).
    """
    from pytacheck._r import r_sorted

    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            rel = os.path.relpath(os.path.join(d, f), root).replace(os.sep, "/")
            out.append(rel)
    return list(r_sorted(out))


_RB_EMPTY = {
    "rb_url": "string",
    "name": "string",
    "file_location": "string",
    "size": "float64",
    "isdir": "boolean",
}


def rbox_file_download(rb_url: Any, pb: Any = None) -> pd.DataFrame | None:
    """Port of R/archive-researchbox.R::rbox_file_download(): download a box's files.

    Asks ResearchBox to build a zip of the box's files, saves it in the
    repository file cache (reused on later calls) and unzips it there.
    Returns one row per file (``rb_url``, ``name``, ``file_location``,
    ``size``, ``isdir``, ``ext``, ``type``), or ``None`` (with a warning)
    when the box cannot be downloaded. A sequence of URLs gives one table,
    aligned with the input.
    """
    from pytacheck._r import bind_rows
    from pytacheck.archives import _spinner, _tick
    from pytacheck.archives.dataverse import _paste
    from pytacheck.archives.psycharchives import _add_ext_type, _url_values
    from pytacheck.utils import left_join

    urls = _url_values(rb_url)
    with _spinner(pb) as bar:
        if len(urls) > 1:
            unique_rb = [u for u in dict.fromkeys(urls) if u is not None]
            file_lists = [rbox_file_download(u, pb=bar) for u in unique_rb]
            info = bind_rows(file_lists)
            orig = pd.DataFrame({"rb_url": pd.Series(urls, dtype="string")})
            if "rb_url" not in info.columns:
                raise ValueError(
                    "Join columns in `y` must be present in the data.\n✖ Problem with `rb_url`."
                )
            return left_join(orig, info, by="rb_url")

        if not urls:
            raise ValueError("argument is of length zero")
        url = urls[0]
        _tick(bar, f"* Retrieving files from {_paste(url)}...")

        tmp_dir = _cache_subdir(_paste(url))
        os.makedirs(tmp_dir, exist_ok=True)
        zip_path = f"{tmp_dir}/archive.zip"
        out_dir = f"{tmp_dir}/unzipped"

        already = os.path.isdir(out_dir) and len(_list_files(out_dir)) > 0
        if not already:
            info = _rbox_info(url, pb=bar)
            if "error" in info.columns:
                return None  # .rbox_info() already warned
            files = info["files"].iloc[0]
            file_ids = [v for v in files["file_id"].tolist() if not is_na(v)]
            box_id = info["box_id"].iloc[0]
            reference = info["reference"].iloc[0]
            if not file_ids or is_na(box_id) or is_na(reference):
                warnings.warn(f"Could not find downloadable files for: {url}", stacklevel=2)
                return None

            _tick(bar, f"Downloading to: {zip_path}")
            status = _download_zip(file_ids, str(box_id), str(reference), zip_path)
            if status != 200 or not os.path.exists(zip_path) or os.path.getsize(zip_path) == 0:
                warnings.warn(
                    f"Download failed or resulted in an empty file: {zip_path}", stacklevel=2
                )
                return None

            os.makedirs(out_dir, exist_ok=True)
            _tick(bar, f"Unzipping into: {out_dir}")
            _unzip(zip_path, out_dir)

        files_rel = _list_files(out_dir)
        if not files_rel:
            warnings.warn("Unzip produced no files. The archive might be corrupt.", stacklevel=2)
            return None

        locations = [f"{out_dir}/{f}" for f in files_rel]
        df = pd.DataFrame(
            {
                "rb_url": pd.Series([url] * len(files_rel), dtype="string"),
                "name": pd.Series(files_rel, dtype="string"),
                "file_location": pd.Series(locations, dtype="string"),
                "size": pd.Series([float(os.path.getsize(p)) for p in locations], dtype="float64"),
                "isdir": pd.Series([os.path.isdir(p) for p in locations], dtype="boolean"),
            }
        )
        return _add_ext_type(df)


def _download_zip(file_ids: list[Any], box_id: str, reference: str, path: str) -> int | None:
    """POST ``download_files.php`` for a zip of *file_ids*, streamed to *path*.

    Returns the HTTP status (``None`` when the request failed; R: ``NA``).
    """
    import json

    import httpx

    from pytacheck import http
    from pytacheck.archives.dataverse import _as_numeric

    # R: req_body_json(list(files = as.numeric(file_ids), ...)) -- jsonlite writes whole
    # doubles without a decimal point, NA / NaN / Inf as the strings "NA", "NaN",
    # "Inf" / "-Inf", and unboxes a length-one vector
    nums: list[Any] = []
    for v in file_ids:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # R warns "NAs introduced by coercion" here too
            x = _as_numeric(v)
        if x != x:
            nan = isinstance(v, str) and v.strip().lower() == "nan"
            nums.append("NaN" if nan else "NA")
        elif x in (float("inf"), float("-inf")):
            nums.append("Inf" if x > 0 else "-Inf")
        else:
            nums.append(int(x) if float(x).is_integer() else x)
    body = {
        "files": nums[0] if len(nums) == 1 else nums,
        "box_id": box_id,
        "reference": reference,
    }
    headers = dict(_rbox_headers())
    headers["Content-Type"] = "application/json"
    try:
        with http.client().stream(
            "POST",
            "https://researchbox.org/download_files.php",
            headers=headers,
            content=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        ) as resp:
            with open(path, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
            return int(resp.status_code)
    except (httpx.HTTPError, httpx.StreamError, httpx.InvalidURL, OSError):
        return None


def _unzip(zip_path: str, exdir: str) -> None:
    """``utils::unzip(zip_path, exdir = exdir)``, refusing members that would escape *exdir*."""
    import zipfile

    try:
        with zipfile.ZipFile(zip_path) as zf:
            base = os.path.realpath(exdir)
            for member in zf.infolist():
                target = os.path.realpath(os.path.join(exdir, member.filename))
                if target != base and not target.startswith(base + os.sep):
                    continue  # divergence: R would write outside exdir
                zf.extract(member, exdir)
    except zipfile.BadZipFile:
        warnings.warn("error 1 in extracting from zip file", stacklevel=3)
