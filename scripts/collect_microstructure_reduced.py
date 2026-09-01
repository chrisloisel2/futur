#!/usr/bin/env python3
"""
scripts/collect_microstructure_reduced.py
─────────────────────────────────────────────────────────────────────────────
Reduced-scope microstructure L2 collector — BBO (top-of-book) + trades ONLY,
for BTCUSDT/ETHUSDT/SOLUSDT on binance/okx/hyperliquid. Bybit excluded (no
dedicated top-of-book stream on that venue — see design doc). NEVER subscribes
to full order-book depth/diff channels (binance `@depth`, okx `books`,
hyperliquid `l2Book`) — that is the entire point of this reduced design: it
avoids the crossed-book bug at the source instead of patching it downstream,
and cuts the original collector's ~56.6 GB/day (full-scope
futur-data-v2/scripts/collect_market_physics_v3.py) to an estimated
~0.66 GB/day compressed.

Design doc (read in full before touching this file):
  reports/live_alpha_lab/MICROSTRUCTURE_REDUCED_COLLECTOR_DESIGN.md

Streams subscribed (exactly this, nothing more):
  binance : {sym}@bookTicker (public ws)  +  {sym}@aggTrade (market ws)
  okx     : bbo-tbt  +  trades
  hyperliquid : bbo  +  trades

Disk safety (mandatory, non-negotiable — see MEMORY.md repo-wide rule:
"INTERDICTION TOTALE de supprimer quoi que ce soit... meme avec un disque
100% plein"):
  - MIN_FREE_DISK_GB floor (default 20GB): whole-machine free space floor;
    protects the rest of the live system, not just this collector.
  - DISK_BUDGET_GB ceiling (default 12GB): this collector's OWN cumulative
    on-disk footprint under --root (data/microstructure_reduced by default).
  - On either breach: STOP CLEANLY (finish writing the current buffer, close
    every open gzip/file handle properly), log a clear ALERT to both
    stdout/journal AND reports/live_alpha_lab/microstructure_collector_alerts.log,
    and NEVER delete any existing data — a full disk is a problem for a human
    to resolve, not something this collector fixes by deleting things.

Output (gzip-rotated JSONL, hourly rotation, one gzip member sequence per
partition-hour — python's gzip module transparently decompresses concatenated
multi-member files, so simply appending fresh GzipFile writes across restarts
is safe to read back):
  data/microstructure_reduced/raw/bbo/venue=.../symbol=.../date=YYYY-MM-DD/events-HH.jsonl.gz
  data/microstructure_reduced/raw/trades/venue=.../symbol=.../date=YYYY-MM-DD/events-HH.jsonl.gz
  data/microstructure_reduced/state.json                  (health / counters)
  reports/live_alpha_lab/microstructure_collector_alerts.log  (disk ALERTs only)

Clean shutdown on SIGTERM/SIGINT (flush + close every handle, matches
scripts/hl_metaorders_collector.py's pattern). Service:
deploy/systemd/futur-microstructure-reduced.service (Restart=always).
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import logging
import os
import shutil
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUT_DEFAULT = ROOT / "data" / "microstructure_reduced"
ALERT_LOG = ROOT / "reports" / "live_alpha_lab" / "microstructure_collector_alerts.log"

VENUES = ("binance", "okx", "hyperliquid")
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")

MIN_FREE_DISK_GB_DEFAULT = 20.0
DISK_BUDGET_GB_DEFAULT = 12.0
DISK_CHECK_MIN_INTERVAL_S = 15.0     # throttle for the periodic re-check
DISK_GUARD_LOOP_S = 30.0             # periodic guard task cadence
ROTATE_CHECK_S = 60.0                # how often we check for an hour rollover
FLUSH_INTERVAL_S = 5.0               # durability flush cadence per open sink

log = logging.getLogger("microstructure_collector")


# ── disk safety (pure, unit-tested in tests/test_microstructure_disk_budget.py) ──

def _dir_size_bytes(path: Path) -> int:
    """Sum of file sizes under `path`. 0 if the path does not exist yet."""
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def disk_budget_status(
    out_root: Path,
    min_free_gb: float,
    budget_gb: float,
    disk_usage_fn: Callable[[str], object] = shutil.disk_usage,
    dir_size_fn: Callable[[Path], int] = _dir_size_bytes,
    disk_usage_path: str = "/",
) -> Dict[str, object]:
    """Pure disk-budget check, injectable for testing (no real filesystem
    access required in tests). Returns:
      {"ok": bool, "reason": str|None, "free_gb": float, "used_gb": float}

    Two independent thresholds, either one breaching stops the collector:
      1. MIN_FREE_DISK_GB — whole-machine free-space floor, checked via
         shutil.disk_usage("/") (protects the rest of the live system,
         independent of what this collector has written or whether
         `out_root` exists yet).
      2. DISK_BUDGET_GB — this collector's own cumulative footprint under
         `out_root` (a self-imposed ceiling, separate from #1).
    """
    usage = disk_usage_fn(disk_usage_path)
    free_gb = usage.free / (1024 ** 3)
    used_gb = dir_size_fn(out_root) / (1024 ** 3)
    if free_gb < min_free_gb:
        return {
            "ok": False,
            "reason": (
                "MIN_FREE_DISK_GB breach: %.2fGB free on disk < floor %.2fGB "
                "(whole-machine floor, not just this collector's budget)"
                % (free_gb, min_free_gb)
            ),
            "free_gb": free_gb,
            "used_gb": used_gb,
        }
    if used_gb >= budget_gb:
        return {
            "ok": False,
            "reason": (
                "DISK_BUDGET_GB breach: collector has written %.2fGB under %s "
                ">= budget %.2fGB"
                % (used_gb, out_root, budget_gb)
            ),
            "free_gb": free_gb,
            "used_gb": used_gb,
        }
    return {"ok": True, "reason": None, "free_gb": free_gb, "used_gb": used_gb}


def alert(msg: str) -> None:
    """Log an ALERT to stdout/journal AND to the dedicated alert file. Never
    deletes anything, never attempts to free space itself."""
    log.error("ALERT: %s", msg)
    try:
        ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s ALERT %s\n" % (datetime.now(timezone.utc).isoformat(), msg))
    except OSError:
        log.exception("failed to write alert log at %s", ALERT_LOG)


class DiskBudgetExceeded(RuntimeError):
    pass


class DiskGuard:
    """Single source of truth for disk-budget status, shared between the
    sink (checked before opening any NEW file) and a periodic background
    task (checked on a fixed cadence regardless of file-open activity, so a
    steady-state collector that stops opening new files still gets caught).
    Alerts once per breach transition (not spammed every check)."""

    def __init__(
        self,
        out_root: Path,
        min_free_gb: float,
        budget_gb: float,
        min_check_interval_s: float = DISK_CHECK_MIN_INTERVAL_S,
        disk_usage_fn: Callable = shutil.disk_usage,
        dir_size_fn: Callable = _dir_size_bytes,
    ):
        self.out_root = out_root
        self.min_free_gb = min_free_gb
        self.budget_gb = budget_gb
        self.min_check_interval_s = min_check_interval_s
        self.disk_usage_fn = disk_usage_fn
        self.dir_size_fn = dir_size_fn
        self._last_check_mono = -1e18
        self._last_status: Dict[str, object] = {
            "ok": True, "reason": None, "free_gb": None, "used_gb": None,
        }
        self._alerted = False

    def status(self, force: bool = False) -> Dict[str, object]:
        now = time.monotonic()
        if not force and (now - self._last_check_mono) < self.min_check_interval_s:
            return self._last_status
        self._last_check_mono = now
        st = disk_budget_status(
            self.out_root, self.min_free_gb, self.budget_gb,
            self.disk_usage_fn, self.dir_size_fn,
        )
        self._last_status = st
        if not st["ok"]:
            if not self._alerted:
                self._alerted = True
                alert(st["reason"])
        else:
            self._alerted = False
        return st

    def ok(self, force: bool = False) -> bool:
        return bool(self.status(force=force)["ok"])


# ── gzip-rotated JSONL sink ────────────────────────────────────────────────

class GzipJsonlSink:
    """One open GzipFile per partition path; flushed (Z_SYNC_FLUSH, safe for
    partial reads) on a durability cadence, closed on hourly rotation or
    shutdown. Refuses to open a brand-new path once the DiskGuard reports a
    breach (existing open handles may still receive their final flush+close
    on shutdown — that is not "writing a new file", it's finishing the one
    already in flight, which the mission brief explicitly allows)."""

    def __init__(self, guard: "DiskGuard", flush_interval_s: float = FLUSH_INTERVAL_S):
        self.guard = guard
        self.flush_interval_s = flush_interval_s
        self._raw: Dict[Path, object] = {}
        self._gz: Dict[Path, gzip.GzipFile] = {}
        self._last_flush: Dict[Path, float] = {}
        self.rows_written = 0
        self.bytes_written = 0

    def append(self, path: Path, row: dict) -> None:
        gz = self._gz.get(path)
        if gz is None:
            if not self.guard.ok():
                raise DiskBudgetExceeded(str(self.guard.status()["reason"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = open(path, "ab", buffering=0)
            gz = gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6)
            self._raw[path] = raw
            self._gz[path] = gz
            self._last_flush[path] = time.monotonic()
        line = (json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        gz.write(line)
        self.rows_written += 1
        self.bytes_written += len(line)
        now = time.monotonic()
        if now - self._last_flush[path] >= self.flush_interval_s:
            self._flush_path(path)

    def _flush_path(self, path: Path) -> None:
        gz = self._gz.get(path)
        raw = self._raw.get(path)
        if gz is None or raw is None:
            return
        gz.flush()          # Z_SYNC_FLUSH: pushes compressed bytes to `raw`, stream stays open/valid
        raw.flush()
        try:
            os.fdatasync(raw.fileno())
        except AttributeError:  # pragma: no cover - non-POSIX fallback
            os.fsync(raw.fileno())
        self._last_flush[path] = time.monotonic()

    def flush_all(self) -> None:
        for path in list(self._gz):
            self._flush_path(path)

    def _close_path(self, path: Path) -> None:
        gz = self._gz.pop(path, None)
        raw = self._raw.pop(path, None)
        self._last_flush.pop(path, None)
        if gz is not None:
            try:
                gz.close()   # writes gzip footer; does NOT close `raw` (fileobj was passed in)
            except Exception:
                log.exception("error closing gzip handle for %s", path)
        if raw is not None:
            try:
                raw.close()
            except Exception:
                log.exception("error closing raw file handle for %s", path)

    def close_all(self) -> None:
        """Flush + close every open handle. Called on hourly rotation and on
        final shutdown. Never deletes any file."""
        for path in list(self._gz):
            self._flush_path(path)
            self._close_path(path)

    def close(self) -> None:
        self.close_all()


def partition_path(out_root: Path, kind: str, venue: str, symbol: str, event_ts_ns: int) -> Path:
    dt = datetime.fromtimestamp(event_ts_ns / 1e9, tz=timezone.utc)
    date = dt.date().isoformat()
    hour = dt.hour
    return (
        out_root / "raw" / kind / ("venue=" + venue) / ("symbol=" + symbol)
        / ("date=" + date) / ("events-%02d.jsonl.gz" % hour)
    )


# ── venue instrument-id helpers ────────────────────────────────────────────

def okx_inst(symbol: str) -> str:
    assert symbol.endswith("USDT")
    return symbol[:-4] + "-USDT-SWAP"


def okx_symbol_from_inst(inst: str) -> str:
    if inst.endswith("-USDT-SWAP"):
        return inst[: -len("-USDT-SWAP")] + "USDT"
    return inst.replace("-", "")


def hl_coin(symbol: str) -> str:
    assert symbol.endswith("USDT")
    return symbol[:-4]


def hl_symbol_from_coin(coin: str) -> str:
    return coin + "USDT"


def _level(row) -> Tuple[float, float]:
    """Accepts either [px, qty, ...] (binance/okx wire lists) or
    {"px":..., "sz":...} (hyperliquid dict levels)."""
    if isinstance(row, dict):
        return float(row["px"]), float(row["sz"])
    return float(row[0]), float(row[1])


MS = 1_000_000


def _clock(event_ms, receive_ns) -> Tuple[int, int]:
    return int(event_ms) * MS, int(receive_ns)


# ── pure parsers (unit-tested in tests/test_microstructure_reduced_parsers.py) ──

def parse_binance_bookticker(msg: dict, receive_ns: int) -> List[dict]:
    d = msg.get("data", msg)
    if d.get("e") != "bookTicker":
        return []
    symbol = str(d["s"]).upper()
    event_ms = int(d.get("T") or d.get("E") or receive_ns // MS)
    event_ns, recv = _clock(event_ms, receive_ns)
    return [{
        "venue": "binance", "symbol": symbol,
        "event_ts_ns": event_ns, "receive_ts_ns": recv,
        "bid_price": float(d["b"]), "bid_qty": float(d["B"]),
        "ask_price": float(d["a"]), "ask_qty": float(d["A"]),
        "source_stream": "bookTicker",
    }]


def parse_binance_aggtrade(msg: dict, receive_ns: int) -> List[dict]:
    d = msg.get("data", msg)
    if d.get("e") != "aggTrade":
        return []
    symbol = str(d["s"]).upper()
    event_ms = d.get("T", d.get("E"))
    event_ns, recv = _clock(event_ms, receive_ns)
    return [{
        "venue": "binance", "symbol": symbol,
        "event_ts_ns": event_ns, "receive_ts_ns": recv,
        "trade_id": str(d.get("a")), "price": float(d["p"]), "qty": float(d["q"]),
        "side": "sell" if d.get("m") else "buy",
        "source_stream": "aggTrade",
    }]


def parse_okx(msg: dict, receive_ns: int) -> Tuple[List[dict], List[dict]]:
    arg = msg.get("arg", {})
    channel = arg.get("channel")
    bbo_rows: List[dict] = []
    trade_rows: List[dict] = []
    if channel not in {"bbo-tbt", "trades"}:
        return bbo_rows, trade_rows
    for d in msg.get("data", []) or []:
        inst = d.get("instId") or arg.get("instId", "")
        symbol = okx_symbol_from_inst(inst)
        event_ms = int(d.get("ts") or 0)
        if not event_ms:
            continue
        event_ns, recv = _clock(event_ms, receive_ns)
        if channel == "bbo-tbt":
            bids = d.get("bids") or []
            asks = d.get("asks") or []
            if not bids or not asks:
                continue
            bp, bq = _level(bids[0])
            ap, aq = _level(asks[0])
            bbo_rows.append({
                "venue": "okx", "symbol": symbol,
                "event_ts_ns": event_ns, "receive_ts_ns": recv,
                "bid_price": bp, "bid_qty": bq, "ask_price": ap, "ask_qty": aq,
                "source_stream": "bbo-tbt",
            })
        elif channel == "trades":
            trade_rows.append({
                "venue": "okx", "symbol": symbol,
                "event_ts_ns": event_ns, "receive_ts_ns": recv,
                "trade_id": str(d.get("tradeId", event_ms)),
                "price": float(d["px"]), "qty": float(d["sz"]),
                "side": "buy" if d.get("side") == "buy" else "sell",
                "source_stream": "trades",
            })
    return bbo_rows, trade_rows


def parse_hyperliquid(msg: dict, receive_ns: int) -> Tuple[List[dict], List[dict]]:
    ch = msg.get("channel")
    data = msg.get("data")
    bbo_rows: List[dict] = []
    trade_rows: List[dict] = []
    if ch == "bbo" and data:
        symbol = hl_symbol_from_coin(str(data["coin"]))
        event_ms = data["time"]
        bbo = data.get("bbo") or [None, None]
        if bbo[0] is None or bbo[1] is None:
            return bbo_rows, trade_rows
        bp, bq = _level(bbo[0])
        ap, aq = _level(bbo[1])
        event_ns, recv = _clock(event_ms, receive_ns)
        bbo_rows.append({
            "venue": "hyperliquid", "symbol": symbol,
            "event_ts_ns": event_ns, "receive_ts_ns": recv,
            "bid_price": bp, "bid_qty": bq, "ask_price": ap, "ask_qty": aq,
            "source_stream": "bbo",
        })
    elif ch == "trades":
        for d in data or []:
            symbol = hl_symbol_from_coin(str(d["coin"]))
            event_ns, recv = _clock(d["time"], receive_ns)
            side = str(d["side"]).upper()
            aggressor = "buy" if side in {"B", "BUY"} else "sell"
            tid = d.get("tid")
            trade_id = ("%s:%s:%s" % (d["time"], symbol, tid)) if tid is not None else str(d.get("hash"))
            trade_rows.append({
                "venue": "hyperliquid", "symbol": symbol,
                "event_ts_ns": event_ns, "receive_ts_ns": recv,
                "trade_id": trade_id, "price": float(d["px"]), "qty": float(d["sz"]),
                "side": aggressor,
                "source_stream": "trades",
            })
    return bbo_rows, trade_rows


# ── collector ───────────────────────────────────────────────────────────────

class Collector:
    def __init__(
        self,
        out_root: Path,
        symbols=SYMBOLS,
        min_free_disk_gb: float = MIN_FREE_DISK_GB_DEFAULT,
        disk_budget_gb: float = DISK_BUDGET_GB_DEFAULT,
    ):
        self.out_root = out_root
        self.symbols = list(symbols)
        self.guard = DiskGuard(out_root, min_free_disk_gb, disk_budget_gb)
        self.sink = GzipJsonlSink(self.guard)
        self.stop = asyncio.Event()
        self.counters = {
            "binance_bbo": 0, "binance_trades": 0,
            "okx_bbo": 0, "okx_trades": 0,
            "hyperliquid_bbo": 0, "hyperliquid_trades": 0,
            "reconnects": {"binance_public": 0, "binance_market": 0, "okx": 0, "hyperliquid": 0},
            "parse_errors": 0,
        }
        self.state_path = out_root / "state.json"

    def _write_bbo(self, row: dict) -> None:
        path = partition_path(self.out_root, "bbo", row["venue"], row["symbol"], row["event_ts_ns"])
        self.sink.append(path, row)
        self.counters["%s_bbo" % row["venue"]] += 1

    def _write_trade(self, row: dict) -> None:
        path = partition_path(self.out_root, "trades", row["venue"], row["symbol"], row["event_ts_ns"])
        self.sink.append(path, row)
        self.counters["%s_trades" % row["venue"]] += 1

    # — binance: public ws (bookTicker) + market ws (aggTrade), routed
    #   separately per Binance USD-M's 2026 stream-routing split (see
    #   futur-data-v2/market_physics_v3/collectors/specs.py comment) —
    async def binance_public_task(self):
        import websockets
        streams = [s.lower() + "@bookTicker" for s in self.symbols]
        url = os.environ.get("MSR_BINANCE_PUBLIC_WS_URL", "wss://fstream.binance.com/public/ws")
        backoff = 1.0
        while not self.stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as ws:
                    await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 1}))
                    log.info("binance public ws connected, subscribed %s", streams)
                    backoff = 1.0
                    async for raw in ws:
                        if self.stop.is_set():
                            break
                        self._handle_binance_public(raw)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self.stop.is_set():
                    return
                self.counters["reconnects"]["binance_public"] += 1
                log.warning("binance public ws disconnected (%s) — retry in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2.0)

    def _handle_binance_public(self, raw) -> None:
        try:
            msg = json.loads(raw)
            for row in parse_binance_bookticker(msg, time.time_ns()):
                self._write_bbo(row)
        except DiskBudgetExceeded:
            raise
        except Exception:
            self.counters["parse_errors"] += 1
            log.exception("binance public parse error")

    async def binance_market_task(self):
        import websockets
        streams = [s.lower() + "@aggTrade" for s in self.symbols]
        url = os.environ.get("MSR_BINANCE_MARKET_WS_URL", "wss://fstream.binance.com/market/ws")
        backoff = 1.0
        while not self.stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=10) as ws:
                    await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": 2}))
                    log.info("binance market ws connected, subscribed %s", streams)
                    backoff = 1.0
                    async for raw in ws:
                        if self.stop.is_set():
                            break
                        self._handle_binance_market(raw)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self.stop.is_set():
                    return
                self.counters["reconnects"]["binance_market"] += 1
                log.warning("binance market ws disconnected (%s) — retry in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2.0)

    def _handle_binance_market(self, raw) -> None:
        try:
            msg = json.loads(raw)
            for row in parse_binance_aggtrade(msg, time.time_ns()):
                self._write_trade(row)
        except DiskBudgetExceeded:
            raise
        except Exception:
            self.counters["parse_errors"] += 1
            log.exception("binance market parse error")

    # — okx: single connection, bbo-tbt + trades —
    async def okx_task(self):
        import websockets
        args = []
        for s in self.symbols:
            inst = okx_inst(s)
            args += [{"channel": "bbo-tbt", "instId": inst}, {"channel": "trades", "instId": inst}]
        url = os.environ.get("MSR_OKX_WS_URL", "wss://ws.okx.com:8443/ws/v5/public")
        backoff = 1.0
        while not self.stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=None, close_timeout=10) as ws:
                    await ws.send(json.dumps({"op": "subscribe", "args": args}))
                    log.info("okx ws connected, subscribed %s", args)
                    backoff = 1.0
                    hb_stop = asyncio.Event()
                    hb = asyncio.create_task(self._okx_heartbeat(ws, hb_stop))
                    try:
                        async for raw in ws:
                            if self.stop.is_set():
                                break
                            if raw == "pong":
                                continue
                            self._handle_okx(raw)
                    finally:
                        hb_stop.set()
                        hb.cancel()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self.stop.is_set():
                    return
                self.counters["reconnects"]["okx"] += 1
                log.warning("okx ws disconnected (%s) — retry in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2.0)

    async def _okx_heartbeat(self, ws, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=20)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                await ws.send("ping")
            except Exception:
                return

    def _handle_okx(self, raw) -> None:
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                return
            bbo_rows, trade_rows = parse_okx(msg, time.time_ns())
            for row in bbo_rows:
                self._write_bbo(row)
            for row in trade_rows:
                self._write_trade(row)
        except DiskBudgetExceeded:
            raise
        except Exception:
            self.counters["parse_errors"] += 1
            log.exception("okx parse error")

    # — hyperliquid: single connection, bbo + trades —
    async def hyperliquid_task(self):
        import websockets
        msgs = []
        for s in self.symbols:
            coin = hl_coin(s)
            for typ in ("bbo", "trades"):
                msgs.append({"method": "subscribe", "subscription": {"type": typ, "coin": coin}})
        url = os.environ.get("MSR_HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws")
        backoff = 1.0
        while not self.stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=None, close_timeout=10) as ws:
                    for m in msgs:
                        await ws.send(json.dumps(m))
                    log.info("hyperliquid ws connected, subscribed %d channels", len(msgs))
                    backoff = 1.0
                    hb_stop = asyncio.Event()
                    hb = asyncio.create_task(self._hl_heartbeat(ws, hb_stop))
                    try:
                        async for raw in ws:
                            if self.stop.is_set():
                                break
                            self._handle_hyperliquid(raw)
                    finally:
                        hb_stop.set()
                        hb.cancel()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if self.stop.is_set():
                    return
                self.counters["reconnects"]["hyperliquid"] += 1
                log.warning("hyperliquid ws disconnected (%s) — retry in %.1fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2.0)

    async def _hl_heartbeat(self, ws, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=20)
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                await ws.send(json.dumps({"method": "ping"}))
            except Exception:
                return

    def _handle_hyperliquid(self, raw) -> None:
        try:
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                return
            bbo_rows, trade_rows = parse_hyperliquid(msg, time.time_ns())
            for row in bbo_rows:
                self._write_bbo(row)
            for row in trade_rows:
                self._write_trade(row)
        except DiskBudgetExceeded:
            raise
        except Exception:
            self.counters["parse_errors"] += 1
            log.exception("hyperliquid parse error")

    # — disk guard: periodic check independent of file-open activity —
    async def disk_guard_task(self):
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=DISK_GUARD_LOOP_S)
            except asyncio.TimeoutError:
                pass
            if self.stop.is_set():
                return
            st = self.guard.status(force=True)
            if not st["ok"]:
                log.error("disk guard breach, stopping cleanly: %s", st["reason"])
                self.stop.set()
                return

    # — hourly rotation: close all open handles once the wall-clock hour
    #   changes so the next write opens a fresh (new-hour) file; keeps
    #   individual files bounded instead of one giant growing stream —
    async def rotate_task(self):
        last_hour = datetime.now(timezone.utc).hour
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=ROTATE_CHECK_S)
            except asyncio.TimeoutError:
                pass
            if self.stop.is_set():
                return
            now_hour = datetime.now(timezone.utc).hour
            if now_hour != last_hour:
                log.info("hourly rotation: closing %d open handle(s)", len(self.sink._gz))
                self.sink.close_all()
                last_hour = now_hour

    # — periodic health/state.json, mirrors hl_metaorders_collector.py —
    async def state_task(self):
        while not self.stop.is_set():
            self._write_state()
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                pass

    def _write_state(self) -> None:
        st = self.guard.status()
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "counters": self.counters,
            "rows_written": self.sink.rows_written,
            "bytes_written_uncompressed_est": self.sink.bytes_written,
            "disk": st,
            "symbols": self.symbols,
            "venues": list(VENUES),
            "min_free_disk_gb": self.guard.min_free_gb,
            "disk_budget_gb": self.guard.budget_gb,
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=1, default=str))
            tmp.replace(self.state_path)
        except OSError:
            log.exception("failed to write state.json")

    async def run(self):
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.stop.set)
            except NotImplementedError:  # pragma: no cover - non-POSIX
                pass
        tasks = [
            asyncio.ensure_future(t()) for t in (
                self.binance_public_task, self.binance_market_task,
                self.okx_task, self.hyperliquid_task,
                self.disk_guard_task, self.rotate_task, self.state_task,
            )
        ]
        await self.stop.wait()
        log.info("stop requested — cancelling tasks and flushing")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.sink.close_all()
        self._write_state()
        log.info("clean shutdown complete — counters: %s", self.counters)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(OUT_DEFAULT))
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--min-free-disk-gb", type=float, default=MIN_FREE_DISK_GB_DEFAULT)
    ap.add_argument("--disk-budget-gb", type=float, default=DISK_BUDGET_GB_DEFAULT)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out_root = Path(args.root)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    collector = Collector(
        out_root, symbols=symbols,
        min_free_disk_gb=args.min_free_disk_gb,
        disk_budget_gb=args.disk_budget_gb,
    )
    log.info(
        "starting microstructure reduced collector: venues=%s symbols=%s "
        "root=%s min_free_disk_gb=%.1f disk_budget_gb=%.1f",
        VENUES, symbols, out_root, args.min_free_disk_gb, args.disk_budget_gb,
    )
    asyncio.get_event_loop().run_until_complete(collector.run())


if __name__ == "__main__":
    main()
