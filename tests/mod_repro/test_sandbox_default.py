"""reproducibility_check runs the paper's code in Docker unless told otherwise (D62).

metacheck's default is ``sandbox = "process"``: the code runs on the user's machine with no
filesystem or network isolation. pytacheck's is ``"docker"``, and ``"process"`` is an explicit,
loud opt-in. These tests do not depend on the parity cases (which pass ``sandbox="process"`` on the
Python side so both sides run the same thing): they mock both backends and check which one a call
reaches, and one test runs a real container.
"""

from __future__ import annotations

import inspect
import os
import re
import stat
import subprocess
import tempfile
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck.core.errors import PytacheckWarning
from metacheck.module import module_find, module_run
from metacheck.modules import _reproducibility as h
from metacheck.modules import reproducibility_check as rc
from metacheck.presets import select
from metacheck.repro import core, docker
from metacheck.statout import r_capture
from tests.mod_repro.test_reproducibility_check import (
    PLAN,
    _run_frame,
    chain,
    code_tbl_row,
    exec_chain,
)

ROOT = Path(__file__).resolve().parents[2]
DOCKER_OK = {"ok": True, "msg": ""}


@pytest.fixture
def paper() -> pc.Paper:
    p = pc.test_paper()
    p.paper_id = "p1"
    return p


