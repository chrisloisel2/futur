# W4_NEWS_SENTIMENT — PREREGISTRATION
Written 2026-09-03, **before** any outcome-bearing test was run.
Only data-*description* checks (row counts, schemas, coverage, feature distributions,
NaN rates) were performed before writing this file. No forward return, no bps, no
t-stat, no verdict was computed before this document was committed to disk.

## 0. Null hypothesis (stated first, on purpose)

**H0: there is nothing here.** Crypto sentiment is the most publicly mined axis in the
retail literature; Fear & Greed in particular has been the subject of thousands of public
backtests since 2018 and should be arbitraged as a directional signal. My prior is that
the honest deliverable of this worker is a well-evidenced KILL, and the report is written
to make a KILL cheap to reach and an edge expensive to claim.

I commit in advance: I will NOT rescue a dead mechanism by re-cutting the threshold.
Any threshold changed after seeing a result is stamped `REFIT` in the report.

## 1. Data inventory as measured (before tests)

| dataset | rows | real coverage | verdict on usability |
|---|---|---|---|
| `data/news_backfill/fear_greed.parquet` | 3,078 daily | **2018-02-01 → 2026-07-10**, 4 missing days | DEEP. The only sentiment series with statistical power. |
| `data/news_raw/date=*/` | **5,793 rows total** | continuous only **2026-07-10 → 2026-09-03 (56 days)**; the 15 partitions dated 2025-12-29..2026-01-23 all have file mtime 2026-07-11 and are RSS backlog, not observations | SHALLOW. ~28 items/day over 4 RSS feeds + CoinGecko trending. |
| `data/news_backfill/news_daily_{sent,vol}.parquet` | 73 days × 45 symbols | derived from news_raw, same 56-day limit, extremely sparse (most cells NaN) | SHALLOW |
| `data/events/liq_cascade_dataset.parquet` | 38,141 | 2021-01-04 → 2026-07-05, 49 symbols | DEEP, and overlaps F&G almost fully |
| `data/positioning/` (live LSR) | — | **2026-07-16 → 2026-09-03** | **ZERO overlap with F&G (ends 2026-07-10)** |
| `data/derivatives_backfill/binance_vision_metrics/` | 312 symbols 5m | 2025-09-17 → 2026-08-12 | ~10 months overlap with F&G |
| `liq_cascade.ls_ratio_z` | 1.2% NaN 2021-2026 | 2021-2026 | usable as the long-history positioning proxy |
| `liq_cascade.funding_z30` | **76% NaN** | 2021-2026 | N-limited |

**Consequence preregistered now:** every mechanism resting on `data/news_raw` is
`DATA_LIMITED` by construction (56 days, ~1,600 declustered articles, one market regime —
2026 is a uniform extreme-fear year, F&G mean 21.6). I will still run those tests, because
one of them (M9, latency) is a *timing* question that does not need long history and can
legitimately kill the axis. But I will not upgrade any 56-day result past `DATA_LIMITED`
on the strength of its bps.

## 2. The confound I must control for, declared up front

F&G regime is **massively collinear with calendar year**: 2026 mean 21.6, 2022 mean 25.3,
2024 mean 63.3, 2023 std only 11.0. A raw "extreme fear vs extreme greed" split is
therefore largely a **2022+2026 vs 2020+2021+2024 split**, i.e. a year bet wearing a
sentiment costume. Two defences, both preregistered:

- **(a)** every F&G bucket reports its **year composition**, and any effect whose bucket is
  >60% concentrated in one or two years is capped at `REGIME_DEPENDENT` regardless of t-stat.
- **(b)** the primary F&G variable is not the raw level but `fg_pct365`, the **causal
  trailing-365-day percentile rank** of today's F&G within the prior 365 daily values
  (strictly `< t`, no same-day inclusion). This de-trends the year effect while staying PIT.
  Raw-level results are reported alongside as a secondary, explicitly confounded view.

## 3. Mechanisms, hypotheses and thresholds — fixed now

