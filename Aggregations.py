from __future__ import annotations

import math
import time
import logging
from dataclasses import dataclass
from typing import Sequence, Optional, Dict, Tuple

import numpy as np
import pandas as pd
import awswrangler as wr

# ============================================================
# Logging (JSON-ish, crash-visible)
# ============================================================

def setup_logger(level=logging.INFO) -> logging.Logger:
    logger = logging.getLogger("trm_feature_factory")
    logger.setLevel(level)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(h)
    return logger

def log_event(logger: logging.Logger, event: str, **kwargs):
    payload = {"event": event, "ts": time.time(), **kwargs}
    logger.info(str(payload).replace("'", '"'))

# ============================================================
# Config
# ============================================================

@dataclass(frozen=True)
class AthenaConfig:
    region: str
    database: str
    table: str
    workgroup: str
    s3_output: str
    ctas: bool = False


@dataclass(frozen=True)
class DatasetSpec:
    symbol: str
    quote: str
    interval: str
    years: Sequence[int]


@dataclass(frozen=True)
class FeatureSpec:
    minutes_per_year: int = 365 * 24 * 60
    windows: Sequence[int] = (5, 15, 30, 60, 120, 240, 720, 1440)
    sma_ema_windows: Sequence[int] = (20, 50, 100, 200)
    rsi_window: int = 14
    atr_window: int = 14
    var_windows: Sequence[int] = (60, 240, 1440)
    var_alpha: float = 0.01

    # extra engineered features
    ema_slope_lags: Sequence[int] = (5, 20, 60)
    vol_ratio_pairs: Sequence[Tuple[int, int]] = ((60, 240), (240, 1440))

    # exp vol proxy weights (must sum to 1.0)
    exp_vol_triplet: Tuple[int, int, int] = (60, 240, 1440)
    exp_vol_weights: Tuple[float, float, float] = (0.5, 0.3, 0.2)


@dataclass(frozen=True)
class LabelSpec:
    # horizon in minutes (2 days = 2880, 3 days = 4320)
    horizon_min: int = 2880

    # triple barrier multipliers (up/down)
    u: float = 1.0
    d: float = 1.0

    # WAIT controls (optional hard floor/ceiling to avoid insane thresholds)
    sigma_floor: float = 1e-6
    sigma_cap: float = 0.10

    # label column names
    label_policy_col: str = "label_policy"      # 0 BUY, 1 SELL, 2 WAIT
    label_tradeable_col: str = "label_tradeable"  # 0/1


# ============================================================
# Core Utils
# ============================================================

EPS = 1e-12

def safe_div_num(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return a / (b + EPS)

def wilder_ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False).mean()

def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)

def ensure_float32(df: pd.DataFrame, cols: Sequence[str]) -> None:
    for c in cols:
        df[c] = df[c].astype("float32", copy=False)

# ============================================================
# Athena Reader (optimized projection + filter pushdown)
# ============================================================

def read_market_from_athena(cfg: AthenaConfig, ds: DatasetSpec, logger: logging.Logger) -> pd.DataFrame:
    years_sql = ",".join(map(str, ds.years))

    # only select what you need (saves time & memory)
    sql = f"""
    SELECT
        open_time,
        close_time,
        open,
        high,
        low,
        close,
        volume,
        quote_volume,
        trades,
        taker_buy_base,
        taker_buy_quote
    FROM {cfg.database}.{cfg.table}
    WHERE interval='{ds.interval}'
      AND quote='{ds.quote}'
      AND symbol='{ds.symbol}'
      AND year IN ({years_sql})
    ORDER BY open_time
    """

    log_event(logger, "athena_query_start", symbol=ds.symbol, interval=ds.interval, years=list(ds.years))
    t0 = time.time()

    df = wr.athena.read_sql_query(
        sql=sql,
        database=cfg.database,
        workgroup=cfg.workgroup,
        s3_output=cfg.s3_output,
        ctas_approach=cfg.ctas,
    )

    log_event(logger, "athena_query_done", rows=int(len(df)), seconds=float(time.time() - t0))

    # standardize column names to match your training schema (snake_case with exact keys)
    df = df.rename(columns={
        "open_time": "Open_Time",
        "close_time": "close_time",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "quote_volume": "Quote_Volume",
        "trades": "Trades",
        "taker_buy_base": "Taker_Buy_Base",
        "taker_buy_quote": "Taker_Buy_Quote",
    })

    # enforce dtypes early
    ensure_float32(df, ["Open", "High", "Low", "Close", "Volume", "Quote_Volume", "Taker_Buy_Base", "Taker_Buy_Quote"])
    df["Trades"] = df["Trades"].astype("int32", copy=False)
    df["Open_Time"] = df["Open_Time"].astype("int64", copy=False)
    df["close_time"] = df["close_time"].astype("int64", copy=False)

    return df


