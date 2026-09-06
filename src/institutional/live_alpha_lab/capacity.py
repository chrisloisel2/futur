"""
src/institutional/live_alpha_lab/capacity.py
─────────────────────────────────────────────────────────────────────────────
CAPACITÉ : au-delà de quel notionnel par trade le lab sort du domaine où son
modèle d'exécution est adossé à une observation.

Le constat de départ
────────────────────
`ShadowExecutionAdapter.submit_order` remplit sauf rejet explicite. Il existe
bien un plafond, `orders.liquidity_cap_quantity()`, mais il est adossé à
`open_interest × 0,002`. L'open interest est un STOCK de positions ouvertes,
pas une profondeur de carnet : il est de plusieurs ordres de grandeur
supérieur à ce qu'on peut réellement traverser. Mesuré sur le forward
(P1_CONTROL, 1 634 ordres) : ce plafond a mordu **16 fois, soit 1,0 %**.
`orders.py` le dit lui-même — « un proxy honnête, pas une simulation de
microstructure réelle ». Il l'est. Il ne répond simplement pas à cette
question-là.

Trois politiques, toutes NOMMÉES et déclarées
─────────────────────────────────────────────
  OPEN_INTEREST  ce que le simulateur applique aujourd'hui (0,2 % de l'OI).
                 Conservée pour pouvoir chiffrer l'écart, pas comme référence.
  TOP_OF_BOOK    le notionnel affiché au MEILLEUR limite. C'est exactement la
                 taille pour laquelle le spread coté est observé ; au-delà, le
                 fill au mid moins 2 bps ne repose plus sur rien.
  ADV_FRACTION   une fraction du volume échangé, prorata de l'horizon de
                 détention. Convention de marché classique, seuil déclaré.

Aucune de ces trois n'est « la vraie capacité ». TOP_OF_BOOK est une BORNE
BASSE (niveau 1 seulement : un ordre valant 3× le meilleur limite ne paie pas
forcément beaucoup plus, les niveaux suivants sont souvent proches).
ADV_FRACTION est une convention, pas une mesure d'impact. La vraie réponse
demande un carnet L2 complet, que `data/microstructure_reduced` ne capture que
pour BTC/ETH/SOL — soit 37 des 548 décisions labellisées.

Ce que ce module donne malgré ça, et qui manquait : une capacité CHIFFRÉE par
alpha, avec la politique qui la produit écrite à côté. « À 200 K$ ce n'est
probablement pas contraignant » devient une mesure au lieu d'une supposition.

Ce module NE MODIFIE PAS le plafond du simulateur. Même raison que pour le
coût (voir slippage.py) : changer la règle de fill en cours de route mélange
deux régimes d'exécution dans une même courbe d'équité. Le remplacement est
une décision séparée, qui devra déclarer sa frontière de segment.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from src.institutional.live_alpha_lab.slippage import load_probes

ROOT = Path(__file__).resolve().parents[3]

# Fraction du volume traversable sur l'horizon. 1 % d'ADV prorata est la
# convention prudente usuelle pour un participant qui ne veut pas être
# l'essentiel du flux. Déclarée ici, jamais implicite.
ADV_PARTICIPATION = 0.01

# Le plafond que le simulateur applique réellement aujourd'hui
# (orders.MAX_FILL_FRACTION_OF_LIQUIDITY) -- répliqué pour pouvoir chiffrer
# l'écart entre les politiques, pas pour l'endosser.
OI_FRACTION = 0.002

POLICIES = {
    "OPEN_INTEREST": f"{OI_FRACTION:.1%} de l'open interest — la règle actuelle du simulateur",
    "TOP_OF_BOOK": "notionnel affiché au meilleur limite — borne basse, niveau 1 seulement",
    "ADV_FRACTION": f"{ADV_PARTICIPATION:.0%} du volume 24 h, prorata de l'horizon",
}


@dataclass(frozen=True)
class Liquidity:
    symbol: str
    n_probes: int
    top_of_book_usd: Optional[float]      # médiane des sondes, moyenne bid/ask
    quote_volume_24h_usd: Optional[float]


def liquidity_by_symbol(probes: Optional[pd.DataFrame] = None) -> Dict[str, Liquidity]:
    probes = probes if probes is not None else load_probes()
    if probes is None:
        return {}
    out = {}
    for symbol, g in probes.groupby("symbol"):
        top = pd.concat([g["top_bid_notional_usd"], g["top_ask_notional_usd"]])
        vol = g["quote_volume_24h_usd"] if "quote_volume_24h_usd" in g else pd.Series(dtype=float)
        vol = vol.dropna()
        out[symbol] = Liquidity(
            symbol=symbol, n_probes=len(g),
            top_of_book_usd=float(np.median(top)) if len(top) else None,
            quote_volume_24h_usd=float(np.median(vol)) if len(vol) else None,
        )
    return out


def capacity_notional(symbol: str, policy: str, horizon_hours: float = 4.0,
                      liquidity: Optional[Dict[str, Liquidity]] = None,
                      oi_notional_usd: Optional[float] = None) -> Optional[float]:
    """Notionnel maximal par trade sous une politique. None = non mesurable
    pour ce symbole — jamais un repli silencieux sur une autre politique."""
    if policy not in POLICIES:
        raise ValueError(f"politique inconnue : {policy} (connues : {sorted(POLICIES)})")
    if policy == "OPEN_INTEREST":
        return None if oi_notional_usd is None else oi_notional_usd * OI_FRACTION
    liq = (liquidity or {}).get(symbol)
    if liq is None:
        return None
    if policy == "TOP_OF_BOOK":
        return liq.top_of_book_usd
    if liq.quote_volume_24h_usd is None:
        return None
    return liq.quote_volume_24h_usd * (horizon_hours / 24.0) * ADV_PARTICIPATION


def binding_rate(orders: pd.DataFrame, policy: str, horizon_hours: float = 4.0,
                 liquidity: Optional[Dict[str, Liquidity]] = None) -> dict:
    """Combien d'ordres RÉELS cette politique aurait plafonnés, et quel
    notionnel elle aurait refusé.

    `orders` : colonnes `symbol` et `notional_usd`. Un symbole non mesurable
    est compté à part (`n_unmeasurable`) et JAMAIS traité comme illimité —
    l'assimiler à « pas de plafond » ferait passer une lacune de données pour
    une bonne nouvelle."""
    liquidity = liquidity if liquidity is not None else liquidity_by_symbol()
    caps = orders["symbol"].map(
        lambda s: capacity_notional(s, policy, horizon_hours, liquidity))
    known = caps.notna()
    over = known & (orders["notional_usd"] > caps)
    refused = float((orders.loc[over, "notional_usd"] - caps[over]).sum())
    return {
        "policy": policy,
        "n_orders": int(len(orders)),
        "n_measurable": int(known.sum()),
        "n_unmeasurable": int((~known).sum()),
        "n_capped": int(over.sum()),
        "pct_capped": round(float(over.sum()) / max(int(known.sum()), 1) * 100, 1),
        "notional_refused_usd": round(refused, 1),
        "notional_total_usd": round(float(orders["notional_usd"].sum()), 1),
    }


def alpha_capacity(symbols: pd.Series, policy: str, horizon_hours: float = 4.0,
                   liquidity: Optional[Dict[str, Liquidity]] = None) -> dict:
    """Capacité PAR TRADE d'un alpha, lue sur les symboles qu'il touche
    réellement (pondérés par sa fréquence de décision, pas par l'univers
    déclaré : un alpha qui ne trade que des symboles minces n'hérite pas de la
    liquidité de BTC).

    `p10` est le chiffre qui compte : le notionnel au-delà duquel 90 % des
    décisions de cet alpha sortent déjà du domaine observé."""
    liquidity = liquidity if liquidity is not None else liquidity_by_symbol()
    caps = symbols.map(lambda s: capacity_notional(s, policy, horizon_hours, liquidity)).dropna()
    if caps.empty:
        return {"policy": policy, "n_decisions": int(len(symbols)), "n_measurable": 0}
    return {
        "policy": policy,
        "n_decisions": int(len(symbols)),
        "n_measurable": int(len(caps)),
        "per_trade_p10_usd": round(float(np.percentile(caps, 10)), 1),
        "per_trade_median_usd": round(float(np.median(caps)), 1),
        "per_trade_p90_usd": round(float(np.percentile(caps, 90)), 1),
    }
