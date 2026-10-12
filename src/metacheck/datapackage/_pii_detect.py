"""Personal data by value: detectors for the data files of a data package.

Six detectors look at the cells of one column and say whether the column holds
a Dutch citizen service number (BSN), student numbers, phone numbers, IPv6
addresses, Dutch postcodes or people's names. They are pytacheck's own and
separate from :mod:`metacheck.datacheck` (the port of R's ``data_check``), whose
PII checks are not changed by anything here.

How a column is judged
----------------------

A *value* test says whether a whole cell is, say, a phone number (``"06-12345678"``
is, ``"call 06-12345678 tomorrow"`` is not). A *column* is flagged only when
enough of its non-empty cells pass: at least ``min_hits`` cells and at least
``min_share`` of the non-empty cells. A column whose **name** points at the same
kind of data (``telefoon``, ``bsn``, ``postcode``) needs fewer cells; a student
number or a name is only looked for in a column whose name says so, because
a bare 7-digit number or a capitalised word is far too common to mean anything.

Nothing here keeps a value: a column's verdict is a count.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

__all__ = [
    "DETECTORS",
    "Detector",
    "bsn_ok",
    "column_context",
    "column_tokens",
    "is_bsn",
    "is_ipv6",
    "is_person_name",
    "is_phone",
    "is_postcode",
    "is_student_number",
    "judge_column",
    "name_column_kind",
]

#: Cells that mean "no value".
_EMPTY = frozenset({"", "na", "n/a", "nan", "null", "none", "-", "--", ".", "nvt", "missing"})

#: Cells longer than this are free text, not a number or a name.
_MAX_CELL = 60


# -- column names ----------------------------------------------------------------------


def column_tokens(name: str) -> list[str]:
    """The lower-case words of a column name (``StudentNr`` and ``student_nr`` give student, nr)."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", spaced)
    return [t for t in re.split(r"[^0-9a-zA-ZÀ-ɏ]+", spaced.lower()) if t]


def _squash(tokens: Sequence[str]) -> str:
    return "".join(tokens)


# -- BSN -------------------------------------------------------------------------------

_BSN_PLAIN = re.compile(r"\d{9}")
_BSN_GROUPED = re.compile(r"\d{3}([ .\-])\d{3}\1\d{3}")


def bsn_ok(digits: str) -> bool:
    """The "elfproef" (11-check) of a BSN: 9 digits, the weighted sum divisible by 11.

    The weights are 9, 8, 7, 6, 5, 4, 3, 2 and -1; ``000000000`` is not a BSN.
    """
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = sum(w * int(d) for w, d in zip((9, 8, 7, 6, 5, 4, 3, 2, -1), digits, strict=True))
    return total % 11 == 0 and total != 0


def is_bsn(value: str, context: bool = False) -> bool:
    """A whole cell that is a BSN: 9 digits (``123456782``, ``123.456.782``) passing the 11-check.

    With *context* (the column is named like a BSN column) an 8-digit number is
    accepted too, padded with a zero: a spreadsheet that stored the number as a
    number dropped its leading zero.
    """
    s = value.strip()
    if _BSN_GROUPED.fullmatch(s):
        return bsn_ok(re.sub(r"\D", "", s))
    if _BSN_PLAIN.fullmatch(s):
        return bsn_ok(s)
    if context and re.fullmatch(r"\d{8}", s):
        return bsn_ok("0" + s)
    return False


# -- student number --------------------------------------------------------------------

_STUDENT = re.compile(r"[sS]?\d{7}|[sS]\d{6}")


def is_student_number(value: str) -> bool:
    """A whole cell of 7 digits, with or without a leading ``s`` (``s123456`` too).

    Only meaningful in a column whose name says "student" (see :func:`column_context`).
    """
    return bool(_STUDENT.fullmatch(value.strip()))


# -- phone numbers ---------------------------------------------------------------------

_PHONE_CHARS = re.compile(r"[+\d\s().\-/]+")
_SEPARATORS = re.compile(r"[\s.\-/]+")
_NL_SERVICE = ("0800", "0900", "0906", "0909")


