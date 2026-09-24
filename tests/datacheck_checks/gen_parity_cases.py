"""Generate ``parity/cases/datacheck_checks.yaml``.

Run from the repository root::

    .venv/bin/python tests/datacheck_checks/gen_parity_cases.py
    .venv/bin/python -m parity generate --area datacheck_checks

Every string in the YAML is double-quoted (R's YAML reader would otherwise
turn ``"yes"``/``"1.0"`` into logicals/numbers). Long vectors are ``$expr``
blocks with equivalent R and Python code; the fixture files come from
``tests/datacheck_checks/data/make_fixtures.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "datacheck_checks.yaml"
DATA = "tests/datacheck_checks/data"
CK = "pytacheck.datacheck.checks."
PH = "tests.datacheck_checks.parity_helpers."

CASES: list[dict[str, Any]] = []


def add(cid: str, r: str, args: dict[str, Any], py: str | None = None, **extra: Any) -> None:
    if py is None:
        py = CK + (("_" + r[1:].replace(".", "_")) if r.startswith(".") else r)
    spec: dict[str, Any] = {"id": cid, "r": r, "py": py, "args": args}
    spec.update(extra)
    CASES.append(spec)


def dbl(*v: Any) -> dict[str, Any]:
    return {"$dbl": [None if x is None else float(x) for x in v]}


def int_(*v: Any) -> dict[str, Any]:
    return {"$int": list(v)}


def chr_(*v: Any) -> dict[str, Any]:
    return {"$chr": list(v)}


def lgl(*v: Any) -> dict[str, Any]:
    return {"$lgl": list(v)}


def ex(r: str, py: str) -> dict[str, Any]:
    return {"$expr": {"r": r, "py": py}}


def df(**cols: Any) -> dict[str, Any]:
    return {"$df": cols}


def lst(*items: Any) -> dict[str, Any]:
    return {"$list": list(items)}


def fpath(name: str) -> dict[str, Any]:
    return {"$file": f"{DATA}/{name}"}


NULL = {"$null": True}


def read_head(name: str, n_rows: float = float("inf")) -> dict[str, Any]:
    return {
        "$call": {
            "r": "data_read_head",
            "py": "pytacheck.datacheck.files.data_read_head",
            "args": {"path": fpath(name), "n_rows": n_rows},
        }
    }


# -- data_check_scale_values ----------------------------------------------------------

L15 = ("rep(1:5, 40)", "[*range(1, 6)] * 40")
L17 = ("rep(1:7, 30)", "[*range(1, 8)] * 30")
L28 = ("rep(2:8, 30)", "[*range(2, 9)] * 30")


def vec(base: tuple[str, str], *extra: Any) -> dict[str, Any]:
    if not extra:
        return ex(base[0], base[1])
    r_extra = ", ".join("NA" if e is None else str(e) for e in extra)
    py_extra = ", ".join(repr(e) for e in extra)
    return ex(f"c({base[0]}, {r_extra})", f"{base[1]} + [{py_extra}]")


def scale_values() -> None:
    f = "data_check_scale_values"
    add(f"{f}.sentinels", f, {"x": vec(L15, -99, 999)})
    add(f"{f}.typo_and_missing", f, {"x": vec(L15, 55, -99)})
    add(f"{f}.typo33", f, {"x": vec(L17, 33)})
    add(f"{f}.unexplained9", f, {"x": vec(L17, 9)})
    add(f"{f}.clean", f, {"x": vec(L17)})
    add(f"{f}.adjacent_overshoot", f, {"x": vec(L15, 6)})
    add(f"{f}.blood_pressure", f,
        {"x": ex("rep(c(118, 121, 125, 130, 112, 140, 99, 135), 25)",
                 "[118.0, 121.0, 125.0, 130.0, 112.0, 140.0, 99.0, 135.0] * 25")})  # fmt: skip
    add(f"{f}.noninteger", f,
        {"x": ex("rep(c(1.5, 2.25, 3.75, 4.5), 50)", "[1.5, 2.25, 3.75, 4.5] * 50")})
    add(f"{f}.age", f, {"x": ex("rep(18:65, 5)", "[*range(18, 66)] * 5")})
    add(f"{f}.count", f, {"x": ex("rep(0:30, 7)", "[*range(0, 31)] * 7")})
    add(f"{f}.valid_values_flags6", f, {"x": vec(L15, 6), "valid_values": int_(1, 2, 3, 4, 5)})
    add(f"{f}.valid_values_allows6", f,
        {"x": vec(L15, 6), "valid_values": int_(1, 2, 3, 4, 5, 6)})
    add(f"{f}.valid_range", f,
        {"x": ex("c(rep(1:7, 9), 9)", "[*range(1, 8)] * 9 + [9]"), "valid_range": dbl(1, 7)})
    add(f"{f}.declared77", f, {"x": vec(L15, 77), "declared": dbl(77)})
    add(f"{f}.declared_neg8", f, {"x": vec(L17, -8), "declared": dbl(-8)})
    add(f"{f}.declared_absent", f, {"x": vec(L15), "declared": dbl(77)})
    add(f"{f}.endpoints", f, {"x": vec(L28), "valid_values": dbl(2, 8)})
    add(f"{f}.endpoints_9999", f, {"x": vec(L28, 9999), "valid_values": dbl(2, 8)})
    add(f"{f}.contiguous_2_8", f, {"x": vec(L28), "valid_values": int_(2, 3, 4, 5, 6, 7, 8)})
    add(f"{f}.coverage_discard", f, {"x": vec(L15, -99), "valid_values": dbl(50, 60)})
    add(f"{f}.character", f, {"x": chr_("a", "b")})
    add(f"{f}.empty", f, {"x": dbl()})
    add(f"{f}.all_na", f, {"x": dbl(None, None)})
    add(f"{f}.n_max", f, {"x": vec(L15, 97, 98, 99, 997, 998), "n_max": 2})
    add(f"{f}.noninteger_range_error", f,
        {"x": ex("c(rep(c(1.5, 2.5, 3.5), 20), 99)", "[1.5, 2.5, 3.5] * 20 + [99.0]"),
         "valid_range": dbl(1.5, 3.5)})  # fmt: skip
    add(f"{f}.code_set_interior", f,
        {"x": ex("c(rep(c(1, 2, 3, 9), 20), 5)", "[1.0, 2.0, 3.0, 9.0] * 20 + [5.0]"),
         "valid_values": dbl(1, 2, 3, 9)})  # fmt: skip
    add(f"{f}.valid_values_character", f,
        {"x": ex("rep(1:5, 10)", "[*range(1, 6)] * 10"), "valid_values": chr_("1", "5")})
    add(f"{f}.integer_with_na", f,
        {"x": ex("c(rep(1:5, 40), NA, 99L)", "[*range(1, 6)] * 40 + [None, 99]")})
    add(f"{f}.infinite", f,
        {"x": ex("c(rep(1:5, 40), Inf, -99)", "[*range(1, 6)] * 40 + [float('inf'), -99]")})
    add(f"{f}.logical", f, {"x": lgl(True, False, True)})
    add(f"{f}.sentinels_arg", f, {"x": vec(L15, 42), "sentinels": dbl(42)})


# -- outliers / constant / empty / design / spss -----------------------------------------


def outliers() -> None:
    f = "data_check_outliers"
    add(f"{f}.basic", f, {"x": dbl(*range(1, 11), 500)})
    add(f"{f}.clean", f, {"x": dbl(*range(1, 21))})
    add(f"{f}.too_few", f, {"x": dbl(1, 2)})
    add(f"{f}.constant", f, {"x": dbl(*([5] * 20))})
    add(f"{f}.character", f, {"x": chr_("a", "b")})
    add(f"{f}.with_na", f, {"x": dbl(1, 2, 3, None, 4, 5, 100)})
    add(f"{f}.infinite", f, {"x": dbl(1, 2, 3, 4, float("inf"))})
    add(f"{f}.iqr_nan", f, {"x": dbl(1, float("inf"), float("inf"), float("inf"))})
    add(f"{f}.n_max", f, {"x": dbl(*range(10, 20), 100, 200, 300, -100), "n_max": 2})
    add(f"{f}.k3", f, {"x": dbl(*range(10, 20), 30, 60), "k": 3})
    add(f"{f}.integer", f, {"x": int_(*range(1, 11), 123456)})
    add(f"{f}.small_decimals", f, {"x": dbl(0.001, 0.002, 0.0015, 0.0012, 0.0018, 5.5)})
    add(f"{f}.logical", f, {"x": lgl(True, False, True, True, False)})


def constant() -> None:
    f = "data_check_constant"
    add(f"{f}.numeric", f, {"x": dbl(*([5] * 10))})
    add(f"{f}.near", f, {"x": ex('c(rep("a", 99), "b")', '["a"] * 99 + ["b"]')})
    add(f"{f}.balanced", f, {"x": chr_(*(["a"] * 5 + ["b"] * 5))})
    add(f"{f}.all_na", f, {"x": dbl(None, None)})
    add(f"{f}.empty", f, {"x": chr_()})
    add(f"{f}.logical", f, {"x": lgl(True, True, None, True)})
    add(f"{f}.float_labels", f, {"x": ex("c(0.1 + 0.2, 0.3, 0.3)", "[0.1 + 0.2, 0.3, 0.3]")})
    add(f"{f}.tie_sorted", f, {"x": chr_("b", "a"), "threshold": 0.5})
    add(f"{f}.factor_unused_level", f,
        {"x": ex('factor(c("x", "x"), levels = c("x", "y"))',
                 'pd.Series(pd.Categorical(["x", "x"], categories=["x", "y"]))')})  # fmt: skip
    add(f"{f}.threshold", f, {"x": dbl(*([1] * 9), 2), "threshold": 0.8})
    add(f"{f}.sci_label", f, {"x": dbl(1e5, 1e5, 1e5)})
    add(f"{f}.collation", f,
        {"x": chr_("b", "B", "a", "A", "_x", "b", "B", "a", "A", "_x"), "threshold": 0.2})
    add(f"{f}.integer_near", f, {"x": int_(*([3] * 199), 4)})


def empty() -> None:
    f = "data_check_empty"
    add(f"{f}.numeric_na", f, {"x": dbl(None, None, None, None, None)})
    add(f"{f}.single_na", f, {"x": dbl(None)})
    add(f"{f}.blank_text", f, {"x": chr_(None, "", "  ")})
    add(f"{f}.filled", f, {"x": dbl(None, 1)})
    add(f"{f}.zero_length", f, {"x": dbl()})
    add(f"{f}.logical_na", f, {"x": lgl(None, None)})
    add(f"{f}.text", f, {"x": chr_("a")})
    add(f"{f}.logical", f, {"x": lgl(False)})


def design_name() -> None:
    f = "data_check_design_name"
    add(f"{f}.vector", f, {"col": chr_(
        "condition", "exp_cond", "Group", "treatment_arm", "cond1", "age", "charm",
        "response", "grp.2", "Dose", "manipulation_check", "intervention", "treated",
        "conditions", "arm", "my group", "ARM-3", None)})  # fmt: skip
    add(f"{f}.scalar", f, {"col": "Condition"})


def spss_filter() -> None:
    f = "data_check_spss_filter"
    add(f"{f}.all_selected", f, {"col": "filter_$", "x": dbl(*([1] * 10))})
    add(f"{f}.some_selected", f, {"col": "filter_.", "x": dbl(1, 1, 1, 0, 0)})
    add(f"{f}.other_name", f, {"col": "excluded", "x": dbl(1, 1, 0)})
    add(f"{f}.text_values", f, {"col": "FILTER_", "x": chr_("1", "0", "x")})
    add(f"{f}.all_na", f, {"col": "filter_$", "x": dbl(None, None)})
    add(f"{f}.logical", f, {"col": "filter_", "x": lgl(True, False, True)})
    add(f"{f}.na_name", f, {"col": {"$NA": True}, "x": dbl(1, 1)})


def text_checks() -> None:
    f = "data_check_case_issues"
    add(f"{f}.basic", f, {"x": chr_("Male", "male", "Female")})
    add(f"{f}.none", f, {"x": chr_("Male", "Female")})
    add(f"{f}.numeric", f, {"x": dbl(1, 2, 3)})
    add(f"{f}.groups", f,
        {"x": chr_("Yes", "YES", "yes", "no", "No", None, "", " ", "maybe", "Yes")})
    add(f"{f}.unicode", f, {"x": chr_("ÄRGER", "ärger", "Ärger")})
    add(f"{f}.factor", f,
        {"x": ex('factor(c("a", "A", "b"))', 'pd.Series(pd.Categorical(["a", "A", "b"]))')})
    add(f"{f}.empty", f, {"x": chr_(None, "")})

    f = "data_check_whitespace"
    add(f"{f}.basic", f, {"x": chr_("Male ", "Male", "Female")})
    add(f"{f}.none", f, {"x": chr_("Male", "Female")})
    add(f"{f}.numeric", f, {"x": dbl(1, 2, 3)})
    add(f"{f}.many", f, {"x": ex('paste0(" v", 1:12)', '[f" v{i}" for i in range(1, 13)]')})
    add(f"{f}.blank_tab", f, {"x": chr_("  ", " a", "b\t", None, "c\n", " a")})

    f = "data_check_numeric_in_text"
    add(f"{f}.basic", f,
        {"x": ex('c(as.character(1:20), "n/a", ">100")', '[str(i) for i in range(1, 21)] + ["n/a", ">100"]')})  # fmt: skip
    add(f"{f}.categorical", f, {"x": chr_("apple", "pear", "kiwi", "plum", "fig")})
    add(f"{f}.all_numeric", f, {"x": ex("as.character(1:20)", "[str(i) for i in range(1, 21)]")})
    add(f"{f}.comma_decimal", f, {"x": chr_("1,5", "2,5", "3,5", "4,5", "x")})
    add(f"{f}.too_few", f, {"x": chr_("1", "2", "x")})
    add(f"{f}.numeric", f, {"x": dbl(1, 2, 3, 4, 5, 6)})
    add(f"{f}.args", f,
        {"x": chr_("1", "2", "3", "a", "b", " c ", "4", "", None), "threshold": 0.5, "n_max": 1})
    add(f"{f}.nan_inf", f, {"x": chr_("1", "2", "3", "4", "NaN", "Inf", "1e5", "0x1A", "-inf")})


def colname() -> None:
    f = "data_check_colname"
    emergent = "_H:\t$Name\t%Input[4:0,0,0,0]<4:1,8,1,1>\t%Input[4:0,1,0,0]"
    names = {
        "emergent": emergent,
        "colon": "score:pre",
        "tab": "a\tb",
        "quote": 'he said "hi"',
        "slash": "ratio a/b",
        "padded": " score ",
        "long65": "a" * 65,
        "long64": "a" * 64,
        "ordinary": "reaction_time",
        "spaces_dots": "orginal hip.go value",
        "unicode": "âge",
        "backslash": "a\\b",
        "many_illegal": "a|b*c?d<e>f|g",
        "newline": "line1\nline2\r",
    }
    for k, v in names.items():
        add(f"{f}.{k}", f, {"col_name": v})
    add(f"{f}.na", f, {"col_name": chr_(None)})
    add(f"{f}.null", f, {"col_name": NULL})
    add(f"{f}.max_chars", f, {"col_name": "abcdefghijk", "max_chars": 10})
    add(f"{f}.two_names", f, {"col_name": chr_("a", "b")})

    f = "data_check_colname_collisions"
    add(f"{f}.ipa", f, {"col_names": chr_("t'", "t\u032a", "score", "kw")})
    add(f"{f}.identical", f, {"col_names": chr_("id", "id", "x")})
    add(f"{f}.distinct", f, {"col_names": chr_("a", "b", "a_1")})
    add(f"{f}.modifier_letter", f, {"col_names": chr_("k", "k\u02b7")})
    add(f"{f}.six_way", f, {"col_names": chr_("a b", "a.b", "a_b", "a-b", "a/b", "a+b")})
    add(f"{f}.three_identical", f, {"col_names": chr_("x", "x", "x")})
    add(f"{f}.mixed", f, {"col_names": chr_("a b", "a b", "a.b")})
    add(f"{f}.empty", f, {"col_names": chr_()})


# -- PII ---------------------------------------------------------------------------------


def pii() -> None:
    f = "data_check_pii_values"
    add(f"{f}.email", f, {"x": chr_("a@b.com", "c@d.org", "e@f.net")})
    add(f"{f}.ip", f, {"x": chr_("192.168.0.1", "10.0.0.255", "8.8.8.8")})
    add(f"{f}.ssn", f, {"x": chr_("123-45-6789", "987-65-4321", "222-33-4444")})
    add(f"{f}.cards", f, {"x": chr_("4111111111111111", "5500005555555559", "4012888888881881")})
    add(f"{f}.ordinary", f, {"x": chr_("1", "2", "3", "4", "5", "3", "2")})
    add(f"{f}.decimals", f,
        {"x": ex("as.character(seq(10, 30, length.out = 40))",
                 "[pc._r.as_character(10 + i * 20 / 39) for i in range(40)]")})  # fmt: skip
    add(f"{f}.words", f, {"x": chr_("yes", "no", "maybe", "yes", "no")})
    add(f"{f}.rare_emails", f,
        {"x": ex('c(rep("n/a", 38), "a@b.com", "c@d.com")', '["n/a"] * 38 + ["a@b.com", "c@d.com"]')})  # fmt: skip
    add(f"{f}.single_email", f,
        {"x": ex('c(rep("none", 39), "x@y.com")', '["none"] * 39 + ["x@y.com"]')})
    add(f"{f}.luhn_fail", f, {"x": chr_(*(["1234567890123456"] * 4))})
    add(f"{f}.embedded_run", f, {"x": chr_(*(["A1234567890123456Z"] * 6))})
    add(f"{f}.dates", f, {"x": chr_(*(["2021-05-03"] * 6))})
    add(f"{f}.long_ids", f,
        {"x": ex("as.character(100000000 + 7919 * (1:40))", "[str(100000000 + 7919 * i) for i in range(1, 41)]")})  # fmt: skip
    add(f"{f}.prices", f,
        {"x": ex('sprintf("%.2f", 1 + (1:40) * 2.37)', '[f"{1 + i * 2.37:.2f}" for i in range(1, 41)]')})  # fmt: skip
    add(f"{f}.timestamps", f, {"x": chr_(*(["2019-01-15 14:32:07"] * 10))})
    add(f"{f}.phone", f, {"x": chr_(*(["+31612345678"] * 5))})
    add(f"{f}.phone_local", f, {"x": chr_(*(["040-247 1234"] * 8))})
    add(f"{f}.mixed_patterns", f,
        {"x": chr_("a@b.com", "192.168.0.1", "123-45-6789", "4111 1111 1111 1111", "x")})
    add(f"{f}.ssn_exclusions", f,
        {"x": chr_("000-12-3456", "666-12-3456", "900-12-3456", "123-00-4567", "123-45-0000")})
    add(f"{f}.numeric_input", f, {"x": dbl(1, 2, 3)})
    add(f"{f}.too_few", f, {"x": chr_("a@b.com", "c@d.com", None, "  ")})
    add(f"{f}.ms_timestamps", f, {"x": chr_(*(["1595957929810"] * 5))})
    add(f"{f}.card_schemes", f,
        {"x": chr_("378282246310005", "6011111111111117", "3530111333300000", "30569309025904",
                   "2223003122003222", "6445644564456445", "36000000000008")})  # fmt: skip
    add(f"{f}.broad_arg", f, {"x": chr_("a@b.com", "x", "y"), "broad_min_frac": 0.9})

    for s in ("4111111111111111", "4111111111111112", "4111-1111-1111-1111", "123", "12345678901234567",
              "378282246310005", "0000000000000"):  # fmt: skip
        add(f".pii_luhn_ok.{s}", ".pii_luhn_ok", {"s": s})
    for s in ("4111111111111111", "341111111111111", "3411111111111111", "1595957929810",
              "6011111111111117", "5500005555555559", "2720999999999996", "30569309025904",
              "4222222222222"):  # fmt: skip
        add(f".pii_card_ok.{s}", ".pii_card_ok", {"s": s})

    for i, s in enumerate(("RecipientFirstName", "IPAddress", "ZIPcode", "phone_number", "__x__",
                           "ABC", "a1B2", "", "date.of.birth", "ÂgeParticipant", "TIPI_1")):  # fmt: skip
        add(f".pii_split_name.{i}", ".pii_split_name", {"x": s})
    add(".pii_split_name.na", ".pii_split_name", {"x": chr_(None)})

    f = "data_check_pii_name"
    for s in ("participant_email", "DOB", "home_address", "latitude", "RecipientFirstName",
              "PeronalData_fullname", "IPAddress", "description", "score", "reaction_time",
              "experimentName", "trial_name", "videoName", "fileName", "Conditionname",
              "Rolename", "telefoonnummer", "geboortedatum", "first_name", "date_of_birth",
              "E-mail", "ip", "user.name", "microphone", "Zip Code", "e_mail_address"):  # fmt: skip
        add(f"{f}.{s}", f, {"col_name": s})
    add(f"{f}.na", f, {"col_name": chr_(None)})
    add(f"{f}.empty", f, {"col_name": ""})
    add(f"{f}.null", f, {"col_name": NULL})
    add(f"{f}.punctuation", f, {"col_name": "___"})

    f = "data_check_pii_geo"
    add(f"{f}.latitude", f, {"col_name": "latitude", "x": dbl(52.37, 4.89, 51.5)})
    add(f"{f}.gps", f, {"col_name": "gps", "x": dbl(1, 2, 3)})
    add(f"{f}.plain_name", f,
        {"col_name": "x", "x": ex("seq(10, 30, length.out = 40)", "[10 + i * 20 / 39 for i in range(40)]")})  # fmt: skip
    add(f"{f}.temperature", f, {"col_name": "temperature", "x": dbl(20.1, 21.5, 19.8)})
    add(f"{f}.qualtrics_pair", f,
        {"col_name": "LocationLatitude", "x": dbl(52.37, 4.89, 51.5),
         "sibling_names": chr_("LocationLatitude", "LocationLongitude")})  # fmt: skip
    add(f"{f}.gps_lat", f,
        {"col_name": "gps_lat", "x": dbl(52.37, 4.89, 51.5), "sibling_names": chr_("gps_lat", "gps_lon")})  # fmt: skip
    psych = chr_("id", "lat", "rt", "accuracy")
    add(f"{f}.latency_seconds", f,
        {"col_name": "lat", "x": ex("round(seq(0.3, 3.5, length.out = 50), 3)",
                                    "[pc._r.r_round(0.3 + i * 3.2 / 49, 3) for i in range(50)]"),
         "sibling_names": psych})  # fmt: skip
    add(f"{f}.latin_square", f,
        {"col_name": "lat", "x": ex("rep(0:1, 50)", "[0, 1] * 50"), "sibling_names": psych})
    add(f"{f}.loneliness", f,
        {"col_name": "lon", "x": ex("rep(1:7, 10)", "[*range(1, 8)] * 10"),
         "sibling_names": chr_("id", "lon", "rt")})  # fmt: skip
    add(f"{f}.with_partner", f,
        {"col_name": "lat", "x": ex("50 + (1:40) / 10", "[50 + i / 10 for i in range(1, 41)]"),
         "sibling_names": chr_("id", "lat", "lon")})  # fmt: skip
    add(f"{f}.gps_no_partner", f,
        {"col_name": "gps", "x": ex("seq(-10, 10, length.out = 20)", "[-10 + i * 20 / 19 for i in range(20)]"),
         "sibling_names": psych})  # fmt: skip
    add(f"{f}.out_of_range", f,
        {"col_name": "lat", "x": dbl(650, 700, 800, 910), "sibling_names": chr_("id", "lat", "lon")})
    add(f"{f}.no_siblings", f,
        {"col_name": "lat", "x": dbl(52.1, 52.2, 52.3), "sibling_names": chr_()})
    add(f"{f}.lat_lon_name", f,
        {"col_name": "lat_lon", "x": dbl(120, 130, 140), "sibling_names": chr_("lat_lon", "longitude")})  # fmt: skip
    add(f"{f}.comma_decimals", f, {"col_name": "latitude", "x": chr_("52,37", "4,89", "51,5")})
    add(f"{f}.null_name", f, {"col_name": NULL, "x": dbl(1, 2, 3)})
    add(f"{f}.few_values", f, {"col_name": "latitude", "x": dbl(500, 600)})
    add(f"{f}.longitude_range", f, {"col_name": "Longitude", "x": dbl(150, -170, 100)})
    add(f"{f}.latitude_range", f, {"col_name": "Latitude", "x": dbl(150, -170, 100)})

    f = "data_check_pii_freetext"
    prose = chr_(
        "I really enjoyed the study and thought it was interesting overall today.",
        "The instructions were a bit unclear at the start but fine later on here.",
        "My name is Jane and I live in Amsterdam near the central train station.",
        "Great experience overall, I would happily participate again next time.",
        "The room felt cold and I had some trouble focusing on the main tasks.",
    )
    add(f"{f}.prose", f, {"x": prose})
    add(f"{f}.codes", f, {"x": chr_("yes", "no", "yes", "maybe", "no", "yes", "no")})
    add(f"{f}.numbers", f,
        {"x": ex('sprintf("%.4f", 1000 + (1:30) * 13.1234)', '[f"{1000 + i * 13.1234:.4f}" for i in range(1, 31)]')})  # fmt: skip
    add(f"{f}.response_ids", f,
        {"x": ex('paste0("R_", substr(sprintf("%020d", (1:30) * 7919), 5, 20), "ABCDEFGH")',
                 '["R_" + f"{i * 7919:020d}"[4:20] + "ABCDEFGH" for i in range(1, 31)]')})  # fmt: skip
    add(f"{f}.urls", f,
        {"x": ex('paste0("https://example.com/path/", 1:30, "/item?x=", 1:30)',
                 '[f"https://example.com/path/{i}/item?x={i}" for i in range(1, 31)]')})  # fmt: skip
    add(f"{f}.paths", f,
        {"x": ex('paste0("C:/data/study/participant_", 1:30, "/trial_data.csv")',
                 '[f"C:/data/study/participant_{i}/trial_data.csv" for i in range(1, 31)]')})  # fmt: skip
    add(f"{f}.digit_prose", f,
        {"x": ex('paste("12345 67890 12345 67890 12345 67890 12345 ab", 1:6)',
                 '[f"12345 67890 12345 67890 12345 67890 12345 ab {i}" for i in range(1, 7)]')})  # fmt: skip
    add(f"{f}.numeric", f, {"x": dbl(1, 2, 3, 4, 5, 6)})
    add(f"{f}.short_threshold", f,
        {"x": chr_("a b c", "d e f", "g h i", "j k l", "m n o"), "min_median_chars": 3})
    add(f"{f}.even_median", f,
        {"x": chr_("aaaa bbbb cccc", "dddd eeee ffff gggg", "hhhh iiii", "jjjj kkkk llll mmmm"),
         "min_median_chars": 10, "min_unique_frac": 0.5})  # fmt: skip
    add(f"{f}.too_few", f, {"x": chr_("one two three", None, "")})


# -- demographics ----------------------------------------------------------------------


def demographics() -> None:
    f = "data_check_demographic"
    cases: list[tuple[str, str, Any]] = [
        ("age", "age", dbl(23, 45, 31, 29, 55, 19, 67, 40)),
        ("age_years", "Age_years", ex("18:35", "list(range(18, 36))")),
        ("participant_age", "participant_age", dbl(34, 29, 41)),
        ("age_sentinel", "age", dbl(23, 45, 31, 29, 55, 19, 67, 999)),
        ("age_birth_years", "age", dbl(1990, 1985, 2001, 1979, 1995, 2003)),
        ("age_text", "age", chr_("young", "old", "middle", "young", "old")),
        ("gender_words", "gender", chr_("Male", "Female", "Female", "Male", "Non-binary")),
        ("sex_coded", "sex", dbl(1, 2, 1, 2, 1, 2)),
        ("geslacht", "Geslacht", chr_("man", "vrouw", "man")),
        ("gender_fruit", "gender", chr_("apple", "banana", "cherry", "kiwi", "melon")),
        ("condition_coded", "condition", dbl(1, 2, 1, 2, 1, 2)),
        ("race_words", "race", chr_("White", "Black", "Asian", "Hispanic", "Other")),
        ("ethnicity", "ethnicity", chr_("Hispanic", "Non-Hispanic")),
        ("race_coded", "race_ethnicity", dbl(1, 2, 3, 4, 5)),
        ("race_prose", "race", ex('rep(paste(rep("word", 30), collapse = " "), 5)',
                                  '[" ".join(["word"] * 30)] * 5')),
        ("empty_name", "", dbl(1, 2, 3)),
        ("race_many", "race", ex('paste0("r", 1:40)', '[f"r{i}" for i in range(1, 41)]')),
        ("age_comma", "leeftijd", chr_("23,5", "45", "31", "29")),
        ("gender_numeric_wide", "sex", dbl(1, 2, 3, 4, 5)),
        ("gender_decimal", "sex", dbl(1.5, 2, 1)),
        ("age_mostly_text", "alter", chr_("20", "x", "y", "z", "30")),
        ("race_decimal", "race", dbl(1.5, 2, 3)),
        ("age_only_sentinels", "age", dbl(-99, 999, 99)),
        ("years_age", "yrs_age", dbl(20, 30, 40)),
        ("agejaren", "age_jaren", dbl(20, 30, 40)),
        ("race_logical", "race", lgl(True, False, True)),
    ]
    for cid, nm, x in cases:
        add(f"{f}.{cid}", f, {"col_name": nm, "x": x})
    for nm in ("percentage", "page", "average_rt", "image_id", "coverage", "storage", "language",
               "damage", "usage", "agent"):  # fmt: skip
        add(f"{f}.false_friend.{nm}", f, {"col_name": nm, "x": dbl(10, 20, 30, 40, 50)})
    add(f"{f}.na_name", f, {"col_name": chr_(None), "x": dbl(1, 2, 3)})
    add(f"{f}.zero_length_name", f, {"col_name": chr_(), "x": dbl(1, 2, 3)})
    add(f"{f}.null_name", f, {"col_name": NULL, "x": dbl(1, 2, 3)})

    f = ".demographic_values_ok"
    for kind, x, cid in (("age", dbl(1, 2), "few"), ("age", dbl(20, 30, 150, 160), "old"),
                         ("gender", chr_("m", "f", "x"), "m_f_x"), ("race", dbl(1, 2, 3), "coded"),
                         ("other", dbl(1, 2, 3), "unknown_kind")):  # fmt: skip
        add(f"{f}.{kind}.{cid}", f, {"kind": kind, "x": x})


# -- concepts and facets ---------------------------------------------------------------

DT_FMTS = (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S",
    "%d-%m-%Y %H:%M:%S", "%Y-%m-%d_%Hh%M", "%Y_%b_%d_%H%M",
)  # fmt: skip
D_FMTS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y",
    "%m/%d/%y", "%d/%m/%y", "%d %b %Y", "%B %d, %Y",
)  # fmt: skip


def concepts() -> None:
    f = "data_col_concept"
    cases: list[tuple[str, Any, Any]] = [
        ("rt", "RT", dbl(543, 612, 498, 701)),
        ("accuracy", "accuracy", dbl(1, 0, 1, 1, 0)),
        ("correct_words", "correct", chr_("correct", "incorrect", "correct")),
        ("condition", "condition", dbl(1, 2, 1, 2)),
        ("age", "age", dbl(23, 45, 31)),
        ("time_clock", "time", chr_("10:00", "10:05", "10:10")),
        ("rt_negative", "rt", dbl(*([-5] * 10))),
        ("response_time_neg", "response_time", dbl(-1, -2, -3, 4)),
        ("timestamp", "timestamp", chr_("2021-01-01 10:00:00", "2021-01-02 11:30:00", "bad")),
        ("date", "date", chr_("2021-01-01", "2021-02-01", "2021-03-01")),
        ("dob_slash", "dob", chr_("01/02/1990", "15/06/1985", "30/12/2001")),
        ("startdate", "StartDate", chr_("2021-05-01 10:00:00", "2021-05-02 10:00:00")),
        ("group", "Group", chr_("a", "b", "a")),
        ("hits", "hits", chr_("hit", "miss", "hit")),
        ("error_counts", "error", dbl(0, 1, 2)),
        ("sex", "sex", dbl(1, 2, 1, 2)),
        ("day_numeric", "day", dbl(1, 2, 3)),
        ("birth_date_mixed", "birth_date", chr_("1990-01-01", "1985-05-05", "bad")),
        ("onset", "onset", chr_("2021-01-01T10:00:00", "2021-01-02T10:00:00")),
        ("no_concept", "score", dbl(1, 2, 3)),
        ("empty_name", "", dbl(1, 2, 3)),
        ("rt_few", "rt", dbl(-1, 2)),
        ("acc_logical", "acc", lgl(True, False, True, True)),
        ("psychopy_date", "date", chr_("2020-05-19_16h20.01.792", "2020-05-20_09h01.11.001")),
        ("compact_datetime", "session_time", chr_("2022_Feb_08_1523", "2022_Mar_09_0915")),
        ("day_month_name", "test_day", chr_("5 Mar 2021", "12 April 2021", "1 jun 2021")),
        ("month_name_first", "dated", chr_("March 5, 2021", "Apr 12, 2021")),
        ("between", "between_subjects", chr_("a", "b")),
        ("cond_numbers", "cond", dbl(1, 1, 1)),
    ]
    for cid, nm, x in cases:
        add(f"{f}.{cid}", f, {"col_name": nm, "x": x})
    add(f"{f}.na_name", f, {"col_name": chr_(None), "x": dbl(1, 2)})
    add(f"{f}.null_name", f, {"col_name": NULL, "x": dbl(1, 2)})

    f = ".parse_frac"
    vals: list[tuple[str, Any, tuple[str, ...]]] = [
        ("datetimes", chr_("2021-01-01 10:00:00", "2021-01-01T10:00:00", "2021-13-01 10:00:00",
                           "x", None, ""), DT_FMTS),  # fmt: skip
        ("dates", chr_("2021-02-29", "2020-02-29", "31/12/2020", "12/31/2020", "1/2/3"), D_FMTS),
        ("numeric", dbl(20210101, 20210102), D_FMTS),
        ("empty", chr_(None, ""), D_FMTS),
        ("long", ex('c(paste0("2021-01-01", strrep(" x", 150)), "2021-01-02")',
                    '["2021-01-01" + " x" * 150, "2021-01-02"]'), D_FMTS),
        ("psychopy", chr_("2020-05-19_16h20.01.792", "2022_Feb_08_1523", "2021-01-01 24:00:00",
                          "2021-01-01 24:30:00", "2021-01-01 23:59:60", "2021-01-01 23:59:61"),
         DT_FMTS),  # fmt: skip
        ("months", chr_("5 Mar 2021", "5 March 2021", "5 Sept 2021", "March 5, 2021",
                        "March 5 2021", "05Mar2021", " 5  mar   2021"), D_FMTS),  # fmt: skip
        ("two_digit_years", chr_("01/02/03", "12/31/69", "12/31/68", "13/13/13"), D_FMTS),
        ("factor", ex('factor(c("2021-01-01", "2021-01-02", "no"))',
                      'pd.Series(pd.Categorical(["2021-01-01", "2021-01-02", "no"]))'), D_FMTS),
        ("logical", lgl(True, False), D_FMTS),
        ("spaces", chr_("2021- 1- 1", "  2021-01-01", "2021 -01-01", "2021-1-100"), D_FMTS),
    ]
    for cid, v, fmts in vals:
        add(f"{f}.{cid}", f, {"v": v, "fmts": chr_(*fmts)})

    for fn, nm, x in (
        (".concept_is_rt", "reaction.time", dbl(300, 400, 500, -1)),
        (".concept_is_rt", "rts", dbl(300, 400)),
        (".concept_is_rt", "resp_time", chr_("0,5", "0,7", "1,2")),
        (".concept_is_accuracy", "is_correct", chr_("TRUE", "FALSE", "true")),
        (".concept_is_accuracy", "misses", dbl(0, 1, 2)),
        (".concept_is_accuracy", "Accuracy", chr_("right", "wrong", "RIGHT", "")),
        (".concept_is_condition", "manipulation", dbl(1, 2)),
        (".concept_is_condition", "conditions_asthma", dbl(1, 2)),
        (".concept_is_timestamp", "RecordedDate", chr_("2021-05-01 10:05:00", "x")),
        (".concept_is_timestamp", "time", chr_("x", "y", "2021-05-01 10:05")),
        (".concept_is_date", "test_date", chr_("2021-05-01", "2021-05-02 10:00:00")),
        (".concept_is_date", "dag", chr_("01-05-2021", "02-05-2021")),
    ):
        add(f"{fn}.{nm}", fn, {"col_name": nm, "x": x})

    f = ".coltype_to_facets"
    for ct in ("empty", "constant", "binary", "date", "text", "id", "continuous",
               "continuous_comma_decimal", "continuous_outliers_excluded", "unknown_type"):  # fmt: skip
        add(f"{f}.{ct}", f, {"ct": ct, "is_numeric_hint": True})
    add(f"{f}.na", f, {"ct": chr_(None), "is_numeric_hint": False})
    add(f"{f}.null", f, {"ct": NULL, "is_numeric_hint": False})

    f = "data_col_facets"
    cases2: list[tuple[str, str, Any, Any]] = [
        ("id", "subject_id", chr_("s01", "s02", "s03"), None),
        ("age", "age", dbl(23, 45, 31, 29, 55, 19, 67, 40), None),
        ("rt_ms", "RT", dbl(543, 612, 498, 701, 555), None),
        ("rt_s", "rt_s", dbl(0.54, 0.61, 0.49, 0.70, 0.55), None),
        ("likert_block", "panas_1", ex("rep(1:5, 12)", "[*range(1, 6)] * 12"), True),
        ("likert_unknown", "panas_1", ex("rep(1:5, 12)", "[*range(1, 6)] * 12"), None),
        ("trial_counter", "round", ex("rep(1:5, 12)", "[*range(1, 6)] * 12"), False),
        ("comma_decimal", "price", chr_("1,50", "2,30", "4,10", "5,00", "3,25", "6,60", "2,10", "1,90"), None),
        ("constant", "k", dbl(*([7] * 10)), None),
        ("empty", "e", dbl(None, None), None),
        ("gender", "gender", chr_("Male", "Female", "Other", "Female", "Male"), None),
        ("race", "race", chr_("White", "Black", "Asian", "Other", "White", "Black"), None),
        ("condition", "condition", chr_("ctrl", "treat", "ctrl", "treat", "other"), None),
        ("date", "date", chr_("2021-01-01", "2021-02-01", "2021-03-01", "2021-04-01"), None),
        ("timestamp", "StartDate", chr_("2021-05-01 10:00:00", "2021-05-02 10:00:00",
                                        "2021-05-03 11:00:00"), None),  # fmt: skip
        ("text_nominal", "object_label", chr_("Wine", "Hammock", "Binoculars", "Apple", "Tree"), None),
        ("mostly_numeric", "score", chr_("1.5", "2.5", "3.7", "4.1", "a"), None),
        ("binary_numeric", "flag", dbl(0, 1, 0, 1), None),
        ("accuracy", "correct", dbl(0, 1, 1, 0, 1, 1), None),
        ("continuous", "x", dbl(1.5, 2.5, 3.7, 4.1, 5.9), None),
        ("rt_empty", "rt", dbl(None, None, None), None),
        ("rt_zero", "rt", dbl(0, 0, 0, 1), None),
        ("logical", "done", lgl(True, False, True, None), None),
    ]
    for cid, nm, x, blk in cases2:
        args: dict[str, Any] = {"col_name": nm, "values": x}
        if blk is not None:
            args["in_scale_block"] = blk
        add(f"{f}.{cid}", f, args)

    f = ".tabular_usable"
    prose_df = df(
        note_a=["some long free text here", "another comment entirely", "third remark on it"],
        note_b=["alpha beta gamma", "delta epsilon", "zeta eta theta"],
        value=[1, 2, 3],
    )
    text_f = {"representation": "text", "concept": None}
    num_f = {"representation": "numeric", "concept": None}
    add(f"{f}.prose", f, {"facets": lst(text_f, text_f, text_f), "df": prose_df})
    add(f"{f}.ordinary", f, {"facets": lst(text_f, text_f, num_f), "df": prose_df})
    sparse_df = df(
        a=["code one text", None, None, "code four"],
        b=["x1", "x2", "x3", "x4"],
        c=[None, None, None, 1],
        d=[None, 1, None, None],
        e=[1, 2, 3, 4],
    )
    add(f"{f}.worksheet", f,
        {"facets": lst(text_f, text_f, num_f, num_f, num_f), "df": sparse_df})
    add(f"{f}.id_text", f,
        {"facets": lst({"representation": "text", "concept": "id"}, text_f, num_f), "df": prose_df})
    add(f"{f}.no_rows", f,
        {"facets": lst(num_f), "df": ex("data.frame(a = numeric(0))", "pd.DataFrame({'a': pd.Series([], dtype='float64')})")})  # fmt: skip
    add(f"{f}.no_facets", f, {"facets": lst(), "df": prose_df})
    add(f"{f}.null_df", f, {"facets": lst(num_f), "df": NULL})
    add(f"{f}.repeated_text", f,
        {"facets": lst(text_f, num_f),
         "df": df(a=["x", "x", "x", "y"], b=[1, 2, 3, 4])})  # fmt: skip


# -- Qualtrics and header repair --------------------------------------------------------


QUALTRICS_LONG_R = """local({
  meta <- c("StartDate", "EndDate", "Progress", "Duration (in seconds)",
            "Finished", "RecordedDate", "ResponseId")
  items <- paste0("q_", 1:60)
  nm <- c(meta, items)
  question_row <- c("Start Date", "End Date", "Progress", "Duration (in seconds)",
                    "Finished", "Recorded Date", "Response ID",
                    paste("Question text for item", 1:60))
  import_row <- sprintf('{"ImportId":"%s"}', nm)
  data_rows <- lapply(seq_along(nm), function(i)
    if (i <= length(meta)) rep("x", 3) else as.character((i + 0:2) %% 7 + 1))
  q <- as.data.frame(rbind(question_row, import_row, do.call(cbind, data_rows)),
                     stringsAsFactors = FALSE)
  names(q) <- nm
  rownames(q) <- NULL
  q
})"""
QUALTRICS_LONG_PY = (
    "(lambda meta, items: pd.DataFrame("
    "[['Start Date', 'End Date', 'Progress', 'Duration (in seconds)', 'Finished', "
    "'Recorded Date', 'Response ID'] + ['Question text for item %d' % i for i in range(1, 61)], "
    "['{\"ImportId\":\"%s\"}' % n for n in meta + items]] + "
    "[['x'] * 7 + [str((i + k) % 7 + 1) for i in range(8, 68)] for k in range(3)], "
    "columns=meta + items))("
    "['StartDate', 'EndDate', 'Progress', 'Duration (in seconds)', 'Finished', "
    "'RecordedDate', 'ResponseId'], ['q_%d' % i for i in range(1, 61)])"
)


def qualtrics() -> None:
    f = "data_check_is_qualtrics"
    q1 = df(**{"StartDate": ["2021-01-01"], "EndDate": ["2021-01-01"], "Progress": [100],
               "Duration (in seconds)": [300], "Finished": [1], "ResponseId": ["R_abc123de"],
               "Q1": [3]})  # fmt: skip
    add(f"{f}.metadata", f, {"df": q1})
    add(f"{f}.lone_startdate", f,
        {"df": df(id=[1, 2, 3], StartDate=["a", "b", "c"], score=[1, 2, 3])})
    add(f"{f}.plain", f, {"df": df(a=[1, 2, 3], b=[4, 5, 6])})
    add(f"{f}.thin_responseid", f,
        {"df": df(StartDate=["2021-01-01 10:00:00"] * 3, Progress=[100, 100, 50],
                  ResponseId=["R_abc123de", "R_xyz789gh", "R_qwe456rt"], Q1=[1, 2, 3])})  # fmt: skip
    add(f"{f}.thin_bad_ids", f,
        {"df": df(StartDate=["2021-01-01"] * 3, Progress=[100, 100, 50],
                  ResponseId=["1", "2", "R_x"], Q1=[1, 2, 3])})  # fmt: skip
    add(f"{f}.thin_importid", f,
        {"df": df(StartDate=["Start Date", '{"ImportId":"startDate"}', "2021-01-01"],
                  Finished=["Finished", '{"ImportId":"finished"}', "1"],
                  Q1=["x", "y", "z"])})  # fmt: skip
    add(f"{f}.min_meta", f, {"df": q1, "min_meta": 7})
    add(f"{f}.no_columns", f, {"df": ex("data.frame()", "pd.DataFrame()")})
    add(f"{f}.null", f, {"df": NULL})
    add(f"{f}.long", f, {"df": ex(QUALTRICS_LONG_R, QUALTRICS_LONG_PY)})
    add(f"{f}.fixture", f, {"df": read_head("qualtrics.csv")})
    add(f"{f}.fixture_plain", f, {"df": read_head("plain.csv")})

    f = "data_strip_qualtrics_header"
    add(f"{f}.long", f, {"df": ex(QUALTRICS_LONG_R, QUALTRICS_LONG_PY)})
    add(f"{f}.three_columns", f,
        {"df": df(**{"Progress": ["Progress", '{"ImportId":"progress"}', "100", "100"],
                     "Duration (in seconds)": ["Duration (in seconds)", '{"ImportId":"duration"}', "300", "120"],
                     "ResponseId": ["Response ID", '{"ImportId":"_recordId"}', "R_abc", "R_xyz"]})})  # fmt: skip
    add(f"{f}.no_header_rows", f, {"df": df(a=["1", "2"], b=["x", "y"])})
    add(f"{f}.max_strip1", f,
        {"df": df(**{"Progress": ["Progress", '{"ImportId":"progress"}', "100"],
                     "Finished": ["Finished", '{"ImportId":"finished"}', "1"]}),
         "max_strip": 1})  # fmt: skip
    add(f"{f}.zero_rows", f, {"df": ex("data.frame(a = character(0))", "pd.DataFrame({'a': pd.Series([], dtype=object)})")})  # fmt: skip
    add(f"{f}.mixed_types", f,
        {"df": df(StartDate=["Start Date", "2021-01-01", "2021-01-02"],
                  EndDate=["End Date", "2021-01-01", None],
                  Status=["Response Type", "0", " 1 "],
                  Finished=["Finished", "1", ""],
                  num=[None, 1.5, 2.5],
                  flag=[True, None, False])})  # fmt: skip
    add(f"{f}.single_column", f, {"df": df(Progress=["Progress", "100", "90"])})
    add(f"{f}.na_strings", f,
        {"df": df(StartDate=["Start Date", "1", "NA"], EndDate=["End Date", "2", "3"],
                  Progress=["Progress", "x", "4"], Finished=["Finished", "1", "0"])})  # fmt: skip

    f = ".qualtrics_is_header_row"
    for cid, v in (
        ("importid", chr_('{"ImportId":"x"}', "a")),
        ("labels4", chr_("Start Date", "End Date", "Progress", "Finished", "Q1 text", "Q2")),
        ("labels3", chr_("Start Date", "End Date", "Progress", "Q1 text")),
        ("narrow_all", chr_("Start Date", "Response ID")),
        ("narrow_mixed", chr_("Start Date", "x")),
        ("blank", chr_("", None, "  ")),
        ("data", chr_("1", "2", "3", "4", "5")),
    ):
        add(f"{f}.{cid}", f, {"row_vals": v})

    add(".qualtrics_key.vector", ".qualtrics_key",
        {"nm": chr_("Duration (in seconds)", "Duration..in.seconds.", "ÉtéQ1", None)})
    add(".qualtrics_tag_cols.vector", ".qualtrics_tag_cols",
        {"col_names": chr_("StartDate", "Q1", "Location Latitude", "RecipientEmail",
                           "externalDataReference", "")})  # fmt: skip
    add(".qualtrics_tag_cols.empty", ".qualtrics_tag_cols", {"col_names": chr_()})
    for i, nm in enumerate(("TIPI_1", "BSQ_10", "Q1_2", "x_1", "a_b_12", "StartDate", "TIPI",
                            "ab_", "_12", "", "Duration_1", "1a_b_2")):  # fmt: skip
        add(f".qualtrics_col_stem.{i}", ".qualtrics_col_stem", {"nm": nm})
    add(".qualtrics_col_stem.na", ".qualtrics_col_stem", {"nm": chr_(None)})
    add(".qualtrics_is_display_order.vector", ".qualtrics_is_display_order",
        {"col_names": chr_("Q1_DO_1", "Q1_DO", "Q1_DOG", "doable", "X_do_1", "Q_DO_")})


def header_repair() -> None:
    f = ".detect_header_row"
    py = PH + "detect_header_row"
    cda_raw = lst(
        chr_("CDA", "CDA", "CDA", "CDA", "CDA"),
        chr_("Participant", "Reject", "Condition", "-100", "0"),
        chr_("1", "0", "1", "0.5", "0.25"),
        chr_("2", "1", "2", "0.7", "-0.1"),
        chr_("3", "0", "1", "0.2", "0.3"),
    )
    add(f"{f}.banner", f, {"rows": cda_raw}, py=py)
    add(f"{f}.correct", f,
        {"rows": lst(chr_("id", "score", "grp"), chr_("1", "2", "a"), chr_("2", "3", "b"))}, py=py)
    add(f"{f}.blank_rows", f,
        {"rows": lst(chr_("", "", ""), chr_("Title", "", ""), chr_("id", "x", "y"),
                     chr_("1", "2", "3"), chr_("2", "3", "4"))}, py=py)  # fmt: skip
    add(f"{f}.headerless_numeric", f,
        {"rows": lst(chr_("1", "1", "3"), chr_("2", "2", "4"), chr_("3", "5", "6"))}, py=py)
    add(f"{f}.placeholder_row", f,
        {"rows": lst(chr_("...1", "...2", "...3"), chr_("id", "a", "b"), chr_("1", "2", "3"),
                     chr_("4", "5", "6"))}, py=py)  # fmt: skip
    add(f"{f}.one_row", f, {"rows": lst(chr_("a", "b"))}, py=py)
    add(f"{f}.one_column", f, {"rows": lst(chr_("a"), chr_("1"), chr_("2"))}, py=py)
    add(f"{f}.all_text", f,
        {"rows": lst(chr_("", "", ""), chr_("term", "def", "note"), chr_("a", "b", "c"))}, py=py)
    add(f"{f}.max_scan1", f,
        {"rows": lst(chr_("", "", ""), chr_("", "", ""), chr_("id", "x", "y"),
                     chr_("1", "2", "3")), "max_scan": 1}, py=py)  # fmt: skip
    add(f"{f}.na_header_tokens", f,
        {"rows": lst(chr_("X", "X", "X"), chr_("NA", "NaN", "id"), chr_("1", "2", "3"),
                     chr_("4", "5", "6"))}, py=py)  # fmt: skip
    add(f"{f}.ragged", f,
        {"rows": lst(chr_("T", "T", "T", "T"), chr_("a", "b"), chr_("1", "2", "3", "4", "5"),
                     chr_("6", "7", "8", "9"))}, py=py)  # fmt: skip

    f = "data_promote_header_row"
    cda_df = df(**{"CDA...1": ["Participant", "1", "2", "3"], "CDA...2": ["Reject", "0", "1", "0"],
                   "CDA...3": ["Condition", "1", "2", "1"], "CDA...4": ["-100", "0.5", "0.7", "0.2"],
                   "CDA...5": ["0", "0.25", "-0.1", "0.3"]})  # fmt: skip
    add(f"{f}.raw_rows", f, {"df": cda_df, "raw_rows": cda_raw})
    add(f"{f}.fallback", f, {"df": cda_df})
    add(f"{f}.unchanged", f, {"df": df(a=[1, 2, 3], b=[4, 5, 6])})
    add(f"{f}.one_column", f, {"df": df(a=["x", "1", "2"])})
    add(f"{f}.one_row", f, {"df": df(a=["x"], b=["y"])})
    add(f"{f}.null", f, {"df": NULL})
    add(f"{f}.duplicate_names", f,
        {"df": df(**{"...1": ["id", "1", "2"], "...2": ["id", "3", "4"], "...3": ["", "5", "6"],
                     "...4": ["id.1", "7", "8"]})})  # fmt: skip
    add(f"{f}.typed_fallback", f,
        {"df": df(**{"V1": ["id", "1", "2", "3"], "V2": ["score", "5", "6", "7"]})})
    add(f"{f}.fixture_offset", f, {"df": read_head("offset_header.csv")})
    add(f"{f}.fixture_blank_top", f, {"df": read_head("blank_top_header.csv")})

    add(".is_placeholder_name.vector", ".is_placeholder_name",
        {"x": chr_("...1", "CDA...4", "V3", "X", "X.1", "col_2", "Unnamed: 2", "unnamed.3", "",
                   "  ", None, "id", "Value", "V", "Xa")})  # fmt: skip
    for cid, v in (("banner", chr_("CDA", "CDA", "CDA", "x")), ("unique", chr_("a", "b", "c")),
                   ("empty", chr_("", None))):  # fmt: skip
        add(f".row_duplication.{cid}", ".row_duplication", {"vals": v})
    add(".numeric_col_fraction.mixed", ".numeric_col_fraction",
        {"df": df(a=["1", "2"], b=["x", "2"], c=["", None], d=[" 3 ", "4e2"])})
    add(".numeric_col_fraction.empty", ".numeric_col_fraction", {"df": NULL})
    for cid, v, bn in (("near_empty", chr_("T", "", "", ""), 0.9),
                       ("banner", chr_("C", "C", "C", "D"), 0.9),
                       ("placeholders", chr_("...1", "...2", "x", "y"), 0.9),
                       ("header", chr_("a", "b", "c", "d"), 0.9),
                       ("text_body", chr_("T", "", "", ""), 0.2)):  # fmt: skip
        add(f".is_junk_above_header.{cid}", ".is_junk_above_header",
            {"vals": v, "body_numeric": bn})
    add(".as_num_safe.vector", ".as_num_safe",
        {"x": chr_("1", " 2 ", "NaN", "x", None, "1e3", "Inf", "0x10", "NA")})


# -- trial-level formats ------------------------------------------------------------------


def formats() -> None:
    for fn, cases in {
        "data_check_is_behaverse": [
            ("long", df(instrument_id=["X"], trial_index=[1])),
            ("wide", df(a_response_numeric_i1=[1], a_trial_index_i1=[1])),
            ("wide_partial", df(a_response_numeric_i1=[1], b=[1])),
            ("instrument_only", df(instrument_id=["X"], score=[1])),
            ("fixture", read_head("behaverse.csv")),
        ],
        "data_check_is_inquisit": [
            ("iqdat", df(subject=[1], BlockCode=["a"], trialcode=["b"], other=[1])),
            ("two", df(subject=[1], latency=[300])),
            ("fixture", read_head("inquisit.iqdat")),
        ],
        "data_check_is_jspsych": [
            ("basic", df(trial_type=["x"], rt=[300], time_elapsed=[1000])),
            ("no_counter", df(trial_type=["x"], rt=[300])),
            ("fixture", read_head("jspsych.csv")),
        ],
        "data_check_is_psychopy": [
            ("loop", df(**{"trials.thisN": [0], "resp.keys": ["a"]})),
            ("timing", df(**{"text.started": [1.5]})),
            ("meta", df(expName=["x"], participant=[1])),
            ("underscore_names", df(**{"trials_thisN": [0], "rt": [1]})),
            ("fixture", read_head("psychopy.csv")),
        ],
    }.items():
        for cid, d in cases:
            add(f"{fn}.{cid}", fn, {"df": d})
        add(f"{fn}.no_columns", fn, {"df": ex("data.frame()", "pd.DataFrame()")})
        add(f"{fn}.null", fn, {"df": NULL})
        add(f"{fn}.plain", fn, {"df": read_head("plain.csv")})

    for name in ("behaverse.csv", "behaverse_wide.csv", "blank_top_header.csv", "empty.csv",
                 "eprime.txt", "eprime_levels.txt", "eprime_utf16.txt", "inquisit.iqdat",
                 "jspsych.csv", "jspsych_bom.csv", "notes.txt", "plain.csv", "psychopy.csv",
                 "qualtrics.csv"):  # fmt: skip
        add(f".bh_is_trial_level_file.{name}", ".bh_is_trial_level_file", {"path": fpath(name)})
        if name.endswith(".txt") or name in ("plain.csv", "empty.csv"):
            add(f".eprime_is_export.{name}", ".eprime_is_export", {"path": fpath(name)})
    add(".bh_is_trial_level_file.missing", ".bh_is_trial_level_file", {"path": fpath("nope.csv")})
    add(".bh_is_trial_level_file.directory", ".bh_is_trial_level_file", {"path": {"$file": DATA}})
    add(".eprime_is_export.missing", ".eprime_is_export", {"path": fpath("nope.txt")})

    for name in ("qualtrics.csv", "offset_header.csv", "blank_top_header.csv", "psychopy.csv"):
        add(f"data_read_head.{name}", "data_read_head", {"path": fpath(name), "n_rows": float("inf")},
            py="pytacheck.datacheck.files.data_read_head")  # fmt: skip


# -- scale / task blocks -------------------------------------------------------------------


def blocks() -> None:
    f = ".is_likert_item"
    for cid, x in (
        ("likert", ex("rep(1:5, 4)", "[*range(1, 6)] * 4")),
        ("too_few", dbl(1, 2, 3, 4, 5)),
        ("continuous", ex("(1:20) / 3", "[i / 3 for i in range(1, 21)]")),
        ("binary", ex("rep(0:1, 10)", "[0, 1] * 10")),
        ("character", ex("as.character(rep(1:5, 4))", "[str(i) for i in range(1, 6)] * 4")),
        ("character_na", ex('c(as.character(rep(1:5, 2)), rep("x", 4))', '[str(i) for i in range(1, 6)] * 2 + ["x"] * 4')),  # fmt: skip
        ("wide_range", ex("rep(c(1, 5, 20), 5)", "[1.0, 5.0, 20.0] * 5")),
        ("negative", ex("rep(-7:-5, 5)", "[-7, -6, -5] * 5")),
        ("twelve_levels", ex("rep(1:12, 2)", "[*range(1, 13)] * 2")),
        ("factor", ex("factor(rep(1:5, 4))", "pd.Series(pd.Categorical([str(i) for i in range(1, 6)] * 4))")),  # fmt: skip
        ("logical", ex("rep(c(TRUE, FALSE), 10)", "[True, False] * 10")),
        ("with_inf", ex("c(rep(1:5, 4), Inf)", "[*range(1, 6)] * 4 + [float('inf')]")),
        ("with_na", ex("c(rep(1:5, 4), NA, NA)", "[*range(1, 6)] * 4 + [None, None]")),
    ):
        add(f"{f}.{cid}", f, {"x": x})

    f = ".looks_like_rt"
    for cid, x in (
        ("ms", ex("300 + (1:40) * 23", "[300 + i * 23 for i in range(1, 41)]")),
        ("seconds", ex("0.3 + (1:40) * 0.037", "[0.3 + i * 0.037 for i in range(1, 41)]")),
        ("likert", ex("rep(1:5, 8)", "[*range(1, 6)] * 8")),
        ("negative", ex("c(-1, 300 + (1:40) * 23)", "[-1] + [300 + i * 23 for i in range(1, 41)]")),
        ("few_unique", ex("rep(c(300, 500), 20)", "[300.0, 500.0] * 20")),
        ("character", ex("as.character(300 + (1:40) * 23)", "[str(300 + i * 23) for i in range(1, 41)]")),  # fmt: skip
        ("character_bad", ex('c(as.character(300 + (1:10) * 23), rep("x", 5))', '[str(300 + i * 23) for i in range(1, 11)] + ["x"] * 5')),  # fmt: skip
        ("big_integers", ex("(1:40) * 1000", "[i * 1000 for i in range(1, 41)]")),
    ):
        add(f"{f}.{cid}", f, {"x": x})

    f = ".looks_like_accuracy"
    for cid, x in (
        ("binary", ex("rep(0:1, 5)", "[0, 1] * 5")),
        ("proportion", ex("(1:10) / 11", "[i / 11 for i in range(1, 11)]")),
        ("logical", ex("rep(c(TRUE, FALSE), 5)", "[True, False] * 5")),
        ("logical_few", lgl(True, False)),
        ("character", ex('rep(c("0", "1"), 5)', '["0", "1"] * 5')),
        ("likert", ex("rep(1:5, 2)", "[*range(1, 6)] * 2")),
        ("two_levels_frac", ex("rep(c(0.5, 1), 5)", "[0.5, 1.0] * 5")),
    ):
        add(f"{f}.{cid}", f, {"x": x})

    f = ".is_accuracy_item"
    for cid, x in (
        ("binary", ex("rep(0:1, 5)", "[0, 1] * 5")),
        ("one_two", ex("rep(1:2, 5)", "[1, 2] * 5")),
        ("logical", ex("rep(c(TRUE, FALSE), 5)", "[True, False] * 5")),
        ("all_ones", ex("rep(1, 10)", "[1.0] * 10")),
        ("character", ex('rep(c("1", "0"), 5)', '["1", "0"] * 5')),
        ("few", dbl(0, 1)),
    ):
        add(f"{f}.{cid}", f, {"x": x})

    task_r = """data.frame(id = rep(1:4, each = 10), trial = rep(1:10, 4),
      stroop_rt = 400 + ((1:40) * 37) %% 500, stroop_correct = rep(c(0, 1, 1, 1), 10),
      condition = rep(c("congruent", "incongruent"), 20), block = rep(1:2, each = 20),
      accuracy_prop = (1:40) / 41, RT_mean = rep(1:5, 8), stringsAsFactors = FALSE)"""
    task_py = """pd.DataFrame({'id': [i for i in range(1, 5) for _ in range(10)], 'trial': [*range(1, 11)] * 4,
      'stroop_rt': [400 + (i * 37) % 500 for i in range(1, 41)], 'stroop_correct': [0.0, 1.0, 1.0, 1.0] * 10,
      'condition': ['congruent', 'incongruent'] * 20, 'block': [1] * 20 + [2] * 20,
      'accuracy_prop': [i / 41 for i in range(1, 41)], 'RT_mean': [*range(1, 6)] * 8})"""
    add(".detect_task_columns.trials", ".detect_task_columns", {"df": ex(task_r, task_py)})
    add(".detect_task_columns.none", ".detect_task_columns", {"df": df(a=[1, 2], b=["x", "y"])})
    add(".detect_task_columns.null", ".detect_task_columns", {"df": NULL})
    add(".is_task_data.trials", ".is_task_data", {"df": ex(task_r, task_py)})
    add(".is_task_data.none", ".is_task_data", {"df": df(a=[1, 2], b=["x", "y"])})

    acc_r = """local({
      d <- data.frame(id = 1:12)
      for (i in 1:10) d[[paste0("raven_", i)]] <- as.numeric(((1:12) + i) %% 3 > 0)
      d$score <- (1:12) * 2.5
      for (i in 1:3) d[[paste0("iat_", i)]] <- as.numeric(((1:12) * i) %% 2)
      d
    })"""
    acc_py = """(lambda: pd.DataFrame({'id': list(range(1, 13)),
      **{f'raven_{i}': [float((r + i) % 3 > 0) for r in range(1, 13)] for i in range(1, 11)},
      'score': [r * 2.5 for r in range(1, 13)],
      **{f'iat_{i}': [float((r * i) % 2) for r in range(1, 13)] for i in range(1, 4)}}))()"""
    py_acc = PH + "detect_accuracy_blocks"
    add(".detect_accuracy_blocks.raven", ".detect_accuracy_blocks", {"df": ex(acc_r, acc_py)}, py=py_acc)
    add(".detect_accuracy_blocks.min3", ".detect_accuracy_blocks",
        {"df": ex(acc_r, acc_py), "min_items": 3}, py=py_acc)  # fmt: skip
    add(".detect_accuracy_blocks.null", ".detect_accuracy_blocks", {"df": NULL}, py=py_acc)
    add(".is_task_data.raven", ".is_task_data", {"df": ex(acc_r, acc_py)})

    add(".scale_name_prefix.vector", ".scale_name_prefix",
        {"nm": chr_("bfi_1", "RSE10", "PANAS.3", "x-12", "item", "a1b2", "Q1_2_", "12", "SCALE__4",
                    "Été_1")})  # fmt: skip
    add(".scale_name_prefix.scalar", ".scale_name_prefix", {"nm": "TIPI_10"})

    add(".scale_block_range.frame", ".scale_block_range",
        {"block": df(a=[1, 2, 5], b=[2, None, 7], c=["3", "x", "1"])})
    add(".scale_block_range.empty", ".scale_block_range", {"block": df(a=[None, None])})
    add(".scale_block_range.decimals", ".scale_block_range", {"block": df(a=[0.5, 100000.0])})

    scale_r = """data.frame(id = 1:12, PANAS_1 = rep(1:4, 3), PANAS_2 = rep(2:5, 3),
      PANAS_3 = rep(1:3, 4), PANAS_4 = rep(c(1, 3, 5), 4), RSE_1 = rep(1:4, 3),
      RSE_2 = rep(1:4, 3), RSE_3 = rep(1:4, 3), age = 20:31, x_1 = rep(1:3, 4),
      x_2 = rep(1:3, 4), txt = letters[1:12], y1 = rep(1:3, 4), y2 = rep(1:3, 4),
      y3 = rep(1:3, 4), stringsAsFactors = FALSE)"""
    scale_py = """pd.DataFrame({'id': list(range(1, 13)), 'PANAS_1': [1, 2, 3, 4] * 3,
      'PANAS_2': [2, 3, 4, 5] * 3, 'PANAS_3': [1, 2, 3] * 4, 'PANAS_4': [1.0, 3.0, 5.0] * 4,
      'RSE_1': [1, 2, 3, 4] * 3, 'RSE_2': [1, 2, 3, 4] * 3, 'RSE_3': [1, 2, 3, 4] * 3,
      'age': list(range(20, 32)), 'x_1': [1, 2, 3] * 4, 'x_2': [1, 2, 3] * 4,
      'txt': list('abcdefghijkl'), 'y1': [1, 2, 3] * 4, 'y2': [1, 2, 3] * 4, 'y3': [1, 2, 3] * 4})"""
    py_sc = PH + "detect_scale_blocks"
    add(".detect_scale_blocks.frame", ".detect_scale_blocks", {"df": ex(scale_r, scale_py)}, py=py_sc)
    add(".detect_scale_blocks.min2", ".detect_scale_blocks",
        {"df": ex(scale_r, scale_py), "min_items": 2}, py=py_sc)  # fmt: skip
    add(".detect_scale_blocks.none", ".detect_scale_blocks", {"df": df(a=[1, 2])}, py=py_sc)

    cols_df = df(
        source_file=["a.csv", "a.csv", "a.csv", "a.csv", "b.csv", "a.csv"],
        column_name=["q1", "q2", "q3", "q4", "q1", "p1"],
        min=[0, 1, 0, None, -5, 0],
        max=[100, 7, 5, None, 50, 0.9],
    )
    f = ".scale_block_is_ratinglike"
    add(f"{f}.slider", f, {"cols": chr_("q1", "q2", "q3"), "source_file": "a.csv", "columns_df": cols_df})
    add(f"{f}.too_few", f, {"cols": chr_("q1", "q2"), "source_file": "a.csv", "columns_df": cols_df})
    add(f"{f}.other_file", f, {"cols": chr_("q1", "q2", "q3"), "source_file": "b.csv", "columns_df": cols_df})  # fmt: skip
    add(f"{f}.with_missing", f,
        {"cols": chr_("q1", "q2", "q3", "q4"), "source_file": "a.csv", "columns_df": cols_df})
    add(f"{f}.probabilities", f,
        {"cols": chr_("p1", "p1", "p1"), "source_file": "a.csv",
         "columns_df": df(source_file=["a.csv"] * 3, column_name=["p1", "p2", "p3"],
                          min=[0, 0.1, 0.2], max=[0.9, 1, 0.8])})  # fmt: skip
    add(f"{f}.probabilities3", f,
        {"cols": chr_("p1", "p2", "p3"), "source_file": "a.csv",
         "columns_df": df(source_file=["a.csv"] * 3, column_name=["p1", "p2", "p3"],
                          min=[0, 0.1, 0.2], max=[0.9, 1, 0.8])})  # fmt: skip
    add(f"{f}.negative", f,
        {"cols": chr_("p1", "p2", "p3"), "source_file": "a.csv",
         "columns_df": df(source_file=["a.csv"] * 3, column_name=["p1", "p2", "p3"],
                          min=[-52, 0, 1], max=[10, 5, 5])})  # fmt: skip
    add(f"{f}.char_stats", f,
        {"cols": chr_("p1", "p2", "p3"), "source_file": "a.csv",
         "columns_df": df(source_file=["a.csv"] * 3, column_name=["p1", "p2", "p3"],
                          min=["1", "1", "x"], max=["5", "7", "y"])})  # fmt: skip
    add(f"{f}.missing_columns", f,
        {"cols": chr_("q1"), "source_file": "a.csv", "columns_df": df(a=[1])})
    add(f"{f}.null", f, {"cols": chr_("q1"), "source_file": "a.csv", "columns_df": NULL})


def data_sets() -> None:
    add("scales.data", "identity", {"x": ex("metacheck::scales", "None")},
        py="pytacheck.datacheck.scales.scales", py_drop=["x"])
    add("tasks.data", "identity", {"x": ex("metacheck::tasks", "None")},
        py="pytacheck.datacheck.tasks.tasks", py_drop=["x"])


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, s: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", s, style='"')


_Dumper.add_representer(str, _str)


def main() -> None:
    scale_values()
    outliers()
    constant()
    empty()
    design_name()
    spss_filter()
    text_checks()
    colname()
    pii()
    demographics()
    concepts()
    qualtrics()
    header_repair()
    formats()
    blocks()
    data_sets()
    ids = [c["id"] for c in CASES]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise SystemExit(f"duplicate ids: {sorted(dup)}")
    header = (
        "# Parity cases for data_check helpers part 3 (checks, PII, facets, formats, blocks)\n"
        "# and the scales / tasks data sets. Generated by\n"
        "# tests/datacheck_checks/gen_parity_cases.py -- edit that script, not this file.\n"
    )
    body = yaml.dump(
        {"area": "datacheck_checks", "cases": CASES},
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    OUT.write_text(header + body, encoding="utf-8")
    print(f"{len(CASES)} cases -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
