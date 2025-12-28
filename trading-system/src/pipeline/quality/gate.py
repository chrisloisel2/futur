from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from common.logging.setup import get_logger
from domain.state.quality import QualityDecision, QualityFlag
from infra.storage.object_store import S3ParquetWriter, write_clean_events, write_quality_flags
from infra.storage.timeseries_db import MongoBufferWriter
from pipeline.quality.checks import (
    BaseCheck,
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
from pipeline.quality.clock_sync import ClockSyncModel

logger = get_logger(__name__)


class QualityGate:
    def __init__(
        self,
        checks: Iterable[BaseCheck],
        mode: str,
        watermark_ms: int,
        run_id: str,
        output_clean_path: str,
        output_flags_path: str,
        quarantine_path: Optional[str] = None,
        mongo_writer: Optional[MongoBufferWriter] = None,
        check_version: int = 1,
    ) -> None:
        self.checks = list(checks)
        self.mode = mode
        self.watermark_ms = watermark_ms
        self.run_id = run_id
        self.output_clean_path = output_clean_path
        self.output_flags_path = output_flags_path
        self.quarantine_path = quarantine_path
        self.mongo_writer = mongo_writer
        self.writer = S3ParquetWriter()
        self.check_version = check_version
        self.clock_model = ClockSyncModel()
        self.critical_flags = {
            QualityFlag.SCHEMA_INVALID,
            QualityFlag.MISSING_FIELDS,
            QualityFlag.TIME_TRAVEL,
            QualityFlag.BOOK_INVALID,
            QualityFlag.CROSS_SOURCE_MISMATCH,
            QualityFlag.CLOCK_SKEW_HIGH,
        }

    def apply(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        if df_raw.empty:
            return df_raw
        df = df_raw.copy()
        df["quality_flags"] = 0
        df["is_valid"] = True
        df["decision"] = QualityDecision.ACCEPT.value
        df["quality_run_id"] = self.run_id
        df["check_version"] = self.check_version
        df = self.clock_model.align_event_time(df)
        df["late_event"] = False
        df["staleness_ms"] = 0
        if self.mode == "live":
            df["staleness_ms"] = self.clock_model.staleness(df).astype(int)
            df["late_event"] = df["staleness_ms"] > self.watermark_ms
        for check in self.checks:
            df = check.apply(df)
        # decisions
        for flag in self.critical_flags:
            critical_mask = (df["quality_flags"] & int(flag)) > 0
            df.loc[critical_mask, "is_valid"] = False
            df.loc[critical_mask, "decision"] = QualityDecision.REJECT.value
        df.loc[df.get("duplicate", False) & df["decision"].eq(QualityDecision.ACCEPT.value), "decision"] = QualityDecision.QUARANTINE.value
        df.loc[df.get("late_event", False), "quality_flags"] = df.loc[df.get("late_event", False), "quality_flags"].astype(int) | int(QualityFlag.LATE_EVENT)
        df.loc[df["late_event"], "decision"] = QualityDecision.QUARANTINE.value
        return df

    def emit_metrics(self, df_clean: pd.DataFrame) -> Dict[str, float]:
        if df_clean.empty:
            return {}
        total = len(df_clean)
        invalid = (~df_clean["is_valid"]).sum()
        duplicate = df_clean.get("duplicate", pd.Series(dtype=bool)).sum()
        return {
            "total": float(total),
            "invalid": float(invalid),
            "duplicate": float(duplicate),
            "invalid_rate": float(invalid / total) if total else 0.0,
        }

    def run_batch(self, df_raw: pd.DataFrame) -> Dict[str, Path]:
        df_clean = self.apply(df_raw)
        dt = pd.to_datetime(df_clean["event_time_aligned"]).dt.strftime("%Y-%m-%d")
        df_clean["dt"] = dt
        write_clean_events(df_clean, self.output_clean_path, partition_cols=["dt", "symbol", "venue", "source"])
        flags = df_clean[["event_time", "symbol", "venue", "quality_flags", "is_valid", "staleness_ms", "quality_run_id"]].copy()
        flags["gate_run_id"] = self.run_id
        flags["tradeable"] = flags["is_valid"]
        flags["data_ok"] = ~((df_clean["quality_flags"] & int(QualityFlag.SCHEMA_INVALID)) > 0)
        flags["microstructure_ok"] = ~((df_clean["quality_flags"] & int(QualityFlag.MICROSTRUCTURE_TOXIC)) > 0)
        flags["cross_source_ok"] = ~((df_clean["quality_flags"] & int(QualityFlag.CROSS_SOURCE_MISMATCH)) > 0)
        flags["stale"] = (df_clean["quality_flags"] & int(QualityFlag.STALE_EVENT)) > 0
        flags["halted"] = (df_clean["quality_flags"] & int(QualityFlag.HALT_DETECTED)) > 0
        flags["toxic"] = (df_clean["quality_flags"] & int(QualityFlag.MICROSTRUCTURE_TOXIC)) > 0
        flags["skew_ewma_ms"] = df_clean.get("skew_ewma_ms", 0)
        flags["dt"] = dt
        write_quality_flags(flags, self.output_flags_path, partition_cols=["dt", "symbol", "venue"])
        if self.quarantine_path:
            rejected = df_clean[df_clean["decision"] == QualityDecision.REJECT.value]
            if not rejected.empty:
                write_clean_events(rejected, self.quarantine_path, partition_cols=["dt", "symbol", "venue", "source"])
        metrics = self.emit_metrics(df_clean)
        out_dir = Path(f"artifacts/quality_gate/{self.run_id}")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
        (out_dir / "report.md").write_text(self._report(df_clean, metrics))
        invalid_path = out_dir / "examples_invalid.parquet"
        df_clean[~df_clean["is_valid"]].head(100).to_parquet(invalid_path, index=False)
        return {
            "metrics": out_dir / "metrics.json",
            "report": out_dir / "report.md",
            "invalid": invalid_path,
        }

    def run_live(self, df_raw: pd.DataFrame) -> pd.DataFrame:
        df_clean = self.apply(df_raw)
        if self.mongo_writer:
            snapshots = df_clean[["event_time", "symbol", "venue", "quality_flags", "staleness_ms"]].copy()
            self.mongo_writer.write_quality_snapshots(snapshots)
        return df_clean

    def _report(self, df: pd.DataFrame, metrics: Dict[str, float]) -> str:
        lines = ["# Quality Gate Report", f"Run: {self.run_id}", "", "## Metrics"]
        for k, v in metrics.items():
            lines.append(f"- {k}: {v}")
        lines.append("")
        lines.append("## Flags count")
        for flag in QualityFlag:
            mask = (df["quality_flags"] & int(flag)) > 0
            lines.append(f"- {flag.name}: {int(mask.sum())}")
        return "\n".join(lines) + "\n"
