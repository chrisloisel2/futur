#!/usr/bin/env python3
"""
scripts/ablation_study.py — Ablation causale des features microstructure
=========================================================================

Protocole de falsification :
  1. Grille 9 combos (A=Base … H=Tout, I=MIC_ONLY sans OHLCV)
  2. Walk-forward strict 2022-2025 par combo et par asset
  3. Shuffle test : permuter 20% des labels → PF doit chuter
  4. Contrôles négatifs : BNB + XRP (ne doivent PAS monter comme SOL)
  5. Critères multi-années : years_positive ≥ 3, worst_year_pf > 0.95

Règle : l'objectif est de FALSIFIER l'hypothèse SOL PF>1.3, pas de la confirmer.

Usage :
  python scripts/ablation_study.py                          # tout
  python scripts/ablation_study.py --combos A H I          # sélectif
  python scripts/ablation_study.py --symbols SOLUSDT XRP   # assets spécifiques
  python scripts/ablation_study.py --skip-shuffle           # sans permutation
"""
from __future__ import annotations

import argparse
import time
import sys
import warnings
from collections import defaultdict
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
from ai.level_0.constants import TARGET_COL, COST_PCT, HORIZON_BARS
from scripts.live_data_update import (
    _add_cvd_features, _add_oi_features, _add_basis_features, _add_taker_flow_features,
)
from scripts.simple_model_test import _train_simple, _simulate, _pf

try:
    import lightgbm as lgb
    _HAS_LGB = True
except ImportError:
    _HAS_LGB = False

ENRICHED_DIR = ROOT / "data" / "enriched"
SIZING       = 0.25
CAL_MO       = 7

# ─── Feature groups ───────────────────────────────────────────────────────────

FEAT_BASE = [
    "return_5", "return_10", "return_20", "log_return_5", "log_return_10",
    "realized_vol_20", "atr_pct_20", "bb_width_20", "bb_percent_b_20",
    "close_position_in_range", "body_to_range",
    "distance_ema_20", "distance_ema_50", "distance_ema_200",
    "ema_slope_20", "ema_21_50_spread",
    "macd_hist", "macd_hist_slope",
    "rsi_13", "rsi_20", "stoch_k_20",
    "adx_20", "di_spread_20", "choppiness_20",
    "volume_ratio_20", "cmf_20",
    "trend_score", "momentum_score", "volatility_score",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "mtf_4h_adx_20", "mtf_4h_rsi_10", "mtf_4h_return_5",
    "mtf_1d_return_5", "mtf_1d_rsi_5",
    # Déjà présentes (v2) — partie du "base"
    "taker_buy_ratio_base", "taker_flow_imbalance_20",
    "funding_rate", "funding_rate_z_24", "funding_rate_z_72",
]

FEAT_CVD   = ["cvd_4h_z", "cvd_24h_z", "cvd_momentum"]
FEAT_OI    = ["oi_delta_8h", "oi_delta_24h", "oi_price_regime"]
FEAT_BASIS = ["basis_annualized", "basis_momentum_8h", "basis_extreme_long"]

# Grille 9 combos — A à I
COMBOS: Dict[str, List[str]] = {
    "A_base":      FEAT_BASE,
    "B_cvd":       FEAT_BASE + FEAT_CVD,
    "C_oi":        FEAT_BASE + FEAT_OI,
    "D_basis":     FEAT_BASE + FEAT_BASIS,
    "E_cvd_oi":    FEAT_BASE + FEAT_CVD + FEAT_OI,
    "F_cvd_basis": FEAT_BASE + FEAT_CVD + FEAT_BASIS,
    "G_oi_basis":  FEAT_BASE + FEAT_OI  + FEAT_BASIS,
    "H_all":       FEAT_BASE + FEAT_CVD + FEAT_OI + FEAT_BASIS,
    "I_micro_only":FEAT_CVD  + FEAT_OI  + FEAT_BASIS  # sans OHLCV — alpha pur
    + ["taker_buy_ratio_base", "taker_flow_imbalance_20",
       "funding_rate", "funding_rate_z_24", "funding_rate_z_72"],
}

