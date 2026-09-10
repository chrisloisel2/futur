#!/usr/bin/env python3
"""
microstructure_window.py -- capture lourde declenchee : l'etat microstructurel d'UN marche
autour d'un evenement (naissance, mort, stress), de t0 - pre_window a t0 + post_window.

Flux Binance USDS-M (SUBSCRIBE) : /public/ws -> <sym>@bookTicker, <sym>@depth20@100ms ;
/market/ws -> <sym>@aggTrade, <sym>@markPrice@1s ; REST : depth (1 000 niveaux) toutes les 5 s, openInterest
toutes les 5 s. Sorties append-only : flux bruts (jsonl.gz), snapshots d'etat a 1 s
(market_state_snapshot), manifeste (triggered_window_manifest) ecrit a la fin ET toutes les 60 s.
Pas d'ordre, pas de signal, pas de jointure alpha.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from data_lake.collectors.market_state_schema import (MARKET_STATE_SNAPSHOT, TAPE_ROOT, AppendOnlyJsonl, completeness,
                                                      make_manifest, make_snapshot, now_local, now_ns, raw_hash, sha256_file, validate)

BINANCE_WS = "wss://fstream.binance.com/market/ws"          # aggTrade, markPrice
BINANCE_PUBLIC_WS = "wss://fstream.binance.com/public/ws"   # bookTicker, depth20 (le /market/ws ne les sert pas ici)
FAPI = "https://fapi.binance.com/fapi/v1"
WINDOWS_ROOT = TAPE_ROOT / "windows"
BANDS = (10, 25, 50)


# ----------------------------------------------------------------------------- fonctions pures
def book_metrics(bids: List[Tuple[float, float]], asks: List[Tuple[float, float]]) -> Dict[str, Any]:
    """bids decroissants, asks croissants, (prix, qty). Profondeur en USD dans +-x bps du mid,
    desequilibre a 10 bps, et la bande la plus large REELLEMENT couverte par le carnet fourni."""
    if not bids or not asks:
        return {}
    b0, a0 = bids[0][0], asks[0][0]; mid = (b0 + a0) / 2.0
    out: Dict[str, Any] = {"mid": mid, "bid": b0, "ask": a0, "spread_bps": (a0 - b0) / mid * 1e4}
    for x in BANDS:
        lo, hi = mid * (1 - x / 1e4), mid * (1 + x / 1e4)
        out[f"{x}_bid"] = sum(p * q for p, q in bids if p >= lo); out[f"{x}_ask"] = sum(p * q for p, q in asks if p <= hi)
    covered = 0
    for x in BANDS:
        if bids[-1][0] <= mid * (1 - x / 1e4) and asks[-1][0] >= mid * (1 + x / 1e4):
            covered = x
    out["covered_bps"] = covered
    for x in BANDS:
        if x > covered:
            out[f"{x}_bid"] = None; out[f"{x}_ask"] = None     # carnet trop court : on ne devine pas
    b10, a10 = out.get("10_bid"), out.get("10_ask")
    out["imbalance_10"] = ((b10 - a10) / (b10 + a10)) if (b10 is not None and a10 is not None and (b10 + a10) > 0) else None
    return out


def parse_levels(levels: List[List[str]]) -> List[Tuple[float, float]]:
    return [(float(p), float(q)) for p, q in levels if float(q) > 0]


def window_bounds(t0: Optional[str], pre_window_s: int, post_window_s: int, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Debut = maintenant ; fin = t0 + post_window si t0 connu, sinon maintenant + post_window.
    pre_window_missing = vrai si on demarre apres t0 (ou t0 inconnu)."""
    now = now or datetime.now(timezone.utc)
    if t0:
        t0d = datetime.fromisoformat(t0.replace("Z", "+00:00"))
        end = t0d.timestamp() + post_window_s
        pre_missing = now.timestamp() >= t0d.timestamp()
        pre_actual = max(0.0, min(pre_window_s, t0d.timestamp() - now.timestamp()))
    else:
        t0d = now; end = now.timestamp() + post_window_s; pre_missing = True; pre_actual = 0.0
    return {"t0": t0d.isoformat(timespec="seconds"), "start": now.isoformat(timespec="seconds"), "end_epoch": end,
            "pre_window_missing": pre_missing, "pre_window_actual_s": pre_actual, "post_window_s": post_window_s}


