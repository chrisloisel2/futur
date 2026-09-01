"""
src/institutional/live_alpha_lab/intents.py
─────────────────────────────────────────────────────────────────────────────
PortfolioIntent — schéma commun que le PORTFOLIO_SHADOW_LAYER consomme,
indépendamment du schéma propre à chaque alpha (Opportunity-style pour
SHORT_COVERING_CONTINUATION_V1, event-ledger pour la famille liq_cascade,
basket hebdo pour CROSS_SECTIONAL_MOMENTUM_LIVE_V1, etc.).

Un adaptateur par alpha_id normalise son ledger `decisions.parquet` en
List[PortfolioIntent]. Le portfolio layer ne lit JAMAIS un ledger brut
directement -- toujours via ces adaptateurs, pour ne pas dupliquer la
connaissance du schéma de chaque alpha.

Deux alphas ne produisent PAS d'intent de position (traités séparément par
le portfolio layer, pas ici) :
  - WHALE_LSR_SCREEN_V1 : un GATE (réduit/bloque les intents d'autres alphas
    sur le même instrument), pas une position propre. Voir gate.py.
  - VOL_FORECAST_LAYER_V1 : alimente le multiplicateur de sizing de l'overlay
    (item 3 de la mission), pas une position propre. Voir overlay.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import pandas as pd

REGISTRY_HORIZON_HOURS = {
    "fwd_4h": 4.0,
    "fwd_7d": 168.0,
    "fwd_24h": 24.0,
    "k30d": 720.0,
}


@dataclass(frozen=True)
class PortfolioIntent:
    alpha_id: str
    family: str
    risk_bucket: str
    correlation_family: str
    timestamp: pd.Timestamp          # event_time de la décision source
    instrument: str                  # symbole, ou "BTCUSDT_QUARTERLY_VS_PERP" pour un multi-leg
    direction: str                   # LONG | SHORT | (jamais SHORT pour un alpha long-only)
    target_position_fraction: float  # 0..1, fraction du budget alloué à cet alpha
    confidence: float                # 0..1
    horizon_hours: float
    expiry: pd.Timestamp             # timestamp + horizon_hours
    multi_leg: bool = False          # True pour FUNDING_BASIS_DISAGREEMENT_V2 (2 jambes opposées)
    leg_instrument_b: Optional[str] = None   # 2e jambe si multi_leg


def _expiry(ts: pd.Timestamp, hours: float) -> pd.Timestamp:
    return ts + pd.Timedelta(hours=hours)


def _liq_cascade_intents(alpha_id: str, family: str, risk_bucket: str,
                         correlation_family: str, df: pd.DataFrame) -> List[PortfolioIntent]:
    """LIQ_CASCADE_REPEAT_V1 / LIQ_CASCADE_FAR_FROM_LOW_V1 : conviction pleine
    (pas de gradient de sizing dans la spec gelée), horizon fwd_4h fixe."""
    out = []
    for _, r in df.iterrows():
        ts = pd.Timestamp(r["event_time"])
        out.append(PortfolioIntent(
            alpha_id=alpha_id, family=family, risk_bucket=risk_bucket,
            correlation_family=correlation_family, timestamp=ts,
            instrument=r["symbol"], direction="LONG",
            target_position_fraction=1.0, confidence=1.0,
            horizon_hours=4.0, expiry=_expiry(ts, 4.0),
        ))
    return out


def _short_covering_intents(alpha_id: str, family: str, risk_bucket: str,
                            correlation_family: str, df: pd.DataFrame) -> List[PortfolioIntent]:
    """SHORT_COVERING_CONTINUATION_V1 : Opportunity-style (decision_zone,
    p_success). A_TRADE -> pleine conviction ; B_SHADOW -> poids nominal
    réduit (shadow obligatoire, tracé mais pas dimensionné comme un vrai
    trade -- zone B sert à mesurer E[return|zone B], pas à trader)."""
    ZONE_WEIGHT = {"A_TRADE": 1.0, "B_SHADOW": 0.25}
    out = []
    for _, r in df.iterrows():
        zone = r.get("decision_zone", "B_SHADOW")
        w = ZONE_WEIGHT.get(zone, 0.0)
        if w <= 0:
            continue
        ts = pd.Timestamp(r["timestamp"])
        out.append(PortfolioIntent(
            alpha_id=alpha_id, family=family, risk_bucket=risk_bucket,
            correlation_family=correlation_family, timestamp=ts,
            instrument=r["asset"], direction=r.get("direction", "LONG"),
            target_position_fraction=w * float(r.get("p_success", 1.0)),
            confidence=float(r.get("confidence", w)),
            horizon_hours=4.0, expiry=_expiry(ts, 4.0),
        ))
    return out


def _cross_sectional_intents(alpha_id: str, family: str, risk_bucket: str,
                             correlation_family: str, df: pd.DataFrame) -> List[PortfolioIntent]:
    """CROSS_SECTIONAL_MOMENTUM_LIVE_V1/_V2 : panier top-quintile hebdo, poids
    égal entre les noms sélectionnés à un même rebalance (pas de pondération
    par force du signal dans la spec gelée).

    Réel schéma des ledgers (V1 et V2, vérifié 2026-08-31) : PAS de colonne
    `bucket_size` -- la taille du panier à un rebalance donné est le nombre
    de lignes qui partagent le même `event_time`. Calculée directement par
    comptage (robuste : ne dépend pas de reconstruire la formule de sélection
    top-quintile, juste ce qui a RÉELLEMENT été retenu ce jour-là)."""
    out = []
    basket_size = df.groupby("event_time")["symbol"].transform("count")
    for (_, r), size in zip(df.iterrows(), basket_size):
        ts = pd.Timestamp(r["event_time"])
        out.append(PortfolioIntent(
            alpha_id=alpha_id, family=family, risk_bucket=risk_bucket,
            correlation_family=correlation_family, timestamp=ts,
            instrument=r["symbol"], direction="LONG",
            target_position_fraction=1.0 / max(int(size), 1),
            confidence=1.0, horizon_hours=168.0, expiry=_expiry(ts, 168.0),
        ))
    return out


def _funding_basis_intents(alpha_id: str, family: str, risk_bucket: str,
                           correlation_family: str, df: pd.DataFrame) -> List[PortfolioIntent]:
    """FUNDING_BASIS_DISAGREEMENT_V2 : intent MULTI_LEG (perp vs quarterly),
    tracée pour l'exposition/risk_bucket mais PAS envoyée au
    ShadowExecutionAdapter (pas de simulateur de mismatch de jambe -- voir
    execution_blocked_reason dans son freeze_spec.json)."""
    out = []
    for _, r in df.iterrows():
        ts = pd.Timestamp(r["date"])
        regime = r.get("regime", "")
        direction = "LONG" if regime == "RICH" else "SHORT"  # LONG quarterly / SHORT perp si RICH
        out.append(PortfolioIntent(
            alpha_id=alpha_id, family=family, risk_bucket=risk_bucket,
            correlation_family=correlation_family, timestamp=ts,
            instrument=f"{r['symbol']}_QUARTERLY", direction=direction,
            target_position_fraction=1.0, confidence=1.0,
            horizon_hours=720.0, expiry=_expiry(ts, 720.0),
            multi_leg=True, leg_instrument_b=f"{r['symbol']}_PERP",
        ))
    return out


# alpha_id -> (family, risk_bucket, correlation_family, adapter_fn)
ADAPTERS: Dict[str, Callable] = {
    "LIQ_CASCADE_REPEAT_V1": _liq_cascade_intents,
    "LIQ_CASCADE_FAR_FROM_LOW_V1": _liq_cascade_intents,
    "SHORT_COVERING_CONTINUATION_V1": _short_covering_intents,
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V1": _cross_sectional_intents,
    "CROSS_SECTIONAL_MOMENTUM_LIVE_V2": _cross_sectional_intents,
    "FUNDING_BASIS_DISAGREEMENT_V2": _funding_basis_intents,
}

# Alphas volontairement SANS adaptateur d'intent (gate ou overlay, pas une
# position) -- listés explicitement pour que l'absence soit lisible comme un
# choix et pas un oubli.
NOT_A_POSITION_ALPHA = {"WHALE_LSR_SCREEN_V1", "VOL_FORECAST_LAYER_V1"}


def build_intents(alpha_id: str, registry_entry: dict, decisions_forward_only: pd.DataFrame
                  ) -> List[PortfolioIntent]:
    if alpha_id not in ADAPTERS:
        if alpha_id in NOT_A_POSITION_ALPHA:
            return []
        raise KeyError(
            f"Pas d'adaptateur PortfolioIntent pour {alpha_id!r} -- ajouter explicitement "
            "dans intents.ADAPTERS ou dans NOT_A_POSITION_ALPHA si c'est voulu."
        )
    if decisions_forward_only.empty:
        return []
    fn = ADAPTERS[alpha_id]
    return fn(alpha_id, registry_entry.get("family"), registry_entry.get("risk_bucket"),
             registry_entry.get("correlation_family"), decisions_forward_only)
