"""Consolidate every mechanism into RESULTS.json with the full round-4 §2 gate."""
import json, os
from pathlib import Path
W = Path("/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-09-03_round4/w4_news_sentiment")
E = W / "evidence"
m1 = json.load(open(E / "m1_results.json"))
m28 = json.load(open(E / "m2_m8_results.json"))
m5 = json.load(open(E / "m5_results.json"))
m910 = json.load(open(E / "m9_m10_results.json"))
m9b = json.load(open(E / "m9b_placebo_results.json"))
m1113 = json.load(open(E / "m11_m13_results.json"))

GATE = ("n_raw", "n_independent_L1", "n_independent_L2", "n_independent_L3", "gross_bps",
        "net_bps", "net_bps_stress28", "t_stat_declustered", "bootstrap_ci95",
        "year_by_year", "ex_best_year", "year_concentration_frac", "n_required",
        "event_rate_per_week_6m", "eta_forward_confirmation_days",
        "eta_forward_confirmation_years")

def g(d):
    return {k: d.get(k) for k in GATE}

R = []

R.append({
 "id": "M1", "name": "F&G extreme-fear -> long BTC 1d (NEGATIVE CONTROL)",
 "family": "sentiment_directional", "data_depth": "2018-02 to 2026-07 (8.5y)",
 "best_arm": "fg_pct365 <= 0.20", **g(m1["1d"]["fear"]),
 "arm_comparison": {"fear_minus_greed_bps": m1["1d"]["spread_fear_minus_greed"]["spread_bps"],
                    "greed_arm_net_bps": m1["1d"]["greed"]["net_bps"],
                    "mid_arm_net_bps": m1["1d"]["mid"]["net_bps"],
                    "always_long_same_pop_net_bps": m1["1d"]["baseline_always_long"]["net_bps"]},
 "verdict": "UNCONFIRMABLE_IN_HORIZON",
 "why": ("The single most publicly backtested crypto rule reproduces: +49.8bps net, t=2.25, "
         "positive in 7 of 8 years, ex-best-year +41.8 (t=2.11), concentration 0.29 - it is NOT a "
         "year artifact. It is nevertheless unusable: F&G regimes are slow, so 8.5 years yield only "
         "94 independent regime episodes (~11/yr), and confirming a 50%-haircut edge forward needs "
         "20.8 YEARS. It is also public knowledge, and M5b shows it cannot be separated from "
         "trailing drawdown on this sample."),
 "gate_box_missing": None})

for key, mid, arm, verdict, why in [
 ("M2_liq_cascade_repeat_x_fg", "M2", "low_fear", "DEAD",
  "Preregistered direction CONFIRMED but economically empty. low_fear 16.25 vs high_greed 2.04 "
  "(spread +14.21) looks like a gate until compared to the RIGHT baseline: the SAME population "
  "ungated pays 15.81. The F&G gate adds +0.44bps and throws away 68% of the events. Tercile "
  "ordering is non-monotone (low 16.25 < mid 26.95 > high 2.04) - the shape of noise, not a regime. "
  "t=1.10 on 140 episodes."),
 ("M3_short_squeeze_repeat_x_fg", "M3", "low_fear", "DEAD",
  "Every arm is net-negative under episode-level declustering (low -2.98, mid -14.96, high -21.19). "
  "Spread +18.21 is in the predicted direction but no arm clears cost, so there is nothing to gate. "
  "See the round-3 A4 cross-check below: the underlying base signal is itself fragile."),
 ("M4_cascade_onset_x_fg", "M4", "mid", "DEAD",
  "Exogenous sentiment does NOT rescue the onset null that market-internal regimes could not rescue "
  "(round 3 A1/A2/T1.9). All three arms net-negative; low_fear minus high_greed = -2.38, i.e. the "
  "wrong sign versus the preregistered prediction. Clean corroboration of the existing DEAD."),
 ("M6_fg_change_x_liq_repeat", "M6", "flat", "DEAD",
  "The best arm is 'sentiment NOT changing' (+19.74, t=2.34) - which is just the majority of the "
  "population (3565/5442) and therefore not a signal. Both extreme arms fail: deteriorating +10.97 "
  "(t=0.52), improving -6.97 (t=-0.38). Sentiment momentum carries no more than sentiment level."),
 ("M8_fg_vs_lsr_divergence", "M8", "other", "DEAD",
  "HYPOTHESIS REJECTED IN DIRECTION. The brief's most-favoured angle - talk disagreeing with money - "
  "is the WORST arm: divergent (fear + crowded long) -16.74, aligned +9.45, neither-fear +24.14. "
  "Spread divergent-minus-aligned = -26.19. On the long-history positioning proxy (ls_ratio_z, "
  "1.2% NaN, 2021-2026) divergence destroys the edge rather than sharpening it."),
]:
    d = m28[key][arm]
    R.append({"id": mid, "name": f"{key} (best arm: {arm})", "family": "sentiment_regime_conditioner",
              "data_depth": "2021-01 to 2026-07 x F&G", "best_arm": arm, **g(d),
              "arm_comparison": {k: v for k, v in m28[key].items()
                                 if isinstance(v, dict) and "comparison" in v},
              "year_composition_max_share": m28[key].get("year_composition", {}).get(arm + "_max_year_share"),
              "verdict": verdict, "why": why, "gate_box_missing": None})

