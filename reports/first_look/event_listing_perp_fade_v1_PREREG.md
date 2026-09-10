# event_listing_perp_fade_v1 — preregistered first look (H2 made executable)

Written and pushed BEFORE any price join. One hypothesis, one look, one verdict. Budget 2 → 1 at
the look. Family `news`, fifth sealed hypothesis → threshold_t(5) = 2.3263 one-sided.

## Why this test exists

Regard seq 7 (event first look) found the listing fade on the pre-existing **spot** market:
+202 bps mean, +169 median, t 3.25, 70 % positive, consistent 2023–2025 — verdict INDECIDABLE
because a short on spot is 0 % executable. This test asks whether the same fade exists, and is
executable, on the **perpetual itself**, on **disjoint events**: the listings where the perpetual is
the first Binance market actually tradable. The direction (short) is therefore informed by the
spot result; nothing else is. No event of this universe has ever been priced in this repository.

## Frozen inputs

- Event tape: the frozen snapshot of regard seq 7, sha256 `6e957851e452395f…` (tape at b0d483b).
- Universe: `reports/first_look/event_listing_perp_fade_v1_UNIVERSE.json`, sha256 `cbb5d9619a01f05c996556ef54c15383b2fbad46d6b2804bf51ca46194caba24`,
  **174 events**, built by `first_look.py --build-universe` from metadata only.
- Launch times: `fapi/v1/exchangeInfo` `onboardDate` (snapshot kept locally) and the open_time of
  the first 1-minute bar of the perpetual on Binance Vision (daily files, existence probed by HEAD,
  first row read for its timestamp only). Prices are fetched only at the look.

## Universe (pure functions of the record and of file existence)

From the 351 Binance perp listing announcements of the tape (same selection as H1/H2), excluded:
- 145 events whose asset was priced in H1/H2 (spot pre-existing)
- 21 whose Binance spot `<ASSET>USDT` traded before the perpetual's first minute (spot file of D−1 exists, or a spot bar precedes the launch on launch day)
- 4 with no Binance perpetual file within 7 days of publication (non-USDT quotes, e.g. USD1 contracts)
- 3 "Will Convert" notices (the contract already existed)
- 2 with bars before the official onboarding (incoherent timestamps)
- 2 without a readable first bar
- duplicates by asset (first event kept)

Launch time = first Vision bar; agreement with `onboardDate` within 5 min for
170 events; for the others the
`onboardDate` is a date-only field and the Vision bar is kept. Launch within [publication − 1 h,
publication + 7 d]; median announcement → launch delay ≈ 1.7 h. Launch years: {'2022': 1, '2023': 11, '2024': 26, '2025': 85, '2026': 51}.

## Hypothesis (single, direction fixed)

On these listings, the perpetual **falls** in excess of BTCUSDT between launch + 15 min and
launch + 6 h: the median gross short return exceeds 30 bps and the mean exceeds 3 × taker cost
after cost.

- Entry: open of the first 1-min bar with open_time ≥ launch + 15 min (tolerance 5 min, else the
  event is excluded and counted)
- Exit: open of the first 1-min bar with open_time ≥ launch + 6 h (same tolerance)
- Statistic: side × (perp log return − BTCUSDT spot log return), bps, side = short
- Sensitivities (descriptive, never selected on): exit at launch + 60 min and launch + 24 h
- Also reported: first-15-minutes excess (launch → entry), win rate, by launch year

## Costs (P1.1 official schedule, VIP0, no institutional tier)

Taker: 5.0 bps per side × 2 + spread 8 + slippage 6 = **24 bps round trip** (new book, declared
wide on purpose; identical to the original event_reaction_v1 spec written before any look).
Maker sensitivity: 2.0 × 2 + 8 + 6 = 18 bps, reported only. Promotion on the taker path only.

Cost wall (strict reading): **net = mean − 24 ≥ 3 × 24**, i.e. mean ≥ 96 bps.

## Metrics

N, mean gross, median gross, cluster-robust SE by launch day, t, win rate, N_eff = (Σ|x|)²/Σx²,
top-1 share of positive contributions, net (taker and maker), capacity proxy = quote volume (USD)
of the perpetual over [entry, exit] (median across events; 100 k$ must be ≤ 5 % of it), spread /
volatility proxy = median 1-min (high − low)/open in bps over the window.

## Power (declared, not measured)

Assumed σ of the 6-h excess on a new perpetual: 400 bps. N ≈ 174: MDE ≈ 2.33 × 400 / √174 ≈
71 bps on the mean. Together with the cost wall (mean ≥ 96), only a large fade can be
promoted; a 30 bps median with a small mean ends REJECTED_COST_WALL by construction.

## Verdict (fixed order)

1. N < 30 → `INDECIDABLE`
2. share of launch timestamps unreliable > 20 % → `INDECIDABLE`
3. capacity not measurable → `INDECIDABLE`
4. median gross < 30 bps → `REJECTED_NO_GROSS`
5. net (mean − 24) < 3 × 24 → `REJECTED_COST_WALL`
6. t < 2.3263, or N_eff < 30, or top-1 share > 20 %, or median 6-h quote volume < 2 M$ → `INDECIDABLE`
7. otherwise → `FORWARD_SEAL_REQUIRED` (no promotion from a first look; no live trading)

## Execution discipline

- This file, the universe and the FREEZE are committed and pushed before the look (branch
  `p3-h2-perp-first-executable`, with upstream: the witness is the remote).
- `python mechanisms/event_listing_perp_fade_v1/first_look.py --run` refuses without FREEZE, if
  any pinned file or the universe changed, if the prereg is not pushed, or if a result exists.
- The LOOK_LEDGER entry is written and 1 test debited before the first price is fetched.
- Positive control (synthetic, 80 bps injected, 600 events): recovered 76.7 bps, t 2.79 ≥ 2.33,
  null REJECTED_NO_GROSS — `results/positive_control.json`.
- No optimization, no second look, no other hypothesis on this universe.
