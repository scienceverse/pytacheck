"""Helpers of the Data Check module (port of ``inst/modules/data_check.R``).

The module body lives in :mod:`pytacheck.modules.data_check`; this file holds
its local functions -- the ones defined inside ``data_check()`` (the file tree
and report tabsets) and the ``.dv_*`` data-validation helpers defined after
it (careless responding, Qualtrics metadata, the "Issues Identified" cell and
the spreadsheet-formatting inspectors) -- with R's names, ``.`` -> ``_``.
"""

from __future__ import annotations

import functools
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pandas as pd

from pytacheck._r import grepl, plural, r_round, r_sort_key, strsplit, sub

# -----------------------------------------------------------------------------
# small R idioms
# -----------------------------------------------------------------------------


def _na(x: Any) -> bool:
    """R ``is.na()`` of one value."""
    if x is None or x is pd.NA:
        return True
    try:
        return bool(x != x)
    except (TypeError, ValueError):
        return False


def _col(df: pd.DataFrame | None, name: str) -> list[Any] | None:
    """``df$name`` as a plain list (``None`` values for NA), ``None`` when absent."""
    if df is None or name not in df.columns:
        return None
    return [None if _na(v) else v for v in _first_col(df, name).tolist()]


def _first_col(df: pd.DataFrame, name: str) -> pd.Series:
    """``df[[name]]``: the first column called *name* (R's lookup with duplicates)."""
    pos = [j for j, c in enumerate(df.columns) if c == name]
    return df.iloc[:, pos[0]]


def _is_true(x: Any) -> bool:
    """R ``isTRUE()``: a single ``TRUE`` (Python or numpy bool)."""
    import numpy as np

    return isinstance(x, bool | np.bool_) and bool(x)


def _is_false(x: Any) -> bool:
    """R ``isFALSE()``: a single ``FALSE`` (Python or numpy bool)."""
    import numpy as np

    return isinstance(x, bool | np.bool_) and not bool(x)


def _nzchar(x: Any) -> bool:
    """R ``nzchar()``: ``NA`` is ``TRUE``."""
    return x is None or str(x) != ""


def _has_value(x: Any) -> bool:
    """``!is.na(x) & nzchar(x)``."""
    return x is not None and str(x) != ""


def _file_exists(path: Any) -> bool:
    import os

    return path is not None and str(path) != "" and os.path.exists(str(path))


def _file_ext(x: Any) -> str | None:
    """``tolower(tools::file_ext(x))`` (``NA`` for ``NA``)."""
    from pytacheck.datacheck.files import _file_ext as fe

    if x is None:
        return None
    e = fe(str(x))
    return "" if e is None else e.lower()


def _dquote_free(x: Any) -> str:
    """``sprintf("%s", x)``: ``NA`` prints as ``"NA"``."""
    from pytacheck._r import as_character

    if x is None:
        return "NA"
    if isinstance(x, str):
        return x
    s = as_character(x)
    return "NA" if s is None else s


def _chr(x: Any) -> list[str | None]:
    """R ``as.character()`` of a column."""
    from pytacheck.datacheck._checks_rvec import chr as r_chr

    return r_chr(x)


def _is_integer64(df: pd.DataFrame, j: int) -> bool:
    """Whether column *j* carries bit64's ``integer64`` class (``df.attrs["col_attrs"]``)."""
    from pytacheck.datacheck._colattrs import col_attrs_at

    cls = col_attrs_at(df, j).get("class")
    cls = [cls] if isinstance(cls, str) else list(cls or [])
    return "integer64" in cls


def col_chr(df: pd.DataFrame, j: int) -> list[str | None]:
    """R ``as.character(df[[j]])``.

    An ``integer64`` column (fread's type for integers beyond 32 bits) prints
    every value as an exact integer (bit64's ``as.character.integer64()``:
    ``"100000"``, never ``"1e+05"``), unlike a double column.
    """
    col = df.iloc[:, j]
    if _is_integer64(df, j):
        return [None if _na(v) else str(int(v)) for v in col.tolist()]
    return _chr(col)


def _setdiff(x: Sequence[Any], y: Sequence[Any]) -> list[Any]:
    """R ``setdiff()`` (``unique(x[match(x, y, 0) == 0])``)."""
    ys = set(y)
    out: list[Any] = []
    seen: set[Any] = set()
    for v in x:
        if v in ys or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _unique(x: Sequence[Any]) -> list[Any]:
    return list(dict.fromkeys(x))


def _r_order_chr(values: Sequence[Any]) -> list[int]:
    """R ``order()`` of a character vector (ICU collation, stable, NA last)."""
    idx = list(range(len(values)))
    return sorted(idx, key=lambda i: (values[i] is None, r_sort_key(values[i] or "")))


def _c_key(s: Any) -> tuple[int, str]:
    """dplyr's C-locale key (``arrange()``/``count()``), NA last."""
    return (1, "") if s is None else (0, str(s))


# -----------------------------------------------------------------------------
# display width (R's format() pads by display columns)
# -----------------------------------------------------------------------------


