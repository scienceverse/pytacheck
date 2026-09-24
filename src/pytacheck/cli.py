"""The ``pytacheck`` command line.

Examples::

    pytacheck modules                                # list available modules
    pytacheck run paper.json -m all_p_values -m marginal
    pytacheck run paper.pdf -m marginal --json       # PDFs need pytacheck[bibr]
    pytacheck report paper.json -o report.html
    pytacheck read paper.pdf -o paper.json           # extract with bibr, save JSON
    pytacheck serve --port 8000                      # REST API (pytacheck[api])
    pytacheck version
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _parse_value(text: str) -> Any:
    import orjson

    try:
        return orjson.loads(text)
    except orjson.JSONDecodeError:
        return text


def _module_args(pairs: Sequence[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--arg expects key=value, got {pair!r}")
        out[key.strip()] = _parse_value(value)
    return out


def _read(paths: Sequence[str]) -> Any:
    from pytacheck.io.read import read

    return read(paths[0] if len(paths) == 1 else list(paths))


def cmd_modules(ns: argparse.Namespace) -> int:
    from pytacheck.module import format_module_list, module_help, module_list

    if ns.name:
        print(module_help(ns.name))
    else:
        print(format_module_list(module_list()))
    return 0


def cmd_run(ns: argparse.Namespace) -> int:
    import orjson

    from pytacheck.module import module_run

    papers = _read(ns.papers)
    kwargs = _module_args(ns.arg or [])
    result: Any = papers
    outputs = []
    for name in ns.module:
        result = module_run(result, name, **kwargs)
        outputs.append(result)
    if ns.json:
        payload = [
            {
                "module": o.module,
                "title": o.title,
                "traffic_light": o.traffic_light,
                "summary_text": o.summary_text,
                "table": None if o.table is None else o.table.to_dict(orient="records"),
            }
            for o in outputs
        ]
        sys.stdout.buffer.write(
            orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY, default=str)
        )
        sys.stdout.write("\n")
    else:
        from rich.console import Console

        console = Console()
        colours = {"red": "red", "yellow": "yellow", "green": "green", "fail": "magenta"}
        for o in outputs:
            colour = colours.get(o.traffic_light, "cyan")
            console.print(f"[{colour}]●[/] [bold]{o.title}[/]: {o.summary_text}")
        if outputs and outputs[-1].summary_table is not None:
            console.print(outputs[-1].summary_table.to_string(index=False))
    return 0


def cmd_report(ns: argparse.Namespace) -> int:
    try:
        from pytacheck.report.report import report
    except ImportError:  # pragma: no cover - until the report renderer lands
        print("report rendering is not available in this build", file=sys.stderr)
        return 2
    papers = _read(ns.papers)
    kwargs: dict[str, Any] = {"output_file": ns.output, "output_format": ns.format}
    if ns.module:
        kwargs["modules"] = ns.module
    path = report(papers, **kwargs)
    print(path)
    return 0


def cmd_read(ns: argparse.Namespace) -> int:
    from pytacheck.papers.io import paper_write
    from pytacheck.papers.model import PaperList

    papers = _read(ns.papers)
    out = Path(ns.output) if ns.output else Path(".")
    if isinstance(papers, PaperList):
        paths = paper_write(papers, save_path=out)
        print("\n".join(str(p) for p in paths))  # type: ignore[union-attr]
    else:
        target_dir = out.parent if out.suffix == ".json" else out
        name = out.stem if out.suffix == ".json" else None
        print(paper_write(papers, name, target_dir))
    return 0


def cmd_serve(ns: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print('The API needs the api extra: pip install "pytacheck[api]"', file=sys.stderr)
        return 2
    uvicorn.run("pytacheck.api.app:create_app", factory=True, host=ns.host, port=ns.port)
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    from pytacheck._version import UPSTREAM, __version__
    from pytacheck.io.bibr import bibr_version

    print(f"pytacheck {__version__}")
    print(f"metacheck parity: {UPSTREAM['version']} ({UPSTREAM['branch']}@{UPSTREAM['commit'][:10]})")
    print(f"bibr: {bibr_version() or 'not installed'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pytacheck",
        description="Check research outputs for best practices.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("modules", help="list modules or show help for one")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_modules)

    p = sub.add_parser("run", help="run modules on papers")
    p.add_argument("papers", nargs="+", help="bibr JSON, Grobid XML, PDF/DOCX (with bibr), or a directory")
    p.add_argument("-m", "--module", action="append", required=True, help="module name or path (repeatable)")
    p.add_argument("-a", "--arg", action="append", help="module argument key=value (JSON values allowed)")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="write a report for papers")
    p.add_argument("papers", nargs="+")
    p.add_argument("-m", "--module", action="append")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("-f", "--format", default="html", choices=["html", "qmd", "md"])
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("read", help="read/extract papers and save bibr JSON")
    p.add_argument("papers", nargs="+")
    p.add_argument("-o", "--output", default=None, help="output file or directory")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("serve", help="run the REST API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("version", help="show versions")
    p.set_defaults(func=cmd_version)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    return int(ns.func(ns))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
