#!/usr/bin/env python3
"""
h2_launch_coverage_matrix.py -- une ligne par evenement H2, une colonne par champ necessaire,
et pour chacun : en main / backfillable gratuitement / payant / live seulement.

Entrees : l'univers gele de event_listing_perp_fade_v1 (174 lancements perp-first), le tape officiel
des evenements (precedence de venue), la tape P4 (evenements de cycle de vie et fenetres, pour les
lancements futurs), et un sondage d'EXISTENCE Binance Vision (HEAD, jamais le contenu) mis en cache.
Aucun prix, aucun signal, aucun verdict, aucun budget.
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
TAPE = ROOT / "data_lake" / "events" / "official_event_tape.jsonl"
MS = ROOT / "data_lake" / "market_state"
OUT = ROOT / "reports" / "data_acquisition"
CACHE = OUT / "_vision_probe_cache.json"
VISION = "https://data.binance.vision/data/futures/um"
DATASETS = ("aggTrades", "bookDepth", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines", "metrics")

# ------------------------------------------------------------- bareme (points), P0 / P1 / P2
FIELDS = [  # (colonne, groupe, points, priorite, source si absent)
    ("announcement_ts", "timestamps", 5, "P1", "have"),
    ("onboardDate_present", "timestamps", 5, "P1", "free_api"),
    ("trading_ts_present", "timestamps", 5, "P1", "free_vision"),
    ("first_trade_ts_present", "timestamps", 5, "P0", "free_vision:aggTrades"),
    ("pending_trading_ts_present", "timestamps", 5, "P2", "live_only"),
    ("first_orderbook_ts_present", "market_state", 5, "P1", "free_vision:bookDepth|paid"),
    ("first_mark_ts_present", "market_state", 5, "P0", "free_vision:markPriceKlines"),
    ("first_index_ts_present", "market_state", 5, "P0", "free_vision:indexPriceKlines"),
    ("first_oi_ts_present", "market_state", 5, "P1", "free_vision:metrics"),
    ("funding_present", "market_state", 5, "P1", "free_vision:fundingRate"),
    ("l2_t0_t6h_present", "execution", 8, "P1", "free_vision:bookDepth(1-min)|paid(tick)"),
    ("trades_t0_t6h_present", "execution", 6, "P0", "free_vision:aggTrades"),
    ("actual_fee_present", "execution", 3, "P1", "account_key"),
    ("capacity_present", "execution", 3, "P1", "derived:bookDepth+klines"),
    ("spot_existed_before", "cross_venue", 5, "P1", "have"),
    ("other_venue_existed_before", "cross_venue", 10, "P1", "collect:gate/mexc/kucoin/bitget"),
    ("announcement_body_present", "body", 10, "P2", "scrape"),
    ("body_trading_time_present", "body", 5, "P0", "scrape"),
]
GROUP_MAX = {"timestamps": 25, "market_state": 25, "execution": 20, "cross_venue": 15, "body": 15}
assert sum(p for _, _, p, _, _ in FIELDS) == 100 and all(sum(p for _, g2, p, _, _ in FIELDS if g2 == g) == m for g, m in GROUP_MAX.items())
COLUMNS = ["symbol", "event_id", "asset", "launch_ts", "announcement_ts", "announcement_body_present", "body_trading_time_present", "onboardDate_present", "pending_trading_ts_present", "trading_ts_present",
           "first_orderbook_ts_present", "first_trade_ts_present", "first_mark_ts_present", "first_index_ts_present", "first_oi_ts_present", "l2_t0_t6h_present", "trades_t0_t6h_present", "funding_present",
           "spot_existed_before", "other_venue_existed_before", "actual_fee_present", "capacity_present", "coverage_score", "coverage_score_after_free", "coverage_score_after_free_and_key", "bucket", "bucket_after_free",
           "missing_p0_fields", "missing_p1_fields", "backfill_source", "action", "vision_available", "vision_missing", "other_venue_first", "launch_year"]


def _head(url: str) -> str:
    for _ in range(3):
        try:
            with urlopen(Request(url, method="HEAD", headers={"User-Agent": "futur-coverage"}), timeout=30):
                return "ok"
        except HTTPError as e:
            if e.code == 404:
                return "404"
        except Exception:
            pass
    return "error"


def vision_urls(symbol: str, day: str) -> Dict[str, str]:
    u = {ds: (f"{VISION}/daily/{ds}/{symbol}/1m/{symbol}-1m-{day}.zip" if ds.endswith("Klines") else f"{VISION}/daily/{ds}/{symbol}/{symbol}-{ds}-{day}.zip") for ds in DATASETS}
    u["fundingRate"] = f"{VISION}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{day[:7]}.zip"
    return u


def probe_vision(events: List[dict], cache_path: Path = CACHE, workers: int = 12, network: bool = True) -> Dict[str, str]:
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    jobs = {}
    for e in events:
        for ds, url in vision_urls(e["symbol"], e["launch_ts"][:10]).items():
            k = f"{ds}|{e['symbol']}|{e['launch_ts'][:10]}"
            if k not in cache or cache[k] == "error":
                jobs[k] = url
    if jobs and network:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for k, st in zip(jobs.keys(), ex.map(_head, jobs.values())):
                cache[k] = st
        cache_path.parent.mkdir(parents=True, exist_ok=True); cache_path.write_text(json.dumps(cache, indent=0, sort_keys=True))
    return cache


def load_events(universe: Path = UNIVERSE) -> List[dict]:
    u = json.loads(universe.read_text())
    return [{"symbol": e["symbol"], "event_id": e["event_id"], "asset": e["asset"], "launch_ts": e["tradable_start_ts"], "announcement_ts": e["publication_ts"], "onboard_ts": e.get("onboard_ts"),
             "vision_first_bar_ts": e.get("vision_first_bar_ts"), "launch_ts_source": e.get("launch_ts_source")} for e in u["events"]]


def other_venue_precedence(events: List[dict], tape: Path = TAPE) -> Dict[str, Optional[str]]:
    """Premiere annonce OKX/Bybit (listing spot ou perp) anterieure au lancement Binance, par actif. Metadonnees seulement."""
    if not tape.exists():
        return {e["asset"]: None for e in events}
    by = defaultdict(list)
    for l in tape.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("asset") and r.get("publication_ts_exchange") and r.get("source") in ("okx", "bybit") and r.get("event_type") in ("listing", "futures_listing"):
            by[r["asset"]].append((r["publication_ts_exchange"], r["source"], r["event_type"]))
    out = {}
    for e in events:
        prior = sorted(x for x in by.get(e["asset"], []) if x[0] < e["launch_ts"])
        out[e["asset"]] = f"{prior[0][1]}:{prior[0][2]}@{prior[0][0][:16]}" if prior else None
    return out


def p4_live_fields(symbol: str, ms: Path = MS) -> Dict[str, bool]:
    """Ce que la tape P4 a deja en main pour ce symbole (lancements captures en direct)."""
    out = {"pending_trading_ts": False, "trading_ts": False, "first_orderbook_ts": False, "first_trade_ts": False, "first_mark_ts": False, "first_index_ts": False, "first_oi_ts": False, "l2": False, "trades": False}
    if not ms.exists():
        return out
    for f in ms.glob("symbol_lifecycle_event/venue=binance_um/date=*/*.jsonl"):
        for l in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if symbol in l:
                try:
                    e = json.loads(l)
                except ValueError:
                    continue
                if e.get("symbol") == symbol:
                    if e.get("new_status") == "PENDING_TRADING":
                        out["pending_trading_ts"] = True
                    if e.get("new_status") == "TRADING":
                        out["trading_ts"] = True
    for m in ms.glob("windows/*/manifest.json"):
        try:
            d = json.loads(m.read_text())
        except ValueError:
            continue
        if d.get("symbol") == symbol and d.get("trigger_type") in ("new_perp_listing", "onboard_date_change") and d.get("final"):
            ft = d.get("first_timestamps") or {}
            for k in ("first_orderbook_ts", "first_trade_ts", "first_mark_ts", "first_index_ts", "first_oi_ts"):
                out[k] = out[k] or bool(ft.get(k))
            out["l2"] = out["l2"] or bool(ft.get("first_orderbook_ts")); out["trades"] = out["trades"] or bool(ft.get("first_trade_ts"))
    return out


def build_rows(events: List[dict], vision: Dict[str, str], precedence: Dict[str, Optional[str]], ms: Path = MS, account_key: bool = False, bodies: Optional[Dict[str, dict]] = None, downloaded: Optional[Dict[str, set]] = None) -> List[dict]:
    """downloaded : event_id -> datasets Vision REELLEMENT sur disque (fenetre complete) ; ils comptent comme en main."""
    rows = []
    for e in events:
        dl = (downloaded or {}).get(e["event_id"], set())
        day = e["launch_ts"][:10]; v = {ds: vision.get(f"{ds}|{e['symbol']}|{day}") for ds in DATASETS + ("fundingRate",)}
        live = p4_live_fields(e["symbol"], ms); body = (bodies or {}).get(e["event_id"]) or {}
        have = {  # en main MAINTENANT
            "announcement_ts": bool(e["announcement_ts"]), "onboardDate_present": bool(e.get("onboard_ts")), "trading_ts_present": bool(e.get("vision_first_bar_ts")) or live["trading_ts"],
            "first_trade_ts_present": live["first_trade_ts"], "pending_trading_ts_present": live["pending_trading_ts"],
            "first_orderbook_ts_present": live["first_orderbook_ts"], "first_mark_ts_present": live["first_mark_ts"], "first_index_ts_present": live["first_index_ts"], "first_oi_ts_present": live["first_oi_ts"],
            "funding_present": False, "l2_t0_t6h_present": live["l2"], "trades_t0_t6h_present": live["trades"], "actual_fee_present": account_key, "capacity_present": False,
            "spot_existed_before": True,   # verifie a la construction de l'univers (aucun spot Binance avant le perp) : le champ est connu
            "other_venue_existed_before": precedence.get(e["asset"]) is not None, "announcement_body_present": bool(body.get("body_text")), "body_trading_time_present": bool(body.get("trading_start_ts"))}
        if dl:   # fichiers Vision deja telecharges et hashes pour toute la fenetre : en main
            have["first_trade_ts_present"] |= "aggTrades" in dl; have["trades_t0_t6h_present"] |= "aggTrades" in dl
            have["first_orderbook_ts_present"] |= "bookDepth" in dl; have["l2_t0_t6h_present"] |= "bookDepth" in dl
            have["first_mark_ts_present"] |= "markPriceKlines" in dl; have["first_index_ts_present"] |= ("indexPriceKlines" in dl or "premiumIndexKlines" in dl)
            have["first_oi_ts_present"] |= "metrics" in dl; have["funding_present"] |= "fundingRate" in dl
            # capacity_present reste faux : le champ est DERIVE (profondeur x volume) et P6 ne calcule rien.
        free = dict(have)   # apres backfill gratuit (Vision + scrape du corps + calcul)
        free["first_trade_ts_present"] |= v["aggTrades"] == "ok"; free["trades_t0_t6h_present"] |= v["aggTrades"] == "ok"
        free["first_orderbook_ts_present"] |= v["bookDepth"] == "ok"; free["l2_t0_t6h_present"] |= v["bookDepth"] == "ok"
        free["first_mark_ts_present"] |= v["markPriceKlines"] == "ok"; free["first_index_ts_present"] |= (v["indexPriceKlines"] == "ok" or v["premiumIndexKlines"] == "ok")
        free["first_oi_ts_present"] |= v["metrics"] == "ok"; free["funding_present"] |= v["fundingRate"] == "ok"
        free["capacity_present"] |= (v["bookDepth"] == "ok" and v["aggTrades"] == "ok")
        free["announcement_body_present"] = True; free["body_trading_time_present"] = True   # scrape : gratuit
        key = dict(free); key["actual_fee_present"] = True
        def score(flags):
            return sum(p for col, _, p, _, _ in FIELDS if flags.get(col))
        s_now, s_free, s_key = score(have), score(free), score(key)
        missing_p0 = [c for c, _, _, pr, _ in FIELDS if pr == "P0" and not have[c]]; missing_p1 = [c for c, _, _, pr, _ in FIELDS if pr == "P1" and not have[c]]
        needs_paid = (v["bookDepth"] != "ok") or (v["indexPriceKlines"] != "ok" and v["premiumIndexKlines"] != "ok")
        src = "free" if s_free >= 70 else ("paid" if needs_paid else "live_only")
        if any(not have[c] for c in ("first_index_ts_present", "first_mark_ts_present", "trades_t0_t6h_present", "first_trade_ts_present")) and s_free > s_now:
            action = "collect"
        elif not have["body_trading_time_present"]:
            action = "scrape"
        elif needs_paid:
            action = "buy"
        else:
            action = "wait"
        vis_ok = [ds for ds in DATASETS + ("fundingRate",) if v[ds] == "ok"]; vis_missing = [ds for ds in DATASETS + ("fundingRate",) if v[ds] != "ok"]
        rows.append({"symbol": e["symbol"], "event_id": e["event_id"], "asset": e["asset"], "launch_ts": e["launch_ts"], "announcement_ts": e["announcement_ts"], **{k: have[k] for k in have if k != "announcement_ts"},
                     "coverage_score": s_now, "coverage_score_after_free": s_free, "coverage_score_after_free_and_key": s_key, "bucket": bucket(s_now), "bucket_after_free": bucket(s_free),
                     "missing_p0_fields": ";".join(missing_p0), "missing_p1_fields": ";".join(missing_p1), "backfill_source": src, "action": action,
                     "vision_available": ";".join(vis_ok), "vision_missing": ";".join(vis_missing), "other_venue_first": precedence.get(e["asset"]) or "", "launch_year": e["launch_ts"][:4]})
    return rows


def bucket(score: int) -> str:
    return "unusable" if score < 40 else ("partial" if score < 70 else ("near_usable" if score < 90 else "clean"))


def summarize(rows: List[dict]) -> Dict[str, Any]:
    n = len(rows)
    return {"n_events": n, "buckets_now": dict(Counter(r["bucket"] for r in rows)), "buckets_after_free": dict(Counter(r["bucket_after_free"] for r in rows)),
            "score_now": {"min": min(r["coverage_score"] for r in rows), "median": sorted(r["coverage_score"] for r in rows)[n // 2], "max": max(r["coverage_score"] for r in rows)} if n else {},
            "score_after_free": {"min": min(r["coverage_score_after_free"] for r in rows), "median": sorted(r["coverage_score_after_free"] for r in rows)[n // 2], "max": max(r["coverage_score_after_free"] for r in rows)} if n else {},
            "clean_after_free_and_key": sum(1 for r in rows if r["coverage_score_after_free_and_key"] >= 90), "near_usable_or_better_after_free": sum(1 for r in rows if r["coverage_score_after_free"] >= 70),
            "dominant_missing_now": dict(Counter(f for r in rows for f in (r["missing_p0_fields"].split(";") + r["missing_p1_fields"].split(";")) if f).most_common(10)),
            "missing_p0_now": dict(Counter(f for r in rows for f in r["missing_p0_fields"].split(";") if f)), "actions": dict(Counter(r["action"] for r in rows)), "backfill_source": dict(Counter(r["backfill_source"] for r in rows)),
            "vision_missing": dict(Counter(f for r in rows for f in r["vision_missing"].split(";") if f)), "other_venue_known": sum(1 for r in rows if r["other_venue_existed_before"]), "by_year": dict(Counter(r["launch_year"] for r in rows))}


def write(rows: List[dict], out: Path = OUT) -> Dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "H2_LAUNCH_COVERAGE_MATRIX.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in COLUMNS})
    s = summarize(rows)
    (out / "H2_LAUNCH_COVERAGE_MATRIX.json").write_text(json.dumps({"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "scoring": {"fields": [dict(zip(("column", "group", "points", "priority", "source_if_missing"), x)) for x in FIELDS], "group_max": GROUP_MAX,
                                                                      "buckets": {"unusable": "0-39", "partial": "40-69", "near_usable": "70-89", "clean": "90-100"}}, "summary": s, "rows": rows, "no_alpha_test": True}, indent=1, ensure_ascii=False) + "\n")
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--no-network", action="store_true"); ap.add_argument("--account-key", action="store_true", help="marquer actual_fee_present si une cle read-only a ete branchee")
    a = ap.parse_args()
    ev = load_events(); vis = probe_vision(ev, network=not a.no_network); prec = other_venue_precedence(ev)
    rows = build_rows(ev, vis, prec, account_key=a.account_key); s = write(rows); print(json.dumps(s, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