class Tripwire:
    """Backends that must not start: each one records its name and raises when called."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        self.mp = monkeypatch

    def _trip(self, name: str) -> Callable[..., Any]:
        def tripped(*args: Any, **kwargs: Any) -> Any:
            self.calls.append(name)
            raise AssertionError(f"{name} was called")

        return tripped

    def process(self) -> None:
        """The host-Rscript backends (script runs, installs, the Rscript runner itself)."""
        self.mp.setattr(core, "repro_run_scripts", self._trip("repro_run_scripts"))
        self.mp.setattr(core, "repro_install_deps", self._trip("repro_install_deps"))
        self.mp.setattr(core, "_callr_run", self._trip("_callr_run"))
        self.mp.setattr(r_capture, "_run_rscript", self._trip("_run_rscript"))

    def docker(self) -> None:
        """The Docker backends and the ``docker`` CLI call under them."""
        self.mp.setattr(docker, "repro_run_scripts_docker", self._trip("repro_run_scripts_docker"))
        self.mp.setattr(
            docker, "repro_install_deps_docker", self._trip("repro_install_deps_docker")
        )
        self.mp.setattr(docker, "_docker", self._trip("_docker"))


@pytest.fixture
def tripwire(monkeypatch: pytest.MonkeyPatch) -> Tripwire:
    return Tripwire(monkeypatch)


def _fake_docker_run(seen: dict[str, Any]) -> Callable[..., pd.DataFrame]:
    def fake(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        seen.update(kw)
        seen["order"] = list(order)
        return _run_frame([{"fn": "ok.R", "outcome": "ran_ok", "stdout": "> 1 + 1\n[1] 2\n"}])

    return fake


# -- the default -------------------------------------------------------------------------


def test_default_sandbox_is_docker() -> None:
    sig = inspect.signature(module_find("reproducibility_check").func)
    assert sig.parameters["sandbox"].default == "docker"
    assert module_find("reproducibility_check").arg_defaults["sandbox"] == "docker"
    assert rc._SANDBOXES == ("docker", "process")  # match.arg(): the first choice is the default
    assert rc._match_sandbox(None) == "docker"
    assert rc._match_sandbox(["docker", "process"]) == "docker"


def test_execute_without_a_sandbox_argument_runs_in_docker_and_starts_no_process(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch, tripwire: Tripwire
) -> None:
    seen: dict[str, Any] = {}
    tripwire.process()
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")  # an R is there, and must stay unused
    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(docker, "repro_run_scripts_docker", _fake_docker_run(seen))
    fake = exec_chain(paper, "ok.R", repro_fixture)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mo = module_run(fake, "reproducibility_check", execute=True)
    assert tripwire.calls == []
    assert seen["image"] == "ghcr.io/scienceverse/metacheck_r:latest"
    assert mo.table["outcome"].tolist() == ["ran_ok"]
    assert not [x for x in w if issubclass(x.category, PytacheckWarning)]  # nothing to warn about


@pytest.mark.parametrize("sandbox", [None, "docker", "d", ["docker", "process"]])
def test_sandbox_none_or_the_default_vector_means_docker(
    paper: pc.Paper,
    repro_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
    tripwire: Tripwire,
    sandbox: Any,
) -> None:
    seen: dict[str, Any] = {}
    tripwire.process()
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(docker, "repro_run_scripts_docker", _fake_docker_run(seen))
    mo = module_run(
        exec_chain(paper, "ok.R", repro_fixture),
        "reproducibility_check",
        execute=True,
        sandbox=sandbox,
    )
    assert tripwire.calls == []
    assert seen["order"] == ["ok.R"]
    assert mo.table["outcome"].tolist() == ["ran_ok"]


def test_a_preset_that_omits_sandbox_gets_docker(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch, tripwire: Tripwire
) -> None:
    # report(args = list(reproducibility_check = list(execute = TRUE))), or a preset's `args`
    entries = select(
        modules=["reproducibility_check"], args={"reproducibility_check": {"execute": True}}
    )
    ((ref, args),) = list(entries)
    assert args == {"execute": True}
    seen: dict[str, Any] = {}
    tripwire.process()
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(docker, "repro_run_scripts_docker", _fake_docker_run(seen))
    module_run(exec_chain(paper, "ok.R", repro_fixture), ref, **args)
    assert tripwire.calls == []
    assert seen["order"] == ["ok.R"]


@pytest.mark.parametrize("old_default", [["process", "docker"], ("process", "docker")])
def test_the_old_default_vector_runs_nothing(
    paper: pc.Paper,
    repro_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
    tripwire: Tripwire,
    old_default: Any,
) -> None:
    tripwire.process()
    tripwire.docker()
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    with pytest.raises(pc.ModuleError, match="'arg' must be of length 1"):
        module_run(
            exec_chain(paper, "ok.R", repro_fixture),
            "reproducibility_check",
            execute=True,
            sandbox=old_default,
        )
    assert tripwire.calls == []


# -- Docker is not there: refuse, never fall back to the host ---------------------------------


def _docker_down(monkeypatch: pytest.MonkeyPatch, msg: str = "no daemon") -> None:
    monkeypatch.setattr(docker, "repro_docker_available", lambda: {"ok": False, "msg": msg})


@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # the default
        {"sandbox": None},
        {"sandbox": "docker"},
        {"sandbox": "d"},
        {"sandbox": ["docker", "process"]},
        {"install_missing": True},
    ],
)
def test_docker_unavailable_refuses_and_nothing_runs(
    paper: pc.Paper,
    repro_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
    tripwire: Tripwire,
    kwargs: dict[str, Any],
) -> None:
    tripwire.process()
    tripwire.docker()
    _docker_down(monkeypatch)
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")  # an R is there: no fallback to it
    # nothing is even prepared: the chain's upstream modules, the layout, the scripts
    monkeypatch.setattr(rc, "_new_sandbox", lambda *a, **k: pytest.fail("a sandbox was made"))
    monkeypatch.setattr(rc, "_check", lambda *a, **k: pytest.fail("the check was started"))
    with pytest.raises(pc.ModuleError) as err:
        module_run(
            exec_chain(paper, "ok.R", repro_fixture),
            "reproducibility_check",
            execute=True,
            **kwargs,
        )
    text = str(err.value)
    assert 'sandbox = "docker": no daemon' in text  # why
    assert "nothing was run" in text
    assert "Install or start Docker" in text  # how to go on ...
    assert 'sandbox = "process"' in text  # ... or the explicit opt-out
    assert "WITHOUT any isolation" in text
    assert "not for code you do not trust" in text
    assert tripwire.calls == []


def test_the_refusal_is_a_runtime_error(paper: pc.Paper, monkeypatch: pytest.MonkeyPatch) -> None:
    # called directly, without the module system's wrapping
    _docker_down(monkeypatch)
    with pytest.raises(RuntimeError, match="Install or start Docker"):
        rc.reproducibility_check(paper, execute=True)


def test_docker_missing_from_path_refuses_too(
    paper: pc.Paper,
    repro_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
    tripwire: Tripwire,
) -> None:
    # the real availability check, with no docker CLI to be found
    tripwire.process()
    monkeypatch.setattr(docker.shutil, "which", lambda _: None)
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    with pytest.raises(pc.ModuleError, match="'docker' command was not found") as err:
        module_run(exec_chain(paper, "ok.R", repro_fixture), "reproducibility_check", execute=True)
    assert 'sandbox = "process"' in str(err.value)
    assert tripwire.calls == []


def test_an_error_while_checking_docker_does_not_fall_back(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch, tripwire: Tripwire
) -> None:
    tripwire.process()
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")

    def broken() -> dict[str, Any]:
        raise OSError("socket blew up")

    monkeypatch.setattr(docker, "repro_docker_available", broken)
    with pytest.raises(pc.ModuleError, match="socket blew up"):
        module_run(exec_chain(paper, "ok.R", repro_fixture), "reproducibility_check", execute=True)
    assert tripwire.calls == []


def test_a_batch_without_docker_is_refused_before_any_worker_starts(
    monkeypatch: pytest.MonkeyPatch, tripwire: Tripwire
) -> None:
    tripwire.process()
    _docker_down(monkeypatch)
    monkeypatch.setattr(h, "_process_executor", lambda n: pytest.fail("a worker pool was made"))
    monkeypatch.setattr(h, "_batch_worker", lambda *a: pytest.fail("a worker started"))
    papers = pc.read([ROOT / f for f in h_psychsci()[:2]])
    with pytest.raises(pc.ModuleError, match="Install or start Docker"):
        module_run(papers, "reproducibility_check", execute=True, workers=2)
    assert tripwire.calls == []


def h_psychsci() -> list[str]:
    from tests.mod_repro import helpers as rh

    return list(rh.PSYCHSCI)


def test_execute_stops_at_a_sandbox_that_skipped_the_matching() -> None:
    # _execute() sends everything that is not "docker" to the host process; it must not be
    # reachable with a value _match_sandbox() did not vouch for
    with pytest.raises(ValueError, match="unknown sandbox 'vm'"):
        rc._execute(  # type: ignore[arg-type]
            None,
            [],
            {"sandbox": "vm", "timeout": 1, "install_missing": False},
            names=[],
            code_text_list=None,
            rewrite_list=None,
            plan=None,
            structure_df=None,
            code_tbl=None,
            version_pin=None,
            deps=None,
            install_deps=None,
            io=None,
            order_tbl=None,
            runs_as_r=[],
            file_unresolved=[],
            sandbox_root=None,
            keep_sandbox=False,
            stat_output=[],
        )


# -- sandbox = "process": still there, loud, and it does not raise -------------------------------


@pytest.mark.parametrize("sandbox", ["process", "proc", "p", ["process"]])
def test_explicit_process_warns_once_and_runs_through_the_process_backend(
    paper: pc.Paper,
    repro_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
    tripwire: Tripwire,
    sandbox: Any,
) -> None:
    ran: dict[str, Any] = {}

    def fake_run(run_tbl: pd.DataFrame, order: Any, **kw: Any) -> pd.DataFrame:
        ran["order"] = list(order)
        return _run_frame([{"fn": "ok.R", "outcome": "ran_ok", "stdout": "> 1 + 1\n[1] 2\n"}])

    tripwire.docker()
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    monkeypatch.setattr(core, "repro_run_scripts", fake_run)
    monkeypatch.setattr(
        docker, "repro_docker_available", lambda: pytest.fail("Docker was looked at")
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mo = module_run(
            exec_chain(paper, "ok.R", repro_fixture),
            "reproducibility_check",
            execute=True,
            sandbox=sandbox,
        )
    assert ran["order"] == ["ok.R"]
    assert mo.table["outcome"].tolist() == ["ran_ok"]  # it did not raise
    ours = [x for x in w if issubclass(x.category, PytacheckWarning)]
    assert len(ours) == 1
    text = str(ours[0].message)
    assert "on this machine" in text
    assert "no filesystem or network isolation" in text
    assert 'sandbox = "docker"' in text  # names the safe choice
    assert tripwire.calls == []


def test_every_process_run_warns(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    monkeypatch.setattr(
        core,
        "repro_run_scripts",
        lambda *a, **k: _run_frame([{"fn": "ok.R", "outcome": "ran_ok"}]),
    )
    fake = exec_chain(paper, "ok.R", repro_fixture)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        for _ in range(2):
            module_run(fake, "reproducibility_check", execute=True, sandbox="process")
    assert len([x for x in w if issubclass(x.category, PytacheckWarning)]) == 2


def test_a_preset_that_names_the_process_still_warns(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the arguments of a preset (or of report(args = ...)) reach the module as any others do
    entries = select(
        modules=["reproducibility_check"],
        args={"reproducibility_check": {"execute": True, "sandbox": "process"}},
    )
    ((ref, args),) = list(entries)
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    monkeypatch.setattr(
        core,
        "repro_run_scripts",
        lambda *a, **k: _run_frame([{"fn": "ok.R", "outcome": "ran_ok"}]),
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        module_run(exec_chain(paper, "ok.R", repro_fixture), ref, **args)
    assert len([x for x in w if issubclass(x.category, PytacheckWarning)]) == 1


def test_the_process_warning_is_a_pytacheck_warning_that_can_be_made_an_error(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch, tripwire: Tripwire
) -> None:
    # a caller who turns warnings into errors gets a refusal, and nothing runs
    tripwire.process()
    monkeypatch.setattr(core, "_rscript", lambda: "Rscript")
    with warnings.catch_warnings():
        warnings.simplefilter("error", PytacheckWarning)
        with pytest.raises(pc.ModuleError, match="no filesystem or network isolation"):
            module_run(
                exec_chain(paper, "ok.R", repro_fixture),
                "reproducibility_check",
                execute=True,
                sandbox="process",
            )
    assert tripwire.calls == []


def test_process_without_r_is_still_refused(
    paper: pc.Paper, repro_fixture: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core, "_rscript", lambda: None)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        with pytest.raises(pc.ModuleError, match='sandbox = "process" needs R'):
            module_run(
                exec_chain(paper, "ok.R", repro_fixture),
                "reproducibility_check",
                execute=True,
                sandbox="process",
            )
    assert not [x for x in w if issubclass(x.category, PytacheckWarning)]  # nothing was run


# -- the static phase is unchanged ---------------------------------------------------------------


@pytest.mark.parametrize("kwargs", [{}, {"sandbox": "docker"}, {"sandbox": "process"}])
def test_execute_false_never_touches_docker_and_warns_of_nothing(
    paper: pc.Paper,
    repro_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
    tripwire: Tripwire,
    kwargs: dict[str, Any],
) -> None:
    tripwire.process()
    tripwire.docker()
    monkeypatch.setattr(
        docker, "repro_docker_available", lambda: pytest.fail("Docker was looked at")
    )
    monkeypatch.setattr(core, "_rscript", lambda: pytest.fail("Rscript was looked for"))
    code_tbl = code_tbl_row(paper, "ok.R", repro_fixture("ok.R"))
    structure_df = pd.DataFrame(
        {
            "paper_id": ["p1", "p1"],
            "file_name": ["ok.R", "data.csv"],
            "file_location": [repro_fixture("ok.R"), repro_fixture("data.csv")],
        }
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mo = module_run(
            chain(paper, code_tbl, structure_df, PLAN), "reproducibility_check", **kwargs
        )
    assert mo.traffic_light == "green"
    assert mo.get("run_results") is None  # nothing ran
    assert not [x for x in w if issubclass(x.category, PytacheckWarning)]
    assert tripwire.calls == []


# -- a real container ----------------------------------------------------------------------------


def _usable_local_image() -> str | None:
    """A Docker image with R that is already on this machine (a pull is never made).

    ``PYTACHECK_TEST_DOCKER_IMAGE`` first, then the image the module defaults to, then the
    ones the repository's own tooling uses.
    """
    out = subprocess.run(
        ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    have = set(out.stdout.split())
    wanted = [
        os.environ.get("PYTACHECK_TEST_DOCKER_IMAGE"),
        docker._REPRO_DOCKER_DEFAULT_IMAGE,
        "rocker/r-ver:latest",
        *sorted(i for i in have if i.startswith("pytacheck-r-reference:")),
    ]
    for image in wanted:
        if image and image in have:
            try:
                probe = subprocess.run(
                    [
                        *["docker", "run", "--rm", "--pull", "never", "--network", "none", image],
                        *["Rscript", "-e", "cat(1+1)"],
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                continue
            if probe.returncode == 0 and probe.stdout.strip() == "2":
                return image
    return None


PROBE_R = r"""
p <- function(x) gsub("@", "/", x, fixed = TRUE)
# a URL and a path the module would resolve statically, and skip the script on; built at run time
u <- function(x) paste0(rawToChar(as.raw(c(104, 116, 116, 112, 58, 47, 47))), x)
site <- paste0("ex", "ample", rawToChar(as.raw(46)), "com")
probe <- function(name, expr) {
  out <- tryCatch({ force(expr); "ALLOWED" },
    error = function(e) paste("BLOCKED", conditionMessage(e)),
    warning = function(w) paste("BLOCKED", conditionMessage(w)))
  cat("PROBE ", name, " ", out, "\n", sep = "")
}
say <- function(name, value) cat("PROBE ", name, " ", value, "\n", sep = "")

