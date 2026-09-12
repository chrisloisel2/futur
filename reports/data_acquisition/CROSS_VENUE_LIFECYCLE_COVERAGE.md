# CROSS-VENUE LIFECYCLE — coverage (2026-09-11T23:11:37 UTC)

Where did this market exist before Binance opened its perpetual? Metadata only: instrument existence, first listing date, status. No price joined, no return computed, no signal, no verdict.

| venue | instruments | with a published listing date | markets | native listing time |
|---|---|---|---|---|
| okx | 1446 | 1446 | {'spot': 983, 'perp': 463} | True |
| bybit | 1314 | 829 | {'perp': 829, 'spot': 485} | True |
| gate | 3139 | 0 | {'spot': 2158, 'perp': 981} | False |
| mexc | 3038 | 2996 | {'spot': 1882, 'perp': 1156} | True |
| kucoin | 1570 | 1465 | {'perp': 682, 'spot': 888} | True |

10507 instruments in total. Gate publishes no listing date for any market; 0 assets were resolved by asking Gate for the first daily candle of the pair, which is the market stating its own birth.

## The four answers

1. **H2 events with a known venue precedence: 143 / 174.**
2. **Truly Binance-first: 0** (plus 6 listed on no other collected venue at all).
3. **Already priced elsewhere: 137**, first venue {'bybit': 10, 'okx': 8, 'mexc': 113, 'kucoin': 6}, lead time in days min 0.01, median 11.02, max 1808.31.
4. **Still unknown: 31.** See `VENUE_PRECEDENCE_UNKNOWN_REMAINING.md`.

## What this changes about H2, and what it does not

`event_listing_perp_fade_v1` selects launches where the Binance **perpetual** is the first Binance market. That is a statement about Binance, not about the world. On the collected venues, 137 of 174 of those assets already had a market elsewhere, with a median lead of 11.02 days. A price therefore existed before the Binance launch for most of the universe.

This is a fact about the data, not a verdict. It does not re-open the H2 result (INDECIDABLE, regard seq 8) and it produces no signal. What it does is make the population describable: a future preregistration can say which sub-population it is testing instead of assuming they are the same events.

## Confidence

{'high': 143, 'low': 25, 'medium': 6} — `high` means a published listing date settled it; `medium` means the event tape shows an earlier official announcement but no instrument date; `low` means neither.

