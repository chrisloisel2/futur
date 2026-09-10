# H3 — delisting forced pressure: forward seal (SEALED_FORWARD_ACTIVE)

Sealed 2026-09-10 on `event_delisting_pressure_v1` after regard seq 7 left it **INDECIDABLE**
(n = 56, mean +631 bps / median +67 at 60 min, t 2.44, N_eff 21.6, top-1 13 %, 82 % tradable on the
perp, placebo +3, pre-publication drift −11). The effect is real on the events seen; the sample is
concentrated and the declared cost is not the cost of size in micro-caps. Only future events can decide.

- Canonical seal: `sealed_forwards/active/event_delisting_pressure_v1_2026-09-11.json` (research_kernel.forward_seal, hash `11035f26c9d55d5a…`),
  human copy `sealed_forwards/H3_DELISTING_PRESSURE_FORWARD.json`. Witness: this commit pushed to `origin`.
- Window: 2026-09-11 → 2028-09-11. Eligible events: official Binance asset delisting announcements
  (`Binance Will Delist …`, `Binance Futures Will Delist …`) **published after the seal**, collected by
  `futur-event-tape.timer`. No old event enters.
- Rules frozen (spec `rules_hash` 62b430899fd2f113…): entry = open of the first 1-min bar ≥ publication + 60 s,
  short, exit + 60 min, excess vs BTCUSDT spot, Binance perp if it existed ≥ 24 h before, else spot.
  No parameter may change; the harness of regard seq 7 (pinned) is the measuring instrument.
- One look, at the earlier of **30 eligible events with a pre-existing Binance perp** or the window end.
  Budget: 1 test at the look. Family `news`: 5 hypotheses sealed at the time of this seal (kernel multiplicity ledger), threshold_t recorded in the seal = 2.3263; the bar at the look is threshold_t(family size then).
- Promotion requires all of: N ≥ 30 new events · N_eff ≥ 30 · top-1 ≤ 20 % · median gross ≥ 30 bps ·
  gross ≥ 3 × **measured** executable cost (depth at ±10 bps at announcement, from the forced-flow / BBO
  tape on the names concerned) · after-cost gross ≥ 3 × cost · t ≥ family bar · executable short path
  documented (perp exists, borrow not needed).
- Kill: median < 30, gross < 3 × cost, placebo ≥ gross, pre-publication drift ≥ gross.
- Forbidden: interim peeking, parameter changes, old events, live trading.
- Not a short-term result: at the 2025 rate (≈ 22 delistings/yr, 82 % with a perp) the count is
  reached in ≈ 18–20 months.
