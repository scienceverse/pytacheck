"""The R engine: R metacheck's own checks on a paper, shown with pytacheck's report.

The checks run in R metacheck, in an ``Rscript`` subprocess per run (``run.R`` next to this
file). Its module outputs come back as canonical JSON (``canonical.R``, the parity tooling's
encoder) and become :class:`~metacheck.module.ModuleOutput` objects, so pytacheck's report
renderer shows them as it shows its own. Reading the paper stays shared: R reads the same
bibr JSON or GROBID XML file that pytacheck read.

Nothing here needs a Python package beyond pytacheck's own; ``pip install "metacheck[r]"``
only names the choice. What it needs is R with the metacheck R package installed:

* ``METACHECK_R_ENGINE_RSCRIPT``: the ``Rscript`` to use (else ``METACHECK_RSCRIPT``, else
  the first ``Rscript`` on ``PATH``);
* ``pytacheck r-engine`` says whether it works, and which versions it found.

A run is a separate process: R's start and ``library(metacheck)`` cost a few seconds, an R
error or crash cannot take the caller down, and a run is ended cleanly at its time limit,
when ``stop()`` says so, or when the process that started it is terminated.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import orjson

from metacheck._env import env_get

__all__ = [
    "ENGINES",
    "REngineError",
    "REngineInfo",
    "REngineStopped",
    "REngineTimeout",
    "REngineUnavailable",
    "RRun",
    "available",
    "info",
    "paper_file",
    "report_version",
    "rscript",
    "run",
]

#: the engines a run can use; "python" is pytacheck's own modules
ENGINES = ("python", "r")
#: seconds a version check may take (R and library(metacheck) start in a few)
INFO_TIMEOUT = 60.0
#: the last characters of R's error output kept in an error message
_STDERR_TAIL = 2000
_HERE = Path(__file__).resolve().parent
RUNNER = _HERE / "run.R"


class REngineError(RuntimeError):
    """The R engine could not run the checks (the message says why)."""


class REngineUnavailable(REngineError):
    """No R, or no metacheck R package, to run the checks with."""


class REngineTimeout(REngineError):
    """The R run took longer than its time limit and was ended."""


class REngineStopped(REngineError):
    """The R run was stopped (``stop()`` said so)."""


@dataclass(frozen=True)
class REngineInfo:
    """What the R engine runs with."""

    rscript: str
    r_version: str
    metacheck_version: str
    #: the modules this R metacheck has (its ``inst/modules``)
    modules: tuple[str, ...]


@dataclass
class RRun:
    """The module outputs of one R run, by module, in the order R gave them."""

    outputs: Any  # metacheck.report.report.ReportOutput
    metacheck_version: str
    r_version: str
    seconds: float


def rscript() -> str | None:
    """The ``Rscript`` of the R engine, or ``None`` when there is none."""
    import shutil

    return env_get("R_ENGINE_RSCRIPT") or env_get("RSCRIPT") or shutil.which("Rscript")


#: what R answered, by ``Rscript``: its versions, or why it cannot run metacheck
_INFO: dict[str, REngineInfo | REngineUnavailable] = {}
_INFO_LOCK = threading.Lock()


def info(exe: str | None = None, *, refresh: bool = False) -> REngineInfo:
    """The versions and modules of the R engine (asked once per ``Rscript``, then kept, as is
    an answer that it cannot run: ``refresh`` asks again).

    Raises :class:`REngineUnavailable` when there is no ``Rscript`` or R cannot load
    metacheck.
    """
    exe = exe or rscript()
    if not exe:
        raise REngineUnavailable(
            "R was not found: install R and the metacheck R package, or set "
            "METACHECK_R_ENGINE_RSCRIPT to its Rscript"
        )
    with _INFO_LOCK:
        known = None if refresh else _INFO.get(exe)
    if isinstance(known, REngineUnavailable):
        raise known
    if known is not None:
        return known
    try:
        answer = _call({"info": True}, exe=exe, timeout=INFO_TIMEOUT)
    except REngineError as exc:
        failed = exc if isinstance(exc, REngineUnavailable) else None
        if failed is None:
            failed = REngineUnavailable(f"R cannot run metacheck: {exc}")
            failed.__cause__ = exc
        with _INFO_LOCK:
            _INFO[exe] = failed
        raise failed  # noqa: B904 - the cause is set above (or it is this error)
    found = REngineInfo(
        rscript=exe,
        r_version=str(answer.get("r_version") or ""),
        metacheck_version=str(answer.get("metacheck_version") or ""),
        modules=tuple(_strings(answer.get("modules"))),
    )
    with _INFO_LOCK:
        _INFO[exe] = found
    return found


def available(exe: str | None = None) -> bool:
    """Whether the R engine can run (R found, and it loads metacheck)."""
    try:
        info(exe)
    except REngineError:
        return False
    return True


def report_version(metacheck_version: str, r_version: str) -> str:
    """How a report made from an R run names the versions behind it."""
    from metacheck._version import __version__

    return f"{metacheck_version} (R metacheck on R {r_version}; report by pytacheck {__version__})"


def paper_file(paper: Any, folder: str | os.PathLike[str]) -> Path:
    """*paper* written as a JSON file in *folder*, for R to read: a bibr 12.0 export for a
    paper read from one, else the paper as metacheck's ``paper_write()`` saves it.

    Only needed when the paper did not come from a file R reads (a bibr JSON or a GROBID
    XML file): R reads those files themselves.
    """
    from metacheck.papers.io import paper_write

    try:
        return Path(str(paper_write(paper, "paper", folder)))
    except ValueError as exc:
        raise REngineError(f"This paper cannot be handed to R: {exc}") from exc


def run(
    paper: str | os.PathLike[str],
    modules: Sequence[str],
    *,
    args: Mapping[str, Mapping[str, Any]] | None = None,
    chain: bool = True,
    timeout: float | None = None,
    stop: Callable[[], bool] | None = None,
    exe: str | None = None,
) -> RRun:
    """Run R metacheck's *modules* on the paper file *paper* (bibr JSON or GROBID XML).

    ``chain`` runs them as R's ``report_module_run()`` does (in order, each seeing the earlier
    outputs, sorted by section, as a report); without it each module runs on its own, as R's
    plumber ``POST /check`` runs them. Either way a module that fails is a ``"fail"``
    output. ``args`` are the modules' arguments by module name (JSON values).

    Raises :class:`REngineError` when R cannot read the paper or the run fails as a whole,
    :class:`REngineTimeout` after ``timeout`` seconds, and :class:`REngineStopped` as soon as
    ``stop()`` is true.
    """
    from metacheck.rengine._decode import decode, module_output
    from metacheck.report.report import ReportOutput

    exe = exe or rscript()
    if not exe:
        info(exe)  # raises the "R was not found" error
    start = time.perf_counter()
    request = {
        "paper": str(Path(paper).resolve()),
        "modules": list(modules),
        "args": {k: dict(v) for k, v in (args or {}).items() if k in modules},
        "chain": chain,
    }
    raw = _call(request, exe=str(exe), timeout=timeout, stop=stop, decoded=False)
    parts = _parts(raw)
    tables = [decode(t) for t in parts.get("tables", {}).get("v", [])]
    outs = parts.get("outputs") or {"names": [], "v": []}
    outputs = ReportOutput(
        (str(name), module_output(value, tables))
        for name, value in zip(outs.get("names") or [], outs.get("v") or [], strict=True)
    )
    return RRun(
        outputs=outputs,
        metacheck_version=str(decode(parts.get("metacheck_version")) or ""),
        r_version=str(decode(parts.get("r_version")) or ""),
        seconds=time.perf_counter() - start,
    )


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _parts(raw: dict[str, Any]) -> dict[str, Any]:
    """The elements of the result list, still encoded."""
    return dict(zip(raw.get("names") or [], raw.get("v") or [], strict=True))


def _env() -> dict[str, str]:
    """The environment of an R run: this one, in a UTF-8 locale (papers are UTF-8)."""
    env = dict(os.environ)
    if sys.platform != "win32":
        locale = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
        if "utf-8" not in locale.lower() and "utf8" not in locale.lower():
            env["LC_ALL"] = env["LANG"] = "C.UTF-8"
    return env


def _call(
    request: dict[str, Any],
    *,
    exe: str,
    timeout: float | None = None,
    stop: Callable[[], bool] | None = None,
    decoded: bool = True,
) -> dict[str, Any]:
    """Run ``run.R`` on *request*; its result list (decoded, or as canonical JSON)."""
    from metacheck.rengine._decode import decode

    with tempfile.TemporaryDirectory(prefix="pytacheck-r-") as tmp:
        folder = Path(tmp)
        ask, answer, errors = folder / "request.json", folder / "result.json", folder / "err.txt"
        ask.write_bytes(orjson.dumps(request))
        command = [exe, "--no-save", "--no-restore", str(RUNNER), str(ask), str(answer)]
        with open(errors, "wb") as err:
            try:
                proc = subprocess.Popen(  # noqa: S603 - the configured Rscript, our own script
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=err,
                    cwd=tmp,
                    env=_env(),
                    **_own_group(),
                )
            except OSError as exc:
                raise REngineUnavailable(f"R could not be started ({exe}): {exc}") from exc
            with _ended_with_parent(proc):
                _wait(proc, timeout, stop)
        if not answer.is_file():
            tail = errors.read_text(encoding="utf-8", errors="replace")[-_STDERR_TAIL:].strip()
            raise REngineError(
                f"R ended without a result (exit status {proc.returncode})"
                + (f": {tail}" if tail else "")
            )
        raw: dict[str, Any] = orjson.loads(answer.read_bytes())
    parts = _parts(raw)
    if "error" in parts:
        raise REngineError(str(decode(parts["error"])))
    if not decoded:
        return raw
    return {k: decode(v) for k, v in parts.items()}


def _own_group() -> dict[str, Any]:
    """Start R in a process group of its own, so that ending it ends what it started."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _end(proc: subprocess.Popen[bytes]) -> None:
    """End R and every process it started."""
    if proc.poll() is not None:
        return
    if sys.platform != "win32":
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(5)


def _wait(
    proc: subprocess.Popen[bytes], timeout: float | None, stop: Callable[[], bool] | None
) -> None:
    deadline = None if timeout is None else time.monotonic() + timeout
    try:
        while True:
            try:
                proc.wait(0.25)
                return
            except subprocess.TimeoutExpired:
                pass
            if stop is not None and stop():
                raise REngineStopped("The R run was stopped")
            if deadline is not None and time.monotonic() >= deadline:
                raise REngineTimeout(f"The R run took longer than {timeout:g} seconds")
    finally:
        _end(proc)


@contextlib.contextmanager
def _ended_with_parent(proc: subprocess.Popen[bytes]) -> Iterator[None]:
    """While R runs, a SIGTERM to this process (a hosted run ended at its time limit, or left
    by the person) ends R first: R runs in its own session, so it would outlive this one."""
    if sys.platform == "win32" or threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.getsignal(signal.SIGTERM)

    def on_term(signum: int, frame: Any) -> None:
        _end(proc)
        if callable(previous):
            previous(signum, frame)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, on_term)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
