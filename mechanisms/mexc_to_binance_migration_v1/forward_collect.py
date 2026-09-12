#!/usr/bin/env python3
"""
mexc_to_binance_migration_v1 / forward_collect.py -- la collecte FORWARD du prereg scelle MEXC_TO_BINANCE_V1.

Pour chaque perpetuel USDT lance sur Binance APRES le scellement (exchangeInfo public, onboardDate > seal), et seulement
ceux-la, on assemble sans lire aucun rendement :
  1. l'annonce officielle (tape P2, rafraichi par futur-event-tape.timer) -> publication_ts, corps (P7) -> heure d'ouverture annoncee
  2. t0 = premiere barre Vision 1 min du perp (P6 ; provisoire = onboardDate tant que Vision n'a pas publie)
  3. la precedence de place (P9 : instruments MEXC/OKX/Bybit/KuCoin/Gate) -> MEXC_FIRST ou non
  4. si MEXC_FIRST : la tape horaire MEXC close <= t0 (P12) -> pre_announcement_return_24h, drapeaux de wash
  5. la capacite Binance a +15 min (P11, archives Vision bookDepth + aggTrades du jour du lancement)
Chaque etape a un statut ; un evenement n'est eligible que quand tout est la. Les cinq fichiers d'entree du harnais
(UNIVERSE, MEXC_PRE_ANNOUNCEMENT_RETURN_24H, CAUSAL_MATRIX, CAPACITY_FEATURES, WASH) sont reecrits a chaque passage
sous reports/forward/mexc_to_binance_v1/ ; le registre REGISTRY.json garde l'historique des statuts. Aucun prix
post-t0 n'est lu ; aucun signal ; aucun ordre.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import importlib.util as _ilu  # noqa: E402
_h = _ilu.spec_from_file_location("m2b_first_look", ROOT / "mechanisms" / "mexc_to_binance_migration_v1" / "first_look.py")
H = _ilu.module_from_spec(_h); _h.loader.exec_module(H)
from data_lake.collectors import binance_account_readonly as RO  # noqa: E402
from data_lake.collectors import venue_pre_binance_paths as VPP  # noqa: E402
from data_lake.collectors import venue_precedence as VP  # noqa: E402
from data_lake.collectors import vision_paths as VIS  # noqa: E402
from data_lake.indices import mexc_volume_trust as MVT  # noqa: E402
from data_lake.indices import pre_binance_features as PF  # noqa: E402

FORWARD_DIR = H.FORWARD_DIR
REGISTRY = FORWARD_DIR / "REGISTRY.json"
LOG = FORWARD_DIR / "forward_collect.log.jsonl"
TAPE = ROOT / "data_lake" / "events" / "official_event_tape.jsonl"
INSTRUMENTS = ROOT / "data" / "cross_venue" / "instruments.json"
DATASETS = ["klines", "bookDepth", "aggTrades", "fundingRate", "markPriceKlines"]
MAX_TS_DISAGREEMENT_MIN = 15
STEPS = ("announcement", "vision", "precedence", "mexc_tape", "capacity")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_atomic(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True); tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str)); os.replace(tmp, p)


def log(rec: Dict[str, Any]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), **rec}, ensure_ascii=False, default=str) + "\n")


def load_registry() -> Dict[str, Any]:
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text())
        except ValueError:
            pass
    return {"mechanism_id": H.MECHANISM_ID, "events": {}, "no_return_computed": True, "no_alpha_test": True}


# ----------------------------------------------------------------------------- 0. lancements apres le scellement
def cutoff_ms() -> Optional[int]:
    try:
        return H.forward_cutoff_ms()
    except SystemExit:
        return None


def scan_launches(cut_ms: int) -> List[Dict[str, Any]]:
    """Perpetuels USDT dont onboardDate > scellement, depuis l'exchangeInfo public (client read-only, GET, sans cle)."""
    r = RO.ReadOnlyClient().get("/fapi/v1/exchangeInfo")
    if r["status"] != "ok":
        log({"kind": "scan_error", "error": r.get("error")}); return []
    out = []
    for s in r["data"].get("symbols", []):
        od = int(s.get("onboardDate") or 0)
        if s.get("contractType") == "PERPETUAL" and s.get("quoteAsset") == "USDT" and od > cut_ms:
            out.append({"symbol": s["symbol"], "asset": s.get("baseAsset"), "onboard_ms": od, "onboard_ts": datetime.fromtimestamp(od / 1000, tz=timezone.utc).isoformat(timespec="seconds"), "status_exchange": s.get("status")})
    return sorted(out, key=lambda x: x["onboard_ms"])


