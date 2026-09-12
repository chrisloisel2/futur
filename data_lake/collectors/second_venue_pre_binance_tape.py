#!/usr/bin/env python3
"""
second_venue_pre_binance_tape.py -- le controle HONNETE du test anti-wash : MEME actif, MEMES heures, AUTRE place.

Pour chaque lancement H2 dont MEXC fut la premiere place, on cherche dans le magasin cross-venue (P9) un second marche
du meme actif (KuCoin, Bybit, OKX, Gate) deja cote avant la fenetre, et l'on stocke ses bougies HORAIRES sur la meme
fenetre de 72 h, bornees par la cloture <= t0. Rien apres t0 n'est demande. Aucun signal, aucun verdict.

Les places datees (KuCoin, Bybit perp, OKX) passent d'abord ; Gate et Bybit spot n'ont pas de date de cotation : on
tente, et la donnee decide (n heures dans la fenetre).
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_lake.collectors import venue_pre_binance_paths as VP
from data_lake.collectors.control_venue_pre_binance_tape import fetch_window
from data_lake.collectors.mexc_pre_binance_tape import log, mexc_first_events
from data_lake.collectors.venue_precedence import normalise_base
from data_lake.indices.mexc_volume_trust import H, MIN_HOURS, WINDOW_H, window_bounds

ROOT = Path(__file__).resolve().parents[2]
INSTRUMENTS = ROOT / "data" / "cross_venue" / "instruments.json"
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
ROLE = "same_asset_control"
MAX_TAPES = 2          #: au plus deux seconds marches par evenement
MAX_ATTEMPTS = 4
PACE_S = 0.4
GATE_MAX_POINTS = 10_000   #: Gate ne sert que les 10 000 dernieres bougies (~416 j en 1 h) : au-dela, ne pas demander
VENUE_ORDER = {"kucoin": 0, "bybit": 1, "okx": 2, "gate": 3}


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def _rel(p: Path) -> str:
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


def _ms(ts: Optional[str]) -> Optional[int]:
    return int(VP.parse_ts(ts).timestamp() * 1000) if ts else None


def publication_ms() -> Dict[str, Optional[int]]:
    if not UNIVERSE.exists():
        return {}
    return {e["event_id"]: e.get("publication_ts_ms") for e in json.loads(UNIVERSE.read_text())["events"]}


def load_instruments(path: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    p = Path(path) if path else INSTRUMENTS
    if not p.exists():
        return {}
    by: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for i in json.loads(p.read_text())["instruments"]:
        if i.get("venue") != "mexc" and (i.get("quote") or "").upper() == "USDT":
            by[normalise_base(i.get("base") or "")].append(i)
    return by


def candidates(ev: Dict[str, Any], by_base: Dict[str, List[Dict[str, Any]]], pub_ms: Optional[int]) -> List[Dict[str, Any]]:
    """Seconds marches du meme actif, dates d'abord (cotes >= 24 h avant le debut de la fenetre utile), puis non dates."""
    t0 = _ms(ev["t0"]); start, end = window_bounds(t0, pub_ms, None)
    dated, undated = [], []
    for i in by_base.get(normalise_base(ev["asset"]), []):
        c = {"venue": i["venue"], "market": i.get("market_type") or "spot", "symbol": i["symbol"], "first_listed_ts": i.get("first_listed_ts"), "dated": bool(i.get("first_listed_ts"))}
        if c["dated"]:
            lm = _ms(c["first_listed_ts"])
            if lm is not None and lm + 24 * H <= end - MIN_HOURS * H:
                c["overlap_h"] = round((end - max(start, lm + 24 * H)) / H, 1); dated.append(c)
        else:
            undated.append(c)
    dated.sort(key=lambda c: (-c["overlap_h"], VENUE_ORDER.get(c["venue"], 9), c["market"] != "spot"))
    undated.sort(key=lambda c: (VENUE_ORDER.get(c["venue"], 9), c["market"] != "spot"))
    return dated + undated


