import pandas as pd

from infra.storage.object_store import S3ParquetReader, S3ParquetWriter


def test_s3_parquet_roundtrip(tmp_path):
    df = pd.DataFrame(
        {
            "event_time": pd.date_range("2024-01-01", periods=2, freq="1H"),
            "recv_time": pd.date_range("2024-01-01", periods=2, freq="1H"),
            "event_time_aligned": pd.date_range("2024-01-01", periods=2, freq="1H"),
            "skew_ms": [0, 0],
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "venue": ["binance", "binance"],
            "source": ["spot", "spot"],
            "event_type": ["trade", "trade"],
            "seq": [1, 2],
            "ingest_run_id": ["test", "test"],
            "payload_version": [1, 1],
            "is_snapshot": [False, False],
            "price": [100.0, 101.0],
            "qty": [1.0, 1.0],
        }
    )
    writer = S3ParquetWriter()
    prefix = str(tmp_path / "raw")
    writer.write(df, prefix, partition_cols=["symbol", "source"])
    reader = S3ParquetReader()
    out = reader.read(prefix)
    assert len(out) == len(df)
