"""The guided setup: the questions, what is saved, and how the app uses it."""

from __future__ import annotations

import os
from pathlib import Path

import orjson
import pytest

import metacheck.config as config
from metacheck.app import bibr, settings, setup, ui


@pytest.fixture(autouse=True)
def private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("METACHECK_EMAIL", "PYTACHECK_EMAIL", "METACHECK_BIBR_URL", "PYTACHECK_BIBR_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(bibr.KEY_ENV, raising=False)
    monkeypatch.setitem(config._state, "email", None)
    return tmp_path


def _wizard(answers: list[str], **kw):
    lines: list[str] = []
    typed = iter(answers)
    result = setup.run_wizard(lambda _p: next(typed), lines.append, **kw)
    return result, "\n".join(lines)


def test_data_only_is_the_default_and_needs_just_an_email() -> None:
    ans, _ = _wizard(["", "me@example.org"], hw="cpu", free=100)
    assert (ans.papers, ans.access, ans.email) == (False, "", "me@example.org")
    assert not ans.wants_bibr_package


def test_steward_mode_skips_the_paper_question() -> None:
    ans, text = _wizard(["me@example.org"], data_only=True, hw="cpu", free=100)
    assert not ans.papers and "What do you want to check" not in text


def test_a_key_is_taken_and_a_blank_key_means_later() -> None:
    ans, _ = _wizard(["2", "1", "bibr_pat_abc", "me@example.org"], hw="cpu", free=100)
    assert (ans.papers, ans.access, ans.key) == (True, "key", "bibr_pat_abc")
    ans, text = _wizard(["2", "1", "", "me@example.org"], hw="cpu", free=100)
    assert ans.access == "later" and ans.key == ""
    assert "metacheck-setup again" in text


def test_self_hosting_warns_about_space_and_hardware_and_needs_a_yes() -> None:
    ans, text = _wizard(["2", "2", "y", "me@example.org"], hw="cpu", free=100)
    assert ans.access == "selfhost" and ans.wants_bibr_package
    assert "several GB" in text and "slowly" in text
    ans, _ = _wizard(["2", "2", "", "me@example.org"], hw="cpu", free=100)
    assert ans.access == "later" and not ans.wants_bibr_package
    _, text = _wizard(["2", "2", "y", "me@example.org"], hw="apple", free=5)
    assert "Apple Silicon" in text and "Only 5 GB are free" in text
    _, text = _wizard(["2", "2", "y", "me@example.org"], hw="gpu", free=100)
    assert "NVIDIA GPU was found" in text


def test_self_hosting_is_the_default_only_where_it_is_fast_and_fits() -> None:
    ans, _ = _wizard(["2", "", "y", "me@example.org"], hw="gpu", free=100)
    assert ans.access == "selfhost"
    ans, _ = _wizard(["2", "", "bibr_pat_abc", "me@example.org"], hw="gpu", free=3)
    assert ans.access == "key"


def test_a_bad_answer_is_asked_again() -> None:
    ans, text = _wizard(["x", "7", "1", "nonsense", "me@example.org"], hw="cpu", free=100)
    assert ans.email == "me@example.org" and "from 1 to 2" in text
    assert "does not look like an email" in text


def test_three_bad_addresses_skip_the_email_with_a_note() -> None:
    ans, text = _wizard(["1", "a", "b", "c"], hw="cpu", free=100)
    assert ans.email == "" and "Skipped" in text


def test_the_email_in_the_environment_is_the_default() -> None:
    ans, _ = _wizard(["1", ""], hw="cpu", free=100, default_email="env@example.org")
    assert ans.email == "env@example.org"


def test_save_remembers_the_email_the_key_and_the_choice() -> None:
    setup.save(setup.Answers(papers=True, access="key", key="bibr_pat_abc", email="me@example.org"))
    assert settings.load() == {"email": "me@example.org"}
    assert bibr.load_key() == "bibr_pat_abc"
    assert oct(settings.settings_path().stat().st_mode & 0o777) == "0o600" or os.name == "nt"
    setup.save(setup.Answers(papers=True, access="selfhost", email="me@example.org"))
    assert settings.load()["bibr_selfhost"] is True
    setup.save(setup.Answers(papers=True, access="key", key="k" * 12, email="me@example.org"))
    assert "bibr_selfhost" not in settings.load()  # changed their mind


def test_apply_sets_the_email_and_the_local_address_but_the_environment_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.write(email="me@example.org", bibr_selfhost=True)
    settings.apply()
    assert config.email() == "me@example.org"
    assert os.environ["METACHECK_BIBR_URL"] == settings.SELFHOST_URL
    monkeypatch.delenv("METACHECK_BIBR_URL")
    monkeypatch.setenv("PYTACHECK_BIBR_URL", "https://bibr.example.org")
    settings.apply()
    assert "METACHECK_BIBR_URL" not in os.environ


def test_apply_ignores_a_damaged_file() -> None:
    path = settings.settings_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{nope")
    assert settings.apply() == {}


def test_start_selfhost_does_nothing_unless_chosen_and_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "_bibr_command", lambda: None)
    assert settings.start_selfhost() is None
    settings.write(bibr_selfhost=True)
    assert settings.start_selfhost() is None  # no bibr command next to this Python


def test_bibr_on_this_computer_needs_no_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from metacheck.app.run import UserError

    monkeypatch.setenv("METACHECK_BIBR_URL", settings.SELFHOST_URL)
    seen: dict[str, str] = {}

    import metacheck as pc

    def fake(path, **kw):
        seen.update(kw)
        out = tmp_path / "paper.json"
        out.write_text("{}")
        return [str(out)]

    monkeypatch.setattr(pc, "convert", fake)
    bibr.convert_pdf(tmp_path / "p.pdf", tmp_path, "")
    assert seen["api_url"] == settings.SELFHOST_URL
    monkeypatch.setenv("METACHECK_BIBR_URL", "https://bibr.example.org")
    with pytest.raises(UserError, match="Enter your bibr key"):
        bibr.convert_pdf(tmp_path / "p.pdf", tmp_path, "")


def test_the_page_starts_on_bibr_after_the_setup_chose_it() -> None:
    assert ui.default_reader(True) == "grobid"
    bibr.save_key("bibr_pat_abc")
    assert ui.default_reader(True) == "bibr"
    assert ui.default_reader() == "grobid"  # a shared server never reads this computer's files


def test_main_writes_the_result_and_skips_when_answered_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    typed = iter(["2", "2", "y", "me@example.org"])
    monkeypatch.setattr(setup, "_tty_prompter", lambda: (lambda _p: next(typed), None))
    monkeypatch.setattr(setup, "hardware", lambda: "gpu")
    result = tmp_path / "result"
    assert setup.main(["--result", str(result)]) == 0
    assert result.read_text() == "install_bibr=1\n"
    assert orjson.loads(settings.settings_path().read_bytes())["email"] == "me@example.org"
    # asked once: --if-new does not ask again
    monkeypatch.setattr(setup, "_tty_prompter", lambda: pytest.fail("asked again"))
    assert setup.main(["--if-new", "--result", str(result)]) == 0
    assert result.read_text() == "install_bibr=0\n"


def test_main_without_a_terminal_skips_politely(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(setup, "_tty_prompter", lambda: None)
    assert setup.main([]) == 0
    assert "skipping setup" in capsys.readouterr().out


def test_cancelling_saves_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def ask(_p: str) -> str:
        raise EOFError

    monkeypatch.setattr(setup, "_tty_prompter", lambda: (ask, None))
    assert setup.main([]) == 1
    assert not settings.settings_path().exists()


def test_a_console_nobody_types_into_is_not_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    class NotATerminal(io.StringIO):
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(setup.sys, "stdin", NotATerminal())
    monkeypatch.setattr("builtins.open", lambda *a, **k: NotATerminal())
    assert setup._tty_prompter() is None
