"""Data quality validation and monitoring."""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DataQualityReport:
    """Report on data quality issues."""

    total_rows: int
    missing_values: Dict[str, int]
    temporal_gaps: List[Dict[str, str]]
    ohlc_violations: List[int]
    extreme_outliers: List[Dict[str, any]]
    volatility_spikes: List[Dict[str, any]]
    is_valid: bool
    warnings: List[str]
    errors: List[str]


class DataQualityValidator:
    """Validate data quality for trading pipelines."""

    def __init__(
        self,
        max_gap_multiplier: float = 2.0,
        volatility_threshold: float = 10.0,
        price_change_threshold: float = 0.5,
    ) -> None:
        """
        Initialize validator.

        Args:
            max_gap_multiplier: Max gap as multiple of expected timeframe
            volatility_threshold: Threshold for extreme volatility (% per period)
            price_change_threshold: Threshold for extreme price changes (0-1)
        """
        self.max_gap_multiplier = max_gap_multiplier
        self.volatility_threshold = volatility_threshold
        self.price_change_threshold = price_change_threshold

    def validate(
        self, df: pd.DataFrame, timeframe: Optional[str] = None
    ) -> DataQualityReport:
        """
        Run all validation checks on DataFrame.

        Args:
            df: DataFrame with OHLCV data
            timeframe: Expected timeframe (e.g., '1h', '1d')
        """
        warnings = []
        errors = []

        # Check missing values
        missing = self._check_missing_values(df)
        if any(missing.values()):
            warnings.append(f"Missing values detected: {missing}")

        # Check temporal gaps
        gaps = self._check_temporal_gaps(df, timeframe)
        if gaps:
            warnings.append(f"Found {len(gaps)} temporal gaps")

        # Check OHLC consistency
        ohlc_violations = self._check_ohlc_consistency(df)
        if ohlc_violations:
            errors.append(f"Found {len(ohlc_violations)} OHLC violations")

        # Check extreme outliers
        outliers = self._check_extreme_outliers(df)
        if outliers:
            warnings.append(f"Found {len(outliers)} extreme outliers")

        # Check volatility spikes
        vol_spikes = self._check_volatility_spikes(df)
        if vol_spikes:
            warnings.append(f"Found {len(vol_spikes)} volatility spikes")

        is_valid = len(errors) == 0

        report = DataQualityReport(
            total_rows=len(df),
            missing_values=missing,
            temporal_gaps=gaps,
            ohlc_violations=ohlc_violations,
            extreme_outliers=outliers,
            volatility_spikes=vol_spikes,
            is_valid=is_valid,
            warnings=warnings,
            errors=errors,
        )

        self._log_report(report)
        return report

    def _check_missing_values(self, df: pd.DataFrame) -> Dict[str, int]:
        """Check for missing values in each column."""
        return df.isnull().sum().to_dict()

    def _check_temporal_gaps(
        self, df: pd.DataFrame, timeframe: Optional[str]
    ) -> List[Dict[str, str]]:
        """Check for gaps in timestamp sequence."""
        if "timestamp" not in df.columns:
            return []

        df_sorted = df.sort_values("timestamp")
        time_diffs = df_sorted["timestamp"].diff()

        if timeframe:
            expected_diff = pd.Timedelta(timeframe)
            max_allowed = expected_diff * self.max_gap_multiplier

            gaps = time_diffs[time_diffs > max_allowed]
            return [
                {
                    "index": idx,
                    "gap_size": str(gap),
                    "timestamp": str(df_sorted.loc[idx, "timestamp"]),
                }
                for idx, gap in gaps.items()
            ]

        # If no timeframe, just detect unusually large gaps
        median_diff = time_diffs.median()
        if pd.isna(median_diff):
            return []

        large_gaps = time_diffs[time_diffs > median_diff * self.max_gap_multiplier]
        return [
            {
                "index": idx,
                "gap_size": str(gap),
                "timestamp": str(df_sorted.loc[idx, "timestamp"]),
            }
            for idx, gap in large_gaps.items()
        ]

    def _check_ohlc_consistency(self, df: pd.DataFrame) -> List[int]:
        """Check OHLC data consistency (high >= low, etc.)."""
        violations = []

        if not all(col in df.columns for col in ["open", "high", "low", "close"]):
            return violations

        # Check high >= low
        invalid_high_low = df[df["high"] < df["low"]].index.tolist()
        violations.extend(invalid_high_low)

        # Check high >= open, close
        invalid_high = df[(df["high"] < df["open"]) | (df["high"] < df["close"])].index.tolist()
        violations.extend(invalid_high)

        # Check low <= open, close
        invalid_low = df[(df["low"] > df["open"]) | (df["low"] > df["close"])].index.tolist()
        violations.extend(invalid_low)

        return sorted(set(violations))

    def _check_extreme_outliers(self, df: pd.DataFrame) -> List[Dict[str, any]]:
        """Detect extreme price movements (potential data errors)."""
        outliers = []

        if "close" not in df.columns:
            return outliers

        pct_change = df["close"].pct_change().abs()

        extreme = pct_change[pct_change > self.price_change_threshold]

        for idx, change in extreme.items():
            outliers.append(
                {
                    "index": idx,
                    "price_change_pct": float(change * 100),
                    "close": float(df.loc[idx, "close"]) if idx in df.index else None,
                }
            )

        return outliers

    def _check_volatility_spikes(self, df: pd.DataFrame) -> List[Dict[str, any]]:
        """Detect volatility spikes (> threshold% per period)."""
        spikes = []

        if not all(col in df.columns for col in ["high", "low", "close"]):
            return spikes

        # Calculate intrabar volatility
        df_copy = df.copy()
        df_copy["range_pct"] = ((df_copy["high"] - df_copy["low"]) / df_copy["close"]) * 100

        extreme_vol = df_copy[df_copy["range_pct"] > self.volatility_threshold]

        for idx, row in extreme_vol.iterrows():
            spikes.append(
                {
                    "index": idx,
                    "volatility_pct": float(row["range_pct"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                }
            )

        return spikes

    def _log_report(self, report: DataQualityReport) -> None:
        """Log data quality report."""
        if report.is_valid:
            if report.warnings:
                logger.warning(f"Data quality: VALID with warnings")
                for warning in report.warnings:
                    logger.warning(f"  - {warning}")
            else:
                logger.info(f"Data quality: VALID ({report.total_rows} rows)")
        else:
            logger.error(f"Data quality: INVALID")
            for error in report.errors:
                logger.error(f"  - {error}")
            for warning in report.warnings:
                logger.warning(f"  - {warning}")
