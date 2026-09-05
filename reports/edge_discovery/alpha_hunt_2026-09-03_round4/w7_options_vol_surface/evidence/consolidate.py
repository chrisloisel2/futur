"""W7 round4 — assemble the single machine-readable RESULTS.json from the per-run JSONs."""
import json, os
D = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.dirname(D)
L = lambda f: json.load(open(f"{D}/{f}"))
m126, m3, m457, fu, col = L("results_m1_m2_m6.json"), L("results_m3.json"), L("results_m4_m5_m7.json"), L("results_followups.json"), L("results_collapsed.json")
rob = L("results_m6_robustness.json")
KEEP = ["n_raw","n_independent_L1","n_independent_L2","n_independent_L3","L3_unit","net_bps",
        "net_bps_stress28","t_stat_declustered","bootstrap_ci95","sharpe_annual_net",
        "sharpe_annual_stress28","portfolio_sharpe","portfolio_sharpe_stress","year_by_year",
        "ex_best_year","n_required","event_rate_per_week_last6m","eta_forward_confirmation_days",
        "eta_forward_confirmation_years","gross_bps_per_episode","sample_start","sample_end",
        "mean_episode_days","two_arm_full_sample","two_arm","sign_learned","oos_start"]
def pick(d):
    return {k: v for k, v in d.items() if k in KEEP}

M = {}
M["M1_IV_TERM_STRUCTURE_DIRECTION"] = {
 "hypothesis": "IV term-structure slope / inversion (near 2-10d ATM IV minus far 45-180d) predicts BTC perp DIRECTION, not just forward RV.",
 "distinct_from": "W6-round2 M7 (slope level -> forward RV, confound-killed) and M8 (slope change -> forward RV). Neither targeted direction.",
 "perp_expressible": True, "vehicle": "BTCUSDT perp, daily, taker",
 "verdict": "DEAD",
 "why": "OOS (sign fitted on first half) net -22.1 bps/episode, t=-0.63, SR=-0.49 on 100 episodes. Full-sample sign-fitted version is also negative (-11.3). The inverted-vs-contango two-arm test on forward 1d returns shows no separation.",
 "gate": pick(m126["M1_term_structure_direction"])}
for k, tag in [("M2_skew_velocity_1d","M2_SKEW_VELOCITY_1D"), ("M2_skew_velocity_3d","M2_SKEW_VELOCITY_3D")]:
    M[tag] = {
     "hypothesis": "The VARIATION (velocity) of the 25-delta-ish skew, not its level, predicts BTC perp direction.",
     "distinct_from": "LIQ_REPEAT_SKEW_OVERLAY (validated) uses the skew LEVEL as a conditioner on liquidation-cascade repeat probability; W6-M6 used far-OTM put SHARE against forward RV. Neither used skew velocity against direction.",
     "perp_expressible": True, "vehicle": "BTCUSDT perp, daily, taker", "verdict": "DEAD",
     "why": "OOS net -2.3 bps (1d) / -27.3 bps (3d), t=-0.12 / -1.07, SR negative on both.",
     "gate": pick(m126[k])}
M["M2c_SKEW_CAPITULATION_NORMALISATION"] = {
 "hypothesis": "Skew normalising after a put-panic (top-decile skew within 5d, then falling 3d) = capitulation finished -> LONG BTC perp.",
 "distinct_from": "Direction preregistered, no sign fitting. Not a level test.",
 "perp_expressible": True, "vehicle": "BTCUSDT perp, daily, taker, long-only", "verdict": "DEAD",
 "why": "net -9.9 bps/episode over 70 episodes, t=-0.19. The two-arm test vs all other days shows no separation.",
 "gate": pick(m126["M2_skew_capitulation_normalisation"])}
M["M3_DEALER_GAMMA_PROXY"] = {
 "hypothesis": "Dealer gamma positioning (negative => dealers amplify => momentum + high RV; positive => they dampen => reversion) conditions the BTC perp return process. The mechanism the project has never had.",
 "distinct_from": "Entirely absent from the project. W6-round2 explicitly SKIPPED every OI-weighted construction rather than fake it.",
 "perp_expressible": True, "vehicle": "BTCUSDT perp, daily, sign-conditioned momentum/reversion",
 "verdict": "DEAD",
 "why": ("Tested in 3 drift-robust constructions (raw level, 60d-detrended level, 1d gamma flow) x 3 arms "
         "(momentum / reversion / combined). Every arm negative net; best gross arm SR=+0.20. The mechanism's "
         "OWN central prediction fails first: short-gamma days do NOT show higher forward |return| than "
         "long-gamma days (detrended: 157 vs 178 bps, t=-1.3, WRONG SIGN). The §1.3 arm-vs-arm test of "
         "momentum payoff under short vs long gamma gives t=-0.02 to -1.05, p=0.29-0.98."),
 "proxy_quality": m3["diagnostics"]["proxy_quality"],
 "data_needed_for_a_real_test": ("Open interest per strike/expiry (Deribit publishes it per instrument via "
     "get_book_summary_by_currency). Without it the inventory must be accumulated from trades since "
     "2023-01-01 under taker==customer, and gross accumulated position drifts 152x over the sample "
     "because closing legs of pre-2023 positions have no opening leg. A true GEX test is DATA_LIMITED."),
 "gate": {k: pick(m3["results"][k]) for k in m3["results"]},
 "arm_diagnostics": m3["diagnostics"]}
