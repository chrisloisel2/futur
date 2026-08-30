# W8 — Execution probe: maker-alpha mining (P(fill), E(markout), E(PnL_maker))

Date of analysis: 2026-08-30. Scope: `data/execution_probe/` (196MB, 12,101 parquet files,
2026-07-12 → 2026-08-30, 47 calendar dates). Standalone raw pandas analysis, **not** run through
the Alpha Foundry pipeline (per mission scope — too heavy for a discovery pass). Round-2 worker;
no direct round-1 predecessor touched this dataset (referenced only as "A16" in the Alpha Foundry
catalog, marked not yet runnable through the full pipeline). Read-only throughout; no sealed
experiments (A2, A2-RV-v1, A13-H) or TRM Fleet files touched; nothing directional or SHORT-shaped
proposed — everything below is execution-quality (maker fill/markout), not a directional bet.

## Executive summary

**No state-conditioning scheme found in this dataset makes naive "post at the touch, hold until
filled" maker orders profitable — not even before fees.** Gross-of-fee economics (half-spread
capture + post-fill markout) are negative in literally every one of the ~24 conditioning schemes
tested, for every symbol, every session, every volatility/spread/momentum bucket. The best
achievable state (BTC/ETH, bottom-half realized vol, moderate spread) still averages **-1.0 to
-1.2bps gross per fill** before any fee is applied; at the project's standard 2bps maker fee this
becomes **-3.0 to -3.2bps net per fill**, **-2.2 to -2.9bps unconditional per order attempt**.
Adverse selection dominates spread capture everywhere in this sample. This is an honest **DEAD**
verdict for "maker-alpha exists here as a standalone strategy" — but the exercise still produced
several genuine, stable, economically sensible **relative** differentiators (which states are
*less bad* than others) that would matter for improving execution of some other directional
signal, per the mission's stated motivation, plus one important methodological catch (a
confounded "fast fill = safe fill" result that evaporates under proper controls).

