# PROJECT TRUTH — 2026-09-10

**Validated sleeves: 0.** Tradable edges: 0. Live edges: 0. Capital deployable: **false.**

No candidate in the history of this repository has passed all of: internal gates,
sealed window at its family threshold, forward window, costs, robustness,
multiplicity, executable path. Several passed some. None passed all.

## The only surviving hypothesis — and its three statuses, which are not one

Crowd positioning on Binance USDT-M perps (`count_long_short_ratio`, contrarian).
Three *rules* exist for this one *mechanism*, and they do not share a verdict:

| rule | where | verdict |
|---|---|---|
| sweep rule `lsr_globacct_x \| mkt \| h3 k8 hd1` (top-120, close→close, 3-day) | `tools/alpha_sweep_v4.py`, `reports/loop/` | **INDECIDABLE** — in-sample t 4.06, sealed-window t 2.399 against an operative two-sided bar of 2.955 (n=16, harness re-chose the side: defect I13). Passes cribles 5/6/7 (286.6 effective episodes in the top decile). Arithmetic Sharpe 2.43 IS / **1.66 OOS**; half of it is momentum. |
| kernel rule `lsr_globacct_x_v2` (top-100, 15v15, open→close, 2-day step) | `mechanisms/lsr_globacct_x_v2/` | **COST_WALL** — gross 9.60 bps < 3 × cost 16.00 bps. Family `crowd_positioning`, 88 trials, threshold 3.254. Data 2021-12-02 → 2026-08-12. |
| forward rule `FORWARD_CROWD_POSITIONING_V1` (deciles, daily, capacity universe) | `reports/loop/prereg/`, orphan branch `prereg/forward-crowd-positioning-v1` @ `0de75a9` | **SEALED_FORWARD_ACTIVE** — n=1, one-sided threshold 1.6449, window from 2026-09-09, **one look, not before 2028-12-06** (819 usable days). Code pinned (tag `prereg-forward-v1-code`). NO_LIVE. |

Status of the mechanism as a whole: **INDECIDABLE / FORWARD_WATCH_ONLY / NO_LIVE.**
It is not tradable and it is not validated. Nobody looks at the forward window before the date.

## Dead / rejected (measured, not assumed)

| family | how it died |
|---|---|
| basis perp/spot | 16 signals, payers written first: best t 2.49 < placebo median 2.81; best one is ρ 0.69 with positioning |
| CME segmentation | `CME_SEGMENTATION_V1`, preregistered n=2, one look: t_net 1.577 < 1.960; gross would pass, net was the criterion; secondary prediction (post-ETF compression) consistent; DD −30 % > 25 % |
| Bitfinex margin funding | examined under the sealed selection rule, refused at condition 3: same payer as perp funding |
| funding (level, cross-venue spread, term structure) | measured in all three forms; nothing |
| amihud / illiquidity | is the cost model (ρ 0.964 with `symbol_cost_bps`); capacity-blocked; `roll_spread` is an alphabetical list |
| cross-exchange dislocation | `cross_exchange_dislocation_v1`: **COST_WALL**, gross 1.97 bps < 3 × 24.00 |
| microstructure imbalance | `microstructure_imbalance_v1`: **COST_WALL**, gross 0.78 bps < 3 × 12.55 |
| liquidation cascades | inexecutable by architecture: 45–48 h data lag against a 4 h horizon |
| SHORT | SHORT_REJECTED, three approaches |
| event scanner | 4/4 KILL |
| hunt rounds 1–4 | ~700 mechanisms, 0 validated |
| options VRP, stablecoin regime | measured before this rebuild, never promoted |
| `close_location_20` | the best number the project ever produced (t 3.28) was a 50-symbol artefact: sign flips on 696 symbols |

## What bounds everything

- The library's 138 effective trials (rank-content dedup) match the placebo's implied N to
  the unit (median max|t| 2.8069 ⟹ N = 138). Nothing ever exceeded that noise median.
