from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel

from common.logging.setup import get_logger

logger = get_logger(__name__)


class ValidationConfig(BaseModel):
    report_path: str = "artifacts/validation"
    label_set: str = "v1"
    feature_set: str = "default"


class ValidationSuite:
    def __init__(self, config: ValidationConfig):
        self.config = config

    def run(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
        trades: pd.DataFrame,
        equity: pd.DataFrame,
        run_id: str,
        output_dir: Optional[Path] = None,
    ) -> Dict[str, float]:
        out_dir = Path(output_dir or self.config.report_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        metrics = {
            "leakage_rate": self._leakage_rate(features, labels),
            "stability": self._stability(trades),
            "turnover": self._turnover(trades),
            "capacity": self._capacity(trades),
            "slippage_sensitivity": self._slippage_sensitivity(trades),
            "monotonicity": self._monotonicity(features, labels),
            "overfitting_risk": self._overfitting_risk(trades, labels),
        }
        (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2))
        self._write_report(metrics, out_dir / "validation_report.md", run_id)
        logger.info({"msg": "validation complete", "run_id": run_id})
        return metrics

    def _leakage_rate(self, features: pd.DataFrame, labels: pd.DataFrame) -> float:
        if features.empty or labels.empty:
            return 0.0
        merged = labels.merge(features, left_on=["t0", "symbol"], right_on=["event_time", "symbol"], how="left")
        if "event_time" not in merged.columns:
            return 0.0
        violations = merged["event_time"] > merged["t0"]
        return float(violations.mean()) if not violations.isna().all() else 0.0

    def _stability(self, trades: pd.DataFrame) -> float:
        if trades.empty:
            return 0.0
        returns = trades.get("net_pnl", trades.get("gross_pnl", pd.Series(dtype=float)))
        if returns.empty:
            return 0.0
        return float(returns.rolling(window=min(5, len(returns)), min_periods=1).std().mean())

    def _turnover(self, trades: pd.DataFrame) -> float:
        if trades.empty:
            return 0.0
        trades = trades.copy()
        trades["day"] = pd.to_datetime(trades["t_entry"]).dt.date
        counts = trades.groupby("day").size()
        return float(counts.mean())

    def _capacity(self, trades: pd.DataFrame) -> float:
        if trades.empty:
            return 0.0
        notionals = trades["qty"].abs() * trades["entry_px"]
        return float(notionals.quantile(0.95))

    def _slippage_sensitivity(self, trades: pd.DataFrame) -> float:
        if trades.empty or "slippage" not in trades:
            return 0.0
        gross_col = "gross_pnl" if "gross_pnl" in trades else "net_pnl"
        gross = trades[gross_col].replace(0, np.nan)
        ratio = trades["slippage"] / gross.abs()
        return float(ratio.fillna(0).mean())

    def _monotonicity(self, features: pd.DataFrame, labels: pd.DataFrame) -> float:
        if features.empty or labels.empty:
            return 0.0
        merged = labels.merge(features, left_on=["t0", "symbol"], right_on=["event_time", "symbol"], how="left")
        prob_cols = [c for c in merged.columns if "prob" in c]
        if not prob_cols:
            return 0.0
        probs = merged[prob_cols[0]]
        returns = merged["return_fwd"] if "return_fwd" in merged else pd.Series(dtype=float)
        if probs.isna().all() or returns.isna().all():
            return 0.0
        corr = probs.corr(returns)
        return float(corr) if not np.isnan(corr) else 0.0

    def _overfitting_risk(self, trades: pd.DataFrame, labels: pd.DataFrame) -> float:
        if trades.empty or labels.empty:
            return 0.0
        win_rate = (trades.get("net_pnl", trades["gross_pnl"]) > 0).mean()
        horizon_counts = labels.groupby("horizon_s").size()
        diversity = horizon_counts.count() / max(horizon_counts.sum(), 1)
        return float(max(0.0, win_rate - diversity))

    def _write_report(self, metrics: Dict[str, float], path: Path, run_id: str) -> None:
        lines = [
            f"# Validation Report for {run_id}",
            "",
            "| metric | value |",
            "| --- | --- |",
        ]
        for key, val in metrics.items():
            lines.append(f"| {key} | {val:.6f} |")
        path.write_text("\n".join(lines) + "\n")
