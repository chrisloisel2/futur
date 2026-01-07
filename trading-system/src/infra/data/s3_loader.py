"""
S3 Data Loader for Trading System
Loads processed market data from S3 for backtesting/training.
"""

from __future__ import annotations

from typing import List, Optional
import numpy as np
import pandas as pd
import s3fs

from common.logging.setup import get_logger

logger = get_logger(__name__)


# =============================================================================
# SCHEMA NORMALIZATION
# =============================================================================

_RENAME_MAP = {
    # OHLCV legacy -> normalized
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",

    # timestamps legacy -> normalized
    "Open_Time": "open_time",
    "Close_Time": "close_time",

    # extra legacy -> normalized
    "Quote_Volume": "quote_volume",
    "Trades": "trades",
    "Taker_Buy_Base": "taker_buy_base",
    "Taker_Buy_Quote": "taker_buy_quote",
}


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize column names to match trading system expectations.
    Keeps unknown columns unchanged.
    """
    if df is None or df.empty:
        return df
    return df.rename(columns=_RENAME_MAP)


# =============================================================================
# MODEL FEATURES (39)
# =============================================================================

MODEL_39_FEATURES = [
    # OHLCV
    "open", "high", "low", "close", "volume",
    # Returns
    "returns_1", "returns_5", "returns_10", "log_returns_1",
    # RV + RV_ANN
    "rv_5", "rv_15", "rv_30", "rv_60",
    "rv_5_ann", "rv_15_ann", "rv_30_ann", "rv_60_ann",
    # ATR + Volume + Regime
    "atr_14", "atr_20", "atr_pct_14", "atr_pct_20",
    "volume_ma_20", "volume_std_20",
    "vol_regime",
    # EMA 12/26/50
    "ema_12", "ema_12_slope", "ema_12_dist",
    "ema_26", "ema_26_slope", "ema_26_dist",
    "ema_50", "ema_50_slope", "ema_50_dist",
    # Momentum
    "rsi_14",
    # Other
    "high_low_range", "close_open_ret",
    "trend_regime",
    "month_sin", "month_cos",
]


def _print_loaded_columns(df: pd.DataFrame, title: str):
    cols = list(df.columns)
    logger.info({"msg": title, "n_cols": int(len(cols)), "cols": cols})

    print("\n" + "=" * 90)
    print(title)
    print(f"{len(cols)} colonnes")
    for c in cols:
        print("-", c)
    print("=" * 90 + "\n")


def _print_required_columns_status(df: pd.DataFrame, required: List[str]) -> None:
    cols = set(df.columns)
    present = [c for c in required if c in cols]
    missing = [c for c in required if c not in cols]

    print("\n" + "=" * 90)
    print("MODEL REQUIRED COLUMNS (39 FEATURES)")
    print(f"Present: {len(present)}/{len(required)}")
    for c in present:
        print("✓", c)
    if missing:
        print("\nMissing:")
        for c in missing:
            print("✗", c)
    print("=" * 90 + "\n")


def _ensure_dt_index(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure df is indexed by datetime (UTC).
    Supports: datetime column, open_time (ms), close_time (ms).
    """
    if df is None or df.empty:
        return df

    df = normalize_columns(df)

    if "datetime" in df.columns:
        dt = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
    elif "open_time" in df.columns:
        dt = pd.to_datetime(df["open_time"], unit="ms", utc=True, errors="coerce")
    elif "close_time" in df.columns:
        dt = pd.to_datetime(df["close_time"], unit="ms", utc=True, errors="coerce")
    else:
        raise ValueError("No datetime/open_time/close_time column in dataset")

    df = df.copy()
    df["datetime"] = dt
    df = df.dropna(subset=["datetime"])
    df = df.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="first")
    df = df.set_index("datetime", drop=True).sort_index()
    return df


