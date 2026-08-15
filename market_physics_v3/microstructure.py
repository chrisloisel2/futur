from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

import numpy as np

from .schema import BookEvent, BookLevel, TradeEvent


@dataclass(frozen=True)
class BookSnapshot:
    event_ts_ns: int
    bids: Tuple[BookLevel, ...]
    asks: Tuple[BookLevel, ...]

    def __post_init__(self) -> None:
        if not self.bids or not self.asks:
            raise ValueError("snapshot needs both bid and ask")
        bids = tuple(sorted(self.bids, key=lambda x: x.price, reverse=True))
        asks = tuple(sorted(self.asks, key=lambda x: x.price))
        if bids[0].price >= asks[0].price:
            raise ValueError("crossed or locked book")
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)

    @property
    def best_bid(self) -> BookLevel:
        return self.bids[0]

    @property
    def best_ask(self) -> BookLevel:
        return self.asks[0]

    @property
    def mid(self) -> float:
        return 0.5 * (self.best_bid.price + self.best_ask.price)

    @property
    def spread_bps(self) -> float:
        return 1e4 * (self.best_ask.price - self.best_bid.price) / self.mid

    @property
    def microprice(self) -> float:
        qb = self.best_bid.qty
        qa = self.best_ask.qty
        denom = qb + qa
        if denom <= 0:
            return self.mid
        return (self.best_ask.price * qb + self.best_bid.price * qa) / denom

    def queue_imbalance(self, levels: int = 1) -> float:
        qb = sum(x.qty for x in self.bids[:levels])
        qa = sum(x.qty for x in self.asks[:levels])
        denom = qb + qa
        return (qb - qa) / denom if denom > 0 else 0.0

    def depth_within_bps(self, side: str, distance_bps: float) -> float:
        levels = self.bids if side == "bid" else self.asks
        if side not in {"bid", "ask"}:
            raise ValueError("side must be bid/ask")
        m = self.mid
        if side == "bid":
            return sum(x.qty for x in levels if 1e4 * (m - x.price) / m <= distance_bps)
        return sum(x.qty for x in levels if 1e4 * (x.price - m) / m <= distance_bps)

    def notional_to_move_bps(self, side: str, distance_bps: float) -> float:
        levels = self.asks if side == "buy" else self.bids
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy/sell")
        m = self.mid
        total = 0.0
        for level in levels:
            distance = 1e4 * ((level.price - m) / m if side == "buy" else (m - level.price) / m)
            if distance > distance_bps:
                break
            total += level.price * level.qty
        return total

    def weighted_depth_distance_bps(self, side: str, levels: int = 10) -> float:
        book = self.bids if side == "bid" else self.asks
        if side not in {"bid", "ask"}:
            raise ValueError("side must be bid/ask")
        m = self.mid
        q = np.array([x.qty for x in book[:levels]], dtype=float)
        if q.sum() <= 0:
            return float("nan")
        if side == "bid":
            d = np.array([1e4 * (m - x.price) / m for x in book[:levels]], dtype=float)
        else:
            d = np.array([1e4 * (x.price - m) / m for x in book[:levels]], dtype=float)
        return float(np.average(d, weights=q))


def top_of_book_ofi(previous: BookSnapshot, current: BookSnapshot) -> float:
    pb0, qb0 = previous.best_bid.price, previous.best_bid.qty
    pa0, qa0 = previous.best_ask.price, previous.best_ask.qty
    pb1, qb1 = current.best_bid.price, current.best_bid.qty
    pa1, qa1 = current.best_ask.price, current.best_ask.qty
    return float(
        (qb1 if pb1 >= pb0 else 0.0)
        - (qb0 if pb1 <= pb0 else 0.0)
        - (qa1 if pa1 <= pa0 else 0.0)
        + (qa0 if pa1 >= pa0 else 0.0)
    )


def cancellation_imbalance(events: Sequence[BookEvent]) -> float:
    bid_cancel = sum(e.price * e.qty for e in events if e.event_type == "cancel" and e.side == "bid")
    ask_cancel = sum(e.price * e.qty for e in events if e.event_type == "cancel" and e.side == "ask")
    denom = bid_cancel + ask_cancel
    return float((ask_cancel - bid_cancel) / denom) if denom > 0 else 0.0