#: The width R 4.5's ``format()`` gives each character when it pads a
#: string (C ``Rstrwid()``): a printable character counts its display width
#: (R's own ``rlocale`` tables, not the C library's), a non-printable one the
#: width of its escape (``\t``: 2, ``\001``: 4, ``\u0085``: 6,
#: ``\U000e0080``: 10), a backslash 2. Stored as the code-point ranges whose
#: width is not 1, ``<first>[-<last>]`` in hex plus a width code
#: (:data:`_WIDTH_CODE`). Generated by padding every code point with
#: ``formatC(width = -20)``; Python's ``unicodedata`` differs from R's tables
#: on ~500 code points (control characters, the soft hyphen, newer emoji, ...).
_R_WIDTHS = (
    "1-6o 7-dw e-1fo 5cw 7fo 80-9fu 300-36fz 378-379u 380-383u 38bu 38du 3a2u 483-489z 530u "
    "557-558u 58b-58cu 590u 591-5bdz 5bfz 5c1-5c2z 5c4-5c5z 5c7z 5c8-5cfu 5eb-5eeu 5f5-5ffu "
    "600-605z 610-61az 61cz 64b-65fz 670z 6d6-6ddz 6df-6e4z 6e7-6e8z 6ea-6edz 70eu 70fz 711z "
    "730-74az 74b-74cu 7a6-7b0z 7b2-7bfu 7eb-7f3z 7fb-7fcu 7fdz 816-819z 81b-823z 825-827z "
    "829-82dz 82e-82fu 83fu 859-85bz 85c-85du 85fu 86b-86fu 88fu 892-897u 8d3-902z 93az 93cz "
    "941-948z 94dz 951-957z 962-963z 981z 984u 98d-98eu 991-992u 9a9u 9b1u 9b3-9b5u 9ba-9bbu "
    "9bcz 9c1-9c4z 9c5-9c6u 9c9-9cau 9cdz 9cf-9d6u 9d8-9dbu 9deu 9e2-9e3z 9e4-9e5u 9fez "
    "9ff-a00u a01-a02z a04u a0b-a0eu a11-a12u a29u a31u a34u a37u a3a-a3bu a3cz a3du a41-a42z "
    "a43-a46u a47-a48z a49-a4au a4b-a4dz a4e-a50u a51z a52-a58u a5du a5f-a65u a70-a71z a75z "
    "a77-a80u a81-a82z a84u a8eu a92u aa9u ab1u ab4u aba-abbu abcz ac1-ac5z ac6u ac7-ac8z "
    "acau acdz ace-acfu ad1-adfu ae2-ae3z ae4-ae5u af2-af8u afa-affz b00u b01z b04u b0d-b0eu "
    "b11-b12u b29u b31u b34u b3a-b3bu b3cz b3fz b41-b44z b45-b46u b49-b4au b4dz b4e-b54u "
    "b55-b56z b58-b5bu b5eu b62-b63z b64-b65u b78-b81u b82z b84u b8b-b8du b91u b96-b98u b9bu "
    "b9du ba0-ba2u ba5-ba7u bab-badu bba-bbdu bc0z bc3-bc5u bc9u bcdz bce-bcfu bd1-bd6u "
    "bd8-be5u bfb-bffu c00z c04z c0du c11u c29u c3a-c3bu c3e-c40z c45u c46-c48z c49u c4a-c4dz "
    "c4e-c54u c55-c56z c57u c5b-c5cu c5e-c5fu c62-c63z c64-c65u c70-c76u c81z c8du c91u ca9u "
    "cb4u cba-cbbu cbcz cbfz cc5u cc6z cc9u ccc-ccdz cce-cd4u cd7-cdcu cdfu ce2-ce3z ce4-ce5u "
    "cf0u cf4-cffu d00-d01z d0du d11u d3b-d3cz d41-d44z d45u d49u d4dz d50-d53u d62-d63z "
    "d64-d65u d80u d81z d84u d97-d99u db2u dbcu dbe-dbfu dc7-dc9u dcaz dcb-dceu dd2-dd4z dd5u "
    "dd6z dd7u de0-de5u df0-df1u df5-e00u e31z e34-e3az e3b-e3eu e47-e4ez e5c-e80u e83u e85u "
    "e8bu ea4u ea6u eb1z eb4-ebcz ebe-ebfu ec5u ec7u ec8-ecdz ecfu eda-edbu ee0-effu f18-f19z "
    "f35z f37z f39z f48u f6d-f70u f71-f7ez f80-f84z f86-f87z f8d-f97z f98u f99-fbcz fbdu fc6z "
    "fcdu fdb-fffu 102d-1030z 1032-1037z 1039-103az 103d-103ez 1058-1059z 105e-1060z "
    "1071-1074z 1082z 1085-1086z 108dz 109dz 10c6u 10c8-10ccu 10ce-10cfu 1100-115fw "
    "1160-11ffz 1249u 124e-124fu 1257u 1259u 125e-125fu 1289u 128e-128fu 12b1u 12b6-12b7u "
    "12bfu 12c1u 12c6-12c7u 12d7u 1311u 1316-1317u 135b-135cu 135d-135fz 137d-137fu "
    "139a-139fu 13f6-13f7u 13fe-13ffu 169d-169fu 16f9-16ffu 1712-1714z 1716-171eu 1732-1734z "
    "1737-173fu 1752-1753z 1754-175fu 176du 1771u 1772-1773z 1774-177fu 17b4-17b5z 17b7-17bdz "
    "17c6z 17c9-17d3z 17ddz 17de-17dfu 17ea-17efu 17fa-17ffu 180b-180ez 181a-181fu 1879-187fu "
    "1885-1886z 18a9z 18ab-18afu 18f6-18ffu 191fu 1920-1922z 1927-1928z 192c-192fu 1932z "
    "1939-193bz 193c-193fu 1941-1943u 196e-196fu 1975-197fu 19ac-19afu 19ca-19cfu 19db-19ddu "
    "1a17-1a18z 1a1bz 1a1c-1a1du 1a56z 1a58-1a5ez 1a5fu 1a60z 1a62z 1a65-1a6cz 1a73-1a7cz "
    "1a7d-1a7eu 1a7fz 1a8a-1a8fu 1a9a-1a9fu 1aae-1aafu 1ab0-1ac0z 1acf-1affu 1b00-1b03z 1b34z "
    "1b36-1b3az 1b3cz 1b42z 1b4d-1b4fu 1b6b-1b73z 1b7fu 1b80-1b81z 1ba2-1ba5z 1ba8-1ba9z "
    "1bab-1badz 1be6z 1be8-1be9z 1bedz 1bef-1bf1z 1bf4-1bfbu 1c2c-1c33z 1c36-1c37z 1c38-1c3au "
    "1c4a-1c4cu 1c89-1c8fu 1cbb-1cbcu 1cc8-1ccfu 1cd0-1cd2z 1cd4-1ce0z 1ce2-1ce8z 1cedz 1cf4z "
    "1cf8-1cf9z 1cfb-1cffu 1dc0-1df9z 1dfb-1dffz 1f16-1f17u 1f1e-1f1fu 1f46-1f47u 1f4e-1f4fu "
    "1f58u 1f5au 1f5cu 1f5eu 1f7e-1f7fu 1fb5u 1fc5u 1fd4-1fd5u 1fdcu 1ff0-1ff1u 1ff5u 1fffu "
    "200b-200fz 2028-2029u 202a-202ez 2060-2064z 2065u 2066-206fz 2072-2073u 208fu 209d-209fu "
    "20c1-20cfu 20d0-20f0z 20f1-20ffu 218c-218fu 231a-231bw 2329-232aw 23e9-23ecw 23f0w 23f3w "
    "2427-243fu 244b-245fu 25fd-25few 2614-2615w 2648-2653w 267fw 2693w 26a1w 26aa-26abw "
    "26bd-26bew 26c4-26c5w 26cew 26d4w 26eaw 26f2-26f3w 26f5w 26faw 26fdw 2705w 270a-270bw "
    "2728w 274cw 274ew 2753-2755w 2757w 2795-2797w 27b0w 27bfw 2b1b-2b1cw 2b50w 2b55w "
    "2b74-2b75u 2b96u 2cef-2cf1z 2cf4-2cf8u 2d26u 2d28-2d2cu 2d2e-2d2fu 2d68-2d6eu 2d71-2d7eu "
    "2d7fz 2d97-2d9fu 2da7u 2dafu 2db7u 2dbfu 2dc7u 2dcfu 2dd7u 2ddfu 2de0-2dffz 2e5e-2e7fu "
    "2e80-2e99w 2e9au 2e9b-2ef3w 2ef4-2effu 2f00-2fd5w 2fd6-2fefu 2ff0-2ffbw 3000-3029w "
    "302a-302dz 302e-303ew 3040u 3041-3096w 3097-3098u 3099-309az 309b-30ffw 3100-3104u "
    "3105-312fw 3130u 3131-318ew 318fu 3190-31e3w 31e4-31eeu 31f0-321ew 321fu 3220-3247w "
    "3250-4dbfw 4e00-a48cw a48d-a48fu a490-a4c6w a4c7-a4cfu a62c-a63fu a66f-a672z a674-a67dz "
    "a69e-a69fz a6f0-a6f1z a6f8-a6ffu a7cb-a7cfu a7d2u a7d4u a7da-a7f1u a802z a806z a80bz "
    "a825-a826z a82cz a82d-a82fu a83a-a83fu a878-a87fu a8c4-a8c5z a8c6-a8cdu a8da-a8dfu "
    "a8e0-a8f1z a8ffz a926-a92dz a947-a951z a954-a95eu a960-a97cw a97d-a97fu a980-a982z a9b3z "
    "a9b6-a9b9z a9bc-a9bdz a9ceu a9da-a9ddu a9e5z a9ffu aa29-aa2ez aa31-aa32z aa35-aa36z "
    "aa37-aa3fu aa43z aa4cz aa4e-aa4fu aa5a-aa5bu aa7cz aab0z aab2-aab4z aab7-aab8z "
    "aabe-aabfz aac1z aac3-aadau aaec-aaedz aaf6z aaf7-ab00u ab07-ab08u ab0f-ab10u ab17-ab1fu "
    "ab27u ab2fu ab6c-ab6fu abe5z abe8z abedz abee-abefu abfa-abffu ac00-d7a3w d7a4-d7afu "
    "d7c7-d7cau d7fc-d7ffu f900-fa6dw fa6e-fa6fu fa70-fad9w fada-faffu fb07-fb12u fb18-fb1cu "
    "fb1ez fb37u fb3du fb3fu fb42u fb45u fbc3-fbd2u fd90-fd91u fdc8-fdceu fdd0-fdefu "
    "fe00-fe0fz fe10-fe19w fe1a-fe1fu fe20-fe2fz fe30-fe52w fe53u fe54-fe66w fe67u fe68-fe6bw "
    "fe6c-fe6fu fe75u fefd-fefeu feffz ff00u ff01-ff60w ffbf-ffc1u ffc8-ffc9u ffd0-ffd1u "
    "ffd8-ffd9u ffdd-ffdfu ffe0-ffe6w ffe7u ffef-fff8u fff9-fffbz fffe-ffffx 1000cU 10027U "
    "1003bU 1003eU 1004e-1004fU 1005e-1007fU 100fb-100ffU 10103-10106U 10134-10136U 1018fU "
    "1019d-1019fU 101a1-101cfU 101fdz 101fe-1027fU 1029d-1029fU 102d1-102dfU 102e0z "
    "102fc-102ffU 10324-1032cU 1034b-1034fU 10376-1037az 1037b-1037fU 1039eU 103c4-103c7U "
    "103d6-103ffU 1049e-1049fU 104aa-104afU 104d4-104d7U 104fc-104ffU 10528-1052fU "
    "10564-1056eU 1057bU 1058bU 10593U 10596U 105a2U 105b2U 105baU 105bd-105ffU 10737-1073fU "
    "10756-1075fU 10768-1077fU 10786U 107b1U 107bb-107ffU 10806-10807U 10809U 10836U "
    "10839-1083bU 1083d-1083eU 10856U 1089f-108a6U 108b0-108dfU 108f3U 108f6-108faU "
    "1091c-1091eU 1093a-1093eU 10940-1097fU 109b8-109bbU 109d0-109d1U 10a01-10a03z 10a04U "
    "10a05-10a06z 10a07-10a0bU 10a0c-10a0fz 10a14U 10a18U 10a36-10a37U 10a38-10a3az "
    "10a3b-10a3eU 10a3fz 10a49-10a4fU 10a59-10a5fU 10aa0-10abfU 10ae5-10ae6z 10ae7-10aeaU "
    "10af7-10affU 10b36-10b38U 10b56-10b57U 10b73-10b77U 10b92-10b98U 10b9d-10ba8U "
    "10bb0-10bffU 10c49-10c7fU 10cb3-10cbfU 10cf3-10cf9U 10d24-10d27z 10d28-10d2fU "
    "10d3a-10e5fU 10e7fU 10eaaU 10eab-10eacz 10eae-10eafU 10eb2-10efcU 10f28-10f2fU "
    "10f46-10f50z 10f5a-10f6fU 10f8a-10fafU 10fcc-10fdfU 10ff7-10fffU 11001z 11038-11046z "
    "1104e-11051U 11076-1107eU 1107f-11081z 110b3-110b6z 110b9-110baz 110bdz 110c3-110ccU "
    "110cdz 110ce-110cfU 110e9-110efU 110fa-110ffU 11100-11102z 11127-1112bz 1112d-11134z "
    "11135U 11148-1114fU 11173z 11177-1117fU 11180-11181z 111b6-111bez 111c9-111ccz 111cfz "
    "111e0U 111f5-111ffU 11212U 1122f-11231z 11234z 11236-11237z 1123ez 11242-1127fU 11287U "
    "11289U 1128eU 1129eU 112aa-112afU 112dfz 112e3-112eaz 112eb-112efU 112fa-112ffU "
    "11300-11301z 11304U 1130d-1130eU 11311-11312U 11329U 11331U 11334U 1133aU 1133b-1133cz "
    "11340z 11345-11346U 11349-1134aU 1134e-1134fU 11351-11356U 11358-1135cU 11364-11365U "
    "11366-1136cz 1136d-1136fU 11370-11374z 11375-113ffU 11438-1143fz 11442-11444z 11446z "
    "1145cU 1145ez 11462-1147fU 114b3-114b8z 114baz 114bf-114c0z 114c2-114c3z 114c8-114cfU "
    "114da-1157fU 115b2-115b5z 115b6-115b7U 115bc-115bdz 115bf-115c0z 115dc-115ddz "
    "115de-115ffU 11633-1163az 1163dz 1163f-11640z 11645-1164fU 1165a-1165fU 1166d-1167fU "
    "116abz 116adz 116b0-116b5z 116b7z 116ba-116bfU 116ca-116ffU 1171b-1171cU 1171d-1171fz "
    "11722-11725z 11727-1172bz 1172c-1172fU 11747-117ffU 1182f-11837z 11839-1183az "
    "1183c-1189fU 118f3-118feU 11907-11908U 1190a-1190bU 11914U 11917U 11936U 11939-1193aU "
    "1193b-1193cz 1193ez 11943z 11947-1194fU 1195a-1199fU 119a8-119a9U 119d4-119d7z "
    "119d8-119d9U 119da-119dbz 119e0z 119e5-119ffU 11a01-11a0az 11a33-11a38z 11a3b-11a3ez "
    "11a47z 11a48-11a4fU 11a51-11a56z 11a59-11a5bz 11a8a-11a96z 11a98-11a99z 11aa3-11aafU "
    "11af9-11affU 11b0a-11bffU 11c09U 11c30-11c36z 11c37U 11c38-11c3dz 11c3fz 11c46-11c4fU "
    "11c6d-11c6fU 11c90-11c91U 11c92-11ca7z 11ca8U 11caa-11cb0z 11cb2-11cb3z 11cb5-11cb6z "
    "11cb7-11cffU 11d07U 11d0aU 11d31-11d36z 11d37-11d39U 11d3az 11d3bU 11d3c-11d3dz 11d3eU "
    "11d3f-11d45z 11d47z 11d48-11d4fU 11d5a-11d5fU 11d66U 11d69U 11d8fU 11d90-11d91z 11d92U "
    "11d95z 11d97z 11d99-11d9fU 11daa-11edfU 11ef3-11ef4z 11ef9-11effU 11f11U 11f3b-11f3dU "
    "11f5a-11fafU 11fb1-11fbfU 11ff2-11ffeU 1239a-123ffU 1246fU 12475-1247fU 12544-12f8fU "
    "12ff3-12fffU 13430-13438z 13456-143ffU 14647-167ffU 16a39-16a3fU 16a5fU 16a6a-16a6dU "
    "16abfU 16aca-16acfU 16aee-16aefU 16af0-16af4z 16af6-16affU 16b30-16b36z 16b46-16b4fU "
    "16b5aU 16b62U 16b78-16b7cU 16b90-16e3fU 16e9b-16effU 16f4b-16f4eU 16f4fz 16f88-16f8eU "
    "16f8f-16f92z 16fa0-16fdfU 16fe0-16fe3w 16fe4z 16fe5-16fefU 16ff0-16ff1w 16ff2-16fffU "
    "17000-187f7w 187f8-187ffU 18800-18cd5w 18cd6-18cffU 18d00-18d08w 18d09-1afefU 1aff4U "
    "1affcU 1afffU 1b000-1b11ew 1b123-1b131U 1b133-1b14fU 1b150-1b152w 1b153-1b154U "
    "1b156-1b163U 1b164-1b167w 1b168-1b16fU 1b170-1b2fbw 1b2fc-1bbffU 1bc6b-1bc6fU "
    "1bc7d-1bc7fU 1bc89-1bc8fU 1bc9a-1bc9bU 1bc9d-1bc9ez 1bca0-1bca3z 1bca4-1ceffU "
    "1cf2e-1cf2fU 1cf47-1cf4fU 1cfc4-1cfffU 1d0f6-1d0ffU 1d127-1d128U 1d167-1d169z "
    "1d173-1d182z 1d185-1d18bz 1d1aa-1d1adz 1d1eb-1d1ffU 1d242-1d244z 1d246-1d2bfU "
    "1d2d4-1d2dfU 1d2f4-1d2ffU 1d357-1d35fU 1d379-1d3ffU 1d455U 1d49dU 1d4a0-1d4a1U "
    "1d4a3-1d4a4U 1d4a7-1d4a8U 1d4adU 1d4baU 1d4bcU 1d4c4U 1d506U 1d50b-1d50cU 1d515U 1d51dU "
    "1d53aU 1d53fU 1d545U 1d547-1d549U 1d551U 1d6a6-1d6a7U 1d7cc-1d7cdU 1da00-1da36z "
    "1da3b-1da6cz 1da75z 1da84z 1da8c-1da9aU 1da9b-1da9fz 1daa0U 1daa1-1daafz 1dab0-1deffU "
    "1df1f-1df24U 1df2b-1dfffU 1e000-1e006z 1e007U 1e008-1e018z 1e019-1e01aU 1e01b-1e021z "
    "1e022U 1e023-1e024z 1e025U 1e026-1e02az 1e02b-1e02fU 1e06e-1e08eU 1e090-1e0ffU "
    "1e12d-1e12fU 1e130-1e136z 1e13e-1e13fU 1e14a-1e14dU 1e150-1e28fU 1e2af-1e2bfU "
    "1e2ec-1e2efz 1e2fa-1e2feU 1e300-1e4cfU 1e4fa-1e7dfU 1e7e7U 1e7ecU 1e7efU 1e7ffU "
    "1e8c5-1e8c6U 1e8d0-1e8d6z 1e8d7-1e8ffU 1e944-1e94az 1e94c-1e94fU 1e95a-1e95dU "
    "1e960-1ec70U 1ecb5-1ed00U 1ed3e-1edffU 1ee04U 1ee20U 1ee23U 1ee25-1ee26U 1ee28U 1ee33U "
    "1ee38U 1ee3aU 1ee3c-1ee41U 1ee43-1ee46U 1ee48U 1ee4aU 1ee4cU 1ee50U 1ee53U 1ee55-1ee56U "
    "1ee58U 1ee5aU 1ee5cU 1ee5eU 1ee60U 1ee63U 1ee65-1ee66U 1ee6bU 1ee73U 1ee78U 1ee7dU "
    "1ee7fU 1ee8aU 1ee9c-1eea0U 1eea4U 1eeaaU 1eebc-1eeefU 1eef2-1efffU 1f004w 1f02c-1f02fU "
    "1f094-1f09fU 1f0af-1f0b0U 1f0c0U 1f0cfw 1f0d0U 1f0f6-1f0ffU 1f18ew 1f191-1f19aw "
    "1f1ae-1f1e5U 1f200-1f202w 1f203-1f20fU 1f210-1f23bw 1f23c-1f23fU 1f240-1f248w "
    "1f249-1f24fU 1f250-1f251w 1f252-1f25fU 1f260-1f265w 1f266-1f2ffU 1f300-1f320w "
    "1f32d-1f335w 1f337-1f37cw 1f37e-1f393w 1f3a0-1f3caw 1f3cf-1f3d3w 1f3e0-1f3f0w 1f3f4w "
    "1f3f8-1f43ew 1f440w 1f442-1f4fcw 1f4ff-1f53dw 1f54b-1f54ew 1f550-1f567w 1f57aw "
    "1f595-1f596w 1f5a4w 1f5fb-1f64fw 1f680-1f6c5w 1f6ccw 1f6d0-1f6d2w 1f6d5-1f6d7w "
    "1f6d8-1f6dbU 1f6eb-1f6ecw 1f6ed-1f6efU 1f6f4-1f6fcw 1f6fd-1f6ffU 1f777-1f77aU "
    "1f7da-1f7dfU 1f7e0-1f7ebw 1f7ec-1f7efU 1f7f1-1f7ffU 1f80c-1f80fU 1f848-1f84fU "
    "1f85a-1f85fU 1f888-1f88fU 1f8ae-1f8afU 1f8b2-1f8ffU 1f90c-1f93aw 1f93c-1f945w "
    "1f947-1f978w 1f97a-1f9cbw 1f9cd-1f9ffw 1fa54-1fa5fU 1fa6e-1fa6fU 1fa70-1fa74w "
    "1fa78-1fa7aw 1fa7d-1fa7fU 1fa80-1fa86w 1fa89-1fa8fU 1fa90-1faa8w 1fab0-1fab6w 1fabeU "
    "1fac0-1fac2w 1fac6-1facdU 1fad0-1fad6w 1fadc-1fadfU 1fae9-1faefU 1faf9-1faffU 1fb93U "
    "1fbcb-1fbefU 1fbfa-1ffffU 20000-2a6dfw 2a6e0-2a6ffU 2a700-2b739w 2b73a-2b73fU "
    "2b740-2b81dw 2b81e-2b81fU 2b820-2cea1w 2cea2-2ceafU 2ceb0-2ebe0w 2ebe1-2ebefU "
    "2ebf0-2ee5dw 2ee5e-2f7ffU 2f800-2fa1dw 2fa1e-2ffffU 30000-3134aw 3134b-3134fU "
    "31350-323afw 323b0-e0000U e0001z e0002-e001fU e0020-e007fz e0080-e00ffU e0100-e01efz "
    "e01f0-effffU ffffe-fffffU 10fffe-10ffffU "
)
_WIDTH_CODE = {"z": 0, "w": 2, "o": 4, "u": 6, "U": 10, "x": 12}


