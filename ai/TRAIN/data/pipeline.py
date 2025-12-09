"""
Data pipeline with real OHLCV ingestion (ccxt) and synthetic fallback.
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, TensorDataset

from data_pipeline.data_sources import CcxtDataSource
from data_pipeline.feature_engineering import build_feature_set
from data_pipeline.utils import ohlcv_to_df

from .alternative_sources import (
    CoinglassClient,
    CryptoPanicScraper,
    DuneAnalyticsFetcher,
    EtherscanScraper,
    MemPoolSpaceFetcher,
    RedditScraper,
    TwitterSentiment,
    WhaleAlertStream,
)

logger = logging.getLogger(__name__)


class DataPipeline:
    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config
        self.train_split = float(config.get("train_split", 0.7))
        self.val_split = float(config.get("val_split", 0.15))
        self.test_split = float(config.get("test_split", 0.15))
        self.lookback = int(config.get("lookback_window", 100))
        self.feature_dim = int(config.get("feature_dim", 128))
        self.batch_size = int(config.get("batch_size", 32))
        self.symbols = config.get("symbols", ["BTC/USDT"])
        self.timeframe = config.get("timeframe", "1h")
        self.history_days = int(config.get("history_days", 180))
        self.shuffle = bool(config.get("shuffle", True))
        self.use_synthetic = bool(config.get("use_synthetic_data", False))

    def _build_synthetic_dataset(self, n_samples: int) -> TensorDataset:
        """
        Build a synthetic sequence dataset shaped (batch, lookback, feature_dim).
        """
        x = torch.randn(n_samples, self.lookback, self.feature_dim)
        weights = torch.randn(self.lookback, self.feature_dim)
        # Collapse lookback and feature dims to derive a target
        y = (x * weights).sum(dim=(1, 2)) + 0.1 * torch.randn(n_samples)
        return TensorDataset(x, y)

    def _get_synthetic_loaders(self) -> Tuple[DataLoader, DataLoader, DataLoader]:
        n_total = max(int(self.lookback * 10), 1000)
        dataset = self._build_synthetic_dataset(n_total)

        n_train = int(self.train_split * n_total)
        n_val = int(self.val_split * n_total)

        train_ds, val_ds, test_ds = torch.utils.data.random_split(
            dataset,
            [n_train, n_val, n_total - n_train - n_val],
            generator=torch.Generator().manual_seed(42),
        )

        def _loader(ds, shuffle: bool = False):
            return DataLoader(ds, batch_size=self.batch_size, shuffle=shuffle)

        return _loader(train_ds, shuffle=self.shuffle), _loader(val_ds), _loader(test_ds)

    def _fetch_ohlcv(self) -> pd.DataFrame:
        source = CcxtDataSource()
        end = datetime.utcnow()
        start = end - timedelta(days=self.history_days)
        frames: List[pd.DataFrame] = []
        for sym in self.symbols:
            try:
                raw = source.fetch_historical_range(sym, timeframe=self.timeframe, start=start, end=end)
                df = ohlcv_to_df(raw)
                df["symbol"] = sym
                frames.append(df)
            except Exception as exc:
                logger.warning("Failed to fetch %s: %s", sym, exc)
        if not frames:
            return pd.DataFrame()
        full = pd.concat(frames).sort_values("timestamp")
        return full

    def _build_feature_dataset(self, df: pd.DataFrame) -> TensorDataset:
        logger.info(
            "Building features from raw data: shape=%s, columns=%s, head=%s",
            df.shape,
            list(df.columns),
            df.head(3).to_dict(orient="list"),
        )
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        if "symbol" not in df.columns:
            df["symbol"] = "DEFAULT"

        symbols = sorted(df["symbol"].unique())
        logger.info("Processing %d symbols: %s", len(symbols), symbols)

        X_batches: list[np.ndarray] = []
        y_batches: list[float] = []
        feature_dim: Optional[int] = None

        for sym_id, sym in enumerate(symbols):
            sym_df = df[df["symbol"] == sym].copy()
            sym_df = sym_df.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp")

            # Build features for this symbol only to avoid index cross-products
            sym_feats = build_feature_set(sym_df, drop_na=True)
            if "timestamp" in sym_feats.columns:
                sym_feats = sym_feats.copy()
                sym_feats["timestamp"] = pd.to_datetime(sym_feats["timestamp"], utc=True)
                sym_feats.set_index("timestamp", inplace=True)

            # Align target on this symbol's close
            prices = sym_df.set_index("timestamp")["close"]
            returns = prices.pct_change().shift(-1)

            # Keep numeric features and append symbol identifier
            sym_numeric = sym_feats.select_dtypes(include="number").copy()
            sym_numeric["symbol_id"] = float(sym_id)
            sym_numeric["target"] = returns.reindex(sym_numeric.index)
            sym_numeric.dropna(inplace=True)

            if sym_numeric.empty:
                logger.warning("Symbol %s produced no rows after dropna; skipping.", sym)
                continue

            values = sym_numeric.values
            current_dim = values.shape[1] - 1  # exclude target
            if feature_dim is None:
                feature_dim = current_dim
            elif current_dim != feature_dim:
                raise ValueError(f"Feature dimension mismatch across symbols ({current_dim} vs {feature_dim}).")

            for i in range(len(values) - self.lookback):
                X_batches.append(values[i : i + self.lookback, :-1])
                y_batches.append(values[i + self.lookback - 1, -1])

            logger.info(
                "Symbol %s: feats=%s target_len=%s seq=%s",
                sym,
                sym_numeric.shape,
                len(sym_numeric),
                max(len(values) - self.lookback, 0),
            )

        if not X_batches:
            raise ValueError("Not enough data to build sequences for any symbol.")

        self.feature_dim = feature_dim or self.feature_dim
        X_tensor = torch.tensor(np.stack(X_batches), dtype=torch.float32)
        y_tensor = torch.tensor(np.array(y_batches), dtype=torch.float32)
        return TensorDataset(X_tensor, y_tensor)

    def _split_dataset(self, dataset: TensorDataset) -> Tuple[DataLoader, DataLoader, DataLoader]:
        n_total = len(dataset)
        n_train = int(self.train_split * n_total)
        n_val = int(self.val_split * n_total)

        lengths = [n_train, n_val, n_total - n_train - n_val]
        train_ds, val_ds, test_ds = torch.utils.data.random_split(
            dataset, lengths, generator=torch.Generator().manual_seed(42)
        )

        def _loader(ds, shuffle: bool = False):
            return DataLoader(ds, batch_size=self.batch_size, shuffle=shuffle)

        return _loader(train_ds, shuffle=self.shuffle), _loader(val_ds), _loader(test_ds)

    def get_data_loaders(self) -> Tuple[DataLoader, DataLoader, DataLoader]:
        if self.use_synthetic:
            return self._get_synthetic_loaders()
        try:
            raw_df = self._fetch_ohlcv()
            logger.info(
                "Fetched OHLCV: shape=%s, columns=%s, head=%s",
                raw_df.shape,
                list(raw_df.columns),
                raw_df.head(3).to_dict(orient="list"),
            )
            if raw_df.empty:
                logger.warning("No OHLCV fetched, falling back to synthetic data.")
                return self._get_synthetic_loaders()
            dataset = self._build_feature_dataset(raw_df)
            return self._split_dataset(dataset)
        except Exception as exc:
            logger.warning("Data pipeline failed (%s), using synthetic loaders.", exc)
            return self._get_synthetic_loaders()


class EnhancedDataPipeline(DataPipeline):
    """
    Extended pipeline that registers multiple data sources.
    """

    def __init__(self, config: Dict[str, Any], enable_alternative: bool = False) -> None:
        super().__init__(config)
        self.enable_alternative = enable_alternative
        self.sources = {
            "standard": [
                "binance",
                "kraken",
                "coinbase",
                CoinglassClient(),
            ],
            "alternative": [
                CryptoPanicScraper(),
                WhaleAlertStream(),
                TwitterSentiment(["elonmusk", "cz_binance"]),
                RedditScraper("cryptocurrency"),
            ],
            "onchain_deep": [
                DuneAnalyticsFetcher(),
                EtherscanScraper(),
                MemPoolSpaceFetcher(),
            ],
        }

    def fetch_all_sources(self) -> Dict[str, Any]:
        datasets: Dict[str, Any] = {"standard": [], "alternative": [], "onchain_deep": []}

        for src in self.sources.get("standard", []):
            if hasattr(src, "fetch"):
                datasets["standard"].append(src.fetch())

        if self.enable_alternative:
            for src in self.sources.get("alternative", []):
                if hasattr(src, "fetch"):
                    datasets["alternative"].append(src.fetch())
            for src in self.sources.get("onchain_deep", []):
                if hasattr(src, "fetch"):
                    datasets["onchain_deep"].append(src.fetch())

        return datasets

    def resolve_data_conflicts(self, datasets: Dict[str, Any]) -> Dict[str, Any]:
        priority_map = {
            "binance": 1.0,
            "kraken": 0.9,
            "coinbase": 0.8,
            "coinglass": 0.8,
            "twitter_sentiment": 0.3,
            "dune_analytics": 0.8,
            "reddit_scraper": 0.2,
        }

        resolved: Dict[str, Any] = {}
        for key, frames in datasets.items():
            resolved[key] = next((f for f in frames if getattr(f, "empty", False) is False), None)
        logger.info("Conflict resolution placeholder applied with priorities: %s", priority_map)
        return resolved

    def get_data_loaders(self) -> Tuple[DataLoader, DataLoader, DataLoader]:
        _ = self.fetch_all_sources()
        if self.use_synthetic:
            return self._get_synthetic_loaders()

        if not self.enable_alternative:
            logger.info("Using standard pipeline.")
            return super().get_data_loaders()

        logger.info("Using enhanced pipeline with alternative sources.")
        return super().get_data_loaders()