| rank | mechanism / state-rule | N orders | P(fill) | E(markout) 60s / 300s | E(PnL_maker) net of 2bps fee | stability | confidence | status |
|---|---|---:|---:|---:|---:|---|---|---|
| 1 | **Frozen rule**: symbol∈{BTC,ETH,BNB} AND rvol_20 ≤ own-symbol median AND spread_decile∈{1,2,3} (own-symbol) AND side fades mom_20 | 113,436 | 0.869 | -1.11 / -1.04bps | **-2.68bps/attempt** (vs -3.65 baseline, a +0.97bps relative improvement) | stable, sign unchanged H1→H2 (-2.79→-2.62) | MEDIUM | **WEAK** (best-found, still net-negative) |
| 2 | Realized-vol quintile, within-symbol (rvol_20, causal trailing 10min std) | 3.61M | 0.70→0.91 (rising with vol) | monotonic -3.24→-4.25bps (60s) | monotonic worse with vol, both halves | **very stable**, monotonic in H1 AND H2 separately | HIGH | **WEAK/informative** — cleanest, most robust differentiator found |
| 3 | Symbol tier (BTC/ETH/BNB vs 12 wide-spread alts) | 3.61M | 0.90 (tight) vs 0.77-0.85 (wide) | -1.2 / -1.2bps (tight) vs -4.4 to -9.7bps (wide) | -2.87 to -2.92 (tight) vs -3.5 to -5.0 (wide) | stable both halves, same rank order | HIGH | **WEAK/informative** — large, stable, but tight tier is still net-negative |
| 4 | Spread decile, within-symbol (non-linear) | 3.61M | 0.73-0.91 | worst at decile 0 (unusually tight-for-symbol): -3.37/-3.61bps; best at decile 2: -2.80/-2.84bps | worst at decile0 (-4.14), best at decile2 (-3.42) | **holds in both halves** (decile0 worst, decile2 best, both H1 & H2) | MEDIUM-HIGH | **PROMISING shape, non-monotonic** — unusually tight spread is a real toxic-fill signature |
| 5 | Momentum-fade side selection (BUY when mom_20<0, SELL when mom_20>0) vs momentum-chase | 1.72M / 1.72M | ~0.80 both | fade: -3.62/-3.49bps avg; chase: -3.61/-3.68bps avg | fade -3.62 vs chase -3.65 (tiny, ~0.03bps) | present but small in both halves | LOW | **WEAK** — real sign, economically negligible size |
| 6 | Time-to-fill decile, **global/pooled (uncontrolled)** | 2.89M fills | n/a (outcome) | decile0 (ttf~1s): -2.40bps; decile9 (ttf~423s): -4.96bps | monotonic worsening with slower fill | monotonic in both halves | — | **CONFOUNDED — see below**, not usable as reported |
| 7 | Time-to-fill decile, **within-symbol / single-symbol controlled** | 2.89M fills | n/a (outcome) | BTC: -1.06 to -1.28bps (non-monotonic, hump-shaped, worst at ttf~30-60s not the tails); ETH: similar | flat/noisy, no clean monotonic pattern once symbol is controlled | not tested further, effect too small | LOW | **DEAD as an actionable rule** — the pooled "fast fill = safe" story was ~90% a symbol-composition artifact |
| 8 | Cancel-after-T-seconds policy (elapsed-time-only decision, causally valid) | full pop. | shrinks 0.14→0.80 as T grows 5s→∞ | per-fill quality improves with tighter T (-3.35bps@T=5s vs -4.56bps@T=∞, net of fee) | **unconditional E(PnL) gets *less* negative only because P(fill) shrinks — every fill is still a loss**, not a real edge | consistent direction, trivial mechanism | — | **DEAD as a standalone rule**, but the underlying per-fill quality signal (item 6/7 above) is what would drive it, and that signal doesn't survive controls |
| 9 | Session / UTC hour (3-bucket and 24-bucket) | 3.61M | 0.78-0.84 flat across sessions | -3.4 to -3.9bps (60s), no bucket >0.5bps different from mean | **no stable structure** — EU session BUY looked better in aggregate but flips/weakens on inspection | not stable, see side-asymmetry note below | LOW | **DEAD** — no time-of-day signal |
| 10 | Side (BUY vs SELL) unconditional | 3.61M | 0.80 both | -3.68 / -3.61bps (BUY) vs -3.72 / -3.96bps (SELL) | nearly identical unconditionally (-3.65 both) | **NOT stable**: SELL degrades sharply H1→H2 (-3.68→-4.21bps@300s) while BUY holds/improves | LOW | **DEAD as unconditional rule** — likely just beta to the sample's realized +21.7% BTC uptrend (see note), not a structural ask-side toxicity |
| 11 | Spread × realized-vol 2D interaction, within-symbol quartiles | 3.61M | 0.69-0.91 across 16 cells | best cell (Q0 spread, Q0 rvol): -3.36/-3.25bps; worst (Q3 spread, Q3 rvol): -5.22/-5.28bps | best -3.21 vs worst -4.49 | directionally consistent with items 2+4 individually | MEDIUM | **WEAK/informative** — mostly recovers items 2 and 4 combined, no extra interaction effect beyond the additive story |
| 12 | Short-horizon flow proxy (sign-run of last 5 returns, `flow_5_updn`) | 3.61M | 0.77-0.86 | small spread across buckets (-3.11 to -3.99bps) | -3.62 to -3.78, weak ordering | not tested for stability, effect small | LOW | **WEAK/marginal** |

Overall verdict for the mission's core question ("find states where posting is profitable"): **none
found — DEAD as a standalone edge on this data at the standard 2bps maker fee, and in fact DEAD
even at 0bps fee** (gross economics are negative in the best state too). The relative
differentiators (items 2-4) are real, stable, and directly usable to *avoid the worst states*
(elevated trailing vol, unusually-tight-for-symbol spread, wide-spread alt symbols) if a maker leg
is needed for some other reason — e.g., cheaper entry on a directional signal from elsewhere in
this sweep — but they do not by themselves turn maker posting into a positive-EV activity here.

## Data schema (as found — described, not assumed)

`data/execution_probe/date=YYYY-MM-DD/part-HHMMSS.parquet`, hive-partitioned by date, one row per
simulated **passive limit order attempt**. Columns, confirmed identical across a spread of 5 files
sampled from across the full date range:

