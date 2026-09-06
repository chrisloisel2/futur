"""
tests/conftest.py — garde-fous globaux de la suite.

⚠ COLLECTE (item E4, 2026-09-06). Le venv tourne sous Python 3.8.10, mais tout
un sous-arbre (`src/futur/truth/`, le Truth Engine, plus quelques modules
alpha20/foundry) utilise la syntaxe d'union PEP 604 (`X | Y`) au niveau
module, qui exige Python 3.10. Ces modules lèvent `TypeError` À L'IMPORT.

Conséquence, et elle était bien pire que « 14 tests au rouge » : pytest
s'interrompait sur « 22 errors during collection » et n'exécutait AUCUN test.
La suite entière était donc inerte, ce qui explique que personne ne voyait
d'échecs — il n'y avait pas d'exécution du tout.

`collect_ignore_glob` les écarte explicitement, AVEC motif, tant que
l'interpréteur est trop ancien. Ce n'est pas masquer un problème : c'est le
transformer en fait déclaré (« N modules non collectés, interpréteur 3.8 »)
au lieu d'une panne globale silencieuse. Sur un interpréteur 3.10+, rien
n'est ignoré et ces tests tournent normalement.

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

# Modules dont les dépendances utilisent la syntaxe `X | Y` (PEP 604, 3.10+).
# Liste EXPLICITE plutôt qu'un try/except à la collecte : une exclusion doit se
# lire dans le dépôt, pas se produire au hasard des imports qui échouent.
_NEEDS_PY310 = [
    "truth/*.py",
    "integration/test_alpha20_carry_truth_shadow_*.py",
    "unit/test_alpha20_deployment_guard.py",
    "unit/test_alpha_foundry_v5_research.py",
    "unit/test_momentum_engine.py",
    "unit/test_multileg_engine.py",
]

collect_ignore_glob = [] if sys.version_info >= (3, 10) else list(_NEEDS_PY310)

if collect_ignore_glob:
    print(f"\n[conftest] Python {sys.version_info.major}.{sys.version_info.minor} : "
          f"{len(collect_ignore_glob)} motif(s) de test non collectés (syntaxe PEP 604 "
          f"requérant 3.10+) — voir tests/conftest.py. Ils NE SONT PAS en échec, "
          f"ils ne sont pas exécutés.", file=sys.stderr)


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
