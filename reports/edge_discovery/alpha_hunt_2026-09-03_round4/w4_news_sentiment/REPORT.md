# W4_NEWS_SENTIMENT — REPORT (Alpha Hunt Round 4, 2026-09-03)

Axis: news, sentiment, attention. Read-only on all `data/`, `src/institutional/`,
`reports/live_alpha_lab/`, `configs/live_alpha_registry.yaml`. Nothing written outside
this worker directory. No intermediate parquet written anywhere (disk was at 97%).

## 0. Verdict in one paragraph

**The axis is killed, and it is killed on the right grounds.** 16 entries taken to the
round-4 §2 gate: **8 DEAD, 5 DATA_LIMITED, 2 UNCONFIRMABLE_IN_HORIZON, 1 REGIME_DEPENDENT,
0 PROMISING, 0 VALIDATED_FOR_FORWARD.** The preregistered null — "there is nothing here" —
survives. Two results are worth more than the verdicts themselves:

1. **The kill is a timing kill, not a sample-size kill (M9).** Against a 1000-draw
   random-time placebo, crypto news stories genuinely sit in volatile moments (+4 to +9bps
   of excess absolute BTC move, p<0.001 in every window). But that excess is **symmetric
   about the timestamp**, and at the only anchor a real system could act on — recovered
   collection time — **more of the move is already gone (+5.42bps in the 30min before) than
   remains (+4.71bps in the 30min after)**. Publication is a *coincident* marker of
   volatility, not a leading one. This conclusion does not depend on having more history.
2. **`data/news_raw` is look-ahead biased and nobody knew (M10).** The collector stores the
   source-declared `pubDate` as `ts` and persists no collection time. Median declared→collected
   lag is **21.3 minutes**; 28.3% of RSS rows arrive >30min after their own timestamp, 14.2%
   >2h, p95 = 31 hours. Any future backtest anchored on `ts` is biased by that amount.

Plus one live-infrastructure defect found on the way (**X2**): `futur-news.service` advertises
"RSS + F&G + CoinGecko" but `collect_once()` never fetches Fear & Greed. The F&G series has
been **frozen at 2026-07-10 for 55 days**, silently.

## 1. What the data actually is (measured, not assumed)

The single most important finding of the survey phase: **the briefing's "16 Mo of news" is
5,793 rows over 53 real collection days.**

| | measured |
|---|---|
| `data/news_raw` rows / unique url_hash | 5,793 / 2,390 |
| distinct **collection** days | **53** (2026-07-10 → 2026-09-03) |
| the 15 partitions dated 2025-12-29..2026-01-23 | RSS backlog, all written 2026-07-11, max declared-vs-collected lag **193 days** |
| genuinely new items per 30-min collector run | **1–2** (`reports/news_collector.log`) |
| BTC-tagged independent stories in the whole window | **272** after story-level declustering |
| `data/news_backfill/fear_greed.parquet` | 3,078 daily obs, **2018-02-01 → 2026-07-10**, only 4 missing days |
| `data/positioning/` (live LSR) | starts **2026-07-16** → **zero overlap with F&G** |

So the axis splits into one deep leg (F&G, 8.5 years) and one shallow leg (news_raw, 53 days),
and they cannot be combined: **the two datasets do not overlap in time at all.**

## 2. The confound I declared up front, and what it cost

F&G level is massively collinear with calendar year (2026 mean 21.6, 2022 mean 25.3, 2024
mean 63.3). A raw fear-vs-greed split is a year bet in a sentiment costume. The primary
variable was therefore preregistered as `fg_pct365` — the **causal trailing-365-day percentile
rank**, computed strictly on days `< t`. This worked: the M1 fear arm is positive in 7 of 8
years with a year-concentration of 0.286, i.e. the de-trending removed the artifact rather
than hiding it. Every F&G bucket's year composition is recorded in `evidence/m2_m8_results.json`.

**L3 declustering:** F&G is slow and strongly autocorrelated, so consecutive days inside one
regime run are **one bet, not many**. L3 = the maximal run of consecutive calendar days in the
same F&G tercile. This is what shrinks 5,445 cascade events to **425 independent episodes**, and
it is the reason nearly every ETA below is catastrophic. For the news block, L3 = the
title-token story cluster within 24h (a story reprinted by 4 feeds is N=1; raw/independent
ratio measured at 1.16 — the RSS set is less clustered than feared, because the collector's
url_hash dedup already removes literal reprints).

*(Estimator audit, M5d: event-weighted vs episode-weighted means differ by only 0.21bps on the
cascade base, so the episode weighting is not what produces the negative results. It is not an
artifact of my estimator.)*

## 3. Gate table — every mechanism

Costs: `net = gross − 14`, stress `− 28`. `t` is on L3 episodes. ETA in years.

