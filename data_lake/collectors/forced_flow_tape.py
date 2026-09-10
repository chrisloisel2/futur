#!/usr/bin/env python3
"""
forced_flow_tape.py -- P2B : la tape des flux forces (liquidations), en direct.

Pourquoi en direct : la famille cascade de ce depot est morte de LATENCE (backfill
Vision a 45-48 h contre un horizon de 4 h), pas d'absence de mecanisme. Ici chaque
liquidation est horodatee a l'exchange ET a la reception, et enrichie avec ce que le
carnet et le mark price disaient AU MOMENT de l'evenement -- jamais apres.

Flux (publics, lecture seule) :
    binance  wss://fstream.binance.com/market/ws  SUBSCRIBE !forceOrder@arr, !markPrice@arr@1s
             wss://fstream.binance.com/public/ws  SUBSCRIBE !bookTicker
             (le chemin standard /ws/<stream> est muet sur cet hote : on prend ceux du
             collecteur microstructure_reduced, qui tournent depuis le 2026-08-31)
    bybit    wss://stream.bybit.com/v5/public/linear  allLiquidation.<sym>, tickers.<sym>
             (symboles VALIDES contre /v5/market/instruments-info : un topic inconnu fait
             echouer tout le lot -- PEPEUSDT s'appelle 1000PEPEUSDT sur Bybit)
    hyperliquid  aucun flux public de liquidations sans contexte utilisateur : absent en v1

Limites connues, ecrites avant la premiere ligne :
    - Binance !forceOrder@arr pousse AU PLUS une liquidation par symbole et par seconde
      (la derniere) : les rafales sont sous-comptees, le notional agrege est une BORNE BASSE
    - Bybit allLiquidation agrege aussi par seconde
    - depth_10bps_before = null : aucun flux de profondeur n'est souscrit (BBO seulement)
    - open_interest : Bybit tickers le porte ; Binance ne l'a pas en flux -> null en v1
    - return_5s/30s/5m : null ici, remplis hors ligne par la jointure (jamais par le collecteur)

Sorties (memes conventions que microstructure_reduced) :
    data_lake/events/liquidations/venue=<v>/date=<YYYY-MM-DD>/events-HH.jsonl.gz
    data_lake/events/liquidations.jsonl   (export consolide : --export)

Usage :
    python3 -m data_lake.collectors.forced_flow_tape --dry-run --seconds 40 --out /tmp/x
    python3 -m data_lake.collectors.forced_flow_tape                      # service
    python3 -m data_lake.collectors.forced_flow_tape --export             # -> liquidations.jsonl
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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import websockets

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "data_lake" / "events" / "liquidations"
EXPORT = ROOT / "data_lake" / "events" / "liquidations.jsonl"
BPS = 1e4
SCHEMA_VERSION = 1
log = logging.getLogger("forced_flow")

BINANCE_MARKET_WS = os.environ.get("MSR_BINANCE_MARKET_WS_URL", "wss://fstream.binance.com/market/ws")
BINANCE_PUBLIC_WS = os.environ.get("MSR_BINANCE_PUBLIC_WS_URL", "wss://fstream.binance.com/public/ws")
BYBIT_WS = os.environ.get("MSR_BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/linear")
BYBIT_TOP_N = int(os.environ.get("FFT_BYBIT_TOP_N", "60"))


# ------------------------------------------------------------------ sink

class GzipJsonlSink:
    """Un GzipFile ouvert par partition, flush synchrone : une coupure laisse un
    fichier lisible (membres gzip concatenes, transparents pour gzip.open)."""

    def __init__(self, out_root: Path):
        self.out_root = out_root; self._gz: Dict[Path, gzip.GzipFile] = {}; self._raw: Dict[Path, object] = {}

    def path(self, venue: str, event_ts_ns: int) -> Path:
        d = datetime.fromtimestamp(event_ts_ns / 1e9, tz=timezone.utc)
        return self.out_root / f"venue={venue}" / f"date={d:%Y-%m-%d}" / f"events-{d:%H}.jsonl.gz"

    def write(self, venue: str, rec: dict):
        p = self.path(venue, rec["event_ts_exchange_ns"] or rec["recv_ts_local_ns"])
        if p not in self._gz:
            p.parent.mkdir(parents=True, exist_ok=True)
            raw = open(p, "ab"); self._raw[p] = raw
            self._gz[p] = gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=6)
            for q in [q for q in self._gz if q != p and q.parent == p.parent or (q != p and q.parent.parent == p.parent.parent and q < p)]:
                self._close(q)
        gz = self._gz[p]; gz.write((json.dumps(rec, ensure_ascii=False) + "\n").encode()); gz.flush()

    def _close(self, p: Path):
        try:
            self._gz.pop(p).close(); self._raw.pop(p).close()
        except Exception:
            log.exception("close %s", p)

    def close(self):
        for p in list(self._gz):
            self._close(p)


class DiskGuard:
    def __init__(self, out_root: Path, min_free_gb: float, budget_gb: float):
        self.out_root, self.min_free, self.budget = out_root, min_free_gb * 2**30, budget_gb * 2**30

    def check(self):
        free = shutil.disk_usage(self.out_root.parent).free
        used = sum(f.stat().st_size for f in self.out_root.rglob("*.gz")) if self.out_root.exists() else 0
        if free < self.min_free or used > self.budget:
            raise RuntimeError(f"disk guard: free={free/2**30:.1f} GiB used={used/2**30:.2f} GiB")


# ------------------------------------------------------------------ etat marche (memoire)

BUFFER_MS = 15_000


class MarketState:
    """Tampon de cotes et de marks par symbole (BUFFER_MS), et selection de la derniere
    valeur dont l'horodatage est <= celui de la liquidation. Le premier dry-run gardait
    seulement la DERNIERE cote : Binance pousse forceOrder ~1 s apres son propre T
    (throttle 1/s/symbole), donc la cote 'd'avant' etait celle d'APRES l'impact --
    exactement le biais qui fabriquerait un faux edge de continuation."""

    def __init__(self):
        self.quote: Dict[str, list] = {}; self.mark: Dict[str, list] = {}

    @staticmethod
    def _push(buf: Dict[str, list], sym: str, item: dict):
        b = buf.setdefault(sym, []); b.append(item)
        cut = item["ts_ms"] - BUFFER_MS
        while b and b[0]["ts_ms"] < cut:
            b.pop(0)

    @staticmethod
    def _before(buf: Dict[str, list], sym: str, event_ms: int) -> Optional[dict]:
        b = buf.get(sym) or []
        for item in reversed(b):
            if item["ts_ms"] <= event_ms:
                return item
        return None

    def enrich(self, venue: str, sym: str, event_ms: int) -> dict:
        q, m = self._before(self.quote, sym, event_ms), self._before(self.mark, sym, event_ms)
        out = {"spread_before_bps": None, "book_imbalance_before": None, "bbo_age_ms": None,
               "mark_price": None, "index_price": None, "funding_rate": None, "open_interest": None, "mark_age_ms": None,
               "depth_10bps_before": None, "depth_note": "no depth stream subscribed (BBO only)"}
        if q and q["bid"] > 0 and q["ask"] > 0:
            mid = 0.5 * (q["bid"] + q["ask"])
            out.update({"spread_before_bps": (q["ask"] - q["bid"]) / mid * BPS,
                        "book_imbalance_before": (q["bq"] - q["aq"]) / (q["bq"] + q["aq"]) if (q["bq"] + q["aq"]) > 0 else None,
                        "bbo_age_ms": event_ms - q["ts_ms"]})
        if m:
            out.update({"mark_price": m.get("mark"), "index_price": m.get("index"), "funding_rate": m.get("funding"),
                        "open_interest": m.get("oi"), "mark_age_ms": event_ms - m["ts_ms"]})
        return out


def base_record(venue, sym, event_ms, recv_ns, side_order, price, qty, extra):
    liquidated = "long" if side_order == "sell" else "short"      # un ordre SELL force = un long liquide
    rec = {"schema_version": SCHEMA_VERSION, "venue": venue, "symbol": sym,
           "event_ts_exchange": datetime.fromtimestamp(event_ms / 1000, tz=timezone.utc).isoformat(),
           "event_ts_exchange_ns": int(event_ms) * 1_000_000, "recv_ts_local": datetime.fromtimestamp(recv_ns / 1e9, tz=timezone.utc).isoformat(),
           "recv_ts_local_ns": recv_ns, "latency_ms": recv_ns / 1e6 - event_ms,
           "side": side_order, "liquidated_position": liquidated, "price": price, "qty": qty, "notional_usd": price * qty,
           "return_5s_bps": None, "return_30s_bps": None, "return_5m_bps": None, "return_30m_bps": None}
    rec.update(extra); return rec


# ------------------------------------------------------------------ binance

async def binance_market(state: MarketState, sink: GzipJsonlSink, stats: dict, stop: asyncio.Event):
    while not stop.is_set():
        try:
            async with websockets.connect(BINANCE_MARKET_WS, ping_interval=20, max_size=2**23) as ws:
                await ws.send(json.dumps({"method": "SUBSCRIBE", "params": ["!forceOrder@arr", "!markPrice@arr@1s"], "id": 1}))
                log.info("binance market ws connected")
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=60); recv_ns = time.time_ns(); m = json.loads(raw)
                    if isinstance(m, list):                                   # !markPrice@arr@1s
                        for d in m:
                            if d.get("e") == "markPriceUpdate":
                                state._push(state.mark, d["s"], {"mark": float(d["p"]), "index": float(d.get("i") or 0) or None,
                                                                  "funding": float(d.get("r") or 0), "oi": None, "ts_ms": int(d["E"])})
                        stats["binance_mark_arrays"] = stats.get("binance_mark_arrays", 0) + 1
                    elif m.get("e") == "forceOrder":
                        o = m["o"]; sym = o["s"]; event_ms = int(o.get("T") or m["E"]); side = "sell" if o["S"] == "SELL" else "buy"
                        stream_delay_ms = int(m["E"]) - event_ms                  # le throttle de l'exchange, mesure
                        price = float(o.get("ap") or o.get("p")); qty = float(o.get("q"))
                        extra = state.enrich("binance", sym, event_ms)
                        extra.update({"order_type": o.get("o"), "time_in_force": o.get("f"), "order_status": o.get("X"), "limit_price": float(o.get("p") or 0),
                                      "filled_qty": float(o.get("z") or 0), "source_stream": "forceOrder", "stream_delay_ms": stream_delay_ms,
                                      "throttle_note": "binance pushes at most one liquidation per symbol per second"})
                        sink.write("binance", base_record("binance", sym, event_ms, recv_ns, side, price, qty, extra))
                        stats["binance_liquidations"] = stats.get("binance_liquidations", 0) + 1
        except (asyncio.TimeoutError, websockets.ConnectionClosed, OSError) as e:
            log.warning("binance market ws: %s -> reconnect", type(e).__name__); await asyncio.sleep(3)
        except asyncio.CancelledError:
            return


async def binance_public(state: MarketState, stats: dict, stop: asyncio.Event):
    while not stop.is_set():
        try:
            async with websockets.connect(BINANCE_PUBLIC_WS, ping_interval=20, max_size=2**23) as ws:
                await ws.send(json.dumps({"method": "SUBSCRIBE", "params": ["!bookTicker"], "id": 2}))
                log.info("binance public ws connected (!bookTicker)")
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=60); m = json.loads(raw)
                    if m.get("e") == "bookTicker":
                        state._push(state.quote, m["s"], {"bid": float(m["b"]), "ask": float(m["a"]), "bq": float(m["B"]), "aq": float(m["A"]), "ts_ms": int(m.get("T") or m["E"])})
                        stats["binance_bbo"] = stats.get("binance_bbo", 0) + 1
        except (asyncio.TimeoutError, websockets.ConnectionClosed, OSError) as e:
            log.warning("binance public ws: %s -> reconnect", type(e).__name__); await asyncio.sleep(3)
        except asyncio.CancelledError:
            return


# ------------------------------------------------------------------ bybit

def bybit_symbols(top_n: int) -> list:
    """Symboles linear VALIDES, tries par turnover 24 h : un topic inconnu fait echouer le lot."""
    def get(url):
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "futur-p2"}), timeout=20) as r:
            return json.loads(r.read().decode())
    inst = {x["symbol"] for x in get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")["result"]["list"] if x.get("status") == "Trading"}
    tick = get("https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]
    ranked = sorted((x for x in tick if x["symbol"] in inst and x["symbol"].endswith("USDT")), key=lambda x: -float(x.get("turnover24h") or 0))
    return [x["symbol"] for x in ranked[:top_n]]


async def bybit(state: MarketState, sink: GzipJsonlSink, stats: dict, stop: asyncio.Event, symbols: list):
    while not stop.is_set():
        try:
            async with websockets.connect(BYBIT_WS, ping_interval=20, max_size=2**23) as ws:
                for i in range(0, len(symbols), 10):                          # lots de 10 topics
                    batch = symbols[i:i + 10]
                    await ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{s}" for s in batch] + [f"tickers.{s}" for s in batch]}))
                log.info("bybit ws connected, %d symbols", len(symbols))
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=60); recv_ns = time.time_ns(); m = json.loads(raw)
                    topic = m.get("topic", "")
                    if topic.startswith("tickers."):
                        d = m["data"]; sym = d["symbol"]; prevs = state.mark.get(sym) or []; prev = prevs[-1] if prevs else {}
                        state._push(state.mark, sym, {"mark": float(d.get("markPrice") or prev.get("mark") or 0) or None, "index": float(d.get("indexPrice") or prev.get("index") or 0) or None,
                                                      "funding": float(d.get("fundingRate") or prev.get("funding") or 0), "oi": float(d.get("openInterest") or prev.get("oi") or 0) or None, "ts_ms": int(m.get("ts"))})
                        if d.get("bid1Price") and d.get("ask1Price"):
                            state._push(state.quote, sym, {"bid": float(d["bid1Price"]), "ask": float(d["ask1Price"]), "bq": float(d.get("bid1Size") or 0), "aq": float(d.get("ask1Size") or 0), "ts_ms": int(m.get("ts"))})
                        stats["bybit_tickers"] = stats.get("bybit_tickers", 0) + 1
                    elif topic.startswith("allLiquidation."):
                        for d in m.get("data", []):
                            sym = d["s"]; event_ms = int(d.get("T") or m.get("ts")); side = "sell" if d["S"] == "Sell" else "buy"
                            price = float(d["p"]); qty = float(d["v"])
                            extra = state.enrich("bybit", sym, event_ms); extra.update({"source_stream": "allLiquidation", "throttle_note": "bybit aggregates liquidations per second"})
                            sink.write("bybit", base_record("bybit", sym, event_ms, recv_ns, side, price, qty, extra))
                            stats["bybit_liquidations"] = stats.get("bybit_liquidations", 0) + 1
                    elif m.get("op") == "subscribe" and not m.get("success"):
                        log.error("bybit subscribe refused: %s", m.get("ret_msg"))
        except (asyncio.TimeoutError, websockets.ConnectionClosed, OSError) as e:
            log.warning("bybit ws: %s -> reconnect", type(e).__name__); await asyncio.sleep(3)
        except asyncio.CancelledError:
            return


# ------------------------------------------------------------------ export

def export(out_root: Path, dest: Path):
    from data_lake.collectors.tape_io import iter_lines          # lecteur tolerant aux partitions vivantes
    n = 0
    with open(dest, "w", encoding="utf-8") as f:
        for p in sorted(out_root.rglob("events-*.jsonl.gz")):
            for line in iter_lines(p):
                f.write(line + "\n"); n += 1
    print(f"-> {dest} ({n} liquidations)")


async def run(args):
    out_root = Path(args.out); sink = GzipJsonlSink(out_root); state = MarketState(); stats = {}; stop = asyncio.Event()
    guard = DiskGuard(out_root, args.min_free_disk_gb, args.disk_budget_gb)
    try:
        syms = bybit_symbols(BYBIT_TOP_N)
    except Exception as e:
        log.error("bybit symbols: %s", e); syms = []
    tasks = [asyncio.create_task(binance_market(state, sink, stats, stop)), asyncio.create_task(binance_public(state, stats, stop))]
    if syms:
        tasks.append(asyncio.create_task(bybit(state, sink, stats, stop, syms)))
    loop = asyncio.get_running_loop()
    for s in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(s, stop.set)
    t0 = time.time(); last = 0
    try:
        while not stop.is_set():
            await asyncio.sleep(1)
            if args.seconds and time.time() - t0 >= args.seconds:
                stop.set()
            if time.time() - last >= 60:
                last = time.time(); log.info("stats %s", stats)
                try: guard.check()
                except RuntimeError as e: log.error("%s -> stop", e); stop.set()
    finally:
        for t in tasks: t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True); sink.close()
        print(json.dumps({"stats": stats, "seconds": round(time.time() - t0), "bybit_symbols": len(syms), "out": str(out_root)}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_ROOT)); ap.add_argument("--seconds", type=int, default=0, help="0 = service")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--export", action="store_true")
    ap.add_argument("--disk-budget-gb", type=float, default=4.0); ap.add_argument("--min-free-disk-gb", type=float, default=15.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.export:
        export(Path(a.out), EXPORT); return
    asyncio.run(run(a))


if __name__ == "__main__":
    main()
