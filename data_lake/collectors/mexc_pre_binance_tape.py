#!/usr/bin/env python3
"""
mexc_pre_binance_tape.py -- la dynamique du marche MEXC AVANT que Binance ouvre son perpetuel.

Pour chaque lancement H2 dont MEXC fut la premiere place : bougies journalieres (30 j), horaires (3 j) et
5 min (6 h) jusqu'a t0 exclu. Brut sous data/pre_binance/mexc/ (gitignore), un manifeste par evenement,
journal append-only. Rien apres t0 n'est demande : ce module ne peut pas, par construction, mesurer une
performance post-Binance.

Borne de cloture (P12) : une bougie est gardee seulement si open + intervalle <= t0. La version P11 bornait sur
l'ouverture et gardait donc une bougie qui chevauchait t0 ; `refilter_store` retire ces bougies du stock existant
et l'inscrit dans le manifeste (close_bounded, dropped_straddling) et le journal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from data_lake.collectors import venue_pre_binance_paths as VP

ROOT = Path(__file__).resolve().parents[2]
PRECEDENCE = ROOT / "data" / "cross_venue" / "precedence.json"
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
SPOT = "https://api.mexc.com/api/v3/klines?symbol=%s&interval=%s&startTime=%d&endTime=%d&limit=1000"
PERP = "https://contract.mexc.com/api/v1/contract/kline/%s?interval=%s&start=%d&end=%d"
PERP_IV = {"1d": "Day1", "60m": "Min60", "5m": "Min5"}
PACE_S = 0.35
UA = {"User-Agent": "futur-pre-binance/1.0"}


def _rel(p: Path) -> str:
    """Chemin relatif au depot quand possible, sinon absolu (tests hors depot)."""
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def _get(url: str, timeout: int = 30) -> Any:
    with urlopen(Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def log(rec: Dict[str, Any], root: Optional[Path] = None, venue: str = "mexc") -> None:
    p = (Path(root) if root else VP.STORE) / venue / "tape_log.jsonl"; p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts_local": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec}, ensure_ascii=False, default=str) + "\n")


def mexc_first_events() -> List[Dict[str, Any]]:
    """Les evenements H2 dont la premiere place datee est MEXC, avec le symbole et le marche MEXC de la decision P9."""
    if not PRECEDENCE.exists() or not UNIVERSE.exists():
        return []
    dec = {d["asset"]: d for d in json.loads(PRECEDENCE.read_text())["decisions"]}
    out = []
    for e in json.loads(UNIVERSE.read_text())["events"]:
        d = dec.get(e["asset"])
        if d and d.get("classification") == "OTHER_VENUE_FIRST" and d.get("first_elsewhere_venue") == "mexc":
            out.append({"event_id": e["event_id"], "asset": e["asset"], "binance_symbol": e["symbol"], "t0": e["tradable_start_ts"],
                        "mexc_symbol": d.get("first_elsewhere_symbol"), "mexc_market": d.get("first_elsewhere_market"), "mexc_listed_ts": d.get("first_elsewhere_ts"), "lead_days": d.get("lead_days")})
    return out


def normalise_candles(payload: Any, market: str) -> List[Dict[str, float]]:
    """-> [{open_time_ms, open, high, low, close, volume, quote_volume}], tries par temps. Format spot (liste de listes,
    Binance-like) ou contrat (dict de colonnes)."""
    out: List[Dict[str, float]] = []
    if market == "spot" and isinstance(payload, list):
        for k in payload:
            if not isinstance(k, list) or len(k) < 6:
                continue
            try:
                out.append({"open_time_ms": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                            "quote_volume": float(k[7]) if len(k) > 7 and k[7] not in ("", None) else None})
            except (TypeError, ValueError):
                continue
    elif market == "perp" and isinstance(payload, dict):
        d = payload.get("data") or {}
        t = d.get("time") or []
        for i, ts in enumerate(t):
            try:
                out.append({"open_time_ms": int(ts) * 1000, "open": float(d["open"][i]), "high": float(d["high"][i]), "low": float(d["low"][i]), "close": float(d["close"][i]),
                            "volume": float(d["vol"][i]) if "vol" in d else None, "quote_volume": float(d["amount"][i]) if "amount" in d else None})
            except (KeyError, IndexError, TypeError, ValueError):
                continue
    out.sort(key=lambda c: c["open_time_ms"]); return out


def fetch_window(symbol: str, market: str, interval: str, start_ms: int, end_ms: int) -> Dict[str, Any]:
    url = SPOT % (symbol, interval, start_ms, end_ms) if market == "spot" else PERP % (symbol, PERP_IV[interval], start_ms // 1000, end_ms // 1000)
    for attempt in range(4):
        try:
            payload = _get(url); rows = normalise_candles(payload, market)
            rows = VP.closed_by(rows, interval, end_ms, start_ms)                    # rien qui cloture apres t0, par construction
            return {"status": "ok" if rows else "empty", "url": url, "rows": rows, "raw_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(), "error": None}
        except HTTPError as e:
            if e.code in (400, 404):
                return {"status": "not_found", "url": url, "rows": [], "raw_hash": None, "error": "HTTP %s" % e.code}
            time.sleep(2 * (attempt + 1) if e.code != 429 else 20 * (attempt + 1))
        except Exception as ex:
            err = "%s: %s" % (type(ex).__name__, str(ex)[:60]); time.sleep(1 + attempt)
    return {"status": "error", "url": url, "rows": [], "raw_hash": None, "error": locals().get("err", "unreachable")}


def collect_event(ev: Dict[str, Any], root: Optional[Path] = None, refetch: bool = False) -> Dict[str, Any]:
    root = Path(root) if root else VP.STORE
    mp = VP.manifest_path("mexc", ev["event_id"], root)
    if mp.exists() and not refetch:
        try:
            return json.loads(mp.read_text())
        except ValueError:
            pass
    sym, market = ev.get("mexc_symbol"), ev.get("mexc_market") or "spot"
    if not sym:
        man = {"event_id": ev["event_id"], "venue": "mexc", "status": "not_collected", "reason": "no MEXC symbol in the precedence decision", "files": {}}
        _write_atomic(mp, json.dumps(man, indent=1)); return man
    files: Dict[str, Any] = {}
    for req in VP.requests_for(ev["t0"]):
        r = fetch_window(sym, market, req["interval"], req["start_ms"], req["end_ms"]); time.sleep(PACE_S)
        p = VP.local_path("mexc", market, sym, req["interval"], ev["t0"], root)
        if r["rows"]:
            _write_atomic(p, json.dumps({"venue": "mexc", "market": market, "symbol": sym, "interval": req["interval"], "t0": ev["t0"], "fetched_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "url": r["url"], "raw_hash": r["raw_hash"], "rows": r["rows"]}, separators=(",", ":")))
        files[req["interval"]] = {"status": r["status"], "rows": len(r["rows"]), "path": _rel(p) if r["rows"] else None, "raw_hash": r["raw_hash"], "error": r["error"],
                                  "first_open_ms": r["rows"][0]["open_time_ms"] if r["rows"] else None, "last_open_ms": r["rows"][-1]["open_time_ms"] if r["rows"] else None}
        log({"kind": "fetch", "event_id": ev["event_id"], "symbol": sym, "market": market, "interval": req["interval"], "status": r["status"], "rows": len(r["rows"]), "error": r["error"]}, root)
    ok = [iv for iv, f in files.items() if f["status"] == "ok"]
    man = {"event_id": ev["event_id"], "venue": "mexc", "symbol": sym, "market": market, "t0": ev["t0"], "mexc_listed_ts": ev.get("mexc_listed_ts"), "lead_days": ev.get("lead_days"),
           "status": "collected" if "1d" in ok else ("partial" if ok else "not_collected"), "files": files, "written_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "no_post_t0_data": True, "no_alpha_test": True, "close_bounded": True}
    _write_atomic(mp, json.dumps(man, indent=1, ensure_ascii=False)); return man


def refilter_store(venue: str = "mexc", root: Optional[Path] = None) -> Dict[str, Any]:
    """Correctif de borne sur le stock deja telecharge : retire de chaque fichier les bougies qui chevauchent t0
    (open < t0 < open + intervalle), marque le manifeste close_bounded et journalise. Idempotent."""
    root = Path(root) if root else VP.STORE; mdir = root / venue / "manifests"
    n = {"manifests": 0, "already": 0, "files": 0, "dropped": 0}
    for mp in sorted(mdir.glob("*.json")) if mdir.exists() else []:
        try:
            man = json.loads(mp.read_text())
        except ValueError:
            continue
        n["manifests"] += 1
        if man.get("close_bounded") or man.get("status") not in ("collected", "partial"):
            n["already"] += 1; continue
        t0_ms = int(VP.parse_ts(man["t0"]).timestamp() * 1000); dropped: Dict[str, int] = {}
        for iv, f in man.get("files", {}).items():
            if not f.get("path"):
                continue
            fp = Path(f["path"]) if Path(f["path"]).is_absolute() else ROOT / f["path"]
            if not fp.exists():
                continue
            doc = json.loads(fp.read_text()); kept = VP.closed_by(doc["rows"], iv, t0_ms)
            dropped[iv] = len(doc["rows"]) - len(kept); n["files"] += 1; n["dropped"] += dropped[iv]
            if dropped[iv]:
                doc["rows"] = kept; doc["close_bounded"] = True; doc["dropped_straddling"] = dropped[iv]
                _write_atomic(fp, json.dumps(doc, separators=(",", ":")))
                f["rows"] = len(kept); f["last_open_ms"] = kept[-1]["open_time_ms"] if kept else None
                if not kept:
                    f["status"] = "empty"
        man["close_bounded"] = True; man["dropped_straddling"] = dropped
        _write_atomic(mp, json.dumps(man, indent=1, ensure_ascii=False))
        log({"kind": "correction", "what": "close_bound_refilter", "event_id": man.get("event_id"), "dropped": dropped}, root, venue)
    return n


def collect_all(limit: Optional[int] = None, refetch: bool = False) -> Dict[str, Any]:
    evs = mexc_first_events()[:limit] if limit else mexc_first_events()
    mans = []
    for i, ev in enumerate(evs, 1):
        mans.append(collect_event(ev, refetch=refetch))
        if i % 20 == 0:
            print("  %d / %d" % (i, len(evs)), flush=True)
    from collections import Counter
    return {"events": len(evs), "by_status": dict(Counter(m["status"] for m in mans)), "manifests": mans}


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--collect", action="store_true"); ap.add_argument("--limit", type=int); ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--refilter", action="store_true", help="applique la borne de cloture au stock existant")
    a = ap.parse_args()
    if a.refilter:
        print(json.dumps(refilter_store(), indent=1))
    elif a.collect:
        r = collect_all(a.limit, a.refetch); print(json.dumps({k: v for k, v in r.items() if k != "manifests"}, indent=1))
    else:
        print(json.dumps({"mexc_first_events": len(mexc_first_events())}))


if __name__ == "__main__":
    main()
