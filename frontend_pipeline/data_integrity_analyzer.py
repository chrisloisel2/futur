"""
Data Integrity Analyzer
Analyse la complétude et l'intégrité des données par crypto
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DataIntegrityAnalyzer:
    """Analyse l'intégrité des données crypto"""

    def __init__(self, s3_cache_path: str = None):
        if s3_cache_path is None:
            # Try to find the cache path
            possible_paths = [
                Path(__file__).parent.parent / "ai" / "cache" / "s3_data",
                Path("ai/cache/s3_data"),
                Path("../ai/cache/s3_data")
            ]
            for p in possible_paths:
                if p.exists():
                    s3_cache_path = str(p)
                    break
            if s3_cache_path is None:
                s3_cache_path = str(possible_paths[0])

        self.s3_cache_path = Path(s3_cache_path)
        self.supported_cryptos = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'MATIC']

    def get_available_cryptos(self) -> List[str]:
        """Liste les cryptos disponibles dans le cache S3"""
        available = []
        for crypto in self.supported_cryptos:
            pattern = f"{crypto}USDT_*.parquet"
            files = list(self.s3_cache_path.glob(pattern))
            if files:
                available.append(crypto)
        return available

    def analyze_crypto_data(self, crypto: str) -> Dict:
        """Analyse complète d'une crypto"""
        logger.info(f"Analyzing {crypto}...")

        pattern = f"{crypto}USDT_*.parquet"
        files = list(self.s3_cache_path.glob(pattern))

        if not files:
            return {
                "crypto": crypto,
                "status": "no_data",
                "message": f"No data files found for {crypto}"
            }

        # Charger tous les fichiers
        dfs = []
        years_data = {}

        for file in sorted(files):
            try:
                df = pd.read_parquet(file)
                year = file.stem.split('_')[1]  # BTCUSDT_2024_1m -> 2024
                years_data[year] = {
                    "file": file.name,
                    "rows": len(df),
                    "size_mb": file.stat().st_size / (1024 * 1024)
                }
                dfs.append(df)
            except Exception as e:
                logger.error(f"Error loading {file}: {e}")

        if not dfs:
            return {
                "crypto": crypto,
                "status": "error",
                "message": "Failed to load data files"
            }

        # Combiner toutes les données
        full_df = pd.concat(dfs, ignore_index=True)

        # IMPORTANT: Filtrer les données futures
        if 'timestamp' in full_df.columns:
            full_df['timestamp'] = pd.to_datetime(full_df['timestamp'])
            now = pd.Timestamp.now(tz='UTC')
            full_df = full_df[full_df['timestamp'] <= now]
            logger.info(f"Filtered to {len(full_df)} rows (removed future dates)")

        # Trier par timestamp
        if 'timestamp' in full_df.columns:
            full_df = full_df.sort_values('timestamp')

        # Analyser la complétude
        integrity = self._analyze_integrity(full_df, crypto)

        # Calculer les statistiques
        stats = self._calculate_stats(full_df, crypto)

        # Analyser les gaps temporels
        gaps = self._analyze_time_gaps(full_df)

        # Analyser les indicateurs techniques (si présents)
        indicators = self._analyze_indicators(full_df)

        # Analyser les métadonnées scrapées (si présentes)
        metadata = self._analyze_metadata(full_df)

        return {
            "crypto": crypto,
            "status": "ok",
            "years": years_data,
            "total_rows": len(full_df),
            "integrity": integrity,
            "stats": stats,
            "gaps": gaps,
            "indicators": indicators,
            "metadata": metadata,
            "date_range": {
                "start": str(full_df['timestamp'].min()) if 'timestamp' in full_df.columns else None,
                "end": str(full_df['timestamp'].max()) if 'timestamp' in full_df.columns else None
            }
        }

    def _analyze_integrity(self, df: pd.DataFrame, crypto: str) -> Dict:
        """Analyse l'intégrité des colonnes essentielles"""
        essential_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']

        integrity = {
            "total_rows": len(df),
            "columns": {}
        }

        for col in essential_columns:
            if col in df.columns:
                missing = df[col].isna().sum()
                missing_pct = (missing / len(df)) * 100

                # Détecter les valeurs aberrantes
                if col in ['open', 'high', 'low', 'close']:
                    outliers = 0
                    if len(df) > 0:
                        q1 = df[col].quantile(0.25)
                        q3 = df[col].quantile(0.75)
                        iqr = q3 - q1
                        outliers = ((df[col] < (q1 - 3 * iqr)) | (df[col] > (q3 + 3 * iqr))).sum()

                    integrity["columns"][col] = {
                        "present": True,
                        "missing_count": int(missing),
                        "missing_pct": round(float(missing_pct), 2),
                        "completeness": round(100 - float(missing_pct), 2),
                        "outliers": int(outliers)
                    }
                else:
                    integrity["columns"][col] = {
                        "present": True,
                        "missing_count": int(missing),
                        "missing_pct": round(float(missing_pct), 2),
                        "completeness": round(100 - float(missing_pct), 2)
                    }
            else:
                integrity["columns"][col] = {
                    "present": False,
                    "missing_count": len(df),
                    "missing_pct": 100.0,
                    "completeness": 0.0
                }

        # Score global de complétude
        completeness_scores = [col['completeness'] for col in integrity["columns"].values()]
        integrity["overall_completeness"] = round(sum(completeness_scores) / len(completeness_scores), 2)

        return integrity

    def _calculate_stats(self, df: pd.DataFrame, crypto: str) -> Dict:
        """Calcule les statistiques de prix"""
        if 'close' not in df.columns or len(df) == 0:
            return {}

        close_prices = df['close'].dropna()

        if len(close_prices) == 0:
            return {}

        return {
            "price_min": round(float(close_prices.min()), 2),
            "price_max": round(float(close_prices.max()), 2),
            "price_mean": round(float(close_prices.mean()), 2),
            "price_median": round(float(close_prices.median()), 2),
            "price_std": round(float(close_prices.std()), 2),
            "price_current": round(float(close_prices.iloc[-1]), 2) if len(close_prices) > 0 else None,
            "volume_mean": round(float(df['volume'].mean()), 2) if 'volume' in df.columns else None,
        }

    def _analyze_time_gaps(self, df: pd.DataFrame) -> Dict:
        """Analyse les gaps temporels"""
        if 'timestamp' not in df.columns or len(df) < 2:
            return {"gaps_detected": 0, "max_gap_minutes": 0}

        df_sorted = df.sort_values('timestamp')
        timestamps = pd.to_datetime(df_sorted['timestamp'])

        # Calculer les différences
        diffs = timestamps.diff()

        # Considérer un gap si > 2 minutes (pour des données 1m)
        gaps = diffs[diffs > pd.Timedelta(minutes=2)]

        return {
            "gaps_detected": int(len(gaps)),
            "max_gap_minutes": int(gaps.max().total_seconds() / 60) if len(gaps) > 0 else 0,
            "total_gap_hours": round(float(gaps.sum().total_seconds() / 3600), 2) if len(gaps) > 0 else 0
        }

    def _analyze_indicators(self, df: pd.DataFrame) -> Dict:
        """Analyse les indicateurs techniques présents"""
        # Indicateurs techniques communs
        indicator_cols = [
            'sma_20', 'sma_50', 'sma_200',
            'ema_12', 'ema_26',
            'rsi', 'macd', 'macd_signal',
            'bbands_upper', 'bbands_middle', 'bbands_lower',
            'atr', 'adx',
            'obv', 'mfi'
        ]

        found_indicators = {}
        for col in indicator_cols:
            if col in df.columns:
                missing = df[col].isna().sum()
                missing_pct = (missing / len(df)) * 100
                found_indicators[col] = {
                    "present": True,
                    "completeness": round(100 - float(missing_pct), 2)
                }

        if not found_indicators:
            return {
                "count": 0,
                "indicators": {},
                "overall_completeness": 0
            }

        completeness_scores = [ind['completeness'] for ind in found_indicators.values()]
        overall = sum(completeness_scores) / len(completeness_scores)

        return {
            "count": len(found_indicators),
            "indicators": found_indicators,
            "overall_completeness": round(overall, 2)
        }

    def _analyze_metadata(self, df: pd.DataFrame) -> Dict:
        """Analyse les métadonnées scrapées présentes"""
        # Métadonnées de scraping
        metadata_cols = [
            'news_sentiment', 'news_count',
            'social_sentiment', 'social_mentions',
            'whale_transactions', 'whale_volume',
            'funding_rate', 'open_interest',
            'fear_greed_index',
            'github_commits', 'dev_activity',
            'google_trends'
        ]

        found_metadata = {}
        for col in metadata_cols:
            if col in df.columns:
                missing = df[col].isna().sum()
                missing_pct = (missing / len(df)) * 100
                found_metadata[col] = {
                    "present": True,
                    "completeness": round(100 - float(missing_pct), 2)
                }

        if not found_metadata:
            return {
                "count": 0,
                "metadata": {},
                "overall_completeness": 0,
                "missing_critical": [
                    "news_sentiment",
                    "social_sentiment",
                    "funding_rate",
                    "fear_greed_index"
                ]
            }

        completeness_scores = [meta['completeness'] for meta in found_metadata.values()]
        overall = sum(completeness_scores) / len(completeness_scores)

        # Identifier les métadonnées critiques manquantes
        critical_metadata = ['news_sentiment', 'social_sentiment', 'funding_rate', 'fear_greed_index']
        missing_critical = [col for col in critical_metadata if col not in df.columns]

        return {
            "count": len(found_metadata),
            "metadata": found_metadata,
            "overall_completeness": round(overall, 2),
            "missing_critical": missing_critical
        }

    def analyze_all_cryptos(self) -> Dict:
        """Analyse toutes les cryptos disponibles"""
        available_cryptos = self.get_available_cryptos()

        results = {
            "timestamp": datetime.utcnow().isoformat(),
            "total_cryptos": len(available_cryptos),
            "cryptos": {}
        }

        for crypto in available_cryptos:
            results["cryptos"][crypto] = self.analyze_crypto_data(crypto)

        # Calculer les stats globales
        results["global_stats"] = self._calculate_global_stats(results["cryptos"])

        return results

    def _calculate_global_stats(self, cryptos_data: Dict) -> Dict:
        """Calcule des statistiques globales sur toutes les cryptos"""
        total_rows = sum(
            data.get("total_rows", 0)
            for data in cryptos_data.values()
            if data.get("status") == "ok"
        )

        completeness_scores = [
            data["integrity"]["overall_completeness"]
            for data in cryptos_data.values()
            if data.get("status") == "ok"
        ]

        indicator_completeness = [
            data["indicators"]["overall_completeness"]
            for data in cryptos_data.values()
            if data.get("status") == "ok" and data["indicators"]["count"] > 0
        ]

        metadata_completeness = [
            data["metadata"]["overall_completeness"]
            for data in cryptos_data.values()
            if data.get("status") == "ok" and data["metadata"]["count"] > 0
        ]

        return {
            "total_data_points": total_rows,
            "avg_data_completeness": round(sum(completeness_scores) / len(completeness_scores), 2) if completeness_scores else 0,
            "avg_indicator_completeness": round(sum(indicator_completeness) / len(indicator_completeness), 2) if indicator_completeness else 0,
            "avg_metadata_completeness": round(sum(metadata_completeness) / len(metadata_completeness), 2) if metadata_completeness else 0,
        }


if __name__ == "__main__":
    analyzer = DataIntegrityAnalyzer()
    results = analyzer.analyze_all_cryptos()

    print(json.dumps(results, indent=2))