@functools.cache
def _r_width_table() -> tuple[list[int], list[int], list[int]]:
    starts: list[int] = []
    ends: list[int] = []
    widths: list[int] = []
    for tok in "".join(_R_WIDTHS).split():
        rng, w = tok[:-1], tok[-1]
        first, _, last = rng.partition("-")
        starts.append(int(first, 16))
        ends.append(int(last or first, 16))
        widths.append(_WIDTH_CODE[w])
    return starts, ends, widths


def _char_width(c: str) -> int:
    """The width R's ``format()`` counts for one character (see :data:`_R_WIDTHS`)."""
    import bisect

    o = ord(c)
    if o == 0:
        return 0
    starts, ends, widths = _r_width_table()
    k = bisect.bisect_right(starts, o) - 1
    return widths[k] if k >= 0 and o <= ends[k] else 1


def _disp_width(s: str) -> int:
    """The width R's ``format()`` pads *s* to (C ``Rstrwid()``, see :func:`_char_width`)."""
    return sum(_char_width(c) for c in s)


def _pad(x: list[str], extra: int = 2) -> list[str]:
    """``formatC(x, width = -(max(nchar(x)) + extra), flag = " ")``.

    ``formatC()`` of a character vector is ``format(x, width, justify =
    "left")``, which pads every element to the same *display* width: the
    larger of the requested width (counted in characters) and the widest
    element (counted as :func:`_disp_width` does: an emoji two columns, a
    tab two, ...).
    """
    w = max([len(s) for s in x] + [0]) + extra
    target = max([w] + [_disp_width(s) for s in x])
    return [s + " " * (target - _disp_width(s)) for s in x]


# -----------------------------------------------------------------------------
# data tree
# -----------------------------------------------------------------------------

_TREE_ICONS = {
    "data": "\U0001f4ca",  # 📊
    "code": "\U0001f4bb",  # 💻
    "documentation": "\U0001f4d6",  # 📖
    "materials": "\U0001f4c1",  # 📁
    "output": "\U0001f4c8",  # 📈
}
_README_ICON = "\U0001f4c4"  # 📄
_UNKNOWN_ICON = "❓"  # ❓


def _tree_type_icon(data_type: Any, doc_role: Any) -> str | None:
    """Port of ``.tree_type_icon()``: the icon of the "Classified as" column."""
    if doc_role is not None and doc_role == "readme":
        return _README_ICON
    if data_type is None:
        return None
    return _TREE_ICONS.get(data_type, _UNKNOWN_ICON)


def _tree_type_label(data_type: Any, doc_role: Any) -> str | None:
    """Port of ``.tree_type_label()``: ``readme`` for a readme, else the data type."""
    if doc_role is not None and doc_role == "readme":
        return "readme"
    return None if data_type is None else str(data_type)