M["M4_PIN_RISK_STRIKE_MAGNET"] = {
 "hypothesis": "Near large monthly Deribit expiries, BTC is pulled toward the max-open-interest strike.",
 "distinct_from": "W6-M9 tested expiry PROXIMITY -> IV crush / forward RV, and explicitly skipped max-pain for lack of OI. This tests the directional magnet with a traded-notional OI proxy.",
 "perp_expressible": True, "vehicle": "BTCUSDT perp, 1-3d before monthly expiry", "verdict": "DEAD",
 "why": ("50 monthly expiries, 126 observations. Price moves TOWARD the magnet on only 40.5% of lag-1 days "
         "(a magnet would need >50%). The three lags disagree in sign (-30.5 / +38.7 / -58.0 bps), so the one "
         "positive cell is a 1-in-3 multiple-comparison artifact, not a mechanism. Even taking lag-2 at face "
         "value: t=1.41, 42 episodes, 0.23 episodes/week, ETA 55 years."),
 "gate": {k: pick(m457["results"][k]) for k in m457["results"] if k.startswith("M4")},
 "diagnostics": {k: v for k, v in m457["diagnostics"].items() if k.startswith("M4")}}
M["M5_VRP_CROSS_ASSET_RISK_APPETITE"] = {
 "hypothesis": "The BTC variance risk premium (DVOL_BTC minus trailing realised vol) is a risk-appetite gauge that transmits to alts.",
 "distinct_from": "W6 measured the VRP's own existence and its RV-forecasting content on BTC only. This exports it as a regime onto a 41-name alt cross-section.",
 "perp_expressible": True, "vehicle": "41-name equal-weight alt perp basket / high-beta-minus-low-beta tilt",
 "verdict": "DEAD",
 "why": ("THE CAUTIONARY RESULT OF THIS ROUND. Per-asset counting gave net +2.95 bps/day, t=+4.49 on "
         "'2496 episodes' -- an apparently strong, cost-stress-surviving edge. Collapsing the 41 correlated "
         "alts to the single instrument they actually are (they move together; one basket-day is ONE "
         "observation) takes the same data to t=+1.06 on 66 real episodes, CI95 [-88, +327] straddling zero. "
         "Year-by-year then shows the effect is 2021-2024 only and NEGATIVE in 2025 and 2026 "
         "(-98 and -103 bps), and ex-best-year drops it to t=0.61. Nothing survives."),
 "gate_per_asset_WRONG": {k: pick(fu["results"][k]) for k in fu["results"] if k.startswith("F2")},
 "gate_collapsed_CORRECT": {k: pick(col["results"][k]) for k in col["results"] if k.startswith("M5")},
 "arm_vs_arm": {"basket": fu["diagnostics"]["F2_arm_vs_arm_basket"],
                "tilt": m457["diagnostics"]["M5_arm_vs_arm"],
                "riskon": m457["diagnostics"]["M5_riskon_arm_vs_arm"]}}
