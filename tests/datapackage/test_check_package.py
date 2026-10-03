"""Running the checks on a data package: ``check_package``, ``report_package``, ``metacheck package``.

The three package checks are being written, so most tests here run small modules
of their own (a probe that records what it was given); only the default preset's
modules are named, not run, except ``data_check`` and ``codebook_check``, which run
on a tiny package with every network access refused.
"""

from __future__ import annotations

import json
import textwrap
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from metacheck.cli import main
from metacheck.config import update_config
from metacheck.datapackage import (
    DEFAULT_PRESET,
    PackageError,
    check_package,
    package_selection,
    report_package,
)
from metacheck.packs.registry import refresh
from tests.httpmock import no_network

PROBE = textwrap.dedent(
    """
    from metacheck.module import module


    @module(title="Probe", description="Records what it was given", keywords=["general"])
    def probe(paper, local_path=None, local_only=None, note="none"):
        import os

        import pandas as pd

        from metacheck.datapackage import checklist_frame, package_for

        pkg = package_for(local_path)
        files = [] if pkg is None else sorted(pkg.files()["rel"])
        title = str(paper.info["title"].iloc[0])
        given = {"local_path": str(local_path), "local_only": local_only, "title": title}
        return {
            "summary_text": f"{len(files)} files, local_only={local_only}, note={note}",
            "table": pd.DataFrame({"rel": pd.Series(files, dtype="string")}),
            "checklist": checklist_frame(
                [{"item": "probe", "title": "Probe", "status": "pass", "detail": "ok"}]
            ),
            "given": given,
            "existed": pkg is not None and os.path.isdir(local_path),
        }
    """
)

PLAIN = textwrap.dedent(
    """
    from metacheck.module import module


    @module(title="Plain", description="Takes nothing but a paper", keywords=["general"])
    def plain(paper):
        return {"summary_text": "ran"}
    """
)

BROKEN = textwrap.dedent(
    """
    from metacheck.module import module


    @module(title="Broken", description="Always fails", keywords=["general"])
    def broken(paper, local_path=None):
        raise RuntimeError("this check is broken")
    """
)

ONLINE = textwrap.dedent(
    """
    from metacheck.module import module


    @module(title="Online", description="Needs the network", keywords=["general"],
            requires=["network"])
    def online(paper):
        return {"summary_text": "never runs offline"}
    """
)

