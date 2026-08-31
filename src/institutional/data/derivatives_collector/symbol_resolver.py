"""
src/institutional/data/derivatives_collector/symbol_resolver.py
─────────────────────────────────────────────────────────────────────────────
Résout un univers CANONIQUE (ex. configs/portfolio_v1_1_parallel_50.yaml)
vers les symboles RÉELLEMENT tradeables côté Binance USDM futures, en
interrogeant /fapi/v1/exchangeInfo (métadonnées exchange live) — jamais un
mapping deviné silencieusement.

Bug corrigé (2026-08-31, découvert par le worker SHORT_COVERING_CONTINUATION_V1
du Live Alpha Lab) : `scripts/run_derivatives_collector.py` recevait la liste
canonique BRUTE en `--symbols`, sans résolution — 3/50 symboles échouaient
silencieusement (aucune trace explicite de pourquoi) : MKRUSDT (délisté,
confirmé via l'API live, code -4108), PEPEUSDT et RNDRUSDT (renommés côté
Binance en 1000PEPEUSDT et RENDERUSDT — le "1000-prefix" pour les meme-coins
est déjà un pattern connu de ce repo, voir collector.py:213-214, la
convention RNDR->RENDER est déjà documentée dans la mémoire projet).

Règle : un rename n'est accepté QUE s'il est dans KNOWN_RENAMES (mapping
explicite, documenté, avec preuve) ET que le symbole renommé existe RÉELLEMENT
dans l'exchangeInfo live avec status=TRADING. Jamais de heuristique de
matching approximatif (ex. distance de chaîne) — silence = risque caché.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"

# Renames EXPLICITES et documentés (pas un guess) : le symbole canonique n'est
# plus listé tel quel dans exchangeInfo, mais un remplacement connu existe.
# - PEPEUSDT -> 1000PEPEUSDT : convention Binance de re-listing avec
#   multiplicateur notionnel x1000 pour les meme-coins à prix très bas
#   (pattern déjà présent dans ce repo, collector.py:213-214).
# - RNDRUSDT -> RENDERUSDT : migration de ticker RNDR->RENDER (rebrand du
#   projet Render Network), déjà notée dans la mémoire projet.
KNOWN_RENAMES: Dict[str, str] = {
    "PEPEUSDT": "1000PEPEUSDT",
    "RNDRUSDT": "RENDERUSDT",
}


@dataclass(frozen=True)
class ResolvedSymbol:
    canonical_asset: str          # nom tel qu'il apparaît dans l'univers figé
    exchange_symbol: Optional[str]  # symbole réel côté exchange, None si introuvable
    instrument_status: str        # TRADING | RENAMED | DELISTED | NOT_FOUND
    eligibility_reason: str       # explication humaine, jamais vide

    @property
    def eligible(self) -> bool:
        return self.instrument_status in ("TRADING", "RENAMED")


def fetch_exchange_info(timeout: float = 10.0) -> dict:
    """Métadonnées exchange LIVE (pas de cache disque — appelé une fois par run
    de résolution, coût réseau négligeable vs le risque de mapping périmé)."""
    req = urllib.request.Request(EXCHANGE_INFO_URL, headers={"User-Agent": "futur-symbol-resolver"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _status_by_symbol(exchange_info: dict) -> Dict[str, str]:
    return {s["symbol"]: s.get("status", "UNKNOWN") for s in exchange_info.get("symbols", [])}


def resolve_symbol(canonical: str, exchange_info: dict) -> ResolvedSymbol:
    statuses = _status_by_symbol(exchange_info)

    if canonical in statuses:
        st = statuses[canonical]
        if st == "TRADING":
            return ResolvedSymbol(canonical, canonical, "TRADING",
                                  f"trouvé tel quel dans exchangeInfo, status={st}")
        # statut exchange RÉEL préservé tel quel (SETTLING/BREAK/PENDING_TRADING/…) —
        # ne jamais forcer un label générique "DELISTED" qui perdrait l'info exacte.
        return ResolvedSymbol(canonical, None, st,
                              f"trouvé dans exchangeInfo mais status={st} (pas TRADING) — exclu")

    renamed = KNOWN_RENAMES.get(canonical)
    if renamed and renamed in statuses and statuses[renamed] == "TRADING":
        return ResolvedSymbol(canonical, renamed, "RENAMED",
                              f"canonique absent d'exchangeInfo ; rename connu et documenté "
                              f"{canonical}->{renamed} confirmé TRADING dans exchangeInfo live")

    return ResolvedSymbol(canonical, None, "NOT_FOUND",
                          f"absent d'exchangeInfo, aucun rename connu dans KNOWN_RENAMES "
                          f"ne résout vers un symbole TRADING (essayé: {renamed!r})")


def resolve_universe(canonical_list: List[str], exchange_info: Optional[dict] = None
                     ) -> List[ResolvedSymbol]:
    """Résout toute une liste — jamais un symbole muet : chaque canonical_asset
    en entrée produit EXACTEMENT une ResolvedSymbol en sortie (éligible ou pas,
    mais toujours tracée avec une raison)."""
    ei = exchange_info if exchange_info is not None else fetch_exchange_info()
    return [resolve_symbol(c, ei) for c in canonical_list]


def eligible_exchange_symbols(canonical_list: List[str], exchange_info: Optional[dict] = None
                              ) -> List[str]:
    resolved = resolve_universe(canonical_list, exchange_info)
    return [r.exchange_symbol for r in resolved if r.eligible]