def is_phone(value: str, context: bool = False) -> bool:
    """A whole cell that is a phone number, Dutch or international.

    * ``+`` or ``00`` and a country code, 9 to 15 digits in all (10 without
      separators after ``+``; ``00`` needs separators unless it is Dutch,
      ``0031`` and 9 digits): ``+31 6 12345678``, ``+44 20 7946 0958``,
      ``0031612345678``;
    * Dutch national: ``0`` and 10 digits in all, written with separators and a
      leading area code of 2 to 4 digits (``040-2474747``, ``(020) 123 4567``,
      ``06 1234 5678``), or as one run of digits when it starts with ``06``
      (``0612345678``). In a column named like a phone column any run of ``0``
      and 9 digits counts. Service numbers (``0800``, ``0900``) do not.

    A run of digits that does not start with ``0`` or ``+`` is never a phone
    number, so timestamps, EANs and ordinary numeric IDs are not.
    """
    s = value.strip()
    if not 9 <= len(s) <= 25 or not _PHONE_CHARS.fullmatch(s):
        return False
    digits = re.sub(r"\D", "", s)
    sep = len(re.sub(r"[+\d]", "", s)) > 0
    if s.startswith("+"):
        rest = s[1:]
        if not rest or rest[0] in "0 " or not 9 <= len(digits) <= 15:
            return False
        if not sep and len(digits) < 10:
            return False
        return _groups_ok(rest)
    if s.startswith("00"):
        after = digits[2:]
        if not after or after[0] == "0" or not 9 <= len(after) <= 15:
            return False
        if not sep:
            return after.startswith("31") and len(after) == 11
        return _groups_ok(s[2:])
    return _is_dutch_national(s, digits, sep, context)


def _groups_ok(text: str) -> bool:
    """Digits in a handful of groups, optionally with a ``(0)`` or an area code in brackets."""
    groups = [g for g in _SEPARATORS.split(text.replace("(", " ").replace(")", " ")) if g]
    return 1 <= len(groups) <= 8 and all(g.isdigit() for g in groups)


def _is_dutch_national(s: str, digits: str, sep: bool, context: bool) -> bool:
    if len(digits) != 10 or digits[0] != "0" or digits[1] == "0":
        return False
    if digits.startswith(_NL_SERVICE):
        return False
    if not sep:
        return digits.startswith("06") or context
    first = _SEPARATORS.split(s.replace("(", " ").replace(")", " ").strip(), maxsplit=1)[0]
    return first.isdigit() and 2 <= len(first) <= 4 and first.startswith("0") and _groups_ok(s)


# -- IPv6 ------------------------------------------------------------------------------


def is_ipv6(value: str) -> bool:
    """A whole cell that is an IPv6 address, written in full or compressed (``::``).

    Parsed with :mod:`ipaddress`, so ``12:30:45`` (a time), ``aa:bb:cc:dd:ee:ff``
    (a MAC address) and ``1:2`` are not. The loopback ``::1`` and the unspecified
    ``::`` are not personal. A ``/64`` prefix length, a ``%eth0`` zone and
    brackets (``[2001:db8::1]``) are accepted.
    """
    s = value.strip()
    if s.count(":") < 2 or len(s) > 60:
        return False
    if s.startswith("["):
        end = s.find("]")
        if end < 0:
            return False
        s = s[1:end]
    s = re.sub(r"/\d{1,3}$", "", s)
    try:
        address = ipaddress.IPv6Address(s)
    except ValueError:
        return False
    return not (address.is_loopback or address.is_unspecified)


# -- postcodes -------------------------------------------------------------------------

_POSTCODE = re.compile(r"([1-9]\d{3})([  ]?)([A-Za-z]{2})")
_POSTCODE_NOT = frozenset({"SA", "SD", "SS"})
#: Letters that follow a number as a unit or an era (``1000 MB``, ``500 BC``): not a postcode
#: when the column has no postcode name.
_POSTCODE_UNITS = frozenset(
    {"BC", "AD", "CE", "BP", "MB", "GB", "KB", "TB", "KG", "MG", "HZ", "MS", "MM", "CM", "KM"}
    | {"ML", "DB", "PX", "KJ", "KW"}
)


