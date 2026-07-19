#!/usr/bin/env python3
"""
scripts/run_wave_portfolio_breadth_breaker.py
─────────────────────────────────────────────────────────────────────────────
Breadth circuit-breaker sur le portefeuille 3-moteurs à l'unité VAGUE.

Motivation (TRADE_HISTORY_ANALYSIS 2026-07-11) : les pires journées du stack
sont des KRACHS SYSTÉMIQUES où les moteurs mean-reversion achètent le dip sur
~28 symboles simultanément (2025-10-10, 2022-11-08 FTX, 2026-02-05,
2025-02-02). Worst 2% des trades = 18% des gains bruts effacés.
Le breadth MODÉRÉ (>5) est BON (PF 1.31 per deep dive) — seul l'extrême tue.

Règle PRÉ-DÉCLARÉE (issue de l'analyse, PAS tunée sur cette sim) :
  breadth(t) = nb de SYMBOLES distincts ayant émis ≥1 event (union des flux
  COMPLETS cascade+crowding+premium, pas seulement les trades sélectionnés)
  dans la fenêtre CAUSALE (t-60min, t].
  → si breadth(t) > 15 : krach systémique, on s'abstient (trade sauté).
  Primaire = 15. Sensibilité rapportée : 10 / 20 / 25 (multiplicité honnête,
  la revendication reste ancrée sur 15).

Tout le reste = design du wave portfolio INCHANGÉ
(run_three_engine_wave_portfolio.py : vague gap≤30min, dédup symbole,
top-3/vague, hold par moteur, gross cap 60%). Nets déjà à 14 bps de coûts.
Sortie : reports/liq_cascade/WAVE_BREADTH_BREAKER.{json,md}
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "reports" / "liq_cascade"
EVENTS = ROOT / "data" / "events"
HOLD_H = {"LIQ_CASCADE": 4, "CROWDING_REVERSAL": 24, "PREMIUM_DISLOCATION": 4}
WAVE_GAP = pd.Timedelta(minutes=30)
TOP_K = 3
GROSS_CAP = 0.60
SIZING = 0.02          # l'échelle rapportée comme sleeve additif (cf. THREE_ENGINE)
BREADTH_WIN = pd.Timedelta(minutes=60)
THRESH_PRIMARY = 15
THRESH_SENS = (10, 20, 25)


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
    allt["year"] = allt["event_time"].dt.year
    allt["rank"] = (allt.groupby(["engine", "year"])["score"].rank(pct=True))
    return allt.reset_index(drop=True)


def load_event_stream() -> pd.DataFrame:
    """Union des flux COMPLETS d'events (pas seulement les sélectionnés)."""
    frames = []
    for f in ("cascade_dataset", "crowding_dataset", "premium_dataset"):
        p = EVENTS / f"{f}.parquet"
        d = pd.read_parquet(p, columns=["event_time", "symbol"])
        d["event_time"] = pd.to_datetime(d["event_time"], utc=True)
        frames.append(d)
    s = pd.concat(frames, ignore_index=True).sort_values("event_time")
    return s.reset_index(drop=True)


def breadth_at(times: pd.Series, stream: pd.DataFrame) -> np.ndarray:
    """breadth(t) = symboles distincts dans (t-60min, t] — causal (≤ t)."""
    ev_t = stream["event_time"].values
    ev_s = stream["symbol"].values
    out = np.zeros(len(times), dtype=np.int32)
    t_arr = times.values
    lo = np.searchsorted(ev_t, t_arr - BREADTH_WIN.to_timedelta64(), side="right")
    hi = np.searchsorted(ev_t, t_arr, side="right")
    for i, (a, b) in enumerate(zip(lo, hi)):
        out[i] = len(set(ev_s[a:b])) if b > a else 0
    return out


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
    allt = (allt.sort_values(["wave", "rank"], ascending=[True, False])
                .drop_duplicates(subset=["wave", "symbol"], keep="first"))
    allt["k_in_wave"] = allt.groupby("wave").cumcount()
    return allt[allt["k_in_wave"] < TOP_K].sort_values("event_time")


