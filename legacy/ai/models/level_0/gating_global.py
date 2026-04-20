# level0_gating_global_fixed.py
# -----------------------------------------------------------------------------
# LEVEL 0 — Global Gating corrigé
#
# Objectif :
#   1) OFFLINE / TRAIN
#      - construire des labels futurs (tradeable + direction) à partir du futur
#      - calibrer des seuils de labels sur TRAIN ONLY
#      - calibrer des seuils de gating live à partir d'états causaux courants
#      - produire des diagnostics riches
#
#   2) ONLINE / LIVE
#      - utiliser UNIQUEMENT des métriques causales (passé + présent)
#      - décider tradeable / wait
#      - sortir un paquet diagnostique détaillé
#
# Correction majeure vs ancien fichier :
#   - plus aucune utilisation du futur dans la décision live
#   - séparation propre LABELS FUTURS vs GATE LIVE
#   - logs détaillés de calibration et de diagnostic
#   - garde-fous sur NaN/Inf / séries trop courtes / shifts de distribution
#
# Dépendances :
#   - numpy obligatoire
#   - pandas optionnel uniquement pour la démo CLI en bas
# -----------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple
import json
import math
import os
import statistics
import numpy as np


# =============================================================================
# CONFIG
# =============================================================================

FEATURE_KEYS: List[str] = [
    "Open", "High", "Low", "Close", "Volume",
    "Quote_Volume", "Trades", "Taker_Buy_Base", "Taker_Buy_Quote",
    "ret", "log_ret",
    "rv_5", "rv_15", "rv_30", "rv_60", "rv_120", "rv_240", "rv_720", "rv_1440",
    "rv_ann_5", "rv_ann_15", "rv_ann_30", "rv_ann_60", "rv_ann_120", "rv_ann_240", "rv_ann_720", "rv_ann_1440",
    "ema_20", "ema_50", "ema_100", "ema_200",
    "dist_ema_20", "dist_ema_50", "dist_ema_100", "dist_ema_200",
    "atr_14", "atr_pct_14", "rsi_14",
    "var_99_60", "cvar_99_60", "var_99_240", "cvar_99_240", "var_99_1440", "cvar_99_1440",
]

TIME_KEY = "datetime"
RET_KEY = "log_ret"
CLOSE_KEY = "Close"
HIGH_KEY = "High"
LOW_KEY = "Low"
VOLUME_KEY = "Volume"
ATR_PCT_KEY = "atr_pct_14"
RV_KEY = "rv_60"


@dataclass(frozen=True)
class GatingConfig:
    lookback: int = 256
    horizon: int = 12

    warmup: int = 512
    max_buffer: int = 4096

    # Quantiles pour les LABELS OFFLINE (futurs)
    q_label_absR: float = 0.65
    q_label_future_rv: float = 0.60
    q_label_future_dd: float = 0.75
    q_label_abs_score: float = 0.60

    # Quantiles pour le GATE LIVE (causal / courant)
    q_live_state_abs_mom: float = 0.45
    q_live_state_rv: float = 0.50
    q_live_state_atr_pct: float = 0.45
    q_live_state_range_pct: float = 0.45
    q_live_state_vol_ratio: float = 0.50

    # Direction labels
    use_trinary_label: bool = True
    flat_class_id: int = 1
    short_class_id: int = 0
    long_class_id: int = 2

    # Gating live
    require_atr_pct: bool = True
    require_range_pct: bool = False
    require_vol_ratio: bool = False

    # Diagnostics / protections
    eps: float = 1e-9
    clamp_rv_min: float = 1e-8
    clamp_rv_max: float = 10.0
    clamp_absR_max: float = 5.0
    clamp_dd_max: float = 5.0
    clamp_score_abs_max: float = 1e6

    # Logs
    verbose: bool = True
    diag_every: int = 0  # 0 = off


# =============================================================================
# UTILS
# =============================================================================

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return float(default)
        return x
    except Exception:
        return float(default)