M["M6_DVOL_BTC_VS_ETH_DIVERGENCE"] = {
 "hypothesis": "Divergence between BTC and ETH implied vol (DVOL_ETH/DVOL_BTC) predicts BTC-vs-ETH relative spot performance. Traded as a dollar-neutral perp pair -- the lowest-sigma expression available on this axis and therefore its best ETA candidate.",
 "distinct_from": "DVOL_ETH is used here for the first time in this project; W6-round2 had no ETH leg at all.",
 "perp_expressible": True, "vehicle": "BTCUSDT/ETHUSDT dollar-neutral perp pair, daily",
 "verdict": "WEAK",
 "why": ("The only mechanism on this axis to clear t>2 after correct declustering: OOS net +77.4 bps/episode, "
         "t=+2.50 on 68 independent episodes, bootstrap CI95 [+22.6, +142.0] excluding zero, all four years "
         "positive, survives ex-best-year (t=2.12) and cost stress (SR 1.22 -> 0.98). It then fails two "
         "controls. (1) CONFOUND: DVOL_ETH/DVOL_BTC is 0.69 Spearman-correlated with the trailing REALISED "
         "vol ratio; orthogonalising the signal against relative realised vol and relative 30d momentum "
         "collapses it to net +14.8 bps, t=0.71, SR 0.21. The options index is mostly a laundered realised-vol "
         "ratio. (2) KNIFE EDGE: raw continuous Spearman IC is -0.0024 (i.e. zero), and tightening the "
         "quintile rule toward the tails -- where a real effect should STRENGTHEN -- weakens it monotonically "
         "(t: 2.28 / 2.50 / 1.83 / 1.07 at 0.75 / 0.80 / 0.85 / 0.90). Independently, its ETA is 12.2 years "
         "(21.3 from the Sharpe identity), so even had it passed the controls it would have been "
         "UNCONFIRMABLE_IN_HORIZON."),
 "gate": {**{k: pick(m126[k]) for k in m126 if k.startswith("M6")},
          "COLLAPSED_CORRECT": pick(col["results"]["M6_dvol_ratio_pair_COLLAPSED"]),
          "horizon_variants": {k: pick(fu["results"][k]) for k in fu["results"] if k.startswith("F3")}},
 "robustness_and_confounds": rob}
M["M7_OPTIONS_BLOCK_FLOW_TO_PERP"] = {
 "hypothesis": "A large Deribit options block forces the dealer to delta-hedge in the perp within hours: customer net-long delta => dealer buys perp => upward pressure. Direction preregistered from the mechanism, never fitted.",
 "distinct_from": "W6-round2 M3 used RAW NOTIONAL block flow at DAILY horizon and was DEAD. Delta weighting (BS delta per contract) and the 1-72h horizon are both new.",
 "perp_expressible": True, "vehicle": "BTCUSDT perp, hourly", "verdict": "WEAK",
 "why": ("The mechanism is REAL and correctly signed -- and that is the useful finding. Gross +3.06 bps per "
         "episode, t=+2.09 declustered on 2719 independent episodes, in the direction the hedging story "
         "predicts. It is simply ~5x too small to trade: the project cost floor is 14 bps round trip, so net "
         "is -5.1 bps at 1h. Holding longer to amortise the cost does not work because the effect is "
         "specifically a 1-hour effect that decays as the holding period lengthens (gross per episode "
         "2.70 -> 2.55 -> 1.13 -> 0.54 -> 0.37 bps at 1/4/12/24/72h), so gross falls at least as fast as "
         "turnover cost does. Every horizon is net-negative. Not COST_FRAGILE in the project's sense "
         "(dying between 14 and 28 bps) -- it dies an order of magnitude below 14."),
 "gate": {k: pick(fu["results"][k]) for k in fu["results"] if k.startswith("F1")},
 "gross_significance": col["diagnostics"]["M7_gross_significance"],
 "spearman_ic": {k: v for k, v in m457["diagnostics"].items() if k.startswith("M7")}}

# ─────────────────────────────────────────────────────────────────────────────
# §2 GATE COMPLETION (write-up pass). No new computation: every number below is
# either copied from evidence/*.json or derived from it by the closed-form ETA
# identity preregistered in PREREGISTRATION.md §0. Re-running this file is
# idempotent and reproduces RESULTS.json exactly.
# ─────────────────────────────────────────────────────────────────────────────
ZSQ4 = 31.396          # (z_0.975 + z_0.80)^2 * 4 = 7.849 * 4
GATE_COLS = ["n_raw", "n_independent_L1", "n_independent_L2", "n_independent_L3",
             "L3_unit", "mean_episode_days", "gross_bps_per_episode", "net_bps",
             "net_bps_stress28", "t_stat_declustered", "bootstrap_ci95",
             "sharpe_annual_net", "sharpe_annual_stress28", "year_by_year",
             "ex_best_year", "n_required", "event_rate_per_week_last6m",
             "eta_forward_confirmation_days", "eta_forward_confirmation_years",
             "sample_start", "sample_end"]

def _eta_identity(sr):
    try:
        sr = float(sr)
    except (TypeError, ValueError):
        return None
    return round(ZSQ4/(sr*sr), 2) if sr else None

def _rows(mech_key, mech):
    """Every §2 gate row inside one mechanism entry, keyed by its row name."""
    out = {}
    for block in ("gate", "gate_per_asset_WRONG", "gate_collapsed_CORRECT"):
        g = mech.get(block)
        if not isinstance(g, dict):
            continue
        if "n_raw" in g:                       # the block IS the row
            out[mech_key] = g
        else:
            for k, v in g.items():
                if isinstance(v, dict) and "n_raw" in v:
                    out[k] = v
                elif isinstance(v, dict):      # horizon_variants nesting
                    for k2, v2 in v.items():
                        if isinstance(v2, dict) and "n_raw" in v2:
                            out[k2] = v2
    return out