# where am I
say("dockerenv", file.exists("/.dockerenv"))
say("nodename", Sys.info()[["nodename"]])
status <- readLines("/proc/self/status")
say("uid", sub("^Uid:\\s+([0-9]+).*", "\\1", status[grepl("^Uid:", status)]))
say("capeff", sub("^CapEff:\\s+", "", status[grepl("^CapEff:", status)]))
say("nonewprivs", sub("^NoNewPrivs:\\s+", "", status[grepl("^NoNewPrivs:", status)]))

# (a) the network
probe("net_dns", readLines(url(u(site)), n = 1))
probe("net_ip", socketConnection(host = "1.1.1.1", port = 53, open = "r+", blocking = TRUE,
                                 timeout = 3))

# (b) writing outside the sandbox directory and /tmp
probe("write_etc", writeLines("x", "/etc/repro_probe"))
probe("write_usr", writeLines("x", "/usr/repro_probe"))
probe("write_root", writeLines("x", "/repro_probe"))
# controls: where writing is meant to work
probe("write_sandbox", writeLines("x", "output/control.txt"))
probe("write_tmp", writeLines("x", "/tmp/control.txt"))

# (c) a host file that is not in the mounted sandbox
secret <- p("__SECRET__")
say("secret_exists", file.exists(secret))
probe("read_secret", readLines(secret))
say("elsewhere_exists", dir.exists(dirname(secret)))