# ----------------------------------------------------------------------------- 1. annonce
def find_announcement(symbol: str, asset: str, onboard_ms: int) -> Optional[Dict[str, Any]]:
    """Le dernier enregistrement Binance futures_listing du tape qui nomme le symbole, publie avant l'ouverture (et au plus 30 j avant)."""
    if not TAPE.exists():
        return None
    best = None
    for l in TAPE.read_text().splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("source") != "binance" or r.get("event_type") != "futures_listing":
            continue
        title = (r.get("raw_title") or "").upper(); pub = r.get("publication_ts_exchange_ms") or 0
        if (r.get("symbol") == symbol or symbol in title) and onboard_ms - 30 * 86_400_000 <= pub <= onboard_ms + 6 * 3_600_000:
            if best is None or pub > best["publication_ts_exchange_ms"]:
                best = r
    return best


def announcement_step(ev: Dict[str, Any]) -> Dict[str, Any]:
    from data_lake.collectors import announcement_body_archive as ABA
    rec = find_announcement(ev["symbol"], ev["asset"], ev["onboard_ms"])
    if not rec:
        return {"status": "pending", "reason": "no Binance futures_listing record in the official tape yet"}
    out = {"status": "ok", "publication_ts": rec["publication_ts_exchange"], "publication_ts_ms": rec["publication_ts_exchange_ms"], "url": rec.get("raw_url"), "tape_event_id": rec.get("event_id"), "announced_opening_ts": None}
    try:
        fetched = ABA.fetch_body(rec["raw_url"], "binance"); stored = ABA.store_record({"source": "binance", "url": rec["raw_url"], "event_ids": [ev["event_id"]], "titles": [rec.get("raw_title")]}, fetched)
        ex = stored.get("extracted") or {}
        out["announced_opening_ts"] = ex.get("trading_start_ts") or rec.get("trading_start_ts"); out["body_status"] = stored.get("http_status")
    except Exception as e:                                                # une URL morte est un fait, pas un plantage
        out["body_status"] = "error: %s" % type(e).__name__; out["announced_opening_ts"] = rec.get("trading_start_ts")
    return out


# ----------------------------------------------------------------------------- 2. t0 (Vision)
def vision_step(ev: Dict[str, Any]) -> Dict[str, Any]:
    from data_lake.collectors import vision_free_backfill as VFB
    t0_iso = ev.get("t0") or ev["onboard_ts"]
    try:
        r = VFB.run([{"event_id": ev["event_id"], "symbol": ev["symbol"], "t0": t0_iso}], DATASETS, workers=4, max_gb=2.0)
    except Exception as e:
        return {"status": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:80])}
    first_bar = first_vision_bar_ms(ev["symbol"], ev["onboard_ms"])
    out = {"status": "ok" if first_bar else "pending", "files_ok": r.get("files_ok"), "files_not_yet_published": r.get("files_not_yet_published"), "first_bar_ms": first_bar}
    if first_bar:
        out["t0"] = datetime.fromtimestamp(first_bar / 1000, tz=timezone.utc).isoformat(timespec="seconds"); out["t0_ms"] = first_bar
    return out


def first_vision_bar_ms(symbol: str, onboard_ms: int) -> Optional[int]:
    """L'ouverture de la premiere barre 1 min du perp dans les archives klines du jour d'onboarding (ou du lendemain)."""
    import csv, io, zipfile
    for d in (0, 1):
        day = datetime.fromtimestamp(onboard_ms / 1000 + d * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
        p = VIS.local_path("klines", symbol, day)
        if not p.exists():
            continue
        try:
            with zipfile.ZipFile(p) as z:
                for row in csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding="utf-8")):
                    if row and row[0].isdigit():
                        ot = int(row[0]); return ot // 1000 if ot > 10**14 else ot
        except Exception:
            return None
    return None


