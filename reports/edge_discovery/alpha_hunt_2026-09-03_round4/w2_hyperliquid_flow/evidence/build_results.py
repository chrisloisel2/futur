#!/usr/bin/env python
"""W2 -- consolidate every gated mechanism into RESULTS.json with a uniform verdict rule.

The verdict ladder is applied identically to every mechanism, in this order (briefing s3).
The first failed criterion names the verdict; every failed criterion is also listed in
`gate_failures`, so a reader can see exactly why.

  DATA_LIMITED              n_independent_L3 < 60  (fewer than ~2 months of independent days)
  DEAD                      gross <= 0, or |t_L3| < 1.5
  WEAK                      t_L3 in [1.5, 3) and/or net_bps <= 0  (real-ish but under cost)
  COST_FRAGILE              net_bps > 0 but net_bps_stress28 <= 0
  REGIME_DEPENDENT          ex_best_year <= 0  (the edge is one year)
  UNCONFIRMABLE_IN_HORIZON  everything above passes but eta_forward_confirmation > 3 years
  VALIDATED_FOR_FORWARD     passes all of it

Re-executable: .venv/bin/python evidence/build_results.py
"""
import os, json, numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def load(fn):
    p = os.path.join(HERE, fn)
    return json.load(open(p)) if os.path.exists(p) else {}


def verdict(m):
    f = []
    n3 = m.get("n_independent_L3_day")
    g = m.get("gross_bps"); t = m.get("t_stat_declustered_L3day")
    net = m.get("net_bps"); st = m.get("net_bps_stress28")
    exb = m.get("ex_best_year"); eta = m.get("eta_forward_confirmation_years")
    if n3 is None or g is None:
        return "DATA_LIMITED", ["no gate computed"]
    if n3 < 60:
        f.append(f"n_independent_L3={n3} < 60")
        return "DATA_LIMITED", f
    if g <= 0 or t is None or abs(t) < 1.5:
        f.append(f"gross={g} bps, t_L3={t}")
        return "DEAD", f
    if abs(t) < 3 or (net is not None and net <= 0):
        if abs(t) < 3: f.append(f"t_L3={t} < 3")
        if net is not None and net <= 0: f.append(f"net_bps={net} <= 0 (under the 14bps cost)")
        return "WEAK", f
    if st is not None and st <= 0:
        f.append(f"net_bps_stress28={st} <= 0")
        return "COST_FRAGILE", f
    if exb is not None and exb <= 0:
        f.append(f"ex_best_year={exb} <= 0")
        return "REGIME_DEPENDENT", f
    if eta is None or eta > 3:
        f.append(f"eta_forward_confirmation_years={eta} > 3")
        return "UNCONFIRMABLE_IN_HORIZON", f
    return "VALIDATED_FOR_FORWARD", f


