from domain.state.quality import QualityFlag, QualityFlagsSnapshot


def test_quality_flag_bitmask_roundtrip():
    flags = QualityFlag.SCHEMA_INVALID | QualityFlag.DUPLICATE_EVENT
    snap = QualityFlagsSnapshot(
        event_time="2024-01-01T00:00:00Z",
        symbol="BTCUSDT",
        venue="binance",
        quality_flags=int(flags),
        tradeable=False,
        data_ok=False,
        microstructure_ok=True,
        cross_source_ok=True,
        stale=False,
        halted=False,
        toxic=False,
        skew_ewma_ms=0,
        staleness_ms=0,
        gate_run_id="run",
    )
    assert snap.to_int() == int(flags)
    snap2 = QualityFlagsSnapshot.from_int(int(flags), **{k: v for k, v in snap.dict().items() if k != 'quality_flags'})
    assert snap2.quality_flags == int(flags)
