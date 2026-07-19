#!/usr/bin/env python3
"""
scripts/hl_metaorders_collector.py
─────────────────────────────────────────────────────────────────────────────
Collecteur LOCAL Hyperliquid pour la piste hyperliquid_metaorders — SANS AWS.
Uniquement les API publiques (probées 2026-07-18, HTTP 200 réels) :

  REST POST https://api.hyperliquid.xyz/info
    {"type":"metaAndAssetCtxs"}          → funding/premium/OI/volume/mark/oracle
    {"type":"l2Book","coin":C}           → carnet (niveaux {px,sz,n})
    {"type":"twapHistory","user":U}      → TWAPs d'un utilisateur (user-scopé,
                                           PAS de flux global public)
  WS   wss://api.hyperliquid.xyz/ws
    {"type":"trades","coin":C}           → tape avec adresses des 2 contreparties

Détection métaordres : la tape publique inclut `users` ; un utilisateur qui
exécute ≥ MIN_FILLS fills même sens/même coin espacés ~20-45 s est un candidat
TWAP → confirmation ground-truth via twapHistory(user) (dédupliqué, throttlé).

Sorties locales partitionnées, append-only (idempotent, reprise sans perte) :
  data/hyperliquid/{trades,l2,ctxs,twap}/date=YYYY-MM-DD/part-<ms>.parquet
  data/hyperliquid/state.json            (fraîcheur, compteurs, trous)

Aucun secret. Horloge UTC. Arrêt propre sur SIGTERM (flush final).
Service : deploy/systemd/futur-hl-collector.service (Restart=always).
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hyperliquid"
STATE = OUT / "state.json"

API = "https://api.hyperliquid.xyz/info"
WS_URL = "wss://api.hyperliquid.xyz/ws"
COINS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "AVAX", "LINK",
         "BNB", "LTC", "SUI", "HYPE"]
SCHEMA_V = 1
SOURCE = "hyperliquid_public_api"

L2_EVERY_S = 20
CTX_EVERY_S = 60
DETECT_EVERY_S = 30
FLUSH_EVERY_S = 120
TWAP_QUERY_COOLDOWN_S = 600
TRADE_BUFFER_S = 900            # fenêtre en mémoire pour la détection
MIN_FILLS = 4                   # fills même (user, coin, side) …
SPACING_S = (15.0, 60.0)        # … espacés médians dans cette plage
GAP_WARN_S = 300
DEPTH_LEVELS = 5

log = logging.getLogger("hl_collector")


def utc_ms() -> int:
    return int(time.time() * 1000)


def day_of(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


# ── parseurs purs (testés dans tests/test_hl_collector.py) ───────────────────
def parse_trades(msg: dict) -> List[dict]:
    """Message WS {'channel':'trades','data':[...]} → lignes normalisées."""
    if msg.get("channel") != "trades":
        return []
    rows = []
    for t in msg.get("data", []):
        try:
            rows.append({
                "schema_v": SCHEMA_V, "source": SOURCE,
                "coin": str(t["coin"]), "side": str(t["side"]),
                "px": float(t["px"]), "sz": float(t["sz"]),
                "time_ms": int(t["time"]), "tid": int(t["tid"]),
                "hash": str(t.get("hash", "")),
                "buyer": str(t.get("users", ["", ""])[0]),
                "seller": str(t.get("users", ["", ""])[1]),
            })
        except (KeyError, ValueError, TypeError, IndexError):
            log.warning("trade non parsable ignoré: %s", str(t)[:200])
    return rows


def dedup_new(rows: List[dict], seen: Set[int], key: str = "tid") -> List[dict]:
    """Ne garde que les lignes jamais vues (clé entière), met à jour `seen`."""
    out = []
    for r in rows:
        k = r[key]
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def l2_features(book: dict, ts_ms: int) -> Optional[dict]:
    """l2Book → meilleures limites, spread, profondeur/imbalance top-N."""
    try:
        bids, asks = book["levels"][0], book["levels"][1]
        bb, ba = float(bids[0]["px"]), float(asks[0]["px"])
        bid_depth = sum(float(x["px"]) * float(x["sz"]) for x in bids[:DEPTH_LEVELS])
        ask_depth = sum(float(x["px"]) * float(x["sz"]) for x in asks[:DEPTH_LEVELS])
        tot = bid_depth + ask_depth
        return {"schema_v": SCHEMA_V, "source": SOURCE,
                "coin": str(book["coin"]), "time_ms": ts_ms,
                "book_time_ms": int(book.get("time", ts_ms)),
                "best_bid": bb, "best_ask": ba, "mid": (bb + ba) / 2,
                "spread_bps": (ba - bb) / ((ba + bb) / 2) * 1e4,
                "bid_depth_usd": bid_depth, "ask_depth_usd": ask_depth,
                "imbalance": (bid_depth - ask_depth) / tot if tot else 0.0}
    except (KeyError, ValueError, TypeError, IndexError):
        return None


def parse_ctxs(payload: list, ts_ms: int, coins: List[str]) -> List[dict]:
    """metaAndAssetCtxs → une ligne par coin suivi (funding/OI/premium/…)."""
    try:
        universe = payload[0]["universe"]
        ctxs = payload[1]
    except (KeyError, IndexError, TypeError):
        return []
    keep = set(coins)
    rows = []
    for meta, ctx in zip(universe, ctxs):
        name = meta.get("name")
        if name not in keep:
            continue
        def f(k):
            v = ctx.get(k)
            try:
                return float(v) if v is not None else None
            except (ValueError, TypeError):
                return None
        rows.append({"schema_v": SCHEMA_V, "source": SOURCE,
                     "coin": name, "time_ms": ts_ms,
                     "funding": f("funding"), "open_interest": f("openInterest"),
                     "premium": f("premium"), "oracle_px": f("oraclePx"),
                     "mark_px": f("markPx"), "day_ntl_vlm": f("dayNtlVlm"),
                     "raw": json.dumps(ctx)})
    return rows


def detect_twap_users(trades: List[dict], now_ms: int,
                      window_s: int = TRADE_BUFFER_S) -> List[Tuple[str, str, str]]:
    """(user, coin, side) avec ≥ MIN_FILLS fills et espacement médian TWAP-like.
    `user` = buyer si side=='B' (agresseur acheteur), sinon seller — les slices
    TWAP sont exécutées taker côté utilisateur."""
    lo = now_ms - window_s * 1000
    groups: Dict[Tuple[str, str, str], List[int]] = {}
    for t in trades:
        if t["time_ms"] < lo:
            continue
        user = t["buyer"] if t["side"] == "B" else t["seller"]
        if not user:
            continue
        groups.setdefault((user, t["coin"], t["side"]), []).append(t["time_ms"])
    out = []
    for key, ts in groups.items():
        if len(ts) < MIN_FILLS:
            continue
        ts.sort()
        gaps = [(b - a) / 1000.0 for a, b in zip(ts, ts[1:])]
        gaps.sort()
        med = gaps[len(gaps) // 2]
        if SPACING_S[0] <= med <= SPACING_S[1]:
            out.append(key)
    return out


def parse_twap_history(user: str, payload: list, ts_ms: int) -> List[dict]:
    """twapHistory(user) → une ligne par TWAP (id = user+coin+timestamp)."""
    rows = []
    for item in payload or []:
        try:
            st = item.get("state", item)
            rows.append({"schema_v": SCHEMA_V, "source": SOURCE,
                         "user": user, "queried_ms": ts_ms,
                         "coin": str(st.get("coin", "")),
                         "side": "B" if st.get("side") in ("B", "buy", "Bid") else "A",
                         "sz": float(st.get("sz", 0) or 0),
                         "executed_sz": float(st.get("executedSz", 0) or 0),
                         "executed_ntl": float(st.get("executedNtl", 0) or 0),
                         "minutes": float(st.get("minutes", 0) or 0),
                         "start_ms": int(item.get("time", st.get("timestamp", 0)) or 0),
                         "status": (item.get("status", {}).get("status", "")
                                    if isinstance(item.get("status"), dict)
                                    else str(item.get("status", ""))),
                         "raw": json.dumps(item)})
        except (ValueError, TypeError):
            log.warning("twap non parsable: %s", str(item)[:200])
    return rows


DEDUP_KEYS = {"trades": ["tid"], "l2": ["coin", "time_ms"],
              "ctxs": ["coin", "time_ms"], "twap": ["user", "coin", "start_ms"]}


def read_table(table: str, root: Path = OUT):
    """Lecture idempotente : concatène les parts et déduplique sur la clé
    métier — les doublons inter-redémarrages sont éliminés ICI (les parts
    sont append-only, jamais réécrites)."""
    import pandas as pd
    parts = sorted((root / table).glob("date=*/part-*.parquet"))
    if not parts:
        return pd.DataFrame()
    df = pd.concat([pd.read_parquet(p) for p in parts], ignore_index=True)
    return df.drop_duplicates(subset=DEDUP_KEYS[table]).reset_index(drop=True)


# ── collecteur ───────────────────────────────────────────────────────────────
class Collector:
    def __init__(self, coins: List[str]):
        self.coins = coins
        self.buffers: Dict[str, List[dict]] = {
            "trades": [], "l2": [], "ctxs": [], "twap": []}
        self.seen_tids: Set[int] = set()
        self.seen_twap_ids: Set[str] = set()
        self.trade_window: List[dict] = []
        self.last_trade_ms: Dict[str, int] = {}
        self.last_twap_query: Dict[str, float] = {}
        self.counters = {"trades": 0, "l2": 0, "ctxs": 0, "twap": 0,
                         "ws_reconnects": 0, "gaps_warned": 0}
        self.stop = asyncio.Event()

    # — REST helper (aiohttp) —
    async def post(self, session, body: dict):
        async with session.post(API, json=body, timeout=20) as r:
            if r.status != 200:
                raise RuntimeError("HTTP %s sur %s" % (r.status, body.get("type")))
            return await r.json()

    # — tâche WS trades —
    async def ws_task(self):
        import websockets
        while not self.stop.is_set():
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    for c in self.coins:
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "subscription": {"type": "trades", "coin": c}}))
                    log.info("WS connecté, %d coins souscrits", len(self.coins))
                    async for raw in ws:
                        if self.stop.is_set():
                            break
                        rows = parse_trades(json.loads(raw))
                        new = dedup_new(rows, self.seen_tids)
                        if new:
                            self.buffers["trades"] += new
                            self.trade_window += new
                            self.counters["trades"] += len(new)
                            for t in new:
                                self.last_trade_ms[t["coin"]] = max(
                                    self.last_trade_ms.get(t["coin"], 0),
                                    t["time_ms"])
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.counters["ws_reconnects"] += 1
                log.warning("WS déconnecté (%s) — reconnexion dans 5 s", e)
                await asyncio.sleep(5)

    # — tâche REST l2 + ctxs —
    async def rest_task(self):
        import aiohttp
        last_l2, last_ctx = 0.0, 0.0
        async with aiohttp.ClientSession() as s:
            while not self.stop.is_set():
                now = time.time()
                try:
                    if now - last_ctx >= CTX_EVERY_S:
                        last_ctx = now
                        rows = parse_ctxs(await self.post(
                            s, {"type": "metaAndAssetCtxs"}), utc_ms(), self.coins)
                        self.buffers["ctxs"] += rows
                        self.counters["ctxs"] += len(rows)
                    if now - last_l2 >= L2_EVERY_S:
                        last_l2 = now
                        for c in self.coins:
                            book = await self.post(s, {"type": "l2Book", "coin": c})
                            row = l2_features(book, utc_ms())
                            if row:
                                self.buffers["l2"].append(row)
                                self.counters["l2"] += 1
                            await asyncio.sleep(0.25)   # lissage rate-limit
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    log.warning("REST erreur: %s", e)
                await asyncio.sleep(1.0)

    # — tâche détection TWAP + confirmation ground-truth —
    async def twap_task(self):
        import aiohttp
        async with aiohttp.ClientSession() as s:
            while not self.stop.is_set():
                try:
                    await asyncio.sleep(DETECT_EVERY_S)
                    now_ms = utc_ms()
                    lo = now_ms - TRADE_BUFFER_S * 1000
                    self.trade_window = [t for t in self.trade_window
                                         if t["time_ms"] >= lo]
                    for user, coin, side in detect_twap_users(
                            self.trade_window, now_ms):
                        if time.time() - self.last_twap_query.get(user, 0) \
                                < TWAP_QUERY_COOLDOWN_S:
                            continue
                        self.last_twap_query[user] = time.time()
                        payload = await self.post(
                            s, {"type": "twapHistory", "user": user})
                        rows = parse_twap_history(user, payload, now_ms)
                        fresh = []
                        for r in rows:
                            tid = "%s|%s|%s" % (r["user"], r["coin"], r["start_ms"])
                            if tid not in self.seen_twap_ids:
                                self.seen_twap_ids.add(tid)
                                fresh.append(r)
                        if fresh:
                            self.buffers["twap"] += fresh
                            self.counters["twap"] += len(fresh)
                            log.info("TWAP confirmés: %d (user %s… %s %s)",
                                     len(fresh), user[:8], coin, side)
                        await asyncio.sleep(0.5)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    log.warning("twap_task erreur: %s", e)

    # — flush parquet append-only + état —
    def flush(self):
        import pandas as pd
        wrote = {}
        for table, rows in self.buffers.items():
            if not rows:
                continue
            by_day: Dict[str, List[dict]] = {}
            for r in rows:
                by_day.setdefault(day_of(r["time_ms"] if "time_ms" in r
                                         else r["queried_ms"]), []).append(r)
            for day, chunk in by_day.items():
                d = OUT / table / ("date=%s" % day)
                d.mkdir(parents=True, exist_ok=True)
                p = d / ("part-%d.parquet" % utc_ms())
                pd.DataFrame(chunk).to_parquet(p, index=False)
                wrote[table] = wrote.get(table, 0) + len(chunk)
            self.buffers[table] = []
        now_ms = utc_ms()
        gaps = {c: round((now_ms - t) / 1000.0)
                for c, t in self.last_trade_ms.items()
                if now_ms - t > GAP_WARN_S * 1000}
        if gaps:
            self.counters["gaps_warned"] += 1
            log.warning("trous tape > %ds: %s", GAP_WARN_S, gaps)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "counters": self.counters,
            "last_trade_ms": self.last_trade_ms, "gaps_s": gaps,
            "coins": self.coins, "schema_v": SCHEMA_V}, indent=1))
        if wrote:
            log.info("flush: %s", wrote)

    async def flush_task(self):
        while not self.stop.is_set():
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=FLUSH_EVERY_S)
            except asyncio.TimeoutError:
                pass
            self.flush()

    async def run(self):
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self.stop.set)
        tasks = [asyncio.ensure_future(t()) for t in
                 (self.ws_task, self.rest_task, self.twap_task, self.flush_task)]
        await self.stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.flush()
        log.info("arrêt propre — compteurs: %s", self.counters)


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    asyncio.get_event_loop().run_until_complete(Collector(COINS).run())


if __name__ == "__main__":
    main()
