from __future__ import annotations

import os


def _url(env_name: str, default: str) -> str:
    return os.environ.get(env_name, default).strip()


def subscriptions(venue, symbols):
    venue = str(venue).lower()
    syms = [s.upper() for s in symbols]
    if venue == "binance":
        streams = []
        for s in syms:
            x = s.lower()
            # USD-M exposes aggregate taker-order trades rather than a guaranteed
            # one-row-per-match tape. Preserve that granularity explicitly.
            streams += [x + "@depth@100ms", x + "@aggTrade", x + "@bookTicker", x + "@markPrice@1s", x + "@forceOrder"]
        return {
            "url": _url("MPV3_BINANCE_WS_URL", "wss://fstream.binance.com/ws"),
            "subscribe": {"method": "SUBSCRIBE", "params": streams, "id": 1},
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
                # OKX index-tickers uses the underlying index identifier
                # (e.g. BTC-USDT), not the SWAP instrument id.
                {"channel": "index-tickers", "instId": index_inst},
            ]
        # Liquidation orders are subscribed by instrument type, not duplicated per symbol.
        args.append({"channel": "liquidation-orders", "instType": "SWAP"})
        return {
            "url": _url("MPV3_OKX_WS_URL", "wss://ws.okx.com:8443/ws/v5/public"),
            "subscribe": {"op": "subscribe", "args": args},
        }
    if venue == "hyperliquid":
        msgs = []
        for s in syms:
            coin = s[:-4] if s.endswith("USDT") else s
            for typ in ["l2Book", "trades", "bbo", "activeAssetCtx"]:
                msgs.append({"method": "subscribe", "subscription": {"type": typ, "coin": coin}})
        return {
            "url": _url("MPV3_HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws"),
            "subscribe_many": msgs,
        }
    raise ValueError("unsupported venue: %s" % venue)
