#!/usr/bin/env python3
"""
train.py — Point d'entrée unique du training
============================================

Centralise les différents profils d'entraînement derrière un seul fichier.

Usage canonique :
    python train.py --data data/bundle_btc/features_merged.parquet --mode combined
    python train.py pipeline --data data/BTCUSD_1h_features.csv --mode long
    python train.py 1m --data data/bundle_btc/features_merged.parquet --mode combined --wf
    python train.py local --data data/BTCUSD_1h_features.csv

Profils disponibles :
    pipeline  : pipeline 1h canonique prod
    1m        : pipeline natif 1 minute (R&D)
    local     : trainer local legacy
"""
from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from typing import Iterable, Tuple

from core.runtime.profiles import format_train_help, get_training_profile, resolve_profile_and_args
from core.settings import configure_project_imports


configure_project_imports()


def print_help() -> None:
    print(format_train_help())


@contextmanager
def patched_argv(argv0: str, forwarded_args: list[str]):
    previous = sys.argv[:]
    sys.argv = [argv0, *forwarded_args]
    try:
        yield
    finally:
        sys.argv = previous


def run_profile(profile: str, forwarded_args: list[str]) -> int:
    module_name = get_training_profile(profile).module_name
    module = importlib.import_module(module_name)
    entrypoint = getattr(module, "main", None)
    if entrypoint is None:
        raise RuntimeError(f"Entrypoint `main()` introuvable dans {module_name}")

    with patched_argv(module_name, forwarded_args):
        entrypoint()
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    profile, forwarded_args = resolve_profile_and_args(list(argv if argv is not None else sys.argv[1:]))
    if profile == "__help__":
        print_help()
        return 0
    return run_profile(profile, forwarded_args)


if __name__ == "__main__":
    raise SystemExit(main())