def _clip_float(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _nanmean(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.nanmean(x))


def _nanstd(x: np.ndarray) -> float:
    if x.size <= 1:
        return 0.0
    return float(np.nanstd(x))


def _quantile(x: np.ndarray, q: float, default: float = 0.0) -> float:
    if x.size == 0:
        return float(default)
    return float(np.quantile(x, q))


def _pct(n: float) -> str:
    return f"{100.0 * float(n):.1f}%"


def _fmt(x: Optional[float], nd: int = 6) -> str:
    if x is None:
        return "None"
    return f"{float(x):.{nd}f}"


def _json_dump(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _print(verbose: bool, msg: str) -> None:
    if verbose:
        print(msg, flush=True)


# =============================================================================
# STREAMING QUANTILE (P²)
# =============================================================================

class P2Quantile:
    """
    Estimateur de quantile online O(1).
    Référence : Jain & Chlamtac (1985).
    """

    def __init__(self, q: float):
        if not (0.0 < q < 1.0):
            raise ValueError("q must be in (0,1)")
        self.q = float(q)
        self.n = 0

        self.np = np.zeros(5, dtype=np.float64)
        self.ni = np.zeros(5, dtype=np.float64)
        self.dn = np.zeros(5, dtype=np.float64)
        self.x = np.zeros(5, dtype=np.float64)
        self._init_buf: List[float] = []

    def update(self, v: float) -> None:
        v = float(v)
        if math.isnan(v) or math.isinf(v):
            return

        self.n += 1
        if self.n <= 5:
            self._init_buf.append(v)
            if self.n == 5:
                self._init_buf.sort()
                self.x[:] = self._init_buf
                self.ni[:] = np.array([1, 2, 3, 4, 5], dtype=np.float64)
                self.np[:] = np.array(
                    [1, 1 + 2 * self.q, 1 + 4 * self.q, 3 + 2 * self.q, 5],
                    dtype=np.float64,
                )
                self.dn[:] = np.array([0, self.q / 2, self.q, (1 + self.q) / 2, 1], dtype=np.float64)
            return

        if v < self.x[0]:
            self.x[0] = v
            k = 0
        elif v < self.x[1]:
            k = 0
        elif v < self.x[2]:
            k = 1
        elif v < self.x[3]:
            k = 2
        elif v <= self.x[4]:
            k = 3
        else:
            self.x[4] = v
            k = 3

        self.ni[k + 1:] += 1
        self.np += self.dn

        for i in (1, 2, 3):
            d = self.np[i] - self.ni[i]
            if (d >= 1 and (self.ni[i + 1] - self.ni[i]) > 1) or (d <= -1 and (self.ni[i - 1] - self.ni[i]) < -1):
                dsign = float(np.sign(d))
                x_new = self._parabolic(i, dsign)
                if self.x[i - 1] < x_new < self.x[i + 1]:
                    self.x[i] = x_new
                else:
                    self.x[i] = self._linear(i, dsign)
                self.ni[i] += dsign

    def _parabolic(self, i: int, d: float) -> float:
        n_im1, n_i, n_ip1 = self.ni[i - 1], self.ni[i], self.ni[i + 1]
        x_im1, x_i, x_ip1 = self.x[i - 1], self.x[i], self.x[i + 1]
        return x_i + d / (n_ip1 - n_im1) * (
            (n_i - n_im1 + d) * (x_ip1 - x_i) / (n_ip1 - n_i)
            + (n_ip1 - n_i - d) * (x_i - x_im1) / (n_i - n_im1)
        )

    def _linear(self, i: int, d: float) -> float:
        j = i + int(d)
        return self.x[i] + d * (self.x[j] - self.x[i]) / (self.ni[j] - self.ni[i])

    def value(self) -> Optional[float]:
        if self.n < 5:
            return None
        return float(self.x[2])


# =============================================================================
# FUTURE STATS
# =============================================================================

def future_path_stats(fut_ret: np.ndarray) -> Tuple[float, float]:
    """
    Retourne :
      R  = retour cumulé futur (somme log-ret)
      DD = max drawdown de la trajectoire cumulée future
    """
    if fut_ret.size == 0:
        return 0.0, 0.0
    path = np.cumsum(fut_ret.astype(np.float64))
    r_total = float(path[-1])
    peak = np.maximum.accumulate(path)
    dd = peak - path
    max_dd = float(np.max(dd)) if dd.size else 0.0
    return r_total, max_dd


def rms_vol(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    z = x.astype(np.float64)
    return float(np.sqrt(np.mean(z * z)))


# =============================================================================
# INTERNAL METRIC COMPUTATION
# =============================================================================

def _linear_slope(y: np.ndarray) -> float:
    if y.size < 2:
        return 0.0
    x = np.arange(y.size, dtype=np.float64)
    x = x - x.mean()
    yy = y.astype(np.float64) - float(np.mean(y))
    denom = float(np.sum(x * x))
    if denom <= 0:
        return 0.0
    return float(np.sum(x * yy) / denom)


def _window_close_to_close_logret(close: np.ndarray) -> float:
    if close.size < 2:
        return 0.0
    c0 = max(float(close[0]), 1e-12)
    c1 = max(float(close[-1]), 1e-12)
    return float(np.log(c1 / c0))


def _window_range_pct(high: np.ndarray, low: np.ndarray, close_last: float) -> float:
    if high.size == 0 or low.size == 0:
        return 0.0
    denom = max(abs(float(close_last)), 1e-12)
    return float((float(np.max(high)) - float(np.min(low))) / denom)


def _last_over_mean(x: np.ndarray, eps: float) -> float:
    if x.size == 0:
        return 0.0
    m = float(np.mean(x))
    return float(float(x[-1]) / (abs(m) + eps))


def _compute_live_state_from_buffers(
    cfg: GatingConfig,
    x_window: np.ndarray,
    ret_window: np.ndarray,
    close_window: np.ndarray,
    high_window: np.ndarray,
    low_window: np.ndarray,
    volume_window: np.ndarray,
) -> Dict[str, float]:
    """
    Ces métriques sont 100% causales.
    Elles servent au gating live.
    """
    close_last = float(close_window[-1]) if close_window.size else 0.0

    # Momentum passé sur sous-fenêtres
    abs_mom_12 = abs(float(np.sum(ret_window[-12:]))) if ret_window.size >= 12 else abs(float(np.sum(ret_window)))
    abs_mom_24 = abs(float(np.sum(ret_window[-24:]))) if ret_window.size >= 24 else abs(float(np.sum(ret_window)))
    mom_signed_12 = float(np.sum(ret_window[-12:])) if ret_window.size >= 12 else float(np.sum(ret_window))
    mom_signed_24 = float(np.sum(ret_window[-24:])) if ret_window.size >= 24 else float(np.sum(ret_window))

    # RV courante causale
    rv_now_24 = float(np.std(ret_window[-24:])) if ret_window.size >= 24 else float(np.std(ret_window)) if ret_window.size > 1 else 0.0
    rv_now_60 = float(np.std(ret_window[-60:])) if ret_window.size >= 60 else float(np.std(ret_window)) if ret_window.size > 1 else 0.0

    # ATR% / range / tendance / volume
    atr_pct_now = float(x_window[-1, FEATURE_KEYS.index(ATR_PCT_KEY)]) if ATR_PCT_KEY in FEATURE_KEYS else 0.0
    range_pct_24 = _window_range_pct(high_window[-24:], low_window[-24:], close_last) if high_window.size >= 24 else _window_range_pct(high_window, low_window, close_last)
    range_pct_64 = _window_range_pct(high_window[-64:], low_window[-64:], close_last) if high_window.size >= 64 else _window_range_pct(high_window, low_window, close_last)
    vol_ratio_24 = _last_over_mean(volume_window[-24:], cfg.eps) if volume_window.size >= 24 else _last_over_mean(volume_window, cfg.eps)

    # Pente / distance EMAs
    close_slope_24 = _linear_slope(close_window[-24:]) if close_window.size >= 24 else _linear_slope(close_window)
    close_slope_64 = _linear_slope(close_window[-64:]) if close_window.size >= 64 else _linear_slope(close_window)

    dist_ema_20 = float(x_window[-1, FEATURE_KEYS.index("dist_ema_20")]) if "dist_ema_20" in FEATURE_KEYS else 0.0
    dist_ema_50 = float(x_window[-1, FEATURE_KEYS.index("dist_ema_50")]) if "dist_ema_50" in FEATURE_KEYS else 0.0
    dist_ema_200 = float(x_window[-1, FEATURE_KEYS.index("dist_ema_200")]) if "dist_ema_200" in FEATURE_KEYS else 0.0

    rsi_14 = float(x_window[-1, FEATURE_KEYS.index("rsi_14")]) if "rsi_14" in FEATURE_KEYS else 50.0

    return {
        "state_abs_mom_12": abs_mom_12,
        "state_abs_mom_24": abs_mom_24,
        "state_mom_signed_12": mom_signed_12,
        "state_mom_signed_24": mom_signed_24,
        "state_rv_24": rv_now_24,
        "state_rv_60": rv_now_60,
        "state_atr_pct": atr_pct_now,
        "state_range_pct_24": range_pct_24,
        "state_range_pct_64": range_pct_64,
        "state_vol_ratio_24": vol_ratio_24,
        "state_close_slope_24": close_slope_24,
        "state_close_slope_64": close_slope_64,
        "state_dist_ema_20": dist_ema_20,
        "state_dist_ema_50": dist_ema_50,
        "state_dist_ema_200": dist_ema_200,
        "state_rsi_14": rsi_14,
        "state_close_to_close_logret": _window_close_to_close_logret(close_window[-24:] if close_window.size >= 24 else close_window),
    }


# =============================================================================
# THRESHOLD ARTIFACT
# =============================================================================

@dataclass
class ThresholdArtifact:
    # Labels offline
    thr_label_absR: float
    thr_label_future_rv: float
    thr_label_future_dd: float
    thr_label_abs_score: float

    # Gate live causal
    thr_live_abs_mom: float
    thr_live_rv: float
    thr_live_atr_pct: float
    thr_live_range_pct: float
    thr_live_vol_ratio: float

    # Stats
    n_total_windows: int
    n_train_windows: int
    n_tradeable_train: int
    tradeable_rate_train: float

    # Diagnostics
    label_distribution_train: Dict[str, float]
    gate_coverage_train: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "ThresholdArtifact":
        return ThresholdArtifact(**d)


# =============================================================================
# OFFLINE TRAINER
# =============================================================================

class Level0GatingTrainer:
    """
    Pipeline offline :
      - extrait fenêtres causales
      - calcule labels futurs
      - calibre seuils train-only
      - calibre seuils de gate live causaux à partir des fenêtres tradeable
      - renvoie diagnostics riches
    """

    def __init__(self, cfg: GatingConfig):
        self.cfg = cfg

    # -------------------------------------------------------------------------
    # Parsing
    # -------------------------------------------------------------------------
    def record_to_vec(self, rec: Dict[str, Any]) -> Tuple[int, np.ndarray, float, float, float, float, float, float]:
        t = int(_safe_float(rec.get(TIME_KEY, 0), 0.0))
        x = np.empty((len(FEATURE_KEYS),), dtype=np.float32)
        for i, k in enumerate(FEATURE_KEYS):
            x[i] = np.float32(_safe_float(rec.get(k, 0.0), 0.0))

        ret = _safe_float(rec.get(RET_KEY, 0.0), 0.0)
        close = _safe_float(rec.get(CLOSE_KEY, 0.0), 0.0)
        high = _safe_float(rec.get(HIGH_KEY, close), close)
        low = _safe_float(rec.get(LOW_KEY, close), close)
        volume = _safe_float(rec.get(VOLUME_KEY, 0.0), 0.0)
        atr_pct = _safe_float(rec.get(ATR_PCT_KEY, 0.0), 0.0)
        rv = _safe_float(rec.get(RV_KEY, 0.0), 0.0)
        return t, x, ret, close, high, low, volume, atr_pct, rv

    # -------------------------------------------------------------------------
    # Build windows + raw stats
    # -------------------------------------------------------------------------
    def build_dataset(self, records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        cfg = self.cfg

        t_buf: List[int] = []
        x_buf: List[np.ndarray] = []
        ret_buf: List[float] = []
        close_buf: List[float] = []
        high_buf: List[float] = []
        low_buf: List[float] = []
        vol_buf: List[float] = []
        atr_pct_buf: List[float] = []
        rv_buf: List[float] = []

        samples: List[Dict[str, Any]] = []

        n = 0
        for rec in records:
            n += 1
            t, x, ret, close, high, low, volume, atr_pct, rv = self.record_to_vec(rec)

            t_buf.append(t)
            x_buf.append(x)
            ret_buf.append(ret)
            close_buf.append(close)
            high_buf.append(high)
            low_buf.append(low)
            vol_buf.append(volume)
            atr_pct_buf.append(atr_pct)
            rv_buf.append(rv)

            if len(x_buf) > cfg.max_buffer:
                del t_buf[0]
                del x_buf[0]
                del ret_buf[0]
                del close_buf[0]
                del high_buf[0]
                del low_buf[0]
                del vol_buf[0]
                del atr_pct_buf[0]
                del rv_buf[0]

            T = len(x_buf)
            L = cfg.lookback
            H = cfg.horizon
            if T < L + H:
                continue

            end_idx = T - H - 1
            start_idx = end_idx - (L - 1)
            if start_idx < 0:
                continue

            x_window = np.stack(x_buf[start_idx:end_idx + 1], axis=0)
            ret_window = np.asarray(ret_buf[start_idx:end_idx + 1], dtype=np.float32)
            close_window = np.asarray(close_buf[start_idx:end_idx + 1], dtype=np.float32)
            high_window = np.asarray(high_buf[start_idx:end_idx + 1], dtype=np.float32)
            low_window = np.asarray(low_buf[start_idx:end_idx + 1], dtype=np.float32)
            volume_window = np.asarray(vol_buf[start_idx:end_idx + 1], dtype=np.float32)

            fut_ret = np.asarray(ret_buf[end_idx + 1:end_idx + 1 + H], dtype=np.float32)
            fut_rv = np.asarray(rv_buf[end_idx + 1:end_idx + 1 + H], dtype=np.float32)

            future_R, future_DD = future_path_stats(fut_ret)
            future_RV = rms_vol(fut_rv)
            future_RV = _clip_float(future_RV, cfg.clamp_rv_min, cfg.clamp_rv_max)
            future_score = float(future_R / (future_RV + cfg.eps))
            future_absR = _clip_float(abs(float(future_R)), 0.0, cfg.clamp_absR_max)
            future_abs_score = _clip_float(abs(float(future_score)), 0.0, cfg.clamp_score_abs_max)
            future_DD = _clip_float(float(future_DD), 0.0, cfg.clamp_dd_max)

            live_state = _compute_live_state_from_buffers(
                cfg=cfg,
                x_window=x_window,
                ret_window=ret_window,
                close_window=close_window,
                high_window=high_window,
                low_window=low_window,
                volume_window=volume_window,
            )

            samples.append(
                {
                    "t": int(t_buf[end_idx]),
                    "X": x_window.astype(np.float32),
                    "future_R": float(future_R),
                    "future_absR": float(future_absR),
                    "future_RV": float(future_RV),
                    "future_DD": float(future_DD),
                    "future_score": float(future_score),
                    "future_abs_score": float(future_abs_score),
                    **live_state,
                }
            )

            if cfg.diag_every and len(samples) % cfg.diag_every == 0:
                _print(cfg.verbose, f"[diag] build_dataset windows={len(samples):,}")

        _print(cfg.verbose, f"[OK] build_dataset windows={len(samples):,} from input_records={n:,}")
        return {"samples": samples, "n_records": n}

    # -------------------------------------------------------------------------
    # Fit thresholds
    # -------------------------------------------------------------------------
    def fit(self, records: Iterable[Dict[str, Any]], train_frac: float = 0.8, out_dir: Optional[str] = None) -> Dict[str, Any]:
        cfg = self.cfg
        ds = self.build_dataset(records)
        samples = ds["samples"]
        n_total = len(samples)
        if n_total < 1000:
            raise RuntimeError(f"Pas assez de fenêtres: {n_total}. Il en faut au moins ~1000 pour calibrer correctement.")

        n_train = max(1, int(n_total * train_frac))
        train_samples = samples[:n_train]
        val_samples = samples[n_train:]

        _print(cfg.verbose, "")
        _print(cfg.verbose, "======================================================================")
        _print(cfg.verbose, "LEVEL 0 — FIT THRESHOLDS")
        _print(cfg.verbose, "======================================================================")
        _print(cfg.verbose, f"  Total windows : {n_total:,}")
        _print(cfg.verbose, f"  Train windows : {n_train:,}")
        _print(cfg.verbose, f"  Val windows   : {len(val_samples):,}")
        _print(cfg.verbose, f"  lookback      : {cfg.lookback}")
        _print(cfg.verbose, f"  horizon       : {cfg.horizon}")

        arr_future_absR = np.asarray([s["future_absR"] for s in train_samples], dtype=np.float64)
        arr_future_rv = np.asarray([s["future_RV"] for s in train_samples], dtype=np.float64)
        arr_future_dd = np.asarray([s["future_DD"] for s in train_samples], dtype=np.float64)
        arr_future_abs_score = np.asarray([s["future_abs_score"] for s in train_samples], dtype=np.float64)

        thr_label_absR = _quantile(arr_future_absR, cfg.q_label_absR)
        thr_label_future_rv = _quantile(arr_future_rv, cfg.q_label_future_rv)
        thr_label_future_dd = _quantile(arr_future_dd, cfg.q_label_future_dd)
        thr_label_abs_score = _quantile(arr_future_abs_score, cfg.q_label_abs_score)

        _print(cfg.verbose, "")
        _print(cfg.verbose, "LABEL THRESHOLDS (TRAIN ONLY)")
        _print(cfg.verbose, "")
        _print(cfg.verbose, "RAW TRAIN STATS")
        _print(cfg.verbose, f"  future_absR mean={np.mean(arr_future_absR):.8f}  q50={np.quantile(arr_future_absR, 0.50):.8f}  q90={np.quantile(arr_future_absR, 0.90):.8f}")
        _print(cfg.verbose, f"  future_rv   mean={np.mean(arr_future_rv):.8f}   q50={np.quantile(arr_future_rv, 0.50):.8f}   q90={np.quantile(arr_future_rv, 0.90):.8f}")
        _print(cfg.verbose, f"  future_dd   mean={np.mean(arr_future_dd):.8f}   q50={np.quantile(arr_future_dd, 0.50):.8f}   q90={np.quantile(arr_future_dd, 0.90):.8f}")
        _print(cfg.verbose, f"  future_abs_score mean={np.mean(arr_future_abs_score):.8f}  q50={np.quantile(arr_future_abs_score, 0.50):.8f}  q90={np.quantile(arr_future_abs_score, 0.90):.8f}")
        _print(cfg.verbose, f"  zeros future_absR      = {np.mean(arr_future_absR == 0):.2%}")
        _print(cfg.verbose, f"  zeros future_rv        = {np.mean(arr_future_rv == 0):.2%}")
        _print(cfg.verbose, f"  zeros future_dd        = {np.mean(arr_future_dd == 0):.2%}")
        _print(cfg.verbose, f"  zeros future_abs_score = {np.mean(arr_future_abs_score == 0):.2%}")
        _print(cfg.verbose, f"  thr_label_absR       = {_fmt(thr_label_absR)}")
        _print(cfg.verbose, f"  thr_label_future_rv  = {_fmt(thr_label_future_rv)}")
        _print(cfg.verbose, f"  thr_label_future_dd  = {_fmt(thr_label_future_dd)}")
        _print(cfg.verbose, f"  thr_label_abs_score  = {_fmt(thr_label_abs_score)}")

        # Label assignment on train
        for s in train_samples:
            s["label_tradeable"] = int(
                (s["future_absR"] >= thr_label_absR)
                and (s["future_RV"] >= thr_label_future_rv)
                and (s["future_DD"] <= thr_label_future_dd)
            )

            if not cfg.use_trinary_label:
                s["label_dir"] = 1 if s["future_score"] > 0.0 else 0
            else:
                if abs(s["future_score"]) < thr_label_abs_score:
                    s["label_dir"] = cfg.flat_class_id
                else:
                    s["label_dir"] = cfg.long_class_id if s["future_score"] > 0 else cfg.short_class_id

        tradeable_train = [s for s in train_samples if s["label_tradeable"] == 1]
        if len(tradeable_train) < 100:
            raise RuntimeError(
                f"Trop peu de fenêtres tradeable sur TRAIN ({len(tradeable_train)}). "
                "Les seuils de labels sont trop stricts ou les données trop calmes."
            )

        # Live gate thresholds are calibrated on the CURRENT state of tradeable train windows.
        arr_state_abs_mom = np.asarray([s["state_abs_mom_12"] for s in tradeable_train], dtype=np.float64)
        arr_state_rv = np.asarray([s["state_rv_60"] for s in tradeable_train], dtype=np.float64)
        arr_state_atr_pct = np.asarray([s["state_atr_pct"] for s in tradeable_train], dtype=np.float64)
        arr_state_range_pct = np.asarray([s["state_range_pct_24"] for s in tradeable_train], dtype=np.float64)
        arr_state_vol_ratio = np.asarray([s["state_vol_ratio_24"] for s in tradeable_train], dtype=np.float64)

        thr_live_abs_mom = _quantile(arr_state_abs_mom, cfg.q_live_state_abs_mom)
        thr_live_rv = _quantile(arr_state_rv, cfg.q_live_state_rv)
        thr_live_atr_pct = _quantile(arr_state_atr_pct, cfg.q_live_state_atr_pct)
        thr_live_range_pct = _quantile(arr_state_range_pct, cfg.q_live_state_range_pct)
        thr_live_vol_ratio = _quantile(arr_state_vol_ratio, cfg.q_live_state_vol_ratio)

        artifact = ThresholdArtifact(
            thr_label_absR=float(thr_label_absR),
            thr_label_future_rv=float(thr_label_future_rv),
            thr_label_future_dd=float(thr_label_future_dd),
            thr_label_abs_score=float(thr_label_abs_score),
            thr_live_abs_mom=float(thr_live_abs_mom),
            thr_live_rv=float(thr_live_rv),
            thr_live_atr_pct=float(thr_live_atr_pct),
            thr_live_range_pct=float(thr_live_range_pct),
            thr_live_vol_ratio=float(thr_live_vol_ratio),
            n_total_windows=n_total,
            n_train_windows=n_train,
            n_tradeable_train=len(tradeable_train),
            tradeable_rate_train=float(len(tradeable_train) / max(1, len(train_samples))),
            label_distribution_train=self._label_distribution(train_samples),
            gate_coverage_train=0.0,
        )

        _print(cfg.verbose, "")
        _print(cfg.verbose, "LIVE GATE THRESHOLDS (CAUSAL, CALIBRATED ON TRADEABLE TRAIN WINDOWS)")
        _print(cfg.verbose, f"  thr_live_abs_mom   = {_fmt(artifact.thr_live_abs_mom)}")
        _print(cfg.verbose, f"  thr_live_rv        = {_fmt(artifact.thr_live_rv)}")
        _print(cfg.verbose, f"  thr_live_atr_pct   = {_fmt(artifact.thr_live_atr_pct)}")
        _print(cfg.verbose, f"  thr_live_range_pct = {_fmt(artifact.thr_live_range_pct)}")
        _print(cfg.verbose, f"  thr_live_vol_ratio = {_fmt(artifact.thr_live_vol_ratio)}")

        # Coverage diagnostics on train/val with the live gate
        live_gate = Level0LiveGate(cfg=cfg, artifact=artifact)

        train_cov = self._gate_coverage(train_samples, live_gate)
        val_cov = self._gate_coverage(val_samples, live_gate)

        artifact.gate_coverage_train = float(train_cov)

        _print(cfg.verbose, "")
        _print(cfg.verbose, "COVERAGE DIAGNOSTICS")
        _print(cfg.verbose, f"  tradeable_rate_train(label) = {_pct(artifact.tradeable_rate_train)}")
        _print(cfg.verbose, f"  live_gate_coverage_train    = {_pct(train_cov)}")
        _print(cfg.verbose, f"  live_gate_coverage_val      = {_pct(val_cov)}")

        # Label distribution diagnostics
        self._print_label_distribution("TRAIN", train_samples)
        if val_samples:
            self._print_shift_report(train_samples, val_samples)

        report = {
            "artifact": artifact.to_dict(),
            "train_label_distribution": self._label_distribution(train_samples),
            "val_summary": self._basic_summary(val_samples),
        }

        if out_dir:
            _ensure_dir(out_dir)
            _json_dump(os.path.join(out_dir, "level0_thresholds.json"), artifact.to_dict())
            _json_dump(os.path.join(out_dir, "level0_fit_report.json"), report)
            _print(cfg.verbose, f"[OK] artifacts saved -> {out_dir}")

        return report

    # -------------------------------------------------------------------------
    # Diagnostics helpers
    # -------------------------------------------------------------------------
    def _label_distribution(self, samples: List[Dict[str, Any]]) -> Dict[str, float]:
        n = len(samples)
        if n == 0:
            return {}
        trade_rate = float(sum(int(s.get("label_tradeable", 0)) for s in samples) / n)

        dirs = [int(s["label_dir"]) for s in samples if "label_dir" in s]
        dist = {"tradeable_rate": trade_rate}
        if dirs:
            unique, counts = np.unique(np.asarray(dirs, dtype=np.int32), return_counts=True)
            for u, c in zip(unique.tolist(), counts.tolist()):
                dist[f"dir_{u}"] = float(c / len(dirs))
        return dist

    def _basic_summary(self, samples: List[Dict[str, Any]]) -> Dict[str, float]:
        if not samples:
            return {}
        return {
            "future_absR_mean": _nanmean(np.asarray([s["future_absR"] for s in samples])),
            "future_rv_mean": _nanmean(np.asarray([s["future_RV"] for s in samples])),
            "future_dd_mean": _nanmean(np.asarray([s["future_DD"] for s in samples])),
            "state_abs_mom_mean": _nanmean(np.asarray([s["state_abs_mom_12"] for s in samples])),
            "state_rv_mean": _nanmean(np.asarray([s["state_rv_60"] for s in samples])),
        }

    def _gate_coverage(self, samples: List[Dict[str, Any]], gate: "Level0LiveGate") -> float:
      if not samples:
          return 0.0
      positives = 0
      for s in samples:
          out = gate.decide_from_state({
              "state_abs_mom_12": s["state_abs_mom_12"],
              "state_abs_mom_24": s["state_abs_mom_24"],
              "state_mom_signed_12": s["state_mom_signed_12"],
              "state_mom_signed_24": s["state_mom_signed_24"],
              "state_rv_24": s["state_rv_24"],
              "state_rv_60": s["state_rv_60"],
              "state_atr_pct": s["state_atr_pct"],
              "state_range_pct_24": s["state_range_pct_24"],
              "state_range_pct_64": s["state_range_pct_64"],
              "state_vol_ratio_24": s["state_vol_ratio_24"],
              "state_close_slope_24": s["state_close_slope_24"],
              "state_close_slope_64": s["state_close_slope_64"],
              "state_dist_ema_20": s["state_dist_ema_20"],
              "state_dist_ema_50": s["state_dist_ema_50"],
              "state_dist_ema_200": s["state_dist_ema_200"],
              "state_rsi_14": s["state_rsi_14"],
              "state_close_to_close_logret": s["state_close_to_close_logret"],
          })
          positives += int(out["tradeable"])
      return float(positives / len(samples))

    def _print_label_distribution(self, name: str, samples: List[Dict[str, Any]]) -> None:
        cfg = self.cfg
        dist = self._label_distribution(samples)
        _print(cfg.verbose, "")
        _print(cfg.verbose, f"LABEL DISTRIBUTION — {name}")
        _print(cfg.verbose, f"  tradeable_rate = {_pct(dist.get('tradeable_rate', 0.0))}")
        for k in sorted(dist.keys()):
            if k.startswith("dir_"):
                _print(cfg.verbose, f"  {k:>8} = {_pct(dist[k])}")

    def _print_shift_report(self, train_samples: List[Dict[str, Any]], val_samples: List[Dict[str, Any]]) -> None:
        cfg = self.cfg
        _print(cfg.verbose, "")
        _print(cfg.verbose, "SHIFT REPORT — TRAIN vs VAL")

        keys = [
            "future_absR", "future_RV", "future_DD",
            "state_abs_mom_12", "state_rv_60", "state_atr_pct",
            "state_range_pct_24", "state_vol_ratio_24",
        ]
        for k in keys:
            a = np.asarray([s[k] for s in train_samples], dtype=np.float64)
            b = np.asarray([s[k] for s in val_samples], dtype=np.float64)
            ma, mb = float(np.mean(a)), float(np.mean(b))
            sa, sb = float(np.std(a)), float(np.std(b))
            drift = (mb - ma) / (abs(ma) + cfg.eps)
            warn = "  !!" if abs(drift) > 0.20 else ""
            _print(cfg.verbose, f"  {k:>18}: train_mean={ma:.6f}  val_mean={mb:.6f}  drift={drift:+.2%}{warn}")


# =============================================================================
# LIVE GATE
# =============================================================================

class Level0LiveGate:
    """
    Décision live 100% causale.
    Aucune dépendance au futur.
    """

    def __init__(self, cfg: GatingConfig, artifact: ThresholdArtifact):
        self.cfg = cfg
        self.artifact = artifact

        self._t_buf: List[int] = []
        self._x_buf: List[np.ndarray] = []
        self._ret_buf: List[float] = []
        self._close_buf: List[float] = []
        self._high_buf: List[float] = []
        self._low_buf: List[float] = []
        self._volume_buf: List[float] = []

    def record_to_vec(self, rec: Dict[str, Any]) -> Tuple[int, np.ndarray, float, float, float, float, float]:
        t = int(_safe_float(rec.get(TIME_KEY, 0), 0.0))
        x = np.empty((len(FEATURE_KEYS),), dtype=np.float32)
        for i, k in enumerate(FEATURE_KEYS):
            x[i] = np.float32(_safe_float(rec.get(k, 0.0), 0.0))
        ret = _safe_float(rec.get(RET_KEY, 0.0), 0.0)
        close = _safe_float(rec.get(CLOSE_KEY, 0.0), 0.0)
        high = _safe_float(rec.get(HIGH_KEY, close), close)
        low = _safe_float(rec.get(LOW_KEY, close), close)
        volume = _safe_float(rec.get(VOLUME_KEY, 0.0), 0.0)
        return t, x, ret, close, high, low, volume

    def feed(self, rec: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
        """
        Live streaming.
        Retourne:
          ready=False tant que le lookback causal n'est pas rempli
          ready=True avec décision et diagnostics détaillés ensuite
        """
        t, x, ret, close, high, low, volume = self.record_to_vec(rec)

        self._t_buf.append(t)
        self._x_buf.append(x)
        self._ret_buf.append(ret)
        self._close_buf.append(close)
        self._high_buf.append(high)
        self._low_buf.append(low)
        self._volume_buf.append(volume)

        if len(self._x_buf) > self.cfg.max_buffer:
            del self._t_buf[0]
            del self._x_buf[0]
            del self._ret_buf[0]
            del self._close_buf[0]
            del self._high_buf[0]
            del self._low_buf[0]
            del self._volume_buf[0]

        if len(self._x_buf) < self.cfg.lookback:
            return False, {
                "ready": False,
                "reason": f"need_lookback_{self.cfg.lookback}",
                "have": len(self._x_buf),
            }

        x_window = np.stack(self._x_buf[-self.cfg.lookback:], axis=0)
        ret_window = np.asarray(self._ret_buf[-self.cfg.lookback:], dtype=np.float32)
        close_window = np.asarray(self._close_buf[-self.cfg.lookback:], dtype=np.float32)
        high_window = np.asarray(self._high_buf[-self.cfg.lookback:], dtype=np.float32)
        low_window = np.asarray(self._low_buf[-self.cfg.lookback:], dtype=np.float32)
        volume_window = np.asarray(self._volume_buf[-self.cfg.lookback:], dtype=np.float32)

        state = _compute_live_state_from_buffers(
            cfg=self.cfg,
            x_window=x_window,
            ret_window=ret_window,
            close_window=close_window,
            high_window=high_window,
            low_window=low_window,
            volume_window=volume_window,
        )

        out = self.decide_from_state(state)
        out["ready"] = True
        out["t"] = int(self._t_buf[-1])
        out["X"] = x_window.astype(np.float32)
        return True, out

    def decide_from_state(self, state: Dict[str, float]) -> Dict[str, Any]:
        a = self.artifact
        cfg = self.cfg

        cond_abs_mom = float(state["state_abs_mom_12"]) >= float(a.thr_live_abs_mom)
        cond_rv = float(state["state_rv_60"]) >= float(a.thr_live_rv)
        cond_atr = float(state["state_atr_pct"]) >= float(a.thr_live_atr_pct)
        cond_range = float(state["state_range_pct_24"]) >= float(a.thr_live_range_pct)
        cond_vol_ratio = float(state["state_vol_ratio_24"]) >= float(a.thr_live_vol_ratio)

        tradeable = cond_abs_mom and cond_rv
        if cfg.require_atr_pct:
            tradeable = tradeable and cond_atr
        if cfg.require_range_pct:
            tradeable = tradeable and cond_range
        if cfg.require_vol_ratio:
            tradeable = tradeable and cond_vol_ratio

        # Direction live pure heuristique causale
        signed_mom = float(state["state_mom_signed_12"])
        if abs(signed_mom) < max(a.thr_live_abs_mom, cfg.eps):
            direction = cfg.flat_class_id
        else:
            direction = cfg.long_class_id if signed_mom > 0 else cfg.short_class_id

        # Confidence heuristique simple, bornée [0,1]
        score_components = [
            float(state["state_abs_mom_12"]) / max(a.thr_live_abs_mom, cfg.eps),
            float(state["state_rv_60"]) / max(a.thr_live_rv, cfg.eps),
        ]
        if cfg.require_atr_pct:
            score_components.append(float(state["state_atr_pct"]) / max(a.thr_live_atr_pct, cfg.eps))
        conf = float(np.mean(score_components))
        conf = float(np.clip((conf - 0.5) / 1.5, 0.0, 1.0))

        return {
            "tradeable": int(bool(tradeable)),
            "direction": int(direction),
            "confidence": conf,
            "state": {k: float(v) for k, v in state.items()},
            "checks": {
                "abs_mom": bool(cond_abs_mom),
                "rv": bool(cond_rv),
                "atr_pct": bool(cond_atr),
                "range_pct": bool(cond_range),
                "vol_ratio": bool(cond_vol_ratio),
            },
            "thresholds": {
                "thr_live_abs_mom": float(a.thr_live_abs_mom),
                "thr_live_rv": float(a.thr_live_rv),
                "thr_live_atr_pct": float(a.thr_live_atr_pct),
                "thr_live_range_pct": float(a.thr_live_range_pct),
                "thr_live_vol_ratio": float(a.thr_live_vol_ratio),
            },
        }


# =============================================================================
# HIGH LEVEL HELPERS
# =============================================================================

def load_threshold_artifact(path: str) -> ThresholdArtifact:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return ThresholdArtifact.from_dict(d)


def save_threshold_artifact(path: str, artifact: ThresholdArtifact) -> None:
    _json_dump(path, artifact.to_dict())


# =============================================================================
# CSV HELPERS (OPTIONAL)
# =============================================================================

def records_from_pandas_dataframe(df: "pd.DataFrame") -> List[Dict[str, Any]]:
    records = df.to_dict(orient="records")
    return records


def load_csv_records(csv_path: str) -> List[Dict[str, Any]]:
    import pandas as pd

    df = pd.read_csv(csv_path, low_memory=False)
    if "Open time" in df.columns and TIME_KEY not in df.columns:
        df = df.rename(columns={"Open time": TIME_KEY})
    if "Quote asset volume" in df.columns and "Quote_Volume" not in df.columns:
        df = df.rename(columns={"Quote asset volume": "Quote_Volume"})

    # Types safe
    if TIME_KEY in df.columns:
        try:
            df[TIME_KEY] = pd.to_datetime(df[TIME_KEY], utc=True)
            df[TIME_KEY] = (df[TIME_KEY].astype("int64") // 10**6).astype("int64")
        except Exception:
            df[TIME_KEY] = pd.to_numeric(df[TIME_KEY], errors="coerce").fillna(0).astype("int64")

    return records_from_pandas_dataframe(df)


# =============================================================================
# DEMO CLI
# =============================================================================

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Level 0 gating fixed — fit offline thresholds and test live gating.")
    ap.add_argument("--csv", type=str, default=None, help="CSV source")
    ap.add_argument("--out", type=str, default="runs/level0_fixed", help="Output directory")
    ap.add_argument("--lookback", type=int, default=256)
    ap.add_argument("--horizon", type=int, default=12)
    ap.add_argument("--train_frac", type=float, default=0.80)
    ap.add_argument("--demo_live", action="store_true", help="After fit, replay live gating on last 200 records")
    args = ap.parse_args()

    cfg = GatingConfig(
        lookback=args.lookback,
        horizon=args.horizon,
        verbose=True,
    )

    if not args.csv:
        raise SystemExit("Utilise --csv <path>")

    _ensure_dir(args.out)

    print("")
    print("======================================================================")
    print("LEVEL 0 GATING FIXED — START")
    print("======================================================================")
    print(f"CSV       : {args.csv}")
    print(f"OUT       : {args.out}")
    print(f"LOOKBACK  : {cfg.lookback}")
    print(f"HORIZON   : {cfg.horizon}")

    records = load_csv_records(args.csv)
    print(f"Loaded records: {len(records):,}")

    trainer = Level0GatingTrainer(cfg)
    report = trainer.fit(records, train_frac=args.train_frac, out_dir=args.out)

    artifact = ThresholdArtifact.from_dict(report["artifact"])
    print("")
    print("FITTED ARTIFACT")
    print(json.dumps(artifact.to_dict(), indent=2, ensure_ascii=False))

    if args.demo_live:
        print("")
        print("======================================================================")
        print("LIVE REPLAY DEMO")
        print("======================================================================")
        gate = Level0LiveGate(cfg=cfg, artifact=artifact)

        tail = records[-200:] if len(records) > 200 else records
        n_ready = 0
        n_tradeable = 0

        for i, rec in enumerate(tail, start=1):
            ready, out = gate.feed(rec)
            if not ready:
                continue
            n_ready += 1
            n_tradeable += int(out["tradeable"])

            if i % 20 == 0:
                print(
                    f"[live] step={i:>4} "
                    f"tradeable={out['tradeable']} "
                    f"direction={out['direction']} "
                    f"conf={out['confidence']:.3f} "
                    f"abs_mom={out['state']['state_abs_mom_12']:.6f} "
                    f"rv={out['state']['state_rv_60']:.6f} "
                    f"atr_pct={out['state']['state_atr_pct']:.6f}"
                )

        cov = n_tradeable / max(1, n_ready)
        print("")
        print("LIVE REPLAY SUMMARY")
        print(f"  ready_samples = {n_ready}")
        print(f"  tradeable     = {n_tradeable}")
        print(f"  coverage      = {cov:.2%}")

    print("")
    print("======================================================================")
    print("DONE")
    print("======================================================================")
