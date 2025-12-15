#!/usr/bin/env python3
"""
Exemple d'intégration des données alternatives dans un modèle ML.

Ce script montre comment :
1. Charger les données OHLCV de base
2. Charger les indicateurs techniques
3. Charger les données alternatives
4. Créer un dataset enrichi pour ML
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import boto3
from ai.TRAIN.data.s3_data_source import S3DataSource


class EnrichedDataLoader:
    """
    Loader pour créer un dataset enrichi avec toutes les sources de données.
    """

    def __init__(self, bucket='qbia'):
        self.bucket = bucket
        self.s3_client = boto3.client('s3', region_name='us-east-1')

    def load_complete_dataset(self, symbol: str, year: int) -> pd.DataFrame:
        """
        Charge et merge toutes les données pour un symbole.

        Returns:
            DataFrame avec OHLCV + indicateurs techniques + données alternatives
        """
        print(f"Loading complete dataset for {symbol} {year}...")

        # 1. OHLCV de base
        print("  [1/5] Loading OHLCV...")
        ohlcv_df = self._load_ohlcv(symbol, year)
        print(f"        ✓ {len(ohlcv_df)} rows")

        # 2. Indicateurs techniques
        print("  [2/5] Loading technical indicators...")
        indicators_df = self._load_indicators(symbol, year)
        print(f"        ✓ {len(indicators_df)} rows")

        # 3. Sentiment
        print("  [3/5] Loading sentiment data...")
        sentiment_df = self._load_sentiment(symbol, year)
        print(f"        ✓ {len(sentiment_df)} rows")

        # 4. On-chain
        print("  [4/5] Loading on-chain metrics...")
        onchain_df = self._load_onchain(symbol, year)
        print(f"        ✓ {len(onchain_df)} rows")

        # 5. Macro
        print("  [5/5] Loading macro-economic data...")
        macro_df = self._load_macro(year)
        print(f"        ✓ {len(macro_df)} rows")

        # Merge all
        print("  Merging all datasets...")
        merged = self._merge_all(ohlcv_df, indicators_df, sentiment_df, onchain_df, macro_df)

        print(f"✓ Complete dataset: {len(merged)} rows, {len(merged.columns)} columns")
        print(f"  Date range: {merged['timestamp'].min()} to {merged['timestamp'].max()}")

        return merged

    def _load_ohlcv(self, symbol: str, year: int) -> pd.DataFrame:
        """Load OHLCV from S3."""
        s3_source = S3DataSource(bucket=self.bucket, prefix='bourse/mintrad')
        df = s3_source.fetch_symbol_data(symbol, year)
        return df

    def _load_indicators(self, symbol: str, year: int) -> pd.DataFrame:
        """Load technical indicators from S3."""
        dfs = []

        for month in range(1, 13):
            try:
                file_path = f's3://{self.bucket}/bourse/indicators/indicators_1m_{year}/{symbol}_{year}_{month:02d}_indicators.parquet'
                month_df = pd.read_parquet(file_path)
                dfs.append(month_df)
            except Exception as e:
                pass  # Month not found

        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Select only indicator columns (not duplicate OHLCV)
            indicator_cols = [
                'timestamp', 'symbol',
                'rsi', 'rsi_14', 'macd', 'macd_signal', 'macd_histogram',
                'sma_7', 'sma_25', 'sma_99', 'ema_7', 'ema_25', 'ema_99',
                'bollinger_upper', 'bollinger_middle', 'bollinger_lower',
                'atr', 'adx', 'cci', 'stoch_k', 'stoch_d',
                'volume_sma', 'obv',
                'pivot_point', 'resistance_1', 'resistance_2', 'support_1', 'support_2'
            ]

            # Keep only columns that exist
            existing_cols = [col for col in indicator_cols if col in df.columns]
            df = df[existing_cols].drop_duplicates(subset=['symbol', 'timestamp'])

            return df
        else:
            return pd.DataFrame()

    def _load_sentiment(self, symbol: str, year: int) -> pd.DataFrame:
        """Load sentiment data from S3."""
        dfs = []

        for month in range(1, 13):
            try:
                file_path = f's3://{self.bucket}/bourse/alternative_data/sentiment/{year}/{year}_{month:02d}_sentiment.parquet'
                month_df = pd.read_parquet(file_path)
                month_df = month_df[month_df['symbol'] == symbol]
                dfs.append(month_df)
            except Exception as e:
                pass

        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Select sentiment columns
            sentiment_cols = [
                'timestamp', 'symbol',
                'sentiment_score', 'fear_greed_index',
                'tweet_volume', 'social_dominance', 'social_volume_change',
                'reddit_sentiment', 'positive_tweets', 'negative_tweets'
            ]

            existing_cols = [col for col in sentiment_cols if col in df.columns]
            df = df[existing_cols].drop_duplicates(subset=['symbol', 'timestamp'])

            # Prefix columns
            df = df.rename(columns={col: f'sent_{col}' for col in df.columns if col not in ['timestamp', 'symbol']})

            return df
        else:
            return pd.DataFrame()

    def _load_onchain(self, symbol: str, year: int) -> pd.DataFrame:
        """Load on-chain metrics from S3."""
        dfs = []

        for month in range(1, 13):
            try:
                file_path = f's3://{self.bucket}/bourse/alternative_data/onchain/{year}/{year}_{month:02d}_onchain.parquet'
                month_df = pd.read_parquet(file_path)
                month_df = month_df[month_df['symbol'] == symbol]
                dfs.append(month_df)
            except Exception as e:
                pass

        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Select on-chain columns
            onchain_cols = [
                'timestamp', 'symbol',
                'active_addresses', 'new_addresses', 'transaction_count',
                'exchange_net_flow', 'whale_transactions', 'supply_on_exchanges',
                'mvrv_ratio', 'hash_rate', 'mining_difficulty',
                'futures_funding_rate', 'liquidations_long', 'liquidations_short'
            ]

            existing_cols = [col for col in onchain_cols if col in df.columns]
            df = df[existing_cols].drop_duplicates(subset=['symbol', 'timestamp'])

            # Prefix columns
            df = df.rename(columns={col: f'onchain_{col}' for col in df.columns if col not in ['timestamp', 'symbol']})

            return df
        else:
            return pd.DataFrame()

    def _load_macro(self, year: int) -> pd.DataFrame:
        """Load macro-economic data from S3."""
        dfs = []

        for month in range(1, 13):
            try:
                file_path = f's3://{self.bucket}/bourse/alternative_data/macro/{year}/{year}_{month:02d}_macro.parquet'
                month_df = pd.read_parquet(file_path)
                dfs.append(month_df)
            except Exception as e:
                pass

        if dfs:
            df = pd.concat(dfs, ignore_index=True)
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Select macro columns
            macro_cols = [
                'timestamp',
                'fed_rate', 'inflation_rate', 'unemployment_rate',
                'sp500', 'nasdaq', 'gold_price', 'oil_price', 'vix_index',
                'btc_dominance', 'btc_sp500_correlation', 'btc_gold_correlation'
            ]

            existing_cols = [col for col in macro_cols if col in df.columns]
            df = df[existing_cols].drop_duplicates(subset=['timestamp'])

            # Prefix columns
            df = df.rename(columns={col: f'macro_{col}' for col in df.columns if col != 'timestamp'})

            return df
        else:
            return pd.DataFrame()

    def _merge_all(
        self,
        ohlcv: pd.DataFrame,
        indicators: pd.DataFrame,
        sentiment: pd.DataFrame,
        onchain: pd.DataFrame,
        macro: pd.DataFrame
    ) -> pd.DataFrame:
        """Merge all dataframes."""

        # Start with OHLCV
        merged = ohlcv.copy()

        # Merge indicators
        if not indicators.empty:
            merged = pd.merge(merged, indicators, on=['symbol', 'timestamp'], how='left', suffixes=('', '_ind'))

        # Merge sentiment (forward fill for missing minutes)
        if not sentiment.empty:
            merged = pd.merge(merged, sentiment, on=['symbol', 'timestamp'], how='left', suffixes=('', '_sent'))
            # Forward fill sentiment (it doesn't change every minute)
            sent_cols = [col for col in merged.columns if col.startswith('sent_')]
            merged[sent_cols] = merged[sent_cols].fillna(method='ffill')

        # Merge on-chain (forward fill)
        if not onchain.empty:
            merged = pd.merge(merged, onchain, on=['symbol', 'timestamp'], how='left', suffixes=('', '_onchain'))
            onchain_cols = [col for col in merged.columns if col.startswith('onchain_')]
            merged[onchain_cols] = merged[onchain_cols].fillna(method='ffill')

        # Merge macro (forward fill, no symbol needed)
        if not macro.empty:
            merged = pd.merge(merged, macro, on='timestamp', how='left', suffixes=('', '_macro'))
            macro_cols = [col for col in merged.columns if col.startswith('macro_')]
            merged[macro_cols] = merged[macro_cols].fillna(method='ffill')

        # Sort by timestamp
        merged = merged.sort_values('timestamp').reset_index(drop=True)

        return merged


def create_ml_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Create additional ML features from the enriched dataset.
    """
    print("Creating ML features...")

    # Price-based features
    df['returns_1m'] = df['close'].pct_change(1)
    df['returns_5m'] = df['close'].pct_change(5)
    df['returns_15m'] = df['close'].pct_change(15)
    df['returns_1h'] = df['close'].pct_change(60)

    # Volatility
    df['volatility_15m'] = df['returns_1m'].rolling(15).std()
    df['volatility_1h'] = df['returns_1m'].rolling(60).std()

    # Volume features
    df['volume_ma_ratio'] = df['volume'] / df['volume'].rolling(60).mean()

    # Sentiment momentum
    if 'sent_sentiment_score' in df.columns:
        df['sentiment_change'] = df['sent_sentiment_score'].diff()
        df['sentiment_momentum'] = df['sent_sentiment_score'].rolling(60).mean()

    # On-chain momentum
    if 'onchain_exchange_net_flow' in df.columns:
        df['exchange_flow_ma'] = df['onchain_exchange_net_flow'].rolling(24*60).mean()  # 24h average

    # Price vs indicators
    if 'sma_25' in df.columns:
        df['price_vs_sma25'] = (df['close'] - df['sma_25']) / df['sma_25']

    if 'bollinger_upper' in df.columns and 'bollinger_lower' in df.columns:
        df['bollinger_position'] = (df['close'] - df['bollinger_lower']) / (df['bollinger_upper'] - df['bollinger_lower'])

    # Target (predict next hour return)
    df['target_1h_return'] = df['close'].shift(-60).pct_change()
    df['target_direction'] = (df['target_1h_return'] > 0).astype(int)

    print(f"✓ Created {len([c for c in df.columns if c.startswith('returns_') or c.startswith('target_')])} new features")

    return df


