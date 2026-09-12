#!/usr/bin/env python3
"""
cross_venue_lifecycle.py -- ou ce marche existait-il avant Binance ?

Collecte le cycle de vie des instruments sur OKX, Bybit, Gate, MEXC et KuCoin (metadonnees seulement :
existence, date de premiere cotation, statut), puis tranche la precedence de chaque actif H2. Les places
qui ne publient pas de date sont resolues a la demande par la premiere bougie du marche lui-meme.

Aucun prix joint, aucun rendement, aucun signal, aucun verdict, aucun budget.

    --collect     recupere les instruments des cinq places (appels groupes) et archive
    --resolve     tranche la precedence des 174 actifs H2, avec repli bougie pour les indecis
    --reports     ecrit les rapports et la matrice H2 apres cross-venue
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from data_lake.collectors import venue_precedence as VP
from data_lake.collectors.venue_clients import VenueError, registry

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / "data" / "cross_venue"                 # data/* est gitignore
OUT = ROOT / "reports" / "data_acquisition"
UNIVERSE_H2 = ROOT / "reports" / "first_look" / "event_listing_perp_fade_v1_UNIVERSE.json"
TAPE = ROOT / "data_lake" / "events" / "official_event_tape.jsonl"


def _write_atomic(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp"); tmp.write_text(text, encoding="utf-8"); os.replace(tmp, p)


def collect(store: Optional[Path] = None) -> Dict[str, Any]:
    store = Path(store) if store else STORE
    out: Dict[str, Any] = {"collected_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "venues": {}, "instruments": []}
    for name, mod in registry().items():
        try:
            ins = mod.fetch_instruments()
            out["venues"][name] = {"status": "ok", "instruments": len(ins), "with_listing_date": sum(1 for i in ins if i["first_listed_ts"]),
                                   "by_market": dict(Counter(i["market_type"] for i in ins)), "native_listing_time": getattr(mod, "HAS_NATIVE_LISTING_TIME", None)}
            out["instruments"] += ins
        except Exception as e:
            out["venues"][name] = {"status": "error", "error": "%s: %s" % (type(e).__name__, str(e)[:80]), "instruments": 0}
    _write_atomic(store / "instruments.json", json.dumps(out, indent=1, ensure_ascii=False, default=str))
    return out


def load_instruments(store: Optional[Path] = None) -> Dict[str, Any]:
    p = (Path(store) if store else STORE) / "instruments.json"
    return json.loads(p.read_text()) if p.exists() else {"instruments": [], "venues": {}}


def announcement_evidence() -> Dict[str, str]:
    """Preuve plus faible que la date d'un instrument : une annonce officielle OKX/Bybit anterieure (tape P2)."""
    if not TAPE.exists():
        return {}
    by: Dict[str, List] = {}
    for l in TAPE.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        r = json.loads(l)
        if r.get("asset") and r.get("publication_ts_exchange") and r.get("source") in ("okx", "bybit") and r.get("event_type") in ("listing", "futures_listing"):
            by.setdefault(VP.normalise_base(r["asset"]), []).append((r["publication_ts_exchange"], r["source"], r["event_type"]))
    return {k: sorted(v)[0] for k, v in by.items()}


def resolve(resolve_undated: bool = True, store: Optional[Path] = None) -> Dict[str, Any]:
    data = load_instruments(store)
    if not data["instruments"]:
        data = collect(store)
    by = VP.index_by_base(data["instruments"])
    ann = announcement_evidence()
    events = json.loads(UNIVERSE_H2.read_text())["events"] if UNIVERSE_H2.exists() else []
    decisions = []
    for e in events:
        base = VP.normalise_base(e["asset"])
        a = ann.get(base)
        ev = None
        if a and a[0] < e["tradable_start_ts"]:
            ev = "%s %s announced %s" % (a[1], a[2], a[0][:16])
        decisions.append(VP.decide(e["asset"], e["tradable_start_ts"], by, ev))
    resolved_by_candle = 0
    if resolve_undated:
        from data_lake.collectors.venue_clients import gate
        for d in decisions:
            if d["classification"] != VP.UNKNOWN_PRECEDENCE or "gate" not in d["undated_venues"]:
                continue
            cands = [i for i in by.get(d["normalised_base"], []) if i["venue"] == "gate" and i["market_type"] == "spot"]
            for i in cands[:2]:
                try:
                    ts = gate.first_candle_ts(i["symbol"])
                except VenueError:
                    ts = None
                if not ts:
                    continue
                i2 = dict(i, first_listed_ts=ts, first_listed_source="first_daily_candle")
                by.setdefault(d["normalised_base"], []).append(i2)
                resolved_by_candle += 1
                break
        decisions = []
        for e in events:
            base = VP.normalise_base(e["asset"]); a = ann.get(base)
            ev = "%s %s announced %s" % (a[1], a[2], a[0][:16]) if a and a[0] < e["tradable_start_ts"] else None
            decisions.append(VP.decide(e["asset"], e["tradable_start_ts"], by, ev))
    return {"resolved_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "venues": data.get("venues", {}),
            "n_instruments": len(data["instruments"]), "resolved_by_first_candle": resolved_by_candle,
            "decisions": decisions, "summary": VP.summarise(decisions), "no_alpha_test": True, "no_price_join": True}


