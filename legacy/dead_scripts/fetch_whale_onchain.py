#!/usr/bin/env python3
"""
scripts/fetch_whale_onchain.py
==============================
Collecte les transactions whale BTC depuis plusieurs sources gratuites :
  - Blockchain.info  (100% gratuit, unlimited)
  - Mempool.space    (100% gratuit, unlimited)
  - Binance large trades proxy (grosses exécutions spot)

Seuil : > 100 BTC par transaction.
Stockage : MongoDB trader.whale_transactions
"""
from __future__ import annotations
import logging, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
from pymongo import MongoClient, UpdateOne

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("whale_onchain")

MONGO_URI = os.getenv("FUTUR_MONGO_URI", "mongodb://localhost:27017")
DB_NAME   = os.getenv("FUTUR_MONGO_DB",  "trader")
COLL_NAME = "whale_transactions"
MIN_BTC   = 50

_s = requests.Session()
_s.headers["User-Agent"] = "Mozilla/5.0"


def _get(url, params=None, timeout=15) -> Any:
    for i in range(3):
        try:
            r = _s.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            log.warning(f"  HTTP {r.status_code}: {url[:60]}")
            return None
        except Exception as e:
            if i == 2: return None
            time.sleep(2 ** i)
    return None


def get_db():
    return MongoClient(MONGO_URI)[DB_NAME]


def ensure_indexes():
    coll = get_db()[COLL_NAME]
    try:
        coll.create_index("tx_hash", unique=True, background=True)
    except Exception:
        pass
    coll.create_index("timestamp", background=True)
    coll.create_index([("amount_btc", -1)], background=True)


def upsert(docs: list[dict]) -> int:
    if not docs:
        return 0
    coll = get_db()[COLL_NAME]
    ops = []
    for d in docs:
        key = d.get("tx_hash") or d.get("_id_key") or str(d)[:40]
        ops.append(UpdateOne({"tx_hash": key}, {"$set": d}, upsert=True))
    res = coll.bulk_write(ops, ordered=False)
    return res.upserted_count + res.modified_count


# ── Source 1 : Blockchain.info (transactions récentes et historiques) ─────────

def fetch_blockchain_info_blocks(n_days: int = 7) -> list[dict]:
    """Analyse les blocs des N derniers jours pour trouver les grosses txs."""
    result = []
    now    = datetime.now(timezone.utc)

    for day_offset in range(n_days):
        day = now - timedelta(days=day_offset)
        ts_ms = int(day.timestamp() * 1000)

        blocks_data = _get(f"https://blockchain.info/blocks/{ts_ms}?format=json")
        if not blocks_data:
            time.sleep(1)
            continue

        blocks = blocks_data if isinstance(blocks_data, list) else blocks_data.get("blocks", [])
        log.info(f"  blockchain.info: {day.date()} — {len(blocks)} blocs")

        for blk in blocks[:6]:  # 6 blocs par jour (évite trop de requêtes)
            block_hash = blk.get("hash")
            if not block_hash:
                continue

            blk_data = _get(f"https://blockchain.info/rawblock/{block_hash}?format=json")
            if not blk_data:
                time.sleep(0.5)
                continue

            ts = datetime.fromtimestamp(blk_data.get("time", 0), tz=timezone.utc)
            height = blk_data.get("height", 0)

            for tx in blk_data.get("tx", []):
                out_total = sum(o.get("value", 0) for o in tx.get("out", []))
                btc = out_total / 1e8
                if btc < MIN_BTC:
                    continue

                # Adresses de destination
                out_addrs = [
                    o.get("addr", "") for o in tx.get("out", [])
                    if o.get("addr") and o.get("value", 0) > 1e8
                ][:5]

                result.append({
                    "tx_hash":          tx.get("hash", ""),
                    "timestamp":        ts,
                    "amount_btc":       round(btc, 4),
                    "amount_usd":       None,
                    "block_height":     height,
                    "inputs":           len(tx.get("inputs", [])),
                    "outputs":          len(tx.get("out", [])),
                    "output_addresses": out_addrs,
                    "symbol":           "BTC",
                    "source":           "blockchain_info",
                    "type":             "exchange_inflow" if len(tx.get("out", [])) == 1 else "transfer",
                })

            time.sleep(0.3)
        time.sleep(1)

    return result


# ── Source 2 : Mempool.space (derniers blocs) ─────────────────────────────────

def fetch_mempool_blocks(n_blocks: int = 20) -> list[dict]:
    """Scan les derniers blocs mempool.space pour les grosses transactions."""
    tip = _get("https://mempool.space/api/blocks/tip/height")
    if not tip:
        return []
    height = int(tip)
    result = []

    for h in range(height, max(height - n_blocks, 0), -1):
        hash_data = _get(f"https://mempool.space/api/block-height/{h}")
        if not hash_data:
            continue
        block_hash = str(hash_data).strip('"')

        txs = _get(f"https://mempool.space/api/block/{block_hash}/txs/0")
        if not txs:
            time.sleep(0.3)
            continue

        blk_meta = _get(f"https://mempool.space/api/block/{block_hash}")
        blk_ts   = datetime.fromtimestamp(
            blk_meta.get("timestamp", 0) if blk_meta else 0, tz=timezone.utc)

        for tx in txs:
            total_sat = sum(v.get("value", 0) for v in tx.get("vout", []))
            total_btc = total_sat / 1e8
            if total_btc < MIN_BTC:
                continue

            out_addrs = [
                v.get("scriptpubkey_address", "")
                for v in tx.get("vout", [])
                if v.get("scriptpubkey_address") and v.get("value", 0) > 1e8
            ][:5]

            fee = tx.get("fee", 0) or 0
            result.append({
                "tx_hash":          tx.get("txid", ""),
                "timestamp":        blk_ts,
                "amount_btc":       round(total_btc, 4),
                "amount_usd":       None,
                "block_height":     h,
                "fee_sat":          fee,
                "fee_rate":         round(fee / (tx.get("size", 1) or 1), 2),
                "inputs":           len(tx.get("vin", [])),
                "outputs":          len(tx.get("vout", [])),
                "output_addresses": out_addrs,
                "symbol":           "BTC",
                "source":           "mempool_space",
            })
        time.sleep(0.2)

    return result