| column | type | meaning (inferred + verified) |
|---|---|---|
| `ts_place` | string (ISO8601, UTC) | order placement timestamp |
| `symbol` | string | one of 15: BTC/ETH/BNB/XRP/SOL/DOGE/LINK/AVAX/SUI/ADA/TIA/ORDI/PYTH/AR/FET USDT |
| `side` | string | `BUY` or `SELL` |
| `limit` | float | limit price of the order |
| `spread_bps` | float | quoted spread at placement time, in bps |
| `filled` | bool | whether the order eventually filled |
| `ttf_s` | float | time-to-fill in seconds; **null iff `filled=False`** |
| `adv_bps_60s` | float | post-fill markout at 60s, **null iff not filled** |
| `adv_bps_300s` | float | post-fill markout at 300s, **null iff not filled** |
| `mid_at_place` | float | mid price at placement |

3,611,301 total rows. 15 symbols, ~240.6-240.9k orders each (near-perfectly balanced). Side
BUY/SELL near-perfectly balanced (1,805,570 / 1,805,731). Overall `filled` rate **80.07%**.
Cadence: ~one order pair (BUY+SELL) per symbol every 30 seconds, continuously, no gaps beyond
normal collection jitter. No columns for order book depth, imbalance, order size, or venue — **all
absent from this dataset**, confirmed by inspecting the full column list across every sampled
file. This materially limits what "state" can mean here versus the mission's full wishlist (see
Limitations).

**Verified structural facts, load-bearing for the analysis:**
- Every order is placed **exactly at the best bid/ask** (join-the-touch, no price improvement, no
  passive-behind-touch orders): `limit_offset_bps := (limit - mid_at_place)/mid_at_place*1e4` sits
  within floating-point rounding (~1e-4bps) of `∓spread_bps/2` for 100% of rows. There is **no
  variation in order aggressiveness-within-spread** to condition on — this rules out one
  potentially-interesting conditioning axis (queue position / price improvement) as simply not
  present in this data.
- `adv_bps_60s`/`adv_bps_300s` are **already sign-adjusted from the maker's perspective**
  (negative side_60s means bad for the maker regardless of BUY/SELL — verified: BUY mean
  -3.68bps, SELL mean -3.72bps, nearly identical, which would not be the case if this were raw
  unsigned mid-price change). This is the standard "markout" convention.
- Definitional ambiguity flagged honestly: it is not possible to determine from this data alone
  whether `adv_bps` already embeds the half-spread capture (fill-price-anchored markout, the
  academic-standard definition) or is a pure forward-drift measure independent of the fill price
  (which is how the mission's suggested formula treats it). A within-symbol test (does markout get
  mechanically *better* as spread widens, as a fill-price-anchored definition would predict via a
  larger embedded credit?) shows the **opposite** — within-symbol, wider spread deciles have
  *worse*, not better, markout (m02 in evidence/) — weak evidence against full fill-price-anchoring
  dominating, but not conclusive since volatility confounds spread width too. **This analysis uses
  the mission's literal formula (spread-capture added separately) as primary**, flagged everywhere
  the ambiguity matters. It does not change any qualitative conclusion: even with the (more
  generous) added spread-capture convention, PnL is negative in every state tested; the
  alternative convention would only make every number more negative, not flip any verdict.

## Causality enforcement

Per hard rule 7, only information available strictly at order-placement time is used to define
"state":
- `spread_bps`, `side`, `symbol`, `mid_at_place`, `ts_place` (hour/session) — contemporaneous,
  directly from the row, no leakage.
