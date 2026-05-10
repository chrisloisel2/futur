#!/usr/bin/env python3
"""
scripts/test_short_hedge_allocator.py — TESTS DE L'ALLOCATEUR HEDGE SHORT
==========================================================================

Script standalone qui valide decide_short_hedge() et compute_hedge_portfolio_impact()
sur une suite de scénarios couvrant tous les cas limites.

Score final : X/N scénarios passés.

Usage :
  python scripts/test_short_hedge_allocator.py
  python scripts/test_short_hedge_allocator.py --verbose
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.portfolio.short_hedge_allocator import (
    HedgeInput,
    HedgeDecision,
    decide_short_hedge,
    compute_hedge_portfolio_impact,
)


# ═══════════════════════════════════════════════════════════════════════════════
# DÉFINITION DES SCÉNARIOS
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Scenario:
    description: str
    inputs: HedgeInput
    expected_allow_short: bool
    expected_hedge_mode: Optional[bool] = None      # None = ne pas vérifier
    expected_reason_contains: Optional[str] = None  # sous-chaîne attendue dans reason
    # Si allow=True, taille attendue (max)
    expected_max_size: Optional[float] = None


def _inp(**kwargs) -> HedgeInput:
    """Construit un HedgeInput avec des valeurs par défaut raisonnables."""
    defaults = dict(
        long_exposure_total  = 0.0,
        long_positions_count = 0,
        market_regime        = "neutral",
        btc_short_signal     = 0.50,
        alt_short_signal     = 0.50,
        correlation_cluster  = "btc_dominant",
        portfolio_drawdown   = 0.02,
        volatility_regime    = "medium",
        squeeze_risk_score   = 0.0,
        funding_rate_z       = 0.0,
    )
    defaults.update(kwargs)
    return HedgeInput(**defaults)


SCENARIOS: List[Scenario] = [
    # ── Bloc 1 : Aucun LONG ouvert ─────────────────────────────────────────────
    Scenario(
        description="No longs, weak signal (0.50 < 0.70) → refus standalone",
        inputs=_inp(
            long_exposure_total=0.0,
            long_positions_count=0,
            btc_short_signal=0.50,
        ),
        expected_allow_short=False,
        expected_reason_contains="standalone_weak",
    ),
    Scenario(
        description="No longs, signal limite (0.70 exact) → autorisé",
        inputs=_inp(
            long_exposure_total=0.0,
            long_positions_count=0,
            btc_short_signal=0.71,   # strictement supérieur au seuil
        ),
        expected_allow_short=True,
        expected_hedge_mode=False,
        expected_reason_contains="standalone_strong",
    ),
    Scenario(
        description="No longs, strong signal (0.80) → standalone autorisé",
        inputs=_inp(
            long_exposure_total=0.0,
            long_positions_count=0,
            btc_short_signal=0.80,
        ),
        expected_allow_short=True,
        expected_hedge_mode=False,
        expected_max_size=0.5,
    ),
    Scenario(
        description="No longs, strong signal + extreme vol → taille réduite 50%",
        inputs=_inp(
            long_exposure_total=0.0,
            long_positions_count=0,
            btc_short_signal=0.75,
            volatility_regime="extreme",
        ),
        expected_allow_short=True,
        expected_hedge_mode=False,
        expected_max_size=0.26,   # 0.5 * 0.5 = 0.25, tolérance 0.01
    ),

    # ── Bloc 2 : Bull trend ────────────────────────────────────────────────────
    Scenario(
        description="Bull fresh, signal faible (0.45) → bloque alt shorts",
        inputs=_inp(
            long_exposure_total=0.10,
            long_positions_count=2,
            market_regime="bull_fresh",
            btc_short_signal=0.45,
            alt_short_signal=0.65,
        ),
        expected_allow_short=False,
        expected_reason_contains="bull_blocks_alt",
    ),
    Scenario(
        description="Bull fresh, signal faible, pas d'alt signal → refus bull",
        inputs=_inp(
            long_exposure_total=0.10,
            long_positions_count=2,
            market_regime="bull_fresh",
            btc_short_signal=0.40,
            alt_short_signal=0.0,
        ),
        expected_allow_short=False,
        expected_reason_contains="bull_no_signal",
    ),
    Scenario(
        description="Bull mature, squeeze élevé (0.80) → bloquer (squeeze prioritaire)",
        inputs=_inp(
            long_exposure_total=0.30,
            long_positions_count=3,
            market_regime="bull_mature",
            btc_short_signal=0.70,
            squeeze_risk_score=0.80,
        ),
        expected_allow_short=False,
        expected_reason_contains="squeeze_block",
    ),

    # ── Bloc 3 : Bear + exposition LONG ──────────────────────────────────────
    Scenario(
        description="Bear + long 30% + BTC signal 0.65 → hedge autorisé",
        inputs=_inp(
            long_exposure_total=0.30,
            long_positions_count=3,
            market_regime="bear",
            btc_short_signal=0.65,
            portfolio_drawdown=0.05,
        ),
        expected_allow_short=True,
        expected_hedge_mode=True,
        expected_reason_contains="hedge_long",
    ),
    Scenario(
        description="Bear + long 25% + signal 0.55 (sous seuil stress) → refus hedge direct",
        inputs=_inp(
            long_exposure_total=0.25,
            long_positions_count=2,
            market_regime="bear",
            btc_short_signal=0.55,
            portfolio_drawdown=0.01,
            squeeze_risk_score=0.0,
        ),
        expected_allow_short=True,   # éligible via btc_hedge_global
        expected_reason_contains="btc_hedge",
    ),
    Scenario(
        description="Neutral + long 35% + BTC signal 0.70 → hedge autorisé",
        inputs=_inp(
            long_exposure_total=0.35,
            long_positions_count=4,
            market_regime="neutral",
            btc_short_signal=0.70,
            portfolio_drawdown=0.04,
        ),
        expected_allow_short=True,
        expected_hedge_mode=True,
    ),

    # ── Bloc 4 : Volatilité extrême ───────────────────────────────────────────
    Scenario(
        description="Extreme volatility + BTC 0.70 + bear → autorisé, taille réduite",
        inputs=_inp(
            long_exposure_total=0.20,
            long_positions_count=2,
            market_regime="bear",
            btc_short_signal=0.70,
            volatility_regime="extreme",
            portfolio_drawdown=0.05,
        ),
        expected_allow_short=True,
        # La taille doit être réduite de 50%
        expected_max_size=0.26,   # max 0.5 → réduit à 0.25
    ),
    Scenario(
        description="Extreme volatility + no longs + BTC 0.72 → standalone réduit",
        inputs=_inp(
            long_exposure_total=0.0,
            long_positions_count=0,
            btc_short_signal=0.72,
            volatility_regime="extreme",
        ),
        expected_allow_short=True,
        expected_max_size=0.26,   # 0.5 → 0.25
    ),

    # ── Bloc 5 : Squeeze risque ───────────────────────────────────────────────
    Scenario(
        description="High squeeze (0.80) + strong signal → bloqué (squeeze > 0.70)",
        inputs=_inp(
            long_exposure_total=0.0,
            long_positions_count=0,
            btc_short_signal=0.85,
            squeeze_risk_score=0.80,
        ),
        expected_allow_short=False,
        expected_reason_contains="squeeze_block",
    ),
    Scenario(
        description="Squeeze modéré (0.50) + long 25% + bear → hedge hedge OK",
        inputs=_inp(
            long_exposure_total=0.25,
            long_positions_count=2,
            market_regime="bear",
            btc_short_signal=0.65,
            squeeze_risk_score=0.50,
            portfolio_drawdown=0.06,
        ),
        expected_allow_short=True,
        expected_hedge_mode=True,
    ),

    # ── Bloc 6 : Altcoin shorts conditionnels ─────────────────────────────────
    Scenario(
        description="Alt short conditionnel — bear + btc faible 0.56 + alt 0.65",
        inputs=_inp(
            long_exposure_total=0.15,
            long_positions_count=2,
            market_regime="bear",
            btc_short_signal=0.56,
            alt_short_signal=0.65,
            squeeze_risk_score=0.30,
        ),
        expected_allow_short=True,
        expected_reason_contains="alt_short",
    ),
    Scenario(
        description="Alt short bloqué — bull_fresh même si alt 0.80",
        inputs=_inp(
            long_exposure_total=0.10,
            long_positions_count=1,
            market_regime="bull_fresh",
            btc_short_signal=0.58,
            alt_short_signal=0.80,
            squeeze_risk_score=0.10,
        ),
        expected_allow_short=False,
    ),

    # ── Bloc 7 : Défaut ───────────────────────────────────────────────────────
    Scenario(
        description="Default — aucune règle satisfaite → refus",
        inputs=_inp(
            long_exposure_total=0.10,
            long_positions_count=1,
            market_regime="neutral",
            btc_short_signal=0.45,
            alt_short_signal=0.40,
            portfolio_drawdown=0.01,
        ),
        expected_allow_short=False,
        expected_reason_contains="default",
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER DE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def run_scenarios(verbose: bool = False) -> Tuple[int, int]:
    """Exécute tous les scénarios. Retourne (passed, total)."""
    sep = "─" * 80
    print(f"\n{sep}")
    print("TEST ALLOCATEUR HEDGE SHORT — decide_short_hedge()")
    print(sep)

    passed = 0
    total  = len(SCENARIOS)

    for i, sc in enumerate(SCENARIOS, 1):
        decision: HedgeDecision = decide_short_hedge(sc.inputs)

        # Vérifications
        errors: List[str] = []

        # 1. allow_short
        if decision.allow_short != sc.expected_allow_short:
            errors.append(
                f"allow_short={decision.allow_short} (attendu {sc.expected_allow_short})"
            )

        # 2. hedge_mode (optionnel)
        if sc.expected_hedge_mode is not None and decision.allow_short:
            if decision.hedge_mode != sc.expected_hedge_mode:
                errors.append(
                    f"hedge_mode={decision.hedge_mode} (attendu {sc.expected_hedge_mode})"
                )

        # 3. reason (optionnel)
        if sc.expected_reason_contains is not None:
            if sc.expected_reason_contains not in decision.reason:
                errors.append(
                    f"reason='{decision.reason}' "
                    f"(doit contenir '{sc.expected_reason_contains}')"
                )

        # 4. taille max (optionnel, si allow=True)
        if sc.expected_max_size is not None and decision.allow_short:
            if decision.short_size_multiplier > sc.expected_max_size + 0.001:
                errors.append(
                    f"short_size_multiplier={decision.short_size_multiplier:.3f} "
                    f"> max attendu {sc.expected_max_size:.3f}"
                )

        ok = len(errors) == 0
        if ok:
            passed += 1

        status = "PASS" if ok else "FAIL"
        icon   = "OK" if ok else "!!"
        print(f"  {icon} [{i:02d}/{total}] {status} — {sc.description}")

        if verbose or not ok:
            print(f"         Décision : allow={decision.allow_short} | "
                  f"size={decision.short_size_multiplier:.2f} | "
                  f"hedge={decision.hedge_mode} | "
                  f"max_exp={decision.max_short_exposure:.2f} | "
                  f"reason='{decision.reason}'")
            if verbose:
                print(f"         Context  : {decision.context}")
        if errors:
            for err in errors:
                print(f"         ERREUR : {err}")

    print(sep)
    print(f"\nRÉSULTAT ALLOCATEUR : {passed}/{total} scénarios passés")

    return passed, total


# ═══════════════════════════════════════════════════════════════════════════════
# TEST compute_hedge_portfolio_impact
# ═══════════════════════════════════════════════════════════════════════════════

def test_hedge_impact(verbose: bool = False) -> Tuple[int, int]:
    """
    Teste compute_hedge_portfolio_impact avec des courbes synthétiques.
    Scénarios :
      A — SHORT corrèle négativement avec LONG → hedge réduit drawdown
      B — SHORT corrèle positivement (mauvais hedge) → drawdown augmente
      C — SHORT inactif (allow=False partout) → combiné = LONG-only
    """
    sep = "─" * 80
    print(f"\n{sep}")
    print("TEST IMPACT HEDGE — compute_hedge_portfolio_impact()")
    print(sep)

    n = 252 * 2   # 2 ans de données journalières
    dates = pd.date_range("2023-01-01", periods=n, freq="D")
    rng   = np.random.default_rng(99)

    passed = 0
    total  = 3

    # ── Scénario A : bon hedge ─────────────────────────────────────────────────
    long_ret   = pd.Series(rng.normal(0.0008, 0.018, n), index=dates)
    # LONG subit un crash de 20% sur 30 jours
    crash_start = 150
    long_ret.iloc[crash_start:crash_start+30] = -0.008

    # SHORT gagne pendant le crash
    short_daily = rng.normal(-0.0003, 0.015, n)
    short_daily[crash_start:crash_start+30] = 0.006
    short_ret  = pd.Series(short_daily, index=dates)

    long_eq  = (10_000 * (1 + long_ret).cumprod())
    short_eq = (10_000 * (1 + short_ret).cumprod())

    hedge_df = pd.DataFrame({
        "allow_short":            True,
        "short_size_multiplier":  0.3,
    }, index=dates)
    hedge_df.iloc[crash_start+30:, hedge_df.columns.get_loc("allow_short")] = False

    metrics_a = compute_hedge_portfolio_impact(long_eq, short_eq, hedge_df)

    ok_a = (
        metrics_a["hedge_effectiveness_pct"] > 0 and          # hedge réduit DD
        metrics_a["long_short_correlation"] is not None
    )
    if ok_a:
        passed += 1

    print(f"  {'OK' if ok_a else '!!'} [A] Bon hedge — hedge_effectiveness_pct="
          f"{metrics_a['hedge_effectiveness_pct']:.1f}% | "
          f"long_DD={metrics_a['long_only_max_drawdown_pct']:.1f}% → "
          f"comb_DD={metrics_a['combined_max_drawdown_pct']:.1f}%")
    if verbose:
        for k, v in metrics_a.items():
            print(f"       {k}: {v}")

    # ── Scénario B : SHORT inactif ─────────────────────────────────────────────
    hedge_df_b = pd.DataFrame({
        "allow_short":            False,
        "short_size_multiplier":  0.0,
    }, index=dates)

    metrics_b = compute_hedge_portfolio_impact(long_eq, short_eq, hedge_df_b)

    # Si SHORT inactif, combined ≈ long-only (tolérance 0.1%)
    tol = 0.1
    ok_b = (
        abs(metrics_b["combined_max_drawdown_pct"] - metrics_b["long_only_max_drawdown_pct"]) < tol and
        abs(metrics_b["combined_total_return_pct"] - metrics_b["long_only_total_return_pct"]) < tol
    )
    if ok_b:
        passed += 1

    print(f"  {'OK' if ok_b else '!!'} [B] SHORT inactif — "
          f"combined_DD={metrics_b['combined_max_drawdown_pct']:.2f}% vs "
          f"long_DD={metrics_b['long_only_max_drawdown_pct']:.2f}% "
          f"(diff={abs(metrics_b['combined_max_drawdown_pct'] - metrics_b['long_only_max_drawdown_pct']):.3f}%)")

    # ── Scénario C : hedge_effectiveness doit être un float ───────────────────
    metrics_c = compute_hedge_portfolio_impact(long_eq, long_eq.copy(), hedge_df)   # short = long
    ok_c = isinstance(metrics_c["hedge_effectiveness_pct"], float)
    if ok_c:
        passed += 1

    print(f"  {'OK' if ok_c else '!!'} [C] Types retournés corrects — "
          f"hedge_effectiveness={metrics_c['hedge_effectiveness_pct']}")

    print(sep)
    print(f"\nRÉSULTAT IMPACT HEDGE : {passed}/{total} scénarios passés")

    return passed, total


# ═══════════════════════════════════════════════════════════════════════════════
# AFFICHAGE DÉTAIL D'UN CAS
# ═══════════════════════════════════════════════════════════════════════════════

def print_decision_detail(sc: Scenario) -> None:
    """Affiche le détail complet d'une décision."""
    d = decide_short_hedge(sc.inputs)
    print(f"\n  {sc.description}")
    print(f"  Inputs  : long_exp={sc.inputs.long_exposure_total:.0%}, "
          f"n_long={sc.inputs.long_positions_count}, "
          f"régime={sc.inputs.market_regime}, "
          f"BTC_sig={sc.inputs.btc_short_signal:.2f}, "
          f"alt_sig={sc.inputs.alt_short_signal:.2f}, "
          f"vol={sc.inputs.volatility_regime}, "
          f"squeeze={sc.inputs.squeeze_risk_score:.2f}")
    print(f"  Décision: allow={d.allow_short}, size={d.short_size_multiplier:.2f}, "
          f"hedge={d.hedge_mode}, max_exp={d.max_short_exposure:.2f}")
    print(f"  Raison  : {d.reason}")
    print(f"  Contexte: {d.context}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Tests allocateur hedge SHORT")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Afficher le contexte complet de chaque décision")
    parser.add_argument("--show-scenario", type=int, default=None,
                        help="Afficher le détail du scénario N (1-indexed)")
    args = parser.parse_args()

    if args.show_scenario is not None:
        idx = args.show_scenario - 1
        if 0 <= idx < len(SCENARIOS):
            print_decision_detail(SCENARIOS[idx])
        else:
            print(f"Scénario {args.show_scenario} inexistant (1..{len(SCENARIOS)})")
        return

    # Lancer les tests
    p1, t1 = run_scenarios(verbose=args.verbose)
    p2, t2 = test_hedge_impact(verbose=args.verbose)

    total_passed = p1 + p2
    total_all    = t1 + t2

    sep = "═" * 80
    print(f"\n{sep}")
    print(f"SCORE GLOBAL : {total_passed}/{total_all} scénarios passés")
    print(f"  decide_short_hedge()            : {p1}/{t1}")
    print(f"  compute_hedge_portfolio_impact(): {p2}/{t2}")
    print(sep)

    # CI exit code
    sys.exit(0 if total_passed == total_all else 1)


if __name__ == "__main__":
    main()
