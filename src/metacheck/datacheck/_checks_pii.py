"""Personal-information (PII) and demographic-column checks (private).

Port of the "Personal / disclosure information" and "Demographic-column
detection" sections of ``R/data_check_helpers.R``. Messages name the matched
pattern, never the matching value, so the report does not leak the PII.
"""

from __future__ import annotations

from typing import Any

from metacheck._r.base import plural
from metacheck._r.regex import grepl, gsub, strsplit
from metacheck.datacheck._checks_rvec import (
    as_numeric_str,
    chr,
    fmt_pct0,
    is_whole,
    median,
    rvec,
    scalar_chr,
    tolower,
    trim,
    trimmed_counts,
    unique,
    weighted_median,
)

__all__ = [
    "_DEMOGRAPHIC_NAME_REGEX",
    "_DEMOGRAPHIC_NAME_TOKENS",
    "_PII_NAME_TOKENS",
    "_PII_VALUE_PATTERNS",
    "_demographic_values_ok",
    "_pii_card_ok",
    "_pii_luhn_ok",
    "_pii_split_name",
    "_utf8_name",
    "data_check_demographic",
    "data_check_pii_freetext",
    "data_check_pii_geo",
    "data_check_pii_name",
    "data_check_pii_values",
]

#: R: .pii_value_patterns -- standard value patterns, all "specific" (one
#: validated match flags the column).
_PII_VALUE_PATTERNS: dict[str, dict[str, str]] = {
    "email": {
        "regex": r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "kind": "specific",
    },
    # IPv4 with each octet 0-255.
    "ip_address": {
        "regex": (r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"),
        "kind": "specific",
    },
    # US SSN: 3-2-4 with separators, excluding obvious non-SSNs.
    "ssn": {
        "regex": r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b",
        "kind": "specific",
    },
    # Credit-card-like: 13-16 digits (grouped by 4 or unbroken), not part of a
    # longer digit/decimal string; must also pass .pii_card_ok().
    "credit_card": {
        "regex": (r"(?<![\d.])(?:\d{13,16}|\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4})(?![\d.])"),
        "kind": "specific",
        "validate": "_pii_card_ok",
    },
}

#: R: .pii_name_tokens -- column-name words that suggest a person is identified.
_PII_NAME_TOKENS: frozenset[str] = frozenset((
    # -- person name --
    "firstname", "lastname", "surname", "fullname",
    "naam", "voornaam", "achternaam", "tussenvoegsel", "geslachtsnaam",
    "vorname", "nachname", "familienname",
    "prenom", "nomdefamille",
    "nombre", "apellido",
    "cognome", "sobrenome", "nomecompleto",
    "fornavn", "etternavn", "efternavn", "fornamn", "sukunimi", "etunimi",
    # -- email / phone --
    "email", "e-mail", "phone", "mobile", "telephone", "fax",
    "phonenumber", "mobilenumber", "cellphone", "cellnumber",
    "epost", "correoelectronico",
    "telefoon", "telefon", "telefono", "telefone", "puhelin",
    "mobiel", "handynummer",
    "telefoonnummer", "mobielnummer", "gsmnummer",
    "telefonnummer", "telefonnummber", "rufnummer", "mobilnummer",
    "numerotelephone", "numerodetelephone", "numeroportable",
    "numerotelefono", "numeromovil", "telefononumero",
    "numerodetelefone", "telemovel",
    "puhelinnumero", "telefonnumer",
    # -- address / postcode --
    "address", "street", "zipcode", "zip", "postcode", "postalcode",
    "adres", "adresse", "direccion", "indirizzo", "endereco", "osoite",
    "postleitzahl", "codepostal", "codigopostal", "postnummer",
    "woonplaats", "wohnort", "straat", "strasse",
    # -- national / government id --
    "ssn", "socialsecurity", "passport", "nationalid", "taxid",
    "bsn", "rijksregisternummer", "sozialversicherungsnummer",
    "numerosecuritesociale", "dni", "codicefiscale", "personnummer",
    "henkilotunnus", "paspoort", "reisepass", "passeport", "pasaporte",
    # -- date of birth --
    "dob", "dateofbirth", "birthdate", "birthday", "birthyear",
    "yearofbirth", "yob",
    "geboortedatum", "geboortejaar", "geburtsdatum", "geburtsjahr",
    "datenaissance", "fechanacimiento", "datadinascita",
    # -- technical / financial --
    "ipaddress", "ip", "mac", "creditcard", "iban", "bankaccount",
    "kontonummer", "bankrekening", "ibannummer", "kreditkarte",
    "cartebancaire", "tarjetacredito",
    # -- location --
    "latitude", "longitude", "lat", "lon", "lng", "geolocation", "gps",
    "breitengrad", "laengengrad", "ortsangabe",
    # -- account / handle --
    "username", "userid", "handle", "initials",
    "gebruikersnaam", "benutzername", "initialen",
))  # fmt: skip

#: R: .demographic_name_tokens (documentation of the name vocabulary; the
#: detector itself uses the anchored regexes below).
_DEMOGRAPHIC_NAME_TOKENS: dict[str, tuple[str, ...]] = {
    "age": ("age", "agejaren", "ageyears", "ageyrs", "leeftijd", "alter"),
    "gender": ("gender", "sex", "geslacht", "genderidentity", "sexgender", "gendersex"),
    "race": (
        "race", "ethnicity", "ethnic", "raceethnicity", "ethnicgroup",
        "raceeth", "hispanic", "raza", "etnia",
    ),
}  # fmt: skip

#: R: .demographic_name_regex -- anchored name patterns (perl = TRUE).
_DEMOGRAPHIC_NAME_REGEX: dict[str, str] = {
    "age": (
        r"(?i)(^|[^a-z])(age|leeftijd|alter)([^a-z]|$)|(?i)age[_.-]?(years|yrs|jaren)"
        r"|(?i)(years|yrs)[_.-]?age"
    ),
    "gender": r"(?i)(^|[^a-z])(gender|sex|geslacht)([^a-z]|$)",
    "race": r"(?i)(^|[^a-z])(race|ethnicity|ethnic|hispanic|raza|etnia)([^a-z]|$)",
}

_GENDER_WORDS = frozenset((
    "m", "f", "male", "female", "man", "woman", "men",
    "women", "boy", "girl", "nonbinary", "non-binary",
    "nb", "other", "trans", "transgender", "genderqueer",
    "prefer not to say", "prefernottosay", "pnts", "n/a",
    "unknown", "d", "diverse", "man/vrouw", "vrouw", "man",
    "intersex", "agender", "fluid", "questioning",
))  # fmt: skip


def _digits(s: str) -> str:
    """``gsub("[^0-9]", "", s)``."""
    return "".join(c for c in s if "0" <= c <= "9")


def _pii_luhn_ok(s: str) -> bool:
    """Luhn checksum of the digits in *s* (13-16 digits).

    Port of ``R/data_check_helpers.R::.pii_luhn_ok()``.
    """
    d = [int(c) for c in _digits(s)]
    n = len(d)
    if n < 13 or n > 16:
        return False
    d.reverse()
    for i in range(1, n, 2):
        d[i] *= 2
    return sum(v - 9 if v > 9 else v for v in d) % 10 == 0


def _pii_card_ok(s: str) -> bool:
    """Does *s* look like a real payment card: issuer prefix (IIN) and Luhn?

    Port of ``R/data_check_helpers.R::.pii_card_ok()``.
    """
    d = _digits(s)
    n = len(d)
    if n < 13 or n > 16:
        return False

    def p(k: int) -> int:
        return int(d[:k])

    scheme = (
        (p(1) == 4 and n in (13, 16))  # Visa
        or (51 <= p(2) <= 55 and n == 16)  # Mastercard
        or (2221 <= p(4) <= 2720 and n == 16)  # Mastercard (2-series)
        or (p(2) in (34, 37) and n == 15)  # American Express
        or (p(4) == 6011 and n == 16)  # Discover
        or (p(2) == 65 and n == 16)  # Discover
        or (644 <= p(3) <= 649 and n == 16)  # Discover
        or (p(2) in (36, 38) and n in (14, 16))  # Diners Club
        or (300 <= p(3) <= 305 and n == 14)  # Diners Club (carte blanche)
        or (3528 <= p(4) <= 3589 and n == 16)  # JCB
    )
    if not scheme:
        return False
    return _pii_luhn_ok(s)


_VALIDATORS = {"_pii_card_ok": _pii_card_ok}


def data_check_pii_values(x: Any, broad_min_frac: float = 0.30) -> dict[str, Any]:
    """Flag values that match a personal-information pattern.

    Port of ``R/data_check_helpers.R::data_check_pii_values()``: email, IPv4
    address, US SSN and (issuer + Luhn validated) credit-card numbers.
    ``values`` holds the matched pattern names, never the data.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    counts = trimmed_counts(x)  # each distinct value is matched once
    n_total = sum(counts.values())
    if n_total < 3:
        return none
    uniq = list(counts)
    hits: list[str] = []
    names: list[str] = []
    for nm, spec in _PII_VALUE_PATTERNS.items():
        need = _PII_PREFILTER[nm]
        cand = [s for s in uniq if need(s)]
        m = grepl(spec["regex"], cand, perl=True) if cand else []
        matched = [s for s, hit in zip(cand, m, strict=True) if hit]
        if not matched:
            continue
        validate = spec.get("validate")
        if validate is not None:
            vfun = _VALIDATORS[validate]
            matched = [s for s in matched if vfun(s)]
            if not matched:
                continue
        n_valid = sum(counts[s] for s in matched)
        frac = n_valid / n_total
        flag = n_valid >= 1 if spec["kind"] == "specific" else frac >= broad_min_frac
        if flag:
            hits.append(f"{nm} ({n_valid} value{plural(n_valid)}, {fmt_pct0(100 * frac)}%)")
            names.append(nm)
    if not hits:
        return none
    return {
        "problem": True,
        "message": (
            f"Values look like personal information: {'; '.join(hits)}. Review before sharing."
        ),
        "values": names,
    }


_NO_DIGITS = str.maketrans("", "", "0123456789")

#: A cheap test each pattern's matches must pass (a literal the regex requires),
#: so the regex only runs on candidate values: exact, never a false negative.
_PII_PREFILTER: dict[str, Any] = {
    "email": lambda s: "@" in s,
    "ip_address": lambda s: s.count(".") >= 3,
    "ssn": lambda s: s.count("-") >= 2,
    "credit_card": lambda s: len(s) - len(s.translate(_NO_DIGITS)) >= 13,
}


def _pii_split_name(x: Any) -> list[str | None]:
    """Split a column name into lower-case word tokens (separators and camelCase).

    Port of ``R/data_check_helpers.R::.pii_split_name()``: both readings of a
    capital run (``IPAddress`` -> ip/address, ``ZIPcode`` -> zip/code) are
    produced. A vector argument uses its first element (as R's
    ``strsplit(x)[[1]]`` does); ``NA`` gives ``[None]`` and a zero-length
    vector is an error (``[[1]]`` of an empty list).
    """
    if x is not None and not isinstance(x, str) and not chr(x):
        raise IndexError("subscript out of bounds")
    x = scalar_chr(x)
    if x is None:
        return [None]
    a = gsub(r"(?<=[a-z0-9])(?=[A-Z])", " ", x, perl=True)
    s1 = gsub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", a, perl=True)
    s2 = gsub(r"(?<=[A-Z])(?=[a-z])", " ", a, perl=True)
    p = [*strsplit([tolower(s1)], "[^a-z0-9]+")[0], *strsplit([tolower(s2)], "[^a-z0-9]+")[0]]
    return unique(t for t in p if t is None or t != "")


def data_check_pii_name(col_name: Any) -> dict[str, Any]:
    """Flag a column whose name suggests personal information.

    Port of ``R/data_check_helpers.R::data_check_pii_name()``: word-based
    matching (separators and camelCase), plus adjacent-word joins and the
    separator-free name, against a multilingual token list.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    if col_name is None:
        return none
    names = chr(col_name)
    if not names:  # `FALSE || logical(0)` is NA inside `if`
        raise ValueError("missing value where TRUE/FALSE needed")
    if len(names) > 1:
        raise ValueError(f"'length = {len(names)}' in coercion to 'logical(1)'")
    nm = names[0]
    if nm is None or nm == "":
        return none
    words = _pii_split_name(nm)
    low = tolower(nm) or ""
    norm = "".join(c for c in low if "a" <= c <= "z" or "0" <= c <= "9")
    if not words and not norm:
        return none
    cand: list[str | None] = list(words)
    if len(words) >= 2:
        cand += [f"{words[i]}{words[i + 1]}" for i in range(len(words) - 1)]
    if len(words) >= 3:
        cand += [f"{words[i]}{words[i + 1]}{words[i + 2]}" for i in range(len(words) - 2)]
    if norm:
        cand.append(norm)
    matched = unique(c for c in cand if c in _PII_NAME_TOKENS)
    if not matched:
        return none
    return {
        "problem": True,
        "message": (
            f"Column name suggests personal information (matched: {', '.join(matched)}). "  # type: ignore[arg-type]
            "Review before sharing."
        ),
        "values": matched,
    }


_LAT_WORDS = ("lat", "latitude", "breitengrad")
_LON_WORDS = ("lon", "lng", "longitude", "laengengrad")
_SOLO_WORDS = ("gps", "geolocation", "coordinate", "coordinates", "geocode")


def data_check_pii_geo(col_name: Any, x: Any, sibling_names: Any = None) -> dict[str, Any]:
    """Flag a column that holds geographic coordinates.

    Port of ``R/data_check_helpers.R::data_check_pii_geo()``: a lat/lon (or
    gps/geolocation) name, values inside the coordinate range, and -- for
    lat/lon, when *sibling_names* is given -- a partner column in the file.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    words = _pii_split_name("" if col_name is None else col_name)  # col_name %||% ""
    if not words or words == [None]:
        return none
    is_lat = any(w in _LAT_WORDS for w in words)
    is_lon = any(w in _LON_WORDS for w in words)
    is_solo = any(w in _SOLO_WORDS for w in words)
    if not is_lat and not is_lon and not is_solo:
        return none
    vals = [
        f
        for s in chr(x)
        if (f := as_numeric_str(None if s is None else s.replace(",", "."))) is not None and f == f
    ]
    if len(vals) >= 3:
        lim = 90 if (is_lat and not is_lon) else 180
        if not all(-lim <= f <= lim for f in vals):
            return none
    if not is_solo and sibling_names is not None:
        sib: list[str | None] = []
        for s in chr(sibling_names):
            sib.extend(_pii_split_name(s))
        want = _LON_WORDS if is_lat else _LAT_WORDS
        if not any(w in want for w in unique(sib)):
            return none
    return {
        "problem": True,
        "message": "Column name suggests geographic coordinates. Review before sharing.",
        "values": "geo",
    }


def data_check_pii_freetext(
    x: Any,
    min_median_chars: float = 40,
    min_unique_frac: float = 0.8,
    min_multiword_frac: float = 0.6,
    min_alpha_frac: float = 0.5,
) -> dict[str, Any]:
    """Flag a free-text column that may contain incidental personal information.

    Port of ``R/data_check_helpers.R::data_check_pii_freetext()``: long,
    varied, multi-word, predominantly alphabetic values.
    """
    none: dict[str, Any] = {"problem": False, "message": "", "values": None}
    v = rvec(x)
    if v.is_numeric:
        return none
    counts = trimmed_counts(v)
    n_total = sum(counts.values())
    if n_total < 5:
        return none
    med = weighted_median((len(s), c) for s, c in counts.items())
    uniq_frac = len(counts) / n_total
    if med < min_median_chars or uniq_frac < min_unique_frac:
        return none
    uniq = list(counts)
    multi = grepl(r"\w\s+\w", uniq)
    if (
        sum(counts[s] for s, hit in zip(uniq, multi, strict=True) if hit) / n_total
        < min_multiword_frac
    ):
        return none
    # x[order(abs(nchar(x) - med))][1]: the first value (in data order) closest to the median
    typical = min(uniq, key=lambda s: abs(len(s) - med))
    n_alpha = sum(1 for c in typical if "A" <= c <= "Z" or "a" <= c <= "z")
    alpha_frac = n_alpha / len(typical) if typical else 0
    if alpha_frac < min_alpha_frac:
        return none
    return {
        "problem": True,
        "message": (
            f"Free-text column (median {fmt_pct0(med)} characters, "
            f"{fmt_pct0(100 * uniq_frac)}% distinct) may contain names or other personal "
            "detail. Review before sharing."
        ),
        "values": None,
    }


def _demographic_values_ok(kind: str, x: Any) -> bool:
    """Do a column's values look like this demographic (age / gender / race)?

    Port of ``R/data_check_helpers.R::.demographic_values_ok()`` -- a
    conservative confirmation of a name match.
    """
    from metacheck.datacheck._checks_quality import _DATA_MISSING_SENTINELS

    x_chr = [t for s in chr(x) if s is not None and (t := trim(s)) != ""]  # type: ignore[misc]
    if len(x_chr) < 3:
        return True
    if kind == "age":
        nums = [as_numeric_str(s.replace(",", ".")) for s in x_chr]
        ok = [f for f in nums if f is not None and f == f]
        if len(ok) / len(nums) < 0.8:
            return False
        sentinels = set(_DATA_MISSING_SENTINELS)
        vals = [f for f in ok if f not in sentinels]
        if not vals:
            return False
        return sum(1 for f in vals if 0 <= f <= 120) / len(vals) >= 0.9
    if kind == "gender":
        u = unique(tolower(s) for s in x_chr)
        hit_frac = sum(1 for s in u if s in _GENDER_WORDS) / len(u)
        nums2 = [as_numeric_str(s) for s in x_chr]
        is_lowcard_numeric = (
            all(f is not None and f == f for f in nums2)
            and len(unique(nums2)) <= 4
            and all(is_whole(f) for f in nums2)  # type: ignore[arg-type]
            and all(0 <= f <= 9 for f in nums2)  # type: ignore[operator]
        )
        return hit_frac >= 0.6 or is_lowcard_numeric
    if kind == "race":
        if len(set(x_chr)) > 30:
            return False
        if median([len(s) for s in x_chr]) > 60:
            return False
        nums3 = [as_numeric_str(s) for s in x_chr]
        if all(f is not None and f == f for f in nums3):
            return len(unique(nums3)) <= 25 and all(is_whole(f) for f in nums3)  # type: ignore[arg-type]
        return True
    return False


def _utf8_name(name: str) -> str:
    """``iconv(name, "latin1", "UTF-8", sub = "")`` for a name that is not valid UTF-8.

    Python strings are Unicode; a name carrying undecodable bytes (as
    ``surrogateescape`` surrogates) is re-read as Latin-1, as R does.
    """
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return name.encode("utf-8", "surrogateescape").decode("latin-1")
    return name


def data_check_demographic(col_name: Any, x: Any) -> str | None:
    """Detect whether a column holds participant age, gender/sex, or race/ethnicity.

    Port of ``R/data_check_helpers.R::data_check_demographic()``: the NAME
    must match the demographic and the VALUES must agree. Returns ``"age"``,
    ``"gender"``, ``"race"`` or ``None`` (``NA``).
    """
    if col_name is None:
        return None
    names = chr(col_name)
    if len(names) != 1 or names[0] is None or names[0] == "":
        return None
    nm = _utf8_name(names[0])
    for kind, rx in _DEMOGRAPHIC_NAME_REGEX.items():
        if grepl(rx, nm, perl=True) and _demographic_values_ok(kind, x):
            return kind
    return None