- Maximum of the book: arithmetic Sharpe **1.66** out of sample. Identity found twice here:
  *confirmable in N years ⟺ Sharpe ≥ 5.60/√N.* The historical shortcut ("one look on a
  never-loaded source") was tried honestly on the only class where it was valid (CME) and
  failed. The calendar is the forward calendar.
- The counts 16 (sealed reads) and 138 are **reconstructions from artefacts** — lower
  bounds. From 2026-09-09 the look ledger is written *before* the look (`tools/look_ledger.py`,
  hash-chained), so the count is exact going forward, never retroactively.

## Two ledgers, one truth

| ledger | governs | file |
|---|---|---|
| kernel multiplicity ledger | trials per **family** on historical data; burned periods; family threshold | `reports/research_kernel/multiplicity_ledger.json` |
| look ledger | every **sealed/forward look**, with the external witness (orphan branch pushed alone) | `reports/loop/LOOK_LEDGER.jsonl` |

They agree on doctrine (multiplicity applies to tests on the same data; a fresh window
restarts the count) and on the crowd-positioning burn. One declared overlap: the kernel
burns the family through 2026-09-10; the forward seal starts 2026-09-09. Two days of 819,
for which no return was computable at seal time. Declared, not re-sealed.

**Operational rule for 2028-12-06:** the single look on `FORWARD_CROWD_POSITIONING_V1` is
recorded in **both** ledgers — `tools/look_ledger.py` (before the computation, witness
required) and `MultiplicityLedger.record_trial('crowd_positioning', ...)` with the forward
window as the data it was run on. One look, two records, one truth.

## Current project asset

Validation infrastructure (`research_kernel/`, 200 tests, gates G0–G8), the look ledger
with external witnesses, the defect history (21 instrument defects closed, four of which
produced false numbers), the falsification system, and a positioning hypothesis with one
clean chance in 2028.

## Current project missing

A structural edge. An executable alpha. A deployable sleeve.

Until that changes: **no live, no leverage, no ML, no dashboard, no optimisation.**
The question is no longer "which signal works" but "which structural advantage exists
before costs, survives costs, placebo, multiplicity, then the forward."

## P1 — payer discovery (2026-09-10): where does real cost get low enough?

Measured on L1 quotes + trades, Binance / OKX / Hyperliquid, BTC/ETH/SOL, 2026-09-07 → 09-09
(`mechanisms/p1_payer_discovery_v1/`, `reports/mechanisms/p1_payer_discovery/venue_cost_table.md`).
Fees are published schedules, not measured on an account. Bybit has no data on disk: fees only.

| mode | round trip at VIP0 | at best published tier | gross needed (×3 wall) |
|---|---|---|---|
| taker | 9.5 – 11.3 bps | 4.2 – 5.3 bps | 12.5 – 34 bps |
| maker (end of queue, 30 s) | 3.0 – 5.1 bps | −0.6 – +2.7 bps | −1.9 – 8 bps |

**Microstructure: NO at VIP0, on every venue and mode** (all four VIP0 schedules official). The only
configurations under the 0.26 bps floor are maker-only on wide-tick instruments — final (official sources
only): **OKX SOL at VIP8** (−0.69 bps round trip, P(fill 30 s) 0.30, ≥ 2 G USD 30-day volume) and
**Hyperliquid** BTC/ETH/SOL at market-maker rebate tier 3 (P(fill) 0.16–0.24, > 3 % of platform maker
volume); Binance SOL at VIP9 is **non-final** (its VIP9 futures schedule and volume threshold could not be
read from an official page).
A negative maker floor there is not an edge: it is the half-tick a market maker earns *if filled*,
which is a liquidity-provision business, not the P0 signal.
**Cross-exchange: NO at any tier** — the dislocated leg is taken, and the cheapest two-leg round
trip is 4.09 bps against 0.66 needed.

Method findings that changed the numbers: the 1-second-grid "effective spread" overstated the
true effective half-spread ×7 (calibrated against exact prevailing quotes: 0.07 bps median,
0.45 mean on Binance BTC); the passive-side loss must be decomposed into drift lost minus half-spread
earned; the OKX VIP8 futures maker fee is officially **negative** — −0.5 bps until 2026-09-09, **−0.25 bps**
since (advance notice of 2026-09-09) — so the earlier "+0.8" was a third-party error, corrected by the P1.1
fee source audit (`reports/mechanisms/p1_payer_discovery/FEE_SOURCE_AUDIT.md`).

**Verdict: no terrain reopens for a 200 k$ account.** The cost door opens only at institutional
rebate tiers, in maker-only mode, on wide-tick instruments — and what it opens is market making.


## P3A — event first look (2026-09-10): the fast part is arbitraged, the slow part needs a short

One sealed look (LOOK_LEDGER seq 7) on the frozen official event tape, four preregistered
hypotheses, family `news`, threshold_t(4) = 2.2414. Verdicts, once, in `mechanisms/<id>/results/verdict.json`:

- `event_reaction_v1` (listing continuation, 60 min): **REJECTED_NO_GROSS** (−2 bps). The pump is inside the publication minute.
- `event_cross_venue_lag_v1` (OKX/Bybit → Binance, 15 min): **REJECTED_NO_GROSS** (−0.3 bps, SE 9.6).
- `event_listing_reversal_v1` (fade +15 min → 6 h): **INDECIDABLE** — +202 bps, t 3.25, broad and consistent since 2023, but a short on spot; not executable as specified.
- `event_delisting_pressure_v1` (delisting, 60 min): **INDECIDABLE** — +631 bps mean, 67 median, t 2.44, tradable on the perp, placebo zero, but N_eff 21.6 and five micro-caps carry the mean; declared cost is not the cost of size in those names.

Validated sleeves: 0 (unchanged). Capital deployable: **false.** No forward seal exists for these; candidates in `reports/loop/hypotheses/H-EVENT.md`. Details: `reports/first_look/EVENT_FIRST_LOOK_RESULTS.md`.


## P3A bis — H2 made executable (2026-09-10, regard seq 8)

`event_listing_perp_fade_v1`: short the new Binance perpetual from launch + 15 min to + 6 h, on 174 perp-first
listings never priced before (universe frozen from metadata, prereg pushed). Mean +251 bps, median +230,
net taker +228, capacity 55 M$ per window, N_eff 93 — **INDECIDABLE**: t 2.12 < threshold_t(5) 2.3263,
because the per-event dispersion is 1 546 bps. No promotion, no seal. Validated sleeves: 0. Capital deployable: **false.**


## P4 — market_state_tape (2026-09-11): a data source, not an alpha

P4 builds the missing link `event -> market state -> executable decision`, not a strategy. `data_lake/collectors/market_state_tape.py`
watches exchangeInfo (USDS-M every 5 s, spot every 60 s) and turns every diff into a `symbol_lifecycle_event` (birth, status
transition, onboardDate, shortability, death); it polls mark/index/funding (all symbols), open interest and 24 h tickers as light
`market_state_snapshot`s; on a trigger (new perp, status change, delisting, OI spike, funding extreme, liquidation burst, manual) it
captures the microstructure of one market (L2, trades, bookTicker, mark/index, OI, funding, latency) from t0 − 30 min when anticipated
to t0 + 6 h, with a hashed `triggered_window_manifest`. Everything is append-only; no signal, no verdict, no order, no join to any
result. Validated sleeves: 0. Capital deployable: **false.** Budget: 0. The tape does not credit budget (no new independent episodes).


## P5 — data completion router (2026-09-11): a map of what is missing, not an alpha

P5 turns the P4 collection into indices: `CURRENT_COLLECTION_INVENTORY` (what the tape holds, hashed, with a health
sheet), `H2_LAUNCH_COVERAGE_MATRIX` (one row per H2 launch: what is in hand, what is free to backfill, what needs a
provider, what is live-only, scored on 100 points) and `H2_PROVIDER_REQUEST_WINDOWS` (the targeted request to send
before paying anything). No price join, no signal, no verdict, no budget. Validated sleeves: 0. Capital deployable: **false.**


## P6–P10 — data completion (2026-09-11/12): the gate is closed, and the reason is named

Five phases of data completion, no test run, no budget consumed, no verdict changed.

- **P6** backfilled the 174 H2 launch windows from Binance Vision: 1 364 files, 1.47 GB, every sha256 verified against
  Vision's own `.CHECKSUM`, 173 of 174 windows with complete core state (mark, index reference, trades).
- **P7** archived 230 announcement bodies: 174 of 174 launch times and 56 of 56 delisting times, the launch time agreeing
  with the first traded bar within 5 minutes for 162 of 174. Instrument defect I22 is closed.
- **P8** built the read-only execution module (GET only, whitelisted endpoints, refuses a key that can trade). No key exists
  here, so the fee actually charged is **unknown**; the published VIP0 schedule is the strongest figure available.
- **P9** settled cross-venue precedence for 143 of 174 assets: **137 were already priced elsewhere** before the Binance
  perpetual opened (MEXC 113, median lead 11 days). Only 6 are first listings anywhere.
- **P10** froze the dataset and ran the decision gate: **NO TEST**, 0 clean events against a threshold of 80. Two blockers,
  both free to clear: no read-only key, and capacity never derived from the depth archives already on disk. If both were
  cleared, 129 events would be clean — of which 123 are `OTHER_VENUE_FIRST` and 6 `TRUE_BINANCE_PERP_FIRST`.

The finding that outlives the counts: `event_listing_perp_fade_v1` pooled two populations that are not the same
phenomenon. That does not re-open regard seq 8 (burned, INDECIDABLE) and produces no signal; it means a future
preregistration must state which population it tests. Validated sleeves: 0. Budget: 0. Capital deployable: **false.**
