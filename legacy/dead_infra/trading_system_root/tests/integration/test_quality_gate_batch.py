import pandas as pd

from pipeline.quality.gate import QualityGate
from pipeline.quality.checks import (
    BookSanityCheck,
    ClockSkewCheck,
    CrossSourceConsistencyCheck,
    DuplicateCheck,
    HaltDetectionCheck,
    MissingnessCheck,
    MicrostructureToxicityCheck,
    OutlierCheck,
    SchemaValidationCheck,
    SequenceGapCheck,
    StalenessCheck,
    TimeTravelCheck,
)


def test_quality_gate_batch(tmp_path):
    df = pd.DataFrame(
        {
            "event_time": pd.to_datetime(["2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"]),
            "recv_time": pd.to_datetime(["2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"]),
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "venue": ["binance", "binance"],
            "source": ["spot", "spot"],
            "event_type": ["trade", "trade"],
            "seq": [1, 2],
            "price": [100.0, 105.0],
            "qty": [1.0, 1.0],
        }
    )
    checks = [
        SchemaValidationCheck(["event_time", "recv_time", "symbol", "venue", "source", "event_type"]),
        MissingnessCheck(["event_time", "recv_time", "symbol", "venue", "source", "event_type"]),
        ClockSkewCheck(2000),
        StalenessCheck(30_000),
        DuplicateCheck(),
        SequenceGapCheck(),
        TimeTravelCheck(),
        OutlierCheck(),
        BookSanityCheck(10_000),
        MicrostructureToxicityCheck(20_000),
        CrossSourceConsistencyCheck(100_000),
        HaltDetectionCheck(300),
    ]
    gate = QualityGate(
        checks=checks,
        mode="batch",
        watermark_ms=5000,
        run_id="test",
        output_clean_path=str(tmp_path / "clean"),
        output_flags_path=str(tmp_path / "flags"),
    )
    result = gate.run_batch(df)
    assert result["metrics"].exists()
