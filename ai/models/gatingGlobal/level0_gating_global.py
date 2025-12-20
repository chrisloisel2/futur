# level0_gating_global.py
# Niveau 0 — Gating global (tradeable / wait) + features vers modèles aval.
# Compatible avec tes enregistrements JSON (BTC 1m typiquement).
#
# Principe:
# - Online quantile tracker (P² algorithm) pour thresholds causaux.
# - Gating: tradeable=1 si |R| >= thr_R et RV <= thr_RV et DD <= thr_DD (optionnel).
# - Construit les fenêtres [t-L+1..t] et index de fin t.
#
# Dépendances: numpy uniquement.
#
# Intégration:
# - Tu streams tes JSON (S3 / fichier) -> GlobalGating.feed(record)
# - Il renvoie (ready, sample) où sample contient:
#     X  : np.ndarray [L, F]
#     t  : int (end-time index)
#     y  : dict { "R": float, "RV": float, "DD": float, "tradeable": int }
# - Pour entraînement, tu appelles partial_fit_thresholds() uniquement sur train.

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
# P² QUANTILE ESTIMATOR (streaming, O(1))
# =========================
class P2Quantile:
    """
    P² algorithm for online quantile estimation.
    Works well for large streams without storing data.

    Reference: Jain & Chlamtac (1985).
    """

    def __init__(self, q: float):
        if not (0.0 < q < 1.0):
            raise ValueError("q must be in (0,1)")
        self.q = float(q)
        self.n = 0

        # Marker positions (n1..n5) and heights (x1..x5)
        self.np = np.zeros(5, dtype=np.float64)
        self.ni = np.zeros(5, dtype=np.float64)
        self.dn = np.zeros(5, dtype=np.float64)
        self.x = np.zeros(5, dtype=np.float64)

        self._init_buf: List[float] = []

    def update(self, v: float) -> None:
        v = float(v)
        self.n += 1

        if self.n <= 5:
            self._init_buf.append(v)
            if self.n == 5:
                self._init_buf.sort()
                self.x[:] = self._init_buf
                self.ni[:] = np.array([1, 2, 3, 4, 5], dtype=np.float64)
                self.np[:] = np.array([1, 1 + 2*self.q, 1 + 4*self.q, 3 + 2*self.q, 5], dtype=np.float64)
                self.dn[:] = np.array([0, self.q/2, self.q, (1+self.q)/2, 1], dtype=np.float64)
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
        self.ni[k+1:] += 1
        self.np += self.dn

        # Adjust heights for markers 2..4
        for i in (1, 2, 3):
            d = self.np[i] - self.ni[i]
            if (d >= 1 and self.ni[i+1] - self.ni[i] > 1) or (d <= -1 and self.ni[i-1] - self.ni[i] < -1):
                dsign = np.sign(d)
                # Parabolic prediction
                x_new = self._parabolic(i, dsign)
                if self.x[i-1] < x_new < self.x[i+1]:
                    self.x[i] = x_new
                else:
                    # Linear
                    self.x[i] = self._linear(i, dsign)
                self.ni[i] += dsign

    def _parabolic(self, i: int, d: float) -> float:
        n_im1, n_i, n_ip1 = self.ni[i-1], self.ni[i], self.ni[i+1]
        x_im1, x_i, x_ip1 = self.x[i-1], self.x[i], self.x[i+1]
        return x_i + d / (n_ip1 - n_im1) * (
            (n_i - n_im1 + d) * (x_ip1 - x_i) / (n_ip1 - n_i) +
            (n_ip1 - n_i - d) * (x_i - x_im1) / (n_i - n_im1)
        )

    def _linear(self, i: int, d: float) -> float:
        return self.x[i] + d * (self.x[i + int(d)] - self.x[i]) / (self.ni[i + int(d)] - self.ni[i])

    def value(self) -> Optional[float]:
        if self.n < 5:
            return None
        return float(self.x[2])  # marker 3 is the quantile estimate


# =========================
# LABEL HELPERS (R, RV, DD)
# =========================
def future_path_stats(fut_ret: np.ndarray) -> Tuple[float, float]:
    """
    Returns:
      R  : cumulative return (sum of log_ret)
      DD : max drawdown on cumulative path
    """
    path = np.cumsum(fut_ret.astype(np.float64))
    R = float(path[-1]) if path.size else 0.0
    if path.size == 0:
        return R, 0.0
    peak = np.maximum.accumulate(path)
    dd = peak - path
    DD = float(np.max(dd))
    return R, DD


def rms_vol(fut_rv: np.ndarray) -> float:
    if fut_rv.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(fut_rv.astype(np.float64)))))


# =========================
# CONFIG
# =========================
@dataclass(frozen=True)
class GatingConfig:
    lookback: int = 256
    horizon: int = 12
    feature_keys: Tuple[str, ...] = tuple(FEATURE_KEYS)

    # Tradeability thresholds (quantiles)
    q_R: float = 0.70     # thr_R from |R|
    q_RV: float = 0.70    # thr_RV from RV
    q_DD: float = 0.70    # thr_DD from DD (drawdown)
    use_dd: bool = True

    # Warmup: don't output labels until enough history
    warmup: int = 2048


