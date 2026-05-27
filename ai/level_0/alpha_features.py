"""
ai/level_0/alpha_features.py — FEATURES ALPHA HAUTE VALEUR (v5)
================================================================

50+ features complémentaires à feature_engineering.py, ciblant des
sources d'alpha orthogonales : régime de volatilité, autocorrélations,
microstructure, bear-bounce, illiquidité Amihud, entropie, KAMA.

Règles anti-leakage :
  - Toutes les fenêtres sont backward-looking uniquement [0..t].
  - Aucune valeur future (t+1, t+2, ...) n'est utilisée.
  - Les labels (future_ret_*, y_long) ne sont jamais touchés ici.

Usage :
    from ai.level_0.alpha_features import compute_alpha_features, FEATURES_ALPHA
    df = compute_alpha_features(df)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List


# ─────────────────────────────────────────────────────────────────────────────
# LISTE CANONIQUE DES FEATURES ALPHA v5
# ─────────────────────────────────────────────────────────────────────────────

FEATURES_ALPHA: List[str] = [
    # ── Régime de volatilité ─────────────────────────────────────────────────
    "vol_pctrank_200",        # percentile rank vol réalisée vs 200 barres
    "vol_of_vol_24",          # std des vol 4-bar sur 24 barres → incertitude
    "vol_spike_zscore_100",   # z-score vol actuelle vs 100-bar mean
    "atr_expansion_ratio",    # ATR(8) / ATR(48) → court vs long terme
    "vol_regime_trend_20",    # pente de vol 4-bar sur 20 barres (vol qui monte/baisse)
    # ── Autocorrélations ─────────────────────────────────────────────────────
    "autocorr_lag1_20",       # lag-1 autocorr des log-returns, 20 barres
    "autocorr_lag4_20",       # lag-4 autocorr (cycle 4h)
    "autocorr_lag8_20",       # lag-8 autocorr (cycle = horizon cible)
    "hurst_exp_48",           # exposant de Hurst approché sur 48 barres
    # ── Ratios Sharpe glissants ───────────────────────────────────────────────
    "sharpe_8",               # mean/std retours 8 barres
    "sharpe_20",              # mean/std retours 20 barres
    "sharpe_48",              # mean/std retours 48 barres
    "calmar_proxy_24",        # mean_ret_8 / max_drawdown 24h
    # ── Skewness & queues ────────────────────────────────────────────────────
    "skew_ret_20",            # skewness glissante 20 barres
    "skew_ret_48",            # skewness glissante 48 barres
    "excess_kurt_20",         # kurtosis excédentaire 20 barres
    "tail_ratio_20",          # 95e pctile / 5e pctile des |rets| 20 barres
    # ── Order flow imbalance ─────────────────────────────────────────────────
    "ofi_zscore_24",          # taker-buy-ratio z-scoré 24 barres
    "ofi_momentum_8",         # dérivée première OFI (accélération)
    "cum_delta_pctrank_48",   # pctrank delta cumulé 48 barres
    "taker_buy_accel",        # dérivée seconde taker buy ratio
    # ── Bear bounce ──────────────────────────────────────────────────────────
    "bear_bounce_score",      # composite 0-4 : RSI+dist_ema200+vol_contraction+candle
    "oversold_depth_atr",     # distance sous EMA200, normalisée par ATR
    "rsi_recovery_slope_8",   # pente de récupération RSI sur 8 barres
    "vol_contraction_ratio",  # ATR(8) / ATR(48) quand < 1 → contraction après spike
    "bear_pressure_24",       # fraction de barres baissières sur 24h
    # ── Illiquidité Amihud ───────────────────────────────────────────────────
    "amihud_8",               # |ret_1h| / volume, moyenne 8 barres × 1e6
    "amihud_24",              # idem 24 barres
    "amihud_pctrank_72",      # percentile rank Amihud sur 72 barres
    "amihud_spike",           # amihud_8 / amihud_24 → choc d'illiquidité
    # ── Entropie prix ────────────────────────────────────────────────────────
    "ret_entropy_20",         # entropie approchée séquence des signes 20 barres
    "hl_entropy_16",          # entropie des intervals high-low 16 barres
    # ── KAMA ─────────────────────────────────────────────────────────────────
    "kama_dev_20",            # distance KAMA(20) / prix → éloignement adaptatif
    "kama_dev_10",            # distance KAMA(10) / prix
    "kama_speed_10",          # pente KAMA(10) / ATR → vitesse normalisée
    # ── Microstructure bougies ───────────────────────────────────────────────
    "wick_pressure_8",        # mean(ombre_haute - ombre_basse) / corps 8 barres
    "upper_shadow_pct_8",     # fraction barres avec ombre haute > ombre basse (selling)
    "candle_body_strength_8", # mean(|corps| / range) × direction 8 barres
    "candle_persistence_8",   # fraction barres dans même direction que close
    # ── Momentum cross-horizon ───────────────────────────────────────────────
    "mom_accel_4_8",          # mom_8 - mom_4 → aligne / diverge
    "mom_divergence_4_24",    # mom_4 - mom_24 → pullback detection
    "ema_cross_speed",        # pente du spread EMA8/EMA48 → rupture tendance
    # ── Mean reversion / trend ───────────────────────────────────────────────
    "zscore_from_sma_20",     # z-score prix vs SMA(20) → extension / compression
    "zscore_from_sma_50",     # z-score prix vs SMA(50)
    "mean_rev_speed_20",      # vitesse de rappel vers la moyenne (ratio)
    # ── Composite régime ─────────────────────────────────────────────────────
    "crisis_score",           # vol_spike + sell_pressure + RSI_drop composite
    "trend_quality_20",       # (abs(end-start) / sum(|rets|)) 20 barres — R²-like
    "vol_trend_alignment",    # (mom_8 > 0) × (1 - vol_of_vol_24/vol_spike_zscore_100)
]


_EPS = 1e-12


def _close(df: pd.DataFrame) -> pd.Series | None:
    for col in ("Close", "close"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    return None


def _vol(df: pd.DataFrame) -> pd.Series | None:
    for col in ("Volume", "volume"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").clip(lower=_EPS)
    return None


def _high(df: pd.DataFrame) -> pd.Series | None:
    for col in ("High", "high"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    return None


def _low(df: pd.DataFrame) -> pd.Series | None:
    for col in ("Low", "low"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    return None


def _open(df: pd.DataFrame) -> pd.Series | None:
    for col in ("Open", "open"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers numériques
# ─────────────────────────────────────────────────────────────────────────────

def _rolling_autocorr(arr: np.ndarray, lag: int, window: int) -> np.ndarray:
    """Lag-k autocorrélation glissante sur `window` barres (O(n·w))."""
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        x = arr[i - window + 1: i + 1]
        x1 = x[:-lag]
        x2 = x[lag:]
        if len(x1) < 4:
            continue
        m1, m2 = x1.mean(), x2.mean()
        num = ((x1 - m1) * (x2 - m2)).sum()
        denom = (np.sqrt(((x1 - m1) ** 2).sum()) *
                 np.sqrt(((x2 - m2) ** 2).sum()))
        if denom > _EPS:
            out[i] = num / denom
    return out


def _rolling_pctrank(arr: np.ndarray, window: int) -> np.ndarray:
    """Percentile rank de arr[i] dans arr[i-window+1:i+1]."""
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = arr[i - window + 1: i + 1]
        finite = w[np.isfinite(w)]
        if len(finite) < 2:
            continue
        out[i] = (finite < arr[i]).sum() / len(finite)
    return out


def _rolling_hurst(log_prices: np.ndarray, window: int) -> np.ndarray:
    """Exposant de Hurst approché via R/S sur `window` barres."""
    n = len(log_prices)
    out = np.full(n, np.nan)
    half = window // 2
    for i in range(window - 1, n):
        seg = log_prices[i - window + 1: i + 1]
        ret = np.diff(seg)
        if len(ret) < 8:
            continue
        # R/S sur la moitié et le tout
        def rs(r: np.ndarray) -> float:
            mean_r = r.mean()
            dev = (r - mean_r).cumsum()
            R = dev.max() - dev.min()
            S = r.std(ddof=0)
            return R / (S + _EPS)
        rs_half = rs(ret[:half])
        rs_full = rs(ret)
        if rs_half > _EPS:
            out[i] = np.log(rs_full / rs_half + _EPS) / np.log(2.0)
    return np.clip(out, 0.0, 1.5)


def _rolling_skew(arr: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(arr).rolling(window, min_periods=window // 2)
    return s.skew().to_numpy()


def _rolling_kurt(arr: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(arr).rolling(window, min_periods=window // 2)
    return s.kurt().to_numpy()


def _rolling_sharpe(arr: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(arr)
    mean = s.rolling(window, min_periods=window // 2).mean()
    std  = s.rolling(window, min_periods=window // 2).std(ddof=0).clip(lower=_EPS)
    return (mean / std).to_numpy()


def _ret_entropy(signs: np.ndarray, window: int) -> np.ndarray:
    """Entropie de Shannon des signes sur une fenêtre glissante."""
    n = len(signs)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = signs[i - window + 1: i + 1]
        p1 = (w > 0).mean()
        p0 = 1.0 - p1
        if 0 < p1 < 1:
            out[i] = -(p1 * np.log2(p1 + _EPS) + p0 * np.log2(p0 + _EPS))
        else:
            out[i] = 0.0
    return out


def _kama(prices: np.ndarray, n: int = 10, f: int = 2, s: int = 30) -> np.ndarray:
    """Kaufman Adaptive Moving Average."""
    fast = 2.0 / (f + 1)
    slow = 2.0 / (s + 1)
    kama_arr = prices.copy().astype(float)
    for i in range(n, len(prices)):
        change = abs(prices[i] - prices[i - n])
        volatility = np.sum(np.abs(np.diff(prices[i - n: i + 1]))) + _EPS
        er = change / volatility
        sc = (er * (fast - slow) + slow) ** 2
        kama_arr[i] = kama_arr[i - 1] + sc * (prices[i] - kama_arr[i - 1])
    return kama_arr


def _rolling_max_drawdown(arr: np.ndarray, window: int) -> np.ndarray:
    """Max drawdown des log-returns glissants."""
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = arr[i - window + 1: i + 1]
        cum = np.cumsum(w)
        peak = np.maximum.accumulate(cum)
        dd = (peak - cum).max()
        out[i] = dd
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Familles de features
# ─────────────────────────────────────────────────────────────────────────────

def _add_vol_regime(df: pd.DataFrame, ret1h: pd.Series) -> pd.DataFrame:
    """Régime de volatilité : percentile rank, vol-of-vol, z-score, expansion."""
    rv4 = ret1h.pow(2).rolling(4, min_periods=2).mean().pipe(np.sqrt)

    df["vol_pctrank_200"] = _rolling_pctrank(rv4.values, 200)

    df["vol_of_vol_24"] = rv4.rolling(24, min_periods=8).std().fillna(0.0)

    rv_mean100 = rv4.rolling(100, min_periods=20).mean().clip(lower=_EPS)
    rv_std100  = rv4.rolling(100, min_periods=20).std().clip(lower=_EPS)
    df["vol_spike_zscore_100"] = ((rv4 - rv_mean100) / rv_std100).fillna(0.0)

    # ATR proxy si non disponible
    atr8 = df.get("atr_8", rv4.rolling(8, min_periods=2).mean() * np.sqrt(8))
    atr48 = df.get("atr_48", rv4.rolling(48, min_periods=8).mean() * np.sqrt(48))
    if isinstance(atr8, pd.Series) and isinstance(atr48, pd.Series):
        df["atr_expansion_ratio"] = (atr8 / atr48.clip(lower=_EPS)).fillna(1.0)
    else:
        df["atr_expansion_ratio"] = 1.0

    # Pente de la vol 4-bar sur 20 barres (linreg slope approché)
    df["vol_regime_trend_20"] = (
        rv4 - rv4.rolling(20, min_periods=8).mean()
    ).fillna(0.0)

    return df


def _add_autocorr(df: pd.DataFrame, ret1h: np.ndarray) -> pd.DataFrame:
    """Autocorrélations lag-1/4/8 et Hurst."""
    df["autocorr_lag1_20"] = _rolling_autocorr(ret1h, lag=1, window=20)
    df["autocorr_lag4_20"] = _rolling_autocorr(ret1h, lag=4, window=20)
    df["autocorr_lag8_20"] = _rolling_autocorr(ret1h, lag=8, window=20)

    close_s = _close(df)
    if close_s is not None:
        log_p = np.log(close_s.values.astype(float) + _EPS)
        df["hurst_exp_48"] = _rolling_hurst(log_p, window=48)
    else:
        df["hurst_exp_48"] = np.nan
    return df


def _add_sharpe(df: pd.DataFrame, ret1h: np.ndarray) -> pd.DataFrame:
    """Ratios Sharpe glissants et Calmar proxy."""
    df["sharpe_8"]  = _rolling_sharpe(ret1h, 8)
    df["sharpe_20"] = _rolling_sharpe(ret1h, 20)
    df["sharpe_48"] = _rolling_sharpe(ret1h, 48)

    mean_ret8 = pd.Series(ret1h).rolling(8, min_periods=4).mean()
    mdd24     = _rolling_max_drawdown(ret1h, 24)
    df["calmar_proxy_24"] = (mean_ret8.values / (np.abs(mdd24) + _EPS)).clip(-10, 10)
    return df


def _add_skew_tail(df: pd.DataFrame, ret1h: np.ndarray) -> pd.DataFrame:
    """Skewness, kurtosis, tail ratio."""
    df["skew_ret_20"]    = _rolling_skew(ret1h, 20)
    df["skew_ret_48"]    = _rolling_skew(ret1h, 48)
    df["excess_kurt_20"] = _rolling_kurt(ret1h, 20)

    ret_s = pd.Series(ret1h)
    p95 = ret_s.abs().rolling(20, min_periods=8).quantile(0.95).clip(lower=_EPS)
    p05 = ret_s.abs().rolling(20, min_periods=8).quantile(0.05).clip(lower=_EPS)
    df["tail_ratio_20"] = (p95 / p05).fillna(1.0).clip(0.1, 50.0)
    return df


def _add_ofi(df: pd.DataFrame) -> pd.DataFrame:
    """Order flow imbalance features."""
    if "taker_buy_ratio_base" in df.columns:
        tbr = pd.to_numeric(df["taker_buy_ratio_base"], errors="coerce").fillna(0.5)
    elif "vol_imbalance" in df.columns:
        tbr = pd.to_numeric(df["vol_imbalance"], errors="coerce").fillna(0.0) * 0.5 + 0.5
    else:
        df["ofi_zscore_24"]      = np.nan
        df["ofi_momentum_8"]     = np.nan
        df["cum_delta_pctrank_48"] = np.nan
        df["taker_buy_accel"]    = np.nan
        return df

    mean24 = tbr.rolling(24, min_periods=8).mean()
    std24  = tbr.rolling(24, min_periods=8).std().clip(lower=_EPS)
    ofi_z  = (tbr - mean24) / std24
    df["ofi_zscore_24"] = ofi_z.fillna(0.0)

    # Dérivée première (momentum OFI)
    df["ofi_momentum_8"] = ofi_z.diff(8).fillna(0.0)

    # Delta cumulé z-scoré + pctrank
    if "delta_taker_pressure" in df.columns:
        cum_d = pd.to_numeric(df["delta_taker_pressure"], errors="coerce").fillna(0.0).cumsum()
    else:
        cum_d = (tbr - 0.5).cumsum()
    df["cum_delta_pctrank_48"] = _rolling_pctrank(cum_d.values, 48)

    # Accélération (dérivée seconde)
    df["taker_buy_accel"] = tbr.diff(1).diff(1).fillna(0.0)
    return df


def _add_bear_bounce(df: pd.DataFrame, ret1h: pd.Series) -> pd.DataFrame:
    """Features de bear-bounce et profondeur oversold."""
    rsi = df.get("rsi_14", pd.Series(50.0, index=df.index))
    if not isinstance(rsi, pd.Series):
        rsi = pd.Series(50.0, index=df.index)
    rsi = pd.to_numeric(rsi, errors="coerce").fillna(50.0)

    dist_ema200 = df.get("dist_ema_200", pd.Series(0.0, index=df.index))
    if not isinstance(dist_ema200, pd.Series):
        dist_ema200 = pd.Series(0.0, index=df.index)
    dist_ema200 = pd.to_numeric(dist_ema200, errors="coerce").fillna(0.0)

    atr14_col = None
    for col in ("atr_14", "atr_pct_20"):
        if col in df.columns:
            atr14_col = pd.to_numeric(df[col], errors="coerce").fillna(0.01)
            break
    if atr14_col is None:
        atr14_col = pd.Series(0.01, index=df.index)

    # Vol contraction : ATR court vs long
    rv4   = ret1h.pow(2).rolling(4, min_periods=2).mean().pipe(np.sqrt)
    atr_s = rv4.rolling(8,  min_periods=2).mean()
    atr_l = rv4.rolling(48, min_periods=8).mean().clip(lower=_EPS)
    vol_ratio = (atr_s / atr_l).fillna(1.0)
    df["vol_contraction_ratio"] = vol_ratio

    # Composite 0–4
    score = (
        (rsi < 35).astype(float)                     # oversold RSI
        + (dist_ema200 < -0.03).astype(float)         # sous EMA200
        + (vol_ratio < 0.85).astype(float)            # vol en contraction
        + (ret1h.rolling(3, min_periods=2).sum() > 0).astype(float)  # récupération récente
    )
    df["bear_bounce_score"] = score

    # Profondeur oversold normalisée par ATR
    df["oversold_depth_atr"] = (
        dist_ema200.clip(upper=0.0).abs() / atr14_col.clip(lower=_EPS)
    ).clip(0.0, 20.0)

    # Pente de récupération RSI sur 8 barres
    df["rsi_recovery_slope_8"] = (rsi - rsi.shift(8)).fillna(0.0) / 8.0

    # Fraction baissière 24h
    df["bear_pressure_24"] = (ret1h < 0).astype(float).rolling(24, min_periods=8).mean()

    return df


def _add_amihud(df: pd.DataFrame, ret1h: pd.Series) -> pd.DataFrame:
    """Ratio d'illiquidité d'Amihud."""
    vol_s = _vol(df)
    abs_ret = ret1h.abs()

    if vol_s is not None:
        amihud = abs_ret / vol_s.clip(lower=_EPS) * 1e6
        df["amihud_8"]  = amihud.rolling(8,  min_periods=2).mean()
        df["amihud_24"] = amihud.rolling(24, min_periods=6).mean()
        amihud_arr = df["amihud_8"].values
        df["amihud_pctrank_72"] = _rolling_pctrank(amihud_arr, 72)
        df["amihud_spike"] = (
            df["amihud_8"] / df["amihud_24"].clip(lower=_EPS)
        ).fillna(1.0).clip(0.1, 20.0)
    else:
        df["amihud_8"]  = np.nan
        df["amihud_24"] = np.nan
        df["amihud_pctrank_72"] = np.nan
        df["amihud_spike"] = np.nan

    return df


def _add_entropy(df: pd.DataFrame, ret1h: np.ndarray) -> pd.DataFrame:
    """Entropie des signes des retours et des high-low."""
    signs = np.sign(ret1h)
    df["ret_entropy_20"] = _ret_entropy(signs, window=20)

    high_s = _high(df)
    low_s  = _low(df)
    close_s = _close(df)
    if high_s is not None and low_s is not None and close_s is not None:
        hl_range = (high_s - low_s) / close_s.clip(lower=_EPS)
        median16 = hl_range.rolling(16, min_periods=8).median().clip(lower=_EPS)
        hl_norm  = hl_range / median16
        signs_hl = np.sign(hl_norm.values - 1.0)
        df["hl_entropy_16"] = _ret_entropy(signs_hl, window=16)
    else:
        df["hl_entropy_16"] = np.nan

    return df


def _add_kama(df: pd.DataFrame) -> pd.DataFrame:
    """Kaufman Adaptive MA : déviation et vitesse."""
    close_s = _close(df)
    if close_s is None:
        df["kama_dev_10"] = np.nan
        df["kama_dev_20"] = np.nan
        df["kama_speed_10"] = np.nan
        return df

    prices = close_s.values.astype(float)
    kama10 = _kama(prices, n=10, f=2, s=30)
    kama20 = _kama(prices, n=20, f=2, s=30)

    df["kama_dev_10"] = ((prices - kama10) / np.abs(prices).clip(_EPS)).clip(-0.2, 0.2)
    df["kama_dev_20"] = ((prices - kama20) / np.abs(prices).clip(_EPS)).clip(-0.2, 0.2)

    # Vitesse KAMA(10) normalisée par volatilité 14-bar
    kama10_slope = np.diff(kama10, prepend=kama10[0])
    atr_col = df.get("atr_14")
    if isinstance(atr_col, pd.Series):
        atr_norm = atr_col.fillna(method="ffill").clip(lower=_EPS).values
    else:
        atr_norm = pd.Series(prices).diff().abs().rolling(14, min_periods=2).mean().clip(lower=_EPS).values
    df["kama_speed_10"] = (kama10_slope / atr_norm).clip(-5, 5)

    return df


def _add_microstructure(df: pd.DataFrame, ret1h: pd.Series) -> pd.DataFrame:
    """Features de microstructure : wick, corps de bougie, persistance."""
    high_s  = _high(df)
    low_s   = _low(df)
    open_s  = _open(df)
    close_s = _close(df)

    if all(x is not None for x in [high_s, low_s, open_s, close_s]):
        body   = (close_s - open_s).abs()
        range_ = (high_s - low_s).clip(lower=_EPS)
        upper_wick = high_s - pd.concat([close_s, open_s], axis=1).max(axis=1)
        lower_wick = pd.concat([close_s, open_s], axis=1).min(axis=1) - low_s

        wick_diff = (upper_wick - lower_wick) / range_
        df["wick_pressure_8"] = wick_diff.rolling(8, min_periods=3).mean().fillna(0.0)

        upper_dom = (upper_wick > lower_wick).astype(float)
        df["upper_shadow_pct_8"] = upper_dom.rolling(8, min_periods=3).mean()

        body_strength = (body / range_) * np.sign(close_s - open_s)
        df["candle_body_strength_8"] = body_strength.rolling(8, min_periods=3).mean()
    else:
        df["wick_pressure_8"]     = np.nan
        df["upper_shadow_pct_8"]  = np.nan
        df["candle_body_strength_8"] = np.nan

    # Persistance directionnelle
    direction = np.sign(ret1h)
    current_dir = direction
    persistence = (direction == current_dir).astype(float)
    df["candle_persistence_8"] = persistence.rolling(8, min_periods=3).mean()

    return df


def _add_cross_horizon_mom(df: pd.DataFrame, ret1h: pd.Series) -> pd.DataFrame:
    """Momentum cross-horizon et alignement EMA."""
    close_s = _close(df)

    if close_s is not None:
        log_c = np.log(close_s.clip(lower=_EPS))
        mom4  = (log_c - log_c.shift(4)).fillna(0.0)
        mom8  = (log_c - log_c.shift(8)).fillna(0.0)
        mom24 = (log_c - log_c.shift(24)).fillna(0.0)
        df["mom_accel_4_8"]     = (mom8 - mom4).fillna(0.0)
        df["mom_divergence_4_24"] = (mom4 - mom24).fillna(0.0)

        ema8  = close_s.ewm(span=8, adjust=False).mean()
        ema48 = close_s.ewm(span=48, adjust=False).mean()
        spread = (ema8 - ema48) / close_s.clip(lower=_EPS)
        df["ema_cross_speed"] = (spread - spread.shift(4)).fillna(0.0)
    else:
        df["mom_accel_4_8"]      = np.nan
        df["mom_divergence_4_24"] = np.nan
        df["ema_cross_speed"]    = np.nan

    return df


def _add_mean_reversion(df: pd.DataFrame, ret1h: pd.Series) -> pd.DataFrame:
    """Z-score depuis SMA et vitesse de mean reversion."""
    close_s = _close(df)
    if close_s is None:
        df["zscore_from_sma_20"] = np.nan
        df["zscore_from_sma_50"] = np.nan
        df["mean_rev_speed_20"]  = np.nan
        return df

    sma20  = close_s.rolling(20, min_periods=8).mean().clip(lower=_EPS)
    std20  = close_s.rolling(20, min_periods=8).std().clip(lower=_EPS)
    sma50  = close_s.rolling(50, min_periods=20).mean().clip(lower=_EPS)
    std50  = close_s.rolling(50, min_periods=20).std().clip(lower=_EPS)

    df["zscore_from_sma_20"] = ((close_s - sma20) / std20).clip(-5, 5).fillna(0.0)
    df["zscore_from_sma_50"] = ((close_s - sma50) / std50).clip(-5, 5).fillna(0.0)

    # Vitesse de retour à la moyenne : combien de std le prix se rapproche en 1 barre
    z_prev = df["zscore_from_sma_20"].shift(1)
    z_curr = df["zscore_from_sma_20"]
    df["mean_rev_speed_20"] = (z_prev - z_curr).fillna(0.0)  # positif = retour vers moyenne

    return df


def _add_composite(df: pd.DataFrame, ret1h: pd.Series) -> pd.DataFrame:
    """Scores composites : crisis, trend quality, vol-trend alignment."""
    # Crisis score : vol_spike + sell_pressure + RSI_drop
    vol_spike_z = df.get("vol_spike_zscore_100", pd.Series(0.0, index=df.index))
    bear_prs    = df.get("bear_pressure_24", pd.Series(0.5, index=df.index))
    rsi_s       = df.get("rsi_14", pd.Series(50.0, index=df.index))
    if not isinstance(rsi_s, pd.Series):
        rsi_s = pd.Series(50.0, index=df.index)
    rsi_norm = pd.to_numeric(rsi_s, errors="coerce").fillna(50.0)

    vol_crisis = pd.to_numeric(vol_spike_z, errors="coerce").fillna(0.0).clip(0, 4) / 4.0
    sell_crisis = pd.to_numeric(bear_prs, errors="coerce").fillna(0.5)
    rsi_crisis  = ((50.0 - rsi_norm.clip(10, 50)) / 40.0).clip(0, 1)
    df["crisis_score"] = (vol_crisis + sell_crisis + rsi_crisis) / 3.0

    # Trend quality (R² approché : déplacement net / somme des mouvements)
    ret_arr = ret1h.values
    n = len(ret_arr)
    tq = np.full(n, np.nan)
    W = 20
    for i in range(W - 1, n):
        w = ret_arr[i - W + 1: i + 1]
        total_move = np.abs(w).sum()
        net_move   = abs(w.sum())
        tq[i] = net_move / (total_move + _EPS)
    df["trend_quality_20"] = tq

    # Vol-trend alignment : momentum haussier × vol basse
    mom8   = df.get("mom_accel_4_8", pd.Series(0.0, index=df.index))
    vov    = df.get("vol_of_vol_24", pd.Series(0.0, index=df.index))
    vzs    = df.get("vol_spike_zscore_100", pd.Series(0.0, index=df.index))

    mom_bull = (pd.to_numeric(mom8, errors="coerce").fillna(0.0) > 0).astype(float)
    vov_n    = pd.to_numeric(vov, errors="coerce").fillna(0.0).clip(0, 5)
    vzs_n    = pd.to_numeric(vzs, errors="coerce").fillna(0.0).clip(0, 5) + 1.0

    df["vol_trend_alignment"] = mom_bull * (1.0 - (vov_n / vzs_n).clip(0, 1))

    return df


# ─────────────────────────────────────────────────────────────────────────────
# Point d'entrée principal
# ─────────────────────────────────────────────────────────────────────────────

def compute_alpha_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule toutes les features alpha v5 et les ajoute à df.

    Anti-leakage : seules les données [0..t] sont utilisées.
    Appeler après compute_long_features() et compute_flow_features().

    Retourne df enrichi (copie).
    """
    df = df.copy()

    close_s = _close(df)
    if close_s is None:
        for col in FEATURES_ALPHA:
            if col not in df.columns:
                df[col] = np.nan
        return df

    # Log-returns 1h (cœur de tous les calculs)
    log_c  = np.log(close_s.clip(lower=_EPS))
    ret1h  = (log_c - log_c.shift(1)).fillna(0.0)
    ret_np = ret1h.values

    df = _add_vol_regime(df, ret1h)
    df = _add_autocorr(df, ret_np)
    df = _add_sharpe(df, ret_np)
    df = _add_skew_tail(df, ret_np)
    df = _add_ofi(df)
    df = _add_bear_bounce(df, ret1h)
    df = _add_amihud(df, ret1h)
    df = _add_entropy(df, ret_np)
    df = _add_kama(df)
    df = _add_microstructure(df, ret1h)
    df = _add_cross_horizon_mom(df, ret1h)
    df = _add_mean_reversion(df, ret1h)
    df = _add_composite(df, ret1h)

    # Remplir les NaN résiduels par 0 (conservateur)
    for col in FEATURES_ALPHA:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    return df
