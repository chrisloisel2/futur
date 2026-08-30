## Findings — W4 Residual RV / Cross-Asset Propagation (Alpha Hunt 2026-08-29)

**Note on deliverable file:** full trade ledgers (`a13h_e1_trades.csv`, 69MB, 546,251 rows; `a12_events.csv`, 5.7MB, 50,207 rows) and analysis scripts (`run_hunt.py`, `analyze.py`) remain in the session scratchpad (not copied into the repo, per the disk-space constraint). `summary_stats.json` and `PREREG.md` are alongside this report.

### Executive summary

Both mandated tests are **DEAD**. A13-H-E1 (contemporaneous cross-sectional percentile trigger on `residual_return_1h/residual_std_30d`, name-vs-leave-one-out-basket, converge-or-4h-timeout) produces a real but tiny gross edge (+2.3 bps/trade) that is dwarfed by even a fee-only cost floor (20 bps/trade, no slippage modeled) — net -17.7 bps/trade, PF 0.63, negative in **19/19 quarters** from 2022 through 2026, both trade directions net-negative, naive t≈-70. It is not a threshold-tuning problem (gross itself is basically nothing) and not a capacity story either (typical capacity is a further problem: median ~$1,900/leg at 1% participation). A12 (BTC/ETH residual innovation → follower same-direction catch-up) is also DEAD **in the hypothesized direction**: forward follower-basket returns are flat-to-negative at 5m/15m (not statistically distinguishable from zero) and turn significantly *negative* at 1h/4h (BTC: -1.5 bps@1h, -5.1 bps@4h; ETH: -2.6 bps@1h, -7.2 bps@7h) — i.e., if anything the cross-section fades the leader's residual move rather than following it, and even that reversal is smaller than A13-H-E1's already-dead reversion edge once costed. Sub-minute (100ms) horizons are BLOCKED_DATA at this 5-minute bar resolution, as expected. The `eligible_*` flags turned out to be pre-registered, PnL-blind data-availability/causal-warmup gates (not signals) for four named alpha families; `eligible_rvd` was used exclusively (it already encodes the causal 30-day warmup and a pre-registered min-30-live-names cross-sectional floor), which also gave the PIT/survivorship check for free.

---

### A13-H-E1 — Cross-sectional residual RV (contemporaneous-percentile trigger)

**Spec (fixed before any result was examined):**
- MECHANISM: idiosyncratic (already beta/factor-neutral) residual returns occasionally diverge sharply from the live cross-section due to transient, symbol-specific flow imbalance; because it's *residual*, it isn't new information and should decay toward the cross-sectional center.
- PAYER: whoever generated the urgent, price-insensitive one-sided flow (forced deleveraging, single-name liquidation cascade, panic flow).
- WHY EDGE EXISTS: causal, real-time monitoring of beta-neutral dispersion across 150-250 live perps simultaneously is infrastructure-heavy; the panel's own `eligible_rvd` gate (min 30 live names, causal 30d warmup) already tells you this competes with real RV desks.
- SIGNAL: `z = residual_return_1h / residual_std_30d` (both causal, no leakage — `residual_std_30d` is a `shift(1)`, full-window rolling std), restricted to `eligible_rvd==True` rows. `rank_pct` = percentile rank of `z` among all eligible names **at the same bar** (contemporaneous cross-section, hence causal — no future bar informs the threshold).
- ENTRY: `rank_pct<=0.02` → LONG (cheap), `rank_pct>=0.98` → SHORT (rich); decision uses bar t (`research_available_at`=t+305s, before bar t+1 opens), execution at bar t+1. One open trade per symbol at a time.
- TRADE: name vs. a leave-one-out equal-weighted basket of the rest of the `eligible_rvd` universe, frozen at entry (no intra-trade rebalancing) — the "or vs a basket" variant, chosen to avoid arbitrary pairing when tail counts differ side-to-side.
- EXIT: convergence (`rank_pct` back in [0.40,0.60]) OR **4h timeout (48 bars)** — 4h chosen because it's the pre-declared outer edge of the catalog's own A13 horizon window (1min–4h), not picked after seeing returns.
- EXECUTION VENUE: binance (only venue in panel).
- COSTS: 4 unit-notional legs per round trip (entry+exit × name+basket) × 5.0bps Binance taker fee (project convention from `market_physics_v3/phase5_2_execution_economics.py::TAKER_FEE_BPS["binance"]`) = flat **20bps/trade**. No L2/spread data exists in this panel, so this is a fee-only floor — real net is worse than reported.
- MAIN FAILURE MODE: the "extreme" move is a genuine, non-reverting idiosyncratic repricing (delisting risk, hack, exchange-specific shock).

