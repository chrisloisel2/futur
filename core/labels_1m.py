"""
core/labels_1m.py — LABELS NATIFS 1 MINUTE
===========================================

Horizon principal : 60 minutes (comparaison directe avec le pipeline 1h).
Horizons additionnels : 15m, 30m (ablation et multi-target).

Convention
----------
Pour la barre 1m à t :
    future_ret_{H}m = log(close[t+H] / close[t])
    H ∈ {15, 30, 60}

Anti-leakage strict
-------------------
    - Tous les seuils sont calibrés UNIQUEMENT sur train_mask.
    - Les colonnes future_* ne sont JAMAIS exposées comme features.
    - Les filtres MAE utilisent les données 1m futures (dans l'horizon H) :
        ce sont des données futures → ne jamais les inclure dans les features.

Labels produits par build_labels_1m
------------------------------------
    future_ret_15m, future_ret_30m, future_ret_60m  (rendements bruts)

    y_long_15m,  y_short_15m,  y_tradeable_15m
    y_long_30m,  y_short_30m,  y_tradeable_30m
    y_long_60m,  y_short_60m,  y_tradeable_60m

    Valeurs : 1 = signal net positif, 0 = négatif, -1 = gray zone (exclu)

Gray zones (exclu = -1)
-----------------------
    1. Mouvement insuffisant (zone ambiguë autour du seuil)
    2. Bruit micro : barre 1m de départ trop volatile (rv_5m trop élevé)
    3. Spread implicite : atr trop faible = coût relatif trop élevé
    4. MAE excessive : excursion adverse > facteur * seuil dans les 1ères minutes
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Paramètres par défaut
# ─────────────────────────────────────────────────────────────────────────────

COST_RT: float = 0.0010        # 10 bps round-trip (frais + slippage conservateur)
COST_RT_SHORT: float = 0.0015  # 15 bps pour le short (funding + spread)

HORIZONS: Tuple[int, ...] = (15, 30, 60)   # minutes

TRADEABLE_QUANTILE: float  = 0.80   # top 20% → ~20% de labels positifs
GRAY_ZONE_FACTOR: float    = 0.15   # zone grise = [thr, thr * (1 + gray)]
GRAY_ZONE_FACTOR_SHORT: float = 0.20

MAE_FACTOR: float          = 0.60   # MAE max = thr * MAE_FACTOR → gris si dépassé
MAE_WINDOW: int            = 10     # barres 1m pour calculer la MAE initiale

# Filtre bruit : gray si rv_5m > ce quantile (barres trop chaotiques)
NOISE_FILTER_QUANTILE: float = 0.97


# ─────────────────────────────────────────────────────────────────────────────
# Calcul des rendements forward (à appeler avant le split)
# ─────────────────────────────────────────────────────────────────────────────

def compute_forward_returns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule les rendements forward à 15m, 30m et 60m.
    Doit être appelé sur le DataFrame ENTIER avant tout split.

    Colonnes ajoutées : future_ret_15m, future_ret_30m, future_ret_60m
    """
    df = df.copy()
    log_close = np.log(df["close"].astype(np.float64).clip(lower=1e-9))

    for h in HORIZONS:
        df[f"future_ret_{h}m"] = log_close.shift(-h) - log_close

    return df


def compute_mae_forward(df: pd.DataFrame, window: int = MAE_WINDOW) -> pd.DataFrame:
    """
    Calcule la Max Adverse Excursion sur les `window` premières barres 1m
    suivant chaque barre.

    Pour un signal LONG à t : MAE_long = max(0, open[t] - min(low[t+1..t+w]))
    Pour un signal SHORT à t : MAE_short = max(0, max(high[t+1..t+w]) - open[t])

    Ces colonnes servent UNIQUEMENT au filtre de gray zone, jamais comme features.
    Colonnes ajoutées : mae_long_{window}m, mae_short_{window}m
    """
    df = df.copy()
    close_ = df["close"].astype(np.float64).values
    low_   = df["low"].astype(np.float64).values
    high_  = df["high"].astype(np.float64).values
    n      = len(df)

    mae_long  = np.full(n, np.nan)
    mae_short = np.full(n, np.nan)

    # Vectorisation partielle : rolling min/max forward
    for i in range(n - window):
        entry  = close_[i]
        min_lo = low_[i + 1 : i + 1 + window].min()
        max_hi = high_[i + 1 : i + 1 + window].max()
        mae_long[i]  = max(0.0, entry - min_lo) / (entry + 1e-9)
        mae_short[i] = max(0.0, max_hi - entry) / (entry + 1e-9)

    df[f"mae_long_{window}m"]  = mae_long
    df[f"mae_short_{window}m"] = mae_short
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Factory de labels
# ─────────────────────────────────────────────────────────────────────────────

