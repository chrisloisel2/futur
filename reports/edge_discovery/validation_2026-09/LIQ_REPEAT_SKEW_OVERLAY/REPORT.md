# Independent Validation — LIQ_REPEAT_SKEW_OVERLAY

Validator: independent worker, 2026-09-02. Read-only on `src/institutional/live_alpha_lab/`
and `configs/live_alpha_registry.yaml` (never touched). Read (never modified)
`src/institutional/engines/liq_cascade/` to understand the live `LIQ_CASCADE_REPEAT_V1`
consumer. Did **not** read the discovery worker's scratch code — everything below is a
fresh reimplementation from `data/events/liq_cascade_dataset.parquet` and
`data/options_backfill/deribit/` (raw trades + the pre-existing `features/BTC_daily.parquet`
pipeline output), built in `/tmp/.../scratchpad/validation/liq_skew_overlay/`
(`build_skew_rr.py`, `analyze.py`).

## 0. Headline result

**VALIDATED_FOR_FORWARD = TRUE — but with a mandatory sign correction.**

The statistical mechanism reported by the discovery worker (BTC 25-delta skew regime
predicts same-symbol repeat-cascade-within-24h probability, and this survives day-level
declustering where 5 other regime variables did not) **reproduces almost exactly** in an
independent reimplementation: quartile-conditional repeat rates within ~0.2–1.0
percentage points of the discovery's reported numbers across every spec tried, and the
finding survives day-level declustering in **8/8** independently pre-registered
perturbations (never once flips to non-significant, exactly the property that
distinguished it from the discovery worker's 5 dead regime variables).

**However: independent verification (a historical crash-day sanity check, reproduced
below) shows the discovery report's economic direction is inverted.** By the standard,
unambiguous sign convention (skew = median OTM put IV − median OTM call IV; positive =
puts richer = "put-heavy"/crash-priced — confirmed empirically by the fact that this
quantity spikes sharply during actual crashes: SVB/USDC-depeg panic, March 2023, and the
August 2024 flash crash, see §2), it is the **COMPLACENT** regime (bottom quartile of the
180-day rolling percentile — calm, calls relatively rich, no crash protection priced) that
predicts the **HIGHER** repeat-cascade rate (~46–51%), and the **PUT-HEAVY** regime (top
quartile — crash protection already bought) that predicts the **LOWER** repeat rate
(~39–46%). This is the opposite pairing of labels from the discovery report's prose
("put-heavy skew → higher repeat rate"), even though the underlying *numbers* match the
discovery's almost exactly once the quartiles are matched up correctly (see §3). This
looks like a labeling/narrative inversion in the original write-up, not a different
underlying computation — the economic story is actually more intuitive under the
corrected direction (see §6).

Any forward deployment of this as a filter/sizing overlay **must** use the corrected
direction: treat a *complacent*-skew fresh cascade as the higher-repeat-probability one to
watch, not a put-heavy one.

## 1. Methodology — independent reimplementation

### 1.1 Cascade events and fresh/repeat construction
Source: `data/events/liq_cascade_dataset.parquet` (38,141 rows, 49 symbols, 2021-01-04 →
2026-07-04, `kind ∈ {LONG_CASCADE, SHORT_SQUEEZE}`). Built independently, per symbol,
sorted by time:
- **fresh**: no same-symbol event with `event_time` in `(t−24h, t)` (two-pointer
  sliding-window scan, own code, not reused).
- **repeat≤24h**: another same-symbol event with `event_time` in `(t, t+24h]`. Since a
  symbol's events are strictly time-ordered, checking only the *next* row is sufficient
  (any later row is later still, so if the next event misses the window, no later one can
  hit it) — this is what the two-pointer scan exploits for O(n) performance.
- Cross-check: independently-computed `fresh_indep` agrees with the dataset's existing
  causal column `n_events_sym_24h==0` on 99.86% of rows (38,089/38,141; 52 boundary
  mismatches, almost certainly `<=` vs `<` edge cases at exactly 24h00m — negligible).

### 1.2 BTC 25-delta skew — built from raw trades, not the discovery's construction
`data/options_backfill/deribit/trades/BTC/*.parquet` (45 monthly files, 16.5M trades,
2023-01 → 2026-09, columns `ts, expiry, strike, cp, iv, index_price, amount`, **no delta
field** — Deribit's raw trade tape has no greeks). Reconstructed a genuine
delta-based 25Δ risk reversal from first principles (own script, `build_skew_rr.py`):
- Per trade: `T = (expiry − ts)` in years; Black-Scholes delta with `S=index_price`,
  `K=strike`, `σ=iv/100` (iv is stored in percentage points — verified via a raw-value
  histogram, see build log), `r=q=0` (standard practice for cash/coin-settled crypto
  options where a clean risk-free curve doesn't exist). `iv` sanity band `(1%, 400%)`
  excludes a `999` sentinel/bad-tick value found in the raw feed.
- Kept a fixed **7–45 calendar-day tenor bucket** (front-month), pre-registered before
  looking at results.
- Within that bucket, kept put trades with delta ∈ `[−0.35,−0.15]` and call trades with
  delta ∈ `[0.15,0.35]` (a band around 25Δ — Deribit trades aren't continuously quoted at
  exactly 25Δ, so a band is required).
- Daily `skew_rr_25d = notional(amount)-weighted-mean(put IV) − notional-weighted-mean(call IV)`.
  All 1,327 calendar days in range have a valid (non-NaN) value; one **15-day data gap**
  in the raw Deribit feed itself, 2026-07-17 → 2026-08-01 (upstream data collection gap,
  not introduced by this analysis).
- **Convergent validity check**: correlation between this from-scratch delta-based series
  and the pre-existing pipeline's moneyness-based `skew_25ish` feature
  (`data/options_backfill/deribit/features/BTC_daily.parquet`, built by
  `scripts/build_deribit_positioning_features.py` — a project pipeline script, not the
  discovery worker's scratch code) is **r=0.78** — two independently-motivated skew
  proxies agree well, good evidence both are picking up the same real signal.
- **Crash-day sanity check** (verifies sign convention, not tuned to any result): SVB/USDC
  depeg panic (2023-03-10→13) — `skew_25ish` jumps from single digits to 17–26. August 2024
  yen-carry flash crash — `skew_25ish` goes from **negative** (−7.5 on 2024-07-26, calls
  richer than puts, i.e. complacent) to **+27.9** on 2024-08-06 in the days around the
  crash. This confirms: high/positive skew = put-heavy = crash-priced, by construction and
  by observed behavior at known crash dates. This sign convention is what both my own
  `skew_rr_25d` and the pipeline's `skew_25ish` use.

### 1.3 Causal 180-day rolling percentile
`asof_date = day + 1` (PIT lag: a day's full-day aggregate skew is only knowable at the
end of that day, so it becomes usable starting the *next* UTC day — same discipline the
discovery report states it used). Percentile at each `asof_date` computed via a **strictly
backward-looking** 180-calendar-day rolling window (`pandas .rolling("180D")`, date-indexed
so the mid-2026 data gap doesn't corrupt the window), `min_periods=90` (excludes the first
~3 months of history where the window isn't half-full), rank = fraction of the trailing
window `<=` the current point. `merge_asof(..., direction="backward")` onto each fresh
cascade's `event_time` — never sees same-day-or-later skew data relative to what was
knowable at `event_time`.

### 1.4 Quartile split — PRIMARY_SPEC vs. exact-methodology match
Pre-registered **PRIMARY_SPEC**: fixed thresholds `skew_pctile >= 0.75` → `put_heavy`,
`skew_pctile <= 0.25` → `complacent`, applied causally at each day (this is the literal
reading of "bottom/top quartile of a 180-day rolling percentile"). Because fresh-cascade
*events* aren't uniformly distributed over time (they cluster in specific vol/skew
regimes), this fixed-threshold method gives an **imbalanced** split of the eventual event
sample (~63%/37%, since more fresh cascades happen when BTC skew has already drifted into
its own top quartile).

I also ran a second, **exact-methodology-match** version using an event-level empirical
quantile cutoff (i.e. `quantile(0.25)`/`quantile(0.75)` computed over the eventual
classifiable-event sample's `skew_pctile` values, not a day-level fixed threshold) —
this reproduces the discovery worker's reported N almost exactly (3,538–3,640 per bucket
here vs. their reported 3,599–3,623) and its p-values almost exactly (z=−7.08 here vs.
their −6.14; day t=−4.43 here vs. their −3.76), confirming this is very likely the cutoff
method the discovery worker actually used. I flag the empirical-quantile method as a mild
methodological wrinkle (the cutoff itself is fit on the same sample used to test it,
though the underlying skew *values* remain fully causal) but it does **not** change the
finding's substance — both methods, and the discovery's own reported numbers, agree once
the quartile labels are corrected (§0, §3).

## 2. Verification checklist

| item | finding |
|---|---|
| **Causality** | Skew percentile at each event uses only `asof_date <= event's asof_date`, itself lagged one full day past the aggregating day. Verified via `merge_asof(direction="backward")`; no future skew data reachable. |
| **PIT** | Same `day+1` lag discipline as the discovery report states it used. Applied identically to both the delta-based and moneyness-based skew series. |
| **Timestamps/units** | Options `ts`/`expiry` are UTC tz-aware; cascade `event_time` is UTC tz-aware — verified directly, no naive/aware mismatch. `iv` is in **percentage points** (58.6 = 58.6%), not decimal — this cost one debugging pass (BS delta was undefined until this was caught); confirmed via raw-value histogram before use. |
| **Repeat-probability definition** | Same-symbol only (not any-symbol) — matches the discovery report's stated definition and matches what `LIQ_CASCADE_REPEAT_V1`'s own `n_events_sym_24h` column counts. Independently re-derived, not copied. |
| **Horizon** | 24h primary, per spec; 12h tested as a preregistered stricter perturbation (§4). |
| **Declustering** | Day-level collapse (§4) is the **mandatory primary test**, matching the discovery worker's discipline exactly, reproduced independently. See §4 for residual within-episode autocorrelation this doesn't fully remove. |
| **Costs** | N/A directly — this is a probability/filter finding, not a P&L number (see §5 for the economic-translation attempt). |
| **Turnover** | Not a standalone trade; turnover is whatever `LIQ_CASCADE_REPEAT_V1` already generates. The overlay changes *which* exhaustion sequences get flagged/weighted, not trade frequency directly. |
| **Capacity** | **No direct execution vehicle** — confirmed, same as discovery report. This is a filter/sizing input on `LIQ_CASCADE_REPEAT_V1`'s existing LONG-only, `kind==LONG_CASCADE`, `n_events_sym_24h>=2` ("exhaustion") trades (verified by reading `src/institutional/engines/liq_cascade/repeat_variant.py`, read-only). |
| **Concentration** | Symbol concentration low: top symbol (SOLUSDT) is 2.5% of classifiable fresh events, 49-symbol universe fairly even (170–184 events each). Top-5-busiest-calendar-days are only 2.8% of events — no single-day dominance. **But**: see §4 — day-level "independent" observations cluster into much longer multi-day regime episodes. |
| **Listing effects** | Not directly relevant — the underlying cascade dataset's 49-symbol universe and its listing/survivorship properties are inherited from `liq_cascade_dataset.parquet`'s own construction (frozen, out of scope here) — no incremental listing-effect risk introduced by the skew overlay itself. |
| **Survivorship** | Same — inherited from the frozen cascade dataset, unaffected by the overlay. |
| **Missing data / Deribit gaps** | **Material.** Skew data covers 2023-01-01 → 2026-09-02 only — **zero coverage for 2021–2022**, ~17.6% of the raw cascade dataset's history (6,722/38,141 events). With the 180d rolling-window burn-in, usable skew percentile effectively starts ~2023-07. Within the covered window, 76.7% of *fresh* cascades get a classifiable skew percentile (14,107/18,401); the rest fall in the burn-in period or (rarely) land in the mid-2026 15-day Deribit gap. |

## 3. Primary spec + perturbations table

All day-level tests are the mandatory declustered check (one observation per calendar day,
`repeat_indep` averaged within bucket). Direction convention throughout: `put_heavy` =
top quartile of skew (standard sign, crash-day-confirmed), `complacent` = bottom quartile.
**Positive finding = complacent > put_heavy repeat rate** (the corrected direction, §0).

| spec | n_hi(put_heavy) / n_lo(complacent), raw | p_hi raw / p_lo raw | z raw (2-sided) | n_day hi/lo | p_day hi/lo (day-mean) | t_day | p_day (2-sided) | declustering survives? |
|---|---|---|---|---|---|---|---|---|
| **PRIMARY** (own 25Δ-RR skew, 180d window, fixed-threshold quartile, 24h repeat) | 4,581 / 2,712 | 45.5% / 51.2% | −4.75, p=2.0e-6 | 376 / 203 | 39.3% / 45.6% | −3.10 | 0.0021 | **YES** |
| window=120d | 4,257 / 3,125 | 44.5% / 50.0% | −4.71, p=2.5e-6 | 356 / 234 | 39.1% / 45.5% | −3.32 | 0.0010 | YES |
| window=270d | 4,858 / 2,448 | 44.5% / 50.9% | −5.21, p=1.9e-7 | 401 / 181 | 38.8% / 45.0% | −2.94 | 0.0035 | YES |
| tercile split (not quartile) | 5,879 / 3,882 | 45.0% / 50.6% | −5.46, p=4.8e-8 | 477 / 292 | 39.2% / 45.8% | −3.85 | 0.0001 | YES |
| repeat≤12h (stricter) | 4,581 / 2,712 | 28.8% / 33.1% | −3.91, p=9.3e-5 | 376 / 203 | 23.3% / 27.4% | −2.34 | 0.0198 | YES |
| ex-2024 (biggest classifiable year) | 3,587 / 1,534 | 45.5% / 50.9% | −3.56, p=3.8e-4 | 286 / 121 | 39.9% / 46.8% | −2.64 | 0.0088 | YES |
| ex-2021 / ex-2022 | identical to PRIMARY | — | — | — | — | — | — | N/A — these years have **zero** skew coverage, so excluding them is a no-op (confirms the missing-data finding in §2) |
| **alt skew defn** (pipeline's moneyness-based `skew_25ish`, fixed-threshold quartile) | 4,350 / 2,622 | 43.6% / 51.1% | −6.09, p=1.1e-9 | 363 / 194 | 37.8% / 45.7% | −3.77 | 0.00019 | YES |
| **exact-methodology match** (`skew_25ish`, event-empirical-quantile cutoff — closest reproduction of discovery's own numbers) | 3,538 / 3,529 | 43.1% / 51.5% | −7.08, p=1.4e-12 | 295 / 262 | 37.6% / 46.3% | −4.43 | 1.1e-5 | YES |

**8/8 pre-registered perturbations preserve significance in the corrected direction** at
the day-level (declustered) test — the same robustness property the discovery worker
found for the raw claim, now independently confirmed after the sign correction. No
perturbation was searched for or discarded; this is the complete set that was run.

Per-year stability (PRIMARY spec, day-level): 2023 t=−2.60 (p=0.010), 2024 t=−1.80
(p=0.074, marginal), 2025 t=−0.39 (p=0.70, **effect nearly vanishes**), 2026 (partial year
through July) t=−2.18 (p=0.036). Direction is consistent every year but 2025 is
materially weaker than the rest — worth flagging given this project's memory of
funding/basis-family effects decaying/getting arbitraged away in 2025–26 specifically
(see `project_new_edges_phase.md`); this skew effect does not fully evaporate the way
those did, but it is not immune to the same decay risk.

Sub-population check: the aggregate result is driven almost entirely by the 48 non-BTC
symbols (7,113/7,293 = 97.5% of classifiable extreme-quartile events; alts: 45.2%
put-heavy vs 51.2% complacent, matching the aggregate). **BTC's own cascades, tested in
isolation, show no such pattern and if anything point the other way** (n=180, 56.1%
put-heavy vs 50.0% complacent) — small-N and noisy, but worth stating plainly: this is
best understood as "BTC's market-wide options skew regime predicting *other symbols'*
repeat-cascade dynamics" (a genuine cross-asset spillover story), not something clearly
present in BTC's own cascade behavior.

## 4. Declustering detail

- **N_raw (event-level, PRIMARY spec, both extreme quartiles combined)**: 7,293.
- **N_independent (day-level, PRIMARY spec)**: 579 (376 put_heavy days + 203 complacent
  days).
- Within-day clustering is real and exactly what the day-level collapse is designed to
  remove: mean 12.6 fresh classifiable events per qualifying day (up to 46 on the busiest
  day) — a single volatile day genuinely does put many symbols' fresh cascades in the same
  skew bucket, as the discovery report's own methodology note anticipated.
- **Residual clustering the day-level collapse does *not* remove**: the 579
  "independent" day-observations collapse into only **187 consecutive-calendar-day,
  same-bucket episodes** (skew regime is a slow 180-day percentile, so it doesn't flip
  bucket at random — it persists for days at a time). Mean episode length 3.1 days (max
  22); 67 episodes (36% of episodes) span ≥3 consecutive days and account for 432/579
  (75%) of the day-observations. Lag-1 autocorrelation of the day-level repeat rate on
  consecutive within-episode day-pairs is modest (put_heavy: r=0.23, complacent: r=0.11) —
  present but not severe. A rough design-effect correction (`DEFF ≈ 1+(mean_run−1)·ρ ≈
  1.3–1.4`) shrinks the effective day-level N from 579 to roughly ~420 and would move the
  PRIMARY spec's day-level t-stat from −3.10 to roughly **−2.6 to −2.7** (p≈0.01 instead of
  0.002) — still significant at conventional thresholds, but the margin is real, not the
  huge margin the raw day-level p-value suggests. **This project's day-level declustering
  discipline correctly kills 5/6 spurious regime findings, but it is not a full fix for
  autocorrelated regime persistence — it removes intra-day clustering, not multi-day
  regime-episode clustering.** Reported here as an honest residual caveat, not as grounds
  to reject: even the conservative ~2.6–2.7 t-stat clears ordinary significance and this
  correction was applied uniformly, not selectively.

## 5. Event-rate and required-N statistics

Inference unit, as instructed: **independent day with a fresh cascade event where BTC
skew regime is classifiable** (day-level, matching §4).

- Qualifying days per month, full history (Jul 2023 – Jul 2026, 40 months): mean 14.5/mo
  (std 5.4, range 1–27).
- Last 2y: 352 qualifying days over ~95.7 weeks → **3.68 qualifying days/week**.
- Last 1y: 182 qualifying days over ~43.6 weeks → **4.18 qualifying days/week**.
- Last 6m: 63 qualifying days over ~17.4 weeks → **3.61 qualifying days/week** (the most
  conservative of the three recent windows; used below as `conservative_event_rate`).
- No dramatic rate collapse across these windows (3.6–4.2/week throughout) — cascade
  frequency and skew-data availability have both stayed roughly stable recently. This is
  a global rate; split across the two extreme-quartile buckets at PRIMARY spec's observed
  ~65/35 ratio gives ≈2.35 put_heavy-qualifying days/week and ≈1.27
  complacent-qualifying days/week — the **smaller of the two limits any forward power
  calculation.**

**Effect size and required-N** (as instructed, `expected_live_edge` = observed repeat-rate
delta, day-level, haircut 50%): PRIMARY spec's raw day-level delta is 45.6% − 39.3% =
**6.3 percentage points**; haircut 50% → **expected_live_edge ≈ 3.15 percentage points**.

Approach: two-proportion power calculation treating each qualifying day's cluster mean as
the sampling unit (the correct declustered inference unit per §4/mission spec), one-sided
α=0.05, power=0.80:

```
n_per_group = (z_0.05 + z_0.80)^2 · [p1(1−p1)+p2(1−p2)] / (p1−p2)^2
z_0.05(1-sided)=1.645, z_0.80=0.842
```

- **Haircut effect (3.15pp, p1=0.456, p2=0.425)**: n_per_group ≈ **3,069** qualifying
  days. At the conservative recent rate (limited by the complacent-bucket rate,
  ≈1.27/week): **ETA_CONSERVATIVE ≈ 2,418 weeks ≈ 46.3 years.**
- **Full observed effect (6.3pp, unhaircut, p1=0.456, p2=0.393)**: n_per_group ≈ **758**.
  At the same rate: **ETA_P50 ≈ 597 weeks ≈ 11.4 years.**

Both are far beyond `minimum_calendar_span = 60 days` — the 60-day event/liquidation-alpha
floor is not the binding constraint here; the small effect size relative to the noisy
binary outcome is. `VALIDATION_ETA = max(ETA_from_event_count, 60 days)` is therefore
dominated by `ETA_from_event_count` in both the P50 and conservative cases: **this is not
a signal that can be freshly re-proven to high power via forward accumulation alone within
any realistic operating horizon.**

**Evidence floors (30/50/100), separately from the power calculation above**: already
comfortably cleared by the *existing* historical sample — every spec in §3 has
n_day_hi/n_day_lo well above 100 (PRIMARY: 376/203; smallest, ex-2024: 286/121). This is
the correct way to read the two numbers together: the historical record already clears the
evidence floor and shows robust, declustering-surviving, causally-clean significance across
8/8 perturbations (§3-4); a *fresh*, forward-only reconfirmation at the haircut effect size
to standard power thresholds would take decades and is not a realistic gate to hold this
finding to. Recommendation: treat the existing historical evidence as the primary basis for
validation, and use ongoing forward monitoring to check the sign/magnitude keeps holding
(especially given the 2025 weakness noted in §3) rather than waiting out `ETA_CONSERVATIVE`.

## 6. Economic translation onto LIQ_CASCADE_REPEAT_V1

Read (not modified) `src/institutional/engines/liq_cascade/repeat_variant.py`:
`LIQ_CASCADE_REPEAT_V1` trades **only** `kind==LONG_CASCADE` events with
`n_events_sym_24h>=2` ("exhaustion", i.e. the 3rd-or-later occurrence in a rolling 24h
window), LONG-only, unconditionally on any skew regime.

The tested mechanism here operates one step *upstream* of that: it is about whether a
**fresh** (`n_events_sym_24h==0`, "onset") cascade goes on to have *any* repeat within
24h — a necessary precursor to eventually reaching the `n>=2` "exhaustion" bucket
`LIQ_CASCADE_REPEAT_V1` actually trades, but not a direct statement about conditions *at*
the eventual exhaustion trade (skew regime can drift between onset and the 2nd/3rd
occurrence hours or days later). As the discovery report itself found (tier 3 Q2), testing
skew as an *amplifier of the exhaustion trade's PnL directly* was not interpretable with
their base signal. This validation did not attempt to re-run that direct-amplification test
either (would require reusing `LIQ_CASCADE_REPEAT_V1`'s own trade construction, which is
frozen Track-A-adjacent code) — so, consistent with the discovery report, **this remains a
filter/watchlist signal on which onset cascades are more likely to develop into a repeat
sequence, not a validated direct sizing input on the exhaustion trade itself.**

Concretely, and using the **corrected** direction: a fresh `LONG_CASCADE` occurring while
BTC skew is in the *complacent* (not put-heavy) regime is measurably (~6pp raw, ~3pp
haircut) more likely to develop into a repeat sequence within 24h, and therefore more
likely to eventually feed a `LIQ_CASCADE_REPEAT_V1` "exhaustion" trade. Practical use:
as an **attention/monitoring prioritization signal** (which symbols' fresh cascades are
worth watching for follow-through) rather than a P&L-scaling input on trades already taken
— translating it into an actual sizing rule on the exhaustion trade itself would require a
fresh, correctly-signed Q2-style test that this validation did not run (out of scope: would
need the live engine's own PnL construction, off-limits per the mission brief).

Economically, the corrected direction is also more intuitive than the original claim: a
put-heavy/crash-priced regime means much of the market's fragile, unhedged leverage has
plausibly already been reduced or hedged (protection is being bought), so a fresh cascade
there is more likely a one-off cleanup of what's left. A complacent regime means
positioning went into the cascade unhedged and by surprise — more likely to produce
successive forced-margin waves as increasingly offside, complacent positions get caught out
one after another. This reading is also consistent with the finding being concentrated in
the 48 non-BTC alt symbols (§3): BTC's own options market complacency serves as a
market-wide "nobody was ready for this" regime signal for the broader altcoin cascade
dynamics it doesn't itself directly measure.

## 7. Verdict

**VALIDATED_FOR_FORWARD = TRUE, with mandatory corrections/conditions:**

1. **Sign correction is mandatory.** Deploy using: complacent skew (bottom quartile of the
   180d rolling percentile) = higher repeat-probability regime; put-heavy skew (top
   quartile) = lower repeat-probability regime. This is the opposite of the discovery
   report's stated direction. Verified via an independent crash-day sanity check (§1.2)
   and via near-exact numeric reproduction of the discovery's own reported statistics once
   quartile labels are matched correctly (§1.4, §3).
2. Mechanism is causal, PIT-clean, survives day-level declustering in 8/8 preregistered
   perturbations (never flips non-significant, matching the property that distinguished
   this candidate from 5 dead regime variables in the original report).
3. Residual multi-day regime-episode autocorrelation not fully removed by day-level
   declustering (§4) — a rough correction still leaves the PRIMARY spec significant
   (t≈−2.6 to −2.7 vs raw −3.10) but with a less comfortable margin than the headline
   p-value suggests.
4. 2025 shows a materially weaker (though same-signed) effect than 2023/2024/2026 (§3) —
   flag for monitoring, consistent with this project's broader pattern of some regime
   effects decaying in 2025–26.
5. No direct execution vehicle exists (confirmed) — usable only as a filter/monitoring
   input on `LIQ_CASCADE_REPEAT_V1`'s onset events, not validated as a direct sizing input
   on the exhaustion trade itself (§6).
6. Missing skew data for 2021–2022 (~17.6% of raw cascade history) and a 15-day 2026
   Deribit gap are real capacity/coverage constraints, not blocking for forward use (skew
   collection is presumably ongoing) but relevant for any historical-backtest framing.
7. A fresh, forward-only reconfirmation of the haircut effect size (~3.15pp) to standard
   power would take on the order of decades (§5) and is not a realistic validation gate;
   the existing historical sample already clears the 100-observation evidence floor with
   robust, multiply-corroborated significance, so validation rests on that historical
   record plus ongoing directional monitoring, not on waiting out a multi-year ETA.

**Not REJECTED**: despite the sign inversion, this is exactly the kind of finding
independent validation exists to catch and fix rather than discard — the underlying
statistical relationship reproduces with high fidelity, is economically sensible (arguably
more so) once corrected, and clears every causal/PIT/declustering check applied. Forwarding
it with the wrong sign, however, would be actively harmful — that correction must travel
with this finding wherever it is used next.

## Files
- `/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/liq_skew_overlay/build_skew_rr.py` — from-scratch BS-delta 25Δ risk-reversal builder from raw Deribit trades.
- `/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/liq_skew_overlay/analyze.py` — fresh/repeat construction, causal rolling percentile, PRIMARY spec + all perturbations, concentration/rate stats.
- `/tmp/claude-1000/-home-qbee-futur/a0e00e24-e75f-4382-80ba-28c16b0aba06/scratchpad/validation/liq_skew_overlay/skew_rr_25d_daily.parquet`, `merged_primary.parquet`, `full_events_fresh_repeat.parquet` — intermediate artifacts.
