#!/usr/bin/env python3
"""
vision_free_backfill.py -- remplir gratuitement les fenetres H2 (t0 - 30 min -> t0 + 6 h) depuis Binance Vision.

    --plan      : fichiers necessaires, sans reseau
    --probe     : existence + taille (HEAD) ; les 404 d'une periode encore non publiee sont re-sondes
    --run       : telecharge (cadence, reprises, verification .CHECKSUM, quarantaine si somme fausse),
                  manifeste par fenetre, journal append-only
    --coverage  : rapports de couverture + matrice H2 apres backfill (rescoring avec ce qui est sur disque)

Donnees brutes ou manifestes seulement. Aucun rendement, aucun score alpha, aucun signal, aucun verdict.
Seules des colonnes d'horodatage sont lues dans les archives (voir vision_manifest.TS_COL).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from data_lake.collectors import vision_manifest as VM
from data_lake.collectors.vision_paths import DATASETS, DEFAULT_SET, LOCAL_ROOT, files_for_event, window

ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
P5_MATRIX = ROOT / "reports" / "data_acquisition" / "H2_LAUNCH_COVERAGE_MATRIX.json"
OUT = ROOT / "reports" / "data_acquisition"
PROBE_CACHE = LOCAL_ROOT / "probe_cache.json"
CHECKSUM_TRIES = 3
PUBLICATION_LAG_DAYS = 3          # Vision publie un jour/mois avec quelques jours de retard : un 404 plus recent n'est pas definitif


def load_events(universe: Path = UNIVERSE) -> List[Dict[str, Any]]:
    u = json.loads(universe.read_text())
    return [{"event_id": e["event_id"], "symbol": e["symbol"], "asset": e["asset"], "t0": e["tradable_start_ts"], "t0_source": e.get("launch_ts_source"),
             "onboard_ts": e.get("onboard_ts"), "announcement_ts": e["publication_ts"]} for e in u["events"]]


# ----------------------------------------------------------------------------- reseau (toute exception est rattrapee)
def _head(url: str) -> Dict[str, Any]:
    for attempt in range(3):
        try:
            with urlopen(Request(url, method="HEAD", headers={"User-Agent": "futur-vision-backfill"}), timeout=30) as r:
                return {"status": "ok", "bytes": int(r.headers.get("Content-Length") or 0)}
        except HTTPError as e:
            if e.code == 404:
                return {"status": "404", "bytes": 0}
            time.sleep(1 + attempt)
        except Exception:            # IncompleteRead, RemoteDisconnected, ssl... ne sont PAS des OSError
            time.sleep(1 + attempt)
    return {"status": "error", "bytes": 0}


def _get(url: str, timeout: int = 120) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "futur-vision-backfill"}), timeout=timeout) as r:
        return r.read()


def period_end_utc(period: str) -> datetime:
    """Fin (exclue) de la periode couverte par un fichier : jour YYYY-MM-DD ou mois YYYY-MM."""
    if len(period) == 7:
        y, m = int(period[:4]), int(period[5:7])
        return datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)
    d = datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return d + timedelta(days=1)


def is_not_yet_published(period: str, now: Optional[datetime] = None) -> bool:
    """Un 404 sur une periode qui n'est pas terminee (+ delai de publication) est un 'pas encore', pas un trou."""
    now = now or datetime.now(timezone.utc)
    return now < period_end_utc(period) + timedelta(days=PUBLICATION_LAG_DAYS)


