"""
src/institutional/data/derivatives_collector/collector.py
─────────────────────────────────────────────────────────────────────────────
Collecteur dérivés Binance Futures (Phase 1) — REST poll + WS forceOrder.

REST (périodique) : openInterest, premiumIndex (mark+funding), takerlongshortRatio,
globalLongShortAccountRatio.
WS (continu)      : !forceOrder@arr (LIQUIDATIONS — la donnée unique introuvable
                    en historique), markPrice.

Append-only immutable (writer.py). Health : compteurs + last-event timestamps.
Chaque jour collecté augmente la valeur des futurs moteurs Liquidation/Breakout.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List

from src.institutional.data.derivatives_collector.writer import write_records

logger = logging.getLogger("derivatives_collector")

REST_BASE = "https://fapi.binance.com"
WS_URL = "wss://fstream.binance.com/stream?streams=!forceOrder@arr"
# ⚠ DIAGNOSTIC 2026-07-03 : depuis cet hôte, fstream.binance.com ACCEPTE la connexion
# mais ne pousse JAMAIS de données (même btcusdt@aggTrade — géo-blocage silencieux,
# REST fapi OK). Le "0 liquidation market-wide" historique était CE blocage, pas un
# marché calme. → sources liquidations : Bybit allLiquidation (WS) + OKX REST poll.
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"
OKX_REST_BASE = "https://www.okx.com"


def _okx_get(path: str, timeout: float = 10.0):
    req = urllib.request.Request(OKX_REST_BASE + path, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _now_ms() -> int:
    return int(time.time() * 1000)


def _get(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class DerivativesCollector:
    def __init__(self, symbols: List[str], rest_interval_s: int = 300,
                 okx_interval_s: int = 60):
        self.symbols = symbols
        self.rest_interval_s = rest_interval_s
        self.okx_interval_s = okx_interval_s
        self.health = {"force_order_events": 0, "rest_polls": 0, "errors": 0,
                       "last_force_order": None, "last_rest": None,
                       "bybit_liq_events": 0, "last_bybit_liq": None,
                       "bybit_subscribed": 0,
                       "okx_liq_events": 0, "last_okx_liq": None}
        # dédup OKX : on ne collecte que les événements > démarrage - 1 min
        # (petit chevauchement possible après restart, dédupé côté event builder)
        self._okx_start_ms = _now_ms() - 60_000
        self._okx_last_ts: Dict[str, int] = {}
        self._okx_ctval: Dict[str, float] = {}

    # ── REST poller ────────────────────────────────────────────────────────────
    def _poll_rest_once(self) -> None:
        ts = _now_ms()
        for sym in self.symbols:
            try:
                oi = _get(f"{REST_BASE}/fapi/v1/openInterest?symbol={sym}")
                prem = _get(f"{REST_BASE}/fapi/v1/premiumIndex?symbol={sym}")
                recv = _now_ms()
                write_records("open_interest", sym, [{
                    "timestamp": ts, "recv_time": recv, "latency_ms": recv - ts, "symbol": sym,
                    "open_interest": float(oi["openInterest"]),
                    "mark_price": float(prem["markPrice"]),
                    "index_price": float(prem.get("indexPrice", 0) or 0),
                    "funding_rate": float(prem.get("lastFundingRate", 0) or 0),
                    "next_funding_time": int(prem.get("nextFundingTime", 0) or 0),
                }])
                try:
                    tk = _get(f"{REST_BASE}/futures/data/takerlongshortRatio?symbol={sym}&period=5m&limit=1")
                    lsr = _get(f"{REST_BASE}/futures/data/globalLongShortAccountRatio?symbol={sym}&period=5m&limit=1")
                    write_records("ratios", sym, [{
                        "timestamp": ts, "symbol": sym,
                        "taker_buy_sell_ratio": float(tk[0]["buySellRatio"]) if tk else None,
                        "global_long_short_ratio": float(lsr[0]["longShortRatio"]) if lsr else None,
                    }])
                except Exception as e:
                    logger.debug("ratios %s: %s", sym, e)
            except Exception as e:
                self.health["errors"] += 1
                logger.warning("REST poll %s échec: %s", sym, e)
        self.health["rest_polls"] += 1
        self.health["last_rest"] = datetime.now(timezone.utc).isoformat()

    async def _rest_loop(self, stop_after_s: float = None) -> None:
        t0 = time.time()
        while True:
            await asyncio.get_event_loop().run_in_executor(None, self._poll_rest_once)
            if stop_after_s and (time.time() - t0) >= stop_after_s:
                return
            await asyncio.sleep(self.rest_interval_s)

    # ── WS forceOrder (liquidations) ────────────────────────────────────────────
    async def _ws_loop(self, stop_after_s: float = None) -> None:
        import websockets
        t0 = time.time()
        while True:
            if stop_after_s and (time.time() - t0) >= stop_after_s:
                return
            try:
                # ping_interval/timeout détectent une connexion morte ; le marché calme
                # (aucune liquidation) N'EST PAS une déconnexion → on ne reconnecte pas dessus.
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                    while True:
                        if stop_after_s and (time.time() - t0) >= stop_after_s:
                            return
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            continue   # calme : pas d'événement, connexion maintenue par ping
                        msg = json.loads(raw)
                        o = msg.get("data", {}).get("o", {})
                        sym = o.get("s")
                        if sym in self.symbols:
                            evt = int(o.get("T", _now_ms())); recv = _now_ms()
                            write_records("force_order", sym, [{
                                "timestamp": evt, "recv_time": recv, "latency_ms": recv - evt,
                                "symbol": sym, "side": o.get("S"), "price": float(o.get("p", 0)),
                                "avg_price": float(o.get("ap", 0) or 0),
                                "qty": float(o.get("q", 0)),
                                "filled_qty": float(o.get("z", 0) or 0),
                                "usd": float(o.get("ap", o.get("p", 0)) or 0) * float(o.get("q", 0)),
                                "order_status": o.get("X"),
                            }])
                            self.health["force_order_events"] += 1
                            self.health["last_force_order"] = datetime.now(timezone.utc).isoformat()
                        if stop_after_s and (time.time() - t0) >= stop_after_s:
                            return
            except Exception as e:
                self.health["errors"] += 1
                logger.warning("WS forceOrder reconnect (%s)", e)
                if stop_after_s and (time.time() - t0) >= stop_after_s:
                    return
                await asyncio.sleep(5)

    # ── WS Bybit allLiquidation (source primaire liquidations, fstream géo-bloqué) ──
    async def _bybit_ws_loop(self, stop_after_s: float = None) -> None:
        """Collecte les liquidations via Bybit v5 allLiquidation.

        Normalisation side → convention Binance forceOrder (celle de l'event builder) :
        doc Bybit : « When you receive a Buy update, this means that a long position
        has been liquidated » → Bybit Buy = LONG liquidé = Binance "SELL" ;
        Bybit Sell = SHORT liquidé = Binance "BUY". `side_raw` conserve la valeur brute.
        Keep-alive : Bybit v5 exige un ping APPLICATIF {"op":"ping"} (~toutes les 15 s).
        """
        import websockets
        t0 = time.time()
        while True:
            if stop_after_s and (time.time() - t0) >= stop_after_s:
                return
            try:
                async with websockets.connect(BYBIT_WS_URL, ping_interval=None) as ws:
                    subscribed = 0
                    # ⚠ souscription INDIVIDUELLE : un topic invalide dans un batch fait
                    # échouer TOUT le batch chez Bybit (constaté : PEPEUSDT invalide a
                    # tué un paquet de 10). Fallback préfixe 1000 (1000PEPEUSDT…).
                    for s in self.symbols:
                        await ws.send(json.dumps({"op": "subscribe",
                                                  "args": [f"allLiquidation.{s}"]}))
                    last_ping = time.time()
                    while True:
                        if stop_after_s and (time.time() - t0) >= stop_after_s:
                            return
                        if time.time() - last_ping > 15:
                            await ws.send(json.dumps({"op": "ping"}))
                            last_ping = time.time()
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=15)
                        except asyncio.TimeoutError:
                            continue   # calme : le ping applicatif maintient la connexion
                        msg = json.loads(raw)
                        if msg.get("op") == "subscribe":
                            if msg.get("success"):
                                subscribed += 1
                                self.health["bybit_subscribed"] = subscribed
                            else:
                                ret = msg.get("ret_msg", "")
                                logger.warning("Bybit subscribe échec: %s", ret)
                                # "error:handler not found,topic:allLiquidation.PEPEUSDT"
                                if "topic:allLiquidation." in ret:
                                    sym = ret.split("topic:allLiquidation.")[-1].strip()
                                    if sym and not sym.startswith("1000"):
                                        await ws.send(json.dumps({
                                            "op": "subscribe",
                                            "args": [f"allLiquidation.1000{sym}"]}))
                            continue
                        topic = msg.get("topic", "")
                        if not topic.startswith("allLiquidation"):
                            continue
                        recv = _now_ms()
                        rows = []
                        for r in msg.get("data", []):
                            sym = r.get("s")
                            # accepte aussi les variantes fallback 1000<SYM> (stockées
                            # sous leur nom Bybit réel — pas de rescale de prix caché)
                            if sym not in self.symbols and not (
                                    sym and sym.startswith("1000") and sym[4:] in self.symbols):
                                continue
                            evt = int(r.get("T", recv))
                            price = float(r.get("p", 0) or 0)
                            qty = float(r.get("v", 0) or 0)
                            side_raw = r.get("S")  # Buy = long liquidé (doc Bybit)
                            rows.append({
                                "timestamp": evt, "recv_time": recv, "latency_ms": recv - evt,
                                "symbol": sym,
                                "side": "SELL" if side_raw == "Buy" else "BUY",
                                "side_raw": side_raw,
                                "price": price, "avg_price": price,
                                "qty": qty, "filled_qty": qty,
                                "usd": price * qty,
                                "order_status": "BYBIT_ALL_LIQUIDATION",
                            })
                        if rows:
                            sym = rows[0]["symbol"]
                            write_records("force_order", sym, rows,
                                          exchange="bybit", market="linear")
                            self.health["bybit_liq_events"] += len(rows)
                            self.health["last_bybit_liq"] = datetime.now(timezone.utc).isoformat()
            except Exception as e:
                self.health["errors"] += 1
                logger.warning("WS Bybit allLiquidation reconnect (%s)", e)
                if stop_after_s and (time.time() - t0) >= stop_after_s:
                    return
                await asyncio.sleep(5)

    # ── OKX REST liquidation-orders (2e source, prouvée accessible depuis cet hôte) ──
    def _okx_load_contract_values(self) -> Dict[str, float]:
        """ctVal par instId — ⚠ sz OKX est en CONTRATS (0.01 BTC, 0.1 ETH…) :
        sans conversion le notional USD serait faux de 10-100×."""
        out = {}
        try:
            d = _okx_get("/api/v5/public/instruments?instType=SWAP")
            for inst in d.get("data", []):
                try:
                    out[inst["instId"]] = float(inst.get("ctVal") or 0)
                except (TypeError, ValueError):
                    continue
        except Exception as e:
            logger.warning("OKX instruments échec: %s", e)
        return out

    def _poll_okx_liq_once(self) -> None:
        if not self._okx_ctval:
            self._okx_ctval = self._okx_load_contract_values()
        for sym in self.symbols:
            uly = sym.replace("USDT", "-USDT")   # BTCUSDT → BTC-USDT
            inst_id = f"{uly}-SWAP"
            ctval = self._okx_ctval.get(inst_id)
            if ctval is None:
                continue    # instrument absent chez OKX (loggé une fois au 1er cycle)
            try:
                d = _okx_get("/api/v5/public/liquidation-orders?instType=SWAP"
                             f"&state=filled&uly={uly}")
            except Exception as e:
                self.health["errors"] += 1
                logger.warning("OKX liq poll %s échec: %s", sym, e)
                continue
            last = self._okx_last_ts.get(sym, self._okx_start_ms)
            recv = _now_ms()
            rows = []
            for inst in d.get("data", []):
                if inst.get("instId") not in (inst_id, None):
                    continue
                for det in inst.get("details", []):
                    ts = int(det.get("ts") or 0)
                    if ts <= last:
                        continue
                    px = float(det.get("bkPx") or 0)
                    sz = float(det.get("sz") or 0)
                    pos_side = det.get("posSide")   # long|short — sans ambiguïté
                    rows.append({
                        "timestamp": ts, "recv_time": recv, "latency_ms": recv - ts,
                        "symbol": sym,
                        "side": "SELL" if pos_side == "long" else "BUY",
                        "side_raw": f"{det.get('side')}/{pos_side}",
                        "price": px, "avg_price": px,
                        "qty": sz * ctval, "filled_qty": sz * ctval,
                        "usd": sz * ctval * px,
                        "order_status": "OKX_LIQUIDATION_ORDER",
                    })
            if rows:
                self._okx_last_ts[sym] = max(r["timestamp"] for r in rows)
                write_records("force_order", sym, rows, exchange="okx", market="swap")
                self.health["okx_liq_events"] += len(rows)
                self.health["last_okx_liq"] = datetime.now(timezone.utc).isoformat()
            time.sleep(0.1)   # pacing rate-limit public OKX

    async def _okx_liq_loop(self, stop_after_s: float = None) -> None:
        t0 = time.time()
        while True:
            await asyncio.get_event_loop().run_in_executor(None, self._poll_okx_liq_once)
            if stop_after_s and (time.time() - t0) >= stop_after_s:
                return
            await asyncio.sleep(self.okx_interval_s)

    async def run(self, duration_s: float = None) -> None:
        logger.info("Collecteur démarré : %s (REST %ds, WS forceOrder Binance + "
                    "allLiquidation Bybit + OKX liq poll %ds)",
                    self.symbols, self.rest_interval_s, self.okx_interval_s)
        await asyncio.gather(self._rest_loop(duration_s), self._ws_loop(duration_s),
                             self._bybit_ws_loop(duration_s), self._okx_liq_loop(duration_s))
