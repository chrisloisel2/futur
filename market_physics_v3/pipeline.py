from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Sequence

from .cross_venue import VenueQuote, fair_value
from .derivatives import cascade_pressure, liquidation_flow, option_surface_state
from .microstructure import (
    BookSnapshot,
    book_feature_vector,
    cancellation_imbalance,
    removal_imbalance,
    top_of_book_ofi,
    trade_flow_features,
)
from .schema import BookEvent, DerivativeEvent, OptionQuote, TradeEvent


@dataclass
class VenueWindow:
    venue: str
    snapshot_start: BookSnapshot
    snapshot_end: BookSnapshot
    book_events: Sequence[BookEvent] = field(default_factory=list)
    trades: Sequence[TradeEvent] = field(default_factory=list)
    derivatives: Sequence[DerivativeEvent] = field(default_factory=list)


class MarketPhysicsStateBuilder:
    """Build a strict point-in-time state from information already received.

    Market event time describes when an exchange event happened. Research
    availability is receive time. A delayed event whose event_ts_ns is in the
    past must not leak into a state built before receive_ts_ns.
    """

    def __init__(self, cross_venue_half_life_ms: float = 500.0) -> None:
        self.cross_venue_half_life_ms = float(cross_venue_half_life_ms)

    @staticmethod
    def _available(seq, asof_ns):
        return [x for x in seq if int(x.receive_ts_ns) <= int(asof_ns)]

    def build(
        self,
        symbol: str,
        asof_ns: int,
        venue_windows: Sequence[VenueWindow],
        option_quotes: Sequence[OptionQuote] = (),
        spot_for_options: Optional[float] = None,
        liquidation_surface: Optional[Mapping[float, float]] = None,
        absorbable_surface: Optional[Mapping[float, float]] = None,
    ) -> Dict[str, float]:
        if not venue_windows:
            raise ValueError("at least one venue window is required")
        result = {"asof_ns": float(asof_ns)}
        quotes = []
        for window in venue_windows:
            if window.snapshot_start.available_ts_ns > asof_ns:
                raise ValueError("snapshot_start was not yet received")
            if window.snapshot_end.available_ts_ns > asof_ns:
                raise ValueError("snapshot_end was not yet received")
            prefix = window.venue.lower() + "__"
            book = book_feature_vector(window.snapshot_end)
            for k, v in book.items():
                result[prefix + k] = float(v)
            result[prefix + "ofi_l1"] = top_of_book_ofi(window.snapshot_start, window.snapshot_end)
            events = self._available(window.book_events, asof_ns)
            # L2 disappearance is not automatically a true cancellation.
            result[prefix + "remove_imbalance"] = removal_imbalance(events)
            result[prefix + "cancel_imbalance"] = cancellation_imbalance(events)
            trades = self._available(window.trades, asof_ns)
            flow = trade_flow_features(trades, window.snapshot_start.mid, window.snapshot_end.mid)
            for k, v in flow.items():
                result[prefix + k] = float(v)
            deriv = self._available(window.derivatives, asof_ns)
            liq = liquidation_flow(deriv)
            for k, v in liq.items():
                result[prefix + k] = float(v)
            depth_usd = 0.5 * (
                window.snapshot_end.notional_to_move_bps("buy", 10.0)
                + window.snapshot_end.notional_to_move_bps("sell", 10.0)
            )
            # Quote market timestamp is kept for staleness weighting, but the
            # snapshot itself was admitted only after receive-time PIT checks.
            quotes.append(
                VenueQuote(
                    window.venue,
                    window.snapshot_end.event_ts_ns,
                    window.snapshot_end.mid,
                    window.snapshot_end.spread_bps,
                    depth_usd,
                )
            )
        cv = fair_value(quotes, asof_ns, self.cross_venue_half_life_ms)
        result["cross__fair_value"] = float(cv["fair_value"])
        result["cross__dispersion_bps"] = float(cv["dispersion_bps"])
        for venue, value in cv["dislocation_bps"].items():
            result["cross__%s_dislocation_bps" % venue.lower()] = float(value)
        causal_options = [q for q in option_quotes if int(q.receive_ts_ns) <= int(asof_ns)]
        if causal_options and spot_for_options is not None:
            for k, v in option_surface_state(causal_options, spot_for_options).items():
                result["options__" + k] = float(v)
        if liquidation_surface is not None and absorbable_surface is not None:
            for k, v in cascade_pressure(liquidation_surface, absorbable_surface).items():
                result["leverage__" + k] = float(v)
        return result
