"""src/futur/cli.py -- canonical `futur` CLI entrypoint.

Phase 3 (base Python minimale) scope: prove a single, canonical, installable
entrypoint exists and works from a clean clone. Deliberately thin -- no
Truth Engine, no strategy code, no heavy imports at module load time (so
`futur --help` stays fast and doesn't depend on src.alpha20/src.institutional
being importable). Real subcommands (`validate`, `replay`, `experiment run`,
...) are for later phases to add, each importing what it needs lazily
inside its own function, not here.
"""
from __future__ import annotations

import argparse
from typing import Optional, Sequence

from futur import __version__


def _cmd_version(args: argparse.Namespace) -> int:
    print(f"futur {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="futur",
        description="futur trading system -- canonical CLI "
                    "(Phase 3: packaging skeleton only, no Truth Engine yet).",
    )
    sub = parser.add_subparsers(dest="command")

    p_version = sub.add_parser("version", help="print the installed futur version")
    p_version.set_defaults(func=_cmd_version)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
