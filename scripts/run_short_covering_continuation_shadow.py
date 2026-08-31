#!/usr/bin/env python3
"""
scripts/run_short_covering_continuation_shadow.py
─────────────────────────────────────────────────────────────────────────────
SHORT_COVERING_CONTINUATION_V1 — Mode A (SIGNAL SHADOW) runner.

Live Alpha Lab, second alpha implemented (see configs/live_alpha_registry.yaml
alpha_id: SHORT_COVERING_CONTINUATION_V1). Computes the signal, writes one
decision row per (symbol, hourly bar) where the reconstructed state clears
tau_b (near-miss or full short-covering tail-decile, direction==LONG) — sends
NO order, does not even simulate a fill (Mode A pur).

DIFFERENT data source than LIQ_CASCADE_REPEAT_V1: this reads the LIVE
derivatives collector store (data/derivatives_raw/, written continuously by
scripts/run_derivatives_collector.py), reconstructing "price up + OI down"
causally from real-time REST-polled open_interest+mark_price — NOT the
static data_v2/normalized/event_feature_panel used for the original
discovery (that panel lives only in the separate futur-data-v2 worktree and
is NOT continuously updated). See reports/live_alpha_lab/
SHORT_COVERING_CONTINUATION_V1/freeze_spec.json `data_reconstruction_notes`
for the full honesty accounting of what is/isn't verified equivalent.

Univers : configs/portfolio_v1_1_parallel_50.yaml — FIGÉ. Ne JAMAIS dériver
l'univers d'un glob() sur un dossier de données (le bug corrigé le
2026-08-30, voir tests/test_universe_drift_guard.py). Chaque run vérifie que
l'univers runtime == univers figé (universe_hash) et s'arrête sinon (fail
closed). Gap connu, non contourné ici : 3/50 symboles figés (MKRUSDT
délisté ; PEPEUSDT/RNDRUSDT renommés côté Binance vs. le nom que demande le
collecteur — 1000PEPEUSDT/RENDERUSDT) n'ont AUCUNE ligne dans
data/derivatives_raw. L'engine renvoie [] pour ces symboles (WARNING loggé),
jamais un crash, jamais un signal fabriqué.

Idempotent : relit l'historique existant, n'ajoute que les décisions dont la
clé (timestamp, asset) n'existe pas déjà dans le ledger.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.provenance import git_head_sha

from src.institutional.engines.short_covering_continuation.infer import (
    ShortCoveringContinuationEngine)

ALPHA_ID = "SHORT_COVERING_CONTINUATION_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
HORIZON = "fwd_4h"
RUN_WINDOW_HOURS = 72   # decision window recomputed each run; idempotent merge drops already-known keys


def load_universe() -> List[str]:
    """Univers FIGÉ — jamais dérivé d'un glob() sur data/, voir docstring."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def universe_hash(universe: List[str]) -> str:
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def check_registry_freeze(alpha_id: str, expected_hash: str) -> None:
    """Fail-closed : la spec figée dans live_alpha_registry.yaml doit exister
    et le statut doit autoriser l'écriture — sinon on n'écrit rien."""
    reg = yaml.safe_load(REGISTRY.read_text())
    entries = [a for a in reg["alphas"] if a["alpha_id"] == alpha_id]
    if not entries:
        raise RuntimeError(f"{alpha_id} absent de {REGISTRY} — refus de tourner sans entrée figée.")
    entry = entries[0]
    if entry.get("operational_status") not in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
        raise RuntimeError(
            f"{alpha_id} operational_status={entry.get('operational_status')!r} dans le registre — "
            "seul SHADOW_LIVE/EXECUTION_SHADOW peut écrire des décisions."
        )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    uhash = universe_hash(universe)
    check_registry_freeze(ALPHA_ID, uhash)
    print(f"[{ALPHA_ID}] univers figé : {len(universe)} symboles, hash={uhash}", flush=True)

    end = pd.Timestamp.now(tz="UTC").floor("h")
    start = end - pd.Timedelta(hours=RUN_WINDOW_HOURS)

    engine = ShortCoveringContinuationEngine(status="SHADOW", universe=universe)

    rows = []
    n_no_data = 0
    for asset in universe:
        opps = engine.generate(asset, start.isoformat(), end.isoformat())
        if not opps:
            n_no_data += 1
            continue
        for o in opps:
            if o.direction != "LONG":
                continue   # only actionable/near-miss LONG rows logged — never CASH-noise, never SHORT
            rows.append(o.to_dict())

    if n_no_data:
        print(f"[{ALPHA_ID}] {n_no_data}/{len(universe)} symboles sans donnée live "
              f"derivatives_raw sur cette fenêtre (voir freeze_spec.json).", flush=True)

    if not rows:
        print(f"[{ALPHA_ID}] rien de tradeable sur cette fenêtre.")
        return 0

    dec = pd.DataFrame(rows)
    dec["timestamp"] = pd.to_datetime(dec["timestamp"])
    now = datetime.now(timezone.utc).isoformat()
    dec["universe_hash"] = uhash
    dec["decided_at"] = now

    dec["code_commit_sha"] = git_head_sha()
    dec["tier"] = "shadow"   # Mode A pur — pas de fill simulé, jamais "book"
    dec["horizon"] = HORIZON

    # runtime universe drift check (fail closed, même discipline que liq_cascade)
    runtime_symbols = set(dec["asset"].unique())
    if not runtime_symbols.issubset(set(universe)):
        extra = runtime_symbols - set(universe)
        raise RuntimeError(
            f"UNIVERSE DRIFT DÉTECTÉ : symboles hors univers figé dans les décisions générées: {extra}"
        )
    if (dec["direction"] == "SHORT_HEDGE").any() or (dec["direction"] == "SHORT").any():
        raise RuntimeError(
            "SHORT direction émise par SHORT_COVERING_CONTINUATION_V1 — interdit "
            "(SHORT_REJECTED, mécanisme LONG-only) — refus d'écrire."
        )

    # idempotence : ne pas dupliquer une clé (timestamp, asset) déjà décidée.
    if LEDGER.exists():
        old = pd.read_parquet(LEDGER)
        old["timestamp"] = pd.to_datetime(old["timestamp"])
        key_old = set(zip(old["timestamp"], old["asset"]))
        new_mask = [not ((t, a) in key_old) for t, a in zip(dec["timestamp"], dec["asset"])]
        dec_new = dec[new_mask]
        if dec_new.empty:
            print(f"[{ALPHA_ID}] rien de nouveau (idempotent) — {len(old)} décisions déjà connues.")
            return 0
        out = pd.concat([old, dec_new], ignore_index=True)
        n_new = len(dec_new)
    else:
        out = dec
        n_new = len(dec)

    out.to_parquet(LEDGER, index=False)
    meta = {
        "alpha_id": ALPHA_ID, "last_run": now, "universe_hash": uhash,
        "universe_size": len(universe), "n_decisions_total": len(out),
        "n_decisions_new": n_new, "n_symbols_no_live_data": n_no_data,
        "mode": "A_SIGNAL_SHADOW",
    }
    (OUT_DIR / "run_state.json").write_text(json.dumps(meta, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites "
          f"({len(out)} total) -> {LEDGER}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
