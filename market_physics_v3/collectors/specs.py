from __future__ import annotations

import os


def _url(env_name: str, default: str) -> str:
    return os.environ.get(env_name, default).strip()


def subscriptions(venue, symbols):
    venue = str(venue).lower()
    syms = [s.upper() for s in symbols]
    if venue == "binance":
        public_streams = []
        market_streams = []
        for s in syms:
            x = s.lower()
            # Binance USD-M split its WebSocket routing in 2026. High-frequency
            # depth/bookTicker belongs on /public; aggregate trades,
            # mark/funding and forceOrder belong on /market. Keep aggTrade
            # explicit as aggregate granularity rather than calling it tick data.
            public_streams += [x + "@depth@100ms", x + "@bookTicker"]
            market_streams += [x + "@aggTrade", x + "@markPrice@1s", x + "@forceOrder"]
        return {
            "connections": [
                {
                    "name": "public",
                    "url": _url("MPV3_BINANCE_PUBLIC_WS_URL", "wss://fstream.binance.com/public/ws"),
                    "subscribe": {"method": "SUBSCRIBE", "params": public_streams, "id": 1},
                },
                {
                    "name": "market",
                    "url": _url("MPV3_BINANCE_MARKET_WS_URL", "wss://fstream.binance.com/market/ws"),
                    "subscribe": {"method": "SUBSCRIBE", "params": market_streams, "id": 2},
                },
            ],
        }
    if venue == "bybit":
        args = []
        for s in syms:
            args += ["orderbook.50." + s, "publicTrade." + s, "allLiquidation." + s, "tickers." + s]
        return {
            "url": _url("MPV3_BYBIT_WS_URL", "wss://stream.bybit.com/v5/public/linear"),
            "subscribe": {"op": "subscribe", "args": args},
        }
    if venue == "okx":
        args = []
        for s in syms:
            if s.endswith("USDT"):
                base = s[:-4]
                inst = base + "-USDT-SWAP"
                index_inst = base + "-USDT"
            else:
                inst = s
                index_inst = s
            args += [
                {"channel": "books", "instId": inst},
                {"channel": "bbo-tbt", "instId": inst},
                {"channel": "trades", "instId": inst},
                {"channel": "open-interest", "instId": inst},
                {"channel": "funding-rate", "instId": inst},
                {"channel": "mark-price", "instId": inst},
                {"channel": "index-tickers", "instId": index_inst},
            ]
        args.append({"channel": "liquidation-orders", "instType": "SWAP"})
        return {
            "url": _url("MPV3_OKX_WS_URL", "wss://ws.okx.com:8443/ws/v5/public"),
            "subscribe": {"op": "subscribe", "args": args},
        }
    if venue == "deribit":
        # Deribit perpetuals are coin-margined; only BTC and ETH exist as of
        # this writing (verified live: GET /public/get_instruments?currency=SOL
        # &kind=future returns an empty result, only SOL_USDC/SOL_ETH spot).
        # Symbols without a mapping are silently skipped, not fabricated.
        instrument_map = {"BTCUSDT": "BTC-PERPETUAL", "ETHUSDT": "ETH-PERPETUAL"}
        instruments = [instrument_map[s] for s in syms if s in instrument_map]
        channels = []
        for inst in instruments:
            channels += ["book." + inst + ".100ms", "trades." + inst + ".100ms", "ticker." + inst + ".100ms"]
        return {
            "url": _url("MPV3_DERIBIT_WS_URL", "wss://www.deribit.com/ws/api/v2"),
            "subscribe_many": [
                {"jsonrpc": "2.0", "id": 3600, "method": "public/subscribe", "params": {"channels": channels}},
                # Server pushes ~30s test_request pings after this; runtime.py's
                # _required_reply() answers them with public/test or Deribit
                # drops the connection.
                {"jsonrpc": "2.0", "id": 3601, "method": "public/set_heartbeat", "params": {"interval": 30}},
            ],
        }
    if venue == "hyperliquid":
        msgs = []
        for s in syms:
            coin = s.removesuffix("USDT")
            for typ in ["l2Book", "trades", "bbo", "activeAssetCtx"]:
                msgs.append({"method": "subscribe", "subscription": {"type": typ, "coin": coin}})
        return {
            "url": _url("MPV3_HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws"),
            "subscribe_many": msgs,
        }
    raise ValueError("unsupported venue: %s" % venue)