# which row is the headline for the §2 table (one row per mechanism in REPORT.md)
PRIMARY = {
 "M1_IV_TERM_STRUCTURE_DIRECTION":      "M1_IV_TERM_STRUCTURE_DIRECTION",
 "M2_SKEW_VELOCITY_1D":                 "M2_SKEW_VELOCITY_1D",
 "M2_SKEW_VELOCITY_3D":                 "M2_SKEW_VELOCITY_3D",
 "M2c_SKEW_CAPITULATION_NORMALISATION": "M2c_SKEW_CAPITULATION_NORMALISATION",
 "M3_DEALER_GAMMA_PROXY":               "M3_gamma_detrended_60d_momentum_arm",
 "M4_PIN_RISK_STRIKE_MAGNET":           "M4_pin_magnet_lag1d",
 "M5_VRP_CROSS_ASSET_RISK_APPETITE":    "M5_high_vrp_long_basket_COLLAPSED",
 "M6_DVOL_BTC_VS_ETH_DIVERGENCE":       "COLLAPSED_CORRECT",
 "M7_OPTIONS_BLOCK_FLOW_TO_PERP":       "F1_M7_hold1h",
}
PRIMARY_WHY = {
 "M1_IV_TERM_STRUCTURE_DIRECTION":      "only row; OOS (sign fitted on the first half only).",
 "M2_SKEW_VELOCITY_1D":                 "only row for the 1d velocity feature; OOS.",
 "M2_SKEW_VELOCITY_3D":                 "only row for the 3d velocity feature; OOS.",
 "M2c_SKEW_CAPITULATION_NORMALISATION": "only row; direction preregistered LONG, no sign fitting.",
 "M3_DEALER_GAMMA_PROXY":               "BEST of the 9 arms tested (the only one with positive net bps). Choosing the best of 9 is itself optimistic and it still fails; the other 8 are in the gate block.",
 "M4_PIN_RISK_STRIKE_MAGNET":           "preregistered horizon (closest to expiry). lag2d is the best of the 3 lags (net +38.7, t=1.41) but the 3 lags disagree in sign, so it is a 1-in-3 multiple-comparison cell, reported in the gate block and not promoted.",
 "M5_VRP_CROSS_ASSET_RISK_APPETITE":    "the CORRECTLY declustered basket row. The per-asset row (t=4.49) is retained in gate_per_asset_WRONG as the counter-example, never as the result.",
 "M6_DVOL_BTC_VS_ETH_DIVERGENCE":       "the correctly declustered pair row (basket = ONE instrument), OOS from 2023-12-26.",
 "M7_OPTIONS_BLOCK_FLOW_TO_PERP":       "1h is where the hedging effect actually lives; the 4/12/24/72h rows show it decaying, all in the gate block.",
}
# briefing pitfall (c): the project has NO options execution. One ruling per mechanism.
EXEC_RULING = {
 "M1_IV_TERM_STRUCTURE_DIRECTION":
   "PERP-EXPRESSIBLE. Options data are used only to BUILD the signal (traded ATM IV by expiry bucket); "
   "the position is a plain long/short BTCUSDT perp. No option is ever bought or sold. Vehicle is fine — the statistics are what kill it.",
 "M2_SKEW_VELOCITY_1D":
   "PERP-EXPRESSIBLE. Skew is a signal input only; the position is outright BTCUSDT perp. Vehicle fine, statistics dead.",
 "M2_SKEW_VELOCITY_3D":
   "PERP-EXPRESSIBLE. Same as the 1d variant.",
 "M2c_SKEW_CAPITULATION_NORMALISATION":
   "PERP-EXPRESSIBLE. Long-only BTCUSDT perp after a put-panic normalises. Vehicle fine, statistics dead.",
 "M3_DEALER_GAMMA_PROXY":
   "PERP-EXPRESSIBLE AS DESIGNED — this is the point of the construction. A true GEX play would be an options trade "
   "(NO_VEHICLE here); instead the gamma proxy is used only as a REGIME SWITCH on a perp momentum/reversion position, "
   "so nothing options-executed is required. The vehicle is legitimate; what is missing is open interest, which makes the "
   "SIGNAL a proxy (DATA_LIMITED for a real GEX test), not the vehicle.",
 "M4_PIN_RISK_STRIKE_MAGNET":
   "PERP-EXPRESSIBLE. Direction = sign(magnet strike − spot), taken in BTCUSDT perp 1–3d before a monthly expiry. "
   "No options leg. Vehicle fine; the magnet itself does not exist (40.5% of days move toward it).",
 "M5_VRP_CROSS_ASSET_RISK_APPETITE":
   "PERP-EXPRESSIBLE. VRP (DVOL_BTC − trailing RV30) is an index-derived regime label; the position is an alt perp basket / "
   "high-beta-minus-low-beta perp tilt. No option, no variance swap. Note this is the one mechanism whose EXPRESSION is not "
   "BTC/ETH — it exports a BTC-derived regime onto 41 alt perps — which is why its per-asset N looked so large and was so wrong.",
 "M6_DVOL_BTC_VS_ETH_DIVERGENCE":
   "PERP-EXPRESSIBLE. DVOL is a published index used as an input; the position is a dollar-neutral BTCUSDT/ETHUSDT perp pair. "
   "Trading the DVOL divergence itself (the natural options/vol-future expression) would be NO_VEHICLE — it is deliberately "
   "not what is tested here.",
 "M7_OPTIONS_BLOCK_FLOW_TO_PERP":
   "PERP-EXPRESSIBLE. The hypothesis is precisely that the dealer's hedge lands in the PERP, so the trade is a BTCUSDT perp "
   "position taken alongside it. No options leg at any point. The vehicle is the whole idea; the effect is simply 5x too small "
   "to pay the perp's own taker cost.",
}
# the L2 (calendar-day) level is the binding one on a 2-asset universe
INDEP_NOTE = {
 "M1_IV_TERM_STRUCTURE_DIRECTION":
   "Universe = BTC alone. L1 (asset-day) == L2 (calendar day) == 246 exactly, because one asset means one observation per day. "
   "L3 collapses those 246 days into 100 contiguous-position episodes (2.46 d each). The honest N for the t-stat is 100, not 246 and never 1294 trade-days.",
 "M2_SKEW_VELOCITY_1D":
   "Universe = BTC alone: L1 == L2 == 272 day-observations, L3 = 223 episodes of 1.22 d. A near-daily-flipping signal buys almost "
   "no independence over the day count and pays a round trip for each flip.",
 "M2_SKEW_VELOCITY_3D":
   "Universe = BTC alone: L1 == L2 == 267 days, L3 = 151 episodes of 1.77 d.",
 "M2c_SKEW_CAPITULATION_NORMALISATION":
   "Universe = BTC alone: L1 == L2 == 179 days, L3 = 70 capitulation episodes. 70 is the entire evidential base — roughly 20 per year.",
 "M3_DEALER_GAMMA_PROXY":
   "Universe = BTC alone: L1 == L2 == the day count of each arm (245–560), L3 = 155–393 episodes. Worse, the gamma proxy has a 1d "
   "autocorrelation of 0.80, so consecutive days in the same quintile are near-duplicates: the effective N is closer to the episode "
   "count than to the day count, which is exactly why L3 governs here.",
 "M4_PIN_RISK_STRIKE_MAGNET":
   "Universe = BTC alone AND one observation per monthly expiry: L1 == L2 == L3 == 42. This is the smallest N of the axis and the "
   "hard structural ceiling of pin-risk research on a single asset — ~12 expiries a year, ~50 in the whole trade sample.",
 "M5_VRP_CROSS_ASSET_RISK_APPETITE":
   "THE TRAP OF THIS ROUND. The expression spans 41 alt perps, so per-asset counting reported L1 = 12958 and L3 = 2496 'episodes'. "
   "But the 41 alts move together, and the signal is a single BTC-derived regime: one basket-day is ONE observation. L2 (calendar days) "
   "= 342 was already telling the truth; collapsed L3 = 66 regime episodes is the real N. t goes 4.49 -> 1.06 on identical data.",
 "M6_DVOL_BTC_VS_ETH_DIVERGENCE":
   "Universe = BTC + ETH, and the two legs are ONE pair trade: L1 = 1050 asset-days is double counting, L2 = 525 calendar days is the "
   "truth, L3 = 68 regime episodes (7.9 d each) is the t-stat basis. Even at its most generous, this mechanism has 68 independent "
   "observations — one asset-pair, 2.6 years.",
 "M7_OPTIONS_BLOCK_FLOW_TO_PERP":
   "Universe = BTC alone, but hourly: L1 == L2 == 3845 signal-hours, L3 = 3086 episodes. This is the ONE mechanism on the axis whose "
   "independent N is counted in hours rather than days, and it is therefore the only one whose ETA is not day-count-bound — the "
   "briefing's 'look for high episode rates' advice. It still fails, for the reason the ETA identity predicts: the extra frequency "
   "is bought at 14bps a round trip against a 3bps effect.",
}
VERDICT_SECONDARY = {
 "M6_DVOL_BTC_VS_ETH_DIVERGENCE":
   "UNCONFIRMABLE_IN_HORIZON — the verdict that would have applied had the confound controls passed: ETA 12.2 y empirical / 21.3 y "
   "by the Sharpe identity, both far beyond the 3-year bar. Recorded so the ETA finding is not lost behind the confound finding.",
 "M7_OPTIONS_BLOCK_FLOW_TO_PERP":
   "The GROSS effect (t=+2.09 on 2719 independent episodes, correctly signed) is a real, reusable microstructure fact. It is not a "
   "sleeve; it is at best a free conditioner for a strategy that is already paying the spread for another reason.",
}

