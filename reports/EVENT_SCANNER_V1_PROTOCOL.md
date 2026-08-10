# EVENT_SCANNER_V1_PROTOCOL

Pre-registered 2026-08-09, **before** DATA_V2_READY and before any event has
been scanned or looked at. This document freezes the four event families,
their detection thresholds, the four horizons, and the statistics/
classification rules. Nothing in this file may be changed after the scan
has been run once real data is used — that is the entire point of
pre-registration (same discipline already used on this project for
FUNDING_XVENUE_PROTOCOL, HL_METAORDERS_PROTOCOL, stress_gate_dispersion_v2).
If a threshold turns out to be wrong, that is a finding for
EVENT_SCANNER_V2, not a same-run edit.

**Order of operations this protocol depends on** (see memory
project-data-v2-rebuild): the four P0 backfills (OI Vision 5m, perp 5m,
spot 5m, aggTrades flow 1m/5m) must be complete, basis must be rebuilt from
zero against the complete corpus (not the partial 12-20 symbol files built
mid-backfill), and `reports/DATA_V2_READINESS.json` must report
`DATA_V2_READY: true` under `data_v2.validation.validator`'s
`strict_alpha_readiness` mode. Running this scanner before that point
produces numbers that describe an incomplete corpus, not the market — do
not report them as edge findings.

## Input feature frame (per symbol, 5m bars, causal)

Every family below reads from one joined frame per symbol with these
columns (built by `data_v2/events/schema.py::REQUIRED_COLUMNS`):

