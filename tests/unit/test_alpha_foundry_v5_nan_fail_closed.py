"""NaN/Inf must never silently pass a gate. Two layers are tested independently:
contracts.py's __post_init__ (can a NaN evidence object even be constructed) and
validation.py's gate comparisons themselves (SimpleNamespace bypasses the
dataclass constructor entirely, so this covers the comparison logic on its own,
independent of whether __post_init__ also happens to block it upstream).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from alpha_foundry_v5.contracts import EconomicEvidence, ResearchStage, StatisticalEvidence
from alpha_foundry_v5.validation import ValidationEngine

VALID_STAT_KWARGS = {
    "n": 1000,
    "ess": 500.0,
    "ic": 0.2,
    "q_value": 0.01,
    "block_p": 0.01,
    "dsr_probability": 0.99,
    "pbo": 0.05,
    "same_sign_halves": True,
    "all_primary_symbols_pass": True,
    "independent_window": True,
}

VALID_ECON_KWARGS = {
    "gross_edge_bps": 5.0,
    "net_edge_bps": 2.0,
    "net_edge_cost_x2_bps": 1.0,
    "delayed_entry_net_bps": 1.5,
    "profit_factor": 1.8,
    "max_drawdown": -0.02,
    "capacity_usd": 500_000.0,
    "top_contributors_removed_net_bps": 1.2,
    "recent_period_net_bps": 1.1,
    "paper_live_net_bps": float("nan"),
    "fill_rate": 1.0,
    "realized_slippage_bps": 0.3,
}


@pytest.mark.parametrize("field", ["ic", "q_value", "block_p", "dsr_probability", "pbo", "ess"])
def test_statistical_evidence_rejects_nan_in_required_field(field):
    kwargs = dict(VALID_STAT_KWARGS)
    kwargs[field] = float("nan")
    with pytest.raises(ValueError):
        StatisticalEvidence(**kwargs)


@pytest.mark.parametrize(
    "field",
    [
        "gross_edge_bps",
        "net_edge_bps",
        "net_edge_cost_x2_bps",
        "delayed_entry_net_bps",
        "max_drawdown",
        "capacity_usd",
        "top_contributors_removed_net_bps",
        "recent_period_net_bps",
        "fill_rate",
        "realized_slippage_bps",
    ],
)
def test_economic_evidence_rejects_nan_in_required_field(field):
    kwargs = dict(VALID_ECON_KWARGS)
    kwargs[field] = float("nan")
    with pytest.raises(ValueError):
        EconomicEvidence(**kwargs)


def test_economic_evidence_rejects_nan_profit_factor():
    kwargs = dict(VALID_ECON_KWARGS)
    kwargs["profit_factor"] = float("nan")
    with pytest.raises(ValueError):
        EconomicEvidence(**kwargs)


def test_economic_evidence_allows_infinite_profit_factor():
    kwargs = dict(VALID_ECON_KWARGS)
    kwargs["profit_factor"] = float("inf")
    EconomicEvidence(**kwargs)  # zero losing trades is a legitimate, not a broken, result


def test_economic_evidence_allows_nan_paper_live_net_bps_before_paper_stage():
    EconomicEvidence(**VALID_ECON_KWARGS)  # paper_live_net_bps=nan in the fixture itself


def _stat_evidence_bypassing_post_init(**overrides) -> SimpleNamespace:
    return SimpleNamespace(**{**VALID_STAT_KWARGS, **overrides})


def _econ_evidence_bypassing_post_init(**overrides) -> SimpleNamespace:
    return SimpleNamespace(**{**VALID_ECON_KWARGS, "paper_live_net_bps": 1.0, **overrides})


def test_statistical_gate_fails_closed_on_nan_ic_discovery():
    engine = ValidationEngine()
    evidence = _stat_evidence_bypassing_post_init(ic=float("nan"))
    decision = engine.statistical_gate(ResearchStage.DEV_DISCOVERY, evidence)
    assert decision.passed is False
    assert "ic" in decision.failures


def test_statistical_gate_fails_closed_on_nan_dsr_confirmation():
    engine = ValidationEngine()
    evidence = _stat_evidence_bypassing_post_init(dsr_probability=float("nan"))
    decision = engine.statistical_gate(ResearchStage.INDEPENDENT_CONFIRMATION, evidence)
    assert decision.passed is False
    assert "dsr" in decision.failures


def test_economic_gate_fails_closed_on_nan_net_edge():
    engine = ValidationEngine()
    evidence = _econ_evidence_bypassing_post_init(net_edge_bps=float("nan"))
    decision = engine.economic_gate(evidence)
    assert decision.passed is False
    assert "net_edge" in decision.failures


def test_economic_gate_passes_infinite_profit_factor_through():
    engine = ValidationEngine()
    evidence = _econ_evidence_bypassing_post_init(profit_factor=float("inf"))
    decision = engine.economic_gate(evidence)
    assert "profit_factor" not in decision.failures


def test_economic_gate_still_requires_paper_live_when_asked():
    engine = ValidationEngine()
    evidence = _econ_evidence_bypassing_post_init(paper_live_net_bps=float("nan"))
    decision = engine.economic_gate(evidence, require_paper=True)
    assert decision.passed is False
    assert "paper_live" in decision.failures
