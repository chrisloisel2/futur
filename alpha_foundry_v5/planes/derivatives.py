from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Sequence, Tuple

import numpy as np

DEFAULT_LIQUIDATION_WINDOWS_MS = (1000, 5000, 30000, 60000, 300000)
STATE_KINDS = ("open_interest", "funding", "mark", "index", "premium")


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


@dataclass
class _Point:
    value: float
    receive_ns: int
    event_ns: int
    change: float = float("nan")
    acceleration: float = float("nan")
    next_funding_ns: Optional[int] = None


class _LiqWindow:
    def __init__(self, window_ns: int):
        self.window_ns = int(window_ns)
        self.rows = deque()
        self.long = self.short = self.other = 0.0

    def append(self, receive_ns: int, notional: float, side: str) -> None:
        self.rows.append((int(receive_ns), float(notional), str(side)))
        if side == "long":
            self.long += notional
        elif side == "short":
            self.short += notional
        else:
            self.other += notional

    def trim(self, asof_ns: int) -> None:
        cutoff = int(asof_ns) - self.window_ns
        while self.rows and self.rows[0][0] <= cutoff:
            _ts, value, side = self.rows.popleft()
            if side == "long":
                self.long -= value
            elif side == "short":
                self.short -= value
            else:
                self.other -= value

    def features(self, duration_s: float) -> Dict[str, float]:
        total = max(0.0, self.long + self.short + self.other)
        directional = self.long + self.short
        return {
            "liquidation_count": float(len(self.rows)),
            "liquidation_notional": total,
            "long_liquidation_notional": max(0.0, self.long),
            "short_liquidation_notional": max(0.0, self.short),
            "liquidation_intensity": float(len(self.rows)) / max(float(duration_s), 1e-9),
            "liquidation_imbalance": (self.short - self.long) / directional if directional > 0 else 0.0,
        }


