"""The installer scripts: what can be checked with sh and Python alone."""

import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "install.sh"
PS1 = ROOT / "install.ps1"

HEX64 = re.compile(r"^[0-9a-f]{64}$")

# install.sh is for macOS and Linux; Windows uses install.ps1.
needs_sh = pytest.mark.skipif(sys.platform == "win32", reason="install.sh is for macOS and Linux")


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


@needs_sh
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


def test_scripts_are_ascii():
    for path in (SH, PS1):
        path.read_text(encoding="ascii")


def _run_uninstall(tmp_path, home_value, home_name="home"):
    """Run install.sh --uninstall in a scratch HOME; returns (result, marker, home)."""
    home = tmp_path / home_name
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


def _make_installed(root):
    for sub in ("uv", "python", "tools", "bin", "cache"):
        (root / sub).mkdir(parents=True)
        (root / sub / "f").write_text("x")
    (root / ".metacheck-installer").write_text("")


@needs_sh
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


@needs_sh
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


@needs_sh
def test_uninstall_removes_only_its_folders(tmp_path):
    root = tmp_path / "home" / "metacheck"
    _make_installed(root)
    (root / "mine").mkdir()
    result, marker, _ = _run_uninstall(tmp_path, str(root))
    assert result.returncode == 0, result.stderr
    assert [p.name for p in root.iterdir()] == ["mine"]
    assert "Left" in result.stdout
    assert "Removed metacheck" not in result.stdout
    assert "settings" in result.stdout

    shutil.rmtree(root)
    _make_installed(root)
    result, _, _ = _run_uninstall(tmp_path, str(root))
    assert result.returncode == 0
    assert "Removed metacheck" in result.stdout
    assert not root.exists()
    assert marker.exists()


@needs_sh
def test_uninstall_needs_the_marker(tmp_path):
    root = tmp_path / "home" / "metacheck"
    _make_installed(root)
    (root / ".metacheck-installer").unlink()
    result, _, _ = _run_uninstall(tmp_path, str(root))
    assert result.returncode != 0
    assert (root / "bin" / "f").read_text() == "x"


@needs_sh
@pytest.mark.parametrize(
    "spelling", ["{h}", "{h}/", "{h}//", "{p}//{n}", "{p}/./{n}", "{h}/../{n}"]
)
def test_uninstall_refuses_the_home_folder_in_any_spelling(tmp_path, spelling):
    # HOME is itself named metacheck, so only the home-folder guard can refuse it.
    home = tmp_path / "hm" / "metacheck"
    _make_installed(home)
    value = spelling.format(h=home, p=home.parent, n=home.name)
    result, _, _ = _run_uninstall(tmp_path / "hm", value, home_name="metacheck")
    assert result.returncode != 0
    assert (home / "bin" / "f").read_text() == "x"


