#!/usr/bin/env python3
"""
scripts/fetch_account_fees.py -- les frais REELS du compte, par venue (P1.1, item 5).

Un bareme publie n'est pas le frais d'un compte : le tier, les remises (BNB, HYPE,
parrainage) et les programmes hors bareme ne se lisent que sur les endpoints
compte. Ce script les interroge, en LECTURE SEULE, et ecrit
    data_lake/manifests/account_fees_<date>.json
avec source_class = "account_actual". Il ne passe aucun ordre.

Sans cle dans l'environnement il n'appelle rien et documente ce qu'il aurait fait :
c'est deliberement inerte. Cles attendues (jamais dans le depot) :
    BINANCE_API_KEY / BINANCE_API_SECRET
    OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE
    BYBIT_API_KEY / BYBIT_API_SECRET
    HYPERLIQUID_USER_ADDRESS   (userFees est public : il ne faut que l'adresse)

Endpoints (documentation officielle de chaque venue) :
    Binance USDT-M  GET  /fapi/v1/commissionRate?symbol=       HMAC-SHA256 sur la query
    OKX             GET  /api/v5/account/trade-fee?instType=SWAP HMAC-SHA256 base64(ts+method+path), passphrase
    Bybit v5        GET  /v5/account/fee-rate?category=linear   HMAC-SHA256(ts+key+recvWindow+query)
    Hyperliquid     POST /info {"type":"userFees","user":<addr>} sans signature
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data_lake" / "manifests"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _get(url, headers=None, data=None, timeout=20):
    req = urllib.request.Request(url, headers=headers or {}, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def binance():
    k, s = os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET")
    if not (k and s):
        return {"status": "skipped_no_key", "endpoint": "GET https://fapi.binance.com/fapi/v1/commissionRate"}
    out = {}
    for sym in SYMBOLS:
        q = urllib.parse.urlencode({"symbol": sym, "timestamp": int(time.time() * 1000), "recvWindow": 5000})
        sig = hmac.new(s.encode(), q.encode(), hashlib.sha256).hexdigest()
        d = _get(f"https://fapi.binance.com/fapi/v1/commissionRate?{q}&signature={sig}", {"X-MBX-APIKEY": k})
        out[sym] = {"maker_bps": float(d["makerCommissionRate"]) * 1e4, "taker_bps": float(d["takerCommissionRate"]) * 1e4}
    return {"status": "ok", "endpoint": "GET /fapi/v1/commissionRate", "fees": out}


def okx():
    k, s, p = os.getenv("OKX_API_KEY"), os.getenv("OKX_API_SECRET"), os.getenv("OKX_API_PASSPHRASE")
    if not (k and s and p):
        return {"status": "skipped_no_key", "endpoint": "GET https://www.okx.com/api/v5/account/trade-fee?instType=SWAP"}
    path = "/api/v5/account/trade-fee?instType=SWAP"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
    sig = base64.b64encode(hmac.new(s.encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()).decode()
    d = _get("https://www.okx.com" + path, {"OK-ACCESS-KEY": k, "OK-ACCESS-SIGN": sig, "OK-ACCESS-TIMESTAMP": ts,
                                            "OK-ACCESS-PASSPHRASE": p, "Content-Type": "application/json"})
    r = d["data"][0]
    # OKX renvoie des taux NEGATIFS pour des frais payes (convention inverse) ; -x => on paie x
    return {"status": "ok", "endpoint": "GET /api/v5/account/trade-fee", "level": r.get("level"),
            "fees": {"maker_bps": -float(r["maker"]) * 1e4, "taker_bps": -float(r["taker"]) * 1e4,
                     "convention": "signe inverse par rapport a la reponse brute OKX"}}


def bybit():
    k, s = os.getenv("BYBIT_API_KEY"), os.getenv("BYBIT_API_SECRET")
    if not (k and s):
        return {"status": "skipped_no_key", "endpoint": "GET https://api.bybit.com/v5/account/fee-rate?category=linear"}
    out = {}
    for sym in SYMBOLS:
        q = f"category=linear&symbol={sym}"; ts = str(int(time.time() * 1000)); rw = "5000"
        sig = hmac.new(s.encode(), (ts + k + rw + q).encode(), hashlib.sha256).hexdigest()
        d = _get(f"https://api.bybit.com/v5/account/fee-rate?{q}", {"X-BAPI-API-KEY": k, "X-BAPI-SIGN": sig,
                                                                     "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": rw})
        r = d["result"]["list"][0]
        out[sym] = {"maker_bps": float(r["makerFeeRate"]) * 1e4, "taker_bps": float(r["takerFeeRate"]) * 1e4}
    return {"status": "ok", "endpoint": "GET /v5/account/fee-rate", "fees": out}


def hyperliquid():
    a = os.getenv("HYPERLIQUID_USER_ADDRESS")
    if not a:
        return {"status": "skipped_no_address", "endpoint": "POST https://api.hyperliquid.xyz/info {type:userFees}"}
    d = _get("https://api.hyperliquid.xyz/info", {"Content-Type": "application/json"},
             json.dumps({"type": "userFees", "user": a}).encode())
    return {"status": "ok", "endpoint": "POST /info userFees",
            "fees": {"maker_bps": float(d.get("userAddRate", "nan")) * 1e4, "taker_bps": float(d.get("userCrossRate", "nan")) * 1e4,
                     "raw_keys": sorted(d.keys())}}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "source_class": "account_actual", "read_only": True, "venues": {}}
    for name, fn in (("binance", binance), ("okx", okx), ("bybit", bybit), ("hyperliquid", hyperliquid)):
        try:
            res["venues"][name] = fn()
        except Exception as e:
            res["venues"][name] = {"status": "error", "error": str(e)[:200]}
        print(f"  {name:12s} {res['venues'][name]['status']}")
    done = [v for v in res["venues"].values() if v["status"] == "ok"]
    if not done:
        print("  aucune cle : rien n'a ete appele, aucun manifeste ecrit (inerte par construction)")
        return 0
    p = OUT / f"account_fees_{res['fetched_at_utc'][:10]}.json"
    p.write_text(json.dumps(res, indent=2)); print(f"-> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
