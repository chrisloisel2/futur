#!/usr/bin/env python3
"""
scripts/debug_asset_quality.py
─────────────────────────────────────────────────────────────────────────────
Diagnostique la qualité des données d'un actif.

Affiche :
  - Source sélectionnée (enriched / data_out)
  - Gaps temporels : date, durée, OHLCV autour du gap
  - Outliers extrêmes : date, log-return, OHLCV contexte
  - Recommandation : reject / patch source / truncate period

Usage :
    python3 scripts/debug_asset_quality.py --asset SOLUSDT
    python3 scripts/debug_asset_quality.py --asset BTCUSDT --max-gap 600
    python3 scripts/debug_asset_quality.py --asset SOLUSDT --start 2022-01-01
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[1]))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Debug asset data quality")
    p.add_argument("--asset",         default="SOLUSDT")
    p.add_argument("--start",         default="2019-01-01")
    p.add_argument("--end",           default="2026-05-31")
    p.add_argument("--max-gap",       type=float, default=120.0,
                   help="Seuil de gap en minutes pour flaguer (default=120)")
    p.add_argument("--outlier-log",   type=float, default=0.20,
                   help="Seuil log-return pour flaguer outlier (default=0.20 = 20%%)")
    p.add_argument("--context-bars",  type=int,   default=3,
                   help="Barres contextuelles autour de chaque anomalie")
    return p.parse_args()


def find_gaps(df: pd.DataFrame, threshold_minutes: float) -> pd.DataFrame:
    """Retourne les gaps supérieurs au seuil."""
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 2:
        return pd.DataFrame()

    gaps = df.index.to_series().diff().dt.total_seconds().div(60).dropna()
    large = gaps[gaps > threshold_minutes].sort_values(ascending=False)

    if large.empty:
        return pd.DataFrame()

    rows = []
    for ts_after, gap_min in large.items():
        pos = df.index.get_loc(ts_after)
        ts_before = df.index[pos - 1] if pos > 0 else None
        rows.append({
            "ts_after":    ts_after,
            "ts_before":   ts_before,
            "gap_minutes": round(gap_min, 0),
            "gap_hours":   round(gap_min / 60, 1),
            "gap_days":    round(gap_min / 1440, 1),
        })

    return pd.DataFrame(rows).sort_values("gap_minutes", ascending=False)


def find_outliers(df: pd.DataFrame, threshold_log: float) -> pd.DataFrame:
    """Retourne les barres avec log-return extrême."""
    if "close" not in df.columns or len(df) < 2:
        return pd.DataFrame()

    log_ret = np.log(df["close"] / df["close"].shift(1)).dropna()
    extreme = log_ret[log_ret.abs() > threshold_log].sort_values(key=abs, ascending=False)

    if extreme.empty:
        return pd.DataFrame()

    rows = []
    for ts, r in extreme.items():
        rows.append({
            "timestamp": ts,
            "log_return": round(r, 4),
            "pct_return": round((np.exp(r) - 1) * 100, 2),
            "close":      float(df.loc[ts, "close"]),
        })

    return pd.DataFrame(rows)


def recommend(
    asset: str,
    gaps: pd.DataFrame,
    outliers: pd.DataFrame,
    df: pd.DataFrame,
) -> str:
    """Génère une recommandation basée sur les anomalies trouvées."""
    lines = [f"\n RECOMMANDATION — {asset}", "─" * 50]

    if gaps.empty and outliers.empty:
        lines.append(" ✓ Données propres — aucune action requise")
        return "\n".join(lines)

    # Gaps
    if not gaps.empty:
        max_gap = float(gaps["gap_minutes"].max())
        n_gaps  = len(gaps)
        if max_gap > 24 * 60 * 7:   # > 1 semaine
            lines.append(f" ✗ Gap CRITIQUE {max_gap/60:.0f}h — données probablement corrompues")
            lines.append(f"   → Recommandation : REJECT cette source, chercher source alternative")
        elif max_gap > 24 * 60:     # > 1 jour
            # Suggérer de tronquer avant/après le gap
            worst_gap = gaps.iloc[0]
            lines.append(f" ⚠ Gap majeur {max_gap/60:.0f}h le {worst_gap['ts_after'].date()}")
            after_date = worst_gap["ts_after"]
            data_after = df.loc[after_date:]
            data_before = df.loc[:worst_gap["ts_before"]] if worst_gap["ts_before"] else pd.DataFrame()
            if len(data_after) > len(data_before):
                lines.append(f"   → Recommandation : TRUNCATE — utiliser data depuis {after_date.date()}")
                lines.append(f"     ({len(data_after):,} barres conservées)")
            else:
                lines.append(f"   → Recommandation : TRUNCATE — utiliser data jusqu'à {worst_gap['ts_before'].date()}")
        else:
            lines.append(f" ⚠ {n_gaps} gap(s) entre {gaps['gap_minutes'].min():.0f} et {max_gap:.0f}min")
            lines.append(f"   → Recommandation : ACCEPTABLE — données utilisables")

    # Outliers
    if not outliers.empty:
        max_ret = float(outliers["log_return"].abs().max())
        n_out   = len(outliers)
        worst   = outliers.iloc[0]

        if max_ret > 0.50:  # > 50%
            lines.append(f" ⚠ {n_out} outlier(s) extrême(s) dont {worst['pct_return']:.1f}% le {worst['timestamp'].date()}")
            lines.append(f"   Vérifier : événement réel de marché ou erreur de données ?")
            # Vérifier si le retour est rapidement récupéré (indique événement réel)
            ts = worst["timestamp"]
            idx = df.index.get_loc(ts)
            if idx + 4 < len(df):
                fwd_4h = (df.iloc[idx + 4]["close"] / df.iloc[idx]["close"]) - 1
                if fwd_4h > 0.10:
                    lines.append(f"   Récupération +{fwd_4h:.1%} en 4h → probable manipulation ou flash crash réel")
                    lines.append(f"   → Recommandation : PATCH — clipper log-return à ±30% pour cet actif")
        else:
            lines.append(f" ⚠ {n_out} outlier(s) modéré(s) (max {worst['pct_return']:.1f}%)")
            lines.append(f"   → Recommandation : ACCEPTABLE — outlier connu (crash de marché réel)")
            lines.append(f"   → Option : ajouter max_price_jump_log=0.30 dans CheckerConfig")

    return "\n".join(lines)


def main() -> None:
    import warnings
    warnings.filterwarnings("ignore")

    args  = parse_args()
    asset = args.asset.upper()
    if not asset.endswith("USDT"):
        asset += "USDT"

    print(f"\n{'═'*60}")
    print(f" DEBUG QUALITÉ DONNÉES — {asset}")
    print(f"{'═'*60}")

    # Charger
    from src.institutional.data.loaders import load_asset_1h
    try:
        df = load_asset_1h(asset, args.start, args.end)
    except FileNotFoundError as e:
        print(f"ERREUR: {e}")
        sys.exit(1)

    source = df["source"].iloc[0] if "source" in df.columns else "unknown"
    print(f"\n Source    : {source}")
    print(f" Barres    : {len(df):,}")
    print(f" Période   : {df.index.min()} → {df.index.max()}")
    if "close" in df.columns:
        print(f" Prix      : min={df['close'].min():.2f}  max={df['close'].max():.2f}")

    # Gaps
    print(f"\n{'─'*60}")
    print(f" GAPS > {args.max_gap:.0f}min")
    print(f"{'─'*60}")
    gaps = find_gaps(df, args.max_gap)

    if gaps.empty:
        print(f" ✓ Aucun gap > {args.max_gap:.0f}min")
    else:
        print(f" {len(gaps)} gap(s) détecté(s):\n")
        for _, row in gaps.iterrows():
            print(f"  ┌─ Gap {row['gap_minutes']:.0f}min ({row['gap_hours']:.1f}h / {row['gap_days']:.1f}j)")
            print(f"  │  Avant : {row['ts_before']}")
            print(f"  │  Après : {row['ts_after']}")

            # Contexte OHLCV
            ts_before = row["ts_before"]
            ts_after  = row["ts_after"]
            if ts_before and ts_before in df.index:
                before_row = df.loc[ts_before]
                print(f"  │  OHLCV avant : O={before_row.get('open',0):.2f} H={before_row.get('high',0):.2f} "
                      f"L={before_row.get('low',0):.2f} C={before_row.get('close',0):.2f}")
            if ts_after in df.index:
                after_row = df.loc[ts_after]
                print(f"  │  OHLCV après : O={after_row.get('open',0):.2f} H={after_row.get('high',0):.2f} "
                      f"L={after_row.get('low',0):.2f} C={after_row.get('close',0):.2f}")
                # Prix avant vs après
                if ts_before and ts_before in df.index:
                    p_before = df.loc[ts_before, "close"]
                    p_after  = after_row["close"]
                    change   = (p_after / p_before - 1) * 100
                    print(f"  │  Variation pendant gap : {change:+.2f}%")
            print(f"  └─")

    # Outliers
    print(f"\n{'─'*60}")
    print(f" OUTLIERS log-return > {args.outlier_log:.0%}")
    print(f"{'─'*60}")
    outliers = find_outliers(df, args.outlier_log)

    if outliers.empty:
        print(f" ✓ Aucun outlier > {args.outlier_log:.0%}")
    else:
        print(f" {len(outliers)} outlier(s) détecté(s):\n")
        for _, row in outliers.iterrows():
            ts  = row["timestamp"]
            print(f"  ┌─ Outlier {row['pct_return']:+.2f}% (log={row['log_return']:.4f}) à {ts}")
            print(f"  │  Close : {row['close']:.4f}")

            # Contexte OHLCV
            idx = df.index.get_loc(ts)
            ctx_start = max(0, idx - args.context_bars)
            ctx_end   = min(len(df), idx + args.context_bars + 1)
            ctx = df.iloc[ctx_start:ctx_end]
            if all(c in ctx.columns for c in ["open", "high", "low", "close", "volume"]):
                print(f"  │  Contexte OHLCV :")
                for ctx_ts, ctx_row in ctx.iterrows():
                    marker = " ► " if ctx_ts == ts else "   "
                    print(f"  │{marker}{ctx_ts}  "
                          f"O={ctx_row['open']:.3f} H={ctx_row['high']:.3f} "
                          f"L={ctx_row['low']:.3f} C={ctx_row['close']:.3f} "
                          f"V={ctx_row['volume']:.0f}")
            print(f"  └─")

    # Recommandation
    print(recommend(asset, gaps, outliers, df))
    print()


if __name__ == "__main__":
    main()
