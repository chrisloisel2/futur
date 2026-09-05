"""Assemble RESULTS.json (briefing §7.3) from every family's results_*.json.

Adds two fields the raw gate cannot know:

* `eta_forward_confirmation_years_datecorrected` — gate.py measures `event_rate` over
  2026-03-01..2026-09-01 (26.14 weeks), but `event_feature_panel` ENDS 2026-07-31, so the
  window is only 21.86 weeks of real data and every panel mechanism's event rate is
  understated by x1.196 (hence its ETA overstated by the same factor). The uncorrected
  figure is kept as the headline because it is the conservative one; the corrected figure is
  reported beside it so the artefact is visible rather than hidden.
* `family` / `status` — which pass a row belongs to, and whether it is VOID (superseded).
"""
import json, os, glob, datetime
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)

PANEL_WEEKS_NOMINAL = (pd.Timestamp("2026-09-01") - pd.Timestamp("2026-03-01")).days / 7.0
PANEL_WEEKS_REAL = (pd.Timestamp("2026-08-01") - pd.Timestamp("2026-03-01")).days / 7.0
CASCADE_END = None   # filled from the cascade results if present

SOURCES = [
    ("results_family_a.json", "A_FUNDING_CLOCK", "headline"),
    ("results_family_a_depth.json", "A_FUNDING_CLOCK_DEPTH", "sweep"),
    ("results_family_b.json", "B_SESSION_CLOCK", "headline"),
    ("results_family_b_depth.json", "B_SESSION_CLOCK_DEPTH", "superseded_by_family_b_final"),
    ("results_family_b_final.json", "B_SESSION_CLOCK_FINAL", "headline"),
    ("results_clock_map.json", "CLOCK_MAP_V1", "VOID_pandas_offset_rolling_bug"),
    ("results_clock_map_v2.json", "CLOCK_MAP_V2", "headline"),
    ("results_clock_map_depth.json", "CLOCK_MAP_DEPTH", "sweep"),
    ("results_family_cdef.json", "CDEF_WEEKEND_MONTHEND_EVENT", "headline"),
]

GATE_FIELDS = [
    "mechanism", "hypothesis", "n_raw", "n_independent_L1", "n_independent_L2",
    "n_independent_L3", "n_independent_L2_eff_diag", "gross_bps", "net_bps",
    "net_bps_stress28", "net_bps_2leg", "net_bps_2leg_stress56", "cost_legs", "sd_day_bps",
    "IR_day", "sharpe_ann_equiv", "t_stat_declustered", "t_stat_naive_WRONG",
    "clustering_inflation_factor", "bootstrap_ci95", "year_by_year", "best_year_dropped",
    "ex_best_year_gross_bps", "ex_best_year_t", "n_required_independent_days",
    "event_rate_per_week_last6m", "eta_forward_confirmation_days",
    "eta_forward_confirmation_years", "verdict", "verdict_reason", "family_maxt_crit", "notes",
]
EXTRA_KEEP = ["hour", "hold_hours", "buckets", "entry_gap_hours", "episodes_per_day",
              "hour_reversion", "hour_continuation", "selection_period", "train_gross_bps",
              "train_sign", "n_train_events", "test_gross_bps", "test_t_signfrozen",
              "test_gross_bps_signfrozen", "n_train", "n_test", "gross_bps_per_hour_held",
              "arm_only", "selection_period"]

# L1 of the two clock arms the combined strategy is made of (from CLOCK_MAP_V2, 8h/q5)
COMBINED_L1 = None
_cm = os.path.join(HERE, "results_clock_map_v2.json")
if os.path.exists(_cm):
    _d = json.load(open(_cm))
    _by = {r["mechanism"]: r.get("n_independent_L1") for r in _d.get("clock_map", [])}
    if _by.get("CLOCKMAP_h03") and _by.get("CLOCKMAP_h15"):
        COMBINED_L1 = int(_by["CLOCKMAP_h03"]) + int(_by["CLOCKMAP_h15"])

