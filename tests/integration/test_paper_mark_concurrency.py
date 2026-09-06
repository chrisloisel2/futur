"""
tests/test_paper_mark_concurrency.py
─────────────────────────────────────────────────────────────────────────────
Correctif concurrence du mark paper (incident 2026-07-18 10:15:50/52 :
double rebalance par marks concurrents PWA 2,5 s + timer 15 min).

  1. deux marks SIMULTANÉS avec rebalance dû → exactement UN rebalance et
     UNE écriture de frais ;
  2. la garde _claim_rebalance est atomique, y compris entre deux instances
     (≈ deux process) partageant la même collection.

Aucun réseau. La FakeCol émule l'atomicité Mongo (deepcopy + lock interne).
"""
import copy
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[2]))

import src.institutional.live.paper_portfolio as pp_mod
from src.institutional.live.paper_portfolio import (
    PaperPortfolio, MAKER_FEE, REBALANCE_S)

FX = 1.1445
PX = {"BTCUSDT": 64000.0, "ETHUSDT": 1840.0}


class AtomicFakeCol:
    """find_one rend une COPIE (comme pymongo) ; find_one_and_update est
    atomique sous lock — c'est la sémantique dont dépend la garde."""
    def __init__(self, doc):
        self.doc = doc
        self.lock = threading.Lock()
        self.replace_calls = 0

    def find_one(self, q):
        with self.lock:
            return copy.deepcopy(self.doc)

    def replace_one(self, q, doc, upsert=False):
        with self.lock:
            self.doc = copy.deepcopy(doc)
            self.replace_calls += 1

    def update_one(self, q, u):
        with self.lock:
            self.doc.update(u.get("$set", {}))

    def find_one_and_update(self, flt, upd):
        with self.lock:
            cond = flt.get("next_rebalance_ms", {})
            if "$lte" in cond and not (
                    self.doc.get("next_rebalance_ms", 0) <= cond["$lte"]):
                return None
            old = copy.deepcopy(self.doc)
            self.doc.update(upd.get("$set", {}))
            return old


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


def _doc_due(now):
    now_ms = int(now.timestamp() * 1000)
    return {"_id": "main", "mode": "strategy", "capital_eur": 200_000.0,
            "eur_usdt_at_init": FX, "preset": "adaptive",
            "policy": "preset_adaptive", "carry": [], "basis": [], "longs": [],
            "alloc_note": None, "regime_at_init": "BEAR",
            "ledger": {"version": 2, "last_mark": now.isoformat(),
                       "carry_accrued": 0.0, "basis_accrued": 0.0,
                       "borrow_accrued": 0.0, "longs_realized": 0.0, "fees": 0.0},
            "notify": {"last_pnl": 0.0},
            "next_rebalance_ms": now_ms - 1000,      # rebalance DÛ
            "created_at": now.isoformat(), "history": []}


def _patch(monkeypatch):
    monkeypatch.setattr(pp_mod, "live_prices", lambda syms: dict(PX))
    monkeypatch.setattr(pp_mod, "eur_usdt", lambda: FX)
    monkeypatch.setattr(pp_mod, "live_funding", lambda s: 5e-5)
    monkeypatch.setattr(pp_mod, "funding_events", lambda s, a, b: [])
    monkeypatch.setattr(pp_mod, "next_quarterly",
                        lambda s: (PX[s] * 1.008, 70, s + "_260925"))
    monkeypatch.setattr(pp_mod, "btc_regime", lambda: "BEAR")
    monkeypatch.setattr(PaperPortfolio, "_inverse_vol_weights",
                        lambda self, syms: {s: 1.0 / len(syms) for s in syms})
    monkeypatch.setattr(PaperPortfolio, "_ma", lambda self, s, n: None)


def test_two_simultaneous_marks_one_rebalance_one_fee_write(monkeypatch):
    _patch(monkeypatch)
    now = datetime.now(timezone.utc)
    col = AtomicFakeCol(_doc_due(now))
    events = FakeEvents()
    db = SimpleNamespace(paper_portfolio=col, portfolio_events=events)

    results, errors = [], []

    def worker():
        try:
            results.append(PaperPortfolio(db).mark_to_market())
        except Exception as e:                       # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors and len(results) == 2
    assert all(not t.is_alive() for t in threads)    # pas de deadlock

    # UN seul rebalance émis et exécuté
    reb_events = [e for e in events.rows if "Rebalance" in e["title"]]
    assert len(reb_events) == 1

    # UNE seule écriture de frais : cibles BEAR = carry BTC 0,40E + carry ETH
    # 0,35E + basis BTC 0,25E créés depuis zéro → maker 2 bps × 2 jambes sur
    # 1,00 × equity, exactement une fois (le double = -183 USDT trahirait la course)
    eq0 = 200_000.0 * FX
    expected_fee = -(0.40 + 0.35 + 0.25) * eq0 * MAKER_FEE * 2
    assert abs(col.doc["ledger"]["fees"] - expected_fee) < 1e-6

    # la fenêtre est réarmée dans le futur, une seule fois
    assert col.doc["next_rebalance_ms"] > int(now.timestamp() * 1000)
    assert len([c for c in col.doc["carry"]]) == 2
    assert len([b for b in col.doc["basis"]]) == 1


def test_claim_rebalance_atomic_across_instances(monkeypatch):
    _patch(monkeypatch)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    col = AtomicFakeCol(_doc_due(now))
    db = SimpleNamespace(paper_portfolio=col, portfolio_events=FakeEvents())
    pp1, pp2 = PaperPortfolio(db), PaperPortfolio(db)   # ≈ deux process

    assert pp1._claim_rebalance(now_ms) is True          # premier : prend la fenêtre
    assert pp2._claim_rebalance(now_ms) is False         # second : refusé
    assert pp1._claim_rebalance(now_ms) is False         # rejeu : refusé aussi
    assert col.doc["next_rebalance_ms"] == now_ms + REBALANCE_S * 1000
