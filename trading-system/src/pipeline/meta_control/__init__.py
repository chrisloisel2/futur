from __future__ import annotations

import json
from datetime import datetime
from typing import Dict

import pandas as pd

from domain.state.alloc import Alloc
from domain.state.meta import MetaControlState
from domain.signal.signal import TradeMode
from pipeline.meta_control.scaler import MetaScaler, MetaScalerConfig
from pipeline.meta_control.adaptive_thresholds import AdaptiveThresholds, AdaptiveThresholdsConfig
from pipeline.meta_control.leverage_engine import LeverageEngine, LeverageEngineConfig
from pipeline.meta_control.portfolio_router import PortfolioRouter, RouterConfig
from pipeline.meta_control.coherence import compute_coherence
from domain.risk.budgets import RiskBudgets
from domain.risk.scenarios import ScenarioState


class MetaController:
    def __init__(self, config: Dict):
        self.scaler = MetaScaler(MetaScalerConfig(**config.get("scaler", {})))
        self.thresholds = AdaptiveThresholds(AdaptiveThresholdsConfig(**config.get("thresholds", {})))
        self.leverage_engine = LeverageEngine(LeverageEngineConfig(**config.get("leverage", {})))
        self.router = PortfolioRouter(RouterConfig(**config.get("router", {})))
        self.threshold_state: Dict[str, float] = {}

    def step(self, states: Dict[str, pd.Series], signals: Dict[str, dict], risk_state: Dict, telemetry: Dict, prev_meta_state: Dict | None, budgets: RiskBudgets, scenario: ScenarioState, clusters: Dict[str, str], run_id: str, model_stack: str, feature_set: str) -> tuple[Alloc, MetaControlState]:
        perf_snapshot = telemetry.get("perf_snapshot", {})
        drift_snapshot = telemetry.get("drift_snapshot", {})
        self.threshold_state = self.thresholds.update_thresholds(perf_snapshot, drift_snapshot, self.threshold_state)
        combined_scale_raw = 0.0
        reasons = []
        last_scale = prev_meta_state.get("scale_smooth", 0.0) if prev_meta_state else 0.0
        dt_seconds = 60
        for sym, sig in signals.items():
            state_row = states.get(sym, pd.Series())
            scale_raw = self.scaler.compute_scale(sig, state_row, {})
            combined_scale_raw = max(combined_scale_raw, scale_raw)
        scale_smooth = self.scaler.smooth_scale(combined_scale_raw, last_scale, dt_seconds)
        leverage_target = self.leverage_engine.compute_leverage(scale_smooth, risk_state, list(states.values())[0] if states else {}, list(signals.values())[0] if signals else {})
        leverage_effective = self.leverage_engine.apply_caps(leverage_target, budgets, scenario)
        leverage_effective = self.leverage_engine.rate_limit(prev_meta_state.get("leverage_target", 0.0) if prev_meta_state else 0.0, leverage_effective, dt_seconds)
        # coherence using first signal
        first_sig = list(signals.values())[0] if signals else {}
        coherence_score = compute_coherence(first_sig.get("regime_probs", {}), first_sig.get("quantiles", {}), scenario.scenario_flags)
        costs_proxy = {s: telemetry.get("slippage_expected_bps", 0) / 10_000 for s in signals.keys()}
        scores = self.router.rank_assets(signals, states, costs_proxy)
        weights = self.router.allocate(scores, budgets, clusters)
        alloc = Alloc(
            event_time=datetime.utcnow(),
            run_id=run_id,
            model_stack=model_stack,
            feature_set=feature_set,
            scale=scale_smooth,
            leverage_target=leverage_effective,
            trade_mode=TradeMode.TAKER,
            asset_weights=weights,
            cooldowns={},
            thresholds=self.threshold_state,
            coherence_score=coherence_score,
            reasons=reasons,
        )
        meta_state = MetaControlState(
            coherence_score=coherence_score,
            scale_raw=combined_scale_raw,
            scale_smooth=scale_smooth,
            leverage_target=leverage_effective,
            leverage_cap_effective=leverage_effective,
            cooldown_seconds=int(self.threshold_state.get("cooldown_seconds", 0)),
            thresholds_active=self.threshold_state,
            router_selected_assets=list(weights.keys()),
            router_weights=weights,
            reasons=reasons,
        )
        return alloc, meta_state
