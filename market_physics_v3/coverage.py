from __future__ import annotations
from typing import Dict, Mapping

VALID_STATUSES = {"EVENT_LEVEL", "PIT_AGGREGATED", "AGGREGATED_ONLY", "STRANDED", "MISSING", "UNKNOWN"}
FAMILIES = {
    "microstructure": ["l2_book_events", "tick_trades", "bbo"],
    "cross_venue": ["binance", "bybit", "okx", "hyperliquid"],
    "leverage": ["open_interest", "funding", "mark_index_premium", "liquidations"],
    "options": ["option_quotes", "option_trades", "option_open_interest"],
    "execution": ["decision_send_ack_fill", "future_markouts"],
    "external": ["stablecoin_flows", "etf_cme", "macro_events", "news_events"],
}

def normalize_status(value):
    s=str(value).strip().upper()
    aliases={"1":"EVENT_LEVEL","TRUE":"EVENT_LEVEL","YES":"EVENT_LEVEL","PRESENT":"EVENT_LEVEL","0":"MISSING","FALSE":"MISSING","NO":"MISSING","ABSENT":"MISSING","AGGREGATED":"AGGREGATED_ONLY","BAR_ONLY":"AGGREGATED_ONLY","AGGREGATED_PIT":"PIT_AGGREGATED","PIT":"PIT_AGGREGATED"}
    s=aliases.get(s,s)
    if s not in VALID_STATUSES: raise ValueError('invalid feed status: %s' % value)
    return s

def _ok(feed,status):
    if feed in {"l2_book_events","tick_trades","bbo","binance","bybit","okx","hyperliquid","mark_index_premium","liquidations","option_quotes","option_trades","decision_send_ack_fill","future_markouts"}:
        return status=="EVENT_LEVEL"
    if feed in {"open_interest","funding","option_open_interest","stablecoin_flows","etf_cme","macro_events","news_events"}:
        return status in {"EVENT_LEVEL","PIT_AGGREGATED"}
    return False

def audit_feed_status(status_by_feed: Mapping[str,object]) -> Dict[str,object]:
    normalized={str(k):normalize_status(v) for k,v in status_by_feed.items()}
    result={"families":{},"blocking":[],"stranded":[],"aggregated_only":[],"pit_aggregated":[],"unknown":[]}
    family_ready={}
    for family,feeds in FAMILIES.items():
        fm={}; block=[]
        for feed in feeds:
            status=normalized.get(feed,"UNKNOWN"); fm[feed]=status
            if status=="STRANDED": result["stranded"].append(feed)
            elif status=="AGGREGATED_ONLY": result["aggregated_only"].append(feed)
            elif status=="PIT_AGGREGATED": result["pit_aggregated"].append(feed)
            elif status=="UNKNOWN": result["unknown"].append(feed)
            if not _ok(feed,status): block.append(feed); result["blocking"].append(feed)
        result["families"][family]={"feeds":fm,"ready":not block,"blocking":block}
        family_ready[family]=not block

    # Cross-venue book research does not require a true one-row-per-match tape.
    # It requires proven deep books + BBO on all P0 venues. This is deliberately
    # separate from the stricter microstructure/P0 gate, where tick_trades stays
    # blocked while Binance provides aggTrade rather than an individual tape.
    result["ready_for_synchronized_book_research"] = bool(
        _ok("l2_book_events", normalized.get("l2_book_events", "UNKNOWN"))
        and _ok("bbo", normalized.get("bbo", "UNKNOWN"))
        and family_ready["cross_venue"]
    )
    result["ready_for_p0_market_research"] = family_ready["microstructure"] and family_ready["cross_venue"] and family_ready["leverage"]
    result["ready_for_p0_research"] = result["ready_for_p0_market_research"]
    result["ready_for_execution_research"] = family_ready["execution"]
    result["ready_for_options_research"] = family_ready["options"]
    result["ready_for_external_context"] = family_ready["external"]
    result["ready_for_full_market_physics_research"] = all(family_ready.values())
    return result
