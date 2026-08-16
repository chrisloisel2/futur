from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterator, Mapping, MutableMapping, Sequence, Tuple

import math
import numpy as np

from .common import ChunkedPlaneWriter, infer_run_window, iter_base_key_chunks, iter_causal_records

BBO_STREAMS = {"bbo", "bookTicker", "bbo-tbt"}
DEFAULT_WINDOWS_MS = (100, 500, 2000, 10000, 60000)


@dataclass
class _BookContribution:
    ts_ns: int
    replenish_bid: float = 0.0
    replenish_ask: float = 0.0
    deplete_bid: float = 0.0
    deplete_ask: float = 0.0
    add_count: int = 0
    remove_count: int = 0


class _RollingBookWindow:
    def __init__(self, window_ms: int):
        self.window_ns = int(window_ms) * 1_000_000
        self.rows: Deque[_BookContribution] = deque()
        self.replenish_bid = 0.0
        self.replenish_ask = 0.0
        self.deplete_bid = 0.0
        self.deplete_ask = 0.0
        self.add_count = 0
        self.remove_count = 0

    def add(self, c: _BookContribution) -> None:
        if not (c.replenish_bid or c.replenish_ask or c.deplete_bid or c.deplete_ask or c.add_count or c.remove_count):
            return
        self.rows.append(c)
        self.replenish_bid += c.replenish_bid
        self.replenish_ask += c.replenish_ask
        self.deplete_bid += c.deplete_bid
        self.deplete_ask += c.deplete_ask
        self.add_count += c.add_count
        self.remove_count += c.remove_count

    def trim(self, asof_ns: int) -> None:
        cutoff = int(asof_ns) - self.window_ns
        while self.rows and self.rows[0].ts_ns <= cutoff:
            c = self.rows.popleft()
            self.replenish_bid -= c.replenish_bid
            self.replenish_ask -= c.replenish_ask
            self.deplete_bid -= c.deplete_bid
            self.deplete_ask -= c.deplete_ask
            self.add_count -= c.add_count
            self.remove_count -= c.remove_count

    def features(self, asof_ns: int) -> Dict[str, float]:
        self.trim(asof_ns)
        dep_total = self.deplete_bid + self.deplete_ask
        rep_total = self.replenish_bid + self.replenish_ask
        depletion_imb = (self.deplete_ask - self.deplete_bid) / dep_total if dep_total > 0 else float("nan")
        replenish_imb = (self.replenish_bid - self.replenish_ask) / rep_total if rep_total > 0 else float("nan")
        pressure_den = dep_total + rep_total
        pressure = (self.deplete_ask + self.replenish_bid - self.deplete_bid - self.replenish_ask) / pressure_den if pressure_den > 0 else float("nan")
        seconds = self.window_ns / 1e9
        return {
            "remove_bid_notional": float(self.deplete_bid),
            "remove_ask_notional": float(self.deplete_ask),
            "remove_count": float(self.remove_count),
            "add_bid_notional": float(self.replenish_bid),
            "add_ask_notional": float(self.replenish_ask),
            "add_count": float(self.add_count),
            "book_depletion_imbalance": float(depletion_imb),
            "book_replenishment_imbalance": float(replenish_imb),
            "queue_pressure": float(pressure),
            "book_event_intensity": float((self.add_count + self.remove_count) / max(seconds, 1e-9)),
        }


@dataclass
class _TradeContribution:
    ts_ns: int
    price: float
    notional: float
    signed_notional: float
    individual: int
    aggregate: int


