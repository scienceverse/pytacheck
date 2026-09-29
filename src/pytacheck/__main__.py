"""``python -m pytacheck`` runs the command line (the same as ``python -m metacheck``)."""

from metacheck.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
