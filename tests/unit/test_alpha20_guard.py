"""
tests/test_alpha20_guard.py
─────────────────────────────────────────────────────────────────────────────
Garde structurelle : AUCUN ordre réel ne peut partir (mission ALPHA_20,
§ contraintes). Trois vecteurs testés indépendamment + le cas nominal (aucun
signal → démarrage autorisé). Aucun réseau, aucun ledger touché.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.alpha20.guard import RealTradingGuardError, assert_paper_only


def test_nominal_state_passes(monkeypatch):
    monkeypatch.delenv("ENABLE_REAL_TRADING", raising=False)
    assert_paper_only(exit_on_fail=False)      # ne lève rien


def test_env_var_trips_the_guard(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_TRADING", "true")
    with pytest.raises(RealTradingGuardError):
        assert_paper_only(exit_on_fail=False)


def test_env_var_case_and_variants(monkeypatch):
    for val in ("1", "True", "YES", "on"):
        monkeypatch.setenv("LIVE_BROKER_ENABLE", val)
        with pytest.raises(RealTradingGuardError):
            assert_paper_only(exit_on_fail=False)
        monkeypatch.delenv("LIVE_BROKER_ENABLE")


def test_env_var_false_does_not_trip(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_TRADING", "false")
    assert_paper_only(exit_on_fail=False)


def test_config_flag_trips_the_guard(monkeypatch):
    import src.alpha20.guard as guard
    monkeypatch.setattr(guard, "load_config",
                        lambda: {"live": {"enabled": True}})
    with pytest.raises(RealTradingGuardError):
        assert_paper_only(exit_on_fail=False)


def test_unknown_execution_module_trips_the_guard(tmp_path, monkeypatch):
    import src.alpha20.guard as guard
    fake_dir = tmp_path / "execution"
    fake_dir.mkdir()
    (fake_dir / "real_binance_adapter.py").write_text("# ordre réel\n")
    monkeypatch.setattr(guard, "EXEC_DIR", fake_dir)
    with pytest.raises(RealTradingGuardError) as exc:
        assert_paper_only(exit_on_fail=False)
    assert "real_binance_adapter.py" in str(exc.value)


def test_exit_on_fail_raises_systemexit(monkeypatch):
    monkeypatch.setenv("ENABLE_REAL_TRADING", "1")
    with pytest.raises(SystemExit) as exc:
        assert_paper_only(exit_on_fail=True)
    assert exc.value.code == 2


def test_broker_never_touches_network(monkeypatch):
    """Le broker paper est de l'arithmétique pure — bloquer socket.socket ne
    doit rien casser (preuve positive qu'aucun ordre ne peut partir)."""
    import socket
    def _boom(*a, **kw):
        raise AssertionError("le broker paper ne doit JAMAIS ouvrir de socket")
    monkeypatch.setattr(socket, "socket", _boom)

    from src.alpha20.execution.paper_broker import Order, PaperBroker
    from src.alpha20.tournament.market_bus import MarketSnapshot
    snap = MarketSnapshot(market_event_id="x", cutoff="2026-07-20T00:00:00Z",
                          decision_ts="2026-07-20T00:00:00Z",
                          received_ts="2026-07-20T00:00:00Z",
                          prices={"BTCUSDT": {"close": 64000.0,
                                              "exchange_ts": "2026-07-20T00:00:00Z"}})
    fills = PaperBroker().execute(
        Order("test_runner", "BTCUSDT", "binance_usdm", 1, 1000.0), snap)
    assert not fills["observed"].rejected
