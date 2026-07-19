"""
tests/test_alpha20_ledger.py
─────────────────────────────────────────────────────────────────────────────
Ledger append-only alpha20 : idempotence, chaîne de hash (altération détectée),
décomposition R_net, provision fiscale. Aucun réseau, tout en tmp_path.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.alpha20.accounting import event_ledger, net_nav, tax_engine
from src.alpha20.contracts import LedgerEvent


def _ev(ts, kind, amount, sleeve="carry_BTCUSDT", ref="t"):
    return LedgerEvent(ts=ts, kind=kind, sleeve=sleeve,
                       venue="binance_usdm", amount_usdt=amount, ref=ref)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(event_ledger, "LEDGER_DIR", tmp_path / "ledger")
    return event_ledger


def test_append_idempotent_and_chain(ledger):
    evs = [_ev("2026-07-19T08:00:00Z", "funding", 2.81),
           _ev("2026-07-19T08:00:01Z", "fee", -36.62)]
    assert len(ledger.append(evs)) == 2
    assert len(ledger.append(evs)) == 0          # rejouer = no-op
    assert ledger.verify_chain()
    df = ledger.read(kinds=["fee"])
    assert len(df) == 1 and df["amount_usdt"].iloc[0] == -36.62


def test_tamper_breaks_chain(ledger, tmp_path):
    ledger.append([_ev("2026-07-19T08:00:00Z", "funding", 2.81)])
    f = sorted((tmp_path / "ledger").glob("*.jsonl"))[0]
    row = json.loads(f.read_text())
    row["amount_usdt"] = 999.0                   # falsification
    f.write_text(json.dumps(row) + "\n")
    assert not ledger.verify_chain()


def test_out_of_order_ts_keeps_chain(ledger):
    """Régression 2026-07-19 : un fait passé audité (ts antérieur) appendu
    après des événements récents ne doit PAS casser la chaîne."""
    ledger.append([_ev("2026-07-19T10:00:00Z", "fee", -1.0)])
    ledger.append([_ev("2026-07-17T08:00:00Z", "funding", 2.0)])   # ts passé
    ledger.append([_ev("2026-07-19T11:00:00Z", "fee", -3.0)])
    assert ledger.verify_chain()
    assert len(ledger.read()) == 3


def test_bad_kind_rejected(ledger):
    with pytest.raises(ValueError):
        ledger.append([_ev("2026-07-19T08:00:00Z", "bonus", 1.0)])


def test_r_net_decomposition(ledger):
    ledger.append([
        _ev("2026-07-19T08:00:00Z", "funding", 100.0),
        _ev("2026-07-19T09:00:00Z", "fill", 50.0),
        _ev("2026-07-19T10:00:00Z", "fee", -30.0),
        _ev("2026-07-19T11:00:00Z", "borrow", -10.0),
        _ev("2026-07-19T12:00:00Z", "tax_provision", -33.0),
    ])
    r = net_nav.r_net(nav_start_usdt=10000.0, since="2026-07-19")
    assert abs(r["r_net"] - (100 + 50 - 30 - 10 - 33) / 10000.0) < 1e-12
    assert abs(r["r_gross"] - 150 / 10000.0) < 1e-12
    assert abs(r["cost_drag"] - 40 / 10000.0) < 1e-12
    assert r["chain_ok"]
    assert abs(net_nav.nav(10000.0) - 10077.0) < 1e-9


def test_tax_provision_math():
    p = tax_engine.provision_for_month(1000.0, "2026-07")
    assert p["provision_usdt"] == 300.0          # scénario PFU 30 %
    assert tax_engine.provision_for_month(-500.0, "2026-07")["provision_usdt"] == 0.0
    # 40 000 nets → 57 143 avant impôt (vérité économique du yaml)
    assert abs(tax_engine.required_pretax_monthly(40000.0) - 57142.857) < 0.01
    ev = tax_engine.provision_event(1000.0, "2026-07")
    assert ev.kind == "tax_provision" and ev.amount_usdt == -300.0
