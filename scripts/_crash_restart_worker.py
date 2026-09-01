#!/usr/bin/env python3
"""
scripts/_crash_restart_worker.py
─────────────────────────────────────────────────────────────────────────────
Worker interne pour tests/test_crash_restart_real_kill.py (item P0.3) :
exécute jusqu'à N steps aggregate()/step() vers une cible plafonnée par la
liquidité (fills partiels garantis sur plusieurs steps, cf orders.py),
imprime "STEP <i> DONE filled=<total>" après CHAQUE step (flush immédiat)
pour que le test puisse synchroniser un vrai SIGKILL en plein milieu de la
séquence -- pas un simple appel Python interne, un vrai process tué par
le kernel.

Relançable tel quel sur le même out_dir : reprend depuis l'état persisté
(load_state), continue vers la MÊME cible -- aucune notion d'ordre "en
attente" à restaurer (cf orders.py : chaque step recalcule le delta contre
la position courante, qui reflète déjà tout fill partiel précédent).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.live_alpha_lab.intents import PortfolioIntent
from src.institutional.live_alpha_lab.marks import MarkQuote
import src.institutional.live_alpha_lab.portfolio as portfolio_mod
from src.institutional.live_alpha_lab.portfolio import aggregate, load_state, step
from src.institutional.live_alpha_lab.portfolio_config import PortfolioConfig

PORTFOLIO_NAME = "CRASH_TEST"
TS0 = pd.Timestamp("2026-09-01T00:00:00Z")


def main() -> int:
    out_dir = Path(sys.argv[1])
    n_steps = int(sys.argv[2])
    portfolio_mod.PORTFOLIO_DIR = out_dir

    config = PortfolioConfig(name="TEST_MTM", capital_eur=100_000,
                             family_budget_fraction={"LIQUIDATION_FAMILY": 1.0},
                             max_gross_exposure_fraction=10.0, max_per_asset_fraction=10.0)

    # liquidity_notional=5_000_000 -> plafond = 0.002*5_000_000/100 = 100
    # unités/step ; target=1000 unités -> converge en ~10 steps pile, comme
    # tests/test_portfolio_shadow_layer.py::test_multiple_partial_fills_...
    def capped_mark(instrument, as_of=None):
        return MarkQuote(instrument=instrument, price=100.0, mark_source="TEST",
                         mark_timestamp=TS0, mark_age_ms=0.0, liquidity_notional=5_000_000.0)

    portfolio_mod.get_mark = capped_mark
    # _latest_funding_rate lit data/derivatives_raw RÉEL (pas mocké comme
    # get_mark) -- pour un test déterministe entre deux VRAIS process lancés
    # à quelques secondes d'écart en wall-clock réel, le collecteur en
    # production peut avoir écrit de nouveaux fichiers entre-temps. Fixé à
    # une valeur constante ici : ce test vérifie la conservation
    # quantity/fee/fills/orders à travers un kill, pas le funding.
    portfolio_mod._latest_funding_rate = lambda symbol, as_of: 0.0

    intent = PortfolioIntent(
        alpha_id="A1", family="liquidation", risk_bucket="LIQUIDATION_FAMILY",
        correlation_family="FAM1", timestamp=TS0, instrument="BTCUSDT", direction="LONG",
        target_position_fraction=1.0, confidence=1.0, horizon_hours=4.0,
        expiry=TS0 + pd.Timedelta(hours=4), multi_leg=False, leg_instrument_b=None,
    )

    # reprise après kill : ne JAMAIS rejouer un as_of <= au dernier step
    # persisté (irait "en arrière" dans le temps -- funding négatif, etc.)
    # -- repart du nombre de steps déjà dans l'equity_curve, pas de i=0.
    existing = load_state(PORTFOLIO_NAME, config.capital_eur)
    start_i = len(existing.equity_curve)

    for i in range(start_i, start_i + n_steps):
        ts = TS0 + pd.Timedelta(seconds=i)   # avance à chaque step -- as_of distinct, order_id jamais réutilisé
        agg = aggregate([intent], config, set(), as_of=ts)
        state = step(PORTFOLIO_NAME, config, agg, ts)
        filled = list(state.positions.values())[0]["quantity"] if state.positions else 0.0
        print(f"STEP {i} DONE filled={filled}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
