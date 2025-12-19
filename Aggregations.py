from __future__ import annotations

import math
import time
import logging
from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
import awswrangler as wr


# ============================================================
# Logging
# ============================================================

def setup_logger(level=logging.INFO) -> logging.Logger:
    logger = logging.getLogger("trm_feature_factory")
    logger.setLevel(level)
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s"))
        logger.addHandler(h)
    return logger


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
    adx_window: int = 14
    boll_window: int = 20
    boll_k: float = 2.0
    donchian_window: int = 20
    keltner_window: int = 20
    keltner_atr_mult: float = 1.5
    var_windows: Sequence[int] = (60, 240, 1440)
    var_alpha: float = 0.01


# ============================================================
# Athena Reader
# ============================================================

def read_market_from_athena(cfg: AthenaConfig, ds: DatasetSpec, logger: logging.Logger) -> pd.DataFrame:
    years_sql = ",".join(map(str, ds.years))
    sql = f"""
    SELECT *
    FROM {cfg.database}.{cfg.table}
    WHERE interval='{ds.interval}'
      AND quote='{ds.quote}'
      AND symbol='{ds.symbol}'
      AND year IN ({years_sql})
    ORDER BY open_time
    """

    logger.info("Athena query start")
    t0 = time.time()

    df = wr.athena.read_sql_query(
        sql=sql,
        database=cfg.database,
        workgroup=cfg.workgroup,
        s3_output=cfg.s3_output,
        ctas_approach=cfg.ctas,
    )

    logger.info("Athena query done: %d rows in %.1fs", len(df), time.time() - t0)

    return df.rename(columns={
        "open_time": "Open Time",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "quote_volume": "Quote Volume",
        "trades": "Trades",
        "taker_buy_base": "Taker Buy Base",
        "taker_buy_quote": "Taker Buy Quote",
    })


# ============================================================
# Core Utils
# ============================================================

EPS = 1e-12

def safe_div(a, b):
    return a / (b.replace(0, np.nan) + EPS)


def wilder_ema(x, n):
    return x.ewm(alpha=1 / n, adjust=False).mean()


def true_range(h, l, c):
    pc = c.shift(1)
    return pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


# ============================================================
# Feature Factory (OPTIMIZED)
# ============================================================

def compute_trm_kpi(df: pd.DataFrame, spec: FeatureSpec, logger: logging.Logger) -> pd.DataFrame:
    t0 = time.time()
    logger.info("Feature computation start")

    df = df.copy()
    df["datetime"] = pd.to_datetime(df["Open Time"], unit="ms", utc=True)
    df = df.sort_values("datetime").set_index("datetime")

    price = df["Close"]
    volume = df["Volume"]

    blocks = []

    # ================= Returns =================
    ret = price.pct_change()
    log_ret = np.log(safe_div(price, price.shift(1)))

    blocks.append(pd.DataFrame({
        "ret": ret,
        "log_ret": log_ret,
    }))

    # ================= Volatility =================
    vol_feats = {}
    for n in spec.windows:
        rv = log_ret.rolling(n).std()
        vol_feats[f"rv_{n}"] = rv
        vol_feats[f"rv_ann_{n}"] = rv * math.sqrt(spec.minutes_per_year)

    blocks.append(pd.DataFrame(vol_feats))

    # ================= Moving averages =================
    ma_feats = {}
    for w in spec.sma_ema_windows:
        ema = price.ewm(span=w, adjust=False).mean()
        ma_feats[f"ema_{w}"] = ema
        ma_feats[f"dist_ema_{w}"] = safe_div(price - ema, ema)

    blocks.append(pd.DataFrame(ma_feats))

    # ================= ATR =================
    tr = true_range(df["High"], df["Low"], price)
    atr = wilder_ema(tr, spec.atr_window)

    blocks.append(pd.DataFrame({
        f"atr_{spec.atr_window}": atr,
        f"atr_pct_{spec.atr_window}": safe_div(atr, price),
    }))

    # ================= RSI =================
    delta = price.diff()
    up = delta.clip(lower=0)
    down = (-delta).clip(lower=0)

    rs = safe_div(wilder_ema(up, spec.rsi_window), wilder_ema(down, spec.rsi_window))
    rsi = 100 - (100 / (1 + rs))

    blocks.append(pd.DataFrame({f"rsi_{spec.rsi_window}": rsi}))

    # ================= Risk (VaR / CVaR) =================
    risk_feats = {}

    for n in spec.var_windows:
        r = ret.rolling(n)

        var = r.quantile(spec.var_alpha)
        cvar = r.apply(
            lambda x: x[x <= np.quantile(x, spec.var_alpha)].mean() if len(x.dropna()) else np.nan,
            raw=False,
        )

        risk_feats[f"var_{int((1-spec.var_alpha)*100)}_{n}"] = var
        risk_feats[f"cvar_{int((1-spec.var_alpha)*100)}_{n}"] = cvar

    blocks.append(pd.DataFrame(risk_feats))

    # ================= Final concat =================
    out = pd.concat([df] + blocks, axis=1)

    logger.info(
        "Feature computation done in %.1fs | columns=%d | mem=%.2f MB",
        time.time() - t0,
        out.shape[1],
        out.memory_usage(deep=True).sum() / 1024**2,
    )

    return out


# ============================================================
# Writer (S3 SAFE)
# ============================================================

def write_features_to_s3(df: pd.DataFrame, s3_path: str, logger: logging.Logger):
    logger.info("Writing to S3: %s", s3_path)
    t0 = time.time()

    wr.s3.to_parquet(
        df=df.reset_index(),
        path=s3_path,
        dataset=True,
        mode="overwrite_partitions",
        partition_cols=["interval", "quote", "symbol", "year"],
        compression="zstd",
        max_rows_by_file=250_000,
        concurrent_partitioning=False,
    )

    logger.info("S3 write done in %.1fs", time.time() - t0)


# ============================================================
# Orchestrator
# ============================================================

def run(athena_cfg, ds, feat_spec, out_s3, logger):
    raw = read_market_from_athena(athena_cfg, ds, logger)

    raw["symbol"] = ds.symbol
    raw["quote"] = ds.quote
    raw["interval"] = ds.interval
    raw["year"] = pd.to_datetime(raw["Open Time"], unit="ms", utc=True).dt.year

    feats = compute_trm_kpi(raw, feat_spec, logger)

    write_features_to_s3(feats, out_s3, logger)


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

    run(
        athena_cfg=athena_cfg,
        ds=ds,
        feat_spec=FeatureSpec(),
        out_s3="s3://qbia/bourse/processed/market/",
        logger=logger,
    )