class DerivativesPlane:
    """Causal derivative state; never sums unit-incompatible raw OI across venues."""

    def __init__(self, venues: Sequence[str], symbols: Sequence[str], liquidation_windows_ms: Sequence[int] = DEFAULT_LIQUIDATION_WINDOWS_MS):
        self.venues = tuple(str(v).lower() for v in venues)
        self.symbols = tuple(str(s).upper() for s in symbols)
        self.windows = tuple(sorted({int(x) for x in liquidation_windows_ms if int(x) > 0}))
        if not self.windows:
            raise ValueError("liquidation windows cannot be empty")
        self.state_points = {}  # type: Dict[Tuple[str, str, str], _Point]
        self.previous_change = {}
        self.emitted_clock = {}
        self.liq_windows = {}
        self.liq_clock = {}
        self.basis_previous = {}

    @staticmethod
    def _key(row: Mapping[str, object]) -> Tuple[str, str]:
        return str(row.get("venue", "")).lower(), str(row.get("symbol", "")).upper()

    def ingest(self, row: Mapping[str, object]) -> None:
        if str(row.get("_source_kind", "")) != "derivatives":
            return
        venue, symbol = self._key(row)
        if venue not in self.venues or symbol not in self.symbols:
            return
        kind = str(row.get("kind") or "")
        receive_ns = int(row.get("receive_ts_ns", 0) or 0)
        event_ns = int(row.get("event_ts_ns", 0) or 0)
        if receive_ns <= 0 or event_ns <= 0:
            return
        if kind == "liquidation":
            value = row.get("value")
            if not _finite(value) or float(value) < 0:
                return
            side = str(row.get("side") or "unknown").lower()
            self.liq_clock[(venue, symbol)] = max(receive_ns, self.liq_clock.get((venue, symbol), 0))
            for ms in self.windows:
                key = (venue, symbol, ms)
                window = self.liq_windows.setdefault(key, _LiqWindow(ms * 1_000_000))
                window.append(receive_ns, float(value), side)
            return
        if kind not in STATE_KINDS or not _finite(row.get("value")):
            return
        value = float(row["value"])
        key = (venue, symbol, kind)
        prev = self.state_points.get(key)
        change = float("nan")
        if prev is not None:
            change = value / prev.value - 1.0 if kind == "open_interest" and abs(prev.value) > 1e-18 else value - prev.value
        prev_change = self.previous_change.get(key)
        accel = float("nan") if prev_change is None or not _finite(change) else change - float(prev_change)
        if _finite(change):
            self.previous_change[key] = float(change)
        next_funding = None
        raw_next = row.get("next_funding_ts_ns")
        try:
            if raw_next not in (None, "") and int(raw_next) > 0:
                next_funding = int(raw_next)
        except (TypeError, ValueError):
            pass
        self.state_points[key] = _Point(value, receive_ns, event_ns, change, accel, next_funding)

    def advance(self, asof_ns: int) -> None:
        for window in self.liq_windows.values():
            window.trim(int(asof_ns))

    def _point(self, venue: str, symbol: str, kind: str) -> Optional[_Point]:
        return self.state_points.get((venue, symbol, kind))

    def state(self, asof_ns: int, symbol: str) -> Dict[str, object]:
        symbol = str(symbol).upper()
        out = {}  # type: Dict[str, object]
        oi_changes = []
        funding_values = []
        basis_values = []
        premium_values = []
        liq_30s = []
        clocks = []
        for venue in self.venues:
            prefix = venue + "__"
            for kind in STATE_KINDS:
                point = self._point(venue, symbol, kind)
                if point is None:
                    continue
                if point.receive_ns > int(asof_ns):
                    raise ValueError("derivative state from the future")
                out[prefix + kind] = point.value
                out[prefix + kind + "_available_ts_ns"] = point.receive_ns
                out[prefix + kind + "_receive_age_ms"] = (int(asof_ns) - point.receive_ns) / 1e6
                clocks.append(point.receive_ns)
                key = (venue, symbol, kind)
                is_new = point.receive_ns > int(self.emitted_clock.get(key, 0))
                if is_new and _finite(point.change):
                    if kind == "open_interest":
                        out[prefix + "open_interest_change_pct"] = point.change
                        out[prefix + "open_interest_event_change_pct"] = point.change
                        oi_changes.append(point.change)
                    else:
                        out[prefix + kind + "_event_change"] = point.change
                if is_new and _finite(point.acceleration):
                    name = "open_interest_acceleration_pct" if kind == "open_interest" else kind + "_event_acceleration"
                    out[prefix + name] = point.acceleration
                if is_new:
                    self.emitted_clock[key] = point.receive_ns
                if kind == "funding":
                    funding_values.append(point.value)
                    if point.next_funding_ns is not None:
                        out[prefix + "next_funding_ts_ns"] = point.next_funding_ns
                        out[prefix + "funding_clock_seconds"] = (point.next_funding_ns - int(asof_ns)) / 1e9
                        out[prefix + "funding_clock_available_ts_ns"] = point.receive_ns
                elif kind == "premium":
                    premium_values.append(point.value)

            mark = self._point(venue, symbol, "mark")
            index = self._point(venue, symbol, "index")
            if mark is not None and index is not None and mark.value > 0 and index.value > 0:
                basis = 1e4 * (mark.value / index.value - 1.0)
                clock = max(mark.receive_ns, index.receive_ns)
                out[prefix + "basis_bps"] = basis
                out[prefix + "basis_available_ts_ns"] = clock
                out[prefix + "basis_sync_span_ms"] = abs(mark.receive_ns - index.receive_ns) / 1e6
                clocks.append(clock)
                basis_values.append(basis)
                previous = self.basis_previous.get((venue, symbol))
                if previous is None:
                    self.basis_previous[(venue, symbol)] = (clock, basis, 0.0)
                elif clock > previous[0]:
                    velocity = basis - previous[1]
                    out[prefix + "basis_velocity"] = velocity
                    out[prefix + "basis_acceleration"] = velocity - previous[2]
                    self.basis_previous[(venue, symbol)] = (clock, basis, velocity)

            liq_clock = self.liq_clock.get((venue, symbol))
            if liq_clock is not None:
                out[prefix + "liquidation_available_ts_ns"] = liq_clock
                out[prefix + "liquidation_receive_age_ms"] = (int(asof_ns) - liq_clock) / 1e6
                clocks.append(liq_clock)
                for ms in self.windows:
                    window = self.liq_windows.get((venue, symbol, ms))
                    if window is None:
                        continue
                    features = window.features(ms / 1000.0)
                    for name, value in features.items():
                        out[prefix + name + "_%sms" % ms] = float(value)
                    out[prefix + "liquidation_total_usd_%sms" % ms] = float(features["liquidation_notional"])
                    if ms == 30000:
                        liq_30s.append(float(features["liquidation_notional"]))

        if oi_changes:
            arr = np.asarray(oi_changes, dtype=float)
            out["deriv__median_oi_change_pct"] = float(np.median(arr))
            out["deriv__oi_change_dispersion"] = float(np.std(arr))
        if len(funding_values) >= 2:
            out["deriv__funding_dispersion"] = float(np.std(np.asarray(funding_values, dtype=float)))
        if len(basis_values) >= 2:
            out["deriv__basis_dispersion_bps"] = float(np.std(np.asarray(basis_values, dtype=float)))
        if len(premium_values) >= 2:
            out["deriv__premium_dispersion"] = float(np.std(np.asarray(premium_values, dtype=float)))
        if liq_30s:
            out["deriv__liquidation_total_notional_30000ms"] = float(np.sum(liq_30s))
        if clocks:
            out["derivatives__available_ts_ns"] = int(max(clocks))
        return out
