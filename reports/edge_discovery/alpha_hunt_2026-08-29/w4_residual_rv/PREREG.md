# Pre-registration (written before any result is examined)

## A13-H-E1 — Cross-sectional residual RV, contemporaneous-percentile trigger

- MECHANISM: idiosyncratic (already factor/beta-neutral) residual returns occasionally
  diverge sharply from the rest of the live cross-section because of transient,
  symbol-specific order-flow imbalance (concentrated liquidation, single-name panic,
  temporary inventory pressure). Because the divergence survives after the common
  factor is stripped out, it is not "new information" and should decay back toward
  the cross-sectional center as liquidity providers unwind the imbalance.
- PAYER: whoever generated the urgent, price-insensitive one-sided flow (forced
  deleveraging / single-name liquidation cascade / panic flow) pays the RV trader's
  compensation for absorbing it.
- WHY EDGE EXISTS: monitoring beta-neutral residual dispersion across 150-200 live
  perps simultaneously, causally, is infrastructure-heavy; on the other side, the
  panel already gates realism into this via `eligible_rvd` (min 30 live names,
  causal 30d warmup) — so any edge here is not free, it competes with real RV desks.
- SIGNAL: z_i,t = residual_return_1h_i,t / residual_std_30d_i,t, restricted to
  eligible_rvd==True rows only (both inputs are the pre-registered causal columns
  already in the panel: residual_std_30d is a shift(1)+full-window rolling std, so
  z uses no same-bar or future information). rank_pct_i,t = cross-sectional
  percentile rank of z among all eligible names AT THE SAME BAR t (contemporaneous,
  hence causal — no future bar is used to define the threshold, unlike a full-sample
  ex-post percentile).
- ENTRY (causal threshold): flag extreme when rank_pct<=0.02 (bottom 2%, "cheap" ->
  LONG) or rank_pct>=0.98 (top 2%, "rich" -> SHORT). Decision is made using bar t's
  data (available at research_available_at = t+305s, i.e. before bar t+1 starts);
  actual entry executes at bar t+1. Only one open trade per symbol at a time (a
  name already in a live trade cannot re-trigger a new entry until it exits).
- TRADE: name vs a leave-one-out equal-weighted basket of the rest of the
  eligible_rvd universe at entry time ("or vs a basket" variant) — avoids arbitrary
  1:1 pairing when tail counts differ side to side, and diversifies the hedge leg.
  Long name = short basket (dollar-neutral, $1 vs $1); short name = long basket.
- EXIT: convergence-to-median (rank_pct back in [0.40,0.60] on any later bar) OR a
  fixed timeout of 48 bars = 4 hours, whichever comes first. 4h is chosen because
  it is the stated OUTER edge of this alpha family's own pre-declared horizon window
  (1min-4h per the catalog entry for "A13 Residual relative value") — not picked
  after looking at returns.
- EXECUTION VENUE: binance (the only venue in this panel).
- EXPECTED HORIZON: minutes to 4h (bounded by the timeout).
- EXPECTED CAPACITY: bounded by the single concentrated name leg (thin small/mid
  caps), estimated at ~1% of that name's typical 5-min traded dollar volume
  (aggressive_buy_usd+aggressive_sell_usd) at entry — a conservative
  participation-rate heuristic since no L2/order-book data exists in this panel.
- COST MODEL: turnover-based, 4 total unit-notional legs per round trip (entry:
  name+basket = 2 units, exit: name+basket = 2 units), Binance public taker fee
  5.0bps/fill (project convention, matches market_physics_v3/phase5_2_execution_
  economics.py TAKER_FEE_BPS["binance"]) => flat 20bps/trade fee cost. No spread/
  slippage model exists in this panel (no L2 data) — net numbers are therefore an
  OPTIMISTIC upper bound on real net edge; flagged explicitly.
- MAIN FAILURE MODE: the "extreme" residual move is not noise/inventory pressure but
  a genuine idiosyncratic repricing (delisting risk, hack, exchange-specific
  liquidity shock) that never reverts — tail risk on the concentrated name leg.

## A12 — Leader-innovation -> follower residual catch-up

- MECHANISM: BTC/ETH residual shocks (idiosyncratic within-name innovations that
  still carry systemic risk-sentiment content) diffuse into smaller-cap follower
  perps with a lag because followers are thinner and less continuously watched.
- PAYER: slow cross-asset repricing / attention-constrained participants in
  smaller names.
- WHY EDGE EXISTS: leader information is public and instant; the alleged edge is
  purely a SPEED/ATTENTION lag in followers repricing the same shock, measurable
  in bars at 5m resolution.
- SIGNAL: leader_z_t = residual_return_1h_t / residual_std_30d_t for BTCUSDT and
  ETHUSDT (own history, causal). Large innovation = |leader_z_t| >= 2.0.
- ENTRY: at bar t+1 (after leader bar t's research_available_at has passed), take a
  position in the SAME direction as the leader's move, sized across the
  leave-one-out equal-weighted basket of the rest of the eligible_rvd universe
  (excluding the leader) — already beta-neutral by construction (these are
  residual returns), no separate hedge leg needed.
- EXIT: fixed horizons only (this is a diffusion/momentum bet, not mean reversion)
  — 5m/15m/1h/4h (1/3/12/48 bars). Sub-minute (100ms) horizons are BLOCKED_DATA:
  the panel is 5-minute bars, no intrabar data exists.
- EXECUTION VENUE: binance.
- EXPECTED CAPACITY: bounded by the basket's aggregate thinnest names; reported as
  a distribution, not a single number.
- COST MODEL: single-sided basket trade (no explicit hedge leg — residual returns
  are already market-neutral), 2 unit-notional legs per round trip (entry+exit),
  flat 10bps/trade fee cost (same 5.0bps binance taker convention).
- MAIN FAILURE MODE: overlapping/autocorrelated events (leader stays "extreme" for
  several consecutive bars) inflate apparent n and understate true independent
  sample size; reported with this caveat, not corrected via a full HAC estimator
  (fast triage, not final validation).

Thresholds (p=0.02 tails, convergence band [0.40,0.60], timeout=48 bars, leader
z=2.0) are fixed BEFORE any result is examined and are not retuned afterward.
