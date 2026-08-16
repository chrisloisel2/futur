import numpy as np
import pandas as pd
import pytest

from alpha_foundry_v4.contracts import AlphaCandidate, PromotionEvidence
from alpha_foundry_v4.event_features import aggregate_trade_window, depletion_hazard, flow_dynamics
from alpha_foundry_v4.execution import expected_maker_edge_bps, expected_taker_edge_bps
from alpha_foundry_v4.experiment import ExperimentProtocol, ResearchStage, validate_stage_transition
from alpha_foundry_v4.foundry import AlphaFoundry, evaluate_evidence
from alpha_foundry_v4.leverage import classify_leverage_state, leverage_tensor
from alpha_foundry_v4.protocol import DEFAULT_GATES
from alpha_foundry_v4.registry import LAB_REGISTRY
from alpha_foundry_v4.target_factory import future_log_return, leave_one_venue_out_fair_value
from alpha_foundry_v4.wallets import informed_flow, wallet_intelligence_table


def good_evidence(edge=1.0, marginal=False):
    return PromotionEvidence(pit_clean=True, independent_forward=True, dsr=0.97, pbo=0.05, cost_x2_positive=True, delayed_entry_positive=True, top_contributors_removed_positive=True, same_sign_halves=True, recent_period_not_destructive=True, sleeve_pf=1.5, capacity_usd=300000.0, paper_live_positive=True, net_edge_bps=edge, mechanism_confirmed=True, marginal_portfolio_positive=marginal)


def candidate(cid, mechanism, pnl_name, edge=1.0, marginal=False):
    spec = LAB_REGISTRY[mechanism]
    return AlphaCandidate(candidate_id=cid, mechanism_id=mechanism, independence_key=spec.independence_key, pnl_series_name=pnl_name, evidence=good_evidence(edge=edge, marginal=marginal))


def test_registry_has_sixteen_distinct_labs_and_independence_keys():
    assert len(LAB_REGISTRY) == 16
    assert len({spec.independence_key for spec in LAB_REGISTRY.values()}) == 16
    assert {"A1", "A3", "A7", "A11", "A16"}.issubset(LAB_REGISTRY)


def test_future_return_is_strictly_forward():
    p = pd.Series([100.0, 101.0, 99.0])
    y = future_log_return(p, steps=1)
    assert y.iloc[0] == 1e4 * np.log(101.0 / 100.0)
    assert np.isnan(y.iloc[-1])


def test_leave_one_venue_out_excludes_source_venue():
    frame = pd.DataFrame({"a__price_mid": [100.0], "a__price_weight": [1.0], "b__price_mid": [110.0], "b__price_weight": [3.0], "c__price_mid": [999.0], "c__price_weight": [1000.0]})
    fv = leave_one_venue_out_fair_value(frame, "c", ["a", "b", "c"])
    assert fv.iloc[0] == 107.5


def test_trade_tape_features_and_flow_dynamics():
    trades = pd.DataFrame({"notional": [10.0, 20.0, 30.0], "side_sign": [1, -1, 1], "receive_ts_ns": [0, 500_000_000, 1_000_000_000]})
    out = aggregate_trade_window(trades)
    assert out["trade_count"] == 3.0
    assert out["gross_notional"] == 60.0
    assert out["signed_notional"] == 20.0
    dynamics = flow_dynamics(pd.Series([1.0, 3.0, 2.0]))
    assert dynamics.loc[1, "flow_acceleration"] == 2.0
    assert dynamics.loc[2, "flow_jerk"] == -3.0


def test_depletion_hazard_rises_with_outflow():
    low = depletion_hazard(100.0, cancel_rate=1.0, trade_rate=1.0, add_rate=1.5)
    high = depletion_hazard(100.0, cancel_rate=20.0, trade_rate=20.0, add_rate=1.5)
    assert high > low


def test_leverage_topology_labels_and_tensor():
    assert classify_leverage_state(1, 1) == "NEW_LONG_LEVERAGE"
    assert classify_leverage_state(1, -1) == "SHORT_SQUEEZE_DELEVERAGING"
    assert classify_leverage_state(-1, 1) == "NEW_SHORT_LEVERAGE"
    assert classify_leverage_state(-1, -1) == "LONG_LIQUIDATION_DELEVERAGING"
    tensor = leverage_tensor(1, 2, 0.01, 0.005, 5, 1, 100, 50, 1000)
    assert tensor["funding_surprise"] == 0.005
    assert tensor["liquidation_depth_ratio"] == 0.15