# =========================
# GLOBAL GATING (Level 0)
# =========================
class GlobalGating:
    """
    Streaming:
    - feed(record) keeps a rolling buffer of features and targets.
    - For training, you also update threshold estimators from TRAIN ONLY:
        partial_fit_thresholds(R, RV, DD)
    - tradeable decision uses frozen thresholds.
    """

    def __init__(self, cfg: GatingConfig):
        self.cfg = cfg
        self.F = len(cfg.feature_keys)

        # Rolling buffers (store raw numeric arrays)
        self._X_buf: List[np.ndarray] = []
        self._ret_buf: List[float] = []
        self._rv_buf: List[float] = []
        self._t_buf: List[int] = []

        # Online quantile estimators (train phase)
        self.q_absR = P2Quantile(cfg.q_R)
        self.q_RV = P2Quantile(cfg.q_RV)
        self.q_DD = P2Quantile(cfg.q_DD)

        # Frozen thresholds (set after fitting on train)
        self.thr_absR: Optional[float] = None
        self.thr_RV: Optional[float] = None
        self.thr_DD: Optional[float] = None

    def record_to_vec(self, rec: Dict[str, Any]) -> Tuple[int, np.ndarray, float, float]:
        t = int(rec.get(TIME_KEY, 0))
        x = np.array([float(rec.get(k, 0.0) or 0.0) for k in self.cfg.feature_keys], dtype=np.float32)
        r = float(rec.get(RET_KEY, 0.0) or 0.0)
        rv = float(rec.get(RV_KEY, 0.0) or 0.0)
        return t, x, r, rv

    def feed(self, rec: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Push a new timestep.
        Returns:
          (ready, sample) where sample exists only if a full window+future is available.
        """
        t, x, r, rv = self.record_to_vec(rec)
        self._t_buf.append(t)
        self._X_buf.append(x)
        self._ret_buf.append(r)
        self._rv_buf.append(rv)

        # Need at least lookback+horizon
        L = self.cfg.lookback
        H = self.cfg.horizon
        T = len(self._X_buf)
        if T < L + H:
            return False, None

        # Build latest sample ending at index end_idx = T-H-1
        # so future horizon is available: [end_idx+1 .. end_idx+H]
        end_idx = T - H - 1
        start_idx = end_idx - (L - 1)
        if start_idx < 0:
            return False, None

        Xw = np.stack(self._X_buf[start_idx:end_idx + 1], axis=0)  # [L,F]
        fut_ret = np.array(self._ret_buf[end_idx + 1:end_idx + 1 + H], dtype=np.float32)  # [H]
        fut_rv = np.array(self._rv_buf[end_idx + 1:end_idx + 1 + H], dtype=np.float32)    # [H]
        t_end = int(self._t_buf[end_idx])

        R, DD = future_path_stats(fut_ret)
        RV = rms_vol(fut_rv)

        # If thresholds not frozen, output sample without gating decision.
        if self.thr_absR is None or self.thr_RV is None or (self.cfg.use_dd and self.thr_DD is None):
            sample = {
                "X": Xw,
                "t": t_end,
                "y": {"R": R, "RV": RV, "DD": DD, "tradeable": None},
            }
            return True, sample

        tradeable = self.is_tradeable(R=R, RV=RV, DD=DD)
        sample = {
            "X": Xw,
            "t": t_end,
            "y": {"R": R, "RV": RV, "DD": DD, "tradeable": int(tradeable)},
        }
        return True, sample

    # ---------- TRAIN: update thresholds causally ----------
    def partial_fit_thresholds(self, R: float, RV: float, DD: float) -> None:
        self.q_absR.update(abs(float(R)))
        self.q_RV.update(float(RV))
        if self.cfg.use_dd:
            self.q_DD.update(float(DD))

    def freeze_thresholds(self) -> Dict[str, float]:
        """
        Call once after scanning TRAIN set and calling partial_fit_thresholds().
        """
        thr_absR = self.q_absR.value()
        thr_RV = self.q_RV.value()
        thr_DD = self.q_DD.value() if self.cfg.use_dd else None

        if thr_absR is None or thr_RV is None or (self.cfg.use_dd and thr_DD is None):
            raise RuntimeError("Not enough samples to freeze thresholds (need >5 updates per quantile).")

        self.thr_absR = float(thr_absR)
        self.thr_RV = float(thr_RV)
        self.thr_DD = float(thr_DD) if thr_DD is not None else None

        out = {"thr_absR": self.thr_absR, "thr_RV": self.thr_RV}
        if self.cfg.use_dd:
            out["thr_DD"] = self.thr_DD
        return out

    def load_thresholds(self, d: Dict[str, float]) -> None:
        self.thr_absR = float(d["thr_absR"])
        self.thr_RV = float(d["thr_RV"])
        if self.cfg.use_dd:
            self.thr_DD = float(d["thr_DD"])

    # ---------- INFER ----------
    def is_tradeable(self, R: float, RV: float, DD: float) -> bool:
        if self.thr_absR is None or self.thr_RV is None or (self.cfg.use_dd and self.thr_DD is None):
            raise RuntimeError("Thresholds not frozen/loaded.")
        cond = (abs(R) >= self.thr_absR) and (RV <= self.thr_RV)
        if self.cfg.use_dd:
            cond = cond and (DD <= self.thr_DD)
        return bool(cond)


# =========================
# MINIMAL DEMO WITH YOUR RECORD
# =========================
if __name__ == "__main__":
    # Example single record (won't be ready, need lookback+horizon stream)
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

    cfg = GatingConfig(lookback=256, horizon=12, warmup=2048)
    gate = GlobalGating(cfg)

    ready, sample = gate.feed(rec)
    print("ready:", ready)
    print("sample:", sample)
