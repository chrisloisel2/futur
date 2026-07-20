"""
tests/conftest.py — garde-fous globaux de la suite.

Le ledger alpha20 est APPEND-ONLY et branché à la source dans paper_portfolio
(_alpha20_emit) et dans le tournoi (chaque adaptateur/compte) : sans
redirection, tout test qui déclenche un mark/rebalance/cycle écrirait des
événements de TEST dans les données réelles (observé 2026-07-19 : gate
forward « évalué » sur des données de test). CHAQUE répertoire d'état
persistant introduit par ALPHA_20 (ledger portefeuille, ledgers par runner du
tournoi, bus de marché, état opérationnel, logs de cycle, rapports) est donc
redirigé vers tmp_path pour CHAQUE test, automatiquement (autouse).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


@pytest.fixture(autouse=True)
def _isolate_alpha20_state(tmp_path, monkeypatch):
    from src.alpha20.accounting import event_ledger
    from src.alpha20.tournament import market_bus

    monkeypatch.setattr(event_ledger, "LEDGER_DIR", tmp_path / "_alpha20_ledger")
    monkeypatch.setattr(event_ledger, "TOURNAMENT_LEDGER_ROOT",
                        tmp_path / "_alpha20_tournament_ledger")
    monkeypatch.setattr(market_bus, "BUS_DIR", tmp_path / "_alpha20_bus")

    try:
        from src.alpha20.tournament import orchestrator
        monkeypatch.setattr(orchestrator, "STATE_DIR", tmp_path / "_alpha20_state")
        monkeypatch.setattr(orchestrator, "CYCLE_LOG_DIR", tmp_path / "_alpha20_cyclelog")
    except ImportError:
        pass
    try:
        from src.alpha20.tournament import dashboard
        monkeypatch.setattr(dashboard, "OUT_DIR", tmp_path / "_alpha20_dashboard")
    except ImportError:
        pass
    try:
        from src.alpha20.tournament import portfolio_search
        monkeypatch.setattr(portfolio_search, "OUT",
                            tmp_path / "_alpha20_portfolio_search.json")
    except ImportError:
        pass
    yield