### Deep leg — F&G × the project's own mechanisms (2018/2021 → 2026)

| id | mechanism (best arm) | n_raw | L1 | L2 | L3 | net | s28 | t(L3) | CI95 | ex-best-yr | ETA(y) | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | F&G fear → long BTC 1d *(negative control)* | 647 | 647 | 647 | 94 | **+49.75** | +35.75 | **2.25** | [7.6, 94.4] | +41.77 (t=2.11) | **20.8** | `UNCONFIRMABLE_IN_HORIZON` |
| M2 | F&G gates LIQ_CASCADE_REPEAT_V1 (low_fear) | 1764 | 1117 | — | 140 | +16.25 | +2.25 | 1.10 | [-12.4, 44.3] | — | 64.5 | `DEAD` |
| M3 | F&G gates SHORT_SQUEEZE repeat (low_fear) | 1231 | 843 | — | 142 | −2.98 | −16.98 | −0.23 | [-26.5, 23.6] | — | 1505 | `DEAD` |
| M4 | F&G rescue of cascade ONSET (mid) | 5544 | 5544 | — | 172 | −6.65 | −20.65 | −0.70 | [-24.7, 12.9] | — | 543 | `DEAD` |
| M6 | F&G 7d *change* gates repeat (flat) | 3565 | 2324 | — | 304 | +19.74 | +5.74 | 2.34 | [3.3, 35.6] | — | 26.4 | `DEAD` |
| M7 | F&G vs **funding** divergence (aligned_fear) | 229 | 158 | — | 89 | +40.33 | +26.33 | 2.31 | [9.3, 76.5] | — | 13.7 | `UNCONFIRMABLE_IN_HORIZON` |
| M8 | F&G vs **LSR** divergence (other) | 3681 | 2277 | — | 211 | +24.14 | +10.14 | 2.21 | [4.4, 46.9] | — | 45.2 | `DEAD` |

### Shallow leg — news_raw (53 days, one regime, all `DATA_LIMITED` by preregistration)

| id | mechanism | n_raw | L3 | net | s28 | t(L3) | ETA(y) | verdict |
|---|---|---|---|---|---|---|---|---|
| M9 | news → price latency + placebo | 755 | **272** | — | — | — | — | **`DEAD`** |
| M10 | PIT audit of the collector | 5793 | — | — | — | — | — | `DATA_LIMITED` / stamped `PIT_UNVERIFIED` |
| M11 | attention **count** → vol / direction | 38 | 13 | +98.82 | +84.82 | 0.93 | 18.2 | `DATA_LIMITED` |
| M12 | attention **dispersion** (HHI) | 19d | 19 | −55.32 (conc.) | — | −0.93 | — | `DATA_LIMITED` |
| M13 | CoinGecko trending **entry** → 1d | 24 | 9 | **−147.73** | −161.73 | **−2.40** | 2.77 | `DATA_LIMITED` |

### Diagnostics and cross-checks

| id | what | verdict |
|---|---|---|
| M5a | is F&G laundered volatility? | `DEAD` |
| M5b | is "buy fear" just buying a drawdown? | `DATA_LIMITED` (not identified) |
| X1 | round-3 A4 under episode weighting | `REGIME_DEPENDENT` |
| X2 | F&G feed dead in production | `DEAD` |

## 4. What I killed and why

### 4.1 The regime-conditioner thesis (M2, M3, M4, M6) — killed by comparing to the right baseline

The brief's mechanism #1 was the strongest a-priori idea: use F&G as ENABLE/DISABLE on alphas
the project already owns. It fails, and the way it fails is instructive.

**M2 looks like a win until you pick the right comparison.** Gating the frozen
`LIQ_CASCADE_REPEAT_V1` on low F&G gives +16.25bps and the preregistered direction is confirmed
(low_fear − high_greed = **+14.21bps**). Compared to *zero*, that is a publishable gate.
Compared to the **same population ungated**, which pays **+15.81bps**, the gate is worth
**+0.44bps** and discards 68% of the events. This is exactly the trap that burned a worker in
round 2, and the only thing separating a `PROMISING` from a `DEAD` here is which baseline you
write down.

The tercile ordering is also **non-monotone**: low 16.25 < mid **26.95** > high 2.04. A real
regime effect is monotone in the regime variable. A U-shape across three buckets with t=1.10
is the shape of noise. M6 fails the same way, more embarrassingly: its best arm is
"sentiment *not* changing", which is 65% of the population — the best gate is no gate.

M4 is a clean corroboration: exogenous sentiment does **not** rescue the cascade-onset null that
round 3's market-internal regimes (A1, A2, T1.9a-c) could not rescue either. The spread is
−2.38, the wrong sign versus prediction. The onset sleeve is robustly dead from two independent
directions now.