# ----------------------------------------------------------------------------- 3. precedence
def precedence_step(ev: Dict[str, Any], refresh: bool) -> Dict[str, Any]:
    from data_lake.collectors import cross_venue_lifecycle as CVL
    try:
        if refresh or not INSTRUMENTS.exists():
            CVL.collect()
        by = VP.index_by_base(json.loads(INSTRUMENTS.read_text())["instruments"])
        d = VP.decide(ev["asset"], ev.get("t0") or ev["onboard_ts"], by)
    except Exception as e:
        return {"status": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:80])}
    return {"status": "ok", **{k: d.get(k) for k in ("classification", "first_elsewhere_venue", "first_elsewhere_symbol", "first_elsewhere_market", "first_elsewhere_ts", "lead_days", "confidence")}}


# ----------------------------------------------------------------------------- 4. tape MEXC + conditionnement + wash
def mexc_step(ev: Dict[str, Any]) -> Dict[str, Any]:
    from data_lake.collectors import mexc_pre_binance_tape as MX
    from data_lake.collectors.pre_binance_venue_tape import load_candles
    pr = ev["steps"].get("precedence", {})
    if pr.get("classification") != "OTHER_VENUE_FIRST" or pr.get("first_elsewhere_venue") != "mexc":
        return {"status": "not_applicable", "reason": "not MEXC_FIRST"}
    if not ev.get("t0") or not (ev["steps"].get("announcement") or {}).get("publication_ts_ms"):
        return {"status": "pending", "reason": "t0 or publication_ts missing"}
    mev = {"event_id": ev["event_id"], "asset": ev["asset"], "binance_symbol": ev["symbol"], "t0": ev["t0"], "mexc_symbol": pr.get("first_elsewhere_symbol"), "mexc_market": pr.get("first_elsewhere_market"),
           "mexc_listed_ts": pr.get("first_elsewhere_ts"), "lead_days": pr.get("lead_days")}
    try:
        man = MX.collect_event(mev, refetch=True); hourly = load_candles(((man.get("files") or {}).get("60m") or {}).get("path"))
        t0_ms = int(VPP.parse_ts(ev["t0"]).timestamp() * 1000); pub = ev["steps"]["announcement"]["publication_ts_ms"]
        r = PF.pre_announcement_return(hourly, pub, t0_ms, 24, H.MIN_CLOSES_24H) if hourly else {"pre_announcement_return_24h": None, "hours_in_window": 0}
        listed = int(VPP.parse_ts(pr["first_elsewhere_ts"]).timestamp() * 1000) if pr.get("first_elsewhere_ts") else None
        w = MVT.compute_event(hourly, t0_ms, pub, listed, man.get("market") or mev["mexc_market"]) if hourly else {"status": "NO_TAPE"}
    except PF.PostT0Leak as e:
        return {"status": "error", "error": "post-t0 candle refused: " + str(e)}
    except Exception as e:
        return {"status": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:80])}
    return {"status": "ok" if r.get("pre_announcement_return_24h") is not None else "insufficient", "tape_status": man.get("status"), "mexc_market": man.get("market") or mev["mexc_market"],
            **r, "wash_status": w.get("status"), "wash_features": {k: w.get(k) for k in MVT.FEATURES}}


# ----------------------------------------------------------------------------- 5. capacite a +15 min
def capacity_step(ev: Dict[str, Any]) -> Dict[str, Any]:
    from data_lake.indices import depth_capacity_features as DCF
    if not ev.get("t0"):
        return {"status": "pending", "reason": "t0 not yet known from Vision"}
    try:
        c = DCF.compute_event(ev["symbol"], ev["event_id"], ev["t0"])
    except Exception as e:
        return {"status": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:80])}
    w15 = next((w for w in c.get("windows", []) if w.get("window_min") == H.WINDOW_MIN), {})
    return {"status": "ok" if w15.get("effective_spread_bps") is not None else "pending", "capacity_event": c, "effective_spread_bps_15m": w15.get("effective_spread_bps")}