class _RollingTradeWindow:
    def __init__(self, window_ms: int):
        self.window_ns = int(window_ms) * 1_000_000
        self.rows: Deque[_TradeContribution] = deque()
        self.gross = 0.0
        self.signed = 0.0
        self.individual = 0
        self.aggregate = 0

    def add(self, c: _TradeContribution) -> None:
        self.rows.append(c)
        self.gross += c.notional
        self.signed += c.signed_notional
        self.individual += c.individual
        self.aggregate += c.aggregate

    def trim(self, asof_ns: int) -> None:
        cutoff = int(asof_ns) - self.window_ns
        while self.rows and self.rows[0].ts_ns <= cutoff:
            c = self.rows.popleft()
            self.gross -= c.notional
            self.signed -= c.signed_notional
            self.individual -= c.individual
            self.aggregate -= c.aggregate

    def features(self, asof_ns: int) -> Dict[str, float]:
        self.trim(asof_ns)
        count = len(self.rows)
        flow = self.signed / self.gross if self.gross > 0 else float("nan")
        if count >= 2 and self.rows[0].price > 0 and self.rows[-1].price > 0:
            impact_bps = 1e4 * math.log(self.rows[-1].price / self.rows[0].price)
            ts = np.asarray([r.ts_ns for r in self.rows], dtype=np.float64)
            dt = np.diff(ts) / 1e9
            interarrival_cv = float(np.std(dt) / np.mean(dt)) if len(dt) and np.mean(dt) > 0 else float("nan")
            absorption = abs(self.signed) / max(abs(impact_bps), 0.01)
        else:
            impact_bps = 0.0
            interarrival_cv = float("nan")
            absorption = float("nan")
        modality_total = self.individual + self.aggregate
        seconds = self.window_ns / 1e9
        return {
            "trade_count": float(count),
            "gross_notional": float(self.gross),
            "signed_notional": float(self.signed),
            "flow_imbalance": float(flow),
            "trades_per_second": float(count / max(seconds, 1e-9)),
            "impact_bps": float(impact_bps),
            "absorption_notional_per_bp": float(absorption),
            "interarrival_cv": float(interarrival_cv),
            "individual_fraction": float(self.individual / modality_total) if modality_total else float("nan"),
            "aggregate_fraction": float(self.aggregate / modality_total) if modality_total else float("nan"),
        }


