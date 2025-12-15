"""
Script de test pour valider le chargement des données S3.
"""
import logging
import sys
from pathlib import Path

# Ajouter le chemin TRAIN au PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent / "TRAIN"))

from data.s3_data_source import S3DataSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


def test_list_years():
    """Test listing available years."""
    logger.info("=" * 60)
    logger.info("Test 1: List available years")
    logger.info("=" * 60)

    s3 = S3DataSource(bucket="qbia", prefix="bourse/mintrad")
    years = s3.list_available_years()

    logger.info(f"Available years: {years}")
    assert len(years) > 0, "No years found"
    logger.info("✓ Test passed\n")


def test_list_symbols():
    """Test listing available symbols for a year."""
    logger.info("=" * 60)
    logger.info("Test 2: List available symbols for 2024")
    logger.info("=" * 60)

    s3 = S3DataSource(bucket="qbia", prefix="bourse/mintrad")
    symbols = s3.list_available_symbols(2024)

    logger.info(f"Found {len(symbols)} symbols")
    logger.info(f"Sample symbols: {symbols[:10]}")
    assert len(symbols) > 0, "No symbols found"
    logger.info("✓ Test passed\n")


def test_fetch_single_symbol():
    """Test fetching data for a single symbol/year."""
    logger.info("=" * 60)
    logger.info("Test 3: Fetch BTCUSDT for 2024")
    logger.info("=" * 60)

    s3 = S3DataSource(
        bucket="qbia",
        prefix="bourse/mintrad",
        cache_dir="/tmp/test_trading_cache"
    )

    df = s3.fetch_symbol_data("BTCUSDT", 2024)

    logger.info(f"DataFrame shape: {df.shape}")
    logger.info(f"Columns: {df.columns.tolist()}")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    logger.info(f"First rows:\n{df.head()}")
    logger.info(f"Data types:\n{df.dtypes}")

    assert not df.empty, "DataFrame is empty"
    assert "timestamp" in df.columns, "Missing timestamp column"
    assert "close" in df.columns, "Missing close column"
    assert "symbol" in df.columns, "Missing symbol column"
    logger.info("✓ Test passed\n")


def test_fetch_symbol_range():
    """Test fetching data across multiple years."""
    logger.info("=" * 60)
    logger.info("Test 4: Fetch ETHUSDT from 2023 to 2024")
    logger.info("=" * 60)

    s3 = S3DataSource(
        bucket="qbia",
        prefix="bourse/mintrad",
        cache_dir="/tmp/test_trading_cache"
    )

    df = s3.fetch_symbol_range("ETHUSDT", 2023, 2024)

    logger.info(f"DataFrame shape: {df.shape}")
    logger.info(f"Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

    assert not df.empty, "DataFrame is empty"
    assert len(df) > 500000, "Expected more rows for 2 years of 1m data"
    logger.info("✓ Test passed\n")


def test_fetch_multiple_symbols():
    """Test fetching multiple symbols."""
    logger.info("=" * 60)
    logger.info("Test 5: Fetch multiple symbols (2024 only)")
    logger.info("=" * 60)

    s3 = S3DataSource(
        bucket="qbia",
        prefix="bourse/mintrad",
        cache_dir="/tmp/test_trading_cache"
    )

    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
    df = s3.fetch_all_symbols_range(symbols, 2024, 2024)

    logger.info(f"DataFrame shape: {df.shape}")
    logger.info(f"Symbols in data: {df['symbol'].unique()}")
    logger.info(f"Rows per symbol:")
    for sym in symbols:
        count = len(df[df['symbol'] == sym])
        logger.info(f"  {sym}: {count:,} rows")

    assert not df.empty, "DataFrame is empty"
    assert set(df['symbol'].unique()) == set(symbols), "Missing symbols"
    logger.info("✓ Test passed\n")


def main():
    """Run all tests."""
    logger.info("Starting S3DataSource tests...")
    logger.info("")

    try:
        test_list_years()
        test_list_symbols()
        test_fetch_single_symbol()
        test_fetch_symbol_range()
        test_fetch_multiple_symbols()

        logger.info("=" * 60)
        logger.info("ALL TESTS PASSED! ✓")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Test failed with error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
