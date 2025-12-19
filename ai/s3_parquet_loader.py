"""
S3 Parquet Data Loader pour entraînement progressif
Charge les données année par année depuis S3
"""

import os
import io
import re
from typing import List, Dict, Tuple, Optional, Iterator
from dataclasses import dataclass

import boto3
import numpy as np
import pandas as pd
import pyarrow.parquet as pq


@dataclass
class YearData:
    """Container pour les données d'une année"""
    year: int
    df: pd.DataFrame
    n_rows: int
    date_range: Tuple[str, str]


class S3ParquetLoader:
    """
    Charge les données Parquet depuis S3 année par année.
    Permet un entraînement progressif et efficace en mémoire.
    """

    def __init__(
        self,
        bucket: str,
        base_prefix: str = "bourse/processed/market/interval=1m/quote=USDT/symbol=BTCUSDT",
        aws_profile: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self.bucket = bucket
        self.base_prefix = base_prefix

        # Initialize S3 client
        if aws_profile:
            session = boto3.Session(profile_name=aws_profile, region_name=region)
            self.s3_client = session.client("s3")
        else:
            self.s3_client = boto3.client("s3", region_name=region)

    def list_years(self) -> List[int]:
        """Liste toutes les années disponibles dans S3"""
        paginator = self.s3_client.get_paginator("list_objects_v2")
        years = set()

        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.base_prefix, Delimiter='/'):
            # Look for year= prefixes
            for prefix in page.get('CommonPrefixes', []):
                match = re.search(r'year=(\d+)', prefix['Prefix'])
                if match:
                    years.add(int(match.group(1)))

        return sorted(years)

    def list_year_files(self, year: int) -> List[str]:
        """Liste tous les fichiers parquet pour une année donnée"""
        prefix = f"{self.base_prefix}/year={year}/"
        paginator = self.s3_client.get_paginator("list_objects_v2")
        files = []

        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('.parquet') or key.endswith('.zstd.parquet'):
                    files.append(key)

        return sorted(files)

    def load_year(self, year: int, verbose: bool = True) -> YearData:
        """
        Charge toutes les données d'une année depuis S3.
        Retourne un YearData avec le DataFrame complet.
        """
        files = self.list_year_files(year)

        if not files:
            raise ValueError(f"Aucun fichier trouvé pour l'année {year}")

        if verbose:
            print(f"\n{'='*80}")
            print(f"Chargement de l'année {year}")
            print(f"Nombre de fichiers: {len(files)}")
            print(f"{'='*80}")

        dfs = []
        total_rows = 0

        for i, key in enumerate(files, 1):
            if verbose:
                print(f"  [{i}/{len(files)}] Chargement: {key.split('/')[-1]}", end=' ... ')

            # Download file from S3
            obj = self.s3_client.get_object(Bucket=self.bucket, Key=key)
            file_content = obj['Body'].read()

            # Read parquet
            df = pd.read_parquet(io.BytesIO(file_content))
            rows = len(df)
            total_rows += rows
            dfs.append(df)

            if verbose:
                print(f"{rows:,} rows")

        # Concatenate all dataframes
        df_year = pd.concat(dfs, ignore_index=True)

        # Sort by timestamp if available
        if 'Open_Time' in df_year.columns:
            df_year = df_year.sort_values('Open_Time').reset_index(drop=True)
            date_range = (
                str(df_year['Open_Time'].iloc[0]),
                str(df_year['Open_Time'].iloc[-1])
            )
        else:
            date_range = ("N/A", "N/A")

        if verbose:
            print(f"\nTotal pour {year}: {total_rows:,} rows")
            print(f"Date range: {date_range[0]} -> {date_range[1]}")
            print(f"Memory usage: {df_year.memory_usage(deep=True).sum() / 1024**2:.2f} MB")

        return YearData(
            year=year,
            df=df_year,
            n_rows=total_rows,
            date_range=date_range
        )

    def iter_years(
        self,
        start_year: Optional[int] = None,
        end_year: Optional[int] = None,
        verbose: bool = True
    ) -> Iterator[YearData]:
        """
        Itère sur les années disponibles.
        Permet de charger et traiter une année à la fois.
        """
        years = self.list_years()

        if start_year:
            years = [y for y in years if y >= start_year]
        if end_year:
            years = [y for y in years if y <= end_year]

        if verbose:
            print(f"\n{'='*80}")
            print(f"Années disponibles: {years}")
            print(f"{'='*80}")

        for year in years:
            yield self.load_year(year, verbose=verbose)

    def get_year_stats(self, year: int) -> Dict:
        """Obtient des statistiques sur une année sans charger toutes les données"""
        files = self.list_year_files(year)
        total_size = 0

        for key in files:
            obj = self.s3_client.head_object(Bucket=self.bucket, Key=key)
            total_size += obj['ContentLength']

        return {
            'year': year,
            'n_files': len(files),
            'total_size_mb': total_size / 1024**2,
            'files': [f.split('/')[-1] for f in files]
        }


