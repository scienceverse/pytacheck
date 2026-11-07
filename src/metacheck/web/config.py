"""What the web interface offers and how much it takes: modules, limits, settings.

The module sets and the limits are those of the Metacheck site on SURF Research Cloud, so a
script written for that web interface keeps working. Nothing here imports FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "ALLOWED_MODULES",
    "DEFAULT_MODULES",
    "KEY_ENV",
    "KEY_SERVICES",
    "MAX_UNPACKED_BYTES",
    "MAX_UPLOAD_BYTES",
    "MAX_ZIP_BYTES",
    "MAX_ZIP_FILES",
    "PSYARXIV_API",
    "REPOSITORY_MODULES",
    "SiteConfig",
    "allowed_modules",
    "module_info",
    "psyarxiv_api",
]

MB = 1024 * 1024

#: a PDF, bibr JSON or GROBID XML
MAX_UPLOAD_BYTES = 50 * MB
#: a zip of a data and code folder
MAX_ZIP_BYTES = 500 * MB
#: what a zip may hold once unpacked, and how many files
MAX_UNPACKED_BYTES = 1000 * MB
MAX_ZIP_FILES = 20_000

#: the checks a visitor may ask for. ``ref_accuracy`` is left out, as on the site this
#: interface follows: most of its flags come from reading errors in the PDF.
ALLOWED_MODULES: tuple[str, ...] = (
    "power",
    "marginal",
    "prereg_check",
    "ethics_check",
    "ref_pubpeer",
    "ref_retraction",
    "ref_replication",
    "stat_check",
    "stat_p_exact",
    "stat_p_nonsig",
    "stat_effect_size",
    "repo_check",
    "code_check",
    "data_check",
    "codebook_check",
)
#: the checks that need no other online service, so a report is quick
DEFAULT_MODULES: tuple[str, ...] = (
    "power",
    "marginal",
    "ethics_check",
    "stat_check",
    "stat_p_exact",
    "stat_p_nonsig",
    "stat_effect_size",
)
#: the checks of a repository: they run one after the other on the unpacked folder
REPOSITORY_MODULES: tuple[str, ...] = ("repo_check", "code_check", "data_check")

#: the data repositories a visitor may send their own key for (field ``key_<service>``)
KEY_SERVICES: tuple[str, ...] = ("github", "osf", "dryad", "researchbox", "gitlab", "openalex")
#: the variable that pytacheck reads each key from; a service with no variable is accepted
#: and has no effect yet (pytacheck reads no ResearchBox or OpenAlex key)
KEY_ENV: dict[str, str | None] = {
    "github": "GITHUB_PAT_GITHUB_COM",
    "osf": "OSF_PAT",
    "dryad": "DRYAD_PAT",
    "gitlab": "GITLAB_PAT",
    "researchbox": None,
    "openalex": None,
}

#: where a PsyArXiv record and its file are fetched from (``METACHECK_PSYARXIV_API`` replaces it)
PSYARXIV_API = "https://next.psyarxiv.org/api/v1"


def psyarxiv_api() -> str:
    """The PsyArXiv API address: ``METACHECK_PSYARXIV_API``, else :data:`PSYARXIV_API`."""
    from metacheck._env import env_get

    return (env_get("PSYARXIV_API") or PSYARXIV_API).strip()


def allowed_modules() -> list[str]:
    """:data:`ALLOWED_MODULES` that this version of pytacheck has, in the same order."""
    import metacheck as pc

    have = set(pc.module_list()["name"].astype(str))
    return [m for m in ALLOWED_MODULES if m in have]


def module_info() -> list[dict[str, object]]:
    """Title, description and default flag of every allowed module (``GET /api/modules``)."""
    import metacheck as pc

    table = pc.module_list()
    rows = {
        str(n): (str(t), str(d))
        for n, t, d in zip(table["name"], table["title"], table["description"], strict=True)
    }
    info: list[dict[str, object]] = []
    for name in allowed_modules():
        title, description = rows[name]
        info.append(
            {
                "name": name,
                "title": title,
                "description": " ".join(description.split()),
                "default": name in DEFAULT_MODULES,
            }
        )
    return info


@dataclass(frozen=True)
class SiteConfig:
    """The settings of one server. The defaults are the limits of the site this follows."""

    #: ask Docker to run a repository's code (``--run-code``); needs a running Docker
    run_code: bool = False
    #: where uploads and results are kept while a job lives; ``None``: a private temporary
    #: folder that is removed when the server stops
    jobs_dir: Path | None = None
    #: jobs that run at the same time, and jobs that may wait
    max_running: int = 2
    max_waiting: int = 20
    #: seconds a job may take, and seconds a finished job's result is kept
    timeout: float = 30 * 60.0
    keep: float = 60 * 60.0
    #: new jobs are refused while less than this is free where the jobs are kept
    min_free_bytes: int = 1024 * MB
    #: the PsyArXiv API (``METACHECK_PSYARXIV_API``)
    psyarxiv_api: str = field(default_factory=lambda: psyarxiv_api())