- **Realized volatility** (`rvol_20`, `rvol_60`) and **momentum** (`mom_20`, `mom_5`) — built from
  a deduplicated per-symbol timeline of `(ts_place, mid_at_place)`, computed as trailing rolling
  statistics over **strictly prior ticks** (`rolling(...).std()`/`shift(K)`, both causal by
  construction — a rolling window ending at row t uses only rows ≤ t, and the mid at t itself is
  legitimately known at t since it's the placement-time mid). Verified no forward-looking merge:
  features table keyed on `(symbol, ts_place)`, joined back to orders by exact key, not by index
  position.
- `ttf_s`, `adv_bps_60s`, `adv_bps_300s` are **outcomes**, never used as conditioning "state" for
  the P(fill)/E(PnL) buckets (items 1-5, 9-12 in the ranked table). They are used only in items 6-8
  as explicit outcome-outcome and elapsed-time-based analyses, exactly per the mission's carve-out
  ("time-to-fill as an outcome AND check if predictive... it's a classic finding worth verifying").
  The one place elapsed time is used as a *decision* input (item 8, cancel-after-T) is causally
  valid: "has T seconds passed since I placed this order" is known in real time without any
  forward information.
- No order-book depth/imbalance features exist in this dataset (see schema section) — cannot be
  computed as a state variable, honestly reported as absent rather than approximated.

## Cost model

Per hard rule 8: maker fee = 2.0bps one-way (`src/institutional/execution/execution_simulator.py`
default), used throughout. The execution_probe data does not document any different realized fee
schedule (no fee/rebate column present), so the project default stands unmodified.

`E(PnL_maker | state) = P(fill|state) × [half_spread_bps(state) + E(markout_bps | fill, state) −
maker_fee_bps]`, per the mission's suggested formula, used as primary (see ambiguity note above).
`half_spread_bps := mean(spread_bps)/2` over the state's population (captures the fact that the
order executes at the touch rather than the arrival mid).

## Detailed findings

### 1. Frozen best rule (found, precisely stated)

**Rule**: post only when ALL of:
- `symbol ∈ {BTCUSDT, ETHUSDT, BNBUSDT}`
- `rvol_20 ≤` that symbol's own median `rvol_20` (bottom-half trailing 10-min realized vol)
- `spread_decile_in_symbol ∈ {1, 2, 3}` (i.e. exclude both the bottom decile — unusually tight for
  that symbol, empirically the worst state, see item 4 — and the top 6 deciles — unusually wide)
- side fades the trailing-20-tick momentum: `BUY` when `mom_20 < 0`, `SELL` when `mom_20 > 0`

N = 113,436 order attempts, P(fill) = 0.869, E(markout|fill) = -1.11bps@60s / -1.04bps@300s,
E(PnL_maker) net of 2bps fee = **-2.68bps/attempt** (60s) / -2.62bps/attempt (300s), vs. the
unconditional baseline of -3.65bps/attempt — a **+0.97bps relative improvement per attempt**, about
27% reduction in expected loss. Stable across the two independent halves of the sample (H1:
-2.79bps, H2: -2.62bps, no sign flip, evidence/m22). Still solidly net-negative — this is the
*least bad* combination found, not a profitable one. Isolating each filter's marginal contribution
(evidence/m23): tight-symbol restriction alone gets to -2.89; adding low-vol + moderate-spread
gets to -2.71; adding the momentum-fade side rule gets the last ~0.03bps to -2.68 — the momentum
filter is real but nearly negligible on its own (matches item 5).

### 2. Realized volatility (within-symbol quintiles) — cleanest, most robust finding

Trailing 10-minute realized vol of the mid-price series (causal, `rvol_20`), ranked within each
symbol's own distribution to remove symbol-composition confound. **Monotonic in E(PnL) across all
5 quintiles, and — critically — monotonic separately within H1 and H2** (evidence/m17e):

| rvol quintile (own-symbol) | H1 e_pnl_60 | H2 e_pnl_60 |
|---|---:|---:|
| 0 (calmest) | -3.24 | -2.98 |
| 1 | -3.38 | -3.31 |
| 2 | -3.57 | -3.63 |
| 3 | -3.85 | -3.93 |
| 4 (most volatile) | -4.25 | -4.41 |

This is the single most reliable differentiator found: elevated trailing volatility monotonically
and consistently predicts worse maker economics, exactly as classical theory (Glosten-Milgrom /
Copeland-Galai — informed flow selectively hits resting orders more in volatile regimes) predicts,
and it replicates cleanly out-of-sample (H2) after being observed in H1.

### 3. Symbol tier (liquidity/size proxy)

BTC/ETH/BNB (tight-spread majors) vs. the 12 wide-spread alts show a large, stable gap: tight tier
P(fill)=0.90, E(PnL)=-2.87 to -2.92bps; wide tier P(fill)=0.77-0.85, E(PnL)=-3.5 to -5.0bps, worst
being FETUSDT (-5.05bps H2) and ADAUSDT. Rank order (which symbols are best/worst) is preserved
across H1/H2 in every one of the 15 symbols individually (evidence/m17a) — the single most stable
symbol-level ranking found, but even the best tier remains net-negative.

