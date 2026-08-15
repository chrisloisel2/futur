from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from .microstructure import BookSnapshot
from .schema import BookEvent, BookLevel

BBO_STREAMS = {"bbo", "bookTicker", "bbo-tbt"}


def stream_role(source_stream: Optional[str]) -> str:
    if source_stream is None or not str(source_stream).strip():
        return "unknown"
    return "bbo" if str(source_stream) in BBO_STREAMS else "deep"


@dataclass(frozen=True)
class BookReadiness:
    venue: str
    symbol: str
    deep_ready: bool
    bbo_ready: bool
    deep_levels: int
    bbo_levels: int
    ignored_unbootstrapped: int
    unknown_provenance_events: int
    source_streams: Tuple[str, ...]
    last_receive_ns: int
    last_event_ns: int


class LocalOrderBook:
    """Reconstruct one venue/symbol book from normalized BookEvents.

    The state is intentionally fail-closed:
    - BBO snapshots never mutate the deep book.
    - deep deltas are ignored until a genuine deep snapshot has bootstrapped it.
    - events without source_stream provenance cannot make a book ready.

    Price state and depth state are intentionally separate.  An explicit BBO is
    preferred for price discovery; when a venue does not expose a dedicated BBO
    (currently Bybit in this collector), the top level of the proven deep book
    supplies a comparable one-level price snapshot.  Deep-book freshness is
    therefore never inferred from BBO freshness.
    """

    def __init__(self, venue: str, symbol: str) -> None:
        self.venue = str(venue).lower()
        self.symbol = str(symbol).upper()
        self.bids: Dict[float, BookLevel] = {}
        self.asks: Dict[float, BookLevel] = {}
        self.bbo_bid: Optional[BookLevel] = None
        self.bbo_ask: Optional[BookLevel] = None
        self.deep_bootstrapped = False
        self.ignored_unbootstrapped = 0
        self.unknown_provenance_events = 0
        self.source_streams = set()
        self.last_receive_ns = 0
        self.last_event_ns = 0
        self.last_deep_receive_ns = 0
        self.last_deep_event_ns = 0
        self.last_bbo_receive_ns = 0
        self.last_bbo_event_ns = 0
        self.last_sequence_by_stream: Dict[str, int] = {}
        self._last_snapshot_key_by_stream: Dict[str, Tuple[int, int, int]] = {}

    def _check_identity(self, event: BookEvent) -> None:
        if str(event.venue).lower() != self.venue:
            raise ValueError("book event venue mismatch")
        if str(event.symbol).upper() != self.symbol:
            raise ValueError("book event symbol mismatch")

    @staticmethod
    def _set_level(book: Dict[float, BookLevel], event: BookEvent) -> None:
        price = float(event.price)
        qty = float(event.qty)
        if qty <= 0.0 or event.event_type == "remove":
            book.pop(price, None)
            return
        book[price] = BookLevel(price, qty, event.order_count)

    def apply(self, event: BookEvent) -> bool:
        self._check_identity(event)
        self.last_receive_ns = max(self.last_receive_ns, int(event.receive_ts_ns))
        self.last_event_ns = max(self.last_event_ns, int(event.event_ts_ns))

        role = stream_role(event.source_stream)
        if role == "unknown":
            self.unknown_provenance_events += 1
            return False

        stream = str(event.source_stream)
        self.source_streams.add(stream)
        self.last_sequence_by_stream[stream] = int(event.sequence_id)

        if role == "bbo":
            level = BookLevel(float(event.price), float(event.qty), event.order_count)
            if event.qty <= 0.0 or event.event_type == "remove":
                if event.side == "bid":
                    self.bbo_bid = None
                else:
                    self.bbo_ask = None
            elif event.side == "bid":
                self.bbo_bid = level
            else:
                self.bbo_ask = level
            self.last_bbo_receive_ns = max(self.last_bbo_receive_ns, int(event.receive_ts_ns))
            self.last_bbo_event_ns = max(self.last_bbo_event_ns, int(event.event_ts_ns))
            return True

        if event.event_type == "snapshot":
            key = (int(event.sequence_id), int(event.event_ts_ns), int(event.receive_ts_ns))
            if self._last_snapshot_key_by_stream.get(stream) != key:
                self.bids.clear()
                self.asks.clear()
                self._last_snapshot_key_by_stream[stream] = key
            self.deep_bootstrapped = True
        elif not self.deep_bootstrapped:
            self.ignored_unbootstrapped += 1
            return False

        target = self.bids if event.side == "bid" else self.asks
        self._set_level(target, event)
        self.last_deep_receive_ns = max(self.last_deep_receive_ns, int(event.receive_ts_ns))
        self.last_deep_event_ns = max(self.last_deep_event_ns, int(event.event_ts_ns))
        return True

    def apply_many(self, events: Iterable[BookEvent]) -> int:
        applied = 0
        for event in sorted(events, key=lambda x: (int(x.receive_ts_ns), int(x.event_ts_ns), int(x.sequence_id))):
            applied += int(self.apply(event))
        return applied

    def readiness(self) -> BookReadiness:
        return BookReadiness(
            venue=self.venue,
            symbol=self.symbol,
            deep_ready=bool(self.deep_bootstrapped and self.bids and self.asks),
            bbo_ready=bool(self.bbo_bid is not None and self.bbo_ask is not None),
            deep_levels=len(self.bids) + len(self.asks),
            bbo_levels=int(self.bbo_bid is not None) + int(self.bbo_ask is not None),
            ignored_unbootstrapped=int(self.ignored_unbootstrapped),
            unknown_provenance_events=int(self.unknown_provenance_events),
            source_streams=tuple(sorted(self.source_streams)),
            last_receive_ns=int(self.last_receive_ns),
            last_event_ns=int(self.last_event_ns),
        )

    def deep_snapshot(self) -> Optional[BookSnapshot]:
        if not (self.deep_bootstrapped and self.bids and self.asks):
            return None
        bids = tuple(sorted(self.bids.values(), key=lambda x: x.price, reverse=True))
        asks = tuple(sorted(self.asks.values(), key=lambda x: x.price))
        return BookSnapshot(
            int(self.last_deep_event_ns), bids, asks, int(self.last_deep_receive_ns)
        )

    def bbo_snapshot(self) -> Optional[BookSnapshot]:
        if self.bbo_bid is None or self.bbo_ask is None:
            return None
        return BookSnapshot(
            int(self.last_bbo_event_ns),
            (self.bbo_bid,),
            (self.bbo_ask,),
            int(self.last_bbo_receive_ns),
        )

    def price_snapshot(self) -> Optional[BookSnapshot]:
        """Return a one-level snapshot suitable for cross-venue price state.

        Dedicated BBO is preferred when available.  If a venue has no explicit
        BBO subscription, derive only the best bid/ask from its proven deep book
        so price-quality weighting is comparable across venues rather than using
        full-depth notional for one venue and one-level notional for another.
        """
        bbo = self.bbo_snapshot()
        if bbo is not None:
            return bbo
        deep = self.deep_snapshot()
        if deep is None:
            return None
        return BookSnapshot(
            int(deep.event_ts_ns),
            (deep.best_bid,),
            (deep.best_ask,),
            int(deep.available_ts_ns),
        )

    def snapshot(self, prefer_deep: bool = True) -> Optional[BookSnapshot]:
        if prefer_deep:
            deep = self.deep_snapshot()
            if deep is not None:
                return deep
            return self.bbo_snapshot()
        return self.price_snapshot()

    def fragmentation_features(self, levels: int = 10) -> Dict[str, float]:
        if not self.deep_bootstrapped:
            return {
                "bid_order_count_l10": float("nan"),
                "ask_order_count_l10": float("nan"),
                "bid_qty_per_order_l10": float("nan"),
                "ask_qty_per_order_l10": float("nan"),
            }
        bids = sorted(self.bids.values(), key=lambda x: x.price, reverse=True)[:levels]
        asks = sorted(self.asks.values(), key=lambda x: x.price)[:levels]

        def side_stats(rows):
            known = [x for x in rows if x.order_count is not None]
            if not known:
                return float("nan"), float("nan")
            orders = sum(int(x.order_count) for x in known)
            qty = sum(float(x.qty) for x in known)
            return float(orders), (float(qty / orders) if orders > 0 else float("nan"))

        bid_orders, bid_qpo = side_stats(bids)
        ask_orders, ask_qpo = side_stats(asks)
        return {
            "bid_order_count_l10": bid_orders,
            "ask_order_count_l10": ask_orders,
            "bid_qty_per_order_l10": bid_qpo,
            "ask_qty_per_order_l10": ask_qpo,
        }
