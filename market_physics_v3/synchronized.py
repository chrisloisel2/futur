from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .cross_venue import VenueQuote, fair_value
from .orderbook import LocalOrderBook
from .schema import BookEvent


@dataclass(frozen=True)
class SynchronizedState:
    symbol: str
    asof_ns: int
    ready: bool
    venues_used: Tuple[str, ...]
    venues_missing: Tuple[str, ...]
    sync_span_ms: float
    fair_value: float
    dispersion_bps: float
    weights: Mapping[str, float]
    dislocation_bps: Mapping[str, float]
    receive_age_ms: Mapping[str, float]
    transport_lag_ms: Mapping[str, float]
    reasons: Tuple[str, ...]

    def as_dict(self) -> Dict[str, object]:
        return {
            "symbol": self.symbol,
            "asof_ns": int(self.asof_ns),
            "ready": bool(self.ready),
            "venues_used": list(self.venues_used),
            "venues_missing": list(self.venues_missing),
            "sync_span_ms": float(self.sync_span_ms),
            "fair_value": float(self.fair_value),
            "dispersion_bps": float(self.dispersion_bps),
            "weights": dict(self.weights),
            "dislocation_bps": dict(self.dislocation_bps),
            "receive_age_ms": dict(self.receive_age_ms),
            "transport_lag_ms": dict(self.transport_lag_ms),
            "reasons": list(self.reasons),
        }


class SynchronizedBookEngine:
    """Receive-time synchronized local-book engine for live/replay research.

    Feed events in nondecreasing receive_ts_ns order. The engine never rewinds;
    for historical replay, call state() as the receive-time cursor advances.
    """

    def __init__(self) -> None:
        self.books: Dict[Tuple[str, str], LocalOrderBook] = {}
        self.last_ingest_receive_ns = 0

    def book(self, venue: str, symbol: str) -> LocalOrderBook:
        key = (str(venue).lower(), str(symbol).upper())
        if key not in self.books:
            self.books[key] = LocalOrderBook(*key)
        return self.books[key]

    def ingest(self, event: BookEvent) -> bool:
        receive_ns = int(event.receive_ts_ns)
        if receive_ns < self.last_ingest_receive_ns:
            raise ValueError("book events must be ingested in receive-time order")
        self.last_ingest_receive_ns = receive_ns
        return self.book(event.venue, event.symbol).apply(event)

    def ingest_many(self, events: Iterable[BookEvent]) -> int:
        applied = 0
        for event in sorted(events, key=lambda x: (int(x.receive_ts_ns), int(x.event_ts_ns), int(x.sequence_id))):
            applied += int(self.ingest(event))
        return applied

    def state(
        self,
        symbol: str,
        asof_ns: int,
        required_venues: Sequence[str] = ("binance", "bybit", "okx", "hyperliquid"),
        require_deep: bool = True,
        max_receive_age_ms: float = 1500.0,
        max_transport_lag_ms: float = 5000.0,
        max_sync_span_ms: float = 1000.0,
        min_venues: int = 2,
        half_life_ms: float = 500.0,
        transport_half_life_ms: float = 2000.0,
    ) -> SynchronizedState:
        symbol = str(symbol).upper()
        asof_ns = int(asof_ns)
        quotes = []
        used = []
        missing = []
        reasons = []
        receive_times = []

        for venue in [str(v).lower() for v in required_venues]:
            book = self.books.get((venue, symbol))
            if book is None:
                missing.append(venue)
                reasons.append("%s:no_book_state" % venue)
                continue
            readiness = book.readiness()
            if require_deep and not readiness.deep_ready:
                missing.append(venue)
                reasons.append("%s:deep_not_ready" % venue)
                continue
            snap = book.snapshot(prefer_deep=require_deep)
            if snap is None:
                missing.append(venue)
                reasons.append("%s:no_snapshot" % venue)
                continue
            if snap.available_ts_ns > asof_ns:
                missing.append(venue)
                reasons.append("%s:not_yet_received" % venue)
                continue
            receive_age_ms = (asof_ns - snap.available_ts_ns) / 1e6
            if receive_age_ms > float(max_receive_age_ms):
                missing.append(venue)
                reasons.append("%s:receive_stale" % venue)
                continue
            if snap.transport_lag_ms > float(max_transport_lag_ms):
                missing.append(venue)
                reasons.append("%s:transport_stale" % venue)
                continue

            depth_usd = 0.5 * (
                snap.notional_to_move_bps("buy", 10.0)
                + snap.notional_to_move_bps("sell", 10.0)
            )
            quotes.append(VenueQuote(
                venue=venue,
                event_ts_ns=int(snap.event_ts_ns),
                mid=float(snap.mid),
                spread_bps=float(snap.spread_bps),
                depth_10bps_usd=float(depth_usd),
                receive_ts_ns=int(snap.available_ts_ns),
            ))
            used.append(venue)
            receive_times.append(int(snap.available_ts_ns))

        sync_span_ms = (
            (max(receive_times) - min(receive_times)) / 1e6 if len(receive_times) >= 2 else float("inf")
        )
        if len(used) < int(min_venues):
            reasons.append("insufficient_venues")
        if receive_times and sync_span_ms > float(max_sync_span_ms):
            reasons.append("sync_span_too_wide")

        if not quotes:
            return SynchronizedState(
                symbol=symbol,
                asof_ns=asof_ns,
                ready=False,
                venues_used=tuple(),
                venues_missing=tuple(sorted(set(missing))),
                sync_span_ms=float("inf"),
                fair_value=float("nan"),
                dispersion_bps=float("nan"),
                weights={},
                dislocation_bps={},
                receive_age_ms={},
                transport_lag_ms={},
                reasons=tuple(sorted(set(reasons))),
            )

        cv = fair_value(
            quotes,
            asof_ns,
            half_life_ms=half_life_ms,
            transport_half_life_ms=transport_half_life_ms,
        )
        ready = (
            len(used) >= int(min_venues)
            and sync_span_ms <= float(max_sync_span_ms)
            and (not require_deep or len(used) == len(required_venues))
        )
        if require_deep and len(used) != len(required_venues):
            reasons.append("required_deep_venues_missing")

        return SynchronizedState(
            symbol=symbol,
            asof_ns=asof_ns,
            ready=bool(ready),
            venues_used=tuple(used),
            venues_missing=tuple(sorted(set(missing))),
            sync_span_ms=float(sync_span_ms),
            fair_value=float(cv["fair_value"]),
            dispersion_bps=float(cv["dispersion_bps"]),
            weights=dict(cv["weights"]),
            dislocation_bps=dict(cv["dislocation_bps"]),
            receive_age_ms=dict(cv["receive_age_ms"]),
            transport_lag_ms=dict(cv["transport_lag_ms"]),
            reasons=tuple(sorted(set(reasons))),
        )
