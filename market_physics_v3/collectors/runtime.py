from __future__ import annotations

import asyncio
import json
import os
import random
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

from market_physics_v3.collectors.binance_bootstrap import (
    BinanceBootstrapError,
    BufferedDepthMessage,
    fetch_depth_snapshot,
    normalized_bootstrap_events,
)
from market_physics_v3.collectors.normalize import BookDeltaState, PARSERS, SequenceGap, canonical_symbol
from market_physics_v3.collectors.specs import subscriptions
from market_physics_v3.collectors.writer import AppendOnlyEventWriter, RawMessageWriter
from market_physics_v3.schema import BookEvent, DerivativeEvent, TradeEvent


DEFAULT_FRESH_EVENT_MAX_LAG_MS = 5000.0
BINANCE_MAX_BUFFER_MESSAGES = 20_000


class CollectorHealth:
    def __init__(self, venue: str, fresh_event_max_lag_ms: float = DEFAULT_FRESH_EVENT_MAX_LAG_MS):
        self.venue = venue
        self.fresh_event_max_lag_ms = float(fresh_event_max_lag_ms)
        self.started_ns = time.time_ns()
        self.stopped_ns = 0
        self.clean_shutdown = False
        self.connected = False
        self._active_connections = set()
        self.reconnects = 0
        self.messages = 0
        self.events = 0
        self.book_events = 0
        self.trade_events = 0
        self.derivative_events = 0
        self.fresh_events = 0
        self.stale_events = 0
        self.fresh_book_events = 0
        self.fresh_trade_events = 0
        self.fresh_derivative_events = 0
        self.stale_book_events = 0
        self.stale_trade_events = 0
        self.stale_derivative_events = 0
        self.max_book_lag_ms = 0.0
        self.max_trade_lag_ms = 0.0
        self.max_derivative_lag_ms = 0.0
        self.parse_errors = 0
        self.sequence_gaps = 0
        self.subscription_acks = 0
        self.subscription_errors = 0
        self.last_receive_ns = 0
        self.last_event_ns = 0
        self.last_exception = None
        self.book_bootstrap_successes = 0
        self.book_bootstrap_errors = 0
        self.book_bootstrap_buffer_resets = 0
        self.book_bootstrapped_symbols = set()
        self.last_book_bootstrap_exception = None

    def connection_open(self, name: str) -> None:
        self._active_connections.add(str(name))
        self.connected = True

    def connection_close(self, name: str) -> None:
        self._active_connections.discard(str(name))
        self.connected = bool(self._active_connections)

    def observe_event(self, event) -> None:
        self.events += 1
        self.last_event_ns = max(self.last_event_ns, int(event.event_ts_ns))
        lag_ms = max(0.0, (int(event.receive_ts_ns) - int(event.event_ts_ns)) / 1e6)
        fresh = lag_ms <= self.fresh_event_max_lag_ms
        if fresh:
            self.fresh_events += 1
        else:
            self.stale_events += 1

        if isinstance(event, BookEvent):
            self.book_events += 1
            self.max_book_lag_ms = max(self.max_book_lag_ms, lag_ms)
            if fresh:
                self.fresh_book_events += 1
            else:
                self.stale_book_events += 1
        elif isinstance(event, TradeEvent):
            self.trade_events += 1
            self.max_trade_lag_ms = max(self.max_trade_lag_ms, lag_ms)
            if fresh:
                self.fresh_trade_events += 1
            else:
                self.stale_trade_events += 1
        elif isinstance(event, DerivativeEvent):
            self.derivative_events += 1
            self.max_derivative_lag_ms = max(self.max_derivative_lag_ms, lag_ms)
            if fresh:
                self.fresh_derivative_events += 1
            else:
                self.stale_derivative_events += 1

    def as_dict(self) -> Dict[str, object]:
        now = time.time_ns()
        return {
            "venue": self.venue,
            "fresh_event_max_lag_ms": self.fresh_event_max_lag_ms,
            "started_ns": self.started_ns,
            "stopped_ns": self.stopped_ns,
            "clean_shutdown": self.clean_shutdown,
            "connected": self.connected,
            "active_connections": sorted(self._active_connections),
            "reconnects": self.reconnects,
            "messages": self.messages,
            "events": self.events,
            "book_events": self.book_events,
            "trade_events": self.trade_events,
            "derivative_events": self.derivative_events,
            "fresh_events": self.fresh_events,
            "stale_events": self.stale_events,
            "fresh_book_events": self.fresh_book_events,
            "fresh_trade_events": self.fresh_trade_events,
            "fresh_derivative_events": self.fresh_derivative_events,
            "stale_book_events": self.stale_book_events,
            "stale_trade_events": self.stale_trade_events,
            "stale_derivative_events": self.stale_derivative_events,
            "max_book_lag_ms": self.max_book_lag_ms,
            "max_trade_lag_ms": self.max_trade_lag_ms,
            "max_derivative_lag_ms": self.max_derivative_lag_ms,
            "parse_errors": self.parse_errors,
            "sequence_gaps": self.sequence_gaps,
            "subscription_acks": self.subscription_acks,
            "subscription_errors": self.subscription_errors,
            "last_receive_ns": self.last_receive_ns,
            "last_event_ns": self.last_event_ns,
            "idle_ms": (now - self.last_receive_ns) / 1e6 if self.last_receive_ns else None,
            "last_exception": self.last_exception,
            "book_bootstrap_successes": int(self.book_bootstrap_successes),
            "book_bootstrap_errors": int(self.book_bootstrap_errors),
            "book_bootstrap_buffer_resets": int(self.book_bootstrap_buffer_resets),
            "book_bootstrapped_symbols": sorted(self.book_bootstrapped_symbols),
            "last_book_bootstrap_exception": self.last_book_bootstrap_exception,
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
            pong = await ws.ping()
            await asyncio.wait_for(pong, timeout=10)


def _start_binance_snapshot_future(loop, symbol: str):
    base_url = os.environ.get("MPV3_BINANCE_REST_URL", "https://fapi.binance.com").strip()
    return loop.run_in_executor(None, fetch_depth_snapshot, symbol, base_url, 1000, 10.0)


def _buffer_all_below_snapshot(snapshot, buffered) -> bool:
    depths = [x for x in buffered if x.payload.get("e") == "depthUpdate"]
    return bool(depths) and all(int(x.payload.get("u", -1)) < snapshot.last_update_id for x in depths)


def _restart_binance_snapshot(binance_snapshots, symbol, health, error=None):
    health.book_bootstrap_errors += 1
    health.last_book_bootstrap_exception = None if error is None else str(error)
    binance_snapshots[symbol] = _start_binance_snapshot_future(
        asyncio.get_running_loop(), symbol
    )


def _observe_and_write(events, writer, health):
    for event in events:
        writer.append(event)
        health.observe_event(event)


async def _run_standard_connection(
    venue,
    spec,
    parser,
    writer,
    raw_writer,
    health,
    health_path,
    max_backoff_s,
):
    import websockets

    backoff = 1.0
    while True:
        state = BookDeltaState()
        heartbeat_stop = asyncio.Event()
        connection_name = "main"
        try:
            async with websockets.connect(
                spec["url"],
                ping_interval=None if venue in {"bybit", "okx", "hyperliquid"} else 20,
                ping_timeout=20,
                close_timeout=10,
                max_queue=8192,
            ) as ws:
                connection_id = "%s-%s" % (venue, time.time_ns())
                health.connection_open(connection_name)
                health.clean_shutdown = False
                health.stopped_ns = 0
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
                            _observe_and_write(parser(parsed, receive_ns, state), writer, health)
                        except SequenceGap as exc:
                            health.parse_errors += 1
                            health.sequence_gaps += 1
                            health.last_exception = str(exc)
                            raw_writer.dead_letter(venue, receive_ns, parsed if parsed is not None else raw, exc)
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
                    health.connection_close(connection_name)
                    _write_health(health_path, health)
        except asyncio.CancelledError:
            health.connection_close(connection_name)
            raise
        except Exception as exc:
            health.connection_close(connection_name)
            health.reconnects += 1
            health.last_exception = str(exc)
            _write_health(health_path, health)
            await asyncio.sleep(backoff + random.random() * min(1.0, backoff * 0.1))
            backoff = min(max_backoff_s, backoff * 2.0)


async def _run_binance_public_connection(
    spec,
    symbols,
    writer,
    raw_writer,
    health,
    health_path,
    max_backoff_s,
):
    import websockets

    parser = PARSERS["binance"]
    backoff = 1.0
    connection_name = "public"
    while True:
        state = BookDeltaState()
        heartbeat_stop = asyncio.Event()
        try:
            async with websockets.connect(
                spec["url"], ping_interval=20, ping_timeout=20,
                close_timeout=10, max_queue=8192,
            ) as ws:
                connection_id = "binance-public-%s" % time.time_ns()
                health.connection_open(connection_name)
                health.clean_shutdown = False
                health.stopped_ns = 0
                health.last_exception = None
                _write_health(health_path, health)
                await ws.send(json.dumps(spec["subscribe"]))

                loop = asyncio.get_running_loop()
                buffers = {symbol: [] for symbol in symbols}
                snapshots = {symbol: _start_binance_snapshot_future(loop, symbol) for symbol in symbols}
                ready = set()

                hb = asyncio.create_task(_heartbeat(ws, "binance", heartbeat_stop))
                backoff = 1.0
                try:
                    async for raw in ws:
                        receive_ns = time.time_ns()
                        health.messages += 1
                        health.last_receive_ns = receive_ns
                        parsed = None
                        try:
                            parsed = json.loads(raw)
                            raw_writer.append("binance", receive_ns, parsed, connection_id)
                            control = _control_status("binance", parsed)
                            if control == "ack":
                                health.subscription_acks += 1
                            elif control == "error":
                                health.subscription_errors += 1

                            if parsed.get("e") == "depthUpdate":
                                symbol = canonical_symbol(parsed["s"])
                                if symbol not in ready:
                                    buffer = buffers.setdefault(symbol, [])
                                    buffer.append(BufferedDepthMessage(receive_ns, parsed))
                                    if len(buffer) > BINANCE_MAX_BUFFER_MESSAGES:
                                        health.book_bootstrap_buffer_resets += 1
                                        buffers[symbol] = []
                                        _restart_binance_snapshot(snapshots, symbol, health, "bootstrap buffer limit exceeded")
                                        continue
                                    future = snapshots.get(symbol)
                                    if future is not None and future.done():
                                        try:
                                            snapshot = future.result()
                                        except Exception as exc:
                                            _restart_binance_snapshot(snapshots, symbol, health, exc)
                                            continue
                                        try:
                                            bootstrap_events = normalized_bootstrap_events(snapshot, buffers[symbol], state)
                                        except BinanceBootstrapError as exc:
                                            if _buffer_all_below_snapshot(snapshot, buffers[symbol]):
                                                continue
                                            health.book_bootstrap_buffer_resets += 1
                                            buffers[symbol] = []
                                            _restart_binance_snapshot(snapshots, symbol, health, exc)
                                            continue
                                        raw_writer.append(
                                            "binance", snapshot.receive_ts_ns,
                                            {"_source":"rest_depth_snapshot","symbol":symbol,"payload":snapshot.raw},
                                            connection_id,
                                        )
                                        _observe_and_write(bootstrap_events, writer, health)
                                        health.book_bootstrap_successes += 1
                                        health.book_bootstrapped_symbols.add(symbol)
                                        health.last_book_bootstrap_exception = None
                                        ready.add(symbol)
                                        buffers[symbol] = []
                                    continue

                            _observe_and_write(parser(parsed, receive_ns, state), writer, health)
                        except SequenceGap as exc:
                            health.parse_errors += 1
                            health.sequence_gaps += 1
                            health.last_exception = str(exc)
                            raw_writer.dead_letter("binance", receive_ns, parsed if parsed is not None else raw, exc)
                            raise
                        except Exception as exc:
                            health.parse_errors += 1
                            health.last_exception = str(exc)
                            raw_writer.dead_letter("binance", receive_ns, parsed if parsed is not None else raw, exc)
                        if health.messages % 100 == 0:
                            _write_health(health_path, health)
                finally:
                    heartbeat_stop.set()
                    hb.cancel()
                    health.connection_close(connection_name)
                    _write_health(health_path, health)
        except asyncio.CancelledError:
            health.connection_close(connection_name)
            raise
        except Exception as exc:
            health.connection_close(connection_name)
            health.reconnects += 1
            health.last_exception = str(exc)
            _write_health(health_path, health)
            await asyncio.sleep(backoff + random.random() * min(1.0, backoff * 0.1))
            backoff = min(max_backoff_s, backoff * 2.0)


async def _run_binance_market_connection(
    spec,
    writer,
    raw_writer,
    health,
    health_path,
    max_backoff_s,
):
    import websockets

    parser = PARSERS["binance"]
    backoff = 1.0
    connection_name = "market"
    while True:
        state = BookDeltaState()
        heartbeat_stop = asyncio.Event()
        try:
            async with websockets.connect(
                spec["url"], ping_interval=20, ping_timeout=20,
                close_timeout=10, max_queue=8192,
            ) as ws:
                connection_id = "binance-market-%s" % time.time_ns()
                health.connection_open(connection_name)
                health.clean_shutdown = False
                health.stopped_ns = 0
                health.last_exception = None
                _write_health(health_path, health)
                await ws.send(json.dumps(spec["subscribe"]))
                hb = asyncio.create_task(_heartbeat(ws, "binance", heartbeat_stop))
                backoff = 1.0
                try:
                    async for raw in ws:
                        receive_ns = time.time_ns()
                        health.messages += 1
                        health.last_receive_ns = receive_ns
                        parsed = None
                        try:
                            parsed = json.loads(raw)
                            raw_writer.append("binance", receive_ns, parsed, connection_id)
                            control = _control_status("binance", parsed)
                            if control == "ack":
                                health.subscription_acks += 1
                            elif control == "error":
                                health.subscription_errors += 1
                            _observe_and_write(parser(parsed, receive_ns, state), writer, health)
                        except Exception as exc:
                            health.parse_errors += 1
                            health.last_exception = str(exc)
                            raw_writer.dead_letter("binance", receive_ns, parsed if parsed is not None else raw, exc)
                        if health.messages % 100 == 0:
                            _write_health(health_path, health)
                finally:
                    heartbeat_stop.set()
                    hb.cancel()
                    health.connection_close(connection_name)
                    _write_health(health_path, health)
        except asyncio.CancelledError:
            health.connection_close(connection_name)
            raise
        except Exception as exc:
            health.connection_close(connection_name)
            health.reconnects += 1
            health.last_exception = str(exc)
            _write_health(health_path, health)
            await asyncio.sleep(backoff + random.random() * min(1.0, backoff * 0.1))
            backoff = min(max_backoff_s, backoff * 2.0)


async def _run_binance(
    spec,
    symbols,
    writer,
    raw_writer,
    health,
    health_path,
    max_backoff_s,
):
    connections = {x["name"]: x for x in spec.get("connections", [])}
    if set(connections) != {"public", "market"}:
        raise RuntimeError("Binance requires public and market routed connections")
    tasks = [
        asyncio.create_task(_run_binance_public_connection(
            connections["public"], symbols, writer, raw_writer, health, health_path, max_backoff_s
        )),
        asyncio.create_task(_run_binance_market_connection(
            connections["market"], writer, raw_writer, health, health_path, max_backoff_s
        )),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _check_websockets_version() -> None:
    try:
        import websockets  # noqa: F401
        from importlib.metadata import version as package_version
    except ImportError as exc:
        raise RuntimeError(
            "Market Physics live collectors require websockets>=12,<14 in a dedicated research environment"
        ) from exc
    ws_version = package_version("websockets")
    try:
        ws_major = int(ws_version.split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("cannot parse installed websockets version: %r" % ws_version) from exc
    if ws_major < 12 or ws_major >= 14:
        raise RuntimeError("unsupported websockets %s; require >=12,<14" % ws_version)


async def run_venue(
    venue: str,
    symbols: Iterable[str],
    root: str = "data/market_physics_v3",
    health_dir: str = "reports/market_physics_v3/health",
    max_backoff_s: float = 30.0,
    fresh_event_max_lag_ms: float = DEFAULT_FRESH_EVENT_MAX_LAG_MS,
) -> None:
    _check_websockets_version()
    venue = venue.lower()
    symbols = [str(s).upper() for s in symbols]
    spec = subscriptions(venue, symbols)
    parser = PARSERS[venue]
    writer = AppendOnlyEventWriter(root)
    raw_writer = RawMessageWriter(root)
    health = CollectorHealth(venue, fresh_event_max_lag_ms=fresh_event_max_lag_ms)
    health_path = Path(health_dir) / (venue + ".json")

    try:
        if venue == "binance":
            await _run_binance(spec, symbols, writer, raw_writer, health, health_path, max_backoff_s)
        else:
            await _run_standard_connection(
                venue, spec, parser, writer, raw_writer, health, health_path, max_backoff_s
            )
    except asyncio.CancelledError:
        health.clean_shutdown = health.last_exception in (None, "")
        health.stopped_ns = time.time_ns()
        raise
    finally:
        writer.close()
        raw_writer.close()
        health._active_connections.clear()
        health.connected = False
        if health.stopped_ns == 0:
            health.stopped_ns = time.time_ns()
        _write_health(health_path, health)


async def run_many(
    venues: Iterable[str], symbols: Iterable[str], root: str, health_dir: str
) -> None:
    tasks = [
        asyncio.create_task(run_venue(v, symbols, root, health_dir)) for v in venues
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