# (d) the host's HOME
home <- p("__HOME__")
say("home_exists", dir.exists(home))
say("home_entries_visible", sum(file.exists(file.path(home, __ENTRIES__))))
"""

NETWORK_R = 'con <- socketConnection(host = "1.1.1.1", port = 53, open = "r+", timeout = 3)\n'


def _real_docker_image(monkeypatch: pytest.MonkeyPatch) -> str:
    """Make the module use a local image with R, or skip: Docker, Linux containers, an image."""
    if not docker.repro_docker_available()["ok"]:
        pytest.skip("Docker not available")
    info = subprocess.run(
        ["docker", "info", "--format", "{{.OSType}}"], capture_output=True, text=True, check=False
    )
    if info.stdout.strip() != "linux":
        pytest.skip("Docker does not run Linux containers")
    image = _usable_local_image()
    if image is None:
        pytest.skip("no Docker image with R is on this machine (a pull is never made)")
    monkeypatch.setattr(docker, "_REPRO_DOCKER_DEFAULT_IMAGE", image)
    return image


@pytest.mark.slow
def test_real_container_isolates_the_run_phase(
    paper: pc.Paper, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default sandbox, for real: no network, no writes outside, no host files, no HOME."""
    image = _real_docker_image(monkeypatch)

    # host files that the container must not see: another temporary directory, and the HOME
    other = tmp_path / "elsewhere"
    other.mkdir()
    secret = other / "secret.txt"
    secret.write_text("TOPSECRET-host-file\n")
    home = os.path.expanduser("~")
    entries = [e for e in sorted(os.listdir(home)) if re.fullmatch(r"[A-Za-z0-9_-]+", e)][:25] or [
        "no-such-entry"
    ]
    r_entries = "c(" + ", ".join(f'"{e}"' for e in entries) + ")"

    def at(path: str) -> str:
        # the R script spells its paths without a literal, or the module would skip it as one
        # with missing inputs
        return path.replace("/", "@")

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "01_probe.R").write_text(
        PROBE_R.replace("__SECRET__", at(str(secret)))
        .replace("__HOME__", at(home))
        .replace("__ENTRIES__", r_entries)
    )
    (scripts / "02_network.R").write_text(NETWORK_R)
    code_tbl = code_tbl_row(
        paper,
        ["01_probe.R", "02_network.R"],
        [str(scripts / "01_probe.R"), str(scripts / "02_network.R")],
    )
    structure_df = pd.DataFrame({"paper_id": [], "file_name": [], "file_location": []})

    made: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def spy(*args: Any, **kwargs: Any) -> str:
        d = real_mkdtemp(*args, **kwargs)
        made.append(d)
        return d

    monkeypatch.setattr(tempfile, "mkdtemp", spy)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        mo = module_run(
            chain(paper, code_tbl, structure_df, PLAN),
            "reproducibility_check",
            execute=True,
            timeout=300,
        )
    monkeypatch.undo()

    assert not [x for x in w if issubclass(x.category, PytacheckWarning)]
    rr = mo.run_results
    assert rr["file_name"].tolist() == ["01_probe.R", "02_network.R"]
    stdout = rr["stdout"].iloc[0]
    print(f"image: {image}\n{stdout}")
    probes = dict(re.findall(r"^PROBE (\w+) (.*)$", stdout, flags=re.MULTILINE))

    # the harness itself works: the probe script ran in a container, and writes that are
    # meant to work did
    assert rr["outcome"].tolist()[0] == "ran_ok", rr[["file_name", "outcome", "error"]]
    assert probes["dockerenv"] == "TRUE"
    assert probes["nodename"] != os.uname().nodename
    assert probes["write_sandbox"] == "ALLOWED"
    assert probes["write_tmp"] == "ALLOWED"

    # (a) no network: a name lookup and a connection to an address both fail
    assert probes["net_dns"].startswith("BLOCKED"), probes["net_dns"]
    assert probes["net_ip"].startswith("BLOCKED"), probes["net_ip"]
    # (b) no writes outside the sandbox directory and /tmp
    for where in ("write_etc", "write_usr", "write_root"):
        assert probes[where].startswith("BLOCKED"), (where, probes[where])
    for path in ("/etc/repro_probe", "/usr/repro_probe", "/repro_probe"):
        assert not os.path.exists(path)  # nor did anything reach the host
    # (c) a host file outside the mounted sandbox is not there, whatever it holds
    assert probes["secret_exists"] == "FALSE"
    assert probes["read_secret"].startswith("BLOCKED"), probes["read_secret"]
    assert probes["elsewhere_exists"] == "FALSE"
    assert "TOPSECRET" not in stdout and "TOPSECRET" not in rr["stderr"].iloc[0]
    assert secret.read_text() == "TOPSECRET-host-file\n"
    # (d) the host's HOME does not exist in the container (/root and /tmp do, as the image's own)
    assert probes["home_entries_visible"] == "0"
    if home not in ("/root", "/tmp", "/home"):
        assert probes["home_exists"] == "FALSE"
    # non-root, no capabilities, no new privileges
    assert probes["uid"] == docker._repro_docker_user().split(":")[0]
    assert probes["uid"] != "0"
    assert set(probes["capeff"]) == {"0"}
    assert probes["nonewprivs"] == "1"

    # the module reports what happened to each script, in its own vocabulary
    assert mo.table["outcome"].tolist() == ["ran_ok", "errored"]
    assert "cannot open the connection" in rr["error"].iloc[1]
    assert mo.traffic_light == "red"
    assert mo.get("sandbox") is None

    # the throwaway directories are gone
    sandboxes = [
        d for d in made if os.path.basename(d).startswith(("repro_sandbox_", "pytacheck_"))
    ]
    assert sandboxes
    assert not [d for d in sandboxes if os.path.exists(d)]


