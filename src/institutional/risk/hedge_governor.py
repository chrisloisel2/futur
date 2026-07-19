"""
src/institutional/risk/hedge_governor.py
─────────────────────────────────────────────────────────────────────────────
HEDGE_GOVERNOR_V1 (Phase 35) — assurance de portefeuille, PAS un moteur short.

Le hedge réduit le beta d'une exposition LONG existante en régime hostile. Ce
n'est jamais un pari baissier autonome.

Garde-fous absolus (cohérent SHORT_REJECTED) :
    SHORT_DIRECTIONAL_ENABLED = False
    NAKED_SHORT_ALLOWED       = False
    HEDGE_SHORT_ALLOWED       = True   (uniquement lié à un long)

Un hedge short est autorisé seulement si : long_exposure > 0, taille bornée par
l'exposition à couvrir, fermé quand le long disparaît.

⚠️ DD en FENÊTRE GLISSANTE (corrige le ratchet monotone du RiskGovernor qui se
figeait en cash sur un run multi-année).

Statut autorisé : PAPER_ONLY.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional

SHORT_DIRECTIONAL_ENABLED = False
NAKED_SHORT_ALLOWED = False
HEDGE_SHORT_ALLOWED = True

HEDGE_STATES = ("NO_HEDGE", "REDUCE_LONGS", "BTC_PARTIAL_HEDGE",
                "ETH_PARTIAL_HEDGE", "CASH_ONLY", "KILL")


@dataclass
class HedgeConfig:
    dd_window_bars: int = 720          # fenêtre glissante (~30 j) pour le DD
    dd_hedge_light: float = 0.010      # -1.0% → hedge 0.25
    dd_hedge_mid: float = 0.015        # -1.5% → hedge 0.40
    dd_hedge_heavy: float = 0.020      # -2.0% → hedge 0.60
    dd_cash: float = 0.025             # -2.5% → cash only
    dd_kill: float = 0.030             # -3.0% → kill
    ratio_light: float = 0.25
    ratio_mid: float = 0.40
    ratio_heavy: float = 0.60
    max_hedge_cap: float = 0.30        # 30% du capital max en hedge
    bear_regimes: tuple = ("HARD_BEAR", "PANIC", "DELEVERAGING", "NO_LONG")


@dataclass
class HedgeDecision:
    state: str
    hedge_asset: Optional[str]      # "BTCUSDT" | "ETHUSDT" | None
    hedge_notional: float           # USD à shorter en hedge (≥ 0)
    hedge_ratio: float
    drawdown: float
    reason: str

    def __post_init__(self):
        assert self.state in HEDGE_STATES, self.state
        # garde-fou : jamais de short non lié / non borné
        assert self.hedge_notional >= 0.0


class HedgeGovernorV1:
    def __init__(self, config: Optional[HedgeConfig] = None):
        assert HEDGE_SHORT_ALLOWED and not SHORT_DIRECTIONAL_ENABLED and not NAKED_SHORT_ALLOWED
        self.config = config or HedgeConfig()
        self._equity: Deque[float] = deque(maxlen=self.config.dd_window_bars)

    def _rolling_dd(self, equity: float) -> float:
        self._equity.append(equity)
        peak = max(self._equity)
        return (equity - peak) / max(peak, 1e-9)

    def _ratio_for_dd(self, dd: float, regime: str) -> float:
        cfg = self.config
        if regime in cfg.bear_regimes:
            return cfg.ratio_heavy
        if dd <= -cfg.dd_hedge_heavy:
            return cfg.ratio_heavy
        if dd <= -cfg.dd_hedge_mid:
            return cfg.ratio_mid
        if dd <= -cfg.dd_hedge_light:
            return cfg.ratio_light
        return 0.0

    def decide(
        self,
        *,
        equity: float,
        long_exposure: float,           # USD long total
        beta_to_btc: float = 1.0,
        beta_to_eth: float = 0.0,
        btc_regime: str = "UNKNOWN",
        vol_spike: bool = False,
        corr_spike: bool = False,
        liquidity_ok: bool = True,
    ) -> HedgeDecision:
        cfg = self.config
        dd = self._rolling_dd(equity)

        # pas de long → jamais de hedge (interdiction de short nu)
        if long_exposure <= 0:
            return HedgeDecision("NO_HEDGE", None, 0.0, 0.0, dd, "no_long_exposure")

        # kill : survie
        if dd <= -cfg.dd_kill:
            return HedgeDecision("KILL", None, 0.0, 0.0, dd, f"drawdown {dd:.2%} ≤ -{cfg.dd_kill:.1%}")
        # cash : plus de nouveaux longs, on déleverage
        if dd <= -cfg.dd_cash:
            return HedgeDecision("CASH_ONLY", None, 0.0, 0.0, dd, f"drawdown {dd:.2%} → cash")

        ratio = self._ratio_for_dd(dd, btc_regime)
        if ratio <= 0.0 and not (vol_spike or corr_spike):
            return HedgeDecision("NO_HEDGE", None, 0.0, 0.0, dd, "régime sain")
        if ratio <= 0.0:
            ratio = cfg.ratio_light  # spike vol/corr → hedge léger préventif

        # actif de hedge = celui qui porte le plus de beta
        use_btc = beta_to_btc >= beta_to_eth
        hedge_asset = "BTCUSDT" if use_btc else "ETHUSDT"
        beta = beta_to_btc if use_btc else beta_to_eth
        beta_adj_long = long_exposure * max(beta, 0.0)

        # sizing borné : jamais > exposition à couvrir, ni > cap capital
        hedge_notional = min(beta_adj_long * ratio, cfg.max_hedge_cap * equity, long_exposure)
        hedge_notional = max(0.0, hedge_notional)
        if hedge_notional <= 0:
            return HedgeDecision("REDUCE_LONGS", None, 0.0, ratio, dd, "beta nul → réduire longs")

        state = "BTC_PARTIAL_HEDGE" if use_btc else "ETH_PARTIAL_HEDGE"
        reason = f"dd {dd:.2%} regime={btc_regime} ratio={ratio:.0%} hedge {hedge_asset}"
        return HedgeDecision(state, hedge_asset, hedge_notional, ratio, dd, reason)
