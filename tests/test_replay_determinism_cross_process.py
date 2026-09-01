"""
tests/test_replay_determinism_cross_process.py — item P1.1 (phase CLOSE THE
EXECUTION LOOP), version FORTE : "Run A vs Run B -> hash identique" testé
avec deux VRAIS processus séparés, chacun avec un PYTHONHASHSEED différent.

Un test intra-processus (tests/test_portfolio_shadow_layer.py::
test_deterministic_replay_identical_hash_across_two_independent_runs) ne
peut PAS détecter une dépendance à l'ordre d'itération d'un `set` -- un
seul processus = un seul hash-seed = un seul ordre d'itération, même sans
le fix (portfolio.py::step, tri explicite des instruments). Confirmé
empiriquement : `list(set([...cinq symboles...]))` diffère réellement
selon PYTHONHASHSEED (5 seeds testés -> 5 ordres différents). Ce test
lance scripts/_replay_determinism_worker.py deux fois, avec des seeds
délibérément différents, et vérifie que le hash final est malgré tout
identique.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "_replay_determinism_worker.py"


def _run_with_seed(seed: str, out_dir: Path) -> str:
    import os
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = seed
    result = subprocess.run(
        [sys.executable, str(WORKER), str(out_dir)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"worker failed (seed={seed}): {result.stderr}"
    return result.stdout.strip()


def test_replay_identical_hash_across_two_processes_with_different_hash_seeds(tmp_path):
    hash_a = _run_with_seed("1", tmp_path / "run_a")
    hash_b = _run_with_seed("999999", tmp_path / "run_b")
    assert hash_a and hash_b
    assert hash_a == hash_b, (
        "le replay diverge entre deux processus avec des PYTHONHASHSEED différents -- "
        "probable régression de l'ordre d'itération d'un set/dict non trié"
    )
