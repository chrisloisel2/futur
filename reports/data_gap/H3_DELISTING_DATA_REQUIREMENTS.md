# H3 — delisting pressure: data requirements (audit, no look)

Verdict on record: `event_delisting_pressure_v1` INDECIDABLE (regard seq 7: +631 / +67 bps at 60 min, t 2.44, N_eff 21.6, top-1 13 %),
forward-sealed 2026-09-11 → 2028-09-11 with a **measured cost** requirement. The verdict is not touched.

| field | status | what we have | decision | priority | what to do |
|---|---|---|---|---|---|
| official delisting timestamp | HAVE | CMS releaseDate to the second | **BUILD** | P2_NICE_TO_HAVE | nothing to add |
| suspension timestamp | MISSING | in the announcement body (not stored) | **BUILD** | P1_HIGH_VALUE | store the body; parse 'delisting at YYYY-MM-DD HH:MM UTC' |
| last tradable timestamp | PARTIAL | Vision last daily file = last tradable day (DREP still had klines after the announced date: the day is not the minute) | **BUILD** | P1_HIGH_VALUE | body text + exchangeInfo status transitions (SETTLING) for perps |
| perp / margin availability at announcement | PARTIAL | perp: Vision existence (46/56 events on a perp); margin availability: no history | **BUILD** | P0_REQUIRED | the tradable share (82 %) rests on perp existence only; margin/borrow status at announcement is unknown and Binance typically suspends borrowing in the notice |
| borrow availability | MISSING | not in any tape; delisting notices state borrow suspension | **BUILD** | P0_REQUIRED | body text (suspension statement) + daily snapshot of margin pairs (sapi margin allPairs, read-only key) |
| borrow cost | MISSING | sapi/v1/margin/interestRateHistory (auth, VIP-dependent) | **BUILD** | P2_NICE_TO_HAVE | small vs a 600 bps move over 60 min; matters only for multi-day holds |
| funding | MISSING | Vision fundingRate monthly | **BUILD** | P2_NICE_TO_HAVE | extreme funding on delisting perps is a cost of carry, not a blocker at 60 min |
| depth before announcement | PARTIAL | Vision bookDepth UM on the announcement day: 41/46 perp events; spot names: no depth anywhere public | **BUILD** | P0_REQUIRED | the forward seal requires MEASURED cost; capacity in micro-caps is the binding constraint (DREP, CVP, BEAM were spot-only: unmeasurable, IGNORE for those) |
| volume after announcement | HAVE | Vision klines | **BUILD** | P2_NICE_TO_HAVE | already available |
| whether short entry was possible after publication | PARTIAL | perp existence yes; borrow no | **BUILD** | P0_REQUIRED | same as borrow availability |
| capacity estimate | MISSING | no depth used in seq 7 | **BUILD** | P0_REQUIRED | bookDepth at announcement minute + realized volume in the first hour |

## Reading

- **What the seal cannot do without**: depth at the announcement minute (capacity, measured cost) and the borrow/margin
  status per event. Both are P0 because the seal's promotion rule requires them and the dataset has neither.
- **Where they come from**: Vision bookDepth for the perp names (41 of 46 covered on the announcement day); the announcement
  body for the borrow-suspension sentence and the delisting minute; a daily margin-pairs snapshot for the future.
- **Where they cannot come from**: spot-only micro-caps (10 of 56 events; DREP, CVP, BEAM among the largest moves) have
  no public depth anywhere; they stay unmeasurable and must be reported as such, not filled with a declared number.
- **Forward**: the event-tape timer keeps collecting; the seal counts only announcements published after 2026-09-11.