# ============================================================
# Feature Factory (optimized + aligned for TRM)
# ============================================================

def compute_trm_kpi(df: pd.DataFrame, spec: FeatureSpec, logger: logging.Logger) -> pd.DataFrame:
    t0 = time.time()
    log_event(logger, "feature_compute_start", rows=int(len(df)))

    df = df.copy()

    # datetime index (UTC) + sort
    df["datetime"] = pd.to_datetime(df["Open_Time"], unit="ms", utc=True)
    df = df.sort_values("datetime", kind="mergesort")
    df = df.set_index("datetime", drop=False)

    price = df["Close"].astype("float32", copy=False)

    blocks = []

    # ================= Returns =================
    # pct_change uses float64 internally; keep final cast
    ret = price.pct_change().astype("float32")
    log_ret = np.log((price / (price.shift(1) + EPS)).astype("float32")).astype("float32")

    blocks.append(pd.DataFrame({
        "ret": ret,
        "log_ret": log_ret,
    }, index=df.index))

    # ================= Volatility (RV std of log_ret) =================
    vol_feats: Dict[str, pd.Series] = {}
    for n in spec.windows:
        rv = log_ret.rolling(n, min_periods=n).std().astype("float32")
        vol_feats[f"rv_{n}"] = rv
        vol_feats[f"rv_ann_{n}"] = (rv * math.sqrt(spec.minutes_per_year)).astype("float32")

    blocks.append(pd.DataFrame(vol_feats, index=df.index))

    # ================= Moving averages + distances =================
    ma_feats: Dict[str, pd.Series] = {}
    ema_series: Dict[int, pd.Series] = {}

    for w in spec.sma_ema_windows:
        ema = price.ewm(span=w, adjust=False).mean().astype("float32")
        ema_series[w] = ema
        ma_feats[f"ema_{w}"] = ema
        ma_feats[f"dist_ema_{w}"] = ((price - ema) / (ema + EPS)).astype("float32")

    blocks.append(pd.DataFrame(ma_feats, index=df.index))

    # ================= EMA slopes (direction structure) =================
    slope_feats: Dict[str, pd.Series] = {}
    for w in spec.sma_ema_windows:
        ema = ema_series[w]
        for lag in spec.ema_slope_lags:
            slope_feats[f"ema_{w}_slope_{lag}"] = (ema.diff(lag) / float(lag)).astype("float32")

    blocks.append(pd.DataFrame(slope_feats, index=df.index))

    # ================= ATR + ATR% =================
    tr = true_range(df["High"].astype("float32", copy=False),
                    df["Low"].astype("float32", copy=False),
                    price)
    atr = wilder_ema(tr, spec.atr_window).astype("float32")

    blocks.append(pd.DataFrame({
        f"atr_{spec.atr_window}": atr,
        f"atr_pct_{spec.atr_window}": (atr / (price + EPS)).astype("float32"),
    }, index=df.index))

    # ================= RSI + RSI slope =================
    delta = price.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)

    rs = (wilder_ema(up, spec.rsi_window) / (wilder_ema(down, spec.rsi_window) + EPS)).astype("float32")
    rsi = (100 - (100 / (1 + rs))).astype("float32")

    blocks.append(pd.DataFrame({
        f"rsi_{spec.rsi_window}": rsi,
        "rsi_slope_5": rsi.diff(5).astype("float32"),
    }, index=df.index))

    # ================= Risk (VaR / CVaR) =================
    # Heavy block. Keep but optimize by working on numpy arrays inside rolling apply only where needed.
    risk_feats: Dict[str, pd.Series] = {}
    for n in spec.var_windows:
        r = ret.rolling(n, min_periods=n)
        var = r.quantile(spec.var_alpha).astype("float32")

        # CVaR with apply is expensive; use rolling apply but with minimal python overhead
        def _cvar(x: np.ndarray) -> float:
            x = x[~np.isnan(x)]
            if x.size == 0:
                return np.nan
            q = np.quantile(x, spec.var_alpha)
            tail = x[x <= q]
            return float(np.mean(tail)) if tail.size else np.nan

        cvar = r.apply(_cvar, raw=True).astype("float32")

        p = int((1 - spec.var_alpha) * 100)
        risk_feats[f"var_{p}_{n}"] = var
        risk_feats[f"cvar_{p}_{n}"] = cvar

    blocks.append(pd.DataFrame(risk_feats, index=df.index))

    # ================= Expected Volatility Proxy (actionable) =================
    a, b, c = spec.exp_vol_triplet
    wa, wb, wc = spec.exp_vol_weights
    exp_vol = (wa * vol_feats[f"rv_{a}"] + wb * vol_feats[f"rv_{b}"] + wc * vol_feats[f"rv_{c}"]).astype("float32")
    blocks.append(pd.DataFrame({
        "exp_vol": exp_vol,
        "exp_vol_ann": (exp_vol * math.sqrt(spec.minutes_per_year)).astype("float32"),
    }, index=df.index))

    # ================= Vol ratios (compression / expansion) =================
    vr_feats: Dict[str, pd.Series] = {}
    for n1, n2 in spec.vol_ratio_pairs:
        vr_feats[f"vol_ratio_{n1}_{n2}"] = (vol_feats[f"rv_{n1}"] / (vol_feats[f"rv_{n2}"] + EPS)).astype("float32")
    blocks.append(pd.DataFrame(vr_feats, index=df.index))

    # ================= Final concat =================
    out = pd.concat([df] + blocks, axis=1)

    mem_mb = float(out.memory_usage(deep=True).sum() / 1024**2)
    log_event(
        logger,
        "feature_compute_done",
        seconds=float(time.time() - t0),
        cols=int(out.shape[1]),
        mem_mb=mem_mb,
    )

    return out