def simulate(waved: pd.DataFrame, sizing: float, breadth_max: int | None) -> dict:
    equity, eq_curve = 1.0, []
    open_pos = {}
    n_breaker = 0
    for _, r in waved.iterrows():
        t = r["event_time"]
        open_pos = {s: (u, sz) for s, (u, sz) in open_pos.items() if u > t}
        if breadth_max is not None and r["breadth"] > breadth_max:
            n_breaker += 1
            continue
        if r["symbol"] in open_pos:
            continue
        gross = sum(sz for _, sz in open_pos.values())
        if gross + sizing > GROSS_CAP:
            continue
        equity *= (1 + r["net"] * sizing)
        eq_curve.append((t, equity, r["net"], int(r["year"])))
        open_pos[r["symbol"]] = (t + pd.Timedelta(hours=HOLD_H[r["engine"]]), sizing)
    df = pd.DataFrame(eq_curve, columns=["t", "equity", "net", "year"])
    out = {"breadth_max": breadth_max, "n_trades": len(df),
           "n_breaker_skips": n_breaker, "by_year": {}}
    for y, g in df.groupby("year"):
        net = g["net"].values
        pf = net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9)
        eq = np.cumprod(1 + net * sizing)
        dd = float(((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq)).min())
        out["by_year"][int(y)] = {
            "n": int(len(g)), "pf": round(float(pf), 2),
            "roi": round(float(eq[-1] - 1), 4), "maxdd": round(dd, 4)}
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
    stream = load_event_stream()
    print(f"trades OOS : {len(allt)} · flux events union : {len(stream)}")
    waved = build_waves(allt)
    waved = waved.copy()
    waved["breadth"] = breadth_at(waved["event_time"], stream)
    q = waved["breadth"].quantile([0.5, 0.9, 0.99]).to_dict()
    print(f"breadth des trades wave-unitisés : médiane {q[0.5]:.0f} · "
          f"p90 {q[0.9]:.0f} · p99 {q[0.99]:.0f} · max {waved['breadth'].max()}")

    results = {"design": {
        "rule": "skip trade si breadth(t-60min,t] > seuil (symboles distincts, "
                "union flux complets 3 moteurs, causal)",
        "primary_threshold": THRESH_PRIMARY,
        "base": "run_three_engine_wave_portfolio design inchangé",
        "sizing": SIZING}, "sims": {}}

    L = ["# Breadth circuit-breaker — wave portfolio 3 moteurs (sizing 2%)\n",
         f"Règle pré-déclarée : skip si >15 symboles distincts en 60min "
         f"(union flux complets). Breadth trades : méd {q[0.5]:.0f}, "
         f"p90 {q[0.9]:.0f}, p99 {q[0.99]:.0f}.\n"]

    base = simulate(waved, SIZING, None)
    results["sims"]["baseline"] = base
    rows = [("baseline (sans breaker)", base)]
    for th in sorted({THRESH_PRIMARY, *THRESH_SENS}):
        r = simulate(waved, SIZING, th)
        results["sims"][f"breadth_{th}"] = r
        tag = " ← PRIMAIRE" if th == THRESH_PRIMARY else ""
        rows.append((f"breaker >{th}{tag}", r))

    L.append("\n| config | n | ROI 23→26 | %/an | maxDD | PF | skips |")
    L.append("|---|---:|---:|---:|---:|---:|---:|")
    for name, r in rows:
        f = r["final"]
        L.append(f"| {name} | {f['n']} | {f['roi_total']*100:+.1f}% | "
                 f"{f['roi_ann']*100:+.1f}% | {f['maxdd']*100:.1f}% | "
                 f"{f['pf']} | {r.get('n_breaker_skips', 0)} |")
        print(f"{name:28} n={f['n']:5} ROI={f['roi_total']*100:+6.1f}% "
              f"({f['roi_ann']*100:+5.1f}%/an) DD={f['maxdd']*100:5.1f}% "
              f"PF={f['pf']:.3f} skips={r.get('n_breaker_skips', 0)}",
              flush=True)

    L.append("\n## Détail par année (primaire vs baseline)\n")
    for tag, r in (("baseline", base), (f">{THRESH_PRIMARY}",
                                        results["sims"][f"breadth_{THRESH_PRIMARY}"])):
        L.append(f"\n### {tag}\n")
        yr_rows = [{"year": y, **d} for y, d in sorted(r["by_year"].items())]
        L.append(pd.DataFrame(yr_rows).to_markdown(index=False))

    (DIR / "WAVE_BREADTH_BREAKER.json").write_text(
        json.dumps(results, indent=2, default=str))
    (DIR / "WAVE_BREADTH_BREAKER.md").write_text("\n".join(L))
    print(f"\n→ {DIR / 'WAVE_BREADTH_BREAKER.md'}")


if __name__ == "__main__":
    main()