d = m28["M7_fg_vs_funding_divergence"]["aligned_fear"]
R.append({"id": "M7", "name": "F&G vs funding divergence (best arm: aligned_fear)",
 "family": "sentiment_vs_positioning", "data_depth": "2021-2026 but funding_z30 is 76% NaN",
 "best_arm": "aligned_fear", **g(d),
 "arm_comparison": m28["M7_fg_vs_funding_divergence"]["spread_divergent_minus_aligned"],
 "verdict": "UNCONFIRMABLE_IN_HORIZON",
 "why": ("HYPOTHESIS REJECTED IN DIRECTION, same as M8. The DIVERGENT arm (fear in the talk, longs "
         "still paying funding) is -29.06; the ALIGNED arm (fear in the talk AND in the money) is "
         "+40.33, t=2.31. Spread = -69.39, i.e. agreement pays and disagreement does not - the exact "
         "opposite of the preregistered hypothesis. The aligned arm survives cost 28 (+26.33) but "
         "rests on 229 raw / 89 episodes carved out of a base where funding is 76% missing, and its "
         "ETA is 13.7 years."),
 "gate_box_missing": "funding_z30 coverage (76% NaN) + ETA 13.7y"})

R.append({"id": "M5a", "name": "INCREMENTALITY: is F&G laundered volatility?",
 "family": "diagnostic", "data_depth": "2021-2026",
 "result": {"unconditional_fg_spread_bps": m5["M5a_fg_incremental_over_btcvol"]["_unconditional_fg_spread"],
            "mean_within_vol_bucket_fg_spread_bps": m5["M5a_fg_incremental_over_btcvol"]["_mean_within_vol_fg_spread"],
            "retention_frac": m5["M5a_fg_incremental_over_btcvol"]["_retention_frac"],
            "per_vol_bucket_fg_spread": {k: m5["M5a_fg_incremental_over_btcvol"][k]["fg_spread_within_vol"]
                                         for k in ("vol_low", "vol_mid", "vol_high")},
            "reverse_vol_spread_within_each_fg_bucket": m5["M5a_fg_incremental_over_btcvol"]["_reverse_vol_spread_within_fg"]},
 "verdict": "DEAD",
 "why": ("F&G is not merely laundered vol (retention 0.84), but that is irrelevant because what "
         "survives is incoherent: the within-vol-bucket F&G spread SIGN-FLIPS across buckets "
         "(+39.4 / -24.2 / +20.7). The reverse test is decisive - the btc_vol spread stays large and "
         "same-signed inside EVERY F&G bucket (+41.4 / +87.6 / +60.2). Round 3's T1.1 volatility gate "
         "is real and robust to F&G; F&G is not robust to volatility. Per preregistered veto rule 6, "
         "M2/M3 are capped."),
 "gate_box_missing": None})

