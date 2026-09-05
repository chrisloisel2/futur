#!/usr/bin/env python
"""W2 -- regenerate the two generated tables inside REPORT.md from RESULTS.json.

Keeps the prose hand-written but guarantees every number in the T8 table and in the annex
comes straight from the evidence JSONs, so the report cannot drift from the results.

Re-executable: .venv/bin/python evidence/finalize_report.py
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
R = json.load(open(os.path.join(ROOT, "RESULTS.json")))
T8 = json.load(open(os.path.join(HERE, "leadlag_t8_results.json")))


def f(v, n=2):
    if v is None:
        return "–"
    if isinstance(v, (int, float)):
        return f"{v:,.0f}" if abs(v) >= 1000 and n == 0 else f"{v:.{n}f}"
    return str(v)


# ---------- table 1: the T8 lead-lag measurement ----------
NAME = {"event_ts_ns": "event", "receive_ts_ns": "receive"}
rows = ["| symbole | horloge | venue | lag argmax | corr max | corr à lag 0 | lecture |",
        "|---|---|---|---|---|---|---|"]
for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
    for clock in ("event_ts_ns", "receive_ts_ns"):
        cl = T8.get("per_symbol", {}).get(sym, {}).get(clock, {})
        for v in ("hyperliquid", "okx"):
            if v not in cl:
                continue
            x = cl[v]
            lag = x["argmax_lag_ms"]
            read = ("**Binance précède**" if lag < 0 else
                    "HL précède" if lag > 0 else "synchrone")
            bold = "**" if v == "hyperliquid" else ""
            rows.append(f'| {sym[:-4]} | {NAME[clock]} | {bold}{v}{bold} | {bold}{lag:+d} ms{bold} | '
                        f'{x["argmax_corr"]:.3f} | {x["corr_at_lag0"]:.3f} | {read} |')
t8_table = "\n".join(rows)

# ---------- table 2: the full annex ----------
order = ["VALIDATED_FOR_FORWARD", "PROMISING_NEEDS_VALIDATION", "UNCONFIRMABLE_IN_HORIZON",
         "COST_FRAGILE", "REGIME_DEPENDENT", "DATA_LIMITED", "WEAK", "DEAD"]
groups = {}
for m in R["mechanisms"]:
    groups.setdefault(m["verdict"], []).append(m)
rows = ["| mécanisme | trk | n_raw | L1 | L2 | L3 | gross | net14 | net28 | t(L3) | CI95 | "
        "ex-best-yr | train/test | ETA (ans) | capacité $ | verdict |",
        "|" + "---|"*16]
for v in order:
    for m in groups.get(v, []):
        if "gross_bps" not in m:
            continue
        ci = m.get("bootstrap_ci95")
        ci = f"[{f(ci[0],1)}, {f(ci[1],1)}]" if ci else "–"
        tt = (f'{f(m.get("train_bps"),1)}/{f(m.get("test_bps"),1)}'
              if m.get("train_bps") is not None else "–")
        tag = ""
        if m.get("robustness_check_of"):
            tag = " ⟨robustesse⟩"
        elif m.get("tradable_series") is False:
            tag = " ⟨diagnostic⟩"
        rows.append(
            f'| {m["mechanism"]}{tag} | {m.get("track","")[0]} | {m["n_raw"]:,} | '
            f'{f(m.get("n_independent_L1_user_coin_day"),0)} | '
            f'{f(m.get("n_independent_L2_coin_day"),0)} | {m.get("n_independent_L3_day")} | '
            f'{f(m["gross_bps"],2)} | {f(m.get("net_bps"),2)} | {f(m.get("net_bps_stress28"),2)} | '
            f'{f(m.get("t_stat_declustered_L3day"),2)} | {ci} | {f(m.get("ex_best_year"),1)} | '
            f'{tt} | {f(m.get("eta_forward_confirmation_years"),2)} | '
            f'{f(m.get("capacity_usd_estimate"),0)} | **{v}** |')
annex = "\n".join(rows)

# ---------- table 3: the verdict tally ----------
rows = ["| verdict | mécanismes primaires | toutes lignes (contrôles + robustesse inclus) |",
        "|---|---|---|"]
for v in order:
    a = R["verdict_counts"].get(v, 0)
    b = R["verdict_counts_all_rows"].get(v, 0)
    if a or b or v == "VALIDATED_FOR_FORWARD":   # always show the 0 -- it is the headline
        mark = "**" if v == "VALIDATED_FOR_FORWARD" else ""
        rows.append(f"| `{v}` | {mark}{a}{mark} | {b} |")
rows.append(f'| **total** | **{R["n_primary_mechanisms"]}** | **{R["n_rows_total"]}** |')
counts = "\n".join(rows)

p = os.path.join(ROOT, "REPORT.md")
s = open(p).read()
B1, B2 = "<!--T8_TABLE-->", "<!--/T8_TABLE-->"
A1, A2 = "<!--ANNEX-->", "<!--/ANNEX-->"
C1, C2 = "<!--COUNTS-->", "<!--/COUNTS-->"
for beg, end, new in ((B1, B2, t8_table), (A1, A2, annex), (C1, C2, counts)):
    if beg in s and end in s:
        s = s[:s.index(beg)+len(beg)] + "\n" + new + "\n" + s[s.index(end):]
    else:
        raise SystemExit(f"marker {beg} not found in REPORT.md -- insert the markers first")
open(p, "w").write(s)
print("REPORT.md tables regenerated:", len(t8_table.splitlines())-2, "T8 rows,",
      len(annex.splitlines())-2, "annex rows,", len(counts.splitlines())-2, "verdict rows")
