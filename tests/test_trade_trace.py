"""
tests/test_trade_trace.py — item P0.3 (phase CLOSE THE EXECUTION LOOP) :
reconstruction "pourquoi cette position existe-t-elle" à partir de
state.json (orders/fills/positions) + intent_ledger.parquet + decisions.parquet.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import src.institutional.live_alpha_lab.trade_trace as trace_mod


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(trace_mod, "LAB_DIR", tmp_path)
    monkeypatch.setattr(trace_mod, "PORTFOLIOS_DIR", tmp_path / "portfolios")
    return tmp_path


def _write_state(lab, portfolio, positions=None, orders=None, fills=None):
    d = lab / "portfolios" / portfolio
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({
        "positions": positions or {}, "orders": orders or [], "fills": fills or [],
    }))


def _write_intent_ledger(lab, portfolio, rows):
    d = lab / "portfolios" / portfolio
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(d / "intent_ledger.parquet", index=False)


def _write_decisions(lab, alpha_id, rows):
    d = lab / alpha_id
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(d / "decisions.parquet", index=False)


def _order(order_id="O1", alpha_id="SHORT_COVERING_CONTINUATION_V1", symbol="JUPUSDT",
          ts_decision="2026-09-01T00:00:00+00:00", status="FILLED",
          filled_quantity=100.0, requested_quantity=100.0, fill_price=1.0):
    return {
        "order_id": order_id, "intent_id": f"{alpha_id}:{symbol}:{ts_decision}",
        "signal_id": f"{alpha_id}:{symbol}:{ts_decision}", "alpha_id": alpha_id,
        "portfolio_id": "P1_EQUAL_RISK", "timestamp_decision": ts_decision,
        "timestamp_submit": ts_decision, "timestamp_fill": ts_decision,
        "symbol": symbol, "side": "BUY", "requested_quantity": requested_quantity,
        "filled_quantity": filled_quantity, "remaining_quantity": requested_quantity - filled_quantity,
        "requested_notional": requested_quantity * fill_price, "fill_price": fill_price,
        "mark_price_at_decision": fill_price, "spread_bps": 4.0, "slippage_bps": 2.0,
        "fee_bps": 5.0, "fee_amount": 0.5, "status": status,
    }


def test_reconstruct_finds_orders_fills_and_matching_decision(lab):
    _write_state(
        lab, "P1_EQUAL_RISK",
        positions={"JUPUSDT": {"quantity": 100.0, "entry_price": 1.0, "owner_alpha": "SHORT_COVERING_CONTINUATION_V1"}},
        orders=[_order()],
        fills=[{"fill_id": "O1:F0", "order_id": "O1", "symbol": "JUPUSDT",
               "timestamp": "2026-09-01T00:00:00+00:00", "quantity": 100.0, "fill_price": 1.0}],
    )
    _write_intent_ledger(lab, "P1_EQUAL_RISK", [
        {"ts": "2026-09-01T00:00:00+00:00", "instrument": "JUPUSDT",
        "alpha_intents": "[]", "portfolio_target": 100.0, "executed_delta": 100.0},
    ])
    _write_decisions(lab, "SHORT_COVERING_CONTINUATION_V1", [
        {"timestamp": pd.Timestamp("2026-09-01T00:00:00+00:00"), "asset": "JUPUSDT",
        "direction": "LONG", "score_net": 0.02, "reason": "ACCEPT_SHADOW"},
    ])

    result = trace_mod.reconstruct("P1_EQUAL_RISK", "JUPUSDT")
    assert result["n_orders"] == 1
    assert result["n_fills"] == 1
    assert result["n_decisions_found"] == 1
    assert result["n_decisions_not_found"] == 0
    assert result["current_position"]["quantity"] == 100.0
    step = result["steps"][0]
    assert step["decision"]["reason"] == "ACCEPT_SHADOW"
    assert step["intent_ledger_row"]["portfolio_target"] == 100.0
    assert step["raw_event_id"] == "NOT_AVAILABLE"
    assert step["feature_snapshot_id"] == "NOT_AVAILABLE"


def test_reconstruct_explicit_when_decision_not_found_not_silent(lab):
    _write_state(lab, "P1_EQUAL_RISK", orders=[_order(ts_decision="2026-08-01T00:00:00+00:00")])
    _write_decisions(lab, "SHORT_COVERING_CONTINUATION_V1", [
        {"timestamp": pd.Timestamp("2026-09-01T00:00:00+00:00"), "asset": "JUPUSDT",
        "direction": "LONG", "score_net": 0.02, "reason": "ACCEPT_SHADOW"},
    ])
    result = trace_mod.reconstruct("P1_EQUAL_RISK", "JUPUSDT")
    assert result["n_decisions_found"] == 0
    assert result["n_decisions_not_found"] == 1
    assert result["steps"][0]["decision"] is None
    assert result["steps"][0]["decision_found"] is False


def test_reconstruct_no_orders_at_all_returns_empty_not_error(lab):
    result = trace_mod.reconstruct("P1_EQUAL_RISK", "NEVERTRADEDUSDT")
    assert result["n_orders"] == 0
    assert result["current_position"] is None
    assert result["steps"] == []


def test_reconstruct_multiple_orders_ordered_chronologically(lab):
    _write_state(lab, "P1_EQUAL_RISK", orders=[
        _order(order_id="O2", ts_decision="2026-09-01T01:00:00+00:00"),
        _order(order_id="O1", ts_decision="2026-09-01T00:00:00+00:00"),
    ])
    result = trace_mod.reconstruct("P1_EQUAL_RISK", "JUPUSDT")
    assert [s["order"]["order_id"] for s in result["steps"]] == ["O1", "O2"]


def test_narrate_produces_readable_text_without_crashing(lab):
    _write_state(
        lab, "P1_EQUAL_RISK",
        positions={"JUPUSDT": {"quantity": 100.0, "entry_price": 1.0,
                              "owner_alpha": "SHORT_COVERING_CONTINUATION_V1", "realized_pnl": 0.0}},
        orders=[_order()],
    )
    result = trace_mod.reconstruct("P1_EQUAL_RISK", "JUPUSDT")
    text = trace_mod.narrate(result)
    assert "JUPUSDT" in text
    assert "O1" in text


def test_find_decision_row_unknown_alpha_returns_none_not_crash(lab):
    assert trace_mod.find_decision_row("UNKNOWN_ALPHA_X", "BTCUSDT", "2026-09-01T00:00:00+00:00") is None


# ── P1 (phase OPERATIONAL HARDENING) : raw_event_id/feature_snapshot_id ──

def test_reconstruct_uses_real_ids_when_decision_has_been_stamped(lab):
    """Une décision écrite APRÈS le déploiement de stamp_event_ids() porte
    de vrais raw_event_id/feature_snapshot_id -- trade_trace doit les
    reprendre tels quels, pas les écraser par NOT_AVAILABLE."""
    _write_state(lab, "P1_EQUAL_RISK", orders=[_order()])
    _write_decisions(lab, "SHORT_COVERING_CONTINUATION_V1", [
        {"timestamp": pd.Timestamp("2026-09-01T00:00:00+00:00"), "asset": "JUPUSDT",
        "direction": "LONG", "score_net": 0.02, "reason": "ACCEPT_SHADOW",
        "raw_event_id": "c9b865d9177eacea", "feature_snapshot_id": "190be779f54786dd"},
    ])
    result = trace_mod.reconstruct("P1_EQUAL_RISK", "JUPUSDT")
    step = result["steps"][0]
    assert step["raw_event_id"] == "c9b865d9177eacea"
    assert step["feature_snapshot_id"] == "190be779f54786dd"
    assert step["raw_event_id_reason"] is None
    assert step["feature_snapshot_id_reason"] is None


def test_reconstruct_old_row_without_id_columns_stays_not_available_with_reason(lab):
    """Colonnes absentes (décisions écrites avant le déploiement) : jamais
    un crash, jamais un ID inventé -- NOT_AVAILABLE avec la raison,
    exactement comme avant ce changement."""
    _write_state(lab, "P1_EQUAL_RISK", orders=[_order()])
    _write_decisions(lab, "SHORT_COVERING_CONTINUATION_V1", [
        {"timestamp": pd.Timestamp("2026-09-01T00:00:00+00:00"), "asset": "JUPUSDT",
        "direction": "LONG", "score_net": 0.02, "reason": "ACCEPT_SHADOW"},
    ])
    result = trace_mod.reconstruct("P1_EQUAL_RISK", "JUPUSDT")
    step = result["steps"][0]
    assert step["raw_event_id"] == "NOT_AVAILABLE"
    assert step["feature_snapshot_id"] == "NOT_AVAILABLE"
    assert step["raw_event_id_reason"] is not None
    assert step["feature_snapshot_id_reason"] is not None