R.append({"id": "M5b", "name": "INCREMENTALITY: is 'buy fear' just buying a drawdown?",
 "family": "diagnostic", "data_depth": "2018-2026",
 "result": {**{k: m5["M5b_fg_incremental_over_drawdown"][k] for k in
               ("dd_deep", "dd_mid", "dd_shallow", "_unconditional_spread",
                "_mean_within_dd_spread", "_retention_frac",
                "_drawdown_only_spread_deep_minus_shallow")},
            "fg_r2_on_trailing_price": m5["M5c_fg_explained_by_trailing_price"]},
 "verdict": "DATA_LIMITED",
 "why": ("F&G is 47.5% mechanically explained by trailing price alone (R^2 of fg_pct365 on 30d "
         "return + 30d vol + 365d drawdown; corr with 30d return = 0.664). Controlling for drawdown "
         "halves the fear-vs-greed spread (retention 0.49) and makes it sign-unstable across buckets "
         "(+173.5 / -67.9 / -2.5). But the off-diagonal cells are nearly empty - greed inside a deep "
         "drawdown is 5 episodes, fear inside a shallow one is 8 - so F&G and drawdown are too "
         "collinear on 8.5 years of DAILY data to be separated at all. Honest answer: not identified. "
         "Trailing drawdown alone does NOT reproduce the effect (-4.37), so it is not pure "
         "mean-reversion either."),
 "gate_box_missing": "identification - needs an instrument for sentiment orthogonal to price"})

R.append({"id": "M10", "name": "PIT AUDIT of data/news_raw (infrastructure finding)",
 "family": "data_integrity", "data_depth": "all 5,793 rows",
 "result": m910["M10_pit_audit"], "verdict": "DATA_LIMITED",
 "pit_stamp": "PIT_UNVERIFIED",
 "why": ("CONFIRMED DEFECT. `collector._parse_rss` sets ts = parsedate_to_datetime(pubDate) - the "
         "SOURCE-DECLARED time - and no collection-time column is persisted, while the hive partition "
         "key is derived from that same declared time. Any backtest anchored on `ts` is look-ahead "
         "biased by a MEDIAN of 21.3 minutes; 28.3% of RSS rows arrive >30min after their own "
         "timestamp, 14.2% >2h, p95 = 31 hours, max 193 days. Collection time is recoverable from the "
         "parquet mtime (filename HHMMSS agrees to a median 0.0s). Real continuous coverage is 53 "
         "collection days, not the 74 partitions the directory listing suggests."),
 "gate_box_missing": None})

pl = m9b["windows"]
R.append({"id": "M9", "name": "News -> price latency (THE KILL TEST) + random-time placebo",
 "family": "news_timing", "data_depth": "2026-07-10 to 2026-09-03, 53 days",
 "pit_stamp": "PIT_OK (anchored on recovered collection time, not on the declared ts)",
 "result": {"n_raw_btc_stories": m910["M9_news_to_price_latency"]["n_btc_rss_raw"],
            "n_independent_story_L3": m910["M9_news_to_price_latency"]["n_btc_rss_independent_story_L3"],
            "cluster_ratio": m910["M9_news_to_price_latency"]["cluster_ratio_raw_over_indep"],
            "placebo_n_stories": m9b["n_independent_stories"], "placebo_draws": 1000,
            "excess_abs_move_over_placebo_bps": {
                a: {w: v["excess_over_placebo_bps"] for w, v in ws.items()} for a, ws in pl.items()},
            "post_over_pre_excess_ratio": {
                "declared_pubDate": round(pl["declared_pubDate"]["[0,30]"]["excess_over_placebo_bps"] /
                                          pl["declared_pubDate"]["[-30,0]"]["excess_over_placebo_bps"], 2),
                "collection_time": round(pl["collection_time"]["[0,30]"]["excess_over_placebo_bps"] /
                                         pl["collection_time"]["[-30,0]"]["excess_over_placebo_bps"], 2)},
            "signed_return_after_bps": {a: ws["[0,30]"]["observed_mean_signed_bps"] for a, ws in pl.items()},
            "full_window_profile": m910["M9_news_to_price_latency"]["window_profile_abs_and_signed_bps"]},
 "verdict": "DEAD",
 "why": ("The decisive result of this worker. Crypto news stories DO sit in volatile moments - the "
         "absolute BTC move around a story beats a 1000-draw random-time placebo by ~4-9bps, p<0.001 "
         "in every window. But that excess is SYMMETRIC about the timestamp: +3.80bps in the 30min "
         "BEFORE the declared publication vs +4.83 in the 30min after (ratio 1.27), and at the "
         "collection anchor - the only time a system could actually act - +5.42 BEFORE vs +4.71 AFTER "
         "(ratio 0.87, i.e. MORE of the move is already gone than remains). Publication is a "
         "COINCIDENT marker of volatility, not a leading one. Signed returns after publication are "
         "+2.3 to +4.0bps against a 14bps cost floor and flip sign between anchors. The feed describes "
         "a move already in progress; adding the 21-minute median collection lag puts a real system "
         "strictly on the late side of it."),
 "gate_box_missing": None})

