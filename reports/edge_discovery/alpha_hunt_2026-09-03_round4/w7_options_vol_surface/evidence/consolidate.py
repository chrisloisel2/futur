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
json.dump(doc, open(f"{OUT}/RESULTS.json","w"), indent=1, default=str)
print("RESULTS.json written;", len(M), "mechanisms")
for k, v in M.items(): print(f"  {k:38s} {v['verdict']}")