def is_postcode(value: str, context: bool = False) -> bool:
    """A whole cell that is a Dutch postcode: four digits (not starting with 0) and two letters.

    ``1234 AB``; the letters ``SA``, ``SD`` and ``SS`` do not occur. Without
    *context* (a column named like a postcode column) the letters must be capitals
    after a space and not a unit (``1000 MB``, ``500 BC``), so ``4521KL`` and
    ``2500 ms`` are not postcodes.
    """
    m = _POSTCODE.fullmatch(value.strip())
    if m is None:
        return False
    letters = m.group(3)
    if letters.upper() in _POSTCODE_NOT:
        return False
    if context:
        return True
    return bool(m.group(2)) and letters.isupper() and letters not in _POSTCODE_UNITS


# -- person names ----------------------------------------------------------------------

_PARTICLES = frozenset(
    {"van", "de", "der", "den", "het", "'t", "ten", "ter", "te", "von", "vom", "zu", "di", "da"}
    | {"del", "della", "la", "le", "el", "al", "bin", "ben", "ibn", "du", "des", "dos", "das"}
    | {"do", "op", "'s"}
)

#: Words that make a value a label or a variable, not a person.
_NOT_A_NAME = frozenset(
    {"group", "condition", "control", "treatment", "test", "task", "scale", "total", "mean"}
    | {"score", "variable", "item", "average", "median", "baseline", "sample", "trial"}
    | {"yes", "no", "male", "female", "man", "woman", "other", "unknown", "none", "true", "false"}
    | {"age", "gender", "sex", "id", "name", "date", "time", "number", "count", "type", "level"}
    | {"university", "school", "department", "faculty", "institute", "company", "team"}
    | {"before", "after", "pre", "post", "high", "low", "medium", "left", "right"}
)

_WORD = re.compile(r"[^\W\d_](?:[^\W\d_]|['\u2019.\-])*")


def is_person_name(value: str, *, minimum_words: int = 1) -> bool:
    """A whole cell that reads like a person's name: 1 to 5 words of letters, capitalised.

    Words may carry a hyphen or apostrophe (``Smit-de Vries``, ``O'Brien``), a
    lower-case particle (``van``, ``de``) may sit between them and an initial
    ends in a full stop (``J.H.``). Digits, ``_``, ``/``, ``@``, a file extension
    or any word that labels a variable or a condition (``Control group``,
    ``Age``) make it not a name. *minimum_words* is the number of words that
    are not particles or initials alone (2 for a ``Name`` column that holds full
    names).
    """
    s = " ".join(value.split())
    if not s or len(s) > 50:
        return False
    words = s.split(" ")
    if len(words) > 5:
        return False
    real = 0
    for word in words:
        low = word.lower()
        if low in _PARTICLES and not word[:1].isupper():
            continue
        if not _WORD.fullmatch(word):
            return False
        if low.rstrip(".") in _NOT_A_NAME:
            return False
        if not word[0].isupper():
            return False
        letters = re.sub(r"[^\w]|\d|_", "", word)
        # an all-capitals word is a name only when it is long enough not to be a code
        if len(letters) >= 2 and word.isupper() and len(words) == 1:
            return False
        if len(letters) == 1 and not word.endswith("."):
            return False  # a lone capital letter is an initial only with a full stop
        real += 1
    return real >= max(minimum_words, 1)


# -- context from the column name ------------------------------------------------------

_BSN_NAMES = ("bsn", "burgerservice", "sofi")
_STUDENT_NAMES = ("student",)
_STUDENT_TOKENS = frozenset({"snummer", "snumber", "snr", "snum", "stud", "studnr", "stnr"})
_PHONE_TOKENS = frozenset(
    {"phone", "telefoon", "tel", "telefon", "mobile", "mobiel", "gsm", "cell", "cellphone", "mob"}
    | {"handy", "telephone", "phonenumber", "telefoonnummer", "mobielnummer", "gsmnummer"}
)
_POSTCODE_NAMES = ("postcode", "postalcode", "zipcode", "postcd")
_POSTCODE_TOKENS = frozenset({"postcode", "zip", "zipcode", "postal", "postalcode"})
_IP_TOKENS = frozenset({"ip", "ipv6", "ipaddress", "ipadres", "ipaddr"})