def manifest_path(event_id: str, root: Optional[Path] = None) -> Path:
    return (Path(root) if root else VP.STORE) / "second_venue" / "manifests" / ("%s.json" % event_id)


def collect_event(ev: Dict[str, Any], by_base: Dict[str, List[Dict[str, Any]]], pub_ms: Optional[int], root: Optional[Path] = None, refetch: bool = False) -> Dict[str, Any]:
    root = Path(root) if root else VP.STORE; mp = manifest_path(ev["event_id"], root)
    if mp.exists() and not refetch:
        try:
            return json.loads(mp.read_text())
        except ValueError:
            pass
    t0 = _ms(ev["t0"]); start, end = window_bounds(t0, pub_ms, None); req_start = t0 - WINDOW_H * H
    attempts: List[Dict[str, Any]] = []; tapes = 0
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    cands = [c for c in candidates(ev, by_base, pub_ms) if not (c["venue"] == "gate" and now_ms - req_start > GATE_MAX_POINTS * H)]
    for c in cands[:MAX_ATTEMPTS]:
        if tapes >= MAX_TAPES:
            break
        r = fetch_window(c["venue"], c["market"], c["symbol"], "60m", req_start, t0); time.sleep(PACE_S)
        in_w = [x for x in r["rows"] if x["open_time_ms"] >= start and x["open_time_ms"] + H <= end]
        p = VP.local_path(c["venue"], c["market"], c["symbol"], "60m", ev["t0"], root)
        if r["rows"]:
            _write_atomic(p, json.dumps({"venue": c["venue"], "market": c["market"], "symbol": c["symbol"], "interval": "60m", "t0": ev["t0"], "role": ROLE, "close_bounded": True,
                                          "fetched_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"), "url": r["url"], "raw_hash": r["raw_hash"], "rows": r["rows"]}, separators=(",", ":")))
        usable = len(in_w) >= MIN_HOURS
        attempts.append({**c, "status": r["status"], "rows": len(r["rows"]), "n_in_window": len(in_w), "usable": usable, "path": _rel(p) if r["rows"] else None, "raw_hash": r["raw_hash"], "error": r["error"]})
        log({"kind": "fetch", "role": ROLE, "event_id": ev["event_id"], "venue": c["venue"], "symbol": c["symbol"], "market": c["market"], "status": r["status"], "rows": len(r["rows"]), "n_in_window": len(in_w)}, root, "second_venue")
        tapes += usable
    man = {"event_id": ev["event_id"], "asset": ev["asset"], "role": ROLE, "t0": ev["t0"], "publication_ts_ms": pub_ms, "window_start_ms": start, "window_end_ms": end,
           "status": "collected" if tapes else ("no_candidate" if not attempts else "not_usable"), "usable_tapes": tapes, "attempts": attempts, "candidates_skipped_gate_depth": len(candidates(ev, by_base, pub_ms)) - len(cands),
           "written_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"), "no_post_t0_data": True, "no_alpha_test": True, "close_bounded": True}
    _write_atomic(mp, json.dumps(man, indent=1, ensure_ascii=False)); return man


def collect_all(limit: Optional[int] = None, refetch: bool = False) -> Dict[str, Any]:
    by = load_instruments(); pub = publication_ms(); evs = mexc_first_events(); evs = evs[:limit] if limit else evs
    mans = []
    for i, ev in enumerate(evs, 1):
        mans.append(collect_event(ev, by, pub.get(ev["event_id"]), refetch=refetch))
        if i % 20 == 0:
            print("  %d / %d" % (i, len(evs)), flush=True)
    used = Counter((a["venue"], a["market"]) for m in mans for a in m["attempts"] if a["usable"])
    return {"events": len(evs), "by_status": dict(Counter(m["status"] for m in mans)), "usable_by_venue": {"%s %s" % k: v for k, v in used.items()}, "manifests": mans}


