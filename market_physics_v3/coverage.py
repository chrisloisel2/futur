from __future__ import annotations

from typing import Dict, Mapping

VALID_STATUSES = {"EVENT_LEVEL", "AGGREGATED_ONLY", "STRANDED", "MISSING", "UNKNOWN"}

FAMILIES = {
    "microstructure": ["l2_book_events", "tick_trades", "bbo"],
    "cross_venue": ["binance", "bybit", "okx", "hyperliquid"],
    "leverage": ["open_interest", "funding", "mark_index_premium", "liquidations"],
    "options": ["option_quotes", "option_trades", "option_open_interest"],
    "execution": ["decision_send_ack_fill", "future_markouts"],
    "external": ["stablecoin_flows", "etf_cme", "macro_events", "news_events"],
}


def normalize_status(value: object) -> str:
    s = str(value).strip().upper()
    aliases = {
        "1": "EVENT_LEVEL", "TRUE": "EVENT_LEVEL", "YES": "EVENT_LEVEL", "PRESENT": "EVENT_LEVEL",
        "0": "MISSING", "FALSE": "MISSING", "NO": "MISSING", "ABSENT": "MISSING",
        "AGGREGATED": "AGGREGATED_ONLY", "BAR_ONLY": "AGGREGATED_ONLY",
    }
    s = aliases.get(s, s)
    if s not in VALID_STATUSES:
        raise ValueError("invalid feed status: %s" % value)
    return s


def audit_feed_status(status_by_feed: Mapping[str, object]) -> Dict[str, object]:
    normalized = {str(k): normalize_status(v) for k, v in status_by_feed.items()}
    result = {"families": {}, "blocking": [], "stranded": [], "aggregated_only": [], "unknown": []}
    for family, feeds in FAMILIES.items():
        family_map = {}
        for feed in feeds:
            status = normalized.get(feed, "UNKNOWN")
            family_map[feed] = status
            if status == "STRANDED":
                result["stranded"].append(feed)
            elif status == "AGGREGATED_ONLY":
                result["aggregated_only"].append(feed)
            elif status == "UNKNOWN":
                result["unknown"].append(feed)
            if status != "EVENT_LEVEL":
                result["blocking"].append(feed)
        result["families"][family] = family_map
    result["ready_for_full_market_physics_research"] = len(result["blocking"]) == 0
    p0 = ["microstructure", "cross_venue", "leverage", "execution"]
    result["ready_for_p0_research"] = all(
        status == "EVENT_LEVEL"
        for fam in p0
        for status in result["families"][fam].values()
    )
    return result
