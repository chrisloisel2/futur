"""
Data collection orchestration for multi-modal trading datasets.

This script pulls market, on-chain, macro, and alternative data with
resume support, quality gates, and versioned outputs in Parquet format.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import ccxt
import pandas as pd
import yfinance as yf
import yaml

from .cache import RedisCache
from .data_quality import DataQualityValidator
from .data_sources import CcxtDataSource, GlassnodeClient, ohlcv_to_df

try:  # Optional, only used if available
    from statsmodels.tsa.stattools import adfuller
except Exception:  # pragma: no cover - optional dependency
    adfuller = None


logger = logging.getLogger(__name__)


def _timeframe_to_timedelta(timeframe: str) -> pd.Timedelta:
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    mapping = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    if unit not in mapping:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return pd.Timedelta(**{mapping[unit]: value})


def _slug_symbol(symbol: str) -> str:
    return symbol.replace("/", "-").replace(":", "-").lower()


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            merged[key] = _deep_merge(base[key], val)
        else:
            merged[key] = val
    return merged


@dataclass
class Artifact:
    category: str
    path: str
    rows: int
    meta: Dict[str, Any] = field(default_factory=dict)


class ProgressTracker:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception as exc:
                logger.warning("Progress file unreadable (%s), starting fresh.", exc)
        return {}

    def save(self) -> None:
        self.path.write_text(json.dumps(self.state, indent=2, default=str))

    def last_timestamp(self, category: str, key: str) -> Optional[pd.Timestamp]:
        ts = self.state.get(category, {}).get(key, {}).get("last_timestamp")
        if not ts:
            return None
        stamp = pd.Timestamp(ts)
        return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")

    def update(self, category: str, key: str, last_ts: pd.Timestamp, rows: int) -> None:
        self.state.setdefault(category, {})[key] = {
            "last_timestamp": last_ts.isoformat(),
            "rows": int(rows),
        }
        self.save()


class StationarityChecker:
    @staticmethod
    def evaluate(series: pd.Series) -> Dict[str, Any]:
        clean = series.dropna()
        if len(clean) < 50:
            return {"is_stationary": False, "reason": "insufficient_length"}

        try:
            if adfuller:
                pvalue = float(adfuller(clean, autolag="AIC")[1])
                return {"is_stationary": pvalue < 0.05, "adf_pvalue": pvalue}
        except Exception as exc:
            logger.debug("ADF test failed: %s", exc)

        diff_ratio = clean.diff().std() / (clean.std() + 1e-9)
        return {"is_stationary": bool(diff_ratio < 0.8), "diff_ratio": float(diff_ratio)}


DEFAULT_COLLECTION_CONFIG: Dict[str, Any] = {
    "data_root": "datasets/trading",
    "snapshot_tag": None,
    "resume": True,
    "price": {
        "exchanges": ["binance", "kraken", "coinbase"],
        "symbols": [
            "BTC/USDT",
            "ETH/USDT",
            "SOL/USDT",
            "BNB/USDT",
            "XRP/USDT",
            "DOGE/USDT",
            "ADA/USDT",
            "LINK/USDT",
            "AVAX/USDT",
            "MATIC/USDT",
        ],
        "timeframes": ["1m", "5m", "1h", "1d"],
        "history_years": 8,
        "max_limit": 1000,
    },
    "onchain": {
        "assets": ["BTC", "ETH"],
        "metrics": [
            "addresses/active_count",
            "transactions/transfers_volume_sum",
            "fees/mean",
            "supply/current",
        ],
        "interval": "24h",
        "history_days": 365 * 5,
    },
    "macro": {
        "tickers": [
            "^GSPC",
            "^NDX",
            "^VIX",
            "DX-Y.NYB",
            "GC=F",
            "CL=F",
            "EURUSD=X",
            "JPY=X",
        ],
        "interval": "1d",
        "history_years": 10,
    },
    "alternative": {
        "enable": True,
        "coinglass": True,
        "cryptopanic": True,
    },
}


class TradingDataCollector:
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        cache: Optional[RedisCache] = None,
        resume: Optional[bool] = None,
    ) -> None:
        merged = _deep_merge(DEFAULT_COLLECTION_CONFIG, config or {})
        if resume is not None:
            merged["resume"] = resume

        self.config = merged
        self.data_root = Path(self.config["data_root"]).expanduser()
        self.run_id = self.config.get("snapshot_tag") or datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        self.raw_dir = self.data_root / "raw" / self.run_id
        self.processed_dir = self.data_root / "processed" / self.run_id
        self.features_dir = self.data_root / "features" / self.run_id
        self.metadata_dir = self.data_root / "metadata"
        self.validation_dir = self.data_root / "validation"

        for d in [self.raw_dir, self.processed_dir, self.features_dir, self.metadata_dir, self.validation_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self.progress = ProgressTracker(self.metadata_dir / "progress.json")
        self.validator = DataQualityValidator()
        self.cache = cache or RedisCache(ttl_seconds=900)
        self.glassnode = GlassnodeClient(cache=self.cache)

    # Public API ---------------------------------------------------------
    def collect(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": datetime.utcnow().isoformat(),
            "artifacts": [],
        }

        summary["artifacts"].extend(self._collect_prices())
        summary["artifacts"].extend(self._collect_onchain())
        summary["artifacts"].extend(self._collect_macro())
        summary["artifacts"].extend(self._collect_alternative())

        summary["completed_at"] = datetime.utcnow().isoformat()
        self._write_run_metadata(summary)
        return summary

    # Internal helpers ---------------------------------------------------
    def _collect_prices(self) -> List[Artifact]:
        cfg = self.config["price"]
        start = datetime.utcnow() - timedelta(days=365 * cfg.get("history_years", 5))
        end = datetime.utcnow()
        artifacts: List[Artifact] = []

        for exchange_id in cfg.get("exchanges", []):
            try:
                exchange_cls = getattr(ccxt, exchange_id)
                exchange = exchange_cls({"enableRateLimit": True})
                exchange.load_markets()
            except Exception as exc:
                logger.warning("Exchange %s init failed: %s", exchange_id, exc)
                continue

            source = CcxtDataSource(exchange=exchange, cache=self.cache, default_limit=cfg.get("max_limit", 1000))

            for symbol in cfg.get("symbols", []):
                for timeframe in cfg.get("timeframes", []):
                    key = f"{exchange_id}:{symbol}:{timeframe}"
                    tf_delta = _timeframe_to_timedelta(timeframe)
                    start_dt = start

                    if self.config.get("resume"):
                        last_ts = self.progress.last_timestamp("price", key)
                        if last_ts is not None:
                            start_dt = max(start_dt, (last_ts + tf_delta).to_pydatetime())

                    try:
                        raw = source.fetch_historical_range(symbol, timeframe=timeframe, start=start_dt, end=end)
                    except Exception as exc:
                        logger.warning("Price fetch failed for %s: %s", key, exc)
                        continue

                    if not raw:
                        continue

                    df = ohlcv_to_df(raw)
                    df["exchange"] = exchange_id
                    df["symbol"] = symbol
                    df["timeframe"] = timeframe
                    df.drop_duplicates(subset="timestamp", inplace=True)
                    df.sort_values("timestamp", inplace=True)

                    quality = self.validator.validate(df, timeframe=timeframe)
                    stationarity = StationarityChecker.evaluate(df["close"])
                    artifact_path = self._write_partition(
                        df,
                        category="prices",
                        path_parts=[exchange_id, _slug_symbol(symbol), timeframe],
                    )

                    self.progress.update("price", key, df["timestamp"].iloc[-1], len(df))
                    self._write_validation_report(
                        category="price",
                        key=key,
                        quality=quality,
                        stationarity=stationarity,
                        path_hint=artifact_path,
                    )

                    artifacts.append(
                        Artifact(
                            category="price",
                            path=str(artifact_path),
                            rows=len(df),
                            meta={
                                "exchange": exchange_id,
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "quality": quality.__dict__,
                                "stationarity": stationarity,
                            },
                        )
                    )

        return artifacts

    def _collect_onchain(self) -> List[Artifact]:
        cfg = self.config.get("onchain", {})
        artifacts: List[Artifact] = []
        if not cfg.get("assets"):
            return artifacts

        base_start = datetime.utcnow() - timedelta(days=int(cfg.get("history_days", 365)))
        end = datetime.utcnow()
        interval = cfg.get("interval", "24h")
        interval_delta = None
        try:
            interval_delta = _timeframe_to_timedelta(interval)
        except Exception:
            interval_delta = None

        for asset in cfg.get("assets", []):
            for metric in cfg.get("metrics", []):
                key = f"{asset}:{metric}:{interval}"
                start = base_start
                if self.config.get("resume") and interval_delta:
                    last_ts = self.progress.last_timestamp("onchain", key)
                    if last_ts is not None:
                        start = max(start, (last_ts + interval_delta).to_pydatetime())

                params = {"i": interval, "s": int(start.timestamp()), "u": int(end.timestamp())}
                try:
                    raw = self.glassnode.fetch_metric(endpoint=metric, asset=asset, params=params)
                    df = self.glassnode.to_df(raw)
                    df["asset"] = asset
                    df["metric"] = metric
                except Exception as exc:
                    logger.warning("On-chain fetch failed for %s: %s", key, exc)
                    continue

                df.drop_duplicates(subset="timestamp", inplace=True)
                df.sort_values("timestamp", inplace=True)

                quality = self.validator.validate(df, timeframe=interval if interval.endswith("h") else None)
                numeric_cols = [c for c in df.select_dtypes(include="number").columns if c != "timestamp"]
                stationarity = (
                    StationarityChecker.evaluate(df[numeric_cols[0]]) if numeric_cols else {"is_stationary": False, "reason": "no_numeric"}
                )

                artifact_path = self._write_partition(
                    df,
                    category="onchain",
                    path_parts=[asset.lower(), metric.replace("/", "-")],
                )
                self._write_validation_report("onchain", key, quality, stationarity, artifact_path)
                self.progress.update("onchain", key, df["timestamp"].iloc[-1], len(df))

                artifacts.append(
                    Artifact(
                        category="onchain",
                        path=str(artifact_path),
                        rows=len(df),
                        meta={
                            "asset": asset,
                            "metric": metric,
                            "stationarity": stationarity,
                            "quality": quality.__dict__,
                        },
                    )
                )
        return artifacts

    def _collect_macro(self) -> List[Artifact]:
        cfg = self.config.get("macro", {})
        artifacts: List[Artifact] = []
        if not cfg.get("tickers"):
            return artifacts

        base_start = datetime.utcnow() - timedelta(days=365 * int(cfg.get("history_years", 8)))
        end = datetime.utcnow()
        interval = cfg.get("interval", "1d")
        interval_delta = None
        try:
            interval_delta = _timeframe_to_timedelta(interval)
        except Exception:
            interval_delta = None

        for ticker in cfg.get("tickers", []):
            start = base_start
            if self.config.get("resume") and interval_delta:
                last_ts = self.progress.last_timestamp("macro", ticker)
                if last_ts is not None:
                    start = max(start, (last_ts + interval_delta).to_pydatetime())

            try:
                df = yf.download(
                    ticker,
                    start=start,
                    end=end,
                    interval=interval,
                    progress=False,
                    threads=False,
                )
            except Exception as exc:
                logger.warning("Macro fetch failed for %s: %s", ticker, exc)
                continue

            if df.empty:
                continue

            df = df.reset_index().rename(columns=str.lower)
            if "datetime" in df.columns and "timestamp" not in df.columns:
                df.rename(columns={"datetime": "timestamp"}, inplace=True)
            elif "date" in df.columns and "timestamp" not in df.columns:
                df.rename(columns={"date": "timestamp"}, inplace=True)

            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df["ticker"] = ticker
            df.drop_duplicates(subset="timestamp", inplace=True)
            df.sort_values("timestamp", inplace=True)

            quality = self.validator.validate(df, timeframe=interval if interval.endswith("d") else None)
            stationarity = StationarityChecker.evaluate(df.get("close", pd.Series(dtype=float)))

            artifact_path = self._write_partition(
                df,
                category="macro",
                path_parts=[_slug_symbol(ticker)],
            )
            self._write_validation_report("macro", ticker, quality, stationarity, artifact_path)
            self.progress.update("macro", ticker, df["timestamp"].iloc[-1], len(df))

            artifacts.append(
                Artifact(
                    category="macro",
                    path=str(artifact_path),
                    rows=len(df),
                    meta={"ticker": ticker, "quality": quality.__dict__, "stationarity": stationarity},
                )
            )

        return artifacts

    def _collect_alternative(self) -> List[Artifact]:
        from data.alternative_sources import CoinglassClient, CryptoPanicScraper

        cfg = self.config.get("alternative", {})
        artifacts: List[Artifact] = []
        if not cfg.get("enable", True):
            return artifacts

        if cfg.get("coinglass"):
            client = CoinglassClient()
            df = client.fetch_futures_data(symbols=self.config["price"].get("symbols"))
            if df is not None and not df.empty:
                if "timestamp" in df.columns:
                    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                artifact_path = self._write_partition(df, category="alternative", path_parts=["coinglass"])
                artifacts.append(Artifact(category="alternative", path=str(artifact_path), rows=len(df), meta={"source": "coinglass"}))

        if cfg.get("cryptopanic"):
            scraper = CryptoPanicScraper()
            df = scraper.fetch_news_sentiment(limit=200)
            if df is not None and not df.empty:
                artifact_path = self._write_partition(df, category="alternative", path_parts=["cryptopanic_news"])
                artifacts.append(Artifact(category="alternative", path=str(artifact_path), rows=len(df), meta={"source": "cryptopanic"}))

        return artifacts

    def _write_partition(self, df: pd.DataFrame, category: str, path_parts: Iterable[str]) -> Path:
        target_dir = self.raw_dir / category
        for part in path_parts:
            target_dir = target_dir / str(part)
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / "data.parquet"
        df.to_parquet(path, index=False)
        return path

    def _write_validation_report(
        self,
        category: str,
        key: str,
        quality: Any,
        stationarity: Dict[str, Any],
        path_hint: Path,
    ) -> None:
        payload = {
            "category": category,
            "key": key,
            "artifact": str(path_hint),
            "quality": quality.__dict__ if hasattr(quality, "__dict__") else quality,
            "stationarity": stationarity,
        }
        slug_key = key.replace(":", "-").replace("/", "-")
        out_path = self.validation_dir / f"{category}_{slug_key}.json"
        out_path.write_text(json.dumps(payload, indent=2, default=str))

    def _write_run_metadata(self, summary: Dict[str, Any]) -> None:
        run_path = self.metadata_dir / f"run_{self.run_id}.json"
        run_path.write_text(json.dumps(summary, indent=2, default=str))
        latest_path = self.metadata_dir / "latest.json"
        latest_path.write_text(json.dumps({"latest_run": self.run_id}, indent=2))


def _load_config(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return yaml.safe_load(cfg_path.read_text()) or {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect multi-modal trading datasets with resume + QC.")
    parser.add_argument("--config", type=str, help="YAML config path for collection settings", default=None)
    parser.add_argument("--resume", action="store_true", help="Resume from previous progress file")
    parser.add_argument("--log_level", type=str, default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(message)s")

    user_config = _load_config(args.config)
    collector = TradingDataCollector(config=user_config, resume=args.resume)
    summary = collector.collect()
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
