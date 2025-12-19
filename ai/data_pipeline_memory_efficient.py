"""
Version memory-efficient du data pipeline
Utilise tf.data generator pour ne jamais charger toutes les années en RAM
"""

import os
import numpy as np
import tensorflow as tf
from typing import List


def create_npz_generator(windows_dir: str, years: List[int]):
    """
    Generator qui yield les données depuis NPZ files
    Ne charge qu'un batch à la fois en mémoire
    """
    for year in years:
        path = os.path.join(windows_dir, f"year_{year}.npz")
        data = np.load(path)

        Xw = data['Xw']
        y_ret = data['y_ret']
        y_dir = data['y_dir']
        y_rv = data['y_rv']

        # Yield sample par sample
        for i in range(len(Xw)):
            yield (
                Xw[i],
                {
                    'ret': y_ret[i],
                    'dir': y_dir[i],
                    'rv': y_rv[i]
                }
            )


def build_tf_dataset_from_disk_efficient(
    windows_dir: str,
    years: List[int],
    batch_size: int,
    shuffle_buffer: int,
    training: bool,
    prefetch: int = 2,
    verbose: bool = True
) -> tf.data.Dataset:
    """
    Version memory-efficient: utilise tf.data.Dataset.from_generator
    Ne charge jamais toutes les années en RAM
    """
    if verbose:
        print(f"\nBuilding MEMORY-EFFICIENT TensorFlow Dataset from {windows_dir}...")
        print(f"  Years: {years}")
        print(f"  Using generator (streaming from disk)")

    # Détermine les shapes en lisant le premier fichier
    first_path = os.path.join(windows_dir, f"year_{years[0]}.npz")
    first_data = np.load(first_path)

    input_shape = first_data['Xw'].shape[1:]  # (lookback, features)
    horizon = first_data['y_ret'].shape[1]

    if verbose:
        print(f"  Input shape: {input_shape}")
        print(f"  Horizon: {horizon}")

    # Crée le dataset depuis le generator
    ds = tf.data.Dataset.from_generator(
        lambda: create_npz_generator(windows_dir, years),
        output_signature=(
            tf.TensorSpec(shape=input_shape, dtype=tf.float32),
            {
                'ret': tf.TensorSpec(shape=(horizon,), dtype=tf.float32),
                'dir': tf.TensorSpec(shape=(), dtype=tf.int32),
                'rv': tf.TensorSpec(shape=(), dtype=tf.float32),  # CORRECTED: Scalar RV
            }
        )
    )

    if training:
        # Shuffle avant batching pour mélanger les années
        ds = ds.shuffle(shuffle_buffer, reshuffle_each_iteration=True)

    ds = ds.batch(batch_size, drop_remainder=training)
    ds = ds.prefetch(prefetch)

    if verbose:
        print(f"  Dataset created: {ds}")
        print(f"  Memory-efficient: ✓ Streaming from disk")

    return ds
