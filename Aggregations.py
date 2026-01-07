from __future__ import annotations

import math
import time
import logging
from dataclasses import dataclass
from typing import Sequence, Optional, Dict, Tuple, List, Set

import numpy as np
import pandas as pd
import awswrangler as wr

# ============================================================
# Logging (JSON-ish, crash-visible)
# ============================================================

def setup_logger(level=logging.INFO) -> logging.Logger:
    logger = logging.getLogger("trm_feature_factory_v2")
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

    # windows tuned for 1m (keep short/medium; long windows create dead features)
    windows: Sequence[int] = (3, 5, 10, 15, 30, 60, 120, 240, 480, 720)
    ema_windows: Sequence[int] = (8, 21, 55, 144)  # common in systematic trading
    rsi_window: int = 14
    atr_window: int = 14

    # risk / distribution
    var_windows: Sequence[int] = (60, 240, 720)
    var_alpha: float = 0.01

    # engineered
    ema_slope_lags: Sequence[int] = (3, 10, 30, 60)
    vol_ratio_pairs: Sequence[Tuple[int, int]] = ((30, 120), (60, 240), (240, 720))

    # exp vol proxy
    exp_vol_triplet: Tuple[int, int, int] = (30, 120, 480)
    exp_vol_weights: Tuple[float, float, float] = (0.5, 0.3, 0.2)

    # microstructure / flow
    flow_windows: Sequence[int] = (5, 15, 60, 240)

    # normalization windows
    z_windows: Sequence[int] = (60, 240)

@dataclass(frozen=True)
class LabelSpec:
    # keep horizons realistic for 1m learning
    horizon_min: int = 480  # 8h default (NOT 2 days)

    # barrier multipliers (sigma-scaled)
    u: float = 1.0
    d: float = 1.0

    # volatility controls
    sigma_floor: float = 1e-6
    sigma_cap: float = 0.10

    label_policy_col: str = "label_policy"         # 0 BUY, 1 SELL, 2 WAIT
    label_tradeable_col: str = "label_tradeable"   # 0/1

    # tradeable filter (keep only sufficiently volatile moments)
    tradeable_vol_q: float = 0.25

@dataclass(frozen=True)
class DiscoverySpec:
    raw_dataset_s3_prefix: str
    interval: str
    quote: str
    symbol_key: str = "symbol"
    year_key: str = "year"

# ============================================================
# Core Utils
# ============================================================

EPS = 1e-12

def wilder_ema(x: pd.Series, n: int) -> pd.Series:
    return x.ewm(alpha=1 / n, adjust=False).mean()

def true_range(h: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)

def ensure_float32(df: pd.DataFrame, cols: Sequence[str]) -> None:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype("float32", copy=False)

def rolling_zscore(x: pd.Series, w: int) -> pd.Series:
    mu = x.rolling(w, min_periods=w).mean()
    sd = x.rolling(w, min_periods=w).std()
    return (x - mu) / (sd + EPS)

def mad_zscore(x: pd.Series, w: int) -> pd.Series:
    med = x.rolling(w, min_periods=w).median()
    mad = (x - med).abs().rolling(w, min_periods=w).median()
    return (x - med) / (mad + EPS)

# ============================================================
# S3 Discovery
# ============================================================

def _extract_partition_value(s: str, key: str) -> Optional[str]:
    token = f"{key}="
    idx = s.find(token)
    if idx == -1:
        return None
    start = idx + len(token)
    end = s.find("/", start)
    return s[start:] if end == -1 else s[start:end]

