# H2 — no test (P10 decision, 2026-09-11T23:15:17 UTC)

Freeze `4f077025c30f6bfd…` at commit `1f55ec3f03b2`. This is the operative decision of P10. Budget stays at 0, no budget is
requested, `capital_deployable` remains **false**, and no alpha verdict is written.

## The decision

**NO TEST.** 0 events are clean against a threshold of 80.

Two blockers, in the order they must be cleared:

1. **Execution cost is unknown.** No read-only API key exists in this environment, so the fee this account is
   actually charged was never read. The published VIP0 schedule is official and strong enough to *reject* a
   hypothesis, not to promote one. This is a credentials problem, not a data problem.
2. **Capacity was never derived.** The depth archives are on disk (174 window
   manifests, 1.47 GB of Vision data, every checksum verified) but nothing has been computed from them. P6
   deliberately refused to credit a derived field it had not derived.

Both are free to clear. Neither requires buying data.

## What would happen if they were cleared

**129 events would be clean** — comfortably above the threshold of 80. Their structure:

| class | events | what it means |
|---|---|---|
| `OTHER_VENUE_FIRST` | 123 | the token already traded elsewhere before the Binance perpetual opened |
| `TRUE_BINANCE_PERP_FIRST` | 6 | the Binance perpetual is the first market anywhere |

## The finding that matters more than the count

`event_listing_perp_fade_v1` was built on launches where the Binance **perpetual** is the first **Binance**
market. P9 showed that for 123 of those 129 events a price already existed
on another venue — MEXC in most cases, with a median lead of 11 days. Only
**6 events** are first listings in the sense the hypothesis assumed.

The original look (regard seq 8, INDECIDABLE at t = 2.12 with a per-event dispersion of 1 546 bps) therefore
measured one number over two populations that are not the same phenomenon. That is not a reason to re-read the
old result — it is burned, and re-reading it is forbidden. It is a reason why any future preregistration must
state which population it tests.

And it is a warning about the count: 6 genuine first listings is far
below any threshold that could decide anything. If the interesting hypothesis is about genuine first listings,
the dataset does not contain enough of them, and no amount of backfill will create more.

## Why the remaining 45 stay blocked even then

| class | events | route |
|---|---|---|
| `UNKNOWN_PRECEDENCE` | 30 | improve the P9 venue clients (Gate publishes no listing date; the per-pair first-candle call resolves them one at a time) |
| `BAD_TIMESTAMP` | 8 | the announced opening time and the first traded bar disagree by more than 15 minutes; a human decides which one is the event |
| `PROVIDER_NEEDED` | 7 | no free depth or index reference for that launch day; the targeted request CSV is ready |

## What happens next, in order

1. Configure a Binance API key **with no trading permission** and re-run P8. The module refuses a key that can trade.
2. Derive capacity and effective spread from the P6 depth archives. Free, the data is already local.
3. Re-run this freeze. If it then reports at least 80 clean events, the preregistration draft in
   `H2_CLEAN_RETEST_PREREG.md` becomes signable and `H2_BUDGET_REOPEN_REQUEST.md` becomes a real request.
4. Until then: no test, no budget, no capital.
