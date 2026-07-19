"""
tests/test_paper_churn_guard.py
─────────────────────────────────────────────────────────────────────────────
Règle NET 2026-07-19 du paper adaptatif (anti-churn structurel) :

  1. gate d'OUVERTURE au score net : un yield brut > plancher 1 % mais sous
     coûts A/R amortis + marge reste DORMANT (l'ancienne règle aurait ouvert
     → la divergence est comptée dans churn_guard.fees_legacy) ;
  2. hystérésis : un sleeve DÉJÀ ouvert est gardé tant que son brut > 0,
     même sous le plancher 1 % (l'ancienne règle l'aurait coupé) ;
  3. min-hold 72 h : une coupe (cible 0) sur un sleeve jeune est DIFFÉRÉE
     sans frais — y compris pour un doc pré-existant sans opened_ms
     (backdaté sur created_at) — et le mark expose le résumé churn_guard ;
  4. min-hold expiré → la coupe passe et facture une seule fois ;
  5. yield toxique (< -borrow) → le min-hold saute, coupe immédiate ;
  6. _guard_accrue : différentiels d'accrual et de borrow du contrefactuel ;
  7. v1.1 veto de réversion : un resize opposé au précédent < 72 h est différé
     même sur un sleeve âgé (le min-hold ne protège que les jeunes) ;
  8. réversion expirée (> 72 h) ou même direction ou yield toxique → passe ;
  9. tout resize exécuté tamponne last_resize_{ms,dir}.

Aucun réseau. Mêmes fakes que test_paper_mark_concurrency.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))

import src.institutional.live.paper_portfolio as pp_mod
from src.institutional.live.paper_portfolio import (
    PaperPortfolio, MAKER_FEE, MIN_HOLD_S, BORROW_ANN)

FX = 1.1445
PX = {"BTCUSDT": 64000.0, "ETHUSDT": 1840.0}
EQ0 = 200_000.0 * FX                       # equity à frais/accruals nuls


class FakeCol:
    def __init__(self, doc):
        self.doc = doc
    def find_one(self, q):
        return self.doc
    def replace_one(self, q, doc, upsert=False):
        self.doc = doc
    def update_one(self, q, u):
        self.doc.update(u.get("$set", {}))
    def find_one_and_update(self, flt, upd):
        cond = flt.get("next_rebalance_ms", {})
        if "$lte" in cond and not (
                self.doc.get("next_rebalance_ms", 0) <= cond["$lte"]):
            return None
        old = dict(self.doc)
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


def _doc(now, carry=None, created_hours_ago=0.0):
    created = now - timedelta(hours=created_hours_ago)
    now_ms = int(now.timestamp() * 1000)
    return {"_id": "main", "mode": "strategy", "capital_eur": 200_000.0,
            "eur_usdt_at_init": FX, "preset": "adaptive",
            "policy": "preset_adaptive", "carry": carry or [], "basis": [],
            "longs": [], "alloc_note": None, "regime_at_init": "BEAR",
            "ledger": {"version": 2, "last_mark": now.isoformat(),
                       "carry_accrued": 0.0, "basis_accrued": 0.0,
                       "borrow_accrued": 0.0, "longs_realized": 0.0,
                       "fees": 0.0},
            "notify": {"last_pnl": 0.0},
            "next_rebalance_ms": now_ms - 1000,      # rebalance DÛ
            "created_at": created.isoformat(), "history": []}


def _carry(notional, now, funding_paid_until=None, opened_ms=None):
    c = {"symbol": "BTCUSDT", "notional": notional, "spot_entry": 64000.0,
         "funding_rate": 5e-5,
         "funding_paid_until": funding_paid_until or int(now.timestamp() * 1000)}
    if opened_ms is not None:
        c["opened_ms"] = opened_ms
    return c


def _patch(monkeypatch, y_ann):
    """Fixe le yield carry annualisé (funding = y/(3·365)) ; pas de basis."""
    monkeypatch.setattr(pp_mod, "live_prices", lambda syms: dict(PX))
    monkeypatch.setattr(pp_mod, "eur_usdt", lambda: FX)
    monkeypatch.setattr(pp_mod, "live_funding", lambda s: y_ann / (3 * 365))
    monkeypatch.setattr(pp_mod, "funding_events", lambda s, a, b: [])
    monkeypatch.setattr(pp_mod, "next_quarterly", lambda s: (None, None, None))
    monkeypatch.setattr(pp_mod, "btc_regime", lambda: "BEAR")
    monkeypatch.setattr(PaperPortfolio, "_inverse_vol_weights",
                        lambda self, syms: {s: 1.0 / len(syms) for s in syms})
    monkeypatch.setattr(PaperPortfolio, "_ma", lambda self, s, n: None)


def _pp(doc):
    db = SimpleNamespace(paper_portfolio=FakeCol(doc),
                         portfolio_events=FakeEvents())
    return PaperPortfolio(db)


def test_net_gate_keeps_dormant_sleeve_dormant(monkeypatch):
    # 1,5 %/an brut : > plancher 1 % (l'ancienne règle OUVRE) mais net
    # ≈ +0,5 % ≤ marge 1 % → la règle NET laisse dormant, zéro frais réels
    _patch(monkeypatch, y_ann=0.015)
    pp = _pp(_doc(datetime.now(timezone.utc)))
    pp.mark_to_market()
    doc = pp.col.doc
    assert doc["carry"] == []                        # rien d'ouvert
    assert doc["ledger"]["fees"] == 0.0              # aucun frais réel
    g = doc["churn_guard"]
    legacy_open = (0.40 + 0.35) * EQ0 * MAKER_FEE * 2   # carry BTC+ETH legacy
    assert abs(g["fees_legacy_usdt"] - legacy_open) < 1e-6
    assert g["fees_new_usdt"] == 0.0


def test_hysteresis_keeps_held_sleeve_under_floor(monkeypatch):
    # 0,5 %/an brut : sous le plancher 1 % (l'ancienne règle COUPE) mais > 0
    # → la règle NET garde le sleeve tenu, sans frais ni resize
    _patch(monkeypatch, y_ann=0.005)
    now = datetime.now(timezone.utc)
    held = _carry(0.40 * EQ0, now, opened_ms=int(now.timestamp() * 1000))
    pp = _pp(_doc(now, carry=[held]))
    pp.mark_to_market()
    doc = pp.col.doc
    assert len(doc["carry"]) == 1
    assert abs(doc["carry"][0]["notional"] - 0.40 * EQ0) < 1e-6
    assert doc["ledger"]["fees"] == 0.0
    g = doc["churn_guard"]
    legacy_cut = 0.40 * EQ0 * MAKER_FEE * 2          # le shadow legacy coupe
    assert abs(g["fees_legacy_usdt"] - legacy_cut) < 1e-6
    assert g["fees_new_usdt"] == 0.0


def test_min_hold_blocks_cut_within_72h(monkeypatch):
    # -1,1 %/an : hystérésis → cible 0, mais sleeve ouvert il y a 10 h
    # (doc SANS opened_ms → backdaté sur created_at) → coupe DIFFÉRÉE
    _patch(monkeypatch, y_ann=-0.011)
    now = datetime.now(timezone.utc)
    pp = _pp(_doc(now, carry=[_carry(0.40 * EQ0, now)], created_hours_ago=10))
    res = pp.mark_to_market()
    doc = pp.col.doc
    assert len(doc["carry"]) == 1                    # conservé malgré cible 0
    assert doc["ledger"]["fees"] == 0.0              # coupe différée = 0 frais
    g = doc["churn_guard"]
    assert g["blocks"] == 1
    assert g["events"][0]["sleeve"] == "carry_BTCUSDT"
    assert g["events"][0]["target_usdt"] == 0.0
    assert res["churn_guard"]["blocks"] == 1         # exposé dans le mark
    assert "edge_vs_legacy_eur" in res["churn_guard"]


def test_min_hold_expired_allows_cut(monkeypatch):
    _patch(monkeypatch, y_ann=-0.011)
    now = datetime.now(timezone.utc)
    opened = int((now - timedelta(seconds=MIN_HOLD_S + 3600)).timestamp() * 1000)
    pp = _pp(_doc(now, carry=[_carry(0.40 * EQ0, now, opened_ms=opened)]))
    pp.mark_to_market()
    doc = pp.col.doc
    assert doc["carry"] == []                        # coupe passée
    assert abs(doc["ledger"]["fees"] + 0.40 * EQ0 * MAKER_FEE * 2) < 1e-6
    assert doc["churn_guard"]["blocks"] == 0


def test_hard_exit_overrides_min_hold(monkeypatch):
    # -12 %/an < -borrow (-8 %) : sleeve toxique → coupe malgré les 10 h d'âge
    _patch(monkeypatch, y_ann=-0.12)
    now = datetime.now(timezone.utc)
    opened = int((now - timedelta(hours=10)).timestamp() * 1000)
    pp = _pp(_doc(now, carry=[_carry(0.40 * EQ0, now, opened_ms=opened)]))
    pp.mark_to_market()
    doc = pp.col.doc
    assert doc["carry"] == []
    assert abs(doc["ledger"]["fees"] + 0.40 * EQ0 * MAKER_FEE * 2) < 1e-6
    assert doc["churn_guard"]["blocks"] == 0


def test_guard_accrue_rev_and_borrow_diff(monkeypatch):
    # Divergence installée : le réel tient 0,40E que le shadow legacy a coupé
    # → sur 8 h, rev_diff = notional·y·dt et borrow_diff = 0 (sous 1×E)
    _patch(monkeypatch, y_ann=0.005)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    doc = _doc(now, carry=[_carry(0.40 * EQ0, now, opened_ms=now_ms)])
    dt_s = 8 * 3600
    doc["churn_guard"] = {
        "since": now.isoformat(), "rule": "net_v1_2026-07-19",
        "fees_new_usdt": 0.0, "fees_legacy_usdt": 0.0,
        "rev_diff_usdt": 0.0, "borrow_diff_usdt": 0.0,
        "shadow": {}, "shadow_ms": now_ms - dt_s * 1000,
        "blocks": 0, "events": []}
    pp = _pp(doc)
    g = pp._guard_accrue(doc, {("carry", "BTCUSDT"): 0.005}, EQ0,
                         200_000.0 * FX, "BEAR", now_ms)
    expected_rev = 0.40 * EQ0 * 0.005 * dt_s / (365 * 86400)
    assert abs(g["rev_diff_usdt"] - expected_rev) < 1e-9
    assert g["borrow_diff_usdt"] == 0.0              # gross < 1×E des 2 côtés
    assert g["fees_legacy_usdt"] == 0.0              # legacy déjà à 0, cible 0

    # même divergence mais gross au-delà de 1×E : le réel paie du borrow en
    # plus du shadow → borrow_diff > 0 (il PÉNALISE l'edge de la règle NET)
    doc["longs"] = [{"symbol": "ETHUSDT", "notional": 0.80 * EQ0,
                     "entry": 1840.0, "ma20": None, "ma20_ts": now_ms,
                     "active": True, "realized": 0.0}]
    g["shadow_ms"] = now_ms - dt_s * 1000
    g = pp._guard_accrue(doc, {("carry", "BTCUSDT"): 0.005}, EQ0,
                         200_000.0 * FX, "BEAR", now_ms)
    expected_borrow = 0.20 * EQ0 * BORROW_ANN * dt_s / (365 * 86400)
    assert abs(g["borrow_diff_usdt"] - expected_borrow) < 1e-9


def test_reversal_veto_blocks_opposite_resize_within_72h(monkeypatch):
    # sleeve ÂGÉ (min-hold expiré) mais AUGMENTÉ il y a 8 h : la coupe (cible 0
    # via hystérésis, y < 0) est DIFFÉRÉE par le veto de réversion, sans frais
    _patch(monkeypatch, y_ann=-0.011)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    c = _carry(0.40 * EQ0, now,
               opened_ms=now_ms - (MIN_HOLD_S + 7200) * 1000)
    c["last_resize_ms"] = now_ms - 8 * 3600 * 1000
    c["last_resize_dir"] = 1
    pp = _pp(_doc(now, carry=[c]))
    pp.mark_to_market()
    doc = pp.col.doc
    assert len(doc["carry"]) == 1                    # conservé malgré cible 0
    assert doc["ledger"]["fees"] == 0.0
    g = doc["churn_guard"]
    assert g["blocks"] == 1
    assert g["events"][0]["reason"] == "réversion"
    assert g["rule"] == "net_v1.1_2026-07-19"


def test_reversal_expired_allows_resize(monkeypatch):
    # même config mais dernier resize > 72 h → la coupe passe et facture
    _patch(monkeypatch, y_ann=-0.011)
    now = datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    c = _carry(0.40 * EQ0, now,
               opened_ms=now_ms - (MIN_HOLD_S + 7200) * 1000)
    c["last_resize_ms"] = now_ms - (MIN_HOLD_S + 3600) * 1000
    c["last_resize_dir"] = 1
    pp = _pp(_doc(now, carry=[c]))
    pp.mark_to_market()
    doc = pp.col.doc
    assert doc["carry"] == []
    assert abs(doc["ledger"]["fees"] + 0.40 * EQ0 * MAKER_FEE * 2) < 1e-6
    assert doc["churn_guard"]["blocks"] == 0


def test_reversal_same_direction_or_toxic_not_vetoed():
    # unitaires : même direction jamais vetoée ; yield toxique fait sauter le veto
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    pp = _pp(_doc(datetime.now(timezone.utc)))
    pos = {"last_resize_ms": now_ms - 1000, "last_resize_dir": -1}
    y_ok = {("carry", "BTCUSDT"): 0.02}
    assert not pp._reversal_veto("carry", "BTCUSDT", pos, y_ok, now_ms, -1)
    pos["last_resize_dir"] = 1
    assert pp._reversal_veto("carry", "BTCUSDT", pos, y_ok, now_ms, -1)
    y_tox = {("carry", "BTCUSDT"): -0.12}
    assert not pp._reversal_veto("carry", "BTCUSDT", pos, y_tox, now_ms, -1)
    assert not pp._reversal_veto("carry", "BTCUSDT", {}, y_ok, now_ms, -1)


def test_resize_stamps_direction(monkeypatch):
    # ouverture initiale (net > marge) : le resize exécuté tamponne la direction
    _patch(monkeypatch, y_ann=0.06)
    now = datetime.now(timezone.utc)
    pp = _pp(_doc(now))
    pp.mark_to_market()
    doc = pp.col.doc
    assert doc["carry"]
    for c in doc["carry"]:
        if c["notional"] > 0:
            assert c["last_resize_dir"] == 1
            assert c["last_resize_ms"] > 0
