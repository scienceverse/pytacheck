"""Local RegCheck server helpers (port of ``R/regcheck-local.R``).

metacheck bundles a minimal RegCheck server (a FastAPI app using Ollama for
local inference) in ``inst/regcheck/``; these functions start it via Docker
(recommended, no Python setup needed) or in a dedicated Python virtual
environment. pytacheck ships the same app as ``regcheck_app.tar.gz`` (built
from metacheck's ``inst/regcheck`` with :func:`_build_app_archive`) and
unpacks it into the user data directory on first use, so the virtual
environment and uploads never live inside the installed package.

Regenerate the archive after an upstream change with::

    python -c "from pytacheck.db.regcheck_local import _build_app_archive; \\
        _build_app_archive('upstream/metacheck/inst/regcheck')"
"""

from __future__ import annotations

import atexit
import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any

from pytacheck.db._utils import message, user_data_dir

__all__ = ["regcheck_setup_local", "regcheck_start_local", "regcheck_stop_local"]

_ARCHIVE = "regcheck_app.tar.gz"
_DEFAULT_TOKEN = "metacheck-local"  # noqa: S105 - public token of the local server
_state: dict[str, Any] = {}


def _archive_bytes() -> bytes:
    from importlib import resources

    return resources.files("pytacheck.db").joinpath(_ARCHIVE).read_bytes()


