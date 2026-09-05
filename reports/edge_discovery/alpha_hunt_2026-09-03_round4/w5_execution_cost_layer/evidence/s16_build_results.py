"""W5/s16 - assemble RESULTS.json (machine-readable) and copy the small result JSON/CSVs into
evidence/. Nothing large is copied: the scratch parquets stay in scratch."""
import os, json, shutil, glob
import pandas as pd

S = os.environ["W5_SCRATCH"]
OUT = "/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w5_execution_cost_layer"
EV = f"{OUT}/evidence"


def jload(p, default=None):
    try:
        return json.load(open(p))
    except Exception:
        return default


res = {
 "worker": "W5_EXECUTION_COST_LAYER",
 "round": "alpha_hunt_2026-09-03_round4",
 "date_completed": "2026-09-05",
 "framing": ("This worker measures the COST FLOOR of every mechanism, not a directional signal. "
             "The deliverable of record is the retrospective re-judgement of rounds 1-3 under a "
             "measured cost model replacing the flat net_bps = gross_bps - 14."),
 "headline": {
   "cost_taker_roundtrip_bps": "10.0 + 1.00 * spread_bps   (R2=1.000, identity given the spread)",
   "cost_maker_roundtrip_bps": "8.2 + 1.22 * spread_bps    (R2=0.982, post-only TTL 600s, "
                               "must-trade policy, 1 bps/side simulator haircut included)",
   "adverse_selection_60s_bps": "0.88 + 1.00 * spread_bps  (R2=0.989)",
   "interpretation": ("A maker fill gives back exactly the spread it captured, plus 0.9 bps. "
                      "Maker execution is worth a flat ~2 bps round-trip, and only if the "
                      "strategy can wait >= 60 s. The '5bps taker -> 2bps maker therefore 6bps "
                      "saved' arithmetic overstates the gain by 3x."),
   "convention_error": {"T1_MAJOR": -5.4, "T2_LIQUID_ALT": -4.6, "T3_MID_ALT": -2.3,
                        "T4_WIDE_ALT": +1.3,
                        "note": "bps round-trip vs the -14 convention; NEGATIVE = the convention "
                                "is too pessimistic, POSITIVE = too generous"},
   "resurrection_verdict": ("NO. Under the preregistered resurrection rule (survive the measured "
                            "cost AND 1.5x it), the resurrection band is 1.1 bps wide and exists "
                            "only on BTC/ETH/BNB. The round 1-3 graveyard between +5 and +14 "
                            "gross bps stays dead."),
   "worst_news": ("Posting on the side the market has ALREADY moved toward, during a shock, costs "
                  "+1.95 bps RT at the 99th percentile and +10.39 at the 99.9th (t=8.1 / 9.3, "
                  "declustered on symbol-days). The spread does NOT widen during shocks - it "
                  "tightens (x0.88) - so the penalty is invisible to any spread-based cost model.")
 },
 "hypotheses": {
   "H1_probe_instrument_audit": {"verdict": "CONFOUNDED_BY_CONSTRUCTION",
     "preset_thresholds": "pooled slope on spread in [-1.2,-0.3] AND cross-symbol Spearman>0.7",
     "measured": {"pooled_slope": -1.214, "spearman_markout_vs_floor": 0.982,
                  "ols_r2_cross_symbol": 0.983, "median_ratio_observed_over_floor": 1.333},
     "conclusion": "~75% of the probe's headline adverse selection is the instrument."},
   "H2_queue_aware_maker": {"verdict": "MAKER_IS_A_REAL_BUT_SMALL_COST_LAYER",
     "preset_threshold_material_difference_bps": 1.0,
     "measured_pooled_bias_bps": 0.437,
     "measured_bias_on_widest_overlap_symbol_SOLUSDT_bps": 1.135,
     "preset_threshold_usable_cost_maker_oneway_lt": 5.0,
     "measured_cost_maker_oneway_bps": 3.337,
     "note": "pooled bias misses the 1.0 threshold, the wide-spread symbol clears it; the bias "
             "scales with the spread, which is what makes the s10 bridge possible."},
   "H3_capacity": {"verdict": "NOT_BINDING_AT_CURRENT_SIZE",
     "slippage_free_clip_usd": {"binance_BTCUSDT": 205856, "binance_ETHUSDT": 85985,
                                "binance_SOLUSDT": 45518, "okx_BTCUSDT": 145163,
                                "hyperliquid_SOLUSDT": 20100},
     "project_clip_usd_estimate": "1k-10k (per_alpha_budget_fraction=0.05 on 200k books)",
     "conclusion": "the 2 bps slippage half of the -14 convention has no empirical basis at the "
                   "sizes this project trades; it becomes real above ~100k/clip and during shocks",
     "capacity_under_shock_ratio": 0.61},
   "H4_urgency": {"verdict": "URGENCY_PENALTY_IS_REAL_MAKER_ONLY_AND_DIRECTIONAL",
     "SPREAD_SHOCK": "REJECTED (measured multiplier 1.000, CI95 [0.990,1.004]; preset was >1.5x)",
     "MAKER_UNUSABLE_ON_EVENTS": "REJECTED (fill probability RISES 0.44 -> 0.83)",
     "URGENCY_PENALTY_MATERIAL": "CONFIRMED at the 99.9th pct momentum arm (+10.39 RT > 5 preset), "
                                 "REJECTED at the 99th (+1.95 RT)",
     "method_bug_found_and_fixed": "conditioning on |return| blends the two arms and cancels them"},
   "H5_retrospective": {"verdict": "NO_RESURRECTIONS", "see": "rejudgement table"},
   "H6_spread_proxy": {"verdict": "DATA_LIMITED",
     "preset_threshold_spearman": 0.6,
     "corwin_schultz_spearman": 0.867, "abdi_ranaldo_spearman": -0.237,
     "conclusion": "CS ranks but does not level (5.81 bps for BTC vs a true 0.015). Since "
                   "cost = const + 1.00*spread, a level error is a cost error one for one. No "
                   "numeric re-judgement is issued outside the 15 probe symbols."}
 },
 "cost_model": jload(f"{S}/cost_floor.json"),
 "queue_simulator_and_probe_calibration": jload(f"{S}/cost_model.json"),
 "bridge_3_to_15_symbols": jload(f"{S}/bridge_cost.json"),
 "urgency_signed_and_proxy": jload(f"{S}/signed_urgency_proxy.json"),
 "urgency_two_arms_and_capacity": jload(f"{S}/directional_urgency_capacity.json"),
 "h1_instrument_audit": jload(f"{S}/h1.json"),
 "h4_first_pass_unsigned_superseded": jload(f"{S}/h4.json"),
 "round4_gate": jload(f"{S}/gate.json"),
 "rejudgement": jload(f"{S}/rejudgement.json"),
 "bugs_and_pitfalls": [
   {"id": "MICRO_COLLECTOR_GAP_2026_09_04",
    "detail": "binance+okx+hyperliquid all lost 15.3h on 2026-09-04 (one 55086s quote gap). "
              "Without a staleness guard a time-grid join reads frozen quotes as 'nothing "
              "happened': fill rates read 0.30 instead of 0.92.",
    "fix": "s08 admits an attempt only if [t0, t0+TTL+300s] has no quote gap > 30s",
    "recommendation": "put the guard in the shared loader, not in one worker's script"},
   {"id": "OKX_SIZES_IN_CONTRACTS",
    "detail": "data/microstructure_reduced normalises okx sizes in CONTRACTS "
              "(BTC-USDT-SWAP=0.01 BTC, ETH=0.1, SOL=1). okx BTC top-of-book reads $27M instead "
              "of $270k. Verified against binance and hyperliquid on the same instant.",
    "impact": "fill/queue logic unaffected (book and trades share the unit); every okx NOTIONAL "
              "is inflated 100x/10x/1x", "fix": "corrected in s12"},
   {"id": "ENRICHED_STOPPED_TRACKING_6_SYMBOLS",
    "detail": "ARUSDT FETUSDT ORDIUSDT PYTHUSDT SUIUSDT TIAUSDT return ZERO 1h bars in "
              "data/enriched after 2026-07-12; a join returns an empty frame with no error"},
   {"id": "PROBE_FILL_RATE_IS_A_LOWER_BOUND",
    "detail": "traversal implies a real fill, so the probe UNDER-states fill probability by "
              "1.7pp at TTL 600s. The standing assumption that the probe is optimistic on fills "
              "is wrong; the optimism lives in the simulator and is haircut explicitly."},
   {"id": "BINANCE_USDM_SPREAD_IS_ONE_TICK",
    "detail": "all 15 probe symbols sit at 0.970-1.096 ticks. AR/ADA/FET have 5.4-6.7 bps "
              "spreads because their TICK is 5.3-6.7 bps. This is a listing parameter, not a "
              "liquidity measurement, and it is the largest single driver of their cost."}
 ],
 "recommendations": [
   "replace net_bps = gross_bps - 14 with cost_rt = spread_bps + 10.0 (taker) / + 8.2 (maker, TTL>=300s)",
   "WIDEN maker_fill_probe.py SYMBOLS: it is a 15-line list and it converts H6's DATA_LIMITED "
   "into a measured cost for the whole 312-symbol universe. Highest value / lowest cost action here.",
   "stop treating maker execution as a lever: ~1.5-2 bps RT, and only above 60s TTL",
   "never post on the side the market has already moved toward during a shock; cross instead "
   "(the taker penalty is ~0 because the spread does not widen)",
   "re-price the illiquid tail: AMIHUD_ILLIQUIDITY_PREMIUM_V1 is frozen, live, and deliberately "
   "buys the names where the -14 convention is too generous (it survives: +105.7 bps net)",
   "move the staleness guard and the okx contract multiplier into the shared loader"
 ],
 "limitations": [
   "both instruments are virtual; latency, post-only rejection, queue joiners, hidden size and "
   "own-footprint are NOT modelled - the 1 bps/side haircut is a stand-in, not a measurement",
   "regime coverage: 4 days of real books, 7 weeks of probe, ONE volatility regime; "
   "year_by_year and ex_best_year are N/A_COST_LAYER by preregistration",
   "the bridge rho(spread) is fitted on 0.013-0.99 bps and applied up to 6.7 bps (flagged "
   "extrapolated=true per row); this is the weakest joint in the worker",
   "no numeric cost outside the 15 probe symbols (H6 failed its own preset level test)",
   "VIP0 fees assumed (5.0/2.0 bps). A real rebate tier is the single input that would most "
   "change these conclusions and it is not observable from the data.",
   "adverse selection measured on a direction-agnostic probe is an UPPER bound for a mechanism "
   "that itself predicts the post-fill drift"
 ]
}

json.dump(res, open(f"{OUT}/RESULTS.json", "w"), indent=1, default=float)
for f in ["cost_floor.json", "cost_model.json", "bridge_cost.json", "gate.json", "h1.json",
          "h4.json", "signed_urgency_proxy.json", "directional_urgency_capacity.json",
          "cost_floor_per_symbol.csv", "cost_floor_by_tier.csv", "per_symbol_cost.csv",
          "h1_per_symbol.csv", "h4_per_symbol.csv", "ticks.csv", "rejudgement.csv",
          "rejudgement.json"]:
    p = f"{S}/{f}"
    if os.path.exists(p):
        shutil.copy(p, f"{EV}/{f}")
sz = sum(os.path.getsize(x) for x in glob.glob(f"{EV}/*"))
print(f"RESULTS.json written; evidence/ = {len(glob.glob(f'{EV}/*'))} files, {sz/1e6:.2f} MB")