#: Words next to ``name`` that say the column is about a person.
_PERSON_QUALIFIERS = frozenset(
    {"participant", "respondent", "student", "patient", "teacher", "parent", "child", "person"}
    | {"employee", "author", "rater", "interviewer", "informant", "speaker", "customer", "client"}
    | {"contact", "owner", "pupil", "learner", "subject", "proband", "deelnemer"}
    | {"leerling", "docent", "ouder", "kind", "patient", "persoon"}
)
#: Words next to ``name`` that say the column is not about a person.
_NOT_PERSON_QUALIFIERS = frozenset(
    {"file", "filename", "variable", "var", "column", "col", "scale", "item", "stimulus", "task"}
    | {"condition", "group", "experiment", "study", "label", "sheet", "table", "dataset", "package"}
    | {"function", "field", "path", "folder", "test", "measure", "country", "city", "language"}
    | {"brand", "product", "company", "organisation", "organization", "university", "school"}
    | {"site", "location", "gene", "species", "treatment", "software", "module", "param"}
    | {"parameter", "short", "long", "display", "dir", "directory", "key", "type"}
    | {"bestand", "bestandsnaam", "variabele", "conditie", "groep", "taak", "schaal", "stad"}
    | {"land", "taal", "locatie", "onderzoek", "studie", "instelling", "faculteit", "afdeling"}
)
#: Name-column words that are a person's name by themselves.
_SPECIFIC_NAME_WORDS = frozenset(
    {"voornaam", "achternaam", "tussenvoegsel", "geslachtsnaam", "surname", "lastname"}
    | {"firstname", "fullname", "givenname", "familyname", "middlename", "vorname", "nachname"}
    | {"fname", "lname", "volledigenaam", "roepnaam", "meisjesnaam"}
)
_GENERIC_NAME_WORDS = frozenset({"name", "naam", "names", "namen"})
_NAME_PARTS = frozenset({"first", "last", "full", "given", "family", "middle", "sur", "birth"})


def name_column_kind(name: str) -> str | None:
    """How a column name speaks of a person's name: ``"specific"``, ``"generic"`` or ``None``.

    ``"specific"``: ``voornaam``, ``first_name``, ``surname``, ``participant_name``:
    a person's name by the name alone. ``"generic"``: ``name`` or ``naam``, with
    nothing or only an unknown word next to it: the values decide, and a full name
    (two words or more) is needed. ``None``: no name word, or a word that says the
    column names something else (``file_name``, ``variable name``, ``country``).
    """
    tokens = column_tokens(name)
    if not tokens:
        return None
    if any(t in _NOT_PERSON_QUALIFIERS for t in tokens):
        return None
    if any(t in _SPECIFIC_NAME_WORDS for t in tokens):
        return "specific"
    if not any(t in _GENERIC_NAME_WORDS for t in tokens):
        return None
    if any(t in _PERSON_QUALIFIERS or t in _NAME_PARTS for t in tokens):
        return "specific"
    others = [t for t in tokens if t not in _GENERIC_NAME_WORDS and not t.isdigit()]
    return "generic" if len(others) <= 1 else None


def column_context(name: str) -> frozenset[str]:
    """The detector ids a column's name points at (``bsn``, ``phone``, ``student-number``, ...)."""
    tokens = column_tokens(name)
    squashed = _squash(tokens)
    found: set[str] = set()
    if any(part in squashed for part in _BSN_NAMES):
        found.add("bsn")
    if (
        any(part in squashed for part in _STUDENT_NAMES)
        or _STUDENT_TOKENS & set(tokens)
        or (tokens[:1] == ["s"] and set(tokens[1:2]) & {"number", "nummer", "nr", "no", "id"})
    ):
        found.add("student-number")
    if _PHONE_TOKENS & set(tokens) or any(p in squashed for p in ("phone", "telefoon", "mobiel")):
        found.add("phone")
    if _IP_TOKENS & set(tokens):
        found.add("ipv6")
    if _POSTCODE_TOKENS & set(tokens) or any(p in squashed for p in _POSTCODE_NAMES):
        found.add("postcode")
    kind = name_column_kind(name)
    if kind:
        found.add("person-name")
    return frozenset(found)


