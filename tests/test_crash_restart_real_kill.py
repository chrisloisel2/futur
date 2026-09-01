"""
tests/test_crash_restart_real_kill.py — item P0.3 (phase OPERATIONAL
HARDENING) : scénario A explicitement demandé -- submit -> PARTIALLY_FILLED
-> vrai SIGKILL du processus -> restart -> vérifie filled_quantity
conservée, remaining correcte, aucune double fee, aucun fill/ordre dupliqué.

Un test qui appelle juste load_state()/save_state() dans le MÊME process
Python ne prouve pas la même chose qu'un vrai kill -9 par le kernel --
celui-ci utilise un vrai sous-processus tué en plein milieu de sa séquence
de steps.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "_crash_restart_worker.py"


def _launch(out_dir: Path, n_steps: int):
    return subprocess.Popen(
        [sys.executable, str(WORKER), str(out_dir), str(n_steps)],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def _read_state(out_dir: Path) -> dict:
    p = out_dir / "CRASH_TEST" / "state.json"
    return json.loads(p.read_text())


def test_real_sigkill_mid_partial_fill_sequence_then_restart(tmp_path):
    ref_dir = tmp_path / "reference"
    kill_dir = tmp_path / "killed"

    # run de référence, jamais interrompu -- sert de vérité pour comparer
    proc_ref = _launch(ref_dir, 10)
    out_ref, err_ref = proc_ref.communicate(timeout=30)
    assert proc_ref.returncode == 0, err_ref
    ref_state = _read_state(ref_dir)

    # run interrompu : lit stdout ligne par ligne, tue le process avec un
    # VRAI SIGKILL dès que le step 3 est vu -- en plein milieu de la
    # séquence de remplissage partiel (position à 400/1000 sur 10 steps).
    proc_kill = _launch(kill_dir, 10)
    killed_at_step = None
    try:
        for line in proc_kill.stdout:
            line = line.strip()
            if line.startswith("STEP 3 DONE"):
                killed_at_step = line
                os.kill(proc_kill.pid, signal.SIGKILL)
                break
    finally:
        proc_kill.wait(timeout=10)

    assert killed_at_step is not None, "le process s'est terminé avant le step 3 -- test non concluant"
    assert proc_kill.returncode != 0   # confirme que le process a bien été tué, pas terminé normalement

    # état persisté après le kill : doit être chargeable (pas de JSON tronqué,
    # cf le fix d'écriture atomique P0.3) et montrer une progression PARTIELLE
    state_after_kill = _read_state(kill_dir)
    qty_after_kill = state_after_kill["positions"]["BTCUSDT"]["quantity"]
    assert 0 < qty_after_kill < 1000.0, f"qty après kill = {qty_after_kill}, attendu partiel (0, 1000)"
    n_orders_after_kill = len(state_after_kill["orders"])
    total_fees_after_kill = state_after_kill["cumulative_fees_usd"]

    # redémarrage : relance le MÊME worker sur le MÊME répertoire -- reprend
    # depuis l'état persisté (voir _crash_restart_worker.py, ne rejoue pas
    # les steps déjà faits, ne va jamais en arrière dans le temps)
    proc_resume = _launch(kill_dir, 10)
    out_resume, err_resume = proc_resume.communicate(timeout=30)
    assert proc_resume.returncode == 0, err_resume

    final_state = _read_state(kill_dir)
    final_qty = final_state["positions"]["BTCUSDT"]["quantity"]
    final_fees = final_state["cumulative_fees_usd"]
    final_n_orders = len(final_state["orders"])

    # === les assertions explicitement demandées par le scénario A ===
    assert final_qty == ref_state["positions"]["BTCUSDT"]["quantity"] == 1000.0   # filled_quantity conservée, convergence identique
    assert final_fees == ref_state["cumulative_fees_usd"]                          # aucune double fee sur la quantité déjà remplie
    # aucun fill dupliqué : le total de fills correspond exactement au
    # nombre d'ordres FILLED/PARTIALLY_FILLED réellement exécutés, pas plus
    n_real_fills = sum(1 for o in final_state["orders"] if o["filled_quantity"] > 0)
    assert len(final_state["fills"]) == n_real_fills
    # les ordres du run interrompu sont TOUJOURS là (jamais rejoués/supprimés)
    # plus les nouveaux du redémarrage -- pas de duplication du même order_id
    order_ids = [o["order_id"] for o in final_state["orders"]]
    assert len(order_ids) == len(set(order_ids)), "order_id dupliqué détecté"
    assert final_n_orders > n_orders_after_kill   # le redémarrage a bien continué, pas juste relu l'ancien état

    # equity/realized/unrealized identiques à la référence non-interrompue
    assert final_state["equity_curve"][-1]["equity"] == ref_state["equity_curve"][-1]["equity"]
