"""
tests/institutional/test_contracts.py
═══════════════════════════════════════════════════════════════════════════════
Tests unitaires — Phase 1 : contrats fondamentaux.

Couvre :
    SignalFrame          (contracts.py)
    RobustnessScore      (contracts.py)
    DataQualityReport    (data/schemas.py)
    PortfolioState       (portfolio/portfolio_state.py)
    RiskState            (risk/risk_state_store.py)
    RiskStateStore       (risk/risk_state_store.py)
    ExperimentRecord     (experiments/experiment_logger.py)
    ExperimentLogger     (experiments/experiment_logger.py)

Conventions :
    - Chaque classe de test couvre : construction valide, validation des
      invariants, sérialisation round-trip JSON, comportement des méthodes.
    - Les tests de validation vérifient le MESSAGE d'erreur, pas seulement
      le type d'exception.
    - Les tests de round-trip vérifient que from_dict(to_dict(x)) == x.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from institutional.contracts import (
    Direction,
    EngineID,
    RobustnessScore,
    SignalFrame,
    Verdict,
    SIGNAL_FRAME_COLUMNS,
    _ROBUSTNESS_WEIGHTS,
)
from institutional.data.schemas import (
    DataQualityReport,
    QualityIssue,
    QualityLevel,
    QualityThresholds,
)
from institutional.portfolio.portfolio_state import (
    PortfolioState,
    Position,
    Side,
)
from institutional.risk.risk_state_store import (
    KillReason,
    RiskState,
    RiskStateStore,
)
from institutional.experiments.experiment_logger import (
    ExperimentLogger,
    ExperimentRecord,
)


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════

_TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def valid_signal() -> SignalFrame:
    return SignalFrame(
        timestamp=_TS,
        asset="BTCUSDT",
        engine_name=EngineID.INSTITUTIONAL,
        signal_name="trend_following_v1",
        direction=Direction.LONG,
        raw_score=0.75,
        calibrated_score=0.70,
        confidence=0.80,
        expected_return=0.025,
        expected_vol=0.45,
        horizon_minutes=240,
        max_holding_minutes=720,
        stop_distance=0.02,
        take_profit_distance=0.04,
        model_version="lgbm_v1.0",
        feature_version="feat_v1.0",
        label_version="lbl_v1.0",
        run_id="run_20240601_abc123",
    )


@pytest.fixture
def valid_position() -> Position:
    return Position(
        asset="BTCUSDT",
        size=0.01,
        entry_price=60_000.0,
        current_price=62_000.0,
        notional_usd=620.0,
        unrealized_pnl=20.0,
        weight=0.062,
        engine_name="INSTITUTIONAL_ENGINE",
        signal_name="trend_following_v1",
        open_timestamp=_TS,
        stop_price=58_800.0,
        take_profit_price=64_800.0,
    )


@pytest.fixture
def valid_portfolio(valid_position: Position) -> PortfolioState:
    ps = PortfolioState.empty(initial_cash=10_000.0, timestamp=_TS)
    ps.add_position(valid_position)
    return ps


@pytest.fixture
def valid_risk_state() -> RiskState:
    rs = RiskState()
    rs.peak_equity = 10_000.0
    return rs


@pytest.fixture
def valid_dq_report() -> DataQualityReport:
    return DataQualityReport(
        asset="BTCUSDT",
        source="futures",
        timeframe="1h",
        rows=8_760,
        valid_rows=8_740,
        rejected_rows=20,
        duplicate_count=0,
        stale_intervals=2,
        missing_rate=0.002,
        max_gap_minutes=60.0,
        outlier_count=3,
        first_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        last_timestamp=datetime(2024, 12, 31, 23, tzinfo=timezone.utc),
        issues=(),
    )


@pytest.fixture
def valid_experiment_record() -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id="exp_20240601_abc",
        run_id="exp_20240601_abc",
        timestamp=_TS,
        engine_name="INSTITUTIONAL_ENGINE",
        signal_name="trend_following_v1",
        assets=("BTCUSDT", "ETHUSDT"),
        features_version="feat_v1.0",
        labels_version="lbl_v1.0",
        model_type="LightGBM",
        model_params={"n_estimators": 500, "learning_rate": 0.05},
        train_period={"start": "2021-01-01", "end": "2023-12-31"},
        validation_period={"start": "2023-10-01", "end": "2023-12-31"},
        test_period={"start": "2024-01-01", "end": "2024-12-31"},
        walk_forward_config={"mode": "expanding", "folds": 4},
        cost_config={"cost_bps": 10.0},
        risk_config={"max_drawdown": 0.10},
        metrics={"pf": 1.35, "sharpe": 1.2, "cagr": 0.28},
        robustness_tests={"cost_x2": {"pf": 1.12}, "shuffle": {"pf": 0.98}},
        decision=Verdict.PAPER,
        notes="Premier walk-forward BTC institutionnel",
        artifact_paths={"model": "artifacts/models/lgbm_btc_v1.pkl"},
        code_hash="a1b2c3d4e5f6",
    )


# ══════════════════════════════════════════════════════════════════════════════
# TestSignalFrame
# ══════════════════════════════════════════════════════════════════════════════


class TestSignalFrame:

    def test_valid_construction(self, valid_signal: SignalFrame) -> None:
        assert valid_signal.asset == "BTCUSDT"
        assert valid_signal.direction == Direction.LONG
        assert valid_signal.engine_name == "INSTITUTIONAL_ENGINE"

    def test_frozen_immutability(self, valid_signal: SignalFrame) -> None:
        with pytest.raises((AttributeError, TypeError)):
            valid_signal.confidence = 0.5  # type: ignore[misc]

    # ── Validation des invariants ──────────────────────────────────────────────

    @pytest.mark.parametrize("field,bad_val,expected_msg", [
        ("calibrated_score", -0.01,  "calibrated_score"),
        ("calibrated_score",  1.01,  "calibrated_score"),
        ("confidence",       -0.01,  "confidence"),
        ("confidence",        1.01,  "confidence"),
        ("expected_vol",      0.0,   "expected_vol"),
        ("expected_vol",     -0.01,  "expected_vol"),
        ("horizon_minutes",   0,     "horizon_minutes"),
        ("horizon_minutes",  -1,     "horizon_minutes"),
        ("stop_distance",     0.0,   "stop_distance"),
        ("take_profit_distance", 0.0, "take_profit_distance"),
    ])
    def test_invalid_numeric_fields(
        self,
        valid_signal: SignalFrame,
        field: str,
        bad_val: object,
        expected_msg: str,
    ) -> None:
        d = valid_signal.to_dict()
        d[field] = bad_val
        with pytest.raises(ValueError, match=expected_msg):
            SignalFrame.from_dict(d)

    @pytest.mark.parametrize("field", ["asset", "engine_name", "signal_name", "run_id"])
    def test_empty_string_fields_raise(
        self, valid_signal: SignalFrame, field: str
    ) -> None:
        d = valid_signal.to_dict()
        d[field] = ""
        with pytest.raises(ValueError, match=field):
            SignalFrame.from_dict(d)

    def test_max_holding_less_than_horizon_raises(
        self, valid_signal: SignalFrame
    ) -> None:
        d = valid_signal.to_dict()
        d["max_holding_minutes"] = d["horizon_minutes"] - 1
        with pytest.raises(ValueError, match="max_holding_minutes"):
            SignalFrame.from_dict(d)

    def test_multiple_errors_reported_together(self, valid_signal: SignalFrame) -> None:
        d = valid_signal.to_dict()
        d["calibrated_score"] = 2.0
        d["confidence"]       = -0.5
        d["expected_vol"]     = -1.0
        with pytest.raises(ValueError) as exc_info:
            SignalFrame.from_dict(d)
        msg = str(exc_info.value)
        assert "calibrated_score" in msg
        assert "confidence"       in msg
        assert "expected_vol"     in msg

    # ── Direction ──────────────────────────────────────────────────────────────

    def test_invalid_direction_raises(self, valid_signal: SignalFrame) -> None:
        d = valid_signal.to_dict()
        d["direction"] = "sideways"
        with pytest.raises(ValueError):
            SignalFrame.from_dict(d)

    def test_is_actionable_long(self, valid_signal: SignalFrame) -> None:
        assert valid_signal.is_actionable() is True

    def test_is_actionable_flat(self) -> None:
        sf = SignalFrame.make_flat(
            timestamp=_TS,
            asset="ETHUSDT",
            engine_name=EngineID.TRM,
            signal_name="sniper_long",
            run_id="run_001",
        )
        assert sf.is_actionable() is False
        assert sf.direction == Direction.FLAT

    # ── Sérialisation ─────────────────────────────────────────────────────────

    def test_to_dict_has_all_columns(self, valid_signal: SignalFrame) -> None:
        d = valid_signal.to_dict()
        assert set(SIGNAL_FRAME_COLUMNS) <= set(d.keys())

    def test_json_round_trip(self, valid_signal: SignalFrame) -> None:
        raw = valid_signal.to_json()
        restored = SignalFrame.from_json(raw)
        assert restored == valid_signal

    def test_dict_round_trip(self, valid_signal: SignalFrame) -> None:
        restored = SignalFrame.from_dict(valid_signal.to_dict())
        assert restored == valid_signal

    def test_json_is_valid_json(self, valid_signal: SignalFrame) -> None:
        parsed = json.loads(valid_signal.to_json())
        assert parsed["asset"] == "BTCUSDT"
        assert parsed["direction"] == "long"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def test_make_flat_valid(self) -> None:
        sf = SignalFrame.make_flat(
            timestamp=_TS,
            asset="BTCUSDT",
            engine_name="TRM_EVENT_ENGINE",
            signal_name="test",
            run_id="run_test",
        )
        sf_d = sf.to_dict()
        assert sf_d["direction"]         == "flat"
        assert sf_d["calibrated_score"]  == 0.5
        assert sf_d["confidence"]        == 0.0

    def test_replace_changes_field(self, valid_signal: SignalFrame) -> None:
        updated = valid_signal.replace(confidence=0.95)
        assert updated.confidence == pytest.approx(0.95)
        assert updated.asset == valid_signal.asset

    def test_replace_invalid_field_raises(self, valid_signal: SignalFrame) -> None:
        with pytest.raises(KeyError, match="nonexistent"):
            valid_signal.replace(nonexistent=42)

    def test_replace_invalid_value_raises(self, valid_signal: SignalFrame) -> None:
        with pytest.raises(ValueError):
            valid_signal.replace(calibrated_score=99.0)

    # ── Compatibilité engines ─────────────────────────────────────────────────

    @pytest.mark.parametrize("engine", [
        EngineID.TRM,
        EngineID.INSTITUTIONAL,
        EngineID.META,
        "CUSTOM_ENGINE_V2",   # extensibilité
    ])
    def test_engine_name_accepted(self, valid_signal: SignalFrame, engine: str) -> None:
        updated = valid_signal.replace(engine_name=str(engine))
        assert updated.engine_name == str(engine)


# ══════════════════════════════════════════════════════════════════════════════
# TestRobustnessScore
# ══════════════════════════════════════════════════════════════════════════════


class TestRobustnessScore:

    def _make(self, **overrides: float) -> RobustnessScore:
        defaults = {
            "pf_score": 0.8, "cost_score": 0.7, "shuffle_score": 0.6,
            "year_stability_score": 0.8, "threshold_stability_score": 0.7,
            "contribution_score": 0.6, "drawdown_score": 0.9, "trade_count_score": 0.8,
        }
        defaults.update(overrides)
        return RobustnessScore(**defaults)

    def test_all_ones_live_ready(self) -> None:
        rs = self._make(**{k: 1.0 for k in [
            "pf_score", "cost_score", "shuffle_score", "year_stability_score",
            "threshold_stability_score", "contribution_score", "drawdown_score", "trade_count_score",
        ]})
        assert rs.verdict == Verdict.LIVE_READY

    def test_all_zeros_reject(self) -> None:
        rs = self._make(**{k: 0.0 for k in [
            "pf_score", "cost_score", "shuffle_score", "year_stability_score",
            "threshold_stability_score", "contribution_score", "drawdown_score", "trade_count_score",
        ]})
        assert rs.verdict == Verdict.REJECT
        assert rs.total_score == pytest.approx(0.0)

    @pytest.mark.parametrize("score,expected_verdict", [
        (0.90, Verdict.LIVE_READY),
        (0.80, Verdict.PROMOTE),
        (0.65, Verdict.PAPER),
        (0.50, Verdict.INCUBATE),
        (0.30, Verdict.REJECT),
    ])
    def test_verdict_thresholds(self, score: float, expected_verdict: Verdict) -> None:
        rs = self._make(**{k: score for k in [
            "pf_score", "cost_score", "shuffle_score", "year_stability_score",
            "threshold_stability_score", "contribution_score", "drawdown_score", "trade_count_score",
        ]})
        assert rs.verdict == expected_verdict

    def test_component_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="pf_score"):
            self._make(pf_score=1.01)
        with pytest.raises(ValueError, match="cost_score"):
            self._make(cost_score=-0.01)

    def test_weights_sum_to_one(self) -> None:
        assert sum(_ROBUSTNESS_WEIGHTS) == pytest.approx(1.0)

    def test_json_round_trip(self) -> None:
        rs = self._make()
        restored = RobustnessScore.from_dict(json.loads(rs.to_json()))
        assert restored == rs

    def test_total_score_in_range(self) -> None:
        rs = self._make()
        assert 0.0 <= rs.total_score <= 1.0


# ══════════════════════════════════════════════════════════════════════════════
# TestDataQualityReport
# ══════════════════════════════════════════════════════════════════════════════


class TestDataQualityReport:

    def test_valid_construction(self, valid_dq_report: DataQualityReport) -> None:
        assert valid_dq_report.asset == "BTCUSDT"
        assert valid_dq_report.rows == 8_760

    def test_is_valid_clean_data(self, valid_dq_report: DataQualityReport) -> None:
        assert valid_dq_report.is_valid() is True

    def test_is_invalid_high_missing_rate(
        self, valid_dq_report: DataQualityReport
    ) -> None:
        d = valid_dq_report.to_dict()
        d["missing_rate"] = 0.10
        report = DataQualityReport.from_dict(d)
        assert report.is_valid() is False

    def test_is_invalid_duplicates(self, valid_dq_report: DataQualityReport) -> None:
        d = valid_dq_report.to_dict()
        d["duplicate_count"] = 5
        report = DataQualityReport.from_dict(d)
        assert report.is_valid() is False

    def test_quality_level_ok(self, valid_dq_report: DataQualityReport) -> None:
        assert valid_dq_report.quality_level() == QualityLevel.OK

    def test_quality_level_warning_one_failure(
        self, valid_dq_report: DataQualityReport
    ) -> None:
        d = valid_dq_report.to_dict()
        d["missing_rate"] = 0.10
        report = DataQualityReport.from_dict(d)
        assert report.quality_level() == QualityLevel.WARNING

    def test_quality_level_critical_multiple_failures(
        self, valid_dq_report: DataQualityReport
    ) -> None:
        d = valid_dq_report.to_dict()
        d["missing_rate"]    = 0.20
        d["duplicate_count"] = 100
        d["max_gap_minutes"] = 5_000.0
        report = DataQualityReport.from_dict(d)
        assert report.quality_level() == QualityLevel.CRITICAL

    def test_custom_thresholds(self, valid_dq_report: DataQualityReport) -> None:
        strict = QualityThresholds(max_missing_rate=0.001)
        assert valid_dq_report.is_valid(strict) is False

        loose = QualityThresholds(max_missing_rate=0.99)
        assert valid_dq_report.is_valid(loose) is True

    def test_json_round_trip(self, valid_dq_report: DataQualityReport) -> None:
        restored = DataQualityReport.from_json(valid_dq_report.to_json())
        assert restored == valid_dq_report

    def test_with_issues(self, valid_dq_report: DataQualityReport) -> None:
        d = valid_dq_report.to_dict()
        d["issues"] = [
            {"level": "WARNING", "field": "volume", "message": "Quelques valeurs nulles"}
        ]
        report = DataQualityReport.from_dict(d)
        assert len(report.issues) == 1
        assert report.issues[0].level == QualityLevel.WARNING

    def test_empty_asset_raises(self, valid_dq_report: DataQualityReport) -> None:
        d = valid_dq_report.to_dict()
        d["asset"] = ""
        with pytest.raises(ValueError, match="asset"):
            DataQualityReport.from_dict(d)

    def test_valid_rows_exceeds_rows_raises(
        self, valid_dq_report: DataQualityReport
    ) -> None:
        d = valid_dq_report.to_dict()
        d["valid_rows"] = d["rows"] + 1  # type: ignore[operator]
        with pytest.raises(ValueError, match="valid_rows"):
            DataQualityReport.from_dict(d)

    def test_missing_rate_out_of_range_raises(
        self, valid_dq_report: DataQualityReport
    ) -> None:
        d = valid_dq_report.to_dict()
        d["missing_rate"] = 1.5
        with pytest.raises(ValueError, match="missing_rate"):
            DataQualityReport.from_dict(d)

    def test_coverage_days_computed(self, valid_dq_report: DataQualityReport) -> None:
        days = valid_dq_report.coverage_days()
        assert days is not None
        assert days > 364.0

    def test_coverage_days_none_when_no_timestamps(self) -> None:
        d = DataQualityReport(
            asset="TEST", source="raw", timeframe="1h",
            rows=0, valid_rows=0, rejected_rows=0,
            duplicate_count=0, stale_intervals=0,
            missing_rate=0.0, max_gap_minutes=0.0, outlier_count=0,
            first_timestamp=None, last_timestamp=None, issues=(),
        )
        assert d.coverage_days() is None

    def test_summary_contains_asset(self, valid_dq_report: DataQualityReport) -> None:
        summary = valid_dq_report.summary()
        assert "BTCUSDT" in summary
        assert "futures" in summary


# ══════════════════════════════════════════════════════════════════════════════
# TestPortfolioState
# ══════════════════════════════════════════════════════════════════════════════


class TestPosition:

    def test_valid_construction(self, valid_position: Position) -> None:
        assert valid_position.asset == "BTCUSDT"
        assert valid_position.is_long is True
        assert valid_position.is_short is False

    def test_update_price(self, valid_position: Position) -> None:
        valid_position.update_price(65_000.0)
        assert valid_position.current_price  == pytest.approx(65_000.0)
        assert valid_position.notional_usd   == pytest.approx(650.0)
        assert valid_position.unrealized_pnl == pytest.approx(50.0)  # 0.01 × (65k - 60k)

    def test_update_price_zero_raises(self, valid_position: Position) -> None:
        with pytest.raises(ValueError, match="price"):
            valid_position.update_price(0.0)

    def test_short_position(self) -> None:
        pos = Position(
            asset="ETHUSDT", size=-1.0, entry_price=3_000.0, current_price=2_800.0,
            notional_usd=2_800.0, unrealized_pnl=200.0, weight=0.28,
            engine_name="INSTITUTIONAL_ENGINE", signal_name="short_test",
            open_timestamp=_TS, stop_price=3_200.0, take_profit_price=2_400.0,
        )
        assert pos.is_short is True
        assert pos.is_long is False
        from institutional.contracts import Direction
        assert pos.direction == Direction.SHORT

    def test_zero_size_raises(self) -> None:
        with pytest.raises(ValueError, match="size"):
            Position(
                asset="BTCUSDT", size=0.0, entry_price=60_000.0, current_price=60_000.0,
                notional_usd=0.0, unrealized_pnl=0.0, weight=0.0,
                engine_name="X", signal_name="X",
                open_timestamp=_TS, stop_price=58_000.0, take_profit_price=62_000.0,
            )

    def test_stop_triggered_long(self, valid_position: Position) -> None:
        valid_position.update_price(58_800.0)
        assert valid_position.stop_triggered() is True

    def test_tp_triggered_long(self, valid_position: Position) -> None:
        valid_position.update_price(65_000.0)
        assert valid_position.tp_triggered() is True

    def test_time_expired(self, valid_position: Position) -> None:
        now_future = datetime(2030, 1, 1, tzinfo=timezone.utc)
        valid_position.max_holding_until = datetime(2025, 1, 1, tzinfo=timezone.utc)
        assert valid_position.time_expired(now_future) is True

    def test_return_pct(self, valid_position: Position) -> None:
        # entry=60k, current=62k, size=0.01 → pnl=20 / (0.01 × 60k) = 3.33%
        assert valid_position.return_pct == pytest.approx(20.0 / 600.0, rel=1e-3)


class TestPortfolioState:

    def test_empty_portfolio(self) -> None:
        ps = PortfolioState.empty(initial_cash=10_000.0, timestamp=_TS)
        assert ps.cash   == pytest.approx(10_000.0)
        assert ps.equity == pytest.approx(10_000.0)
        assert ps.n_positions == 0

    def test_add_position_updates_metrics(
        self, valid_portfolio: PortfolioState, valid_position: Position
    ) -> None:
        assert valid_portfolio.n_positions == 1
        assert valid_portfolio.gross_exposure == pytest.approx(620.0)

    def test_duplicate_asset_raises(
        self, valid_portfolio: PortfolioState, valid_position: Position
    ) -> None:
        with pytest.raises(ValueError, match="BTCUSDT"):
            valid_portfolio.add_position(valid_position)

    def test_remove_position_updates_cash(
        self, valid_portfolio: PortfolioState
    ) -> None:
        initial_cash = valid_portfolio.cash
        closed = valid_portfolio.remove_position("BTCUSDT", realized_pnl=25.0)
        assert closed.asset == "BTCUSDT"
        assert valid_portfolio.n_positions == 0
        assert valid_portfolio.realized_pnl_today == pytest.approx(25.0)

    def test_remove_nonexistent_raises(
        self, valid_portfolio: PortfolioState
    ) -> None:
        with pytest.raises(KeyError, match="ETHUSDT"):
            valid_portfolio.remove_position("ETHUSDT", realized_pnl=0.0)

    def test_update_prices_refreshes_pnl(
        self, valid_portfolio: PortfolioState
    ) -> None:
        valid_portfolio.update_prices({"BTCUSDT": 65_000.0})
        pos = valid_portfolio.positions["BTCUSDT"]
        assert pos.current_price == pytest.approx(65_000.0)
        assert pos.unrealized_pnl == pytest.approx(50.0)

    def test_per_engine_exposure(self, valid_portfolio: PortfolioState) -> None:
        exp = valid_portfolio.per_engine_exposure()
        assert "INSTITUTIONAL_ENGINE" in exp
        assert exp["INSTITUTIONAL_ENGINE"] > 0

    def test_leverage_computation(self, valid_portfolio: PortfolioState) -> None:
        # gross = 620, equity ≈ 10000 + 20 pnl = 10020
        assert valid_portfolio.leverage < 0.1

    def test_to_dict_serializable(self, valid_portfolio: PortfolioState) -> None:
        d = valid_portfolio.to_dict()
        raw = json.dumps(d)  # ne doit pas lever d'exception
        assert "BTCUSDT" in raw

    def test_empty_with_zero_cash_raises(self) -> None:
        with pytest.raises(ValueError, match="initial_cash"):
            PortfolioState.empty(initial_cash=0.0)

    def test_negative_cash_raises(self) -> None:
        with pytest.raises(ValueError, match="cash"):
            PortfolioState(timestamp=_TS, cash=-1.0, equity=0.0)


# ══════════════════════════════════════════════════════════════════════════════
# TestRiskState
# ══════════════════════════════════════════════════════════════════════════════


class TestRiskState:

    def test_default_construction(self) -> None:
        rs = RiskState()
        assert rs.total_trades   == 0
        assert rs.kill_switch_active is False
        assert rs.kill_reason    == KillReason.NONE

    def test_record_win(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.record_trade(pnl=100.0, timestamp=_TS)
        assert valid_risk_state.total_trades      == 1
        assert valid_risk_state.total_wins        == 1
        assert valid_risk_state.consecutive_wins  == 1
        assert valid_risk_state.consecutive_losses == 0
        assert valid_risk_state.day_pnl           == pytest.approx(100.0)

    def test_record_loss_resets_wins(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.record_trade(pnl=100.0, timestamp=_TS)
        valid_risk_state.record_trade(pnl=-50.0, timestamp=_TS)
        assert valid_risk_state.consecutive_wins  == 0
        assert valid_risk_state.consecutive_losses == 1
        assert valid_risk_state.total_losses      == 1

    def test_update_drawdown(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.update_drawdown(9_000.0)
        assert valid_risk_state.realized_drawdown == pytest.approx(-0.10)

    def test_drawdown_never_positive(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.update_drawdown(12_000.0)  # new peak
        valid_risk_state.update_drawdown(12_000.0)  # même niveau
        assert valid_risk_state.realized_drawdown == pytest.approx(0.0)

    def test_win_rate(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.record_trade(pnl=100.0, timestamp=_TS)
        valid_risk_state.record_trade(pnl=100.0, timestamp=_TS)
        valid_risk_state.record_trade(pnl=-50.0, timestamp=_TS)
        assert valid_risk_state.win_rate == pytest.approx(2 / 3)

    def test_win_rate_zero_trades(self) -> None:
        rs = RiskState()
        assert rs.win_rate == pytest.approx(0.0)

    def test_activate_kill_switch(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.activate_kill_switch(
            KillReason.MAX_DRAWDOWN,
            cooldown_until=_TS,
        )
        assert valid_risk_state.kill_switch_active is True
        assert valid_risk_state.kill_reason        == KillReason.MAX_DRAWDOWN

    def test_deactivate_kill_switch(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.activate_kill_switch(KillReason.MANUAL)
        valid_risk_state.deactivate_kill_switch()
        assert valid_risk_state.kill_switch_active is False
        assert valid_risk_state.kill_reason        == KillReason.NONE

    def test_reset_daily(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.record_trade(pnl=500.0, timestamp=_TS)
        valid_risk_state.reset_daily(timestamp=_TS)
        assert valid_risk_state.day_pnl         == pytest.approx(0.0)
        assert valid_risk_state.total_trades     == 1   # non réinitialisé

    def test_json_round_trip(self, valid_risk_state: RiskState) -> None:
        valid_risk_state.record_trade(pnl=200.0, timestamp=_TS)
        valid_risk_state.activate_kill_switch(KillReason.DAILY_LOSS)
        restored = RiskState.from_json(valid_risk_state.to_json())
        assert restored.total_trades      == valid_risk_state.total_trades
        assert restored.kill_switch_active == valid_risk_state.kill_switch_active
        assert restored.kill_reason        == valid_risk_state.kill_reason


class TestRiskStateStore:

    def test_load_creates_empty_when_missing(self, tmp_path: Path) -> None:
        store = RiskStateStore(tmp_path / "risk_state.json")
        state = store.load()
        assert isinstance(state, RiskState)
        assert state.total_trades == 0

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        store = RiskStateStore(tmp_path / "risk_state.json")
        state = store.load()
        state.record_trade(pnl=150.0, timestamp=_TS)
        store.save(state)

        loaded = store.load()
        assert loaded.total_trades == 1
        assert loaded.day_pnl == pytest.approx(150.0)

    def test_atomic_write_creates_file(self, tmp_path: Path) -> None:
        store = RiskStateStore(tmp_path / "subdir" / "risk.json")
        state = RiskState()
        store.save(state)
        assert store.path.exists()

    def test_reset_clears_state(self, tmp_path: Path) -> None:
        store = RiskStateStore(tmp_path / "risk_state.json")
        state = store.load()
        state.record_trade(pnl=100.0, timestamp=_TS)
        store.save(state)

        fresh = store.reset()
        assert fresh.total_trades == 0
        assert store.load().total_trades == 0

    def test_corrupt_file_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "risk_state.json"
        path.write_text("not valid json", encoding="utf-8")
        store = RiskStateStore(path)
        with pytest.raises(ValueError, match="corrompu"):
            store.load()

    def test_exists_false_before_save(self, tmp_path: Path) -> None:
        store = RiskStateStore(tmp_path / "risk_state.json")
        assert store.exists() is False

    def test_exists_true_after_save(self, tmp_path: Path) -> None:
        store = RiskStateStore(tmp_path / "risk_state.json")
        store.save(RiskState())
        assert store.exists() is True


# ══════════════════════════════════════════════════════════════════════════════
# TestExperimentRecord
# ══════════════════════════════════════════════════════════════════════════════


class TestExperimentRecord:

    def test_valid_construction(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        assert valid_experiment_record.decision == Verdict.PAPER
        assert "BTCUSDT" in valid_experiment_record.assets

    def test_frozen_immutability(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        with pytest.raises((AttributeError, TypeError)):
            valid_experiment_record.decision = Verdict.PROMOTE  # type: ignore[misc]

    def test_empty_engine_name_raises(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        d = valid_experiment_record.to_dict()
        d["engine_name"] = ""
        with pytest.raises(ValueError, match="engine_name"):
            ExperimentRecord.from_dict(d)

    def test_empty_assets_raises(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        d = valid_experiment_record.to_dict()
        d["assets"] = []
        with pytest.raises(ValueError, match="assets"):
            ExperimentRecord.from_dict(d)

    def test_invalid_verdict_raises(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        d = valid_experiment_record.to_dict()
        d["decision"] = "MAYBE"
        with pytest.raises(ValueError):
            ExperimentRecord.from_dict(d)

    def test_json_round_trip(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        restored = ExperimentRecord.from_json(
            valid_experiment_record.to_json()
        )
        assert restored == valid_experiment_record

    def test_dict_round_trip(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        restored = ExperimentRecord.from_dict(valid_experiment_record.to_dict())
        assert restored == valid_experiment_record

    def test_assets_stored_as_tuple(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        assert isinstance(valid_experiment_record.assets, tuple)

    def test_is_final_pending(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        d = valid_experiment_record.to_dict()
        d["decision"] = "PENDING"
        r = ExperimentRecord.from_dict(d)
        assert r.is_final() is False

    def test_is_final_paper(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        assert valid_experiment_record.is_final() is True

    def test_with_decision(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        final = valid_experiment_record.with_decision(
            Verdict.PROMOTE,
            metrics={"pf": 1.50, "sharpe": 1.5},
            code_hash="abcdef12",
        )
        assert final.decision    == Verdict.PROMOTE
        assert final.metrics["pf"] == pytest.approx(1.50)
        assert final.code_hash   == "abcdef12"
        # original non modifié
        assert valid_experiment_record.decision == Verdict.PAPER

    def test_code_hash_computation(
        self, valid_experiment_record: ExperimentRecord
    ) -> None:
        h = valid_experiment_record.compute_code_hash("print('hello')")
        assert len(h) == 16
        assert h == valid_experiment_record.compute_code_hash("print('hello')")

    def test_save_and_load(
        self, valid_experiment_record: ExperimentRecord, tmp_path: Path
    ) -> None:
        path = tmp_path / "exp.json"
        valid_experiment_record.save(path)
        loaded = ExperimentRecord.load(path)
        assert loaded == valid_experiment_record


class TestExperimentLogger:

    def test_start_creates_pending_record(self, tmp_path: Path) -> None:
        logger = ExperimentLogger(tmp_path)
        run_id = logger.start(
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name="trend_following_v1",
            assets=("BTCUSDT",),
        )
        assert run_id
        record = logger.load(run_id)
        assert record.decision    == Verdict.PENDING
        assert record.engine_name == "INSTITUTIONAL_ENGINE"

    def test_finish_updates_decision(self, tmp_path: Path) -> None:
        logger = ExperimentLogger(tmp_path)
        run_id = logger.start(
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name="trend_following_v1",
            assets=("BTCUSDT", "ETHUSDT"),
        )
        final = logger.finish(
            run_id=run_id,
            decision=Verdict.PAPER,
            metrics={"pf": 1.25, "sharpe": 0.9},
            robustness_tests={"cost_x2": {"pf": 1.05}},
        )
        assert final.decision            == Verdict.PAPER
        assert final.metrics["pf"]       == pytest.approx(1.25)
        assert final.is_final()          is True

    def test_load_nonexistent_raises(self, tmp_path: Path) -> None:
        logger = ExperimentLogger(tmp_path)
        with pytest.raises(FileNotFoundError, match="inexistant"):
            logger.load("inexistant")

    def test_list_all_returns_entries(self, tmp_path: Path) -> None:
        logger = ExperimentLogger(tmp_path)
        for i in range(3):
            logger.start(
                engine_name="INSTITUTIONAL_ENGINE",
                signal_name=f"signal_{i}",
                assets=("BTCUSDT",),
            )
        all_records = logger.list_all()
        assert len(all_records) >= 3

    def test_list_by_decision(self, tmp_path: Path) -> None:
        logger = ExperimentLogger(tmp_path)
        run_id = logger.start(
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name="test",
            assets=("BTCUSDT",),
        )
        logger.finish(run_id=run_id, decision=Verdict.REJECT)

        papers = logger.list_by_decision(Verdict.PAPER)
        rejects = logger.list_by_decision(Verdict.REJECT)
        assert len(rejects) >= 1
        # Le run ci-dessus a d'abord été PENDING puis REJECT
        assert not any(r.get("decision") == "PAPER" for r in rejects)

    def test_list_by_engine(self, tmp_path: Path) -> None:
        logger = ExperimentLogger(tmp_path)
        logger.start(
            engine_name="TRM_EVENT_ENGINE",
            signal_name="sniper",
            assets=("BTCUSDT",),
        )
        logger.start(
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name="trend",
            assets=("BTCUSDT",),
        )
        trm_runs = logger.list_by_engine("TRM_EVENT_ENGINE")
        inst_runs = logger.list_by_engine("INSTITUTIONAL_ENGINE")
        assert any(r.get("engine_name") == "TRM_EVENT_ENGINE" for r in trm_runs)
        assert any(r.get("engine_name") == "INSTITUTIONAL_ENGINE" for r in inst_runs)

    def test_registry_file_is_jsonl(self, tmp_path: Path) -> None:
        logger = ExperimentLogger(tmp_path)
        logger.start(
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name="test",
            assets=("BTCUSDT",),
        )
        content = (tmp_path / "registry.jsonl").read_text()
        for line in content.strip().splitlines():
            json.loads(line)  # chaque ligne doit être du JSON valide


# ══════════════════════════════════════════════════════════════════════════════
# TestCrossContractCompatibility
# ══════════════════════════════════════════════════════════════════════════════


class TestCrossContractCompatibility:
    """
    Vérifie que TRM_EVENT_ENGINE et INSTITUTIONAL_ENGINE utilisent
    le même contrat SignalFrame sans conflit.
    """

    def test_trm_and_institutional_signals_coexist(self) -> None:
        trm_signal = SignalFrame.make_flat(
            timestamp=_TS,
            asset="BTCUSDT",
            engine_name=EngineID.TRM,
            signal_name="sniper_long_v5",
            run_id="trm_run_001",
        )
        inst_signal = SignalFrame.make_flat(
            timestamp=_TS,
            asset="BTCUSDT",
            engine_name=EngineID.INSTITUTIONAL,
            signal_name="trend_following_v1",
            run_id="inst_run_001",
        )
        assert trm_signal.engine_name  == "TRM_EVENT_ENGINE"
        assert inst_signal.engine_name == "INSTITUTIONAL_ENGINE"
        assert trm_signal != inst_signal

    def test_signal_frame_columns_complete(self) -> None:
        sf = SignalFrame.make_flat(
            timestamp=_TS,
            asset="BTCUSDT",
            engine_name=EngineID.INSTITUTIONAL,
            signal_name="test",
            run_id="r1",
        )
        d = sf.to_dict()
        missing = [col for col in SIGNAL_FRAME_COLUMNS if col not in d]
        assert missing == [], f"Colonnes manquantes : {missing}"

    def test_risk_state_persists_across_engines(self, tmp_path: Path) -> None:
        """Le RiskState stocke l'exposition par engine indépendamment."""
        store = RiskStateStore(tmp_path / "risk.json")
        state = store.load()
        state.per_engine_exposure["TRM_EVENT_ENGINE"]   = 2_500.0
        state.per_engine_exposure["INSTITUTIONAL_ENGINE"] = 3_000.0
        store.save(state)

        loaded = store.load()
        assert loaded.per_engine_exposure["TRM_EVENT_ENGINE"] == pytest.approx(2_500.0)
        assert loaded.per_engine_exposure["INSTITUTIONAL_ENGINE"] == pytest.approx(3_000.0)

    @pytest.mark.parametrize("verdict", list(Verdict))
    def test_all_verdicts_serializable(self, verdict: Verdict) -> None:
        """Chaque Verdict doit survivre à une sérialisation JSON."""
        payload = json.dumps({"verdict": str(verdict)})
        v = Verdict(json.loads(payload)["verdict"])
        assert v == verdict

    @pytest.mark.parametrize("direction", list(Direction))
    def test_all_directions_in_signal_frame(self, direction: Direction) -> None:
        """Chaque Direction doit être acceptée dans SignalFrame."""
        sf = SignalFrame(
            timestamp=_TS,
            asset="BTCUSDT",
            engine_name="INSTITUTIONAL_ENGINE",
            signal_name="test",
            direction=direction,
            raw_score=0.0,
            calibrated_score=0.5,
            confidence=0.5,
            expected_return=0.01,
            expected_vol=0.2,
            horizon_minutes=60,
            max_holding_minutes=120,
            stop_distance=0.02,
            take_profit_distance=0.04,
            model_version="v1",
            feature_version="v1",
            label_version="v1",
            run_id="r1",
        )
        assert sf.direction == direction
