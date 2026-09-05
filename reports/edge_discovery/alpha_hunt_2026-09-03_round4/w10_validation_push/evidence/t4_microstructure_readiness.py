#!/usr/bin/env python
"""T4 — MICROSTRUCTURE_ALL_ROUND3 : combien de JOURS INDEPENDANTS COMPLETS sont
sur disque, et a quelle date calendaire la famille devient jugeable ?

Convention PREENREGISTREE :
  - unite independante = JOUR CALENDAIRE (un regime de book/flux persiste sur
    la journee) ; un jour PARTIEL ne compte pas ;
  - seuil de jugeabilite = >= 60 jours complets ET >= 2 regimes de vol distincts.

Un jour est COMPLET si les 24 heures sont presentes pour les 3 venues x 3
symboles x 2 flux (bbo, trades) = 3*3*24 = 216 fichiers horaires par flux.

Le script mesure aussi la contrainte de DISQUE, qui s'avere etre le facteur
limitant reel (le collecteur a un plafond `--disk-budget-gb` en dur).
"""
import json
import os
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path("/home/qbee/futur")
RAW = ROOT / "data/microstructure_reduced/raw"
OUT_ROOT = ROOT / "data/microstructure_reduced"
OUT = Path(__file__).resolve().parent

VENUES = 3
SYMBOLS = 3
HOURS = 24
FILES_PER_COMPLETE_DAY_PER_STREAM = VENUES * SYMBOLS * HOURS   # 216

MIN_COMPLETE_DAYS = 60          # seuil preenregistre
DISK_BUDGET_GB = 12.0           # valeur passee au service (verifiee via ps)


def scan():
    per_day = defaultdict(lambda: defaultdict(set))   # date -> stream -> {(venue,sym,hour)}
    per_day_bytes = defaultdict(int)
    for dp, _dn, fn in os.walk(RAW):
        if not fn:
            continue
        parts = dict(seg.split("=", 1) for seg in dp.split("/") if "=" in seg)
        d = parts.get("date")
        if d is None:
            continue
        stream = "bbo" if "/bbo/" in dp + "/" else ("trades" if "/trades/" in dp + "/" else "?")
        for f in fn:
            m = re.search(r"events-(\d{2})", f)
            if not m:
                continue
            per_day[d][stream].add((parts.get("venue"), parts.get("symbol"), m.group(1)))
            per_day_bytes[d] += os.path.getsize(os.path.join(dp, f))
    return per_day, per_day_bytes


def main():
    per_day, per_day_bytes = scan()
    days = []
    for d in sorted(per_day):
        streams = per_day[d]
        n_bbo, n_tr = len(streams.get("bbo", ())), len(streams.get("trades", ()))
        hours = sorted({h for (_v, _s, h) in streams.get("bbo", ())})
        complete = (n_bbo == FILES_PER_COMPLETE_DAY_PER_STREAM
                    and n_tr == FILES_PER_COMPLETE_DAY_PER_STREAM)
        days.append({"date": d, "bbo_slots": n_bbo, "trades_slots": n_tr,
                     "expected_slots": FILES_PER_COMPLETE_DAY_PER_STREAM,
                     "hours_present": len(hours),
                     "hours": "".join("1" if "%02d" % h in hours else "0" for h in range(24)),
                     "bytes": per_day_bytes[d],
                     "complete": complete})

    complete_days = [x for x in days if x["complete"]]
    n_complete = len(complete_days)
    gib = 1024 ** 3
    daily_gib = (sum(x["bytes"] for x in complete_days) / len(complete_days) / gib
                 if complete_days else None)

    used_gib = sum(f.stat().st_size for f in OUT_ROOT.rglob("*") if f.is_file()) / gib
    free_gib = shutil.disk_usage("/").free / gib

    # taux de rendement observe en jours COMPLETS par jour calendaire ecoule
    full_elapsed = [x for x in days if x["date"] not in (days[0]["date"], days[-1]["date"])]
    yield_rate = (sum(1 for x in full_elapsed if x["complete"]) / len(full_elapsed)
                  if full_elapsed else 1.0)

    remaining = MIN_COMPLETE_DAYS - n_complete
    today = date.fromisoformat(days[-1]["date"])
    eta_perfect = today + timedelta(days=remaining)
    eta_observed = today + timedelta(days=int(round(remaining / yield_rate))) if yield_rate else None

    budget_headroom_gib = DISK_BUDGET_GB - used_gib
    days_until_budget = budget_headroom_gib / daily_gib if daily_gib else None
    gib_needed_for_target = remaining * daily_gib if daily_gib else None

    res = {
        "scan_root": str(RAW),
        "definition_complete_day": "24h x 3 venues x 3 symbols present on BOTH bbo and trades (=%d slots/stream)" % FILES_PER_COMPLETE_DAY_PER_STREAM,
        "days": days,
        "n_complete_independent_days": n_complete,
        "complete_days": [x["date"] for x in complete_days],
        "partial_days": [{"date": x["date"], "hours_present": x["hours_present"]}
                         for x in days if not x["complete"]],
        "mean_gib_per_complete_day": round(daily_gib, 3) if daily_gib else None,
        "collector_footprint_gib": round(used_gib, 3),
        "machine_free_gib": round(free_gib, 1),
        "observed_complete_day_yield_per_elapsed_day": round(yield_rate, 3),
        "threshold_complete_days": MIN_COMPLETE_DAYS,
        "days_remaining": remaining,
        "target_date_if_100pct_uptime": str(eta_perfect),
        "target_date_at_observed_yield": str(eta_observed),
        "disk": {
            "collector_disk_budget_gb": DISK_BUDGET_GB,
            "budget_headroom_gib": round(budget_headroom_gib, 2),
            "days_until_budget_stop": round(days_until_budget, 1) if days_until_budget else None,
            "budget_stop_date_estimate": str(today + timedelta(days=int(days_until_budget)))
            if days_until_budget else None,
            "complete_days_reachable_before_budget_stop": n_complete + int(days_until_budget)
            if days_until_budget else None,
            "gib_needed_to_reach_threshold": round(gib_needed_for_target, 1) if gib_needed_for_target else None,
            "verdict": None,
        },
    }
    need = res["disk"]["gib_needed_to_reach_threshold"]
    if need is not None:
        if need > budget_headroom_gib and need > free_gib:
            res["disk"]["verdict"] = ("UNREACHABLE: le seuil demande %.1f GiB de plus, "
                                      "au-dela du plafond collecteur (%.1f GiB restants) ET "
                                      "de l'espace libre machine (%.1f GiB)"
                                      % (need, budget_headroom_gib, free_gib))
        elif need > budget_headroom_gib:
            res["disk"]["verdict"] = ("BLOCKED_BY_COLLECTOR_BUDGET: %.1f GiB requis > %.1f GiB "
                                      "de marge sous --disk-budget-gb %.0f (l'espace machine, lui, suffirait)"
                                      % (need, budget_headroom_gib, DISK_BUDGET_GB))
        else:
            res["disk"]["verdict"] = "OK"
    (OUT / "t4_microstructure_readiness.json").write_text(json.dumps(res, indent=2))
    print(json.dumps({k: v for k, v in res.items() if k != "days"}, indent=2))


if __name__ == "__main__":
    main()
