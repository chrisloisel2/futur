"""Emit the §9 gate table of REPORT.md from RESULTS.json: every mechanism classed better
than WEAK, with every briefing §2 column. Written to stdout as markdown."""
import json, os, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(os.path.dirname(HERE), "RESULTS.json")))
df = pd.DataFrame(R["mechanisms"])
live = df[~df["status"].astype(str).str.startswith(("VOID", "superseded"))]
better = live[~live["verdict"].isin(["WEAK", "DEAD", "DATA_LIMITED", "VOID"])].copy()
better["absg"] = better["gross_bps"].abs()


def ci(x):
    return f"[{x[0]:.1f}, {x[1]:.1f}]" if isinstance(x, list) and x and x[0] == x[0] else "—"


def yby(x):
    if not isinstance(x, dict):
        return "—"
    return " ".join(f"{y[-2:]}:{v['gross_bps']:+.0f}" for y, v in sorted(x.items()))


rows = []
for _, r in better.sort_values("absg", ascending=False).iterrows():
    rows.append({
        "mechanism": r["mechanism"],
        "family": r["family"],
        "n_raw": r["n_raw"],
        "L1": r.get("n_independent_L1"),
        "L2": r["n_independent_L2"],
        "L3": r["n_independent_L3"],
        "gross": r["gross_bps"],
        "net14": r["net_bps"],
        "net28": r["net_bps_stress28"],
        "net_2leg": r["net_bps_2leg"],
        "net_2leg_str56": r["net_bps_2leg_stress56"],
        "t_declust": r["t_stat_declustered"],
        "t_naive": r["t_stat_naive_WRONG"],
        "clust_infl": r["clustering_inflation_factor"],
        "ci95": ci(r["bootstrap_ci95"]),
        "ex_best_yr": r["ex_best_year_gross_bps"],
        "n_req_days": r["n_required_independent_days"],
        "ep/wk": r["event_rate_per_week_last6m"],
        "ETA_y": r["eta_forward_confirmation_years"],
        "ETA_y_corr": r.get("eta_forward_confirmation_years_datecorrected"),
        "verdict": r["verdict"],
        "year_by_year": yby(r.get("year_by_year")),
    })
t = pd.DataFrame(rows)
hdr = [c for c in t.columns if c != "year_by_year"]
print(f"Total mechanisms run: **{len(df)}** — {len(live)} live, "
      f"{len(df) - len(live)} void/superseded (§2). Classed better than `WEAK`: **{len(better)}**. "
      f"`VALIDATED_FOR_FORWARD`: **0**.\n")
print("### 9.1 All mechanisms by family and verdict\n")
piv = live.pivot_table(index="family", columns="verdict", values="mechanism",
                       aggfunc="count", fill_value=0)
piv["TOTAL"] = piv.sum(axis=1)
print("| family | " + " | ".join(str(c) for c in piv.columns) + " |")
print("|" + "|".join(["---"] * (len(piv.columns) + 1)) + "|")
for fam, r in piv.iterrows():
    print(f"| {fam} | " + " | ".join(str(int(v)) for v in r) + " |")
print("\n### 9.2 Every mechanism better than `WEAK`, all §2 columns\n")
print("| " + " | ".join(hdr) + " |")
print("|" + "|".join(["---"] * len(hdr)) + "|")
for _, r in t.iterrows():
    print("| " + " | ".join("—" if pd.isna(r[c]) else (f"{r[c]:g}" if isinstance(r[c], float) else str(r[c]))
                            for c in hdr) + " |")
print("\n**Year-by-year (gross bps) for the top rows:**\n")
print("| mechanism | " + " | ".join(["year_by_year"]) + " |")
print("|---|---|")
for _, r in t.head(20).iterrows():
    print(f"| {r['mechanism']} | {r['year_by_year']} |")
