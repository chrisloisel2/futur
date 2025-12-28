# Quality Gate (Hard)

## Checks
- SchemaValidationCheck: required columns present and non-null
- MissingnessCheck: missing critical fields
- ClockSkewCheck: absolute skew beyond max
- StalenessCheck: staleness_ms exceeds threshold
- DuplicateCheck: duplicate keys
- SequenceGapCheck: gaps in seq/update_id
- TimeTravelCheck: event_time_aligned not monotonic
- OutlierCheck: robust z-score on price/qty
- BookSanityCheck: spread/ordering/depth checks
- MicrostructureToxicityCheck: spread explosion
- CrossSourceConsistencyCheck: spot/futures/index diff bounds
- HaltDetectionCheck: prolonged gaps in trades

Critical flags trigger REJECT: SCHEMA_INVALID, MISSING_FIELDS, TIME_TRAVEL, BOOK_INVALID, CROSS_SOURCE_MISMATCH, CLOCK_SKEW_HIGH.

## Parquet outputs
- CleanEventStream: partition dt, symbol, venue, source; includes quality_flags (int64), is_valid (bool), decision (string), staleness_ms, late_event, duplicate, outlier, schema_ok, skew_ms, skew_ewma_ms, check_version, quality_run_id plus event-specific fields.
- QualityFlags snapshots: event_time, symbol, venue, quality_flags, tradeable, data_ok, microstructure_ok, cross_source_ok, stale, halted, toxic, skew_ewma_ms, staleness_ms, gate_run_id; partitioned by dt, symbol, venue.
- Quarantine: same schema as clean events with decision=REJECT.

## Examples
- Late book update: staleness_ms > watermark_ms → LATE_EVENT flag, decision=QUARANTINE.
- Bad schema: missing price on trade → SCHEMA_INVALID flag, decision=REJECT.
- Spread spike: spread_bps > threshold → SPREAD_ANOMALY + MICROSTRUCTURE_TOXIC; if critical book invalid → REJECT.
