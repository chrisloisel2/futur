from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .base import LabPlugin, LabSpec


AUDIT_SUFFIXES = ("_available_ts_ns", "_receive_ts_ns")
AUDIT_COLUMNS = {"asof_ns"}


def _is_model_feature(column: str) -> bool:
    name = str(column)
    if name in AUDIT_COLUMNS:
        return False
    return not name.endswith(AUDIT_SUFFIXES)


def _numeric(frame: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    out = pd.DataFrame(index=frame.index)
    for c in columns:
        if c in frame and _is_model_feature(c):
            out[c] = pd.to_numeric(frame[c], errors="coerce")
    return out


class CrossVenuePlugin(LabPlugin):
    plugin_name = "cross_venue"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        disloc = [c for c in frame.columns if c.endswith("__price_dislocation_bps") or c.endswith("__dislocation_bps")]
        mids = [c for c in frame.columns if c.endswith("__price_mid")]
        out = _numeric(frame, disloc)
        if disloc:
            d = out[[c for c in disloc if c in out]]
            if len(d.columns):
                out["cross__max_dislocation_bps"] = d.max(axis=1)
                out["cross__min_dislocation_bps"] = d.min(axis=1)
                out["cross__dispersion_dislocation_bps"] = d.max(axis=1) - d.min(axis=1)
                out["cross__mean_abs_dislocation_bps"] = d.abs().mean(axis=1)
        for c in mids:
            x = pd.to_numeric(frame[c], errors="coerce")
            out[c + "__ret_1"] = np.log(x / x.shift(1)) * 1e4
        if "price_fair_value" in frame:
            fv = pd.to_numeric(frame["price_fair_value"], errors="coerce")
            out["cross__fair_value_ret_1"] = np.log(fv / fv.shift(1)) * 1e4
        return out


class EventMicrostructurePlugin(LabPlugin):
    plugin_name = "event_microstructure"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        tokens = (
            "signed_notional", "flow_imbalance", "cvd", "absorption",
            "interarrival_cv", "trades_per_second", "flow_acceleration",
            "flow_jerk", "ofi", "queue_imbalance", "cancel", "remove",
            "queue_pressure", "replenishment", "depletion", "book_event_intensity",
        )
        cols = [c for c in frame.columns if _is_model_feature(c) and any(t in c for t in tokens)]
        out = _numeric(frame, cols)
        signed = [c for c in out.columns if "signed_notional" in c and not c.startswith("event__cross_venue")]
        if signed:
            s = out[signed]
            out["event__cross_venue_signed_flow"] = s.sum(axis=1, min_count=1)
            out["event__signed_flow_dispersion"] = s.std(axis=1, ddof=0)
        return out


class ShockPropagationPlugin(LabPlugin):
    plugin_name = "shock_propagation"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        cols = [c for c in frame.columns if _is_model_feature(c) and any(t in c for t in ("spread_bps", "depth_", "notional_to_move", "dispersion_bps"))]
        out = _numeric(frame, cols)
        for c in list(out.columns):
            out[c + "__shock"] = out[c].diff()
        return out


class LeveragePlugin(LabPlugin):
    plugin_name = "leverage"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        tokens = ("open_interest", "funding", "basis", "premium", "liquidation")
        cols = [c for c in frame.columns if _is_model_feature(c) and any(t in c.lower() for t in tokens)]
        out = _numeric(frame, cols)

        price_col = "price_fair_value" if "price_fair_value" in frame else ("fair_value" if "fair_value" in frame else None)
        if price_col:
            p = pd.to_numeric(frame[price_col], errors="coerce")
            out["lev__price_ret_1"] = np.log(p / p.shift(1)) * 1e4

        # Derive each venue separately. Never pick an arbitrary first OI/funding
        # series and never sum raw OI across venues whose units can differ.
        oi_cols = [c for c in out.columns if c.endswith("__open_interest")]
        funding_cols = [c for c in out.columns if c.endswith("__funding")]
        basis_cols = [c for c in out.columns if c.endswith("__basis_bps")]
        premium_cols = [c for c in out.columns if c.endswith("__premium")]

        oi_changes = []
        for c in oi_cols:
            x = out[c]
            change = x.pct_change(fill_method=None)
            name = c + "__pct_change"
            out[name] = change
            oi_changes.append(name)
            if "lev__price_ret_1" in out:
                out[c + "__price_x_oi"] = out["lev__price_ret_1"] * change

        for c in funding_cols:
            out[c + "__change"] = out[c].diff()

        for c in basis_cols:
            velocity = out[c].diff()
            out[c + "__velocity"] = velocity
            out[c + "__acceleration"] = velocity.diff()

        for c in premium_cols:
            out[c + "__change"] = out[c].diff()

        if oi_changes:
            out["lev__median_oi_change"] = out[oi_changes].median(axis=1, skipna=True)
            out["lev__oi_change_dispersion"] = out[oi_changes].std(axis=1, ddof=0, skipna=True)
        if basis_cols:
            out["lev__basis_cross_venue_dispersion"] = out[basis_cols].std(axis=1, ddof=0, skipna=True)

        # Prefer already-causal plane summaries when present.
        for c in ("deriv__median_oi_change_pct", "deriv__basis_dispersion_bps"):
            if c in frame and _is_model_feature(c):
                out[c] = pd.to_numeric(frame[c], errors="coerce")
        return out


class FundingPlugin(LeveragePlugin):
    plugin_name = "funding_basis"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        out = super().build_features(frame, spec)
        candidates = [
            c for c in list(out.columns)
            if _is_model_feature(c)
            and (c.endswith("__funding") or c.endswith("__basis_bps") or c.endswith("__premium"))
        ]
        for c in candidates:
            mean = out[c].rolling(300, min_periods=50).mean()
            sd = out[c].rolling(300, min_periods=50).std(ddof=1)
            out[c + "__z300"] = (out[c] - mean) / sd.where(sd > 1e-12)
        return out


class WalletPlugin(LabPlugin):
    plugin_name = "wallet"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        cols = [
            c for c in frame.columns
            if _is_model_feature(c)
            and (c.startswith("wallet__") or "informed_wallet" in c)
        ]
        return _numeric(frame, cols)


class CrossAssetPlugin(LabPlugin):
    plugin_name = "cross_asset"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        cols = [
            c for c in frame.columns
            if _is_model_feature(c)
            and (c.startswith("cross_asset__") or "residual" in c or "innovation" in c or "beta" in c)
        ]
        out = _numeric(frame, cols)
        for c in list(out.columns):
            if "residual" in c or "innovation" in c:
                out[c + "__lag1"] = out[c].shift(1)
        return out


class OptionsPlugin(LabPlugin):
    plugin_name = "options"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        cols = [
            c for c in frame.columns
            if _is_model_feature(c)
            and any(t in c.lower() for t in ("iv_", "skew", "rr25", "butterfly", "term_structure", "option_oi", "gamma"))
        ]
        out = _numeric(frame, cols)
        for c in list(out.columns):
            out[c + "__change"] = out[c].diff()
        return out


class OnchainPlugin(LabPlugin):
    plugin_name = "onchain"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        cols = [
            c for c in frame.columns
            if _is_model_feature(c)
            and (c.startswith("onchain__") or any(t in c.lower() for t in ("exchange_netflow", "stablecoin", "whale_transfer")))
        ]
        return _numeric(frame, cols)


class ExecutionPlugin(LabPlugin):
    plugin_name = "execution"

    def build_features(self, frame: pd.DataFrame, spec: LabSpec) -> pd.DataFrame:
        cols = [
            c for c in frame.columns
            if _is_model_feature(c)
            and (c.startswith("exec__") or any(t in c.lower() for t in ("queue_ahead", "fill_probability", "markout", "latency", "spread_bps")))
        ]
        return _numeric(frame, cols)


PLUGIN_REGISTRY: Dict[str, LabPlugin] = {
    p.plugin_name: p
    for p in [
        CrossVenuePlugin(), EventMicrostructurePlugin(), ShockPropagationPlugin(),
        LeveragePlugin(), FundingPlugin(), WalletPlugin(), CrossAssetPlugin(),
        OptionsPlugin(), OnchainPlugin(), ExecutionPlugin(),
    ]
}
