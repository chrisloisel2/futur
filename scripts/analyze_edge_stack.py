#!/usr/bin/env python3
"""
scripts/analyze_edge_stack.py
─────────────────────────────────────────────────────────────────────────────
Analyse du STACK d'edges événementiels — à partir des tapes de trades OOS
exportées par train_event_engine.py (trades RÉELLEMENT sélectionnés par les
modèles walk-forward, aucun re-fit ici).

  1. PnL mensuel par moteur → matrice de corrélation (la diversification
     réelle, pas supposée).
  2. Chevauchement : % de trades partagés (même symbole, ±4h).
  3. STACK : union des trades triée dans le temps, cap global de concurrence
     (max 6), sizing 10%/trade → equity combinée, par année.

Sortie : reports/liq_cascade/EDGE_STACK.{json,md}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "reports" / "liq_cascade"
SIZING = 0.10
MAX_CONCURRENT_STACK = 6
HOLD_H = {"LIQ_CASCADE": 4, "CROWDING_REVERSAL": 24, "PREMIUM_DISLOCATION": 4}


def load_tapes():
    tapes = {}
    for name in HOLD_H:
        p = DIR / f"{name}_trades.parquet"
        if p.exists():
            t = pd.read_parquet(p)
            t["event_time"] = pd.to_datetime(t["event_time"], utc=True)
            t["engine"] = name
            tapes[name] = t.sort_values("event_time")
    return tapes


def main():
    tapes = load_tapes()
    if len(tapes) < 2:
        print(f"tapes trouvées : {list(tapes)} — besoin d'au moins 2")
        sys.exit(1)

    L = ["# EDGE STACK — analyse inter-moteurs (trades OOS walk-forward)\n"]

    # 1. PnL mensuel + corrélations
    monthly = {}
    for name, t in tapes.items():
        m = (t.set_index("event_time")["net"] * SIZING).resample("M").sum()
        monthly[name] = m
        L.append(f"- **{name}** : {len(t)} trades OOS, "
                 f"{t.event_time.min().date()} → {t.event_time.max().date()}, "
                 f"net total (sizé 10%) {t.net.sum()*SIZING*100:+.1f}%")
    M = pd.DataFrame(monthly).fillna(0.0)
    corr = M.corr()
    L.append("\n## Corrélation des PnL mensuels\n")
    L.append(corr.round(3).to_markdown())

    # 2. chevauchement de trades (même symbole, ±4h)
    L.append("\n## Chevauchement des trades (même symbole ± 4h)\n")
    names = list(tapes)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = tapes[names[i]], tapes[names[j]]
            bb = b.set_index("symbol")
            n_shared = 0
            for _, r in a.iterrows():
                if r["symbol"] not in bb.index:
                    continue
                cand = bb.loc[[r["symbol"]]]
                if (abs(cand["event_time"] - r["event_time"])
                        <= pd.Timedelta(hours=4)).any():
                    n_shared += 1
            L.append(f"- {names[i]} ∩ {names[j]} : {n_shared}/{len(a)} "
                     f"({n_shared/max(len(a),1)*100:.1f}% des trades de {names[i]})")

    # 3. stack union sous cap global
    allt = pd.concat(tapes.values(), ignore_index=True).sort_values("event_time")
    equity, open_until, taken = 1.0, [], []
    for _, r in allt.iterrows():
        t = r["event_time"]
        open_until = [u for u in open_until if u > t]
        if len(open_until) >= MAX_CONCURRENT_STACK or not np.isfinite(r["net"]):
            continue
        equity *= (1 + r["net"] * SIZING)
        taken.append({"t": t, "engine": r["engine"], "net": r["net"]})
        open_until.append(t + pd.Timedelta(hours=HOLD_H[r["engine"]]))
    tk = pd.DataFrame(taken)
    L.append(f"\n## STACK (union, cap {MAX_CONCURRENT_STACK}, 10%/trade)\n")
    L.append(f"- trades pris : {len(tk)}/{len(allt)} | equity finale : "
             f"{(equity-1)*100:+.1f}%")
    if len(tk):
        tk["year"] = tk["t"].dt.year
        rows = []
        for y, g in tk.groupby("year"):
            net = g["net"].values
            pf = net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9)
            eq_y = np.cumprod(1 + net * SIZING)[-1] - 1
            rows.append({"year": int(y), "n": len(g), "pf": round(pf, 2),
                         "roi": f"{eq_y*100:+.1f}%",
                         "mix": g["engine"].value_counts().to_dict()})
        L.append("\n" + pd.DataFrame(rows).to_markdown(index=False))

    # 4. SIM B — cap sur le GROSS, pas le NOMBRE (anti-adverse-selection des
    # vagues : les slots FIFO ratent les gains groupés). 2%/trade, gross ≤ 60%.
    SIZE_B, GROSS_CAP = 0.02, 0.60
    equity_b, open_b, taken_b = 1.0, [], []
    for _, r in allt.iterrows():
        t = r["event_time"]
        open_b = [(u, s) for (u, s) in open_b if u > t]
        gross = sum(s for _, s in open_b)
        if gross + SIZE_B > GROSS_CAP or not np.isfinite(r["net"]):
            continue
        equity_b *= (1 + r["net"] * SIZE_B)
        taken_b.append({"t": t, "engine": r["engine"], "net": r["net"]})
        open_b.append((t + pd.Timedelta(hours=HOLD_H[r["engine"]]), SIZE_B))
    tb = pd.DataFrame(taken_b)
    L.append(f"\n## STACK SIM B (gross-cap {GROSS_CAP:.0%}, {SIZE_B:.0%}/trade, "
             f"pas de cap de nombre)\n")
    L.append(f"- trades pris : {len(tb)}/{len(allt)} | equity finale : "
             f"{(equity_b-1)*100:+.1f}%")
    if len(tb):
        tb["year"] = tb["t"].dt.year
        rows = []
        for y, g in tb.groupby("year"):
            net = g["net"].values
            pf = net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9)
            eq_y = np.cumprod(1 + net * SIZE_B)[-1] - 1
            rows.append({"year": int(y), "n": len(g), "pf": round(pf, 2),
                         "roi": f"{eq_y*100:+.1f}%"})
        L.append("\n" + pd.DataFrame(rows).to_markdown(index=False))

    # 5. SIM C — priorité au SCORE dans chaque batch 5-min (pas premier-arrivé),
    # gross ≤ 60%, 2%/trade. Les scores viennent des modèles WF (aucun re-fit).
    allt2 = allt.copy()
    allt2["bar"] = allt2["event_time"].dt.floor("5min")
    equity_c, open_c, taken_c = 1.0, [], []
    for _, batch in allt2.groupby("bar", sort=True):
        t = batch["event_time"].iloc[0]
        open_c = [(u, s) for (u, s) in open_c if u > t]
        gross = sum(s for _, s in open_c)
        for _, r in batch.sort_values("score", ascending=False).iterrows():
            if gross + SIZE_B > GROSS_CAP or not np.isfinite(r["net"]):
                continue
            equity_c *= (1 + r["net"] * SIZE_B)
            gross += SIZE_B
            taken_c.append({"t": r["event_time"], "net": r["net"]})
            open_c.append((r["event_time"] + pd.Timedelta(hours=HOLD_H[r["engine"]]),
                           SIZE_B))
    tc = pd.DataFrame(taken_c)
    L.append(f"\n## STACK SIM C (priorité score par batch, gross ≤ 60%, 2%/trade)\n")
    L.append(f"- trades pris : {len(tc)}/{len(allt)} | equity finale : "
             f"{(equity_c-1)*100:+.1f}%")
    if len(tc):
        tc["year"] = tc["t"].dt.year
        rows = []
        for y, g in tc.groupby("year"):
            net = g["net"].values
            pf = net[net > 0].sum() / max(abs(net[net < 0].sum()), 1e-9)
            rows.append({"year": int(y), "n": len(g), "pf": round(pf, 2),
                         "roi": f"{(np.cumprod(1+net*SIZE_B)[-1]-1)*100:+.1f}%"})
        L.append("\n" + pd.DataFrame(rows).to_markdown(index=False))

    # 6. SIM D — 3 books SÉPARÉS (20% gross chacun, 2%/trade) : les moteurs ne
    # se disputent pas la capacité ; le stack = somme des books.
    L.append("\n## STACK SIM D (books séparés, 20% gross/moteur, 2%/trade)\n")
    rows, total = [], 0.0
    for name, t in tapes.items():
        eq, open_d = 1.0, []
        for _, r in t.iterrows():
            tt = r["event_time"]
            open_d = [u for u in open_d if u > tt]
            if len(open_d) * SIZE_B >= 0.20 or not np.isfinite(r["net"]):
                continue
            eq *= (1 + r["net"] * SIZE_B)
            open_d.append(tt + pd.Timedelta(hours=HOLD_H[name]))
        rows.append({"engine": name, "roi_book": f"{(eq-1)*100:+.1f}%"})
        total += (eq - 1)
    L.append(pd.DataFrame(rows).to_markdown(index=False))
    L.append(f"\n- somme des books : {total*100:+.1f}% (sur la fenêtre de chaque tape)")

    out = "\n".join(str(x) for x in L)
    (DIR / "EDGE_STACK.md").write_text(out)
    print(out)


if __name__ == "__main__":
    main()
