from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Mapping, Optional, Sequence, Tuple

import numpy as np


DEFAULT_TIME_WINDOWS_MS = (100, 500, 2000, 10000, 60000)
DEFAULT_EVENT_WINDOWS = (10, 50, 250)
ACTION_TYPES = ("add", "modify", "update", "remove", "cancel")
SIDES = ("bid", "ask")
BBO_STREAMS = {"bbo", "bookTicker", "bbo-tbt", "books5"}


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _safe_float(value, default=float("nan")) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return out if math.isfinite(out) else float(default)


@dataclass
class _BookBin:
    counts: Dict[str, float] = field(default_factory=lambda: defaultdict(float))

    def observe(self, side: str, event_type: str) -> None:
        if side not in SIDES or event_type not in ACTION_TYPES:
            return
        self.counts["%s_%s" % (side, event_type)] += 1.0
        self.counts["book_event_count"] += 1.0


class _RollingBookWindow:
    def __init__(self, bins: int):
        self.max_bins = max(1, int(bins))
        self.rows = deque()  # type: Deque[Dict[str, float]]
        self.total = defaultdict(float)  # type: Dict[str, float]

    def push(self, counts: Mapping[str, float]) -> None:
        row = dict(counts)
        self.rows.append(row)
        for key, value in row.items():
            self.total[key] += float(value)
        while len(self.rows) > self.max_bins:
            old = self.rows.popleft()
            for key, value in old.items():
                self.total[key] -= float(value)
                if abs(self.total[key]) < 1e-15:
                    self.total[key] = 0.0

    def feature_dict(self, duration_s: float) -> Dict[str, float]:
        duration = max(float(duration_s), 1e-9)
        out = {"book_event_count": float(self.total.get("book_event_count", 0.0))}
        for side in SIDES:
            for event_type in ACTION_TYPES:
                count = float(self.total.get("%s_%s" % (side, event_type), 0.0))
                out["%s_%s_count" % (side, event_type)] = count
                out["%s_%s_intensity" % (side, event_type)] = count / duration
        add_bid = out["bid_add_count"]
        add_ask = out["ask_add_count"]
        remove_bid = out["bid_remove_count"]
        remove_ask = out["ask_remove_count"]
        cancel_bid = out["bid_cancel_count"]
        cancel_ask = out["ask_cancel_count"]
        add_den = add_bid + add_ask
        remove_den = remove_bid + remove_ask
        cancel_den = cancel_bid + cancel_ask
        depletion_den = remove_bid + remove_ask + cancel_bid + cancel_ask
        out["replenishment_imbalance"] = (add_bid - add_ask) / add_den if add_den > 0 else 0.0
        out["removal_imbalance"] = (remove_ask - remove_bid) / remove_den if remove_den > 0 else 0.0
        out["cancellation_imbalance"] = (
            (cancel_ask - cancel_bid) / cancel_den if cancel_den > 0 else float("nan")
        )
        out["depletion_pressure"] = (
            (remove_ask + cancel_ask - remove_bid - cancel_bid) / depletion_den
            if depletion_den > 0
            else 0.0
        )
        out["book_event_intensity"] = out["book_event_count"] / duration
        return out


@dataclass
class _TradePoint:
    ts_ns: int
    price: float
    notional: float
    sign: float
    granularity: str


