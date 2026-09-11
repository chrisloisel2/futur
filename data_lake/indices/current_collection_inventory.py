#!/usr/bin/env python3
"""
current_collection_inventory.py -- ce que la collecte P4 a REELLEMENT sur disque, avec hashes.

Scanne data_lake/market_state (best-effort : ne leve jamais si les donnees sont absentes) et
produit reports/data_acquisition/CURRENT_COLLECTION_INVENTORY.{md,json} et
CURRENT_COLLECTION_HEALTH.md. Aucun prix, aucun signal, aucun verdict.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
MS = ROOT / "data_lake" / "market_state"
OUT = ROOT / "reports" / "data_acquisition"
FIRST_FIELDS = ("first_orderbook_ts", "first_trade_ts", "first_mark_ts", "first_index_ts", "first_oi_ts")


def _sha(p: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""):
                h.update(c)
        return h.hexdigest()
    except OSError:
        return None


def _lines(p: Path):
    """Lignes d'un jsonl ou jsonl.gz ; les partitions gz encore ouvertes par le collecteur n'ont pas de
    trailer : on passe par tape_io.decompress_partial (membres concatenes, fin tronquee toleree)."""
    if p.suffix == ".gz":
        try:
            from data_lake.collectors.tape_io import iter_lines
            for l in iter_lines(p):
                l = l.strip()
                if l:
                    yield l
        except Exception:
            return
        return
    try:
        with open(p, "rt", encoding="utf-8", errors="replace") as f:
            for l in f:
                l = l.strip()
                if l:
                    yield l
    except (OSError, EOFError):
        return


def _jsonl(p: Path):
    for l in _lines(p):
        try:
            yield json.loads(l)
        except ValueError:
            continue


def _size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def scan(root: Path = MS) -> Dict[str, Any]:
    inv: Dict[str, Any] = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "root": str(root), "present": root.exists(),
                           "exchange_info": {}, "lifecycle": {}, "market_state_snapshot": {}, "windows": {"count": 0, "items": []}, "triggers": {}, "watch_log": {}, "disk": {}, "hashes": {}, "no_alpha_test": True}
    if not root.exists():
        return inv
    # exchange_info
    for vd in sorted((root / "exchange_info").glob("venue=*")) if (root / "exchange_info").exists() else []:
        snaps = sorted(vd.glob("snapshot_*.json")); last = {}
        if snaps:
            try:
                last = json.loads(snaps[-1].read_text())
            except ValueError:
                last = {}
        ch = vd / "changes.jsonl"; n_ch = sum(1 for _ in _lines(ch)) if ch.exists() else 0
        kinds = Counter(c.get("type") for c in _jsonl(ch)) if ch.exists() else Counter()
        inv["exchange_info"][vd.name.split("=", 1)[1]] = {"snapshots": len(snaps), "last_snapshot_at": last.get("captured_at_local"), "n_symbols": last.get("n_symbols"),
                                                          "changes_logged": n_ch, "changes_by_type": dict(kinds.most_common(8)), "last_view_sha256": _sha(vd / "last_view.json") if (vd / "last_view.json").exists() else None,
                                                          "changes_sha256": _sha(ch) if ch.exists() else None}
    # lifecycle
    ev = [e for f in sorted(root.glob("symbol_lifecycle_event/venue=*/date=*/*.jsonl")) for e in _jsonl(f)]
    st_p = root / "lifecycle" / "state.json"; st = {}
    if st_p.exists():
        try:
            st = json.loads(st_p.read_text())
        except ValueError:
            st = {}
    inv["lifecycle"] = {"events": len(ev), "by_kind": dict(Counter(e.get("event_kind") for e in ev)), "by_venue": dict(Counter(e.get("venue") for e in ev)),
                        "by_change": dict(Counter((e.get("detail") or {}).get("change") for e in ev if (e.get("detail") or {}).get("change")).most_common(6)),
                        "last_event_at": max((e.get("detected_at_local") or "" for e in ev), default=None), "first_event_at": min((e.get("detected_at_local") or "" for e in ev), default=None),
                        "symbols_tracked": len(st), "dead": sum(1 for v in st.values() if v.get("died_at")), "max_history_len": max((len(v.get("history", [])) for v in st.values()), default=0),
                        "state_size_mb": round(st_p.stat().st_size / 1e6, 2) if st_p.exists() else None, "state_sha256": _sha(st_p) if st_p.exists() else None,
                        "births": [(e.get("detected_at_local"), e.get("venue"), e.get("symbol"), e.get("new_status")) for e in ev if e.get("event_kind") == "born"][-10:],
                        "deaths": [(e.get("detected_at_local"), e.get("venue"), e.get("symbol"), e.get("old_status")) for e in ev if e.get("event_kind") == "died"][-10:],
                        "status_changes": [(e.get("detected_at_local"), e.get("venue"), e.get("symbol"), e.get("old_status"), e.get("new_status")) for e in ev if e.get("event_kind") == "status_change"][-10:]}
    # light snapshots (watch)
    per = {}
    for f in sorted(root.glob("market_state_snapshot/venue=*/date=*/*.jsonl*")):
        name = f.name.split(".")[0]; d = per.setdefault(name, {"files": 0, "rows": 0, "symbols": set(), "first_ts_local": None, "last_ts_local": None, "sources": Counter(), "bytes": 0})
        d["files"] += 1; d["bytes"] += f.stat().st_size
        for r in _jsonl(f):
            d["rows"] += 1; d["symbols"].add(r.get("symbol")); d["sources"][r.get("source")] += 1; t = r.get("ts_local")
            if t:
                d["first_ts_local"] = min(d["first_ts_local"] or t, t); d["last_ts_local"] = max(d["last_ts_local"] or t, t)
    inv["market_state_snapshot"] = {k: {**v, "symbols": len(v["symbols"]), "sources": dict(v["sources"]), "size_mb": round(v["bytes"] / 1e6, 1)} for k, v in per.items()}
    # windows
    items = []
    for m in sorted(root.glob("windows/*/manifest.json")):
        try:
            d = json.loads(m.read_text())
        except ValueError:
            continue
        ft = d.get("first_timestamps") or {}; mc = d.get("message_counts") or {}
        try:
            dur = (datetime.fromisoformat(d["end_ts"]) - datetime.fromisoformat(d["start_ts"])).total_seconds() if d.get("end_ts") else None
        except (ValueError, TypeError):
            dur = None
        items.append({"trigger_id": d.get("trigger_id"), "trigger_type": d.get("trigger_type"), "symbol": d.get("symbol"), "start_ts": d.get("start_ts"), "end_ts": d.get("end_ts"), "final": d.get("final"),
                      "duration_s": dur, "snapshots": (d.get("row_counts") or {}).get("snapshots"), "completeness_score": d.get("completeness_score"), "missing_fields": d.get("missing_fields") or [],
                      "first_fields_present": {k: bool(ft.get(k)) for k in FIRST_FIELDS}, "ws_reconnects": mc.get("ws_reconnects"), "latency_ms_median": d.get("latency_ms_median"),
                      "pre_window_missing": d.get("pre_window_missing"), "files_hashed": len(d.get("sha256") or {}), "size_mb": round(_size(m.parent) / 1e6, 1), "manifest_sha256": _sha(m)})
    fin = [w for w in items if w["final"]]
    inv["windows"] = {"count": len(items), "final": len(fin), "in_progress": len(items) - len(fin), "by_type": dict(Counter(w["trigger_type"] for w in items)),
                      "mean_completeness_final": round(statistics.mean([w["completeness_score"] for w in fin]), 4) if fin else None,
                      "missing_fields_top": dict(Counter(f for w in fin for f in w["missing_fields"]).most_common(8)),
                      "first_fields_missing_in_final": dict(Counter(k for w in fin for k, v in w["first_fields_present"].items() if not v)),
                      "total_size_mb": round(sum(w["size_mb"] for w in items), 1), "reconnects_total": sum(w["ws_reconnects"] or 0 for w in items), "items": items}
    # triggers
    tl = root / "triggers" / "triggers.jsonl"; tr = list(_jsonl(tl)) if tl.exists() else []
    inv["triggers"] = {"total": len(tr), "fired": sum(1 for t in tr if t.get("pid")), "logged_only": sum(1 for t in tr if t.get("fired") and not t.get("pid")),
                       "skipped_by_reason": dict(Counter((t.get("decision") or "").split(" (")[0][:48] for t in tr if not t.get("fired")).most_common(8)),
                       "by_type": dict(Counter(t.get("trigger_type") for t in tr)), "fired_by_type": dict(Counter(t.get("trigger_type") for t in tr if t.get("pid"))),
                       "last_at": max((t.get("decided_at_local") or "" for t in tr), default=None), "sha256": _sha(tl) if tl.exists() else None}
    # watch log
    wl = root / "watch.log"; hb = []
    if wl.exists():
        for l in _lines(wl):
            if '"heartbeat": true' in l:
                try:
                    hb.append(json.loads(l))
                except ValueError:
                    pass
    if hb:
        rss = [h.get("rss_mb") for h in hb if h.get("rss_mb") is not None]; last = hb[-1]
        inv["watch_log"] = {"heartbeats": len(hb), "first_at": hb[0].get("ts"), "last_at": last.get("ts"), "rss_mb_first": rss[0] if rss else None, "rss_mb_last": rss[-1] if rss else None, "rss_mb_max": max(rss) if rss else None,
                            "errors_last": {k: v for k, v in last.items() if k.endswith("_errors")}, "active_captures_last": last.get("active_captures"), "polls_last": {k: v for k, v in last.items() if k.endswith("_polls")},
                            "force_orders_last": last.get("force_orders"), "light_snapshots_last": last.get("light_snapshots")}
    else:
        inv["watch_log"] = {"heartbeats": 0}
    # disk
    try:
        du = shutil.disk_usage(str(root)); inv["disk"] = {"tape_size_mb": round(_size(root) / 1e6, 1), "free_gb": round(du.free / 1e9, 1), "total_gb": round(du.total / 1e9, 1)}
    except OSError:
        inv["disk"] = {}
    return inv


def health(inv: Dict[str, Any]) -> List[Dict[str, str]]:
    """Regles simples, chacune avec sa preuve. level: ok / warn / critical."""
    out = []
    now = datetime.now(timezone.utc)
    def age_min(ts):
        try:
            return (now - datetime.fromisoformat(ts)).total_seconds() / 60
        except (ValueError, TypeError):
            return None
    if not inv.get("present"):
        return [{"level": "critical", "check": "tape present", "detail": "data_lake/market_state absent: the watch never ran here"}]
    wl = inv.get("watch_log") or {}
    a = age_min(wl.get("last_at")) if wl.get("heartbeats") else None
    out.append({"level": "ok" if (a is not None and a < 15) else "critical", "check": "heartbeat age", "detail": f"last heartbeat {wl.get('last_at')} ({round(a) if a is not None else 'n/a'} min ago)"})
    if wl.get("rss_mb_last") is not None:
        out.append({"level": "warn" if wl["rss_mb_last"] > 800 else "ok", "check": "watch RSS", "detail": f"first {wl.get('rss_mb_first')} MB, last {wl['rss_mb_last']} MB, max {wl.get('rss_mb_max')} MB over {wl.get('heartbeats')} heartbeats (plateau ≈ 560 MB observed; unbounded growth not confirmed)"})
    errs = {k: v for k, v in (wl.get("errors_last") or {}).items() if v}
    out.append({"level": "warn" if errs else "ok", "check": "watch errors", "detail": f"{errs or 'none'} (cumulative since start)"})
    for v, e in (inv.get("exchange_info") or {}).items():
        a2 = age_min(e.get("last_snapshot_at"))
        out.append({"level": "ok", "check": f"exchangeInfo {v}", "detail": f"{e.get('n_symbols')} symbols, {e.get('snapshots')} snapshots, last change archived {e.get('last_snapshot_at')} ({round(a2) if a2 is not None else 'n/a'} min ago; a quiet market changes rarely: age is not an alarm by itself)"})
    lc = inv.get("lifecycle") or {}
    out.append({"level": "warn" if (lc.get("by_change") or {}).get("filters_changed", 0) > 1000 else "ok", "check": "lifecycle noise", "detail": f"{lc.get('events')} events, by kind {lc.get('by_kind')}, by change {lc.get('by_change')}; max history per symbol {lc.get('max_history_len')}, state {lc.get('state_size_mb')} MB"})
    w = inv.get("windows") or {}
    out.append({"level": "ok" if w.get("count") else "warn", "check": "triggered windows", "detail": f"{w.get('count')} windows ({w.get('final')} final, {w.get('in_progress')} in progress), mean completeness of final {w.get('mean_completeness_final')}, missing fields top {w.get('missing_fields_top')}, reconnects {w.get('reconnects_total')}, {w.get('total_size_mb')} MB"})
    miss = w.get("first_fields_missing_in_final") or {}
    out.append({"level": "warn" if miss else "ok", "check": "first_* timestamps in final captures", "detail": f"missing counts {miss or 'none'} — a capture without them is debug material, not alpha material"})
    stale = [x["trigger_id"] for x in w.get("items", []) if not x["final"] and x.get("start_ts") and (age_min(x["start_ts"]) or 0) > 7 * 60]
    out.append({"level": "warn" if stale else "ok", "check": "captures past their window without final manifest", "detail": stale or "none"})
    t = inv.get("triggers") or {}
    out.append({"level": "warn" if t.get("total", 0) > 5000 else "ok", "check": "trigger journal volume", "detail": f"{t.get('total')} decisions, {t.get('fired')} fired ({t.get('fired_by_type')}), skipped by reason {t.get('skipped_by_reason')}"})
    d = inv.get("disk") or {}
    out.append({"level": "critical" if (d.get("free_gb") or 99) < 10 else ("warn" if (d.get("free_gb") or 99) < 25 else "ok"), "check": "disk", "detail": f"tape {d.get('tape_size_mb')} MB, free {d.get('free_gb')} GB of {d.get('total_gb')} GB"})
    births = lc.get("births") or []
    out.append({"level": "ok", "check": "real perp births captured so far", "detail": f"{len([b for b in births if b[1] == 'binance_um'])} USDS-M births since the watch started; new_perp_listing captures fired: {(t.get('fired_by_type') or {}).get('new_perp_listing', 0)}"})
    return out


def write(inv: Dict[str, Any], out: Path = OUT) -> Dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "CURRENT_COLLECTION_INVENTORY.json").write_text(json.dumps(inv, indent=1, ensure_ascii=False, default=str) + "\n")
    h = health(inv)
    L = [f"# CURRENT COLLECTION INVENTORY — P4 market_state_tape ({inv['generated_at_utc'][:19]} UTC)", "", f"Root `{inv['root']}` present: {inv['present']}. Best-effort scan; no price join, no signal, no verdict. Hashes are sha256 of the files named.", ""]
    if inv["present"]:
        L += ["## exchangeInfo", "", "| venue | symbols | snapshots archived | changes logged | by type | last change archived | last_view sha256 |", "|---|---|---|---|---|---|---|"]
        for v, e in inv["exchange_info"].items():
            L.append(f"| {v} | {e['n_symbols']} | {e['snapshots']} | {e['changes_logged']} | {e['changes_by_type']} | {e['last_snapshot_at']} | `{(e['last_view_sha256'] or '')[:16]}` |")
        lc = inv["lifecycle"]
        L += ["", "## symbol_lifecycle_event", "", f"- events: **{lc['events']}** ({lc['first_event_at']} → {lc['last_event_at']}); by kind {lc['by_kind']}; by venue {lc['by_venue']}; by change {lc['by_change']}",
              f"- state: {lc['symbols_tracked']} symbols tracked, {lc['dead']} dead, max history {lc['max_history_len']}, {lc['state_size_mb']} MB, sha256 `{(lc['state_sha256'] or '')[:16]}`",
              f"- births (last 10): {lc['births'] or 'none yet'}", f"- deaths (last 10): {lc['deaths'] or 'none yet'}", f"- status changes (last 10): {lc['status_changes'] or 'none yet'}",
              "", "## market_state_snapshot (watch)", "", "| stream | files | rows | symbols | sources | first | last | size |", "|---|---|---|---|---|---|---|---|"]
        for k, v in inv["market_state_snapshot"].items():
            L.append(f"| {k} | {v['files']} | {v['rows']} | {v['symbols']} | {v['sources']} | {(v['first_ts_local'] or '')[:19]} | {(v['last_ts_local'] or '')[:19]} | {v['size_mb']} MB |")
        w = inv["windows"]
        L += ["", "## triggered windows", "", f"- {w['count']} windows ({w['final']} final, {w['in_progress']} in progress), by type {w['by_type']}, mean completeness of final {w['mean_completeness_final']}, missing fields top {w['missing_fields_top']}, first_* missing in final {w['first_fields_missing_in_final']}, reconnects {w['reconnects_total']}, total {w['total_size_mb']} MB", "",
              "| window | type | symbol | start | final | duration s | snapshots | completeness | missing | first_* | reconnects | latency ms | pre-window missing | files hashed | size MB | manifest sha256 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for x in w["items"]:
            ff = "".join("1" if x["first_fields_present"][k] else "0" for k in FIRST_FIELDS)
            L.append(f"| {x['trigger_id']} | {x['trigger_type']} | {x['symbol']} | {(x['start_ts'] or '')[:16]} | {x['final']} | {round(x['duration_s']) if x['duration_s'] else '-'} | {x['snapshots']} | {x['completeness_score']} | {', '.join(x['missing_fields']) or '-'} | {ff} | {x['ws_reconnects']} | {round(x['latency_ms_median']) if x['latency_ms_median'] else '-'} | {x['pre_window_missing']} | {x['files_hashed']} | {x['size_mb']} | `{(x['manifest_sha256'] or '')[:12]}` |")
        t = inv["triggers"]; wl = inv["watch_log"]; d = inv["disk"]
        L += ["", "(first_* column order: orderbook, trade, mark, index, oi; 1 = present)", "", "## triggers", "", f"- {t['total']} decisions, **{t['fired']} captures fired** ({t['fired_by_type']}), {t['logged_only']} logged-only (dry runs), skipped by reason {t['skipped_by_reason']}, by type {t['by_type']}, last {t['last_at']}, journal sha256 `{(t['sha256'] or '')[:16]}`",
              "", "## watch.log", "", f"- heartbeats {wl.get('heartbeats')} ({wl.get('first_at')} → {wl.get('last_at')}), RSS first/last/max {wl.get('rss_mb_first')}/{wl.get('rss_mb_last')}/{wl.get('rss_mb_max')} MB, errors {wl.get('errors_last')}, polls {wl.get('polls_last')}, force orders seen {wl.get('force_orders_last')}, light snapshots {wl.get('light_snapshots_last')}, active captures {wl.get('active_captures_last')}",
              "", "## disk", "", f"- tape {d.get('tape_size_mb')} MB; free {d.get('free_gb')} GB of {d.get('total_gb')} GB"]
    (out / "CURRENT_COLLECTION_INVENTORY.md").write_text("\n".join(L) + "\n")
    H = [f"# CURRENT COLLECTION HEALTH ({inv['generated_at_utc'][:19]} UTC)", "", "| level | check | detail |", "|---|---|---|"] + [f"| **{x['level']}** | {x['check']} | {x['detail']} |" for x in h]
    (out / "CURRENT_COLLECTION_HEALTH.md").write_text("\n".join(H) + "\n")
    return {"json": out / "CURRENT_COLLECTION_INVENTORY.json", "md": out / "CURRENT_COLLECTION_INVENTORY.md", "health": out / "CURRENT_COLLECTION_HEALTH.md"}


def main():
    ap = argparse.ArgumentParser(description=__doc__); ap.add_argument("--root", default=str(MS)); ap.add_argument("--out", default=str(OUT)); a = ap.parse_args()
    inv = scan(Path(a.root)); paths = write(inv, Path(a.out))
    print(json.dumps({"present": inv["present"], "windows": inv["windows"].get("count"), "lifecycle_events": (inv.get("lifecycle") or {}).get("events"), "triggers_fired": (inv.get("triggers") or {}).get("fired"), "health": [(x["level"], x["check"]) for x in health(inv) if x["level"] != "ok"], "written": [str(p.relative_to(ROOT)) for p in paths.values()]}, default=str))


if __name__ == "__main__":
    main()
