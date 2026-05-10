"""
ai/portfolio/short_hedge_allocator.py — ALLOCATEUR DE HEDGE SHORT
=================================================================

Décide si et comment utiliser des positions SHORT pour hedger un portefeuille
principalement LONG.

Logique centrale :
  1. Sans LONG ouvert  → SHORT standalone autorisé uniquement si signal fort (> 0.70)
  2. Avec LONG exposé  → SHORT comme hedge si signal court-terme ou squeeze élevé
  3. Bull sain         → bloquer les shorts altcoins
  4. Volatilité extreme → réduire la taille de 50%
  5. Squeeze élevé     → bloquer tout short (trop dangereux)
  6. Par défaut        → allow_short = False
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class HedgeInput:
    """Entrées du moteur de décision hedge."""

    # Exposition LONG courante
    long_exposure_total: float      # 0.0 à 1.0 (fraction du capital total)
    long_positions_count: int       # nombre de positions LONG ouvertes

    # Régime marché
    market_regime: str              # "bull_fresh" / "bull_mature" / "bear" / "neutral"

    # Signaux directionnels [0, 1]
    btc_short_signal: float         # probabilité signal short BTC
    alt_short_signal: float         # probabilité signal short altcoins

    # Corrélation
    correlation_cluster: str        # "btc_dominant" / "alt_season" / "mixed"

    # État portefeuille
    portfolio_drawdown: float       # drawdown courant [0.0 à 1.0], positif = perte
    volatility_regime: str          # "low" / "medium" / "high" / "extreme"

    # Risques spécifiques
    squeeze_risk_score: float = 0.0 # [0, 1] — risque de short squeeze
    funding_rate_z: float = 0.0     # z-score du funding rate (+ = longs payent)

    def __post_init__(self) -> None:
        # Validation des plages
        assert 0.0 <= self.long_exposure_total  <= 1.0, "long_exposure_total hors plage"
        assert 0.0 <= self.btc_short_signal     <= 1.0, "btc_short_signal hors plage"
        assert 0.0 <= self.alt_short_signal     <= 1.0, "alt_short_signal hors plage"
        assert 0.0 <= self.portfolio_drawdown   <= 1.0, "portfolio_drawdown hors plage"
        assert 0.0 <= self.squeeze_risk_score   <= 1.0, "squeeze_risk_score hors plage"
        assert self.market_regime in (
            "bull_fresh", "bull_mature", "bear", "neutral"
        ), f"market_regime invalide : {self.market_regime}"
        assert self.volatility_regime in (
            "low", "medium", "high", "extreme"
        ), f"volatility_regime invalide : {self.volatility_regime}"
        assert self.correlation_cluster in (
            "btc_dominant", "alt_season", "mixed"
        ), f"correlation_cluster invalide : {self.correlation_cluster}"


@dataclass
class HedgeDecision:
    """Résultat du moteur de décision hedge."""

    allow_short: bool               # SHORT autorisé
    short_size_multiplier: float    # 0.0 à 1.0 — multiplicateur sur position standard
    hedge_mode: bool                # True = SHORT agit comme hedge du LONG
    max_short_exposure: float       # fraction max du capital allouable au SHORT
    reason: str                     # raison principale (code court)
    context: str                    # explication détaillée

    def __post_init__(self) -> None:
        self.short_size_multiplier = float(np.clip(self.short_size_multiplier, 0.0, 1.0))
        self.max_short_exposure    = float(np.clip(self.max_short_exposure,    0.0, 1.0))


# ═══════════════════════════════════════════════════════════════════════════════
# MOTEUR DE DÉCISION
# ═══════════════════════════════════════════════════════════════════════════════

_BLOCK_DECISION = HedgeDecision(
    allow_short=False,
    short_size_multiplier=0.0,
    hedge_mode=False,
    max_short_exposure=0.0,
    reason="",
    context="",
)


def decide_short_hedge(inputs: HedgeInput) -> HedgeDecision:
    """
    Moteur de décision principal.

    Évalue les règles dans l'ordre suivant (priorité décroissante) :
      1. Squeeze élevé              → bloquer (trop dangereux)
      2. Sans LONG ouvert           → standalone seulement si signal fort
      3. Avec LONG + stress signal  → hedge autorisé
      4. Bull sain + signal faible  → bloquer altcoin shorts
      5. Volatilité extrême         → réduire la taille
      6. Par défaut                 → refus

    Retourne un HedgeDecision.
    """
    inp = inputs

    # ── Règle 7 / Priorité 1 : Squeeze élevé → toujours bloquer ───────────────
    if inp.squeeze_risk_score > 0.70:
        return HedgeDecision(
            allow_short=False,
            short_size_multiplier=0.0,
            hedge_mode=False,
            max_short_exposure=0.0,
            reason="squeeze_block",
            context=(
                f"Squeeze risk score {inp.squeeze_risk_score:.2f} > 0.70. "
                "Short trop dangereux — squeeze possible contre la position."
            ),
        )

    # ── Règle 1 : Aucun LONG ouvert — mode standalone ─────────────────────────
    if inp.long_positions_count == 0:
        # SHORT autorisé uniquement si signal BTC fort
        if inp.btc_short_signal > 0.70:
            size = 0.5
            # Volatilité extreme → réduire de 50% (règle 6)
            if inp.volatility_regime == "extreme":
                size *= 0.5
            return HedgeDecision(
                allow_short=True,
                short_size_multiplier=size,
                hedge_mode=False,
                max_short_exposure=0.05,    # max 5% du capital en standalone
                reason="standalone_strong_signal",
                context=(
                    f"Aucun LONG ouvert. Signal BTC short {inp.btc_short_signal:.2f} > 0.70. "
                    f"Mode standalone — taille {size:.1%}."
                    + (" Volatilité extreme : taille réduite de 50%."
                       if inp.volatility_regime == "extreme" else "")
                ),
            )
        else:
            return HedgeDecision(
                allow_short=False,
                short_size_multiplier=0.0,
                hedge_mode=False,
                max_short_exposure=0.0,
                reason="standalone_weak_signal",
                context=(
                    f"Aucun LONG ouvert mais signal BTC {inp.btc_short_signal:.2f} ≤ 0.70. "
                    "Edge insuffisant pour un short standalone."
                ),
            )

    # ── Règle 2 : LONG exposé + stress signal → hedge autorisé ────────────────
    stress_signal = (
        inp.btc_short_signal > 0.60 or
        inp.squeeze_risk_score > 0.50     # squeeze modéré déclenche le hedge
    )

    if inp.long_exposure_total > 0.20 and stress_signal:
        # Taille du hedge proportionnelle au drawdown courant
        dd_ratio = inp.portfolio_drawdown / max(0.05, 1e-9)   # normalisé à 5% ref
        size = float(np.clip(0.3 * dd_ratio, 0.0, 0.5))

        # Volatilité extreme → réduire de 50%
        if inp.volatility_regime == "extreme":
            size *= 0.5

        max_short = min(0.10, 0.5 * inp.long_exposure_total)

        return HedgeDecision(
            allow_short=True,
            short_size_multiplier=size,
            hedge_mode=True,
            max_short_exposure=max_short,
            reason="hedge_long_exposure",
            context=(
                f"LONG exposure {inp.long_exposure_total:.0%} > 20% avec stress signal. "
                f"BTC short {inp.btc_short_signal:.2f} | squeeze {inp.squeeze_risk_score:.2f}. "
                f"Taille hedge {size:.1%} | max exposition {max_short:.0%}."
                + (" Volatilité extreme : taille réduite de 50%."
                   if inp.volatility_regime == "extreme" else "")
            ),
        )

    # ── Règle 3 : Bull sain + signal faible → bloquer altcoin shorts ──────────
    bull_healthy = (
        inp.market_regime in ("bull_fresh", "bull_mature") and
        inp.btc_short_signal < 0.60
    )

    if bull_healthy:
        # Aucun short altcoin autorisé
        if inp.alt_short_signal > 0.0:
            return HedgeDecision(
                allow_short=False,
                short_size_multiplier=0.0,
                hedge_mode=False,
                max_short_exposure=0.0,
                reason="bull_blocks_alt_short",
                context=(
                    f"Régime {inp.market_regime} avec BTC signal {inp.btc_short_signal:.2f} < 0.60. "
                    f"Shorts altcoins bloqués (alt signal {inp.alt_short_signal:.2f} ignoré). "
                    "Pas de short contre actif isolé en bull sain."
                ),
            )

        return HedgeDecision(
            allow_short=False,
            short_size_multiplier=0.0,
            hedge_mode=False,
            max_short_exposure=0.0,
            reason="bull_no_signal",
            context=(
                f"Régime {inp.market_regime} sain, BTC signal {inp.btc_short_signal:.2f} < 0.60. "
                "Aucun edge short détecté en tendance haussière fraîche."
            ),
        )

    # ── Règle 4 : Prioriser BTC/ETH shorts comme hedge global ─────────────────
    # Si BTC dominant + bear/neutral, autoriser le hedge BTC
    btc_hedge_eligible = (
        inp.btc_short_signal > 0.60 and
        inp.market_regime in ("bear", "neutral") and
        inp.correlation_cluster in ("btc_dominant", "mixed")
    )

    if btc_hedge_eligible:
        size = 0.5 if inp.long_exposure_total > 0.0 else 0.3
        if inp.volatility_regime == "extreme":
            size *= 0.5
        max_short = 0.10 if inp.long_exposure_total > 0.0 else 0.05

        return HedgeDecision(
            allow_short=True,
            short_size_multiplier=size,
            hedge_mode=inp.long_exposure_total > 0.0,
            max_short_exposure=max_short,
            reason="btc_hedge_global",
            context=(
                f"BTC/ETH hedge prioritaire. Régime {inp.market_regime}, "
                f"cluster {inp.correlation_cluster}, BTC signal {inp.btc_short_signal:.2f}. "
                f"Taille {size:.1%}."
                + (" Volatilité extreme : taille réduite de 50%."
                   if inp.volatility_regime == "extreme" else "")
            ),
        )

    # ── Règle 5 : Altcoin shorts conditionnels ─────────────────────────────────
    # own breakdown + BTC weak + liquidité ok + hors bull_fresh
    alt_short_eligible = (
        inp.alt_short_signal > 0.60 and
        inp.btc_short_signal > 0.55 and
        inp.market_regime not in ("bull_fresh",) and
        inp.squeeze_risk_score <= 0.50
    )

    if alt_short_eligible:
        size = 0.3
        if inp.volatility_regime == "extreme":
            size *= 0.5
        max_short = min(0.05, 0.3 * inp.long_exposure_total) if inp.long_exposure_total > 0 else 0.03

        return HedgeDecision(
            allow_short=True,
            short_size_multiplier=size,
            hedge_mode=inp.long_exposure_total > 0.0,
            max_short_exposure=max_short,
            reason="alt_short_conditional",
            context=(
                f"Short altcoin conditionnel — breakdown propre. "
                f"Alt signal {inp.alt_short_signal:.2f}, BTC support {inp.btc_short_signal:.2f}, "
                f"régime {inp.market_regime}, squeeze {inp.squeeze_risk_score:.2f} ≤ 0.50. "
                f"Taille {size:.1%}."
                + (" Volatilité extreme : taille réduite de 50%."
                   if inp.volatility_regime == "extreme" else "")
            ),
        )

    # ── Règle 6 : Défaut — pas de short ───────────────────────────────────────
    return HedgeDecision(
        allow_short=False,
        short_size_multiplier=0.0,
        hedge_mode=False,
        max_short_exposure=0.0,
        reason="default_no_edge",
        context=(
            f"Aucune règle d'activation satisfaite. "
            f"régime={inp.market_regime}, BTC={inp.btc_short_signal:.2f}, "
            f"alt={inp.alt_short_signal:.2f}, vol={inp.volatility_regime}, "
            f"squeeze={inp.squeeze_risk_score:.2f}."
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# IMPACT HEDGE SUR PORTEFEUILLE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_hedge_portfolio_impact(
    long_equity_curve: pd.Series,
    short_equity_curve: pd.Series,
    hedge_decisions: pd.DataFrame,
) -> dict:
    """
    Calcule l'impact d'une stratégie de hedge SHORT sur le portefeuille combiné.

    Paramètres
    ----------
    long_equity_curve : pd.Series
        Courbe d'équité du portefeuille LONG seul (index datetime, valeurs en USD).
    short_equity_curve : pd.Series
        Courbe d'équité de la composante SHORT (même index).
        Les jours sans trade SHORT doivent avoir valeur = dernière valeur (pas de NaN).
    hedge_decisions : pd.DataFrame
        DataFrame avec colonne "allow_short" (bool) et "short_size_multiplier" (float),
        aligné sur l'index de long_equity_curve.

    Retourne
    --------
    dict avec les métriques combinées vs long-only.
    """
    # Alignement
    idx = long_equity_curve.index
    short_eq = short_equity_curve.reindex(idx).fillna(method="ffill").fillna(
        short_equity_curve.iloc[0] if len(short_equity_curve) > 0 else 10_000.0
    )

    # Retours journaliers
    long_ret  = long_equity_curve.pct_change().fillna(0.0)
    short_ret = short_eq.pct_change().fillna(0.0)

    # Appliquer le sizing du hedge (short_size_multiplier)
    if "short_size_multiplier" in hedge_decisions.columns:
        size_series = hedge_decisions["short_size_multiplier"].reindex(idx).fillna(0.0)
    else:
        size_series = pd.Series(0.0, index=idx)

    if "allow_short" in hedge_decisions.columns:
        allow_series = hedge_decisions["allow_short"].reindex(idx).fillna(False).astype(float)
    else:
        allow_series = pd.Series(0.0, index=idx)

    hedge_weight = size_series * allow_series
    long_weight  = 1.0 - hedge_weight.clip(0.0, 0.5)   # LONG toujours dominant

    # Retour du portefeuille combiné
    combined_ret = long_weight * long_ret + hedge_weight * short_ret

    # Courbes d'équité
    equity0       = float(long_equity_curve.iloc[0])
    long_eq_curve = equity0 * (1 + long_ret).cumprod()
    comb_eq_curve = equity0 * (1 + combined_ret).cumprod()

    def _max_drawdown(eq: pd.Series) -> float:
        run_max = eq.cummax()
        dds = (eq - run_max) / (run_max + 1e-9)
        return float(dds.min()) * 100

    def _sharpe(ret: pd.Series, rf_annual: float = 0.05) -> float:
        rf_daily = rf_annual / 252
        excess   = ret - rf_daily
        return float((excess.mean() / (excess.std() + 1e-9)) * np.sqrt(252)) if len(excess) > 1 else 0.0

    long_dd   = _max_drawdown(long_eq_curve)
    comb_dd   = _max_drawdown(comb_eq_curve)
    long_sh   = _sharpe(long_ret)
    comb_sh   = _sharpe(combined_ret)

    dd_reduction = ((abs(long_dd) - abs(comb_dd)) / (abs(long_dd) + 1e-9)) * 100

    # Corrélation retours
    valid_mask = (~long_ret.isna()) & (~short_ret.isna())
    if valid_mask.sum() > 10:
        corr = float(long_ret[valid_mask].corr(short_ret[valid_mask]))
    else:
        corr = float("nan")

    hedge_active_pct = float(allow_series.mean() * 100)

    return {
        "long_only_max_drawdown_pct":  round(long_dd, 2),
        "combined_max_drawdown_pct":   round(comb_dd, 2),
        "long_only_sharpe":            round(long_sh, 3),
        "combined_sharpe":             round(comb_sh, 3),
        "hedge_effectiveness_pct":     round(dd_reduction, 2),
        "long_short_correlation":      round(corr, 4) if not np.isnan(corr) else None,
        "hedge_active_pct_of_time":    round(hedge_active_pct, 1),
        "combined_total_return_pct":   round(
            (comb_eq_curve.iloc[-1] - equity0) / equity0 * 100, 2),
        "long_only_total_return_pct":  round(
            (long_eq_curve.iloc[-1] - equity0) / equity0 * 100, 2),
        "avg_hedge_weight":            round(float(hedge_weight.mean()), 4),
    }
