"""``metacheck-setup``: a short guided setup that the installer runs once.

It asks what the person wants to do, how to reach bibr when they want to check papers, and
for an email address for the open APIs (OSF, Crossref, OpenAlex). It only asks and saves;
installing bibr, when it is wanted, is the installer's job (``--result FILE`` says so).
It does not import gradio.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from metacheck._env import env_get
from metacheck.app import bibr, settings

__all__ = ["Answers", "main", "run_wizard"]

#: GB of free space the bibr models and caches want
SELFHOST_GB = 15


@dataclass
class Answers:
    papers: bool = False
    #: ``key``, ``selfhost`` or ``later`` (only when ``papers``)
    access: str = ""
    key: str = ""
    email: str = ""

    @property
    def wants_bibr_package(self) -> bool:
        return self.papers and self.access == "selfhost"


def hardware() -> str:
    """``gpu`` (NVIDIA driver found), ``apple`` (Apple Silicon) or ``cpu``."""
    if platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64"):
        return "apple"
    if shutil.which("nvidia-smi"):
        return "gpu"
    return "cpu"


def free_gb(path: Path | None = None) -> float:
    target = path or Path.home()
    try:
        return shutil.disk_usage(target).free / 1e9
    except OSError:
        return float("inf")


def selfhost_warning(hw: str, free: float) -> str:
    lines = [
        f"Running bibr on this computer downloads several GB of models (plan for about "
        f"{SELFHOST_GB} GB of free disk space) and is installed on top of metacheck.",
    ]
    if hw == "gpu":
        lines.append("An NVIDIA GPU was found, so it should run at a good speed.")
    elif hw == "apple":
        lines.append("This is an Apple Silicon Mac, which runs it at a reasonable speed.")
    else:
        lines.append(
            "No NVIDIA GPU or Apple Silicon was found: it will work, but slowly. "
            "A key for the bibr service is the easier choice here."
        )
    if free < SELFHOST_GB:
        lines.append(f"Only {free:.0f} GB are free on this disk.")
    return "\n".join(lines)


def run_wizard(
    ask: Callable[[str], str],
    say: Callable[[str], None],
    *,
    data_only: bool = False,
    hw: str | None = None,
    free: float | None = None,
    default_email: str = "",
) -> Answers:
    """Ask the questions. *ask* shows a prompt and returns the typed line."""
    hw = hw or hardware()
    free = free_gb() if free is None else free
    ans = Answers()

    def choose(title: str, options: list[str], default: int = 1) -> int:
        say(title)
        for i, text in enumerate(options, 1):
            say(f"  {i}) {text}")
        while True:
            raw = ask(f"Your choice [{default}]: ").strip()
            if not raw:
                return default
            if raw.isdigit() and 1 <= int(raw) <= len(options):
                return int(raw)
            say(f"Please type a number from 1 to {len(options)}.")

    say("")
    say("Welcome to metacheck. Three quick questions, then the app opens in your browser.")
    say("")
    if not data_only:
        pick = choose(
            "1. What do you want to check?",
            [
                "Data packages only (the simplest; nothing else to set up)",
                "Data packages and papers",
            ],
        )
        ans.papers = pick == 2
    if ans.papers:
        say("")
        pick = choose(
            "2. How should papers (PDFs) be read? This uses bibr.",
            [
                "I have a bibr API key",
                f"Run bibr on this computer (about {SELFHOST_GB} GB of disk; needs a GPU or "
                "Apple Silicon to be fast)",
                "Decide later (the app can use GROBID in the meantime)",
            ],
            default=2 if hw != "cpu" and free >= SELFHOST_GB else 1,
        )
        ans.access = ("key", "selfhost", "later")[pick - 1]
        if ans.access == "key":
            say(
                "Paste the key (it starts with bibr_pat_). It is saved on this computer "
                "and is sent to the bibr service only."
            )
            ans.key = ask("Key (Enter to skip): ").strip()
            if not ans.key:
                ans.access = "later"
        elif ans.access == "selfhost":
            say("")
            say(selfhost_warning(hw, free))
            if ask("Install it anyway? [y/N]: ").strip().lower() not in ("y", "yes"):
                ans.access = "later"
        if ans.access == "later":
            say("OK. Run metacheck-setup again whenever you have a key.")
    say("")
    say(
        "Open services such as OSF, Crossref and OpenAlex ask for a contact address, "
        "so they can write to you instead of blocking you. It is sent to them only."
    )
    for _ in range(3):
        prompt = (
            f"Your email address [{default_email}]: " if default_email else "Your email address: "
        )
        address = ask(prompt).strip() or default_email
        if settings.valid_email(address):
            ans.email = address
            break
        say("That does not look like an email address." if address else "Please type an address.")
    else:
        say("Skipped. Some online checks may be slower. Run metacheck-setup to add it later.")
    return ans


def save(ans: Answers) -> None:
    """Remember the answers."""
    values: dict[str, object] = {}
    if ans.email:
        values["email"] = ans.email
    if ans.papers:
        values["bibr_selfhost"] = True if ans.access == "selfhost" else None
    if ans.key:
        bibr.save_key(ans.key)
    if values:
        settings.write(**values)


def _tty_prompter() -> tuple[Callable[[str], str], TextIO | None] | None:
    """Questions go to the person's terminal, also under ``curl | sh`` where stdin is the pipe."""
    if sys.stdin is not None and sys.stdin.isatty():
        return input, None
    try:
        tty = open("/dev/tty" if os.name != "nt" else "CONIN$", encoding="utf-8")  # noqa: SIM115
    except OSError:
        return None
    if not tty.isatty():
        # a console handle that nobody types into (a CI runner) would wait for ever
        tty.close()
        return None

    def ask(prompt: str) -> str:
        print(prompt, end="", flush=True)
        line = tty.readline()
        if not line:
            raise EOFError
        return line.rstrip("\n")

    return ask, tty


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metacheck-setup",
        description="Answer a few questions once; metacheck remembers them.",
    )
    parser.add_argument("--data-only", action="store_true", help="skip the paper questions")
    parser.add_argument(
        "--if-new", action="store_true", help="do nothing when the questions were answered before"
    )
    parser.add_argument(
        "--result", metavar="FILE", help="write install_bibr=1 here when bibr must be installed"
    )
    ns = parser.parse_args(argv)
    if ns.if_new and settings.settings_path().exists():
        if ns.result:
            Path(ns.result).write_text("install_bibr=0\n", encoding="utf-8")
        return 0
    prompter = _tty_prompter()
    if prompter is None:
        print("No terminal to ask questions in; skipping setup. Run metacheck-setup later.")
        return 0
    ask, tty = prompter
    try:
        ans = run_wizard(ask, print, data_only=ns.data_only, default_email=env_get("EMAIL") or "")
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled; nothing was saved. Run metacheck-setup to try again.")
        return 1
    finally:
        if tty is not None:
            tty.close()
    save(ans)
    if ns.result:
        Path(ns.result).write_text(
            "install_bibr=1\n" if ans.wants_bibr_package else "install_bibr=0\n", encoding="utf-8"
        )
    elif ans.wants_bibr_package:
        print("To install bibr, run the metacheck installer again with --with-bibr.")
    print("Saved. You can change these answers any time with metacheck-setup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