# -- what the container leaves in its sandbox directory ---------------------------------------------

PLANT_R = r"""
p <- function(x) gsub("@", "/", x, fixed = TRUE)
nxt <- paste0("02_next", ".R")
# the sandbox directory is writable, and a link there can name a path on the host: the host would
# follow it where it writes the next script's runner, and where it reads the next script
file.symlink(p("__VICTIM__"), paste0(".runner_X", nxt, ".R"))
unlink(nxt)
file.symlink(p("__SECRET__"), nxt)
file.symlink("01_plant.R", "inside_link.R")
cat("planted\n")
"""


def _plant_chain(paper: pc.Paper, scripts: Path) -> Any:
    names = ["01_plant.R", "02_next.R", "03_last.R"]
    for i, name in enumerate(names[1:], start=2):  # different texts: equal scripts count once
        (scripts / name).write_text(f'cat("script {i}\\n")\n')
    code_tbl = code_tbl_row(paper, names, [str(scripts / n) for n in names])
    structure_df = pd.DataFrame({"paper_id": [], "file_name": [], "file_location": []})
    return chain(paper, code_tbl, structure_df, PLAN)


@pytest.mark.slow
def test_real_container_links_in_the_sandbox_never_reach_the_host(
    paper: pc.Paper, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link the code leaves in its sandbox is not followed by the host (no write, no read)."""
    _real_docker_image(monkeypatch)
    other = tmp_path / "elsewhere"
    other.mkdir()
    secret = other / "secret.txt"
    secret.write_text("TOPSECRET-host-file\n")
    victim = other / "victim.txt"
    victim.write_text("KEEP-this-file\n")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "01_plant.R").write_text(
        PLANT_R.replace("__VICTIM__", str(victim).replace("/", "@")).replace(
            "__SECRET__", str(secret).replace("/", "@")
        )
    )
    mo = module_run(
        _plant_chain(paper, scripts), "reproducibility_check", execute=True, timeout=120
    )
    rr = mo.run_results
    assert rr["file_name"].tolist() == ["01_plant.R", "02_next.R", "03_last.R"]
    assert rr["outcome"].tolist()[0] == "ran_ok", rr[["file_name", "outcome", "error"]]
    # the host wrote no runner script through a link, and read no file through one
    assert victim.read_text() == "KEEP-this-file\n"
    everything = "\n".join(
        str(v) for col in rr.columns for v in rr[col].tolist() if v is not None
    ) + str(mo.report)
    assert "TOPSECRET" not in everything
    # the script the code replaced by a link is gone, so it errors; the one it left alone runs
    assert rr["outcome"].tolist() == ["ran_ok", "errored", "ran_ok"]
    assert "script 3" in rr["stdout"].iloc[2]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes need a POSIX system")
def test_the_scrub_removes_what_the_host_must_not_follow_or_open(tmp_path: Path) -> None:
    """Links that leave the sandbox, pipes and sockets go; files, directories, inner links stay."""
    root = tmp_path / "sandbox"
    (root / "output").mkdir(parents=True)
    (root / "script.R").write_text("x <- 1\n")
    (root / "output" / "result.csv").write_text("a\n1\n")
    outside = tmp_path / "outside.txt"
    outside.write_text("host\n")
    (root / "to_host").symlink_to(outside)
    (root / "to_host_dir").symlink_to(tmp_path, target_is_directory=True)
    (root / "output" / "dangling").symlink_to("/no/such/host/path")
    (root / "output" / "up").symlink_to("../../outside.txt")
    (root / "inner").symlink_to("script.R")
    (root / "inner_dir").symlink_to("output", target_is_directory=True)
    os.mkfifo(root / "pipe.R")
    removed = docker._repro_docker_scrub(root)
    assert sorted(removed) == sorted(
        ["to_host", "to_host_dir", "output/dangling", "output/up", "pipe.R"]
    )
    assert sorted(p.name for p in root.iterdir()) == ["inner", "inner_dir", "output", "script.R"]
    assert (root / "output" / "result.csv").read_text() == "a\n1\n"
    assert outside.read_text() == "host\n"  # nothing was followed
    assert docker._repro_docker_scrub(root) == []


def test_the_scrub_runs_after_every_container_and_before_the_host_reads(
    paper: pc.Paper, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host's reads and writes in the sandbox come after the scrub (container mocked)."""
    order: list[str] = []
    real_scrub = docker._repro_docker_scrub
    real_read = docker._script_lines

    def scrub(root: Any) -> list[str]:
        order.append("scrub")
        return real_scrub(root)

    def read(path: Any) -> list[str]:
        order.append("read")
        return real_read(path)

    def fake_docker(args: Any, **kw: Any) -> dict[str, Any]:
        order.append("container")
        return {"status": 0, "timeout": False, "stdout": ""}

    monkeypatch.setattr(docker, "_repro_docker_scrub", scrub)
    monkeypatch.setattr(docker, "_script_lines", read)
    monkeypatch.setattr(docker, "_docker", fake_docker)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for n in ("a.R", "b.R"):
        (scripts / n).write_text("x <- 1\n")
    run_tbl = pd.DataFrame(
        {
            "file_name": ["a.R", "b.R"],
            "script_path": [str(scripts / "a.R"), str(scripts / "b.R")],
            "run_dir": [str(scripts)] * 2,
        }
    )
    docker.repro_run_scripts_docker(run_tbl, ["a.R", "b.R"], scripts, image="img")
    assert order == ["read", "container", "scrub", "read", "container", "scrub"]


# -- who the container runs as ----------------------------------------------------------------------


def _as_host(
    monkeypatch: pytest.MonkeyPatch, platform: str, uid: int | None = None, gid: int = 0
) -> None:
    """Make :mod:`metacheck.repro.docker` see a host of the given platform and user."""
    monkeypatch.setattr(docker.sys, "platform", platform)
    if uid is None:
        monkeypatch.delattr(os, "getuid", raising=False)
    else:
        monkeypatch.setattr(os, "getuid", lambda: uid, raising=False)
        monkeypatch.setattr(os, "getgid", lambda: gid, raising=False)


@pytest.mark.parametrize(
    ("platform", "uid", "gid", "expected"),
    [
        ("linux", 1001, 1002, "1001:1002"),  # a runner, or any account that is not the first one
        ("linux", 1000, 1000, "1000:1000"),
        ("linux", 0, 0, "1000:1000"),  # never root, whoever runs this
        ("darwin", 501, 20, "1000:1000"),  # Docker Desktop does not enforce host ownership
        ("win32", None, 0, "1000:1000"),  # no getuid at all
    ],
)
def test_the_container_runs_as_the_host_user_never_root(
    monkeypatch: pytest.MonkeyPatch, platform: str, uid: int | None, gid: int, expected: str
) -> None:
    _as_host(monkeypatch, platform, uid, gid)
    assert docker._repro_docker_user() == expected
    assert docker._repro_docker_user().split(":")[0] != "0"


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="owners and modes are POSIX")
def test_the_sandbox_is_private_to_the_host_user(tmp_path: Path) -> None:
    """The premise of :func:`_repro_docker_user`: a container user who is not the host user
    cannot enter the directory the sandbox is made in, so it must be the host user."""
    root = tempfile.mkdtemp(prefix="repro_sandbox_", dir=tmp_path)
    assert stat.S_IMODE(os.stat(root).st_mode) == 0o700
    assert os.stat(root).st_uid == os.getuid()


