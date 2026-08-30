# W3 Derivatives — Fast Triage Report (A10, A9-new-angle, Liquidation Cross-Venue Propagation)

## Executive summary

Three mechanisms were fast-triaged before any heavy modeling, using PIT-safe data (`research_available_at`, not `timestamp`) and out-of-sample threshold discipline (thresholds fit on an earlier slice, tested on a later one). **A10 (funding settlement event) is DEAD**: fading extreme funding at the settlement bar gives |t| < 1.3 across 5m/15m/1h/4h pooled over 29 symbols and 27k-112k events, sign flips year to year, correlation ≈ 0. **A9 "basis velocity" is not actually new**: looks strong alone (t up to 21 pooled, 14.2 OOS 2024-2026 with thresholds fit on 2023) but collapses to noise/marginal (t=0.9-3.3) once orthogonalized against basis LEVEL — confirms it's mostly the already-exhausted level effect repackaged. **Calendar basis (perp vs Binance quarterly futures, BTC/ETH) is genuinely new and the strongest finding**: a hedged cash-and-carry harvest captures 360-680bps of convergence in 1-14 days at decile-extreme OOS entries (t=10.4-13.1), dominating ~10-20bps round-trip costs by 20-50x — but the naive *unhedged* directional version loses catastrophically (-393 to -625bps) because rich calendar basis co-occurs with ongoing momentum, not reversal; only the hedged spread trade works. **Cross-venue liquidation propagation (Bybit→OKX) is real but only marginally tradeable**: a Bybit cascade lifts P(OKX cascade in next 5-30min) by 2.2-4.3x for BTC/ETH, but the originally-specced "follow the cascade" trade is wrong-signed (loses money, t to -2.9); fading it is marginally profitable (t=2.5-2.9, 3.5-5.3bps) but thin versus realistic taker costs, and Binance couldn't be included at all (no live liquidation feed; historical backfill is COIN-margined only and dead-ends 2024-10-14). Net: 1 dead, 1 not-actually-new, 2 promising-but-narrow — two of which required inverting the naive first hypothesis, underscoring the value of testing before sizing.

## Mechanism A10 — Funding settlement event

**MECHANISM**: Funding settles every 8h; hypothesis is predictable inventory adjustment right after settlement. **PAYER**: funding-sensitive inventory unwinding post-settlement. **WHY EDGE EXISTS (hyp.)**: crowded positioning (extreme funding) should fade after settlement. **SIGNAL**: `funding_is_settlement==True` AND `funding_rate_percentile_90d` in extreme decile. **ENTRY/EXIT**: short/long at settlement `research_available_at`, fixed horizon 5m/15m/1h/4h. **VENUE**: Binance perp. **CAPACITY**: would be large if real. **FAILURE MODE (anticipated)**: funding is predictable pre-settlement, so any adjustment likely already priced in.

Data: `event_feature_panel` (PIT-safe), 29 liquid symbols, 2023-2026.

| Horizon | n | mean pnl (bps) | t-stat | corr(funding_rate, fwd_ret) |
|---|---|---|---|---|
| 5m | 27,459 | -0.14 | -0.66 | -0.017 |
| 15m | 39,046 | +0.20 | 0.64 | -0.007 |
| 1h | 39,046 | -0.59 | -1.08 | -0.005 |
| 4h | 39,044 | -1.20 | -1.22 | -0.017 |

Year-by-year (15m): 2023 +1.03, 2024 -1.08, 2025 +0.20, 2026 +0.80bps — unstable sign. **VERDICT: DEAD.**

## Mechanism A9 (angle #1) — Basis velocity fade

**MECHANISM/PAYER/WHY**: tests whether the *rate of change* of perp-spot basis (not its level, already exhausted per scoreboard) carries independent information. **SIGNAL**: extreme decile of `basis_z_1d` change over trailing 1h. **ENTRY/EXIT**: fade at 15m/1h/4h/1d.

Threshold fit on 2023, tested OOS 2024-2026 (29 symbols, 10.45M obs):

| Horizon | OOS n | mean pnl (bps) | OOS t | Level-fade benchmark t | Velocity-only (level≈0) t |
|---|---|---|---|---|---|
| 15m | 1,480,220 | 0.75 | 14.17 | 19.06 | 3.26 |
| 1h | 1,480,157 | 0.75 | 8.12 | 13.62 | 0.93 |
| 4h | 1,479,931 | 1.12 | 6.62 | 9.41 | 1.14 |
| 1d | 1,478,313 | 2.03 | 5.11 | 13.42 | 2.22 |