class _TradeWindow:
    def __init__(self, window_ns: int):
        self.window_ns = max(1, int(window_ns))
        self.rows = deque()  # type: Deque[_TradePoint]
        self.gross = 0.0
        self.signed = 0.0
        self.aggregate_count = 0
        self.individual_count = 0
        self.sum_dt = 0.0
        self.sum_dt2 = 0.0
        self.n_dt = 0

    def append(self, point: _TradePoint) -> None:
        if self.rows:
            dt = max(0.0, (int(point.ts_ns) - int(self.rows[-1].ts_ns)) / 1e9)
            self.sum_dt += dt
            self.sum_dt2 += dt * dt
            self.n_dt += 1
        self.rows.append(point)
        self.gross += float(point.notional)
        self.signed += float(point.notional) * float(point.sign)
        if point.granularity == "aggregate":
            self.aggregate_count += 1
        elif point.granularity == "individual":
            self.individual_count += 1

    def trim(self, asof_ns: int) -> None:
        cutoff = int(asof_ns) - int(self.window_ns)
        while self.rows and int(self.rows[0].ts_ns) <= cutoff:
            old = self.rows.popleft()
            self.gross -= float(old.notional)
            self.signed -= float(old.notional) * float(old.sign)
            if old.granularity == "aggregate":
                self.aggregate_count -= 1
            elif old.granularity == "individual":
                self.individual_count -= 1
            if self.rows:
                dt = max(0.0, (int(self.rows[0].ts_ns) - int(old.ts_ns)) / 1e9)
                self.sum_dt -= dt
                self.sum_dt2 -= dt * dt
                self.n_dt -= 1
        if not self.rows:
            self.gross = 0.0
            self.signed = 0.0
            self.aggregate_count = 0
            self.individual_count = 0
            self.sum_dt = 0.0
            self.sum_dt2 = 0.0
            self.n_dt = 0

    def features(self, duration_s: float) -> Dict[str, float]:
        n = len(self.rows)
        duration = max(float(duration_s), 1e-9)
        flow = float(self.signed / self.gross) if self.gross > 0 else 0.0
        impact = 0.0
        if n >= 2 and self.rows[0].price > 0 and self.rows[-1].price > 0:
            impact = 1e4 * math.log(float(self.rows[-1].price) / float(self.rows[0].price))
        if self.n_dt > 0:
            mean_dt = self.sum_dt / float(self.n_dt)
            var_dt = max(0.0, self.sum_dt2 / float(self.n_dt) - mean_dt * mean_dt)
            interarrival_cv = math.sqrt(var_dt) / mean_dt if mean_dt > 0 else float("nan")
        else:
            interarrival_cv = float("nan")
        classified = self.aggregate_count + self.individual_count
        return {
            "trade_count": float(n),
            "gross_notional": float(max(0.0, self.gross)),
            "signed_notional": float(self.signed),
            "flow_imbalance": flow,
            "trades_per_second": float(n) / duration,
            "interarrival_cv": float(interarrival_cv),
            "impact_bps": float(impact),
            "absorption": float(abs(self.signed) / (abs(impact) + 1.0)),
            "aggregate_fraction": (
                float(self.aggregate_count) / float(classified) if classified > 0 else float("nan")
            ),
            "individual_fraction": (
                float(self.individual_count) / float(classified) if classified > 0 else float("nan")
            ),
        }