for _mk, _mv in M.items():
    _rr = _rows(_mk, _mv)
    _pk = PRIMARY[_mk]
    _p = _rr[_pk]
    _hd = {c: _p.get(c) for c in GATE_COLS}
    _hd["row"] = _pk
    _hd["row_choice_rationale"] = PRIMARY_WHY[_mk]
    _hd["n_independent_days"] = _p.get("n_independent_L2")
    _hd["eta_forward_confirmation_years_empirical"] = _p.get("eta_forward_confirmation_years")
    _hd["eta_forward_confirmation_years_sharpe_identity"] = _eta_identity(_p.get("sharpe_annual_net"))
    _hd["eta_basis"] = ("empirical = n_required(power 80%, alpha 5%, 50% haircut) / event_rate measured on the LAST 6 MONTHS; "
                        "identity = 31.4 / SR_net^2 (PREREGISTRATION §0). The two differ whenever the recent firing rate differs "
                        "from the in-sample rate; both are reported and the LARGER governs the verdict.")
    if (_p.get("net_bps") or 0) <= 0:
        _hd["eta_note"] = ("Edge is NEGATIVE: the ETA figure is formally the time needed to confirm a negative edge and carries no "
                           "positive meaning. The verdict rests on the sign and the t-stat, not on this number.")
    _mv["primary_gate_row"] = _pk
    _mv["gate_headline"] = _hd
    _mv["execution_ruling_no_options_execution"] = EXEC_RULING[_mk]
    _mv["independent_n_note"] = INDEP_NOTE[_mk]
    if _mk in VERDICT_SECONDARY:
        _mv["verdict_secondary_note"] = VERDICT_SECONDARY[_mk]