def test_execution_economics_are_explicit():
    maker = expected_maker_edge_bps(1.0, 0.6, 0.2, 0.1, 0.1, 0.8)
    assert maker.expected_edge_bps > 0
    assert expected_taker_edge_bps(2.0, 0.5, 0.2, 0.2, 0.1) == 1.0


def test_wallet_intelligence_weights_persistent_markout():
    rows = []
    for wallet, direction in [("good", 1), ("bad", -1)]:
        for _ in range(25):
            rows.append({"wallet": wallet, "signed_notional": 100.0, "markout_bps": direction * 2.0})
    trades = pd.DataFrame(rows)
    scores = wallet_intelligence_table(trades, min_trades=20)
    assert scores.iloc[0]["wallet"] == "good"
    flow = informed_flow(pd.DataFrame([{"wallet": "good", "signed_notional": 100.0}]), scores)
    assert flow > 0


def test_scientific_gates_reject_soft_alpha():
    bad = PromotionEvidence(pit_clean=True, independent_forward=True, dsr=0.8, pbo=0.2, cost_x2_positive=False, delayed_entry_positive=True, top_contributors_removed_positive=True, same_sign_halves=True, recent_period_not_destructive=True, sleeve_pf=1.1, capacity_usd=300000, paper_live_positive=True, net_edge_bps=0.2, mechanism_confirmed=True)
    result = evaluate_evidence(bad)
    assert not result.passed
    assert {"dsr", "pbo", "cost_x2", "sleeve_pf"}.issubset(result.failures)


def test_same_mechanism_variants_do_not_count_as_multiple_alphas():
    a = candidate("a", "A1", "pnl_a", edge=2.0)
    b = AlphaCandidate(candidate_id="b", mechanism_id="A1", independence_key=a.independence_key, pnl_series_name="pnl_b", evidence=good_evidence(edge=1.0))
    pnl = pd.DataFrame({"pnl_a": np.arange(100), "pnl_b": np.arange(100) * 2.0})
    verdict = AlphaFoundry().build_portfolio([a, b], pnl)
    assert verdict.independent_mechanisms == 1
    assert "same_independence_key" in verdict.rejection_reasons["b"]


def test_corr_gate_rejects_clone_across_different_labs_without_marginal_value():
    a = candidate("a", "A1", "pnl_a", edge=2.0)
    b = candidate("b", "A2", "pnl_b", edge=1.0)
    pnl = pd.DataFrame({"pnl_a": np.arange(100), "pnl_b": np.arange(100) + 0.01})
    verdict = AlphaFoundry().build_portfolio([a, b], pnl)
    assert verdict.independent_mechanisms == 1
    assert verdict.rejection_reasons["b"] == ("correlation_gate",)


def test_ten_orthogonal_mechanisms_are_required_for_foundry_ready():
    candidates = []
    pnl = {}
    n = 200
    x = np.linspace(0, 8 * np.pi, n)
    for i, lab_id in enumerate(sorted(LAB_REGISTRY)[:10]):
        cid = "c%s" % i
        name = "p%s" % i
        candidates.append(candidate(cid, lab_id, name, edge=10.0 - 0.1 * i))
        pnl[name] = np.sin((i + 1) * x) + 0.01 * i * np.cos((i + 3) * x)
    verdict = AlphaFoundry(DEFAULT_GATES).build_portfolio(candidates, pd.DataFrame(pnl))
    assert verdict.independent_mechanisms == 10
    assert verdict.production_ready
    assert verdict.target_independent_mechanisms == 10


def test_independent_confirmation_cannot_reuse_dev_window():
    dev = ExperimentProtocol(experiment_id="dev", mechanism_id="A3", stage=ResearchStage.DEV_DISCOVERY, feature_names=("hazard",), target_name="next_mid_move", horizons_ms=(100,), data_window_id="window-1", preregistered=True)
    confirm = ExperimentProtocol(experiment_id="confirm", mechanism_id="A3", stage=ResearchStage.INDEPENDENT_CONFIRMATION, feature_names=("hazard",), target_name="next_mid_move", horizons_ms=(100,), data_window_id="window-1", preregistered=True, parent_experiment_id="dev")
    with pytest.raises(ValueError, match="new data window"):
        validate_stage_transition(dev, confirm)