def repo_tree_rows(paths: Sequence[Any]) -> pd.DataFrame:
    """Walk relative paths into tree rows (port of ``repo_tree_rows()``).

    Returns ``text`` (the tree line) and ``leaf_idx`` (the 0-based index into
    *paths* of the file a row is the leaf of; ``None`` for a folder row).
    """
    keep = [p is not None and str(p) != "" for p in paths]
    kept = [str(p) for p, k in zip(paths, keep, strict=True) if k]
    orig_idx = [i for i, k in enumerate(keep) if k]
    if not kept:
        return pd.DataFrame(
            {"text": pd.Series([], dtype="string"), "leaf_idx": pd.Series([], dtype="Int64")}
        )
    split = strsplit([p.replace("\\", "/") for p in kept], "/")
    parts_list = [[s for s in parts if s != ""] for parts in split]
    out_text: list[str] = []
    out_leaf: list[int | None] = []

    def walk(parts_subset: list[list[str]], idx_subset: list[int], prefix: str = "") -> None:
        # the paths under each head, heads in first-appearance order (unique(heads))
        groups: dict[str, list[int]] = {}
        for k, p in enumerate(parts_subset):
            groups.setdefault(p[0], []).append(k)
        has_child = {h: any(len(parts_subset[k]) > 1 for k in ks) for h, ks in groups.items()}
        # order(!has_child, tolower(head_order)): folders first, then by name (stable)
        head_order = sorted(groups, key=lambda h: (not has_child[h], r_sort_key(h.lower())))
        for i, head in enumerate(head_order):
            ks = groups[head]
            tails = [parts_subset[k][1:] for k in ks]
            tail_idx = [idx_subset[k] for k in ks]
            is_last = i == len(head_order) - 1
            child_exists = any(len(t) > 0 for t in tails)
            branch = "└── " if is_last else "├── "
            next_prefix = prefix + ("    " if is_last else "│   ")
            out_text.append(prefix + branch + head + ("/" if child_exists else ""))
            out_leaf.append(None if child_exists else tail_idx[0])
            next_subset = [t for t in tails if len(t) > 0]
            next_idx = [x for t, x in zip(tails, tail_idx, strict=True) if len(t) > 0]
            if next_subset:
                walk(next_subset, next_idx, next_prefix)

    walk(parts_list, orig_idx)
    return pd.DataFrame(
        {
            "text": pd.Series(out_text, dtype="string"),
            "leaf_idx": pd.Series(out_leaf, dtype="Int64"),
        }
    )


def repo_tree_block(
    files: pd.DataFrame | None, naming_issues: pd.DataFrame | None = None
) -> list[str] | None:
    """The "Data Tree" report section (port of ``repo_tree_block()``).

    One preformatted tree per repository: path | classification icon + type |
    study | naming issue (the first ``repo_check`` naming finding of the
    file). ``None`` when there are no files or no repository URLs.
    """
    if files is None or len(files) == 0:
        return None
    repo_col = _col(files, "repo_url")
    if repo_col is None:
        return None
    repo_urls = _unique([r for r in repo_col if r is not None and str(r) != ""])
    if not repo_urls:
        return None

    ni_names = _col(naming_issues, "file_name") if naming_issues is not None else None
    ni_detail = _col(naming_issues, "detail") if naming_issues is not None else None

    def naming_of(file_names: list[Any]) -> list[Any]:
        if naming_issues is None or len(naming_issues) == 0 or ni_names is None:
            return [None] * len(file_names)
        first: dict[Any, int] = {}
        for k, nm in enumerate(ni_names):
            if nm is not None and nm not in first:
                first[nm] = k
        out = []
        for nm in file_names:
            hit = first.get(nm) if nm is not None else None
            out.append(None if hit is None or ni_detail is None else ni_detail[hit])
        return out

    names_all = _col(files, "file_name") or [None] * len(files)
    path_all = _col(files, "file_path")
    type_all = _col(files, "data_type")
    role_all = _col(files, "doc_role")
    group_all = _col(files, "group")

    blocks = []
    for repo in repo_urls:
        rows_i = [i for i, r in enumerate(repo_col) if r is not None and r == repo]
        rel_paths = [(path_all if path_all is not None else names_all)[i] for i in rows_i]
        rows = repo_tree_rows(rel_paths)
        if len(rows) == 0:
            continue
        data_type = [type_all[i] if type_all is not None else None for i in rows_i]
        doc_role = [role_all[i] if role_all is not None else None for i in rows_i]
        group = [group_all[i] if group_all is not None else None for i in rows_i]
        naming = naming_of([names_all[i] for i in rows_i])

        texts = [str(t) for t in rows["text"].tolist()]
        leaves = [None if _na(v) else int(v) for v in rows["leaf_idx"].tolist()]
        type_txt, study_txt, naming_txt = [], [], []
        for leaf in leaves:
            if leaf is None:
                type_txt.append("")
                study_txt.append("")
                naming_txt.append("")
                continue
            icon = _tree_type_icon(data_type[leaf], doc_role[leaf])
            label = _tree_type_label(data_type[leaf], doc_role[leaf])
            type_txt.append(f"{_dquote_free(icon)} {_dquote_free(label)}")
            study_txt.append("" if group[leaf] is None else str(group[leaf]))
            naming_txt.append("" if naming[leaf] is None else str(naming[leaf]))
        has_study = any(s != "" for s in study_txt)
        has_naming = any(s != "" for s in naming_txt)
        p_text = _pad(texts)
        p_type = _pad(type_txt)
        p_study = _pad(study_txt) if has_study else [""] * len(texts)
        lines = [
            a + b + c + (d if has_naming else "")
            for a, b, c, d in zip(p_text, p_type, p_study, naming_txt, strict=True)
        ]
        lines = sub(r"\s+$", "", lines)
        blocks.append(
            {"repo": repo, "lines": lines, "has_study": has_study, "has_naming": has_naming}
        )
    if not blocks:
        return None

    sections: list[str] = []
    for b in blocks:
        sections.append(f"**Repository: {b['repo']}**")
        sections.append("\n".join(["```", *b["lines"], "```"]))
    legend = (
        "Each file is followed by how it was classified"
        + (", which study it belongs to" if any(b["has_study"] for b in blocks) else "")
        + (
            ", and any file-naming issue found by repo_check"
            if any(b["has_naming"] for b in blocks)
            else ""
        )
        + "."
    )
    return [
        "#### Data Tree",
        f"Where the files sit within each repository. {legend}",
        *sections,
    ]


# -----------------------------------------------------------------------------
# report tabsets
# -----------------------------------------------------------------------------


def file_tabset(files: Sequence[str], table_fun: Callable[[str], list[Any]]) -> list[Any] | None:
    """A Quarto ``.panel-tabset`` with one tab per file (port of ``file_tabset()``).

    R pastes the tabs into one string; here the tabset is a list of report
    blocks (fences, a ``## file`` heading and the tab's blocks), which joined
    with blank lines is R's text. ``None`` for no files.
    """
    if len(files) == 0:
        return None
    blocks: list[Any] = ["::: {.panel-tabset}"]
    for f in files:
        blocks.append(f"## {f}")
        blocks.extend(table_fun(f))
    blocks.append(":::")
    return blocks


def desc_file_tabset(desc: pd.DataFrame) -> list[Any] | None:
    """One descriptives table per source file (port of ``desc_file_tabset()``)."""
    from pytacheck.report import scroll_table

    src = [None if _na(v) else v for v in desc["source_file"].tolist()]
    rest = [j for j, c in enumerate(desc.columns) if c != "source_file"]
    rows_of: dict[Any, list[int]] = {}
    for i, s in enumerate(src):
        rows_of.setdefault(s, []).append(i)

    def table_fun(f: str) -> list[Any]:
        rows = rows_of[f]
        return [scroll_table(desc.iloc[rows, rest].reset_index(drop=True), maxrows=25)]

    return file_tabset(list(rows_of), table_fun)


def data_file_tabset(
    previews: Mapping[str, pd.DataFrame], preview_rows: int = 100
) -> list[Any] | None:
    """One raw-data preview per file (port of ``data_file_tabset()``)."""
    from pytacheck.report import scroll_table

    def table_fun(f: str) -> list[Any]:
        df = previews[f]
        n_full = len(df)
        shown = df.head(preview_rows).reset_index(drop=True)
        out: list[Any] = [scroll_table(shown, maxrows=10)]
        if n_full > preview_rows:
            out.append(f"*Showing the first {preview_rows:d} of {n_full:d} rows.*")
        return out

    return file_tabset(list(previews), table_fun)


# -----------------------------------------------------------------------------
# careless responding
# -----------------------------------------------------------------------------

#: careless screening needs at least this many items / respondents
DV_CARELESS_MIN_ITEMS = 5
DV_CARELESS_MIN_ROWS = 30
#: a block of at most this many items is "short" (straightlining is weak evidence)
DV_SHORT_SCALE_MAX = 7


def _longstring(rows: Sequence[Sequence[float | None]]) -> list[float]:
    """``careless::longstring(x)``: each row's longest run of identical answers.

    ``rle()`` breaks a run at every ``NA`` (``NA != x`` is ``NA``).
    """
    out: list[float] = []
    for row in rows:
        best = 0
        run = 0
        prev: float | None = None
        for k, v in enumerate(row):
            if k == 0 or v is None or prev is None or v != prev:
                run = 1
            else:
                run += 1
            best = max(best, run)
            prev = v
        out.append(float(best))
    return out


def _irv(rows: Sequence[Sequence[float | None]]) -> list[float]:
    """``careless::irv(x, na.rm = TRUE)``: each row's SD (``NA`` below 2 values)."""
    import numpy as np

    from pytacheck.datacheck.columns import _r_var

    out: list[float] = []
    for row in rows:
        vals = np.array([v for v in row if v is not None and v == v], dtype="float64")
        out.append(math.sqrt(_r_var(vals)) if vals.size > 1 else math.nan)
    return out


def _careless_available() -> bool:
    """Whether careless-responding indices can be computed.

    metacheck needs the suggested ``careless`` package; pytacheck ports its
    ``longstring()`` and ``irv()``, so they are always available unless the
    option ``pytacheck.careless`` is ``FALSE`` (metacheck without
    ``careless`` installed).
    """
    from pytacheck.utils import get_option

    return bool(get_option("pytacheck.careless", True))


def _as_num_chr(x: Any) -> list[float | None]:
    """``suppressWarnings(as.numeric(as.character(x)))``."""
    from pytacheck.datacheck._checks_rvec import as_numeric_str

    out: list[float | None] = []
    for s in _chr(x):
        f = None if s is None else as_numeric_str(s)
        out.append(None if f is None or f != f else f)
    return out


