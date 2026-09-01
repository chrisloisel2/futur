"""
src/institutional/live_alpha_lab/execution_adapter.py
─────────────────────────────────────────────────────────────────────────────
ShadowExecutionAdapter expose la MÊME interface qu'un futur RealExecutionAdapter
(item 19 de la mission) -- submit/cancel/replace/positions/fills/balances/
reconcile -- pour que le code appelant (portfolio.py::step) puisse un jour
être pointé sur un adapter réel SANS changer sa propre logique.

Volontairement MINIMAL (instruction utilisateur : "ne pas implémenter du réel
si cela ralentit le forward") : ShadowExecutionAdapter délègue tout le calcul
réel à `portfolio.shadow_execute()` (déjà utilisé par step()) -- cette classe
n'est qu'une façade d'interface, pas une réécriture. `cancel`/`replace`
n'ont pas de sens pour un fill shadow instantané (pas d'ordre en carnet à
annuler) -- lèvent NotImplementedError explicitement plutôt que de faire
semblant.

AUCUN ordre réel envoyé nulle part dans ce fichier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.institutional.live_alpha_lab.marks import get_mark
from src.institutional.live_alpha_lab.portfolio import Fill, shadow_execute


@dataclass
class ExecutionAdapter:
    """Interface commune Shadow/Real -- méthodes que les DEUX doivent
    implémenter. Ne pas instancier directement."""

    def submit(self, instrument: str, delta_quantity: float):
        raise NotImplementedError

    def cancel(self, order_id: str):
        raise NotImplementedError

    def replace(self, order_id: str, **kwargs):
        raise NotImplementedError

    def positions(self) -> Dict[str, float]:
        raise NotImplementedError

    def fills(self) -> List[Fill]:
        raise NotImplementedError

    def balances(self) -> Dict[str, float]:
        raise NotImplementedError

    def reconcile(self, expected_positions: Dict[str, float]) -> bool:
        raise NotImplementedError


@dataclass
class ShadowExecutionAdapter(ExecutionAdapter):
    """AUCUN ordre réel. `submit` calcule un Fill immédiat via
    `shadow_execute()` (le même modèle de coût que portfolio.py::step) et
    l'enregistre localement -- pas de carnet d'ordres réel à annuler/remplacer."""
    _fills: List[Fill] = field(default_factory=list)
    _positions: Dict[str, float] = field(default_factory=dict)   # quantité, PAS notional

    def submit(self, instrument: str, delta_quantity: float, as_of=None) -> Optional[Fill]:
        mark = get_mark(instrument, as_of)
        if mark is None:
            return None   # jamais de fill sans prix réel -- voir marks.py
        fill = shadow_execute(delta_quantity, instrument, mark)
        self._fills.append(fill)
        self._positions[instrument] = self._positions.get(instrument, 0.0) + delta_quantity
        return fill

    def cancel(self, order_id: str):
        raise NotImplementedError(
            "ShadowExecutionAdapter remplit instantanément -- pas d'ordre en carnet à annuler. "
            "RealExecutionAdapter (jamais implémenté ici, aucun ordre réel) devra fournir ceci."
        )

    def replace(self, order_id: str, **kwargs):
        raise NotImplementedError(
            "Idem cancel() -- pas de concept d'ordre en attente en mode shadow instantané."
        )

    def positions(self) -> Dict[str, float]:
        return dict(self._positions)

    def fills(self) -> List[Fill]:
        return list(self._fills)

    def balances(self) -> Dict[str, float]:
        raise NotImplementedError(
            "balances() reflète un compte réel (cash/marge exchange) -- le portfolio shadow "
            "suit son propre cash simulé dans PortfolioState, pas un concept 'balances' d'exchange. "
            "Voir portfolio.PortfolioState.cash pour l'équivalent shadow."
        )

    def reconcile(self, expected_positions: Dict[str, float]) -> bool:
        """Un adapter shadow EST son propre livre de vérité (pas d'exchange
        externe à réconcilier contre) -- vérifie juste l'auto-cohérence."""
        return self._positions == expected_positions
