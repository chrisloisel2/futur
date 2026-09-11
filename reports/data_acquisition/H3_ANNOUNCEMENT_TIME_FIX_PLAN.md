# H3 — announcement time fix plan (P7, 2026-09-11T23:00:32Z)

The delisting forward seal (`event_delisting_pressure_v1`, active 2026-09-11 → 2028-09-11) requires a **measured**
executable cost and an executable short path. Two of the inputs it needs were not in the dataset: the minute the
delisting takes effect, and whether borrowing is suspended. Both are in the announcement body. This phase extracts them.
No look, no verdict, no budget: the seal is untouched.

## What the bodies gave

| field | events | note |
|---|---|---|
| bodies archived | 56 / 56 | Binance CMS detail endpoint |
| `delisting_ts` stated in the text | **56** | the effective minute, not the title's date |
| `suspension_ts` stated in the text | 23 | deposits / withdrawals / borrow suspension |

Lead time from publication to the delisting taking effect, in hours: min 0.5, p25 93.2,
median 140.5, p75 171.9, max 335.5.
0 announcements state a time **before** their own publication (a correction or a re-announcement of an
already-effective delisting); those are flagged, not silently used.

## Why this matters to the seal, and what it does not change

- The seal measures a 60-minute window from publication + 60 s. The delisting minute is a **separate** timestamp: it
  tells you how long the forced flow has to run before the market disappears. With a median lead of
  140.5 h, the hour after publication is inside the pressure window for most events — a fact the
  first look assumed and can now state.
- The suspension sentence is what decides the short path on spot names: if borrowing is suspended in the same notice,
  a spot short is not executable at all, whatever the depth says.
- **Nothing here re-opens the H3 verdict.** The seal counts only announcements published after 2026-09-11; these 56 are
  the burned population of regard seq 7. The extraction exists so the future look has the fields its promotion rule
  demands, not so the past one can be re-read.

## What the forward collector must now store per delisting announcement

1. `delisting_ts` and `suspension_ts` from the body (this module, already running on the event tape's URLs).
2. Depth at the announcement minute on the affected market (P6 gives it historically for perps; the P4 tape records it
   live for future events).
3. Margin / borrow availability at the announcement minute (P8, read-only key: `sapi` margin pairs and interest rates).
4. Whether a Binance perpetual for the asset still exists at publication — already in the universe rule.

Items 1 and 2 exist now. Item 3 is the only remaining blocker for the seal's "measured cost" requirement, and it is a
credentials problem, not a data problem.

## Sample of what was extracted

| asset | market | published | delisting takes effect | lead (h) | suspension stated |
|---|---|---|---|---|---|
| A2Z | um | 2026-03-18T09:00 | 2026-03-24T09:00 | 144.0 | 2026-03-19T06:00 |
| APT | um | 2026-03-20T14:14 | 2026-03-25T09:00 | 114.8 | — |
| BIFI | spot | 2026-04-09T06:00 | 2026-04-15T09:00 | 147.0 | 2026-04-10T06:00 |
| DEGO | um | 2026-04-17T09:00 | 2026-04-21T09:00 | 96.0 | 2026-04-18T06:00 |
| ATA | um | 2026-05-13T08:00 | 2026-05-19T09:00 | 145.0 | 2026-05-14T06:00 |
| IP | um | 2026-06-25T17:00 | 2026-06-28T09:00 | 64.0 | — |
| ALCX | spot | 2026-06-26T09:00 | 2026-07-10T03:00 | 330.0 | 2026-06-27T06:00 |
| AERGO | um | 2026-07-21T14:15 | 2026-07-24T06:30 | 64.2 | — |
| ACX | um | 2026-08-03T03:30 | 2026-08-17T03:00 | 335.5 | 2026-08-04T06:00 |
| ICX | um | 2026-08-20T06:00 | 2026-09-03T03:00 | 333.0 | 2026-08-21T06:00 |
