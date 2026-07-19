#!/usr/bin/env python3
"""
scripts/walkforward_v3.py — Walk-Forward Validation : Baseline vs V3
=====================================================================

Teste la validité des 3 changements structurels de la v3 :
  1. ReturnPredictor (multi-task Ridge)
  2. Regime-aware oversampling
  3. (Le walk-forward mensuel lui-même est la 3ème amélioration)

Protocole :
  Pour chaque année test ∈ [2021, 2022, 2023, 2024, 2025] :
    → Train sur toutes les données ANTÉRIEURES (expanding window)
    → Calibration sur le H2 de l'année précédente
    → Test sur l'année complète
    → Comparer baseline (v2) vs v3 (les 3 changements)

Métriques par mois/année :
  - P&L net (25% sizing, 10bps coût)
  - Win Rate
  - Profit Factor
  - Sharpe mensuel
  - n_signals

Usage :
  python scripts/walkforward_v3.py
  python scripts/walkforward_v3.py --symbols BTCUSDT ETHUSDT
  python scripts/walkforward_v3.py --years 2023 2024 2025  # test rapide
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from ai.level_0.labels import compute_label_columns, build_labels, compute_long_regime_col
from ai.level_0.features import get_available_features
from ai.level_0.constants import COST_PCT, HORIZON_BARS, TARGET_COL
from ai.level_0.augmentation import augment_positives, regime_aware_augment
from ai.level_0.return_predictor import ReturnPredictor
from ai.level_2.trm_fleet_long_v4 import TRMFleetLongV4, calibrate_context_thresholds_v4

try:
    from ai.level_0.institutional_features import FEATURES_INST_LONG
except ImportError:
    from ai.level_0.features import FEATURES_LONG as FEATURES_INST_LONG

ENRICHED_DIR = ROOT / "data" / "enriched"
RESULTS_DIR  = ROOT / "reports" / "walkforward_v3"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]

SIZING   = 0.25
HORIZON  = HORIZON_BARS   # 8


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _add_rv_aliases(df: pd.DataFrame) -> pd.DataFrame:
    rv_map = {
        "rv_24": "realized_volatility_20",
        "rv_72": "realized_volatility_50",
    }
    for t, s in rv_map.items():
        if t not in df.columns and s in df.columns:
            df[t] = df[s]
    if "rv_ratio_24_72" not in df.columns and "rv_24" in df.columns and "rv_72" in df.columns:
        df["rv_ratio_24_72"] = df["rv_24"] / df["rv_72"].replace(0.0, float("nan"))
    return df


def _simulate_year(
    df:        pd.DataFrame,
    test_year: int,
    fleet:     TRMFleetLongV4,
    thresholds: Dict[str, float],
    ret_pred:  Optional[ReturnPredictor],
    thr_floor: float,
    version:   str,
) -> Dict[str, list]:
    """
    Simule les trades sur test_year et retourne un dict de résultats mensuels.
    """
    years   = df["datetime"].dt.year.values
    tst_mask = years == test_year
    if tst_mask.sum() < 50:
        return {}

    df_test = df.iloc[np.where(tst_mask)[0]].copy().reset_index(drop=True)
    ones    = np.ones(len(df_test), dtype=bool)

    try:
        p_entry = fleet.predict(df_test, ones)
    except Exception as e:
        print(f"    [{version}] predict error: {e}")
        return {}

    close_arr = df_test["close"].values
    dt_arr    = df_test["datetime"].values
    regime_arr = (df_test["regime_long"].values
                  if "regime_long" in df_test.columns
                  else np.full(len(df_test), "NEUTRAL"))

    results: Dict[str, list] = defaultdict(list)

    for si in range(len(df_test)):
        p = float(p_entry[si])
        thr = float(thresholds.get("general", thr_floor))
        if p < thr:
            continue
        if str(regime_arr[si]) == "NO_LONG":
            continue
        if si + HORIZON >= len(df_test):
            continue

        ep  = close_arr[si]
        xp  = close_arr[si + HORIZON]
        if ep <= 0:
            continue

        ret_log = float(np.log(xp / ep) - COST_PCT)

        # ReturnPredictor boost (v3 only)
        size = SIZING
        if ret_pred is not None and ret_pred.fitted_:
            try:
                bar = df_test.iloc[si]
                rv24 = float(bar.get("rv_24", 0.02))
                z = ret_pred.single_zscore(bar, rv_24=rv24)
                size = SIZING * ret_pred.size_boost(z)
                size = min(size, SIZING * 2.0)  # cap 2×
            except Exception:
                pass

        pnl = ret_log * size * 100   # en %

        dt  = pd.Timestamp(dt_arr[si])
        key = f"{dt.year}-{dt.month:02d}"
        results[key].append({
            "pnl": pnl,
            "ret": ret_log,
            "win": int(ret_log > 0),
            "size": size,
        })

    return dict(results)


def _monthly_stats(trades: list) -> dict:
    if not trades:
        return {"n": 0, "pnl": 0.0, "wr": 0.0, "pf": 0.0}
    n    = len(trades)
    pnl  = sum(t["pnl"] for t in trades)
    wins = [t["ret"] for t in trades if t["win"]]
    loss = [abs(t["ret"]) for t in trades if not t["win"]]
    wr   = len(wins) / n
    pf   = sum(wins) / max(sum(loss), 1e-9)
    return {"n": n, "pnl": round(pnl, 3), "wr": round(wr, 3), "pf": round(pf, 3)}


# ─── Walk-forward principal ───────────────────────────────────────────────────

def run_walkforward(
    symbols:    List[str],
    test_years: List[int],
    verbose:    bool = True,
) -> dict:
    """
    Pour chaque (symbol, test_year) :
      - Entraîne BASELINE (v2 : SMOTE global, pas de ReturnPredictor)
      - Entraîne V3      (regime-aware SMOTE + ReturnPredictor)
      - Simule les deux sur test_year
      - Accumule les résultats

    Retourne un dict de résultats pour analyse.
    """
    all_results: dict = {
        "baseline": defaultdict(lambda: defaultdict(list)),
        "v3":       defaultdict(lambda: defaultdict(list)),
    }
    timing: dict = {}

    for sym in symbols:
        path = ENRICHED_DIR / f"{sym}_1h_enriched.parquet"
        if not path.exists():
            print(f"  [{sym}] parquet absent — skip")
            continue

        sname = sym.replace("USDT", "")
        print(f"\n{'─'*70}")
        print(f"  {sym}")
        print(f"{'─'*70}")

        # Charger le parquet une fois
        df_full = pd.read_parquet(path)
        df_full["datetime"] = pd.to_datetime(df_full["datetime"], utc=True)
        if "Close" not in df_full.columns and "close" in df_full.columns:
            df_full["Close"] = df_full["close"]
        df_full = _add_rv_aliases(df_full)
        df_full = df_full.sort_values("datetime").reset_index(drop=True)
        df_full = compute_label_columns(df_full)
        df_full = compute_long_regime_col(df_full)

        years_col = df_full["datetime"].dt.year.values
        dt_col    = pd.to_datetime(df_full["datetime"], utc=True)

        for test_year in test_years:
            t_yr = time.time()
            print(f"\n  [Test {test_year}]  train sur <{test_year}  cal H2-{test_year-1}  test {test_year}")

            # Masques stricts
            cal_mask = (years_col == test_year - 1) & (dt_col.dt.month >= 7)
            tr_mask  = (years_col < test_year) & ~cal_mask

            if tr_mask.sum() < 1000:
                print(f"    Skip : train trop court ({tr_mask.sum()} barres)")
                continue

            # Labels (calibrés sur train uniquement)
            try:
                df_full, _ = build_labels(df_full, tr_mask)
            except Exception as e:
                print(f"    build_labels error: {e}")
                continue

            # Feature discovery
            avail_feats = get_available_features(df_full, FEATURES_INST_LONG, min_fill=0.75)
            if len(avail_feats) < 10:
                print(f"    Trop peu de features ({len(avail_feats)})")
                continue

            n_pos_tr = int((df_full["y_long"].values[tr_mask] == 1).sum())
            print(f"    n_train={tr_mask.sum():,}  n_pos={n_pos_tr:,}  feats={len(avail_feats)}")

            # ── VERSION BASELINE (v2 : SMOTE global) ─────────────────────────
            t_bl = time.time()
            df_tr_bl = df_full.loc[tr_mask].copy()
            if 30 <= n_pos_tr < 4000:
                try:
                    mult = min(3, max(1, 2000 // max(n_pos_tr, 1)))
                    df_tr_bl = augment_positives(
                        df_tr_bl, features=avail_feats,
                        label_col="y_long", multiplier=mult,
                    )
                except Exception:
                    pass

            fleet_bl = TRMFleetLongV4(features=avail_feats)
            df_cal   = df_full.loc[cal_mask].copy()
            try:
                fleet_bl.train(
                    df_tr_bl, np.ones(len(df_tr_bl), dtype=bool),
                    df_val_btc=df_cal,
                    val_mask_in_btc=np.ones(len(df_cal), dtype=bool),
                    label_col="y_long",
                )
            except Exception as e:
                print(f"    [BL] train error: {e}")
                fleet_bl = None

            thr_bl = {"general": 0.55}
            if fleet_bl is not None and len(df_cal) > 20:
                try:
                    ret_cal = df_cal[TARGET_COL].fillna(0.0).values
                    thr_bl  = calibrate_context_thresholds_v4(
                        fleet_bl, df_cal,
                        filter_p=np.ones(len(df_cal)), filter_thr=0.50,
                        ret_cal=ret_cal, cost_pct=COST_PCT,
                    )
                except Exception:
                    pass

            elapsed_bl = time.time() - t_bl

            # ── VERSION V3 (regime-aware SMOTE + ReturnPredictor) ─────────────
            t_v3 = time.time()
            df_tr_v3 = df_full.loc[tr_mask].copy()
            if 30 <= n_pos_tr < 4000:
                try:
                    df_tr_v3 = regime_aware_augment(
                        df_tr_v3, features=avail_feats,
                        label_col="y_long", regime_col="regime_long",
                        global_target_pos=3000,
                    )
                except Exception:
                    pass

            fleet_v3 = TRMFleetLongV4(features=avail_feats)
            try:
                fleet_v3.train(
                    df_tr_v3, np.ones(len(df_tr_v3), dtype=bool),
                    df_val_btc=df_cal,
                    val_mask_in_btc=np.ones(len(df_cal), dtype=bool),
                    label_col="y_long",
                )
            except Exception as e:
                print(f"    [V3] train error: {e}")
                fleet_v3 = None

            thr_v3 = {"general": 0.55}
            if fleet_v3 is not None and len(df_cal) > 20:
                try:
                    ret_cal = df_cal[TARGET_COL].fillna(0.0).values
                    thr_v3  = calibrate_context_thresholds_v4(
                        fleet_v3, df_cal,
                        filter_p=np.ones(len(df_cal)), filter_thr=0.50,
                        ret_cal=ret_cal, cost_pct=COST_PCT,
                    )
                except Exception:
                    pass

            # ReturnPredictor
            ret_pred_v3 = ReturnPredictor()
            try:
                ret_pred_v3.fit(df_full, avail_feats, tr_mask)
            except Exception:
                ret_pred_v3 = None

            elapsed_v3 = time.time() - t_v3

            print(f"    Train : BL={elapsed_bl:.0f}s  V3={elapsed_v3:.0f}s")

            # ── Simulation sur test_year ──────────────────────────────────────
            thr_floor = fleet_bl.adaptive_threshold() if fleet_bl else 0.55

            res_bl = {}
            if fleet_bl is not None:
                res_bl = _simulate_year(df_full, test_year, fleet_bl, thr_bl,
                                        None, thr_floor, "BL")
            res_v3 = {}
            if fleet_v3 is not None:
                res_v3 = _simulate_year(df_full, test_year, fleet_v3, thr_v3,
                                        ret_pred_v3, thr_floor, "V3")

            # Accumuler
            for month_key, trades in res_bl.items():
                all_results["baseline"][month_key][sname].extend(trades)
            for month_key, trades in res_v3.items():
                all_results["v3"][month_key][sname].extend(trades)

            # Stats rapides par année
            bl_pnl  = sum(t["pnl"] for ts in res_bl.values() for t in ts)
            v3_pnl  = sum(t["pnl"] for ts in res_v3.values() for t in ts)
            bl_n    = sum(len(ts) for ts in res_bl.values())
            v3_n    = sum(len(ts) for ts in res_v3.values())
            bl_wr   = (sum(t["win"] for ts in res_bl.values() for t in ts) / max(bl_n, 1))
            v3_wr   = (sum(t["win"] for ts in res_v3.values() for t in ts) / max(v3_n, 1))
            delta   = v3_pnl - bl_pnl

            print(f"    {test_year} BL : n={bl_n:>4}  PnL={bl_pnl:>+7.1f}%  WR={bl_wr:.1%}")
            print(f"    {test_year} V3 : n={v3_n:>4}  PnL={v3_pnl:>+7.1f}%  WR={v3_wr:.1%}  Δ={delta:>+7.1f}%")

            timing[(sym, test_year)] = time.time() - t_yr

    return dict(all_results)


# ─── Rapport final ────────────────────────────────────────────────────────────

def print_report(results: dict) -> None:
    SYMS = ["BTC","ETH","BNB","SOL","XRP","DOGE","ADA","AVAX","DOT","LINK"]
    baseline = results.get("baseline", {})
    v3       = results.get("v3", {})

    all_months = sorted(set(list(baseline.keys()) + list(v3.keys())))

    print("\n" + "═"*110)
    print("  WALK-FORWARD V3 — Résultats comparatifs BASELINE vs V3")
    print("  Sizing 25% | Coût 10bps | Val split propre (train < test, cal = H2 précédent)")
    print("═"*110)
    print(f"  {'Mois':<10} {'BL PnL':>9} {'V3 PnL':>9} {'Δ':>8}  {'BL n':>5} {'V3 n':>5}  {'BL WR':>6} {'V3 WR':>6}")
    print("  " + "─"*100)

    annual_bl: dict = defaultdict(lambda: {"pnl": 0.0, "n": 0, "wins": 0, "n_v3": 0})
    annual_v3: dict = defaultdict(lambda: {"pnl": 0.0, "n": 0, "wins": 0})

    for mo in all_months:
        bl_trades = [t for sym_trades in baseline.get(mo, {}).values() for t in sym_trades]
        v3_trades = [t for sym_trades in v3.get(mo, {}).values() for t in sym_trades]

        bl_s = _monthly_stats(bl_trades)
        v3_s = _monthly_stats(v3_trades)
        delta = v3_s["pnl"] - bl_s["pnl"]

        yr = int(mo.split("-")[0])
        annual_bl[yr]["pnl"] += bl_s["pnl"]
        annual_bl[yr]["n"]   += bl_s["n"]
        annual_v3[yr]["pnl"] += v3_s["pnl"]
        annual_v3[yr]["n"]   += v3_s["n"]

        sign = "✓" if delta > 0 else " "
        print(f"  {mo:<10} {bl_s['pnl']:>+8.1f}% {v3_s['pnl']:>+8.1f}% {delta:>+7.1f}%{sign}"
              f"  {bl_s['n']:>5} {v3_s['n']:>5}  {bl_s['wr']:>5.1%} {v3_s['wr']:>5.1%}")

        if mo.endswith("-12") or mo == all_months[-1]:
            bl_y = annual_bl[yr]
            v3_y = annual_v3[yr]
            dy   = v3_y["pnl"] - bl_y["pnl"]
            print("  " + "─"*100)
            print(f"  {yr} TOTAL{'':4}  {bl_y['pnl']:>+8.1f}%{' '*9}{dy:>+7.1f}%  "
                  f"{bl_y['n']:>5} {v3_y['n']:>5}  "
                  f"  ≈ {bl_y['pnl']/12:>+.1f}%/mois BL  {v3_y['pnl']/12:>+.1f}%/mois V3")
            print("  " + "═"*100)

    # Total toutes années
    total_bl = sum(d["pnl"] for d in annual_bl.values())
    total_v3 = sum(d["pnl"] for d in annual_v3.values())
    total_n_bl = sum(d["n"] for d in annual_bl.values())
    total_n_v3 = sum(d["n"] for d in annual_v3.values())
    n_months   = len({mo[:7] for mo in all_months})

    print(f"\n  TOTAL ALL YEARS :")
    print(f"    Baseline : {total_bl:>+9.1f}%  avg {total_bl/max(n_months,1):>+.1f}%/mois  n={total_n_bl}")
    print(f"    V3       : {total_v3:>+9.1f}%  avg {total_v3/max(n_months,1):>+.1f}%/mois  n={total_n_v3}")
    print(f"    Δ        : {total_v3-total_bl:>+9.1f}%  ({(total_v3-total_bl)/max(n_months,1):>+.2f}%/mois)")
    print(f"\n  Verdict : {'V3 > Baseline ✓' if total_v3 > total_bl else 'V3 ≤ Baseline — revoir les hyperparamètres'}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols",  nargs="+", default=None)
    parser.add_argument("--years",    nargs="+", type=int, default=None)
    parser.add_argument("--save",     action="store_true", help="Sauvegarder JSON")
    args = parser.parse_args()

    t_start = time.time()
    now     = datetime.now(timezone.utc)

    symbols    = args.symbols or TOP_10
    test_years = args.years   or [2021, 2022, 2023, 2024, 2025]
    available  = [s for s in symbols
                  if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]

    print("=" * 70)
    print("  WALK-FORWARD V3 — Validation des 3 changements structurels")
    print(f"  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Assets : {', '.join(s.replace('USDT','') for s in available)}")
    print(f"  Années test : {test_years}")
    print("=" * 70)

    results = run_walkforward(available, test_years, verbose=True)
    print_report(results)

    elapsed = time.time() - t_start
    print(f"\n  Durée totale : {elapsed/60:.0f}m{elapsed%60:.0f}s")

    if args.save:
        out = RESULTS_DIR / f"wf_v3_{now.strftime('%Y%m%d_%H%M')}.json"
        # Convertir les defaultdicts pour la sérialisation
        serializable = {
            ver: {mo: {sym: trades for sym, trades in syms.items()}
                  for mo, syms in months.items()}
            for ver, months in results.items()
        }
        out.write_text(json.dumps(serializable, indent=2, default=str))
        print(f"  Sauvegardé : {out}")


if __name__ == "__main__":
    main()