# ---- explicit, documented overrides.  The mechanical ladder above only knows "arm vs zero";
# briefing s1.3 also requires "arm A - arm B on the same population", and PREREGISTRATION.md
# contains one disqualification decided in advance.  Both are applied here by name so that a
# reader can audit exactly which mechanism was moved and why.
OVERRIDES = {
 "T6 informed-user cohort (TRAIN-scored) @24h TEST-only": (
   "DATA_LIMITED",
   ["PREREGISTRATION.md section 0 disqualified this test IN ADVANCE: the 2,215-wallet list is "
    "harvested from the 2026-07/08 live trade tape, so every wallet in it survived to July 2026. "
    "A wallet-SKILL study is exactly the case that bias invalidates, and T6 is a wallet-skill "
    "study. The gate numbers (gross 44.81, net 30.81, stress 16.81, t_L3 4.20, ETA 1.62 y) are "
    "reported for completeness and must NOT be read as an edge.",
    "arm contrast top-cohort minus bottom-cohort is +39.61 bps at t_daypaired = 0.70: the "
    "briefing s1.3 comparison is underpowered, not passed",
    "the t-7d placebo-adjusted twin falls to +20.47 bps at t_L3 = 1.34, CI [-16.43, 59.75]",
    "WHAT WOULD BE NEEDED: a point-in-time wallet universe (wallets known to be active as of "
    "each historical date), not a universe reconstructed from a later snapshot"]),
 "T4_FLOW_IMBALANCE_XS_LS_4h_v1_LEAKY": (
   "DEAD",
   ["NOT A MECHANISM. This row is the reproduction of the PIT violation, kept so the leak is "
    "auditable. Its signal sums the scheduled-flow matrix forward over [t, t+H), and a median "
    "74.4% of that notional belongs to TWAPs that do not exist yet at t. Any number on this row "
    "is unattainable by construction.",
    "the PIT-legal form of the same idea is T4_FLOW_IMBALANCE_XS_LS_4h_v2b_CLEAN_RESIDUAL: "
    "gross 13.55 (from 26.98), IC 0.0198 (from 0.0433), net -0.45 bps"]),
 "HLTWAP_COINQUIET_1WK_AGO_S1440 [REFIT]": (
   "UNCONFIRMABLE_IN_HORIZON",
   ["eta_forward_confirmation_years = 5.28 (best scheme tried: 6.01) > 3 -- briefing s2 makes "
    "this decisive regardless of bps",
    "LISTING-AGE CONTROL (briefing s8.10): the trigger is 2.2x over-represented in coins less "
    "than 30 days old on Binance (9.3% of trigger events vs a 4.2% baseline), and 1% of them "
    "fire on the symbol's very first bars. Applying the project's own ListingAgeGate "
    "(age >= 30 d) takes gross from 29.72 to 26.13 and, decisively, takes net_bps_stress28 "
    "from +1.72 to -1.87. Under the project's own listing policy this mechanism is "
    "COST_FRAGILE: its survival of the 28 bps stress was carried by newly listed coins.",
    "arm contrast against the rest of the TWAP population is +19.82 bps at t_daypaired = 1.16, "
    "so the trigger is not demonstrably better than the population it selects from",
    "the t-7d placebo-adjusted twin is +21.02 bps with CI [-0.89, 41.33], which includes zero",
    "REFIT: this trigger is not in PREREGISTRATION.md; it was found while auditing the placebo",
    "equal-weighting across coins (the natural portfolio form) cuts the daily mean from 16.46 "
    "to 9.89 bps, i.e. under the 14 bps cost"]),
}

mechs = []
srcs = [("event_gate_results_v2.json", "mechanisms", "A_binance_executed"),
        ("flow_leak_diagnostic.json", "mechanisms", "A_binance_executed"),
        ("quietweekago_gate_results.json", "mechanisms", "A_binance_executed"),
        ("firsttouch_gate_results.json", "mechanisms", "A_binance_executed"),
        ("trackb_gate_results.json", "mechanisms", "B_hyperliquid_native"),
        ("listingage_check.json", "gates", "A_binance_executed")]
for fn, key, track in srcs:
    for m in load(fn).get(key, []):
        if "gross_bps" not in m:
            continue
        m.setdefault("track", track)
        m["source_evidence"] = fn
        if fn == "listingage_check.json":
            # not independent findings: these are the same mechanism re-gated under successive
            # listing-age floors, to test whether the edge is a newly-listed-coin artefact.
            m["robustness_check_of"] = "HLTWAP_COINQUIET_1WK_AGO_S1440 [REFIT]"
        v, why = verdict(m)
        if m["mechanism"] in OVERRIDES:
            ov, reasons = OVERRIDES[m["mechanism"]]
            m["verdict_before_override"] = v
            v, why = ov, reasons + why
            m["override_applied"] = True
        m["verdict"] = v
        m["gate_failures"] = why
        # placebo-adjusted rows are CONTROLS, not order flow. "signal minus placebo" is not a
        # series anyone can trade; its only job is to test whether the raw edge is specific to
        # the event or is the coin's persistent drift. Flagged so no reader mistakes a
        # placebo-adjusted bps for a tradable one.
        if "PLACEBOADJ" in m["mechanism"]:
            m["tradable_series"] = False
            m["diagnostic_role"] = ("control variate: raw edge minus the same symbol/direction "
                                    "7 days earlier. Tests event-specificity, cannot be traded.")
        if "PLACEBOADJ2SIDED" in m["mechanism"]:
            m["pit_status"] = "PIT_VIOLATING_BY_DESIGN"
            m["diagnostic_role"] += (" The 2-sided variant averages the t-7d and t+7d windows, "
                                     "so it reads the future on purpose. It is a diagnostic "
                                     "only and must never be read as an attainable edge.")
        mechs.append(m)

# ---- T8 is a MEASUREMENT, not a bps mechanism: carried with its own summary
t8 = load("leadlag_t8_results.json")
t8sum = {}
for s, pc in t8.get("per_symbol", {}).items():
    for clock, cl in pc.items():
        for v in ("hyperliquid", "okx"):
            if v in cl:
                t8sum[f"{s}|{clock}|{v}"] = {
                    "argmax_lag_ms": cl[v]["argmax_lag_ms"],
                    "argmax_corr": cl[v]["argmax_corr"],
                    "corr_at_lag0": cl[v]["corr_at_lag0"],
                    "reading": ("negative lag = Binance LEADS this venue"
                                if cl[v]["argmax_lag_ms"] < 0 else
                                "positive lag = this venue leads Binance" if cl[v]["argmax_lag_ms"] > 0
                                else "synchronous")}
