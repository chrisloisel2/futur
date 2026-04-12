"""
Placeholders for alternative data sources. Replace with real implementations that
respect provider TOS, robots.txt, and rate limits.
"""
import logging
import os
from typing import Any, Dict, List, Optional

import requests

import pandas as pd

logger = logging.getLogger(__name__)


class BaseFetcher:
    """Base fetcher stub."""

    source_name: str = "base"

    def fetch(self, *args, **kwargs) -> pd.DataFrame:
        logger.warning("Fetcher %s is a stub. Replace with real implementation.", self.source_name)
        return pd.DataFrame()

    def validate_source_quality(self) -> Dict[str, Any]:
        """Return placeholder quality metrics."""
        return {
            "coverage": 0.0,
            "latency_ms": None,
            "freshness_s": None,
            "reliability": 0.0,
        }


class CoinglassClient(BaseFetcher):
    source_name = "coinglass"

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://open-api.coinglass.com/api") -> None:
        super().__init__()
        self.api_key = api_key or os.getenv("COINGLASS_API_KEY")
        self.base_url = base_url.rstrip("/")

    def fetch_futures_data(self, symbols: Optional[List[str]] = None) -> pd.DataFrame:
        """
        Fetch futures data (open interest / funding) using the official Coinglass API.
        If API key is missing, returns an empty DataFrame.
        """
        if not self.api_key:
            logger.warning("COINGLASS_API_KEY not set, skipping Coinglass fetch.")
            return pd.DataFrame()

        symbols = symbols or []
        headers = {"coinglassSecret": self.api_key}
        params = {"currency": ",".join(symbols)} if symbols else {}
        url = f"{self.base_url}/futures"
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and "data" in data:
                rows = data["data"]
            else:
                rows = data
            df = pd.DataFrame(rows)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            return df
        except Exception as exc:
            logger.warning("Coinglass fetch failed: %s", exc)
            return pd.DataFrame()


class CryptoPanicScraper(BaseFetcher):
    source_name = "crypto_panic"

    def __init__(self, api_key: Optional[str] = None, base_url: str = "https://cryptopanic.com/api/v1") -> None:
        super().__init__()
        self.api_key = api_key or os.getenv("CRYPTOPANIC_API_KEY")
        self.base_url = base_url.rstrip("/")

    def fetch_news_sentiment(self, limit: int = 100) -> pd.DataFrame:
        """
        Fetch news and sentiment from CryptoPanic API.
        """
        if not self.api_key:
            logger.warning("CRYPTOPANIC_API_KEY not set, skipping CryptoPanic fetch.")
            return pd.DataFrame()

        url = f"{self.base_url}/posts/"
        params = {"auth_token": self.api_key, "kind": "news", "filter": "rising", "public": "true", "currencies": "BTC,ETH", "limit": limit}
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", []) if isinstance(data, dict) else data
            df = pd.DataFrame(results)
            if not df.empty and "published_at" in df.columns:
                df["timestamp"] = pd.to_datetime(df["published_at"], utc=True)
            return df
        except Exception as exc:
            logger.warning("CryptoPanic fetch failed: %s", exc)
            return pd.DataFrame()


class WhaleAlertStream(BaseFetcher):
    source_name = "whale_alert"


class TwitterSentiment(BaseFetcher):
    source_name = "twitter_sentiment"

    def __init__(self, handles: Optional[List[str]] = None):
        self.handles = handles or []


class RedditScraper(BaseFetcher):
    source_name = "reddit_scraper"

    def __init__(self, subreddit: str):
        self.subreddit = subreddit


class DuneAnalyticsFetcher(BaseFetcher):
    source_name = "dune_analytics"


class EtherscanScraper(BaseFetcher):
    source_name = "etherscan"


class MemPoolSpaceFetcher(BaseFetcher):
    source_name = "mempool_space"
