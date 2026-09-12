#!/usr/bin/env python3
"""
pre_binance_features.py -- l'etat d'un marche AVANT que Binance ouvre son perpetuel, decrit depuis les bougies
d'une autre place. Fonctions pures sur des bougies deja stockees.

Borne structurelle : toute bougie dont l'ouverture est >= t0 est REJETEE avant tout calcul. Ce module ne peut
donc pas decrire ce qui se passe apres l'arrivee de Binance ; il ne produit ni signal ni verdict. Les
rendements calcules ici sont des rendements PASSES sur une AUTRE place : une description, pas une prediction.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional

FEATURES = ["first_venue", "listing_age_days_at_binance_open", "pre_binance_return_30d", "pre_binance_return_14d", "pre_binance_return_7d", "pre_binance_return_3d",
            "pre_binance_return_24h", "pre_binance_volatility_7d", "pre_binance_volume_7d", "pre_binance_volume_24h", "pre_binance_range_7d", "pre_binance_max_drawdown_7d",
            "pre_binance_pump_score", "pre_binance_exhaustion_score", "pre_binance_liquidity_proxy", "data_source", "coverage_status"]
COVERAGE = ("full", "partial", "daily_only", "not_collected")


class PostT0Leak(ValueError):
    pass


def strictly_before(candles: List[Dict[str, Any]], t0_ms: int) -> List[Dict[str, Any]]:
    """Refuse -- ne filtre pas en silence -- toute bougie a ou apres t0."""
    for c in candles:
        if c["open_time_ms"] >= t0_ms:
            raise PostT0Leak("candle at %d is not before t0 %d" % (c["open_time_ms"], t0_ms))
    return sorted(candles, key=lambda c: c["open_time_ms"])


def _ret(candles: List[Dict[str, Any]], span_ms: int, t0_ms: int) -> Optional[float]:
    """close de la derniere bougie avant t0 / close de la premiere bougie a ou apres t0 - span, moins 1."""
    if not candles:
        return None
    last = candles[-1]
    start = [c for c in candles if c["open_time_ms"] >= t0_ms - span_ms]
    if not start or start[0] is last or start[0]["close"] <= 0:
        return None
    return round(last["close"] / start[0]["close"] - 1.0, 6)


def _window(candles: List[Dict[str, Any]], span_ms: int, t0_ms: int) -> List[Dict[str, Any]]:
    return [c for c in candles if c["open_time_ms"] >= t0_ms - span_ms]


def compute(daily: List[Dict[str, Any]], hourly: List[Dict[str, Any]], t0_ms: int, first_venue: str, listing_age_days: Optional[float], source: str) -> Dict[str, Any]:
    d = strictly_before(daily, t0_ms); h = strictly_before(hourly, t0_ms)
    D, H = 86_400_000, 3_600_000
    f: Dict[str, Any] = {k: None for k in FEATURES}
    f["first_venue"] = first_venue; f["listing_age_days_at_binance_open"] = listing_age_days; f["data_source"] = source
    if not d and not h:
        f["coverage_status"] = "not_collected"; return f
    f["pre_binance_return_30d"] = _ret(d, 30 * D, t0_ms); f["pre_binance_return_14d"] = _ret(d, 14 * D, t0_ms)
    f["pre_binance_return_7d"] = _ret(d, 7 * D, t0_ms); f["pre_binance_return_3d"] = _ret(d, 3 * D, t0_ms)
    f["pre_binance_return_24h"] = _ret(h, 24 * H, t0_ms) if h else None
    w7 = _window(d, 7 * D, t0_ms)
    if len(w7) >= 3:
        lr = [math.log(w7[i]["close"] / w7[i - 1]["close"]) for i in range(1, len(w7)) if w7[i]["close"] > 0 and w7[i - 1]["close"] > 0]
        f["pre_binance_volatility_7d"] = round(statistics.pstdev(lr), 6) if len(lr) >= 2 else None
        hi, lo = max(c["high"] for c in w7), min(c["low"] for c in w7)
        f["pre_binance_range_7d"] = round((hi - lo) / lo, 6) if lo > 0 else None
        peak, mdd = 0.0, 0.0
        for c in w7:
            peak = max(peak, c["high"]); mdd = min(mdd, (c["close"] - peak) / peak if peak > 0 else 0.0)
        f["pre_binance_max_drawdown_7d"] = round(mdd, 6)
        qv = [c["quote_volume"] for c in w7 if c.get("quote_volume") is not None]
        f["pre_binance_volume_7d"] = round(sum(qv), 2) if qv else None
        f["pre_binance_liquidity_proxy"] = round(statistics.median(qv), 2) if qv else None
    w24 = _window(h, 24 * H, t0_ms)
    qv24 = [c["quote_volume"] for c in w24 if c.get("quote_volume") is not None]
    f["pre_binance_volume_24h"] = round(sum(qv24), 2) if qv24 else None
    # scores descriptifs, bornes, expliques : pump = rendement 7 j en unites de volatilite journaliere ; exhaustion = recul
    # depuis le plus haut 7 j combine au tarissement du volume 24 h contre la moyenne 7 j. Ni l'un ni l'autre n'est un signal.
    r7, v7 = f["pre_binance_return_7d"], f["pre_binance_volatility_7d"]
    if r7 is not None and v7:
        f["pre_binance_pump_score"] = round(max(-10.0, min(10.0, r7 / (v7 * math.sqrt(7)))), 4)
    if f["pre_binance_max_drawdown_7d"] is not None:
        dd = -f["pre_binance_max_drawdown_7d"]
        vol_dry = None
        if f["pre_binance_volume_24h"] is not None and f["pre_binance_volume_7d"]:
            vol_dry = 1.0 - min(1.0, f["pre_binance_volume_24h"] / (f["pre_binance_volume_7d"] / 7.0))
        f["pre_binance_exhaustion_score"] = round(min(1.0, dd) * 0.6 + (vol_dry if vol_dry is not None else 0.0) * 0.4, 4)
    have_d, have_h = bool(w7), bool(w24)
    f["coverage_status"] = "full" if (have_d and have_h) else ("daily_only" if have_d else ("partial" if (d or h) else "not_collected"))
    return f
