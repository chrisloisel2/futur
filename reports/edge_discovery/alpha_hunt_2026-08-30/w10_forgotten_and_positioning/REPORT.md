# W10 — Forgotten data coverage sweep + positioning standalone mining

Read-only, round 2 of `alpha_hunt` (round 1: `alpha_hunt_2026-08-29`). No data collected, nothing
deleted/moved. Note: this worker could not call `Write` directly for the report file (a harness
constraint hit mid-run); its evidence CSVs were written successfully, and this report is
transcribed by the orchestrating session from the worker's final findings, verbatim in substance.

## Part A — coverage inventory (fast pass, nothing new tradeable except one item)

| dataset | verdict |
|---|---|
| `data/alpha20` | production ledger, not a research dataset, N/A |
| `data/derivatives` | single stale BTC file, superseded by `derivatives_raw`/`derivatives_backfill` (W2/W3's data) |
| `data/derivatives_live` | one snapshot date only, dead |
| `data/fundamentals_backfill` | already tested end-to-end — `reports/FUNDAMENTALS_V1_VERDICT.json` = NO_EDGE, 19 DeFi protocols |
| `data/paper_xvenue` | n=44, already has its own dedicated harness elsewhere |
| `data/news_backfill` | `fear_greed` already a live production feature; `news_daily_sent`/`news_daily_vol` are the same dead `news_raw` source W6 already found DEAD (2026-08-29 sweep) |
| `data/worldmon_jsonl` + `data/worldmon_features` | **genuine new find** — see below |

**`data/worldmon_*`**: a GDELT media-tone + macro-mcap + USGS event pipeline with its own
pre-built causal correlator that had **never been run** — permanently gated
`data_trustworthy=False` in its own code. Ran it manually (read-only). Found a real bug:
`bigdata_store.py::events_df` drops **15% of events** via a `pd.to_datetime` format-inference
failure — one-line fix is `format="ISO8601"`, not applied (read-only mandate for this sweep).
Fast-triaged anyway despite the bug (56 tests): GDELT tone/volume shows nothing against BTC
forward returns, max |r|=0.23, all confidence intervals cross 0. **Verdict: DEAD/WEAK**,
consistent with W6's news_raw finding — this is the same "sparse alt-data, no real signal"
pattern, just a different source.

## Part B — `data/positioning` standalone mining (main effort)

47 symbols, 4 endpoints (`global_account`, `top_account`, `top_position`, `taker_vol`), 5-minute
cadence, 2026-07-16 → 2026-08-30 (45 days). No explicit PIT-flag column (unlike
`stablecoin_daily.parquet`'s `research_available_at`), but the archiver
(`positioning_archiver.py`) polls Binance's live-only endpoints forward-only with no documented
restatement mechanism — reasonably trustworthy as point-in-time, just not provably so.

**Critical confound found and controlled for**: the entire 45-day window is a single bull regime
(BTC +20.9%, second half averaging +0.84%/day vs first half +0.09%/day). Every mechanism below
was tested three ways — raw overlapping, market-neutral (cross-sectionally demeaned forward
returns), and declustered market-neutral (one observation per independent episode) — because the
raw/overlapping numbers are inflated by both autocorrelation and this single-regime beta.

Tested 9 distinct mechanisms × 18 parameterizations × 2-3 horizons × 3 statistical treatments
(~78 individual tests logged in `evidence/positioning_mechanisms_*.csv`): crowd LSR extremes,
top-account LSR extremes, top-position ("whale") LSR extremes, top-vs-crowd divergence,
whale-vs-top-account divergence, LSR momentum (Δratio not level), price-positioning divergence,
taker buy/sell-ratio extremes, cross-sectional positioning dispersion.

### Ranked results (declustered, market-neutral — the only honest treatment)

| rank | mechanism | horizon | n (independent) | gross bps | t | p | stability | status |
|---|---|---|---|---|---|---|---|---|
| 1 | **M3a** — top-position ("whale") LSR extremely LONG vs its own 7d history → forward relative underperformance | 24h | 87 | -57.8 | -2.82 | 0.006 | sign-stable both regime halves | **WEAK/PROMISING-BUT-THIN** — short-shaped, see below |
| 2 | M3a mirror — whale extremely SHORT → forward relative outperformance | 24h | 39 | ~+40 (marginal) | — | 0.09 | not confirmed | NEEDS_FULL_VALIDATION |
| 3 | M1 — crowd/global LSR extremes | 24h | — | ~0 after decluster | — | n.s. | — | DEAD |
| 4 | M2 — top-account LSR extremes | 24h | — | ~0 after decluster | — | n.s. | — | DEAD |
| 5 | M4 — top-vs-crowd divergence | 24h | — | ~0 after decluster | — | n.s. | — | DEAD |
| 6 | M5 — whale-vs-top-account divergence | 24h | — | ~0 after decluster | — | n.s. | — | DEAD |
| 7 | **M6 — LSR momentum (Δratio)** | 24h | large (raw, overlapping) | **+134 (raw/overlapping only)** | — | <0.0001 (raw only) | **evaporates to n.s. once declustered** | DEAD — see caveat below |
| 8 | M7 — price-positioning divergence | 24h | — | ~0 after decluster | — | n.s. | — | DEAD |
| 9 | M8 — taker buy/sell-ratio extremes | 24h | — | ~0 after decluster | — | n.s. | — | DEAD |
| 10 | M9 — cross-sectional positioning dispersion | 24h | — | ~0 after decluster | — | n.s. | — | DEAD |

**M3a is the only survivor**: whale (top-position) accounts sitting at an extreme long
long/short ratio relative to their own trailing 7-day history predicts *forward relative*
(cross-sectionally demeaned) 24h underperformance — n=87 independent episodes, gross -57.8bps,
p=0.006, and the sign holds in both the low-beta and high-beta halves of the sample (not just an
artifact of the single bull regime). It is **short-shaped**: standing project rule
`SHORT_REJECTED` means this is **not deployable as a directional short**. The only legitimate use
is as a screen — avoid/reduce new LONG entries elsewhere in the portfolio when a symbol's whale
LSR is at a 7d extreme — not a standalone sleeve. Its long-mirror (whale extreme short →
outperform) is directionally consistent but only marginal (p=0.09, n=39, thinner sample) —
NEEDS_FULL_VALIDATION, not yet a claim.

**Explicit methodological lesson, worth flagging to the rest of this sweep**: M6 (LSR momentum)
produced the single best-looking raw number in this entire worker's output — +134bps/24h,
p<0.0001 — and it is **entirely an artifact of autocorrelated overlapping observations plus the
single bull-regime beta**. Once tested on true independent (declustered) episodes with
market-neutral returns, it collapses to non-significant. This is the same failure mode W4 already
documented for calendar basis in yesterday's sweep (raw daily prints of an ongoing regime ≠
independent observations) — it applies just as much to 5-minute positioning data with short
lookback windows on a young dataset. Any other worker reporting a large raw effect from
`data/positioning` or similarly short/young datasets should replicate this decluster+market-neutral
check before trusting the number.

## Bottom line

Coverage sweep: one genuine new dataset found (`worldmon_*`, GDELT), tested, DEAD/WEAK, plus a
real bug flagged (event-date parsing drops 15%) but not fixed (read-only mandate). Positioning:
9 mechanisms tried, 1 weak-but-real survivor (M3a whale-LSR-extreme screen, short-shaped,
SHORT_REJECTED for direct use), 1 marginal mirror not yet validated, everything else DEAD once
properly declustered and market-neutralized — including the sample's single best raw-looking
number, which is a cautionary tale about this dataset's short 45-day history more than an edge.
