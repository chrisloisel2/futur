# event_classifier.py
import tensorflow as tf


class EventClassifier(tf.keras.Model):
    """
    Détecteur d'événements de marché (non directionnel primaire)
    """

    def __init__(self, d_model=64, n_layers=3, n_classes=4, dropout=0.2):
        super().__init__()

        self.in_proj = tf.keras.layers.Dense(d_model, activation="gelu")
        self.in_ln = tf.keras.layers.LayerNormalization()

        self.blocks = []
        for i in range(n_layers):
            self.blocks.append(
                tf.keras.Sequential([
                    tf.keras.layers.Conv1D(
                        d_model,
                        kernel_size=3,
                        padding="causal",
                        dilation_rate=2 ** i,
                    ),
                    tf.keras.layers.LayerNormalization(),
                    tf.keras.layers.Activation("gelu"),
                    tf.keras.layers.Dropout(dropout),
                ])
            )

        self.pool = tf.keras.layers.GlobalAveragePooling1D()

        self.head = tf.keras.Sequential([
            tf.keras.layers.Dense(d_model, activation="gelu"),
            tf.keras.layers.Dense(n_classes),
            tf.keras.layers.Activation("softmax", dtype="float32"),
        ])

    def call(self, x, training=False):
        h = self.in_ln(self.in_proj(x))
        for b in self.blocks:
            h = b(h, training=training)
        z = self.pool(h)
        return self.head(z)