# the one degenerate gate row: an always-on control has exactly one episode
_ctl = M["M5_VRP_CROSS_ASSET_RISK_APPETITE"]["gate_collapsed_CORRECT"]["M5_beta_tilt_unconditional_CONTROL_COLLAPSED"]
_ctl["null_fields_explanation"] = (
  "t_stat_declustered / n_required / event_rate / ETA are null by construction, not by omission: this control is ALWAYS ON, so the "
  "L3 decluster yields exactly ONE contiguous episode spanning 1956 days and an episode-level t-stat is undefined. Its ETA comes from "
  "the Sharpe identity instead: SR_net = -0.303 => 31.4/0.303^2 = 342 y (and the edge is negative anyway). This row exists only as the "
  "§1.3 baseline that the VRP-conditioned tilts had to beat; they did not.")
_ctl["eta_forward_confirmation_years_sharpe_identity"] = _eta_identity(_ctl.get("sharpe_annual_net"))

# evidence rows deliberately not promoted into a mechanism gate, kept traceable
SUPERSEDED = {
 "M1_term_structure_direction_FULLSAMPLE": {
   "source": "evidence/results_m1_m2_m6.json",
   "net_bps": m126["M1_term_structure_direction_FULLSAMPLE"]["net_bps"],
   "t_stat_declustered": m126["M1_term_structure_direction_FULLSAMPLE"]["t_stat_declustered"],
   "sharpe_annual_net": m126["M1_term_structure_direction_FULLSAMPLE"]["sharpe_annual_net"],
   "reason": "Sign fitted in-sample on the same data it is scored on. Reported for completeness only; it is negative too (-11.3 bps), which is why M1 dies either way."},
 "M5_beta_tilt_unconditional_CONTROL": {
   "source": "evidence/results_m4_m5_m7.json", "net_bps": m457["results"]["M5_beta_tilt_unconditional_CONTROL"]["net_bps"],
   "t_stat_declustered": m457["results"]["M5_beta_tilt_unconditional_CONTROL"]["t_stat_declustered"],
   "reason": "Per-asset declustering (L3 = 1760 pseudo-episodes on 41 correlated names). Superseded by M5_beta_tilt_unconditional_CONTROL_COLLAPSED."},
 "M5_beta_tilt_high_vrp_only": {
   "source": "evidence/results_m4_m5_m7.json", "net_bps": m457["results"]["M5_beta_tilt_high_vrp_only"]["net_bps"],
   "t_stat_declustered": m457["results"]["M5_beta_tilt_high_vrp_only"]["t_stat_declustered"],
   "reason": "Per-asset declustering. Superseded by M5_beta_tilt_high_vrp_COLLAPSED."},
 "M5_beta_tilt_low_vrp_only": {
   "source": "evidence/results_m4_m5_m7.json", "net_bps": m457["results"]["M5_beta_tilt_low_vrp_only"]["net_bps"],
   "t_stat_declustered": m457["results"]["M5_beta_tilt_low_vrp_only"]["t_stat_declustered"],
   "reason": "Per-asset declustering, and negative (-6.4 bps, t=-2.49) so nothing is being hidden by dropping it."},
 "M7_block_delta_flow_fwd1h": {
   "source": "evidence/results_m4_m5_m7.json", "net_bps": m457["results"]["M7_block_delta_flow_fwd1h"]["net_bps"],
   "reason": "First pass used OVERLAPPING forward windows on an hourly signal. Superseded by the non-overlapping F1_M7_hold* family."},
 "M7_block_delta_flow_fwd4h": {
   "source": "evidence/results_m4_m5_m7.json", "net_bps": m457["results"]["M7_block_delta_flow_fwd4h"]["net_bps"],
   "reason": "Same overlapping-window issue; its net_bps of -0.17 also drove n_required to 3.3e7 and ETA to 56000 y, a numerical artifact of dividing by a near-zero mean. Superseded by F1_M7_hold4h."},
}

