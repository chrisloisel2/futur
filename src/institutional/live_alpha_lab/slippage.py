"""
src/institutional/live_alpha_lab/slippage.py
─────────────────────────────────────────────────────────────────────────────
COÛT D'EXÉCUTION : remplacer une constante par une mesure, ou à défaut par une
borne déclarée -- jamais par une constante muette.

L'état de départ
────────────────
`portfolio.py` applique `FIXED_SLIPPAGE_BPS = 2.0` par jambe à tous les
symboles, dans tous les régimes, et `execution_adapter.py` le documente
lui-même : « pas de bid/ask réel dans derivatives_raw (seulement mark_price) ».
Le coût était donc la seule pièce du PnL adossée à aucune observation.

Ce que la mesure dit (2026-09-06)
─────────────────────────────────
1. RÉGIME — le spread ne s'écarte PAS pendant les cascades, sur les seuls
   symboles où la bande BBO existe. Mesuré sur data/microstructure_reduced,
   12 heures « avec cascade » contre 12 heures calmes, ~250-300 k points par
   cellule :

       BTCUSDT   cascade/calme  p50 0,99x   p99 1,17x   p99,9 1,28x
       ETHUSDT   cascade/calme  p50 0,99x   p99 0,80x   p99,9 0,80x
       SOLUSDT   cascade/calme  p50 1,00x   p99 0,99x   p99,9 1,00x

   L'hypothèse de départ -- « le coût explose précisément quand ces alphas
   tradent » -- n'est donc pas soutenue par la donnée disponible. Réserve
   explicite : une « heure de cascade » est une heure contenant un événement
   sur des ALTS, ce qui est un proxy faible du stress sur le carnet de BTC.
   Ceci borne les majors, pas les alts.

2. COUPE TRANSVERSALE — c'est là qu'est le vrai problème, et il n'est pas
   temporel mais transversal. Spread aller-retour, frozen-50, marché calme :

       BTCUSDT 0,013   ETHUSDT 0,040   BNBUSDT 0,132   médiane alts 1,71
       ... ARUSDT 6,61   ATOMUSDT 6,23   IMXUSDT 7,43

   Soit un demi-spread de 0,86 bps pour l'alt médian -- l'hypothèse de 2 bps
   est CONSERVATRICE au centre. Mais elle est déjà dépassée dans la queue, en
   marché calme : ARUSDT 3,31 bps par jambe, IMXUSDT 3,72. Or ARUSDT est le
   symbole le PLUS tradé du lab (30 des 548 décisions labellisées).

   Une constante unique est donc simultanément trop pessimiste pour la
   majorité des symboles et trop optimiste là où le lab engage le plus de
   capital. Ces deux erreurs ne se compensent pas : elles déplacent le capital
   vers les symboles dont le coût est sous-estimé.

Ce que ce module fait, et ne fait pas
─────────────────────────────────────
Il FOURNIT un coût par symbole issu de la sonde
(scripts/probe_spread_cross_section.py) et permet de re-tarifer les résultats
déjà scellés sous plusieurs scénarios déclarés.

Il NE MODIFIE PAS `FIXED_SLIPPAGE_BPS`. Changer le coût du simulateur en
cours de route créerait une discontinuité dans la courbe d'équité live et
mélangerait deux régimes de comptabilité dans une même série -- exactement ce
que `data_segment_boundaries` existe pour empêcher dans ce projet. Le chiffre
mesuré se lit ici, à côté ; le remplacement dans le simulateur est une
décision séparée, qui devra déclarer sa frontière de segment.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
PROBE_ROOT = ROOT / "data" / "spread_probe"

TAKER_FEE_BPS = 5.0          # identique à portfolio.TAKER_FEE_BPS, pas une copie divergente
SIMULATOR_SLIPPAGE_BPS = 2.0  # identique à portfolio.FIXED_SLIPPAGE_BPS

# Borne haute de l'audit : 10 bps de slippage par jambe. Conservée COMME BORNE
# et non comme estimation -- aucune mesure ne la soutient aujourd'hui, et c'est
# précisément son rôle : si une conclusion survit à 10 bps, elle ne dépend plus
# de l'hypothèse de coût.
STRESS_SLIPPAGE_BPS = 10.0

# Sous ce nombre de sondes, aucun percentile par symbole n'est publié : un p90
# calculé sur trois points est un maximum déguisé. La sonde tourne à chaque
# cycle (15 min), donc ~96/jour -- ce seuil est atteint en quelques heures.
MIN_PROBES_FOR_PERCENTILE = 20


@dataclass(frozen=True)
class SpreadStats:
    symbol: str
    n_probes: int
    half_spread_median_bps: Optional[float]
    half_spread_p90_bps: Optional[float]
    half_spread_max_bps: Optional[float]
    top_notional_median_usd: Optional[float]

    @property
    def thin(self) -> bool:
        return self.n_probes < MIN_PROBES_FOR_PERCENTILE


def load_probes(since: Optional[pd.Timestamp] = None,
                probe_root: Path = PROBE_ROOT) -> Optional[pd.DataFrame]:
    files = sorted(probe_root.glob("date=*/part-*.parquet")) if probe_root.exists() else []
    if not files:
        return None
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["probe_at"] = pd.to_datetime(df["probe_at"], utc=True)
    if since is not None:
        df = df[df["probe_at"] >= since]
    return df if not df.empty else None


def spread_stats(probes: Optional[pd.DataFrame] = None) -> Dict[str, SpreadStats]:
    """Demi-spread par symbole. Demi, parce qu'une jambe traversée au mid paie
    la moitié de l'écart -- c'est l'unité comparable à FIXED_SLIPPAGE_BPS, qui
    est bien un coût PAR JAMBE."""
    probes = probes if probes is not None else load_probes()
    if probes is None:
        return {}
    out = {}
    for symbol, g in probes.groupby("symbol"):
        half = g["spread_bps"].to_numpy(dtype=float) / 2.0
        n = len(half)
        enough = n >= MIN_PROBES_FOR_PERCENTILE
        top = pd.concat([g["top_bid_notional_usd"], g["top_ask_notional_usd"]])
        out[symbol] = SpreadStats(
            symbol=symbol, n_probes=n,
            half_spread_median_bps=round(float(np.median(half)), 4),
            half_spread_p90_bps=round(float(np.percentile(half, 90)), 4) if enough else None,
            half_spread_max_bps=round(float(half.max()), 4),
            top_notional_median_usd=round(float(top.median()), 1) if len(top) else None,
        )
    return out


# Scénarios de coût. Chacun est une HYPOTHÈSE NOMMÉE, jamais un défaut muet.
SCENARIOS = {
    "SIMULATOR": "constante du simulateur : 2,0 bps/jambe pour tous les symboles",
    "MEASURED_MEDIAN": "demi-spread médian mesuré par symbole (sonde REST bookTicker)",
    "MEASURED_P90": "demi-spread p90 mesuré par symbole — refusé sous "
                    f"{MIN_PROBES_FOR_PERCENTILE} sondes",
    "STRESS_BOUND": f"borne haute déclarée : {STRESS_SLIPPAGE_BPS:.0f} bps/jambe, "
                    "aucune mesure ne la soutient — c'est son rôle",
}


def slippage_bps(symbol: str, scenario: str,
                 stats: Optional[Dict[str, SpreadStats]] = None) -> Optional[float]:
    """Coût de slippage PAR JAMBE, en bps. None = pas mesurable sous ce
    scénario pour ce symbole (jamais un repli silencieux sur la constante :
    un coût inconnu doit se voir comme inconnu)."""
    # Le nom du scénario est validé AVANT toute recherche de mesure. Sinon un
    # scénario mal orthographié tombait sur le `return None` du symbole inconnu
    # et se lisait « coût non mesurable » au lieu de « tu t'es trompé de nom » —
    # la faute de frappe se serait déguisée en donnée manquante.
    if scenario not in SCENARIOS:
        raise ValueError(f"scénario inconnu : {scenario} (connus : {sorted(SCENARIOS)})")
    if scenario == "SIMULATOR":
        return SIMULATOR_SLIPPAGE_BPS
    if scenario == "STRESS_BOUND":
        return STRESS_SLIPPAGE_BPS
    st = (stats or {}).get(symbol)
    if st is None:
        return None
    if scenario == "MEASURED_MEDIAN":
        return st.half_spread_median_bps
    return st.half_spread_p90_bps          # MEASURED_P90 : None tant que n < seuil


def roundtrip_cost_bps(symbol: str, scenario: str,
                       stats: Optional[Dict[str, SpreadStats]] = None) -> Optional[float]:
    """Aller-retour = 2 x (frais taker + slippage d'une jambe)."""
    slip = slippage_bps(symbol, scenario, stats)
    return None if slip is None else 2.0 * (TAKER_FEE_BPS + slip)


def reprice(outcomes: pd.DataFrame, scenario: str, anchor: str = "dec",
            metric: str = "excess",
            stats: Optional[Dict[str, SpreadStats]] = None) -> pd.Series:
    """Net par décision sous un scénario de coût donné, à partir du BRUT déjà
    scellé. Rien n'est réécrit : le ledger garde l'observation, le coût reste
    une hypothèse qu'on fait varier."""
    stats = stats if stats is not None else spread_stats()
    col = f"{anchor}_{metric}_bps"
    cost = outcomes["symbol"].map(lambda s: roundtrip_cost_bps(s, scenario, stats))
    return outcomes[col] - cost