d = m1113["M11_attention_count"]["gate_spike_long"]
R.append({"id": "M11", "name": "Attention COUNT (not polarity) -> forward vol / direction",
 "family": "attention", "data_depth": "53 days, one regime",
 "pit_stamp": "PIT_OK (anchored on recovered collection time)", "best_arm": "att_z >= 1.0", **g(d),
 "result": {"vol_test": m1113["M11_attention_count"]["vol_test"],
            "direction_test": m1113["M11_attention_count"]["direction_test"],
            "n_symbol_days": m1113["M11_attention_count"]["n_symbol_days"],
            "n_distinct_days_L2": m1113["M11_attention_count"]["n_distinct_days_L2"]},
 "verdict": "DATA_LIMITED",
 "why": ("Preregistered as the more robust half of the axis, and it fails on its OWN hypothesis: "
         "attention spikes precede LOWER relative vol (rv ratio 0.915 vs 1.005 calm, spread -0.09, "
         "t=0.08), so the plausible leg is flat. The direction leg shows +98.8bps net but on 13 "
         "independent days with a bootstrap CI of [-79, +315] and ETA 18.2 years. After the causal "
         "14d z-window warm-up only 20 usable days survive. Nothing is claimable."),
 "gate_box_missing": "history - needs >=8 months and multiple F&G regimes"})

R.append({"id": "M12", "name": "Attention DISPERSION (HHI / entropy) as a regime marker",
 "family": "attention", "data_depth": "19 usable days",
 "pit_stamp": "PIT_OK (anchored on recovered collection time)",
 "result": m1113["M12_attention_dispersion"], "verdict": "DATA_LIMITED",
 "why": ("19 usable days. Concentrated-attention days precede a higher forward vol ratio (1.065 vs "
         "0.887) which is the plausible sign, and a worse forward BTC return (-55.3 vs +15.2 net, "
         "spread -70.6) - but t=-0.93 on 19 observations inside a single extreme-fear regime. "
         "Reporting the sign only; there is no test here."),
 "gate_box_missing": "history"})

d = m1113["M13_coingecko_trending_entry"]["gate"]
R.append({"id": "M13", "name": "CoinGecko trending ENTRY -> forward 1d return",
 "family": "attention", "data_depth": "53 days",
 "pit_stamp": "PIT_OK (anchored on recovered collection time)", "best_arm": "first appearance in >=7d", **g(d),
 "result": {k: m1113["M13_coingecko_trending_entry"][k] for k in
            ("n_entries_raw", "n_distinct_days", "n_distinct_symbols", "raw_net_bps",
             "excess_vs_peer_bps", "t_excess_daymeans")},
 "verdict": "DATA_LIMITED",
 "why": ("Directionally consistent with the preregistered 'attention is a sell' prior: entering the "
         "CoinGecko trending list is followed by -147.7bps net, t=-2.40, and -38.3bps excess versus "
         "same-day peers. But 24 entries on 9 independent days in one regime, and the tradeable "
         "version is a SHORT on small-cap alts where the 14bps cost model does not apply. Recorded as "
         "the single most interesting lead in the shallow block, not as a result."),
 "gate_box_missing": "N (9 episodes), regime coverage, and a cost model for small-cap alt shorts"})

