#!/usr/bin/env python3
"""
scripts/run_alpha20_gate.py
─────────────────────────────────────────────────────────────────────────────
Gate forward ALPHA_20, exécuté après chaque rebalance (timer 8 h).

  1. forward_gate par INTERVALLE : Δ cumuls Mongo ≡ Σ événements ledger
     (≤ 0,01 USDT), chaîne de hash valide, un événement par fait ;
  2. état persistant reports/alpha20/gate_state.json + événement
     reconciliation dans le ledger ;
  3. RÈGLE MÉCANIQUE DU TAG (ordre R0, 2026-07-20) : quand
     consecutive_ok ≥ 3 avec intégrité parfaite, pose le tag git
     `alpha20-r0-ledger-trusted` (une seule fois) et le pousse.
     Aucun humain dans la boucle : la règle était pré-déclarée.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alpha20.accounting import event_ledger  # noqa: E402
from src.alpha20.contracts import LedgerEvent  # noqa: E402
from src.alpha20.validation.live_reconciliation import forward_gate  # noqa: E402

OUT = ROOT / "reports" / "alpha20"
TAG = "alpha20-r0-ledger-trusted"
TAG_THRESHOLD = 3


def tag_exists() -> bool:
    r = subprocess.run(["git", "tag", "-l", TAG], cwd=ROOT,
                       capture_output=True, text=True)
    return TAG in r.stdout.split()


def place_tag(gate: dict) -> bool:
    msg = (f"R0 ledger trusted — {gate['consecutive_ok']} intervalles "
           f"consécutifs réconciliés ≤ {gate['gate_usdt']} USDT, chaîne OK, "
           f"1 événement/fait (règle mécanique du 2026-07-20)")
    if subprocess.run(["git", "tag", "-a", TAG, "-m", msg], cwd=ROOT).returncode:
        return False
    subprocess.run(["git", "push", "origin", TAG], cwd=ROOT,
                   capture_output=True, timeout=60)
    return True


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    gate = forward_gate()
    now = datetime.now(timezone.utc).isoformat()
    tagged = tag_exists()
    state = {"updated_at": now, "gate": gate, "tag": TAG,
             "tag_threshold": TAG_THRESHOLD, "tagged": tagged}
    if (not tagged and gate.get("consecutive_ok", 0) >= TAG_THRESHOLD
            and gate["integrity"]["chain_ok"]
            and gate["integrity"]["one_event_per_fact"]):
        state["tagged"] = place_tag(gate)
        state["tagged_at"] = now
    (OUT / "gate_state.json").write_text(json.dumps(state, indent=2))
    event_ledger.append([LedgerEvent(
        ts=now, kind="reconciliation", sleeve="portfolio", venue="offchain",
        amount_usdt=0.0, ref="forward_gate",
        meta={"status": gate["status"], "passed": gate["passed"],
              "consecutive_ok": gate.get("consecutive_ok", 0)})])
    print(f"[alpha20 gate] {gate['status']} | passed={gate['passed']} | "
          f"consécutifs OK={gate.get('consecutive_ok', 0)}/{TAG_THRESHOLD} | "
          f"tagged={state['tagged']}", flush=True)


if __name__ == "__main__":
    main()
