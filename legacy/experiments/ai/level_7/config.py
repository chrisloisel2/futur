"""
level_7/config.py — CONFIGURATION ASYMÉTRIQUE DU RISQUE PAR CÔTÉ
==================================================================

Long et short n'ont PAS les mêmes paramètres de risque.

Asymétries connues du marché crypto :
  - Les baisses sont plus abruptes → stop plus serré pour le short
  - La volatilité des shorts est plus élevée → taille de position réduite
  - Le coût de portage short est supérieur → objectif de profit plus élevé
  - Le drawdown court plus vite en short → drawdown daily limit plus strict

Ces différences sont documentées, justifiées, et hardcodées.
Pas de config yaml pour ces valeurs — elles font partie du modèle de risque.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RiskConfig:
    """
    Configuration complète du risque pour un côté (long ou short).

    Tous les pourcentages sont en décimales (0.01 = 1%).
    Tous les paramètres sont indépendants par côté.
    """

    side: str  # "long" ou "short"

    # ── Taille de position ────────────────────────────────────────────────────
    position_size_pct: float = 0.02    # taille en % du capital par trade
    max_open_positions: int = 1        # jamais > 1 position simultanée par côté

    # ── Stop loss / Take profit ───────────────────────────────────────────────
    stop_loss_pct: float    = 0.015    # stop en % du prix d'entrée
    take_profit_pct: float  = 0.025    # TP en % du prix d'entrée
    risk_reward_ratio: float = 1.5    # TP = stop * rr (override TP si != None)
    use_atr_stop: bool      = True     # stop ATR-adaptatif si disponible

    # ── Gestion du capital dynamique ─────────────────────────────────────────
    kelly_fraction: float = 0.25       # fraction de Kelly à appliquer (safety)
    max_position_pct: float = 0.05     # jamais > 5% du capital sur un seul trade

    # ── Cooldown ─────────────────────────────────────────────────────────────
    cooldown_bars: int = 3             # bars minimum entre deux trades
    cooldown_after_loss: int = 6       # bars de cooldown supplémentaires après perte

    # ── Drawdown / Losses consécutives ───────────────────────────────────────
    max_consecutive_losses: int = 4    # stoppe après N pertes consécutives
    max_daily_drawdown_pct: float = 0.03  # stoppe si -3% sur la journée
    max_total_drawdown_pct: float = 0.10  # stoppe si -10% depuis le pic

    # ── Edge filter ──────────────────────────────────────────────────────────
    min_abs_edge: float  = 0.45        # probabilité minimum pour déclencher
    min_scale: float     = 0.45        # scaling minimum sur la taille de position

    # ── Volatilité de référence ───────────────────────────────────────────────
    rv_key: str = "rv_24"              # colonne de réalised vol dans le DataFrame
    rv_max: float = 0.08               # stoppe si vol > 8% / 24h (crises)

    # ── Limites journalières ─────────────────────────────────────────────────
    max_trades_per_day: int = 10       # limite de trades par jour


def make_long_risk_config(**overrides) -> RiskConfig:
    """
    Retourne la configuration de risque canonique pour le LONG.

    Paramètres de base :
      - stop 1.5%, TP 2.5% (RR = 1.67)
      - cooldown 3 bars
      - max 4 pertes consécutives
    """
    defaults = dict(
        side="long",
        position_size_pct=0.02,
        max_open_positions=1,
        stop_loss_pct=0.015,
        take_profit_pct=0.025,
        risk_reward_ratio=1.67,
        use_atr_stop=True,
        kelly_fraction=0.25,
        max_position_pct=0.05,
        cooldown_bars=3,
        cooldown_after_loss=6,
        max_consecutive_losses=4,
        max_daily_drawdown_pct=0.03,
        max_total_drawdown_pct=0.10,
        min_abs_edge=0.45,
        min_scale=0.45,
        rv_key="rv_24",
        rv_max=0.08,
        max_trades_per_day=10,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


def make_short_risk_config(**overrides) -> RiskConfig:
    """
    Retourne la configuration de risque canonique pour le SHORT.

    Plus conservateur que le long :
      - stop plus serré (1.2% vs 1.5%) car baisses abruptes = gap risk
      - RR plus élevé (2.0 vs 1.67) car coût de portage supérieur
      - cooldown plus long (5 bars vs 3) car faux signaux plus coûteux
      - max 3 pertes consécutives (vs 4) car séquence de pertes courte dangereuse
      - max daily DD 2% (vs 3%) car les baisses s'emballent plus vite
      - position_size réduite (1.5% vs 2.0%) car vol intrinsèquement plus élevée
    """
    defaults = dict(
        side="short",
        position_size_pct=0.015,
        max_open_positions=1,
        stop_loss_pct=0.012,
        take_profit_pct=0.024,
        risk_reward_ratio=2.0,
        use_atr_stop=True,
        kelly_fraction=0.20,
        max_position_pct=0.04,
        cooldown_bars=5,
        cooldown_after_loss=10,
        max_consecutive_losses=3,
        max_daily_drawdown_pct=0.02,
        max_total_drawdown_pct=0.08,
        min_abs_edge=0.50,
        min_scale=0.50,
        rv_key="rv_24",
        rv_max=0.06,
        max_trades_per_day=6,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)