@needs_sh
def test_uninstall_refuses_the_home_folder_when_home_has_a_trailing_slash(tmp_path):
    home = tmp_path / "hm" / "metacheck"
    _make_installed(home)
    env = {"PATH": os.environ["PATH"], "HOME": f"{home}/", "METACHECK_HOME": str(home)}
    result = subprocess.run(
        ["sh", str(SH), "--uninstall"], env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode != 0
    assert (home / "bin" / "f").read_text() == "x"


@needs_sh
def test_uninstall_of_a_missing_folder_is_fine(tmp_path):
    result, _, _ = _run_uninstall(tmp_path, "@HOME@/metacheck")
    assert result.returncode == 0
    assert "Nothing to remove" in result.stdout


@needs_sh
def test_unknown_option_is_an_error(tmp_path):
    result = subprocess.run(
        ["sh", str(SH), "--bogus"],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unknown option" in result.stderr


FAKE_UV = """\
#!/bin/sh
case "$1" in
  --version) echo "uv 0.12.20" ;;
  tool)
    echo "$@" >>"$UV_TOOL_BIN_DIR/../calls"
    env | grep '^UV_' | sort >"$UV_TOOL_BIN_DIR/../env"
    if [ -n "${FAKE_UV_SLEEP:-}" ]; then
      : >"$UV_TOOL_BIN_DIR/../started"
      sleep "$FAKE_UV_SLEEP"
    fi
    mkdir -p "$UV_TOOL_BIN_DIR"
    : >"$UV_TOOL_BIN_DIR/metacheck-app"
    chmod 755 "$UV_TOOL_BIN_DIR/metacheck-app"
    ;;
esac
"""


def _fake_install_env(tmp_path, **extra):
    """A metacheck folder that already holds a stand-in uv, so nothing is downloaded."""
    root = tmp_path / "home" / "metacheck"
    (root / "uv").mkdir(parents=True)
    fake = root / "uv" / "uv"
    fake.write_text(FAKE_UV)
    fake.chmod(0o755)
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("")
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "METACHECK_HOME": str(root),
        "METACHECK_SPEC": "pytacheck",
        "METACHECK_CONSTRAINTS": str(constraints),
        "METACHECK_NO_LAUNCH": "1",
        **extra,
    }
    return root, env


@needs_sh
def test_install_pins_uv_and_keeps_the_tool_on_a_second_run(tmp_path):
    root, env = _fake_install_env(
        tmp_path, UV_PYTHON_DOWNLOADS="never", UV_PYTHON_PREFERENCE="only-system", UV_OFFLINE="1"
    )
    for _ in range(2):
        result = subprocess.run(
            ["sh", str(SH)], env=env, capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, result.stderr
    assert "metacheck is installed" in result.stdout
    assert (root / ".metacheck-installer").exists()
    seen = (root / "env").read_text()
    assert "UV_PYTHON_DOWNLOADS=automatic" in seen
    assert "UV_PYTHON_PREFERENCE" not in seen
    assert "UV_OFFLINE" not in seen
    assert f"UV_TOOL_DIR={root}/tools" in seen
    # no --force: re-running must not rebuild an environment that is in use
    assert "--force" not in (root / "calls").read_text()


@needs_sh
def test_a_terminate_signal_stops_the_installer(tmp_path):
    root, env = _fake_install_env(tmp_path, FAKE_UV_SLEEP="3")
    proc = subprocess.Popen(
        ["sh", str(SH)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    deadline = time.monotonic() + 30
    while not (root / "started").exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode == 143
    assert "metacheck is installed" not in out


STUB_APP = """\
#!{python}
import http.server, sys, threading, time
if "--self-test" in sys.argv:
    sys.exit(0)
port = int(sys.argv[sys.argv.index("--port") + 1])

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def reply(self, code, cookie=False):
        self.send_response(code)
        if cookie:
            self.send_header("Set-Cookie", "session=1; Path=/")
        self.end_headers()
        self.wfile.write(b'{{"ok": true}}')
    def do_GET(self):
        if self.path == "/healthz":
            self.reply(200)
        elif self.path.startswith("/?token=abc"):
            self.reply(200, cookie=True)
        elif "session=1" in (self.headers.get("Cookie") or ""):
            self.reply(200)
        else:
            self.reply(403)

server = http.server.HTTPServer(("127.0.0.1", port), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(1)  # the server answers before the link is printed
print("metacheck is running at http://127.0.0.1:%d/?token=abc" % port)  # not flushed
time.sleep(60)
"""


@needs_sh
def test_smoke_waits_for_the_link_and_reads_unflushed_output(tmp_path):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    stub = tmp_path / "metacheck-app"
    stub.write_text(STUB_APP.format(python=sys.executable))
    stub.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(ROOT / "install" / "smoke.py"), str(stub), "--port", str(port)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "token link and cookie: 200" in result.stdout


# A stand-in uv that keeps uv's real layout: tools/<name>/ holds the tool and its
# receipt, bin/ holds links into it, and a link that belongs to another tool is
# only replaced with --force.
FAKE_UV_TOOLS = """\
#!/bin/sh
case "$1" in
  --version) echo "uv 0.12.20" ;;
  tool)
    echo "$@" >>"$UV_TOOL_BIN_DIR/../calls"
    force=0
    for a in "$@"; do [ "$a" = "--force" ] && force=1; done
    if [ -n "${FAKE_UV_FAIL:-}" ]; then
      echo "error: no network" >&2
      exit 1
    fi
    mkdir -p "$UV_TOOL_DIR/metacheck/bin" "$UV_TOOL_BIN_DIR"
    : >"$UV_TOOL_DIR/metacheck/uv-receipt.toml"
    printf '#!/bin/sh\\necho NEW\\n' >"$UV_TOOL_DIR/metacheck/bin/metacheck-app"
    chmod 755 "$UV_TOOL_DIR/metacheck/bin/metacheck-app"
    if [ -e "$UV_TOOL_BIN_DIR/metacheck-app" ] || [ -L "$UV_TOOL_BIN_DIR/metacheck-app" ]; then
      if [ "$force" = "0" ]; then
        echo "error: executable already exists: metacheck-app" >&2
        exit 1
      fi
      rm -f "$UV_TOOL_BIN_DIR/metacheck-app"
    fi
    ln -s "$UV_TOOL_DIR/metacheck/bin/metacheck-app" "$UV_TOOL_BIN_DIR/metacheck-app"
    ;;
esac
"""


def _old_tool_env(tmp_path, **extra):
    """A folder as an installer before 0.4.0a1 left it: tool pytacheck with two commands."""
    root, env = _fake_install_env(tmp_path, **extra)
    (root / "uv" / "uv").write_text(FAKE_UV_TOOLS)
    old = root / "tools" / "pytacheck"
    (old / "bin").mkdir(parents=True)
    (old / "uv-receipt.toml").write_text("[tool]\n")
    (root / "bin").mkdir()
    for name in ("metacheck-app", "pytacheck-extra"):
        script = old / "bin" / name
        script.write_text("#!/bin/sh\necho OLD\n")
        script.chmod(0o755)
        (root / "bin" / name).symlink_to(script)
    return root, env


def _run_installer(env):
    return subprocess.run(["sh", str(SH)], env=env, capture_output=True, text=True, timeout=60)


@needs_sh
def test_old_tool_is_replaced_once_the_new_one_is_in(tmp_path):
    root, env = _old_tool_env(tmp_path)
    result = _run_installer(env)
    assert result.returncode == 0, result.stderr
    assert not (root / "tools" / "pytacheck").exists()
    assert (root / "tools" / "metacheck").is_dir()
    assert "--force" in (root / "calls").read_text()
    assert "uninstall" not in (root / "calls").read_text()
    out = subprocess.run([str(root / "bin" / "metacheck-app")], capture_output=True, text=True)
    assert out.stdout.strip() == "NEW"
    # a command only the old tool had would point at nothing
    assert not (root / "bin" / "pytacheck-extra").is_symlink()


@needs_sh
def test_old_tool_stays_when_the_new_install_fails(tmp_path):
    root, env = _old_tool_env(tmp_path, FAKE_UV_FAIL="1")
    result = _run_installer(env)
    assert result.returncode != 0
    assert "the install failed" in result.stderr
    assert "earlier version is still installed" in result.stderr
    assert (root / "tools" / "pytacheck" / "uv-receipt.toml").exists()
    for name in ("metacheck-app", "pytacheck-extra"):
        out = subprocess.run([str(root / "bin" / name)], capture_output=True, text=True)
        assert out.stdout.strip() == "OLD"


@needs_sh
def test_fresh_install_has_no_force(tmp_path):
    root, env = _fake_install_env(tmp_path)
    (root / "uv" / "uv").write_text(FAKE_UV_TOOLS)
    result = _run_installer(env)
    assert result.returncode == 0, result.stderr
    assert "--force" not in (root / "calls").read_text()
    assert (root / "tools" / "metacheck").is_dir()
    failing = _run_installer({**env, "FAKE_UV_FAIL": "1"})
    assert failing.returncode != 0
    assert "earlier version" not in failing.stderr


# A stand-in uv whose app answers --help (with or without the --page option, as
# FAKE_APP_PAGE says) and records the arguments it was started with.
FAKE_UV_APP = """\
#!/bin/sh
case "$1" in
  --version) echo "uv 0.12.20" ;;
  tool)
    echo "$@" >>"$UV_TOOL_BIN_DIR/../calls"
    mkdir -p "$UV_TOOL_BIN_DIR"
    cat >"$UV_TOOL_BIN_DIR/metacheck-app" <<'APP'
#!/bin/sh
if [ "$1" = "--help" ]; then
  echo "usage: metacheck-app [-h] [--no-browser] [--port PORT]"
  [ -z "${FAKE_APP_PAGE:-}" ] || echo "  --page {paper,package}"
  exit 0
fi
echo "started: $*" >"$(dirname "$0")/../launched"
APP
    chmod 755 "$UV_TOOL_BIN_DIR/metacheck-app"
    ;;
esac
"""


def _steward_env(tmp_path, **extra):
    root, env = _fake_install_env(tmp_path, **extra)
    (root / "uv" / "uv").write_text(FAKE_UV_APP)
    env.pop("METACHECK_NO_LAUNCH")  # these tests launch the app
    return root, env


def _install(env, *args):
    return subprocess.run(
        ["sh", str(SH), *args], env=env, capture_output=True, text=True, timeout=60
    )


@needs_sh
def test_a_plain_install_opens_the_app_without_arguments(tmp_path):
    root, env = _steward_env(tmp_path, FAKE_APP_PAGE="1")
    result = _install(env)
    assert result.returncode == 0, result.stderr
    assert (root / "launched").read_text().strip() == "started:"
    assert "--page" not in result.stdout


@needs_sh
@pytest.mark.parametrize("how", ["flag", "variable"])
def test_steward_opens_the_data_package_page(tmp_path, how):
    root, env = _steward_env(tmp_path, FAKE_APP_PAGE="1")
    if how == "variable":
        result = _install({**env, "METACHECK_STEWARD": "1"})
    else:
        result = _install(env, "--steward")
    assert result.returncode == 0, result.stderr
    assert (root / "launched").read_text().strip() == "started: --page package"
    assert f"or run: {root}/bin/metacheck-app --page package" in result.stdout
    # no step for a PDF reader or a key: the installer has none
    assert "bibr" not in result.stdout.lower() and "grobid" not in result.stdout.lower()


@needs_sh
def test_steward_with_an_app_that_has_no_such_page_still_opens_the_app(tmp_path):
    # the pinned commit can be older than the page: --page would be an unknown option there
    root, env = _steward_env(tmp_path)
    result = _install(env, "--steward")
    assert result.returncode == 0, result.stderr
    assert (root / "launched").read_text().strip() == "started:"
    assert "no data package page yet" in result.stdout
    assert "--page" not in result.stdout


@needs_sh
def test_steward_and_no_launch_install_only(tmp_path):
    root, env = _steward_env(tmp_path, FAKE_APP_PAGE="1")
    result = _install({**env, "METACHECK_NO_LAUNCH": "1"}, "--steward")
    assert result.returncode == 0, result.stderr
    assert (root / "bin" / "metacheck-app").exists()
    assert not (root / "launched").exists()


@needs_sh
def test_steward_does_not_change_what_is_installed(tmp_path):
    root, env = _steward_env(tmp_path, FAKE_APP_PAGE="1")
    _install({**env, "METACHECK_NO_LAUNCH": "1"})
    plain = (root / "calls").read_text()
    (root / "calls").unlink()
    _install({**env, "METACHECK_NO_LAUNCH": "1"}, "--steward")
    assert (root / "calls").read_text() == plain


@needs_sh
def test_help_and_unknown_options_name_steward(tmp_path):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    result = subprocess.run(["sh", str(SH), "--help"], env=env, capture_output=True, text=True)
    assert result.returncode == 0 and "--steward" in result.stdout
    bad = subprocess.run(["sh", str(SH), "--bogus"], env=env, capture_output=True, text=True)
    assert "--steward" in bad.stderr


def test_both_scripts_have_the_steward_option():
    sh, ps1 = SH.read_text(), PS1.read_text()
    assert "--steward) steward=1" in sh and "METACHECK_STEWARD" in sh
    assert "'^-Steward$' { $steward = $true }" in ps1 and "METACHECK_STEWARD" in ps1
    # both open the app on the page the same way, and only when the app knows the option
    assert 'exec "$app" --page "$page"' in sh and "grep -q -e '--page'" in sh
    assert "& $app --page package" in ps1 and ".Contains('--page')" in ps1
