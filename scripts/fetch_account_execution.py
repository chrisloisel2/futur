#!/usr/bin/env python3
"""
scripts/fetch_account_execution.py -- P2C : ce que le compte a REELLEMENT execute.

Lecture seule. Aucun ordre, aucune annulation, aucun retrait : les cles attendues
sont des cles READ-ONLY (permission 'Enable Reading' seule chez Binance ; 'Read'
chez OKX et Bybit ; Hyperliquid n'a besoin que de l'adresse). Sans cle, le script
n'appelle rien et documente ce qu'il aurait fait.

Ce qu'il lit, par venue :
    Binance USDT-M  GET /fapi/v1/commissionRate   frais maker/taker du compte
                    GET /fapi/v1/userTrades       fills : prix, qty, commission, maker?, realizedPnl, time
                    GET /fapi/v1/allOrders        ordres : status, type, timeInForce, time, updateTime
    OKX             GET /api/v5/account/trade-fee ; GET /api/v5/trade/fills-history ; GET /api/v5/trade/orders-history-archive
    Bybit v5        GET /v5/account/fee-rate ; GET /v5/execution/list ; GET /v5/order/history
    Hyperliquid     POST /info {type: userFees} ; POST /info {type: userFills}

Sortie : reports/mechanisms/p1_payer_discovery/account_actual_fee_summary.json
         data_lake/manifests/account_execution_<date>.json   (fills bruts, hashes)
Ce que ces endpoints NE donnent PAS : la latence soumission->ack et soumission->fill,
le taux de rejet post-only, le slippage vs le mid a la soumission. Cela exige un
journal cote client au moment de l'envoi (P1.3 execution_shadow_logger, en paper).
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
SUMMARY = ROOT / "reports" / "mechanisms" / "p1_payer_discovery" / "account_actual_fee_summary.json"
MAN = ROOT / "data_lake" / "manifests"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def _get(url, headers=None, data=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or {}, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _sha(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True).encode()).hexdigest()


def binance():
    k, s = os.getenv("BINANCE_API_KEY"), os.getenv("BINANCE_API_SECRET")
    ep = ["GET /fapi/v1/commissionRate", "GET /fapi/v1/userTrades", "GET /fapi/v1/allOrders"]
    if not (k and s):
        return {"status": "skipped_no_key", "endpoints": ep, "permission_required": "Enable Reading only"}
    def signed(path, params):
        params.update({"timestamp": int(time.time() * 1000), "recvWindow": 5000}); q = urllib.parse.urlencode(params)
        sig = hmac.new(s.encode(), q.encode(), hashlib.sha256).hexdigest()
        return _get(f"https://fapi.binance.com{path}?{q}&signature={sig}", {"X-MBX-APIKEY": k})
    out = {"fees": {}, "fills": {}, "orders": {}}
    for sym in SYMBOLS:
        c = signed("/fapi/v1/commissionRate", {"symbol": sym}); out["fees"][sym] = {"maker_bps": float(c["makerCommissionRate"]) * 1e4, "taker_bps": float(c["takerCommissionRate"]) * 1e4}
        fills = signed("/fapi/v1/userTrades", {"symbol": sym, "limit": 1000})
        out["fills"][sym] = [{"time": f["time"], "price": float(f["price"]), "qty": float(f["qty"]), "commission": float(f["commission"]), "commissionAsset": f["commissionAsset"],
                              "maker": bool(f["maker"]), "side": f["side"], "realizedPnl": float(f["realizedPnl"]), "orderId": f["orderId"]} for f in fills]
        orders = signed("/fapi/v1/allOrders", {"symbol": sym, "limit": 1000})
        out["orders"][sym] = [{"orderId": o["orderId"], "status": o["status"], "type": o["type"], "timeInForce": o["timeInForce"], "time": o["time"], "updateTime": o["updateTime"],
                               "executedQty": float(o["executedQty"]), "origQty": float(o["origQty"])} for o in orders]
    return {"status": "ok", "endpoints": ep, **out}


def okx():
    k, s, p = os.getenv("OKX_API_KEY"), os.getenv("OKX_API_SECRET"), os.getenv("OKX_API_PASSPHRASE")
    ep = ["GET /api/v5/account/trade-fee?instType=SWAP", "GET /api/v5/trade/fills-history?instType=SWAP", "GET /api/v5/trade/orders-history-archive?instType=SWAP"]
    if not (k and s and p):
        return {"status": "skipped_no_key", "endpoints": ep, "permission_required": "Read"}
    def signed(path):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
        sig = base64.b64encode(hmac.new(s.encode(), (ts + "GET" + path).encode(), hashlib.sha256).digest()).decode()
        return _get("https://www.okx.com" + path, {"OK-ACCESS-KEY": k, "OK-ACCESS-SIGN": sig, "OK-ACCESS-TIMESTAMP": ts, "OK-ACCESS-PASSPHRASE": p, "Content-Type": "application/json"})
    fee = signed("/api/v5/account/trade-fee?instType=SWAP")["data"][0]
    fills = signed("/api/v5/trade/fills-history?instType=SWAP&limit=100")["data"]
    return {"status": "ok", "endpoints": ep, "fees": {"level": fee.get("level"), "maker_bps": -float(fee["maker"]) * 1e4, "taker_bps": -float(fee["taker"]) * 1e4, "convention": "signe inverse"},
            "fills": [{"ts": f["ts"], "instId": f["instId"], "px": float(f["fillPx"]), "sz": float(f["fillSz"]), "fee": float(f["fee"]), "execType": f.get("execType"), "side": f["side"]} for f in fills]}


def bybit():
    k, s = os.getenv("BYBIT_API_KEY"), os.getenv("BYBIT_API_SECRET")
    ep = ["GET /v5/account/fee-rate?category=linear", "GET /v5/execution/list?category=linear", "GET /v5/order/history?category=linear"]
    if not (k and s):
        return {"status": "skipped_no_key", "endpoints": ep, "permission_required": "Read-only"}
    def signed(path, q):
        ts = str(int(time.time() * 1000)); rw = "5000"; sig = hmac.new(s.encode(), (ts + k + rw + q).encode(), hashlib.sha256).hexdigest()
        return _get(f"https://api.bybit.com{path}?{q}", {"X-BAPI-API-KEY": k, "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": rw})
    out = {"fees": {}, "fills": []}
    for sym in SYMBOLS:
        r = signed("/v5/account/fee-rate", f"category=linear&symbol={sym}")["result"]["list"][0]
        out["fees"][sym] = {"maker_bps": float(r["makerFeeRate"]) * 1e4, "taker_bps": float(r["takerFeeRate"]) * 1e4}
    ex = signed("/v5/execution/list", "category=linear&limit=100")["result"]["list"]
    out["fills"] = [{"execTime": e["execTime"], "symbol": e["symbol"], "execPrice": float(e["execPrice"]), "execQty": float(e["execQty"]), "execFee": float(e["execFee"]),
                     "isMaker": bool(e.get("isMaker")), "side": e["side"], "execType": e.get("execType")} for e in ex]
    return {"status": "ok", "endpoints": ep, **out}


def hyperliquid():
    a = os.getenv("HYPERLIQUID_USER_ADDRESS")
    ep = ["POST /info userFees", "POST /info userFills"]
    if not a:
        return {"status": "skipped_no_address", "endpoints": ep, "permission_required": "none (address only)"}
    h = {"Content-Type": "application/json"}
    fees = _get("https://api.hyperliquid.xyz/info", h, json.dumps({"type": "userFees", "user": a}).encode())
    fills = _get("https://api.hyperliquid.xyz/info", h, json.dumps({"type": "userFills", "user": a}).encode())
    return {"status": "ok", "endpoints": ep, "fees": {"maker_bps": float(fees.get("userAddRate", "nan")) * 1e4, "taker_bps": float(fees.get("userCrossRate", "nan")) * 1e4},
            "fills": [{"time": f["time"], "coin": f["coin"], "px": float(f["px"]), "sz": float(f["sz"]), "fee": float(f.get("fee", 0)), "crossed": bool(f.get("crossed")), "side": f["side"]} for f in fills[:200]]}


def summarize(res):
    """Le cout reel a battre : frais + (taker ? demi-spread paye) par fill, agreges."""
    s = {}
    for v, d in res["venues"].items():
        if d.get("status") != "ok":
            s[v] = {"status": d.get("status")}; continue
        fills = d.get("fills") or {}
        flat = [f for lst in (fills.values() if isinstance(fills, dict) else [fills]) for f in lst]
        n = len(flat)
        maker_share = (sum(1 for f in flat if f.get("maker") or f.get("isMaker") or (f.get("crossed") is False) or f.get("execType") == "M") / n) if n else None
        s[v] = {"status": "ok", "fees": d.get("fees"), "n_fills": n, "maker_share": maker_share}
    return s


def main():
    res = {"fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source_class": "account_actual", "read_only": True, "venues": {}}
    for name, fn in (("binance", binance), ("okx", okx), ("bybit", bybit), ("hyperliquid", hyperliquid)):
        try:
            res["venues"][name] = fn()
        except Exception as e:
            res["venues"][name] = {"status": "error", "error": str(e)[:200]}
        print(f"  {name:12s} {res['venues'][name]['status']}")
    if not any(v.get("status") == "ok" for v in res["venues"].values()):
        print("  aucune cle : rien n'a ete appele, rien n'est ecrit (inerte par construction)"); return 0
    MAN.mkdir(parents=True, exist_ok=True); SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    res["sha256"] = _sha(res["venues"]); (MAN / f"account_execution_{res['fetched_at_utc'][:10]}.json").write_text(json.dumps(res, indent=2))
    SUMMARY.write_text(json.dumps({"fetched_at_utc": res["fetched_at_utc"], "summary": summarize(res), "raw_manifest_sha256": res["sha256"]}, indent=2))
    print(f"-> {SUMMARY}"); return 0


if __name__ == "__main__":
    sys.exit(main())
