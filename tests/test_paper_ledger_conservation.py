"""
tests/test_paper_ledger_conservation.py
─────────────────────────────────────────────────────────────────────────────
Conservation comptable du paper v2 (audit 2026-07-18) — SANS réseau ni Mongo.

Invariants testés sur PaperPortfolio._mark_strategy :
  1. un mark pur (rien de dû) ne crée NI frais NI ordre NI réalisation ;
  2. identité : value_eur = capital + Σ(composantes ledger + latent)/fx ;
  3. un événement de funding n'est encaissé qu'UNE fois (marks répétés) ;
  4. un flip de gate réalise une fois et facture une fois (taker) ;
  5. le marquage rapide (poll 2,5 s) ne fait pas croître l'historique
     au-delà du throttle 30 s.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

import src.institutional.live.paper_portfolio as pp_mod
from src.institutional.live.paper_portfolio import PaperPortfolio, TAKER_FEE

FX = 1.1445
PX = {"BTCUSDT": 64000.0, "ETHUSDT": 1840.0}


class FakeCol:
    def __init__(self, doc):
        self.doc = doc
    def find_one(self, q):
        return self.doc
    def replace_one(self, q, doc, upsert=False):
        self.doc = doc
    def update_one(self, q, u):
        self.doc.update(u.get("$set", {}))


class FakeEvents:
    def __init__(self):
        self.rows = []
    def insert_one(self, d):
        self.rows.append(d)
    def count_documents(self, q):
        return len(self.rows)
    def find(self):
        class _C(list):
            def sort(self, *a):
                return self
            def limit(self, n):
                return self[:n]
        return _C(self.rows)
    def delete_one(self, q):
        pass


def _doc(now, regime_longs_active=True):
    now_ms = int(now.timestamp() * 1000)
    return {
        "_id": "main", "mode": "strategy", "capital_eur": 200_000.0,
        "eur_usdt_at_init": FX, "preset": "adaptive", "policy": "preset_adaptive",
        "carry": [{"symbol": "BTCUSDT", "notional": 91_000.0,
                   "spot_entry": 64000.0, "funding_rate": 5e-5,
                   "funding_paid_until": now_ms}],
        "basis": [{"symbol": "BTCUSDT", "notional": 50_000.0,
                   "spot_entry": 64000.0, "q_entry": 64500.0,
                   "days_to_expiry": 70, "q_symbol": "BTCUSDT_260925",
                   "basis_entry": 64500.0 / 64000.0 - 1, "accrued_frac": 0.0}],
        "longs": [{"symbol": "ETHUSDT", "notional": 12_000.0, "entry": 1840.0,
                   "ma20": None, "ma20_ts": now_ms,
                   "active": regime_longs_active, "realized": 0.0}],
        "alloc_note": None, "regime_at_init": "BULL",
        "ledger": {"version": 2, "last_mark": now.isoformat(),
                   "carry_accrued": 0.0, "basis_accrued": 0.0,
                   "borrow_accrued": 0.0, "longs_realized": 0.0, "fees": -100.0},
        "notify": {"last_pnl": 0.0},
        "next_rebalance_ms": now_ms + 10**9,     # jamais dû pendant le test
        "created_at": now.isoformat(), "history": [],
    }


def _patch(monkeypatch, regime="BULL", funding=None):
    monkeypatch.setattr(pp_mod, "live_prices", lambda syms: dict(PX))
    monkeypatch.setattr(pp_mod, "eur_usdt", lambda: FX)
    monkeypatch.setattr(pp_mod, "live_funding", lambda s: 5e-5)
    monkeypatch.setattr(pp_mod, "funding_events",
                        lambda s, a, b: list(funding or []))
    monkeypatch.setattr(pp_mod, "next_quarterly",
                        lambda s: (64500.0, 70, "BTCUSDT_260925"))
    monkeypatch.setattr(pp_mod, "btc_regime", lambda: regime)


def _pp(doc):
    db = SimpleNamespace(paper_portfolio=FakeCol(doc), portfolio_events=FakeEvents())
    return PaperPortfolio(db)


def _identity_gap(res):
    b = res["breakdown_eur"]
    total = (b["funding_encaissé"] + b["basis_accrué"] + b["longs_réalisé"]
             + b["longs_latent"] + b["frais"] + b["borrow"])
    return abs(res["value_eur"] - res["capital_eur"] - total)


def test_pure_mark_creates_no_fee_no_realization(monkeypatch):
    _patch(monkeypatch)
    now = datetime.now(timezone.utc)
    pp = _pp(_doc(now))
    r1 = pp.mark_to_market()
    r2 = pp.mark_to_market()
    led = pp.col.doc["ledger"]
    assert led["fees"] == -100.0                     # aucun frais créé par un mark
    assert led["carry_accrued"] == 0.0               # rien de dû (funding vide)
    assert led["longs_realized"] == 0.0
    assert led["basis_accrued"] >= 0.0               # accrual temporel seul
    assert r1["accounting"] == "v2_realized" and r2["accounting"] == "v2_realized"


def test_conservation_identity(monkeypatch):
    _patch(monkeypatch)
    pp = _pp(_doc(datetime.now(timezone.utc)))
    res = pp.mark_to_market()
    assert _identity_gap(res) < 0.05                 # € — arrondis d'affichage seuls


def test_funding_event_accrues_once(monkeypatch):
    now = datetime.now(timezone.utc)
    past = now - timedelta(hours=9)                  # funding dû (≥ 8 h)
    ev_ms = int((now - timedelta(hours=1)).timestamp() * 1000)
    _patch(monkeypatch, funding=[(ev_ms, 1e-4)])
    doc = _doc(now)
    doc["carry"][0]["funding_paid_until"] = int(past.timestamp() * 1000)
    pp = _pp(doc)
    pp.mark_to_market()
    after_first = pp.col.doc["ledger"]["carry_accrued"]
    assert abs(after_first - 91_000.0 * 1e-4) < 1e-9
    # l'événement est marqué payé → un 2e mark ne doit PAS le recompter
    monkeypatch.setattr(pp_mod, "funding_events", lambda s, a, b: [])
    pp.mark_to_market()
    assert pp.col.doc["ledger"]["carry_accrued"] == after_first
    assert pp.col.doc["carry"][0]["funding_paid_until"] == ev_ms


def test_gate_flip_realizes_and_charges_once(monkeypatch):
    _patch(monkeypatch, regime="BEAR")               # gate OFF → flip du long actif
    now = datetime.now(timezone.utc)
    doc = _doc(now, regime_longs_active=True)
    pp = _pp(doc)
    pp.mark_to_market()
    led = pp.col.doc["ledger"]
    expected_real = 12_000.0 * (PX["ETHUSDT"] / 1840.0 - 1)
    assert abs(led["longs_realized"] - expected_real) < 1e-9
    assert abs(led["fees"] - (-100.0 - 12_000.0 * TAKER_FEE)) < 1e-9
    assert pp.col.doc["longs"][0]["active"] is False
    # 2e mark : toujours OFF → aucune nouvelle réalisation ni frais
    pp.mark_to_market()
    led = pp.col.doc["ledger"]
    assert abs(led["longs_realized"] - expected_real) < 1e-9
    assert abs(led["fees"] - (-100.0 - 12_000.0 * TAKER_FEE)) < 1e-9


def test_fast_polling_throttles_history(monkeypatch):
    _patch(monkeypatch)
    pp = _pp(_doc(datetime.now(timezone.utc)))
    for _ in range(20):                              # simule le poll 2,5 s
        pp.mark_to_market()
    assert len(pp.col.doc["history"]) == 1           # throttle 30 s : 1 seul point