def write_reports(res: Dict[str, Any], out: Optional[Path] = None) -> Dict[str, Any]:
    from data_lake.indices import h2_launch_coverage_matrix as M
    out = Path(out) if out else OUT
    out.mkdir(parents=True, exist_ok=True)
    dec = {d["asset"]: d for d in res["decisions"]}; s = res["summary"]
    _write_atomic(out / "CROSS_VENUE_LIFECYCLE_COVERAGE.json", json.dumps(res, indent=1, ensure_ascii=False, default=str) + "\n")

    known = s["by_classification"].get(VP.OTHER_VENUE_FIRST, 0) + s["by_classification"].get(VP.BINANCE_FIRST, 0) + s["by_classification"].get(VP.NO_OTHER_VENUE, 0)
    md = [f"# CROSS-VENUE LIFECYCLE — coverage ({res['resolved_at_utc'][:19]} UTC)", "",
          "Where did this market exist before Binance opened its perpetual? Metadata only: instrument existence, first "
          "listing date, status. No price joined, no return computed, no signal, no verdict.", "",
          "| venue | instruments | with a published listing date | markets | native listing time |", "|---|---|---|---|---|"]
    for v, info in res["venues"].items():
        if info.get("status") == "ok":
            md.append(f"| {v} | {info['instruments']} | {info['with_listing_date']} | {info['by_market']} | {info['native_listing_time']} |")
        else:
            md.append(f"| {v} | — | — | — | **{info.get('error')}** |")
    md += ["", f"{res['n_instruments']} instruments in total. Gate publishes no listing date for any market; "
           f"{res['resolved_by_first_candle']} assets were resolved by asking Gate for the first daily candle of the pair, "
           "which is the market stating its own birth.", "",
           "## The four answers", "",
           f"1. **H2 events with a known venue precedence: {known} / {s['n']}.**",
           f"2. **Truly Binance-first: {s['by_classification'].get(VP.BINANCE_FIRST, 0)}** "
           f"(plus {s['by_classification'].get(VP.NO_OTHER_VENUE, 0)} listed on no other collected venue at all).",
           f"3. **Already priced elsewhere: {s['by_classification'].get(VP.OTHER_VENUE_FIRST, 0)}**, "
           f"first venue {s['first_venue']}, lead time in days min {s['lead_days']['min'] if s['lead_days'] else '—'}, "
           f"median {s['lead_days']['median'] if s['lead_days'] else '—'}, max {s['lead_days']['max'] if s['lead_days'] else '—'}.",
           f"4. **Still unknown: {s['unknown']}.** See `VENUE_PRECEDENCE_UNKNOWN_REMAINING.md`.", "",
           "## What this changes about H2, and what it does not", "",
           "`event_listing_perp_fade_v1` selects launches where the Binance **perpetual** is the first Binance market. "
           "That is a statement about Binance, not about the world. On the collected venues, "
           f"{s['by_classification'].get(VP.OTHER_VENUE_FIRST, 0)} of {s['n']} of those assets already had a market "
           "elsewhere, with a median lead of "
           f"{s['lead_days']['median'] if s['lead_days'] else '—'} days. A price therefore existed before the Binance "
           "launch for most of the universe.", "",
           "This is a fact about the data, not a verdict. It does not re-open the H2 result (INDECIDABLE, regard seq 8) "
           "and it produces no signal. What it does is make the population describable: a future preregistration can say "
           "which sub-population it is testing instead of assuming they are the same events.", "",
           "## Confidence", "", f"{s['by_confidence']} — `high` means a published listing date settled it; `medium` means the "
           "event tape shows an earlier official announcement but no instrument date; `low` means neither.", ""]
    _write_atomic(out / "CROSS_VENUE_LIFECYCLE_COVERAGE.md", "\n".join(md) + "\n")

    unk = [d for d in res["decisions"] if d["classification"] == VP.UNKNOWN_PRECEDENCE]
    um = [f"# VENUE PRECEDENCE — what is still unknown ({res['resolved_at_utc'][:19]} UTC)", "",
          f"{len(unk)} of {s['n']} H2 assets cannot be classified. An unknown is reported as an unknown; it is never "
          "counted as Binance-first by default.", ""]
    if unk:
        um += ["| asset | markets found | venues without a date | announcement evidence in the tape |", "|---|---|---|---|"]
        for d in sorted(unk, key=lambda x: x["asset"]):
            um.append(f"| {d['asset']} | {d['n_instruments']} on {', '.join(d['venues_matched']) or '—'} | {', '.join(d['undated_venues']) or '—'} | {d['announcement_evidence'] or '—'} |")
        um += ["", "## Which venue client to improve next", "",
               "Counted by how many unknowns it would settle:", ""]
        blame = Counter(v for d in unk for v in d["undated_venues"])
        for v, n in blame.most_common():
            um.append(f"- **{v}**: {n} assets. " + ("Gate publishes no listing date; the per-pair first-candle call resolves it one symbol at a time, and fails when the pair was renamed or delisted." if v == "gate" else "No listing date in the instruments endpoint."))
        um += ["", "The remaining route for all of them is the same: ask the market for its own first candle, or read the "
               "venue's own announcement archive (the P7 body archive already does this for Binance and can be pointed at "
               "OKX and Bybit).", ""]
    _write_atomic(out / "VENUE_PRECEDENCE_UNKNOWN_REMAINING.md", "\n".join(um) + "\n")

    # matrice H2 apres cross-venue : la precedence connue devient un champ renseigne
    ev = M.load_events()
    vis = json.loads((out / "_vision_probe_cache.json").read_text()) if (out / "_vision_probe_cache.json").exists() else {}
    # Le champ de la matrice mesure la COMPLETUDE de la donnee : la precedence est-elle tranchee ?
    # Un "Binance etait bien le premier" est une reponse, pas une donnee manquante.
    prec = {e["asset"]: (dec[e["asset"]]["evidence"] if e["asset"] in dec and dec[e["asset"]]["classification"] != VP.UNKNOWN_PRECEDENCE else None) for e in ev}
    kwargs: Dict[str, Any] = {}
    try:
        from data_lake.collectors.announcement_body_archive import bodies_by_event, _vision_downloaded
        kwargs["bodies"] = bodies_by_event(); kwargs["downloaded"] = _vision_downloaded()
    except Exception:
        pass
    try:
        rows = M.build_rows(ev, vis, prec, **kwargs)
    except TypeError:
        rows = M.build_rows(ev, vis, prec)
    for r in rows:
        d = dec.get(r.get("asset") or "")
        if d:
            r["venue_precedence"] = d["classification"]; r["first_elsewhere_venue"] = d.get("first_elsewhere_venue"); r["lead_days"] = d.get("lead_days")
    cols = M.COLUMNS + ["venue_precedence", "first_elsewhere_venue", "lead_days"]
    with open(out / "H2_AFTER_CROSS_VENUE_COVERAGE_MATRIX.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})
    summ = M.summarize(rows)
    _write_atomic(out / "H2_AFTER_CROSS_VENUE_COVERAGE_MATRIX.json",
                  json.dumps({"generated_at_utc": res["resolved_at_utc"], "precedence_summary": s, "summary": summ, "rows": rows, "no_alpha_test": True}, indent=1, ensure_ascii=False, default=str) + "\n")
    return {"coverage": s, "matrix": summ, "unknown": len(unk)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collect", action="store_true"); ap.add_argument("--resolve", action="store_true"); ap.add_argument("--reports", action="store_true")
    ap.add_argument("--no-candle-fallback", action="store_true")
    a = ap.parse_args()
    if a.collect:
        d = collect(); print(json.dumps({"venues": d["venues"], "instruments": len(d["instruments"])}, indent=1))
    if a.resolve or a.reports:
        res = resolve(resolve_undated=not a.no_candle_fallback)
        _write_atomic(STORE / "precedence.json", json.dumps(res, indent=1, ensure_ascii=False, default=str))
        print(json.dumps(res["summary"], indent=1, ensure_ascii=False))
        if a.reports:
            print(json.dumps(write_reports(res), indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