# ============================================================
# Labels — Triple Barrier (optimized via optional numba)
# ============================================================

def _try_numba(logger: logging.Logger):
    try:
        import numba  # type: ignore
        log_event(logger, "numba_enabled", version=str(numba.__version__))
        return numba
    except Exception:
        log_event(logger, "numba_disabled")
        return None

def generate_labels_triple_barrier(df: pd.DataFrame, ls: LabelSpec, logger: logging.Logger) -> pd.DataFrame:
    """
    Labels:
      - label_policy: 0 BUY, 1 SELL, 2 WAIT
      - label_tradeable: 1 if not WAIT and exp_vol above q30 (configurable)
    No leakage: uses current exp_vol for threshold and future prices for hit test.
    """
    t0 = time.time()
    if "exp_vol" not in df.columns:
        raise RuntimeError("Missing exp_vol. Compute features first.")

    price = df["Close"].to_numpy(dtype=np.float32, copy=False)
    sigma = df["exp_vol"].to_numpy(dtype=np.float32, copy=False)

    # clamp sigma to avoid insane thresholds
    sigma = np.clip(sigma, ls.sigma_floor, ls.sigma_cap).astype(np.float32, copy=False)

    H = int(ls.horizon_min)
    N = int(len(price))

    labels = np.full(N, 2, dtype=np.int8)  # default WAIT

    numba = _try_numba(logger)

    if numba is not None:
        njit = numba.njit

        @njit(cache=True)
        def tb_numba(price_arr, sig_arr, horizon, u, d):
            n = price_arr.shape[0]
            out = np.full(n, 2, np.int8)
            for i in range(n - horizon - 1):
                p0 = price_arr[i]
                s = sig_arr[i]
                # thresholds in price space (log barrier)
                up = p0 * math.exp(+u * s * math.sqrt(horizon))
                dn = p0 * math.exp(-d * s * math.sqrt(horizon))

                hit_up = -1
                hit_dn = -1
                # scan forward with early break
                for k in range(1, horizon + 1):
                    pk = price_arr[i + k]
                    if hit_up == -1 and pk >= up:
                        hit_up = k
                    if hit_dn == -1 and pk <= dn:
                        hit_dn = k
                    if hit_up != -1 and hit_dn != -1:
                        break

                if hit_up != -1 and hit_dn != -1:
                    out[i] = 0 if hit_up < hit_dn else 1
                elif hit_up != -1:
                    out[i] = 0
                elif hit_dn != -1:
                    out[i] = 1
                else:
                    out[i] = 2
            return out

        labels = tb_numba(price, sigma, H, float(ls.u), float(ls.d))

    else:
        # fallback: chunked loop + early break (slower but safe)
        # still avoids full window materialization
        for i in range(0, N - H - 1):
            p0 = float(price[i])
            s = float(sigma[i])
            up = p0 * math.exp(+ls.u * s * math.sqrt(H))
            dn = p0 * math.exp(-ls.d * s * math.sqrt(H))

            hit_up = None
            hit_dn = None
            for k in range(1, H + 1):
                pk = float(price[i + k])
                if hit_up is None and pk >= up:
                    hit_up = k
                if hit_dn is None and pk <= dn:
                    hit_dn = k
                if hit_up is not None and hit_dn is not None:
                    break

            if hit_up is not None and hit_dn is not None:
                labels[i] = 0 if hit_up < hit_dn else 1
            elif hit_up is not None:
                labels[i] = 0
            elif hit_dn is not None:
                labels[i] = 1
            else:
                labels[i] = 2

    df = df.copy()
    df[ls.label_policy_col] = labels.astype("int8")

    # tradeable meta-label (simple, robust): not WAIT and sigma above q30
    sig_s = pd.Series(sigma, index=df.index)
    q30 = float(sig_s.quantile(0.30))
    df[ls.label_tradeable_col] = ((df[ls.label_policy_col] != 2) & (sig_s > q30)).astype("int8")

    # distribution logs
    buy = int((df[ls.label_policy_col] == 0).sum())
    sell = int((df[ls.label_policy_col] == 1).sum())
    wait = int((df[ls.label_policy_col] == 2).sum())

    log_event(
        logger,
        "labels_done",
        seconds=float(time.time() - t0),
        horizon_min=H,
        u=float(ls.u),
        d=float(ls.d),
        sigma_q30=q30,
        dist={"BUY": buy, "SELL": sell, "WAIT": wait},
    )

    return df


