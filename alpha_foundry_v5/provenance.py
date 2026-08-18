from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional, Tuple

import pandas as pd


MANIFEST_NAME = "FEATURE_PROVENANCE.json"
META_COLUMNS = {"asof_ns", "symbol"}
META_SUFFIXES = ("_available_ts_ns",)

# Every feature emitted by EventTradePlane must match this contract unless it
# is explicit audit metadata. Keep event-count-window statistics here as well
# as clock-window statistics: both are built only from receive-time-admitted
# book/trade records and are governed by the same availability plane.
EVENT_TRADE_TOKENS = (
    "trade_count", "gross_notional", "signed_notional", "flow_imbalance",
    "trades_per_second", "interarrival_cv", "impact_bps", "absorption",
    "trade_size_entropy", "large_trade_fraction",
    "aggregate_fraction", "individual_fraction", "cvd", "flow_acceleration",
    "flow_jerk", "book_event_count", "_add_count", "_add_intensity",
    "_modify_count", "_modify_intensity", "_update_count", "_update_intensity",
    "_remove_count", "_remove_intensity", "_cancel_count", "_cancel_intensity",
    "replenishment_imbalance", "removal_imbalance", "cancellation_imbalance",
    "depletion_pressure", "book_event_intensity", "trade_receive_age_ms",
    "book_event_receive_age_ms",
)
DERIVATIVE_TOKENS = (
    "open_interest", "funding", "premium", "basis_", "liquidation",
    "__mark", "__index", "mark_receive_age_ms", "index_receive_age_ms", "derivatives__",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_digest(payload: Mapping[str, object]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(body)


def _parquet_columns(root: Path) -> Tuple[str, ...]:
    parts = sorted(root.glob("part-*.parquet"))
    if not parts:
        raise ValueError("no part-*.parquet under %s" % root)
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("pyarrow is required to inspect parquet schema") from exc
    return tuple(str(x) for x in pq.ParquetFile(str(parts[0])).schema_arrow.names)


def _is_metadata(column: str) -> bool:
    return column in META_COLUMNS or column.endswith(META_SUFFIXES)


def _new_feature_origin(column: str) -> Optional[Tuple[str, Tuple[str, ...], str]]:
    name = str(column)
    lower = name.lower()
    if name.startswith("lev__") or "liquidation_to_depth" in lower:
        return (
            "cross_plane",
            ("book__available_ts_ns", "derivatives__available_ts_ns"),
            "derived only after book and derivative planes are advanced to the same asof_ns",
        )
    if name.startswith("event__") or any(token in lower for token in EVENT_TRADE_TOKENS):
        return (
            "event_trade",
            ("event_trade__available_ts_ns",),
            "receive-time replay admits only book/trade records with receive_ts_ns <= asof_ns",
        )
    if name.startswith("deriv__") or any(token in lower for token in DERIVATIVE_TOKENS):
        return (
            "derivatives",
            ("derivatives__available_ts_ns",),
            "receive-time replay admits only derivative records with receive_ts_ns <= asof_ns",
        )
    return None


def build_feature_provenance_manifest(tensor_dir: str, base_tape: str) -> Dict[str, object]:
    tensor_root = Path(tensor_dir)
    base_root = Path(base_tape)
    tensor_columns = _parquet_columns(tensor_root)
    base_columns = set(_parquet_columns(base_root))
    features = {}
    unknown = []
    for column in tensor_columns:
        if _is_metadata(column):
            continue
        if column in base_columns:
            features[column] = {
                "origin": "base_state_tape",
                "governing_clocks": [],
                "proof_method": "inherited from the separately validated causal Market Physics state tape",
            }
            continue
        origin = _new_feature_origin(column)
        if origin is None:
            unknown.append(column)
            continue
        domain, clocks, method = origin
        features[column] = {
            "origin": domain,
            "governing_clocks": list(clocks),
            "proof_method": method,
        }
    payload = {
        "version": 1,
        "tensor_dir": str(tensor_root),
        "base_tape": str(base_root),
        "tensor_columns": len(tensor_columns),
        "feature_columns": len(features),
        "features": features,
        "unclassified_columns": sorted(unknown),
        "policy": "every research feature must have an explicit origin; non-base derived features must declare governing availability clocks",
    }
    payload["manifest_digest"] = _json_digest(payload)
    return payload


def write_feature_provenance_manifest(tensor_dir: str, base_tape: str) -> Dict[str, object]:
    payload = build_feature_provenance_manifest(tensor_dir, base_tape)
    if payload["unclassified_columns"]:
        unknown = payload["unclassified_columns"]
        raise ValueError(
            "unclassified tensor columns (%d): %s"
            % (len(unknown), unknown[:20])
        )
    path = Path(tensor_dir) / MANIFEST_NAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def load_feature_provenance_manifest(path_or_dir: str) -> Optional[Dict[str, object]]:
    path = Path(path_or_dir)
    if path.is_dir():
        path = path / MANIFEST_NAME
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    claimed = str(payload.get("manifest_digest") or "")
    body = dict(payload)
    body.pop("manifest_digest", None)
    if not claimed or claimed != _json_digest(body):
        raise ValueError("feature provenance manifest digest mismatch")
    return payload


@dataclass(frozen=True)
class FeatureProvenanceAuditResult:
    total_frame_features: int
    declared_features: int
    undeclared_features: Tuple[str, ...]
    missing_clock_columns: Tuple[str, ...]
    empty_clock_columns: Tuple[str, ...]
    manifest_digest: str

    @property
    def clean(self) -> bool:
        return not self.undeclared_features and not self.missing_clock_columns and not self.empty_clock_columns


def audit_feature_provenance(frame: pd.DataFrame, manifest: Mapping[str, object]) -> FeatureProvenanceAuditResult:
    declared = dict(manifest.get("features") or {})
    frame_features = tuple(
        str(c) for c in frame.columns
        if not _is_metadata(str(c))
    )
    undeclared = tuple(sorted(c for c in frame_features if c not in declared))
    missing_clocks = set()
    empty_clocks = set()
    for feature, spec in declared.items():
        if feature not in frame.columns:
            continue
        clocks = tuple(str(x) for x in (spec.get("governing_clocks") or ()))
        if not clocks:
            continue
        feature_active = bool(frame[feature].notna().any())
        if not feature_active:
            continue
        for clock in clocks:
            if clock not in frame.columns:
                missing_clocks.add(clock)
            elif not bool(pd.to_numeric(frame[clock], errors="coerce").notna().any()):
                empty_clocks.add(clock)
    return FeatureProvenanceAuditResult(
        total_frame_features=len(frame_features),
        declared_features=len(declared),
        undeclared_features=undeclared,
        missing_clock_columns=tuple(sorted(missing_clocks)),
        empty_clock_columns=tuple(sorted(empty_clocks)),
        manifest_digest=str(manifest.get("manifest_digest") or ""),
    )
