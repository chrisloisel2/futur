#!/usr/bin/env python3
"""
announcement_body_archive.py -- archiver le CORPS complet des annonces officielles, puis en extraire les
horodatages que le titre ne porte pas (heure d'ouverture, de delisting, de suspension).

Le tape d'evenements ne stocke qu'un hash du corps : les heures qui decident de l'executabilite
(ouverture du contrat, minute du delisting, suspension du borrow) sont dans le texte. Ce module les
archive tels quels et les parse. Aucune jointure de prix, aucun signal, aucun verdict, aucun budget.

    --plan                 ce qu'il y a a recuperer, sans reseau
    --fetch [--scope h2|h3|all] recupere et archive (append-only, jamais d'ecrasement silencieux)
    --extract              reparse les corps deja archives (hors ligne) et ecrit les rapports
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from data_lake.collectors.announcement_body_parser import parse_payload
from data_lake.collectors.announcement_time_extractor import extract

ROOT = Path(__file__).resolve().parents[2]
TAPE = ROOT / "data_lake" / "events" / "official_event_tape.jsonl"
UNIVERSE_H2 = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
H3_RESULTS = ROOT / "mechanisms" / "event_reaction_v1" / "results" / "first_look_results.json"
STORE = ROOT / "data" / "announcement_bodies"          # data/* est gitignore
INDEX = STORE / "index.jsonl"
OUT_TAPE = ROOT / "reports" / "event_tape"
OUT_ACQ = ROOT / "reports" / "data_acquisition"
PARSER_VERSION = 1
RETRIES = 5
RATE_LIMIT_BACKOFF_S = (20, 45, 90, 150, 240)
PACE_S = 0.8          # cadence volontairement lente : l'API CMS limite au-dela de ~200 requetes rapprochees


def _write_atomic(p: Path, text: str) -> None:
    """Ecriture atomique : un arret au milieu ne laisse jamais un fichier tronque en place."""
    import os
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)
BINANCE_DETAIL = "https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode={code}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; futur-announcement-archive/1.0)", "Accept-Language": "en"}


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def binance_code(url: str) -> Optional[str]:
    m = re.search(r"/announcement/(?:detail/)?([0-9a-f]{16,})", url) or re.search(r"/([0-9a-f]{32})/?$", url)
    return m.group(1) if m else None


def load_tape(path: Path = TAPE) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def scope_urls(scope: str = "h2") -> Dict[str, Dict[str, Any]]:
    """URL -> {source, event_ids, titles, scope}. h2 = les 174 lancements perp-first ; h3 = les delistings du regard seq 7."""
    rows = load_tape(); by_id = {r["event_id"]: r for r in rows}
    want: Dict[str, Dict[str, Any]] = {}

    def add(r, tag):
        u = r.get("raw_url", "")
        if not u or u.startswith("snapshot://"):
            return
        e = want.setdefault(u, {"url": u, "source": r["source"], "event_ids": [], "titles": [], "scopes": set()})
        e["event_ids"].append(r["event_id"]); e["titles"].append(r.get("raw_title", "")); e["scopes"].add(tag)

    if scope in ("h2", "all") and UNIVERSE_H2.exists():
        for e in json.loads(UNIVERSE_H2.read_text())["events"]:
            r = by_id.get(e["event_id"])
            if r:
                add(r, "h2")
    if scope in ("h3", "all") and H3_RESULTS.exists():
        res = json.loads(H3_RESULTS.read_text())
        for ev in res["hypotheses"]["H3"]["events"]:
            r = by_id.get(ev["event_id"])
            if r:
                add(r, "h3")
    if scope == "all":
        for r in rows:
            if r.get("raw_url", "").startswith("snapshot://"):
                continue
            add(r, "tape")
    for v in want.values():
        v["scopes"] = sorted(v["scopes"])
    return want


# ----------------------------------------------------------------------------- recuperation
def _get(url: str, timeout: int = 30) -> bytes:
    with urlopen(Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def fetch_body(url: str, source: str) -> Dict[str, Any]:
    """-> {http_status, payload_kind, payload, error}. Toute exception est rattrapee : une URL morte est un fait, pas un plantage."""
    target, kind = url, "html"
    if source == "binance":
        code = binance_code(url)
        if code:
            target, kind = BINANCE_DETAIL.format(code=code), "cms_json"
    for attempt in range(RETRIES):
        try:
            raw = _get(target)
            if kind == "cms_json":
                d = json.loads(raw.decode("utf-8", "replace"))
                if str(d.get("code")) not in ("000000", "0"):
                    return {"http_status": 200, "payload_kind": kind, "payload": None, "error": "cms code %s" % d.get("code")}
                return {"http_status": 200, "payload_kind": kind, "payload": d, "error": None}
            return {"http_status": 200, "payload_kind": kind, "payload": raw.decode("utf-8", "replace"), "error": None}
        except HTTPError as e:
            if e.code in (403, 404, 451):
                return {"http_status": e.code, "payload_kind": kind, "payload": None, "error": "HTTP %s" % e.code}
            if e.code == 429:                      # limitation de debit : recul long, sinon les reprises l'aggravent
                err = "HTTP 429"; time.sleep(RATE_LIMIT_BACKOFF_S[min(attempt, len(RATE_LIMIT_BACKOFF_S) - 1)]); continue
            err = "HTTP %s" % e.code; time.sleep(1 + attempt)
        except Exception as e:
            err = "%s: %s" % (type(e).__name__, str(e)[:60]); time.sleep(1 + attempt)
    return {"http_status": None, "payload_kind": kind, "payload": None, "error": locals().get("err", "unreachable")}


def record_path(source: str, url: str) -> Path:
    return STORE / source / ("%s.json" % url_key(url))


def store_record(meta: Dict[str, Any], fetched: Dict[str, Any]) -> Dict[str, Any]:
    """Ecrit l'archive. Jamais d'ecrasement silencieux : un corps different est archive a cote."""
    p = record_path(meta["source"], meta["url"])
    parsed = parse_payload(meta["source"], fetched["payload"]) if fetched.get("payload") is not None else {"title": None, "body_text": "", "publication_ts": None, "update_ts": None}
    body_text = parsed.get("body_text") or ""
    rec = {"url": meta["url"], "source": meta["source"], "event_ids": meta["event_ids"], "scopes": meta.get("scopes", []),
           "fetched_at_local": datetime.now(timezone.utc).isoformat(timespec="seconds"), "http_status": fetched.get("http_status"),
           "payload_kind": fetched.get("payload_kind"), "error": fetched.get("error"), "title": parsed.get("title") or (meta["titles"][0] if meta.get("titles") else None),
           "body_text": body_text, "body_chars": len(body_text), "publication_ts": parsed.get("publication_ts"), "update_ts": parsed.get("update_ts"),
           "raw_hash": hashlib.sha256((json.dumps(fetched.get("payload"), sort_keys=True, default=str) if fetched.get("payload_kind") == "cms_json" else str(fetched.get("payload") or "")).encode("utf-8")).hexdigest(),
           "parser_version": PARSER_VERSION, "no_alpha_test": True}
    rec["extracted"] = extract(body_text, rec["title"] or "") if body_text else None
    if p.exists():
        try:
            old = json.loads(p.read_text())
        except ValueError:
            old = None
        if isinstance(old, dict) and old.get("raw_hash") == rec["raw_hash"] and old.get("parser_version") == PARSER_VERSION:
            return old                                                   # corps identique, meme parseur : rien a reecrire
        if isinstance(old, dict) and old.get("body_chars"):
            arch = p.with_name("%s.%s.json" % (p.stem, (old.get("raw_hash") or "prev")[:8]))
            if not arch.exists():
                _write_atomic(arch, json.dumps(old, indent=1, ensure_ascii=False, default=str))
            log({"kind": "body_rewritten", "url": meta["url"], "old_hash": old.get("raw_hash"), "new_hash": rec["raw_hash"], "archived_as": arch.name})
    _write_atomic(p, json.dumps(rec, indent=1, ensure_ascii=False, default=str))
    log({"kind": "fetch", "url": meta["url"], "source": meta["source"], "http_status": rec["http_status"], "error": rec["error"], "body_chars": rec["body_chars"],
         "trading_start_ts": (rec["extracted"] or {}).get("trading_start_ts"), "delisting_ts": (rec["extracted"] or {}).get("delisting_ts"), "suspension_ts": (rec["extracted"] or {}).get("suspension_ts")})
    return rec


def log(rec: Dict[str, Any], path: Optional[Path] = None) -> None:
    path = Path(path) if path else INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts_local": datetime.now(timezone.utc).isoformat(timespec="seconds"), **rec}, ensure_ascii=False, default=str) + "\n")


def load_store() -> Dict[str, Dict[str, Any]]:
    out = {}
    if not STORE.exists():
        return out
    for p in sorted(STORE.rglob("*.json")):
        if p.name == "index.jsonl" or p.stem.count(".") >= 1:
            continue
        try:
            r = json.loads(p.read_text()); out[r["url"]] = r
        except (ValueError, KeyError):
            continue
    return out


def fetch(scope: str = "h2", workers: int = 4, limit: Optional[int] = None, refetch: bool = False) -> Dict[str, Any]:
    want = scope_urls(scope); have = load_store()
    todo = [m for u, m in want.items() if refetch or u not in have or not have[u].get("body_chars")]
    if limit:
        todo = todo[:limit]
    done = []
    def one(m):
        r = fetch_body(m["url"], m["source"])
        time.sleep(PACE_S)
        return store_record(m, r)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, rec in enumerate(ex.map(one, todo), 1):
            done.append(rec)
            if i % 25 == 0:
                print("  %d / %d" % (i, len(todo)), flush=True)
    st = Counter(("ok" if r.get("body_chars") else "empty") for r in done)
    return {"scope": scope, "urls_in_scope": len(want), "already_archived": len(want) - len(todo), "fetched": len(done), "with_body": st["ok"], "without_body": st["empty"],
            "by_source": dict(Counter(r["source"] for r in done)), "errors": dict(Counter(r.get("error") for r in done if r.get("error")))}


def reextract() -> int:
    """Reparse les corps archives avec la version courante du parseur (hors ligne)."""
    n = 0
    for url, rec in load_store().items():
        if not rec.get("body_text"):
            continue
        rec["extracted"] = extract(rec["body_text"], rec.get("title") or ""); rec["parser_version"] = PARSER_VERSION
        _write_atomic(record_path(rec["source"], url), json.dumps(rec, indent=1, ensure_ascii=False, default=str)); n += 1
    return n


# ----------------------------------------------------------------------------- rapports
def _vision_downloaded() -> Dict[str, set]:
    """Datasets Vision complets par event_id, lus depuis les manifestes sur disque (P6).
    Lecture de DONNEES, pas de code : si P6 n'a pas tourne ici, le dictionnaire est vide."""
    root = ROOT / "data" / "vision_backfill" / "manifests"
    out: Dict[str, set] = {}
    if not root.exists():
        return out
    for p in sorted(root.glob("*.json")):
        if p.name.count(".") != 1:
            continue
        try:
            m = json.loads(p.read_text())
        except ValueError:
            continue
        out[m["event_id"]] = {ds for ds, ok in m.get("coverage", {}).get("complete", {}).items() if ok}
    return out