Once restricted to rows where basis level is near zero (isolating pure velocity), t collapses to 0.9-3.3 and stays below the level-fade benchmark on the same sample. **VERDICT: WEAK — not a genuinely new angle**, confirms the scoreboard's exhaustion warning applies here too.

## Mechanism A9 (angle #2) — Perp vs. quarterly-futures calendar basis (the fresh angle)

**MECHANISM**: dated quarterly futures (`BTCUSDT_260327` etc.) are contractually forced to converge to spot/perp at expiry; thinner books / less continuous arb capital than perp allow large mispricings to build up. **PAYER**: directional speculators crowding the quarterly leg. **WHY EDGE EXISTS**: capacity-constrained arbitrage capital in a less-liquid instrument — mispricings found are large (annualized basis in the hundreds of %), consistent with genuine constraint rather than noise. **SIGNAL**: front-quarter annualized basis `(quarterly/perp - 1)*(365/dte)`, extreme decile (train-fit), `dte>=7` filter. **ENTRY**: HEDGED — short rich leg / long cheap leg. **EXIT**: convergence captured within 1-14 days. **VENUE**: Binance perp + Binance quarterly future (2-leg hedge). **HORIZON**: 1-14 days. **CAPACITY**: capped to BTC/ETH only, and to quarterly-futures book depth (materially thinner than perp) — a hard ceiling. **FAILURE MODE**: unhedged directional version is a trap (see below); margin/funding risk on the open hedge; daily-bar granularity means intraday execution mechanics unverified.

Contracts 2021-2026 (24 expiries/symbol), train = first 60% chronologically, test = last 40% (~2024-05 to 2026-07):

| Symbol | Train thresholds | k=1d n/bps/t | k=3d | k=7d | k=14d |
|---|---|---|---|---|---|
| BTCUSDT | q10=-27.3%, q90=+52.0% | 111/403.6/10.44 | 432.2/12.75 | 364.7/11.20 | 426.0/12.92 |
| ETHUSDT | q10=-41.5%, q90=+66.5% | 114/600.7/10.90 | 681.3/12.93 | 614.7/12.93 | 623.4/13.14 |