def compute_features(df: pd.DataFrame, verbose: bool = True, chunk_size: int = 100_000) -> pd.DataFrame:
    """
    Calcule toutes les features nécessaires pour le modèle.
    Compatible avec les colonnes OHLCV de vos données.
    Optimisé pour traiter de grandes quantités de données par chunks.
    """
    if verbose:
        print(f"\nCalcul des features sur {len(df):,} rows...")

    # Ensure we have the basic columns
    required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Colonne manquante: {col}")

    # Copy dataframe
    df = df.copy()

    # === Returns ===
    if verbose:
        print("  - Returns...")
    df['ret'] = df['Close'].pct_change()
    df['log_ret'] = np.log(df['Close'] / df['Close'].shift(1))

    # === Realized Volatility (multiple windows) ===
    if verbose:
        print("  - Realized Volatility...")
    windows_rv = [5, 15, 30, 60, 120, 240, 720, 1440]
    for w in windows_rv:
        df[f'rv_{w}'] = df['log_ret'].rolling(w).std()
        # Annualized (assuming 1-minute data, 525600 minutes per year)
        df[f'rv_ann_{w}'] = df[f'rv_{w}'] * np.sqrt(525600)

    # === EMAs ===
    if verbose:
        print("  - EMAs...")
    ema_periods = [20, 50, 100, 200]
    for p in ema_periods:
        df[f'ema_{p}'] = df['Close'].ewm(span=p, adjust=False).mean()
        df[f'dist_ema_{p}'] = (df['Close'] - df[f'ema_{p}']) / df[f'ema_{p}']

    # === ATR ===
    if verbose:
        print("  - ATR...")
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr_14'] = true_range.rolling(14).mean()
    df['atr_pct_14'] = df['atr_14'] / df['Close']

    # === RSI ===
    if verbose:
        print("  - RSI...")
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-10)
    df['rsi_14'] = 100 - (100 / (1 + rs))

    # === VaR / CVaR (tail risk) - Optimized ===
    if verbose:
        print("  - VaR/CVaR (tail risk)...")

    for window in [60, 240, 1440]:
        # Vectorized approach for VaR
        rolling_log_ret = df['log_ret'].rolling(window)
        df[f'var_99_{window}'] = rolling_log_ret.quantile(0.01)

        # CVaR: mean of values below VaR
        def cvar_vectorized(x):
            if len(x) == 0:
                return 0
            var_threshold = np.percentile(x, 1)
            below_var = x[x <= var_threshold]
            return below_var.mean() if len(below_var) > 0 else 0

        df[f'cvar_99_{window}'] = rolling_log_ret.apply(cvar_vectorized, raw=True)

    # Add missing columns that model expects
    if 'Quote_Volume' not in df.columns:
        df['Quote_Volume'] = df['Volume'] * df['Close']
    if 'Trades' not in df.columns:
        df['Trades'] = 0
    if 'Taker_Buy_Base' not in df.columns:
        df['Taker_Buy_Base'] = df['Volume'] * 0.5
    if 'Taker_Buy_Quote' not in df.columns:
        df['Taker_Buy_Quote'] = df['Quote_Volume'] * 0.5

    # Fill NaN with 0 (or forward fill for some)
    df = df.fillna(0)

    if verbose:
        print(f"  Features calculées. Shape: {df.shape}")

    return df


def prepare_model_data(df: pd.DataFrame, feature_keys: List[str]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Prépare les données pour le modèle.
    Retourne: X (features), y_ret (log_ret), y_rv (rv_60)
    """
    # Extract features
    X = np.zeros((len(df), len(feature_keys)), dtype=np.float32)
    for i, key in enumerate(feature_keys):
        if key in df.columns:
            X[:, i] = df[key].values.astype(np.float32)
        else:
            print(f"Warning: Feature {key} non trouvée, remplie avec 0")
            X[:, i] = 0.0

    # Extract targets
    y_ret = df.get('log_ret', pd.Series(0, index=df.index)).values.astype(np.float32)
    y_rv = df.get('rv_60', pd.Series(0, index=df.index)).values.astype(np.float32)

    return X, y_ret, y_rv


if __name__ == "__main__":
    # Test du loader
    loader = S3ParquetLoader(bucket="qbia")

    # Liste les années
    years = loader.list_years()
    print(f"Années disponibles: {years}")

    # Stats d'une année
    if years:
        stats = loader.get_year_stats(years[0])
        print(f"\nStats {stats['year']}:")
        print(f"  Fichiers: {stats['n_files']}")
        print(f"  Taille: {stats['total_size_mb']:.2f} MB")

    # Test de chargement d'une année
    if years:
        year_data = loader.load_year(years[0])
        print(f"\nAnnée {year_data.year} chargée:")
        print(f"  Rows: {year_data.n_rows:,}")
        print(f"  Colonnes: {list(year_data.df.columns)}")