FILES = {
    "README.md": "# Study\n\nData and code.\n",
    "data/survey.csv": "participant_id,age,gender\n1,23,f\n2,31,m\n3,27,f\n4,45,m\n",
    "docs/codebook.csv": "variable,label\nparticipant_id,Participant id\nage,Age in years\n"
    "gender,Gender\n",
    "code/analysis.R": "d <- read.csv('data/survey.csv')\n",
}


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No user config or packs, and the concept model is never loaded (a download)."""
    monkeypatch.setenv("PYTACHECK_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PYTACHECK_PRESET", raising=False)
    monkeypatch.chdir(tmp_path)

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the local concept model must not be loaded in a test")

    monkeypatch.setattr("metacheck.datacheck.concepts.load_classifier", refuse)
    refresh()
    yield
    refresh()


@pytest.fixture
def probe(tmp_path: Path) -> str:
    path = tmp_path / "mods" / "probe.py"
    path.parent.mkdir()
    path.write_text(PROBE)
    return str(path)


def _mod(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / "mods" / f"{name}.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(source)
    return str(path)


def _only(result: Any) -> Any:
    """The one module output of a report."""
    (out,) = result.values()
    return out


def _folder(tmp_path: Path, name: str = "study1", files: dict[str, str] | None = None) -> Path:
    root = tmp_path / name
    for rel, text in (FILES if files is None else files).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def _zip(tmp_path: Path, name: str = "study1", wrapper: bool = True) -> Path:
    archive = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for rel, text in FILES.items():
            zf.writestr(f"{name}/{rel}" if wrapper else rel, text)
    return archive


# -- which modules run ----------------------------------------------------------------


def test_the_default_selection_is_the_datapackage_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    sel = package_selection()
    assert sel.preset == DEFAULT_PRESET == "datapackage::default"
    assert sel.modules == [
        "datapackage::package_files",
        "datapackage::package_structure",
        "datapackage::package_docs",
        "metacheck::data_check",
        "metacheck::codebook_check",
    ]
    # the preset the user configured for papers is not a package preset
    monkeypatch.setenv("PYTACHECK_PRESET", "metacheck::repository")
    update_config("user", lambda cfg: cfg.update(preset="metacheck::repository"))
    assert package_selection().modules[0] == "datapackage::package_files"


def test_modules_beat_preset_beat_default() -> None:
    assert package_selection(modules=["package_files"]).modules == ["package_files"]
    assert package_selection(modules=["package_files"]).preset is None
    assert package_selection(preset="datapackage::default").modules[0] == (
        "datapackage::package_files"
    )
    both = package_selection(preset="datapackage::default", modules=["package_docs"])
    assert both.modules[-1] == "package_docs"  # a preset, then the modules, as in run
    ready = package_selection(modules=["package_files"])
    assert package_selection(modules=ready, preset="other") is ready


# -- check_package --------------------------------------------------------------------


def test_every_module_that_takes_them_gets_the_folder_and_local_only(
    tmp_path: Path, probe: str
) -> None:
    root = _folder(tmp_path)
    plain = _mod(tmp_path, "plain", PLAIN)
    chain = check_package(root, modules=[probe, plain])
    assert [o.title for o in chain] == ["Probe", "Plain"]
    out = chain[0]
    assert out.extras["given"] == {
        "local_path": str(root.resolve()),
        "local_only": True,
        "title": "study1",  # the stand-in paper is titled with the package's name
    }
    assert out.table["rel"].tolist() == [
        "README.md",
        "code/analysis.R",
        "data/survey.csv",
        "docs/codebook.csv",
    ]
    assert chain[1].summary_text == "ran"  # no local_path to give it, and no error
    assert chain[1].run_provenance["args"] == {}
    assert out.extras["checklist"]["status"].tolist() == ["pass"]


def test_a_value_the_caller_gave_is_not_overridden(tmp_path: Path, probe: str) -> None:
    root = _folder(tmp_path)
    other = _folder(tmp_path, "other", {"a.csv": "a\n1\n"})
    chain = check_package(
        root,
        modules=[probe],
        args={"probe": {"local_path": str(other), "local_only": False, "note": "mine"}},
    )
    assert chain[0].extras["given"]["local_path"] == str(other)
    assert chain[0].extras["given"]["local_only"] is False
    assert chain[0].table["rel"].tolist() == ["a.csv"]
    assert chain[0].summary_text == "1 files, local_only=False, note=mine"
    # only local_only was given by the caller here: local_path still comes from the package
    chain = check_package(root, modules=[probe], args={"probe": {"local_only": False}})
    assert chain[0].extras["given"]["local_path"] == str(root.resolve())
    assert chain[0].extras["given"]["local_only"] is False


def test_a_zip_is_extracted_and_removed_afterwards(tmp_path: Path, probe: str) -> None:
    archive = _zip(tmp_path)
    chain = check_package(archive, modules=[probe])
    out = chain[0]
    extracted = Path(out.extras["given"]["local_path"])
    assert out.extras["existed"]  # it was there while the module ran
    assert extracted.name == "study1"  # the archive's wrapper folder
    assert not extracted.exists()  # and is removed now
    assert not extracted.parent.exists()
    assert out.extras["given"]["title"] == "study1"
    assert out.table["rel"].tolist() == [
        "README.md",
        "code/analysis.R",
        "data/survey.csv",
        "docs/codebook.csv",
    ]
    assert out.table is not None  # the results are data, complete after the folder is gone


def test_an_archive_over_the_limits_given_is_refused(tmp_path: Path, probe: str) -> None:
    archive = _zip(tmp_path)  # four small files
    with pytest.raises(PackageError, match="more than 3 files"):
        check_package(archive, modules=[probe], max_files=3)
    with pytest.raises(PackageError, match="bigger than"):
        check_package(archive, modules=[probe], max_bytes=10)
    with pytest.raises(PackageError, match="more than 3 files"):
        report_package(archive, tmp_path / "r.html", modules=[probe], max_files=3)
    assert len(check_package(archive, modules=[probe], max_files=4, max_bytes=10_000)) == 1
    # a folder has no limits
    assert len(check_package(_folder(tmp_path), modules=[probe], max_files=1)) == 1


def test_the_temporary_folder_is_removed_when_a_module_fails(
    tmp_path: Path, probe: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import metacheck.datapackage._check as mod

    archive = _zip(tmp_path)
    broken = _mod(tmp_path, "broken", BROKEN)
    seen: list[str] = []
    real = mod._give_package

    def spy(selection: Any, opened: Any) -> Any:
        seen.append(str(opened.root))
        return real(selection, opened)

    monkeypatch.setattr(mod, "_give_package", spy)
    with pytest.warns(UserWarning, match="Error in"):
        chain = check_package(archive, modules=[broken, probe])
    assert [o.traffic_light for o in chain] == ["fail", "info"]
    assert chain[0].run_provenance["status"] == "fail"
    assert chain[1].summary_text.startswith("4 files")  # the chain went on
    assert seen
    assert not Path(seen[0]).exists()


def test_the_run_record_names_the_package(tmp_path: Path, probe: str) -> None:
    archive = _zip(tmp_path)
    chain = check_package(archive, modules=[probe], record=tmp_path / "run.json")
    data = json.loads((tmp_path / "run.json").read_text())
    assert data["package"] == {"name": "study1", "source": str(archive), "archive": True}
    assert data["papers"] == []
    assert data["preset"] is None
    # the temporary folder is not part of the record
    assert data["modules"][0]["args"]["local_path"] == str(archive)
    assert data["modules"][0]["args"]["local_only"] is True
    assert chain.run_record.package["name"] == "study1"
    assert chain[0].run_provenance["args"]["local_path"] == str(archive)

    folder = _folder(tmp_path)
    chain = check_package(folder, modules=[probe])
    assert chain.run_record.package == {
        "name": "study1",
        "source": str(folder),
        "archive": False,
    }
    from metacheck.provenance import RunRecord

    assert RunRecord.read(chain.run_record.to_json()).package == chain.run_record.package


def test_a_folder_given_as_a_dot_is_named_after_the_folder(
    tmp_path: Path, probe: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _folder(tmp_path, "my package")
    monkeypatch.chdir(root)
    chain = check_package(".", modules=[probe])
    assert chain[0].extras["given"]["title"] == "my package"


def test_bad_paths_raise_package_error(tmp_path: Path, probe: str) -> None:
    with pytest.raises(PackageError, match="No folder or archive found"):
        check_package(tmp_path / "nope", modules=[probe])
    (tmp_path / "paper.pdf").write_text("x")
    with pytest.raises(PackageError, match="neither a folder nor an archive"):
        check_package(tmp_path / "paper.pdf", modules=[probe])
    with pytest.raises(PackageError):
        report_package(tmp_path / "nope", tmp_path / "r.html", modules=[probe])
    assert not (tmp_path / "r.html").exists()


def test_a_pack_can_supply_its_own_policy(tmp_path: Path, probe: str) -> None:
    pack = tmp_path / "policy"
    pack.mkdir()
    (pack / "pack.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "name": "policy",
                "version": "1.0.0",
                "presets": {
                    "default": {
                        "description": "Our archive policy",
                        "modules": ["probe"],
                        "args": {"probe": {"note": "from the preset", "local_only": False}},
                    }
                },
            }
        )
    )
    (pack / "probe.py").write_text(PROBE)
    update_config(
        "user", lambda cfg: cfg.setdefault("packs", {}).update(policy={"path": str(pack)})
    )
    refresh()
    root = _folder(tmp_path)
    chain = check_package(root, preset="policy::default")
    assert [o.module for o in chain] == ["probe"]
    assert chain[0].summary_text == "4 files, local_only=False, note=from the preset"
    assert chain[0].extras["given"]["local_path"] == str(root.resolve())  # still given
    assert chain.run_record.preset == "policy::default"


# -- the stock modules run offline ----------------------------------------------------


def test_data_check_and_codebook_check_run_offline_on_a_small_package(tmp_path: Path) -> None:
    root = _folder(tmp_path)
    with no_network() as attempts:
        chain = check_package(
            root,
            modules=["metacheck::data_check", "metacheck::codebook_check"],
            args={"data_check": {"concepts": "rules"}},
        )
    assert attempts == []  # no connection, and no name looked up
    assert [o.module for o in chain] == ["data_check", "codebook_check"]
    assert all(o.run_provenance.get("status") != "fail" for o in chain)
    data, codebook = chain
    assert data.run_provenance["args"]["local_only"] is True
    assert data.run_provenance["args"]["local_path"] == str(root.resolve())
    assert sorted(data.table["column_name"].dropna()) == ["age", "gender", "participant_id"]
    assert codebook.traffic_light != "fail"
    assert "3 of 3 data columns" in str(codebook.summary_text)


def test_the_default_preset_modules_run_through_the_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path)
    with no_network() as attempts:
        assert main(["package", str(root), "-a", "concepts=rules"]) == 0
    assert attempts == []
    out = capsys.readouterr().out
    assert "Data Check" in out
    assert "Codebook Check" in out
    assert "3 of 3 data columns" in out


# -- report_package -------------------------------------------------------------------


def test_report_package_writes_a_report_titled_with_the_package(tmp_path: Path, probe: str) -> None:
    root = _folder(tmp_path)
    out = tmp_path / "reports"
    out.mkdir()
    result = report_package(root, out / "r.html", modules=[probe])
    assert result.save_path == str(out / "r.html")
    html = (out / "r.html").read_text(encoding="utf-8")
    assert "study1" in html
    assert "<title>" in html
    assert [o.title for o in result.values()] == ["Probe"]


def test_the_default_report_file_is_named_after_the_package(tmp_path: Path, probe: str) -> None:
    archive = _zip(tmp_path, "mydata")
    result = report_package(archive, modules=[probe])
    assert result.save_path == "mydata_report.html"
    assert (tmp_path / "mydata_report.html").exists()
    report_package(archive, output_format="md", modules=[probe])
    assert "mydata" in (tmp_path / "mydata_report.md").read_text(encoding="utf-8")
    report_package(archive, output_format="qmd", modules=[probe])
    assert (tmp_path / "mydata_report.qmd").exists()
    with pytest.raises(ValueError, match="output_format"):
        report_package(archive, output_format="pdf", modules=[probe])


def test_a_report_inside_the_package_is_not_one_of_its_files(tmp_path: Path, probe: str) -> None:
    root = _folder(tmp_path)
    result = report_package(root, root / "report.html", modules=[probe])
    assert (root / "report.html").exists()
    assert "report.html" not in _only(result).table["rel"].tolist()
    # and a second run, with the first report in the folder, lists it like any other file
    again = report_package(root, root / "report2.html", modules=[probe])
    assert "report.html" in _only(again).table["rel"].tolist()
    assert "report2.html" not in _only(again).table["rel"].tolist()


def test_an_unwritable_report_path_fails_before_anything_runs(tmp_path: Path, probe: str) -> None:
    root = _folder(tmp_path)
    with pytest.raises(ValueError, match="not a valid path"):
        report_package(root, tmp_path / "no" / "such" / "r.html", modules=[probe])


def test_report_package_gives_the_package_to_its_modules(tmp_path: Path, probe: str) -> None:
    archive = _zip(tmp_path)
    result = report_package(
        archive, tmp_path / "r.html", modules=[probe], record=tmp_path / "r.json"
    )
    out = _only(result)
    extracted = Path(out.extras["given"]["local_path"])
    assert out.extras["given"]["local_only"] is True
    assert not extracted.exists()
    record = json.loads((tmp_path / "r.json").read_text())
    assert record["package"]["name"] == "study1"
    assert record["modules"][0]["args"]["local_path"] == str(archive)


# -- metacheck package ----------------------------------------------------------------


def test_cli_prints_the_results_and_the_checklist(
    tmp_path: Path, probe: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path)
    assert main(["package", str(root), "-m", probe]) == 0
    captured = capsys.readouterr()
    assert "Probe: 4 files, local_only=True, note=none" in captured.out
    assert "Checklist" in captured.out
    assert "probe" in captured.out and "pass" in captured.out
    assert not list(tmp_path.glob("*_report.*"))  # printing writes no report


def test_cli_json(tmp_path: Path, probe: str, capsys: pytest.CaptureFixture[str]) -> None:
    archive = _zip(tmp_path)
    assert main(["package", str(archive), "-m", probe, "--json", "-a", "note=hello"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [p["title"] for p in payload] == ["Probe"]
    assert payload[0]["summary_text"] == "4 files, local_only=True, note=hello"
    assert payload[0]["table"][0] == {"rel": "README.md"}
    assert payload[0]["checklist"][0]["item"] == "probe"


def test_cli_arguments_for_one_module(tmp_path: Path, probe: str, capsys: Any) -> None:
    root = _folder(tmp_path)
    assert main(["package", str(root), "-m", probe, "-a", "probe.local_only=false"]) == 0
    assert "local_only=False" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="none of the selected modules accepts 'nope'"):
        main(["package", str(root), "-m", probe, "-a", "nope=1"])


def test_cli_writes_a_report_with_o(tmp_path: Path, probe: str, capsys: Any) -> None:
    root = _folder(tmp_path)
    assert main(["package", str(root), "-m", probe, "-o", "report.html"]) == 0
    captured = capsys.readouterr()
    assert "Probe" in captured.out
    assert "Report: report.html" in captured.err
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "study1" in html
    # the folder was not touched
    assert sorted(p.name for p in root.iterdir()) == ["README.md", "code", "data", "docs"]


def test_cli_f_alone_writes_the_default_file(tmp_path: Path, probe: str, capsys: Any) -> None:
    archive = _zip(tmp_path, "pkg2")
    assert main(["package", str(archive), "-m", probe, "-f", "md"]) == 0
    assert (tmp_path / "pkg2_report.md").exists()
    assert "Report: pkg2_report.md" in capsys.readouterr().err


def test_cli_record(tmp_path: Path, probe: str, capsys: Any) -> None:
    root = _folder(tmp_path)
    assert main(["package", str(root), "-m", probe, "--record", "run.json"]) == 0
    assert json.loads((tmp_path / "run.json").read_text())["package"]["name"] == "study1"
    assert "Run record: run.json" in capsys.readouterr().err


def test_cli_a_missing_path_is_one_clean_line_and_exit_2(
    tmp_path: Path, probe: str, capsys: Any
) -> None:
    assert main(["package", str(tmp_path / "nope"), "-m", probe]) == 2
    err = capsys.readouterr().err
    assert "error: No folder or archive found at" in err
    assert "Traceback" not in err
    (tmp_path / "paper.pdf").write_text("x")
    assert main(["package", str(tmp_path / "paper.pdf"), "-m", probe, "-o", "r.html"]) == 2
    assert "neither a folder nor an archive" in capsys.readouterr().err
    assert not (tmp_path / "r.html").exists()
    (tmp_path / "bad.zip").write_text("not a zip")
    assert main(["package", str(tmp_path / "bad.zip"), "-m", probe]) == 2
    assert "cannot be read as an archive" in capsys.readouterr().err


def test_cli_an_unwritable_report_path_is_exit_2(tmp_path: Path, probe: str, capsys: Any) -> None:
    root = _folder(tmp_path)
    assert main(["package", str(root), "-m", probe, "-o", str(tmp_path / "x" / "r.html")]) == 2
    assert "not a valid path" in capsys.readouterr().err


def test_cli_exit_1_when_a_module_failed(tmp_path: Path, probe: str, capsys: Any) -> None:
    root = _folder(tmp_path)
    broken = _mod(tmp_path, "broken", BROKEN)
    assert main(["package", str(root), "-m", broken, "-m", probe]) == 1
    captured = capsys.readouterr()
    assert "this check is broken" in captured.out + captured.err
    assert "1 module(s) failed to run" in captured.err
    assert "Probe" in captured.out  # the others still ran
    # also when a report is written
    assert main(["package", str(root), "-m", broken, "-o", str(tmp_path / "r.html")]) == 1
    assert (tmp_path / "r.html").exists()
    assert "1 module(s) failed to run" in capsys.readouterr().err


def test_cli_offline_leaves_out_what_needs_the_network(
    tmp_path: Path, probe: str, capsys: Any
) -> None:
    root = _folder(tmp_path)
    online = _mod(tmp_path, "online", ONLINE)
    assert main(["package", str(root), "-m", online, "-m", probe, "--offline"]) == 0
    captured = capsys.readouterr()
    assert "Offline: skipped" in captured.err
    assert "Online" not in captured.out
    assert main(["package", str(root), "-m", online, "--offline"]) == 2
    assert "No modules to run" in capsys.readouterr().err


def test_the_package_command_is_listed_in_the_help(capsys: Any) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    assert "package" in out
    with pytest.raises(SystemExit):
        main(["package", "--help"])
    out = capsys.readouterr().out
    for flag in ("--preset", "--offline", "--record", "--json", "--output", "--format"):
        assert flag in out
