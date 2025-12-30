# level0_gating_global.py
# Niveau 0 — Gating global (tradeable / wait) + fenêtres features vers modèles aval.
# Compatible JSON stream (BTC 1m typiquement).
#
# Améliorations (par rapport à ta version):
# - Gating "bon sens" : tradeable si |R| élevé ET (RV élevé) ET (DD contrôlé) + filtres optionnels.
#   (Ton ancienne condition RV <= thr_RV favorisait la faible vol, contraire à l'intuition "tradable".)
# - Ajout label 3-classes (short/flat/long) basé sur score normalisé (R/RV) avec zone neutre causale.
# - Ajout d'un head logique "trade/no-trade" via tradeable, cohérent avec la logique ROI.
# - Quantiles causaux séparés: absR, RV, DD, absScore (|R|/RV), score_pos, score_neg (optionnel).
# - Warmup: pas de sortie avant suffisamment d'historique; purge mémoire (ring buffer) pour CPU/RAM.
# - Robustesse NaN/inf, clamp epsilon, types float32.
#
# Dépendances: numpy uniquement.

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Any
import numpy as np


# =========================
# FEATURE KEYS (doit matcher tes JSON)
# =========================
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

RET_KEY = "log_ret"
RV_KEY = "rv_60"
TIME_KEY = "datetime"


# =========================
# UTILS
# =========================
def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        if np.isnan(x) or np.isinf(x):
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


# =========================
# P² QUANTILE ESTIMATOR (streaming, O(1))
# =========================
class P2Quantile:
    """
    P² algorithm for online quantile estimation.
    Reference: Jain & Chlamtac (1985).
    """

    def __init__(self, q: float):
        if not (0.0 < q < 1.0):
            raise ValueError("q must be in (0,1)")
        self.q = float(q)
        self.n = 0

        self.np = np.zeros(5, dtype=np.float64)  # desired positions
        self.ni = np.zeros(5, dtype=np.float64)  # actual positions
        self.dn = np.zeros(5, dtype=np.float64)  # increments
        self.x = np.zeros(5, dtype=np.float64)   # marker heights

        self._init_buf: List[float] = []

    def update(self, v: float) -> None:
        v = float(v)
        if np.isnan(v) or np.isinf(v):
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

        # Find k such that x[k] <= v < x[k+1]
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

        # Increment positions
        self.ni[k + 1 :] += 1
        self.np += self.dn

        # Adjust heights for markers 2..4
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
        return float(self.x[2])  # marker 3 is quantile estimate


# =========================
# FUTURE STATS (R, RV, DD)
# =========================
def future_path_stats(fut_ret: np.ndarray) -> Tuple[float, float]:
    """
    Returns:
      R  : cumulative return (sum of log_ret)
      DD : max drawdown on cumulative path
    """
    if fut_ret.size == 0:
        return 0.0, 0.0
    path = np.cumsum(fut_ret.astype(np.float64))
    R = float(path[-1])
    peak = np.maximum.accumulate(path)
    dd = peak - path
    DD = float(np.max(dd)) if dd.size else 0.0
    return R, DD


def rms_vol(fut_rv: np.ndarray) -> float:
    if fut_rv.size == 0:
        return 0.0
    z = fut_rv.astype(np.float64)
    return float(np.sqrt(np.mean(z * z)))