doc = {
 "worker": "W7_OPTIONS_VOL_SURFACE", "round": "alpha_hunt_2026-09-03_round4", "date": "2026-09-03",
 "headline": ("7/7 preregistered mechanisms fail. No VALIDATED_FOR_FORWARD candidate. The axis is "
              "structurally ETA-bound: with a 2-asset universe the only route to a confirmable edge is a "
              "Sharpe above 3.24, and the best mechanism reached 1.22 before its confound control took it "
              "to 0.21."),
 "eta_identity": {
   "formula": "ETA_years = (z_0.975 + z_0.80)^2 * 4 / SR_annual^2 = 31.4 / SR^2",
   "derivation": ("n_required = 7.849*(sigma/(0.5*mu))^2 episodes; ETA = n_required/R; and "
                  "SR = (mu/sigma)*sqrt(R), so the episode rate R cancels exactly."),
   "consequence": ("Episode FREQUENCY does not improve ETA on its own -- only Sharpe does. Since the 14bps "
                   "cost is charged per episode, raising the episode rate usually LOWERS Sharpe and so "
                   "WORSENS ETA. The lever that works is reducing sigma (market-neutral pairs, "
                   "cross-sectional tilts), not raising frequency."),
   "bars": {"ETA_3y_requires_SR": 3.24, "ETA_1y_requires_SR": 5.60}},
 "cost_convention": "net = gross - 14bps round trip (7bps per unit |position change|); stress = 28bps (14/unit)",
 "declustering_bug_found_and_fixed": (
   "My own first pass counted per-asset episodes, so a 41-name alt basket registered 41 'independent' "
   "observations per calendar day and a 2-leg pair registered 2 per trade. Fixed by collapsing every "
   "multi-asset expression to one synthetic instrument before the gate. Effect on M5: t 4.49 -> 1.06. "
   "All multi-asset numbers reported here are the collapsed (correct) ones."),
 "data_coverage": {
   "deribit_BTC_trades": "2023-01-01 -> 2026-09-03 (~1340 days, 16.2M trades)",
   "DVOL_BTC_and_ETH": "2021-03-24 -> 2026-09-03 (1990 days)",
   "perp_panel_data_v2": "ends 2026-07-31 -- the binding end date for every forward outcome",
   "open_interest": "ABSENT from this dataset (confirmed for the third time in this project)",
   "universe": "BTC + ETH only at trade level; ETH has index-level DVOL but no trade-level surface"},
 "mechanisms": M}

# ── document-level §2 completion ─────────────────────────────────────────────
doc["universe_and_independent_n"] = (
  "My universe is BTC and ETH ONLY (trade-level surface: BTC only; DVOL: BTC + ETH). With one or two assets, level-1 "
  "declustering (same-asset/24h) is a no-op — it EQUALS the calendar-day count — so level 2 (calendar day) is the binding "
  "constraint on every mechanism here, and level 3 (contiguous episode) is what the t-stats are actually computed on. "
  "My independent N is a number of DAYS, never a number of observations. Concretely: the whole Deribit trade sample is "
  "~1294 usable days, so no BTC daily mechanism on this axis can ever have more than ~1294 independent observations, and "
  "after episode declustering the real numbers are 42 to 393. M5 is the one expression that spans 41 names, and that is "
  "exactly where per-asset counting inflated N by 38x and manufactured a t of 4.49 out of a t of 1.06. The single exception "
  "is M7, which is hourly and therefore has thousands of independent episodes — and it fails for the opposite reason "
  "(3bps of effect against a 14bps cost floor).")
