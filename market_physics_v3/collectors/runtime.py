from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

from market_physics_v3.collectors.normalize import BookDeltaState, PARSERS, SequenceGap
from market_physics_v3.collectors.specs import subscriptions
from market_physics_v3.collectors.writer import AppendOnlyEventWriter, RawMessageWriter


class CollectorHealth:
    def __init__(self, venue: str):
        self.venue = venue
        self.connected = False
        self.reconnects = 0
        self.messages = 0
        self.events = 0
        self.parse_errors = 0
        self.sequence_gaps = 0
        self.subscription_acks = 0
        self.subscription_errors = 0
        self.last_receive_ns = 0
        self.last_event_ns = 0
        self.last_exception = None

    def as_dict(self) -> Dict[str, object]:
        now = time.time_ns()
        return {
            "venue": self.venue,
            "connected": self.connected,
            "reconnects": self.reconnects,
            "messages": self.messages,
            "events": self.events,
            "parse_errors": self.parse_errors,
            "sequence_gaps": self.sequence_gaps,
            "subscription_acks": self.subscription_acks,
            "subscription_errors": self.subscription_errors,
            "last_receive_ns": self.last_receive_ns,
            "last_event_ns": self.last_event_ns,
            "idle_ms": (now - self.last_receive_ns) / 1e6 if self.last_receive_ns else None,
            "last_exception": self.last_exception,
        }


def _write_health(path: Optional[Path], health: CollectorHealth) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(health.as_dict(), indent=2, sort_keys=True))
    tmp.replace(path)


def _control_status(venue: str, msg: object):
    if not isinstance(msg, dict):
        return None
    if venue == "binance" and "id" in msg and "result" in msg:
        return "ack" if msg.get("result") is None else "error"
    if venue == "bybit" and (msg.get("op") == "subscribe" or "success" in msg):
        return "ack" if msg.get("success", True) else "error"
    if venue == "okx" and msg.get("event") in {"subscribe", "error"}:
        return "ack" if msg.get("event") == "subscribe" else "error"
    if venue == "hyperliquid" and msg.get("channel") == "subscriptionResponse":
        return "ack"
    return None


async def _heartbeat(ws, venue: str, stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.sleep(20)
        if stop.is_set():
            return
        if venue == "bybit":
            await ws.send(json.dumps({"op": "ping"}))
        elif venue == "hyperliquid":
            await ws.send(json.dumps({"method": "ping"}))
        elif venue == "okx":
            await ws.send("ping")
        else:
            # Binance uses protocol-level ping frames; websockets normally handles
            # them, but an explicit ping gives us a fast liveness failure too.
            pong = await ws.ping()
            await asyncio.wait_for(pong, timeout=10)


async def run_venue(
    venue: str,
    symbols: Iterable[str],
    root: str = "data/market_physics_v3",
    health_dir: str = "reports/market_physics_v3/health",
    max_backoff_s: float = 30.0,
) -> None:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError(
            "Market Physics live collectors require websockets>=12,<14 in a dedicated research environment"
        ) from exc

    venue = venue.lower()
    spec = subscriptions(venue, symbols)
    parser = PARSERS[venue]
    writer = AppendOnlyEventWriter(root)
    raw_writer = RawMessageWriter(root)
    health = CollectorHealth(venue)
    health_path = Path(health_dir) / (venue + ".json")
    backoff = 1.0

    try:
        while True:
            # Incremental state is connection-local. A reconnect or sequence gap
            # invalidates it; never carry a stale book across sessions.
            state = BookDeltaState()
            heartbeat_stop = asyncio.Event()
            try:
                async with websockets.connect(
                    spec["url"],
                    ping_interval=20 if venue == "binance" else None,
                    ping_timeout=20,
                    close_timeout=10,
                    max_queue=8192,
                ) as ws:
                    connection_id = "%s-%s" % (venue, time.time_ns())
                    health.connected = True
                    health.last_exception = None
                    _write_health(health_path, health)
                    if "subscribe" in spec:
                        await ws.send(json.dumps(spec["subscribe"]))
                    else:
                        for subscription in spec.get("subscribe_many", []):
                            await ws.send(json.dumps(subscription))
                    hb = asyncio.create_task(_heartbeat(ws, venue, heartbeat_stop))
                    backoff = 1.0
                    try:
                        async for raw in ws:
                            receive_ns = time.time_ns()
                            health.messages += 1
                            health.last_receive_ns = receive_ns
                            if raw == "pong":
                                continue
                            parsed = None
                            try:
                                parsed = json.loads(raw)
                                raw_writer.append(venue, receive_ns, parsed, connection_id)
                                control = _control_status(venue, parsed)
                                if control == "ack":
                                    health.subscription_acks += 1
                                elif control == "error":
                                    health.subscription_errors += 1
                                events = parser(parsed, receive_ns, state)
                                for event in events:
                                    writer.append(event)
                                    health.events += 1
                                    health.last_event_ns = max(health.last_event_ns, event.event_ts_ns)
                            except SequenceGap as exc:
                                health.parse_errors += 1
                                health.sequence_gaps += 1
                                health.last_exception = str(exc)
                                raw_writer.dead_letter(venue, receive_ns, parsed if parsed is not None else raw, exc)
                                # Fail closed: force reconnect and reconstruct venue state.
                                raise
                            except Exception as exc:
                                health.parse_errors += 1
                                health.last_exception = str(exc)
                                raw_writer.dead_letter(venue, receive_ns, parsed if parsed is not None else raw, exc)
                            if health.messages % 100 == 0:
                                _write_health(health_path, health)
                    finally:
                        heartbeat_stop.set()
                        hb.cancel()
                        health.connected = False
                        _write_health(health_path, health)
            except asyncio.CancelledError:
                health.connected = False
                _write_health(health_path, health)
                raise
            except Exception as exc:
                health.connected = False
                health.reconnects += 1
                health.last_exception = str(exc)
                _write_health(health_path, health)
                await asyncio.sleep(backoff + random.random() * min(1.0, backoff * 0.1))
                backoff = min(max_backoff_s, backoff * 2.0)
    finally:
        writer.close()
        raw_writer.close()
        health.connected = False
        _write_health(health_path, health)


async def run_many(venues: Iterable[str], symbols: Iterable[str], root: str, health_dir: str) -> None:
    tasks = [asyncio.create_task(run_venue(v, symbols, root, health_dir)) for v in venues]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
