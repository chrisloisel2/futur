# pairwise_comparator.py
import tensorflow as tf


class PairwiseComparator(tf.keras.Model):
    def __init__(self, d_model=64, dropout=0.2):
        super().__init__()

        self.encoder = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.LayerNormalization(),
            tf.keras.layers.Dense(d_model),
        ])

        self.classifier = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dropout(dropout),
            tf.keras.layers.Dense(3),
            tf.keras.layers.Activation("softmax", dtype="float32"),
        ])

    def call(self, x_now, x_ref, training=False):
        """
        x_now: [B, D]
        x_ref: [B, D]
        """
        z_now = self.encoder(x_now)
        z_ref = self.encoder(x_ref)

        diff = tf.concat([
            z_now,
            z_ref,
            z_now - z_ref,
            tf.abs(z_now - z_ref),
        ], axis=-1)

        return self.classifier(diff)
