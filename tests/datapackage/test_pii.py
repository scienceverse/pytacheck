"""Personal data by value in a data package: the scan, the module, the reports and the leak check."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from metacheck.cli import main
from metacheck.datapackage import check_package, open_package, report_package
from metacheck.datapackage.pii import (
    CHECK_TITLES,
    HITS_COLUMNS,
    check_personal_data,
    load_severity,
)
from metacheck.packs.registry import refresh

# values that must never show up in any output; all made up
SENTINELS = (
    "111222333",  # a BSN
    "123456782",
    "06-98765432",  # a phone number
    "+31 6 55443322",
    "5611 ZK",  # a postcode
    "1012 AB",
    "2001:db8:85a3::8a2e:370:7334",  # an IPv6 address
    "Jan de Vries",  # names
    "Anna Smit",
    "Piet van der Berg",
    "Sandra Zwart",
    "2345671",  # student numbers
    "3456782",
    "4567893",
)

SURVEY = (
    "participant_id,studentnummer,telefoon,postcode,bsn,naam,ip,age\n"
    "1,2345671,06-98765432,5611 ZK,111222333,Jan de Vries,2001:db8:85a3::8a2e:370:7334,23\n"
    "2,3456782,+31 6 55443322,1012 AB,123456782,Anna Smit,2001:db8:85a3::8a2e:370:7335,31\n"
    "3,4567893,06-11223344,5611 ZK,111222333,Piet van der Berg,2001:db8:85a3::8a2e:370:7336,27\n"
    "4,2345671,06-98765432,1012 AB,123456782,Sandra Zwart,2001:db8:85a3::8a2e:370:7337,45\n"
)

CLEAN = (
    "participant_id,age,gender,rt,session_time,mac\n"
    "100000001,23,f,512,12:30:45,aa:bb:cc:dd:ee:ff\n"
    "100000002,31,m,498,12:31:10,aa:bb:cc:dd:ee:00\n"
    "100000003,27,f,630,12:35:59,aa:bb:cc:dd:ee:01\n"
    "100000004,45,m,577,13:01:00,aa:bb:cc:dd:ee:02\n"
)

VARIABLES = (
    "name,type\nparticipant_id,integer\nage,integer\nreaction_time,numeric\nscore_total,numeric\n"
)


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PYTACHECK_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PYTACHECK_PRESET", raising=False)
    monkeypatch.chdir(tmp_path)
    refresh()
    yield
    refresh()


def _folder(tmp_path: Path, files: dict[str, str], name: str = "pkg") -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _scan(root: Path, **kwargs: Any) -> Any:
    with open_package(root) as pkg:
        return check_personal_data(pkg, **kwargs)


def _rules(result: Any) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    hits = result.hits
    for rule, column in zip(hits["rule"].tolist(), hits["column"].tolist(), strict=True):
        out.setdefault(rule, set()).add(column)
    return out


# -- the scan -------------------------------------------------------------------------


def test_a_survey_with_personal_data(tmp_path: Path) -> None:
    root = _folder(tmp_path, {"data/survey.csv": SURVEY, "README.md": "# Study\n"})
    res = _scan(root)
    assert _rules(res) == {
        "bsn": {"bsn"},
        "student-number": {"studentnummer"},
        "phone": {"telefoon"},
        "postcode": {"postcode"},
        "ipv6": {"ip"},
        "person-name": {"naam"},
    }
    assert (res.n_files, res.n_scanned, res.n_tables, res.rows_read) == (1, 1, 1, 4)
    assert list(res.hits.columns) == list(HITS_COLUMNS)
    phone = res.hits[res.hits["rule"] == "phone"].iloc[0]
    assert (phone["path"], phone["hits"], phone["checked"], phone["named"]) == (
        "data/survey.csv",
        4,
        4,
        True,
    )
    status = dict(zip(res.checklist["item"], res.checklist["status"], strict=True))
    assert status == {
        "pii_bsn": "manual",
        "pii_student_number": "manual",
        "pii_phone": "manual",
        "pii_ipv6": "manual",
        "pii_postcode": "manual",
        "pii_name": "manual",
        "pii_scan": "pass",
    }
    assert res.traffic_light == "yellow"
    assert list(res.checklist["item"]) == list(CHECK_TITLES)
    assert "A person has to check these" in res.summary_text


def test_the_columns_of_ids_times_and_macs_are_not_flagged(tmp_path: Path) -> None:
    root = _folder(tmp_path, {"data/clean.csv": CLEAN, "docs/variables.csv": VARIABLES})
    res = _scan(root)
    assert res.hits.empty
    assert set(res.checklist["status"]) == {"pass"}
    assert res.traffic_light == "green"
    assert "No personal data found by value in 2 data files" in res.summary_text


def test_a_package_without_data_files(tmp_path: Path) -> None:
    root = _folder(tmp_path, {"README.md": "# Study\n", "code/run.R": "x <- 1\n"})
    res = _scan(root)
    assert set(res.checklist["status"]) == {"na"}
    assert res.traffic_light == "na"
    assert "no data files" in res.summary_text
    assert res.findings.empty and res.hits.empty


def test_leading_zeros_in_text_files_are_kept(tmp_path: Path) -> None:
    # read as numbers, 06-numbers lose the 0 and a phone number looks like a BSN
    root = _folder(
        tmp_path,
        {"a.csv": "tel,x\n0612345678,1\n0687654321,2\n0611223344,3\n"},
    )
    assert _rules(_scan(root)) == {"phone": {"tel"}}


def test_tab_and_semicolon_files_and_latin1(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "a.tsv").write_text("voornaam\tx\nJan\t1\nAnna\t2\nPiet\t3\n", encoding="utf-8")
    (root / "b.csv").write_bytes(
        "naam;x\nJosé García;1\nRenée Bos;2\nPiet Dijk;3\n".encode("latin-1")
    )
    assert _rules(_scan(root)) == {"person-name": {"voornaam", "naam"}}


def test_a_file_without_a_header(tmp_path: Path) -> None:
    root = _folder(tmp_path, {"a.csv": "111222333,1\n123456782,2\n111222333,3\n"})
    res = _scan(root)
    assert _rules(res) == {"bsn": {"column 1"}}


def test_a_column_name_that_looks_like_a_value_is_not_repeated(tmp_path: Path) -> None:
    root = _folder(
        tmp_path,
        {
            "a.csv": "111222333,x@example.org,1\n123456782,y@example.org,2\n111222333,z,3\n123456782,w,4\n"
        },
    )
    res = _scan(root)
    assert "111222333" not in json.dumps(res.findings.to_dict("records"))
    assert _rules(res) == {"bsn": {"column 1"}}


def test_workbooks_every_sheet_and_numbers_stored_as_numbers(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["id", "score"])
    for i in range(1, 6):
        ws.append([f"p{i}", i])
    second = wb.create_sheet("Contacts")
    second.append(["bsn", "mobiel", "naam"])
    second.append([111222333, "06-98765432", "Jan de Vries"])
    second.append([123456782, "+31 6 55443322", "Anna Smit"])
    second.append([111222333.0, "06-11223344", "Piet van der Berg"])
    root = tmp_path / "pkg"
    root.mkdir()
    wb.save(root / "book.xlsx")
    res = _scan(root)
    assert _rules(res) == {
        "bsn": {"bsn"},
        "phone": {"mobiel"},
        "person-name": {"naam"},
    }
    assert set(res.hits["sheet"]) == {"Contacts"}
    assert res.n_tables == 2
    assert "sheet 'Contacts'" in " ".join(res.findings["detail"])


def test_spss_files(tmp_path: Path) -> None:
    import pandas as pd
    import pyreadstat

    root = tmp_path / "pkg"
    root.mkdir()
    df = pd.DataFrame(
        {
            "studentnummer": [2345671, 3456782, 4567893],
            "telefoon": ["06-98765432", "+31 6 55443322", "06-11223344"],
            "score": [1.5, 2.5, 3.5],
        }
    )
    pyreadstat.write_sav(df, str(root / "a.sav"))
    assert _rules(_scan(root)) == {
        "student-number": {"studentnummer"},
        "phone": {"telefoon"},
    }


def test_codebooks_hidden_files_and_other_formats_are_not_read(tmp_path: Path) -> None:
    root = _folder(
        tmp_path,
        {
            "codebook.csv": "name,label\nJan de Vries,x\nAnna Smit,y\nPiet Bos,z\n",
            ".hidden/data.csv": "voornaam\nJan\nAnna\nPiet\n",
            "._data.csv": "voornaam\nJan\nAnna\nPiet\n",
            "notes.txt": "voornaam\nJan\nAnna\nPiet\n",
            "data.json": '{"voornaam": ["Jan", "Anna", "Piet"]}',
        },
    )
    res = _scan(root)
    assert res.n_files == 0
    assert res.hits.empty


def test_limits_and_unreadable_files(tmp_path: Path) -> None:
    rows = "".join(f"{i}\n" for i in range(50))
    root = _folder(
        tmp_path,
        {
            "big.csv": "tel\n" + "06-12345678\n" * 40,
            "late.csv": "x\n" + "a\n" * 30 + "06-12345678\n" * 5,
            "empty.csv": "",
            "bad.xlsx": "this is not a workbook",
            "numbers.csv": "n\n" + rows,
        },
    )
    res = _scan(root, max_file_size=0.0001)  # 100 bytes
    skipped = res.findings[res.findings["rule"] == "file-too-large"]
    assert set(skipped["path"]) == {"big.csv", "late.csv", "numbers.csv"}
    status = dict(zip(res.checklist["item"], res.checklist["status"], strict=True))
    assert status["pii_scan"] == "warn"
    assert "not scanned" in res.summary_text

    res = _scan(root)
    unreadable = res.findings[res.findings["rule"] == "file-unreadable"]
    assert list(unreadable["path"]) == ["bad.xlsx"]
    # only the first rows are read: the numbers in the last rows of late.csv are not seen
    res = _scan(root, max_rows=20)
    assert "late.csv" not in set(res.hits["path"])
    capped = res.hits[res.hits["path"] == "big.csv"].iloc[0]
    assert (capped["rows_read"], bool(capped["rows_capped"])) == (20, True)
    assert "Only the first 20 rows were read" in " ".join(res.findings["detail"])

    res = _scan(root, max_files=2)
    assert "files-not-scanned" in set(res.findings["rule"])
    assert res.n_skipped >= 3


def test_severity_overrides(tmp_path: Path) -> None:
    root = _folder(tmp_path, {"data/survey.csv": SURVEY})
    res = _scan(root, severity={"bsn": "problem", "phone": "ignore", "ipv6": "info"})
    status = dict(zip(res.checklist["item"], res.checklist["status"], strict=True))
    assert status["pii_bsn"] == "fail"
    assert status["pii_phone"] == "na"
    assert status["pii_ipv6"] == "pass"
    assert "phone" not in set(res.hits["rule"])
    assert res.traffic_light == "red"
    with pytest.raises(ValueError, match="must be one of"):
        load_severity({"bsn": "bad"})
    path = tmp_path / "sev.json"
    path.write_text('{"bsn": "problem"}')
    assert load_severity(path) == {"bsn": "problem"}


# -- the module -----------------------------------------------------------------------


def test_the_module_is_in_the_default_preset_and_runs(tmp_path: Path) -> None:
    root = _folder(tmp_path, {"data/survey.csv": SURVEY, "README.md": "# Study\n"})
    chain = check_package(root, modules=["package_pii"])
    (out,) = chain
    assert out.title == "Data Package Personal Data"
    assert out.traffic_light == "yellow"
    assert list(out["checklist"]["item"]) == list(CHECK_TITLES)
    assert len(out["hits"]) == 6


def test_the_module_in_an_archive_and_offline(tmp_path: Path) -> None:
    archive = tmp_path / "study.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("study/data/survey.csv", SURVEY)
    chain = check_package(archive, modules=["package_pii"], offline=True)
    assert [o.title for o in chain] == ["Data Package Personal Data"]
    assert len(chain[0]["hits"]) == 6
    assert chain[0]["hits"]["path"].iloc[0] == "data/survey.csv"


def test_module_options(tmp_path: Path) -> None:
    root = _folder(tmp_path, {"data/survey.csv": SURVEY})
    chain = check_package(
        root,
        modules=["package_pii"],
        args={"package_pii": {"max_rows": 2, "severity": {"person-name": "ignore"}}},
    )
    hits = chain[0]["hits"]
    assert "person-name" not in set(hits["rule"])
    assert set(hits["rows_read"]) == {2}


# -- nothing leaks --------------------------------------------------------------------


def _everything(out: Any) -> str:
    """The text of every output field of a module, as far as it can be shown."""
    parts = [str(out.summary_text), str(out.traffic_light)]
    for key in ("table", "checklist", "hits", "summary_table"):
        parts.append(out[key].to_csv())
    parts.append(repr(out["report"]))
    parts.append(json.dumps(out.run_provenance, default=str))
    return "\n".join(parts)


def test_no_value_is_in_any_output(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    root = _folder(tmp_path, {"data/survey.csv": SURVEY})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["studentnummer", "telefoon", "naam"])
    ws.append([2345671, "06-98765432", "Jan de Vries"])
    ws.append([3456782, "+31 6 55443322", "Anna Smit"])
    ws.append([4567893, "06-98765432", "Piet van der Berg"])
    wb.save(root / "data" / "contacts.xlsx")
    (out,) = check_package(root, modules=["package_pii"])
    assert len(out["hits"]) > 6
    text = _everything(out)
    for sentinel in SENTINELS:
        assert sentinel not in text, sentinel
    # the column names and the counts are shown
    assert "telefoon" in text and "survey.csv" in text


def test_no_value_is_in_the_reports_or_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path, {"data/survey.csv": SURVEY})
    for fmt in ("html", "md", "qmd"):
        out_file = tmp_path / f"report.{fmt}"
        report_package(root, out_file, fmt, modules=["package_pii"])
        written = out_file.read_text(encoding="utf-8")
        assert "telefoon" in written
        for sentinel in SENTINELS:
            assert sentinel not in written, (fmt, sentinel)
    assert main(["package", str(root), "-m", "package_pii", "--json"]) == 0
    printed = capsys.readouterr().out
    assert "pii_phone" in printed
    for sentinel in SENTINELS:
        assert sentinel not in printed, sentinel
    assert main(["package", str(root), "-m", "package_pii"]) == 0
    printed = capsys.readouterr().out
    assert "pii_bsn" in printed
    for sentinel in SENTINELS:
        assert sentinel not in printed, sentinel


def test_a_file_without_a_header_row_does_not_leak_its_first_row(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # the first row is data, so a reader takes it for the column names
    first = ["5612 PV", "2a02:aa::f1", "050 321 9876", "Jan de Vries", "2345671"]
    rows = [
        first,
        ["1013 TX", "2a02:aa::f2", "(070) 888 1234", "Anna Smit", "3456782"],
        ["5612 PV", "2a02:aa::f3", "06-11223344", "Piet van der Berg", "4567893"],
        ["1013 TX", "2a02:aa::f4", "+31 6 55443322", "Sandra Zwart", "2345671"],
    ]
    root = _folder(tmp_path, {"data/raw.csv": "\n".join(",".join(r) for r in rows) + "\n"})
    (out,) = check_package(root, modules=["package_pii"])
    assert set(out["hits"]["rule"]) == {"postcode", "ipv6", "phone"}
    assert set(out["hits"]["column"]) == {"column 1", "column 2", "column 3"}
    texts = [_everything(out)]
    assert main(["package", str(root), "-m", "package_pii", "--json"]) == 0
    texts.append(capsys.readouterr().out)
    report_package(root, tmp_path / "r.md", "md", modules=["package_pii"])
    texts.append((tmp_path / "r.md").read_text(encoding="utf-8"))
    for text in texts:
        for value in first:
            assert value not in text, value


def test_a_sheet_named_like_a_value_is_shown_by_its_position(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.title = "Data"
    wb.active.append(["x"])
    sheet = wb.create_sheet("Jan de Vries")
    sheet.append(["telefoon"])
    for number in ("06-98765432", "+31 6 55443322", "06-11223344"):
        sheet.append([number])
    root = tmp_path / "pkg"
    root.mkdir()
    wb.save(root / "book.xlsx")
    res = _scan(root)
    assert list(res.hits["sheet"]) == ["sheet 2"]
    assert "Jan de Vries" not in " ".join(res.findings["detail"])
