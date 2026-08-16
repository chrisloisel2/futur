from __future__ import annotations

from typing import Dict

from .contracts import DataDomain, ExecutionStyle, MechanismSpec, ModelFamily


def _lab(
    lab_id,
    name,
    hypothesis,
    payer,
    domains,
    targets,
    horizons_ms,
    model_families,
    execution_styles,
    independence_key,
    notes="",
):
    return MechanismSpec(
        lab_id=lab_id,
        name=name,
        hypothesis=hypothesis,
        payer=payer,
        domains=tuple(domains),
        targets=tuple(targets),
        horizons_ms=tuple(int(v) for v in horizons_ms),
        model_families=tuple(model_families),
        execution_styles=tuple(execution_styles),
        independence_key=independence_key,
        notes=notes,
    )


LAB_REGISTRY: Dict[str, MechanismSpec] = {
    "A1": _lab("A1", "Cross-venue price discovery", "One venue incorporates public information before peers and transmits a directional innovation.", "Slower venues and stale quote takers", [DataDomain.BOOK, DataDomain.TRADE], ["loo_fair_value_return", "venue_response_return", "time_to_convergence"], [10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000], [ModelFamily.STATE_SPACE, ModelFamily.INFORMATION_SCREEN], [ExecutionStyle.TAKER, ExecutionStyle.MAKER, ExecutionStyle.HYBRID], "cross_venue_price_discovery"),
    "A2": _lab("A2", "Venue dislocation convergence", "Transient venue-specific price dislocations mean-revert toward a robust cross-venue anchor.", "Urgent venue-local flow", [DataDomain.BOOK], ["venue_dislocation_convergence", "loo_fair_value_return"], [100, 250, 500, 1000, 2000, 5000, 10000, 30000], [ModelFamily.ERROR_CORRECTION, ModelFamily.INFORMATION_SCREEN], [ExecutionStyle.TAKER, ExecutionStyle.HEDGE], "venue_dislocation_convergence"),
    "A3": _lab("A3", "Queue depletion hazard", "Conditional order arrival, cancel and execution intensities predict which side of the queue depletes first.", "Liquidity providers caught on the depleted side", [DataDomain.BOOK, DataDomain.TRADE], ["next_mid_move", "time_to_queue_depletion", "passive_fill_probability"], [10, 25, 50, 100, 250, 500, 1000, 2000, 5000], [ModelFamily.SURVIVAL, ModelFamily.POINT_PROCESS], [ExecutionStyle.MAKER, ExecutionStyle.HYBRID], "queue_depletion_hazard"),
    "A4": _lab("A4", "Liquidity replenishment and resilience", "The speed and asymmetry of depth refill after a sweep predicts short-horizon continuation versus rejection.", "Traders extrapolating a liquidity shock incorrectly", [DataDomain.BOOK, DataDomain.TRADE], ["refill_half_life", "post_sweep_return", "future_spread"], [100, 250, 500, 1000, 2000, 5000, 10000, 30000], [ModelFamily.SURVIVAL, ModelFamily.INFORMATION_SCREEN], [ExecutionStyle.MAKER, ExecutionStyle.FILTER], "liquidity_resilience"),
    "A5": _lab("A5", "Toxic trade flow and absorption", "Signed flow that produces unusually high or low price impact reveals informed pressure or hidden absorption.", "Late market-order followers and adverse-selected makers", [DataDomain.TRADE, DataDomain.BOOK], ["future_fair_value_return", "future_impact", "absorption_resolution"], [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000], [ModelFamily.POINT_PROCESS, ModelFamily.INFORMATION_SCREEN], [ExecutionStyle.TAKER, ExecutionStyle.MAKER, ExecutionStyle.FILTER], "toxic_trade_flow"),
    "A6": _lab("A6", "Liquidity-shock propagation", "A depth or spread shock on a leading venue propagates to other venues with a measurable impulse response.", "Slower venues and cross-venue inventory managers", [DataDomain.BOOK, DataDomain.CROSS_ASSET], ["cross_venue_depth_response", "future_loo_return", "future_volatility"], [100, 250, 500, 1000, 2000, 5000, 10000, 30000], [ModelFamily.STATE_SPACE, ModelFamily.GRAPH_TEMPORAL], [ExecutionStyle.TAKER, ExecutionStyle.FILTER], "liquidity_shock_propagation"),
    "A7": _lab("A7", "Liquidation cascade", "Forced liquidation flow becomes nonlinear when liquidations exceed available depth and open interest remains vulnerable.", "Leveraged positions forced to cross the spread", [DataDomain.DERIVATIVES, DataDomain.BOOK, DataDomain.TRADE], ["future_liquidation_intensity", "cascade_return", "cascade_duration"], [1000, 2000, 5000, 10000, 30000, 60000, 300000, 900000], [ModelFamily.POINT_PROCESS, ModelFamily.HMM], [ExecutionStyle.TAKER, ExecutionStyle.FILTER], "liquidation_cascade"),
    "A8": _lab("A8", "Leverage positioning topology", "Joint price, OI, funding, basis and signed-flow states distinguish new leverage from forced deleveraging.", "Crowded leveraged positioning", [DataDomain.DERIVATIVES, DataDomain.TRADE, DataDomain.SPOT], ["future_fair_value_return", "future_oi_change", "future_liquidation_intensity"], [10000, 30000, 60000, 300000, 900000, 3600000], [ModelFamily.HMM, ModelFamily.CHANGE_POINT], [ExecutionStyle.TAKER, ExecutionStyle.FILTER], "leverage_positioning"),
    "A9": _lab("A9", "Funding and basis convergence", "Extreme perp-spot basis and funding deviations converge when inventory and arbitrage capital can enter.", "Perp traders paying persistent carry", [DataDomain.DERIVATIVES, DataDomain.SPOT], ["basis_convergence", "carry_adjusted_return", "future_funding"], [60000, 300000, 900000, 3600000, 14400000], [ModelFamily.ERROR_CORRECTION, ModelFamily.STATE_SPACE], [ExecutionStyle.HEDGE, ExecutionStyle.MAKER], "funding_basis_convergence"),
    "A10": _lab("A10", "Funding settlement event", "Funding snapshots create predictable inventory adjustments and basis pressure around settlement boundaries.", "Traders forced to rebalance around funding settlement", [DataDomain.DERIVATIVES, DataDomain.SPOT, DataDomain.EVENT], ["pre_funding_return", "post_funding_return", "basis_jump"], [60000, 300000, 900000, 1800000, 3600000], [ModelFamily.INFORMATION_SCREEN, ModelFamily.CHANGE_POINT], [ExecutionStyle.HEDGE, ExecutionStyle.FILTER], "funding_settlement_event"),
    "A11": _lab("A11", "Hyperliquid informed wallet flow", "A persistent subset of public wallets exhibits positive forward markout and their aggregated flow leads broader price discovery.", "Less-informed counterparties interacting with informed public flow", [DataDomain.WALLET, DataDomain.TRADE, DataDomain.BOOK], ["wallet_markout", "informed_flow_return", "cross_venue_response"], [1000, 5000, 10000, 30000, 60000, 300000], [ModelFamily.HIERARCHICAL_BAYES, ModelFamily.TEMPORAL_TRANSFORMER], [ExecutionStyle.TAKER, ExecutionStyle.FILTER], "wallet_informed_flow"),
    "A12": _lab("A12", "Cross-asset causal propagation", "Innovations in leader assets predict residual returns in economically linked follower assets.", "Slow cross-asset repricing", [DataDomain.CROSS_ASSET, DataDomain.TRADE, DataDomain.BOOK], ["residual_return", "graph_response", "time_to_reprice"], [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000, 300000], [ModelFamily.GRAPH_TEMPORAL, ModelFamily.STATE_SPACE], [ExecutionStyle.TAKER, ExecutionStyle.HEDGE], "cross_asset_propagation"),
    "A13": _lab("A13", "Residual relative value", "Factor-neutral residuals exhibit temporary divergence and convergence independent of market beta.", "Asset-specific flow and temporary inventory imbalance", [DataDomain.CROSS_ASSET, DataDomain.SPOT, DataDomain.DERIVATIVES], ["factor_neutral_residual_return", "spread_half_life"], [60000, 300000, 900000, 3600000, 14400000], [ModelFamily.ERROR_CORRECTION, ModelFamily.STATE_SPACE], [ExecutionStyle.HEDGE], "residual_relative_value"),
    "A14": _lab("A14", "Options surface shock", "Changes in IV level, skew, term structure and options positioning lead spot/perp repricing under hedging pressure.", "Dealers and traders rehedging convex exposure", [DataDomain.OPTIONS, DataDomain.DERIVATIVES, DataDomain.SPOT], ["future_fair_value_return", "future_realized_vol", "skew_reversion"], [60000, 300000, 900000, 3600000, 14400000], [ModelFamily.STATE_SPACE, ModelFamily.CHANGE_POINT], [ExecutionStyle.HEDGE, ExecutionStyle.FILTER], "options_surface_shock"),
    "A15": _lab("A15", "On-chain and exchange flow", "Exchange deposits, withdrawals, stablecoin flows and large transfers shift future supply-demand balance.", "Participants slower to react to settlement-layer inventory movement", [DataDomain.ONCHAIN, DataDomain.EVENT, DataDomain.SPOT], ["future_fair_value_return", "exchange_inventory_change", "stablecoin_impulse"], [300000, 900000, 3600000, 14400000, 86400000], [ModelFamily.CHANGE_POINT, ModelFamily.HIERARCHICAL_BAYES], [ExecutionStyle.TAKER, ExecutionStyle.FILTER], "onchain_exchange_flow"),
    "A16": _lab("A16", "Execution alpha", "Queue position, local book state and flow predict fill probability and post-fill adverse selection.", "Counterparties demanding immediacy", [DataDomain.EXECUTION, DataDomain.BOOK, DataDomain.TRADE], ["passive_fill_probability", "time_to_fill", "post_fill_markout", "maker_edge"], [10, 25, 50, 100, 250, 500, 1000, 5000, 30000], [ModelFamily.SURVIVAL, ModelFamily.EXECUTION_MODEL], [ExecutionStyle.MAKER, ExecutionStyle.INVENTORY_SKEW], "execution_alpha"),
}


def get_lab_registry() -> Dict[str, MechanismSpec]:
    return dict(LAB_REGISTRY)
