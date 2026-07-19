"""
src/institutional/execution/maker_fill_probe.py
─────────────────────────────────────────────────────────────────────────────
SONDE DE FILLS MAKER — mesure EMPIRIQUE du levier post-only, sans ordre réel.

Contexte : les backtests embarquent 14 bps/trade ; l'exécution maker (~2 bps)
récupérerait +2 à +3,7 %/an, MAIS le taux de fill et la sélection adverse d'un
ordre post-only ne se simulent pas depuis des chandelles. Cette sonde les
MESURE sur le carnet réel (le WS bookTicker Binance futures passe depuis cet
hôte ; aggTrade/markPrice restent filtrés — vérifié 2026-07-12).

Protocole (déclaré) :
  • toutes les PLACE_INTERVAL (30 s) par symbole : ordre virtuel post-only
    BUY au best bid + SELL au best ask (join du touch, dernier de la file) ;
  • fill CONSERVATEUR (position de file inconnue → pire cas) :
      BUY rempli  ⟺ best_ask < limite (le carnet a traversé le niveau) ;
      SELL rempli ⟺ best_bid > limite ;
  • expiration 600 s sans fill → enregistré unfilled (le taux de fill est LA
    donnée) ; après fill : mid à +60 s et +300 s → sélection adverse en bps ;
  • append-only parquet data/execution_probe/date=*/part-*.parquet.

Ce que ça donnera après quelques jours : fill_rate(symbole, horizon) et
adverse_bps → l'économie maker RÉELLE à mettre dans les backtests, mesurée,
pas décrétée.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = ROOT / "data" / "execution_probe"
WS_BASE = "wss://fstream.binance.com/stream?streams="
PLACE_INTERVAL_S = 30.0
ORDER_TTL_S = 600.0
POST_FILL_MARKS_S = (60.0, 300.0)
FLUSH_S = 300.0
STALL_S = 30.0

SYMBOLS = [
    # cœur du book
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT",
    # gagnants du stack événementiel (là où l'exécution maker paierait le plus)
    "ORDIUSDT", "FETUSDT", "PYTHUSDT", "ARUSDT", "TIAUSDT", "SUIUSDT",
]


@dataclass
class VirtualOrder:
    symbol: str
    side: str                  # "BUY" | "SELL"
    limit: float
    t_place: float             # loop time (s)
    ts_place: str              # ISO UTC
    bid_at_place: float
    ask_at_place: float
    t_fill: Optional[float] = None
    fill_marks: Dict[float, float] = field(default_factory=dict)  # delay -> mid

    def spread_bps(self) -> float:
        m = (self.bid_at_place + self.ask_at_place) / 2
        return (self.ask_at_place - self.bid_at_place) / m * 1e4 if m else 0.0


def check_fill(order: VirtualOrder, bid: float, ask: float) -> bool:
    """Règle conservatrice : rempli seulement si le carnet a TRAVERSÉ le niveau."""
    if order.side == "BUY":
        return ask < order.limit
    return bid > order.limit


def order_row(o: VirtualOrder, now_t: float) -> dict:
    mid0 = (o.bid_at_place + o.ask_at_place) / 2
    row = {
        "ts_place": o.ts_place, "symbol": o.symbol, "side": o.side,
        "limit": o.limit, "spread_bps": round(o.spread_bps(), 3),
        "filled": o.t_fill is not None,
        "ttf_s": round(o.t_fill - o.t_place, 2) if o.t_fill else None,
    }
    sign = 1 if o.side == "BUY" else -1
    for d in POST_FILL_MARKS_S:
        mid = o.fill_marks.get(d)
        # adverse < 0 : le mid a continué CONTRE nous après le fill
        row[f"adv_bps_{int(d)}s"] = (round(sign * (mid / o.limit - 1) * 1e4, 2)
                                     if (mid and o.t_fill) else None)
    row["mid_at_place"] = mid0
    return row


class MakerFillProbe:
    def __init__(self, symbols: List[str] = None, out_dir: Path = None):
        self.symbols = symbols or SYMBOLS
        self.out_dir = out_dir or OUT_DIR
        self.book: Dict[str, tuple] = {}          # symbol -> (bid, ask)
        self.open_orders: List[VirtualOrder] = []
        self.done_rows: List[dict] = []
        self.n_msgs = 0

    # ── logique pure (testable) ──────────────────────────────────────────────
    def on_book(self, symbol: str, bid: float, ask: float, now_t: float):
        self.book[symbol] = (bid, ask)
        still = []
        for o in self.open_orders:
            if o.symbol == symbol and o.t_fill is None and check_fill(o, bid, ask):
                o.t_fill = now_t
            # marks post-fill
            if o.t_fill is not None and o.symbol == symbol:
                for d in POST_FILL_MARKS_S:
                    if d not in o.fill_marks and now_t - o.t_fill >= d:
                        o.fill_marks[d] = (bid + ask) / 2
            # terminé ?
            expired = o.t_fill is None and (now_t - o.t_place) > ORDER_TTL_S
            complete = (o.t_fill is not None
                        and len(o.fill_marks) == len(POST_FILL_MARKS_S))
            if expired or complete:
                self.done_rows.append(order_row(o, now_t))
            else:
                still.append(o)
        self.open_orders = still

    def place_orders(self, now_t: float):
        ts = datetime.now(timezone.utc).isoformat()
        for s in self.symbols:
            ba = self.book.get(s)
            if not ba or ba[0] <= 0 or ba[1] <= 0:
                continue
            bid, ask = ba
            for side, limit in (("BUY", bid), ("SELL", ask)):
                self.open_orders.append(VirtualOrder(
                    symbol=s, side=side, limit=limit, t_place=now_t,
                    ts_place=ts, bid_at_place=bid, ask_at_place=ask))

    def flush(self):
        if not self.done_rows:
            return 0
        import pandas as pd
        now = datetime.now(timezone.utc)
        d = self.out_dir / f"date={now:%Y-%m-%d}"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"part-{now:%H%M%S}.parquet"
        pd.DataFrame(self.done_rows).to_parquet(p, index=False)
        n, self.done_rows = len(self.done_rows), []
        return n

    # ── boucle réseau ────────────────────────────────────────────────────────
    async def run(self):
        import websockets
        streams = "/".join(f"{s.lower()}@bookTicker" for s in self.symbols)
        url = WS_BASE + streams
        loop = asyncio.get_event_loop()
        last_place = last_flush = 0.0
        while True:
            try:
                ws = await asyncio.wait_for(
                    websockets.connect(url, ping_interval=15), timeout=15)
                print(f"[probe] connecté ({len(self.symbols)} bookTicker)", flush=True)
                while True:
                    try:
                        m = await asyncio.wait_for(ws.recv(), timeout=STALL_S)
                    except asyncio.TimeoutError:
                        print("[probe] stall bookTicker → reconnect", flush=True)
                        break
                    now_t = loop.time()
                    self.n_msgs += 1
                    try:
                        d = json.loads(m)["data"]
                        self.on_book(d["s"], float(d["b"]), float(d["a"]), now_t)
                    except (KeyError, ValueError):
                        continue
                    if now_t - last_place >= PLACE_INTERVAL_S:
                        self.place_orders(now_t)
                        last_place = now_t
                    if now_t - last_flush >= FLUSH_S:
                        n = self.flush()
                        if n:
                            print(f"[probe] flush {n} ordres · msgs {self.n_msgs} "
                                  f"· open {len(self.open_orders)}", flush=True)
                        last_flush = now_t
                await ws.close()
            except Exception as e:
                print(f"[probe] {type(e).__name__} {e} → retry 10s", flush=True)
                await asyncio.sleep(10)
