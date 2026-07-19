"""
src/institutional/portfolio/opportunity_ranker.py
─────────────────────────────────────────────────────────────────────────────
Ranker d'opportunités (univers 50) — on n'exécute PAS tous les signaux, on
sélectionne les MEILLEURS. 50 cryptos doivent augmenter la SÉLECTION, pas le risque.

Score, tri, puis application des caps (top-k, max alts, max/bucket, no meme excess,
no BLOCK/WARN). Retourne les opportunités retenues + les rejets avec raison.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from src.institutional.risk.correlation_buckets import bucket_of, is_meme
from src.institutional.universe.asset_quality_filter import AssetQualityStatus


@dataclass
class RankerLimits:
    max_total_positions: int = 7
    max_alt_positions: int = 5
    max_positions_per_bucket: int = 2
    max_meme_positions: int = 1
    majors: tuple = ("BTCUSDT", "ETHUSDT")


@dataclass(frozen=True)
class RankedOpportunity:
    symbol: str
    engine: str
    final_score: float
    rank: int
    allowed: bool
    bucket: str
    rejection_reason: Optional[str]


def rank_opportunities(
    candidates: List[dict],            # {symbol, engine, score}
    quality: Dict[str, AssetQualityStatus],
    limits: RankerLimits = RankerLimits(),
) -> List[RankedOpportunity]:
    """candidates triés par score décroissant → sélection top-k sous contraintes."""
    ranked = sorted(candidates, key=lambda c: -c.get("score", 0.0))
    out: List[RankedOpportunity] = []
    n_total = n_alt = n_meme = 0
    bucket_count: Dict[str, int] = {}
    chosen_assets = set()

    for i, c in enumerate(ranked):
        sym = c["symbol"]; bucket = bucket_of(sym)
        is_major = sym in limits.majors
        reason = None
        q = quality.get(sym, AssetQualityStatus.BLOCK)

        if q == AssetQualityStatus.BLOCK:
            reason = "ASSET_BLOCKED_QUALITY"
        elif sym in chosen_assets:
            reason = "ALREADY_OPEN_ASSET"
        elif n_total >= limits.max_total_positions:
            reason = "MAX_TOTAL_POSITIONS"
        elif not is_major and n_alt >= limits.max_alt_positions:
            reason = "MAX_ALT_POSITIONS"
        elif bucket_count.get(bucket, 0) >= limits.max_positions_per_bucket:
            reason = "MAX_BUCKET_POSITIONS"
        elif is_meme(sym) and n_meme >= limits.max_meme_positions:
            reason = "MAX_MEME_POSITIONS"

        allowed = reason is None
        out.append(RankedOpportunity(sym, c.get("engine", "?"), float(c.get("score", 0)),
                                     i, allowed, bucket, reason))
        if allowed:
            n_total += 1; chosen_assets.add(sym)
            bucket_count[bucket] = bucket_count.get(bucket, 0) + 1
            if not is_major:
                n_alt += 1
            if is_meme(sym):
                n_meme += 1
    return out


def selected(ranked: List[RankedOpportunity]) -> List[RankedOpportunity]:
    return [r for r in ranked if r.allowed]