def dv_careless_block(
    block: pd.DataFrame, ids: Sequence[Any], scale: str, prefix: Any
) -> pd.DataFrame | None:
    """Respondents of one scale block that straightline (port of ``.dv_careless_block()``).

    A respondent is flagged when the same answer runs for at least 80% of the
    block's items (and at least 5); the IRV is returned for context only.
    """
    cols = [_as_num_chr(block.iloc[:, j]) for j in range(block.shape[1])]
    n_items = len(cols)
    rows = [[c[i] for c in cols] for i in range(len(block))]
    ls = _longstring(rows)
    iv = _irv(rows)
    straight_cut = max(5, math.ceil(0.8 * n_items))
    flagged = [i for i, v in enumerate(ls) if v >= straight_cut]
    if not flagged:
        return None
    n = len(flagged)
    label = f"{_dquote_free(prefix)} ({scale}, {n_items} items)"
    return pd.DataFrame(
        {
            "scale": pd.Series([label] * n, dtype="string"),
            "respondent": pd.Series([ids[i] for i in flagged], dtype="string"),
            "longstring": pd.Series([ls[i] for i in flagged], dtype="float64"),
            "irv": pd.Series([r_round(iv[i], 2) for i in flagged], dtype="float64"),
            "reason": pd.Series(["straightlining"] * n, dtype="string"),
            "n_items": pd.Series([n_items] * n, dtype="Int64"),
            "straight_cut": pd.Series([float(straight_cut)] * n, dtype="float64"),
            "scale_range": pd.Series([scale] * n, dtype="string"),
        }
    )


def dv_careless_coverage_text(n_scored: int, n_skipped: int) -> str:
    """What the careless screen does not cover (port of ``.dv_careless_coverage_text()``)."""
    scope = (
        f"Only {n_scored:d} of {n_scored + n_skipped:d} data file{plural(n_scored + n_skipped)} "
        "could be screened: the rest had no detectable multi-item scale block, or too few "
        "respondents. "
        if n_skipped > 0
        else ""
    )
    return (
        "**What this does not cover.** "
        + scope
        + "Even in a screened file, these checks only look at multi-item rating scales — the "
        "only place where a repeated or flat answer pattern is interpretable. Single "
        "questions, open text, demographics and any item outside a detected scale block are "
        "never examined. A respondent can therefore answer most of a survey carelessly and "
        "still not be flagged here, and finding nothing is not evidence that a dataset is "
        "free of careless responding."
    )


def dv_careless_threshold_text(row: Mapping[str, Any] | None) -> str | None:
    """The rule a flagged respondent crossed (port of ``.dv_careless_threshold_text()``)."""
    if row is None:
        return None
    return (
        f"same answer {int(row['longstring']):d} times in a row, out of "
        f"{int(row['n_items']):d} items (flagged at {int(row['straight_cut']):d})"
    )


def dv_scale_n_items(scale_label: Sequence[Any]) -> list[int | None]:
    """Item count of a ``"prefix (min-max, N items)"`` label (port of ``.dv_scale_n_items()``)."""
    from pytacheck._r import regextract

    m = regextract("([0-9]+) items", list(scale_label))
    out: list[int | None] = []
    for s in m:
        if s is None:
            out.append(None)
            continue
        try:
            out.append(int(sub(" items$", "", s)))
        except ValueError:
            out.append(None)
    return out


_CARELESS_COLS = (
    "respondent",
    "source_file",
    "n_blocks_flagged",
    "scales",
    "reasons",
    "max_longstring",
    "irv",
    "short_scale_only",
    "threshold",
)


def dv_careless_by_respondent(
    block_df: pd.DataFrame | None,
    previews: Any = None,  # noqa: ARG001 - R's signature (unused there too)
    columns_df: Any = None,  # noqa: ARG001
) -> pd.DataFrame:
    """One row per flagged respondent (port of ``.dv_careless_by_respondent()``)."""
    if block_df is None or len(block_df) == 0:
        return pd.DataFrame({c: pd.Series([], dtype="boolean") for c in _CARELESS_COLS})
    scale = _col(block_df, "scale") or []
    reason = _col(block_df, "reason") or []
    n_items = dv_scale_n_items(scale)
    is_short = [
        grepl("straightlin", r if r is not None else "")
        and r is not None
        and not grepl("IRV", r)
        and k is not None
        and k <= DV_SHORT_SCALE_MAX
        for r, k in zip(reason, n_items, strict=True)
    ]
    resp = _col(block_df, "respondent") or []
    src = _col(block_df, "source_file") or []
    ls = _col(block_df, "longstring") or []
    irv = _col(block_df, "irv") or []
    nit = _col(block_df, "n_items") or []
    cut = _col(block_df, "straight_cut") or []
    groups: dict[str, list[int]] = {}
    for i, r in enumerate(resp):
        if r is None:  # split() drops NA
            continue
        groups.setdefault(str(r), []).append(i)
    rows: list[dict[str, Any]] = []
    for key in sorted(groups, key=r_sort_key):
        g = groups[key]
        g_ls = [ls[i] for i in g]
        best = max((v for v in g_ls if v is not None), default=None)
        i_max = next(i for i in g if ls[i] == best) if best is not None else g[0]
        irvs = [irv[i] for i in g if irv[i] is not None and not _na(irv[i])]
        rows.append(
            {
                "respondent": key,
                "source_file": "; ".join(
                    sorted(_unique([src[i] for i in g if src[i] is not None]), key=r_sort_key)
                ),
                "n_blocks_flagged": len(g),
                "scales": "; ".join(
                    sorted(_unique([scale[i] for i in g if scale[i] is not None]), key=r_sort_key)
                ),
                "reasons": "; ".join(
                    sorted(_unique([reason[i] for i in g if reason[i] is not None]), key=r_sort_key)
                ),
                "max_longstring": best if best is not None else -math.inf,
                "irv": min(irvs) if irvs else math.inf,
                "short_scale_only": all(is_short[i] for i in g),
                "threshold": dv_careless_threshold_text(
                    {"longstring": ls[i_max], "n_items": nit[i_max], "straight_cut": cut[i_max]}
                ),
            }
        )
    rows.sort(key=lambda r: (-r["n_blocks_flagged"], -r["max_longstring"]))
    return pd.DataFrame(
        {
            "respondent": pd.Series([r["respondent"] for r in rows], dtype="string"),
            "source_file": pd.Series([r["source_file"] for r in rows], dtype="string"),
            "n_blocks_flagged": pd.Series([r["n_blocks_flagged"] for r in rows], dtype="Int64"),
            "scales": pd.Series([r["scales"] for r in rows], dtype="string"),
            "reasons": pd.Series([r["reasons"] for r in rows], dtype="string"),
            "max_longstring": pd.Series([r["max_longstring"] for r in rows], dtype="float64"),
            "irv": pd.Series([r["irv"] for r in rows], dtype="float64"),
            "short_scale_only": pd.Series([r["short_scale_only"] for r in rows], dtype="boolean"),
            "threshold": pd.Series([r["threshold"] for r in rows], dtype="string"),
        }
    )


# -----------------------------------------------------------------------------
# Qualtrics metadata
# -----------------------------------------------------------------------------


def dv_q_col(df: pd.DataFrame, tag: str) -> pd.Series | None:
    """The first column carrying a Qualtrics metadata *tag* (port of ``.dv_q_col()``)."""
    from pytacheck.datacheck.checks import _qualtrics_tag_cols

    tags = _qualtrics_tag_cols([str(c) for c in df.columns])
    for j, t in enumerate(tags):
        if t == tag:
            return df.iloc[:, j]
    return None


def _parse_posix(s: str | None, fmt: str) -> Any:
    """``as.POSIXct(s, tz = "UTC", format = fmt)`` of one string (``None`` for NA)."""
    import datetime as dt

    from pytacheck.datacheck._checks_facets import _strptime_ok

    if s is None or not _strptime_ok(s, fmt):
        return None
    from pytacheck.datacheck._checks_facets import _format_regex

    rx, fields = _format_regex(fmt)
    m = rx.match(s)
    vals = dict(zip(fields, (int(g) for g in m.groups()), strict=True))  # type: ignore[union-attr]
    hour = vals.get("H", 0)
    base = dt.datetime(vals["Y"], vals["m"], vals["d"], 0, vals.get("M", 0), 0)
    return base + dt.timedelta(hours=hour, seconds=vals.get("S", 0))


def dv_q_datetime(x: Any) -> list[Any] | None:
    """Parse a Qualtrics datetime column (port of ``.dv_q_datetime()``).

    ``"%Y-%m-%d %H:%M:%S"`` first, then ``"%Y-%m-%d"``; ``None`` for values
    that parse under neither (and for a missing column).
    """
    if x is None:
        return None
    xc = _chr(x)
    out: list[Any] = [None] * len(xc)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        miss = [i for i, v in enumerate(out) if v is None]
        if not miss:
            break
        for i in miss:
            out[i] = _parse_posix(xc[i], fmt)
    return out


def _r_median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return math.nan
    h = n // 2
    return s[h] if n % 2 else (s[h - 1] + s[h]) / 2


_QUALTRICS_PII = {
    "qualtrics_ip": "IP address",
    "qualtrics_email": "email",
    "qualtrics_lat": "location",
    "qualtrics_lon": "location",
    "qualtrics_externalref": "external reference (e.g. panel ID)",
    "qualtrics_recipient": "recipient name",
}


