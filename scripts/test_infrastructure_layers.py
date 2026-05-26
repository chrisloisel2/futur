#!/usr/bin/env python3
"""
scripts/test_infrastructure_layers.py
======================================
Test complet des 6 couches d'infrastructure sur données réelles 2019-2025.
Rapport année par année, mois par mois.

Usage:
    python3 scripts/test_infrastructure_layers.py
    python3 scripts/test_infrastructure_layers.py --years 2022,2023,2024
    python3 scripts/test_infrastructure_layers.py --sym ETHUSDT
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data_out" / "result"

# ──────────────────────────────────────────────────────────────────────────────
# Imports des layers
# ──────────────────────────────────────────────────────────────────────────────
from ai.regime import (
    GaussianHMMEngine, VolatilityFSM, VolState,
    LiquidityStressEngine, CompositeRegime, RegimeState,
)
from ai.alphas import build_default_registry, AlphaRegistry
from ai.meta import OODDetector, MetaSuppressor
from risk import PortfolioVaR, CorrelationEngine, DynamicSizer, KillSwitch
from execution import SlippageModel, SmartRouter
from research import ExperimentTracker, DriftDetector

# ──────────────────────────────────────────────────────────────────────────────
# Colonnes à charger + resample (identique à walk_forward_v5)
# ──────────────────────────────────────────────────────────────────────────────
_LAST_COLS = [
    "funding_rate", "rsi_14", "atr_pct_14",
    "oi_sum", "oi_value_sum", "oi_chg_60m",
    "funding_z_7d", "funding_z_30d", "funding_extreme",
    "fear_greed", "global_long_short_ratio",
    "taker_buy_sell_ratio", "taker_buy_ratio",
    "sin_hour", "cos_hour",
    "bb_width_20", "bb_pctb_20",
    "hl_range_pct", "vol_expansion_1d",
    "oi_accel_z_1d", "oi_accel_1h",
    "ret_240m", "ret_480m",
    "rv_60m", "rv_240m", "rv_1440m",
]
_LOAD_COLS = ["timestamp", "open", "high", "low", "close", "volume"] + _LAST_COLS


def _load_year(sym: str, year: int) -> Optional[pd.DataFrame]:
    path = DATA_DIR / f"{year}_{sym}_features.parquet"
    if not path.exists():
        return None
    try:
        import pyarrow.parquet as pq
        avail = set(pq.ParquetFile(path).schema_arrow.names)
        cols  = [c for c in _LOAD_COLS if c in avail]
        df    = pd.read_parquet(path, columns=cols)
        df["datetime"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("datetime").sort_index()

        agg = {"open": "first", "high": "max", "low": "min",
               "close": "last", "volume": "sum"}
        for c in _LAST_COLS:
            if c in df.columns:
                agg[c] = "last"

        df1h = df.resample("1h").agg(agg).dropna(subset=["close"]).reset_index()
        return df1h
    except Exception as e:
        print(f"  ⚠  {year} {sym}: {e}")
        return None


def _add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule les features dérivées identiques à walk_forward_v5."""
    df = df.copy()
    c  = df["close"].ffill()
    lc = np.log(c.clip(lower=1e-9))
    r1 = lc.diff().fillna(0.0)

    # RV (identiques aux noms attendus par les layers)
    df["rv_24"]  = r1.rolling(24).std()
    df["rv_72"]  = r1.rolling(72).std()
    df["rv_ratio_24_72"] = (df["rv_24"] / df["rv_72"].clip(lower=1e-9)).fillna(1.0)

    # Momentum
    df["mom_logret_72"]  = lc - lc.shift(72)
    df["mom_logret_720"] = lc - lc.shift(720)

    # EMA
    ema50  = c.ewm(span=50,  adjust=False).mean()
    ema200 = c.ewm(span=200, adjust=False).mean()
    df["dist_ema_200"]      = (c / ema200 - 1.0)
    df["ema_spread_50_200"] = (ema50 / ema200 - 1.0)

    # Alias vers les noms attendus par les alphas/layers
    if "sin_hour"           in df.columns: df["hour_sin"] = df["sin_hour"]
    if "cos_hour"           in df.columns: df["hour_cos"] = df["cos_hour"]
    if "bb_width_20"        in df.columns: df["boll_width_20"] = df["bb_width_20"]
    if "bb_pctb_20"         in df.columns: df["boll_pos_20"]   = df["bb_pctb_20"]
    if "hl_range_pct"       in df.columns: df["intrabar_range_pct"] = df["hl_range_pct"]
    if "vol_expansion_1d"   in df.columns: df["vol_ratio_24"]  = df["vol_expansion_1d"]
    if "oi_accel_z_1d"      in df.columns: df["oi_acceleration_z"] = df["oi_accel_z_1d"]
    if "funding_z_7d"       in df.columns: df["funding_rate_z_72"]  = df["funding_z_7d"]
    if "ret_240m"           in df.columns: df["log_ret_4"] = df["ret_240m"]
    if "fear_greed"         in df.columns: df["fear_greed_value"] = df["fear_greed"]
    if "funding_z_30d"      in df.columns: df["fear_greed_value_z_72"] = df.get("fear_greed_z_30d", 0.0)

    # Liquidation spikes (proxy: hl_range_pct extrêmes z-scorés)
    if "hl_range_pct" in df.columns:
        hr = df["hl_range_pct"]
        roll_mean = hr.rolling(12).mean()
        roll_std  = hr.rolling(12).std().clip(lower=1e-6)
        spike_z   = (hr - roll_mean) / roll_std
        df["liq_long_spike_12"]  = spike_z.clip(lower=0)
        df["liq_short_spike_12"] = spike_z.clip(lower=0) * 0.6  # asymétrique

    # L/S ratio z-score (rolling)
    if "global_long_short_ratio" in df.columns:
        ls = df["global_long_short_ratio"]
        ls_mean = ls.rolling(72).mean()
        ls_std  = ls.rolling(72).std().clip(lower=1e-6)
        df["global_ls_longShortRatio_z_72"] = ((ls - ls_mean) / ls_std).fillna(0.0)

    # OI acceleration z-score si pas déjà là
    if "oi_acceleration_z" not in df.columns and "oi_chg_60m" in df.columns:
        oi_chg = df["oi_chg_60m"]
        m = oi_chg.rolling(24).mean()
        s = oi_chg.rolling(24).std().clip(lower=1e-6)
        df["oi_acceleration_z"] = ((oi_chg - m) / s).fillna(0.0)

    df["year"]  = df["datetime"].dt.year
    df["month"] = df["datetime"].dt.month
    return df.fillna(method="ffill").fillna(0.0)