class EventTradePlane:
    """Causal event/trade state on a fixed research grid.

    Book action rates are aggregated into grid bins so tens of millions of L2
    events do not get multiplied by every rolling window. Trade windows remain
    event-level because trade volume is much smaller and event-count windows are
    economically meaningful.
    """

    def __init__(
        self,
        cadence_ms: int,
        venues: Sequence[str],
        symbols: Sequence[str],
        time_windows_ms: Sequence[int] = DEFAULT_TIME_WINDOWS_MS,
        event_windows: Sequence[int] = DEFAULT_EVENT_WINDOWS,
    ):
        self.cadence_ms = int(cadence_ms)
        if self.cadence_ms <= 0:
            raise ValueError("cadence_ms must be positive")
        self.venues = tuple(str(v).lower() for v in venues)
        self.symbols = tuple(str(s).upper() for s in symbols)
        self.time_windows_ms = tuple(sorted({int(x) for x in time_windows_ms if int(x) > 0}))
        self.event_windows = tuple(sorted({int(x) for x in event_windows if int(x) > 0}))
        if not self.time_windows_ms or not self.event_windows:
            raise ValueError("time and event windows cannot be empty")
        for window_ms in self.time_windows_ms:
            if window_ms % self.cadence_ms != 0:
                raise ValueError("time windows must be exact multiples of cadence")
        self._current_book_bins = defaultdict(_BookBin)  # type: Dict[Tuple[str, str], _BookBin]
        self._book_windows = {}  # type: Dict[Tuple[str, str, int], _RollingBookWindow]
        self._trade_windows = {}  # type: Dict[Tuple[str, str, int], _TradeWindow]
        self._last_trades = defaultdict(deque)  # type: Dict[Tuple[str, str], Deque[_TradePoint]]
        self._event_cache = {}  # type: Dict[Tuple[str, str, int], Tuple[int, Dict[str, float]]]
        self._trade_version = defaultdict(int)  # type: Dict[Tuple[str, str], int]
        self._cvd = defaultdict(float)  # type: Dict[Tuple[str, str], float]
        self._trade_available = {}  # type: Dict[Tuple[str, str], int]
        self._book_available = {}  # type: Dict[Tuple[str, str], int]
        self._previous_flow = {}  # type: Dict[Tuple[str, str, int], float]
        self._previous_accel = {}  # type: Dict[Tuple[str, str, int], float]

    def _key(self, row: Mapping[str, object]) -> Tuple[str, str]:
        return str(row.get("venue", "")).lower(), str(row.get("symbol", "")).upper()

    def ingest(self, row: Mapping[str, object]) -> None:
        kind = str(row.get("_source_kind", ""))
        key = self._key(row)
        if key[0] not in self.venues or key[1] not in self.symbols:
            return
        receive_ns = int(row.get("receive_ts_ns", 0) or 0)
        if receive_ns <= 0:
            return
        if kind == "book_events":
            source_stream = str(row.get("source_stream") or "")
            event_type = str(row.get("event_type") or "")
            side = str(row.get("side") or "")
            if source_stream not in BBO_STREAMS:
                self._book_available[key] = max(receive_ns, self._book_available.get(key, 0))
            if event_type in ACTION_TYPES and event_type != "snapshot" and source_stream not in BBO_STREAMS:
                self._current_book_bins[key].observe(side, event_type)
            return
        if kind != "trades":
            return
        price = _safe_float(row.get("price"))
        qty = _safe_float(row.get("qty"))
        if not (_finite(price) and _finite(qty)) or price <= 0 or qty <= 0:
            return
        aggressor = str(row.get("aggressor") or "").lower()
        if aggressor not in {"buy", "sell"}:
            return
        point = _TradePoint(
            receive_ns,
            float(price),
            float(price * qty),
            1.0 if aggressor == "buy" else -1.0,
            str(row.get("granularity") or "unknown"),
        )
        self._trade_available[key] = max(receive_ns, self._trade_available.get(key, 0))
        self._cvd[key] += point.notional * point.sign
        for window_ms in self.time_windows_ms:
            window_key = (key[0], key[1], int(window_ms))
            window = self._trade_windows.get(window_key)
            if window is None:
                window = _TradeWindow(int(window_ms) * 1_000_000)
                self._trade_windows[window_key] = window
            window.append(point)
        last = self._last_trades[key]
        last.append(point)
        while len(last) > max(self.event_windows):
            last.popleft()
        self._trade_version[key] += 1

    def advance(self, asof_ns: int) -> None:
        """Close the current grid bin exactly once after ingesting <= asof."""
        for venue in self.venues:
            for symbol in self.symbols:
                key = (venue, symbol)
                counts = self._current_book_bins[key].counts
                for window_ms in self.time_windows_ms:
                    bins = int(window_ms // self.cadence_ms)
                    window_key = (venue, symbol, int(window_ms))
                    window = self._book_windows.get(window_key)
                    if window is None:
                        window = _RollingBookWindow(bins)
                        self._book_windows[window_key] = window
                    window.push(counts)
                    trade_window = self._trade_windows.get(window_key)
                    if trade_window is not None:
                        trade_window.trim(int(asof_ns))
                self._current_book_bins[key] = _BookBin()

    def _event_window_features(self, key: Tuple[str, str], n: int) -> Dict[str, float]:
        version = int(self._trade_version[key])
        cache_key = (key[0], key[1], int(n))
        cached = self._event_cache.get(cache_key)
        if cached is not None and int(cached[0]) == version:
            return dict(cached[1])
        rows = list(self._last_trades[key])[-int(n):]
        if not rows:
            out = {
                "trade_count": 0.0,
                "gross_notional": 0.0,
                "signed_notional": 0.0,
                "flow_imbalance": 0.0,
                "trade_size_entropy": float("nan"),
                "large_trade_fraction": float("nan"),
                "interarrival_cv": float("nan"),
                "impact_bps": 0.0,
                "absorption": 0.0,
            }
        else:
            notionals = np.asarray([x.notional for x in rows], dtype=float)
            signs = np.asarray([x.sign for x in rows], dtype=float)
            gross = float(np.sum(notionals))
            signed = float(np.sum(notionals * signs))
            weights = notionals / gross if gross > 0 else np.zeros_like(notionals)
            positive = weights > 0
            entropy = float(-np.sum(weights[positive] * np.log(weights[positive]))) if np.any(positive) else float("nan")
            q90 = float(np.quantile(notionals, 0.90)) if len(notionals) else float("nan")
            ts = np.asarray([x.ts_ns for x in rows], dtype=np.int64)
            if len(ts) >= 3:
                d = np.diff(ts.astype(float)) / 1e9
                mean_d = float(np.mean(d))
                cv = float(np.std(d) / mean_d) if mean_d > 0 else float("nan")
            else:
                cv = float("nan")
            impact = 0.0
            if len(rows) >= 2 and rows[0].price > 0 and rows[-1].price > 0:
                impact = 1e4 * math.log(rows[-1].price / rows[0].price)
            flow = signed / gross if gross > 0 else 0.0
            out = {
                "trade_count": float(len(rows)),
                "gross_notional": gross,
                "signed_notional": signed,
                "flow_imbalance": float(flow),
                "trade_size_entropy": entropy,
                "large_trade_fraction": float(np.mean(notionals >= q90)) if _finite(q90) else float("nan"),
                "interarrival_cv": cv,
                "impact_bps": float(impact),
                "absorption": float(abs(signed) / (abs(impact) + 1.0)),
            }
        self._event_cache[cache_key] = (version, dict(out))
        return out

    def state(self, asof_ns: int, symbol: str) -> Dict[str, object]:
        symbol = str(symbol).upper()
        out = {}  # type: Dict[str, object]
        cross_signed = defaultdict(float)
        cross_gross = defaultdict(float)
        clocks = []
        for venue in self.venues:
            key = (venue, symbol)
            prefix = venue + "__"
            trade_clock = self._trade_available.get(key)
            book_clock = self._book_available.get(key)
            if trade_clock is not None:
                out[prefix + "trade_available_ts_ns"] = int(trade_clock)
                out[prefix + "trade_receive_age_ms"] = float((int(asof_ns) - int(trade_clock)) / 1e6)
                clocks.append(int(trade_clock))
            if book_clock is not None:
                out[prefix + "book_event_available_ts_ns"] = int(book_clock)
                out[prefix + "book_event_receive_age_ms"] = float((int(asof_ns) - int(book_clock)) / 1e6)
                clocks.append(int(book_clock))
            out[prefix + "cvd"] = float(self._cvd[key])
            for window_ms in self.time_windows_ms:
                window_key = (venue, symbol, int(window_ms))
                book_window = self._book_windows.get(window_key)
                if book_window is not None and book_clock is not None:
                    for name, value in book_window.feature_dict(float(window_ms) / 1000.0).items():
                        out[prefix + name + "_%sms" % window_ms] = float(value)
                trade_window = self._trade_windows.get(window_key)
                if trade_window is not None and trade_clock is not None:
                    tf = trade_window.features(float(window_ms) / 1000.0)
                    for name, value in tf.items():
                        out[prefix + name + "_%sms" % window_ms] = float(value)
                    signed = float(tf["signed_notional"])
                    gross = float(tf["gross_notional"])
                    cross_signed[int(window_ms)] += signed
                    cross_gross[int(window_ms)] += gross
                    flow_key = (venue, symbol, int(window_ms))
                    previous = self._previous_flow.get(flow_key)
                    accel = float("nan") if previous is None else signed - float(previous)
                    previous_accel = self._previous_accel.get(flow_key)
                    jerk = (
                        float("nan")
                        if previous_accel is None or not _finite(accel)
                        else float(accel) - float(previous_accel)
                    )
                    out[prefix + "flow_acceleration_%sms" % window_ms] = accel
                    out[prefix + "flow_jerk_%sms" % window_ms] = jerk
                    self._previous_flow[flow_key] = signed
                    if _finite(accel):
                        self._previous_accel[flow_key] = float(accel)
            if trade_clock is not None:
                for n in self.event_windows:
                    ef = self._event_window_features(key, int(n))
                    for name, value in ef.items():
                        out[prefix + name + "_last%s" % n] = float(value)
        for window_ms in self.time_windows_ms:
            gross = float(cross_gross.get(int(window_ms), 0.0))
            signed = float(cross_signed.get(int(window_ms), 0.0))
            if gross > 0:
                out["event__cross_venue_signed_notional_%sms" % window_ms] = signed
                out["event__cross_venue_gross_notional_%sms" % window_ms] = gross
                out["event__cross_venue_flow_imbalance_%sms" % window_ms] = signed / gross
        if clocks:
            out["event_trade__available_ts_ns"] = int(max(clocks))
        return out