# ============================================================
# Writer (S3 safe)
# ============================================================

def write_features_to_s3(df: pd.DataFrame, s3_path: str, logger: logging.Logger):
    log_event(logger, "s3_write_start", path=s3_path, rows=int(len(df)), cols=int(df.shape[1]))
    t0 = time.time()

    # partition columns must exist
    required = ["interval", "quote", "symbol", "year"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"Missing partition columns: {missing}")

    wr.s3.to_parquet(
        df=df.reset_index(drop=True),
        path=s3_path,
        dataset=True,
        mode="overwrite_partitions",
        partition_cols=required,
        compression="zstd",
        max_rows_by_file=250_000,
        concurrent_partitioning=False,
    )

    log_event(logger, "s3_write_done", seconds=float(time.time() - t0))


# ============================================================
# Orchestrator
# ============================================================

def run(
    athena_cfg: AthenaConfig,
    ds: DatasetSpec,
    feat_spec: FeatureSpec,
    label_spec: Optional[LabelSpec],
    out_s3: str,
    logger: logging.Logger,
):
    raw = read_market_from_athena(athena_cfg, ds, logger)

    # partition columns
    raw["symbol"] = ds.symbol
    raw["quote"] = ds.quote
    raw["interval"] = ds.interval
    raw["year"] = pd.to_datetime(raw["Open_Time"], unit="ms", utc=True).dt.year.astype("int32")

    log_event(logger, "raw_ready", rows=int(len(raw)), year_min=int(raw["year"].min()), year_max=int(raw["year"].max()))

    feats = compute_trm_kpi(raw, feat_spec, logger)

    # drop unusable leading NaNs (rolling indicators)
    before = len(feats)
    feats = feats.dropna()
    log_event(logger, "dropna_done", dropped=int(before - len(feats)), remain=int(len(feats)))

    if label_spec is not None:
        feats = generate_labels_triple_barrier(feats, label_spec, logger)

    write_features_to_s3(feats, out_s3, logger)
    log_event(logger, "pipeline_done", out_s3=out_s3)


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    logger = setup_logger()

    athena_cfg = AthenaConfig(
        region="eu-west-3",
        database="bourse",
        table="market_raw",
        workgroup="primary",
        s3_output="s3://qbia/athena/results/",
        ctas=False,
    )

    ds = DatasetSpec(
        symbol="BTCUSDT",
        quote="USDT",
        interval="1m",
        years=[2019, 2020, 2021, 2022, 2023, 2024],
    )

    # 2 days horizon example (2880 minutes). For 3 days: 4320.
    label_spec = LabelSpec(
        horizon_min=2880,
        u=1.0,
        d=1.0,
        sigma_floor=1e-6,
        sigma_cap=0.10,
        label_policy_col="label_policy",
        label_tradeable_col="label_tradeable",
    )

    run(
        athena_cfg=athena_cfg,
        ds=ds,
        feat_spec=FeatureSpec(),
        label_spec=label_spec,
        out_s3="s3://qbia/bourse/processed/market/",
        logger=logger,
    )
