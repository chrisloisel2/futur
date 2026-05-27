from __future__ import annotations

from domain.events.raw import BaseEvent


class CrossVenuePremiumEvent(BaseEvent):
    ref_venue: str
    ref_price: float
    target_venue: str
    target_price: float
    premium_bps: float
    basis_bps: float
    cross_source_ok: bool = True
    cross_source_error_code: int = 0
    source: str = "cross_venue"
    event_type: str = "cross_venue_premium"
