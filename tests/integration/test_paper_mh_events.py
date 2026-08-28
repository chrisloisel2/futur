"""
tests/test_paper_mh_events.py
─────────────────────────────────────────────────────────────────────────────
Sleeve paper MH (scripts/run_paper_mh_events.py) : filtre book/MH/start,
capacité déterministe, conservation equity = capital + Σ pnl labellisés,
lecture seule du ledger shadow. Aucun réseau.
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))

_spec = importlib.util.spec_from_file_location(
    "rmh", Path(__file__).parents[2] / "scripts" / "run_paper_mh_events.py")
rmh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rmh)

START = pd.Timestamp("2026-07-19", tz="UTC")


def _row(ts, score=0.8, tier="book", horizon="MH_consensus(fwd_1h,fwd_4h,fwd_8h)",
         engine="LIQ_CASCADE", symbol="BTCUSDT", net=np.nan):
    return {"event_time": pd.Timestamp(ts, tz="UTC"), "symbol": symbol,
            "engine": engine, "horizon": horizon, "score": score,
            "rank_pct": score, "kind": "long", "tier": tier,
            "decided_at": "2026-07-19T12:00:00+00:00", "net_labeled": net}


def test_select_book_filters_tier_horizon_start():
    led = pd.DataFrame([
        _row("2026-07-20T01:00"),                                   # gardé
        _row("2026-07-20T02:00", tier="probe"),                     # écarté : probe
        _row("2026-07-20T03:00", horizon="fwd_4h"),                 # écarté : pas MH
        _row("2026-07-18T01:00"),                                   # écarté : avant start
    ])
    book = rmh.select_book(led, START)
    assert len(book) == 1
    assert book["event_time"].iloc[0] == pd.Timestamp("2026-07-20T01:00", tz="UTC")


def test_allocate_capacity_by_score():
    ts = "2026-07-20T01:00"
    led = pd.DataFrame([_row(ts, score=0.70 + i / 100, symbol=f"S{i}USDT")
                        for i in range(7)])
    book = rmh.allocate(rmh.select_book(led, START), capital=10000)
    assert int(book["taken"].sum()) == rmh.MAX_OPEN
    # les MAX_OPEN meilleurs scores sont pris, les 2 pires écartés
    assert set(book[~book["taken"]]["score"].round(2)) == {0.70, 0.71}


def test_capacity_frees_after_horizon():
    led = pd.DataFrame(
        [_row("2026-07-20T01:00", symbol=f"S{i}USDT") for i in range(5)]
        + [_row("2026-07-20T06:00", symbol="LATEUSDT")])   # cascade 4 h : slots libérés
    book = rmh.allocate(rmh.select_book(led, START), capital=10000)
    assert bool(book[book["symbol"] == "LATEUSDT"]["taken"].iloc[0])


def test_conservation_and_pending(tmp_path, monkeypatch):
    led = pd.DataFrame([
        _row("2026-07-20T01:00", net=0.02),                # labellisé : +2 %
        _row("2026-07-20T06:00", net=-0.01, symbol="ETHUSDT"),  # labellisé : −1 %
        _row("2026-07-20T12:00", symbol="SOLUSDT"),        # pending
    ])
    ledger = tmp_path / "decisions.parquet"
    led.to_parquet(ledger, index=False)
    monkeypatch.setattr(rmh, "SHADOW_LEDGER", ledger)
    monkeypatch.setattr(rmh, "SHADOW_STATE", tmp_path / "absent.json")
    monkeypatch.setattr(rmh, "OUT", tmp_path / "out")
    args = argparse.Namespace(capital=10000.0, paper_start="2026-07-19")
    state = rmh.run_once(args)
    notional = 10000.0 * rmh.WEIGHT
    assert np.isclose(state["equity"], 10000.0 + notional * (0.02 - 0.01))
    assert state["n_taken"] == 3 and state["n_labeled"] == 2
    assert state["n_pending"] == 1
    out = pd.read_parquet(tmp_path / "out" / "ledger.parquet")
    assert np.isnan(out[out["symbol"] == "SOLUSDT"]["pnl"].iloc[0])


def test_old_format_ledger_all_filtered(tmp_path, monkeypatch):
    """Régression 2026-07-19 : ledger réel pré-MH (sans colonne tier, horizon
    fwd_4h) → book vide ; le masque taken doit rester bool, pas object."""
    led = pd.DataFrame([_row("2026-07-11T01:00", horizon="fwd_4h"),
                        _row("2026-07-12T02:00", horizon="fwd_4h")]
                       ).drop(columns=["tier"])
    ledger = tmp_path / "decisions.parquet"
    led.to_parquet(ledger, index=False)
    monkeypatch.setattr(rmh, "SHADOW_LEDGER", ledger)
    monkeypatch.setattr(rmh, "SHADOW_STATE", tmp_path / "absent.json")
    monkeypatch.setattr(rmh, "OUT", tmp_path / "out")
    args = argparse.Namespace(capital=10000.0, paper_start="2026-07-19")
    state = rmh.run_once(args)
    assert state["status"] == "active"
    assert state["n_decisions"] == 0 and state["n_taken"] == 0
    assert state["equity"] == 10000.0
    assert state["per_engine"] == {}


def test_no_ledger_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(rmh, "SHADOW_LEDGER", tmp_path / "absent.parquet")
    monkeypatch.setattr(rmh, "SHADOW_STATE", tmp_path / "absent.json")
    monkeypatch.setattr(rmh, "OUT", tmp_path / "out")
    args = argparse.Namespace(capital=10000.0, paper_start="2026-07-19")
    state = rmh.run_once(args)
    assert state["status"] == "no_shadow_ledger"
    assert state["equity"] == 10000.0