Convention throughout: `net_bps = gross_bps − 14`, stress `− 28`. Arms are compared
**to each other on the same population**, never to zero (round 2 burned a worker on this).

### Deep-history block (F&G × the project's own established mechanisms)

- **M1 — F&G as a directional signal on BTC (the literature control).**
  Long BTC next day when `fg_pct365 <= 0.20`, short/flat when `>= 0.80`, 1d and 7d horizons.
  *Hypothesis*: DEAD. This is the single most publicly backtested crypto rule in existence.
  I run it as a **negative control**: if my pipeline reports an edge here, my pipeline is broken.
  Thresholds 0.20/0.80 fixed now.

- **M2 — F&G gates LIQ_CASCADE_REPEAT_V1 (the frozen alpha).**
  Base = exact frozen rule: `n_events_sym_24h>=2` AND `is_long_cascade==1` → LONG @ `fwd_4h`.
  Arms: `fg_pct365` terciles (low/mid/high), compared arm-vs-arm.
  *Hypothesis*: forced deleveraging into an already-fearful tape has less remaining marginal
  seller, so exhaustion-fade should pay MORE in the low-F&G arm. Directional prediction:
  `low − high > 0`.

- **M3 — F&G gates SHORT_SQUEEZE repeat (momentum convention).**
  Base = `is_long_cascade==0` AND `n_events_sym_24h>=2` → LONG @ `fwd_4h` (round-3 A4's
  resolved convention). Arms: `fg_pct365` terciles.
  *Hypothesis*: short-squeeze continuation should be STRONGER in the low-F&G arm (shorts are
  crowded when everyone is fearful, so more fuel). Prediction: `low − high > 0`.

- **M4 — F&G rescue attempt on the cascade ONSET null.**
  Base = `n_events_sym_24h==0` (round 3 A1/A2/T1.9 all DEAD, market-internal regimes only).
  Arms: `fg_pct365` terciles. *Hypothesis*: an **exogenous** sentiment regime might separate
  what market-internal regimes could not. Prediction: weak. Pre-committed: if no arm clears
  net14, verdict is DEAD, no further cutting.

- **M5 — INCREMENTALITY: does F&G add anything over `btc_vol_24h`?** *(the decisive test)*
  Round 3 T1.1 already found that gating LIQ_CASCADE_REPEAT on high BTC 24h vol pays
  (delta +17.87bps OOS). F&G is *constructed* from volatility + momentum + volume + social +
  dominance, so it may be a laundered vol signal. Test: within each `btc_vol_24h` tercile,
  compare F&G arms. *Hypothesis*: F&G's apparent effect collapses once vol is controlled.
  Pre-commitment: **if the within-vol-bucket F&G spread is <50% of the unconditional F&G
  spread, M2/M3 are downgraded to `WEAK` (redundant) no matter their standalone t-stat.**

- **M6 — F&G momentum (Δ) vs F&G level.** `fg_chg_7d = fg[t] − fg[t−7]`, split at ±10 points.
  *Hypothesis*: the *change* in sentiment (a surprise proxy) carries information the *level*
  does not, because the level is a slow-moving regime marker everyone already sees.

- **M7 — Sentiment/money divergence: F&G vs funding.** Low `fg_pct365` (fear) while
  `funding_z30 > 0` (money still paying to be long) = the discourse and the positioning
  disagree. Applied as a gate to the M2 base. *Hypothesis*: the most plausible angle in the
  brief; the divergence should be more informative than either leg. **Known N risk:
  `funding_z30` is 76% NaN.**

- **M8 — Sentiment/money divergence: F&G vs LSR.** Same construction with `ls_ratio_z`
  (1.2% NaN, 2021-2026) as the positioning leg. Fear + crowded-long = the real long-history
  version of M7.

### Shallow block (news_raw, 56 days — DATA_LIMITED by construction)

