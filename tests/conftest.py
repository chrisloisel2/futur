"""
tests/conftest.py — garde-fous globaux de la suite.

Le ledger alpha20 est APPEND-ONLY et branché à la source dans paper_portfolio
(_alpha20_emit) : sans redirection, tout test qui déclenche un mark/rebalance
écrirait des événements de TEST dans le ledger de production (observé
2026-07-19 : gate forward « évalué » sur des données de test). Chaque test
écrit donc dans son tmp_path.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


@pytest.fixture(autouse=True)
def _isolate_alpha20_ledger(tmp_path, monkeypatch):
    from src.alpha20.accounting import event_ledger
    monkeypatch.setattr(event_ledger, "LEDGER_DIR", tmp_path / "_alpha20_ledger")
    yield
