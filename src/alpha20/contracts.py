"""
src/alpha20/contracts.py — types partagés de la couche ALPHA_20.

Tout échange entre modules passe par ces contrats ; aucun module ne lit les
structures internes d'un autre. Python 3.8 (venv qbee).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

# Genres d'événements du ledger append-only (étape 1) — un fait économique
# élémentaire chacun, jamais un agrégat.
EVENT_KINDS = (
    "decision",        # intention datée (sleeve, cible, prix de décision)
    "order",           # ordre émis
    "fill",            # exécution (partielle ou totale)
    "funding",         # funding encaissé/payé RÉEL
    "fee",             # commission réelle
    "borrow",          # intérêt d'emprunt couru
    "transfer",        # mouvement de collatéral inter-venues
    "gas",             # frais on-chain
    "infra",           # coût d'infrastructure amorti
    "tax_provision",   # provision fiscale du mois
    "mark",            # marquage NAV
    "reconciliation",  # résultat d'un audit (résidu, verdict)
)


@dataclass
class LedgerEvent:
    ts: str                     # ISO-8601 UTC
    kind: str                   # ∈ EVENT_KINDS
    sleeve: str                 # ex. carry_BTCUSDT, mh_events, portfolio
    venue: str                  # binance_usdm, hyperliquid, offchain…
    amount_usdt: float          # signe : + entre dans la NAV, − en sort
    ref: str = ""               # id externe (ordre, tx, commit, décision)
    meta: Dict = field(default_factory=dict)

    def validate(self) -> "LedgerEvent":
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"kind inconnu: {self.kind}")
        float(self.amount_usdt)
        return self


@dataclass
class CostSnapshot:
    """Coûts RÉELS datés d'un (venue, instrument) — source obligatoire."""
    venue: str
    instrument: str
    maker_bp: float
    taker_bp: float
    as_of: str                  # ISO date
    source: str                 # "api_signed" | "assumed" | "invoice"
    borrow_ann: Optional[float] = None
    slippage_bp: Optional[float] = None
    meta: Dict = field(default_factory=dict)


@dataclass
class RiskProfile:
    name: str
    dd_reduce: float
    dd_cash: float
    dd_kill: float
    daily_loss: float
    weekly_loss: float
    es99_1d: float
    net_delta_cap: float
    margin_used_cap: float
    venue_unsecured_cap: float
    naked_leg_max_s: int


@dataclass
class GovernorDecision:
    state: str                  # risk_on | risk_reduced | cash | kill
    scale: float                # multiplicateur de gross autorisé
    reasons: Dict               # {limite: valeur observée} des limites touchées


@dataclass
class SleeveStats:
    """Distribution empirique NETTE d'un sleeve (étape 5) — jamais la moyenne
    du backtest seule."""
    name: str
    net_returns_daily: object   # pd.Series
    capacity_eur: float
    venue: str
    rotation_cost_bp: float     # coût marginal d'un aller-retour complet


@dataclass
class GateResult:
    gate: str
    passed: bool
    value: Optional[float] = None
    threshold: Optional[float] = None
    note: str = ""