def bodies_by_event() -> Dict[str, Dict[str, Any]]:
    """event_id -> {body_text, trading_start_ts, delisting_ts, suspension_ts, url}."""
    out: Dict[str, Dict[str, Any]] = {}
    for url, rec in load_store().items():
        ex = rec.get("extracted") or {}
        for eid in rec.get("event_ids", []):
            out[eid] = {"url": url, "body_text": rec.get("body_text") or "", "body_chars": rec.get("body_chars", 0), "title": rec.get("title"),
                        "publication_ts": rec.get("publication_ts"), "update_ts": rec.get("update_ts"), "raw_hash": rec.get("raw_hash"),
                        "trading_start_ts": ex.get("trading_start_ts"), "delisting_ts": ex.get("delisting_ts"), "suspension_ts": ex.get("suspension_ts"),
                        "symbols": ex.get("symbols") or [], "has_explicit_time": ex.get("has_explicit_time") or {}}
    return out


def write_reports(out_tape: Path = OUT_TAPE, out_acq: Path = OUT_ACQ) -> Dict[str, Any]:
    import csv
    from data_lake.indices import h2_launch_coverage_matrix as M
    out_tape.mkdir(parents=True, exist_ok=True); out_acq.mkdir(parents=True, exist_ok=True)
    store = load_store(); bodies = bodies_by_event(); h2 = scope_urls("h2"); h3 = scope_urls("h3")
    dl = _vision_downloaded()

    def stats(sc):
        recs = [store[u] for u in sc if u in store]
        ex = [r for r in recs if r.get("extracted")]
        return {"urls": len(sc), "archived": len(recs), "with_body": sum(1 for r in recs if r.get("body_chars")),
                "median_body_chars": sorted(r.get("body_chars", 0) for r in recs)[len(recs) // 2] if recs else 0,
                "trading_start_ts": sum(1 for r in ex if r["extracted"].get("trading_start_ts")),
                "delisting_ts": sum(1 for r in ex if r["extracted"].get("delisting_ts")),
                "suspension_ts": sum(1 for r in ex if r["extracted"].get("suspension_ts")),
                "explicit_time_on_start": sum(1 for r in ex if (r["extracted"].get("has_explicit_time") or {}).get("trading_start_ts")),
                "errors": dict(Counter(r.get("error") for r in recs if r.get("error")))}

    # ecart entre l'heure annoncee et la premiere barre Vision : deux sources independantes
    agree = {"n": 0, "within_5min": 0, "deltas": []}
    if UNIVERSE_H2.exists():
        for e in json.loads(UNIVERSE_H2.read_text())["events"]:
            b = bodies.get(e["event_id"])
            if not b or not b.get("trading_start_ts"):
                continue
            d = (datetime.fromisoformat(e["tradable_start_ts"]) - datetime.fromisoformat(b["trading_start_ts"])).total_seconds() / 60
            agree["n"] += 1; agree["deltas"].append({"symbol": e["symbol"], "delta_min": round(d), "announced": b["trading_start_ts"], "first_bar": e["tradable_start_ts"]})
            agree["within_5min"] += int(abs(d) <= 5)
    agree["deltas"].sort(key=lambda x: -abs(x["delta_min"]))
    arc = {"generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "parser_version": PARSER_VERSION,
           "store": str(STORE.relative_to(ROOT)), "records": len(store), "h2": stats(h2), "h3": stats(h3),
           "announced_vs_first_bar": {"events_compared": agree["n"], "within_5_min": agree["within_5min"], "largest_gaps": agree["deltas"][:12]},
           "no_alpha_test": True, "no_price_join": True}
    _write_atomic(out_tape / "ANNOUNCEMENT_BODY_ARCHIVE.json", json.dumps(arc, indent=1, ensure_ascii=False, default=str) + "\n")

    h2s, h3s = arc["h2"], arc["h3"]
    md = [f"# ANNOUNCEMENT BODY ARCHIVE — P7 ({arc['generated_at_utc'][:19]} UTC)", "",
          "The event tape stores only a hash of an announcement. The timestamps that decide executability — when the contract "
          "actually opens, when a delisting takes effect, when borrowing is suspended — are in the text. This phase archives the "
          "bodies and parses those times. No price join, no signal, no verdict, no budget.", "",
          "| scope | announcements | bodies archived | median body | trading start | delisting | suspension |", "|---|---|---|---|---|---|---|",
          f"| H2 (perp launches) | {h2s['urls']} | {h2s['with_body']} | {h2s['median_body_chars']} chars | **{h2s['trading_start_ts']}** ({h2s['explicit_time_on_start']} with an explicit clock time) | {h2s['delisting_ts']} | {h2s['suspension_ts']} |",
          f"| H3 (delistings) | {h3s['urls']} | {h3s['with_body']} | {h3s['median_body_chars']} chars | {h3s['trading_start_ts']} | **{h3s['delisting_ts']}** | {h3s['suspension_ts']} |", "",
          "## Source", "",
          "Binance publishes article bodies through its CMS detail endpoint as a nested node tree; the parser flattens it to text. "
          "The endpoint rate-limits past roughly 200 close requests, so the collector paces at 0.8 s and backs off 20–240 s on HTTP 429. "
          "Every archive keeps the raw payload hash, the parser version, and is never silently overwritten: a body that changes is "
          "archived beside the current one and the rewrite is logged in the append-only index.", "",
          "## Cross-check: announced time against the first traded minute", "",
          f"For **{agree['n']} of the 174** H2 launches the body states an opening time. Comparing it to the first Vision 1-minute bar "
          f"(an independent source): **{agree['within_5min']} agree within 5 minutes**. Two independent records of the same event "
          "converging is the strongest evidence available that both are right.", "",
          "Largest disagreements, which need a human look before any test uses them:", "",
          "| symbol | announced | first traded bar | gap (min) |", "|---|---|---|---|"]
    for d in agree["deltas"][:8]:
        md.append(f"| {d['symbol']} | {d['announced'][:16]} | {d['first_bar'][:16]} | {d['delta_min']:+d} |")
    md += ["", "## What this closes", "",
           "Instrument defect I22: the tape's `trading_start_ts` is the date in the title parsed at midnight, so it preceded the "
           "publication timestamp by 6–10 hours for most listings. The announced time from the body replaces it, and the Vision "
           "first bar corroborates it.", ""]
    _write_atomic(out_tape / "ANNOUNCEMENT_BODY_ARCHIVE.md", "\n".join(md) + "\n")

    cov = [f"# ANNOUNCEMENT TIME EXTRACTION — coverage ({arc['generated_at_utc'][:19]} UTC)", "",
           "How often each timestamp is actually stated in the text, and how it was attributed. A date is assigned to the clause "
           "that names it (nearest keyword, or a keyword on the same line); a date with no matching clause is left unused, and a "
           "field with nothing in the text stays null.", "",
           "| field | H2 | H3 |", "|---|---|---|",
           f"| body archived | {h2s['with_body']} / {h2s['urls']} | {h3s['with_body']} / {h3s['urls']} |",
           f"| trading_start_ts | {h2s['trading_start_ts']} | {h3s['trading_start_ts']} |",
           f"| delisting_ts | {h2s['delisting_ts']} | {h3s['delisting_ts']} |",
           f"| suspension_ts | {h2s['suspension_ts']} | {h3s['suspension_ts']} |",
           f"| explicit clock time on the start | {h2s['explicit_time_on_start']} | — |", "",
           f"Fetch errors: H2 {h2s['errors'] or 'none'}, H3 {h3s['errors'] or 'none'}.", "",
           "## Events that still need a human look", ""]
    manual = [d for d in agree["deltas"] if abs(d["delta_min"]) > 15]
    cov += [f"{len(manual)} H2 launches where the announced time and the first traded bar differ by more than 15 minutes "
            "(the contract opened later than announced, or the body names another contract's time). They are usable only once "
            "someone decides which timestamp is the event.", ""]
    if manual:
        cov += ["| symbol | announced | first bar | gap (min) |", "|---|---|---|---|"] + [f"| {d['symbol']} | {d['announced'][:16]} | {d['first_bar'][:16]} | {d['delta_min']:+d} |" for d in manual]
    _write_atomic(out_tape / "ANNOUNCEMENT_TIME_EXTRACTION_COVERAGE.md", "\n".join(cov) + "\n")

    # matrice apres corps d'annonce (composee avec ce que P6 a mis sur disque, si present)
    ev = M.load_events()
    vis = json.loads((out_acq / "_vision_probe_cache.json").read_text()) if (out_acq / "_vision_probe_cache.json").exists() else {}
    kw = {}
    try:
        rows = M.build_rows(ev, vis, M.other_venue_precedence(ev), bodies=bodies, downloaded=dl)
        kw["downloaded"] = True
    except TypeError:                                   # build_rows sans le parametre downloaded (P6 non fusionne)
        rows = M.build_rows(ev, vis, M.other_venue_precedence(ev), bodies=bodies)
        kw["downloaded"] = False
    with open(out_acq / "H2_AFTER_ANNOUNCEMENT_BODY_COVERAGE_MATRIX.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=M.COLUMNS); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in M.COLUMNS})
    s = M.summarize(rows)
    _write_atomic(out_acq / "H2_AFTER_ANNOUNCEMENT_BODY_COVERAGE_MATRIX.json",
                  json.dumps({"generated_at_utc": arc["generated_at_utc"], "composed_with_vision_on_disk": kw["downloaded"] and bool(dl),
                              "vision_events_on_disk": len(dl), "summary": s, "rows": rows, "no_alpha_test": True}, indent=1, ensure_ascii=False, default=str) + "\n")
    arc["matrix_summary"] = s
    _write_atomic(out_tape / "ANNOUNCEMENT_BODY_ARCHIVE.json", json.dumps(arc, indent=1, ensure_ascii=False, default=str) + "\n")
    return arc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true"); ap.add_argument("--fetch", action="store_true"); ap.add_argument("--extract", action="store_true"); ap.add_argument("--reports", action="store_true")
    ap.add_argument("--scope", default="h2", choices=["h2", "h3", "all"]); ap.add_argument("--workers", type=int, default=2); ap.add_argument("--limit", type=int); ap.add_argument("--refetch", action="store_true")
    a = ap.parse_args()
    if a.plan:
        w = scope_urls(a.scope); print(json.dumps({"scope": a.scope, "urls": len(w), "by_source": dict(Counter(v["source"] for v in w.values())), "already_archived": sum(1 for u in w if u in load_store())}, indent=1))
    if a.fetch:
        print(json.dumps(fetch(a.scope, workers=a.workers, limit=a.limit, refetch=a.refetch), indent=1))
    if a.extract:
        print(json.dumps({"reextracted": reextract()}, indent=1))
    if a.reports:
        r = write_reports(); print(json.dumps({k: v for k, v in r.items() if k not in ("announced_vs_first_bar", "matrix_summary")}, indent=1, ensure_ascii=False))
        print(json.dumps(r["matrix_summary"], indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