# =========================
# CONFIG
# =========================
@dataclass(frozen=True)
class GatingConfig:
    lookback: int = 256
    horizon: int = 12
    feature_keys: Tuple[str, ...] = tuple(FEATURE_KEYS)

    # Online quantiles (train only)
    # Gate: absR high, RV high, DD low
    q_absR: float = 0.70
    q_RV_hi: float = 0.70
    q_DD_lo: float = 0.70
    use_dd: bool = True

    # Labeling (3 classes) using normalized score = R / (RV + eps)
    # - "flat" zone around 0 is determined by quantile of |score|
    use_trinary_label: bool = True
    q_absScore: float = 0.70  # threshold on |score| for leaving "flat"

    # Extra safety filters (optional)
    # If set, require ATR% >= thr_atr_pct (frozen from train quantile)
    use_atr_filter: bool = False
    q_atr_pct_hi: float = 0.60
    atr_pct_key: str = "atr_pct_14"

    # Warmup / memory
    warmup: int = 2048
    max_buffer: int = 4096  # ring buffer cap (>= lookback + horizon + warmup margin)

    # Numeric stability
    eps: float = 1e-12
    clamp_rv_min: float = 1e-8
    clamp_rv_max: float = 1.0  # defensive
    clamp_absR_max: float = 0.5  # defensive (log-ret sums over short H shouldn't exceed)
    clamp_absScore_max: float = 1e3


