"""The installer scripts: what can be checked with sh and Python alone."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _sh_targets():
    return dict(
        re.findall(r'^\s+([a-z0-9_]+-[a-z0-9-]+)\) expected="([0-9a-f]*)"', SH.read_text(), re.M)
    )


def _ps1_targets():
    return dict(re.findall(r"'([a-z0-9_]+-[a-z0-9-]+)'\s*=\s*'([0-9a-f]*)'", PS1.read_text()))


def test_same_ref_in_both_scripts():
    ref_sh = re.search(r'^\s+REF="([^"]*)"', SH.read_text(), re.M)
    ref_ps1 = re.search(r"^\s+\$Ref = '([^']*)'", PS1.read_text(), re.M)
    assert ref_sh and ref_ps1
    assert re.fullmatch(r"[0-9a-f]{40}", ref_sh.group(1))
    assert ref_sh.group(1) == ref_ps1.group(1)


def test_same_uv_version_in_both_scripts():
    v_sh = re.search(r'^\s+UV_VERSION="([^"]+)"', SH.read_text(), re.M)
    v_ps1 = re.search(r"^\s+\$UvVersion = '([^']+)'", PS1.read_text(), re.M)
    assert v_sh and v_ps1
    assert v_sh.group(1) == v_ps1.group(1)
    assert re.fullmatch(r"\d+\.\d+\.\d+", v_sh.group(1))


def test_every_target_has_a_checksum():
    sh, ps1 = _sh_targets(), _ps1_targets()
    assert set(sh) == {
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-unknown-linux-gnu",
        "aarch64-unknown-linux-gnu",
        "x86_64-unknown-linux-musl",
        "aarch64-unknown-linux-musl",
    }
    assert set(ps1) == {"x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc"}
    for digest in [*sh.values(), *ps1.values()]:
        assert HEX64.match(digest)


def test_sh_syntax():
    """`sh -n` always; bash, dash and zsh as well where they are installed."""
    shells = [shutil.which(name) for name in ("sh", "bash", "dash", "zsh")]
    found = [path for path in shells if path]
    assert shells[0], "sh is required"
    for path in found:
        result = subprocess.run([path, "-n", str(SH)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"


def _code_lines(path, comment):
    lines = path.read_text().splitlines()
    return [ln for ln in lines if ln.strip() and not ln.lstrip().startswith(comment)]


def test_sh_body_is_one_function_called_last():
    code = _code_lines(SH, "#")
    assert code[0] == "main() {"
    assert code[-1] == 'main "$@"'
    # the only other line at column 0 is the closing brace
    top = [ln for ln in code if not ln.startswith((" ", "\t"))]
    assert top == ["main() {", "}", 'main "$@"']


def test_ps1_body_is_one_function_called_last():
    code = _code_lines(PS1, "#")
    assert code[0] == "function Install-Metacheck {"
    assert code[-1] == "Install-Metacheck $args"
    top = [ln for ln in code if not ln.startswith((" ", "\t"))]
    assert top == ["function Install-Metacheck {", "}", "Install-Metacheck $args"]
    assert not re.search(r"^\s*param\s*\(", "\n".join(code[:1] + code[-1:]))


def test_scripts_are_ascii():
    for path in (SH, PS1):
        path.read_text(encoding="ascii")


def _run_uninstall(tmp_path, home_value):
    """Run install.sh --uninstall in a scratch HOME; returns (result, marker)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    marker = home / "keep.txt"
    marker.write_text("keep")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "XDG_DATA_HOME": str(home / "share"),
        "METACHECK_HOME": home_value.replace("@HOME@", str(home)),
    }
    result = subprocess.run(
        ["sh", str(SH), "--uninstall"], env=env, capture_output=True, text=True, timeout=60
    )
    return result, marker, home


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/",
        "@HOME@",
        "@HOME@/",
        "/tmp",
        "@HOME@/notmetacheck",
        "relative/metacheck",
        "@HOME@/metacheck2",
    ],
)
def test_uninstall_refuses_unsafe_folders(tmp_path, value):
    victim_dir = tmp_path / "home" / "notmetacheck"
    victim_dir.mkdir(parents=True)
    (victim_dir / "file").write_text("x")
    result, marker, home = _run_uninstall(tmp_path, value)
    assert result.returncode != 0
    assert "refusing" in result.stderr or "must" in result.stderr
    assert marker.read_text() == "keep"
    assert (victim_dir / "file").read_text() == "x"
    assert home.is_dir()


def test_uninstall_refuses_a_link(tmp_path):
    target = tmp_path / "home" / "real"
    target.mkdir(parents=True)
    (target / "file").write_text("x")
    link = tmp_path / "home" / "metacheck"
    link.symlink_to(target)
    result, marker, _ = _run_uninstall(tmp_path, str(link))
    assert result.returncode != 0
    assert (target / "file").read_text() == "x"
    assert marker.exists()


def test_uninstall_removes_only_its_folders(tmp_path):
    root = tmp_path / "home" / "metacheck"
    for sub in ("uv", "python", "tools", "bin", "cache"):
        (root / sub).mkdir(parents=True)
        (root / sub / "f").write_text("x")
    (root / "mine").mkdir()
    result, marker, _ = _run_uninstall(tmp_path, str(root))
    assert result.returncode == 0, result.stderr
    assert [p.name for p in root.iterdir()] == ["mine"]
    assert "Left" in result.stdout
    assert "settings" in result.stdout

    (root / "mine").rmdir()
    result, _, _ = _run_uninstall(tmp_path, str(root))
    assert result.returncode == 0
    assert not root.exists()
    assert marker.exists()


def test_uninstall_of_a_missing_folder_is_fine(tmp_path):
    result, _, _ = _run_uninstall(tmp_path, "@HOME@/metacheck")
    assert result.returncode == 0
    assert "Nothing to remove" in result.stdout


def test_unknown_option_is_an_error(tmp_path):
    result = subprocess.run(
        ["sh", str(SH), "--bogus"],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unknown option" in result.stderr