def trade_flow_features(trades: Sequence[TradeEvent], start_mid: float, end_mid: float) -> Dict[str, float]:
    if start_mid <= 0 or end_mid <= 0:
        raise ValueError("mids must be positive")
    if not trades:
        return {
            "trade_count": 0.0,
            "signed_notional": 0.0,
            "gross_notional": 0.0,
            "flow_imbalance": 0.0,
            "mid_move_bps": 1e4 * (end_mid - start_mid) / start_mid,
            "impact_bps_per_million": 0.0,
            "absorption_notional_per_bp": 0.0,
            "trades_per_second": 0.0,
            "interarrival_cv": float("nan"),
        }
    signed = 0.0
    gross = 0.0
    for t in trades:
        notion = t.price * t.qty
        gross += notion
        signed += notion if t.aggressor == "buy" else -notion
    move_bps = 1e4 * (end_mid - start_mid) / start_mid
    impact = move_bps / (signed / 1e6) if abs(signed) > 1e-12 else 0.0
    absorption = abs(signed) / (abs(move_bps) + 1.0)
    ts = np.array(sorted(t.event_ts_ns for t in trades), dtype=np.int64)
    duration_s = max((ts[-1] - ts[0]) / 1e9, 1e-9)
    rate = len(ts) / duration_s if len(ts) > 1 else 0.0
    if len(ts) > 2:
        d = np.diff(ts).astype(float) / 1e9
        cv = float(np.std(d) / np.mean(d)) if np.mean(d) > 0 else float("nan")
    else:
        cv = float("nan")
    return {
        "trade_count": float(len(trades)),
        "signed_notional": float(signed),
        "gross_notional": float(gross),
        "flow_imbalance": float(signed / gross) if gross > 0 else 0.0,
        "mid_move_bps": float(move_bps),
        "impact_bps_per_million": float(impact),
        "absorption_notional_per_bp": float(absorption),
        "trades_per_second": float(rate),
        "interarrival_cv": cv,
    }


def book_feature_vector(snapshot: BookSnapshot, levels: int = 10) -> Dict[str, float]:
    return {
        "best_bid": snapshot.best_bid.price,
        "best_ask": snapshot.best_ask.price,
        "mid": snapshot.mid,
        "spread_bps": snapshot.spread_bps,
        "microprice": snapshot.microprice,
        "microprice_offset_bps": 1e4 * (snapshot.microprice - snapshot.mid) / snapshot.mid,
        "queue_imbalance_l1": snapshot.queue_imbalance(1),
        "queue_imbalance_l5": snapshot.queue_imbalance(min(5, levels)),
        "queue_imbalance_l10": snapshot.queue_imbalance(levels),
        "bid_depth_5bps": snapshot.depth_within_bps("bid", 5.0),
        "ask_depth_5bps": snapshot.depth_within_bps("ask", 5.0),
        "bid_depth_25bps": snapshot.depth_within_bps("bid", 25.0),
        "ask_depth_25bps": snapshot.depth_within_bps("ask", 25.0),
        "buy_notional_10bps": snapshot.notional_to_move_bps("buy", 10.0),
        "sell_notional_10bps": snapshot.notional_to_move_bps("sell", 10.0),
        "bid_weighted_distance_bps": snapshot.weighted_depth_distance_bps("bid", levels),
        "ask_weighted_distance_bps": snapshot.weighted_depth_distance_bps("ask", levels),
    }


def resilience_seconds(snapshots: Sequence[BookSnapshot], side: str, shock_drop_fraction: float = 0.5, recovery_fraction: float = 0.9, depth_bps: float = 10.0) -> float:
    if len(snapshots) < 3:
        return float("nan")
    if side not in {"bid", "ask"}:
        raise ValueError("side must be bid/ask")
    baseline = snapshots[0].depth_within_bps(side, depth_bps)
    if baseline <= 0:
        return float("nan")
    shock_idx = None
    for i, s in enumerate(snapshots[1:], 1):
        if s.depth_within_bps(side, depth_bps) <= baseline * (1.0 - shock_drop_fraction):
            shock_idx = i
            break
    if shock_idx is None:
        return float("nan")
    target = baseline * recovery_fraction
    for s in snapshots[shock_idx + 1:]:
        if s.depth_within_bps(side, depth_bps) >= target:
            return float((s.event_ts_ns - snapshots[shock_idx].event_ts_ns) / 1e9)
    return float("inf")
