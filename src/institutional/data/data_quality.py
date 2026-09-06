"""
src/institutional/data/data_quality.py
─────────────────────────────────────────────────────────────────────────────
Rapports de qualité de données par asset/source.

DataQualityChecker produit un DataQualityReport (voir contracts.py).
Aucune feature ne doit être calculée avant que le rapport soit validé.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.institutional.contracts import DataQualityReport

logger = logging.getLogger(__name__)

# Seuils d'alerte configurables
OUTLIER_ZSCORE_THRESHOLD = 6.0
MAX_PRICE_JUMP_LOG = 0.30    # 30% de variation → outlier log-return


class DataQualityChecker:
    """
    Vérifie la qualité d'un DataFrame OHLCV et produit un DataQualityReport.

    Usage
    -----
    checker = DataQualityChecker(df, asset="BTCUSDT", source="futures", timeframe="1h")
    report = checker.run()
    assert report.is_valid, report.summary()
    """

    def __init__(
        self,
        df: pd.DataFrame,
        asset: str,
        source: str,
        timeframe: str,
        price_col: str = "close",
        volume_col: str = "volume",
        expected_freq_minutes: int = 60,
        max_stale_minutes: float = 1500.0,   # 25h pour tolérer les frontières d'années
        max_missing_rate: float = 0.05,
    ):
        self.df = df
        self.asset = asset
        self.source = source
        self.timeframe = timeframe
        self.price_col = price_col
        self.volume_col = volume_col
        self.expected_freq = expected_freq_minutes
        self.max_stale = max_stale_minutes
        self.max_missing = max_missing_rate

    def run(self) -> DataQualityReport:
        issues: List[str] = []
        df = self.df
        n_total = len(df)

        # 1. Index
        if not isinstance(df.index, pd.DatetimeIndex):
            issues.append("Index non DatetimeIndex")

        # 2. Timestamps
        if df.index.duplicated().any():
            n_dup = df.index.duplicated().sum()
            issues.append(f"{n_dup} timestamps dupliqués")

        duplicate_count = int(df.index.duplicated().sum())

        # 3. Monotonie
        if not df.index.is_monotonic_increasing:
            issues.append("Index non monotone")

        # 4. Missing values
        ohlcv_cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        missing_counts = df[ohlcv_cols].isna().sum()
        total_cells = n_total * len(ohlcv_cols)
        missing_rate = float(missing_counts.sum() / max(total_cells, 1))

        if missing_rate > self.max_missing:
            issues.append(f"Taux NaN {missing_rate:.2%} > seuil {self.max_missing:.0%}")

        # 5. Trous temporels
        if len(df) > 1:
            gaps_minutes = df.index.to_series().diff().dt.total_seconds().div(60).dropna()
            stale_intervals = int((gaps_minutes > self.max_stale).sum())
            max_gap = float(gaps_minutes.max())
        else:
            stale_intervals = 0
            max_gap = 0.0

        if max_gap > self.max_stale:
            issues.append(f"Trou max {max_gap:.0f}min > seuil {self.max_stale:.0f}min")

        # 6. OHLCV consistency
        if all(c in df.columns for c in ["open", "high", "low", "close"]):
            bad_high = (df["high"] < df[["open", "close"]].max(axis=1)).sum()
            bad_low = (df["low"] > df[["open", "close"]].min(axis=1)).sum()
            if bad_high:
                issues.append(f"{bad_high} barres high < max(o,c)")
            if bad_low:
                issues.append(f"{bad_low} barres low > min(o,c)")

        # 7. Outliers (log-returns)
        outlier_count = 0
        if self.price_col in df.columns:
            log_ret = np.log(df[self.price_col] / df[self.price_col].shift(1)).dropna()
            outlier_count = int((log_ret.abs() > MAX_PRICE_JUMP_LOG).sum())
            if outlier_count:
                issues.append(f"{outlier_count} outliers log-return > {MAX_PRICE_JUMP_LOG:.0%}")

        # 8. Valid rows (sans NaN dans les colonnes critiques)
        if ohlcv_cols:
            valid_mask = df[ohlcv_cols].notna().all(axis=1)
            valid_rows = int(valid_mask.sum())
            rejected_rows = n_total - valid_rows
        else:
            valid_rows = n_total
            rejected_rows = 0

        first_ts = df.index.min() if len(df) > 0 else None
        last_ts = df.index.max() if len(df) > 0 else None

        return DataQualityReport(
            asset=self.asset,
            source=self.source,
            timeframe=self.timeframe,
            missing_rate=missing_rate,
            duplicate_count=duplicate_count,
            stale_intervals=stale_intervals,
            max_gap_minutes=max_gap,
            outlier_count=outlier_count,
            first_timestamp=first_ts,
            last_timestamp=last_ts,
            rows=n_total,
            valid_rows=valid_rows,
            rejected_rows=rejected_rows,
            issues=issues,
        )


def run_quality_suite(
    datasets: Dict[str, pd.DataFrame],
    source: str = "unknown",
    timeframe: str = "1h",
    save_path: Optional[Path] = None,
) -> Dict[str, DataQualityReport]:
    """
    Lance les checks qualité sur un dict {asset: DataFrame}.
    Sauvegarde les rapports en JSON si save_path est fourni.
    """
    reports: Dict[str, DataQualityReport] = {}

    for asset, df in datasets.items():
        checker = DataQualityChecker(df, asset=asset, source=source, timeframe=timeframe)
        report = checker.run()
        reports[asset] = report
        logger.info(report.summary())

    if save_path:
        save_path = Path(save_path)
        save_path.mkdir(parents=True, exist_ok=True)
        summary = {a: r.to_dict() for a, r in reports.items()}
        (save_path / "data_quality_report.json").write_text(
            json.dumps(summary, indent=2, default=str)
        )

    n_ok = sum(1 for r in reports.values() if r.is_valid)
    logger.info(f"Quality suite: {n_ok}/{len(reports)} assets OK")
    return reports


def assert_all_valid(reports: Dict[str, DataQualityReport]) -> None:
    """Lève une exception si un asset n'est pas valide."""
    failed = [a for a, r in reports.items() if not r.is_valid]
    if failed:
        msgs = [f"  {a}: {reports[a].issues}" for a in failed]
        raise ValueError("Données invalides — pipeline bloqué :\n" + "\n".join(msgs))
