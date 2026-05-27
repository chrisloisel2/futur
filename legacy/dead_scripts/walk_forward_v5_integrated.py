#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/walk_forward_v5_integrated.py — Walk-Forward v5 + 6 Infrastructure Layers
===================================================================================

Même training que walk_forward_v5.py (TRM Fleet 100 modèles), mais le backtest
passe par les 6 nouvelles couches d'infrastructure :

  L1 CompositeRegime  → HMM + VolFSM + LiquidityStress → sizing multipliers
  L2 AlphaRegistry    → micro-alphas comme signal complémentaire (optionnel)
  L3 MetaSuppressor   → PANIC hard-block + OOD + stress penalty → BLOCKED/REDUCED/ALLOWED
  L5 SlippageModel    → coût ajusté selon vol/spread/impact
  L6 PortfolioVaR     → VaR 95/99% + CVaR
  L6 KillSwitch       → halt intraday/weekly/VaR_breach
  L6 DynamicSizer     → vol targeting + Kelly fraction

Comparaison :
  Baseline v5 : prédictions TRM → filtre regime_long → trade/no-trade
  Intégré     : + CompositeRegime sizing + MetaSuppressor gate + KillSwitch + DynamicSizer

Usage :
  python scripts/walk_forward_v5_integrated.py
  python scripts/walk_forward_v5_integrated.py --folds 2023,2024,2025
  python scripts/walk_forward_v5_integrated.py --no-baseline   # skip baseline
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

# Réutilise le pipeline v5 existant (training + calibration)
from scripts.walk_forward_v5 import (
    load_symbol, _select_features, _backtest,
    COST_PCT, DEPLOY_PF, CATASTROPHIC_PF, MIN_TRADES,
    DATA_DIR, SYMBOLS, PRIMARY_SYM, _BASE_FEATURES,
)
from ai.level_2.trm_fleet_long_v4 import (
    TRMFleetLongV4, calibrate_context_thresholds_v4, classify_context_v4,
    TEMPORAL_HORIZONS_V4, MOVEMENT_ARCHETYPES_V4,
)
from ai.level_0.labels import (
    compute_label_columns, build_labels, compute_long_regime_col,
)
from ai.level_0.constants import TARGET_COL, CLOSE_COL

# Nouvelles couches infrastructure
from ai.regime.composite import CompositeRegime, RegimeState
from ai.regime.vol_state_machine import VolatilityFSM
from ai.regime.liquidity_stress import LiquidityStressEngine
from ai.meta.ood_detector import OODDetector
from ai.meta.suppressor import MetaSuppressor
from risk.portfolio_var import PortfolioVaR
from risk.dynamic_sizing import DynamicSizer
from risk.kill_switch import KillSwitch
from execution.slippage_model import SlippageModel
from research.experiment_tracker import ExperimentTracker, tracker
from research.drift_detector import DriftDetector

# ─────────────────────────────────────────────────────────────────────────────
# Feature engineering identique à test_infrastructure_layers.py
# ─────────────────────────────────────────────────────────────────────────────

INFRA_FEATURES = [
    "rv_24", "rv_72", "rv_ratio_24_72",
    "mom_logret_72", "oi_acceleration_z", "funding_rate_z_72",
    "dist_ema_50", "dist_ema_200", "atr_pct_14",
    "liq_long_spike_12", "liq_short_spike_12",
    "intrabar_range_pct",
]

