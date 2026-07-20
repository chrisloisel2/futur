"""
tests/test_alpha20_tournament_ledger.py
─────────────────────────────────────────────────────────────────────────────
Ledgers ISOLÉS par runner (event_ledger.runner_ledger_dir) : double décision/
fill idempotent, crash/restart (chaîne continue sans état mémoire), écriture
interrompue réparée, corruption détectée → INELIGIBLE via reconciliation.
Aucun ledger réel touché (fixture autouse de conftest).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from src.alpha20.accounting import event_ledger
from src.alpha20.contracts import LedgerEvent
from src.alpha20.tournament import reconciliation
from src.alpha20.tournament.paper_account import PaperAccount


def _ev(ts, kind, amount, ref="t", sleeve="s"):
    return LedgerEvent(ts=ts, kind=kind, sleeve=sleeve, venue="binance_usdm",
                       amount_usdt=amount, ref=ref)


def test_runner_ledgers_are_isolated_from_each_other_and_from_portfolio():
    a = PaperAccount("carry_solusdt", 200000.0)
    b = PaperAccount("carry_bnbusdt", 200000.0)
    a.emit([_ev("2026-07-20T00:00:00Z", "fee", -10.0)])
    b.emit([_ev("2026-07-20T00:00:00Z", "fee", -20.0)])
    assert a.ledger_dir != b.ledger_dir
    assert len(a.read()) == 1 and a.read()["amount_usdt"].iloc[0] == -10.0
    assert len(b.read()) == 1 and b.read()["amount_usdt"].iloc[0] == -20.0
    portfolio_df = event_ledger.read()             # ledger V1.1, jamais touché
    assert portfolio_df.empty


def test_double_decision_same_fact_is_idempotent():
    acc = PaperAccount("basis_term_v0", 200000.0)
    ev = _ev("2026-07-20T00:00:00Z", "decision", 0.0, ref="tournament_cycle")
    acc.emit([ev])
    acc.emit([ev])                                  # rejoué à l'identique
    assert len(acc.read(kinds=["decision"])) == 1


def test_double_fill_same_fact_is_idempotent():
    acc = PaperAccount("mh_events_exec", 200000.0)
    ev = _ev("2026-07-20T01:00:00Z", "fill", 42.0, ref="mh_replay")
    ids1 = acc.emit([ev])
    ids2 = acc.emit([ev])
    assert ids1 and not ids2                        # 2e append = no-op


def test_duplicate_divergent_fact_detected_by_integrity():
    acc = PaperAccount("carry_solusdt", 200000.0)
    acc.emit([_ev("2026-07-20T00:00:00Z", "fee", -10.0, ref="entry")])
    acc.emit([_ev("2026-07-20T00:00:00Z", "fee", -11.0, ref="entry")])  # même fait, montant différent
    integ = acc.integrity()
    assert not integ["one_event_per_fact"]
    assert integ["duplicate_facts"]


def test_crash_restart_continuity_no_memory_state():
    """Un "restart" = un nouvel objet PaperAccount, sans état mémoire — la
    chaîne continue exactement là où elle s'était arrêtée."""
    a1 = PaperAccount("carry_bnbusdt", 200000.0)
    a1.emit([_ev("2026-07-20T00:00:00Z", "fee", -5.0, ref="r1")])
    a2 = PaperAccount("carry_bnbusdt", 200000.0)     # "restart"
    a2.emit([_ev("2026-07-20T01:00:00Z", "fee", -6.0, ref="r2")])
    assert len(a2.read()) == 2
    assert event_ledger.verify_chain(a2.ledger_dir)


def test_interrupted_write_on_runner_ledger_is_repaired():
    acc = PaperAccount("basis_term_v0", 200000.0)
    acc.emit([_ev("2026-07-20T00:00:00Z", "fee", -1.0)])
    f = acc.ledger_dir / "ledger.jsonl"
    with open(f, "a") as fh:
        fh.write('{"ts": "2026-07-20T01:00:00Z", "kind": "fee", "amou')
    assert event_ledger.verify_chain(acc.ledger_dir)
    acc.emit([_ev("2026-07-20T02:00:00Z", "fee", -2.0)])
    assert len(acc.read()) == 2
    assert event_ledger.verify_chain(acc.ledger_dir)


def test_corrupted_ledger_makes_runner_ineligible():
    acc = PaperAccount("mh_events_exec", 200000.0)
    acc.emit([_ev("2026-07-20T00:00:00Z", "fee", -1.0),
             _ev("2026-07-20T01:00:00Z", "fee", -2.0)])
    f = acc.ledger_dir / "ledger.jsonl"
    lines = f.read_text().splitlines()
    lines[0] = lines[0][:30]                          # corruption INTERNE
    f.write_text("\n".join(lines) + "\n")
    gate = reconciliation.runner_gate("mh_events_exec", 200000.0)
    assert gate["status"] == "invalid_ledger"
    assert gate["passed"] is False and gate["eligible"] is False


def test_reconciliation_gate_passes_on_consistent_marks():
    acc = PaperAccount("carry_solusdt", 200000.0)
    acc.emit([_ev("2026-07-20T00:00:00Z", "funding", 5.0)])
    acc.mark(200005.0, {"nav_usdt": 200005.0})
    acc.emit([_ev("2026-07-20T08:00:00Z", "funding", 3.0)])
    acc.mark(200008.0, {"nav_usdt": 200008.0})
    gate = reconciliation.runner_gate("carry_solusdt", 200000.0)
    assert gate["status"] == "evaluated"
    assert gate["passed"] and gate["eligible"]
    assert gate["consecutive_ok"] >= 1


def test_no_ledger_removal_or_truncation_ever_on_valid_content():
    """Garde-fou anti-régression : append() ne tronque QUE la queue non
    commise, jamais du contenu déjà validé par un hash de chaîne."""
    acc = PaperAccount("carry_bnbusdt", 200000.0)
    acc.emit([_ev("2026-07-20T00:00:00Z", "fee", -1.0)])
    before = (acc.ledger_dir / "ledger.jsonl").read_text()
    acc.emit([_ev("2026-07-20T01:00:00Z", "fee", -2.0)])
    after = (acc.ledger_dir / "ledger.jsonl").read_text()
    assert after.startswith(before)