@pytest.mark.parametrize("phase", ["run", "install"])
def test_each_container_is_started_as_that_user(
    paper: pc.Paper, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    _as_host(monkeypatch, "linux", 4242, 4343)
    started: list[list[str]] = []

    def fake_docker(args: Any, **kw: Any) -> dict[str, Any]:
        started.append(list(args))
        return {"status": 0, "timeout": False, "stdout": ""}

    monkeypatch.setattr(docker, "_docker", fake_docker)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    if phase == "run":
        (scripts / "a.R").write_text("x <- 1\n")
        run_tbl = pd.DataFrame(
            {
                "file_name": ["a.R"],
                "script_path": [str(scripts / "a.R")],
                "run_dir": [str(scripts)],
            }
        )
        docker.repro_run_scripts_docker(run_tbl, ["a.R"], scripts, image="img")
    else:
        deps = pd.DataFrame({"package": ["stringr"], "source": ["cran"]})
        docker.repro_install_deps_docker(deps, tmp_path / "lib", image="img")
    assert len(started) == 1
    args = started[0]
    assert args[args.index("--user") + 1] == "4242:4343"
    assert "--cap-drop" in args and "no-new-privileges" in args


def test_a_root_host_hands_the_sandbox_to_the_container_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run as root, the code runs as 1000:1000, and the private directories are made theirs."""
    _as_host(monkeypatch, "linux", 0, 0)
    outside = tmp_path / "outside.txt"
    outside.write_text("host\n")
    root = tmp_path / "sandbox"
    (root / "sub").mkdir(parents=True)
    (root / "a.R").write_text("x\n")
    (root / "sub" / "b.R").write_text("x\n")
    (root / "to_host").symlink_to(outside)
    (root / "to_dir").symlink_to(tmp_path, target_is_directory=True)
    owned: list[tuple[str, int, int]] = []
    monkeypatch.setattr(os, "lchown", lambda p, u, g: owned.append((os.fspath(p), u, g)))

    docker._repro_docker_hand_over(root)
    names = sorted(os.path.relpath(p, root) for p, u, g in owned)
    assert names == [".", "a.R", "sub", os.path.join("sub", "b.R"), "to_dir", "to_host"]
    assert {(u, g) for _, u, g in owned} == {(1000, 1000)}
    handed = [p for p, _, _ in owned]
    assert str(outside) not in handed and str(tmp_path) not in handed  # a link is not followed
    assert all(p.startswith(str(root)) for p in handed)  # nothing outside the sandbox

    owned.clear()
    docker._repro_docker_hand_over(root / "a.R", tree=False)
    assert [p for p, _, _ in owned] == [str(root / "a.R")]


@pytest.mark.parametrize(("platform", "uid"), [("linux", 1001), ("darwin", 0), ("win32", None)])
def test_nothing_is_handed_over_unless_a_linux_root_runs_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str, uid: int | None
) -> None:
    _as_host(monkeypatch, platform, uid)
    (tmp_path / "a.R").write_text("x\n")
    monkeypatch.setattr(os, "lchown", lambda *a: pytest.fail("lchown was called"), raising=False)
    docker._repro_docker_hand_over(tmp_path)


def _chunks_outside_fences(text: str) -> list[str]:
    """The lines that open an executable Quarto chunk at the top level (CommonMark fences)."""
    opened: int | None = None
    found: list[str] = []
    for line in text.split("\n"):
        m = re.match(r"^(`{3,})(.*)$", line)
        if opened is None:
            if m:
                if m.group(2).startswith("{"):
                    found.append(line)
                opened = len(m.group(1))
        elif m and len(m.group(1)) >= opened and m.group(2).strip() == "":
            opened = None
    return found


@pytest.mark.parametrize(
    ("stdout", "fence"),
    [("> 1 + 1\n[1] 2\n", "````"), ("````\n```{r}\nsystem('id')\n```\n````\n", "`````")],
)
def test_the_output_of_the_code_cannot_close_its_fence_in_the_report(
    paper: pc.Paper,
    repro_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    fence: str,
) -> None:
    """A run of backticks in the output must not end the fence: Quarto would run what follows."""
    monkeypatch.setattr(docker, "repro_docker_available", lambda: DOCKER_OK)
    monkeypatch.setattr(
        docker,
        "repro_run_scripts_docker",
        lambda *a, **kw: _run_frame([{"fn": "ok.R", "outcome": "ran_ok", "stdout": stdout}]),
    )
    mo = module_run(exec_chain(paper, "ok.R", repro_fixture), "reproducibility_check", execute=True)
    text = mo.report if isinstance(mo.report, str) else "\n".join(str(x) for x in mo.report)
    assert f"\n{fence}\n" in text
    assert _chunks_outside_fences(text) == []
