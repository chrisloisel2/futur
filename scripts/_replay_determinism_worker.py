#!/usr/bin/env python3
"""
scripts/_replay_determinism_worker.py
─────────────────────────────────────────────────────────────────────────────
Worker interne pour tests/test_replay_determinism_cross_process.py (item
P1.1) : rejoue une séquence fixe d'aggregate()/step() et imprime le hash
SHA256 de l'état final. Lancé deux fois en SUBPROCESS SÉPARÉS avec des
PYTHONHASHSEED différents -- seule façon de vérifier qu'un vrai changement
de hash-seed processus (le scénario réel "deux runs séparés") ne fait pas
diverger le résultat, ce qu'un test intra-processus ne peut pas prouver
(un seul process = un seul hash-seed, donc un seul ordre d'itération de set,
même sans le fix).
"""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.intents import PortfolioIntent
from src.institutional.live_alpha_lab.marks import MarkQuote
import src.institutional.live_alpha_lab.portfolio as portfolio_mod
from src.institutional.live_alpha_lab.portfolio import aggregate, step
from src.institutional.live_alpha_lab.portfolio_config import PortfolioConfig


def main() -> int:
    out_dir = Path(sys.argv[1])
    portfolio_mod.PORTFOLIO_DIR = out_dir

    config = PortfolioConfig(name="TEST_MTM", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                             max_gross_exposure_fraction=10.0, max_per_asset_fraction=10.0)
    ts0 = pd.Timestamp("2026-09-01T00:00:00Z")
    ts1 = ts0 + pd.Timedelta(minutes=5)
    ts2 = ts1 + pd.Timedelta(minutes=5)
    prices = {(ts0, "BTCUSDT"): 100.0, (ts0, "ETHUSDT"): 50.0, (ts0, "SOLUSDT"): 20.0,
             (ts1, "BTCUSDT"): 103.0, (ts1, "ETHUSDT"): 48.0, (ts1, "SOLUSDT"): 22.0,
             (ts2, "BTCUSDT"): 97.0, (ts2, "ETHUSDT"): 52.0, (ts2, "SOLUSDT"): 19.0}

    def pure_mark(instrument, as_of=None):
        return MarkQuote(instrument=instrument, price=prices[(as_of, instrument)],
                         mark_source="TEST", mark_timestamp=as_of, mark_age_ms=0.0)

    portfolio_mod.get_mark = pure_mark

    def intent(instrument, frac, ts, direction="LONG"):
        return PortfolioIntent(
            alpha_id="A1", family="liquidation", risk_bucket="LIQUIDATION_FAMILY",
            correlation_family="FAM1", timestamp=ts, instrument=instrument, direction=direction,
            target_position_fraction=frac, confidence=1.0, horizon_hours=4.0,
            expiry=ts + pd.Timedelta(hours=4), multi_leg=False, leg_instrument_b=None,
        )

    intents_by_ts = {
        ts0: [intent("BTCUSDT", 0.5, ts0), intent("ETHUSDT", 0.3, ts0), intent("SOLUSDT", 0.2, ts0)],
        ts1: [intent("BTCUSDT", 0.2, ts1), intent("ETHUSDT", 0.5, ts1), intent("SOLUSDT", 0.3, ts1)],
        ts2: [intent("BTCUSDT", 0.0, ts2, direction="SHORT")],
    }

    state = None
    for ts in (ts0, ts1, ts2):
        agg = aggregate(intents_by_ts[ts], config, set(), as_of=ts)
        state = step("REPLAY_WORKER", config, agg, ts)

    blob = json.dumps(asdict(state), sort_keys=True, default=str)
    print(hashlib.sha256(blob.encode()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
