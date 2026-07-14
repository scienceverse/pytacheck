"""Command-line entry point for the Pytacheck service."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pytacheck")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="start the Pytacheck API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=2005)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Parse CLI arguments and start Uvicorn only for the serve command."""

    args = _parser().parse_args(argv)
    if args.command == "serve":
        uvicorn.run(
            "pytacheck.api:create_app",
            factory=True,
            host=args.host,
            port=args.port,
        )


if __name__ == "__main__":
    main()
