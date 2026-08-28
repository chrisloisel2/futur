from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from market_physics_v3.collectors.normalize import BookDeltaState, parse_binance
from market_physics_v3.schema import BookEvent


class BinanceBootstrapError(RuntimeError):
    pass


@dataclass(frozen=True)
class BufferedDepthMessage:
    receive_ts_ns: int
    payload: Dict[str, object]


@dataclass(frozen=True)
class BinanceDepthSnapshot:
    symbol: str
    last_update_id: int
    event_ts_ns: int
    receive_ts_ns: int
    bids: Tuple[Tuple[float, float], ...]
    asks: Tuple[Tuple[float, float], ...]
    raw: Dict[str, object]


def fetch_depth_snapshot(
    symbol: str,
    base_url: str = "https://fapi.binance.com",
    limit: int = 1000,
    timeout_s: float = 10.0,
) -> BinanceDepthSnapshot:
    """Fetch the public USD-M order-book snapshot used by Binance bootstrap."""
    symbol = str(symbol).upper()
    if int(limit) not in {5, 10, 20, 50, 100, 500, 1000}:
        raise ValueError("unsupported Binance depth limit")
    url = base_url.rstrip("/") + "/fapi/v1/depth?" + urlencode({"symbol": symbol, "limit": int(limit)})
    request = Request(url, headers={"User-Agent": "market-physics-v3/1"})
    with urlopen(request, timeout=float(timeout_s)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    receive_ns = time.time_ns()
    if "lastUpdateId" not in payload:
        raise BinanceBootstrapError("Binance snapshot missing lastUpdateId")
    event_ms = int(payload.get("T") or payload.get("E") or receive_ns // 1_000_000)
    event_ns = event_ms * 1_000_000
    if event_ns > receive_ns:
        event_ns = receive_ns
    bids = tuple((float(x[0]), float(x[1])) for x in payload.get("bids", []))
    asks = tuple((float(x[0]), float(x[1])) for x in payload.get("asks", []))
    if not bids or not asks:
        raise BinanceBootstrapError("Binance snapshot has empty side")
    return BinanceDepthSnapshot(
        symbol=symbol,
        last_update_id=int(payload["lastUpdateId"]),
        event_ts_ns=int(event_ns),
        receive_ts_ns=int(receive_ns),
        bids=bids,
        asks=asks,
        raw=payload,
    )


def snapshot_events(snapshot: BinanceDepthSnapshot) -> List[BookEvent]:
    out: List[BookEvent] = []
    for side, rows in (("bid", snapshot.bids), ("ask", snapshot.asks)):
        for price, qty in rows:
            out.append(BookEvent(
                venue="binance",
                symbol=snapshot.symbol,
                event_ts_ns=int(snapshot.event_ts_ns),
                receive_ts_ns=int(snapshot.receive_ts_ns),
                sequence_id=int(snapshot.last_update_id),
                event_type="snapshot",
                side=side,
                price=float(price),
                qty=float(qty),
                source_stream="depth_snapshot_rest",
                first_sequence_id=int(snapshot.last_update_id),
            ))
    return out


def align_buffer(
    snapshot: BinanceDepthSnapshot,
    buffered: Sequence[BufferedDepthMessage],
) -> List[BufferedDepthMessage]:
    """Align buffered diff-depth messages to a REST snapshot.

    This implements the USD-M documented rules:
    - discard messages whose final update id u is below lastUpdateId;
    - first processed message must satisfy U <= lastUpdateId <= u;
    - every following message must have pu == previous u.
    """
    candidates = []
    for item in buffered:
        payload = item.payload
        if payload.get("e") != "depthUpdate":
            continue
        if int(payload.get("u", -1)) < int(snapshot.last_update_id):
            continue
        candidates.append(item)
    if not candidates:
        raise BinanceBootstrapError("no buffered depth event overlaps snapshot")

    start = None
    for i, item in enumerate(candidates):
        p = item.payload
        first = int(p.get("U", p.get("u", -1)))
        final = int(p.get("u", -1))
        if first <= int(snapshot.last_update_id) <= final:
            start = i
            break
    if start is None:
        raise BinanceBootstrapError("buffer does not bridge snapshot lastUpdateId")

    aligned = list(candidates[start:])
    previous_u = None
    for i, item in enumerate(aligned):
        payload = item.payload
        u = int(payload["u"])
        pu = payload.get("pu")
        if i > 0 and pu is not None and int(pu) != int(previous_u):
            raise BinanceBootstrapError(
                "Binance buffered sequence gap: pu=%s expected=%s" % (pu, previous_u)
            )
        previous_u = u
    return aligned


def seed_delta_state(snapshot: BinanceDepthSnapshot, state: BookDeltaState) -> None:
    """Seed parser classification state from the REST snapshot."""
    state.reset_snapshot("binance", snapshot.symbol)
    for price, qty in snapshot.bids:
        state.levels[("binance", snapshot.symbol, "bid", float(price))] = float(qty)
    for price, qty in snapshot.asks:
        state.levels[("binance", snapshot.symbol, "ask", float(price))] = float(qty)


def normalized_bootstrap_events(
    snapshot: BinanceDepthSnapshot,
    buffered: Sequence[BufferedDepthMessage],
    state: BookDeltaState,
) -> List[BookEvent]:
    """Return snapshot plus aligned buffered deltas at their true availability.

    Buffered websocket messages may have arrived before the REST snapshot. They
    were not usable for a coherent book until the snapshot was received, so the
    normalized receive_ts_ns for replay is clamped to snapshot.receive_ts_ns.
    Raw-wire capture retains the original wire receive timestamp separately.
    """
    aligned = align_buffer(snapshot, buffered)
    seed_delta_state(snapshot, state)
    out = snapshot_events(snapshot)
    first = aligned[0].payload
    first_pu = first.get("pu")
    state.sequence[("binance", snapshot.symbol, "depth")] = int(
        first_pu if first_pu is not None else snapshot.last_update_id
    )
    for item in aligned:
        availability_ns = max(int(item.receive_ts_ns), int(snapshot.receive_ts_ns))
        events = parse_binance(item.payload, availability_ns, state)
        out.extend([x for x in events if isinstance(x, BookEvent)])
    return out
