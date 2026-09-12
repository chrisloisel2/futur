#!/usr/bin/env python3
"""
control_venue_pre_binance_tape.py -- les tapes pre-Binance des places de CONTROLE (OKX, Bybit, KuCoin) pour les
evenements dont elles furent la premiere place. Memes fenetres que MEXC, meme borne : rien a ou apres t0.

Les 24 evenements non-MEXC ne sont PAS un controle negatif (autre actif, autre marche, autre regime de frais) :
ils servent a une seule difference descriptive au niveau de la place, avec intervalle de confiance. Le controle
honnete -- meme actif, second venue, memes heures -- est dans second_venue_pre_binance_tape.py. Aucun signal,
aucun verdict. Borne de cloture : une bougie est gardee seulement si open + intervalle <= t0 ; OKX en 1Dutc.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from data_lake.collectors import venue_pre_binance_paths as VP
from data_lake.collectors.pre_binance_venue_tape import other_venue_first_events

ROOT = Path(__file__).resolve().parents[2]
UA = {"User-Agent": "futur-pre-binance-control/1.0"}
PACE_S = 0.4
IV = {"okx": {"1d": "1Dutc", "60m": "1H", "5m": "5m"}, "bybit": {"1d": "D", "60m": "60", "5m": "5"}, "kucoin_spot": {"1d": "1day", "60m": "1hour", "5m": "5min"}, "kucoin_perp": {"1d": 1440, "60m": 60, "5m": 5},
      "gate": {"1d": "1d", "60m": "1h", "5m": "5m"}}


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def _rel(p: Path) -> str:
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


def _get(url: str, timeout: int = 30) -> Any:
    with urlopen(Request(url, headers=UA), timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def build_url(venue: str, market: str, symbol: str, interval: str, start_ms: int, end_ms: int) -> str:
    if venue == "okx":
        return "https://www.okx.com/api/v5/market/history-candles?instId=%s&bar=%s&after=%d&before=%d&limit=300" % (symbol, IV["okx"][interval], end_ms, start_ms - 1)
    if venue == "bybit":
        cat = "linear" if market == "perp" else "spot"
        return "https://api.bybit.com/v5/market/kline?category=%s&symbol=%s&interval=%s&start=%d&end=%d&limit=1000" % (cat, symbol, IV["bybit"][interval], start_ms, end_ms)
    if venue == "kucoin":
        if market == "perp":
            return "https://api-futures.kucoin.com/api/v1/kline/query?symbol=%s&granularity=%d&from=%d&to=%d" % (symbol, IV["kucoin_perp"][interval], start_ms, end_ms)
        return "https://api.kucoin.com/api/v1/market/candles?symbol=%s&type=%s&startAt=%d&endAt=%d" % (symbol, IV["kucoin_spot"][interval], start_ms // 1000, end_ms // 1000)
    if venue == "gate":
        if market == "perp":
            return "https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract=%s&interval=%s&from=%d&to=%d" % (symbol, IV["gate"][interval], start_ms // 1000, end_ms // 1000)
        return "https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=%s&interval=%s&from=%d&to=%d" % (symbol, IV["gate"][interval], start_ms // 1000, end_ms // 1000)
    raise ValueError(venue)


def normalise(venue: str, market: str, payload: Any) -> List[Dict[str, float]]:
    """-> [{open_time_ms, open, high, low, close, volume, quote_volume}] tries par temps."""
    out: List[Dict[str, float]] = []
    try:
        if venue == "okx":
            for k in payload.get("data") or []:                       # [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
                out.append({"open_time_ms": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                            "quote_volume": float(k[7]) if len(k) > 7 and k[7] not in ("", None) else (float(k[6]) if len(k) > 6 and k[6] not in ("", None) else None)})
        elif venue == "bybit":
            for k in (payload.get("result") or {}).get("list") or []:  # [start, o, h, l, c, volume, turnover]
                out.append({"open_time_ms": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                            "quote_volume": float(k[6]) if len(k) > 6 and k[6] not in ("", None) else None})
        elif venue == "kucoin" and market == "perp":
            for k in payload.get("data") or []:                        # [ts_ms, o, h, l, c, vol, turnover]
                out.append({"open_time_ms": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                            "quote_volume": float(k[6]) if len(k) > 6 else None})
        elif venue == "kucoin":
            for k in payload.get("data") or []:                        # [ts_s, o, c, h, l, vol, turnover]  (ordre KuCoin spot)
                out.append({"open_time_ms": int(k[0]) * 1000, "open": float(k[1]), "high": float(k[3]), "low": float(k[4]), "close": float(k[2]), "volume": float(k[5]),
                            "quote_volume": float(k[6]) if len(k) > 6 else None})
        elif venue == "gate" and market == "perp":
            for k in payload or []:                                    # {t, v (contrats), c, h, l, o, sum (quote)}
                out.append({"open_time_ms": int(k["t"]) * 1000, "open": float(k["o"]), "high": float(k["h"]), "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"]),
                            "quote_volume": float(k["sum"]) if k.get("sum") not in (None, "") else None})
        elif venue == "gate":
            for k in payload or []:                                    # [t_s, quote_volume, close, high, low, open, base_volume, closed]
                out.append({"open_time_ms": int(k[0]) * 1000, "open": float(k[5]), "high": float(k[3]), "low": float(k[4]), "close": float(k[2]), "volume": float(k[6]) if len(k) > 6 else None,
                            "quote_volume": float(k[1])})
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return []
    out.sort(key=lambda c: c["open_time_ms"]); return out


def fetch_window(venue: str, market: str, symbol: str, interval: str, start_ms: int, end_ms: int) -> Dict[str, Any]:
    url = build_url(venue, market, symbol, interval, start_ms, end_ms)
    for attempt in range(4):
        try:
            payload = _get(url); rows = VP.closed_by(normalise(venue, market, payload), interval, end_ms, start_ms)   # cloture <= t0
            return {"status": "ok" if rows else "empty", "url": url, "rows": rows, "raw_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest(), "error": None}
        except HTTPError as e:
            if e.code in (400, 404):
                return {"status": "not_found", "url": url, "rows": [], "raw_hash": None, "error": "HTTP %s" % e.code}
            time.sleep(20 * (attempt + 1) if e.code == 429 else 2 * (attempt + 1))
        except Exception as ex:
            err = "%s: %s" % (type(ex).__name__, str(ex)[:60]); time.sleep(1 + attempt)
    return {"status": "error", "url": url, "rows": [], "raw_hash": None, "error": locals().get("err", "unreachable")}


def collect_event(ev: Dict[str, Any], root: Optional[Path] = None, refetch: bool = False) -> Dict[str, Any]:
    root = Path(root) if root else VP.STORE; v = ev["venue"]; market = ev["market"] or "spot"; sym = ev["symbol"]
    mp = VP.manifest_path(v, ev["event_id"], root)
    if mp.exists() and not refetch:
        try:
            m = json.loads(mp.read_text())
            if m.get("status") in ("collected", "partial"):
                return m
        except ValueError:
            pass
    if not sym:
        man = {"event_id": ev["event_id"], "venue": v, "status": "not_collected", "reason": "no symbol in the precedence decision", "files": {}}
        _write_atomic(mp, json.dumps(man, indent=1)); return man
    files: Dict[str, Any] = {}
    for req in VP.requests_for(ev["t0"]):
        r = fetch_window(v, market, sym, req["interval"], req["start_ms"], req["end_ms"]); time.sleep(PACE_S)
        p = VP.local_path(v, market, sym, req["interval"], ev["t0"], root)
        if r["rows"]:
            _write_atomic(p, json.dumps({"venue": v, "market": market, "symbol": sym, "interval": req["interval"], "t0": ev["t0"], "fetched_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                          "url": r["url"], "raw_hash": r["raw_hash"], "rows": r["rows"]}, separators=(",", ":")))
        files[req["interval"]] = {"status": r["status"], "rows": len(r["rows"]), "path": _rel(p) if r["rows"] else None, "raw_hash": r["raw_hash"], "error": r["error"]}
    ok = [iv for iv, f in files.items() if f["status"] == "ok"]
    man = {"event_id": ev["event_id"], "venue": v, "symbol": sym, "market": market, "t0": ev["t0"], "listed_ts": ev.get("listed_ts"), "lead_days": ev.get("lead_days"),
           "status": "collected" if "1d" in ok else ("partial" if ok else "not_collected"), "files": files, "written_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "no_post_t0_data": True, "no_alpha_test": True, "role": "control", "close_bounded": True}
    _write_atomic(mp, json.dumps(man, indent=1, ensure_ascii=False)); return man


def collect_all(refetch: bool = False) -> Dict[str, Any]:
    evs = [e for e in other_venue_first_events() if e["venue"] in ("okx", "bybit", "kucoin")]
    mans = [collect_event(e, refetch=refetch) for e in evs]
    return {"events": len(evs), "by_venue": dict(Counter(e["venue"] for e in evs)), "by_status": dict(Counter(m["status"] for m in mans)), "manifests": mans}


def write_coverage(out: Optional[Path] = None, root: Optional[Path] = None) -> Dict[str, Any]:
    """Couverture des 24 tapes de controle (autre place premiere), depuis les manifestes seulement."""
    out = Path(out) if out else ROOT / "reports" / "data_acquisition"; root = Path(root) if root else VP.STORE; mans = []
    for v in ("okx", "bybit", "kucoin"):
        d = root / v / "manifests"
        for p in sorted(d.glob("*.json")) if d.exists() else []:
            m = json.loads(p.read_text())
            if m.get("role") == "control":
                mans.append(m)
    st = Counter(m["status"] for m in mans); by = Counter("%s %s" % (m["venue"], m.get("market")) for m in mans)
    rows = Counter(); [rows.update({iv: 1}) for m in mans for iv, f in m.get("files", {}).items() if f.get("status") == "ok"]
    doc = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "role": "control", "events": len(mans), "by_status": dict(st), "by_venue_market": dict(by),
           "files_ok_by_interval": dict(rows), "dropped_straddling_total": sum(sum((m.get("dropped_straddling") or {}).values()) for m in mans), "okx_daily_bar": "1Dutc",
           "events_list": [{"event_id": m["event_id"], "venue": m["venue"], "market": m.get("market"), "symbol": m.get("symbol"), "status": m["status"], "rows": {iv: f.get("rows") for iv, f in m.get("files", {}).items()}} for m in mans],
           "not_a_negative_control": "other asset, other market type, other fee regime, other era: usable only for a venue-level descriptive difference with CI", "no_post_t0_data": True, "close_bounded": True, "no_alpha_test": True}
    _write_atomic(out / "CONTROL_VENUE_PRE_BINANCE_TAPE_COVERAGE.json", json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    md = ["# Tapes pre-Binance des places de controle (autre place premiere : OKX, Bybit, KuCoin)", "", "Genere %s. %d evenements, memes fenetres que MEXC, borne de cloture <= t0, OKX en 1Dutc." % (doc["generated_at_utc"], len(mans)), "",
          "| place marche | evenements |", "|---|---|"] + ["| %s | %d |" % kv for kv in sorted(by.items())] + ["", "| statut | n |", "|---|---|"] + ["| %s | %d |" % kv for kv in sorted(st.items())] + \
         ["", "Fichiers ok par intervalle : " + ", ".join("%s %d" % kv for kv in sorted(rows.items())) + ". Bougies chevauchant t0 retirees par le correctif : %d." % doc["dropped_straddling_total"], "",
          "**Ce n'est pas un controle negatif** : " + doc["not_a_negative_control"] + "."]
    _write_atomic(out / "CONTROL_VENUE_PRE_BINANCE_TAPE_COVERAGE.md", "\n".join(md) + "\n")
    return {"events": len(mans), "by_status": dict(st)}


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--collect", action="store_true"); ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--refilter", action="store_true", help="borne de cloture sur le stock existant (okx, bybit, kucoin)"); ap.add_argument("--coverage", action="store_true"); a = ap.parse_args()
    if a.coverage:
        print(json.dumps(write_coverage(), indent=1))
    elif a.refilter:
        from data_lake.collectors.mexc_pre_binance_tape import refilter_store
        print(json.dumps({v: refilter_store(v) for v in ("okx", "bybit", "kucoin")}, indent=1))
    elif a.collect:
        r = collect_all(a.refetch); print(json.dumps({k: v for k, v in r.items() if k != "manifests"}, indent=1))
        for m in r["manifests"]:
            print("  %-7s %-14s %-5s %-9s %s" % (m["venue"], m.get("symbol"), m.get("market"), m["status"], {iv: f["rows"] for iv, f in m.get("files", {}).items()}))


if __name__ == "__main__":
    main()