# ──────────────────────────────────────────────────────────────────────────────
# Features pour OOD / HMM
# ──────────────────────────────────────────────────────────────────────────────
HMM_FEATURES = ["rv_24", "rv_72", "mom_logret_72", "funding_rate", "oi_acceleration_z"]
OOD_FEATURES = ["rv_24", "rv_72", "rv_ratio_24_72", "atr_pct_14",
                 "mom_logret_72", "funding_rate", "oi_acceleration_z"]


def _extract(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    available = [c for c in cols if c in df.columns]
    arr = df[available].values.astype(float)
    arr = np.where(np.isnan(arr), np.nanmean(arr, axis=0), arr)
    return arr


# ──────────────────────────────────────────────────────────────────────────────
# Test runner — bar-by-bar pour une année de test
# ──────────────────────────────────────────────────────────────────────────────

def _run_year(
    df_test:    pd.DataFrame,
    composite:  CompositeRegime,
    ood:        OODDetector,
    alpha_reg:  AlphaRegistry,
    suppressor: MetaSuppressor,
    pvar:       PortfolioVaR,
    corr_eng:   CorrelationEngine,
    sizer:      DynamicSizer,
    kill_sw:    KillSwitch,
    slippage:   SlippageModel,
    drift_det:  DriftDetector,
    year:       int,
    sym:        str,
) -> pd.DataFrame:
    """
    Itère barre par barre (1h) sur l'année de test.
    Retourne un DataFrame avec une ligne par barre + toutes les métriques.
    """
    rows = []

    # Init vol FSM per-year
    vol_fsm = VolatilityFSM()
    bar_idx = 0

    for _, row in df_test.iterrows():
        bar = row.to_dict()
        X   = _extract(pd.DataFrame([row]), OOD_FEATURES)

        # L1 — Regime
        regime   = composite.classify(bar)
        vol_st   = vol_fsm.update(bar)
        liq_sc   = composite.liq.score(bar)
        liq_reg  = composite.liq.regime(bar)
        mults    = composite.sizing_multipliers(regime)

        # L2 — Alphas
        signals  = alpha_reg.run_all(row, context={}, regime=regime.value)
        blended  = alpha_reg.blend(signals, regime=regime.value)

        # L3 — Meta-suppression
        supp     = suppressor.evaluate(row, X[0], regime=regime.value, side=blended.side or "long")

        # L6 — Risk (simulated PnL depuis ret_480m = 8h return)
        ret_8h   = float(bar.get("ret_480m", bar.get("ret_240m", 0.0)))
        pvar.update(ret_8h)
        var_rep  = pvar.report() if pvar.n_obs >= 30 else pvar.empty_report()
        ks_dec   = kill_sw.update(bar_idx, ret_8h * 0.01, var_rep)  # trade size = 1%
        sz_res   = sizer.compute_size(
            base_size     = 100.0,
            vol_24h       = float(bar.get("rv_24", 0.02)),
            regime_mult   = mults.get(blended.side or "long", 0.5),
            liquidity_mult= composite.liq.size_multiplier(bar),
        )

        # L5 — Execution
        slip_est = slippage.predict_from_bar(bar, quantity_frac=0.001)

        # L7 — Drift (on échantillonne 1/24)
        drift_score = 0.0
        if bar_idx % 24 == 0 and drift_det._fitted:
            rep = drift_det.score(pd.DataFrame([bar]))
            drift_score = rep.top_drifters[0][1] if rep.top_drifters else 0.0

        rows.append({
            "datetime":        row["datetime"],
            "year":            int(row["year"]),
            "month":           int(row["month"]),
            "close":           float(bar.get("close", 0)),
            # L1
            "regime":          regime.value,
            "vol_state":       vol_st.value,
            "liq_score":       round(liq_sc, 4),
            "liq_regime":      liq_reg,
            # L2
            "n_signals":       len(signals),
            "blend_side":      blended.side,
            "blend_conviction":blended.conviction,
            "alpha_activated": 1 if len(signals) > 0 else 0,
            # L3
            "supp_level":      supp.level,
            "supp_score":      supp.score,
            "supp_allow":      int(supp.allow),
            "supp_size_mult":  supp.size_multiplier,
            # L6
            "var_95":          round(var_rep.var_95, 6),
            "cvar_95":         round(var_rep.cvar_95, 6),
            "kill_active":     int(not ks_dec.allow_trading),
            "final_size":      round(sz_res.final_size, 4),
            "vol_mult":        round(sz_res.vol_multiplier, 4),
            # L5
            "slippage_bps":    round(slip_est.bps, 2),
            # L7
            "drift_score":     drift_score,
            # market
            "ret_8h":          float(ret_8h),
        })
        bar_idx += 1

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────────────────────
# Rapport mensuel / annuel
# ──────────────────────────────────────────────────────────────────────────────

REGIME_EMOJIS = {
    "EXPANSION":    "🟢",
    "COMPRESSION":  "🟡",
    "DELEVERAGING": "🔴",
    "PANIC":        "⛔",
    "SQUEEZE":      "⚡",
    "RECOVERY":     "🔵",
    "UNKNOWN":      "⬜",
}

def _print_monthly_report(results_df: pd.DataFrame, sym: str, year: int) -> None:
    months = sorted(results_df["month"].unique())
    month_names = ["","Jan","Fév","Mar","Avr","Mai","Jun",
                   "Jul","Aoû","Sep","Oct","Nov","Déc"]

    print()
    print(f"  ┌{'─'*100}┐")
    print(f"  │  {sym} {year} — Rapport mensuel par couche{'':55}│")
    print(f"  ├{'─'*100}┤")
    print(f"  │ {'Mois':<5}│{'Régime dominant':<15}│{'Liq%':<8}│"
          f"{'α sig/h':<9}│{'Supp%':<7}│{'KS%':<6}│"
          f"{'VaR95%':<8}│{'Slippage':<9}│{'Drift':<7}│{'BTC ret':<8}│")
    print(f"  ├{'─'*100}┤")

    year_rows = results_df[results_df["year"] == year]
    cumret = 0.0

    for m in months:
        mdf = year_rows[year_rows["month"] == m]
        if mdf.empty:
            continue

        # Régime dominant
        dom_regime = mdf["regime"].value_counts().idxmax()
        regime_pct = mdf["regime"].value_counts(normalize=True)[dom_regime] * 100
        emoji      = REGIME_EMOJIS.get(dom_regime, "?")

        # Liquidité stressée
        liq_stress_pct = (mdf["liq_regime"].isin(["STRESSED", "ILLIQUID"])).mean() * 100

        # Alphas
        alpha_rate = mdf["n_signals"].mean()

        # Suppression (BLOCKED + REDUCED)
        supp_pct   = (mdf["supp_level"].isin(["BLOCKED", "REDUCED"])).mean() * 100

        # Kill switch
        ks_pct     = mdf["kill_active"].mean() * 100

        # VaR
        var_95     = mdf["var_95"].mean() * 100

        # Slippage
        slip       = mdf["slippage_bps"].mean()

        # Drift
        drift      = mdf.loc[mdf["drift_score"] > 0, "drift_score"].mean() if (mdf["drift_score"] > 0).any() else 0.0

        # Retour mensuel (cumulatif des ret_8h approximatifs)
        month_ret = mdf["ret_8h"].sum() * 100
        cumret   += month_ret

        print(
            f"  │ {month_names[m]:<5}│"
            f" {emoji}{dom_regime:<12}({regime_pct:.0f}%) │"
            f" {liq_stress_pct:5.1f}%  │"
            f" {alpha_rate:7.2f}  │"
            f" {supp_pct:5.1f}%  │"
            f" {ks_pct:4.1f}%  │"
            f" {var_95:6.3f}%  │"
            f" {slip:7.1f}bp  │"
            f" {drift:5.3f}  │"
            f" {month_ret:+7.1f}%  │"
        )

    print(f"  ├{'─'*100}┤")

    # Récap annuel
    dom_regime_yr = year_rows["regime"].value_counts(normalize=True).head(3)
    regime_summary = " | ".join(
        f"{REGIME_EMOJIS.get(r,'?')}{r}({v*100:.0f}%)"
        for r, v in dom_regime_yr.items()
    )
    print(
        f"  │ {'ANN.':<5}│ {regime_summary:<30}{'':5}"
        f"│ {year_rows['liq_score'].gt(0.35).mean()*100:5.1f}%  │"
        f" {year_rows['n_signals'].mean():7.2f}  │"
        f" {(year_rows['supp_level']=='BLOCKED').mean()*100:5.1f}%  │"
        f" {year_rows['kill_active'].mean()*100:4.1f}%  │"
        f" {year_rows['var_95'].mean()*100:6.3f}%  │"
        f" {year_rows['slippage_bps'].mean():7.1f}bp  │"
        f" {'—':5}  │"
        f" {year_rows['ret_8h'].sum()*100:+7.1f}%  │"
    )
    print(f"  └{'─'*100}┘")


def _print_year_summary(all_results: dict[int, pd.DataFrame], sym: str) -> None:
    print()
    print("=" * 110)
    print(f"  RAPPORT GLOBAL {sym} — 6 LAYERS INFRASTRUCTURE")
    print("=" * 110)
    print(f"  {'Année':<7}│{'Régime dominant':<22}│{'Supp%':<8}│{'KS actif%':<11}│"
          f"{'VaR95 moy':<11}│{'Slip moy':<10}│{'α activ%':<10}│{'BTC ret':<10}│")
    print(f"  {'─'*7}┼{'─'*22}┼{'─'*8}┼{'─'*11}┼{'─'*11}┼{'─'*10}┼{'─'*10}┼{'─'*10}┤")

    for yr, df in sorted(all_results.items()):
        if df.empty:
            continue
        dom = df["regime"].value_counts(normalize=True).index[0]
        dom_pct = df["regime"].value_counts(normalize=True).iloc[0] * 100
        supp_pct = df["supp_level"].isin(["BLOCKED", "REDUCED"]).mean() * 100
        ks_pct   = df["kill_active"].mean() * 100
        var_pct  = df["var_95"].mean() * 100
        slip     = df["slippage_bps"].mean()
        alpha_pct= df["alpha_activated"].mean() * 100
        ret      = df["ret_8h"].sum() * 100
        emoji    = REGIME_EMOJIS.get(dom, "?")
        print(
            f"  {yr:<7}│ {emoji}{dom:<19}({dom_pct:.0f}%) │"
            f" {supp_pct:5.1f}%  │"
            f" {ks_pct:8.1f}%  │"
            f" {var_pct:8.3f}%  │"
            f" {slip:7.1f}bp  │"
            f" {alpha_pct:7.1f}%  │"
            f" {ret:+8.1f}%  │"
        )
    print("=" * 110)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="2022,2023,2024,2025",
                        help="Années de test (comma-séparées)")
    parser.add_argument("--sym", default="BTCUSDT", help="Symbole")
    args = parser.parse_args()

    TEST_YEARS  = [int(y) for y in args.years.split(",")]
    SYM         = args.sym
    ALL_YEARS   = sorted(set([2019, 2020, 2021] + TEST_YEARS))
    TRAIN_BASE  = [2019, 2020]

    tracker = ExperimentTracker()
    run_id  = tracker.start_run(
        "infrastructure_layers_test",
        params={"sym": SYM, "years": TEST_YEARS},
    )

    print(f"\n{'='*60}")
    print(f"  INFRASTRUCTURE TEST — {SYM}")
    print(f"  Années de test : {TEST_YEARS}")
    print(f"{'='*60}\n")

    # ── Chargement de toutes les données ────────────────────────────────────
    print("  [LOAD] Chargement des données...")
    dfs: dict[int, pd.DataFrame] = {}
    for yr in ALL_YEARS:
        df = _load_year(SYM, yr)
        if df is not None:
            df = _add_features(df)
            dfs[yr] = df
            n = len(df)
            print(f"   ✓ {yr}: {n:,} barres 1h")
        else:
            print(f"   ✗ {yr}: données manquantes")

    if not dfs:
        print("Aucune donnée disponible.")
        return

    # ── Fit global sur 2019-2020 (HMM, OOD, Drift) ─────────────────────────
    print("\n  [FIT] Entraînement initial (2019-2020)...")
    df_base = pd.concat([dfs[y] for y in TRAIN_BASE if y in dfs], ignore_index=True)
    X_base  = _extract(df_base, OOD_FEATURES)

    hmm_engine  = GaussianHMMEngine(n_states=3, n_iter=50)
    hmm_engine.fit(df_base)
    print(f"   ✓ HMM entraîné sur {len(df_base):,} barres")

    vol_fsm    = VolatilityFSM()
    liq_eng    = LiquidityStressEngine()
    composite  = CompositeRegime(hmm_engine, vol_fsm, liq_eng)
    composite.fit(df_base)

    ood = OODDetector(threshold_pct=95.0)
    ood.fit(X_base)
    print(f"   ✓ OOD calibré | threshold={ood.threshold:.3f}")

    drift_det = DriftDetector(n_bins=8)
    drift_det.fit(df_base, feature_cols=OOD_FEATURES)
    print(f"   ✓ DriftDetector calibré sur {len(df_base):,} barres")

    # ── Init layers réutilisés ───────────────────────────────────────────────
    alpha_reg  = build_default_registry()
    suppressor = MetaSuppressor(ood_detector=ood)
    pvar       = PortfolioVaR(window=250)
    corr_eng   = CorrelationEngine(window=60)
    sizer      = DynamicSizer(target_annual_vol=0.15)
    kill_sw    = KillSwitch()
    slippage   = SlippageModel()
    router     = SmartRouter()

    # ── Test année par année ─────────────────────────────────────────────────
    all_results: dict[int, pd.DataFrame] = {}

    for test_year in TEST_YEARS:
        if test_year not in dfs:
            print(f"\n  [SKIP] {test_year}: données manquantes")
            continue

        # Re-fit HMM sur expanding window
        train_years = [y for y in range(2019, test_year) if y in dfs]
        if len(train_years) > 1:
            df_train = pd.concat([dfs[y] for y in train_years], ignore_index=True)
            hmm_engine = GaussianHMMEngine(n_states=3, n_iter=50)
            hmm_engine.fit(df_train)
            X_train = _extract(df_train, OOD_FEATURES)
            ood.fit(X_train)
            drift_det.fit(df_train, feature_cols=OOD_FEATURES)
            composite  = CompositeRegime(hmm_engine, VolatilityFSM(), LiquidityStressEngine())
            print(f"\n  [TRAIN {test_year}] HMM re-fit sur {train_years} ({len(df_train):,} barres)")
        else:
            print(f"\n  [TEST {test_year}] HMM pré-entraîné utilisé")

        df_test = dfs[test_year]

        # Re-init vol des alphas (historique d'entraînement)
        df_last_train = dfs[train_years[-1]] if train_years else df_base
        for alpha in alpha_reg._alphas:
            if hasattr(alpha, "_boll_history") and "boll_width_20" in df_last_train.columns:
                alpha._boll_history = list(df_last_train["boll_width_20"].dropna().values[-300:])

        # Reset kill switch par année
        kill_sw.reset()

        print(f"  [RUN  {test_year}] {len(df_test):,} barres...")
        results = _run_year(
            df_test     = df_test,
            composite   = composite,
            ood         = ood,
            alpha_reg   = alpha_reg,
            suppressor  = suppressor,
            pvar        = pvar,
            corr_eng    = corr_eng,
            sizer       = sizer,
            kill_sw     = kill_sw,
            slippage    = slippage,
            drift_det   = drift_det,
            year        = test_year,
            sym         = SYM,
        )
        all_results[test_year] = results

        # Log métriques dans le tracker
        tracker.log_metrics(run_id, {
            f"{test_year}_supp_reduced_blocked_pct": results["supp_level"].isin(["BLOCKED", "REDUCED"]).mean(),
            f"{test_year}_ks_active_pct":    results["kill_active"].mean(),
            f"{test_year}_mean_var95":       results["var_95"].mean(),
            f"{test_year}_alpha_activation": results["alpha_activated"].mean(),
        })

        _print_monthly_report(results, SYM, test_year)

    # ── Rapport global ───────────────────────────────────────────────────────
    _print_year_summary(all_results, SYM)

    # ── Analyse par régime ───────────────────────────────────────────────────
    all_df = pd.concat(list(all_results.values()), ignore_index=True) if all_results else pd.DataFrame()
    if not all_df.empty:
        print("\n  ANALYSE PAR RÉGIME (toutes années)")
        print(f"  {'Régime':<15}│{'% temps':<9}│{'Supp%':<8}│{'KS%':<7}│{'VaR95':<9}│{'Slip':<8}│")
        print(f"  {'─'*15}┼{'─'*9}┼{'─'*8}┼{'─'*7}┼{'─'*9}┼{'─'*8}┤")
        for reg in [r.value for r in RegimeState]:
            rdf = all_df[all_df["regime"] == reg]
            if rdf.empty:
                continue
            pct   = len(rdf) / len(all_df) * 100
            supp  = rdf["supp_level"].isin(["BLOCKED", "REDUCED"]).mean() * 100
            ks    = rdf["kill_active"].mean() * 100
            var95 = rdf["var_95"].mean() * 100
            slip  = rdf["slippage_bps"].mean()
            emoji = REGIME_EMOJIS.get(reg, "?")
            print(
                f"  {emoji}{reg:<14}│ {pct:6.1f}%  │"
                f" {supp:5.1f}%  │"
                f" {ks:4.1f}%  │"
                f" {var95:6.3f}%  │"
                f" {slip:5.1f}bp  │"
            )
        print()

    tracker.end_run(run_id, "completed")
    print(f"  [L7] Run tracké: {run_id}")


if __name__ == "__main__":
    main()