def write_coverage(out: Optional[Path] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """Couverture du controle meme actif : depuis les manifestes seulement (aucune bougie lue)."""
    out = Path(out) if out else ROOT / "reports" / "data_acquisition"; root = Path(root) if root else VP.STORE; mdir = root / "second_venue" / "manifests"
    mans = [json.loads(p.read_text()) for p in sorted(mdir.glob("*.json"))] if mdir.exists() else []
    st = Counter(m["status"] for m in mans); used = Counter("%s %s" % (a["venue"], a["market"]) for m in mans for a in m["attempts"] if a["usable"])
    att = Counter(a["status"] for m in mans for a in m["attempts"]); tapes = Counter(m["usable_tapes"] for m in mans)
    doc = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "role": ROLE, "events": len(mans), "by_status": dict(st), "usable_tapes_per_event": {str(k): v for k, v in sorted(tapes.items())},
           "usable_by_venue_market": dict(used), "attempts_by_status": dict(att), "min_hours_in_window": MIN_HOURS, "gate_depth_limit_points": GATE_MAX_POINTS,
           "events": [{"event_id": m["event_id"], "asset": m["asset"], "status": m["status"], "usable_tapes": m["usable_tapes"],
                       "usable": [{"venue": a["venue"], "market": a["market"], "symbol": a["symbol"], "n_in_window": a["n_in_window"], "dated": a["dated"]} for a in m["attempts"] if a["usable"]]} for m in mans],
           "no_post_t0_data": True, "close_bounded": True, "no_alpha_test": True}
    _write_atomic(out / "SECOND_VENUE_PRE_BINANCE_TAPE_COVERAGE.json", json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    md = ["# Tape pre-Binance du second venue (meme actif, memes heures)", "", "Genere %s. Controle honnete du test anti-wash : le meme jeton, les memes heures de fenetre, sur une autre place que MEXC." % doc["generated_at_utc"], "",
          "| statut | evenements |", "|---|---|"] + ["| %s | %d |" % kv for kv in sorted(st.items())] + ["", "| tapes utilisables (>= %d h dans W) | evenements |" % MIN_HOURS, "|---|---|"] + \
         ["| %s | %d |" % kv for kv in sorted(tapes.items())] + ["", "| place marche | tapes utilisables |", "|---|---|"] + ["| %s | %d |" % kv for kv in sorted(used.items())] + \
         ["", "Tentatives : " + ", ".join("%s %d" % kv for kv in sorted(att.items())) + ".", "",
          "Limites : Gate ne sert que ses 10 000 dernieres bougies (~416 j en 1 h) ; Bybit spot et Gate n'ont pas de date de cotation (la donnee decide) ; KuCoin futures ne sert pas tout l'historique.",
          "Borne de cloture : chaque bougie stockee cloture au plus tard a t0. Rien apres t0 n'est demande."]
    _write_atomic(out / "SECOND_VENUE_PRE_BINANCE_TAPE_COVERAGE.md", "\n".join(md) + "\n")
    return {"events": len(mans), "by_status": dict(st), "usable_by_venue_market": dict(used)}


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--collect", action="store_true"); ap.add_argument("--limit", type=int); ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--coverage", action="store_true")
    a = ap.parse_args()
    if a.coverage:
        print(json.dumps(write_coverage(), indent=1, ensure_ascii=False))
    elif a.collect:
        r = collect_all(a.limit, a.refetch); print(json.dumps({k: v for k, v in r.items() if k != "manifests"}, indent=1))
    else:
        by = load_instruments(); pub = publication_ms()
        print(json.dumps({"mexc_first_events": len(mexc_first_events()), "with_dated_candidate": sum(1 for e in mexc_first_events() if any(c["dated"] for c in candidates(e, by, pub.get(e["event_id"]))))}))


if __name__ == "__main__":
    main()
