"""
ai/level_2/tiny_specialists.py — FLOTTE TRM MULTI-HORIZON v3
============================================================

Architecture v3 :
  - 72 TRM specialises + 1 general = 9 horizons temporels x 8 mouvements.
  - Horizons : 4h, 12h, 1j, 3j, 1w, 2w, 1m, 1 trimestre, 1 an.
  - Mouvements : acceleration momentum, trend-follow, breakout, squeeze,
    VWAP accumulation, pullback reclaim, choc de volatilite, liquidity squeeze.
  - Chaque TRM utilise toutes les FEATURES_LONG ; la specialisation vient de
    son masque d'entrainement causal et multi-resolution.
  - Routage top-k : chaque barre est melangee par les meilleurs specialistes
    actifs + un modele general, au lieu d'un routage dur a 6 contextes.

Apprentissage recursif :
    Round 1 : chaque TRM apprend sur la queue haute de sa signature temporelle.
    Round 2 : les barres incertaines de l'ensemble sont reponderees x3.

Objectif : capter des mouvements rares a differentes distances temporelles
sans perdre la robustesse du modele general.
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
# Définition des spécialistes — lattice temporelle multi-résolution
# ─────────────────────────────────────────────────────────────────────────────

_EPS = 1e-9


@dataclass(frozen=True)
class TemporalHorizon:
    key: str
    hours: int
    label: str


@dataclass(frozen=True)
class MovementArchetype:
    key: str
    label: str
    train_quantile: float


@dataclass(frozen=True)
class SpecialistSpec:
    """
    Une cellule de la fleet : un horizon temporel × un mouvement de marché.

    Le masque d'entraînement n'est pas exclusif : chaque TRM apprend sur les
    barres où son score est dans la queue haute de SA signature temporelle.
    """
    name: str
    horizon: TemporalHorizon
    movement: MovementArchetype


TEMPORAL_HORIZONS: Tuple[TemporalHorizon, ...] = (
    TemporalHorizon("h04", 4, "4h"),
    TemporalHorizon("h12", 12, "12h"),
    TemporalHorizon("d01", 24, "1d"),
    TemporalHorizon("d03", 72, "3d"),
    TemporalHorizon("w01", 168, "1w"),
    TemporalHorizon("w02", 336, "2w"),
    TemporalHorizon("mo01", 720, "1m"),
    TemporalHorizon("q01", 2160, "1q"),
    TemporalHorizon("y01", 8760, "1y"),
)


MOVEMENT_ARCHETYPES: Tuple[MovementArchetype, ...] = (
    MovementArchetype("momentum_accel", "acceleration momentum", 0.70),
    MovementArchetype("trend_follow", "continuation de tendance", 0.68),
    MovementArchetype("breakout_escape", "cassure de range", 0.74),
    MovementArchetype("squeeze_release", "compression puis expansion", 0.74),
    MovementArchetype("vwap_accum", "accumulation VWAP", 0.70),
    MovementArchetype("pullback_reclaim", "pullback puis reclaim", 0.72),
    MovementArchetype("vol_shock", "choc de volatilité", 0.76),
    MovementArchetype("liquidity_squeeze", "short squeeze / liquidité", 0.76),
)


SPECIALIST_SPECS: Tuple[SpecialistSpec, ...] = tuple(
    SpecialistSpec(
        name=f"{h.key}_{m.key}",
        horizon=h,
        movement=m,
    )
    for h in TEMPORAL_HORIZONS
    for m in MOVEMENT_ARCHETYPES
)

CONTEXT_NAMES = [s.name for s in SPECIALIST_SPECS] + ["general"]
TRM_FLEET_SIZE = len(CONTEXT_NAMES)
PRIMARY_CONTEXT_SCORE_FLOOR = 0.56


def _col(df: pd.DataFrame, name: str, default: float = 0.0) -> np.ndarray:
    if name in df.columns:
        return pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=np.float64)
    return np.full(len(df), default, dtype=np.float64)


def _col_any(df: pd.DataFrame, names: Tuple[str, ...], default: float = 0.0) -> np.ndarray:
    for name in names:
        if name in df.columns:
            return pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=np.float64)
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
    if fn == "mean":
        out = roll.mean()
    elif fn == "std":
        out = roll.std()
    elif fn == "max":
        out = roll.max()
    elif fn == "min":
        out = roll.min()
    else:
        raise ValueError(f"rolling fn inconnue: {fn}")
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


def _temporal_signals(df: pd.DataFrame, horizon_hours: int) -> Dict[str, np.ndarray]:
    n = len(df)
    close_raw = _col_any(df, ("close", "Close"), default=1.0)
    close_valid = np.isfinite(close_raw) & (close_raw > 0)
    close_fill = float(np.nanmedian(close_raw[close_valid])) if close_valid.any() else 1.0
    close = (
        pd.Series(close_raw)
        .where(close_valid)
        .ffill()
        .bfill()
        .fillna(close_fill)
        .to_numpy(dtype=np.float64)
    )
    close = np.maximum(close, _EPS)

    volume_raw = _col_any(df, ("volume", "Volume"), default=0.0)
    volume = np.maximum(_clean(volume_raw, default=0.0), 0.0)
    log_close = np.log(close)

    w = int(max(2, min(horizon_hours, max(n - 1, 2))))
    fast_w = max(2, w // 4)
    mid_w = max(3, w // 2)
    long_w = max(w + 1, min(max(n - 1, 2), w * 2))

    ret_1 = _logret(log_close, 1)
    ret_fast = _logret(log_close, fast_w)
    ret_mid = _logret(log_close, mid_w)
    ret_full = _logret(log_close, w)

    vol_fast = _rolling(ret_1, fast_w, "std")
    vol_full = _rolling(ret_1, w, "std")
    vol_long = _rolling(ret_1, long_w, "std")

    prior_close = _shift(close, 1, close[0] if n else 1.0)
    prior_high = _rolling(prior_close, w, "max")
    prior_low = _rolling(prior_close, w, "min")
    dist_high = close / np.maximum(prior_high, _EPS) - 1.0
    dist_low = close / np.maximum(prior_low, _EPS) - 1.0

    vol_mean = _rolling(volume, w, "mean")
    vol_z = _z(_ratio(volume, vol_mean, default=1.0))

    return {
        "ret_fast": ret_fast,
        "ret_mid": ret_mid,
        "ret_full": ret_full,
        "accel": ret_fast - ret_mid,
        "abs_ret_fast": np.abs(ret_fast),
        "vol_fast": vol_fast,
        "vol_full": vol_full,
        "vol_ratio_fast_full": _ratio(vol_fast, vol_full, default=1.0),
        "vol_ratio_full_long": _ratio(vol_full, vol_long, default=1.0),
        "dist_high": dist_high,
        "dist_low": dist_low,
        "drawdown_from_high": -np.minimum(dist_high, 0.0),
        "volume_z": vol_z,
        "ema_spread_20_50": _col(df, "ema_spread_20_50"),
        "ema_spread_50_200": _col(df, "ema_spread_50_200"),
        "dist_ema_20": _col(df, "dist_ema_20"),
        "dist_ema_50": _col(df, "dist_ema_50"),
        "dist_ema_200": _col(df, "dist_ema_200"),
        "rv_ratio_24_72": _col(df, "rv_ratio_24_72", 1.0),
        "rv_ratio_12_48": _col(df, "rv_ratio_12_48", 1.0),
        "boll_width_20": _col(df, "boll_width_20", 0.02),
        "boll_pos_20": _col(df, "boll_pos_20", 0.5),
        "boll_expansion_6": _col(df, "boll_expansion_6"),
        "momentum_accel_6": _col(df, "momentum_accel_6"),
        "trend_persistence_12": _col(df, "trend_persistence_12"),
        "breakout_strength_24": _col(df, "breakout_strength_24"),
        "above_vwap_4h": _col(df, "above_vwap_4h", 0.5),
        "dist_vwap_pct": _col(df, "dist_vwap_pct"),
        "taker_buy_ratio_base": _col(df, "taker_buy_ratio_base", 0.5),
        "delta_taker_pressure": _col(df, "delta_taker_pressure"),
        "vol_imbalance": _col(df, "vol_imbalance"),
        "buy_vol_ratio_6": _col(df, "buy_vol_ratio_6", 0.5),
        "liq_short_spike_12": _col(df, "liq_short_spike_12"),
        "liq_imbalance": _col(df, "liq_imbalance"),
        "rsi_14": _col(df, "rsi_14", 50.0),
    }


def _score_movement(sig: Dict[str, np.ndarray], movement_key: str) -> np.ndarray:
    ret_fast = sig["ret_fast"]
    ret_mid = sig["ret_mid"]
    ret_full = sig["ret_full"]
    trend = _z(ret_full) + 0.55 * _z(sig["ema_spread_50_200"]) + 0.35 * _z(sig["ema_spread_20_50"])
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
            + 0.35 * _z(ret_mid)
        )
    elif movement_key == "pullback_reclaim":
        pullback = _z(sig["drawdown_from_high"]) - 0.45 * _z(sig["boll_pos_20"] - 0.5)
        reclaim = 0.85 * _z(ret_fast) + 0.55 * _z(sig["dist_ema_20"]) + 0.35 * _z(55.0 - sig["rsi_14"])
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
    else:
        raw = np.zeros_like(ret_full)

    return np.nan_to_num(_sigmoid(raw), nan=0.0, posinf=1.0, neginf=0.0).astype(np.float32)


def build_specialist_scores(
    df: pd.DataFrame,
    specs: Tuple[SpecialistSpec, ...] = SPECIALIST_SPECS,
) -> pd.DataFrame:
    """
    Score causal de chaque spécialiste sur chaque barre.

    Le score ne regarde jamais le futur : il combine rendements passés,
    volatilité réalisée passée, range passé, VWAP/flow et événements déjà
    matérialisés dans les features.
    """
    if len(df) == 0:
        return pd.DataFrame(index=df.index, columns=[s.name for s in specs], dtype=np.float32)

    by_horizon: Dict[str, Dict[str, np.ndarray]] = {}
    scores: Dict[str, np.ndarray] = {}
    for spec in specs:
        if spec.horizon.key not in by_horizon:
            by_horizon[spec.horizon.key] = _temporal_signals(df, spec.horizon.hours)
        scores[spec.name] = _score_movement(by_horizon[spec.horizon.key], spec.movement.key)
    return pd.DataFrame(scores, index=df.index, dtype=np.float32)


def classify_context(df: pd.DataFrame) -> np.ndarray:
    """
    Assigne le contexte primaire à chaque barre.

    La fleet utilise un routage top-k pour la prédiction, mais le contexte
    primaire reste utile pour calibrer un seuil PnL par signature dominante.
    """
    n = len(df)
    if n == 0:
        return np.array([], dtype=object)

    score_df = build_specialist_scores(df)
    values = score_df.to_numpy(dtype=np.float32)
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
class TinySpecialist:
    """
    Modèle spécialiste entraîné sur UN contexte de marché.

    Utilise TOUTES les features (pas de sélection artificielle),
    avec une capacité adaptée à son horizon temporel.
    La spécialisation vient des DONNÉES d'entraînement, pas des features.
    """
    context_name: str
    features: List[str]          # toutes les FEATURES_LONG
    spec: Optional[SpecialistSpec] = None
    clf_:     Optional[Any] = field(default=None, repr=False)
    scaler_:  Optional[StandardScaler] = field(default=None, repr=False)
    val_auc_: float = 0.0
    n_train_: int = 0
    n_pos_:   int = 0
    score_threshold_: float = 0.0

    def _get_X(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        avail = [f for f in self.features if f in df.columns]
        X = df.loc[mask, avail].values.astype(np.float32)
        n_miss = len(self.features) - len(avail)
        if n_miss:
            X = np.hstack([X, np.zeros((X.shape[0], n_miss), dtype=np.float32)])
        return X

    def fit(
        self,
        df: pd.DataFrame,
        train_mask: np.ndarray,
        val_mask:   Optional[np.ndarray] = None,
        sample_weight: Optional[np.ndarray] = None,
    ) -> "TinySpecialist":
        X_tr = self._get_X(df, train_mask)
        y_tr = df.loc[train_mask, "y_long"].values.astype(np.int32)

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

        n_neg  = len(y_tr) - self.n_pos_
        spw    = min(n_neg / max(self.n_pos_, 1), 80.0)

        # Capacité adaptative : le général reste large, les 72 spécialistes sont
        # plus compacts pour garder une fleet entraînable fold après fold.
        if self.context_name == "general":
            max_iter, max_depth, min_leaf = 360, 4, 20
        else:
            assert self.spec is not None
            if self.spec.horizon.hours <= 168:
                max_iter, max_depth, min_leaf = 240, 4, 18
            else:
                max_iter, max_depth, min_leaf = 200, 3, 24

        self.clf_ = HistGradientBoostingClassifier(
            max_iter=max_iter, max_depth=max_depth, learning_rate=0.04,
            l2_regularization=1.0, min_samples_leaf=min_leaf,
            class_weight={0: 1.0, 1: spw}, random_state=42,
        )
        self.clf_.fit(Xsc, y_tr, sample_weight=sample_weight)

        # AUC sur val BTC (si fourni et compatible)
        if (val_mask is not None
                and len(val_mask) == len(df)
                and int(val_mask.sum()) > 10):
            X_val = self._get_X(df, val_mask)
            y_val = df.loc[val_mask, "y_long"].values.astype(np.int32)
            valid_v = y_val >= 0
            X_val, y_val = X_val[valid_v], y_val[valid_v]
            if len(X_val) > 10 and y_val.sum() >= 2:
                p = self.clf_.predict_proba(self.scaler_.transform(X_val))[:, 1]
                self.val_auc_ = float(roc_auc_score(y_val, p))

        return self

    def predict_proba(self, df: pd.DataFrame, mask: np.ndarray) -> np.ndarray:
        """Retourne P(y_long=1) pour les barres df[mask]."""
        if self.clf_ is None or self.scaler_ is None:
            return np.full(int(mask.sum()), 0.5, dtype=np.float32)
        X   = self._get_X(df, mask)
        Xsc = self.scaler_.transform(X)
        return self.clf_.predict_proba(Xsc)[:, 1].astype(np.float32)


# ─────────────────────────────────────────────────────────────────────────────
# Calibration PnL par contexte
# ─────────────────────────────────────────────────────────────────────────────

def calibrate_context_thresholds(
    fleet:      "TRMFleet",
    df_val_btc: pd.DataFrame,
    filter_p:   np.ndarray,          # p_filter pour chaque barre val
    filter_thr: float,
    ret_val:    np.ndarray,          # future_ret_4h sur val
    cost_pct:   float = 0.001,
    min_thr:    float = 0.54,        # plancher explicite — évite le flood
    max_thr:    float = 0.65,
    min_trades: int   = 5,
) -> Dict[str, float]:
    """
    Calibre UN seuil par contexte en maximisant l'expectancy nette.

    Plancher min_thr=0.54 : en dessous, les trades sont trop nombreux
    et trop bruités. L'objectif est PF ≥ 1.3, pas volume.
    """
    n       = len(df_val_btc)
    ones    = np.ones(n, dtype=bool)
    ctx_arr = classify_context(df_val_btc)
    p_fleet = fleet.predict(df_val_btc, ones)

    thresholds: Dict[str, float] = {}

    for ctx, spec in fleet.specialists.items():
        if spec.clf_ is None:
            thresholds[ctx] = min_thr
            continue

        filt_ok = filter_p >= filter_thr
        ctx_ok  = (ctx_arr == ctx) if ctx != "general" else np.ones(n, dtype=bool)
        sel     = filt_ok & ctx_ok

        p_sub  = p_fleet[sel]
        ret_sub = ret_val[sel]
        valid_sub = np.isfinite(ret_sub)
        p_sub, ret_sub = p_sub[valid_sub], ret_sub[valid_sub]

        if len(p_sub) < 10:
            thresholds[ctx] = min_thr
            continue

        best_thr, best_exp = min_thr, -np.inf
        for thr in np.arange(min_thr, max_thr + 0.001, 0.01):
            mask_ = p_sub >= thr
            if mask_.sum() < min_trades:
                continue
            exp = float((ret_sub[mask_] - cost_pct).mean())
            if exp > best_exp:
                best_exp, best_thr = exp, thr

        # Contrainte min_trades : abaisser si nécessaire
        if int((p_sub >= best_thr).sum()) < min_trades:
            for thr in np.arange(best_thr - 0.01, min_thr - 0.001, -0.01):
                if int((p_sub >= thr).sum()) >= min_trades:
                    best_thr = thr
                    break

        thresholds[ctx] = round(float(best_thr), 2)

    return thresholds


# ─────────────────────────────────────────────────────────────────────────────
# Flottée TRM
# ─────────────────────────────────────────────────────────────────────────────

class TRMFleet:
    """
    Flottée TRM multi-résolution.

    La fleet contient par défaut 72 TRM spécialisés + 1 général :
      9 horizons temporels × 8 archétypes de mouvement + général.

    En prédiction, on route chaque barre vers les top-k spécialistes dont la
    signature temporelle est la plus active, puis on mélange leur score avec le
    général. Cela évite le routage dur "un contexte = un modèle".
    """

    SPECIALIST_W = 0.72
    GENERAL_W    = 0.28

    def __init__(
        self,
        features: List[str],
        n_recursive_rounds: int = 2,
        max_specialists: Optional[int] = None,
        routing_top_k: int = 4,
        min_specialist_rows: int = 160,
    ):
        self.features           = features   # FEATURES_LONG
        self.n_recursive_rounds = n_recursive_rounds
        self.routing_top_k      = max(1, int(routing_top_k))
        self.min_specialist_rows = max(20, int(min_specialist_rows))
        self.specialist_specs   = SPECIALIST_SPECS[:max_specialists] if max_specialists else SPECIALIST_SPECS
        self.context_names      = [s.name for s in self.specialist_specs] + ["general"]
        self.specialists: Dict[str, TinySpecialist] = {}
        self.n_ctx_: Dict[str, int] = {}
        self._init_specialists()

    def _init_specialists(self) -> None:
        for spec in self.specialist_specs:
            self.specialists[spec.name] = TinySpecialist(
                context_name=spec.name,
                features=self.features,   # TOUTES les features
                spec=spec,
            )
        self.specialists["general"] = TinySpecialist(
            context_name="general",
            features=self.features,
        )

    def _select_score_tail(
        self,
        scores: np.ndarray,
        spec: SpecialistSpec,
    ) -> Tuple[np.ndarray, float]:
        valid_idx = np.where(np.isfinite(scores))[0]
        mask = np.zeros(len(scores), dtype=bool)
        if len(valid_idx) == 0:
            return mask, 0.0

        target = int(np.ceil((1.0 - spec.movement.train_quantile) * len(valid_idx)))
        min_rows = min(self.min_specialist_rows, max(20, len(valid_idx) // 5))
        target = min(len(valid_idx), max(min_rows, target))

        order = np.argsort(scores[valid_idx], kind="mergesort")
        chosen = valid_idx[order[-target:]]
        mask[chosen] = True
        thr = float(np.nanmin(scores[chosen])) if len(chosen) else 0.0
        return mask, thr

    def train(
        self,
        df:              pd.DataFrame,     # train combiné (multi-actif)
        train_mask:      np.ndarray,
        df_val_btc:      Optional[pd.DataFrame] = None,   # val BTC (index btc)
        val_mask_in_btc: Optional[np.ndarray]   = None,   # masque dans df_val_btc
    ) -> "TRMFleet":
        """
        Entraîne la flottée avec apprentissage récursif.

        df          : train combiné (BTC + altcoins) avec RangeIndex
        df_val_btc  : DataFrame BTC avec DatetimeIndex (pour AUC val)
        val_mask_in_btc : masque bool dans df_val_btc
        """
        t0 = time.time()
        train_idx    = np.where(train_mask)[0]
        n_train      = len(train_idx)
        weights_now  = np.ones(n_train, dtype=np.float64)
        score_df     = build_specialist_scores(df, self.specialist_specs)

        for rnd in range(self.n_recursive_rounds):
            for ctx, spec in self.specialists.items():
                if ctx == "general":
                    ctx_in_train = np.ones(n_train, dtype=bool)
                else:
                    score_local = score_df.loc[train_mask, ctx].to_numpy(dtype=np.float32)
                    assert spec.spec is not None
                    ctx_in_train, score_threshold = self._select_score_tail(score_local, spec.spec)
                    spec.score_threshold_ = score_threshold

                ctx_mask = train_mask.copy()
                ctx_mask[train_idx[~ctx_in_train]] = False
                if ctx_mask.sum() < 20:
                    continue

                ctx_weights = weights_now[ctx_in_train]
                if ctx != "general":
                    score_local = score_df.loc[train_mask, ctx].to_numpy(dtype=np.float64)
                    ctx_weights = ctx_weights * (1.0 + np.clip(score_local[ctx_in_train], 0.0, 1.0))

                spec.fit(df, ctx_mask, val_mask=None, sample_weight=ctx_weights)

            # Barres difficiles : haute incertitude de l'ensemble
            if rnd < self.n_recursive_rounds - 1:
                p_ens   = self._predict_ensemble(df, train_mask, None)
                uncertain = np.abs(p_ens - 0.5) < 0.12
                weights_now = np.where(uncertain, 3.0, 1.0).astype(np.float64)

        # ── AUC sur val BTC (mesure réelle) ───────────────────────────────────
        if df_val_btc is not None and val_mask_in_btc is not None:
            self._compute_val_aucs(df_val_btc, val_mask_in_btc)

        dt = time.time() - t0
        primary_ctx = classify_context(df.loc[train_mask])
        n_ctx = {n: int((primary_ctx == n).sum()) for n in self.context_names}
        self.n_ctx_ = n_ctx
        aucs  = {n: round(s.val_auc_, 3) for n, s in self.specialists.items() if s.clf_}
        trained = [n for n, s in self.specialists.items() if s.clf_ is not None]
        top_ctx = sorted(
            ((k, v) for k, v in n_ctx.items() if v > 0),
            key=lambda item: item[1],
            reverse=True,
        )[:10]
        auc_top = sorted(
            ((k, v) for k, v in aucs.items() if v > 0),
            key=lambda item: item[1],
            reverse=True,
        )[:10]

        print(f"   TRMFleet v3 : {len(trained)}/{len(self.specialists)} TRM entraînés  "
              f"top_k={self.routing_top_k}  rounds={self.n_recursive_rounds}  t={dt:.1f}s")
        print(f"   Contextes primaires top10 : " +
              "  ".join(f"{k}={v:,}" for k, v in top_ctx))
        print(f"   AUC val BTC top10 : " +
              "  ".join(f"{k}={v:.3f}" for k, v in auc_top))

        return self

    def _compute_val_aucs(
        self, df_val: pd.DataFrame, val_mask: np.ndarray
    ) -> None:
        """Calcule l'AUC val de chaque spécialiste sur BTC val."""
        df_sub = df_val.loc[val_mask]
        score_df = build_specialist_scores(df_sub, self.specialist_specs)
        y_val   = df_sub["y_long"].values.astype(np.int32)

        ones = np.ones(val_mask.sum(), dtype=bool)
        for ctx, spec in self.specialists.items():
            if spec.clf_ is None:
                continue
            if ctx == "general":
                ctx_sel = ones
            else:
                assert spec.spec is not None
                score_local = score_df[ctx].to_numpy(dtype=np.float32)
                ctx_sel, _ = self._select_score_tail(score_local, spec.spec)

            if ctx_sel.sum() < 10 or y_val[ctx_sel][y_val[ctx_sel] >= 0].sum() < 2:
                continue

            # Prédire sur les barres de ce contexte
            p_sub = spec.predict_proba(df_sub, ctx_sel)
            y_sub = y_val[ctx_sel]
            valid_sub = y_sub >= 0
            p_sub, y_sub = p_sub[valid_sub], y_sub[valid_sub]
            if len(p_sub) > 5 and y_sub.sum() >= 2:
                spec.val_auc_ = float(roc_auc_score(y_sub, p_sub))

    def _predict_ensemble(
        self, df: pd.DataFrame, mask: np.ndarray, all_contexts: Optional[np.ndarray]
    ) -> np.ndarray:
        """Prédictions vectorisées par contexte pour le calcul d'incertitude."""
        return self.predict(df, mask, verbose=False)

    def predict(
        self,
        df:      pd.DataFrame,
        mask:    np.ndarray,
        verbose: bool = False,
    ) -> np.ndarray:
        """
        Prédictions de la flottée — routage top-k multi-spécialistes.
        p_final = general_w × p_general + specialist_w × Σ(w_k × p_k)
        """
        df_sub  = df.loc[mask].copy().reset_index(drop=True)
        n       = len(df_sub)
        if n == 0:
            return np.array([], dtype=np.float32)
        ones    = np.ones(n, dtype=bool)

        p_general = self.specialists["general"].predict_proba(df_sub, ones)
        p_out     = p_general.copy()

        trained_names = [
            spec.name
            for spec in self.specialist_specs
            if spec.name in self.specialists and self.specialists[spec.name].clf_ is not None
        ]
        if not trained_names:
            return p_out.astype(np.float32)

        score_df = build_specialist_scores(
            df_sub,
            tuple(s for s in self.specialist_specs if s.name in trained_names),
        )
        score_values = score_df[trained_names].to_numpy(dtype=np.float32)
        k = min(self.routing_top_k, len(trained_names))
        top_idx = np.argpartition(score_values, -k, axis=1)[:, -k:]
        top_scores = np.take_along_axis(score_values, top_idx, axis=1)
        active_rows = top_scores.max(axis=1) >= PRIMARY_CONTEXT_SCORE_FLOOR
        if not active_rows.any():
            return p_out.astype(np.float32)

        raw_w = np.maximum(top_scores - 0.50, 0.0) ** 2 + 1e-5
        raw_w[~active_rows, :] = 0.0
        raw_w = raw_w / np.maximum(raw_w.sum(axis=1, keepdims=True), _EPS)

        p_mix = np.zeros(n, dtype=np.float32)
        ctx_counts: Dict[str, int] = {}
        for j, name in enumerate(trained_names):
            where = (top_idx == j) & active_rows[:, None]
            row_mask = where.any(axis=1)
            if not row_mask.any():
                continue
            spec = self.specialists[name]
            ctx_counts[name] = int(row_mask.sum())
            p_spec = spec.predict_proba(df_sub, row_mask)
            row_weights = np.zeros(n, dtype=np.float32)
            # Un spécialiste ne peut apparaître qu'une seule fois par ligne top-k.
            row_weights[row_mask] = raw_w[where]
            p_mix[row_mask] += row_weights[row_mask] * p_spec

        p_out[active_rows] = (
            self.GENERAL_W * p_general[active_rows]
            + self.SPECIALIST_W * p_mix[active_rows]
        )

        if verbose:
            top = sorted(ctx_counts.items(), key=lambda item: item[1], reverse=True)[:10]
            print(f"   TRM predict top10 : " +
                  " ".join(f"{k}={v:,}" for k, v in top))
        return p_out.astype(np.float32)

    def val_auc_summary(self) -> Dict[str, float]:
        return {n: round(s.val_auc_, 3) for n, s in self.specialists.items()}

    def to_fleet_report(self) -> Dict:
        """Retourne un dict sérialisable résumant l'état de chaque TRM."""
        specialists_out = {}
        for name, spec in self.specialists.items():
            sp_spec = spec.spec
            entry: Dict = {
                "trained":  spec.clf_ is not None,
                "val_auc":  round(spec.val_auc_, 3),
                "n_ctx":    self.n_ctx_.get(name, 0),
            }
            if sp_spec is not None:
                entry["horizon"]       = sp_spec.horizon.label
                entry["horizon_hours"] = sp_spec.horizon.hours
                entry["archetype"]     = sp_spec.movement.key
                entry["archetype_desc"]= sp_spec.movement.desc
                entry["train_quantile"]= sp_spec.movement.train_quantile
            else:
                entry["horizon"] = "all"
                entry["archetype"] = "general"
            specialists_out[name] = entry
        return {
            "n_total":    len(self.specialists),
            "n_trained":  sum(1 for s in self.specialists.values() if s.clf_ is not None),
            "n_active":   sum(1 for s in self.specialists.values() if s.val_auc_ > 0),
            "fleet_auc_mean": round(
                float(
                    sum(s.val_auc_ for s in self.specialists.values() if s.val_auc_ > 0)
                    / max(1, sum(1 for s in self.specialists.values() if s.val_auc_ > 0))
                ), 4
            ),
            "specialists": specialists_out,
        }