# ----------------------------------------------------------------------------- assemblage
def eligible(ev: Dict[str, Any]) -> bool:
    st = ev["steps"]
    return all(st.get(k, {}).get("status") == "ok" for k in ("announcement", "vision", "precedence", "mexc_tape", "capacity")) and ev.get("class") != "BAD_TIMESTAMP"


def classify(ev: Dict[str, Any]) -> None:
    pr = ev["steps"].get("precedence", {}); an = ev["steps"].get("announcement", {})
    ev["population"] = "MEXC_FIRST" if (pr.get("classification") == "OTHER_VENUE_FIRST" and pr.get("first_elsewhere_venue") == "mexc") else (pr.get("classification") or "UNKNOWN")
    ev["ts_disagreement_min"] = None
    if ev.get("t0_ms") and an.get("announced_opening_ts"):
        try:
            ann = int(VPP.parse_ts(an["announced_opening_ts"]).timestamp() * 1000); ev["ts_disagreement_min"] = abs(ev["t0_ms"] - ann) / 60_000
        except ValueError:
            pass
    ev["class"] = "BAD_TIMESTAMP" if (ev["ts_disagreement_min"] is not None and ev["ts_disagreement_min"] > MAX_TS_DISAGREEMENT_MIN) else ev["population"]


def write_inputs(reg: Dict[str, Any]) -> Dict[str, int]:
    evs = list(reg["events"].values()); n = {"universe": 0, "conditioning": 0, "causal": 0, "capacity": 0, "wash": 0}
    uni, cond, causal, cap, wash = [], [], [], [], []
    for ev in evs:
        if not ev.get("t0"):
            continue
        an = ev["steps"].get("announcement", {}); pr = ev["steps"].get("precedence", {}); mx = ev["steps"].get("mexc_tape", {}); cp = ev["steps"].get("capacity", {})
        uni.append({"event_id": ev["event_id"], "asset": ev["asset"], "symbol": ev["symbol"], "tradable_start_ts": ev["t0"], "tradable_start_ms": ev["t0_ms"], "publication_ts": an.get("publication_ts"), "publication_ts_ms": an.get("publication_ts_ms"),
                    "ts_disagreement_min": ev.get("ts_disagreement_min"), "launch_ts_source": "vision_first_bar"})
        causal.append({"event_id": ev["event_id"], "asset": ev["asset"], "symbol": ev["symbol"], "population": ev.get("population"), "class": ev.get("class"), "lead_time_days": pr.get("lead_days"),
                       "first_known_venue": pr.get("first_elsewhere_venue"), "first_known_venue_listing_ts": pr.get("first_elsewhere_ts"), "announced_opening_ts": an.get("announced_opening_ts"), "binance_opening_ts": ev["t0"]})
        if mx.get("status") in ("ok", "insufficient"):
            cond.append({"event_id": ev["event_id"], "asset": ev["asset"], "binance_symbol": ev["symbol"], "mexc_market": mx.get("mexc_market"), "t0": ev["t0"], "publication_ts_ms": an.get("publication_ts_ms"),
                         "pre_announcement_return_24h": mx.get("pre_announcement_return_24h"), "hours_in_window": mx.get("hours_in_window"), "window_end_ms": mx.get("window_end_ms"), "post_announcement_hours_dropped": mx.get("post_announcement_hours_dropped"), "announcement_cut": True})
            wash.append({"event_id": ev["event_id"], "asset": ev["asset"], "status": mx.get("wash_status"), "wash_volume_suspect": None, "programme_like": None, **(mx.get("wash_features") or {})})
        if cp.get("status") == "ok":
            cap.append(cp["capacity_event"])
    if wash:
        ranks = MVT.rank_events({w["event_id"]: {"status": w["status"], **{k: w.get(k) for k in MVT.FEATURES}} for w in wash})   # decile pre-declare, intra-forward
        for w in wash:
            w.update({k: ranks[w["event_id"]].get(k) for k in ("wash_volume_suspect", "programme_like", "volume_anomaly_rank")})
    now = _now()
    _write_atomic(FORWARD_DIR / "UNIVERSE.json", {"generated_at_utc": now, "forward_only": True, "events": uni})
    _write_atomic(FORWARD_DIR / "MEXC_PRE_ANNOUNCEMENT_RETURN_24H.json", {"generated_at_utc": now, "variable": "pre_announcement_return_24h", "rows": cond, "no_return_computed": True})
    _write_atomic(FORWARD_DIR / "CAUSAL_MATRIX.json", {"generated_at_utc": now, "rows": causal, "no_return_computed": True})
    _write_atomic(FORWARD_DIR / "CAPACITY_FEATURES.json", {"generated_at_utc": now, "windows_min": list(H.HORIZON_MIN.values()), "events": cap, "no_return_computed": True})
    _write_atomic(FORWARD_DIR / "WASH.json", {"generated_at_utc": now, "rows": wash, "no_alpha_test": True})
    return {"universe": len(uni), "conditioning": len(cond), "causal": len(causal), "capacity": len(cap), "wash": len(wash)}