class EventMicrostructureState:
    def __init__(self, venues: Sequence[str], symbols: Sequence[str], windows_ms: Sequence[int] = DEFAULT_WINDOWS_MS):
        self.venues = tuple(str(v).lower() for v in venues)
        self.symbols = tuple(str(s).upper() for s in symbols)
        self.windows_ms = tuple(sorted({int(x) for x in windows_ms}))
        self.levels: MutableMapping[Tuple[str, str, str, float], float] = {}
        self.snapshot_batches: Dict[Tuple[str, str, str], Tuple[int, int]] = {}
        self.book_windows = {(v, s, w): _RollingBookWindow(w) for v in self.venues for s in self.symbols for w in self.windows_ms}
        self.trade_windows = {(v, s, w): _RollingTradeWindow(w) for v in self.venues for s in self.symbols for w in self.windows_ms}
        self.cvd: Dict[Tuple[str, str], float] = defaultdict(float)
        self.latest_book_receive: Dict[Tuple[str, str], int] = defaultdict(int)
        self.latest_trade_receive: Dict[Tuple[str, str], int] = defaultdict(int)
        self.previous_flow: Dict[Tuple[str, str, int], float] = {}
        self.previous_acceleration: Dict[Tuple[str, str, int], float] = {}
        self.book_records = 0
        self.trade_records = 0

    def _reset_snapshot_batch(self, venue: str, symbol: str, source_stream: str, receive_ns: int, sequence_id: int) -> None:
        batch_key = (venue, symbol, source_stream)
        token = (int(receive_ns), int(sequence_id))
        if self.snapshot_batches.get(batch_key) == token:
            return
        self.levels = {
            key: value
            for key, value in self.levels.items()
            if not (key[0] == venue and key[1] == symbol)
        }
        self.snapshot_batches[batch_key] = token

    def ingest_book(self, row: Mapping[str, object]) -> None:
        venue = str(row.get("venue", "")).lower()
        symbol = str(row.get("symbol", "")).upper()
        if venue not in self.venues or symbol not in self.symbols:
            return
        receive_ns = int(row.get("receive_ts_ns", 0) or 0)
        source_stream = str(row.get("source_stream") or "")
        if source_stream in BBO_STREAMS:
            return
        side = str(row.get("side", ""))
        if side not in {"bid", "ask"}:
            return
        price = float(row.get("price", 0.0) or 0.0)
        qty = float(row.get("qty", 0.0) or 0.0)
        event_type = str(row.get("event_type", ""))
        sequence_id = int(row.get("sequence_id", 0) or 0)
        if price <= 0 or receive_ns <= 0:
            return

        if event_type == "snapshot":
            self._reset_snapshot_batch(venue, symbol, source_stream, receive_ns, sequence_id)
            self.levels[(venue, symbol, side, price)] = qty
            self.latest_book_receive[(venue, symbol)] = max(self.latest_book_receive[(venue, symbol)], receive_ns)
            self.book_records += 1
            return

        key = (venue, symbol, side, price)
        old = self.levels.get(key)
        contribution = _BookContribution(receive_ns)

        if event_type == "add":
            delta = max(qty, 0.0)
            self.levels[key] = qty
            if side == "bid":
                contribution.replenish_bid = delta * price
            else:
                contribution.replenish_ask = delta * price
            contribution.add_count = 1
        elif event_type in {"modify", "update"}:
            if old is not None:
                delta = qty - old
                if delta > 0:
                    if side == "bid":
                        contribution.replenish_bid = delta * price
                    else:
                        contribution.replenish_ask = delta * price
                    contribution.add_count = 1
                elif delta < 0:
                    removed = -delta * price
                    if side == "bid":
                        contribution.deplete_bid = removed
                    else:
                        contribution.deplete_ask = removed
                    contribution.remove_count = 1
            self.levels[key] = qty
        elif event_type in {"remove", "cancel"}:
            removed_qty = max(float(old or 0.0), 0.0)
            self.levels.pop(key, None)
            removed = removed_qty * price
            if side == "bid":
                contribution.deplete_bid = removed
            else:
                contribution.deplete_ask = removed
            contribution.remove_count = 1
        else:
            self.levels[key] = qty

        if contribution.add_count or contribution.remove_count:
            for window_ms in self.windows_ms:
                self.book_windows[(venue, symbol, window_ms)].add(contribution)
        self.latest_book_receive[(venue, symbol)] = max(self.latest_book_receive[(venue, symbol)], receive_ns)
        self.book_records += 1

    def ingest_trade(self, row: Mapping[str, object]) -> None:
        venue = str(row.get("venue", "")).lower()
        symbol = str(row.get("symbol", "")).upper()
        if venue not in self.venues or symbol not in self.symbols:
            return
        receive_ns = int(row.get("receive_ts_ns", 0) or 0)
        price = float(row.get("price", 0.0) or 0.0)
        qty = float(row.get("qty", 0.0) or 0.0)
        aggressor = str(row.get("aggressor", ""))
        if receive_ns <= 0 or price <= 0 or qty <= 0 or aggressor not in {"buy", "sell"}:
            return
        sign = 1.0 if aggressor == "buy" else -1.0
        notional = price * qty
        granularity = str(row.get("granularity") or "unknown")
        c = _TradeContribution(receive_ns, price, notional, sign * notional, int(granularity == "individual"), int(granularity == "aggregate"))
        for window_ms in self.windows_ms:
            self.trade_windows[(venue, symbol, window_ms)].add(c)
        self.cvd[(venue, symbol)] += c.signed_notional
        self.latest_trade_receive[(venue, symbol)] = max(self.latest_trade_receive[(venue, symbol)], receive_ns)
        self.trade_records += 1

    def row(self, asof_ns: int, symbol: str) -> Dict[str, object]:
        symbol = str(symbol).upper()
        out: Dict[str, object] = {"asof_ns": int(asof_ns), "symbol": symbol}
        cross_signed = defaultdict(float)
        latest_book = 0
        latest_trade = 0
        for venue in self.venues:
            prefix = venue + "__"
            book_ts = self.latest_book_receive[(venue, symbol)]
            trade_ts = self.latest_trade_receive[(venue, symbol)]
            out[prefix + "book_event_available_ts_ns"] = int(book_ts) if book_ts else np.nan
            out[prefix + "trade_available_ts_ns"] = int(trade_ts) if trade_ts else np.nan
            latest_book = max(latest_book, book_ts)
            latest_trade = max(latest_trade, trade_ts)
            out[prefix + "cvd"] = float(self.cvd[(venue, symbol)])
            for window_ms in self.windows_ms:
                book = self.book_windows[(venue, symbol, window_ms)].features(asof_ns)
                for name, value in book.items():
                    out[prefix + name + "_%sms" % window_ms] = value
                trade = self.trade_windows[(venue, symbol, window_ms)].features(asof_ns)
                for name, value in trade.items():
                    out[prefix + name + "_%sms" % window_ms] = value
                flow = float(trade["signed_notional"])
                key = (venue, symbol, window_ms)
                prev = self.previous_flow.get(key)
                acceleration = float("nan") if prev is None else flow - prev
                prev_acc = self.previous_acceleration.get(key)
                jerk = float("nan") if prev_acc is None or not np.isfinite(acceleration) else acceleration - prev_acc
                out[prefix + "flow_acceleration_%sms" % window_ms] = acceleration
                out[prefix + "flow_jerk_%sms" % window_ms] = jerk
                self.previous_flow[key] = flow
                if np.isfinite(acceleration):
                    self.previous_acceleration[key] = acceleration
                cross_signed[window_ms] += flow
        out["event__book_available_ts_ns"] = int(latest_book) if latest_book else np.nan
        out["event__trade_available_ts_ns"] = int(latest_trade) if latest_trade else np.nan
        for window_ms, value in cross_signed.items():
            out["event__cross_venue_signed_notional_%sms" % window_ms] = float(value)
        return out