**Fast-triage numbers (312-symbol universe, 2022-08 through 2026-08, 251 symbols actually contributing trades):**

| Metric | Value |
|---|---|
| n trades | 546,251 (251 symbols) |
| Gross mean | +2.30 bps/trade |
| Net mean (after 20bps cost) | **-17.70 bps/trade** |
| Win rate (net) | 40.4% |
| Profit factor (net) | 0.63 |
| Converged-before-timeout rate | 96.3% (right-censored at year-end: 0.06%) |
| Median holding | 12 bars (1h) |
| Sign stability | **negative in 19/19 quarters**, 5/5 years |
| By direction | short leg -16.0bps (n=299,762); long leg -19.8bps (n=246,489) — both dead |
| Capacity (1% of 5-min $ volume, per leg) | median ~$1,910; p90 ~$26,342; mean ~$33,723 (thin — mostly firing on long-tail alts) |
| Naive t-stat (net) | -70 (overlapping trades across symbols means true SE is understated, but the sign/magnitude conclusion doesn't depend on this) |

**VERDICT: DEAD.** Gross edge is real but roughly an order of magnitude too small to cover even a fee-only cost floor; the sign is uniformly negative across every year and quarter and both directions, so it isn't a threshold- or regime-tuning issue. Capacity is a second, independent problem even if costs were zero (median position ~$2k/leg). This is a materially different design (contemporaneous cross-sectional percentile + fixed-timeout state machine) from the sealed A13-H (H1/H2/H3 variants, located under `/home/qbee/futur-merge-main/{docs/A13H_PREREGISTRATION.md, scripts/run_a13h_backtest.py, scripts/build_a13h_panel.py, reports/alpha_foundry_v5/a13h_backtest/VERDICT.md}`, located via grep to know what to avoid but never opened). Both arrive at "residual RV mean-reversion doesn't survive costs" independently.

---

### A12 — Leader-innovation → follower residual catch-up

**Spec:**
- MECHANISM: BTC/ETH residual shocks carry systemic risk-sentiment content that diffuses into thinner, less-watched follower perps with a lag.
- PAYER: attention/liquidity-constrained participants in smaller names.
- SIGNAL: `leader_z = residual_return_1h/residual_std_30d` for BTCUSDT/ETHUSDT (own history, causal); large innovation = `|leader_z|>=2.0`.
- ENTRY: at bar t+1, same direction as leader's move, on the leave-one-out equal-weight basket of the rest of `eligible_rvd` (already beta-neutral, no separate hedge leg needed).
- EXIT: fixed horizons only — 5m/15m/1h/4h (1/3/12/48 bars). **Sub-minute (100ms) horizons: BLOCKED_DATA — panel is 5-minute bars, no intrabar data exists.**
- COSTS: single-sided basket, 2 legs (entry+exit) × 5.0bps = flat **10bps/trade**.
- MAIN FAILURE MODE: overlapping/autocorrelated leader-extreme bars inflate apparent n (flagged, not HAC-corrected — this is a fast triage).

**Fast-triage numbers:**

| Leader | n events | fwd 5m | fwd 15m | fwd 1h | fwd 4h |
|---|---|---|---|---|---|
| BTCUSDT | 25,222 | -0.25bps (t=-0.84) | -0.55bps (t=-1.06) | -1.48bps (t=-2.33) | **-5.10bps (t=-4.83)** |
| ETHUSDT | 24,985 | -0.32bps (t=-1.06) | -0.66bps (t=-1.26) | **-2.64bps (t=-4.09)** | **-7.16bps (t=-7.40)** |

Sign is negative at every horizon for both leaders, and every year 2022–2026 individually shows the same negative tilt at 1h/4h (year-by-year breakdown in `summary_stats.json`). Pct-positive sits at ~48-50% throughout (no directional skew at short horizons, a real skew appears only at 1h/4h and it's the *wrong* sign for the hypothesis).

**VERDICT: DEAD** for the stated momentum/diffusion hypothesis — followers do not chase the leader's residual innovation; if anything the broad ex-leader cross-section mildly fades it at 1h-4h, which is the opposite mechanism from what A12 proposed. That reversal is itself smaller than A13-H-E1's already-dead single-name reversion edge (-5 to -7bps gross before the 10bps cost, i.e. net around -15 to -17bps at 4h) so it isn't a live lead either — flagging it only as a possible adjacent idea for a future, separately pre-registered "fade the leader vs. broad follower basket" test, not claiming it here.

---

### What the `eligible_*` flags mean (from `/home/qbee/futur-data-v2/data_v2/events/eligibility.py`)

All four are **PnL-blind, pre-registered per-(symbol,timestamp) data-availability/causal-warmup gates** for four named alpha families from earlier research phases — never signals, never "is this an event," only "can this row even be observed given data completeness and causal warmup":
- `eligible_deleveraging`: OHLCV/OI/`oi_delta_pct_1h`/`aggressive_sell_usd`/`residual_return_1h`/`research_available_at` all non-null + causal 30d residual-std warmup. Liquidation feed intentionally not required.
- `eligible_crowding`: funding + `funding_rate_percentile_90d` + `basis_z_1d` + OI fields all present + a **genuine** 90-day funding-settlement warmup (distinct from the percentile column merely being non-null).
- `eligible_rvd_base`: per-symbol half of RVD eligibility (`residual_return_1h`, `basis_z_1d`, `signed_volume`, `research_available_at` present + causal 30d residual std) — no cross-sectional check yet.
- `eligible_rvd`: `eligible_rvd_base` **AND** cross-sectional population ≥ `MIN_CROSS_SECTION_SIZE=30` live eligible names at that same timestamp (a pre-registered, structural — not PnL-derived — threshold). This is the "Residual Value Dislocation" family's real eligibility flag.
- `eligible_ffr`: `residual_return_15m`/OI/`oi_delta_pct_1h`/`research_available_at` present + at least one of `{liq_feed_available, signed_volume present}`.

`eligible_rvd` was used exclusively, for both A13-H-E1's tradeable universe and A12's follower universe (drawn from the same gate) — not `eligible_deleveraging`/`eligible_crowding`/`eligible_ffr`, which belong to unrelated alpha families. This bought two things for free: strict causal warmup on the residual-std normalizer, and the pre-registered ≥30-name cross-sectional floor needed for a stable percentile statistic.

### PIT / survivorship check

Verified directly against the data rather than left unresolved: `LUNAUSDT` (Terra/Luna collapse) has rows only through **2022-05-13 06:45 UTC** — the real depeg/delisting date — with no `year=2023+` folder at all; newly-listed names like `AEVOUSDT`/`AIUSDT` only begin at `year=2024` (their real listing year). The pipeline unions whatever `(symbol,timestamp)` rows physically exist per year rather than applying a fixed "current" symbol list retroactively, so both delisting and new-listing dynamics are captured correctly — **no survivorship bias**, confirmed rather than merely assumed.

### Rules compliance

Sealed-experiment locations (grepped to know what to avoid, never opened): A13-H under `/home/qbee/futur-merge-main/{docs/A13H_PREREGISTRATION.md, scripts/run_a13h_backtest.py, scripts/build_a13h_panel.py, reports/alpha_foundry_v5/a13h_backtest/VERDICT.md}`; A2-RV reference at `/home/qbee/futur-merge-main/market_physics_v3/a2rv_execution.py` (not opened — only the sibling, non-sealed `phase5_2_execution_economics.py` was read, purely for the shared TAKER_FEE_BPS convention). No files under `reports/paper_trading/**`, `reports/alpha20/**`, `reports/paper_live/**`, `reports/liq_cascade/**`, `hedge_fund/**` were touched. All source data was read-only.
