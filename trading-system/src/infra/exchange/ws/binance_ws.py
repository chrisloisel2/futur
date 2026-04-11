"""
binance_ws.py — Client WebSocket Binance pour klines en temps réel
===================================================================

Fournit :
  - BinanceKlineStream : connexion async à Binance WebSocket kline
  - AutoReconnectMixin : reconnexion automatique sur déconnexion
  - KlineBar          : structure d'une barre OHLCV complète

Usage typique :
    stream = BinanceKlineStream("btcusdt", "1h")

    async def on_bar(bar: KlineBar):
        print(f"Nouvelle barre fermée : {bar.close}")

    await stream.run(on_bar)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)

# URL du WebSocket Binance (stream kline)
BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"


# ─────────────────────────────────────────────────────────────────────────────
# Structure de données — Barre OHLCV fermée
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KlineBar:
    """
    Représente une barre OHLCV complète reçue via WebSocket Binance.

    Attributs :
        symbol      : ex. "BTCUSDT"
        interval    : ex. "1h"
        open_time   : timestamp d'ouverture (ms)
        close_time  : timestamp de fermeture (ms)
        open        : prix d'ouverture
        high        : plus haut
        low         : plus bas
        close       : prix de clôture
        volume      : volume en base asset (BTC)
        quote_volume: volume en quote asset (USDT)
        n_trades    : nombre de trades dans la barre
        is_closed   : True si la barre est fermée (complète)
    """
    symbol      : str
    interval    : str
    open_time   : int
    close_time  : int
    open        : float
    high        : float
    low         : float
    close       : float
    volume      : float
    quote_volume: float
    n_trades    : int
    is_closed   : bool

    @classmethod
    def from_ws_message(cls, msg: dict) -> "KlineBar":
        """Parse un message WebSocket Binance kline."""
        k = msg["k"]
        return cls(
            symbol       = msg["s"],
            interval     = k["i"],
            open_time    = int(k["t"]),
            close_time   = int(k["T"]),
            open         = float(k["o"]),
            high         = float(k["h"]),
            low          = float(k["l"]),
            close        = float(k["c"]),
            volume       = float(k["v"]),
            quote_volume = float(k["q"]),
            n_trades     = int(k["n"]),
            is_closed    = bool(k["x"]),
        )

    def to_dict(self) -> dict:
        return {
            "symbol"      : self.symbol,
            "interval"    : self.interval,
            "open_time"   : self.open_time,
            "close_time"  : self.close_time,
            "open"        : self.open,
            "high"        : self.high,
            "low"         : self.low,
            "close"       : self.close,
            "volume"      : self.volume,
            "quote_volume": self.quote_volume,
            "n_trades"    : self.n_trades,
            "is_closed"   : self.is_closed,
        }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket Client — Binance Kline Stream
# ─────────────────────────────────────────────────────────────────────────────

class BinanceKlineStream:
    """
    Souscrit au stream kline Binance et émet les barres fermées.

    Paramètres :
        symbol          : "btcusdt" (minuscules)
        interval        : "1m", "5m", "15m", "1h", "4h", "1d"
        on_closed_bar   : callback async appelé sur chaque barre fermée
        max_reconnects  : nombre max de reconnexions (-1 = infini)
        reconnect_delay : délai initial entre reconnexions (secondes)
        ping_interval   : intervalle de ping pour maintenir la connexion
    """

    def __init__(
        self,
        symbol: str,
        interval: str,
        max_reconnects: int = -1,
        reconnect_delay: float = 3.0,
        max_reconnect_delay: float = 60.0,
        ping_interval: float = 20.0,
    ):
        self.symbol             = symbol.lower()
        self.interval           = interval
        self.max_reconnects     = max_reconnects
        self.reconnect_delay    = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.ping_interval      = ping_interval

        self._url               = f"{BINANCE_WS_BASE}/{self.symbol}@kline_{self.interval}"
        self._reconnect_count   = 0
        self._running           = False
        self._last_bar          : Optional[KlineBar] = None
        self._bars_received     = 0
        self._closed_bars       = 0

    @property
    def url(self) -> str:
        return self._url

    @property
    def stats(self) -> dict:
        return {
            "symbol"        : self.symbol,
            "interval"      : self.interval,
            "reconnects"    : self._reconnect_count,
            "bars_received" : self._bars_received,
            "closed_bars"   : self._closed_bars,
            "running"       : self._running,
        }

    async def run(
        self,
        on_closed_bar: Callable[[KlineBar], Awaitable[None]],
        on_raw: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> None:
        """
        Démarre le stream. Bloque jusqu'à arrêt ou max_reconnects dépassé.

        Callbacks :
            on_closed_bar(bar)  : appelé sur chaque barre FERMÉE (is_closed=True)
            on_raw(msg)         : optionnel, appelé sur chaque message brut
        """
        self._running = True
        current_delay = self.reconnect_delay

        while self._running:
            if self.max_reconnects >= 0 and self._reconnect_count > self.max_reconnects:
                logger.error(f"[ws] max_reconnects={self.max_reconnects} atteint, arrêt")
                break

            try:
                logger.info(f"[ws] Connexion à {self._url} …")
                async with websockets.connect(
                    self._url,
                    ping_interval=self.ping_interval,
                    ping_timeout=10,
                    open_timeout=15,
                    close_timeout=5,
                ) as ws:
                    self._reconnect_count = 0 if self._reconnect_count == 0 else self._reconnect_count
                    current_delay = self.reconnect_delay  # reset délai sur connexion réussie
                    logger.info(f"[ws] Connecté — {self.symbol}@kline_{self.interval}")

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                        except json.JSONDecodeError:
                            logger.warning(f"[ws] Message non-JSON reçu : {raw_msg[:100]}")
                            continue

                        # Callback brut (optionnel)
                        if on_raw:
                            await on_raw(msg)

                        # Filtre : seulement les messages kline
                        if msg.get("e") != "kline":
                            continue

                        try:
                            bar = KlineBar.from_ws_message(msg)
                        except (KeyError, ValueError) as e:
                            logger.warning(f"[ws] Erreur parsing kline : {e}")
                            continue

                        self._bars_received += 1
                        self._last_bar = bar

                        # N'émet que les barres fermées (complètes)
                        if bar.is_closed:
                            self._closed_bars += 1
                            logger.debug(f"[ws] Barre fermée : {bar.close} ({bar.interval})")
                            await on_closed_bar(bar)

            except ConnectionClosed as e:
                logger.warning(f"[ws] Connexion fermée : {e}")
            except WebSocketException as e:
                logger.warning(f"[ws] Erreur WebSocket : {e}")
            except OSError as e:
                logger.warning(f"[ws] Erreur réseau : {e}")
            except asyncio.CancelledError:
                logger.info("[ws] Stream annulé")
                break

            if not self._running:
                break

            self._reconnect_count += 1
            logger.info(f"[ws] Reconnexion #{self._reconnect_count} dans {current_delay:.1f}s …")
            await asyncio.sleep(current_delay)
            current_delay = min(current_delay * 1.5, self.max_reconnect_delay)

        logger.info("[ws] Stream arrêté")

    def stop(self) -> None:
        """Arrête le stream proprement."""
        self._running = False


# ─────────────────────────────────────────────────────────────────────────────
# Multi-stream (plusieurs paires/intervalles)
# ─────────────────────────────────────────────────────────────────────────────

class BinanceMultiKlineStream:
    """
    Souscrit à plusieurs streams kline simultanément via une seule connexion.

    Exemple :
        streams = ["btcusdt@kline_1h", "ethusdt@kline_1h"]
        URL : wss://stream.binance.com:9443/stream?streams=btcusdt@kline_1h/ethusdt@kline_1h
    """

    COMBINED_URL = "wss://stream.binance.com:9443/stream"

    def __init__(
        self,
        subscriptions: list[tuple[str, str]],    # [(symbol, interval), ...]
        max_reconnects: int = -1,
        reconnect_delay: float = 3.0,
        max_reconnect_delay: float = 60.0,
    ):
        self.subscriptions      = [(s.lower(), i) for s, i in subscriptions]
        self.max_reconnects     = max_reconnects
        self.reconnect_delay    = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self._running           = False
        self._reconnect_count   = 0

        streams_param = "/".join(f"{s}@kline_{i}" for s, i in self.subscriptions)
        self._url = f"{self.COMBINED_URL}?streams={streams_param}"

    async def run(
        self,
        on_closed_bar: Callable[[KlineBar], Awaitable[None]],
    ) -> None:
        """Démarre le multi-stream. Même interface que BinanceKlineStream."""
        self._running = True
        current_delay = self.reconnect_delay

        while self._running:
            if self.max_reconnects >= 0 and self._reconnect_count > self.max_reconnects:
                break
            try:
                async with websockets.connect(self._url, ping_interval=20) as ws:
                    self._reconnect_count = 0
                    current_delay = self.reconnect_delay
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        msg = json.loads(raw_msg)
                        data = msg.get("data", msg)
                        if data.get("e") != "kline":
                            continue
                        bar = KlineBar.from_ws_message(data)
                        if bar.is_closed:
                            await on_closed_bar(bar)
            except (ConnectionClosed, WebSocketException, OSError) as e:
                logger.warning(f"[ws-multi] Erreur : {e}")
            except asyncio.CancelledError:
                break

            if not self._running:
                break
            self._reconnect_count += 1
            await asyncio.sleep(min(current_delay * 1.5, self.max_reconnect_delay))

    def stop(self) -> None:
        self._running = False