| column | source |
|---|---|
| `open` | perp_5m — the entry bar's fair tradeable price, see "Labels" |
| `close` | perp_5m |
| `oi` | oi_vision_5m (`sum_open_interest`) |
| `oi_delta_pct_1h` | oi.pct_change(12) |
| `aggressive_buy_usd`, `aggressive_sell_usd`, `signed_volume`, `CVD` | agg_trades_flow_5m |
| `funding_rate` | funding (discrete, forward-filled only between real settlements, never interpolated) |
| `basis` (`perp_spot_basis`), `basis_z_1d`, `basis_z_7d` | data_v2/features/basis.py |
| `residual_logret_5m`, `residual_return_15m`, `residual_return_1h` | causal beta-hedge vs BTC/ETH, see below (`data_v2/events/residuals.py`) |
| `research_available_at` | `data_v2/temporal/available_at.py` — the causal cutoff labels start from, see "Four horizons only" |
| `liq_feed_available` | per-bar bool: was the declared-liquidation feed actually up at this bar? See amendment round 4, item 11 |
| `liq_long_usd_5m`, `liq_short_usd_5m` | declared liquidations (Bybit/OKX live since 2026-07-04 — DATA_READY_WITH_INFERRED_LIQUIDATIONS per prior audit; optional confirmation signal only, never a gate, per that audit's own design) |

For BTC/ETH themselves, residual = raw return at each frequency (no
benchmark to regress against). For every other symbol, a causal 2-factor
regression of 1h returns against BTC and ETH 1h returns (60-day trailing
window, shift(1) so beta at t only uses data through t-1) gives `beta_btc`,
`beta_eth`; the SAME pair of betas is applied to the 5m/15m/1h return
series to get `residual_logret_5m`/`residual_return_15m`/
`residual_return_1h` = actual return − (beta_btc × BTC return + beta_eth ×
ETH return) at that frequency. Betas are refit once per UTC calendar day
(at that day's first bar, from data through the prior day) and held frozen
for the rest of the day, matching this section's original wording — see
amendment round 4, item 10, for why an interim continuous-refit version
was reverted.

## Four families (exactly these, no fifth added mid-scan)

### 1. DELEVERAGING
```
price_residual_1h        <= -1.5 * rolling_std(price_residual_1h, 30d)
oi_delta_pct_1h           <= -3%
aggressive_sell_usd_1h  ranked >= P90 (90th percentile, trailing 30d, same symbol)
volume_1h                ranked >= P80 (trailing 30d, same symbol)
liq_long_usd_5m > 0                                    [optional confirmation, not required to fire]
```
Fires once per symbol per 4h cooldown window (no re-firing on adjacent bars
of the same unwind).

### 2. CROWDING
```
|funding_rate| at last settlement  ranked >= P90 (trailing 90d, same symbol)
|basis_z_1d|                        >= 2.0
oi_delta_pct_1h                     >= +3%
sign(aggressive_buy_usd - aggressive_sell_usd) == sign(funding_rate)
```
i.e. aggressive flow is piling INTO the crowded side, not fading it.

### 3. RELATIVE_VALUE_DISLOCATION
```
|residual_return_1h|                >= 2.0 * rolling_std(residual_return_1h, 30d)
|relative_basis_z|                  >= 2.0   (basis_z_1d minus the cross-sectional median basis_z_1d that bar)
|relative_flow_z|                   >= 2.0   (signed_volume z-scored cross-sectionally that bar)
sign(relative_basis_z) == sign(relative_flow_z) == sign(residual_return_1h)
```

### 4. FORCED_FLOW_REVERSAL
```
liq_long_usd_5m + liq_short_usd_5m  ranked >= P95 (trailing 30d, same symbol)
   OR |signed_volume_5m| ranked >= P95 (trailing 30d) if liquidation feed absent/thin for this symbol
oi_delta_pct_1h                     <= -5%   (collapse, sharper than DELEVERAGING's -3%)
price_residual shock                 |price_residual_15m| >= 2.5 * rolling_std(price_residual_15m, 30d)
```
The liquidation-vs-flow choice is made PER BAR from `liq_feed_available`
(amendment round 4, item 11), not once for the whole symbol: a bar where
the feed was down uses the flow fallback even if a later bar of the same
symbol has the feed up and uses the liquidation rank instead.

All percentile/z-score lookbacks are trailing and causal (never include the
current or future bar). All four families are evaluated independently —
an event can belong to more than one family on the same bar; both are
recorded, not deduplicated.

## Four horizons only

`15m`, `1h`, `4h`, `8h` — measured from the triggering bar's
`research_available_at` (data_v2.temporal.available_at), not its raw
timestamp. No other horizon is computed in V1.

## Labels (data_v2/events/labels.py)

Per event, per horizon: `residual_ret_h`, `MFE_h` (max favorable excursion
in residual-return space over [0, h]), `MAE_h` (max adverse excursion),
`time_to_MFE_h`.

**Entry point**: the first bar whose OWN timestamp is `>=` the event's
`research_available_at` — i.e. the triggering bar's own 5m move (whose
close is what produced the trigger condition) is excluded from every
horizon's forward path; a signal produced at close 10:05 can never benefit
from the 10:00→10:05 move that triggered it. The entry price used for cost
purposes (see "Cost model") is that entry bar's OWN OPEN, not its close —
the fair tradeable price before that bar's own move has happened
(amendment round 4, item 9).

**Base increment**: horizons are built by SUMMING `residual_logret_5m`
(non-overlapping 5m log-returns) over the entry bar and the following
`n-1` bars — 15m = 3 bars, 1h = 12, 4h = 48, 8h = 96 — then `expm1()` back
to a simple return. Horizons are never built by cumsumming an already-
overlapping rolling return (e.g. `residual_return_1h`) sampled every 5m;
that would sum ~12 heavily-overlapping 1h windows on top of each other.

**Direction convention** (fixed by MECHANISM, not by a static sign per
family — see amendment log below):
- `DELEVERAGING`: always `+1` (long) — the family is defined as fading a
  down-shock, there is no symmetric case.
- `CROWDING`: `crowded_side == "long" → -1`, `crowded_side == "short" →
  +1` — fade whichever side the funding/basis/flow triggered as crowded,
  captured at detection time, never assumed.
- `RELATIVE_VALUE_DISLOCATION`, `FORCED_FLOW_REVERSAL`: `-sign(trigger_
  residual)` (RVD: `residual_return_1h` at trigger; FFR:
  `residual_return_15m` at trigger) — fade whichever direction the
  residual actually dislocated in, captured at detection time.

This mechanism is fixed by this protocol, not chosen after seeing which
sign was profitable — but it now reads the ACTUAL triggering side per
event rather than assuming one fixed sign for the whole family (see
amendment log).

## Cost model (data_v2/events/costs.py)

**Primary**: per-event `cost_x1 = 2×taker_fee_rate + 2×(tick_size /
entry_price)` (2 sides, each paying one taker fee and losing one tick to
slippage), `cost_x2 = 2×cost_x1`. `taker_fee_rate` defaults to 5bp
(`configs/alpha20.yaml`'s binance_usdm taker convention); `tick_size` and
`entry_price` come from `data_v2/instruments/instrument_master.parquet`
and the event's own entry bar's OPEN, respectively — cost is symbol- and
price-level-specific, never a single number applied to every event
regardless of venue/price. **Secondary, reported alongside but never
substituted for the primary figure**: a flat stress cost (`STRESS_COST_X1`
=30bp, `STRESS_COST_X2`=60bp, this project's existing `cost_rt`
convention, e.g. `scripts/backtest_ctrend_v1.py`) — useful as a blunt
sensitivity check, not as the classification input.

**Cost coverage gate** (amendment round 4, item 8): `event_cost_x1` is NaN
for any event whose symbol has no resolvable `tick_size` (an
InstrumentMaster gap) — its net-of-cost stats are computed only over the
events that DO have a cost, tracked per (family, horizon) as `costable_n`/
`cost_coverage = costable_n / n`. A family can never reach CANDIDATE while
`cost_coverage < 100%` on `PRIMARY_CLASSIFICATION_HORIZON`, however
positive the costable subset looks — see "Classification".

## Statistics computed per (family, horizon)

`N`, gross expectancy (mean residual_ret_h), net expectancy (gross − cost,
both the primary per-event cost and the secondary stress cost, reported
separately), win_rate, PF (gross profit / gross loss), mean MFE, mean MAE,
split by calendar year (2022/2023/2024/2025/2026 — a year with <20 events
is reported but flagged low-N, not hidden), split by asset tier (BTC, ETH,
large alts = top 20 by 30d median quote volume at event time, small alts =
the rest — tier assigned causally at event time from the same PIT
universe, not today's ranking).

## Classification (assigned per family, on `PRIMARY_CLASSIFICATION_HORIZON`
only — see amendment log)

All three rules below apply to `PRIMARY_CLASSIFICATION_HORIZON` (1h, see
amendment log) only. 15m/4h/8h are computed, reported, and eyeballed for
stability, but never substituted into these rules — a family that fails on
1h stays failed even if e.g. 4h looks better; rescuing a family that way
would reopen exactly the multiple-testing problem pre-registration exists
to close.

- **KILL**: net expectancy (cost×1, primary per-event cost) <= 0 in the
  pooled 1h sample, OR sign flips between any two of the 4-5 calendar years
  with >=20 events each (1h), OR N < 100 pooled across the full history
  (1h).
- **WEAK**: net expectancy (cost×1) > 0 pooled AND consistent sign year-
  over-year (1h), but net expectancy (cost×2) <= 0, OR PF < 1.15 (1h).
- **CANDIDATE**: net expectancy (cost×2) > 0, PF >= 1.15, consistent sign
  across years with >=20 events, N >= 100 pooled, AND `cost_coverage ==
  100%` (all on 1h, see "Cost coverage gate" above — amendment round 4).
  A CANDIDATE is what "merits ML" per this project's standing rule (memory
  project-data-v2-rebuild) — it is not itself a green light to deploy
  capital (see project_new_edges_phase.md's own independence/robustness
  bar for that decision, applied separately once a family reaches
  CANDIDATE here).

No machine learning anywhere in this scan. No threshold in this document
may be tuned after results are seen — a family that scores KILL under
these exact thresholds is dead under this protocol; re-testing it with
adjusted thresholds is a new, separately-named protocol (V2), not an edit
to this file, matching this project's existing rule against re-testing
NO_EDGE_DEFINITIF findings.

## Amendment log

All amendments below were made **before** `DATA_V2_READY: true` and before
this scanner has ever been run against real data (`reports/
DATA_V2_READINESS.json` confirms `DATA_V2_READY: false` at every amendment
timestamp) — i.e. before unblinding, not after seeing results. This is the
correct time to fix a methodological error in a pre-registered protocol;
the document's own no-edits-after-the-scan rule is about not tuning
thresholds once economic results are visible, which has not happened yet.

**2026-08-10, pre-unblinding review (round 3)** — found by external review
of the CODE against this document, not by seeing any scan output:
1. Trailing percentile-rank/std thresholds in `data_v2/events/detectors.py`
   were including the current bar in its own threshold window (circular).
   Fixed to compare the current bar only against `t-1` and earlier.
2. `FORCED_FLOW_REVERSAL`'s `price_residual_15m` was computed as
   `residual_return_1h / 4` (a placeholder, flagged as such in its own
   code comment) instead of a genuine 15m residual. Fixed — see
   `data_v2/events/residuals.py`, added to the Input feature frame table
   above.
3. Labels summed overlapping `residual_return_1h` samples taken every 5m
   as if they were independent marginal returns. Fixed to non-overlapping
   `residual_logret_5m` increments (Labels section above).
4. Direction was a fixed +1/-1 constant per family for CROWDING/
   RELATIVE_VALUE_DISLOCATION/FORCED_FLOW_REVERSAL, all of which can
   trigger on either side — a fixed sign could silently cancel a real
   symmetric edge into a fake NO_EDGE. Fixed to read the actual triggering
   side per event (Labels section above).
5. Labels started from the triggering bar's raw timestamp instead of its
   `research_available_at`, letting the very move that produced the
   trigger count a second time as "forward" performance. Fixed (Labels
   section above); `research_available_at` added to the Input feature
   frame table.
6. Cost model was a flat 30/60bp constant for every event regardless of
   symbol/price. Fixed to a per-event formula from real
   `fee_rate`/`tick_size`/`entry_price`; the flat figure survives only as
   an explicitly separate secondary stress test (Cost model section
   above).
7. Classification silently picked `by_horizon["1h"]` with no protocol
   statement that 1h was primary — risked "the best horizon was actually
   4h" being chosen post-hoc. Fixed: `PRIMARY_CLASSIFICATION_HORIZON = 1h`
   stated explicitly (Classification section above); 15m/4h/8h are
   diagnostics only, by this amendment, permanently.
8. `reports/DATA_V2_READINESS.json`'s `funding_coverage_100pct` gate was
   hardcoded `True` instead of measured. Fixed to a real scan of
   `data/derivatives_backfill/binance/funding/`.
9. Spot-market absence (`spot_5m` dataset) was inferred from "no file on
   disk", which cannot distinguish "genuinely no spot market" from "not
   backfilled yet" — the latter would silently vanish from the coverage
   denominator. Fixed to require proof (every expected month attempted,
   confirmed 404) before marking `NOT_APPLICABLE`.

No detection threshold, cooldown, or classification cutoff number changed
in this amendment — only the CORRECTNESS of how each was computed. Items
1-9 are bug fixes to match this document's own stated intent (e.g. "never
include the current or future bar" was already written above before this
amendment; the code simply didn't do it yet).

**2026-08-10, pre-unblinding review (round 4, PREUNBLINDING_R4_FINAL)** —
same standing as round 3: found by external review of the CODE against
this document and against `reports/DATA_V2_READINESS.json` (still
`DATA_V2_READY: false`), not by seeing any scan output:

8. `_classify` (`data_v2/events/scanner.py`) could reach CANDIDATE off a
   minority of a family's detected events: net-of-cost stats are means
   over only the events with a resolvable per-event cost
   (`event_cost_x1`/`x2` NaN wherever `tick_size` is unresolved in
   InstrumentMaster), but the pooled `n` used for the `N >= 100` gate
   counted ALL detected events regardless. A family with 500 detected
   events and only 200 costable could pass on the 200 alone while
   reporting N=500. Fixed: `costable_n`/`cost_coverage` are now tracked
   per (family, horizon), and `cost_coverage == 100%` is a hard,
   independent requirement for CANDIDATE (see "Cost coverage gate" above)
   — a family with any uncostable event caps at WEAK, however strong the
   costable subset's own stats look.
9. DELEVERAGING's `liq_confirmed` and FORCED_FLOW_REVERSAL's liquidation-
   vs-flow branch both keyed off column PRESENCE
   (`"liq_long_usd_5m" in df.columns`), which cannot distinguish "the
   liquidation feed was down for this bar" from "the feed was up and saw
   zero liquidations" — the feed only exists from 2026-07-04 per the
   Input feature frame table above, so every bar before that date (and any
   bar where the feed was genuinely down) was silently read as "checked,
   zero liquidations" rather than "unknown". Fixed: a new required
   `liq_feed_available` column (per bar, bool) is read directly.
   `liq_confirmed` is now a nullable bool (`<NA>` when the feed was down at
   that bar, real True/False only when it was up); FORCED_FLOW_REVERSAL
   picks the liquidation-rank or flow-rank threshold PER BAR from this
   column instead of once for the whole symbol.
10. `data_v2/events/residuals.py`'s betas were refit every bar, documented
    at the time (round 3) as a deliberate, noted deviation from this
    section's "60-day trailing window ... betas refit every bar rather
    than literally once/day-and-frozen" implementation note. Reverted:
    betas are now computed once per UTC calendar day (at that day's first
    bar, from a causal rolling window through the prior day) and frozen
    for the rest of the day, matching the section's actual prose. Still
    causal (the frozen value only ever depends on data strictly before the
    day it's used in); the first calendar day of any panel has no prior
    day to freeze from and is legitimately residual=NaN end to end.
11. RELATIVE_VALUE_DISLOCATION's `research_available_at` was read from a
    single, arbitrary symbol (`panel[symbols[0]]`) for every fired event
    regardless of which symbol actually fired — silently assuming every
    symbol in the panel becomes knowable at the same instant. Since a
    fired event depends on a cross-sectional stat (median/mean/std) built
    from every symbol with real data at that bar, the event cannot be
    knowable before the SLOWEST contributing symbol's own
    `research_available_at`. Fixed to the row-wise MAX of
    `research_available_at` over exactly the symbols that contributed a
    non-NaN value to that bar's residual/basis_z/flow computation.
12. `_min_periods` (`data_v2/events/detectors.py`) capped the warm-up
    requirement at 20 observations regardless of the nominal 30d/60d/90d
    window (`min(window_bars, 20)`) — so a rolling std/percentile could
    already gate a detection right after a symbol's listing or the start
    of the backfilled history, off ~100 minutes of data standing in for a
    30/60/90-day baseline. Fixed: the default is now the FULL nominal
    window (complete warm-up); a `min_periods_override` parameter exists
    solely so unit tests can exercise short synthetic panels — production
    call sites (the real scan) must never pass it.
13. `data_v2/events/labels.py` priced `entry_price` from the entry bar's
    CLOSE, i.e. the price AFTER that bar's own move had already happened
    — overstating how favorably a real order could have been filled and
    understating `event_cost_x1`/`x2`'s slippage term. Fixed to the entry
    bar's OWN OPEN (added as a required column, `open`, in schema.py).
    Only the cost figure is affected — the return path itself is built
    from `residual_logret_5m` increments and does not depend on the entry
    price level.
14. `data_v2/features/basis.py`'s `basis_z_1d`/`basis_z_7d` rolled the mean
    /std directly over `basis` (the bar's own current value included in
    the window it was then judged against — the same circularity already
    fixed for the event detectors in round 3, item 1, but never applied
    here), with `min_periods = window // 3` (a third of a day/week
    standing in for a full day/week). Fixed to `basis.shift(1)` before the
    rolling mean/std (strictly the prior 288/2016 bars) with
    `min_periods` = the full window — the first day (resp. week) of any
    symbol's basis history is now legitimately NaN rather than an
    unreliable early estimate.

No detection threshold, cooldown, or classification cutoff number changed
in this amendment either — items 8-14 are, again, CORRECTNESS fixes to
match this document's stated intent, not new methodology.
