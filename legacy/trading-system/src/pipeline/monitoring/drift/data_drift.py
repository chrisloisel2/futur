from __future__ import annotations

import numpy as np
import pandas as pd

from domain.monitoring.drift import DataDriftReport, DriftMetric


def _psi(curr: np.ndarray, base: np.ndarray, bins: int = 10) -> float:
    """
    Population Stability Index (PSI).

    FIXED: PSI = sum( (curr_pct - base_pct) * ln(curr_pct / base_pct) )
    Must always be >= 0.

    Interpretation:
    - PSI < 0.1: No significant change
    - PSI 0.1-0.2: Small change, monitor
    - PSI > 0.2: Major shift, investigate/retrain
    """
    if len(curr) == 0 or len(base) == 0:
        return 0.0

    # Use baseline quantiles for binning
    q = np.linspace(0, 1, bins + 1)
    b_bins = np.quantile(base, q)

    # Ensure bins are unique (avoid edge cases)
    b_bins = np.unique(b_bins)
    if len(b_bins) < 2:
        return 0.0

    # Histogram both distributions using same bins
    c_counts, _ = np.histogram(curr, bins=b_bins)
    b_counts, _ = np.histogram(base, bins=b_bins)

    # Convert to percentages
    c_pct = c_counts / max(c_counts.sum(), 1)
    b_pct = b_counts / max(b_counts.sum(), 1)

    # PSI formula: avoid log(0) and ensure non-negative
    # Add small epsilon to avoid division by zero
    epsilon = 1e-10
    psi_per_bin = (c_pct - b_pct) * np.log((c_pct + epsilon) / (b_pct + epsilon))
    psi = np.sum(psi_per_bin)

    return float(abs(psi))  # Ensure non-negative


def _js(curr: np.ndarray, base: np.ndarray, bins: int = 20) -> float:
    if len(curr) == 0 or len(base) == 0:
        return 0.0
    hist_c, edges = np.histogram(curr, bins=bins, density=True)
    hist_b, _ = np.histogram(base, bins=edges, density=True)
    m = 0.5 * (hist_c + hist_b)
    kl1 = np.sum(hist_c * np.log((hist_c + 1e-9) / (m + 1e-9)))
    kl2 = np.sum(hist_b * np.log((hist_b + 1e-9) / (m + 1e-9)))
    return float(0.5 * (kl1 + kl2))


class DataDriftDetector:
    def __init__(self, config: dict):
        self.config = config
        self.psi_thresholds = config.get("psi_thresholds", {"warning": 0.1, "critical": 0.2})
        self.js_thresholds = config.get("js_thresholds", {"warning": 0.05, "critical": 0.1})

    def _determine_severity(self, by_symbol: dict) -> str:
        """Determine overall drift severity based on thresholds."""
        max_psi = 0.0
        max_js = 0.0

        for symbol_scores in by_symbol.values():
            for metric in symbol_scores.values():
                if hasattr(metric, 'psi'):
                    max_psi = max(max_psi, metric.psi)
                if hasattr(metric, 'js'):
                    max_js = max(max_js, metric.js)

        if max_psi > self.psi_thresholds["critical"] or max_js > self.js_thresholds["critical"]:
            return "CRITICAL"
        elif max_psi > self.psi_thresholds["warning"] or max_js > self.js_thresholds["warning"]:
            return "WARNING"
        else:
            return "OK"

    def compute(self, state_window: pd.DataFrame, baseline: pd.DataFrame) -> DataDriftReport:
        """
        Compute data drift metrics with proper thresholds and severity.

        FIXED: Now properly determines severity instead of always returning "OK"
        """
        if state_window.empty:
            return DataDriftReport(window=self.config.get("window", ""), by_symbol={}, severity="OK")

        by_symbol = {}

        for symbol, df in state_window.groupby("symbol"):
            scores = {}
            base_df = baseline[baseline["symbol"] == symbol] if not baseline.empty else pd.DataFrame()

            # Get features to monitor
            features_to_check = self.config.get("features", {}).get("x_fast", [])
            if not features_to_check:
                # Default: all numeric columns
                features_to_check = df.select_dtypes(include=[np.number]).columns.tolist()

            for feat in features_to_check:
                if feat not in df.columns:
                    continue

                curr = df[feat].dropna().to_numpy()
                base = base_df[feat].dropna().to_numpy() if feat in base_df.columns else np.array([])

                if base.size == 0:
                    # No baseline: cannot compute drift
                    continue

                # Compute drift metrics
                psi_val = _psi(curr, base) if curr.size > 0 else 0.0
                js_val = _js(curr, base) if curr.size > 0 else 0.0

                # KS test if scipy available
                ks_pval = 1.0
                try:
                    from scipy import stats
                    ks_stat, ks_pval = stats.ks_2samp(curr, base)
                except ImportError:
                    pass

                scores[feat] = DriftMetric(
                    psi=float(psi_val),
                    js=float(js_val),
                    ks_pvalue=float(ks_pval),
                    zshift=float(curr.mean() - base.mean()) if base.size > 0 else 0.0,
                    missing_rate=float(df[feat].isna().mean()),
                )

            by_symbol[symbol] = scores

        # Determine overall severity
        severity = self._determine_severity(by_symbol)

        return DataDriftReport(
            window=self.config.get("window", ""),
            by_symbol=by_symbol,
            severity=severity,
        )
