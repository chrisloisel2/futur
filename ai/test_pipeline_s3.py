"""
Test du pipeline complet avec données S3.
"""
import logging
import sys
from pathlib import Path

# Ajouter le chemin TRAIN au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "TRAIN"))

from data.pipeline import DataPipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


def test_pipeline_s3():
    """Test du DataPipeline avec source S3."""
    logger.info("=" * 60)
    logger.info("Test du DataPipeline avec données S3")
    logger.info("=" * 60)

    # Configuration pour un test rapide
    config = {
        "data_source": "s3",
        "s3_bucket": "qbia",
        "s3_prefix": "bourse/mintrad",
        "start_year": 2024,
        "end_year": 2024,
        "symbols_filter": ["BTCUSDT", "ETHUSDT"],  # Seulement 2 symboles pour test rapide
        "local_cache_dir": "/tmp/test_trading_cache",

        "train_split": 0.7,
        "val_split": 0.15,
        "test_split": 0.15,
        "lookback_window": 50,  # Réduit pour test rapide
        "feature_dim": 128,
        "batch_size": 32,
        "shuffle": True,
        "use_synthetic_data": False,
    }

    # Créer le pipeline
    logger.info("Création du DataPipeline...")
    pipeline = DataPipeline(config)

    # Obtenir les data loaders
    logger.info("Chargement des données et création des DataLoaders...")
    train_loader, val_loader, test_loader = pipeline.get_data_loaders()

    # Vérifier les loaders
    logger.info(f"Train loader: {len(train_loader)} batches")
    logger.info(f"Val loader: {len(val_loader)} batches")
    logger.info(f"Test loader: {len(test_loader)} batches")

    # Tester un batch
    logger.info("\nTest d'un batch d'entraînement:")
    for batch_X, batch_y in train_loader:
        logger.info(f"  Batch X shape: {batch_X.shape}")  # (batch_size, lookback, feature_dim)
        logger.info(f"  Batch y shape: {batch_y.shape}")  # (batch_size,)
        logger.info(f"  X dtype: {batch_X.dtype}")
        logger.info(f"  y dtype: {batch_y.dtype}")
        logger.info(f"  X range: [{batch_X.min():.4f}, {batch_X.max():.4f}]")
        logger.info(f"  y range: [{batch_y.min():.4f}, {batch_y.max():.4f}]")
        break

    # Vérifier que les dimensions correspondent
    assert batch_X.shape[0] <= config["batch_size"], "Batch size incorrect"
    assert batch_X.shape[1] == config["lookback_window"], "Lookback window incorrect"
    assert batch_X.shape[2] == pipeline.feature_dim, "Feature dimension mismatch"

    logger.info("\n✓ Pipeline S3 fonctionne correctement!")
    logger.info(f"Feature dimension détectée: {pipeline.feature_dim}")

    return pipeline, train_loader, val_loader, test_loader


def main():
    """Run pipeline test."""
    logger.info("Test du DataPipeline avec S3...\n")

    try:
        test_pipeline_s3()

        logger.info("\n" + "=" * 60)
        logger.info("TEST PIPELINE S3 RÉUSSI! ✓")
        logger.info("=" * 60)
        logger.info("\nLe système est prêt pour l'entraînement!")
        logger.info("Lancez: python ai/train.py --config ai/configs/train_s3.yaml --device mps")

    except Exception as e:
        logger.error(f"Test pipeline failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