best_trade = None
for s, pc in t8.get("per_symbol", {}).items():
    for clock, cl in pc.items():
        for h, r in cl.get("tradability_hl_move_then_binance", {}).items():
            if r.get("gross_bps") is not None:
                if best_trade is None or r["gross_bps"] > best_trade["gross_bps"]:
                    best_trade = dict(r, symbol=s, clock=clock, horizon_s=h)
hl_lags = [v["argmax_lag_ms"] for k, v in t8sum.items() if k.endswith("|hyperliquid")]
lag_lo, lag_hi = (abs(max(hl_lags)), abs(min(hl_lags))) if hl_lags else (None, None)
mechs.append({
    "mechanism": "T8 HL->BINANCE LEAD-LAG (measurement)",
    "track": "B_hyperliquid_native", "source_evidence": "leadlag_t8_results.json",
    "measurement_days": t8.get("days"),
    "argmax_lag_by_symbol_clock_venue": t8sum,
    "best_case_tradability_hl_move_then_binance": best_trade,
    "cost_convention": "one-leg 14/28 bps",
    "verdict": "DEAD",
    "gate_failures": [
        f"the measured lead runs the WRONG WAY: Binance leads Hyperliquid by {lag_lo}-{lag_hi} "
        f"ms (the argmax cross-correlation lag is negative on all {len(hl_lags)} "
        "symbol x clock combinations, without exception)",
        "OKX, carried as a control venue, is synchronous with Binance at 0 ms, which shows "
        "the negative HL lag is a property of HL and not of the measurement grid",
        f"best-case conditional edge is {best_trade['gross_bps'] if best_trade else None} bps "
        "gross versus a 14 bps round trip",
        f"even if the sign were favourable, a {lag_lo/1000:.1f}-{lag_hi/1000:.1f} s horizon is "
        "unreachable with this project's stack (5-min bars; the HL TWAP collector polls "
        "every ~76 s)"],
})

order = {"VALIDATED_FOR_FORWARD": 0, "PROMISING_NEEDS_VALIDATION": 1,
         "UNCONFIRMABLE_IN_HORIZON": 2, "COST_FRAGILE": 3, "REGIME_DEPENDENT": 4,
         "DATA_LIMITED": 5, "WEAK": 6, "DEAD": 7}
mechs.sort(key=lambda m: (order.get(m["verdict"], 9), -(m.get("gross_bps") or -1e9)))

