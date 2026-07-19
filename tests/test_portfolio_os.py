"""
tests/test_portfolio_os.py
─────────────────────────────────────────────────────────────────────────────
Tests unitaires de l'usine à opportunités (contrats, zones, ledger, sizing,
contraintes, validation bayésienne, backtester portefeuille synthétique).

Lancer : python3 -m pytest tests/test_portfolio_os.py -q
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.institutional.contracts import Opportunity, ReasonCode
from src.institutional.portfolio.zones import classify_zone, get_thresholds
from src.institutional.monitoring.decision_ledger import DecisionLedger
from src.institutional.portfolio.sizing import (
    confidence_shrink, multi_cap_size, kelly_from_stats,
)
from src.institutional.portfolio.constraints import PortfolioConstraints
from src.institutional.portfolio.correlation_model import CorrelationModel
from src.institutional.evaluation.live_validation import (
    profit_factor, effective_sample_size, bootstrap_prob_pf_gt, evaluate_engine,
)


def _opp(p=0.7, zone="A_TRADE", asset="BTCUSDT", reason=ReasonCode.ACCEPT_TRADE):
    return Opportunity(
        timestamp=pd.Timestamp("2026-01-01T00:00:00Z"), engine_id="E", asset=asset,
        direction="LONG", status="PAPER", p_success=p, expected_return=0.01,
        expected_vol=0.04, expected_holding_hours=8.0, expected_cost=0.001,
        score_raw=p, score_net=0.009, confidence=0.5, regime="BULL",
        correlation_bucket="majors", max_position_fraction=0.25, stop_loss=0.025,
        take_profit=0.04, decision_zone=zone, reason=reason.value,
    )


# ── contrat Opportunity ─────────────────────────────────────────────────────
def test_opportunity_roundtrip():
    o = _opp()
    o.validate()
    o2 = Opportunity.from_dict(o.to_dict())
    assert o2.asset == "BTCUSDT" and o2.is_actionable()


def test_opportunity_invalid_raises():
    with pytest.raises(ValueError):
        _opp(p=1.5).validate()
    bad = _opp()
    bad.direction = "WAT"
    with pytest.raises(ValueError):
        bad.validate()


def test_status_size_fraction():
    assert _opp().size_fraction() == 0.0          # PAPER → pas de capital réel
    o = _opp(); o.status = "FULL_LIVE"
    assert o.size_fraction() == 0.25


# ── zones A/B/C ─────────────────────────────────────────────────────────────
def test_zones_boundaries():
    thr = get_thresholds("BTCUSDT")
    assert classify_zone(thr.tau_a, thr.tau_a, thr.tau_b)[0] == "A_TRADE"
    assert classify_zone(thr.tau_b, thr.tau_a, thr.tau_b)[0] == "B_SHADOW"
    assert classify_zone(thr.tau_b - 0.01, thr.tau_a, thr.tau_b)[0] == "C_REJECT"


# ── decision ledger ─────────────────────────────────────────────────────────
def test_decision_ledger_records_all_and_reconciles():
    tmp = tempfile.mkdtemp()
    led = DecisionLedger(path=Path(tmp) / "d.parquet")
    idx = pd.date_range("2026-01-01", periods=24, freq="1H", tz="UTC")
    price = pd.Series(100 * np.cumprod(1 + np.full(24, 0.001)), index=idx)
    for i, ts in enumerate(idx[:12]):
        o = _opp(p=0.4 + 0.03 * i)
        o.timestamp = ts
        z, r = classify_zone(o.p_success, 0.63, 0.52)
        o.decision_zone, o.reason = z, r.value
        led.record(o, tau_a=0.63, tau_b=0.52)
    assert led.flush() == 12
    assert led.reconcile_forward_returns({"BTCUSDT": price}) == 12
    df = led.load()
    assert df["future_return_8h"].notna().all()
    s = led.summary()
    assert s["pct_explained"] == 1.0 and s["n"] == 12
    # non-trade view = tout sauf A
    assert len(led.non_trades()) == (s["n_B_shadow"] + s["n_C_reject"])


# ── sizing multi-cap ────────────────────────────────────────────────────────
def test_confidence_shrink_matches_brief():
    assert abs(confidence_shrink(3) - 0.173) < 0.01
    assert confidence_shrink(100) == 1.0


def test_multi_cap_size_grows_with_live_sample():
    small = multi_cap_size(f_kelly=0.4, n_live=3, regime_state="validated",
                           vol_target_cap=0.3, drawdown_cap=0.3,
                           engine_exposure=0, bucket_exposure=0, gross_exposure=0)
    large = multi_cap_size(f_kelly=0.4, n_live=200, regime_state="validated",
                           vol_target_cap=0.3, drawdown_cap=0.3,
                           engine_exposure=0, bucket_exposure=0, gross_exposure=0)
    assert 0 < small < large


def test_regime_forbidden_zeroes_size():
    s = multi_cap_size(f_kelly=0.4, n_live=200, regime_state="forbidden",
                       vol_target_cap=0.3, drawdown_cap=0.3,
                       engine_exposure=0, bucket_exposure=0, gross_exposure=0)
    assert s == 0.0


def test_kelly_from_stats():
    assert kelly_from_stats(0.7, 0.02, 0.01) > 0
    assert kelly_from_stats(0.3, 0.01, 0.02) == 0.0  # mauvais edge → 0


# ── contraintes ─────────────────────────────────────────────────────────────
def test_constraints_block_reasons():
    c = PortfolioConstraints()
    ok, r = c.check(asset="BTCUSDT", engine_id="E", bucket="majors", n_open=4,
                    open_assets=set(), bucket_count={}, engine_exposure={}, gross_exposure=0)
    assert not ok and r == ReasonCode.REJECT_EXPOSURE_LIMIT
    ok, r = c.check(asset="BTCUSDT", engine_id="E", bucket="majors", n_open=2,
                    open_assets=set(), bucket_count={"majors": 2}, engine_exposure={}, gross_exposure=0)
    assert not ok and r == ReasonCode.REJECT_CORRELATION


def test_correlation_buckets():
    cm = CorrelationModel()
    assert cm.bucket("BTCUSDT") == cm.bucket("ETHUSDT") == "majors"
    assert cm.correlation("BTCUSDT", "BTCUSDT") == 1.0


# ── validation bayésienne ───────────────────────────────────────────────────
def test_profit_factor_and_ess():
    r = np.array([0.02, -0.01, 0.03, -0.01, 0.02])
    assert profit_factor(r) > 1.0
    assert 1.0 <= effective_sample_size(r) <= len(r)


def test_bootstrap_pf_prob_winner_vs_loser():
    winner = np.array([0.02, -0.005] * 50)   # PF ~ 4
    loser = np.array([0.005, -0.02] * 50)    # PF ~ 0.25
    assert bootstrap_prob_pf_gt(winner, 1.30) > 0.9
    assert bootstrap_prob_pf_gt(loser, 1.30) < 0.1


def test_validation_ladder_never_jumps_to_live():
    # un moteur PAPER avec preuves fortes → au plus MICRO_LIVE (jamais FULL_LIVE)
    rng = np.random.default_rng(0)
    strong = rng.normal(0.004, 0.01, 200)
    res = evaluate_engine("E", strong, current_status="PAPER", drift=0.0)
    assert res.recommended_status in {"PAPER", "MICRO_LIVE"}
    assert res.recommended_status != "FULL_LIVE"


def test_validation_drift_disables():
    res = evaluate_engine("E", np.array([0.01, -0.01] * 30), current_status="MICRO_LIVE", drift=1.0)
    assert res.recommended_status == "DISABLED"
