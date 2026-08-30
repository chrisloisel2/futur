# W5 — Options surface (A14), informed wallet flow (A11), execution alpha (A16)

## Executive summary

| # | Mechanism | Verdict | One-liner |
|---|---|---|---|
| A14 | Options surface shock → perp RV/return | **PROMISING signal / NOT independently tradeable** | IV-shock and option order-flow bursts genuinely forecast next-period BTC perp realized vol (survives a vol-clustering confound control, stable sign across time-split). No directional edge at all. No monetization vehicle in this environment (no options execution/greeks) — best used as a risk-overlay on the existing VRP short-variance book, not a new strategy. |
| A11 | Persistent informed-wallet markout (Hyperliquid) | **WEAK / inconclusive, worth another data cycle** | Confirmed untested prior to this report. A genuine, sign-stable, direction-consistent pattern exists in the extreme top ~1% of wallets (ranked strictly out-of-sample), but it does not reach statistical significance at the wallet level (n=282, bootstrap CI crosses zero) and is dominated by a handful of large wallets (top 10 = 54% of that cohort's notional). Point-estimate edge is thin versus realistic round-trip cost. |
| A16 | Execution queue-state → fill/adverse-selection | **WEAK** | Fill probability and post-fill adverse selection both move monotonically with quoted spread distance, exactly as microstructure theory predicts — but the effect is almost entirely a cross-asset liquidity composition artifact (majors vs alts). Within BTC/ETH alone the effect shrinks to ~0.2-0.3bps, not tradeable. Useful only as an execution-calibration input. |

None of the three clears a standalone, net-of-cost, statistically solid PROMISING bar today. A14's signal is the most statistically convincing but has no execution vehicle here; A11 is the most economically interesting *if* it survives more data, and is genuinely new ground; A16 mostly reproduces known market microstructure without adding alpha.

## Prior work recap (not re-litigated)

- **`reports/options/VOL_RISK_PREMIUM.md`**: DVOL vs realized-vol+30d variance risk premium, unconditional monthly short-variance. BTC: mean premium 8.7 vol-pts, 72% of days positive, monthly short-var hit-rate 67%, sized ⟹ +6.0%/yr (maxDD -4.9%), but **decaying** — 2026 YTD mean -4.2pts / hit 50%. ETH: much weaker/negative (-0.9%/yr sized, maxDD -18%, worst month -167pts in 2021-05). Explicitly a measurement of existence, not a deployable backtest (no costs, ≈1-2pt spread ignored).
- **`reports/options/FUNDING_TIMING.md`**: funding(t-1)>0 ⟹ hold rule. Gross timing edge real (persistence 0.91/0.50 pos/neg) but **negative net of taker costs** (-11 to -22%/yr delta vs always-on) and only marginally positive net of maker costs (+4.7-5.5%/yr gross, but -6.2%/yr delta vs always-on).
- Neither touches Hyperliquid, wallet-level flow, or execution-probe data — A11 and A16 are genuinely first tests of this data. A14 extends VOL_RISK_PREMIUM by testing forecastability of next-period RV rather than the unconditional 30d premium.
- `reports/HL_METAORDERS_PROTOCOL.md` is a **different, still-uncompleted** pre-registered test on the same HL data (TWAP-detector continuation/reversion, gated on ≥300 eligible metaorders — no results file exists yet). It is not the "persistent public-wallet markout" mechanism tested here. Confirmed via `grep -rl "hyperliquid.*wallet\|informed.*flow\|buyer.*seller" reports/` → only that protocol doc came back, no prior A11-style analysis exists.

---

## A14 — Options surface shock

**Pre-registered spec**: MECHANISM — IV/skew/DVOL shocks or block-trade bursts predict subsequent BTC perp realized vol (and, separately, direction) via dealer rehedging pressure. PAYER — convexity sellers/dealers caught offside, or slow directional traders. SIGNAL — `|d_atm_iv_traded|`, `|d_skew_25ish|`, `|d_DVOL|`, `block_share`, `top_strike_share`, notional/trade-count z-scores (daily); block-count/notional-z/call-put imbalance (hourly). EXECUTION VENUE — BTC perp if directional, options/DVOL if vol trade (**not available here**). HORIZON — 1min-1h (catalog); tested 1h/1d/3d. MAIN FAILURE MODE anticipated — the effect is just vol-clustering relabeled.

**Data**: BTC only — ETH has DVOL but **no `features/`/`trades/` directory** (confirmed via listing). All options data (features, trades) stops **2026-07-17**; perp data used runs to 2026-08-29.

**Daily test** (n=1294 days, 2023-01-01→2026-07-17): `d_atm_iv_traded → rv_fwd1` IC=+0.323 (p<1e-4), `top_strike_share → rv_fwd1` IC=-0.194 (p<1e-4), `abs_d_dvol → rv_fwd1` IC=+0.128, `net_put_flow_btc → rv_fwd1` IC=+0.107. All directional (`ret_fwd1`) ICs |IC|<0.08 with **signs flipping between time-split halves** — no directional edge at all.

**Confound check** (the key worry — is this just vol clustering?): baseline `rv(t)→rv_fwd1` alone: IC=+0.40 (stronger than any options signal alone). After **partialling out today's realized vol**, `d_atm_iv_traded` retains partial IC=+0.224 (p<1e-4) and `top_strike_share` retains -0.082 (p=0.003) — the options market adds real incremental information, not just repackaged RV persistence.

**Time-split stability**: `top_strike_share→rv_fwd1` -0.215/-0.171 (h1/h2, stable), `abs_d_dvol→rv_fwd1` +0.134/+0.121 (stable) — no sign flips on any vol-forecasting signal.

**Hourly test** (n=1867 hours, May-Jul 2026): `notional_z`/`n_block`/`block_share_hr → abs_ret_fwd1h` IC=+0.20/+0.20/+0.13 (p<1e-4). Confound control: baseline intraday clustering IC=+0.179; partial IC of `notional_z` after controlling = **+0.113** (p<1e-4) — again real incremental information. `cp_imbalance → ret_fwd1h` (directional) IC=-0.014, n.s.

**Verdict: PROMISING as a vol-forecasting signal, explicitly NOT tradeable standalone here.** Real, confound-controlled, time-split-stable, confirmed at two independent granularities. But per the mandate not to fabricate greeks/OI constructs, and with no options execution capability in this environment, there's no vehicle to harvest it. Directional forecasting is **DEAD**. Recommended next step (not executed, flagged as scope): use the IV-shock signal as a conditioning overlay on the existing VRP monthly short-variance book, since VRP's naive premium is visibly decaying in 2026.

---

## A11 — Persistent informed-wallet markout (Hyperliquid)

**Confirmed untested before this report** (see grep above).

**Pre-registered spec**: MECHANISM — some public HL wallets are persistently informed; fills followed by favorable moves more than chance. PAYER — less-informed counterparties. WHY EDGE EXISTS — HL is fully public/pseudonymous, unlike dark venues, so tracking a wallet's track record and mirroring it is actually possible. SIGNAL — a wallet's own strictly-past notional-weighted markout. ENTRY/EXIT — mirror the wallet's side, close at tested horizon. VENUE — HL perps, taker or best-effort maker. HORIZON — 1s-5min tested. MAIN FAILURE MODE anticipated — survivorship (in-sample "smart" deciles are noise) and/or edge too small to survive reaction latency + fees.

**Method (leakage discipline)**: 36.5M trades, 12 coins, 2026-07-18→2026-08-29 (~42 days). Built a 73M-row wallet-trade ledger (every trade attributed to both buyer +1 and seller -1, **markout in bps** — an early version used raw price differences and was caught and corrected, since pooling absolute-dollar markouts across BTC ~$110k and DOGE ~$0.2 is meaningless). Chronological split: **train = 07-18→08-13 (25.5d, 28.7M rows), test = 08-13→08-29 (16d, 44.3M rows)**. Wallet rank set *purely* on train notional-weighted `markout_60s`, ≥30 train trades required (40,911/213,246 wallets qualify). Evaluated only on that wallet's later out-of-sample test trades.

**Decile granularity** (top/bottom 10% = 4091 wallets, train w_mean +3.19/-4.19bps@60s): test `markout_60s` top decile +0.12bps, bottom -0.005bps, population ~0. Top-vs-bottom Mann-Whitney p=8.3e-6 (significant); **top-vs-population p=0.25 (not significant)** — decile granularity is mostly noise, effect decayed ~85-95% train→test.

**Narrowing to the extreme tail** — edge grows monotonically:

| cohort | n train | n in test | test markout_60s | test markout_300s |
|---|---:|---:|---:|---:|
| top 10% | 4091 | 3052 | +0.12bps | +0.02bps |
| top 5% | 2045 | 1525 | +0.58bps | +1.58bps |
| top 2% | 818 | 579 | +0.61bps | +3.08bps |
| **top 1%** | **409** | **282** | **+1.24bps** | **+3.21bps** |
| top 0.5%/200/100/50 | ≤204 | ≤140 | erratic — too small to trust |

Top 1% is **positive across all five horizons in both independent test sub-halves** (08-13→20: [1s,5s,30s,60s,300s]=[+2.0,+1.7,+2.4,+2.7,+6.8]bps; 08-21→29: [+2.5,+2.4,+1.7,+0.4,+1.1]bps) — a real sign-stability pass. Bottom 1% negative at 1s/5s in both halves but flips positive at 60s/300s in the first half — smart-money persistence beats dumb-money persistence, consistent with the literature.

**But the rigorous wallet-level test does not confirm it.** Mann-Whitney top-1%-vs-population at wallet level: **p=0.437**. Bootstrap 95% CI on the top-1% cohort's per-wallet mean `markout_60s`: **[-1.74, +2.80]bps — crosses zero.** Why the trade-level numbers look cleaner: the top-1% cohort's test notional ($601M) is **54% concentrated in just 10 wallets** (mostly BTC/HYPE/ETH) — this is likely a handful of specific sophisticated/large wallets, not a broad "many wallets are informed" effect, and n=282 isn't enough to statistically confirm even that narrower claim.

**Per-coin** (top vs bottom 5%, test markout_60s): cleanest in LINK (+3.66 vs -1.94), XRP (+2.86 vs -2.04), ETH (+2.57 vs -0.19), SUI (+3.14 vs +1.70); reversed/noisy in ADA, DOGE, AVAX; BTC directionally right but weak (-0.02 vs -0.59); SOL a clean null (+0.41 vs +0.49) despite 6299 qualifying wallets.

**Net economics**: even the best point estimate (1.2-3.2bps simple-mean; notional-weighted up to ~11bps but dominated by a few large fills, not trustworthy as "the" edge) is thin against realistic round-trip cost (HL taker ~2-4.5bps/side ⟹ ~4-9bps round trip), before even accounting for not being able to trade at the informed wallet's exact print price.

**Verdict: WEAK / inconclusive, not DEAD.** This is the one signal here with genuine novelty (public pseudonymous wallet history, never exploited this way before) and a twice-out-of-sample-confirmed directional pattern in its extreme tail. What's missing is wallet-level statistical power from only 42 days of collection. Recommendations: (1) let the still-running collector accumulate 12+ weeks and re-test; (2) if it persists, investigate the ~10-40 dominant wallets individually (market maker? HL-insider-adjacent? genuinely skilled discretionary?) since that classification determines whether it's repeatable; (3) do not deploy against this basket as currently measured — CI includes zero.

---

## A16 — Execution alpha (queue-state → fill probability / adverse selection)

**Pre-registered spec**: MECHANISM — spread distance, price acceleration, symbol, time-of-day at placement predict fill probability and post-fill adverse selection. PAYER — immediacy demanders. SIGNAL — `spread_bps`, `ttf_s`, symbol, hour. VENUE — Binance USDT futures. HORIZON — 25ms-30s (catalog); tested via 60s/300s adverse-selection columns (median fill 37s). MAIN FAILURE MODE anticipated — a cross-symbol liquidity composition artifact rather than a real within-symbol timing signal.

**Data**: 3.59M orders, 15 symbols, 2026-07-12→2026-08-29, 80.1% overall fill rate.

**Fill probability**: monotonic in `spread_bps` (tightest quintile 90.5% filled → widest 60.1%, corr=-0.26). Majors fill far more (ETHUSDT 92.7%, BTCUSDT 89.7%) than small alts (ARUSDT 58.6%, FETUSDT 57.4%). Stable across a chronological half/half split.

**Post-fill adverse selection**: pooled, `adv_bps_60s` degrades monotonically with quoted spread — -1.2bps (tightest) → -7.7bps (widest), n≈575k/quintile, p<<0.001; also degrades with time-to-fill (-2.5bps fast → -4.8bps slow).

**But mostly a composition effect.** Within a single symbol the range collapses: BTCUSDT -1.17 to -1.43bps (0.26bps range across all spreads), ETHUSDT -1.13 to -1.27bps. Only a small alt (ADAUSDT, -6.46 to -8.51bps) shows a larger within-symbol range (~2bps), still swamped by realistic costs. The pooled -1.2 to -7.7bps range is overwhelmingly explained by *which symbol* (liquid vs illiquid), not *when/how* you place within a symbol.

**Verdict: WEAK.** Real, monotonic, statistically overwhelming (huge n), time-split-stable relationships that mostly restate known LOB microstructure. Within-symbol effect on the two most liquid assets is ~0.2-0.3bps — noise-level, not tradeable. Best use: a calibration input for an existing execution algo's aggressiveness curve per symbol, not a standalone alpha.