### 4.2 The incrementality veto (M5a) — the test that settles it

Round 3's T1.1 found that gating the same cascade base on **BTC 24h volatility** pays
(+17.87bps OOS). F&G is *constructed* from volatility, momentum, volume, social and dominance,
so the obvious question is whether F&G is laundered vol. The answer is more damning than "yes":

- F&G spread *within* vol buckets: **+39.4 / −24.2 / +20.7** — it **sign-flips**.
- Vol spread *within* F&G buckets: **+41.4 / +87.6 / +60.2** — same sign, large, in all three.

**Volatility survives controlling for F&G. F&G does not survive controlling for volatility.**
Retention arithmetic (0.84) says F&G is not *merely* vol, but what survives is incoherent.
Per preregistered veto rule 6, M2/M3 are capped regardless of their standalone numbers.

### 4.3 The divergence thesis (M7, M8) — hypothesis rejected *in direction*

The brief flagged sentiment-vs-positioning divergence as "the only angle where I expect
something". It is rejected, and rejected in the direction opposite to the hypothesis, on both
positioning legs independently:

| | divergent (talk ≠ money) | aligned (talk = money) | spread |
|---|---|---|---|
| M7, vs funding | **−29.06** | **+40.33** (t=2.31) | **−69.39** |
| M8, vs LSR | **−16.74** | +9.45 | **−26.19** |

**Agreement pays; disagreement does not.** When fear in the discourse coincides with fear in
the money, the cascade-exhaustion trade works; when the discourse is fearful but positioning is
still long, it stops working. That is economically coherent in hindsight — genuine capitulation
requires the positioning to actually capitulate — but it is the opposite of what was
preregistered, and I am reporting it as a rejected hypothesis, not re-labelling it as a find.

M7's aligned arm does survive the 28bps stress (+26.33, t=2.31). It is still not deliverable:
229 raw observations / 89 episodes carved out of a base where `funding_z30` is **76% NaN**, and
**ETA 13.7 years**.

### 4.4 The negative control that fired (M1) — and why it changes nothing

I preregistered "buy extreme fear" as a *negative control*: if my pipeline reported an edge on
the most publicly backtested rule in crypto, my pipeline was broken. It reported one:
**+49.75bps net, t=2.25, positive in 7 of 8 years, ex-best-year +41.77 (t=2.11), year
concentration 0.286.** It is not a year artifact and it is not a bug.

It is also unusable, for the reason this round exists:

- F&G regimes are **slow**. 8.5 years of daily data contain **94 independent fear episodes**
  (~11/yr). Confirming a 50%-haircut version of this edge forward needs **20.8 years**.
- It is public. Every retail blog since 2018 has published it.
- **M5b shows it cannot be identified.** `fg_pct365` is 47.5% mechanically explained by trailing
  price alone (R² on 30d return + 30d vol + 365d drawdown; correlation with 30d return
  **0.664**). Controlling for drawdown halves the spread (retention 0.49) and makes it
  sign-unstable (+173.5 / −67.9 / −2.5). But the off-diagonal cells are nearly empty — *greed
  inside a deep drawdown is 5 episodes; fear inside a shallow one is 8*. F&G and drawdown are
  too collinear on 8.5 years of daily data to be separated at all. The honest verdict is **not
  identified**, not "it's just mean reversion" (drawdown alone gives −4.37, so it is not that
  either).

The control firing was still worth it: it proves the pipeline detects effects when they exist,
which is what licenses the DEADs above.

### 4.5 The news feed itself (M9) — killed on timing, with a placebo

This is the result I would keep if I could keep only one.

272 independent BTC stories, anchored two ways, against **1000 random-time placebo draws** of
the same size from the same calendar span and the same 5-minute price grid:

| window | declared `pubDate` | collection time (what a system can act on) |
|---|---|---|
| **[−30, 0] min** | +3.80 excess abs bps (p<0.001) | **+5.42** (p<0.001) |
| **[0, +30] min** | +4.83 (p<0.001) | **+4.71** (p<0.001) |
| [0, +60] | +6.42 | +3.52 |
| [0, +120] | +7.16 | +8.95 |
| **post/pre ratio** | **1.27** | **0.87** |
| signed return [0,+30] | +2.28 bps | +3.95 bps |

Read it in two steps.

**Step 1 — the feed is not noise.** Absolute moves around stories beat the placebo by 4–9bps
with p<0.001 everywhere. News genuinely concentrates in volatile moments. Anyone testing
"does news relate to volatility?" gets a resounding yes and stops there.