### 4. Spread decile, within-symbol — non-linear, "unusually tight" is toxic

Not linear. Both H1 and H2 independently show the **same non-monotonic shape**: the bottom
decile (spread unusually tight *for that symbol*) is the single worst bucket (H1: -4.59bps, H2:
-4.29bps), decile 2 (moderately tight) is the best (H1: -3.51, H2: -3.51), and deciles 3-4
(wider-than-usual) sit back in the middle-to-bad range (evidence/m17d). This is a genuine
non-linear threshold effect worth flagging: an unusually compressed spread for a given symbol
appears to be a "coiled spring" signature — associated with subsequently worse fills, not better
ones, contrary to the naive expectation that tighter-than-usual spread should be the calm/safe
state. Plausible mechanism (not provable from this data alone): abnormal spread compression
precedes volatility release more often than it reflects genuinely quiet, low-risk conditions.

### 5. Momentum-fade vs momentum-chase side selection

Fading recent 20-tick momentum (BUY when trailing return negative, SELL when positive) beats
chasing it, but the effect is small (~0.03-0.1bps across the six mom_dir×side cells,
evidence/m09, m24) relative to items 2-4 (which run 0.3-2.7bps). Present in both halves but not a
meaningful standalone lever; included in the frozen rule mainly because it's free (doesn't cost
fill probability) rather than because it moves the needle much.

### 6-7. Time-to-fill: a confound caught and corrected — the mission's specific ask

The mission asked explicitly to check the classic microstructure claim that unusually fast fills
are *more* adversely selected. **Pooled/uncontrolled data shows the opposite of the classic claim,
and strongly so**: fastest-decile fills (mean ttf≈1s) average -2.40bps@60s vs. slowest-decile
fills (mean ttf≈423s) at -4.96bps@60s — a clean, monotonic, seemingly strong "wait longer, get
picked off worse" pattern (evidence/m18).

**This pattern is however almost entirely a symbol-composition confound, not a genuine
within-symbol effect** — caught by controlling for symbol identity:
- Within-symbol pooled deciles (evidence/m19): the range collapses from [-2.40, -4.96] to
  [-3.49, -3.81], non-monotonic (worst is a *middle* decile, not either tail).
- Single-symbol tests, the cleanest possible check — BTCUSDT alone (evidence/m20) and ETHUSDT
  alone (evidence/m20b): both show a **hump shape**, not monotonic decay: fastest fills (ttf<1s)
  average -1.17 to -1.18bps, *worst* fills are in the middle of the distribution (ttf≈30-60s,
  -1.27 to -1.28bps), and the *slowest* fills (ttf>200s) are actually back to -1.06 to -1.19bps —
  essentially flat and noisy across the whole range (only ~0.2bps spread top to bottom, versus a
  ~2.6bps spread in the uncontrolled pooled version).

**Root cause**: slow-to-fill orders are disproportionately drawn from the wide-spread/low-liquidity
alt symbols (FETUSDT, ARUSDT, ADAUSDT — which also have the worst intrinsic markout, per item 3),
while fast fills are disproportionately BTC/ETH/BNB (which have the best intrinsic markout). Once
symbol identity is controlled for, the "time-to-fill predicts toxicity" relationship the mission
asked to verify **is not confirmed as a clean, actionable, within-symbol signal on this data** —
verdict flips from what looked like a strong finding to essentially DEAD once done correctly. This
is reported as a **methodological catch**, exactly the kind of thing rule 4 (never inflate a
result) exists for: the naive version of this analysis (skip symbol control) would have shipped a
plausible-sounding but largely spurious "cancel slow orders" rule.

### 8. Cancel-after-T-seconds policy

A causally valid rule (decision uses only elapsed time). Simulated thresholds T ∈
{5,10,...,600,∞}s: as T shrinks, effective P(fill) drops from 0.80 to 0.14, and per-fill quality
does improve monotonically (net-of-fee pnl_fill_60 goes from -4.56bps at T=∞ to -3.35bps at T=5s) —
but **every single fill is still a loss on average, at every T**, so the unconditional
`E(PnL) = P(fill)×pnl_fill` only looks "less negative" at small T because you're accepting fewer
losing trades, not because you found a profitable regime (evidence/m16). Given item 6/7's finding
that the underlying per-fill-quality-vs-ttf relationship is largely a symbol confound rather than
a real within-symbol timing signal, this policy has **no real edge to harvest** — it's DEAD as
proposed, not merely sub-cost.