out = {
 "worker": "W2_HYPERLIQUID_FLOW",
 "round": "alpha_hunt_2026-09-03_round4",
 "completed": "2026-09-05 (session resumed after an interruption on 2026-09-03)",
 "preregistration": "PREREGISTRATION.md (hypotheses T1-T11, fixed before any forward return)",
 "headline": (
   "No mechanism reaches VALIDATED_FOR_FORWARD. The Hyperliquid TWAP tape does carry a real, "
   "PIT-clean signed drift, but (a) most of it is a symbol x direction selection effect that a "
   "t-7d placebo removes, (b) the aggregate-flow formulation T4 was half leak, and (c) the one "
   "population that survives its own controls needs ~6 years of forward data to confirm."),
 "key_findings": [
   "T4 LEAK, found and fixed: summing the scheduled-flow matrix FORWARD over [t, t+H) counts "
   "TWAPs created inside the window. At a 4h horizon a median 74.4% of that notional does not "
   "yet exist at decision time. Correcting it cuts the cross-sectional L/S edge from 26.98 to "
   "13.55 bps gross and the IC from 0.0433 to 0.0198 -> net -0.45 bps, ETA 4.6 y.",
   "The t-7d placebo (same symbol, same clock, same direction) earns MORE than the event window "
   "itself (+17.95 vs +11.88 bps at 24h), so the headline TWAP drift is a coin-selection effect, "
   "not an event effect.",
   "But that placebo is itself contaminated for 90.2% of events (the same coin was already being "
   "TWAPed a week earlier), so it over-corrects. On the uncontaminated 9.9% the event edge "
   "survives: +29.72 bps gross, +15.72 net, +1.72 under the 28 bps stress, t_L3=3.79.",
   "That uncontaminated population is NOT 'first touch after a quiet period' (QUIET_24h/72h/7d "
   "measured just before t are all dead, t<1). It is an attention-onset population: the coin had "
   "no HL TWAP flow a week ago and has flow now.",
   "Its ETA is 5.3-6.6 years under every daily aggregation scheme tried, and equal-weighting "
   "across coins (the natural portfolio) cuts the daily mean from 16.5 to 9.9 bps -> under cost. "
   "Verdict UNCONFIRMABLE_IN_HORIZON.",
   "LISTING AGE was the sharpest control on that candidate. The trigger is 2.2x "
   "over-represented in coins under 30 days old (9.3% of its events vs a 4.2% baseline). "
   "Applying the project's own ListingAgeGate (>= 30 d) leaves the t-stat untouched "
   "(3.79 -> 3.77) but takes net_bps_stress28 from +1.72 to -1.87: the only thing that made "
   "the mechanism clear the 28 bps stress was newly listed coins.",
   "T8: Hyperliquid does NOT lead Binance. The cross-correlation argmax is at -500 to -800 ms, "
   "i.e. Binance leads HL, on every symbol and on both the venue clock and our receive clock. "
   "OKX (control venue) is synchronous at 0 ms.",
   "T9: the HL premium over its own multi-venue oracle does mean-revert, very significantly "
   "(+1.07 to +2.41 bps, t 4.6-14.2), and is 12-26x too small for a 28 bps two-leg round trip.",
   "T11: HL top-of-book imbalance predicts the next 5-60 min at +0.5 to +1.0 bps with t up to "
   "14.6 and an ETA of 0.24 y -- the best episode frequency in the whole round -- but the median "
   "top-of-book depth is 9,139 USD and the edge is 14-16x under cost. High frequency does not save "
   "an edge that is under cost.",
   "BUG FOUND IN MY OWN EARLIER PASS: run_event_gate.py reported the momentum-residualised "
   "series at gross=-0.00 bps and called it a result. OLS residuals are mean-zero by "
   "construction; the number was an artefact. Replaced by the intercept/slope and a quintile "
   "decomposition, which show the control was simply the wrong one."],
 "capacity_note": (
   "Track A executes on Binance USDM, so capacity is 0.5% of Binance quote volume over the "
   "holding window: 0.7-4.8 M USD per episode for the 24h mechanisms. Track B executes on "
   "Hyperliquid, and there the binding constraint is HL's own book: median top-of-book depth "
   "across the 12 HL majors is 9,139 USD."),
 "declustering": {
   "L1": "user x coin x calendar day (one wallet's flow on one coin on one day = ONE episode)",
   "L2": "coin x calendar day",
   "L3": "calendar day, all coins (primary; t_stat_declustered and the block bootstrap use it)"},
 "verdict_counts": {},
 "mechanisms": mechs,
}
# Counts are reported on the PRIMARY mechanisms only. Rows that are controls
# (placebo-adjusted series, which nobody can trade) or robustness re-gates of another
# mechanism are not independent findings and would inflate the tally.
out["verdict_counts_all_rows"] = {}
for m in mechs:
    out["verdict_counts_all_rows"][m["verdict"]] = out["verdict_counts_all_rows"].get(m["verdict"], 0)+1
    if m.get("robustness_check_of") or m.get("tradable_series") is False:
        m["counts_as_primary_mechanism"] = False
        continue
    m["counts_as_primary_mechanism"] = True
    out["verdict_counts"][m["verdict"]] = out["verdict_counts"].get(m["verdict"], 0)+1
out["n_rows_total"] = len(mechs)
out["n_primary_mechanisms"] = sum(1 for m in mechs if m.get("counts_as_primary_mechanism"))
out["counting_note"] = (
    "verdict_counts covers the primary mechanisms only. Placebo-adjusted rows are controls "
    "(not tradable series) and listing-age rows are re-gates of one mechanism; both are "
    "excluded from the tally and carried in verdict_counts_all_rows.")
json.dump(out, open(os.path.join(ROOT, "RESULTS.json"), "w"), indent=1, default=str)
print("PRIMARY:", json.dumps(out["verdict_counts"]))
print("ALL ROWS:", json.dumps(out["verdict_counts_all_rows"]))
print("n primary:", out["n_primary_mechanisms"], "/ n rows:", out["n_rows_total"])
for m in mechs:
    print(f'{m["verdict"]:26s} {m.get("gross_bps","-"):>8} {m.get("net_bps","-"):>8} '
          f'{m.get("net_bps_stress28","-"):>8} {str(m.get("eta_forward_confirmation_years","-")):>9}  {m["mechanism"][:78]}')