**Step 2 — but the excess is symmetric, and at the actionable anchor it is *back-weighted*.**
The market is already moving *before* the article's own declared timestamp by the same amount
it moves after. At the collection anchor the pre-excess (+5.42) **exceeds** the post-excess
(+4.71): by the time the story is in the lake, **more of the episode is behind you than ahead
of you**. And the signed returns — +2.3 to +4.0bps, sign-unstable between anchors — sit far
below the 14bps cost floor.

Add the M10 lag (median 21.3 min, p75 30.9 min) and a live system reading this feed is
structurally, measurably late. **No amount of additional history fixes this**, which is precisely
why this test was preregistered as the one that could kill the axis on its own.

### 4.6 The attention leg (M11–M13) — DATA_LIMITED, and failing its own hypothesis anyway

Preregistered as the more robust half ("the count, not the polarity"), and it fails on its own
terms before sample size even becomes the binding constraint: **attention spikes precede
*lower* relative forward vol** (rv ratio 0.915 vs 1.005 calm, spread −0.09, t=0.08). The
plausible leg is flat. After the causal 14-day z-window warm-up only 20 usable symbol-days
survive, on 19 symbols, in a single extreme-fear regime.

M13 is the one lead I would not throw away: entering the CoinGecko trending list is followed by
**−147.7bps net, t=−2.40**, and −38.3bps excess versus same-day peers — directionally consistent
with the preregistered "attention is a sell" prior. It is 24 entries on **9 independent days**,
its tradeable form is a *short* on small-cap alts where the 14bps cost model does not apply, and
its ETA is 2.77 years. Recorded as a lead, claimed as nothing.

## 5. Two findings for other owners

**X1 — round 3's A4 is fragile.** I reproduced its baseline *exactly* (+11.14bps, N=2381,
gap≥4h decluster), which cross-validates both pipelines. But it is carried entirely by 2024
(+29.5) and 2025 (+64.0) against 2022 (**−62.0**) and 2023 (−8.8), and it flips to **−12.73**
when independent episodes are equal-weighted rather than events. Whoever owns A4 should treat
it as `REGIME_DEPENDENT`, not as a stable base for further gating.

**X2 — the Fear & Greed feed is dead in production.** `futur-news.service` is described as
"RSS + F&G + CoinGecko", but `collect_once()` fetches only the 4 RSS feeds and CoinGecko
trending — there is no Fear & Greed call anywhere in
`src/institutional/data/news_collector/collector.py`. The series exists solely because
`scripts/backfill_fear_greed.py` was run once, on 2026-07-10. It has been **55 days stale**, and
nothing alerted. Anything reading it as a live feature is reading a frozen file. The fix is a
few lines; not applied here (`src/institutional/` is read-only for this worker).

## 6. What would change the verdict

Honest statement of what is missing, per the `DATA_LIMITED` protocol:

- **For the news leg:** ≥8 months of collection spanning at least one greed regime, with the
  collector storing **`ts_collected` alongside `ts_declared`**. Even then, M9 says the timing
  problem is structural — the fix is a **lower-latency source** (exchange announcement
  endpoints, on-chain events, a websocket newswire), not more of the same RSS.
- **For the F&G leg:** nothing fixes it. The constraint is not sample size but **episode rate** —
  ~11 independent regime episodes per year is a hard ceiling set by the physics of the signal.
  A sentiment variable useful to this project must be **high-frequency and orthogonal to
  trailing price**; F&G is neither (47.5% price by R², ~11 episodes/yr).
- **For the divergence leg:** the one structurally fixable gap. `funding_z30` is 76% NaN in the
  event panel; filling it from `data/derivatives_backfill/` would take M7 from 89 to a plausible
  ~350 episodes and cut its ETA from 13.7 years toward ~3.5. Given the hypothesis was rejected
  *in direction*, I do not recommend spending that effort on this axis — but the same gap will
  bite any other worker who uses `funding_z30` from that panel.

## 7. Reproduction

```
.venv/bin/python evidence/m1_fg_directional_control.py   # M1 negative control
.venv/bin/python evidence/m2_m8_deep_block.py            # M2,M3,M4,M6,M7,M8
.venv/bin/python evidence/m5_incrementality.py           # M5a/b/c/d diagnostics
.venv/bin/python evidence/m9_m10_news_pit_latency.py     # M10 PIT audit, M9 profile
.venv/bin/python evidence/m9b_placebo.py                 # M9 random-time placebo (1000 draws)
.venv/bin/python evidence/m11_m13_attention.py           # M11,M12,M13
.venv/bin/python evidence/build_results.py               # -> RESULTS.json
```
`evidence/w4_lib.py` holds the causal F&G features, the 3-level declustering and the §2 gate
(block bootstrap over L3 episodes, 50%-haircut power calculation, ETA). Seeded (20260903),
deterministic. Total disk written by this worker: < 400 kB, no intermediates in `data/`.