- **M9 — News→price latency (THE KILL TEST).**
  For each declustered RSS event, measure BTC/symbol cumulative return over
  `[−6h, +6h]` around (i) the source-declared `pubDate` and (ii) the recoverable
  **collection** timestamp. *Hypothesis*: the price move is already complete at or before
  the declared timestamp, and the collection timestamp is on average tens of minutes later
  still. If confirmed, the news feed is a *lagging description* of a move that already
  happened and the axis is dead for entry timing, independent of sample size.
  **This is the test I expect to kill the axis, and it is the only shallow test whose
  conclusion does not depend on having long history.**

- **M10 — PIT audit of the collector.** Measure the distribution of
  `collection_time − declared_pubDate`. The collector's `_parse_rss` assigns
  `ts = parsedate_to_datetime(pubDate)`, i.e. the **source-declared** time, and no
  collection-time column is persisted. Recover collection time from the partition filename
  (`part-HHMMSS-*.parquet`, written with `datetime.now(utc)`) and file mtime.
  This is an audit, not an alpha; it produces a `PIT_UNVERIFIED` stamp or clears it.

- **M11 — Attention count (not polarity) → forward vol / return.**
  Per symbol-day article count z-scored over the available window; top-decile "attention
  spike" days → forward 24h realized vol and forward 24h return.
  *Hypothesis*: attention predicts **vol** (plausible) but not **direction** (implausible).

- **M12 — Attention dispersion.** Daily HHI of article counts across symbols +
  Shannon entropy of CoinGecko trending membership. Concentration vs dispersion as a
  market-wide regime marker → forward BTC vol and forward cross-sectional dispersion.

- **M13 — CoinGecko trending entry → forward return.** A coin entering the trending list
  (first appearance in ≥7 days) → forward 1d/3d return vs non-trending peers.
  *Hypothesis*: retail attention chasing; if anything, negative forward return (the
  "attention is a sell" prior), but N is tiny.

## 4. Declustering plan (3 levels, fixed now)

- **L1** — same symbol / 24h window: keep the first event per symbol per 24h.
- **L2** — calendar day, all symbols: collapse to one observation per UTC day.
- **L3** — mechanism-natural macro unit: for all F&G mechanisms, the **F&G regime episode**
  (a maximal run of consecutive days in the same `fg_pct365` tercile). This is the correct
  L3 here because F&G is a slow, autocorrelated series: consecutive days in the same regime
  are one bet, not many. For news mechanisms, L3 = the **news episode** (articles sharing a
  title-token cluster within 24h across feeds — one story reprinted by 4 feeds is N=1).

All t-stats reported at gate level are computed on **L3 episodes**.

## 5. Gate fields (per §2 of the briefing)

`n_raw`, `n_independent_L1/L2/L3`, `net_bps`, `net_bps_stress28`, `t_stat_declustered`
(on L3), `bootstrap_ci95` (block bootstrap, blocks = L3 episodes, 2000 resamples),
`year_by_year`, `ex_best_year`, `n_required` (power 80%, alpha 5%, two-sided, on a
**50%-haircut** edge), `event_rate` (L3 episodes/week over the last 6 months),
`eta_forward_confirmation` = `n_required / event_rate`, `verdict`.

## 6. Pre-committed kill rules

1. Any mechanism whose best arm fails `net_bps > 0` at cost 14 → `DEAD` / `WEAK`. No recut.
2. Any mechanism surviving 14 but not 28 → `COST_FRAGILE`.
3. Any mechanism whose L3 t-stat < 2.0 → not better than `WEAK`.
4. Any mechanism whose effect is >60% carried by one year, or which dies in `ex_best_year`
   → `REGIME_DEPENDENT`.
5. Any mechanism with `eta_forward_confirmation > 3 years` → `UNCONFIRMABLE_IN_HORIZON`.
6. **M5 veto:** if F&G is redundant with `btc_vol_24h`, M2/M3 are capped at `WEAK`.
7. Anything resting only on the 56-day news window is capped at `DATA_LIMITED`.
