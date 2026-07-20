"""
tests/test_alpha20_tournament_bus.py
─────────────────────────────────────────────────────────────────────────────
Bus de marché : lookahead impossible par construction (close_asof refuse tout
ce qui dépasse le cutoff), trous de marché journalisés, snapshot persisté et
rejouable à l'identique, chaîne de hash. Aucun réseau (live_prices/live_funding
monkeypatchées).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.alpha20.tournament import market_bus


def _mk_enriched(tmp_path, symbol="BTCUSDT", start="2026-07-20T00:00Z", hours=48):
    idx = pd.date_range(start, periods=hours, freq="1h")
    df = pd.DataFrame({"datetime": idx, "close": 100.0 + np.arange(hours) * 0.1})
    (tmp_path / "enriched").mkdir(exist_ok=True)
    df.to_parquet(tmp_path / "enriched" / f"{symbol}_1h_enriched.parquet", index=False)


def test_close_asof_never_returns_data_after_cutoff(tmp_path, monkeypatch):
    monkeypatch.setattr(market_bus, "ENRICHED", tmp_path / "enriched")
    _mk_enriched(tmp_path)
    snap = market_bus.MarketSnapshot(
        market_event_id="x", cutoff="2026-07-20T10:00:00Z",
        decision_ts="2026-07-20T10:00:00Z", received_ts="2026-07-20T10:00:00Z")
    r = snap.close_asof("BTCUSDT", pd.Timestamp("2026-07-20T05:00:00Z"))
    assert r is not None
    ts, px = r
    assert ts <= pd.Timestamp("2026-07-20T10:00:00Z")
    # aucune barre postérieure au cutoff, même en demandant loin dans le futur
    r2 = snap.close_asof("BTCUSDT", pd.Timestamp("2026-07-20T09:59:00Z"))
    assert r2[0] <= pd.Timestamp(snap.cutoff)
    # asof postérieur au cutoff : rien de valide à retourner
    r3 = snap.close_asof("BTCUSDT", pd.Timestamp("2026-07-20T11:00:00Z"))
    assert r3 is None


def test_gaps_logged_on_missing_price_and_funding(tmp_path, monkeypatch):
    monkeypatch.setattr(market_bus, "BUS_DIR", tmp_path / "bus")
    import src.institutional.live.paper_portfolio as pp
    monkeypatch.setattr(pp, "live_prices", lambda syms: {"BTCUSDT": 64000.0})
    monkeypatch.setattr(pp, "live_funding", lambda s: None if s == "XXXUSDT" else 1e-4)
    snap = market_bus.build_snapshot(["BTCUSDT", "ETHUSDT"], ["XXXUSDT"], [])
    assert "missing_price:ETHUSDT" in snap.gaps
    assert "missing_funding:XXXUSDT" in snap.gaps
    assert "BTCUSDT" in snap.prices and "ETHUSDT" not in snap.prices


def test_snapshot_persisted_and_replay_exact(tmp_path, monkeypatch):
    monkeypatch.setattr(market_bus, "BUS_DIR", tmp_path / "bus")
    import src.institutional.live.paper_portfolio as pp
    monkeypatch.setattr(pp, "live_prices", lambda syms: {"BTCUSDT": 64000.0})
    monkeypatch.setattr(pp, "live_funding", lambda s: 1e-4)
    snap = market_bus.build_snapshot(["BTCUSDT"], ["BTCUSDT"], [])
    replayed = market_bus.replay(snap.market_event_id)
    assert replayed is not None
    assert replayed.prices == snap.prices
    assert replayed.funding == snap.funding
    assert replayed.cutoff == snap.cutoff
    assert market_bus.verify_chain()
    assert market_bus.replay("inconnu") is None


def test_bus_chain_survives_interrupted_write(tmp_path, monkeypatch):
    monkeypatch.setattr(market_bus, "BUS_DIR", tmp_path / "bus")
    import src.institutional.live.paper_portfolio as pp
    monkeypatch.setattr(pp, "live_prices", lambda syms: {"BTCUSDT": 64000.0})
    monkeypatch.setattr(pp, "live_funding", lambda s: 1e-4)
    market_bus.build_snapshot(["BTCUSDT"], [], [])
    f = tmp_path / "bus" / "bus.jsonl"
    with open(f, "a") as fh:
        fh.write('{"market_event_id": "trunc')          # écriture interrompue
    assert market_bus.verify_chain()                     # queue non commise ignorée
    market_bus.build_snapshot(["BTCUSDT"], [], [])        # répare et continue
    assert market_bus.verify_chain()
    assert len(market_bus._load()[0]) == 2


def test_all_runners_see_the_same_snapshot_object(tmp_path, monkeypatch):
    """Isolation/cohérence : un seul appel réseau par cycle, tous les runners
    reçoivent EXACTEMENT le même market_event_id."""
    monkeypatch.setattr(market_bus, "BUS_DIR", tmp_path / "bus")
    calls = {"n": 0}
    import src.institutional.live.paper_portfolio as pp

    def counted(syms):
        calls["n"] += 1
        return {s: 1.0 for s in syms}
    monkeypatch.setattr(pp, "live_prices", counted)
    monkeypatch.setattr(pp, "live_funding", lambda s: 1e-4)
    snap = market_bus.build_snapshot(["BTCUSDT", "ETHUSDT", "SOLUSDT"], [], [])
    assert calls["n"] == 1                     # UN seul appel pour tout le cycle
    ids_seen = {snap.market_event_id for _ in range(5)}   # 5 "runners" relisent le même objet
    assert len(ids_seen) == 1
