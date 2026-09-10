#!/usr/bin/env python3
"""
first_look_gate.py -- le premier regard n'est pas une decision, c'est une porte.

    collecter -> pre-enregistrer -> geler le snapshot -> regarder UNE fois

Ce module ne joint aucun prix. Il fait deux choses :
  --status  : evalue les criteres de la porte sur les tapes vivantes
                forced_flow : n_events >= 300, enrichis >= 95 %, bbo_age >= 0 partout,
                              stream_delay mesure (binance), aucune contamination post-evenement
                event_tape  : n >= 1, publication_ts presente sur les historiques
  --freeze <dataset> <name> : gele un snapshot -- copie immuable + manifeste
                (chemin, lignes, min/max event ts, sha256, commit git, arbre sale ?) --
                dans data_lake/first_look/<name>/. Le manifeste porte des champs VIDES
                (hypothese, horizons, cout, criteres) que seul un pre-enregistrement
                scelle (tools/look_ledger.py --seal-orphan) a le droit de remplir.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors.tape_io import read_tape  # noqa: E402

EVENT_TAPE = ROOT / "data_lake" / "events" / "official_event_tape.jsonl"
LIQ = ROOT / "data_lake" / "events" / "liquidations"
OUT = ROOT / "data_lake" / "first_look"
GATE = {"forced_flow": {"min_events": 300, "min_enriched_ratio": 0.95}, "event_tape": {"min_events": 1}}


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:          # OUT hors depot (tests) ; Path.is_relative_to n'existe pas en 3.8
        return str(p)


def _git(*a):
    r = subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True, text=True)
    return r.stdout.strip()


def forced_flow_status():
    rows = read_tape(LIQ) if LIQ.exists() else []
    n = len(rows); enr = sum(1 for r in rows if r.get("spread_before_bps") is not None)
    ages = [r[k] for r in rows for k in ("bbo_age_ms", "mark_age_ms") if r.get(k) is not None]
    delay = sorted(r["stream_delay_ms"] for r in rows if r.get("stream_delay_ms") is not None)
    st = {"dataset": "forced_flow", "n_events": n, "enriched_ratio": round(enr / n, 4) if n else 0.0,
          "min_age_ms": min(ages) if ages else None, "n_negative_age": sum(1 for a in ages if a < 0),
          "stream_delay_measured": bool(delay), "stream_delay_median_ms": delay[len(delay) // 2] if delay else None,
          "returns_left_null": all(r.get(k) is None for r in rows for k in r if k.startswith("return_")),
          "venues": sorted({r["venue"] for r in rows}),
          "n_ge_50k_usd": sum(1 for r in rows if (r.get("notional_usd") or 0) >= 50_000)}
    g = GATE["forced_flow"]
    st["criteria"] = {"n_events_ge_300": n >= g["min_events"],
                      "enriched_ratio_ge_95pct": st["enriched_ratio"] >= g["min_enriched_ratio"],
                      "bbo_age_ge_0_everywhere": bool(ages) and st["n_negative_age"] == 0,
                      "stream_delay_measured": st["stream_delay_measured"],
                      "no_post_event_contamination": st["returns_left_null"] and st["n_negative_age"] == 0}
    st["open"] = all(st["criteria"].values()); return st


def event_tape_status():
    rows = [json.loads(l) for l in EVENT_TAPE.read_text(encoding="utf-8").splitlines() if l.strip()] if EVENT_TAPE.exists() else []
    hist = [r for r in rows if not str(r.get("raw_url", "")).startswith("snapshot://")]
    st = {"dataset": "event_tape", "n_events": len(rows), "n_historical": len(hist),
          "historical_with_publication_ts": sum(1 for r in hist if r.get("publication_ts_exchange")),
          "with_trading_start_ts": sum(1 for r in hist if r.get("trading_start_ts")), "no_price_join": True}
    st["criteria"] = {"n_events_ge_1": len(rows) >= GATE["event_tape"]["min_events"],
                      "publication_ts_on_all_historical": st["historical_with_publication_ts"] == len(hist),
                      "no_price_join_performed": True}
    st["open"] = all(st["criteria"].values()); return st


def _dataset(arg: str) -> str:
    """Accepte un nom ('event_tape', 'forced_flow') ou le chemin du jeu de donnees."""
    if arg in ("forced_flow", "event_tape"):
        return arg
    p = Path(arg)
    p = p if p.is_absolute() else (ROOT / p)
    if p.resolve() == EVENT_TAPE.resolve():
        return "event_tape"
    if p.resolve() == LIQ.resolve():
        return "forced_flow"
    raise SystemExit(f"dataset inconnu : {arg} (attendu event_tape | forced_flow | leur chemin)")


def committed_blob_check(path: Path) -> dict:
    """Un snapshot doit etre reproductible depuis git : le fichier sur disque doit etre
    exactement le blob de HEAD (le timer d'ingestion peut l'avoir prolonge entre-temps)."""
    try:
        rel = str(path.relative_to(ROOT))
    except ValueError:      # hors depot (tests) : rien a comparer, on le dit
        return {"path": str(path), "disk_blob": None, "head_blob": None, "identical": True, "note": "outside repo: not checked"}
    disk = _git("hash-object", rel)
    head = _git("rev-parse", f"HEAD:{rel}")
    return {"path": rel, "disk_blob": disk, "head_blob": head, "identical": bool(disk) and disk == head}


def freeze(dataset: str, name: str):
    dataset = _dataset(dataset)
    st = forced_flow_status() if dataset == "forced_flow" else event_tape_status()
    if not st["open"]:
        raise SystemExit(f"REFUS : la porte '{dataset}' n'est pas ouverte : {st['criteria']}")
    blob = None
    if dataset == "event_tape":
        blob = committed_blob_check(EVENT_TAPE)
        if not blob["identical"]:
            raise SystemExit(f"REFUS : le tape sur disque n'est pas le blob commite ({blob}). "
                             "Commiter ou restaurer le tape avant de geler.")
    d = OUT / name; d.mkdir(parents=True, exist_ok=False)
    if dataset == "forced_flow":
        rows = read_tape(LIQ); src = d / "liquidations_frozen.jsonl"
        src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        ts = [r["event_ts_exchange"] for r in rows if r.get("event_ts_exchange")]
    else:
        src = d / "official_event_tape_frozen.jsonl"; shutil.copyfile(EVENT_TAPE, src)
        rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
        ts = [r["publication_ts_exchange"] for r in rows if r.get("publication_ts_exchange")]
    man = {"dataset": dataset, "name": name,
           "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "input_path": _rel(src), "row_count": len(rows),
           "min_event_ts": min(ts) if ts else None, "max_event_ts": max(ts) if ts else None,
           "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
           "git_commit": _git("rev-parse", "HEAD"), "committed_blob": blob, "working_tree_dirty": bool(_git("status", "--porcelain", "--", "data_lake", "research_kernel", "mechanisms", "tools")),
           "gate_status": st,
           "TO_BE_FILLED_BY_SEALED_PREREGISTRATION_ONLY": {
               "exact_hypothesis": None, "exact_horizons": None, "exact_cost_model": None,
               "exact_promotion_rejection_criteria": None, "prereg_orphan_branch": None, "look_ledger_seq": None}}
    (d / "SNAPSHOT_MANIFEST.json").write_text(json.dumps(man, indent=2, ensure_ascii=False))
    print(f"-> {d / 'SNAPSHOT_MANIFEST.json'}  rows={len(rows)} sha256={man['sha256'][:16]} commit={man['git_commit'][:12]}")
    return man


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true"); ap.add_argument("--freeze", nargs=2, metavar=("DATASET", "NAME"))
    a = ap.parse_args()
    if a.freeze:
        freeze(*a.freeze); return
    for st in (event_tape_status(), forced_flow_status()):
        print(json.dumps(st, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