def example_usage():
    """Example usage of the enriched data loader."""

    # Initialize loader
    loader = EnrichedDataLoader(bucket='qbia')

    # Load complete dataset for BTC 2024
    df = loader.load_complete_dataset('BTCUSDT', 2024)

    # Create ML features
    df = create_ml_features(df)

    # Show dataset info
    print("\n" + "="*80)
    print("ENRICHED DATASET SUMMARY")
    print("="*80)
    print(f"Total rows: {len(df):,}")
    print(f"Total columns: {len(df.columns)}")
    print(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print()

    # Show column groups
    print("Column groups:")
    print(f"  - OHLCV: {len([c for c in df.columns if c in ['open', 'high', 'low', 'close', 'volume']])}")
    print(f"  - Technical indicators: {len([c for c in df.columns if any(x in c for x in ['sma', 'ema', 'rsi', 'macd', 'bollinger'])])}")
    print(f"  - Sentiment: {len([c for c in df.columns if c.startswith('sent_')])}")
    print(f"  - On-chain: {len([c for c in df.columns if c.startswith('onchain_')])}")
    print(f"  - Macro: {len([c for c in df.columns if c.startswith('macro_')])}")
    print(f"  - Derived features: {len([c for c in df.columns if c.startswith('returns_') or c.startswith('target_')])}")
    print()

    # Show sample
    print("Sample data (first 5 rows, selected columns):")
    sample_cols = ['timestamp', 'close', 'volume', 'rsi', 'sent_sentiment_score', 'sent_fear_greed_index',
                   'onchain_exchange_net_flow', 'macro_btc_dominance', 'target_1h_return']
    existing_sample_cols = [c for c in sample_cols if c in df.columns]
    print(df[existing_sample_cols].head())
    print()

    # Show data completeness
    print("Data completeness:")
    print(df[existing_sample_cols].notna().mean().sort_values(ascending=False))
    print()

    # Correlations with target
    if 'target_1h_return' in df.columns:
        print("Top 15 features correlated with 1h return:")
        correlations = df.corr()['target_1h_return'].abs().sort_values(ascending=False)
        print(correlations.head(15))
        print()

    # Save to file
    output_file = 'btc_enriched_2024.parquet'
    df.to_parquet(output_file, index=False)
    print(f"✓ Saved enriched dataset to: {output_file}")
    print()

    print("="*80)
    print("Next steps:")
    print("  1. Clean the data (handle NaNs)")
    print("  2. Feature selection (remove low-correlation features)")
    print("  3. Split train/test (temporal split)")
    print("  4. Train your ML model")
    print("  5. Backtest on the enriched dataset")
    print("="*80)


if __name__ == '__main__':
    example_usage()