def dv_qualtrics_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Summarise one Qualtrics export's response metadata (port of ``.dv_qualtrics_summary()``)."""
    from pytacheck.datacheck.checks import _qualtrics_tag_cols

    n_rows = len(df)
    status = dv_q_col(df, "qualtrics_status")
    finished = dv_q_col(df, "qualtrics_finished")
    progress = dv_q_col(df, "qualtrics_progress")
    drop = [False] * n_rows
    if status is not None:
        s = _as_num_chr(status)
        st = [None if v is None else v.strip(" \t\r\n").lower() for v in _chr(status)]
        prev = grepl("preview|spam", [v if v is not None else "" for v in st])
        drop = [
            d or (a is not None and a in (1, 2, 8)) or (b is not None and bool(p))
            for d, a, b, p in zip(drop, s, st, prev, strict=True)
        ]
    if finished is not None:
        f = [None if v is None else v.strip(" \t\r\n").lower() for v in _chr(finished)]
        drop = [d or (v in ("0", "false", "no")) for d, v in zip(drop, f, strict=True)]
    if progress is not None:
        p = _as_num_chr(progress)
        drop = [d or (v is not None and v < 100) for d, v in zip(drop, p, strict=True)]
    n_drop = sum(drop)

    dur_col = dv_q_col(df, "qualtrics_duration")
    dur: list[float | None] = _as_num_chr(dur_col) if dur_col is not None else []
    if all(v is None for v in dur):
        sd = dv_q_datetime(dv_q_col(df, "qualtrics_start"))
        ed = dv_q_datetime(dv_q_col(df, "qualtrics_end"))
        if sd is not None and ed is not None:
            dur = [
                None if a is None or b is None else (b - a).total_seconds()
                for a, b in zip(sd, ed, strict=True)
            ]
    durs = [v for v in dur if v is not None and v >= 0]
    median_seconds = _r_median(durs) if durs else math.nan
    n_fast: int | None = None
    if len(durs) >= 10 and not math.isnan(median_seconds):
        cut = min(0.5 * median_seconds, 120)
        n_fast = sum(1 for v in durs if v < cut)

    dtv = dv_q_datetime(dv_q_col(df, "qualtrics_recorded"))
    if dtv is None or all(v is None for v in dtv):
        dtv = dv_q_datetime(dv_q_col(df, "qualtrics_start"))
    date_min = date_max = None
    if dtv is not None and any(v is not None for v in dtv):
        ok = [v for v in dtv if v is not None]
        date_min = min(ok).strftime("%Y-%m-%d")
        date_max = max(ok).strftime("%Y-%m-%d")

    tags = _qualtrics_tag_cols([str(c) for c in df.columns])
    present_tags = _unique([t for t in tags if t is not None and t in _QUALTRICS_PII])
    present = _unique([_QUALTRICS_PII[t] for t in present_tags])
    return {
        "n_rows": n_rows,
        "n_drop": n_drop,
        "median_seconds": median_seconds,
        "n_fast": n_fast,
        "date_min": date_min,
        "date_max": date_max,
        "pii_fields": present,
    }


def _fmt_dur(sec: float) -> str:
    if sec is None or math.isnan(sec):
        return "—"
    if sec < 90:
        return f"{sec:.0f} s"
    if sec < 5400:
        return f"{sec / 60:.1f} min"
    return f"{sec / 3600:.1f} h"


def dv_qualtrics_report(specs: Sequence[Mapping[str, Any]], n_previews: int) -> list[Any]:
    """The "Qualtrics Survey Metadata" section (port of ``.dv_qualtrics_report()``)."""
    from pytacheck.report import scroll_table

    tbl = pd.DataFrame(
        {
            "File": pd.Series([s["source_file"] for s in specs], dtype="string"),
            "Responses": pd.Series([s["n_rows"] for s in specs], dtype="Int64"),
            "Preview/unfinished": pd.Series([s["n_drop"] for s in specs], dtype="Int64"),
            "Median time": pd.Series(
                [_fmt_dur(s["median_seconds"]) for s in specs], dtype="string"
            ),
            "Very fast": pd.Series(
                ["—" if s["n_fast"] is None else str(s["n_fast"]) for s in specs], dtype="string"
            ),
            "Collected": pd.Series(
                [
                    "—"
                    if s["date_min"] is None
                    else s["date_min"]
                    if s["date_min"] == s["date_max"]
                    else f"{s['date_min']} to {s['date_max']}"
                    for s in specs
                ],
                dtype="string",
            ),
            "PII fields": pd.Series(
                [", ".join(s["pii_fields"]) if s["pii_fields"] else "none" for s in specs],
                dtype="string",
            ),
        }
    )
    n_files = len(specs)
    total_drop = sum(int(s["n_drop"]) for s in specs)
    intro = (
        f"{n_files:d} of the {n_previews:d} data file{plural(n_previews)} "
        f"{'is' if n_files == 1 else 'are'} a Qualtrics survey export. The table below "
        "summarises the response metadata Qualtrics records for every survey (not the "
        "substantive question columns): how many rows look like previews or unfinished "
        "responses that usually need dropping before analysis, the typical completion time "
        "(with a count of implausibly fast responses), the data-collection window, and which "
        "Qualtrics fields carry personal information to review before sharing."
    )
    note = (
        f" Across these files, {total_drop:d} row{plural(total_drop)} "
        f"{'is' if total_drop == 1 else 'are'} flagged as a preview, spam, or unfinished "
        "response — check whether they should be excluded."
        if total_drop > 0
        else ""
    )
    return ["#### Qualtrics Survey Metadata", intro + note, scroll_table(tbl, maxrows=20)]


# -----------------------------------------------------------------------------
# "Issues Identified" cell
# -----------------------------------------------------------------------------

DV_ISSUE_ICON = {
    "Values outside the scale": "\U0001f4c8",
    "Miscoded missing": "❓",
    "Constant": "\U0001f7f0",
    "Empty column": "⬜",
    "SPSS filter variable": "\U0001f9ee",
    "Case issues": "\U0001f524",
    "Whitespace": "↔️",
    "Numeric as text": "#️⃣",
    "Problematic column name": "\U0001f3f7️",
    "Colliding column names": "\U0001f465",
    "Mixed encoding": "\U0001f523",
    "Personal info (values)": "\U0001f512",
    "Personal info (column name)": "\U0001f512",
    "Geographic coordinates": "\U0001f30d",
    "Free-text (may hold PII)": "\U0001f512",
    "Not a rectangular dataset": "\U0001f4d0",
    "Header not on first row": "\U0001f4cb",
    "Un-inspectable (.xls)": "⚠️",
    "Unreadable": "⚠️",
    "Colour coding": "\U0001f3a8",
    "Merged cells": "\U0001f517",
    "Empty rows": "⬜",
    "Empty or unnamed columns": "⬜",
}

DV_ISSUE_MERGE_LABEL = {
    "Personal info (values)": "Personally Identifying Information",
    "Personal info (column name)": "Personally Identifying Information",
    "Free-text (may hold PII)": "Personally Identifying Information",
}


def dv_issue_cell(check: Sequence[Any], detail: Sequence[Any]) -> str:
    """The "Issues" cell of one (file, column) group (port of ``.dv_issue_cell()``).

    One line per issue label (the PII checks merged into one), each an icon
    and short label in a ``<span>`` whose ``title`` holds the full details.
    """
    from pytacheck.stats.helpers import _stat_html_escape

    display = [DV_ISSUE_MERGE_LABEL.get(c, c) if c is not None else None for c in check]
    groups: dict[Any, list[int]] = {}
    for i, lbl in enumerate(display):
        if lbl is None:  # split() drops NA
            continue
        groups.setdefault(lbl, []).append(i)
    lines = []
    for lbl in sorted(groups, key=r_sort_key):
        idx = groups[lbl]
        icon = DV_ISSUE_ICON.get(check[idx[0]], "ℹ️")
        short = [sub("[.;].*$", "", detail[i]) if detail[i] is not None else None for i in idx]
        short = [s for s in short if s is not None and s.strip(" \t\r\n") != ""]
        short_u: list[str] = _unique(short)
        short_u = [s[:67] + "..." if len(s) > 70 else s for s in short_u]
        short_txt = (" — " + "; ".join(short_u)) if short_u else ""
        full_detail = " ".join(_dquote_free(d) for d in _unique([detail[i] for i in idx]))
        detail_esc = str(_stat_html_escape(full_detail)).replace("'", "&#39;")
        text_esc = str(_stat_html_escape(f"{lbl}{short_txt}"))
        lines.append(f"<span title='{detail_esc}'>{icon} {text_esc}</span>")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# distributions figure
# -----------------------------------------------------------------------------

#: the most numeric columns drawn in the combined distribution figure
DV_MAX_FACETS = 40


def plotting_available() -> bool:
    """Whether the distribution figure can be drawn (R: the ggplot2 package)."""
    try:
        import matplotlib  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return True


def data_validate_dist_facets(
    plot_specs: Sequence[Mapping[str, Any]], max_facets: int = DV_MAX_FACETS
) -> str | None:
    """ONE faceted histogram figure of the numeric columns, as a base64 ``<img>``.

    Port of ``data_validate_dist_facets()``: ggplot2 is replaced by
    matplotlib (four panels per row, 30 bins, free scales, grey bars; the
    Tukey fences are drawn as dashed red lines when a spec carries them).
    """
    n_total = len(plot_specs)
    if n_total == 0:
        return None
    specs = list(plot_specs)[:max_facets]

    def facet_label(s: Mapping[str, Any]) -> str:
        lab = f"{s['col']}  ({s['file']})" if s["file"] else str(s["col"])
        return lab[:60]

    uri: str | None
    try:
        import base64
        import io

        import matplotlib  # type: ignore[import-not-found]

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore[import-not-found]

        labels = _unique([facet_label(s) for s in specs])
        by_label: dict[str, list[Mapping[str, Any]]] = {}
        for s in specs:
            by_label.setdefault(facet_label(s), []).append(s)
        n_facets = len(labels)
        nrow = math.ceil(n_facets / 4)
        fig, axes = plt.subplots(nrow, 4, figsize=(9, max(2, nrow * 1.3)), dpi=96, squeeze=False)
        for k, ax in enumerate(axes.flat):
            if k >= n_facets:
                ax.axis("off")
                continue
            group = by_label[labels[k]]
            vals = [float(v) for s in group for v in s["values"]]
            ax.hist(vals, bins=30, color="#B3B3B3")
            for s in group:
                if not _na(s.get("lower")):
                    for x in (s["lower"], s["upper"]):
                        ax.axvline(x, linestyle="--", color="red", linewidth=0.6)
            ax.set_title(labels[k], fontsize=6)
            ax.tick_params(labelsize=6)
            ax.set_yticks([])
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white")
        plt.close(fig)
        uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        uri = None
    if uri is None:
        return "*Distribution figure could not be rendered.*"
    img = f'<img src="{uri}" alt="Column distributions" style="max-width:100%"/>'
    note = (
        f"\n\n*Showing the first {len(specs):d} of {n_total:d} numeric columns (all were "
        f"checked; only the figure is limited). Set `max_facets = {n_total:d}` to plot them all.*"
        if n_total > len(specs)
        else ""
    )
    return img + note


# -----------------------------------------------------------------------------
# spreadsheet formatting
# -----------------------------------------------------------------------------

DV_SPREADSHEET_EXTS = ("xlsx", "xls", "ods", "fods")


def _findings_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    cols = ("source_file", "column", "label", "check", "detail")
    return pd.DataFrame({c: pd.Series([r.get(c) for r in rows], dtype="string") for c in cols})