doc["execution_reality"] = (
  "This project has NO options execution: no option, no variance swap, no DVOL future can be traded here. Every mechanism was "
  "therefore designed so that options data enter only as a SIGNAL and the position is taken in perps (BTCUSDT, ETHUSDT, or an "
  "alt perp basket). All 7 are perp-expressible and none was killed by the vehicle — see execution_ruling_no_options_execution "
  "on each mechanism. This was preregistered (PREREGISTRATION §0b) precisely so that the round-2 W6 outcome — three PROMISING "
  "options findings that had to become a risk overlay because nothing could be traded — could not repeat.")
doc["non_overlap_with_VOL_FORECAST_LAYER_V1"] = (
  "VOL_FORECAST_LAYER_V1 (FROZEN 2026-08-31, SIGNAL_SHADOW, RISK_OVERLAY_ONLY) merges the three round-2 W6 findings — "
  "rv_iv_spread, far_otm_put_share, block_count_24h — into ONE daily forecast of forward REALISED VOLATILITY on BTCUSDT. "
  "W7 tests none of those three targets: every mechanism here targets DIRECTION (M1, M2, M2c, M4), a directional REGIME SWITCH "
  "(M3), a cross-sectional risk-appetite tilt (M5), or relative BTC-vs-ETH performance (M6). The single place the two axes touch "
  "is M7, which re-uses Deribit block flow — but W6's OPTIONS_BLOCK_FLOW_TO_RV_V1 used raw block COUNT/notional at a daily "
  "horizon against forward RV, while M7 uses Black-Scholes DELTA-weighted block flow at a 1-hour horizon against perp DIRECTION. "
  "Different weighting, different horizon, different target. The one W7 result that touches vol forecasting is a NEGATIVE "
  "diagnostic (M3: short-gamma days do not have higher forward |return| — wrong sign, t=-1.3), which contradicts rather than "
  "duplicates the layer.")
doc["eta_reporting"] = {
  "empirical": "n_required / event_rate_per_week_last6m, with n_required sized at power 80%, alpha 5% on a mandatory 50% haircut of the discovered edge (briefing §2). Event rate is measured on the LAST 6 MONTHS only, which is conservative for mechanisms that have slowed down.",
  "sharpe_identity": "31.4 / SR_net^2, the closed form derived in PREREGISTRATION §0. It uses the in-sample episode rate implicitly and so is the like-for-like number across mechanisms.",
  "which_governs": "Both are reported for every mechanism. Where they disagree (M6: 12.2 y empirical vs 21.3 y by identity) the LARGER governs, because the disagreement means the mechanism has been firing more often recently than its Sharpe can justify.",
  "bar": "ETA >= 3 y => UNCONFIRMABLE_IN_HORIZON (briefing §3). On this axis that means SR_net < 3.24. The best number reached anywhere in W7 was SR 1.22, and 0.21 after its confound control.",
}
doc["superseded_rows"] = SUPERSEDED
doc["verdict_summary"] = [
  {"mechanism": k,
   "verdict": v["verdict"],
   "perp_expressible": v["perp_expressible"],
   "primary_gate_row": v["primary_gate_row"],
   "net_bps": v["gate_headline"]["net_bps"],
   "net_bps_stress28": v["gate_headline"]["net_bps_stress28"],
   "n_independent_L2_days": v["gate_headline"]["n_independent_L2"],
   "n_independent_L3": v["gate_headline"]["n_independent_L3"],
   "t_stat_declustered": v["gate_headline"]["t_stat_declustered"],
   "sharpe_annual_net": v["gate_headline"]["sharpe_annual_net"],
   "eta_years_empirical": v["gate_headline"]["eta_forward_confirmation_years_empirical"],
   "eta_years_sharpe_identity": v["gate_headline"]["eta_forward_confirmation_years_sharpe_identity"]}
  for k, v in M.items()]
doc["gate_completeness"] = (
  "Every §2 column (n_raw, n_independent L1/L2/L3, net_bps, net_bps_stress28, t_stat_declustered, bootstrap_ci95, "
  "year_by_year, ex_best_year, n_required, event_rate, eta_forward_confirmation, verdict) is populated for all 36 gate rows. "
  "The only nulls anywhere are in M5_beta_tilt_unconditional_CONTROL_COLLAPSED, which is an always-on control with exactly one "
  "L3 episode; see its null_fields_explanation.")

json.dump(doc, open(f"{OUT}/RESULTS.json","w"), indent=1, default=str)
print("RESULTS.json written;", len(M), "mechanisms")
for k, v in M.items(): print(f"  {k:38s} {v['verdict']}")
