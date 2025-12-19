"""
Data Pipeline Optimisé pour Entraînement Streaming
Charge année par année depuis S3 sans exploser la RAM
"""

import os
import pickle
import gc
from typing import List, Tuple, Iterator, Optional
from dataclasses import dataclass

import numpy as np
import tensorflow as tf

# Import depuis le modèle existant
from ai.models.model import (
    RunningRobustScaler,
    FEATURE_KEYS,
    TARGET_RET_KEY,
    TARGET_RV_KEY,
    make_windows,
)

# Import du loader S3
from ai.s3_parquet_loader import (
    S3ParquetLoader,
    YearData,
    compute_features,
    prepare_model_data,
)


@dataclass
class WindowsData:
    """Container pour les windows d'une année (CORRECTED)"""
    Xw: np.ndarray  # [N, lookback, F]
    y_ret: np.ndarray  # [N, horizon]
    y_dir: np.ndarray  # [N] - BINARY: 0=DOWN, 1=UP
    y_rv: np.ndarray  # [N] - SCALAR: RMS aggregated volatility
    year: int
    n_samples: int


class StreamingRobustScaler:
    """
    Wrapper autour de RunningRobustScaler qui ajoute:
    - Sauvegarde/chargement depuis pickle
    - Interface simple pour fit année par année
    """

    def __init__(self, feature_dim: int, reservoir_size: int = 200_000, seed: int = 1337):
        self.scaler = RunningRobustScaler(
            feature_dim=feature_dim,
            reservoir_size=reservoir_size,
            seed=seed
        )
        self.is_fitted = False

    def fit_year(self, X: np.ndarray) -> None:
        """Fit sur une année de données (streaming)"""
        for row in X:
            self.scaler.update(row)

    def finalize(self) -> None:
        """Finalise le scaler après avoir vu toutes les années"""
        self.scaler.finalize()
        self.is_fitted = True

    def transform(self, X: np.ndarray) -> np.ndarray:
        """Transform les données"""
        if not self.is_fitted:
            raise RuntimeError("Scaler not fitted yet. Call finalize() first.")
        return self.scaler.transform(X)

    def save(self, path: str) -> None:
        """Sauvegarde le scaler"""
        with open(path, 'wb') as f:
            pickle.dump(self.scaler, f)
        print(f"Scaler saved to {path}")

    @classmethod
    def load(cls, path: str, feature_dim: int) -> 'StreamingRobustScaler':
        """Charge un scaler depuis un fichier"""
        instance = cls(feature_dim=feature_dim)
        with open(path, 'rb') as f:
            instance.scaler = pickle.load(f)
        instance.is_fitted = True
        print(f"Scaler loaded from {path}")
        return instance


def fit_scaler_streaming(
    loader: S3ParquetLoader,
    years: List[int],
    feature_dim: int,
    verbose: bool = True
) -> StreamingRobustScaler:
    """
    Fit un scaler en streaming année par année.
    Minimise l'utilisation de RAM.
    """
    scaler = StreamingRobustScaler(feature_dim=feature_dim)

    for year in years:
        if verbose:
            print(f"\nFitting scaler on year {year}...")

        # Charge l'année
        year_data = loader.load_year(year, verbose=verbose)

        # Compute features
        df_with_features = compute_features(year_data.df, verbose=verbose)

        # Prépare les données pour le modèle
        X, _, _ = prepare_model_data(df_with_features, FEATURE_KEYS)

        # Fit scaler
        if verbose:
            print(f"  Updating scaler with {X.shape[0]:,} samples...")
        scaler.fit_year(X)

        # Libère mémoire
        del year_data, df_with_features, X
        gc.collect()

        if verbose:
            print(f"  Year {year} processed. Reservoir fill: {scaler.scaler._filled:,}")

    # Finalise le scaler
    if verbose:
        print("\nFinalizing scaler...")
    scaler.finalize()

    if verbose:
        print(f"Scaler statistics:")
        print(f"  Median (first 5 features): {scaler.scaler.median[:5]}")
        print(f"  MAD (first 5 features): {scaler.scaler.mad[:5]}")

    return scaler


def create_windows_for_year(
    year_data: YearData,
    scaler: StreamingRobustScaler,
    lookback: int,
    horizon: int,
    stride: int,
    verbose: bool = True
) -> WindowsData:
    """
    Crée les windows pour une année donnée.
    """
    if verbose:
        print(f"\n  Creating windows for year {year_data.year}...")

    # Compute features
    df_with_features = compute_features(year_data.df, verbose=verbose)

    # Prépare les données
    X, y_ret, y_rv = prepare_model_data(df_with_features, FEATURE_KEYS)

    # Transform avec le scaler
    if verbose:
        print(f"    Scaling features...")
    X = scaler.transform(X)

    # Crée les windows
    if verbose:
        print(f"    Creating windows (lookback={lookback}, horizon={horizon})...")
    # CORRECTED: make_windows now returns y_rv_agg (scalar) instead of y_rv_h (multi-horizon)
    Xw, y_ret_h, y_dir, y_rv_agg = make_windows(
        X, y_ret, y_rv,
        lookback=lookback,
        horizon=horizon,
        stride=stride
    )

    if verbose:
        print(f"    Created {Xw.shape[0]:,} windows")

    return WindowsData(
        Xw=Xw,
        y_ret=y_ret_h,
        y_dir=y_dir,
        y_rv=y_rv_agg,  # CHANGED: Now scalar
        year=year_data.year,
        n_samples=Xw.shape[0]
    )