def _spreadsheet_rows(structure_df: pd.DataFrame | None) -> list[int]:
    """Rows of *structure_df* that are spreadsheets with a readable local copy."""
    if structure_df is None or len(structure_df) == 0:
        return []
    names = _col(structure_df, "file_name") or [None] * len(structure_df)
    locs = _col(structure_df, "file_location")
    if locs is None:
        return []
    return [
        i
        for i, (nm, loc) in enumerate(zip(names, locs, strict=True))
        if _file_ext(nm) in DV_SPREADSHEET_EXTS and _has_value(loc) and _file_exists(loc)
    ]


def dv_spreadsheet_findings(structure_df: pd.DataFrame | None) -> dict[str, Any]:
    """Spreadsheet-formatting findings (port of ``.dv_spreadsheet_findings()``).

    Returns ``{"findings": <source_file, column, label, check, detail>,
    "n_files": <spreadsheets examined>}``.
    """
    xl_rows = _spreadsheet_rows(structure_df)
    if not xl_rows:
        return {"findings": _findings_frame([]), "n_files": 0}
    assert structure_df is not None
    names = _col(structure_df, "file_name") or []
    locs = _col(structure_df, "file_location") or []
    usable = _col(structure_df, "tabular_usable")
    reasons = _col(structure_df, "non_tabular_reason")
    findings: list[dict[str, Any]] = []
    for i in xl_rows:
        fname = names[i]
        path = locs[i]
        ext = _file_ext(fname)
        if usable is not None and _is_false(usable[i]):
            reason = reasons[i] if reasons is not None else None
            findings.append(
                {
                    "source_file": fname,
                    "check": "Not a rectangular dataset",
                    "detail": "This file reads as a table but is not a usable dataset"
                    + (f" ({reason})" if reason is not None else "")
                    + ". Store the data as a plain rectangular table (one header row, one "
                    "column per variable) with a codebook.",
                }
            )
        try:
            oh = dv_spreadsheet_offset_header(path, ext)
        except Exception:
            oh = None
        if oh is not None:
            findings.append(
                {
                    "source_file": fname,
                    "check": "Header not on first row",
                    "detail": f"The column header is on row {oh['header_row']:d}; above it is "
                    f"{oh['detail']}. Remove the row{plural(oh['n_above'])} above the header "
                    "so the first row of the sheet is the column header — otherwise the file "
                    "reads with invented column names (…1, …4) and the data mis-types.",
                }
            )
        if ext == "xls":
            findings.append(
                {
                    "source_file": fname,
                    "check": "Un-inspectable (.xls)",
                    "detail": "Legacy .xls format: colour, merged cells and empty rows/columns "
                    "cannot be inspected. Convert to .xlsx or .ods for a full check.",
                }
            )
            continue
        try:
            insp = dv_ods_inspect(path) if ext in ("ods", "fods") else dv_excel_inspect(path)
        except Exception:
            insp = None
        if insp is None:
            kind = "OpenDocument" if ext in ("ods", "fods") else ".xlsx"
            findings.append(
                {
                    "source_file": fname,
                    "check": "Unreadable",
                    "detail": f"The file could not be parsed as a {kind} workbook.",
                }
            )
            continue
        for s in insp["sheets"]:
            if s["color_cells"] > 0:
                n = s["color_cells"]
                findings.append(
                    {
                        "source_file": fname,
                        "label": s["name"],
                        "check": "Colour coding",
                        # R: "%d cell%s use", "%d merged range%s ... break" and "%d column%s
                        # %s empty or have" whatever the count (UPSTREAM_ISSUES U82)
                        "detail": f"{n:d} cell{plural(n)} use{'s' if n == 1 else ''} fill "
                        "colour to encode information; colour is lost on CSV export.",
                    }
                )
            if s["merges"]:
                n = len(s["merges"])
                findings.append(
                    {
                        "source_file": fname,
                        "label": s["name"],
                        "check": "Merged cells",
                        "detail": f"{n:d} merged range{plural(n)} "
                        f"({', '.join(s['merges'][:5])}) break{'s' if n == 1 else ''} the "
                        "rectangular grid.",
                    }
                )
            if s["empty_rows"] > 0:
                n = s["empty_rows"]
                findings.append(
                    {
                        "source_file": fname,
                        "label": s["name"],
                        "check": "Empty rows",
                        "detail": f"{n:d} fully empty row{plural(n)} inside the data range.",
                    }
                )
            if s["empty_cols"] > 0:
                n = s["empty_cols"]
                findings.append(
                    {
                        "source_file": fname,
                        "label": s["name"],
                        "check": "Empty or unnamed columns",
                        "detail": f"{n:d} column{plural(n)} {'is' if n == 1 else 'are'} "
                        f"empty or {'has' if n == 1 else 'have'} a blank header.",
                    }
                )
    return {"findings": _findings_frame(findings), "n_files": len(xl_rows)}


def dv_spreadsheet_summary_text(findings_df: pd.DataFrame, n_files: int) -> str:
    """Roll-up of the spreadsheet findings (port of ``.dv_spreadsheet_summary_text()``)."""
    n_flagged = len(_unique(_col(findings_df, "source_file") or []))
    return (
        f"{n_flagged:d} of {n_files:d} spreadsheet file{plural(n_files)} "
        f"{'has' if n_flagged == 1 else 'have'} at least one formatting issue (colour coding, "
        "merged cells, empty rows/columns, or an offset header)."
    )


def dv_spreadsheet_report(findings_df: pd.DataFrame, n_files: int) -> list[Any]:
    """The "Spreadsheet Formatting" section (port of ``.dv_spreadsheet_report()``)."""
    from pytacheck.report import scroll_table

    n_flagged = len(_unique(_col(findings_df, "source_file") or []))
    tbl = pd.DataFrame(
        {
            "File": findings_df["source_file"].astype("string").reset_index(drop=True),
            "Sheet": findings_df["label"].astype("string").reset_index(drop=True),
            "Issue": findings_df["check"].astype("string").reset_index(drop=True),
            "Detail": findings_df["detail"].astype("string").reset_index(drop=True),
        }
    )
    return [
        "#### Spreadsheet Formatting",
        f"We examined {n_files:d} spreadsheet file{plural(n_files)} in the repository; "
        f"{n_flagged:d} {'has' if n_flagged == 1 else 'have'} at least one formatting issue.",
        scroll_table(tbl, maxrows=20),
        "Spreadsheet formatting such as colour, merged cells, and blank rows/columns is not "
        "preserved when data are read programmatically or exported to CSV. Store data as a "
        "plain rectangular table (one header row, one column per variable, no colour-encoded "
        "meaning) so it is machine-readable.",
    ]


def dv_spreadsheet_offset_header(path: Any, ext: str | None = None) -> dict[str, Any] | None:
    """A banner / blank / units row above the real header (port of ``.dv_spreadsheet_offset_header()``).

    Returns ``header_row`` (1-based, as R reports it), ``n_above`` and a
    description of the rows above the header, or ``None`` when the header is
    already the first row.
    """
    from pytacheck.datacheck import _files_readers as readers
    from pytacheck.datacheck._checks_rvec import row_as_character, rvec
    from pytacheck.datacheck.checks import (
        _detect_header_row,
        _is_placeholder_name,
        _row_duplication,
    )

    if ext is None:
        ext = _file_ext(path)
    raw: pd.DataFrame | None
    try:
        if ext in ("ods", "fods"):
            raw = readers.read_ods(str(path), col_names=False, name_repair="minimal").head(6)
        else:
            raw = readers.read_excel(
                str(path), col_names=False, col_types="text", n_max=6, name_repair="minimal"
            )
    except Exception:
        raw = None
    if raw is None or len(raw) < 2 or raw.shape[1] < 2:
        return None
    cols = [raw.iloc[:, j] for j in range(raw.shape[1])]
    kinds = [rvec(c).kind for c in cols]
    values = [rvec(c).values for c in cols]
    rows = [
        row_as_character([values[j][i] for j in range(len(cols))], kinds) for i in range(len(raw))
    ]
    det = _detect_header_row(rows)
    stripped = det.get("stripped") or []
    if det["header_row"] <= 0 or len(stripped) == 0:
        return None

    def describe(v: Sequence[Any]) -> str:
        vals = [None if s is None else s.strip(" \t\r\n") for s in v]
        nz = [s for s in vals if s is not None and s != ""]
        if not nz:
            return "an empty row"
        dup = _row_duplication(v)
        uniq = _unique(nz)
        if dup >= 0.6 and len(uniq) <= 3:
            joined = '"/"'.join(uniq)
            return f'"{joined}" repeated across {len(nz):d} column{plural(len(nz))}'
        ph = _is_placeholder_name(list(v))
        if ph and sum(ph) / len(ph) >= 0.5:
            return "a row of placeholder names from an earlier mis-read"
        return f"a partial label row ({', '.join(nz[:3])}{', …' if len(nz) > 3 else ''})"

    descr = [describe(v) for v in stripped]
    return {
        "header_row": int(det["header_row"]) + 1,
        "n_above": len(stripped),
        "detail": "; then ".join(descr),
    }


# -- xlsx ----------------------------------------------------------------------


def _xml(data: bytes) -> Any:
    from lxml import etree

    parser = etree.XMLParser(resolve_entities=False, huge_tree=True, no_network=True)
    return etree.fromstring(data, parser)


def _attr(node: Any, name: str) -> str | None:
    """xml2 ``xml_attr(node, name)``: the first attribute with that local name."""
    for k, v in node.attrib.items():
        local = k.rsplit("}", 1)[-1] if k.startswith("{") else k
        if local == name:
            return str(v)
    return None


def _as_integer(s: str | None) -> int | None:
    """``suppressWarnings(as.integer(s))`` of an attribute value."""
    if s is None:
        return None
    from pytacheck.datacheck._checks_rvec import as_numeric_str

    f = as_numeric_str(s)
    if f is None or f != f or math.isinf(f) or abs(f) >= 2**31:
        return None
    return int(f)


