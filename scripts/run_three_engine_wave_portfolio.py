#!/usr/bin/env python3
"""
scripts/run_three_engine_wave_portfolio.py
─────────────────────────────────────────────────────────────────────────────
Portefeuille 3-moteurs à l'unité VAGUE — le design issu du diagnostic de
conversion (EDGE_INVENTORY_2026-07-10) : l'edge vit dans les rafales
multi-symboles ; l'exécution doit donc trader la VAGUE, pas l'event.

Design DÉCLARÉ (paramètres dérivés de la structure documentée, pas tunés) :
  • vague = trades (tapes OOS des 3 moteurs) chaînés à ≤ 30 min market-wide
    (la fenêtre du deep-dive n_events_mktwide_30m) ;
  • dédup par symbole dans la vague (meilleur rang), top-3 par vague
    (caps du ranker du repo), pas de pyramide sur symbole déjà ouvert ;
  • rang = percentile du score DANS (moteur, année) — rend les scores des
    3 modèles comparables sans re-fit ;
  • hold = horizon du moteur gagnant (cascade/premium 4h, crowding 24h) ;
  • gross cap 60 % ; échelle de sizing RAPPORTÉE EN ENTIER : 2 / 5 / 10 %.

Les nets incluent déjà 14 bps de coûts. 2022 = fold sans entraînement
suffisant (affiché, exclu du bilan). Sortie :
reports/liq_cascade/THREE_ENGINE_WAVE_PORTFOLIO.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "reports" / "liq_cascade"
HOLD_H = {"LIQ_CASCADE": 4, "CROWDING_REVERSAL": 24, "PREMIUM_DISLOCATION": 4}
WAVE_GAP = pd.Timedelta(minutes=30)
TOP_K = 3
GROSS_CAP = 0.60
SIZINGS = (0.02, 0.05, 0.10)


def load_trades() -> pd.DataFrame:
    frames = []
    for name in HOLD_H:
        p = DIR / f"{name}_trades.parquet"
        if not p.exists():
            print(f"⚠ tape absente : {p.name}")
            continue
        t = pd.read_parquet(p)
        t["event_time"] = pd.to_datetime(t["event_time"], utc=True)
        t["engine"] = name
        frames.append(t)
    allt = pd.concat(frames, ignore_index=True).sort_values("event_time")
    allt = allt[np.isfinite(allt["net"])]
    # rang comparable inter-moteurs : percentile du score dans (moteur, année)
    allt["year"] = allt["event_time"].dt.year
    allt["rank"] = (allt.groupby(["engine", "year"])["score"]
                    .rank(pct=True))
    return allt.reset_index(drop=True)


def build_waves(allt: pd.DataFrame) -> pd.DataFrame:
    times = allt["event_time"].values
    wave_id = np.zeros(len(allt), dtype=np.int64)
    wid = 0
    for i in range(1, len(allt)):
        if (times[i] - times[i - 1]) > WAVE_GAP.to_timedelta64():
            wid += 1
        wave_id[i] = wid
    allt = allt.copy()
    allt["wave"] = wave_id
    # dédup par symbole dans la vague (meilleur rang), puis top-K par rang
    allt = (allt.sort_values(["wave", "rank"], ascending=[True, False])
                .drop_duplicates(subset=["wave", "symbol"], keep="first"))
    allt["k_in_wave"] = allt.groupby("wave").cumcount()
    return allt[allt["k_in_wave"] < TOP_K].sort_values("event_time")


def simulate(waved: pd.DataFrame, sizing: float) -> dict:
    equity, eq_curve = 1.0, []
    open_pos = {}   # symbol -> (until, size)
    n_skipped_open, n_skipped_gross = 0, 0
    for _, r in waved.iterrows():
        t = r["event_time"]
        open_pos = {s: (u, sz) for s, (u, sz) in open_pos.items() if u > t}
        if r["symbol"] in open_pos:
            n_skipped_open += 1
            continue
        gross = sum(sz for _, sz in open_pos.values())
        if gross + sizing > GROSS_CAP:
            n_skipped_gross += 1
            continue
        equity *= (1 + r["net"] * sizing)
        eq_curve.append((t, equity, r["net"], int(r["year"]), r["engine"]))
        open_pos[r["symbol"]] = (t + pd.Timedelta(hours=HOLD_H[r["engine"]]), sizing)
    if not eq_curve:
        return {}
    df = pd.DataFrame(eq_curve, columns=["t", "equity", "net", "year", "engine"])
    out = {"sizing": sizing, "n_trades": len(df),
           "skipped_open": n_skipped_open, "skipped_gross": n_skipped_gross,
           "by_year": {}, "final": {}}
    for y, g in df.groupby("year"):
        net = g["net"].values
        pf = net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9)
        eq = np.cumprod(1 + net * sizing)
        dd = float(((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min())
        out["by_year"][int(y)] = {
            "n": int(len(g)), "pf": round(float(pf), 2),
            "roi": round(float(eq[-1] - 1), 4), "maxdd": round(dd, 4),
            "wr": round(float((net > 0).mean()), 3),
            "mix": g["engine"].value_counts().to_dict()}
    # bilan VALIDE = 2023+ (2022 : folds sans train suffisant)
    v = df[df["year"] >= 2023]
    net = v["net"].values
    eq = np.cumprod(1 + net * sizing)
    dd = float(((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min())
    yrs = (v["t"].max() - v["t"].min()).days / 365.25
    out["final"] = {"window": "2023-01 → 2026-07", "n": int(len(v)),
                    "roi_total": round(float(eq[-1] - 1), 4),
                    "roi_ann": round(float((eq[-1]) ** (1 / max(yrs, 0.1)) - 1), 4),
                    "maxdd": round(dd, 4),
                    "pf": round(float(net[net > 0].sum() /
                                      max(abs(net[net < 0].sum()), 1e-9)), 3)}
    return out


def main():
    allt = load_trades()
    print(f"trades OOS chargés : {len(allt)} "
          f"({dict(allt.engine.value_counts())})")
    waved = build_waves(allt)
    n_waves = waved["wave"].nunique()
    print(f"vagues : {n_waves} | trades wave-unitisés : {len(waved)} "
          f"(dédup+top{TOP_K} : {len(allt)} → {len(waved)})", flush=True)

    results = {"design": {"wave_gap_min": 30, "top_k": TOP_K,
                          "gross_cap": GROSS_CAP,
                          "rank": "score pct dans (engine, année)",
                          "note": "2022 exclu du bilan (folds sans train)"},
               "n_waves": int(n_waves), "sims": []}
    L = ["# Portefeuille 3-moteurs — unité VAGUE (tapes OOS re-entraînées)\n",
         f"Vagues : {n_waves} · trades unitisés {len(waved)} (depuis {len(allt)} events)\n"]
    for s in SIZINGS:
        r = simulate(waved, s)
        results["sims"].append(r)
        f = r["final"]
        L.append(f"\n## Sizing {s:.0%}/trade — bilan 2023→2026 : "
                 f"**ROI {f['roi_total']*100:+.1f}% ({f['roi_ann']*100:+.1f}%/an), "
                 f"maxDD {f['maxdd']*100:.1f}%, PF {f['pf']}**, n={f['n']}\n")
        rows = [{"year": y, **{k: v for k, v in d.items() if k != "mix"}}
                for y, d in sorted(r["by_year"].items())]
        L.append(pd.DataFrame(rows).to_markdown(index=False))
        print(f"[{s:.0%}] 2023-26: ROI {f['roi_total']*100:+.1f}% "
              f"({f['roi_ann']*100:+.1f}%/an) maxDD {f['maxdd']*100:.1f}% "
              f"PF {f['pf']}", flush=True)
        for y, d in sorted(r["by_year"].items()):
            print(f"    {y}: n={d['n']:4} PF={d['pf']:5.2f} "
                  f"ROI={d['roi']*100:+6.1f}% DD={d['maxdd']*100:5.1f}% "
                  f"mix={d['mix']}", flush=True)

    (DIR / "THREE_ENGINE_WAVE_PORTFOLIO.json").write_text(
        json.dumps(results, indent=2, default=str))
    (DIR / "THREE_ENGINE_WAVE_PORTFOLIO.md").write_text("\n".join(L))
    print(f"\n→ {DIR / 'THREE_ENGINE_WAVE_PORTFOLIO.md'}")


if __name__ == "__main__":
    main()
