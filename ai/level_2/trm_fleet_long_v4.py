"""
ai/level_2/trm_fleet_long_v4.py — TRM FLEET LONG v4  (100 TRM)
===============================================================

Architecture v4 — unification de toutes les versions précédentes :
  - 100 TRM spécialisés + 1 général = 10 horizons × 10 archétypes
  - Horizons : 4h, 8h, 12h, 1j, 3j, 1w, 2w, 1m, 1t, 1y
  - Archétypes : momentum_accel, trend_follow, breakout_escape, squeeze_release,
                 vwap_accum, pullback_reclaim, vol_shock, liquidity_squeeze,
                 regime_transition (NOUVEAU), mean_reversion (NOUVEAU)

Ce que v4 apporte vs v3 :
  • Horizon h08 (8h) — le SNR 8h > SNR 4h selon l'analyse du projet
  • Archétype regime_transition — capture les reversals bear→bull (point mort v3)
  • Archétype mean_reversion — oversold bounce après extension baissière
  • Routage top-k pondéré par (score - 0.5)^2 et val_auc conjointement
  • Capacité différenciée par horizon (court=240 iter, long=160 iter)
  • SPECIALIST_W adaptatif : monte si val_auc > 0.60, descend sinon

Points pris des versions précédentes :
  - v2 (multi-asset CSV) : SMOTE, gate NO_LONG, multi-actif training
  - v3 (institutional) : TRM grid, adaptive capacity, top-k routing, hard-example rounds
  - institutional_features.py : feature set robuste 80 features (fill > 75%)
  - short_labels.py : horizon secondaire 8h comme signal de confirmation
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler


# ─────────────────────────────────────────────────────────────────────────────
# Lattice temporelle v4 — 10 horizons (ajout de h08)
# ─────────────────────────────────────────────────────────────────────────────

_EPS = 1e-9


@dataclass(frozen=True)
class TemporalHorizon:
    key:   str
    hours: int
    label: str


@dataclass(frozen=True)
class MovementArchetype:
    key:            str
    label:          str
    train_quantile: float  # top (1-q) des barres scorées pour ce mouvement


@dataclass(frozen=True)
class SpecialistSpec:
    name:     str
    horizon:  TemporalHorizon
    movement: MovementArchetype


TEMPORAL_HORIZONS_V4: Tuple[TemporalHorizon, ...] = (
    TemporalHorizon("h04",   4,    "4h"),
    TemporalHorizon("h08",   8,    "8h"),    # NOUVEAU — SNR supérieur au 4h
    TemporalHorizon("h12",   12,   "12h"),
    TemporalHorizon("d01",   24,   "1d"),
    TemporalHorizon("d03",   72,   "3d"),
    TemporalHorizon("w01",   168,  "1w"),
    TemporalHorizon("w02",   336,  "2w"),
    TemporalHorizon("mo01",  720,  "1m"),
    TemporalHorizon("q01",   2160, "1q"),
    TemporalHorizon("y01",   8760, "1y"),
)

MOVEMENT_ARCHETYPES_V4: Tuple[MovementArchetype, ...] = (
    MovementArchetype("momentum_accel",    "accélération momentum",       0.70),
    MovementArchetype("trend_follow",      "continuation de tendance",    0.68),
    MovementArchetype("breakout_escape",   "cassure de range",            0.74),
    MovementArchetype("squeeze_release",   "compression puis expansion",  0.74),
    MovementArchetype("vwap_accum",        "accumulation VWAP",           0.70),
    MovementArchetype("pullback_reclaim",  "pullback puis reclaim",       0.72),
    MovementArchetype("vol_shock",         "choc de volatilité",          0.76),
    MovementArchetype("liquidity_squeeze", "short squeeze / liquidité",   0.76),
    MovementArchetype("regime_transition", "transition bear→bull",        0.72),  # NOUVEAU
    MovementArchetype("mean_reversion",    "rebond oversold",             0.70),  # NOUVEAU
)

SPECIALIST_SPECS_V4: Tuple[SpecialistSpec, ...] = tuple(
    SpecialistSpec(name=f"{h.key}_{m.key}", horizon=h, movement=m)
    for h in TEMPORAL_HORIZONS_V4
    for m in MOVEMENT_ARCHETYPES_V4
)

TRM_FLEET_SIZE_V4 = len(SPECIALIST_SPECS_V4) + 1   # 100 + 1 general = 101
PRIMARY_CONTEXT_SCORE_FLOOR = 0.55


# ─────────────────────────────────────────────────────────────────────────────
# Helpers vectorisés
# ─────────────────────────────────────────────────────────────────────────────

def _col(df: pd.DataFrame, name: str, default: float = 0.0) -> np.ndarray:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").fillna(default).to_numpy(dtype=np.float64)
    return np.full(len(df), default, dtype=np.float64)


def _col_any(df: pd.DataFrame, names: Tuple[str, ...], default: float = 0.0) -> np.ndarray:
    for n in names:
        if n in df.columns:
            return pd.to_numeric(df[n], errors="coerce").fillna(default).to_numpy(dtype=np.float64)
    return np.full(len(df), default, dtype=np.float64)


def _clean(a: np.ndarray, default: float = 0.0) -> np.ndarray:
    return np.nan_to_num(a.astype(np.float64), nan=default, posinf=default, neginf=default)


def _z(a: np.ndarray) -> np.ndarray:
    a = _clean(a)
    finite = a[np.isfinite(a)]
    if finite.size < 8:
        return np.zeros_like(a, dtype=np.float64)
    med = float(np.nanmedian(finite))
    mad = float(np.nanmedian(np.abs(finite - med)))
    scale = max(1.4826 * mad, float(np.nanstd(finite)), 1e-6)
    return np.clip((a - med) / scale, -6.0, 6.0)


def _sigmoid(a: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(a, -20.0, 20.0)))


def _ratio(num: np.ndarray, den: np.ndarray, default: float = 1.0) -> np.ndarray:
    out = np.divide(num, np.maximum(np.abs(den), _EPS))
    return _clean(out, default=default)


def _rolling(a: np.ndarray, window: int, fn: str) -> np.ndarray:
    n = len(a)
    if n == 0:
        return a
    w = int(max(2, min(window, n)))
    minp = max(2, min(w // 4, 48))
    s = pd.Series(_clean(a))
    roll = s.rolling(w, min_periods=minp)
    out = {"mean": roll.mean, "std": roll.std, "max": roll.max, "min": roll.min}[fn]()
    return out.bfill().ffill().fillna(0.0).to_numpy(dtype=np.float64)


def _shift(a: np.ndarray, periods: int, fill: float) -> np.ndarray:
    out = np.empty_like(a, dtype=np.float64)
    if periods <= 0:
        return a.astype(np.float64)
    out[:periods] = fill
    out[periods:] = a[:-periods]
    return out


def _logret(log_close: np.ndarray, window: int) -> np.ndarray:
    n = len(log_close)
    if n == 0:
        return log_close
    w = int(max(1, min(window, max(n - 1, 1))))
    out = np.zeros(n, dtype=np.float64)
    out[w:] = log_close[w:] - log_close[:-w]
    return _clean(out)


# ─────────────────────────────────────────────────────────────────────────────
# Signaux temporels (horizon-dépendants)
# ─────────────────────────────────────────────────────────────────────────────

def _temporal_signals(df: pd.DataFrame, horizon_hours: int) -> Dict[str, np.ndarray]:
    n = len(df)
    close_raw = _col_any(df, ("close", "Close"), default=1.0)
    close_valid = np.isfinite(close_raw) & (close_raw > 0)
    close_fill = float(np.nanmedian(close_raw[close_valid])) if close_valid.any() else 1.0
    close = (
        pd.Series(close_raw)
        .where(close_valid)
        .ffill().bfill().fillna(close_fill)
        .to_numpy(dtype=np.float64)
    )
    close = np.maximum(close, _EPS)

    volume_raw = _col_any(df, ("volume", "Volume"), default=0.0)
    volume = np.maximum(_clean(volume_raw, default=0.0), 0.0)
    log_close = np.log(close)

    w      = int(max(2, min(horizon_hours, max(n - 1, 2))))
    fast_w = max(2, w // 4)
    mid_w  = max(3, w // 2)
    long_w = max(w + 1, min(max(n - 1, 2), w * 2))

    ret_1    = _logret(log_close, 1)
    ret_fast = _logret(log_close, fast_w)
    ret_mid  = _logret(log_close, mid_w)
    ret_full = _logret(log_close, w)

    vol_fast = _rolling(ret_1, fast_w, "std")
    vol_full = _rolling(ret_1, w,      "std")
    vol_long = _rolling(ret_1, long_w, "std")

    prior_close = _shift(close, 1, close[0] if n else 1.0)
    prior_high  = _rolling(prior_close, w, "max")
    prior_low   = _rolling(prior_close, w, "min")
    dist_high   = close / np.maximum(prior_high, _EPS) - 1.0
    dist_low    = close / np.maximum(prior_low,  _EPS) - 1.0

    vol_mean = _rolling(volume, w, "mean")
    vol_z    = _z(_ratio(volume, vol_mean, default=1.0))

    return {
        "ret_fast":            ret_fast,
        "ret_mid":             ret_mid,
        "ret_full":            ret_full,
        "accel":               ret_fast - ret_mid,
        "abs_ret_fast":        np.abs(ret_fast),
        "vol_fast":            vol_fast,
        "vol_full":            vol_full,
        "vol_ratio_fast_full": _ratio(vol_fast, vol_full, default=1.0),
        "vol_ratio_full_long": _ratio(vol_full, vol_long, default=1.0),
        "dist_high":           dist_high,
        "dist_low":            dist_low,
        "drawdown_from_high":  -np.minimum(dist_high, 0.0),
        "volume_z":            vol_z,
        # Features calculées (présentes dans CSV + institutional)
        "ema_spread_20_50":    _col(df, "ema_spread_20_50"),
        "ema_spread_50_200":   _col_any(df, ("ema_spread_50_200", "ema_50_200_spread")),
        "dist_ema_20":         _col_any(df, ("dist_ema_20", "distance_ema_20")),
        "dist_ema_50":         _col_any(df, ("dist_ema_50", "distance_ema_50")),
        "dist_ema_200":        _col_any(df, ("dist_ema_200", "distance_ema_200")),
        "rv_ratio_24_72":      _col(df, "rv_ratio_24_72", 1.0),
        "rv_ratio_12_48":      _col(df, "rv_ratio_12_48", 1.0),
        "boll_width_20":       _col_any(df, ("boll_width_20", "bb_width_20"), 0.02),
        "boll_pos_20":         _col_any(df, ("boll_pos_20", "bb_percent_b_20"), 0.5),
        "boll_expansion_6":    _col(df, "boll_expansion_6"),
        "momentum_accel_6":    _col(df, "momentum_accel_6"),
        "trend_persistence_12":_col(df, "trend_persistence_12"),
        "breakout_strength_24":_col(df, "breakout_strength_24"),
        "above_vwap_4h":       _col(df, "above_vwap_4h", 0.5),
        "dist_vwap_pct":       _col(df, "dist_vwap_pct"),
        "taker_buy_ratio_base":_col(df, "taker_buy_ratio_base", 0.5),
        "delta_taker_pressure":_col(df, "delta_taker_pressure"),
        "vol_imbalance":       _col(df, "vol_imbalance"),
        "buy_vol_ratio_6":     _col(df, "buy_vol_ratio_6", 0.5),
        "liq_short_spike_12":  _col(df, "liq_short_spike_12"),
        "liq_imbalance":       _col(df, "liq_imbalance"),
        "rsi_14":              _col_any(df, ("rsi_14", "rsi_13"), 50.0),
        # Nouveaux pour regime_transition et mean_reversion
        "trend_score":         _col(df, "trend_score"),
        "momentum_score":      _col(df, "momentum_score"),
        "efficiency_ratio_20": _col_any(df, ("efficiency_ratio_20", "eff_ratio_24")),
        "adx_20":              _col_any(df, ("adx_20", "adx_14")),
        "di_spread_20":        _col_any(df, ("di_spread_20", "di_diff")),
        "choppiness_20":       _col(df, "choppiness_20"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scoring des archétypes
# ─────────────────────────────────────────────────────────────────────────────

def _score_movement_v4(sig: Dict[str, np.ndarray], movement_key: str) -> np.ndarray:
    ret_fast = sig["ret_fast"]
    ret_full = sig["ret_full"]

    trend = (
        _z(ret_full)
        + 0.55 * _z(sig["ema_spread_50_200"])
        + 0.35 * _z(sig["ema_spread_20_50"])
    )
    flow = (
        _z(sig["taker_buy_ratio_base"] - 0.5)
        + 0.7 * _z(sig["delta_taker_pressure"])
        + 0.6 * _z(sig["vol_imbalance"])
    )

    if movement_key == "momentum_accel":
        raw = 1.15 * _z(sig["accel"]) + 0.9 * _z(ret_fast) + 0.5 * _z(sig["momentum_accel_6"])

    elif movement_key == "trend_follow":
        raw = trend + 0.5 * _z(sig["trend_persistence_12"]) + 0.35 * _z(sig["dist_ema_50"])

    elif movement_key == "breakout_escape":
        raw = (
            1.15 * _z(sig["dist_high"])
            + 0.75 * _z(ret_fast)
            + 0.55 * sig["volume_z"]
            + 0.45 * _z(sig["breakout_strength_24"])
        )

    elif movement_key == "squeeze_release":
        compression = -_z(sig["boll_width_20"]) - 0.6 * _z(sig["vol_full"])
        release = 0.75 * _z(sig["vol_ratio_fast_full"]) + 0.55 * _z(sig["boll_expansion_6"])
        raw = compression + release + 0.45 * _z(ret_fast)

    elif movement_key == "vwap_accum":
        raw = (
            0.95 * _z(sig["above_vwap_4h"] - 0.5)
            + 0.75 * _z(sig["dist_vwap_pct"])
            + 0.65 * flow
            + 0.35 * _z(sig["ret_mid"])
        )

    elif movement_key == "pullback_reclaim":
        pullback = _z(sig["drawdown_from_high"]) - 0.45 * _z(sig["boll_pos_20"] - 0.5)
        reclaim = (
            0.85 * _z(ret_fast)
            + 0.55 * _z(sig["dist_ema_20"])
            + 0.35 * _z(55.0 - sig["rsi_14"])
        )
        raw = 0.65 * trend + pullback + reclaim

    elif movement_key == "vol_shock":
        raw = (
            1.05 * _z(sig["vol_ratio_fast_full"])
            + 0.85 * _z(sig["abs_ret_fast"])
            + 0.55 * sig["volume_z"]
            + 0.35 * _z(sig["rv_ratio_24_72"] - 1.0)
        )

    elif movement_key == "liquidity_squeeze":
        raw = (
            1.10 * _z(sig["liq_short_spike_12"])
            - 0.60 * _z(sig["liq_imbalance"])
            + 0.70 * flow
            + 0.45 * _z(ret_fast)
        )

    elif movement_key == "regime_transition":
        # Bear→bull : RSI bas + rebond momentum + retour au-dessus EMA50
        # Signal : après un drawdown, les indicateurs pivotent positivement
        rsi_rebound = _z(sig["rsi_14"] - 40.0)         # RSI remonte depuis oversold
        ema_cross   = _z(sig["dist_ema_50"])             # retour vers EMA50
        trend_turn  = _z(sig["trend_score"])             # score composites institutional
        momentum    = _z(sig["momentum_score"])
        adx_rising  = _z(sig["adx_20"])                  # force directionnelle croissante
        raw = (
            0.90 * rsi_rebound
            + 0.80 * ema_cross
            + 0.70 * trend_turn
            + 0.60 * momentum
            + 0.45 * adx_rising
            + 0.40 * _z(ret_fast)
            - 0.50 * _z(sig["choppiness_20"])            # pénaliser le range choppeux
        )

    elif movement_key == "mean_reversion":
        # Oversold bounce : extension baissière extrême + retournement imminent
        # Signal : prix très bas vs historique, RSI extrêmement bas, volume de capitulation
        dist_low_z  = -_z(sig["dist_low"])               # distance au plus bas récent (inversée)
        rsi_extreme = _z(50.0 - sig["rsi_14"])           # plus bas = meilleur pour MR
        boll_extreme = _z(0.5 - sig["boll_pos_20"])      # proche du bas des BB
        vol_climax  = _z(sig["vol_ratio_fast_full"])     # spike de vol = capitulation
        raw = (
            1.00 * dist_low_z
            + 0.85 * rsi_extreme
            + 0.70 * boll_extreme
            + 0.55 * vol_climax
            + 0.40 * _z(sig["liq_imbalance"])            # short squeeze potential
            - 0.40 * trend                               # contre-tendance = pénaliser trend fort
        )

    else:
        raw = np.zeros_like(ret_full)

    return np.nan_to_num(_sigmoid(raw), nan=0.0, posinf=1.0, neginf=0.0).astype(np.float32)


def build_specialist_scores_v4(
    df:    pd.DataFrame,
    specs: Tuple[SpecialistSpec, ...] = SPECIALIST_SPECS_V4,
) -> pd.DataFrame:
    """Score causal de chaque TRM sur chaque barre — sans look-ahead."""
    if len(df) == 0:
        return pd.DataFrame(index=df.index, columns=[s.name for s in specs], dtype=np.float32)

    by_horizon: Dict[str, Dict[str, np.ndarray]] = {}
    scores: Dict[str, np.ndarray] = {}
    for spec in specs:
        if spec.horizon.key not in by_horizon:
            by_horizon[spec.horizon.key] = _temporal_signals(df, spec.horizon.hours)
        scores[spec.name] = _score_movement_v4(by_horizon[spec.horizon.key], spec.movement.key)
    return pd.DataFrame(scores, index=df.index, dtype=np.float32)


def classify_context_v4(df: pd.DataFrame) -> np.ndarray:
    """Contexte primaire par argmax du score des spécialistes."""
    n = len(df)
    if n == 0:
        return np.array([], dtype=object)
    score_df = build_specialist_scores_v4(df)
    values   = score_df.to_numpy(dtype=np.float32)
    best_idx = np.argmax(values, axis=1)
    best_score = values[np.arange(n), best_idx]
    names = np.array(score_df.columns.to_list(), dtype=object)
    ctx = names[best_idx].astype(object)
    ctx[best_score < PRIMARY_CONTEXT_SCORE_FLOOR] = "general"
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# Spécialiste individuel
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TinySpecialistV4:
    """
    TRM individuel — même design que v3 mais avec les 2 nouveaux archétypes
    et la capacité optimisée pour h08.
    """
    context_name: str
    features:     List[str]
    spec:         Optional[SpecialistSpec] = None
    clf_:         Optional[Any]            = field(default=None, repr=False)
    scaler_:      Optional[StandardScaler] = field(default=None, repr=False)
    val_auc_:     float = 0.0
    n_train_:     int   = 0
    n_pos_:       int   = 0
    score_threshold_: float = 0.0

    def _get_X(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        missing = [f for f in self.features if f not in df.columns]
        if missing:
            raise RuntimeError(
                f"TRM {self.context_name!r} — features manquantes dans df : {missing}\n"
                f"Appeler get_available_features(df, features) avant de créer la fleet."
            )
        X = df.loc[mask, self.features].fillna(0.0).values.astype(np.float32)
        return X

    def fit(
        self,
        df:            pd.DataFrame,
        train_mask:    np.ndarray,
        val_mask:      Optional[np.ndarray] = None,
        sample_weight: Optional[np.ndarray] = None,
        label_col:     str                  = "y_long",
    ) -> "TinySpecialistV4":
        X_tr = self._get_X(df, train_mask)
        y_tr = df.loc[train_mask, label_col].values.astype(np.int32)

        valid = y_tr >= 0
        X_tr  = X_tr[valid]
        y_tr  = y_tr[valid]
        if sample_weight is not None:
            sample_weight = sample_weight[valid]

        self.n_train_ = len(y_tr)
        self.n_pos_   = int(y_tr.sum())

        if self.n_pos_ < 5 or self.n_train_ < 20:
            return self

        self.scaler_ = StandardScaler()
        Xsc = self.scaler_.fit_transform(X_tr)

        n_neg = len(y_tr) - self.n_pos_
        spw   = min(n_neg / max(self.n_pos_, 1), 80.0)

        # Capacité adaptative v4 :
        #   général : large (360 iter)
        #   h04/h08 : moyen (260 iter, depth 4) — horizons courts, bruit élevé
        #   h12/d01/d03 : moyen (200 iter)
        #   w01+ : compact (160 iter, depth 3) — horizon long, moins de bruit mais moins de données
        if self.context_name == "general":
            max_iter, max_depth, min_leaf = 360, 4, 20
        elif self.spec is not None:
            h = self.spec.horizon.hours
            if h <= 8:
                max_iter, max_depth, min_leaf = 260, 4, 16
            elif h <= 72:
                max_iter, max_depth, min_leaf = 200, 4, 18
            else:
                max_iter, max_depth, min_leaf = 160, 3, 24
        else:
            max_iter, max_depth, min_leaf = 200, 4, 18

        self.clf_ = HistGradientBoostingClassifier(
            max_iter=max_iter, max_depth=max_depth, learning_rate=0.04,
            l2_regularization=1.0, min_samples_leaf=min_leaf,
            class_weight={0: 1.0, 1: spw}, random_state=42,
        )
        self.clf_.fit(Xsc, y_tr, sample_weight=sample_weight)

        if (val_mask is not None
                and len(val_mask) == len(df)
                and int(val_mask.sum()) > 10):
            X_val = self._get_X(df, val_mask)
            y_val = df.loc[val_mask, label_col].values.astype(np.int32)
            valid_v = y_val >= 0
            X_val, y_val = X_val[valid_v], y_val[valid_v]
            if len(X_val) > 10 and y_val.sum() >= 2:
                p = self.clf_.predict_proba(self.scaler_.transform(X_val))[:, 1]
                self.val_auc_ = float(roc_auc_score(y_val, p))

        return self

    def predict_proba(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        if self.clf_ is None or self.scaler_ is None:
            return np.full(int(mask.sum()), 0.5, dtype=np.float32)
        X   = self._get_X(df, mask)
        Xsc = self.scaler_.transform(X)
        return self.clf_.predict_proba(Xsc)[:, 1].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Calibration PnL par contexte
# ─────────────────────────────────────────────────────────────────────────────

def calibrate_context_thresholds_v4(
    fleet:      "TRMFleetLongV4",
    df_val:     pd.DataFrame,
    filter_p:   np.ndarray,
    filter_thr: float,
    ret_val:    np.ndarray,
    cost_pct:   float = 0.001,
    min_thr:    float = 0.54,
    max_thr:    float = 0.66,
    min_trades: int   = 15,   # relevé de 5→15 : volume minimal pour calibration stable
) -> Dict[str, float]:
    """
    Calibre un seuil PnL par contexte primaire sur la validation.

    Critère : PnL total net × WR_bonus (pénalise WR < 50%)
    Ce critère équilibre volume et qualité — évite de trouver 1 trade parfait
    avec un seuil très haut au détriment de 30 trades profitables avec un seuil raisonnable.
    """
    n       = len(df_val)
    ones    = np.ones(n, dtype=bool)
    ctx_arr = classify_context_v4(df_val)
    p_fleet = fleet.predict(df_val, ones)

    thresholds: Dict[str, float] = {}

    for ctx, spec in fleet.specialists.items():
        if spec.clf_ is None:
            thresholds[ctx] = min_thr
            continue

        filt_ok = filter_p >= filter_thr
        ctx_ok  = (ctx_arr == ctx) if ctx != "general" else np.ones(n, dtype=bool)
        sel     = filt_ok & ctx_ok

        p_sub   = p_fleet[sel]
        ret_sub = ret_val[sel]
        valid_s = np.isfinite(ret_sub)
        p_sub, ret_sub = p_sub[valid_s], ret_sub[valid_s]

        if len(p_sub) < min_trades:
            thresholds[ctx] = min_thr
            continue

        best_thr, best_score = min_thr, -np.inf
        for thr in np.arange(min_thr, max_thr + 0.001, 0.01):
            sel_t = p_sub >= thr
            n_t   = int(sel_t.sum())
            if n_t < min_trades:
                continue
            rets_t   = ret_sub[sel_t] - cost_pct
            # Test de robustesse coûts : PF à 3× coût doit rester > 1.0
            rets_stress = ret_sub[sel_t] - cost_pct * 3.0
            gw_stress = float(rets_stress[rets_stress > 0].sum())
            gl_stress = float(abs(rets_stress[rets_stress < 0].sum()))
            pf_stress = gw_stress / max(gl_stress, 1e-9)
            if pf_stress < 1.0:
                continue   # fragile au coût — seuil refusé

            pnl  = float(rets_t.sum())
            wr   = float((rets_t > 0).sum()) / n_t
            # Score : PnL total × bonus WR
            wr_bonus = 0.5 + max(0.0, wr - 0.40) * 4.0
            score    = pnl * wr_bonus
            if score > best_score:
                best_score, best_thr = score, thr

        # Si aucun seuil ne passe le test PF_stress, relâcher progressivement
        if best_score == -np.inf:
            for thr in np.arange(min_thr, max_thr + 0.001, 0.01):
                sel_t = p_sub >= thr
                if sel_t.sum() >= min_trades:
                    rets_t = ret_sub[sel_t] - cost_pct
                    pnl    = float(rets_t.sum())
                    wr     = float((rets_t > 0).sum()) / sel_t.sum()
                    wr_bonus = 0.5 + max(0.0, wr - 0.40) * 4.0
                    score  = pnl * wr_bonus
                    if score > best_score:
                        best_score, best_thr = score, thr

        # Stabilité ±0.02 : PnL ne doit pas chuter de plus de 25%
        pnl_best = float((ret_sub[p_sub >= best_thr] - cost_pct).sum())
        for nb_thr in (best_thr - 0.02, best_thr + 0.02):
            if nb_thr < min_thr or nb_thr > max_thr:
                continue
            pnl_nb = float((ret_sub[p_sub >= nb_thr] - cost_pct).sum())
            if pnl_best > 0 and pnl_nb < pnl_best * 0.75:
                best_thr = min(best_thr + 0.01, max_thr)

        # Contrainte min_trades
        if int((p_sub >= best_thr).sum()) < min_trades:
            for thr in np.arange(best_thr - 0.01, min_thr - 0.001, -0.01):
                if int((p_sub >= thr).sum()) >= min_trades:
                    best_thr = thr
                    break

        thresholds[ctx] = round(float(best_thr), 2)

    return thresholds


# ─────────────────────────────────────────────────────────────────────────────
# TRM Fleet Long v4
# ─────────────────────────────────────────────────────────────────────────────

class TRMFleetLongV4:
    """
    Flottée TRM LONG v4 — 100 TRM spécialisés + 1 général.

    Innovations vs v3 :
      • h08 (8h) — le SNR 8h s'est avéré > 4h dans les études du projet
      • regime_transition + mean_reversion — comblent les folds perdus en 2023/2025
      • Routage top-k pondéré par (score - 0.5)^2 × auc_boost
        auc_boost = 1 + max(0, val_auc - 0.58) : les TRM avec meilleur AUC ont plus de poids
      • SPECIALIST_W adaptatif : 0.65 si fleet_auc_mean < 0.58, 0.72 sinon
    """

    BASE_SPECIALIST_W  = 0.72
    BASE_GENERAL_W     = 0.28
    AUC_BOOST_FLOOR    = 0.58   # en dessous : pas de boost

    def __init__(
        self,
        features:            List[str],
        n_recursive_rounds:  int            = 2,
        routing_top_k:       int            = 5,   # k=5 vs k=4 en v3
        min_specialist_rows: int            = 120,
    ):
        self.features            = features
        self.n_recursive_rounds  = n_recursive_rounds
        self.routing_top_k       = max(1, int(routing_top_k))
        self.min_specialist_rows = max(20, int(min_specialist_rows))
        self.specialist_specs    = SPECIALIST_SPECS_V4
        self.context_names       = [s.name for s in self.specialist_specs] + ["general"]
        self.specialists: Dict[str, TinySpecialistV4] = {}
        self.n_ctx_: Dict[str, int] = {}
        self._fleet_auc_mean: float = 0.0
        self._init_specialists()

    def _init_specialists(self) -> None:
        for spec in self.specialist_specs:
            self.specialists[spec.name] = TinySpecialistV4(
                context_name=spec.name, features=self.features, spec=spec,
            )
        self.specialists["general"] = TinySpecialistV4(
            context_name="general", features=self.features,
        )

    def _select_score_tail(
        self, scores: np.ndarray, spec: SpecialistSpec
    ) -> Tuple[np.ndarray, float]:
        valid_idx = np.where(np.isfinite(scores))[0]
        mask = np.zeros(len(scores), dtype=bool)
        if len(valid_idx) == 0:
            return mask, 0.0
        target  = int(np.ceil((1.0 - spec.movement.train_quantile) * len(valid_idx)))
        min_rows = min(self.min_specialist_rows, max(20, len(valid_idx) // 5))
        target  = min(len(valid_idx), max(min_rows, target))
        order   = np.argsort(scores[valid_idx], kind="mergesort")
        chosen  = valid_idx[order[-target:]]
        mask[chosen] = True
        thr = float(np.nanmin(scores[chosen])) if len(chosen) else 0.0
        return mask, thr

    def _compute_val_aucs(
        self, df_val: pd.DataFrame, val_mask: np.ndarray, label_col: str = "y_long"
    ) -> None:
        """Calcule l'AUC val de chaque spécialiste sur le dataset de validation (BTC seul)."""
        df_sub   = df_val.loc[val_mask] if val_mask is not None else df_val
        score_df = build_specialist_scores_v4(df_sub.reset_index(drop=True))
        df_sub_ri= df_sub.reset_index(drop=True)
        y_val    = df_sub_ri[label_col].values.astype(np.int32) if label_col in df_sub_ri.columns \
                   else np.zeros(len(df_sub_ri), dtype=np.int32)
        ones     = np.ones(len(df_sub_ri), dtype=bool)

        for ctx, spec in self.specialists.items():
            if spec.clf_ is None:
                continue
            if ctx == "general":
                ctx_sel = ones
            else:
                assert spec.spec is not None
                score_local = score_df[ctx].to_numpy(dtype=np.float32) if ctx in score_df.columns \
                              else np.zeros(len(df_sub_ri), dtype=np.float32)
                ctx_sel, _ = self._select_score_tail(score_local, spec.spec)

            y_sub   = y_val[ctx_sel]
            valid_v = y_sub >= 0
            y_sub   = y_sub[valid_v]
            if len(y_sub) < 10 or y_sub.sum() < 2:
                continue
            X_sub  = spec._get_X(df_sub_ri, ctx_sel)
            X_sub  = X_sub[valid_v]
            p_sub  = spec.clf_.predict_proba(spec.scaler_.transform(X_sub))[:, 1]
            try:
                spec.val_auc_ = float(roc_auc_score(y_sub, p_sub))
            except Exception:
                pass

    def train(
        self,
        df:              pd.DataFrame,    # train combiné (multi-actif)
        train_mask:      np.ndarray,
        df_val_btc:      Optional[pd.DataFrame] = None,   # val BTC uniquement (pour AUC)
        val_mask_in_btc: Optional[np.ndarray]   = None,   # masque dans df_val_btc
        label_col:       str                    = "y_long",
    ) -> "TRMFleetLongV4":
        t0         = time.time()
        train_idx  = np.where(train_mask)[0]
        n_train    = len(train_idx)
        weights_now = np.ones(n_train, dtype=np.float64)
        score_df   = build_specialist_scores_v4(df.loc[train_mask].reset_index(drop=True))

        for rnd in range(self.n_recursive_rounds):
            for ctx, spec in self.specialists.items():
                if ctx == "general":
                    ctx_in_train = np.ones(n_train, dtype=bool)
                else:
                    score_local = score_df.loc[:, ctx].to_numpy(dtype=np.float32)
                    assert spec.spec is not None
                    ctx_in_train, score_threshold = self._select_score_tail(score_local, spec.spec)
                    spec.score_threshold_ = score_threshold

                ctx_mask = train_mask.copy()
                ctx_mask[train_idx[~ctx_in_train]] = False
                if ctx_mask.sum() < 20:
                    continue

                ctx_weights = weights_now[ctx_in_train]
                if ctx != "general":
                    sw_local = score_df.loc[:, ctx].to_numpy(dtype=np.float64)
                    ctx_weights = ctx_weights * (1.0 + np.clip(sw_local[ctx_in_train], 0.0, 1.0))

                # Pas de val_mask ici — val AUC calculé séparément après training
                spec.fit(df, ctx_mask, val_mask=None,
                         sample_weight=ctx_weights, label_col=label_col)

            if rnd < self.n_recursive_rounds - 1:
                p_ens     = self._predict_raw(df, train_mask)
                uncertain = np.abs(p_ens - 0.5) < 0.12
                weights_now = np.where(uncertain, 3.0, 1.0).astype(np.float64)

        # AUC val — calculé séparément sur df_val_btc (même pattern que tiny_specialists v3)
        if df_val_btc is not None and val_mask_in_btc is not None:
            self._compute_val_aucs(df_val_btc, val_mask_in_btc, label_col)

        auc_vals = [s.val_auc_ for s in self.specialists.values() if s.val_auc_ > 0]
        self._fleet_auc_mean = float(np.mean(auc_vals)) if auc_vals else 0.0

        dt         = time.time() - t0
        primary_ctx = classify_context_v4(df.loc[train_mask])
        n_ctx       = {n: int((primary_ctx == n).sum()) for n in self.context_names}
        self.n_ctx_ = n_ctx
        aucs        = {n: round(s.val_auc_, 3) for n, s in self.specialists.items() if s.clf_}
        trained     = [n for n, s in self.specialists.items() if s.clf_ is not None]
        top_ctx     = sorted(((k, v) for k, v in n_ctx.items() if v > 0),
                             key=lambda x: x[1], reverse=True)[:10]
        auc_top     = sorted(((k, v) for k, v in aucs.items() if v > 0),
                             key=lambda x: x[1], reverse=True)[:10]
        thr_adaptive = 0.54 if self._fleet_auc_mean < 0.58 else 0.55

        print(f"   TRMFleetLong v4 : {len(trained)}/{len(self.specialists)} TRM entraînés  "
              f"top_k={self.routing_top_k}  rounds={self.n_recursive_rounds}  t={dt:.1f}s")
        print(f"   Contextes primaires top10 : " +
              "  ".join(f"{k}={v:,}" for k, v in top_ctx))
        print(f"   AUC val top10 : " +
              "  ".join(f"{k}={v:.3f}" for k, v in auc_top))
        print(f"   Threshold min adaptatif : {thr_adaptive:.2f}  "
              f"(AUC moyen={self._fleet_auc_mean:.3f})")
        return self

    def _predict_raw(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        return self.predict(df, mask, verbose=False)

    def predict(
        self,
        df:      pd.DataFrame,
        mask:    np.ndarray,
        verbose: bool = False,
    ) -> np.ndarray:
        """
        Routage top-k avec boost AUC.
        p_final = general_w × p_general + specialist_w × Σ(w_k × auc_k × p_k)
        """
        df_sub = df.loc[mask].copy().reset_index(drop=True)
        n      = len(df_sub)
        if n == 0:
            return np.array([], dtype=np.float32)
        ones   = np.ones(n, dtype=bool)

        p_general = self.specialists["general"].predict_proba(df_sub, ones)
        p_out     = p_general.copy()

        trained_names = [
            s.name for s in self.specialist_specs
            if s.name in self.specialists and self.specialists[s.name].clf_ is not None
        ]
        if not trained_names:
            return p_out.astype(np.float32)

        score_df     = build_specialist_scores_v4(
            df_sub, tuple(s for s in self.specialist_specs if s.name in trained_names)
        )
        score_values = score_df[trained_names].to_numpy(dtype=np.float32)
        k            = min(self.routing_top_k, len(trained_names))
        top_idx      = np.argpartition(score_values, -k, axis=1)[:, -k:]
        top_scores   = np.take_along_axis(score_values, top_idx, axis=1)
        active_rows  = top_scores.max(axis=1) >= PRIMARY_CONTEXT_SCORE_FLOOR
        if not active_rows.any():
            return p_out.astype(np.float32)

        # Poids = (score - 0.5)^2 × auc_boost
        auc_arr = np.array(
            [self.specialists[n].val_auc_ for n in trained_names], dtype=np.float32
        )
        auc_boost = 1.0 + np.maximum(auc_arr - self.AUC_BOOST_FLOOR, 0.0)

        raw_w = np.maximum(top_scores - 0.50, 0.0) ** 2 + 1e-5
        raw_w[~active_rows] = 0.0

        p_mix = np.zeros(n, dtype=np.float32)
        ctx_counts: Dict[str, int] = {}
        for j, name in enumerate(trained_names):
            where    = (top_idx == j) & active_rows[:, None]
            row_mask = where.any(axis=1)
            if not row_mask.any():
                continue
            spec = self.specialists[name]
            ctx_counts[name] = int(row_mask.sum())
            p_spec       = spec.predict_proba(df_sub, row_mask)
            row_weights  = np.zeros(n, dtype=np.float32)
            row_weights[row_mask] = raw_w[where] * auc_boost[j]
            p_mix[row_mask] += row_weights[row_mask] * p_spec

        # Normaliser p_mix
        row_weight_sum = np.zeros(n, dtype=np.float32)
        for j, name in enumerate(trained_names):
            where    = (top_idx == j) & active_rows[:, None]
            row_mask = where.any(axis=1)
            if not row_mask.any():
                continue
            row_weights = np.zeros(n, dtype=np.float32)
            row_weights[row_mask] = raw_w[where] * auc_boost[j]
            row_weight_sum[row_mask] += row_weights[row_mask]

        nonzero = row_weight_sum > 0
        p_mix_norm = np.where(nonzero, p_mix / np.maximum(row_weight_sum, _EPS), p_general)

        # Poids specialist adaptatif
        sp_w = self.BASE_SPECIALIST_W if self._fleet_auc_mean >= self.AUC_BOOST_FLOOR else 0.65
        gn_w = 1.0 - sp_w
        p_out[active_rows] = (
            gn_w * p_general[active_rows]
            + sp_w * p_mix_norm[active_rows]
        )

        if verbose:
            top = sorted(ctx_counts.items(), key=lambda x: x[1], reverse=True)[:10]
            print("   TRM LONG v4 predict top10 : " + " ".join(f"{k}={v:,}" for k, v in top))

        return p_out.astype(np.float32)

    def adaptive_threshold(self) -> float:
        """Seuil minimal adaptatif basé sur l'AUC moyenne de la fleet."""
        return 0.54 if self._fleet_auc_mean < 0.58 else 0.55

    def val_auc_summary(self) -> Dict[str, float]:
        return {n: round(s.val_auc_, 3) for n, s in self.specialists.items()}

    def to_fleet_report(self) -> Dict:
        return {
            "version":       "v4",
            "n_total":       len(self.specialists),
            "n_trained":     sum(1 for s in self.specialists.values() if s.clf_ is not None),
            "fleet_auc_mean": round(self._fleet_auc_mean, 4),
            "n_horizons":    len(TEMPORAL_HORIZONS_V4),
            "n_archetypes":  len(MOVEMENT_ARCHETYPES_V4),
            "routing_top_k": self.routing_top_k,
        }
