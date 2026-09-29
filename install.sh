#!/bin/sh
# Installs the metacheck preview app into one folder and opens it.
#
#   curl -LsSf https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh
#   sh install.sh [--no-launch] [--uninstall]
#   curl -LsSf https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh -s -- --uninstall
#
# Everything goes into METACHECK_HOME (default: ~/.local/share/metacheck):
# uv, its Python, the app and uv's cache. No shell profile and no PATH is
# edited. Other settings:
#   METACHECK_NO_LAUNCH=1     same as --no-launch
#   METACHECK_UNINSTALL=1     same as --uninstall
#   METACHECK_REF=<commit>    install this commit instead of the pinned one
#   METACHECK_SPEC=<spec>     install this requirement instead of the GitHub archive
#   METACHECK_CONSTRAINTS=<file>  use this constraints file instead of the pinned one
#
# The whole script is one function called on the last line, so a download that
# stops half way runs nothing.

main() {
  set -eu

  # The commit that gets installed. Move it when the app moves.
  REF="53d802517291a2be8b0a569da32b27ac3ccd1d99"

  UV_VERSION="0.12.20"
  UV_URL="https://github.com/astral-sh/uv/releases/download/$UV_VERSION"
  ISSUES_URL="https://github.com/scienceverse/pytacheck/issues"
  TOOL="pytacheck"

  say() { printf '%s\n' "$*"; }
  die() {
    printf 'metacheck installer: %s\n' "$*" >&2
    exit 1
  }

  launch=1
  uninstall=0
  [ "${METACHECK_NO_LAUNCH:-}" = "1" ] && launch=0
  [ "${METACHECK_UNINSTALL:-}" = "1" ] && uninstall=1
  for arg in "$@"; do
    case "$arg" in
      --no-launch) launch=0 ;;
      --uninstall) uninstall=1 ;;
      -h | --help)
        say "Usage: sh install.sh [--no-launch] [--uninstall]"
        return 0
        ;;
      *) die "unknown option: $arg (use --no-launch or --uninstall)" ;;
    esac
  done

  # Set but empty is an error (the guard below refuses it); unset means default.
  if [ -z "${METACHECK_HOME+x}" ]; then
    [ -n "${HOME:-}" ] || die "HOME is not set. Set METACHECK_HOME to the folder to use."
    METACHECK_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/metacheck"
  fi
  # Gives one folder one spelling: repeated and trailing slashes go.
  tidy() {
    t="$(printf '%s\n' "$1" | sed 's|//*|/|g')"
    while [ "${#t}" -gt 1 ] && [ "${t%/}" != "$t" ]; do
      t="${t%/}"
    done
    printf '%s\n' "$t"
  }
  root="$(tidy "$METACHECK_HOME")"

  # Refuses any folder that is not clearly ours before anything is removed.
  check_root() {
    case "$root" in
      "") die "METACHECK_HOME is empty; refusing to continue." ;;
      /) die "METACHECK_HOME is /; refusing to continue." ;;
      /*) ;;
      *) die "METACHECK_HOME must be an absolute path: $root" ;;
    esac
    case "/$root/" in
      */./* | */../*) die "METACHECK_HOME must not contain . or .. parts: $root" ;;
    esac
    if [ -n "${HOME:-}" ]; then
      [ "$root" != "$(tidy "$HOME")" ] || die "METACHECK_HOME is your home folder; refusing to continue."
      # The same folder under another name (a link, for instance).
      home_real="$(cd "$HOME" 2>/dev/null && pwd -P || true)"
      root_real="$(cd "$root" 2>/dev/null && pwd -P || true)"
      [ -z "$root_real" ] || [ "$root_real" != "$home_real" ] ||
        die "METACHECK_HOME is your home folder; refusing to continue."
    fi
    case "$root" in
      */metacheck) ;;
      *) die "METACHECK_HOME must end in a folder named metacheck: $root" ;;
    esac
    [ ! -L "$root" ] || die "METACHECK_HOME is a symbolic link; refusing to continue: $root"
  }
  check_root

  uv_dir="$root/uv"
  uv_bin="$uv_dir/uv"
  bin_dir="$root/bin"
  app="$bin_dir/metacheck-app"
  # Written at install time. Uninstall removes nothing from a folder without it.
  marker="$root/.metacheck-installer"

  # Every uv setting that could reach outside the folder is pinned here, and
  # the ones that change how Python or the packages are chosen are dropped.
  # The bin folder goes on PATH for uv only, so uv does not warn that it is missing.
  run_uv() {
    (
      unset UV_PYTHON UV_PYTHON_PREFERENCE UV_MANAGED_PYTHON UV_NO_MANAGED_PYTHON \
        UV_OFFLINE UV_CONSTRAINT UV_BUILD_CONSTRAINT UV_OVERRIDE UV_EXCLUDE_NEWER \
        UV_PRERELEASE UV_RESOLUTION UV_NO_BUILD UV_NO_BINARY UV_FROZEN UV_LOCKED
      exec env \
        PATH="$bin_dir:$PATH" \
        UV_CACHE_DIR="$root/cache" \
        UV_PYTHON_INSTALL_DIR="$root/python" \
        UV_PYTHON_DOWNLOADS=automatic \
        UV_TOOL_DIR="$root/tools" \
        UV_TOOL_BIN_DIR="$bin_dir" \
        UV_PYTHON_INSTALL_BIN=0 \
        UV_PYTHON_INSTALL_REGISTRY=0 \
        UV_NO_MODIFY_PATH=1 \
        UV_NO_CONFIG=1 \
        "$uv_bin" "$@"
    )
  }

  if [ "$uninstall" = "1" ]; then
    if [ ! -d "$root" ]; then
      say "Nothing to remove: $root does not exist."
    elif [ ! -f "$marker" ]; then
      die "$root was not made by this installer (it has no .metacheck-installer file). Nothing was removed."
    else
      if [ -x "$uv_bin" ]; then
        run_uv tool uninstall "$TOOL" >/dev/null 2>&1 || true
      fi
      for sub in uv python tools bin cache; do
        rm -rf "${root:?}/$sub"
      done
      rm -f "$marker"
      if rmdir "$root" 2>/dev/null; then
        say "Removed metacheck from $root."
      else
        say "Left $root in place: it holds files the installer did not create. Everything else is removed."
      fi
    fi
    state=""
    case "$(uname -s)" in
      Darwin) settings="$HOME/Library/Application Support/pytacheck" ;;
      *)
        settings="${XDG_CONFIG_HOME:-$HOME/.config}/pytacheck"
        state="${XDG_STATE_HOME:-$HOME/.local/state}/pytacheck"
        ;;
    esac
    say "The app's own settings are kept in $settings."
    [ -z "$state" ] || say "Its state file is kept in $state."
    say "Delete them too if you want them gone."
    return 0
  fi

  # Platform.
  os="$(uname -s)"
  cpu="$(uname -m)"
  case "$os" in
    Darwin)
      if [ "$cpu" = "x86_64" ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)" = "1" ]; then
        cpu="arm64"
      fi
      case "$cpu" in
        arm64 | aarch64) target="aarch64-apple-darwin" ;;
        x86_64) target="x86_64-apple-darwin" ;;
        *) die "unsupported CPU: $cpu on macOS. Please tell us at $ISSUES_URL" ;;
      esac
      ;;
    Linux)
      case "$cpu" in
        x86_64 | amd64) arch="x86_64" ;;
        aarch64 | arm64) arch="aarch64" ;;
        *) die "unsupported CPU: $cpu on Linux. Please tell us at $ISSUES_URL" ;;
      esac
      libc="gnu"
      # Each place on its own: ls fails when either glob matches nothing.
      if ls /lib/ld-musl-* >/dev/null 2>&1 || ls /usr/lib/ld-musl-* >/dev/null 2>&1; then
        libc="musl"
      fi
      target="$arch-unknown-linux-$libc"
      ;;
    *) die "unsupported system: $os. This installer supports macOS and Linux; on Windows use install.ps1." ;;
  esac
  # SHA-256 of each uv archive, from the .sha256 files of the uv release.
  case "$target" in
    aarch64-apple-darwin) expected="848fdeb602ff1a1baacd4f6c8b7bdc6cf1ad026a6d9cf59475fda17c179743ca" ;;
    x86_64-apple-darwin) expected="ac54283d211fd77cdc152b67606dbaf6406ff4ab03f3af4ae99468fa8e887141" ;;
    x86_64-unknown-linux-gnu) expected="6590717592ace991ff83a63fef799e3ad9d33ecc8f96c5d6bdd732496e79337f" ;;
    aarch64-unknown-linux-gnu) expected="8a7aad7bc76a2fae5151566ff3e43eacce0b2a113d5e4de3e4afe3e58fa2441e" ;;
    x86_64-unknown-linux-musl) expected="14114d66a094f1907af0fcbc863f34226bbb7f5e430e13dce6676ba40dcc6891" ;;
    aarch64-unknown-linux-musl) expected="94bb13feeebc6b59a4124016c957cdc9ee406d1476ce2b2ce2991e95b8e820b7" ;;
  esac

  if command -v curl >/dev/null 2>&1; then
    fetch() { curl -LsSf -o "$2" "$1"; }
  elif command -v wget >/dev/null 2>&1; then
    fetch() { wget -q -O "$2" "$1"; }
  else
    die "neither curl nor wget was found. Please install one of them and run this again."
  fi

  sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
      sha256sum "$1" | cut -d ' ' -f 1
    elif command -v shasum >/dev/null 2>&1; then
      shasum -a 256 "$1" | cut -d ' ' -f 1
    else
      die "neither sha256sum nor shasum was found, so the download cannot be checked."
    fi
  }

  tmp="$(mktemp -d "${TMPDIR:-/tmp}/metacheck.XXXXXX")"
  trap 'rm -rf "$tmp"' EXIT
  trap 'rm -rf "$tmp"; exit 130' INT
  trap 'rm -rf "$tmp"; exit 143' TERM

  mkdir -p "$root" "$uv_dir" "$bin_dir"
  : >"$marker"

  # uv: download and check it, unless the right version is already here.
  have_uv=0
  if [ -x "$uv_bin" ]; then
    case "$("$uv_bin" --version 2>/dev/null || true)" in
      "uv $UV_VERSION" | "uv $UV_VERSION "*) have_uv=1 ;;
    esac
  fi
  if [ "$have_uv" = "0" ]; then
    archive="uv-$target.tar.gz"
    say "Downloading uv $UV_VERSION ..."
    fetch "$UV_URL/$archive" "$tmp/$archive" ||
      die "could not download $UV_URL/$archive. Check your internet connection and any proxy settings."
    actual="$(sha256_of "$tmp/$archive")"
    if [ "$actual" != "$expected" ]; then
      die "checksum mismatch for $archive (expected $expected, got $actual). Nothing was installed."
    fi
    tar -xzf "$tmp/$archive" -C "$tmp" || die "could not unpack $archive."
    cp "$tmp/uv-$target/uv" "$uv_bin.new"
    chmod 755 "$uv_bin.new"
    mv -f "$uv_bin.new" "$uv_bin"
  fi

  # The app.
  ref="${METACHECK_REF:-$REF}"
  spec="${METACHECK_SPEC:-${TOOL}[app] @ https://github.com/scienceverse/pytacheck/archive/$ref.tar.gz}"
  constraints="${METACHECK_CONSTRAINTS:-}"
  if [ -z "$constraints" ]; then
    constraints="$tmp/constraints-app.txt"
    fetch "https://raw.githubusercontent.com/scienceverse/pytacheck/$ref/install/constraints-app.txt" "$constraints" ||
      die "could not download the constraints file for $ref. Check your internet connection."
  elif [ ! -f "$constraints" ]; then
    die "METACHECK_CONSTRAINTS is not a file: $constraints"
  fi

  say "Installing metacheck (Python and the app, about 1 to 2 minutes the first time) ..."
  if ! run_uv tool install --managed-python --python 3.12 "$spec" -c "$constraints"; then
    die "the install failed. If it keeps failing, please tell us at $ISSUES_URL"
  fi
  [ -x "$app" ] || die "the install finished but $app is missing. Please tell us at $ISSUES_URL"

  rm -rf "$tmp"
  trap - EXIT INT TERM

  say ""
  say "metacheck is installed in $root"
  say "To open it again, run the same command again (it takes a few seconds),"
  say "or run: $app"
  say "To remove it, run the same command with --uninstall:"
  say "  curl -LsSf https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh -s -- --uninstall"
  say ""

  if [ "$launch" = "1" ]; then
    exec "$app"
  fi
}

main "$@"