mechanisms, arms, missing = [], [], []
for fn, fam, status in SOURCES:
    p = os.path.join(HERE, fn)
    if not os.path.exists(p):
        missing.append(fn)
        continue
    d = json.load(open(p))
    if isinstance(d, dict):
        rows = d.get("mechanisms") or d.get("clock_map") or []
        for key in ("arm_vs_arm", "arm_vs_arm_fixed", "arm_vs_arm_vs_rest", "arm_vs_arm_pairs"):
            for a in d.get(key, []) or []:
                arms.append(dict(a, family=fam, source=fn, arm_test=key, status=status))
    else:
        rows = d
    for r in rows:
        e = {k: r.get(k) for k in GATE_FIELDS}
        for k in EXTRA_KEEP:
            if k in r:
                e[k] = r[k]
        e["family"] = fam
        e["source_file"] = fn
        e["status"] = status
        er = e.get("event_rate_per_week_last6m")
        eta = e.get("eta_forward_confirmation_years")
        if status.startswith("VOID"):
            # rename so a consumer looking a mechanism up by name cannot pick the void row:
            # CLOCKMAP_h* exists in both the void v1 and the live v2 family
            e["mechanism_original"] = e["mechanism"]
            e["mechanism"] = "VOID__" + str(e["mechanism"])
            e["verdict"] = "VOID"
            e["verdict_reason"] = ("computed from a signal destroyed by the pandas "
                                   "offset-rolling bug; superseded by CLOCK_MAP_V2")
        # --- structural gaps, explained rather than left blank or invented ---
        if e.get("n_independent_L1") is None and str(e["mechanism"]).startswith("CLK_COMBINED"):
            # the combined strategy is built from two day-level arm series; its L1 is the sum
            # of its two constituent arms' L1 (h03 and h15, 8h hold, quintiles = CLOCKMAP arms)
            e["n_independent_L1"] = COMBINED_L1
            e["n_independent_L1_note"] = ("sum of the L1 of the two constituent clock arms "
                                          "(h03 + h15, 8h hold, quintiles)")
        if not er:
            e["eta_forward_confirmation_note"] = (
                "undefined: this is a PERIOD-RESTRICTED diagnostic cell whose window ends "
                "before the 2026-03-01..2026-09-01 event-rate measurement window, so its "
                "recent event rate is 0 by construction. Read the ETA of the corresponding "
                "full-sample cell instead.")
        if er and eta:
            f = PANEL_WEEKS_NOMINAL / PANEL_WEEKS_REAL
            e["event_rate_per_week_datecorrected"] = round(er * f, 3)
            e["eta_forward_confirmation_years_datecorrected"] = round(eta / f, 2)
        mechanisms.append(e)