def _next_or_none(iterator: Iterator[Mapping[str, object]]):
    try:
        return next(iterator)
    except StopIteration:
        return None


def build_event_microstructure_plane(base_tape: str, raw_root: str, out_dir: str, venues: Sequence[str], symbols: Sequence[str], windows_ms: Sequence[int] = DEFAULT_WINDOWS_MS, chunk_rows: int = 50000) -> Mapping[str, object]:
    start_ns, stop_ns = infer_run_window(base_tape)
    book_iter = iter_causal_records(raw_root, "book_events", start_ns, stop_ns, venues, symbols)
    trade_iter = iter_causal_records(raw_root, "trades", start_ns, stop_ns, venues, symbols)
    book = _next_or_none(book_iter)
    trade = _next_or_none(trade_iter)
    state = EventMicrostructureState(venues, symbols, windows_ms)
    writer = ChunkedPlaneWriter(out_dir, chunk_rows=chunk_rows)
    last_asof = -1
    for keys in iter_base_key_chunks(base_tape):
        for asof_ns, symbol in keys[["asof_ns", "symbol"]].itertuples(index=False, name=None):
            asof_ns = int(asof_ns)
            if asof_ns < last_asof:
                raise ValueError("base tape must be globally sorted by asof_ns")
            last_asof = asof_ns
            while book is not None and int(book.get("receive_ts_ns", 0) or 0) <= asof_ns:
                state.ingest_book(book)
                book = _next_or_none(book_iter)
            while trade is not None and int(trade.get("receive_ts_ns", 0) or 0) <= asof_ns:
                state.ingest_trade(trade)
                trade = _next_or_none(trade_iter)
            writer.append(state.row(asof_ns, str(symbol)))
    return writer.close({"plane": "event_microstructure", "start_ns": start_ns, "stop_ns": stop_ns, "book_records": state.book_records, "trade_records": state.trade_records, "windows_ms": list(state.windows_ms)})
