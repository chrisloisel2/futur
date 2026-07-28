"""src/futur/cli.py -- canonical `futur` CLI entrypoint.

Phase 3 (base Python minimale) scope: prove a single, canonical, installable
entrypoint exists and works from a clean clone. Phase 4 adds exactly two
real subcommands (`truth replay`, `truth validate`) -- everything else
(`experiment run`, strategy commands, ...) is for later phases. Both new
subcommands import src.futur.truth lazily, inside their own function, so
`futur --help`/`futur version` still don't pay for it.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from futur import __version__


def _cmd_version(args: argparse.Namespace) -> int:
    print(f"futur {__version__}")
    return 0


def _print_help_and_succeed(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return 0


def _cmd_truth_replay(args: argparse.Namespace) -> int:
    """Replay a JSONL event fixture and print a deterministic summary.
    Only InvariantViolation is caught here and turned into a clean,
    non-zero-exit message -- anything else (a malformed fixture file, a
    missing path) is left to propagate as a normal Python traceback rather
    than swallowed behind a blanket `except Exception`."""
    from src.futur.truth.invariants import InvariantViolation
    from src.futur.truth.replay import replay_file

    try:
        _engine, summary = replay_file(args.fixture)
    except InvariantViolation as exc:
        print(f"INVALID -- invariant violation: {exc}", file=sys.stderr)
        return 1

    print(f"events replayed:   {summary.n_events}")
    print(f"final cash:        {summary.final_cash}")
    print(f"final NAV:         {summary.final_nav}")
    print(f"final ledger hash: {summary.final_ledger_hash}")
    print(f"spot positions:    {summary.spot_positions}")
    print(f"perp positions:    {summary.perp_positions}")
    return 0


def _cmd_truth_validate(args: argparse.Namespace) -> int:
    """Replay a fixture purely to check invariants -- exits non-zero the
    moment any invariant is violated, prints nothing on success but VALID."""
    from src.futur.truth.invariants import InvariantViolation
    from src.futur.truth.replay import replay_file

    try:
        replay_file(args.fixture)
    except InvariantViolation as exc:
        print(f"INVALID -- invariant violation: {exc}", file=sys.stderr)
        return 1
    print("VALID -- all invariants held throughout replay")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="futur",
        description="futur trading system -- canonical CLI "
                    "(Phase 4: Truth Engine replay/validate only).",
    )
    sub = parser.add_subparsers(dest="command")

    p_version = sub.add_parser("version", help="print the installed futur version")
    p_version.set_defaults(func=_cmd_version)

    p_truth = sub.add_parser("truth", help="Truth Engine: replay and validate event fixtures")
    p_truth.set_defaults(func=lambda args: _print_help_and_succeed(p_truth))
    truth_sub = p_truth.add_subparsers(dest="truth_command")

    p_truth_replay = truth_sub.add_parser(
        "replay", help="replay a JSONL event fixture and print a summary")
    p_truth_replay.add_argument("fixture", help="path to a JSONL event fixture")
    p_truth_replay.set_defaults(func=_cmd_truth_replay)

    p_truth_validate = truth_sub.add_parser(
        "validate", help="replay a fixture, exit non-zero on any invariant violation")
    p_truth_validate.add_argument("fixture", help="path to a JSONL event fixture")
    p_truth_validate.set_defaults(func=_cmd_truth_validate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
