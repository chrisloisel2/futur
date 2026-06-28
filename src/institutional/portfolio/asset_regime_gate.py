"""
src/institutional/portfolio/asset_regime_gate.py
─────────────────────────────────────────────────────────────────────────────
Asset Regime Gate (Phase 47) — réparation prioritaire du LONG_BOOK.

Le DD structurel vient d'alts longs tenus pendant des rotations où BTC est
BULL mais l'alt non. On gate donc CHAQUE actif par son PROPRE régime EN PLUS
du régime macro BTC :

    long autorisé(asset)  ⟺  btc_regime ∈ {BULL,RECOVERY}
                              ET asset_regime ∈ {BULL,RECOVERY}

100% causal (réutilise RegimeGate.compute_regime_series par actif). Le flip
exit devient ASSET-LEVEL : on sort un long si BTC OU l'actif devient hostile.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from src.institutional.portfolio.regime_gate import RegimeGate, RegimeGateConfig

ALLOWED = {"BULL", "RECOVERY"}
HOSTILE = {"BEAR", "CRASH", "UNKNOWN"}


@dataclass
class AssetRegimeDecision:
    asset: str
    btc_regime: str
    asset_regime: str
    permission: str          # ALLOW_LONG / REDUCE_LONG / BLOCK_LONG
    size_mult: float


class AssetRegimeGate:
    """Régime macro BTC + régime par actif (causal)."""

    def __init__(self, config: Optional[RegimeGateConfig] = None):
        self.config = config or RegimeGateConfig()
        self._btc = RegimeGate(self.config)
        self._asset_gates: Dict[str, RegimeGate] = {}

    def fit(self, prices: Dict[str, pd.Series]) -> "AssetRegimeGate":
        if "BTCUSDT" in prices:
            self._btc.compute_regime_series(prices["BTCUSDT"])
        for a, s in prices.items():
            g = RegimeGate(self.config)
            g.compute_regime_series(s)
            self._asset_gates[a] = g
        return self

    def decide_long(self, asset: str, ts: pd.Timestamp) -> AssetRegimeDecision:
        btc = self._btc.decide_at(ts).btc_regime
        ag = self._asset_gates.get(asset)
        areg = ag.decide_at(ts).btc_regime if ag is not None else "UNKNOWN"
        # macro BTC requis
        if btc not in ALLOWED:
            return AssetRegimeDecision(asset, btc, areg, "BLOCK_LONG", 0.0)
        if areg in ALLOWED:
            return AssetRegimeDecision(asset, btc, areg, "ALLOW_LONG", 1.0)
        if areg == "NEUTRAL":
            return AssetRegimeDecision(asset, btc, areg, "REDUCE_LONG", 0.5)
        return AssetRegimeDecision(asset, btc, areg, "BLOCK_LONG", 0.0)

    def should_exit_long(self, asset: str, ts: pd.Timestamp) -> bool:
        """Flip exit ASSET-LEVEL : sortir si BTC OU l'actif devient hostile."""
        btc = self._btc.decide_at(ts).btc_regime
        ag = self._asset_gates.get(asset)
        areg = ag.decide_at(ts).btc_regime if ag is not None else "UNKNOWN"
        return (btc in HOSTILE) or (areg in HOSTILE)