def probe(events: List[Dict[str, Any]], datasets: List[str], workers: int = 16, cache_path: Optional[Path] = None,
          refresh_404: bool = False, now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    """HEAD sur chaque fichier. Re-sonde : les erreurs, et les 404 dont la periode n'etait pas encore publiee."""
    cache_path = Path(cache_path) if cache_path else PROBE_CACHE
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    jobs = {}
    for e in events:
        for f in files_for_event(e["symbol"], e["t0"], datasets):
            c = cache.get(f["url"])
            stale404 = c and c.get("status") == "404" and (refresh_404 or is_not_yet_published(f["period"], now))
            if c is None or c.get("status") == "error" or stale404:
                jobs[f["url"]] = f
    if jobs:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for url, res in zip(jobs.keys(), ex.map(_head, jobs.keys())):
                prev = cache.get(url, {})
                cache[url] = {**res, "probed_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "reprobes": (prev.get("reprobes", 0) + 1) if prev else 0}
        VM._write_atomic(cache_path, json.dumps(cache, indent=0, sort_keys=True))
    return cache


def plan(events: List[Dict[str, Any]], datasets: List[str]) -> Dict[str, Any]:
    per = {e["event_id"]: files_for_event(e["symbol"], e["t0"], datasets) for e in events}
    n = sum(len(v) for v in per.values()); by = Counter(f["dataset"] for v in per.values() for f in v)
    return {"events": len(events), "files": n, "by_dataset": dict(by),
            "days_per_event": dict(Counter(len({f['period'] for f in v if DATASETS[f['dataset']]['kind'] != 'monthly'}) for v in per.values())), "per_event": per}


# ----------------------------------------------------------------------------- telechargement d'un fichier
def fetch_one(f: Dict[str, str], probe_info: Optional[Dict[str, Any]], verify_checksum: bool = True, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Telecharge si absent, verifie le sha256 contre le .CHECKSUM de Vision (avec reprises), inspecte les horodatages.
    Somme fausse -> le fichier est mis en quarantaine (.bad) pour qu'une passe suivante le retelecharge.
    Archive illisible -> statut 'error' (un fichier qu'on ne peut pas ouvrir n'est pas une donnee)."""
    p = Path(f["local"]); rec = {**f, "fetched_at_local": None, "bytes": None, "sha256": None, "checksum_verified": None, "status": None}
    if probe_info and probe_info.get("status") == "404":
        rec["status"] = "not_yet_published" if is_not_yet_published(f["period"], now) else "404"
        return rec
    if not p.exists():
        for attempt in range(4):
            try:
                data = _get(f["url"]); p.parent.mkdir(parents=True, exist_ok=True); tmp = p.with_suffix(p.suffix + ".part"); tmp.write_bytes(data); os.replace(tmp, p)
                rec["fetched_at_local"] = datetime.now(timezone.utc).isoformat(timespec="seconds"); break
            except HTTPError as e:
                if e.code == 404:
                    rec["status"] = "not_yet_published" if is_not_yet_published(f["period"], now) else "404"
                    return rec
                time.sleep(2 * (attempt + 1))
            except Exception as e:          # IncompleteRead / RemoteDisconnected / ssl / OSError : jamais fatal pour la passe
                rec["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:60]); time.sleep(2 * (attempt + 1))
        else:
            rec["status"] = "error"; return rec
    rec["bytes"] = p.stat().st_size; rec["sha256"] = VM.sha256_file(p); rec["status"] = "ok"
    if verify_checksum:
        ref = None
        for attempt in range(CHECKSUM_TRIES):
            try:
                ref = _get(f["checksum_url"]).decode("utf-8", "replace").split()[0].lower(); break
            except HTTPError as e:
                if e.code == 404:
                    break                    # pas de .CHECKSUM publie pour ce fichier
                time.sleep(1 + attempt)
            except Exception:
                time.sleep(1 + attempt)
        if ref:
            rec["checksum_verified"] = (ref == rec["sha256"])
            if not rec["checksum_verified"]:
                bad = p.with_suffix(p.suffix + ".bad")
                try:
                    os.replace(p, bad)       # quarantaine : la passe suivante retelecharge
                except OSError:
                    pass
                rec.update({"status": "error", "error": "sha256 mismatch vs Vision .CHECKSUM", "quarantined_as": bad.name, "expected_sha256": ref})
                return rec
    info = VM.inspect_zip(p, f["dataset"])
    rec.update({k: v for k, v in info.items() if k in ("rows", "first_ts", "last_ts", "error")})
    if info.get("error"):
        rec["status"] = "error"              # archive illisible : pas exploitable, ne doit pas compter comme couverte
    return rec


# ----------------------------------------------------------------------------- passe complete
def run(events: List[Dict[str, Any]], datasets: List[str], workers: int = 8, max_gb: float = 20.0, run_id: Optional[str] = None,
        dry: bool = False, refresh_404: bool = False) -> Dict[str, Any]:
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cache = probe(events, datasets, workers=16, refresh_404=refresh_404)
    order = {"P0": 0, "P1": 1, "P2": 2}; budget = max_gb * 1e9
    lock = threading.Lock(); spent = [0.0]                       # reserve AVANT le telechargement, sous verrou
    summary = {"run_id": run_id, "events": 0, "files_ok": 0, "files_404": 0, "files_not_yet_published": 0, "files_error": 0,
               "files_skipped_cap": 0, "checksum_verified": 0, "checksum_unverified": 0, "bytes_on_disk": 0, "manifests": []}
    for e in sorted(events, key=lambda x: x["t0"]):
        files = sorted(files_for_event(e["symbol"], e["t0"], datasets), key=lambda f: order[f["priority"]])

        def one(f):
            info = cache.get(f["url"], {}); sz = info.get("bytes") or 0
            exists = Path(f["local"]).exists()
            if dry:
                return {**f, "status": "skipped", "reason": "dry run", "bytes": None, "probed_bytes": sz}
            if not exists:
                with lock:                                        # reservation : le plafond ne peut plus etre depasse par les fils en vol
                    if spent[0] + max(sz, 0) > budget:
                        return {**f, "status": "skipped", "reason": "max_gb cap", "bytes": None, "probed_bytes": sz}
                    spent[0] += max(sz, 0)
            r = fetch_one(f, info)
            if not exists and r.get("fetched_at_local") and r.get("bytes"):
                with lock:
                    spent[0] += (r["bytes"] - max(sz, 0))          # correction : taille reelle contre taille annoncee
            VM.log({"kind": "fetch", "run_id": run_id, "event_id": e["event_id"], "symbol": e["symbol"], "dataset": f["dataset"], "period": f["period"],
                    "status": r["status"], "bytes": r.get("bytes"), "sha256": r.get("sha256"), "checksum_verified": r.get("checksum_verified"),
                    "rows": r.get("rows"), "quarantined_as": r.get("quarantined_as"), "error": r.get("error")})
            return r

        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(one, files))
        man = VM.build(e, results, run_id)
        if not dry:
            VM.write(man)                                          # un dry run n'ecrase jamais le manifeste d'une vraie passe
        c = Counter(r["status"] for r in results)
        summary["events"] += 1; summary["files_ok"] += c["ok"]; summary["files_404"] += c["404"]; summary["files_not_yet_published"] += c["not_yet_published"]
        summary["files_error"] += c["error"]; summary["files_skipped_cap"] += sum(1 for r in results if r.get("reason") == "max_gb cap")
        summary["checksum_verified"] += man["coverage"]["checksum_verified"]; summary["checksum_unverified"] += man["coverage"]["checksum_unverified"]
        summary["bytes_on_disk"] += man["bytes_on_disk"]
        summary["manifests"].append({"event_id": e["event_id"], "symbol": e["symbol"], "core_complete": man["coverage"]["core_complete"], "score": man["coverage"]["score"], "bytes_on_disk": man["bytes_on_disk"]})
        print(f"  {e['symbol']:14s} {e['t0'][:16]} ok={c['ok']:2d} 404={c['404']:2d} pending={c['not_yet_published']:2d} err={c['error']:2d} core={'Y' if man['coverage']['core_complete'] else 'n'} {man['bytes_on_disk']/1e6:7.1f} MB", flush=True)
    return summary


# ----------------------------------------------------------------------------- couverture et matrice apres backfill
def downloaded_index(mans: Dict[str, Dict[str, Any]]) -> Dict[str, set]:
    """event_id -> datasets complets sur disque (tous les jours de la fenetre en statut ok)."""
    return {eid: {ds for ds, ok in m["coverage"]["complete"].items() if ok} for eid, m in mans.items()}


def coverage_reports(events: List[Dict[str, Any]], out: Path = OUT) -> Dict[str, Any]:
    from data_lake.indices import h2_launch_coverage_matrix as M
    out = Path(out)
    mans = VM.load_all(); ids = {e["event_id"] for e in events}
    mans = {k: v for k, v in mans.items() if k in ids}
    idx = downloaded_index(mans)
    p5 = json.loads(P5_MATRIX.read_text()) if P5_MATRIX.exists() else {"rows": [], "summary": {}, "scoring": None}
    ev = [e for e in M.load_events() if e["event_id"] in ids]          # meme population que `events` : pas de melange
    vis = json.loads((OUT / "_vision_probe_cache.json").read_text()) if (OUT / "_vision_probe_cache.json").exists() else {}
    rows = M.build_rows(ev, vis, M.other_venue_precedence(ev), downloaded=idx)
    with open(out / "H2_AFTER_VISION_COVERAGE_MATRIX.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=M.COLUMNS); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in M.COLUMNS})
    s = M.summarize(rows)
    n = len(events); covered = sum(1 for m in mans.values() if m["coverage"]["core_complete"])
    by_ds, exp, ver, unver = Counter(), Counter(), Counter(), Counter()
    for m in mans.values():
        for ds, c in m["coverage"]["by_dataset"].items():
            by_ds[ds] += c["ok"]; exp[ds] += c["expected"]; ver[ds] += c.get("checksum_verified", 0); unver[ds] += c.get("checksum_unverified", 0)
    pending = Counter((f["dataset"], f["period"]) for m in mans.values() for f in m["files"] if f.get("status") == "not_yet_published")
    provider = [r for r in rows if not (r["first_orderbook_ts_present"] and r["first_index_ts_present"])]
    cov = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "events": n, "manifests": len(mans), "events_core_covered_free": covered,
           "core_definition": "markPriceKlines + aggTrades + (indexPriceKlines or premiumIndexKlines), every day of the window",
           "files_ok_by_dataset": dict(by_ds), "files_expected_by_dataset": dict(exp), "checksum_verified_by_dataset": dict(ver), "checksum_unverified_by_dataset": dict(unver),
           "not_yet_published": {f"{k[0]}@{k[1]}": v for k, v in pending.items()}, "bytes_on_disk": sum(m["bytes_on_disk"] for m in mans.values()),
           "answers": {"1_events_covered_by_free_data": covered, "2_become_clean_ge_90": s["buckets_now"].get("clean", 0),
                       "3_remain_near_usable_70_89": s["buckets_now"].get("near_usable", 0), "3b_partial_40_69": s["buckets_now"].get("partial", 0), "3c_unusable_lt_40": s["buckets_now"].get("unusable", 0),
                       "4_require_announcement_body": sum(1 for r in rows if not r["body_trading_time_present"]), "5_require_account_fees": sum(1 for r in rows if not r["actual_fee_present"]),
                       "6_require_cross_venue_precedence": sum(1 for r in rows if not r["other_venue_existed_before"]), "7_still_need_paid_provider_windows": len(provider),
                       "7_provider_symbols": [r["symbol"] for r in provider]},
           "score_distribution_after_vision": dict(sorted(Counter(r["coverage_score"] for r in rows).items())),
           "projected_after_p7_body": dict(sorted(Counter(r["coverage_score"] + 15 * (not r["body_trading_time_present"]) for r in rows).items())),
           "projected_after_p7_p8": dict(sorted(Counter(r["coverage_score"] + 15 * (not r["body_trading_time_present"]) + 3 * (not r["actual_fee_present"]) for r in rows).items())),
           "p5_summary_for_reference": p5.get("summary", {}), "no_alpha_test": True, "no_return_computed": True}
    VM._write_atomic(out / "VISION_FREE_BACKFILL_COVERAGE.json", json.dumps(cov, indent=1, ensure_ascii=False) + "\n")
    VM._write_atomic(out / "H2_AFTER_VISION_COVERAGE_MATRIX.json", json.dumps({"generated_at_utc": cov["generated_at_utc"], "scoring": p5.get("scoring"), "summary": s, "rows": rows, "no_alpha_test": True}, indent=1, ensure_ascii=False) + "\n")
    a = cov["answers"]; nver, nunver = sum(ver.values()), sum(unver.values())
    md = [f"# VISION FREE BACKFILL — coverage ({cov['generated_at_utc'][:19]} UTC)", "",
          f"{n} H2 windows (t0 − 30 min → t0 + 6 h), {len(mans)} manifests, {cov['bytes_on_disk']/1e9:.2f} GB on disk under `data/vision_backfill/` (gitignored). "
          f"sha256 recomputed for every file; **{nver} of {nver + nunver} verified against Vision's `.CHECKSUM`** (the rest: no `.CHECKSUM` published for that file). "
          "No return computed, no price column read, no signal, no verdict.", "",
          "| dataset | files ok / expected | checksum verified |", "|---|---|---|"] + \
         [f"| {ds} | {by_ds[ds]} / {exp[ds]} | {ver[ds]} |" for ds in sorted(exp)] + \
         ["", f"Core coverage definition: {cov['core_definition']} (the index is recoverable from mark and premium by identity index = mark − premium; that identity is documented, not computed here).", ""]
    if pending:
        md += [f"Files not yet published by Vision (period not finished + {PUBLICATION_LAG_DAYS} d lag; re-probed on every run, not counted as holes): {cov['not_yet_published']}", ""]
    md += ["## The seven answers", "",
           f"1. Events covered by free data (core complete for every day of the window): **{a['1_events_covered_by_free_data']} / {n}**",
           f"2. Events that become clean (≥ 90) with Vision alone: **{a['2_become_clean_ge_90']}** — the announcement body (15 pts) and the actual fee (3 pts) are not on Vision; projected after P7 + P8: **{sum(v for k, v in cov['projected_after_p7_p8'].items() if k >= 90)}** clean",
           f"3. Events near-usable (70–89) after Vision: **{a['3_remain_near_usable_70_89']}**; partial (40–69): {a['3b_partial_40_69']}; unusable (< 40): {a['3c_unusable_lt_40']}",
           f"4. Events that still require the announcement body: **{a['4_require_announcement_body']}** (P7)",
           f"5. Events that still require read-only account fees: **{a['5_require_account_fees']}** (P8)",
           f"6. Events that still require cross-venue precedence: **{a['6_require_cross_venue_precedence']}** (P9)",
           f"7. Events that still need a paid provider window (no Vision depth, or no index reference, on the launch day): **{a['7_still_need_paid_provider_windows']}** — {', '.join(a['7_provider_symbols']) or 'none'}", "",
           f"Score distribution after Vision: {cov['score_distribution_after_vision']}", "", f"Projected after P7 (body): {cov['projected_after_p7_body']}", "", f"Projected after P7 + P8 (body + fee): {cov['projected_after_p7_p8']}"]
    VM._write_atomic(out / "VISION_FREE_BACKFILL_COVERAGE.md", "\n".join(md) + "\n")
    miss = ["# H2 — missing data after the Vision backfill", "", "What Vision could not give, per event (only events with something missing). `pending` = period not yet published, re-probed on every run.", "",
            "| symbol | launch | score | missing P0 | missing P1 | Vision 404 | pending |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        if r["missing_p0_fields"] or r["missing_p1_fields"]:
            fl = mans.get(r["event_id"], {}).get("files", [])
            m404 = ";".join(f"{f['dataset']}@{f['period']}" for f in fl if f.get("status") == "404")
            mpend = ";".join(f"{f['dataset']}@{f['period']}" for f in fl if f.get("status") == "not_yet_published")
            miss.append(f"| {r['symbol']} | {r['launch_ts'][:16]} | {r['coverage_score']} | {r['missing_p0_fields'] or '-'} | {r['missing_p1_fields'] or '-'} | {m404 or '-'} | {mpend or '-'} |")
    VM._write_atomic(out / "H2_AFTER_VISION_MISSING_DATA.md", "\n".join(miss) + "\n")
    return cov


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true"); ap.add_argument("--probe", action="store_true"); ap.add_argument("--run", action="store_true"); ap.add_argument("--coverage", action="store_true")
    ap.add_argument("--datasets", default=",".join(DEFAULT_SET)); ap.add_argument("--workers", type=int, default=8); ap.add_argument("--max-gb", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--limit", type=int); ap.add_argument("--refresh-404", action="store_true")
    a = ap.parse_args(); ds = [d for d in a.datasets.split(",") if d]; ev = load_events()
    if a.limit:
        ev = ev[: a.limit]
    if a.plan:
        p = plan(ev, ds); print(json.dumps({k: v for k, v in p.items() if k != "per_event"}, indent=1))
    if a.probe:
        c = probe(ev, ds, refresh_404=a.refresh_404); st = Counter(v["status"] for v in c.values()); tot = sum(v.get("bytes", 0) for v in c.values() if v["status"] == "ok")
        print(json.dumps({"probed": len(c), "status": dict(st), "total_gb_available": round(tot / 1e9, 2)}, indent=1))
    if a.run:
        s = run(ev, ds, workers=a.workers, max_gb=a.max_gb, dry=a.dry_run, refresh_404=a.refresh_404); print(json.dumps({k: v for k, v in s.items() if k != "manifests"}, indent=1))
    if a.coverage:
        c = coverage_reports(ev); print(json.dumps(c["answers"], indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
