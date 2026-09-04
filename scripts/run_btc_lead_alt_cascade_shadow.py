#!/usr/bin/env python3
"""
scripts/run_btc_lead_alt_cascade_shadow.py
─────────────────────────────────────────────────────────────────────────────
BTC_LEAD_ALT_CASCADE_V1 — Mode A (SIGNAL SHADOW) runner.

Alpha issu du candidat BTC_LEAD_ALT_CASCADE, VALIDÉ INDÉPENDAMMENT le
2026-09-03 (reports/edge_discovery/validation_2026-09/BTC_LEAD_ALT_CASCADE/
REPORT.md, verdict VALIDATED_FOR_FORWARD, recommended_next_step
FREEZE_AND_LAUNCH_SHADOW) et resté sans code : « validé » sans accumuler la
moindre preuve forward. Ce runner le met réellement en paper trading.

Réutilise sans les modifier : le détecteur figé (detector.py / dataset.py, qui
porte déjà la feature causale `btc_ret_30m`), l'univers figé
(configs/portfolio_v1_1_parallel_50.yaml). N'ajoute que la règle de choc BTC
causale (btc_lead_variant.py), reprise à l'identique de la spec du validateur.

Rapport à LIQ_CASCADE_REPEAT_V1 : ce n'est ni un remplacement ni une variante
du repeat-cascade — c'est un conditionnement DIFFÉRENT (choc BTC contemporain,
pas répétition same-symbol) sur le même flux d'événements LONG_CASCADE.
Chevauchement mesuré par le validateur : 22,45 % des événements du bras shock
appariés à ±5 min avec le ledger REPEAT_V1 (critère S5 <= 50 % passé). Même
risk_bucket (LIQUIDATION_FAMILY) et même correlation_family
(LIQ_CASCADE_DETECTOR) : c'est la couche portefeuille qui gère la
déduplication de risque, pas ce runner.

Idempotent (append-only, déduplication sur (event_time, symbol)) — sûr à
relancer à chaque cycle.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.provenance import spec_provenance, stamp_event_ids

from src.institutional.engines.liq_cascade.btc_lead_variant import (
    LOOKBACK_DAYS, MIN_PRIOR_EVENTS, SHOCK_QUANTILE, select_tradeable_btc_lead)
from src.institutional.engines.liq_cascade.dataset import build_event_dataset
from src.institutional.engines.liq_cascade.detector import CascadeConfig

ALPHA_ID = "BTC_LEAD_ALT_CASCADE_V1"
OUT_DIR = ROOT / "reports" / "live_alpha_lab" / ALPHA_ID
LEDGER = OUT_DIR / "decisions.parquet"
UNIVERSE_CONFIG = ROOT / "configs" / "portfolio_v1_1_parallel_50.yaml"
REGISTRY = ROOT / "configs" / "live_alpha_registry.yaml"
HORIZON = "fwd_4h"


def load_universe() -> list:
    """Univers FIGÉ — jamais dérivé d'un glob() sur data/ (bug d'universe-drift
    corrigé le 2026-08-30, cf tests/test_universe_drift_guard.py)."""
    return sorted(yaml.safe_load(UNIVERSE_CONFIG.read_text())["universe"])


def universe_hash(universe: list) -> str:
    return hashlib.sha256(",".join(sorted(universe)).encode()).hexdigest()[:16]


def check_registry_freeze(alpha_id: str) -> None:
    """Fail-closed : pas d'entrée figée dans le registre = pas d'écriture."""
    reg = yaml.safe_load(REGISTRY.read_text())
    entries = [a for a in reg["alphas"] if a["alpha_id"] == alpha_id]
    if not entries:
        raise RuntimeError(f"{alpha_id} absent de {REGISTRY} — refus de tourner sans entrée figée.")
    entry = entries[0]
    if entry.get("operational_status") not in ("SIGNAL_SHADOW", "EXECUTION_SHADOW"):
        raise RuntimeError(
            f"{alpha_id} operational_status={entry.get('operational_status')!r} — "
            "seul SIGNAL_SHADOW/EXECUTION_SHADOW peut écrire des décisions."
        )


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    universe = load_universe()
    uhash = universe_hash(universe)
    check_registry_freeze(ALPHA_ID)
    print(f"[{ALPHA_ID}] univers figé : {len(universe)} symboles, hash={uhash}", flush=True)

    # BTCUSDT doit être dans l'univers : dataset.py en dérive `btc_ret_30m`
    # (load_metrics("BTCUSDT") explicite), et la population exclut BTCUSDT
    # lui-même comme instrument tradé — les deux sont voulus.
    if "BTCUSDT" not in universe:
        raise RuntimeError("BTCUSDT absent de l'univers figé — btc_ret_30m ne serait pas dérivable.")

    ev = build_event_dataset(universe, CascadeConfig(), detector_fn=None)
    if ev.empty:
        print(f"[{ALPHA_ID}] aucun event — rien à écrire.")
        return 0

    runtime_symbols = set(ev["symbol"].unique())
    if not runtime_symbols.issubset(set(universe)):
        raise RuntimeError(
            f"UNIVERSE DRIFT DÉTECTÉ : symboles hors univers figé : "
            f"{runtime_symbols - set(universe)}"
        )

    tradeable = select_tradeable_btc_lead(ev)
    n_long_alt = int(((ev["kind"] == "LONG_CASCADE") & (ev["symbol"] != "BTCUSDT")).sum())
    print(f"[{ALPHA_ID}] {len(ev)} events bruts -> {n_long_alt} LONG_CASCADE alts -> "
          f"{len(tradeable)} shock (|btc_ret_30m| >= q{int(SHOCK_QUANTILE*100)} causal "
          f"{LOOKBACK_DAYS}j, >= {MIN_PRIOR_EVENTS} événements antérieurs)", flush=True)

    if tradeable.empty:
        print(f"[{ALPHA_ID}] rien de tradeable sur cette fenêtre.")
        return 0

    now = datetime.now(timezone.utc).isoformat()
    dec = tradeable[["event_time", "symbol", "kind", "btc_ret_30m", "btc_q90_365d",
                     "btc_shock_sign", "direction", "oi_drop_z", "px_ret_30m",
                     "n_events_sym_24h"]].copy()
    dec["engine"] = ALPHA_ID
    dec["horizon"] = HORIZON
    dec["universe_hash"] = uhash
    dec = stamp_event_ids(dec, ALPHA_ID, "event_time", "symbol")
    dec["decided_at"] = now

    for _k, _v in spec_provenance(ALPHA_ID).items():
        dec[_k] = _v
    dec["tier"] = "shadow"

    if LEDGER.exists():
        import pandas as pd
        old = pd.read_parquet(LEDGER)
        key_old = set(zip(old["event_time"], old["symbol"]))
        new_mask = [not ((et, sy) in key_old) for et, sy in zip(dec["event_time"], dec["symbol"])]
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
    (OUT_DIR / "run_state.json").write_text(json.dumps({
        "alpha_id": ALPHA_ID, "last_run": now, "universe_hash": uhash,
        "universe_size": len(universe), "n_decisions_total": len(out),
        "n_decisions_new": n_new, "mode": "A_SIGNAL_SHADOW",
        "shock_quantile": SHOCK_QUANTILE, "lookback_days": LOOKBACK_DAYS,
        "min_prior_events": MIN_PRIOR_EVENTS,
    }, indent=2))
    print(f"[{ALPHA_ID}] {n_new} nouvelles décisions écrites ({len(out)} total) -> {LEDGER}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