# ── Source 3 : Binance large trades (proxy whale activity) ────────────────────

def fetch_binance_large_trades(symbol: str = "BTCUSDT",
                               min_qty_btc: float = 10.0) -> list[dict]:
    """
    Récupère les derniers trades Binance et filtre les grosses exécutions.
    Un trade >10 BTC sur Binance = whale institutionnel.
    """
    data = _get(f"https://api.binance.com/api/v3/trades",
                {"symbol": symbol, "limit": 1000})
    if not data:
        return []

    result = []
    for t in data:
        qty = float(t.get("qty", 0))
        if qty < min_qty_btc:
            continue
        price = float(t.get("price", 0))
        ts = datetime.fromtimestamp(int(t["time"]) / 1000, tz=timezone.utc)
        result.append({
            "tx_hash":    f"binance_{t['id']}",
            "timestamp":  ts,
            "amount_btc": round(qty, 4),
            "amount_usd": round(qty * price, 2),
            "price":      price,
            "is_buyer_maker": t.get("isBuyerMaker", False),
            "symbol":     "BTC",
            "source":     "binance_trades",
            "type":       "sell" if t.get("isBuyerMaker") else "buy",
        })

    log.info(f"  Binance large trades (>{min_qty_btc} BTC): {len(result)} trades")
    return result


# ── Source 4 : Binance futures liquidations (proxy whale stress) ──────────────

def fetch_binance_liquidations() -> list[dict]:
    """Liquidiations importantes sur les perps Binance (stress whale)."""
    result = []
    for sym in ["BTCUSDT", "ETHUSDT"]:
        data = _get(f"https://fapi.binance.com/fapi/v1/allForceOrders",
                    {"symbol": sym, "limit": 100}, timeout=10)
        if not data:
            continue
        for liq in data:
            qty   = float(liq.get("origQty", 0))
            price = float(liq.get("price", 0) or liq.get("avgPrice", 0))
            if qty * price < 50_000:
                continue  # > $50K
            ts = datetime.fromtimestamp(int(liq.get("time", 0)) / 1000, tz=timezone.utc)
            result.append({
                "tx_hash":    f"liq_{sym}_{liq.get('orderId','')}",
                "timestamp":  ts,
                "amount_btc": round(qty, 4) if "BTC" in sym else None,
                "amount_usd": round(qty * price, 2),
                "price":      price,
                "side":       liq.get("side"),
                "symbol":     sym.replace("USDT", ""),
                "source":     "binance_liquidation",
                "type":       "liquidation",
            })
    log.info(f"  Binance liquidations: {len(result)} > $50K")
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--blocks", type=int, default=15)
    args = parser.parse_args()

    ensure_indexes()

    log.info("=" * 60)
    log.info("WHALE ON-CHAIN COLLECTOR v2")
    log.info(f"Seuil: >{MIN_BTC} BTC | MongoDB: {MONGO_URI}")
    log.info("=" * 60)

    total = 0

    # 1. Blockchain.info historique
    log.info(f"[1/4] blockchain.info — {args.days} jours")
    txs = fetch_blockchain_info_blocks(n_days=args.days)
    n   = upsert(txs)
    total += n
    log.info(f"  ✓ {len(txs)} trouvées, {n} nouvelles")

    # 2. Mempool.space blocs récents
    log.info(f"[2/4] mempool.space — {args.blocks} blocs récents")
    txs = fetch_mempool_blocks(n_blocks=args.blocks)
    n   = upsert(txs)
    total += n
    log.info(f"  ✓ {len(txs)} trouvées, {n} nouvelles")

    # 3. Binance large spot trades
    log.info("[3/4] Binance large spot trades (>10 BTC)")
    for sym in ["BTCUSDT", "ETHUSDT"]:
        txs = fetch_binance_large_trades(sym)
        n   = upsert(txs)
        total += n
        log.info(f"  ✓ {sym}: {len(txs)} trouvées, {n} nouvelles")

    # 4. Binance liquidations
    log.info("[4/4] Binance liquidations (>$50K)")
    txs = fetch_binance_liquidations()
    n   = upsert(txs)
    total += n
    log.info(f"  ✓ {len(txs)} trouvées, {n} nouvelles")

    final = get_db()[COLL_NAME].count_documents({})
    log.info("=" * 60)
    log.info(f"TOTAL {COLL_NAME}: {final:,} documents")
    log.info(f"Nouvelles cette session: {total}")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
