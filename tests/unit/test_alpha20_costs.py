"""
tests/test_alpha20_costs.py
─────────────────────────────────────────────────────────────────────────────
Registre de coûts : fallback assumed étiqueté, précédence des snapshots réels,
parseur de trimestriels (fixture exchangeInfo), borrow, TCA + règle resize ×3,
découverte dynamique dans paper_portfolio (fallback statique). Aucun réseau.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.alpha20.contracts import CostSnapshot
from src.alpha20.costs import borrow_registry, fee_registry
from src.alpha20.costs.implementation_shortfall import (
    TCARecord, full_roundtrip_cost_bp, resize_worth_it)

EXCHANGE_INFO = {"symbols": [
    {"symbol": "BTCUSDT_260925", "pair": "BTCUSDT", "status": "TRADING",
     "contractType": "CURRENT_QUARTER", "deliveryDate": 1790000000000},
    {"symbol": "BTCUSDT_261225", "pair": "BTCUSDT", "status": "TRADING",
     "contractType": "NEXT_QUARTER", "deliveryDate": 1798000000000},
    {"symbol": "BTCUSDT", "pair": "BTCUSDT", "status": "TRADING",
     "contractType": "PERPETUAL", "deliveryDate": 4133404800000},
    {"symbol": "ETHUSDT_260925", "pair": "ETHUSDT", "status": "SETTLING",
     "contractType": "CURRENT_QUARTER", "deliveryDate": 1790000000000},
    {"symbol": "SOLUSDT_250926", "pair": "SOLUSDT", "status": "TRADING",
     "contractType": "CURRENT_QUARTER", "deliveryDate": 1000000000000},
]}


def test_effective_costs_assumed_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(fee_registry, "SNAP_DIR", tmp_path)
    c = fee_registry.effective_costs("binance_usdm", "BTCUSDT")
    assert c.source == "assumed" and c.taker_bp == 5.0 and c.maker_bp == 2.0


def test_snapshot_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setattr(fee_registry, "SNAP_DIR", tmp_path)
    fee_registry.save_snapshot(CostSnapshot(
        venue="binance_usdm", instrument="BTCUSDT", maker_bp=1.8, taker_bp=4.5,
        as_of="2026-07-19", source="api_signed"))
    c = fee_registry.effective_costs("binance_usdm", "BTCUSDT")
    assert c.source == "api_signed" and c.taker_bp == 4.5
    fee_registry.save_snapshot(CostSnapshot(       # plus récent → gagne
        venue="binance_usdm", instrument="BTCUSDT", maker_bp=1.6, taker_bp=4.0,
        as_of="2026-07-20", source="api_signed"))
    assert fee_registry.effective_costs("binance_usdm", "BTCUSDT").taker_bp == 4.0


def test_commission_without_keys_returns_none(monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    assert fee_registry.fetch_binance_commission("BTCUSDT") is None


def test_parse_quarterlies_filters():
    now_ms = 1780000000000
    q = fee_registry.parse_quarterlies(EXCHANGE_INFO, now_ms=now_ms)
    # perp exclu, SETTLING exclu, échéance passée (SOL) exclue
    assert [r["symbol"] for r in q] == ["BTCUSDT_260925", "BTCUSDT_261225"]
    assert q[0]["days_to_expiry"] == pytest.approx(
        (1790000000000 - now_ms) / 86_400_000)
    assert fee_registry.parse_quarterlies(EXCHANGE_INFO, "ETHUSDT",
                                          now_ms=now_ms) == []


def test_borrow_assumed_then_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(borrow_registry, "SNAP_DIR", tmp_path)
    b = borrow_registry.effective_borrow("binance_margin", "USDT")
    assert b["source"] == "assumed" and b["rate_ann"] == 0.08
    borrow_registry.save_borrow("binance_margin", "USDT", 0.061, "api_signed")
    b = borrow_registry.effective_borrow("binance_margin", "USDT")
    assert b["rate_ann"] == 0.061 and b["source"] == "api_signed"


def test_tca_metrics_and_resize_rule():
    rec = TCARecord(decision_ts="2026-07-19T10:00:00Z", sleeve="mh",
                    symbol="BTCUSDT", side=1, qty=2.0, decision_px=100.0,
                    arrival_px=100.05, avg_exec_px=100.10, filled_qty=1.5,
                    spread_paid_bp=1.2, delay_s=3.0,
                    post_px={"5m": 100.00})
    m = rec.metrics()
    assert m["unfilled_frac"] == pytest.approx(0.25)
    assert m["shortfall_bp"] == pytest.approx(10.0, abs=0.01)
    assert m["impact_bp"] == pytest.approx(5.0, abs=0.01)
    assert m["adverse_5m_bp"] == pytest.approx(10.0, abs=0.01)  # prix revenu contre nous
    rt = full_roundtrip_cost_bp(maker_bp=2.0, taker_bp=5.0, spread_bp=1.0,
                                slippage_bp=2.0)
    assert rt == pytest.approx(2 * 2 * 7.0 + 1.0)
    assert resize_worth_it(3.1 * rt, rt)
    assert not resize_worth_it(2.9 * rt, rt)


def test_paper_portfolio_quarterly_dynamic_and_fallback(monkeypatch):
    import src.institutional.live.paper_portfolio as pp
    calls = {}

    def fake_fapi(path):
        calls[path.split("?")[0]] = calls.get(path.split("?")[0], 0) + 1
        if "exchangeInfo" in path:
            return EXCHANGE_INFO
        if "ticker/price" in path:
            return {"price": "64123.5"}
        raise RuntimeError("inattendu")

    monkeypatch.setattr(pp, "_fapi", fake_fapi)
    pp._QUARTERLIES_CACHE.update(ts=0.0, by_pair={})
    px, days, sym = pp.next_quarterly("BTCUSDT")
    assert sym == "BTCUSDT_260925" and px == 64123.5 and days is not None
    # fallback : API exchangeInfo morte → liste statique
    def dead_fapi(path):
        if "exchangeInfo" in path:
            raise RuntimeError("down")
        return {"price": "64123.5"}
    monkeypatch.setattr(pp, "_fapi", dead_fapi)
    pp._QUARTERLIES_CACHE.update(ts=0.0, by_pair={})
    px, days, sym = pp.next_quarterly("BTCUSDT")
    assert px == 64123.5 and sym.startswith("BTCUSDT_")