def discover_symbols_and_years_from_s3(
    spec: DiscoverySpec,
    logger: logging.Logger,
) -> Tuple[List[str], Dict[str, List[int]]]:
    prefix = spec.raw_dataset_s3_prefix.rstrip("/") + f"/interval={spec.interval}/quote={spec.quote}/"
    log_event(logger, "s3_discovery_start", prefix=prefix)

    t0 = time.time()
    objects = wr.s3.list_objects(prefix)
    sec = float(time.time() - t0)

    if not objects:
        log_event(logger, "s3_discovery_empty", prefix=prefix, seconds=sec)
        return [], {}

    symbols: Set[str] = set()
    years_by_symbol: Dict[str, Set[int]] = {}

    for p in objects:
        sym = _extract_partition_value(p, spec.symbol_key)
        yr = _extract_partition_value(p, spec.year_key)

        if sym:
            symbols.add(sym)
            years_by_symbol.setdefault(sym, set())
            if yr is not None:
                try:
                    years_by_symbol[sym].add(int(yr))
                except Exception:
                    pass

    symbols_list = sorted(symbols)
    years_map = {s: sorted(list(years_by_symbol.get(s, set()))) for s in symbols_list}

    log_event(logger, "s3_discovery_done", seconds=sec, symbols_count=len(symbols_list), sample_symbols=symbols_list[:10])
    return symbols_list, years_map

# ============================================================
# Reader
# ============================================================

