"""``metacheck::file_types``: file extension -> coarse file type.

Port of ``data/file_types.rda`` (built upstream by ``data-raw/file_types.R`` from
the dyne/file-extension-list JSON plus metacheck's own additions). The table
below is **generated from R**, not edited by hand: run
``python tests/fileinfo/gen_file_types.py`` (needs ``PYTACHECK_RSCRIPT``) to
rewrite it from ``metacheck::file_types`` at the pinned commit, and
``tests/fileinfo/test_types.py`` checks it against R when R is available.

The table has two columns, ``ext`` (lower case, without the leading dot; some
are compound, e.g. ``fasta.gz``) and ``type`` (``code``, ``data``, ``stats``,
``archive``, ``text``, ...), sorted by ``ext`` as in R. An extension can have
several rows (``json`` is both ``code`` and ``data``).
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["file_types"]

# BEGIN GENERATED: metacheck::file_types as "ext:type" tokens, in row order
_FILE_TYPES = """
1.ada:code 2.ada:code 3dm:image 3ds:3D 3ds:image 3g2:video 3gp:video 3mf:3D 7z:archive
a:archive aac:audio aaf:video aar:archive abw:text ada:code adb:code ado:code ads:code ai:image
aif:audio aiff:audio amr:audio ape:audio apk:archive ar:archive arff:data arw:image asf:video
asm:code asp:code asp:web aspx:code aspx:web au:audio avchd:video avi:video avif:image azw:book
azw1:book azw3:book azw4:book azw6:book bak:config bas:code bash:code bash:exec bat:code
bat:exec bin:exec bmp:image br:archive bz2:archive c:code c++:code cab:archive car:video
cbl:code cbr:book cbz:book cc:code cfg:config class:code clj:code cmake:config cmd:exec
cob:code com:exec command:exec config:config cpio:archive cpp:code cr2:image cr3:image crx:exec
cs:code csh:code csh:exec css:web csv:data csv.sav:data cxx:code d:code dart:code dat:data
dav:video dds:image deb:archive dft:data diff:code dll:code dmg:archive do:stats doc:text
docx:text drc:video dss:code dta:data dwg:image dxf:image e:code ebook:text egg:archive el:code
env:config eot:font eps:image epub:book exe:exec f:code f3d:3D f77:code f90:code fa.gz:data
fasta.gz:data fastq.gz:data feather:data fish:code fish:exec flac:audio flv:video for:code
fq.gz:data fth:code ftn:code gcode:3D gdt:data gdtb:data gen:data geojson:data gif:image
git:config gitignore:config go:code gpx:image gradle:code groovy:code gsm:audio gz:archive
h:code heic:image heif:image hevc:video hh:code hpp:code hs:code htm:code htm:web html:code
html:web hxx:code ico:image ics:data inc:code inc:web ini:config ipynb:code iso:archive
it:audio jar:archive jasp:data jasp:stats java:code jl:code jp2:image jpeg:image jpg:image
js:code js:web json:code json:data jsp:code jsp:web jsx:code jsx:web jxl:image kml:image
kmz:image ksh:code ksh:exec kt:code kts:code less:web lha:archive lhs:code lisp:code
lock:config log:text lua:code lz:archive lz4:archive lzma:archive lzo:archive lzop:archive
m:code m2ts:video m2v:video m3u:audio m4:code m4a:audio m4p:video m4v:video make:config
mar:archive mat:data max:image md:text mid:audio mk:config mka:audio mkv:video mng:video
mobi:book mod:audio mov:video mp2:video mp3:audio mp4:video mpa:audio mpe:video mpeg:video
mpg:video mpv:video msg:text msi:exec mts:video mxf:video ndjson:data nef:image nim:code
nsv:video obj:3D odf:text odg:text odp:slide ods:data odt:text ogg:audio ogm:video ogv:video
ogx:video old:config omv:data opus:audio orc:data org:text orig:config otf:font pages:text
pak:archive parquet:data patch:code pdf:text pea:archive pfb:font pfm:font php:code php:web
php3:code php3:web php4:code php4:web php5:code php5:web phtml:code phtml:web pl:code pls:audio
png:image po:code por:data por:stats pp:code ppt:slide pptx:slide prql:code ps:image ps1:code
ps1xml:code psb:image psc1:code psd:image psd1:code psm1:code psrc:code pssc:code py:code
qmd:code qt:video quarto:code r:code ra:audio rar:archive raw:image rb:code rd:code rda:data
rdata:data rds:code rds:data rm:video rmd:code rmvb:video rnw:code roq:video rpm:archive
rproj:config rs:code rst:text rtf:text rtx:text s:code s3m:audio s7z:archive sas:stats
sas7bdat:data sav:data sav.gz:data scad:3D scala:code scss:web sd7:data sh:code sh:exec
shar:archive sid:audio smt:3D sol:code spo:stats sps:stats spss:stats spv:stats sql:code
srt:video step:3D stl:3D stp:3D svelte:code svg:image svi:video swg:code swift:code swp:config
sz:archive tar:archive tbz2:archive tex:text tga:image tgz:archive thm:image tif:image
tiff:image tlz:archive tmp:config toml:config ts:web ts:code tsv:data tsx:web ttf:font txt:text
txz:archive v:code vb:code vcf:data vcxproj:code vob:video vue:code war:archive wasm:web
wav:audio webm:video webp:image wf1:data whl:archive wll:code wma:audio wmv:video woff:font
woff2:font wpd:text wps:text xba:video xcf:image xcodeproj:code xll:code xls:data xlsx:data
xm:audio xml:code xpi:archive xpt:data xz:archive yaml:config yml:config yuv:image yuv:video
z:archive zig:code zip:archive zipx:archive zsav:data zsh:code zsh:exec zst:archive
"""
# END GENERATED


@functools.cache
def _pairs() -> tuple[tuple[str, str], ...]:
    """The rows of ``metacheck::file_types`` as ``(ext, type)`` pairs, in order."""
    out = []
    for tok in _FILE_TYPES.split():
        ext, _, typ = tok.rpartition(":")
        out.append((ext, typ))
    return tuple(out)


@functools.cache
def _frame() -> pd.DataFrame:
    import pandas as pd

    pairs = _pairs()
    return pd.DataFrame(
        {
            "ext": pd.Series([p[0] for p in pairs], dtype="string"),
            "type": pd.Series([p[1] for p in pairs], dtype="string"),
        }
    )


def file_types() -> pd.DataFrame:
    """``metacheck::file_types`` (``data/file_types.rda``) as a data frame.

    Columns ``ext`` and ``type`` (both ``string``), one row per extension/type
    pair, sorted by ``ext``. A fresh copy is returned, so callers may modify it.
    """
    return _frame().copy()


@functools.cache
def ext_types() -> dict[str, str]:
    """Extension -> its types pasted with ``";"`` in table order (``json`` -> ``"code;data"``).

    This is ``left_join(ext, file_types, by = "ext")`` followed by
    ``summarise(type = paste(type, collapse = ";"))``, as in :func:`filetype`.
    """
    out: dict[str, list[str]] = {}
    for ext, typ in _pairs():
        out.setdefault(ext, []).append(typ)
    return {ext: ";".join(types) for ext, types in out.items()}


@functools.cache
def ext_rows() -> dict[str, tuple[tuple[int, str], ...]]:
    """Extension -> its ``(row index, type)`` pairs (0-based rows of the table)."""
    out: dict[str, list[tuple[int, str]]] = {}
    for i, (ext, typ) in enumerate(_pairs()):
        out.setdefault(ext, []).append((i, typ))
    return {ext: tuple(rows) for ext, rows in out.items()}