# ----------------------------------------------------------------------------- capture
class RestPacer:
    def __init__(self, budget_per_min: int = 200):
        self.budget = budget_per_min; self.spent: Deque[Tuple[float, int]] = deque()

    async def take(self, weight: int):
        while True:
            now = time.time()
            while self.spent and now - self.spent[0][0] > 60:
                self.spent.popleft()
            if sum(w for _, w in self.spent) + weight <= self.budget:
                self.spent.append((now, weight)); return
            await asyncio.sleep(0.5)


def _get(url: str, timeout: int = 20):
    with urlopen(Request(url, headers={"User-Agent": "futur-market-state"}), timeout=timeout) as r:
        return json.loads(r.read())


class WindowCapture:
    def __init__(self, venue: str, symbol: str, trigger_type: str, reason: str, t0: Optional[str], pre_window_s: int, post_window_s: int,
                 trigger_id: Optional[str] = None, root: Path = WINDOWS_ROOT, prereg_link: Optional[str] = None, rest_budget: int = 200):
        self.venue, self.symbol, self.trigger_type, self.reason = venue, symbol, trigger_type, reason
        self.trigger_id = trigger_id or f"{trigger_type}_{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.bounds = window_bounds(t0, pre_window_s, post_window_s); self.prereg_link = prereg_link
        self.dir = Path(root) / self.trigger_id; self.dir.mkdir(parents=True, exist_ok=True)
        self.raw = AppendOnlyJsonl(self.dir, gz=True); self.snap = AppendOnlyJsonl(self.dir, gz=False)
        self.pacer = RestPacer(rest_budget); self.stop = asyncio.Event()
        self.bbo: Optional[dict] = None; self.book_ws: Optional[dict] = None; self.book_rest: Optional[dict] = None; self.mark: Optional[dict] = None; self.oi: Optional[dict] = None
        self.trades: Deque[Tuple[int, float, float]] = deque(); self.lat: Deque[float] = deque(maxlen=200)
        self.first = {"first_orderbook_ts": None, "first_trade_ts": None, "first_mark_ts": None, "first_index_ts": None, "first_oi_ts": None, "first_bookticker_ts": None}
        self.msgs = {"bookTicker": 0, "aggTrade": 0, "depth20": 0, "markPrice": 0, "rest_depth": 0, "rest_oi": 0, "ws_reconnects": 0}
        self.snapshots: List[dict] = []

    # ---- flux
    def _note_latency(self, event_ms: Optional[int]):
        if event_ms:
            self.lat.append(now_ns() / 1e6 - event_ms)

    def _first(self, key: str, event_ms: Optional[int]):
        if self.first[key] is None and event_ms:
            self.first[key] = datetime.fromtimestamp(event_ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds")

    async def _ws(self, url: str, streams: List[str], sub_id: int):
        """Une connexion par famille de flux : /public/ws sert bookTicker et depth20, /market/ws sert aggTrade et markPrice."""
        import websockets
        while not self.stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, max_size=2**23) as ws:
                    await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": sub_id}))
                    while not self.stop.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            await ws.send(json.dumps({"method": "SUBSCRIBE", "params": streams, "id": sub_id + 100}))   # symbole pas encore vivant : on se reabonne
                            continue
                        rn = now_ns(); d = json.loads(msg); d = d.get("data", d); e = d.get("e")
                        if e == "bookTicker":
                            self.bbo = {"bid": float(d["b"]), "bq": float(d["B"]), "ask": float(d["a"]), "aq": float(d["A"]), "E": d.get("E"), "T": d.get("T")}
                            self.msgs["bookTicker"] += 1; self._note_latency(d.get("E")); self._first("first_bookticker_ts", d.get("E") or d.get("T"))
                        elif e == "aggTrade":
                            self.trades.append((int(d["T"]), float(d["p"]), float(d["q"]))); self.msgs["aggTrade"] += 1; self._note_latency(d.get("E")); self._first("first_trade_ts", d.get("T"))
                        elif e == "depthUpdate":
                            self.book_ws = {"bids": parse_levels(d.get("b", [])), "asks": parse_levels(d.get("a", [])), "E": d.get("E"), "T": d.get("T")}
                            self.msgs["depth20"] += 1; self._note_latency(d.get("E")); self._first("first_orderbook_ts", d.get("E") or d.get("T"))
                        elif e == "markPriceUpdate":
                            self.mark = {"mark": float(d["p"]), "index": float(d["i"]) if d.get("i") not in (None, "") else None, "funding": float(d["r"]) if d.get("r") not in (None, "") else None, "E": d.get("E"), "next_funding": d.get("T")}
                            self.msgs["markPrice"] += 1; self._note_latency(d.get("E")); self._first("first_mark_ts", d.get("E"))
                            if self.mark["index"] is not None:
                                self._first("first_index_ts", d.get("E"))
                        else:
                            continue
                        self.raw.write("raw_" + (e or "other"), {"recv_ns": rn, "msg": d}, venue=self.venue, name=e or "other")
            except Exception:
                self.msgs["ws_reconnects"] += 1
                await asyncio.sleep(2)

    async def ws_public_loop(self):
        s = self.symbol.lower(); await self._ws(BINANCE_PUBLIC_WS, [f"{s}@bookTicker", f"{s}@depth20@100ms"], 1)

    async def ws_market_loop(self):
        s = self.symbol.lower(); await self._ws(BINANCE_WS, [f"{s}@aggTrade", f"{s}@markPrice@1s"], 2)

    async def rest_loop(self):
        while not self.stop.is_set():
            try:
                await self.pacer.take(20); d = await asyncio.get_event_loop().run_in_executor(None, _get, f"{FAPI}/depth?symbol={self.symbol}&limit=1000")
                self.book_rest = {"bids": parse_levels(d["bids"]), "asks": parse_levels(d["asks"]), "E": d.get("E"), "T": d.get("T")}; self.msgs["rest_depth"] += 1
                self._first("first_orderbook_ts", d.get("E")); self.raw.write("raw_rest_depth", {"recv_ns": now_ns(), "msg": d}, venue=self.venue, name="rest_depth")
            except Exception:
                pass
            await asyncio.sleep(5)

    async def oi_loop(self):
        while not self.stop.is_set():
            try:
                await self.pacer.take(1); d = await asyncio.get_event_loop().run_in_executor(None, _get, f"{FAPI}/openInterest?symbol={self.symbol}")
                self.oi = {"oi": float(d["openInterest"]), "time": d.get("time")}; self.msgs["rest_oi"] += 1; self._first("first_oi_ts", d.get("time"))
                self.raw.write("raw_rest_oi", {"recv_ns": now_ns(), "msg": d}, venue=self.venue, name="rest_oi")
            except Exception:
                pass
            await asyncio.sleep(5)

    # ---- snapshot a 1 s
    def build_snapshot(self) -> Optional[dict]:
        # bandes de profondeur : le carnet REST (1 000 niveaux) tant qu'il a moins de 10 s ; sinon le depth20 WS (20 niveaux, couverture faible, dite)
        now_ms = now_ns() // 1_000_000
        rest_fresh = bool(self.book_rest) and (now_ms - (self.book_rest.get("E") or self.book_rest.get("T") or 0)) <= 10_000
        book = self.book_rest if rest_fresh else (self.book_ws or self.book_rest)
        if book is None and self.bbo is None:
            return None
        m = book_metrics(book["bids"], book["asks"]) if book else {}
        bid = self.bbo["bid"] if self.bbo else m.get("bid"); ask = self.bbo["ask"] if self.bbo else m.get("ask")
        cutoff = now_ns() // 1_000_000 - 60_000
        while self.trades and self.trades[0][0] < cutoff:
            self.trades.popleft()
        vol = sum(p * q for _, p, q in self.trades) if self.trades else 0.0
        ts_ex = max([x for x in ((self.bbo or {}).get("E"), (book or {}).get("E"), (self.mark or {}).get("E")) if x], default=None)
        rec = make_snapshot(self.venue, self.symbol, "triggered_capture", ts_exchange=datetime.fromtimestamp(ts_ex / 1000, tz=timezone.utc).isoformat(timespec="milliseconds") if ts_ex else None,
                            bid=bid, ask=ask, depth=m, mark_price=(self.mark or {}).get("mark"), index_price=(self.mark or {}).get("index"), funding_rate=(self.mark or {}).get("funding"),
                            open_interest=(self.oi or {}).get("oi"), volume_1m=vol, trade_count_1m=len(self.trades),
                            latency_ms=(statistics.median(self.lat) if self.lat else None), raw={"bbo": self.bbo, "book_E": (book or {}).get("E"), "mark": self.mark, "oi": self.oi},
                            extra={"trigger_id": self.trigger_id, "depth_source": ("rest" if book is self.book_rest else "ws_depth20") if book else None, "depth_covered_bps": m.get("covered_bps"),
                                   "book_levels": (len(book["bids"]), len(book["asks"])) if book else None})
        return rec

    async def snapshot_loop(self):
        last_manifest = time.time()
        while not self.stop.is_set():
            rec = self.build_snapshot()
            if rec:
                self.snap.write("market_state_snapshot", rec, venue=self.venue, name="snapshots"); self.snapshots.append({k: rec.get(k) for k in MARKET_STATE_SNAPSHOT})
            if time.time() - last_manifest > 60:
                self.write_manifest(final=False); last_manifest = time.time()
            if time.time() >= self.bounds["end_epoch"]:
                self.stop.set()
            await asyncio.sleep(1)

    def write_manifest(self, final: bool) -> dict:
        self.raw.flush(); self.snap.flush()
        files = sorted(p for p in self.dir.rglob("*") if p.is_file() and p.name != "manifest.json")
        rel = [str(p.relative_to(self.dir)) for p in files]
        sha = {r: sha256_file(self.dir / r) for r in rel} if final else {}
        comp = completeness(self.snapshots, [f for f in MARKET_STATE_SNAPSHOT if f not in ("venue", "symbol", "ts_local", "source", "raw_hash")])
        man = make_manifest(self.trigger_id, self.trigger_type, self.venue, self.symbol, self.bounds["start"], now_local() if final else None, self.reason, rel,
                            {**self.raw.counts, **self.snap.counts, "snapshots": len(self.snapshots)}, sha, comp["score"], comp["missing_fields"], prereg_link=self.prereg_link,
                            extra={"t0": self.bounds["t0"], "pre_window_missing": self.bounds["pre_window_missing"], "pre_window_actual_s": self.bounds["pre_window_actual_s"],
                                   "post_window_s": self.bounds["post_window_s"], "first_timestamps": self.first, "message_counts": self.msgs, "completeness_per_field": comp["per_field"],
                                   "final": final, "latency_ms_median": (statistics.median(self.lat) if self.lat else None)})
        (self.dir / "manifest.json").write_text(json.dumps(man, indent=1, ensure_ascii=False, default=str))
        return man

    async def run(self) -> dict:
        tasks = [asyncio.ensure_future(c()) for c in (self.ws_public_loop, self.ws_market_loop, self.rest_loop, self.oi_loop, self.snapshot_loop)]
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.raw.close(); self.snap.close()
        return self.write_manifest(final=True)


