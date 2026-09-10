#!/usr/bin/env python3
"""
official_event_tape.py -- P2A : la tape officielle des evenements de marche.

Ce que c'est : un enregistrement par evenement PUBLIE par une venue -- listing,
listing de perpetuel, delisting, retrait de paire, launchpool, changement de
statut -- avec trois horodatages qui ne se confondent jamais :
    publication_ts_exchange  ce que la venue declare (ms UTC)        -> null si inconnu
    first_seen_ts_local      quand CE collecteur l'a vu (ns UTC)
    trading_start_ts         quand le marche ouvre, si le texte le dit -> null sinon
et un hash du texte brut. Une ligne = une (source, url/code, asset).

Sources OFFICIELLES seulement :
    binance     bapi CMS article list (catalogId 48 New Listings, 161 Delisting) -- historique
    okx         GET /api/v5/support/announcements?annType=...                      -- historique
    bybit       GET /v5/announcements/index?type=...                               -- historique
    coinbase    GET /products (status, auction_mode, post_only, cancel_only)       -- SNAPSHOT, diff
    hyperliquid POST /info {type: meta} (universe, isDelisted)                     -- SNAPSHOT, diff

Les enregistrements issus d'un backfill portent backfilled=true et latency_ms=null :
la latence ne se mesure que sur ce qu'on voit arriver.

Sorties :
    data_lake/events/official_event_tape.jsonl            append-only, dedup par event_id
    data_lake/events/snapshots/<source>_<date>.json       etats coinbase / hyperliquid
    data_lake/manifests/official_event_tape_<date>.json   comptes, bornes, sha256

Usage :
    python3 -m data_lake.collectors.official_event_tape --backfill          # tout l'historique
    python3 -m data_lake.collectors.official_event_tape --incremental       # cron : nouvelles pages + snapshots
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAPE = ROOT / "data_lake" / "events" / "official_event_tape.jsonl"
SNAP = ROOT / "data_lake" / "events" / "snapshots"
MAN = ROOT / "data_lake" / "manifests"
UA = "futur-p2-event-tape/1.0 (+read-only)"
SCHEMA_VERSION = 1
PAUSE = 0.35                                    # entre deux pages : on est un lecteur, pas une charge

BINANCE_CATALOGS = {48: "new_listings", 161: "delisting"}
OKX_TYPES = ["announcements-new-listings", "announcements-delistings", "announcements-trading-updates"]
BYBIT_TYPES = ["new_crypto", "delistings", "product_updates"]


# ------------------------------------------------------------------ utilitaires

def now_ns() -> int:
    return time.time_ns()


def http_json(url, data=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Content-Type": "application/json"},
                                 data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_known() -> set:
    if not TAPE.exists():
        return set()
    return {json.loads(l)["event_id"] for l in TAPE.read_text(encoding="utf-8").splitlines() if l.strip()}


def append(records, known):
    TAPE.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(TAPE, "a", encoding="utf-8") as f:
        for r in records:
            if r["event_id"] in known:
                continue
            known.add(r["event_id"])
            f.write(json.dumps(r, ensure_ascii=False) + "\n"); n += 1
    return n


# ---------------------------------------------------------- parsing des titres

_DATE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*\(?UTC\)?", re.I)
_PAREN = re.compile(r"\(([A-Z0-9]{2,12})\)")
_PERP = re.compile(r"\b([A-Z0-9]{2,15}(?:USDT|USDC|USD))\b(?:\s+\w+){0,3}\s+(?:Perpetual|perpetual)")
_DELIST_LIST = re.compile(r"[Dd]elist(?:ing)?(?: of)?\s+([A-Z0-9]{2,12}(?:\s*,\s*(?:and\s+)?[A-Z0-9]{2,12})*)(?:\s+on\b|\s+from\b|\s*$|\s+Spot|\s+Margin)")
_PAIR = re.compile(r"\b([A-Z0-9]{2,12})/(USDT|USDC|USD|BTC|ETH)\b")


def trading_start(title: str):
    d = _DATE.search(title)
    if not d:
        return None
    t = _TIME.search(title)
    hh, mm = (int(t.group(1)), int(t.group(2))) if t else (0, 0)
    try:
        return datetime.strptime(d.group(1), "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


def classify(source: str, title: str, hint: str):
    """(event_type, market_type, [assets], [symbols]). Heuristique sur le titre ;
    le titre brut est toujours conserve, une erreur de classement se corrige apres."""
    t = title.strip(); tl = t.lower()
    assets, symbols = [], []
    for m in _PERP.finditer(t):
        symbols.append(m.group(1)); assets.append(re.sub(r"(USDT|USDC|USD)$", "", m.group(1)))
    for m in _PAIR.finditer(t):
        assets.append(m.group(1)); symbols.append(m.group(1) + m.group(2))
    for m in _PAREN.finditer(t):
        assets.append(m.group(1))
    if "delist" in tl or "removal" in tl:
        # listes de symboles perp : "Delist BADGERUSDT, BALUSDT Perpetual Contracts"
        for m in re.finditer(r"\b([A-Z0-9]{2,15}(?:USDT|USDC))\b", t):
            if m.group(1) not in symbols:
                symbols.append(m.group(1)); assets.append(re.sub(r"(USDT|USDC)$", "", m.group(1)))
    if "delist" in tl and not assets:
        m = _DELIST_LIST.search(t)
        if m:
            assets.extend(a.strip() for a in re.split(r",|\band\b", m.group(1)) if a.strip())
    assets = list(dict.fromkeys(a for a in assets if a not in ("USDT", "USDC", "USD")))
    symbols = list(dict.fromkeys(symbols))
    if "perpetual" in tl or "futures will launch" in tl or "usdⓈ-m" in tl or "usds-m" in tl or "coin-m" in tl:
        market = "perp"
    elif "margin" in tl and "list" in tl:
        market = "margin"
    else:
        market = "spot"
    if "delist" in tl or "removal" in tl or "remove" in tl or hint in ("delisting", "announcements-delistings", "delistings"):
        etype = "futures_delisting" if market == "perp" else "delisting"
    elif "launchpool" in tl:
        etype = "launchpool"
    elif "alpha" in tl and source == "binance":
        etype = "alpha"
    elif "will add" in tl and ("earn" in tl or "convert" in tl or "buy crypto" in tl):
        etype = "product_add"
    elif "tick size" in tl or "tick-size" in tl:
        etype = "tick_size_change"
    elif "list" in tl or "launch" in tl or "new listing" in tl or hint in ("new_listings", "announcements-new-listings", "new_crypto"):
        etype = "futures_listing" if market == "perp" else "listing"
    elif "suspen" in tl or "halt" in tl:
        etype = "suspension"
    else:
        etype = "other"
    return etype, market, assets, symbols


def make_records(source, etype_hint, title, url, pub_ms, raw, backfilled):
    etype, market, assets, symbols = classify(source, title, etype_hint)
    seen = now_ns()
    base = {"schema_version": SCHEMA_VERSION, "source": source, "event_type": etype, "market_type": market,
            "publication_ts_exchange": None if pub_ms is None else datetime.fromtimestamp(pub_ms / 1000, tz=timezone.utc).isoformat(),
            "publication_ts_exchange_ms": pub_ms,
            "first_seen_ts_local": datetime.fromtimestamp(seen / 1e9, tz=timezone.utc).isoformat(),
            "first_seen_ts_local_ns": seen, "trading_start_ts": trading_start(title),
            "raw_url": url, "raw_title": title, "raw_body_hash": sha(raw),
            "raw_body_hash_note": "sha256 de l'enregistrement API (titre, code, dates) -- pas du corps HTML",
            "latency_ms": None if (backfilled or pub_ms is None) else max(0, int(seen / 1e6 - pub_ms)),
            "backfilled": backfilled, "category_hint": etype_hint}
    if not assets:
        assets, symbols = [None], [None]
    out = []
    for i, a in enumerate(assets):
        sym = symbols[i] if i < len(symbols) else (symbols[0] if symbols and len(assets) == 1 else None)
        r = dict(base, asset=a, symbol=sym)
        r["event_id"] = hashlib.sha256(f"{source}|{url}|{a}".encode()).hexdigest()[:24]
        out.append(r)
    return out


# ----------------------------------------------------------------- sources

def binance(known, backfill, max_pages=400):
    n = 0
    for cat, hint in BINANCE_CATALOGS.items():
        page, stale = 1, 0
        while page <= max_pages:
            url = ("https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
                   f"?type=1&catalogId={cat}&pageNo={page}&pageSize=50")
            try:
                d = http_json(url)
                arts = d["data"]["catalogs"][0]["articles"]
            except Exception as e:
                print(f"  binance cat {cat} p{page}: {e}"); break
            if not arts:
                break
            recs = []
            for a in arts:
                aurl = f"https://www.binance.com/en/support/announcement/{a['code']}"
                recs.extend(make_records("binance", hint, a["title"], aurl, a.get("releaseDate"), a, backfill))
            added = append(recs, known); n += added
            if not backfill and added == 0:
                stale += 1
                if stale >= 2:
                    break
            page += 1; time.sleep(PAUSE)
        print(f"  binance catalog {cat} ({hint}) : pages lues {page-1}, nouveaux {n}", flush=True)
    return n


def okx(known, backfill, max_pages=200):
    n = 0
    for at in OKX_TYPES:
        page, total = 1, None
        while page <= max_pages:
            url = f"https://www.okx.com/api/v5/support/announcements?annType={at}&page={page}"
            try:
                d = http_json(url)
                blk = d["data"][0]; det = blk["details"]; total = int(blk.get("totalPage", 1))
            except Exception as e:
                print(f"  okx {at} p{page}: {e}"); break
            recs = []
            for x in det:
                recs.extend(make_records("okx", at, x["title"], x["url"], int(x["pTime"]), x, backfill))
            added = append(recs, known); n += added
            if (not backfill and added == 0) or page >= total:
                break
            page += 1; time.sleep(PAUSE)
        print(f"  okx {at} : pages {page}/{total}, nouveaux cumul {n}", flush=True)
    return n


def bybit(known, backfill, max_pages=200):
    n = 0
    for ty in BYBIT_TYPES:
        page, total = 1, None
        while page <= max_pages:
            url = f"https://api.bybit.com/v5/announcements/index?locale=en-US&type={ty}&limit=50&page={page}"
            try:
                d = http_json(url); lst = d["result"]["list"]; total = int(d["result"].get("total", 0))
            except Exception as e:
                print(f"  bybit {ty} p{page}: {e}"); break
            if not lst:
                break
            recs = []
            for x in lst:
                recs.extend(make_records("bybit", ty, x["title"], x["url"], int(x.get("dateTimestamp") or x.get("publishTime") or 0) or None, x, backfill))
            added = append(recs, known); n += added
            if (not backfill and added == 0) or page * 50 >= total:
                break
            page += 1; time.sleep(PAUSE)
        print(f"  bybit {ty} : pages {page}, total declare {total}, nouveaux cumul {n}", flush=True)
    return n


def _snapshot_diff(source, key, cur, fields, known):
    """Compare l'etat courant au dernier snapshot ; chaque changement de champ
    devient un evenement first_seen-only (publication_ts inconnue)."""
    SNAP.mkdir(parents=True, exist_ok=True)
    prev_files = sorted(SNAP.glob(f"{source}_*.json"))
    prev = json.loads(prev_files[-1].read_text()) if prev_files else {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (SNAP / f"{source}_{today}.json").write_text(json.dumps(cur, sort_keys=True))
    recs = []
    for k, v in cur.items():
        p = prev.get(k)
        if p is None:
            etype = "listing" if prev else "snapshot_baseline"
            changes = {f: v.get(f) for f in fields}
        else:
            changes = {f: v.get(f) for f in fields if v.get(f) != p.get(f)}
            if not changes:
                continue
            etype = "delisting" if (v.get("status") == "delisted" or v.get("isDelisted")) else "status_change"
        title = f"{source} {k} {etype}: {json.dumps(changes, sort_keys=True)}"
        r = make_records(source, etype, title, f"snapshot://{source}/{k}/{today}", None, {"key": k, **changes}, False)[0]
        r.update({"asset": v.get("asset", k.split("-")[0]), "symbol": k, "market_type": v.get("market_type", "spot"),
                  "event_type": etype, "event_id": hashlib.sha256(f"{source}|{k}|{today}|{json.dumps(changes, sort_keys=True)}".encode()).hexdigest()[:24],
                  "publication_ts_note": "inconnue : evenement detecte par diff de snapshot"})
        recs.append(r)
    for k in prev:
        if k not in cur:
            r = make_records(source, "removed", f"{source} {k} removed from universe", f"snapshot://{source}/{k}/{today}", None, {"key": k, "removed": True}, False)[0]
            r.update({"asset": k.split("-")[0], "symbol": k, "event_type": "delisting",
                      "event_id": hashlib.sha256(f"{source}|{k}|{today}|removed".encode()).hexdigest()[:24]})
            recs.append(r)
    return append(recs, known)


def coinbase(known):
    d = http_json("https://api.exchange.coinbase.com/products")
    cur = {p["id"]: {"status": p.get("status"), "auction_mode": p.get("auction_mode"), "post_only": p.get("post_only"),
                     "limit_only": p.get("limit_only"), "cancel_only": p.get("cancel_only"), "asset": p.get("base_currency"),
                     "market_type": "spot"} for p in d}
    n = _snapshot_diff("coinbase", "id", cur, ["status", "auction_mode", "post_only", "limit_only", "cancel_only"], known)
    print(f"  coinbase : {len(cur)} produits, evenements {n}", flush=True); return n


def hyperliquid(known):
    d = http_json("https://api.hyperliquid.xyz/info", data=json.dumps({"type": "meta"}).encode())
    cur = {a["name"]: {"isDelisted": bool(a.get("isDelisted")), "maxLeverage": a.get("maxLeverage"), "szDecimals": a.get("szDecimals"),
                       "asset": a["name"], "market_type": "perp"} for a in d["universe"]}
    n = _snapshot_diff("hyperliquid", "name", cur, ["isDelisted", "maxLeverage", "szDecimals"], known)
    print(f"  hyperliquid : {len(cur)} actifs, evenements {n}", flush=True); return n


def manifest():
    rows = [json.loads(l) for l in TAPE.read_text(encoding="utf-8").splitlines() if l.strip()]
    by = {}
    for r in rows:
        k = f"{r['source']}/{r['event_type']}"; by[k] = by.get(k, 0) + 1
    pubs = [r["publication_ts_exchange"] for r in rows if r.get("publication_ts_exchange")]
    m = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "file": str(TAPE.relative_to(ROOT)),
         "sha256": hashlib.sha256(TAPE.read_bytes()).hexdigest(), "n_records": len(rows), "by_source_type": dict(sorted(by.items())),
         "publication_ts_range": [min(pubs), max(pubs)] if pubs else None,
         "backfilled_share": round(sum(1 for r in rows if r.get("backfilled")) / max(1, len(rows)), 3),
         "schema_version": SCHEMA_VERSION}
    MAN.mkdir(parents=True, exist_ok=True)
    p = MAN / f"official_event_tape_{m['generated_at_utc'][:10]}.json"
    if p.exists():  # fichier suivi par git : ne pas le reecrire (horodatage seul) si le tape est inchange
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
            if old.get("sha256") == m["sha256"] and old.get("n_records") == m["n_records"]:
                print(f"-> {p}  inchange ({len(rows)} enregistrements, sha256 identique)"); return old
        except (OSError, ValueError):
            pass
    p.write_text(json.dumps(m, indent=2, ensure_ascii=False))
    print(f"-> {p}  ({len(rows)} enregistrements)"); return m


def reclassify():
    """Le classement est une fonction pure du titre brut : on peut le refaire sans
    rien retelecharger. Les event_id ne changent pas (source|url|asset) sauf si un
    asset apparait ou disparait ; on regenere alors la ligne et on dedoublonne."""
    rows = [json.loads(l) for l in TAPE.read_text(encoding="utf-8").splitlines() if l.strip()]
    out, seen, changed = [], set(), 0
    for r in rows:
        if r["raw_url"].startswith("snapshot://"):
            key = r["event_id"]
            if key not in seen:
                seen.add(key); out.append(r)
            continue
        etype, market, assets, symbols = classify(r["source"], r["raw_title"], r.get("category_hint", ""))
        if not assets:
            assets, symbols = [None], [None]
        for i, a in enumerate(assets):
            sym = symbols[i] if i < len(symbols) else (symbols[0] if symbols and len(assets) == 1 else None)
            n = dict(r, event_type=etype, market_type=market, asset=a, symbol=sym, trading_start_ts=trading_start(r["raw_title"]))
            n["event_id"] = hashlib.sha256(f"{r['source']}|{r['raw_url']}|{a}".encode()).hexdigest()[:24]
            if n["event_id"] in seen:
                continue
            seen.add(n["event_id"]); out.append(n)
            if (n["event_type"], n["asset"], n["symbol"]) != (r["event_type"], r["asset"], r["symbol"]):
                changed += 1
    TAPE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    print(f"  reclassify : {len(rows)} -> {len(out)} lignes, {changed} reclassees")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true"); ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--reclassify", action="store_true", help="reecrit le classement depuis raw_title, sans refetch")
    ap.add_argument("--sources", default="binance,okx,bybit,coinbase,hyperliquid")
    a = ap.parse_args()
    if a.reclassify:
        reclassify(); manifest(); return
    backfill = bool(a.backfill)
    known = load_known(); t0 = time.time(); src = a.sources.split(",")
    print(f"=== event tape : {'BACKFILL' if backfill else 'incremental'} ; deja connus {len(known)} ===", flush=True)
    if "binance" in src: binance(known, backfill)
    if "okx" in src: okx(known, backfill)
    if "bybit" in src: bybit(known, backfill)
    if "coinbase" in src:
        try: coinbase(known)
        except Exception as e: print(f"  coinbase : {e}")
    if "hyperliquid" in src:
        try: hyperliquid(known)
        except Exception as e: print(f"  hyperliquid : {e}")
    manifest(); print(f"termine en {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