# -- detectors and the column judgement -----------------------------------------------


@dataclass(frozen=True)
class Detector:
    """One kind of personal data and how many cells make a column.

    ``min_hits`` and ``min_share`` apply to a column whose name gives no hint,
    ``min_hits_named`` and ``min_share_named`` to one whose name does. A detector
    that ``needs_name`` is only run on a column whose name points at it.
    """

    id: str  # the rule id, e.g. "student-number"
    item: str  # the checklist item, e.g. "pii_student_number"
    title: str  # the checklist title
    noun: str  # for the sentences: "Dutch citizen service numbers (BSN)"
    min_hits: int
    min_share: float
    min_hits_named: int
    min_share_named: float
    needs_name: bool = False


DETECTORS: tuple[Detector, ...] = (
    Detector(
        "bsn",
        "pii_bsn",
        "No Dutch citizen service numbers (BSN)",
        "Dutch citizen service numbers (BSN)",
        min_hits=3,
        min_share=0.5,
        min_hits_named=1,
        min_share_named=0.3,
    ),
    Detector(
        "student-number",
        "pii_student_number",
        "No student numbers",
        "student numbers",
        min_hits=1,
        min_share=0.5,
        min_hits_named=1,
        min_share_named=0.5,
        needs_name=True,
    ),
    Detector(
        "phone",
        "pii_phone",
        "No phone numbers",
        "phone numbers",
        min_hits=2,
        min_share=0.5,
        min_hits_named=1,
        min_share_named=0.3,
    ),
    Detector(
        "ipv6",
        "pii_ipv6",
        "No IPv6 addresses",
        "IPv6 addresses",
        min_hits=1,
        min_share=0.3,
        min_hits_named=1,
        min_share_named=0.3,
    ),
    Detector(
        "postcode",
        "pii_postcode",
        "No Dutch postcodes",
        "Dutch postcodes",
        min_hits=2,
        min_share=0.5,
        min_hits_named=1,
        min_share_named=0.3,
    ),
    Detector(
        "person-name",
        "pii_name",
        "No columns with people's names",
        "people's names",
        min_hits=3,
        min_share=0.7,
        min_hits_named=3,
        min_share_named=0.7,
        needs_name=True,
    ),
)


def _tester(detector_id: str, context: bool, kind: str | None) -> Callable[[str], bool]:
    if detector_id == "bsn":
        return lambda v: is_bsn(v, context)
    if detector_id == "student-number":
        return is_student_number
    if detector_id == "phone":
        return lambda v: is_phone(v, context)
    if detector_id == "ipv6":
        return is_ipv6
    if detector_id == "postcode":
        return lambda v: is_postcode(v, context)
    return lambda v: is_person_name(v, minimum_words=2 if kind == "generic" else 1)


def judge_column(
    name: str, values: Iterable[str], detectors: Sequence[Detector] = DETECTORS
) -> list[tuple[Detector, int, int, bool]]:
    """Which detectors flag a column: ``(detector, hits, non_empty, named)``.

    *values* are the column's cells as text. ``hits`` counts the cells that pass
    the detector's value test, ``non_empty`` the cells that are not empty, ``named``
    says whether the column's name points at the detector.
    """
    cells = [v.strip() for v in values if isinstance(v, str)]
    cells = [v for v in cells if v.casefold() not in _EMPTY]
    non_empty = len(cells)
    if non_empty == 0:
        return []
    context = column_context(name)
    kind = name_column_kind(name)
    short = [v for v in cells if len(v) <= _MAX_CELL]
    out: list[tuple[Detector, int, int, bool]] = []
    for det in detectors:
        named = det.id in context
        if det.needs_name and not named:
            continue
        test = _tester(det.id, named, kind)
        hits = sum(1 for v in short if test(v))
        need_hits = det.min_hits_named if named else det.min_hits
        need_share = det.min_share_named if named else det.min_share
        if hits >= need_hits and hits / non_empty >= need_share:
            out.append((det, hits, non_empty, named))
    return out