def _build_model_39_from_processed(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build your 39 model features from processed parquets schema.
    If some features are missing in parquet, compute them from OHLCV.
    """

    if df is None or df.empty:
        return df

    df = df.copy()
    df = normalize_columns(df)
    df = _ensure_dt_index(df)

    # Direct mappings from your processed schema to model schema
    rename_map = {
        "ret": "returns_1",
        "log_ret": "log_returns_1",

        "rv_ann_5": "rv_5_ann",
        "rv_ann_15": "rv_15_ann",
        "rv_ann_30": "rv_30_ann",
        "rv_ann_60": "rv_60_ann",
    }
    df = df.rename(columns=rename_map)

    # Base OHLCV required
    base = ["open", "high", "low", "close", "volume"]
    missing_base = [c for c in base if c not in df.columns]
    if missing_base:
        raise ValueError(f"Processed parquet missing base OHLCV columns: {missing_base}")

    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    vol = df["volume"]

    # RETURNS
    if "returns_1" not in df.columns:
        df["returns_1"] = close.pct_change()
    if "returns_5" not in df.columns:
        df["returns_5"] = close.pct_change(5)
    if "returns_10" not in df.columns:
        df["returns_10"] = close.pct_change(10)
    if "log_returns_1" not in df.columns:
        df["log_returns_1"] = np.log(close / close.shift(1))

    # RV (compute if missing)
    for h in (5, 15, 30, 60):
        col = f"rv_{h}"
        if col not in df.columns:
            df[col] = df["returns_1"].abs().rolling(h, min_periods=h).mean()

    # RV ANN (compute if missing)
    ann_factor = np.sqrt(252.0 * 24.0 * 60.0)
    if "rv_5_ann" not in df.columns:
        df["rv_5_ann"] = df["rv_5"] * ann_factor
    if "rv_15_ann" not in df.columns:
        df["rv_15_ann"] = df["rv_15"] * ann_factor
    if "rv_30_ann" not in df.columns:
        df["rv_30_ann"] = df["rv_30"] * ann_factor
    if "rv_60_ann" not in df.columns:
        df["rv_60_ann"] = df["rv_60"] * ann_factor

    # ATR 14/20 + pct
    prev_close = close.shift(1)
    tr = np.maximum(
        high - low,
        np.maximum((high - prev_close).abs(), (low - prev_close).abs()),
    )
    if "atr_14" not in df.columns:
        df["atr_14"] = tr.rolling(14, min_periods=14).mean()
    if "atr_20" not in df.columns:
        df["atr_20"] = tr.rolling(20, min_periods=20).mean()

    if "atr_pct_14" not in df.columns:
        df["atr_pct_14"] = (df["atr_14"] / prev_close).clip(0.0, 1.0)
    if "atr_pct_20" not in df.columns:
        df["atr_pct_20"] = (df["atr_20"] / prev_close).clip(0.0, 1.0)

    # Volume stats
    if "volume_ma_20" not in df.columns:
        df["volume_ma_20"] = vol.rolling(20, min_periods=20).mean()
    if "volume_std_20" not in df.columns:
        df["volume_std_20"] = vol.rolling(20, min_periods=20).std(ddof=1)

    # EMA 12/26/50 (+ slope/dist)
    for span in (12, 26, 50):
        ema = close.ewm(span=span, adjust=False).mean()
        df[f"ema_{span}"] = ema
        df[f"ema_{span}_slope"] = ema.pct_change(5)
        df[f"ema_{span}_dist"] = (close / ema - 1.0)

    # RSI 14
    if "rsi_14" not in df.columns:
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        avg_gain = gain.rolling(14, min_periods=14).mean()
        avg_loss = loss.rolling(14, min_periods=14).mean()
        rs = avg_gain / (avg_loss + 1e-12)
        rsi = 100.0 - (100.0 / (1.0 + rs))
        df["rsi_14"] = rsi

    # Other
    if "high_low_range" not in df.columns:
        df["high_low_range"] = (high - low) / close
    if "close_open_ret" not in df.columns:
        df["close_open_ret"] = (close - open_) / open_

    # trend_regime (3 buckets)
    if "trend_regime" not in df.columns:
        trend_ref = close.pct_change(7 * 24 * 60)
        try:
            df["trend_regime"] = pd.qcut(trend_ref, q=3, labels=False, duplicates="drop")
        except Exception:
            df["trend_regime"] = np.nan
        df["trend_regime"] = pd.to_numeric(df["trend_regime"], errors="coerce")

    # vol_regime (3 buckets)
    if "vol_regime" not in df.columns:
        vol_ref = df["rv_60"].rolling(30 * 24 * 60, min_periods=30 * 24 * 60).mean()
        try:
            df["vol_regime"] = pd.qcut(vol_ref, q=3, labels=False, duplicates="drop")
        except Exception:
            df["vol_regime"] = np.nan
        df["vol_regime"] = pd.to_numeric(df["vol_regime"], errors="coerce")

    # month cyclical
    month = df.index.month.astype(np.int32)
    df["month_sin"] = np.sin(2.0 * np.pi * month / 12.0)
    df["month_cos"] = np.cos(2.0 * np.pi * month / 12.0)

    # Causal shift: shift ONCE all features except OHLCV base
    shift_cols = [c for c in MODEL_39_FEATURES if c not in {"open", "high", "low", "close", "volume"}]
    df[shift_cols] = df[shift_cols].shift(1)

    df = df.replace([np.inf, -np.inf], np.nan)

    missing = [c for c in MODEL_39_FEATURES if c not in df.columns]
    if missing:
        raise ValueError(f"Cannot build model 39 features, missing: {missing}")

    return df[MODEL_39_FEATURES].copy()


# =============================================================================
# LOADER
# =============================================================================

class S3MarketDataLoader:
    """
    Load processed market data from S3.

    Data location:
    s3://{bucket}/{base_path}/interval={interval}/quote={quote}/symbol={SYMBOL}/year={YEAR}/*.parquet
    """

    def __init__(
        self,
        bucket: str = "qbia",
        base_path: str = "bourse/processed/market_v2",  # Updated to market_v2
        interval: str = "1m",
        quote: str = "USDT",
    ):
        self.bucket = bucket
        self.base_path = base_path
        self.interval = interval
        self.quote = quote
        self.fs = s3fs.S3FileSystem(anon=False)

    # ------------------------------------------------------------------
    # RAW OHLCV
    # ------------------------------------------------------------------

    def load_raw_ohlcv(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        required_cols = ["datetime", "open_time", "open", "high", "low", "close", "volume"]

        df_full = self._load_internal(symbol, start_date, end_date, columns=required_cols)
        if df_full.empty:
            return pd.DataFrame()

        df_full = normalize_columns(df_full)
        df_full = _ensure_dt_index(df_full)

        raw_cols = ["open", "high", "low", "close", "volume"]
        missing = [c for c in raw_cols if c not in df_full.columns]
        if missing:
            raise ValueError(f"Missing OHLCV columns after normalization: {missing}")

        df_raw = df_full[raw_cols].copy()

        logger.info(
            {
                "msg": "RAW_OHLCV_LOADED",
                "symbol": symbol,
                "rows": int(len(df_raw)),
                "start": str(df_raw.index.min()),
                "end": str(df_raw.index.max()),
            }
        )
        return df_raw

    # ------------------------------------------------------------------
    # PROCESSED FEATURES (MODEL 39)
    # ------------------------------------------------------------------

    def load_processed_features(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        strict_model_features: bool = True,
        print_columns: bool = True,
    ) -> pd.DataFrame:
        """
        Loads processed parquets and returns the model 39 feature columns (datetime index).
        Builds missing features from OHLCV if needed.
        """

        start = pd.to_datetime(start_date, utc=True)
        end = pd.to_datetime(end_date, utc=True)

        years = list(range(start.year, end.year + 1))
        dfs = []

        # NOTE: processed => NO projection (prevents pyarrow "Open" mismatch warnings)
        for year in years:
            year_df = self._load_year(symbol, year, columns=None)
            if year_df is not None and not year_df.empty:
                dfs.append(year_df)

        if not dfs:
            logger.error({"msg": "NO_DATA_LOADED_PROCESSED", "symbol": symbol, "years": years})
            return pd.DataFrame()

        df_full = pd.concat(dfs, ignore_index=True)

        if print_columns:
            _print_loaded_columns(df_full, "COLUMNS LOADED FROM S3 (PROCESSED PARQUETS)")

        # Build model feature set
        df_model = _build_model_39_from_processed(df_full)

        # Filter range
        df_model = df_model.loc[(df_model.index >= start) & (df_model.index <= end)].copy()

        if print_columns:
            _print_required_columns_status(df_model, MODEL_39_FEATURES)

        if strict_model_features:
            missing = [c for c in MODEL_39_FEATURES if c not in df_model.columns]
            if missing:
                raise ValueError(f"Missing required model columns: {missing}")

        return df_model

    # ------------------------------------------------------------------
    # Internal S3 loading
    # ------------------------------------------------------------------

    def _load_internal(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        columns: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        start = pd.to_datetime(start_date, utc=True)
        end = pd.to_datetime(end_date, utc=True)

        years = list(range(start.year, end.year + 1))
        dfs = []

        for year in years:
            year_df = self._load_year(symbol, year, columns=columns)
            if year_df is not None and not year_df.empty:
                dfs.append(year_df)

        if not dfs:
            logger.error({"msg": "NO_DATA_LOADED", "symbol": symbol, "years": years})
            return pd.DataFrame()

        df = pd.concat(dfs, ignore_index=True)
        df = normalize_columns(df)

        # Ensure datetime for filtering
        if "datetime" not in df.columns:
            if "open_time" in df.columns:
                df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True, errors="coerce")
            elif "close_time" in df.columns:
                df["datetime"] = pd.to_datetime(df["close_time"], unit="ms", utc=True, errors="coerce")
            else:
                raise ValueError("No datetime/open_time/close_time in loaded dataset")

        df = df.dropna(subset=["datetime"])
        mask = (df["datetime"] >= start) & (df["datetime"] <= end)
        df = df.loc[mask].copy()
        df = df.sort_values("datetime").reset_index(drop=True)

        return df

    def _load_year(
        self,
        symbol: str,
        year: int,
        columns: Optional[List[str]] = None,
    ) -> Optional[pd.DataFrame]:
        pattern = (
            f"{self.bucket}/{self.base_path}/"
            f"interval={self.interval}/"
            f"quote={self.quote}/"
            f"symbol={symbol}/"
            f"year={year}/*.parquet"
        )

        files = self.fs.glob(pattern)
        if not files:
            return None

        s3_files = [f"s3://{f}" for f in files]

        dfs = []
        for s3_file in s3_files:
            try:
                if columns is None:
                    part = pd.read_parquet(s3_file, filesystem=self.fs)
                else:
                    part = pd.read_parquet(s3_file, filesystem=self.fs, columns=columns)
                dfs.append(part)
            except Exception as e:
                logger.warning({"msg": "PARQUET_READ_FAILED", "file": s3_file, "err": str(e)})

        if not dfs:
            return None

        return pd.concat(dfs, ignore_index=True)