def _add_infra_features(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute/alias les features nécessaires aux couches d'infrastructure."""
    df = df.copy()
    lc = np.log(df["close"].clip(lower=1e-9))
    r1 = lc.diff()

    if "rv_24" not in df.columns:
        df["rv_24"]  = r1.rolling(24).std()
    if "rv_72" not in df.columns:
        df["rv_72"]  = r1.rolling(72).std()
    df["rv_ratio_24_72"] = (df["rv_24"] / df["rv_72"].clip(lower=1e-9)).fillna(1.0)

    if "mom_logret_72" not in df.columns:
        df["mom_logret_72"] = lc - lc.shift(72)

    # Alias colonnes data_out → noms attendus par infrastructure
    if "atr_pct_14" not in df.columns and "atr_14" in df.columns:
        df["atr_pct_14"] = df["atr_14"] / df["close"]

    if "oi_acceleration_z" not in df.columns:
        oi_col = next((c for c in ["oi_chg_60m", "oi_chg_240m", "oi_sum"] if c in df.columns), None)
        if oi_col:
            oi = df[oi_col].fillna(0.0)
            mu, sig = oi.rolling(72).mean(), oi.rolling(72).std().clip(lower=1e-9)
            df["oi_acceleration_z"] = (oi - mu) / sig
        else:
            df["oi_acceleration_z"] = 0.0

    if "funding_rate_z_72" not in df.columns:
        fr_col = next((c for c in ["funding_z_7d", "funding_rate"] if c in df.columns), None)
        if fr_col:
            fr = df[fr_col].fillna(0.0)
            mu, sig = fr.rolling(72).mean(), fr.rolling(72).std().clip(lower=1e-9)
            df["funding_rate_z_72"] = (fr - mu) / sig
        else:
            df["funding_rate_z_72"] = 0.0

    # EMA distances
    if "dist_ema_50" not in df.columns:
        ema50  = df["close"].ewm(span=50, adjust=False).mean()
        df["dist_ema_50"] = (df["close"] - ema50) / ema50.clip(lower=1.0)
    if "dist_ema_200" not in df.columns:
        ema200 = df["close"].ewm(span=200, adjust=False).mean()
        df["dist_ema_200"] = (df["close"] - ema200) / ema200.clip(lower=1.0)

    # Intrabar range (bid-ask proxy)
    if "intrabar_range_pct" not in df.columns:
        rng = "hl_range_pct" if "hl_range_pct" in df.columns else None
        if rng:
            df["intrabar_range_pct"] = df[rng]
        elif all(c in df.columns for c in ["high", "low"]):
            df["intrabar_range_pct"] = (df["high"] - df["low"]) / df["close"]
        else:
            df["intrabar_range_pct"] = 0.01

    # Liquidation spikes (proxy depuis intrabar range z-score si absent)
    if "liq_long_spike_12" not in df.columns:
        rng_z = (df["intrabar_range_pct"] - df["intrabar_range_pct"].rolling(72).mean()) \
                / df["intrabar_range_pct"].rolling(72).std().clip(lower=1e-6)
        df["liq_long_spike_12"]  = rng_z.clip(lower=0)
        df["liq_short_spike_12"] = rng_z.clip(lower=0)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Backtest intégré
# ─────────────────────────────────────────────────────────────────────────────

_KS_POSITION_SIZE = 0.02   # 2% of capital per signal for KillSwitch PnL tracking


def _backtest_integrated(
    df_test:    pd.DataFrame,
    fleet:      TRMFleetLongV4,
    thresholds: Dict[str, float],
    features:   List[str],
    composite:  CompositeRegime,
    suppressor: MetaSuppressor,
    sizer:      DynamicSizer,
    slip_model: SlippageModel,
    hmm_probs:  Optional[np.ndarray] = None,   # pre-computed (n, K) or None
    cost_pct:   float = COST_PCT,
) -> Dict:
    """
    Backtest avec filtrage multi-couche.

    KillSwitch logic:
      - checked BEFORE computing trade PnL (correct causal order)
      - updated with actual completed trade PnL × fixed 2% capital allocation
      - separate from DynamicSizer which only affects trade PnL scaling

    HMM probs:
      - optionally pre-computed on full test set for speed (batch forward pass)
      - if None, computed per-bar (slower)
    """
    n = len(df_test)
    if n == 0:
        return _empty_result()

    ones   = np.ones(n, dtype=bool)
    p_all  = fleet.predict(df_test, ones)
    ctx    = classify_context_v4(df_test)

    tradeable = ones.copy()
    if "regime_long" in df_test.columns:
        tradeable &= (df_test["regime_long"].values != "NO_LONG")

    rets = df_test[TARGET_COL].fillna(0.0).values if TARGET_COL in df_test.columns \
           else np.zeros(n)

    kill_sw = KillSwitch()
    pvar    = PortfolioVaR()

    baseline_rets:   List[float] = []
    integrated_rets: List[float] = []
    integrated_sizes: List[float] = []

    stats = {
        "n_blocked": 0, "n_reduced": 0, "n_ks_halted": 0,
        "regime_counts": {r.value: 0 for r in RegimeState},
    }

    for i in range(n):
        ret_8h  = float(rets[i])

        pvar.update(ret_8h)
        var_rep = pvar.report() if pvar.n_obs >= 30 else pvar.empty_report()

        # L6 KillSwitch — avancer le temps à chaque barre (pour expiration des halts)
        kill_sw._bar_index = i
        ks_dec = kill_sw.check()

        if not tradeable[i]:
            continue

        thr = thresholds.get(str(ctx[i]), thresholds.get("general", 0.54))
        if p_all[i] < thr:
            continue

        # Baseline trade (identique à _backtest v5)
        baseline_rets.append(ret_8h - cost_pct)

        # ─── Infrastructure layers ────────────────────────────────────────
        # L6 KillSwitch gate
        if not ks_dec.allow_trading:
            stats["n_ks_halted"] += 1
            continue

        bar_dict = df_test.iloc[i].to_dict()

        # L1 CompositeRegime — utilise les probs HMM pré-calculées si disponibles
        if hmm_probs is not None:
            regime = composite.classify(bar_dict, hmm_probs=hmm_probs[i])
        else:
            regime = composite.classify(bar_dict)
        stats["regime_counts"][regime.value] += 1
        mults  = composite.sizing_multipliers(regime)
        regime_size_mult = mults.get("long", 0.5)

        # L3 MetaSuppressor
        supp = suppressor.evaluate(bar_dict, regime=regime.value, side="long")
        if not supp.allow:
            stats["n_blocked"] += 1
            continue
        if supp.level == "REDUCED":
            stats["n_reduced"] += 1

        # L5 Slippage-adjusted cost
        slip_est = slip_model.predict_from_bar(bar_dict, quantity_frac=0.001)
        eff_cost = cost_pct + slip_est.pct

        # L6 DynamicSizer → multiplicateur de taille (pour PnL scaling uniquement)
        rv24 = float(bar_dict.get("rv_24", 0.02))
        liq_mult = composite.liq.size_multiplier(bar_dict)
        size_result = sizer.compute_size(
            base_size=1.0,
            vol_24h=rv24,
            liquidity_mult=liq_mult,
            regime_mult=regime_size_mult,
        )
        final_size = size_result.final_size * supp.size_multiplier

        # Trade validé — PnL avec taille dynamique (coût scalé par la taille)
        trade_ret = (ret_8h - eff_cost) * final_size
        integrated_rets.append(trade_ret)
        integrated_sizes.append(final_size)

        # Update KillSwitch avec le PnL réel du trade (allocation fixe 2% capital)
        trade_portfolio_pnl = (ret_8h - eff_cost) * _KS_POSITION_SIZE
        kill_sw.update(i, trade_portfolio_pnl, var_rep)

    baseline  = _compute_metrics(baseline_rets)
    integrated = _compute_metrics(integrated_rets)

    return {
        "baseline":   baseline,
        "integrated": integrated,
        "stats":      stats,
        "mean_size":  float(np.mean(integrated_sizes)) if integrated_sizes else 0.0,
    }


def _empty_result() -> Dict:
    empty = {"n_trades": 0, "pf": 0.0, "wr": 0.0, "expectancy": 0.0,
             "max_drawdown": 0.0, "total_pnl": 0.0}
    return {"baseline": empty, "integrated": empty, "stats": {}, "mean_size": 0.0}


def _compute_metrics(trade_rets: List[float]) -> Dict:
    if not trade_rets:
        return {"n_trades": 0, "pf": 0.0, "wr": 0.0, "expectancy": 0.0,
                "max_drawdown": 0.0, "total_pnl": 0.0}
    arr   = np.array(trade_rets)
    wins  = arr[arr > 0];  losses = arr[arr < 0]
    gw    = float(wins.sum())        if len(wins)   else 0.0
    gl    = float(abs(losses.sum())) if len(losses) else 0.0
    pf    = gw / max(gl, 1e-9)
    wr    = len(wins) / len(arr)
    eq    = np.cumprod(1.0 + arr * 0.01)
    peak  = np.maximum.accumulate(eq)
    dd    = float(abs(((eq - peak) / np.maximum(peak, 1e-9)).min())) * 100
    return {
        "n_trades":    len(arr),
        "pf":          round(pf, 3),
        "wr":          round(wr, 3),
        "expectancy":  round(float(arr.mean()) * 100, 4),
        "max_drawdown":round(dd, 2),
        "total_pnl":   round(float(arr.sum()), 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Run fold avec infrastructure
# ─────────────────────────────────────────────────────────────────────────────

def run_fold_integrated(
    df_primary: pd.DataFrame,
    extra_dfs:  List[pd.DataFrame],
    test_year:  int,
    features:   List[str],
    label_col:  str = "y_long",
) -> Dict:
    years  = df_primary["datetime"].dt.year.values
    tr_msk = years <= (test_year - 2)
    va_msk = years == (test_year - 1)
    te_msk = years == test_year

    n_tr, n_va, n_te = tr_msk.sum(), va_msk.sum(), te_msk.sum()
    if n_tr < 1000 or n_va < 500 or n_te < 500:
        return {"year": test_year, "skip": True,
                "reason": f"data trop courte: tr={n_tr} va={n_va} te={n_te}"}

    print(f"\n  ── Fold {test_year}  "
          f"[train≤{test_year-2}: {n_tr:,}]  "
          f"[val {test_year-1}: {n_va:,}]  "
          f"[test {test_year}: {n_te:,}]")

    # Labels (identique v5)
    df_full = compute_label_columns(df_primary)
    df_full = compute_long_regime_col(df_full)
    df_full, _ = build_labels(
        df_full, tr_msk,
        tradeable_quantile=0.72,
        gray_zone_factor=0.05,
        use_reversal_filter=False,
        use_long_reversal_filter=False,
    )

    # Features infra sur tout le dataset
    df_full = _add_infra_features(df_full)

    # Multi-actif train
    dfs_train = [df_full.loc[tr_msk].copy()]
    for df_ex in extra_dfs:
        yr_ex = df_ex["datetime"].dt.year.values
        msk_ex = yr_ex <= (test_year - 2)
        if msk_ex.sum() < 500:
            continue
        try:
            df_ex2 = compute_label_columns(df_ex)
            df_ex2 = compute_long_regime_col(df_ex2)
            df_ex2, _ = build_labels(df_ex2, msk_ex, tradeable_quantile=0.72,
                                     gray_zone_factor=0.05, use_reversal_filter=False,
                                     use_long_reversal_filter=False)
            dfs_train.append(df_ex2.loc[msk_ex].copy())
        except Exception:
            continue

    df_train = pd.concat(dfs_train, ignore_index=True)
    feat = _select_features(df_train, features)
    print(f"   Pool training : {len(dfs_train)} actifs | Features : {len(feat)}")

    # ── TRM Fleet training ────────────────────────────────────────────────────
    df_val = df_full.loc[va_msk].copy()
    fleet  = TRMFleetLongV4(features=feat)
    fleet.train(
        df_train, np.ones(len(df_train), dtype=bool),
        df_val_btc=df_val,
        val_mask_in_btc=np.ones(len(df_val), dtype=bool),
        label_col=label_col,
    )

    ret_val   = df_val[TARGET_COL].fillna(0.0).values if TARGET_COL in df_val.columns \
                else np.zeros(len(df_val))
    thresholds = calibrate_context_thresholds_v4(
        fleet, df_val, filter_p=np.ones(len(df_val)), filter_thr=0.50,
        ret_val=ret_val, cost_pct=COST_PCT,
    )
    adapt      = fleet.adaptive_threshold()
    thresholds = {k: max(v, adapt) for k, v in thresholds.items()}

    # ── Infrastructure setup (fit sur training data) ──────────────────────────
    df_tr_infra = df_full.loc[tr_msk].copy()

    infra_feats_avail = [f for f in INFRA_FEATURES if f in df_tr_infra.columns]

    print(f"   Infra setup : fitting HMM + OOD + DriftDetector sur {len(df_tr_infra):,} barres")

    composite = CompositeRegime()
    try:
        composite.fit(df_tr_infra, train_mask=None)
        print(f"   ✓ HMM entraîné")
    except Exception as e:
        print(f"   ⚠  HMM fit error: {e} → rule-based fallback")

    ood = OODDetector()
    X_tr = df_tr_infra[infra_feats_avail].fillna(0).values
    if len(X_tr) > 50:
        ood.fit(X_tr)
        print(f"   ✓ OOD calibré | threshold={ood._threshold:.3f}")

    suppressor = MetaSuppressor(ood_detector=ood)

    drift_det = DriftDetector()
    drift_det.fit(df_tr_infra[infra_feats_avail], feature_cols=infra_feats_avail)

    sizer     = DynamicSizer(target_annual_vol=0.15, max_size=2.0, kelly_fraction=0.25)
    slip_model= SlippageModel()

    # ── Backtest test ─────────────────────────────────────────────────────────
    df_test = df_full.loc[te_msk].copy()

    # Pre-calcul des probs HMM en batch sur tout df_test (beaucoup plus rapide)
    hmm_probs_batch = None
    if composite.hmm._fitted:
        try:
            hmm_probs_batch = composite.hmm.predict_proba(df_test)
            print(f"   ✓ HMM probs pré-calculées ({len(df_test):,} barres batch)")
        except Exception as e:
            print(f"   ⚠  HMM batch predict error: {e}")

    # Baseline (identique v5)
    res_base = _backtest(df_test, fleet, thresholds, feat)

    # Intégré avec infrastructure
    res_int  = _backtest_integrated(
        df_test, fleet, thresholds, feat,
        composite=composite, suppressor=suppressor,
        sizer=sizer, slip_model=slip_model,
        hmm_probs=hmm_probs_batch,
    )

    # Statut des deux backtests
    def _status(n, pf, dd):
        if n < MIN_TRADES:     return "NO_TRADES"
        if pf < CATASTROPHIC_PF or dd > 20.0: return "CATASTROPHIC"
        if pf >= DEPLOY_PF:    return "OK"
        return "WEAK"

    b = res_base
    t = res_int["integrated"]

    base_status = _status(b["n_trades"], b["pf"], b["max_drawdown"])
    int_status  = _status(t["n_trades"], t["pf"], t["max_drawdown"])

    prices = df_test["close"].dropna()
    bh     = 0.0
    if len(prices) > 1:
        bh = (float(prices.iloc[-1]) - float(prices.iloc[0])) / float(prices.iloc[0]) * 100

    s = res_int["stats"]
    dominant_regime = max(s.get("regime_counts", {}).items(), key=lambda x: x[1], default=("?", 0))

    print(
        f"  [{test_year}] BASE  [{base_status:^12}] "
        f"n={b['n_trades']:4d}  PF={b['pf']:.3f}  WR={b['wr']:.0%}  DD={b['max_drawdown']:.1f}%"
    )
    print(
        f"  [{test_year}] INFRA [{int_status:^12}] "
        f"n={t['n_trades']:4d}  PF={t['pf']:.3f}  WR={t['wr']:.0%}  DD={t['max_drawdown']:.1f}%"
        f"  size={res_int['mean_size']:.2f}"
        f"  blk={s.get('n_blocked', 0)}  red={s.get('n_reduced', 0)}  ks={s.get('n_ks_halted', 0)}"
    )

    return {
        "year":         test_year,
        "skip":         False,
        "baseline":     {**res_base, "status": base_status},
        "integrated":   {**t,        "status": int_status},
        "infra_stats":  s,
        "mean_size":    res_int["mean_size"],
        "bh_pct":       round(bh, 2),
        "dominant_regime": dominant_regime[0],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rapport comparatif
# ─────────────────────────────────────────────────────────────────────────────

_STATUS_ICON = {
    "OK": "✓ OK", "WEAK": "~ WEAK", "CATASTROPHIC": "✗ CATA",
    "NO_TRADES": "0 NONE", None: "? ?",
}
_DELTA = lambda a, b: (b - a) if a != 0 else 0.0


def _print_comparison(fold_results: List[Dict]) -> Dict:
    valid = [f for f in fold_results if not f.get("skip")]
    n_tot = len(valid)
    if n_tot == 0:
        print("Aucun fold valide")
        return {}

    print()
    print("=" * 108)
    print("  RAPPORT COMPARATIF — v5 Baseline vs v5 + Infrastructure Layers")
    print("=" * 108)
    print(
        f"\n  {'Fold':^6} │ {'─── Baseline v5 ───':^38} │ {'─── + Infrastructure ───':^38} │ {'ΔPF':>6}"
    )
    print(
        f"  {'':^6} │ {'Status':^14} {'N':>5} {'PF':>6} {'WR':>5} {'DD':>5} │"
        f" {'Status':^14} {'N':>5} {'PF':>6} {'WR':>5} {'DD':>5} │ {'':>6}"
    )
    print("  " + "─" * 106)

    base_pfs  = []
    infra_pfs = []

    for f in fold_results:
        if f.get("skip"):
            print(f"  [{f['year']}] SKIP — {f.get('reason', '')}")
            continue
        b   = f["baseline"]
        t   = f["integrated"]
        dpf = _DELTA(b["pf"], t["pf"])
        delta_str = f"{dpf:+.3f}"
        icon = "✓" if dpf >= 0 else "✗"

        print(
            f"  [{f['year']}] │ {_STATUS_ICON.get(b['status'], b['status']):^14} "
            f"{b['n_trades']:5d} {b['pf']:6.3f} {b['wr']:5.0%} {b['max_drawdown']:5.1f}% │"
            f" {_STATUS_ICON.get(t['status'], t['status']):^14} "
            f"{t['n_trades']:5d} {t['pf']:6.3f} {t['wr']:5.0%} {t['max_drawdown']:5.1f}% │"
            f" {icon} {delta_str}"
        )

        if b["n_trades"] >= MIN_TRADES:
            base_pfs.append(b["pf"])
        if t["n_trades"] >= MIN_TRADES:
            infra_pfs.append(t["pf"])

    pf_base_med  = float(np.median(base_pfs))  if base_pfs  else 0.0
    pf_infra_med = float(np.median(infra_pfs)) if infra_pfs else 0.0
    ok_base  = sum(1 for f in valid if f["baseline"]["status"]  == "OK")
    ok_infra = sum(1 for f in valid if f["integrated"]["status"] == "OK")
    ca_base  = sum(1 for f in valid if f["baseline"]["status"]  == "CATASTROPHIC")
    ca_infra = sum(1 for f in valid if f["integrated"]["status"] == "CATASTROPHIC")

    print("\n  " + "─" * 106)
    print(f"  {'':^6} │ {'':^14} {'':5} {pf_base_med:6.3f} {'':5} {'':5} │"
          f" {'':^14} {'':5} {pf_infra_med:6.3f} {'':5} {'':5} │"
          f"  {_DELTA(pf_base_med, pf_infra_med):+.3f}")
    print()
    print(f"  Folds OK    : {ok_base}/{n_tot} (base) → {ok_infra}/{n_tot} (infra)")
    print(f"  Catastroph. : {ca_base}         (base) → {ca_infra}         (infra)")
    print(f"  PF médian   : {pf_base_med:.3f}  (base) → {pf_infra_med:.3f}  (infra)  "
          f"Δ={_DELTA(pf_base_med, pf_infra_med):+.3f}")
    dep_base  = ok_base >= max(1, int(n_tot * 0.7))  and ca_base == 0  and pf_base_med >= DEPLOY_PF
    dep_infra = ok_infra >= max(1, int(n_tot * 0.7)) and ca_infra == 0 and pf_infra_med >= DEPLOY_PF
    print(f"  Deployable  : {'✓ OUI' if dep_base else '✗ NON'}  (base) → "
          f"{'✓ OUI' if dep_infra else '✗ NON'}  (infra)")

    print("\n  DÉTAIL INFRASTRUCTURE (toutes années)")
    print(f"  {'Fold':>6} │ {'Régime dom.':^14} │ {'Blocked':>7} │ {'Reduced':>7} │ "
          f"{'KS halts':>8} │ {'Moy. size':>9}")
    print("  " + "─" * 70)
    for f in fold_results:
        if f.get("skip"):
            continue
        s = f.get("infra_stats", {})
        rc = s.get("regime_counts", {})
        dom = max(rc.items(), key=lambda x: x[1], default=("?", 0))[0] if rc else "?"
        print(
            f"  [{f['year']}] │ {dom:^14} │ {s.get('n_blocked', 0):7d} │"
            f" {s.get('n_reduced', 0):7d} │ {s.get('n_ks_halted', 0):8d} │ {f.get('mean_size', 0):9.3f}"
        )

    print("=" * 108)

    return {
        "baseline":  {"pf_median": round(pf_base_med, 3),  "folds_ok": ok_base,  "cata": ca_base,  "deployable": dep_base},
        "integrated": {"pf_median": round(pf_infra_med, 3), "folds_ok": ok_infra, "cata": ca_infra, "deployable": dep_infra},
        "delta_pf":   round(_DELTA(pf_base_med, pf_infra_med), 3),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-Forward v5 Integrated")
    parser.add_argument("--folds",  type=str, default="2022,2023,2024,2025")
    parser.add_argument("--symbols",type=str, default=",".join(SYMBOLS))
    parser.add_argument("--no-baseline", action="store_true",
                        help="Skip baseline comparison (faster)")
    args = parser.parse_args()

    test_years = [int(y) for y in args.folds.split(",")]
    symbols    = [s.strip() for s in args.symbols.split(",")]
    all_years  = list(range(max(2019, min(test_years) - 3), max(test_years) + 1))

    print("=" * 108)
    print("  WALK-FORWARD v5 + INFRASTRUCTURE LAYERS — Comparaison avec Baseline")
    print("=" * 108)
    print(f"  TRM  : {len(TEMPORAL_HORIZONS_V4)}h × {len(MOVEMENT_ARCHETYPES_V4)} archétypes = 100 TRM")
    print(f"  Infra: CompositeRegime + MetaSuppressor + VaR + KillSwitch + DynamicSizer + Slippage")
    print(f"  Folds: {test_years} | Pool: {symbols}")
    print()

    # Start tracker
    run_id = tracker.start_run(
        "wf_v5_integrated",
        params={"folds": test_years, "symbols": symbols, "n_trm": 100},
        tags=["walk_forward", "infrastructure", "comparison"],
    )
    print(f"  [L7] Experiment run: {run_id}")
    print()

    # Chargement données
    print("── Chargement + resample 1m→1h …")
    dfs = {}
    for sym in symbols:
        df = load_symbol(sym, all_years)
        if df is not None:
            dfs[sym] = _add_infra_features(df)
            print(f"   ✓ {sym}: {len(dfs[sym]):,} barres | infra cols: "
                  f"{sum(1 for c in INFRA_FEATURES if c in dfs[sym].columns)}/{len(INFRA_FEATURES)}")

    if PRIMARY_SYM not in dfs:
        sys.exit(f"✗ {PRIMARY_SYM} manquant")

    df_primary  = dfs[PRIMARY_SYM]
    extra_dfs   = [v for k, v in dfs.items() if k != PRIMARY_SYM]
    feat_cands  = _select_features(df_primary, _BASE_FEATURES)
    print(f"\n  Features candidates : {len(feat_cands)}")

    # Walk-forward
    fold_results = []
    for ty in test_years:
        fr = run_fold_integrated(df_primary, extra_dfs, ty, feat_cands)
        fold_results.append(fr)

        if not fr.get("skip"):
            b, t = fr["baseline"], fr["integrated"]
            tracker.log_metrics(run_id, {
                f"{ty}_base_pf":  b["pf"],
                f"{ty}_base_n":   b["n_trades"],
                f"{ty}_infra_pf": t["pf"],
                f"{ty}_infra_n":  t["n_trades"],
                f"{ty}_delta_pf": t["pf"] - b["pf"],
                f"{ty}_ks_halts": fr["infra_stats"].get("n_ks_halted", 0),
                f"{ty}_blocked":  fr["infra_stats"].get("n_blocked", 0),
            })

    # Rapport comparatif
    verdict = _print_comparison(fold_results)

    if verdict:
        tracker.log_metrics(run_id, {
            "base_pf_median":  verdict["baseline"]["pf_median"],
            "infra_pf_median": verdict["integrated"]["pf_median"],
            "delta_pf_median": verdict["delta_pf"],
            "base_deployable": float(verdict["baseline"]["deployable"]),
            "infra_deployable": float(verdict["integrated"]["deployable"]),
        })

    tracker.end_run(run_id, "completed")
    print(f"\n  [L7] Run {run_id} loggé dans research/runs/")

    # Comparer avec les runs précédents
    print("\n  ── Comparaison avec les runs précédents ──────────────────────────────")
    try:
        all_runs = tracker.compare_runs("infra_pf_median", n_best=5)
        for r in all_runs:
            metrics = {k: v[-1]["value"] if isinstance(v, list) else v
                       for k, v in r.get("metrics", {}).items()}
            bpm = metrics.get("base_pf_median", None)
            ipm = metrics.get("infra_pf_median", None)
            if bpm is not None and ipm is not None:
                print(f"  [{r['run_id'][:12]}] {r['name']:30s} base={bpm:.3f} infra={ipm:.3f} "
                      f"Δ={ipm-bpm:+.3f}")
    except Exception as e:
        print(f"  (compare_runs: {e})")


if __name__ == "__main__":
    main()