def build_labels_1m(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    cost_rt: float = COST_RT,
    cost_rt_short: float = COST_RT_SHORT,
    tradeable_quantile: float = TRADEABLE_QUANTILE,
    gray_zone_factor: float = GRAY_ZONE_FACTOR,
    gray_zone_factor_short: float = GRAY_ZONE_FACTOR_SHORT,
    mae_factor: float = MAE_FACTOR,
    mae_window: int = MAE_WINDOW,
    noise_filter_q: float = NOISE_FILTER_QUANTILE,
    primary_horizon: int = 60,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Construit tous les labels 1m-natifs avec asymétrie long/short.

    Paramètres
    ----------
    df              : DataFrame 1m avec future_ret_{H}m calculés (via compute_forward_returns)
    train_mask      : masque booléen pour la calibration des seuils
    primary_horizon : horizon principal pour les statistiques (défaut 60m)

    Retourne
    --------
    df_labeled  : DataFrame avec colonnes y_long_*, y_short_*, y_tradeable_*
    stats       : dict de diagnostics
    """
    df = df.copy()

    # ── Vérifications préalables ──────────────────────────────────────────────
    for h in HORIZONS:
        col = f"future_ret_{h}m"
        if col not in df.columns:
            raise RuntimeError(
                f"Colonne '{col}' manquante. Appeler compute_forward_returns() d'abord."
            )

    # ── Calcul MAE si pas déjà fait ───────────────────────────────────────────
    mae_long_col  = f"mae_long_{mae_window}m"
    mae_short_col = f"mae_short_{mae_window}m"
    if mae_long_col not in df.columns:
        print(f"   Calcul MAE (fenêtre {mae_window}m) …")
        df = compute_mae_forward(df, window=mae_window)

    # ── Filtre bruit : rv_5m trop élevé → gray systématique ──────────────────
    if "rv_5m" in df.columns:
        rv5_train = df.loc[train_mask, "rv_5m"].dropna()
        noise_thr = float(rv5_train.quantile(noise_filter_q))
        noise_mask = (df["rv_5m"] > noise_thr)
    else:
        noise_mask = pd.Series(False, index=df.index)

    stats: Dict = {"horizons": {}}

    for h in HORIZONS:
        ret_col = f"future_ret_{h}m"
        ret     = df[ret_col].values.astype(np.float64)
        ret_abs = np.abs(ret)

        # ── Seuils calibrés sur train uniquement ─────────────────────────────
        ret_train = ret_abs[train_mask]
        ret_train = ret_train[~np.isnan(ret_train)]
        thr_raw   = float(np.quantile(ret_train, tradeable_quantile))
        thr_long  = thr_raw + cost_rt
        thr_short = thr_raw + cost_rt_short

        # ── Seuils gray zone ──────────────────────────────────────────────────
        thr_long_hi  = thr_long  * (1.0 + gray_zone_factor)
        thr_short_hi = thr_short * (1.0 + gray_zone_factor_short)

        # ── MAE filter ────────────────────────────────────────────────────────
        max_mae_long  = thr_long  * mae_factor
        max_mae_short = thr_short * mae_factor

        mae_long_ok  = (df[mae_long_col].values  <= max_mae_long)  if mae_long_col  in df.columns else np.ones(len(df), bool)
        mae_short_ok = (df[mae_short_col].values <= max_mae_short) if mae_short_col in df.columns else np.ones(len(df), bool)

        noise = noise_mask.values

        # ── Labels LONG ───────────────────────────────────────────────────────
        y_long = np.full(len(df), 0, dtype=np.int8)
        long_signal  = ret > thr_long
        long_gray_hi = (ret > thr_long) & (ret < thr_long_hi)
        y_long[long_signal]  = 1
        y_long[long_gray_hi] = -1    # zone ambiguë
        y_long[~mae_long_ok & long_signal] = -1   # MAE trop élevée
        y_long[noise & (y_long == 1)] = -1         # bruit micro

        # ── Labels SHORT ──────────────────────────────────────────────────────
        y_short = np.full(len(df), 0, dtype=np.int8)
        short_signal  = ret < -thr_short
        short_gray_hi = (ret < -thr_short) & (ret > -thr_short_hi)
        y_short[short_signal]  = 1
        y_short[short_gray_hi] = -1
        y_short[~mae_short_ok & short_signal] = -1
        y_short[noise & (y_short == 1)] = -1

        # ── Label tradeable (symétrique) ──────────────────────────────────────
        y_tr = ((ret > thr_long) | (ret < -thr_short)).astype(np.int8)
        y_tr[noise] = 0  # pas tradeable si bruit micro

        df[f"y_long_{h}m"]      = y_long
        df[f"y_short_{h}m"]     = y_short
        df[f"y_tradeable_{h}m"] = y_tr

        # ── Statistiques ─────────────────────────────────────────────────────
        n       = len(df)
        n_long  = int((y_long  == 1).sum())
        n_short = int((y_short == 1).sum())
        n_tr    = int((y_tr    == 1).sum())
        n_lg    = int((y_long  == -1).sum())
        n_sg    = int((y_short == -1).sum())

        stats["horizons"][h] = {
            "thr_raw":       round(thr_raw,   6),
            "thr_long":      round(thr_long,  6),
            "thr_short":     round(thr_short, 6),
            "n_long":        n_long,
            "n_short":       n_short,
            "n_tradeable":   n_tr,
            "n_long_gray":   n_lg,
            "n_short_gray":  n_sg,
            "frac_long":     round(n_long  / n, 4),
            "frac_short":    round(n_short / n, 4),
            "warn_low_long":  n_long  < 2000,
            "warn_low_short": n_short < 1000,
        }

        flag = "⚠ " if stats["horizons"][h]["warn_low_long"] else "  "
        print(f"   {flag}H={h:2d}m  LONG={n_long:,} ({n_long/n:.1%})  "
              f"SHORT={n_short:,} ({n_short/n:.1%})  "
              f"thr_long={thr_long:.5f}  thr_short={thr_short:.5f}")

    # Statistiques globales bruit
    stats["noise_bars_pct"] = round(float(noise_mask.mean()), 4)
    if stats["noise_bars_pct"] > 0.10:
        print(f"   ⚠  {stats['noise_bars_pct']:.1%} de barres filtrées comme bruit micro")

    return df, stats


# ─────────────────────────────────────────────────────────────────────────────
# Split chronologique avec purge
# ─────────────────────────────────────────────────────────────────────────────

def chronological_split_1m(
    df: pd.DataFrame,
    train_end_year: int = 2022,
    val_year: int = 2023,
    test_from_year: int = 2024,
    purge_bars: int = 240,    # 4h de purge entre les splits (anti-leakage autocorrélation)
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Split strict chronologique avec gap de purge.

    purge_bars : nombre de barres 1m supprimées aux frontières.
    Avec purge_bars=240 (4h), on évite le leakage dû à l'autocorrélation
    des features rolling longues (ex: eff_ratio_30m = 30 barres de look-back).
    """
    idx   = pd.to_datetime(df.index)
    years = idx.year.values
    n     = len(df)

    train_raw = years <= train_end_year
    val_raw   = years == val_year
    test_raw  = years >= test_from_year

    # Purge : supprimer les `purge_bars` barres de CHAQUE côté des frontières
    train_end_idx = int(np.where(train_raw)[0].max())
    val_end_idx   = int(np.where(val_raw)[0].max())

    # Purge autour de la frontière train/val
    purge_start_val = train_end_idx + 1
    purge_end_val   = min(purge_start_val + purge_bars, n)
    # Purge autour de la frontière val/test
    purge_start_test = val_end_idx + 1
    purge_end_test   = min(purge_start_test + purge_bars, n)

    train_mask = train_raw.copy()
    val_mask   = val_raw.copy()
    test_mask  = test_raw.copy()

    # Neutraliser les zones purgées
    train_mask[max(0, train_end_idx - purge_bars + 1) : purge_end_val] = False
    val_mask[purge_start_val : purge_end_val] = False
    val_mask[max(0, val_end_idx - purge_bars + 1) : purge_end_test] = False
    test_mask[purge_start_test : purge_end_test] = False

    n_tr = int(train_mask.sum())
    n_v  = int(val_mask.sum())
    n_te = int(test_mask.sum())

    print(
        f"   Split 1m  train≤{train_end_year}: {n_tr:,}  "
        f"val={val_year}: {n_v:,}  "
        f"test≥{test_from_year}: {n_te:,}  "
        f"(purge={purge_bars} barres aux frontières)"
    )

    assert not (train_mask & val_mask).any(),  "Chevauchement train/val"
    assert not (val_mask   & test_mask).any(), "Chevauchement val/test"
    assert not (train_mask & test_mask).any(), "Chevauchement train/test"

    return train_mask, val_mask, test_mask


# ─────────────────────────────────────────────────────────────────────────────
# Régimes 1m — filtre contextuel local
# ─────────────────────────────────────────────────────────────────────────────

REGIME_LABELS = ("TREND_UP", "TREND_DOWN", "RANGE", "BREAKOUT", "HIGH_VOL", "LOW_VOL")


def compute_local_regime_1m(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule un régime local à partir des features 1m.

    Régimes :
        TREND_UP   : momentum haussier + faible reversal + eff_ratio élevé
        TREND_DOWN : idem baissier
        RANGE      : faible momentum + forte reversal density
        BREAKOUT   : barre breakout_up ou breakout_dn (50m) + vol expansion
        HIGH_VOL   : rv_60m > Q75 (train)
        LOW_VOL    : rv_60m < Q25 (train) → potential coil
        NEUTRAL    : défaut

    Retourne df avec colonne 'regime_1m' (str).
    Non utilisé comme feature du modèle — sert de gate dans le backtest.
    """
    df = df.copy()

    required = ["mom_15m", "reversal_density_10m", "eff_ratio_10m",
                "rv_60m", "breakout_up_30m", "breakout_dn_30m"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"   ⚠  compute_local_regime_1m : colonnes manquantes {missing} → NEUTRAL")
        df["regime_1m"] = "NEUTRAL"
        return df

    mom      = df["mom_15m"]
    rev_den  = df["reversal_density_10m"]
    eff      = df["eff_ratio_10m"]
    rv60     = df["rv_60m"]
    brkup    = df["breakout_up_30m"].astype(bool)
    brkdn    = df["breakout_dn_30m"].astype(bool)
    vol_exp  = df["rv_ratio_5_30m"] if "rv_ratio_5_30m" in df.columns else pd.Series(1.0, index=df.index)

    rv60_q75 = float(rv60.quantile(0.75))
    rv60_q25 = float(rv60.quantile(0.25))

    n   = len(df)
    reg = np.full(n, "NEUTRAL", dtype=object)

    reg[brkup & (vol_exp > 1.2)]  = "BREAKOUT"
    reg[brkdn & (vol_exp > 1.2)]  = "BREAKOUT"
    reg[(mom > 0.002) & (eff > 0.5) & (rev_den < 0.4)]  = "TREND_UP"
    reg[(mom < -0.002) & (eff > 0.5) & (rev_den < 0.4)] = "TREND_DOWN"
    reg[(mom.abs() < 0.001) & (rev_den > 0.55)]          = "RANGE"
    reg[rv60 > rv60_q75] = "HIGH_VOL"
    reg[rv60 < rv60_q25] = "LOW_VOL"

    df["regime_1m"] = reg

    dist = pd.Series(reg).value_counts(normalize=True).round(3).to_dict()
    print(f"   Régimes 1m : {dist}")
    return df
