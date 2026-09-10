# event_listing_perp_fade_v1 — first look result (regard seq 8, 2026-09-10)

Prereg `event_listing_perp_fade_v1_PREREG.md` (commit 7703101, pushed) · universe sha `cbb5d9619a01f05c…` (174 events) ·
threshold_t(5) = 2.3263 · taker cost 24 bps, wall net ≥ 72 · budget 2 → 1.

| horizon | n | mean | median | SE | t | win | N_eff | top-1 |
|---|---|---|---|---|---|---|---|---|
| **6 h (primary)** | 174 | **+251** | **+230** | 119 | **2.12** | 0.57 | 93 | 0.06 |
| 60 min | 174 | +67 | +93 | 59 | 1.12 | 0.59 | 87 | 0.04 |
| 24 h | 174 | +386 | +404 | 185 | 2.09 | 0.63 | 90 | 0.04 |

**Verdict: `INDECIDABLE` — t 2.12 < threshold 2.3263.** Every other criterion passed: N 174 ≥ 30, timestamps
reliable (1.1 % excluded), median +230 ≥ 30, net taker +228 ≥ 72 (maker +234), N_eff 93 ≥ 30, top-1 6 %,
capacity median 6-h quote volume 55 M$ (p10 14 M$): a 100 k$ short is 0.2 % of the window.

By launch year (mean, n): 2022: -642 (1), 2023: -57 (11), 2024: +322 (26), 2025: +249 (85), 2026: +303 (51).
First 15 minutes (launch → entry) mean excess +228 bps: the opening pump, then the fade.

## Why INDECIDABLE and not more

The dispersion is the fact of the day: σ of the 6-h excess = 1 546 bps (quantiles 10/25/75/90:
−1 536 / −527 / +1 151 / +2 184). The prereg assumed 400. With 174 events the SE is 119 bps and the
minimum detectable effect 276 bps; a +250 bps effect lands at t 2.1 by construction. The sign test
gives the same answer (57 % wins, z 1.85). Instrument checked on SXT (+3 233 bps at +16 min → −4 544
at 6 h), STABLE, BRETT (median event): first Vision bar = launch minute, 60 bars in the first hour, paths
real. A forward window would need ≈ 200 new perp-first listings (≈ 4 years) to decide at this σ.

## Consequences

- Same picture as H2 spot: the listing headline is bought in minutes and given back over hours; on
  the perpetual the short is executable and cheap, but the outcome per event is a coin with a large
  positive drift, not a steady edge. No promotion, no seal from this look.
- Not to be done: another horizon, a trimmed mean, a volume filter on these 174 events. That would be
  a second look.
- The last test goes to P3B (forced flow), as planned.