payload = {
    "worker": "W1_CALENDAR_CLOCK",
    "round": "alpha_hunt_2026-09-03_round4",
    "generated_utc": datetime.datetime.utcnow().isoformat() + "Z",
    "data": {
        "panel": "/home/qbee/futur-data-v2/data_v2/normalized/event_feature_panel (venue=binance)",
        "period_actual": "2020-01-01 .. 2026-07-31 (the panel ENDS 2026-07-31, not 2026-08-31 "
                         "as assumed in PREREGISTRATION §2)",
        "cascade_dataset": "/home/qbee/futur/data/events/liq_cascade_dataset.parquet",
        "universe": "30d median dollar volume >= $10M on strictly prior days, 30d listing "
                    "burn-in, >= 20 symbols in the cross-section",
        "timezone": "UTC asserted in every script (DuckDB renders tz-aware timestamps in "
                    "local time by default; on a clock axis that alone would shift every bucket)",
    },
    "cost_convention": {
        "one_leg": "net = gross - 14 (base), gross - 28 (stress)",
        "two_leg_basket": "net = gross - 28 (base), gross - 56 (stress) -- the verdict column "
                          "for every cross-sectional mechanism here",
        "turnover": "cost counted per executed round trip, not per signal (briefing §8.9)",
    },
    "decluster": {
        "L1": "distinct (symbol, UTC day) slots contributing",
        "L2": "calendar day, all symbols -- PRIMARY unit; every headline t-stat is computed "
              "on day-aggregated observations",
        "L3": "calendar week",
        "note": "a clock effect is maximally clustered: every symbol sees the same hour at "
                "the same instant, so L2 is the binding level, not the loosest one",
    },
    "void_artifacts": [
        {"file": "results_clock_map.json",
         "reason": "pandas 2.0.3 offset rolling ('6h') on a datetime64[us] index silently "
                   "degenerates into an expanding window; the 6h momentum signal was actually "
                   "the cumulative residual since the symbol's first eligible bar "
                   "(corr with the true signal 0.035). Proof: verify_b2_vs_clockmap.py.",
         "superseded_by": "results_clock_map_v2.json"},
        {"file": "results_family_b_depth.json (arm_vs_arm block only)",
         "reason": "each arm was indexed by its own session boundary (07:00/13:00/21:00/00:00), "
                   "so the paired join returned zero rows: every comparison read n_days=0, NaN. "
                   "The clock claim was never actually tested there.",
         "superseded_by": "results_family_b_final.json (arm_vs_arm_fixed)"},
    ],
    "headline_claims": [
        {"claim": "CLOCK_CONDITIONS_XS_AUTOCORRELATION_SIGN",
         "statement": "The sign of 6h cross-sectional autocorrelation depends on the UTC "
                      "entry hour: reversion h00-h06, continuation h13-h18, flat at the "
                      "handovers. Measured arm-vs-arm on paired calendar days.",
         "evidence": "results_clock_map_v2.json; h02 - h15 = +34.72bps, t=8.50, n=2188 "
                     "paired days, family-wise max-t crit 3.073; 13/24 contrasts significant; "
                     "sign stable TRAIN 2020-23 -> TEST 2024-26",
         "verdict": "COST_FRAGILE",
         "verdict_reason": "established as an effect, but max single arm 19.13bps gross vs "
                           "28bps 2-leg base cost; 0/24 arms clear cost",
         "answers_preregistered_hypothesis": "F1"},
        {"claim": "LATE_TO_ASIA_IS_A_BID_ASK_BOUNCE_ARTEFACT",
         "statement": "The strongest single number of the axis (+14.08bps, t=5.21) decays "
                      "with the entry gap and does not replicate out of sample.",
         "evidence": "gap sweep 14.08/8.46/6.59/8.67 bps at +0/1/2/3h; TEST 2024-26 t = "
                     "1.14/1.04/0.98/1.84 with the sign frozen on TRAIN 2020-23",
         "verdict": "DEAD", "verdict_reason": "bid-ask-bounce artefact, honest kill"},
        {"claim": "EU_TO_US_CONTINUATION_IS_REAL",
         "statement": "EU-session winners continue through the US session. Strengthens with "
                      "the entry gap and replicates out of sample.",
         "evidence": "-10.66 -> -14.44bps at +1h gap; TEST 2024-26 +14.20bps t=2.96 with the "
                     "sign frozen on TRAIN; era-stable -9.6/-11.3/-11.4bps; placebo -0.53 t=-0.26",
         "verdict": "COST_FRAGILE",
         "verdict_reason": "14.44bps gross vs 28bps 2-leg base cost",
         "sign_note": "SIGN_OPPOSITE_TO_HYPOTHESIS vs PREREG H_B2 (reversion); re-signed only "
                      "on a disjoint period per Amendment 1"},
        {"claim": "CASCADE_BOUNCE_IS_SESSION_CONDITIONAL",
         "statement": "The liquidation-cascade bounce is absent in the EU session (07:00-13:00 "
                      "UTC) and present in every other session.",
         "evidence": "fwd_8h US minus EU = +25.53bps, t=3.14, 1322 paired days; Bonferroni "
                     "crit for 18 arm tests = 2.99. Levels fwd_8h: ASIA +11.60 / EU -2.14 / "
                     "US +16.86 / LATE +15.58",
         "verdict_standalone_mechanical": "COST_FRAGILE",
         "verdict_as_conditioner": "PROMISING_NEEDS_VALIDATION",
         "verdict_reason": "as a standalone 1-leg trade the best session arm is +16.9bps gross "
                           "vs 14bps base / 28bps stress. As a SCREEN on the existing "
                           "LIQ_CASCADE_REPEAT_V1 shadow alpha it is a real conditioner. "
                           "MISSING GATE CELL: never tested as an overlay on the actual "
                           "LIQ_CASCADE_REPEAT_V1 position stream, only on the raw cascade "
                           "dataset. This verdict is assigned to an arm-CONTRAST object that "
                           "gate.py::auto_verdict does not score; it does not override any "
                           "mechanical verdict."},
        {"claim": "FUNDING_CLOCK_IS_ARBITRAGED_FLAT",
         "statement": "92 cells (17 headline + 75 depth). Max |gross| anywhere 7.52bps vs "
                      "28bps 2-leg cost; the mechanically certain part (t=55.4) is the funding "
                      "cashflow itself, an accounting identity.",
         "evidence": "results_family_a.json, results_family_a_depth.json; 0/75 depth cells "
                     "clear 28bps",
         "verdict": "COST_FRAGILE"},
        {"claim": "NO_WEEKEND_MONTHEND_OR_EXPIRY_CONDITIONING",
         "statement": "Month-end +8.69bps (t=0.35), quarterly-expiry week +10.63bps (t=0.45), "
                      "arm-vs-arm. Weekend +26.99bps (t=2.19) is below the family-wise "
                      "critical value 2.969. Friday->Monday reversal -12.10bps (t=-0.66).",
         "verdict": "DEAD"},
        {"claim": "NO_HOUR_OF_DAY_DRIFT_IN_THE_MARKET_FACTOR",
         "statement": "Pre-registered null H_B1 confirmed. Max |t| 2.66 over 24 buckets; "
                      "ex_best_year is negative for 20 of the 24 hours, so the raw drift is a "
                      "2021 artefact.",
         "verdict": "DEAD"},
    ],
    "bugs_found": [
        {"id": "PANDAS_OFFSET_ROLLING_ON_MICROSECOND_INDEX",
         "severity": "voids 24 mechanisms",
         "detail": "pandas 2.0.3: df.rolling('6h') on a datetime64[us] index silently becomes "
                   "an EXPANDING window. DuckDB .df() returns [us]. Proof: "
                   "roll6.iloc[37] == series.iloc[:38].sum() exactly; corr with the true 6h "
                   "signal 0.035. Caught only because CLOCKMAP_h13 and B2_EU_to_US are the "
                   "same trade by two code paths and disagreed (+0.80 vs -10.66bps).",
         "fix": "evidence/clock_lib.py::resid_roll_hours (integer window + contiguity guard) "
                "with assert_roll_ok() cross-checking a ns-index reference",
         "project_wide_risk": "any DuckDB->pandas path in this repo that uses an offset "
                              "rolling window is exposed"},
        {"id": "ARM_VS_ARM_JOIN_KEY",
         "severity": "the axis's central claim was never tested",
         "detail": "family_b_depth.py indexed each session arm by its own boundary instant, so "
                   "the paired concat overlapped nowhere; every comparison printed "
                   "n_days=0 / NaN and it was not noticed.",
         "fix": "family_b_armfix.py indexes every arm on its originating calendar day and "
                "reports contrasts both raw and per hour held"},
    ],
    "n_mechanisms": len(mechanisms),
    "mechanisms": mechanisms,
    "arm_vs_arm": arms,
}
json.dump(payload, open(os.path.join(OUT, "RESULTS.json"), "w"), indent=1, default=str)
print("RESULTS.json written:", len(mechanisms), "mechanisms,", len(arms), "arm-vs-arm rows")
if missing:
    print("MISSING (not yet produced):", missing)

df = pd.DataFrame(mechanisms)
live = df[~df["status"].str.startswith("VOID") & ~df["status"].str.startswith("superseded")]
print("\nverdict counts (live rows only):")
print(live["verdict"].value_counts().to_string())
print("\nrows better than WEAK:")
better = live[~live["verdict"].isin(["WEAK", "DEAD", "DATA_LIMITED", "VOID"])]
cols = ["mechanism", "family", "n_independent_L2", "n_independent_L3", "gross_bps",
        "net_bps_2leg", "net_bps_2leg_stress56", "t_stat_declustered",
        "ex_best_year_gross_bps", "eta_forward_confirmation_years",
        "eta_forward_confirmation_years_datecorrected", "verdict"]
pd.set_option("display.width", 260); pd.set_option("display.max_rows", 200)
print(better[[c for c in cols if c in better.columns]].sort_values("gross_bps", key=abs,
      ascending=False).to_string(index=False))