# =========================
# GLOBAL GATING (Level 0)
# =========================
class GlobalGating:
    """
    Streaming:
    - feed(record) keeps a rolling buffer of features and targets.
    - Train phase: partial_fit_thresholds(...) on TRAIN ONLY
    - freeze_thresholds() once => causal thresholds fixed
    - Inference: tradeable + y_dir (optional 3-class)
    """

    def __init__(self, cfg: GatingConfig):
        self.cfg = cfg
        self.F = len(cfg.feature_keys)

        # Rolling buffers
        self._X_buf: List[np.ndarray] = []
        self._ret_buf: List[float] = []
        self._rv_buf: List[float] = []
        self._t_buf: List[int] = []
        self._atr_pct_buf: List[float] = []

        # Quantile estimators (train only)
        self.q_absR = P2Quantile(cfg.q_absR)
        self.q_RV_hi = P2Quantile(cfg.q_RV_hi)
        self.q_DD_lo = P2Quantile(cfg.q_DD_lo)
        self.q_absScore = P2Quantile(cfg.q_absScore)

        self.q_atr_pct_hi = P2Quantile(cfg.q_atr_pct_hi) if cfg.use_atr_filter else None

        # Frozen thresholds
        self.thr_absR: Optional[float] = None
        self.thr_RV_hi: Optional[float] = None
        self.thr_DD_lo: Optional[float] = None
        self.thr_absScore: Optional[float] = None
        self.thr_atr_pct: Optional[float] = None

        # For training pipelines: count produced samples
        self._produced = 0

    # ---------- parsing ----------
    def record_to_vec(self, rec: Dict[str, Any]) -> Tuple[int, np.ndarray, float, float, float]:
        t = int(_safe_float(rec.get(TIME_KEY, 0), 0.0))
        x = np.empty((self.F,), dtype=np.float32)
        for i, k in enumerate(self.cfg.feature_keys):
            x[i] = np.float32(_safe_float(rec.get(k, 0.0), 0.0))
        r = _safe_float(rec.get(RET_KEY, 0.0), 0.0)
        rv = _safe_float(rec.get(RV_KEY, 0.0), 0.0)
        atr_pct = _safe_float(rec.get(self.cfg.atr_pct_key, 0.0), 0.0)
        return t, x, r, rv, atr_pct

    def _trim_buffers_if_needed(self) -> None:
        maxb = int(self.cfg.max_buffer)
        if maxb <= 0:
            return
        n = len(self._X_buf)
        if n <= maxb:
            return
        drop = n - maxb
        # Drop oldest
        del self._X_buf[:drop]
        del self._ret_buf[:drop]
        del self._rv_buf[:drop]
        del self._t_buf[:drop]
        del self._atr_pct_buf[:drop]

    # ---------- streaming ----------
    def feed(self, rec: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Push one timestep. Returns (ready, sample).
        sample:
          X: [L,F]
          t: end timestamp
          y: dict with:
             R, RV, DD
             score = R/(RV+eps)
             tradeable: int or None
             y_dir: {0:short,1:flat,2:long} or None
        """
        t, x, r, rv, atr_pct = self.record_to_vec(rec)

        self._t_buf.append(t)
        self._X_buf.append(x)
        self._ret_buf.append(float(r))
        self._rv_buf.append(float(rv))
        self._atr_pct_buf.append(float(atr_pct))

        self._trim_buffers_if_needed()

        L = self.cfg.lookback
        H = self.cfg.horizon
        T = len(self._X_buf)

        # Need full window + future
        if T < L + H:
            return False, None

        # end_idx so that future [end_idx+1 .. end_idx+H] exists
        end_idx = T - H - 1
        start_idx = end_idx - (L - 1)
        if start_idx < 0:
            return False, None

        # Warmup: avoid outputting too early (stabilise indicators)
        if self._produced < self.cfg.warmup:
            # Still produce samples if you want, but mark tradeable/label as None.
            pass

        Xw = np.stack(self._X_buf[start_idx : end_idx + 1], axis=0)  # [L,F]
        fut_ret = np.asarray(self._ret_buf[end_idx + 1 : end_idx + 1 + H], dtype=np.float32)
        fut_rv = np.asarray(self._rv_buf[end_idx + 1 : end_idx + 1 + H], dtype=np.float32)

        t_end = int(self._t_buf[end_idx])

        R, DD = future_path_stats(fut_ret)
        RV = rms_vol(fut_rv)

        # Defensive clamps
        RV = _clip_float(RV, self.cfg.clamp_rv_min, self.cfg.clamp_rv_max)
        absR = _clip_float(abs(R), 0.0, self.cfg.clamp_absR_max)

        score = float(R / (RV + self.cfg.eps))
        absScore = _clip_float(abs(score), 0.0, self.cfg.clamp_absScore_max)

        # Default outputs
        tradeable_out: Optional[int] = None
        y_dir_out: Optional[int] = None

        thresholds_ready = (
            (self.thr_absR is not None)
            and (self.thr_RV_hi is not None)
            and (self.thr_absScore is not None if self.cfg.use_trinary_label else True)
            and ((self.thr_DD_lo is not None) if self.cfg.use_dd else True)
            and ((self.thr_atr_pct is not None) if self.cfg.use_atr_filter else True)
        )

        # If not frozen or during warmup, return sample with None decisions
        if (not thresholds_ready) or (self._produced < self.cfg.warmup):
            sample = {
                "X": Xw,
                "t": t_end,
                "y": {
                    "R": float(R),
                    "RV": float(RV),
                    "DD": float(DD),
                    "score": float(score),
                    "tradeable": None,
                    "y_dir": None,
                },
            }
            self._produced += 1
            return True, sample

        tradeable = self.is_tradeable(R=float(R), RV=float(RV), DD=float(DD), atr_pct=float(self._atr_pct_buf[end_idx]))
        tradeable_out = int(tradeable)

        if self.cfg.use_trinary_label:
            y_dir_out = int(self.trinary_direction(score=float(score)))
        else:
            # Fallback: binaire direction (0 short, 1 long)
            y_dir_out = int(1 if R > 0 else 0)

        sample = {
            "X": Xw,
            "t": t_end,
            "y": {
                "R": float(R),
                "RV": float(RV),
                "DD": float(DD),
                "score": float(score),
                "tradeable": tradeable_out,
                "y_dir": y_dir_out,
            },
        }
        self._produced += 1
        return True, sample

    # ---------- TRAIN: update thresholds causally (TRAIN ONLY) ----------
    def partial_fit_thresholds(self, R: float, RV: float, DD: float, atr_pct: Optional[float] = None) -> None:
        """
        Update quantile estimators using TRAIN-only labels/stats.
        Feed:
          absR, RV, DD, absScore
        """
        RV = _clip_float(float(RV), self.cfg.clamp_rv_min, self.cfg.clamp_rv_max)
        R = float(R)
        DD = float(DD)

        absR = _clip_float(abs(R), 0.0, self.cfg.clamp_absR_max)
        score = float(R / (RV + self.cfg.eps))
        absScore = _clip_float(abs(score), 0.0, self.cfg.clamp_absScore_max)

        self.q_absR.update(absR)
        self.q_RV_hi.update(RV)
        if self.cfg.use_dd:
            self.q_DD_lo.update(_clip_float(DD, 0.0, 10.0))  # DD is in log-return units

        if self.cfg.use_trinary_label:
            self.q_absScore.update(absScore)

        if self.cfg.use_atr_filter and self.q_atr_pct_hi is not None and atr_pct is not None:
            self.q_atr_pct_hi.update(_clip_float(float(atr_pct), 0.0, 1.0))

    def freeze_thresholds(self) -> Dict[str, float]:
        """
        Call once after scanning TRAIN set and calling partial_fit_thresholds().
        """
        thr_absR = self.q_absR.value()
        thr_RV_hi = self.q_RV_hi.value()
        thr_DD_lo = self.q_DD_lo.value() if self.cfg.use_dd else None
        thr_absScore = self.q_absScore.value() if self.cfg.use_trinary_label else None

        if thr_absR is None or thr_RV_hi is None:
            raise RuntimeError("Not enough samples to freeze thresholds (need >5 updates per quantile).")
        if self.cfg.use_dd and thr_DD_lo is None:
            raise RuntimeError("Not enough samples to freeze DD threshold.")
        if self.cfg.use_trinary_label and thr_absScore is None:
            raise RuntimeError("Not enough samples to freeze absScore threshold.")

        # Interpretations:
        # - absR threshold: require absR >= thr_absR
        # - RV threshold: require RV >= thr_RV_hi (high vol to be tradable)
        # - DD threshold: require DD <= thr_DD_lo (avoid nasty future path) [optional]
        # - absScore threshold: |R|/RV must be >= thr_absScore to be directional; else "flat"
        self.thr_absR = float(thr_absR)
        self.thr_RV_hi = float(thr_RV_hi)
        self.thr_DD_lo = float(thr_DD_lo) if thr_DD_lo is not None else None
        self.thr_absScore = float(thr_absScore) if thr_absScore is not None else None

        if self.cfg.use_atr_filter and self.q_atr_pct_hi is not None:
            thr_atr_pct = self.q_atr_pct_hi.value()
            if thr_atr_pct is None:
                raise RuntimeError("Not enough samples to freeze ATR% threshold.")
            self.thr_atr_pct = float(thr_atr_pct)

        out: Dict[str, float] = {"thr_absR": self.thr_absR, "thr_RV_hi": self.thr_RV_hi}
        if self.cfg.use_dd and self.thr_DD_lo is not None:
            out["thr_DD_lo"] = self.thr_DD_lo
        if self.cfg.use_trinary_label and self.thr_absScore is not None:
            out["thr_absScore"] = self.thr_absScore
        if self.cfg.use_atr_filter and self.thr_atr_pct is not None:
            out["thr_atr_pct"] = self.thr_atr_pct
        return out

    def load_thresholds(self, d: Dict[str, float]) -> None:
        self.thr_absR = float(d["thr_absR"])
        self.thr_RV_hi = float(d["thr_RV_hi"])
        if self.cfg.use_dd:
            self.thr_DD_lo = float(d["thr_DD_lo"])
        if self.cfg.use_trinary_label:
            self.thr_absScore = float(d["thr_absScore"])
        if self.cfg.use_atr_filter:
            self.thr_atr_pct = float(d["thr_atr_pct"])

    # ---------- INFER ----------
    def is_tradeable(self, R: float, RV: float, DD: float, atr_pct: float = 0.0) -> bool:
        if self.thr_absR is None or self.thr_RV_hi is None:
            raise RuntimeError("Thresholds not frozen/loaded.")
        if self.cfg.use_dd and self.thr_DD_lo is None:
            raise RuntimeError("DD threshold not frozen/loaded.")
        if self.cfg.use_atr_filter and self.thr_atr_pct is None:
            raise RuntimeError("ATR% threshold not frozen/loaded.")

        RV = _clip_float(float(RV), self.cfg.clamp_rv_min, self.cfg.clamp_rv_max)
        absR = _clip_float(abs(float(R)), 0.0, self.cfg.clamp_absR_max)
        DD = _clip_float(float(DD), 0.0, 10.0)

        # Core gating: require move and volatility
        cond = (absR >= float(self.thr_absR)) and (RV >= float(self.thr_RV_hi))

        # Optional: avoid pathological future path (drawdown too high)
        if self.cfg.use_dd:
            cond = cond and (DD <= float(self.thr_DD_lo))

        # Optional: ATR% floor
        if self.cfg.use_atr_filter:
            cond = cond and (float(atr_pct) >= float(self.thr_atr_pct))

        return bool(cond)

    def trinary_direction(self, score: float) -> int:
        """
        Returns:
          0 = short
          1 = flat
          2 = long
        Uses |score| threshold from train quantile.
        """
        if self.thr_absScore is None:
            raise RuntimeError("absScore threshold not frozen/loaded.")
        s = float(score)
        if abs(s) < float(self.thr_absScore):
            return 1
        return 2 if s > 0 else 0


# =========================
# MINIMAL DEMO
# =========================
if __name__ == "__main__":
    rec = {
        "datetime": 1704078600000,
        "Open_Time": 1704078600000,
        "Open": 42538.61,
        "High": 42538.64,
        "Low": 42538.6,
        "Close": 42538.64,
        "Volume": 3.44335,
        "close_time": 1704078659999,
        "Quote_Volume": 146475.3872145,
        "Trades": 306,
        "Taker_Buy_Base": 2.30459,
        "Taker_Buy_Quote": 98034.1058557,
        "ret": 7.052416615138668e-7,
        "log_ret": 7.052414128310831e-7,
        "rv_5": 0.0003420550298734311,
        "rv_ann_5": 0.24798399908759597,
        "rv_15": 0.000312416882366486,
        "rv_ann_15": 0.22649685315368026,
        "rv_30": 0.0003826290540110869,
        "rv_ann_30": 0.2773994670269378,
        "rv_60": 0.0003359173641448399,
        "rv_ann_60": 0.24353429725744857,
        "rv_120": 0.0003976061984770537,
        "rv_ann_120": 0.2882576385350645,
        "rv_240": 0.0004197695861187946,
        "rv_ann_240": 0.30432571244340967,
        "rv_720": 0.0004801998643540658,
        "rv_ann_720": 0.3481366222502433,
        "rv_1440": 0.000454598174539738,
        "rv_ann_1440": 0.3295758385485494,
        "ema_20": 42560.31010972928,
        "dist_ema_20": -0.0005091624020927604,
        "ema_50": 42563.89594215096,
        "dist_ema_50": -0.0005933653767334486,
        "ema_100": 42555.514975020196,
        "dist_ema_100": -0.000396540261117798,
        "ema_200": 42526.49419251812,
        "dist_ema_200": 0.00028560566095328524,
        "atr_14": 11.860318273327435,
        "atr_pct_14": 0.00027881282225589334,
        "rsi_14": 41.13274527987002,
        "var_99_60": -0.0006843160444092133,
        "cvar_99_60": -0.0008061215456587334,
        "var_99_240": -0.0008772486891516284,
        "cvar_99_240": -0.0010366318779165458,
        "var_99_1440": -0.0011734623340209926,
        "cvar_99_1440": -0.0016550692181500923,
    }

    cfg = GatingConfig(
        lookback=256,
        horizon=12,
        warmup=2048,
        max_buffer=4096,
        use_trinary_label=True,
        use_dd=True,
        use_atr_filter=False,
    )
    gate = GlobalGating(cfg)

    ready, sample = gate.feed(rec)
    print("ready:", ready)
    print("sample:", sample)