R.append({"id": "X1", "name": "CROSS-CHECK: round-3 A4 SHORT_SQUEEZE repeat under episode weighting",
 "family": "cross_check", "data_depth": "2021-2026",
 "result": {"round3_reported_net_bps_gap4h_event_weighted": 11.14, "reproduced_exactly": True,
            "n_round3": 2381,
            "by_year_net_bps": {"2021": 9.38, "2022": -62.01, "2023": -8.80,
                                "2024": 29.51, "2025": 63.96, "2026": 2.48},
            "net_bps_under_fg_episode_equal_weighting": -12.73},
 "verdict": "REGIME_DEPENDENT",
 "why": ("Round 3's A4 baseline (+11.14bps, N=2381, gap>=4h) reproduces EXACTLY here, which validates "
         "both pipelines. But it is carried entirely by 2024 (+29.5) and 2025 (+64.0) against 2022 "
         "(-62.0) and 2023 (-8.8), and it flips to -12.73 when independent episodes are equal-weighted "
         "instead of events. Handing this back to whoever owns A4: it is REGIME_DEPENDENT, not a "
         "stable base to build a gate on."),
 "gate_box_missing": None})

R.append({"id": "X2", "name": "INFRA: the Fear & Greed feed is dead in production",
 "family": "infrastructure", "data_depth": "n/a",
 "result": {"systemd_unit_description": "Futur news/social collector (RSS + F&G + CoinGecko, sources publiques)",
            "actually_fetched_by_collect_once": ["cointelegraph", "decrypt", "bitcoinmagazine",
                                                 "newsbtc", "coingecko_trending"],
            "fear_greed_fetch_in_collector": False,
            "fear_greed_last_value_date": "2026-07-10",
            "days_stale_as_of_2026_09_03": 55,
            "only_producer": "scripts/backfill_fear_greed.py (one-shot, not scheduled)"},
 "verdict": "DEAD",
 "why": ("futur-news.service advertises 'RSS + F&G + CoinGecko' but collect_once() fetches only RSS "
         "and CoinGecko trending - there is no Fear & Greed call anywhere in the collector. "
         "data/news_backfill/fear_greed.parquet is produced solely by a one-shot backfill script that "
         "last ran 2026-07-10, so the series has been 55 days stale and silently so. Anything that "
         "reads it as a live feature is reading a frozen file. Cheap fix; reported, not applied "
         "(src/institutional/ is read-only for this worker)."),
 "gate_box_missing": None})

out = {"worker": "W4_NEWS_SENTIMENT", "round": "alpha_hunt_2026-09-03_round4", "date": "2026-09-03",
       "axis": "news, sentiment, attention",
       "headline": ("AXIS KILLED. 13 mechanisms taken to the gate. Zero VALIDATED_FOR_FORWARD. The "
                    "deep leg (F&G, 8.5y) is a coherent but unusable regime marker: the one real "
                    "effect needs 20.8 years to confirm and adds +0.44bps as a gate on the project's "
                    "own frozen alpha. The shallow leg (news_raw, 53 days) is killed on TIMING rather "
                    "than on sample size: publication is a coincident, not leading, marker of "
                    "volatility, and the collector is look-ahead biased by a median 21 minutes."),
       "cost_convention": {"net_bps": "gross - 14", "stress": "gross - 28"},
       "declustering": {"L1": "first event per symbol per 24h",
                        "L2": "distinct calendar days",
                        "L3": "F&G regime episode (maximal consecutive-day run in one tercile) for "
                              "the deep block; title-token story cluster within 24h for the news block"},
       "mechanisms": R}
json.dump(out, open(W / "RESULTS.json", "w"), indent=1, default=str)
print("wrote RESULTS.json with", len(R), "mechanisms")
for r in R:
    print(f"  {r['id']:4s} {r['verdict']:26s} {r['name'][:66]}")