ASSET_THRESHOLDS = {
    "BTCUSDT": 0.62,
    "ETHUSDT": 0.54,
    "SOLUSDT": 0.55,
    "BNBUSDT": 0.63,  # contrôle négatif
    "XRPUSDT": 0.62,  # contrôle négatif
}

# ─── Go/No-go multi-critères ──────────────────────────────────────────────────

GO_CRITERIA = {
    "pf_total":       1.20,   # PF global > 1.20
    "n_trades":       100,    # total trades ≥ 100
    "years_positive": 3,      # ≥ 3 années sur 4 avec PF > 1.0
    "worst_year_pf":  0.95,   # pire année PF ≥ 0.95
    "pf_stress":      1.15,   # PF recalculé avec 2× les coûts > 1.15
    "pf_vs_lgb":     -0.05,   # TRM PF ≥ LGB PF - 0.05 (TRM ne doit pas régresser)
    "shuffle_delta":  0.10,   # PF original - PF shuffled ≥ 0.10 (signal réel)
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_asset(sym: str) -> Optional[pd.DataFrame]:
    path = ENRICHED_DIR / f"{sym}_1h_enriched.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    if "Close" not in df.columns and "close" in df.columns:
        df["Close"] = df["close"]
    for rv_t, rv_s in [("rv_24", "realized_volatility_20"), ("rv_72", "realized_volatility_50")]:
        if rv_t not in df.columns and rv_s in df.columns:
            df[rv_t] = df[rv_s]
    df = _add_cvd_features(df)
    df = _add_oi_features(df)
    df = _add_basis_features(df)
    df = _add_taker_flow_features(df)
    df = df.sort_values("datetime").reset_index(drop=True)
    df = compute_label_columns(df)
    df = compute_long_regime_col(df)
    return df


def _pf_stress(pnls: List[float], extra_cost_pct: float = COST_PCT) -> float:
    """PF recalculé en ajoutant un coût supplémentaire à chaque trade."""
    adjusted = [p - extra_cost_pct * SIZING * 100 for p in pnls]
    wins  = [a for a in adjusted if a > 0]
    loss  = [abs(a) for a in adjusted if a < 0]
    return sum(wins) / max(sum(loss), 1e-9)


def _shuffle_test(
    df_train: pd.DataFrame,
    df_test:  pd.DataFrame,
    features: List[str],
    thr:      float,
    perm_rate: float = 0.20,
    n_runs:    int   = 5,
    seed:      int   = 42,
) -> float:
    """
    Retourne le PF moyen après permutation de perm_rate des labels.
    Si PF reste élevé après permutation → signal non robuste / leakage.
    """
    rng   = np.random.default_rng(seed)
    avail = [f for f in features if f in df_train.columns]

    y_all = df_train["y_long"].values
    valid = y_all >= 0
    X_tr  = df_train.loc[valid, avail].fillna(0.0).values
    y_base = (y_all[valid] == 1).astype(int)

    pfs = []
    for _ in range(n_runs):
        y_perm = y_base.copy()
        idx    = rng.choice(len(y_perm), size=int(len(y_perm) * perm_rate), replace=False)
        y_perm[idx] = 1 - y_perm[idx]   # flip

        if int(y_perm.sum()) < 5:
            continue
        try:
            model, scaler = _train_simple(X_tr, y_perm)
            pnls, _ = _simulate(df_test, model, scaler, avail, thr)
            pf_val, _, _ = _pf(pnls)
            pfs.append(pf_val)
        except Exception:
            pass

    return float(np.mean(pfs)) if pfs else 0.0


def _eval_combo(
    df:         pd.DataFrame,
    test_years: List[int],
    features:   List[str],
    thr:        float,
) -> Dict[str, object]:
    """
    Walk-forward sur test_years pour un combo de features.
    Retourne dict avec pf, wr, n, pnl_by_year, shuffle_pf.
    """
    years  = df["datetime"].dt.year.values
    dt_col = pd.to_datetime(df["datetime"], utc=True)
    avail  = [f for f in features if f in df.columns]

    pnls_by_year: Dict[int, List[float]] = {}
    shuffle_pfs: List[float] = []

    for test_year in test_years:
        cal_mask = (years == test_year - 1) & (dt_col.dt.month >= CAL_MO)
        tr_mask  = (years < test_year) & ~cal_mask
        tst_mask = years == test_year

        if tr_mask.sum() < 300 or tst_mask.sum() < 100:
            continue

        df_w = df.copy()
        try:
            df_w, _ = build_labels(df_w, tr_mask)
        except Exception:
            continue

        y_all = df_w["y_long"].values
        valid = y_all >= 0
        X_tr  = df_w.loc[tr_mask & valid, avail].fillna(0.0).values if len(avail) > 0 else np.empty((0, 0))
        y_tr  = (y_all[tr_mask & valid] == 1).astype(int)

        if int(y_tr.sum()) < 8 or X_tr.shape[0] == 0:
            pnls_by_year[test_year] = []
            continue

        try:
            model, scaler = _train_simple(X_tr, y_tr)
        except Exception:
            pnls_by_year[test_year] = []
            continue

        df_tst = df_w.loc[tst_mask].copy().reset_index(drop=True)
        pnls, _ = _simulate(df_tst, model, scaler, avail, thr)
        pnls_by_year[test_year] = pnls

        # Shuffle sur la dernière année pour rapidité
        if test_year == max(test_years):
            df_tr_sub = df_w.loc[tr_mask].copy().reset_index(drop=True)
            spf = _shuffle_test(df_tr_sub, df_tst, avail, thr)
            shuffle_pfs.append(spf)

    all_pnls: List[float] = [p for ps in pnls_by_year.values() for p in ps]
    pf, wr, n = _pf(all_pnls)
    years_pos  = sum(1 for ps in pnls_by_year.values() if _pf(ps)[0] > 1.0)
    worst_pf   = min((_pf(ps)[0] for ps in pnls_by_year.values() if ps), default=0.0)
    stress_pf  = _pf_stress(all_pnls)
    shuf_pf    = float(np.mean(shuffle_pfs)) if shuffle_pfs else None

    return {
        "pf": pf, "wr": wr, "n": n,
        "pnl_total": sum(all_pnls),
        "pnls_by_year": pnls_by_year,
        "years_positive": years_pos,
        "worst_year_pf": worst_pf,
        "stress_pf": stress_pf,
        "shuffle_pf": shuf_pf,
        "n_feats": len(avail),
    }


def _verdict(res: dict, lgb_pf: float = 0.0) -> Tuple[str, List[str]]:
    """GO / INCUBATE / REJECT avec liste des critères échoués."""
    failures = []
    if res["pf"] < GO_CRITERIA["pf_total"]:
        failures.append(f"pf={res['pf']:.3f}<{GO_CRITERIA['pf_total']}")
    if res["n"] < GO_CRITERIA["n_trades"]:
        failures.append(f"n={res['n']}<{GO_CRITERIA['n_trades']}")
    if res["years_positive"] < GO_CRITERIA["years_positive"]:
        failures.append(f"years_pos={res['years_positive']}<{GO_CRITERIA['years_positive']}")
    if res["worst_year_pf"] < GO_CRITERIA["worst_year_pf"]:
        failures.append(f"worst_yr={res['worst_year_pf']:.3f}<{GO_CRITERIA['worst_year_pf']}")
    if res["stress_pf"] < GO_CRITERIA["pf_stress"]:
        failures.append(f"stress_pf={res['stress_pf']:.3f}<{GO_CRITERIA['pf_stress']}")
    if lgb_pf > 0 and res["pf"] < lgb_pf + GO_CRITERIA["pf_vs_lgb"]:
        failures.append(f"pf_vs_lgb={res['pf']:.3f}<{lgb_pf+GO_CRITERIA['pf_vs_lgb']:.3f}")
    if res["shuffle_pf"] is not None:
        delta = res["pf"] - res["shuffle_pf"]
        if delta < GO_CRITERIA["shuffle_delta"]:
            failures.append(f"shuffle_delta={delta:.3f}<{GO_CRITERIA['shuffle_delta']}")

    if not failures:
        return "GO", []
    elif len(failures) <= 2 and res["pf"] > 1.0:
        return "INCUBATE", failures
    else:
        return "REJECT", failures


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_ablation(
    symbols:     List[str],
    combos:      List[str],
    test_years:  List[int],
    skip_shuffle: bool = False,
) -> None:

    print("=" * 90)
    print("  ABLATION STUDY — Falsification de l'hypothèse SOL PF>1.3")
    print(f"  Règle : une variable à la fois. Objectif : DÉTRUIRE l'hypothèse, pas la confirmer.")
    print(f"  Combos : {combos}  |  Assets : {[s.replace('USDT','') for s in symbols]}")
    print(f"  Années test : {test_years}  |  Shuffle test : {'OFF' if skip_shuffle else 'ON (20% labels)'}")
    print("=" * 90)

    # Charger assets
    dfs: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        print(f"  Chargement {sym}...", end=" ", flush=True)
        df = _load_asset(sym)
        if df is not None:
            dfs[sym] = df
            print(f"✓ ({len(df):,} barres)")
        else:
            print("✗ absent")

    if not dfs:
        print("  Aucun asset disponible.")
        return

    # ── Grille principale ─────────────────────────────────────────────────────
    all_results: Dict[str, Dict[str, dict]] = {}  # combo → sym → results

    for combo_name in combos:
        if combo_name not in COMBOS:
            print(f"\n  COMBO {combo_name} inconnu — skip")
            continue
        feats = COMBOS[combo_name]
        print(f"\n{'─'*90}")
        print(f"  COMBO {combo_name}  ({len(feats)} features max)")
        print(f"  {'Asset':<8} {'PF':>7} {'WR':>6} {'n':>5} {'PnL':>8} {'Yr+':>4} "
              f"{'WrstYr':>8} {'Stress':>8} {'Shuffle':>9} {'Verdict'}")
        print(f"  {'─'*88}")

        all_results[combo_name] = {}

        for sym in symbols:
            df = dfs.get(sym)
            if df is None:
                continue

            thr   = ASSET_THRESHOLDS.get(sym, 0.55)
            sname = sym.replace("USDT", "")
            t0    = time.time()

            if skip_shuffle:
                # Pas de shuffle : set shuffle_pf manuellement à None
                import copy
                res = _eval_combo(df, test_years, feats, thr)
                res["shuffle_pf"] = None
            else:
                res = _eval_combo(df, test_years, feats, thr)

            all_results[combo_name][sym] = res
            elapsed = time.time() - t0

            # LGB reference (combo A = baseline)
            lgb_ref = (all_results.get("A_base", {}).get(sym, {}).get("pf", 0.0)
                       if combo_name != "A_base" else 0.0)
            verdict, fails = _verdict(res, lgb_ref)

            shuf_str = f"{res['shuffle_pf']:.3f}" if res["shuffle_pf"] is not None else "  —"
            print(f"  {sname:<8} {res['pf']:>7.3f} {res['wr']:>5.1%} {res['n']:>5} "
                  f"{res['pnl_total']:>+7.1f}% {res['years_positive']:>4} "
                  f"{res['worst_year_pf']:>8.3f} {res['stress_pf']:>8.3f} {shuf_str:>9}  "
                  f"{verdict}")
            if fails:
                print(f"  {'':8}   Échecs : {', '.join(fails[:4])}")

    # ── Tableau comparatif ΔPF vs A_base ─────────────────────────────────────
    print(f"\n{'═'*90}")
    print("  TABLEAU Δ PF vs A_base (falsification)")
    print(f"  {'Combo':<15}" + "".join(f"{s.replace('USDT',''):>10}" for s in symbols))
    print("  " + "─" * 70)

    base_pf = {sym: all_results.get("A_base", {}).get(sym, {}).get("pf", 0.0) for sym in symbols}

    for combo_name in combos:
        row = f"  {combo_name:<15}"
        for sym in symbols:
            res = all_results.get(combo_name, {}).get(sym)
            if res:
                delta = res["pf"] - base_pf.get(sym, res["pf"])
                row += f"  {delta:>+7.3f}"
            else:
                row += f"  {'     —':>8}"
        print(row)

    # ── Analyse des contrôles négatifs ────────────────────────────────────────
    PRIMARIES  = {"SOLUSDT", "ETHUSDT"}
    CONTROLS   = set(symbols) - PRIMARIES
    if CONTROLS:
        print(f"\n{'─'*90}")
        print("  CONTRÔLES NÉGATIFS")
        best_combo = max(combos, key=lambda c: max(
            (all_results.get(c, {}).get(s, {}).get("pf", 0.0) for s in PRIMARIES), default=0
        ))
        sol_best_pf = all_results.get(best_combo, {}).get("SOLUSDT", {}).get("pf", 0.0)

        suspicious = False
        for sym in CONTROLS:
            ctrl_pf = all_results.get(best_combo, {}).get(sym, {}).get("pf", 0.0)
            sname   = sym.replace("USDT", "")
            gap     = sol_best_pf - ctrl_pf
            status  = "OK" if gap > 0.10 else "⚠ SUSPECT"
            print(f"  {sname}: best_combo={best_combo}  PF={ctrl_pf:.3f}  "
                  f"vs SOL={sol_best_pf:.3f}  gap={gap:+.3f}  [{status}]")
            if gap <= 0.10:
                suspicious = True

        if suspicious:
            print("  ⚠ ATTENTION : contrôle négatif monte autant que SOL → sur-ajustement probable.")
        else:
            print("  ✓ Contrôles négatifs stables → signal SOL potentiellement spécifique.")

    # ── Synthèse shuffle ────────────────────────────────────────────────────────
    if not skip_shuffle:
        print(f"\n{'─'*90}")
        print("  SYNTHÈSE SHUFFLE TEST (20% labels permutés)")
        for combo_name in combos:
            for sym in ["SOLUSDT", "ETHUSDT"]:
                if sym not in symbols:
                    continue
                res = all_results.get(combo_name, {}).get(sym, {})
                if res.get("shuffle_pf") is None:
                    continue
                orig  = res["pf"]
                shuf  = res["shuffle_pf"]
                delta = orig - shuf
                flag  = "✓ Signal réel" if delta >= GO_CRITERIA["shuffle_delta"] else "✗ Signal fragile"
                print(f"  {sym.replace('USDT',''):<6} {combo_name:<15}  "
                      f"PF={orig:.3f}  shuffle={shuf:.3f}  Δ={delta:+.3f}  {flag}")

    # ── Recommandation finale ─────────────────────────────────────────────────
    print(f"\n{'═'*90}")
    print("  RECOMMANDATION")

    # Meilleur combo sur SOL
    if "SOLUSDT" in dfs:
        sol_results = {c: all_results.get(c, {}).get("SOLUSDT", {}) for c in combos}
        best_c = max(sol_results, key=lambda c: sol_results[c].get("pf", 0))
        best_r = sol_results[best_c]
        verdict, fails = _verdict(best_r)
        print(f"  SOL best combo : {best_c}  PF={best_r.get('pf',0):.3f}  "
              f"n={best_r.get('n',0)}  → {verdict}")
        if fails:
            print(f"  Critères manquants : {', '.join(fails)}")

    print(f"\n  Source d'alpha identifiée si GO : relancer walkforward_v3.py avec combo gagnant.")
    print(f"  Logger dans experiment_log.py avant tout retrain TRM.")
    print("=" * 90)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols",
                        nargs="+",
                        default=["SOLUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT"])
    parser.add_argument("--combos",
                        nargs="+",
                        default=list(COMBOS.keys()))
    parser.add_argument("--years",
                        nargs="+",
                        type=int,
                        default=[2022, 2023, 2024, 2025])
    parser.add_argument("--skip-shuffle",
                        action="store_true",
                        help="Désactiver le shuffle test (plus rapide)")
    args = parser.parse_args()

    available = [s for s in args.symbols
                 if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]
    valid_combos = [c for c in args.combos if c in COMBOS]

    if not valid_combos:
        print(f"Combos disponibles : {list(COMBOS.keys())}")
        sys.exit(1)

    run_ablation(available, valid_combos, args.years, args.skip_shuffle)


if __name__ == "__main__":
    main()