def save_windows_to_disk(
    loader: S3ParquetLoader,
    years: List[int],
    scaler: StreamingRobustScaler,
    lookback: int,
    horizon: int,
    stride: int,
    output_dir: str,
    verbose: bool = True
) -> None:
    """
    Crée et sauvegarde les windows année par année sur disque.
    Format: NPZ compressé (plus simple que TFRecord pour ce use case)
    """
    os.makedirs(output_dir, exist_ok=True)

    total_windows = 0

    for year in years:
        if verbose:
            print(f"\n{'='*80}")
            print(f"Processing year {year}")
            print(f"{'='*80}")

        # Charge l'année
        year_data = loader.load_year(year, verbose=verbose)

        # Crée les windows
        windows_data = create_windows_for_year(
            year_data, scaler, lookback, horizon, stride, verbose=verbose
        )

        # Sauvegarde en NPZ compressé
        output_path = os.path.join(output_dir, f"year_{year}.npz")
        np.savez_compressed(
            output_path,
            Xw=windows_data.Xw,
            y_ret=windows_data.y_ret,
            y_dir=windows_data.y_dir,
            y_rv=windows_data.y_rv,
        )

        if verbose:
            size_mb = os.path.getsize(output_path) / 1024**2
            print(f"    Saved to {output_path} ({size_mb:.2f} MB)")

        total_windows += windows_data.n_samples

        # Libère mémoire
        del year_data, windows_data
        gc.collect()

    if verbose:
        print(f"\n{'='*80}")
        print(f"Total windows created: {total_windows:,}")
        print(f"Saved to: {output_dir}")
        print(f"{'='*80}")


def load_windows_from_disk(year: int, windows_dir: str) -> WindowsData:
    """Charge les windows d'une année depuis le disque"""
    path = os.path.join(windows_dir, f"year_{year}.npz")
    data = np.load(path)

    return WindowsData(
        Xw=data['Xw'],
        y_ret=data['y_ret'],
        y_dir=data['y_dir'],
        y_rv=data['y_rv'],
        year=year,
        n_samples=data['Xw'].shape[0]
    )


def build_tf_dataset_from_disk(
    windows_dir: str,
    years: List[int],
    batch_size: int,
    shuffle_buffer: int,
    training: bool,
    prefetch: int = 2,
    verbose: bool = True
) -> tf.data.Dataset:
    """
    Construit un tf.data.Dataset depuis les windows sauvegardées sur disque.
    Charge année par année pour limiter la RAM.
    """
    if verbose:
        print(f"\nBuilding TensorFlow Dataset from {windows_dir}...")
        print(f"  Years: {years}")

    all_Xw = []
    all_y_ret = []
    all_y_dir = []
    all_y_rv = []

    for year in years:
        if verbose:
            print(f"  Loading year {year}...")

        windows_data = load_windows_from_disk(year, windows_dir)

        all_Xw.append(windows_data.Xw)
        all_y_ret.append(windows_data.y_ret)
        all_y_dir.append(windows_data.y_dir)
        all_y_rv.append(windows_data.y_rv)

        if verbose:
            print(f"    Loaded {windows_data.n_samples:,} windows")

    # Concatenate all years
    Xw = np.concatenate(all_Xw, axis=0)
    y_ret = np.concatenate(all_y_ret, axis=0)
    y_dir = np.concatenate(all_y_dir, axis=0)
    y_rv = np.concatenate(all_y_rv, axis=0)

    if verbose:
        print(f"\nTotal windows: {Xw.shape[0]:,}")
        print(f"Input shape: {Xw.shape}")
        print(f"Memory usage: {Xw.nbytes / 1024**2:.2f} MB")

    # Crée le dataset TensorFlow
    ds = tf.data.Dataset.from_tensor_slices((
        Xw,
        {"ret": y_ret, "dir": y_dir, "rv": y_rv}
    ))

    if training:
        ds = ds.shuffle(min(shuffle_buffer, Xw.shape[0]), reshuffle_each_iteration=True)

    ds = ds.batch(batch_size, drop_remainder=training)
    ds = ds.prefetch(prefetch)

    if verbose:
        print(f"Dataset created: {ds}")

    return ds


if __name__ == "__main__":
    # Test du pipeline
    print("Testing Data Pipeline...")

    # Initialize loader
    loader = S3ParquetLoader(bucket="qbia")

    # Test avec 1 année seulement
    test_years = [2020]

    print("\n1. Fitting scaler...")
    scaler = fit_scaler_streaming(
        loader=loader,
        years=test_years,
        feature_dim=len(FEATURE_KEYS),
        verbose=True
    )

    print("\n2. Creating and saving windows...")
    save_windows_to_disk(
        loader=loader,
        years=test_years,
        scaler=scaler,
        lookback=256,
        horizon=12,
        stride=1,
        output_dir="test_windows",
        verbose=True
    )

    print("\n3. Loading windows and building TF dataset...")
    ds = build_tf_dataset_from_disk(
        windows_dir="test_windows",
        years=test_years,
        batch_size=32,
        shuffle_buffer=10000,
        training=True,
        verbose=True
    )

    print("\n4. Testing dataset iteration...")
    for batch in ds.take(1):
        X_batch, y_batch = batch
        print(f"X shape: {X_batch.shape}")
        print(f"y_ret shape: {y_batch['ret'].shape}")
        print(f"y_dir shape: {y_batch['dir'].shape}")
        print(f"y_rv shape: {y_batch['rv'].shape}")

    print("\nPipeline test complete!")