def dv_excel_inspect(path: Any) -> dict[str, Any] | None:
    """Inspect an ``.xlsx`` as a zip of XML parts (port of ``.dv_excel_inspect()``).

    Returns ``{"sheets": [{name, color_cells, merges, empty_rows,
    empty_cols}, ...]}``, or ``None`` when the file is not a readable workbook.
    """
    import os
    import zipfile

    if path is None or not os.path.exists(str(path)):
        return None
    try:
        zf = zipfile.ZipFile(str(path))
        members = {n.replace("\\", "/"): n for n in zf.namelist()}
    except Exception:
        return None
    with zf:
        if not any(n.startswith("xl/") for n in members):
            return None

        def read(name: str) -> bytes | None:
            real = members.get(name)
            if real is None:
                return None
            try:
                return zf.read(real)
            except Exception:
                return None

        colored = dv_excel_colored_styles(read("xl/styles.xml"))
        sheet_names: list[str | None] = []
        wb = read("xl/workbook.xml")
        if wb is not None:
            try:
                doc = _xml(wb)
                sheet_names = [_attr(s, "name") for s in doc.xpath(".//*[local-name()='sheet']")]
            except Exception:
                sheet_names = []
        sheet_files = [
            n
            for n in members
            if n.startswith("xl/worksheets/")
            and "/" not in n[len("xl/worksheets/") :]
            and grepl(r"^sheet[0-9]+\.xml$", n[len("xl/worksheets/") :])
        ]
        sheet_files = sorted(sheet_files, key=r_sort_key)
        sheets = []
        for j, sf in enumerate(sheet_files):
            if j < len(sheet_names):
                nm = sheet_names[j]
            else:
                nm = os.path.splitext(os.path.basename(sf))[0]
            # a <sheet> without a name attribute: NA (the findings' label is NA)
            sheets.append(dv_excel_inspect_sheet(read(sf), nm, colored))
    return {"sheets": sheets}


def dv_excel_colored_styles(styles: bytes | None) -> list[int]:
    """0-based cell-style indices whose fill is a real colour (port of ``.dv_excel_colored_styles()``)."""
    if styles is None:
        return []
    try:
        st = _xml(styles)
    except Exception:
        return []
    fills = st.xpath(".//*[local-name()='fills']/*[local-name()='fill']")
    colored_fill_ids = []
    for k, fl in enumerate(fills):
        fg = fl.xpath(".//*[local-name()='fgColor']")
        if not fg:
            continue
        rgb = _attr(fg[0], "rgb")
        if (
            rgb is not None
            and rgb != ""
            and rgb.upper() not in ("FF000000", "FFFFFFFF", "00000000")
        ):
            colored_fill_ids.append(k)
    if not colored_fill_ids:
        return []
    xfs = st.xpath(".//*[local-name()='cellXfs']/*[local-name()='xf']")
    ids = set(colored_fill_ids)
    return [k for k, xf in enumerate(xfs) if _as_integer(_attr(xf, "fillId")) in ids]


def _empty_rows_cols(
    val_row: list[int | None], val_col: list[Any], all_cols: list[Any]
) -> tuple[int, int]:
    """The empty-row / empty-column counts shared by the xlsx and ODS inspectors."""
    empty_rows = 0
    populated = sorted({r for r in val_row if r is not None})
    if len(populated) > 1:
        empty_rows = (max(populated) - min(populated) + 1) - len(populated)
    empty_cols = 0
    if val_row:
        hdr_row = None if any(r is None for r in val_row) else min(val_row)  # type: ignore[type-var]
        if hdr_row is None:  # min() of a vector with NA is NA: every index is NA
            hdr_cols: list[Any] = [None] * len(val_col)
            body_cols: list[Any] = [None] if val_col else []
        else:
            hdr_cols = [c for c, r in zip(val_col, val_row, strict=True) if r == hdr_row]
            body_cols = _unique(
                [c for c, r in zip(val_col, val_row, strict=True) if r is not None and r > hdr_row]
                + (
                    [None] * sum(1 for r in val_row if r is None)
                    if any(r is None for r in val_row)
                    else []
                )
            )
        blank_header = _setdiff(all_cols, hdr_cols)
        header_no_body = _setdiff(hdr_cols, body_cols)
        empty_cols = len(_unique(blank_header + header_no_body))
    return empty_rows, empty_cols


def dv_excel_inspect_sheet(
    sheet: bytes | None, name: str | None, colored_styles: Sequence[int]
) -> dict[str, Any]:
    """Colour-coded cells, merges, empty rows and columns of one worksheet.

    Port of ``.dv_excel_inspect_sheet()``.
    """
    blank: dict[str, Any] = {
        "name": name,
        "color_cells": 0,
        "merges": [],
        "empty_rows": 0,
        "empty_cols": 0,
    }
    if sheet is None:
        return blank
    try:
        sh = _xml(sheet)
    except Exception:
        return blank
    cells = sh.xpath(".//*[local-name()='c']")
    cell_ref = [_attr(c, "r") for c in cells]
    val_cells = sh.xpath(".//*[local-name()='c'][*[local-name()='v'] or *[local-name()='is']]")
    val_ref = [_attr(c, "r") for c in val_cells]
    color_cells = 0
    if colored_styles and cells:
        styles = set(colored_styles)
        color_cells = sum(1 for c in cells if _as_integer(_attr(c, "s")) in styles)
    merges = [
        m
        for m in (_attr(x, "ref") for x in sh.xpath(".//*[local-name()='mergeCell']"))
        if m is not None
    ]

    def ref_col(ref: str | None) -> str | None:
        return None if ref is None else sub("[0-9]+$", "", ref)

    def ref_row(ref: str | None) -> int | None:
        return None if ref is None else _as_integer(sub("^[A-Za-z]+", "", ref))

    val_col = [ref_col(r) for r in val_ref]
    val_rownum = [ref_row(r) for r in val_ref]
    all_cols = _unique([ref_col(r) for r in cell_ref])
    empty_rows, empty_cols = _empty_rows_cols(val_rownum, val_col, all_cols)
    return {
        "name": name,
        "color_cells": color_cells,
        "merges": merges,
        "empty_rows": empty_rows,
        "empty_cols": empty_cols,
    }


# -- OpenDocument --------------------------------------------------------------


def _local_name(node: Any) -> str:
    tag = node.tag if isinstance(node.tag, str) else ""
    return tag.rsplit("}", 1)[-1]


def dv_ods_inspect(path: Any) -> dict[str, Any] | None:
    """Inspect an ``.ods`` / ``.fods`` workbook (port of ``.dv_ods_inspect()``).

    Same shape as :func:`dv_excel_inspect`.
    """
    import os
    import zipfile

    if path is None or not os.path.exists(str(path)):
        return None
    ext = _file_ext(str(path))
    doc = None
    if ext == "fods":
        try:
            with open(str(path), "rb") as fh:
                doc = _xml(fh.read())
        except Exception:
            return None
    else:
        try:
            with zipfile.ZipFile(str(path)) as zf:
                names = {n.replace("\\", "/"): n for n in zf.namelist()}
                if "content.xml" not in names:
                    return None
                data = zf.read(names["content.xml"])
        except Exception:
            return None
        try:
            doc = _xml(data)
        except Exception:
            return None
    colored = dv_ods_colored_styles(doc)
    tables = [
        t
        for t in doc.xpath(".//*[local-name()='table']")
        if t.getparent() is not None and _local_name(t.getparent()) == "spreadsheet"
    ]
    if not tables:
        return {"sheets": []}
    sheets = []
    for j, tb in enumerate(tables, 1):
        nm = _attr(tb, "name")
        if nm is None or nm == "":
            nm = f"Sheet{j}"
        sheets.append(dv_ods_inspect_sheet(tb, nm, colored))
    return {"sheets": sheets}


def dv_ods_colored_styles(doc: Any) -> list[str]:
    """Names of cell styles with a real background colour (port of ``.dv_ods_colored_styles()``)."""
    sty = doc.xpath(".//*[local-name()='style'][@*[local-name()='family']='table-cell']")
    out = []
    for s in sty:
        nm = _attr(s, "name")
        props = s.xpath(".//*[local-name()='table-cell-properties']")
        bg = _attr(props[0], "background-color") if props else None
        if (
            bg is not None
            and bg != ""
            and bg.lower() not in ("transparent", "none", "#ffffff", "#fff", "#000000", "#000")
            and nm is not None
        ):
            out.append(nm)
    return out


def dv_ods_col_letter(i: Sequence[int] | int) -> list[str]:
    """1-based column index to spreadsheet letters (port of ``.dv_ods_col_letter()``)."""
    vals = [i] if isinstance(i, int) else list(i)
    out = []
    for n in vals:
        s = ""
        while n > 0:
            r = (n - 1) % 26
            s = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[r] + s
            n = (n - 1) // 26
        out.append(s)
    return out


def _xml_text(node: Any) -> str:
    return "".join(node.itertext())


def dv_ods_inspect_sheet(tbl: Any, name: str, colored: Sequence[str]) -> dict[str, Any]:
    """One ``<table:table>`` with its repeat counters expanded (port of ``.dv_ods_inspect_sheet()``)."""
    blank = {"name": name, "color_cells": 0, "merges": [], "empty_rows": 0, "empty_cols": 0}
    rows = tbl.xpath("./*[local-name()='table-row']")
    if not rows:
        return blank

    def int_attr(node: Any, a: str, default: int = 1) -> int:
        v = _as_integer(_attr(node, a))
        return default if v is None or v < 1 else v

    colored_set = set(colored)
    val_row: list[int] = []
    val_col: list[int] = []
    seen_col: list[int] = []
    color_cells = 0
    merges: list[str] = []
    r = 0
    for row in rows:
        rep_r = int_attr(row, "number-rows-repeated")
        cells = row.xpath("./*[local-name()='table-cell' or local-name()='covered-table-cell']")
        cc = 0
        for cell in cells:
            rep_c = int_attr(cell, "number-columns-repeated")
            vt = _attr(cell, "value-type")
            txt = _xml_text(cell).strip(" \t\r\n")
            populated = (vt is not None and vt != "") or txt != ""
            span_c = rep_c if populated else min(rep_c, 1024)
            idx = list(range(cc + 1, cc + span_c + 1))
            seen_col.extend(idx)
            if populated:
                for rr in range(1, rep_r + 1):
                    val_row.extend([r + rr] * span_c)
                    val_col.extend(idx)
            sn = _attr(cell, "style-name")
            if sn is not None and sn in colored_set:
                color_cells += span_c * rep_r
            sc = _as_integer(_attr(cell, "number-columns-spanned"))
            sr = _as_integer(_attr(cell, "number-rows-spanned"))
            sc = 1 if sc is None else sc
            sr = 1 if sr is None else sr
            if sc > 1 or sr > 1:
                merges.append(
                    f"{dv_ods_col_letter(cc + 1)[0]}{r + 1:d}:"
                    f"{dv_ods_col_letter(cc + sc)[0]}{r + sr:d}"
                )
            cc += span_c
        r += rep_r
    max_col = max(val_col) if val_col else None
    all_cols = _unique([c for c in seen_col if max_col is not None and c <= max_col])
    empty_rows, empty_cols = _empty_rows_cols(list(val_row), list(val_col), all_cols)
    return {
        "name": name,
        "color_cells": color_cells,
        "merges": merges,
        "empty_rows": empty_rows,
        "empty_cols": empty_cols,
    }
