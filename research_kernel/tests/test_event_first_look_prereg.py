"""The sealed event first look: selection rules are pure functions of the record, the verdict
order is fixed, the seal refuses tampering, and the FREEZE pins must match the files on disk
(so that editing the harness after the seal fails the whole test suite)."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mechanisms" / "event_reaction_v1"))
import first_look as FL  # noqa: E402

FREEZE = ROOT / "reports" / "first_look" / "event_reaction_v1_FREEZE.json"


def _rec(**kw):
    base = {"source": "binance", "event_type": "futures_listing", "market_type": "perp", "asset": None, "raw_title": "",
            "raw_url": "https://x", "raw_body_hash": "h", "publication_ts_exchange": "2024-03-01T10:00:00+00:00",
            "publication_ts_exchange_ms": 1709287200000, "event_id": "e"}
    base.update(kw); return base


def test_selection_rules_are_pure_and_match_the_prereg():
    ok = _rec(raw_title="Binance Futures Will Launch USDⓈ-Margined ABCUSDT Perpetual Contract (2024-03-01)")
    assert FL.selection_reason("binance_futures_listing", ok) == "" and FL.asset_of(ok) == "ABC"
    assert FL.selection_reason("binance_futures_listing", _rec(raw_title="Binance Futures Will Launch Multiple USDⓈ-Margined Perpetual Contracts")) == "multi_asset_title_without_assets"
    assert FL.selection_reason("binance_futures_listing", _rec(raw_title="Binance Futures Will Launch Multiple USDⓈ-Margined TradFi Perpetual Contracts")) == "non_crypto"
    assert FL.selection_reason("binance_futures_listing", _rec(raw_title="Binance Futures Will Launch ABC Coin-Margined Perpetual Contract")) == "coin_margined"
    assert FL.selection_reason("binance_futures_listing", _rec(publication_ts_exchange_ms=None)) == "no_publication_ts"
    assert FL.selection_reason("binance_delisting", _rec(event_type="delisting", market_type="spot", asset="XYZ", raw_title="Binance Will Delist XYZ on 2024-03-10")) == ""
    assert FL.selection_reason("binance_delisting", _rec(event_type="delisting", market_type="spot", asset="XYZ", raw_title="Notice of Removal of Trading Pairs - 2024-03-10")) == "not_asset_delisting"
    assert FL.selection_reason("okx_bybit_listing", _rec(source="bybit", event_type="listing", market_type="spot", asset="XAUT", raw_title="XAUT Token Splash— Grab a share")) == "promo_or_non_orderbook"
    assert FL.selection_reason("okx_bybit_listing", _rec(source="bybit", asset="ABC", raw_title="New listing: ABCUSDT Perpetual Contract, with up to 25x leverage")) == ""
    assert FL.spot_symbol("1000PEPE") == "PEPEUSDT" and FL.spot_symbol("ABC") == "ABCUSDT"


def test_verdict_order_is_fixed():
    h = FL.HYPOTHESES["H1"]; thr = 2.2414
    good = {"n": 100, "mean_bps": 100.0, "t": 3.0, "n_eff": 60, "top1_share": 0.05}
    assert FL.verdict_for(h, {**good, "mean_bps": 10.0}, 28.0, 1.0, None, None, thr)[0] == "REJECTED_NO_GROSS"
    assert FL.verdict_for(h, {**good, "mean_bps": 50.0}, 28.0, 1.0, None, None, thr)[0] == "REJECTED_COST_WALL"
    assert FL.verdict_for(h, {**good, "t": 1.0}, 28.0, 1.0, None, None, thr)[0] == "INDECIDABLE"
    assert FL.verdict_for(h, good, 28.0, 1.0, 120.0, None, thr)[0] == "INDECIDABLE"      # pre-publication drift >= gross
    assert FL.verdict_for(h, good, 28.0, 1.0, None, 150.0, thr)[0] == "INDECIDABLE"      # placebo >= gross
    assert FL.verdict_for(h, good, 28.0, 0.5, None, None, thr)[0] == "INDECIDABLE"       # not tradable
    assert FL.verdict_for(h, good, 28.0, 1.0, 5.0, 1.0, thr)[0] == "FORWARD_SEAL_REQUIRED"


def test_constants_are_the_protocol_ones():
    assert FL.MIN_GROSS_BPS == 30 and FL.COST_WALL_X == 3 and FL.N_FAMILY_TESTS == 4 and FL.ENTRY_DELAY_S == 60
    assert FL.COST_RT_BPS == {"um": 18.0, "spot": 28.0}
    assert set(FL.HORIZON_MIN) == {"1m", "5m", "15m", "60m", "6h", "24h"}
    for h in FL.HYPOTHESES.values():
        assert len(h["sensitivities"]) <= 2 and h["primary"] not in h["sensitivities"]


def test_synthetic_chain_recovers_injected_effect_exactly():
    res = FL.positive_control(n_per_h=12, effect_bps=80.0, seed=3)
    for k, v in res["recovered_bps"].items():
        assert 0.75 * 80 <= v <= 1.15 * 80, (k, v)
    assert all(v["verdict"] != "FORWARD_SEAL_REQUIRED" for v in res["null"].values())


def test_seal_refuses_tampering_and_second_look(monkeypatch, tmp_path):
    monkeypatch.setattr(FL, "FREEZE", tmp_path / "nope.json")
    with pytest.raises(SystemExit, match="pas de FREEZE"):
        FL.check_seal()
    fz = tmp_path / "F.json"; frozen = tmp_path / "frozen.jsonl"; frozen.write_text("{}\n")
    fz.write_text(json.dumps({"snapshot": {"sha256": hashlib.sha256(frozen.read_bytes()).hexdigest()},
                              "pins": {"mechanisms/event_reaction_v1/first_look.py": "0" * 64}}))
    monkeypatch.setattr(FL, "FREEZE", fz); monkeypatch.setattr(FL, "FROZEN", frozen)
    with pytest.raises(SystemExit, match="a change depuis le scellement"):
        FL.check_seal()


@pytest.mark.skipif(not FREEZE.exists(), reason="not sealed yet")
def test_freeze_pins_match_files_on_disk():
    fz = json.loads(FREEZE.read_text())
    for rel, expected in fz["pins"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == expected, f"{rel} modified after the seal"
    for k in ("hypothesis", "horizons", "cost_model", "promotion_criteria", "rejection_criteria", "multiplicity_family"):
        assert fz[k], k
    assert fz["snapshot"]["row_count"] > 0 and len(fz["snapshot"]["sha256"]) == 64