def collect(refresh_instruments: bool = True, limit: Optional[int] = None) -> Dict[str, Any]:
    cut = cutoff_ms()
    if cut is None:
        return {"status": "not_sealed", "note": "no seal entry in the LOOK_LEDGER: forward collection has no cutoff yet"}
    reg = load_registry(); launches = scan_launches(cut); new = 0
    for l in launches[: limit or None]:
        eid = "fwd_" + l["symbol"].lower() + "_" + str(l["onboard_ms"])
        ev = reg["events"].get(eid) or {"event_id": eid, "symbol": l["symbol"], "asset": l["asset"], "onboard_ms": l["onboard_ms"], "onboard_ts": l["onboard_ts"], "first_seen_utc": _now(), "steps": {}}
        if eid not in reg["events"]:
            new += 1; log({"kind": "new_launch", "event_id": eid, "symbol": l["symbol"], "onboard_ts": l["onboard_ts"]})
        for step in STEPS:
            if ev["steps"].get(step, {}).get("status") in ("ok", "not_applicable"):
                continue
            fn = {"announcement": announcement_step, "vision": vision_step, "precedence": lambda e: precedence_step(e, refresh_instruments), "mexc_tape": mexc_step, "capacity": capacity_step}[step]
            res = fn(ev); ev["steps"][step] = {**res, "at": _now()}
            if step == "vision" and res.get("t0"):
                ev["t0"], ev["t0_ms"] = res["t0"], res["t0_ms"]
            log({"kind": "step", "event_id": eid, "step": step, "status": res.get("status"), "reason": res.get("reason") or res.get("error")})
            time.sleep(0.5)
        classify(ev); ev["eligible"] = eligible(ev); ev["updated_utc"] = _now(); reg["events"][eid] = ev
    reg["last_collect_utc"] = _now(); reg["seal_cutoff_ms"] = cut; reg["n_launches_after_seal"] = len(launches); reg["n_eligible"] = sum(1 for e in reg["events"].values() if e.get("eligible"))
    reg["n_mexc_first"] = sum(1 for e in reg["events"].values() if e.get("population") == "MEXC_FIRST")
    _write_atomic(REGISTRY, reg); written = write_inputs(reg)
    summary = {"seal_cutoff_ms": cut, "launches_after_seal": len(launches), "new_this_pass": new, "n_mexc_first": reg["n_mexc_first"], "n_eligible": reg["n_eligible"], "inputs_written": written,
               "look_rule": "n_eligible >= %d or %s" % (H.N_FORWARD_MIN, H.FORWARD_LATEST_LOOK)}
    log({"kind": "pass", **summary}); return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true", help="liste les lancements apres le scellement, sans rien collecter")
    ap.add_argument("--collect", action="store_true"); ap.add_argument("--no-refresh-instruments", action="store_true"); ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    if a.scan:
        cut = cutoff_ms(); print(json.dumps({"seal_cutoff_ms": cut, "launches_after_seal": scan_launches(cut) if cut else "not sealed"}, indent=1, ensure_ascii=False))
    elif a.collect:
        print(json.dumps(collect(not a.no_refresh_instruments, a.limit), indent=1, ensure_ascii=False))
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
