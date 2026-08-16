from __future__ import annotations

from typing import Dict, Iterable, Set

from .contracts import DataDomain
from .registry import LAB_REGISTRY


DOMAIN_COLUMN_HINTS = {
    DataDomain.BOOK: ("bid", "ask", "depth", "book", "queue"),
    DataDomain.TRADE: ("trade", "signed_notional", "cvd", "flow"),
    DataDomain.DERIVATIVES: ("funding", "open_interest", "oi", "mark", "liquidation", "basis"),
    DataDomain.SPOT: ("spot", "index"),
    DataDomain.WALLET: ("wallet", "address"),
    DataDomain.OPTIONS: ("iv", "skew", "option", "gamma", "delta"),
    DataDomain.ONCHAIN: ("onchain", "exchange_flow", "stablecoin", "deposit", "withdrawal"),
    DataDomain.EVENT: ("event", "news", "calendar"),
    DataDomain.EXECUTION: ("fill", "queue_position", "slippage", "markout", "execution"),
    DataDomain.CROSS_ASSET: ("beta", "residual", "leader", "follower", "cross_asset"),
}


def infer_available_domains(columns: Iterable[str]) -> Set[DataDomain]:
    lowered = [str(column).lower() for column in columns]
    available = set()
    for domain, hints in DOMAIN_COLUMN_HINTS.items():
        if any(any(hint in column for hint in hints) for column in lowered):
            available.add(domain)
    return available


def lab_readiness(columns: Iterable[str]) -> Dict[str, Dict[str, object]]:
    available = infer_available_domains(columns)
    out = {}
    for lab_id, spec in LAB_REGISTRY.items():
        missing = [domain.value for domain in spec.domains if domain not in available]
        out[lab_id] = {"name": spec.name, "ready": not missing, "missing_domains": missing, "independence_key": spec.independence_key}
    return out