def _regcheck_app_dir() -> Path:
    """The RegCheck app directory (port of ``.regcheck_app_dir()``).

    ``REGCHECK_APP_DIR`` points at an existing checkout; otherwise the
    bundled app is unpacked (once per app version) into
    ``<user data dir>/regcheck/app-<hash>``.
    """
    override = os.environ.get("REGCHECK_APP_DIR")
    if override:
        return Path(override)
    raw = _archive_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    target = user_data_dir() / "regcheck" / f"app-{digest}"
    if not (target / "backend" / "main.py").exists():
        tmp = target.with_name(target.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            try:
                tar.extractall(tmp, filter="data")
            except TypeError:  # Python < 3.11.4: no extraction filters (our own archive)
                tar.extractall(tmp)  # noqa: S202
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
    return target


def _regcheck_venv_dir() -> Path:
    """The app's Python virtual environment (port of ``.regcheck_venv_dir()``)."""
    return _regcheck_app_dir() / ".venv"


def _build_app_archive(
    src: str | os.PathLike[str], dest: str | os.PathLike[str] | None = None
) -> Path:
    """Pack metacheck's ``inst/regcheck`` into the bundled, reproducible archive."""
    src_path = Path(src)
    dest_path = Path(dest) if dest else Path(__file__).with_name(_ARCHIVE)
    files = sorted(
        p
        for p in src_path.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and ".venv" not in p.parts
    )
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for p in files:
            info = tarfile.TarInfo(p.relative_to(src_path).as_posix())
            data = p.read_bytes()
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    import gzip

    gz = bytearray(gzip.compress(buf.getvalue(), compresslevel=9, mtime=0))
    gz[9] = 3  # header OS byte: Unix, whatever the platform or Python version writes
    dest_path.write_bytes(bytes(gz))
    return dest_path


def _venv_bin(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / (f"{name}.exe")
    return venv / "bin" / name


def regcheck_setup_local(python: str | None = None) -> Path:
    """Set up the local RegCheck server's Python environment (port of ``regcheck_setup_local()``).

    Creates a virtual environment for the bundled RegCheck app, installs its
    dependencies and downloads the NLTK sentence tokeniser data. Only needed
    for ``regcheck_start_local(method="python")``; with Docker use
    :func:`regcheck_start_local` directly.

    Requires Python 3.12+ and Ollama (https://ollama.com/download) with
    ``ollama pull llama3.2`` and ``ollama pull nomic-embed-text-v2-moe``.
    Returns the path to the virtual environment.
    """
    app_dir = _regcheck_app_dir()
    venv_dir = _regcheck_venv_dir()

    python = python or shutil.which("python3") or shutil.which("python")
    if not python:
        raise RuntimeError(
            "Python 3.12+ not found. Install it from https://www.python.org/downloads/ "
            "and ensure it is on your PATH."
        )
    try:
        ver = subprocess.run(  # noqa: S603 - trusted arguments
            [python, "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        ver = ""
    message(f"Using Python: {python} ({ver})")

    if not venv_dir.exists():
        message("Creating Python virtual environment...")
        venv_cmd = [python, "-m", "venv", str(venv_dir)]
        if subprocess.run(venv_cmd, check=False).returncode != 0:  # noqa: S603
            raise RuntimeError("Failed to create virtual environment.")
    else:
        message("Virtual environment already exists, skipping creation.")

    pip = _venv_bin(venv_dir, "pip")
    message("Installing Python dependencies (this may take a few minutes)...")
    req_file = app_dir / "requirements.txt"
    if (
        subprocess.run(  # noqa: S603 - trusted arguments
            [str(pip), "install", "-r", str(req_file), "--quiet"], check=False
        ).returncode
        != 0
    ):
        raise RuntimeError("pip install failed.")

    message("Downloading NLTK sentence tokeniser data...")
    subprocess.run(  # noqa: S603 - trusted arguments
        [
            str(_venv_bin(venv_dir, "python")),
            "-c",
            "import nltk; nltk.download('punkt'); nltk.download('punkt_tab')",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    message("\nSetup complete. Start the server with regcheck_start_local().")
    return venv_dir


def _ollama_models(base_url: str) -> list[dict[str, Any]]:
    """Models pulled in Ollama with their size and capabilities (``ellmer::models_ollama()``)."""
    from pytacheck import http
    from pytacheck.db._utils import resp_body_json

    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    resp = http.request("GET", f"{base}/api/tags", max_tries=1)
    if resp is None or resp.status_code >= 400:
        raise ConnectionError("Ollama is not running")
    models = []
    for m in resp_body_json(resp).get("models", []):
        name = m.get("name") or m.get("model")
        show = http.request("POST", f"{base}/api/show", json={"model": name}, max_tries=1)
        caps: list[str] = []
        if show is not None and show.status_code < 400:
            caps = list(resp_body_json(show).get("capabilities") or [])
        models.append({"id": name, "size": m.get("size") or 0, "capabilities": caps})
    return models


def _select_model() -> str:
    ollama = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    try:
        models = _ollama_models(ollama)
    except Exception:
        models = []
    if not models:
        raise RuntimeError(
            "Ollama is not running or has no models pulled.\n"
            "Start Ollama and pull a language model, e.g.:\n"
            "  ollama pull llama3.2"
        )
    lm = [m for m in models if "completion" in m["capabilities"]]
    if not lm:
        raise RuntimeError(
            "No language models found in Ollama (only embedding models are installed).\n"
            "Pull a language model, e.g.:\n"
            "  ollama pull llama3.2"
        )
    model = str(max(lm, key=lambda m: m["size"])["id"])
    message(f"Auto-selected Ollama model: {model}")
    return model


def _server_up(url: str) -> bool:
    from pytacheck import http

    try:
        http.client().get(url, timeout=2)  # any HTTP response means it is up
    except Exception:
        return False
    return True


def regcheck_start_local(
    method: str | list[str] = ("docker", "python"),  # type: ignore[assignment]
    model: str | None = None,
    port: int = 8000,
) -> subprocess.Popen[bytes]:
    """Start the local RegCheck server (port of ``regcheck_start_local()``).

    Runs the bundled RegCheck app in the background, via Docker
    (``method="docker"``, recommended; the first run builds the image) or
    the virtual environment made by :func:`regcheck_setup_local`
    (``method="python"``), at ``http://localhost:<port>``. Ollama must be
    running with ``nomic-embed-text-v2-moe`` and a language model pulled;
    *model* defaults to the largest pulled language model. Sets
    ``REGCHECK_API_TOKEN`` to the local server's built-in token, so
    :func:`~pytacheck.db.regcheck.regcheck_compare` needs no configuration.
    Returns the server process; stop it with :func:`regcheck_stop_local`.
    """
    from pytacheck.utils import match_arg

    choices = ("docker", "python")
    if not isinstance(method, str) and list(method) != list(choices) and len(list(method)) != 1:
        raise ValueError("'arg' must be of length 1")
    if not isinstance(method, str) and len(list(method)) == 1:
        method = next(iter(method))
    method = match_arg(method, choices)  # partial matching, like match.arg()

    app_dir = _regcheck_app_dir()
    os.environ["REGCHECK_API_TOKEN"] = _DEFAULT_TOKEN
    if model is None:
        model = _select_model()

    env = {**os.environ, "OLLAMA_MODEL": model, "REGCHECK_API_TOKEN": _DEFAULT_TOKEN}
    if method == "docker":
        docker = shutil.which("docker")
        if not docker:
            raise RuntimeError(
                "Docker not found. Install it from https://www.docker.com/get-started/"
            )
        message(
            f"Starting local RegCheck server via Docker at http://localhost:{port} (model: {model}) ..."
        )
        message("(First run will build the image -- this takes a few minutes.)")
        cmd = [docker, "compose", "up", "--build", "--force-recreate"]
        if int(port) != 8000:
            # the bundled compose file publishes port 8000; metacheck ignored
            # `port` with Docker (U14), here an override file publishes it
            override = app_dir / "docker-compose.port.yml"
            override.write_text(
                f'services:\n  regcheck:\n    ports: !override\n      - "{int(port)}:8000"\n',
                encoding="utf-8",
            )
            cmd[2:2] = ["-f", "docker-compose.yml", "-f", override.name]
    else:
        venv_dir = _regcheck_venv_dir()
        if not venv_dir.exists():
            raise RuntimeError("Virtual environment not found. Run regcheck_setup_local() first.")
        uvicorn = _venv_bin(venv_dir, "uvicorn")
        message(
            f"Starting local RegCheck server (Python) at http://localhost:{port} (model: {model}) ..."
        )
        cmd = [
            str(uvicorn),
            "backend.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ]

    log_path = app_dir / "regcheck-server.log"
    log = log_path.open("wb")
    proc = subprocess.Popen(cmd, cwd=app_dir, env=env, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603
    log.close()
    atexit.register(_terminate, proc)

    message("Waiting for RegCheck server to become ready")
    server_url = f"http://localhost:{port}"
    deadline = time.monotonic() + 600
    ready = False
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = log_path.read_text(encoding="utf-8", errors="replace")[-5000:]
            raise RuntimeError(f"RegCheck server failed to start.\n{output}")
        if _server_up(server_url):
            ready = True
            break
        time.sleep(3)
    if not ready:
        _terminate(proc)
        raise RuntimeError("RegCheck server did not become ready within 10 minutes.")

    message(f"RegCheck server running (PID {proc.pid}). Stop it with regcheck_stop_local().")
    _state["proc"] = proc
    return proc


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def regcheck_stop_local() -> None:
    """Stop the local RegCheck server started by :func:`regcheck_start_local`."""
    proc = _state.get("proc")
    if proc is None or proc.poll() is not None:
        message("No local RegCheck server appears to be running.")
        return None
    _terminate(proc)
    _state.pop("proc", None)
    message("Local RegCheck server stopped.")
    return None