def capture(venue: str, symbol: str, trigger_type: str, reason: str = "", t0: Optional[str] = None, pre_window_s: int = 1800, post_window_s: int = 6 * 3600,
            trigger_id: Optional[str] = None, prereg_link: Optional[str] = None, root: Path = WINDOWS_ROOT) -> dict:
    validate("triggered_window_manifest", {"trigger_id": "x", "trigger_type": trigger_type, "venue": venue, "symbol": symbol, "start_ts": "", "end_ts": None, "reason": reason, "prereg_link": prereg_link,
                                           "files_written": [], "row_counts": {}, "sha256": {}, "completeness_score": 0.0, "missing_fields": [], "no_alpha_test": True})
    w = WindowCapture(venue, symbol, trigger_type, reason, t0, pre_window_s, post_window_s, trigger_id=trigger_id, root=root, prereg_link=prereg_link)
    return asyncio.get_event_loop().run_until_complete(w.run())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", required=True); ap.add_argument("--trigger-type", default="manual"); ap.add_argument("--reason", default="manual capture")
    ap.add_argument("--t0", default=None, help="ISO UTC ; si absent, pre_window_missing=true"); ap.add_argument("--pre-window", type=int, default=1800); ap.add_argument("--post-window", type=int, default=6 * 3600)
    ap.add_argument("--venue", default="binance"); a = ap.parse_args()
    man = capture(a.venue, a.symbol, a.trigger_type, a.reason, a.t0, a.pre_window, a.post_window)
    print(json.dumps({k: man[k] for k in ("trigger_id", "start_ts", "end_ts", "row_counts", "completeness_score", "missing_fields", "first_timestamps", "message_counts")}, indent=1, default=str))


if __name__ == "__main__":
    main()