### 9-12. Session/hour, unconditional side, spread×vol interaction, short-flow proxy — weak/dead

- **Session (3-bucket UTC and 24-bucket hour-of-day)**: no economically meaningful structure,
  neither globally nor within BTC alone (evidence/m10, m11, m14, m14b) — flat within noise.
- **Unconditional side (BUY vs SELL)**: nearly identical in aggregate (-3.65bps both), but
  **not stable across halves** — SELL degrades from -3.68 to -4.21bps@300s H1→H2 while BUY holds
  roughly flat. BTC rose ~21.7% over the sample window (64,162 → 78,085, first-to-last mid), and
  the H1/H2 split lands almost exactly on the transition into that run — a persistent uptrend
  mechanically makes passive SELL/ask fills look worse after the fact (price kept rising past
  where you sold) and passive BUY/bid fills look better (price kept rising above where you
  bought), independent of any structural ask-side toxicity. Flagged explicitly as **not usable as
  a standalone rule** — this asymmetry is very likely realized-trend beta, not a repeatable
  microstructure edge, and would be expected to reverse in a downtrend sample.
- **Spread × realized-vol 2D interaction** (within-symbol quartiles, evidence/m12): recovers
  roughly the sum of items 2 and 4 individually; no meaningfully new interaction effect beyond
  what each axis explains alone.
- **Short-horizon flow proxy** (`flow_5_updn`, sign-run of last 5 ticks): weak, small ordering,
  not tested further for stability given the small effect size.

## What could not be tested (data limitations, honestly flagged)

- **No order size** column exists — cannot test the mission's requested order-size conditioning at
  all.
- **No venue** column — single execution venue implied, cross-venue comparison not possible.
- **No order-book depth or imbalance** columns — the mission's requested depth/imbalance
  conditioning is not computable from this dataset; only spread (a 1D proxy for liquidity) is
  available.
- **No queue-position/aggressiveness-within-spread** variation — every order is placed exactly at
  the touch (verified, see schema section), so "how passive was the order relative to the book" is
  not a usable axis here.
- adv_bps fill-price-vs-placement-mid anchoring ambiguity (see schema section) — flagged, does not
  change any verdict but affects the precise magnitude of gross-of-fee numbers by up to
  ~half_spread_bps (≤0.03bps for BTC/ETH, up to ~1.7bps for the widest-spread alts).

## Evidence files

All in `reports/edge_discovery/alpha_hunt_2026-08-30/w8_execution_probe/evidence/` (24 CSVs,
148KB total, m01-m24 matching the mechanism numbering used implicitly above — full per-bucket N,
P(fill), markout, and PnL for every scheme, plus the four stability-by-half files m17a-e, m18,
m22, m24, and the breakeven-fee sensitivity table m21). No large derived data copies written back
to disk, per hard rule 5.

## Bottom line

This probe dataset, taken at face value with the project's standard 2bps maker fee, shows **no
profitable maker-posting state** — adverse selection dominates spread capture everywhere tested,
including gross of any fee. The most defensible, stable finding is a **relative** one: trailing
realized volatility (item 2) is a clean, monotonic, both-halves-stable predictor of how bad maker
fills will be, and symbol tier (item 3) and within-symbol spread-decile shape (item 4) add further,
also-stable, differentiation. The frozen rule combining all of these gets the expected loss down by
~27% relative to unconditional posting but does not cross into profitability. The most important
negative result methodologically is item 6/7: the "fast fills are toxic" pattern the mission
specifically asked to check is real in pooled data but **evaporates under proper symbol-level
control** — a caught confound, not a shipped false positive. Per the mission's informational note,
even though no standalone maker edge exists here, items 2-4 (avoid high trailing vol, avoid
unusually-tight-for-symbol spread, prefer BTC/ETH/BNB) are legitimate, low-risk execution-quality
filters that could reduce round-trip cost on directional signals found elsewhere in this sweep,
without themselves constituting a new sleeve.