Convergence is captured almost entirely within day 1 and stays flat to day 14. Assumed round-trip cost for the 2-leg trade: ~10-20bps (project's `configs/institutional/execution.yaml`: taker=5bps/maker=2bps; more conservative `run_parallel_50_*` configs: taker=10bps+slippage=4bps). Gross edge dominates by 20-50x.

**Trap found and avoided**: first pass tested the *unhedged* perp-only fade and got -393.5bps (t=-16.4, BTC) / -624.5bps (t=-16.8, ETH) at k=1d — rich calendar basis co-occurs with strong ongoing momentum, so shorting the perp outright is betting against the trend. The hedged spread trade sidesteps this since it takes no net directional bet.

**Caveat**: raw n (111-114) is inflated by autocorrelation — extreme-basis regimes persist 1-3 weeks once triggered, so true independent episodes are roughly 6-8 per symbol over the ~2yr OOS window, not 100+.

**VERDICT: PROMISING.** Genuinely new angle, large OOS-stable gross edge, clear hedged execution recipe, narrow but real capacity, needs intraday PIT execution work and more expiries/venues before sizing.

## Mechanism C — Cross-venue liquidation cascade propagation (Bybit → OKX)

**MECHANISM**: a liquidation cascade on one venue should propagate to a second venue with a lag if crowded leverage is correlated cross-venue — distinct from W1's single-venue depth-exhaustion work (not duplicated; `reports/liq_cascade/` untouched beyond context). **PAYER**: late-liquidated positions on the lagging venue, plus (per finding) momentum-chasers on the wrong side. **SIGNAL**: trailing 5-min Bybit liquidation notional > train-period p95 ("cascade"). **ENTRY (as specced)**: trade OKX in the cascade's net direction (momentum). **VENUE**: OKX. **HORIZON**: 5-30min. **CAPACITY**: capped to BTC/ETH — see degeneracy note below. **FAILURE MODE (anticipated)**: cross-venue arb may already be fast enough for majors to leave nothing exploitable.

Data: `derivatives_raw/exchange={bybit,okx}/.../stream=force_order`, **2026-07-04 to 2026-08-29 only (57 days)** — a hard ceiling, no year-over-year check possible. Binance has **no `force_order` stream at all**, and its COIN-margined historical liquidation backfill (`binance_vision_liquidation`) dead-ends **2024-10-14** (matches the known project pitfall) — Binance excluded entirely.

Propagation lift (robust only for BTC/ETH; degenerate for SOL/BNB/XRP/DOGE/ADA/AVAX/LINK/LTC/TRX/DOT because >90-95% of their 5-min windows have zero liquidation notional, collapsing the percentile threshold to 0 and making "cascade" trivially true almost every minute):

| Symbol | K=5min lift | K=15min lift | K=30min lift |
|---|---|---|---|
| BTCUSDT | 4.11x | 2.83x | 2.17x |
| ETHUSDT | 4.27x | 2.91x | 2.21x |

Directional trade (train/test split, last 40%):

| Symbol | K=5min follow | K=15min follow | K=30min follow | K=30min FADE |
|---|---|---|---|---|
| BTCUSDT | 0.83bps/t1.33 | -0.82bps/t-0.87 | **-3.71bps/t-2.91** | **+3.71bps/t2.91** |
| ETHUSDT | -0.55bps/t-0.62 | **-3.48bps/t-2.50** | **-5.27bps/t-2.75** | **+5.27bps/t2.75** |

Price mean-reverts after a cascade rather than continuing — "follow" is wrong-signed and significantly negative at 15-30min; "fade" is right-signed but thin (3.5-5.3bps) against a realistic ~10bps round-trip taker cost, clearing only maker-level costs (~4bps) with modest margin.

**VERDICT: WEAK / PROMISING-BUT-MARGINAL.** Event-level propagation confirmed for BTC/ETH; the tradeable version required inverting the original hypothesis and even then is economically thin and unconfirmed beyond one 57-day regime.

## Bugs / leakage risks noticed while building signals

1. **Binance has no live liquidation feed and its historical backfill dead-ends 2024-10-14** (COIN-margined only) — confirmed and re-flagged (matches existing project memory); silently caps any Binance-based liquidation work.
2. **OKX/Bybit funding backfill is stale/thin**: OKX 9 symbols, 2026-03-23 to 2026-06-28 only (~3 months); Bybit 9 symbols, longer history but also stops 2026-06-28 — **2 months before "today."** This **blocks** a live cross-venue funding-disagreement test (the other named A9 sub-angle); deprioritized rather than silently dropped.
3. **Quarterly-futures parquet files contain trailing stale/forward-filled prices past actual expiry** (e.g. `BTCUSDT_251226_1d.parquet` repeats the last real close for several days after the 2025-12-26 expiry). Fixed by hard-truncating at `date<=expiry` plus trimming trailing duplicate-close runs. Anyone reusing this data without truncation will compute a fictitious near-zero basis around every roll.
4. **Annualized-basis blow-up near expiry**: `raw_basis_pct * 365/dte` explodes as `dte→0` (values up to ±2500-3100% observed). Real math, not a data error, but requires a `dte>=7` floor before thresholding.
5. **Unit bug caught before it reached this report**: first pass computed carry-harvest P&L on the *annualized* basis column instead of *raw* basis-pct, inflating apparent P&L by up to ~52x in economic-unit terms. Recomputed on raw basis-pct for the numbers reported above (360-680bps) — flagged as an easy mistake to repeat.
6. **Percentile-threshold degeneracy for sparse liquidation series**: for any symbol besides BTC/ETH, >90-95% of 5-min windows have zero liquidation notional, so a percentile threshold collapses to 0, making the "conditional" test vacuous (fires on almost every minute). Caught by inspecting the printed thresholds; the raw per-symbol CSV is preserved but flagged as unusable without a fixed-notional threshold redesign.
7. **PIT hygiene check (positive finding)**: verified via `build_event_feature_panel.py` that `research_available_at` is a genuine row-wise max over per-source availability lags and is correctly used throughout this work instead of `timestamp`. No leakage found in the mechanism's own construction.

## Files written
Under `/home/qbee/futur/reports/edge_discovery/alpha_hunt_2026-08-29/w3_derivatives/`:
- `a10_summary.json`, `a10_settlement_by_symbol.csv`, `a10_settlement_by_symbol_year.csv`
- `a9_basis_velocity_summary.json`
- `a9_calendar_basis_summary.json`
- `c_liq_propagation_summary.json`, `c_liq_propagation_results.csv` (raw; includes flagged-degenerate rows for non-BTC/ETH symbols — do not reuse without fixing the threshold)