def read_market_from_athena(cfg: AthenaConfig, ds: DatasetSpec, logger: logging.Logger) -> pd.DataFrame:
    log_event(logger, "s3_read_start", symbol=ds.symbol, interval=ds.interval, years=list(ds.years))
    t0 = time.time()

    dfs = []
    for year in ds.years:
        s3_path = f"s3://qbia/bourse/raw/market/interval={ds.interval}/quote={ds.quote}/symbol={ds.symbol}/year={year}/"
        try:
            year_df = wr.s3.read_parquet(s3_path)
            dfs.append(year_df)
        except Exception as e:
            log_event(logger, "s3_read_year_failed", year=year, error=str(e))

    if not dfs:
        log_event(logger, "s3_read_done", rows=0, seconds=float(time.time() - t0))
        return pd.DataFrame()

    df = pd.concat(dfs, ignore_index=True)

    if "0" in df.columns:
        df = df.rename(columns={
            "0": "open_time",
            "1": "open",
            "2": "high",
            "3": "low",
            "4": "close",
            "5": "volume",
            "6": "close_time",
            "7": "quote_volume",
            "8": "trades",
            "9": "taker_buy_base",
            "10": "taker_buy_quote",
            "11": "ignore",
        })

    if "open_time" in df.columns:
        df = df.sort_values("open_time").reset_index(drop=True)

    log_event(logger, "s3_read_done", rows=int(len(df)), seconds=float(time.time() - t0))

    df = df.rename(columns={
        "open_time": "Open_Time",
        "close_time": "Close_Time",
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

    ensure_float32(df, ["Open", "High", "Low", "Close", "Volume", "Quote_Volume", "Taker_Buy_Base", "Taker_Buy_Quote"])

    df["Open_Time"] = df["Open_Time"].where(df["Open_Time"].notna(), -1).astype("int64")
    df["Close_Time"] = df["Close_Time"].where(df["Close_Time"].notna(), -1).astype("int64")
    df["Trades"] = df["Trades"].where(df["Trades"].notna(), 0).astype("int32")

    df = df[(df["Open_Time"] > 0) & (df["Close_Time"] > 0)].copy()
    return df

# ============================================================
# Feature Factory (v2) — aligned, normalized, microstructure
# ============================================================

def compute_features_v2(df: pd.DataFrame, spec: FeatureSpec, logger: logging.Logger) -> pd.DataFrame:
    """
    Goals:
      1) Remove scale issues by using normalized / z-scored features
      2) Add microstructure (flow / pressure) for 1m edge
      3) Avoid long dead windows; keep features reactive
      4) Align features for execution: use bar-close info -> predict next bar action
    """
    t0 = time.time()
    log_event(logger, "feature_compute_start_v2", rows=int(len(df)))

    df = df.copy()
    df["datetime"] = pd.to_datetime(df["Open_Time"], unit="ms", utc=True)
    df = df.sort_values("datetime", kind="mergesort")
    df = df.set_index("datetime", drop=False)

    price = df["Close"].astype("float32", copy=False)
    high = df["High"].astype("float32", copy=False)
    low = df["Low"].astype("float32", copy=False)

    volume = df["Volume"].astype("float32", copy=False)
    qvol = df["Quote_Volume"].astype("float32", copy=False)
    trades = df["Trades"].astype("float32", copy=False)
    tbq = df["Taker_Buy_Quote"].astype("float32", copy=False)

    blocks: List[pd.DataFrame] = []

    # ------------------------------------------------------------------
    # Returns
    # ------------------------------------------------------------------
    log_ret = np.log((price / (price.shift(1) + EPS)).astype("float32")).astype("float32")
    ret = price.pct_change().astype("float32")
    blocks.append(pd.DataFrame({"ret": ret, "log_ret": log_ret}, index=df.index))

    # ------------------------------------------------------------------
    # Volatility: compute sigma per-minute (dimensionally consistent)
    # rv_n = std over n minutes
    # sigma_minute_n = rv_n / sqrt(n)
    # ------------------------------------------------------------------
    vol_feats: Dict[str, pd.Series] = {}
    sig_min_feats: Dict[str, pd.Series] = {}
    for n in spec.windows:
        rv = log_ret.rolling(n, min_periods=n).std().astype("float32")
        vol_feats[f"rv_{n}"] = rv
        sig_min_feats[f"sig_min_{n}"] = (rv / math.sqrt(n)).astype("float32")

        # normalized return (classic)
        vol_feats[f"ret_norm_{n}"] = (log_ret / (rv + EPS)).astype("float32")

    blocks.append(pd.DataFrame(vol_feats, index=df.index))
    blocks.append(pd.DataFrame(sig_min_feats, index=df.index))

    # exp sigma minute proxy (for labels)
    a, b, c = spec.exp_vol_triplet
    wa, wb, wc = spec.exp_vol_weights
    sigma_min = (
        wa * sig_min_feats[f"sig_min_{a}"] +
        wb * sig_min_feats[f"sig_min_{b}"] +
        wc * sig_min_feats[f"sig_min_{c}"]
    ).astype("float32")
    blocks.append(pd.DataFrame({"sigma_min": sigma_min}, index=df.index))

    # ------------------------------------------------------------------
    # Trend / mean reversion: use returns around EMA in z-space
    # ------------------------------------------------------------------
    trend_feats: Dict[str, pd.Series] = {}
    ema_series: Dict[int, pd.Series] = {}
    for w in spec.ema_windows:
        ema = price.ewm(span=w, adjust=False).mean().astype("float32")
        ema_series[w] = ema

        # distance in ATR units (more stable than %)
        tr = true_range(high, low, price)
        atr = wilder_ema(tr, spec.atr_window).astype("float32")
        trend_feats[f"dist_ema_atr_{w}"] = ((price - ema) / (atr + EPS)).astype("float32")

        # ema slope normalized
        for lag in spec.ema_slope_lags:
            trend_feats[f"ema_slope_{w}_{lag}"] = (ema.diff(lag) / (atr + EPS)).astype("float32")

    blocks.append(pd.DataFrame(trend_feats, index=df.index))

    # ------------------------------------------------------------------
    # ATR / range / candle structure
    # ------------------------------------------------------------------
    tr = true_range(high, low, price)
    atr = wilder_ema(tr, spec.atr_window).astype("float32")
    candle_range = (high - low).astype("float32")

    # close location value (0..1)
    clv = ((price - low) / (candle_range + EPS)).astype("float32")

    blocks.append(pd.DataFrame({
        f"atr_{spec.atr_window}": atr,
        f"atr_pct_{spec.atr_window}": (atr / (price + EPS)).astype("float32"),
        "range": candle_range,
        "range_atr": (candle_range / (atr + EPS)).astype("float32"),
        "clv": clv,
        "body": (df["Close"] - df["Open"]).astype("float32"),
        "body_atr": ((df["Close"] - df["Open"]).astype("float32") / (atr + EPS)).astype("float32"),
    }, index=df.index))

    # ------------------------------------------------------------------
    # RSI (keep only one + slope)
    # ------------------------------------------------------------------
    delta = price.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    rs = (wilder_ema(up, spec.rsi_window) / (wilder_ema(down, spec.rsi_window) + EPS)).astype("float32")
    rsi = (100 - (100 / (1 + rs))).astype("float32")
    blocks.append(pd.DataFrame({
        f"rsi_{spec.rsi_window}": rsi,
        "rsi_slope_5": rsi.diff(5).astype("float32"),
    }, index=df.index))

    # ------------------------------------------------------------------
    # Microstructure / flow (THIS is what your original pipeline lacked)
    # ------------------------------------------------------------------
    flow_feats: Dict[str, pd.Series] = {}

    buy_pressure = (tbq / (qvol + EPS)).astype("float32")  # 0..1 approx
    signed_quote = (2.0 * tbq - qvol).astype("float32")    # positive => aggressive buy
    trades_per_qvol = (trades / (qvol + EPS)).astype("float32")

    flow_feats["buy_pressure"] = buy_pressure
    flow_feats["signed_quote"] = signed_quote
    flow_feats["trades_per_qvol"] = trades_per_qvol

    for w in spec.flow_windows:
        flow_feats[f"buy_pressure_z_{w}"] = rolling_zscore(buy_pressure, w).astype("float32")
        flow_feats[f"signed_quote_z_{w}"] = rolling_zscore(signed_quote, w).astype("float32")
        flow_feats[f"qvol_z_{w}"] = rolling_zscore(qvol, w).astype("float32")
        flow_feats[f"trades_z_{w}"] = rolling_zscore(trades, w).astype("float32")

    blocks.append(pd.DataFrame(flow_feats, index=df.index))

    # ------------------------------------------------------------------
    # Robust z-scored returns & volatility (stable across symbols)
    # ------------------------------------------------------------------
    z_feats: Dict[str, pd.Series] = {}
    for w in spec.z_windows:
        z_feats[f"log_ret_z_{w}"] = rolling_zscore(log_ret, w).astype("float32")
        z_feats[f"sig_min_z_{w}"] = rolling_zscore(sigma_min, w).astype("float32")
        z_feats[f"qvol_mad_{w}"] = mad_zscore(qvol, w).astype("float32")

    blocks.append(pd.DataFrame(z_feats, index=df.index))

    # ------------------------------------------------------------------
    # Assemble
    # ------------------------------------------------------------------
    out = pd.concat([df] + blocks, axis=1)

    # ------------------------------------------------------------------
    # EXECUTION ALIGNMENT (critical): features at t predict action at t+1
    # Shift ONLY feature columns, not raw OHLCV (keep them for debug/backtest)
    # ------------------------------------------------------------------
    feature_cols = [c for c in out.columns if c not in df.columns]  # all engineered
    out[feature_cols] = out[feature_cols].shift(1)

    mem_mb = float(out.memory_usage(deep=True).sum() / 1024**2)
    log_event(logger, "feature_compute_done_v2", seconds=float(time.time() - t0), cols=int(out.shape[1]), mem_mb=mem_mb)
    return out

# ============================================================
# Labels — Triple Barrier (v2) using sigma_min (dimensionally correct)
# ============================================================

def _try_numba(logger: logging.Logger):
    try:
        import numba  # type: ignore
        log_event(logger, "numba_enabled", version=str(numba.__version__))
        return numba
    except Exception:
        log_event(logger, "numba_disabled")
        return None

def generate_labels_triple_barrier_v2(df: pd.DataFrame, ls: LabelSpec, logger: logging.Logger) -> pd.DataFrame:
    """
    Uses sigma_min (per-minute sigma). Barrier scaling becomes consistent:
      up/dn = p0 * exp( +/- u * sigma_min * sqrt(H) )
    but sigma_min truly represents per-minute volatility.
    """
    t0 = time.time()
    if "sigma_min" not in df.columns:
        raise RuntimeError("Missing sigma_min. Compute features v2 first.")

    price = df["Close"].to_numpy(dtype=np.float32, copy=False)
    sigma = df["sigma_min"].to_numpy(dtype=np.float32, copy=False)
    sigma = np.clip(sigma, ls.sigma_floor, ls.sigma_cap).astype(np.float32, copy=False)

    H = int(ls.horizon_min)
    N = int(len(price))
    labels = np.full(N, 2, dtype=np.int8)

    numba = _try_numba(logger)

    if numba is not None:
        njit = numba.njit

        @njit(cache=True)
        def tb_numba(price_arr, sig_arr, horizon, u, d):
            n = price_arr.shape[0]
            out = np.full(n, 2, np.int8)
            root_h = math.sqrt(horizon)
            for i in range(n - horizon - 1):
                p0 = price_arr[i]
                s = sig_arr[i]
                up = p0 * math.exp(+u * s * root_h)
                dn = p0 * math.exp(-d * s * root_h)

                hit_up = -1
                hit_dn = -1
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
        root_h = math.sqrt(H)
        for i in range(0, N - H - 1):
            p0 = float(price[i])
            s = float(sigma[i])
            up = p0 * math.exp(+ls.u * s * root_h)
            dn = p0 * math.exp(-ls.d * s * root_h)

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

    sig_s = pd.Series(sigma, index=df.index)
    q = float(sig_s.quantile(ls.tradeable_vol_q))
    df[ls.label_tradeable_col] = ((df[ls.label_policy_col] != 2) & (sig_s > q)).astype("int8")

    buy = int((df[ls.label_policy_col] == 0).sum())
    sell = int((df[ls.label_policy_col] == 1).sum())
    wait = int((df[ls.label_policy_col] == 2).sum())

    log_event(logger, "labels_done_v2", seconds=float(time.time() - t0), horizon_min=H, u=float(ls.u), d=float(ls.d),
              sigma_q=float(q), dist={"BUY": buy, "SELL": sell, "WAIT": wait})

    return df

# ============================================================
# Writer
# ============================================================

def write_features_to_s3(df: pd.DataFrame, s3_path: str, logger: logging.Logger):
    log_event(logger, "s3_write_start", path=s3_path, rows=int(len(df)), cols=int(df.shape[1]))
    t0 = time.time()

    required = ["interval", "quote", "symbol", "year"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"Missing partition columns: {missing}")

    # Write in chunks to avoid OOM (4M rows = ~2.5GB in memory)
    chunk_size = 500_000  # 500k rows per chunk
    total_rows = len(df)
    n_chunks = (total_rows + chunk_size - 1) // chunk_size

    log_event(logger, "s3_write_chunked", total_rows=total_rows, chunks=n_chunks, chunk_size=chunk_size)

    for i in range(0, total_rows, chunk_size):
        chunk = df.iloc[i:i+chunk_size]
        chunk_num = i // chunk_size + 1

        log_event(logger, "s3_write_chunk_start", chunk=chunk_num, rows=len(chunk))

        wr.s3.to_parquet(
            df=chunk.reset_index(drop=True),
            path=s3_path,
            dataset=True,
            mode="append",  # Changed to append for chunked writes
            partition_cols=required,
            compression="zstd",
            max_rows_by_file=250_000,
            concurrent_partitioning=False,
            use_threads=False,  # Disable threading to reduce memory
        )

        log_event(logger, "s3_write_chunk_done", chunk=chunk_num, seconds=float(time.time() - t0))

        # Force garbage collection between chunks
        import gc
        gc.collect()

    log_event(logger, "s3_write_done", seconds=float(time.time() - t0))

# ============================================================
# Orchestrator
# ============================================================

def run_one(
    athena_cfg: AthenaConfig,
    ds: DatasetSpec,
    feat_spec: FeatureSpec,
    label_spec: Optional[LabelSpec],
    out_s3: str,
    logger: logging.Logger,
):
    raw = read_market_from_athena(athena_cfg, ds, logger)

    if raw.empty:
        log_event(logger, "raw_empty", symbol=ds.symbol, interval=ds.interval, quote=ds.quote, years=list(ds.years))
        return

    raw["symbol"] = ds.symbol
    raw["quote"] = ds.quote
    raw["interval"] = ds.interval
    raw["year"] = pd.to_datetime(raw["Open_Time"], unit="ms", utc=True).dt.year.astype("int32")

    log_event(logger, "raw_ready", symbol=ds.symbol, rows=int(len(raw)),
              year_min=int(raw["year"].min()), year_max=int(raw["year"].max()))

    feats = compute_features_v2(raw, feat_spec, logger)

    before = len(feats)
    feats = feats.dropna()
    log_event(logger, "dropna_done", symbol=ds.symbol, dropped=int(before - len(feats)), remain=int(len(feats)))

    if label_spec is not None:
        feats = generate_labels_triple_barrier_v2(feats, label_spec, logger)

    write_features_to_s3(feats, out_s3, logger)
    log_event(logger, "pipeline_done", symbol=ds.symbol, out_s3=out_s3)

def run_all_from_s3_discovery(
    athena_cfg: AthenaConfig,
    discovery: DiscoverySpec,
    feat_spec: FeatureSpec,
    label_spec: Optional[LabelSpec],
    out_s3: str,
    logger: logging.Logger,
    years_fallback: Optional[Sequence[int]] = None,
    symbols_filter: Optional[List[str]] = None,  # NEW: filter specific symbols
):
    symbols, years_by_symbol = discover_symbols_and_years_from_s3(discovery, logger)
    if not symbols:
        raise RuntimeError("No symbols discovered in S3. Check raw_dataset_s3_prefix / partition layout.")

    # NEW: Apply symbol filter if provided
    if symbols_filter is not None:
        symbols = [s for s in symbols if s in symbols_filter]
        log_event(logger, "symbols_filtered", filtered_count=len(symbols), filter_list=symbols_filter)

    if not symbols:
        log_event(logger, "no_symbols_after_filter", filter=symbols_filter)
        return

    ok = 0
    fail = 0

    for sym in symbols:
        years = years_by_symbol.get(sym) or (list(years_fallback) if years_fallback is not None else [])
        if not years:
            log_event(logger, "symbol_skip_no_years", symbol=sym)
            continue

        ds = DatasetSpec(symbol=sym, quote=discovery.quote, interval=discovery.interval, years=years)
        log_event(logger, "symbol_start", symbol=sym, years=years)

        try:
            run_one(athena_cfg, ds, feat_spec, label_spec, out_s3, logger)
            ok += 1
        except Exception as e:
            fail += 1
            log_event(logger, "symbol_failed", symbol=sym, err=str(e))

    log_event(logger, "run_all_done", ok=ok, fail=fail, total=len(symbols))

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

    # Horizon tuned for 1m learning (8h).
    # If you want swing-trading on 1m bars: 720 to 1440 can work.
    label_spec = LabelSpec(
        horizon_min=480,
        u=1.0,
        d=1.0,
        sigma_floor=1e-6,
        sigma_cap=0.10,
        label_policy_col="label_policy",
        label_tradeable_col="label_tradeable",
        tradeable_vol_q=0.25,
    )

    discovery = DiscoverySpec(
        raw_dataset_s3_prefix="s3://qbia/bourse/raw/market/",
        interval="1m",
        quote="USDT",
    )

    run_all_from_s3_discovery(
        athena_cfg=athena_cfg,
        discovery=discovery,
        feat_spec=FeatureSpec(),
        label_spec=label_spec,
        out_s3="s3://qbia/bourse/processed/market_v2/",
        logger=logger,
        years_fallback=[2019, 2020, 2021, 2022, 2023, 2024, 2025],
        symbols_filter=["BTCUSDT"],  # Process only Bitcoin
    )
