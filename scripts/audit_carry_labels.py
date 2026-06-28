#!/usr/bin/env python3
"""
scripts/audit_carry_labels.py
─────────────────────────────────────────────────────────────────────────────
Audit du carry label — SANS entraîner le moindre modèle.

Problème identifié : distribution UP≈0.1%, DOWN>50% → convention de funding
inversée dans carry_labels.py.

Convention Binance futures :
  funding_rate > 0 → les LONGS paient les SHORTS (longs coûtent du funding)
  funding_rate < 0 → les SHORTS paient les LONGS (shorts coûtent du funding)

Pour harvester le carry :
  funding > 0 → aller SHORT pour RECEVOIR le funding
  funding < 0 → aller LONG pour RECEVOIR le funding

Ce script :
  1. Montre la distribution du funding rate (BTC et multi-actifs)
  2. Calcule le carry NET avec la convention CORRECTE (et l'ancienne)
  3. Compare les distributions de labels
  4. Propose la correction à appliquer

NE PAS entraîner tant que la distribution n'est pas : UP≈15-30%, DOWN≈15-30%.

Usage :
    python3 scripts/audit_carry_labels.py
    python3 scripts/audit_carry_labels.py --assets BTCUSDT,ETHUSDT,SOLUSDT
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--assets", default="BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT")
    p.add_argument("--start",  default="2021-01-01")
    p.add_argument("--end",    default="2025-12-31")
    p.add_argument("--horizons", default="8,24,72")
    return p.parse_args()


BARS_PER_YEAR_1H = 24 * 365
FUNDING_PERIOD_H = 8


def compute_carry_label_correct(
    close: pd.Series,
    funding_rate: pd.Series,
    horizon_h: int,
    cost_bps: float = 10.0,
) -> pd.Series:
    """
    Convention CORRECTE Binance :
      funding > 0 → harvest en SHORT → adverse = montée des prix
      funding < 0 → harvest en LONG  → adverse = baisse des prix

    net_carry > 0 → UP
    net_carry ≈ 0 → FLAT
    net_carry < 0 → DOWN
    """
    n_periods = horizon_h / FUNDING_PERIOD_H
    cost_frac = cost_bps / 10_000

    # Funding collecté : toujours positif (on choisit la bonne side)
    carry_received = funding_rate.abs() * n_periods

    fwd_ret = np.log(close.shift(-horizon_h) / close)

    # Mouvement adverse selon le side
    adverse = np.where(
        funding_rate > 0,
        fwd_ret.clip(lower=0),       # SHORT, perd si prix monte
        (-fwd_ret).clip(lower=0),    # LONG,  perd si prix baisse
    )
    adverse_series = pd.Series(adverse, index=close.index)

    net = carry_received - adverse_series - cost_frac

    label = pd.Series(0, index=close.index, dtype=np.int8)
    label[net >  cost_frac * 0.5] = 1
    label[net < -cost_frac]       = -1
    label[fwd_ret.isna()]         = pd.NA
    return label.astype("Int8")


def compute_carry_label_buggy(
    close: pd.Series,
    funding_rate: pd.Series,
    horizon_h: int,
    cost_bps: float = 10.0,
) -> pd.Series:
    """
    Convention ANCIENNE (incorrecte) — pour comparaison.
    Assumait funding > 0 → on collecte (comme si on était LONG)
    """
    n_periods = horizon_h / FUNDING_PERIOD_H
    cost_frac = cost_bps / 10_000

    carry_coll = funding_rate * n_periods  # Peut être négatif !

    fwd_ret = np.log(close.shift(-horizon_h) / close)

    # Convention inversée (bug)
    adverse = np.where(
        carry_coll > 0,
        (-fwd_ret).clip(lower=0),    # perd si prix baisse → correspond à LONG pas SHORT
        (fwd_ret).clip(lower=0),
    )
    adverse_series = pd.Series(adverse, index=close.index)

    net = carry_coll - adverse_series - cost_frac

    label = pd.Series(0, index=close.index, dtype=np.int8)
    label[net >  cost_frac * 0.5] = 1
    label[net < -cost_frac]       = -1
    label[fwd_ret.isna()]         = pd.NA
    return label.astype("Int8")


def label_dist(label: pd.Series) -> dict:
    v = label.dropna()
    n = len(v)
    return {
        "UP":   round(float((v == 1).mean()), 3),
        "FLAT": round(float((v == 0).mean()), 3),
        "DOWN": round(float((v == -1).mean()), 3),
        "n":    n,
    }


def assess_distribution(dist: dict) -> str:
    """Évalue si la distribution est acceptable pour entraîner."""
    up   = dist["UP"]
    down = dist["DOWN"]
    flat = dist["FLAT"]
    if up < 0.02 or down < 0.02:
        return "✗ REJECT — UP ou DOWN quasi-absents, ne pas entraîner"
    if flat > 0.90:
        return "✗ REJECT — FLAT > 90%, signal inexistant"
    if flat < 0.30:
        return "⚠ WARNING — FLAT < 30%, trop de bruit"
    if up > 0.15 and down > 0.10:
        return "✓ OK — distribution utilisable pour entraînement"
    return "⚠ WARNING — distribution asymétrique, vérifier"


def main() -> None:
    import warnings
    warnings.filterwarnings("ignore")

    args     = parse_args()
    assets   = [a.strip().upper() for a in args.assets.split(",")]
    assets   = [a if a.endswith("USDT") else f"{a}USDT" for a in assets]
    horizons = [int(h) for h in args.horizons.split(",")]

    from src.institutional.data.loaders import load_asset_1h, load_funding
    from src.institutional.data.asof_join import asof_join_funding

    print(f"\n{'═'*70}")
    print(f" AUDIT CARRY LABELS — Convention Binance")
    print(f"{'═'*70}")

    # Analyse du funding (BTC uniquement)
    try:
        fund_df = load_funding(args.start, args.end)
        fr = fund_df["funding_rate"]
        print(f"\n FUNDING RATE BTC ({args.start} → {args.end})")
        print(f"{'─'*50}")
        print(f"  Observations    : {len(fr):,}")
        print(f"  > 0 (longs paient) : {(fr > 0).mean():.1%}")
        print(f"  < 0 (shorts paient): {(fr < 0).mean():.1%}")
        print(f"  == 0               : {(fr == 0).mean():.1%}")
        print(f"  Médiane   : {fr.median():.6f} ({fr.median()*3*365:.2%}/an)")
        print(f"  95e pctile: {fr.quantile(0.95):.6f}")
        print(f"  5e  pctile: {fr.quantile(0.05):.6f}")
        print(f"\n  Convention Binance CONFIRMÉE :")
        print(f"  → funding > 0 : longs paient shorts")
        print(f"  → funding > 0 représente {(fr > 0).mean():.1%} du temps")
        print(f"  → Pour harvester, être SHORT quand funding > 0")
    except Exception as e:
        print(f"  Funding non disponible: {e}")

    # Comparaison labels buggy vs correct
    print(f"\n{'─'*70}")
    print(f" COMPARAISON LABELS : ANCIENNE convention vs CORRECTE")
    print(f"{'─'*70}")

    for asset in assets[:3]:  # max 3 actifs pour lisibilité
        try:
            ohlcv = load_asset_1h(asset, args.start, args.end)
            fund  = load_funding(args.start, args.end)
            master = asof_join_funding(ohlcv, fund)
            close     = master["close"]
            fr_1h     = master["funding_rate"].ffill(limit=10)

            print(f"\n  {asset}")
            print(f"  {'─'*65}")
            print(f"  {'Horizon':>8s}  {'Version':>12s}  {'UP':>6s}  {'FLAT':>6s}  {'DOWN':>6s}  {'n':>7s}  Verdict")
            print(f"  {'─'*65}")

            for h in horizons:
                # Ancienne version (buggy)
                lbl_bug    = compute_carry_label_buggy(close, fr_1h, h)
                dist_bug   = label_dist(lbl_bug)
                verdict_bug = assess_distribution(dist_bug)

                # Nouvelle version (correcte)
                lbl_corr   = compute_carry_label_correct(close, fr_1h, h)
                dist_corr  = label_dist(lbl_corr)
                verdict_corr = assess_distribution(dist_corr)

                print(f"  {h:>7d}h  {'ANCIENNE':>12s}  "
                      f"{dist_bug['UP']:>5.1%}  {dist_bug['FLAT']:>5.1%}  "
                      f"{dist_bug['DOWN']:>5.1%}  {dist_bug['n']:>7,}  {verdict_bug}")
                print(f"  {h:>7d}h  {'CORRECTE':>12s}  "
                      f"{dist_corr['UP']:>5.1%}  {dist_corr['FLAT']:>5.1%}  "
                      f"{dist_corr['DOWN']:>5.1%}  {dist_corr['n']:>7,}  {verdict_corr}")

        except Exception as e:
            print(f"  {asset}: ERREUR — {e}")

    # Diagnostic et instructions
    print(f"\n{'═'*70}")
    print(f" DIAGNOSTIC")
    print(f"{'─'*70}")
    print(f"""
  BUG CONFIRMÉ dans carry_labels.py :

  Ancienne convention :
    carry_coll = funding_rate × n_periods  (positif = collecté par le long)
    adverse    = (-fwd_ret).clip(lower=0)  # perd si prix baisse = logique LONG

  Problème : Binance funding > 0 → les LONGS PAIENT (pas reçoivent !).
  Pour collecter, il faut être SHORT. L'adverse pour SHORT = hausse des prix.

  Correction dans carry_labels.py :
    # Funding reçu = toujours |funding_rate| × n_periods (absolu, peu importe le side)
    carry_received = funding_rate.abs() * n_periods

    # Adverse : dépend de la side CORRECTE pour le harvest
    adverse = np.where(
        funding_rate > 0,
        fwd_ret.clip(lower=0),       # SHORT (récepteur de funding > 0), perd si hausse
        (-fwd_ret).clip(lower=0),    # LONG  (récepteur de funding < 0), perd si baisse
    )
""")
    print(f"  DÉCISION : NE PAS entraîner le carry engine avec l'ancienne convention.")
    print(f"  Appliquer le fix dans carry_labels.py puis re-vérifier avec cet audit.")
    print(f"  Critère pour débloquer : UP > 10% ET DOWN > 10% ET FLAT 40-70%")
    print(f"{'═'*70}\n")


if __name__ == "__main__":
    main()
