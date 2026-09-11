# EDGE SOURCE SELECTION — P4 (2026-09-11)

Question: which source of advantage can this repository observe before or better than the market? Not "which
strategy", but "which imbalance is visible in time and executable at our cost". Verdicts are about data
collection only: no test, no signal, no order.

State on record: 0 validated sleeves, capital deployable false, budget 0. H2 listing fade INDECIDABLE (large gross,
σ 1 546 bps, judged against BTC because no reference price was in the dataset). H3 delisting pressure forward-sealed
(needs measured cost and borrow status). Forced-flow public liquidation: REJECTED (−7 bps after a +2 s entry; the
message is the corpse, not the signal).

| # | source | advantage type | latency sensitivity | data requirement | feasibility here | expected usefulness | verdict |
|---|---|---|---|---|---|---|---|
| 1 | public OHLCV (candles, any venue) | none (everyone has it) | none | already 696 perps daily since 2019, 1-min on demand | trivial | measured: three well-powered zeros; per-event dispersion dominates | **IGNORE** as an edge source; keep as outcome ruler only |
| 2 | public funding (alone) | weak structural (crowding) | hours | premiumIndex 1 call/min for 900 symbols | trivial | as a stand-alone signal: exhausted by arbitrage 2025-26 (loop record); as a STATE variable around events: useful | **IGNORE alone / COLLECT as state** |
| 3 | public liquidation message | none: 1 order/s/symbol, published after execution | seconds; structurally late | forceOrder stream (collected) | trivial | direct signal: dead (seq 9). Burst counter: a trigger for captures, not a signal | **IGNORE as direct signal / COLLECT as trigger** |
| 4 | official announcements | structural (forced flows are scheduled) | minutes to hours (pump inside the publication minute) | CMS/OKX/Bybit tape (7 449) + body text + live latency | done, body missing | the pump is arbitraged in < 60 s; the slow legs (fade, delisting pressure) need the announced times and the state at launch | **COLLECT** (add body text, exact launch/delisting minutes) |
| 5 | exchange status transition | structural + niche (nobody archives PENDING_TRADING → TRADING → SETTLING) | seconds (5 s poll) | exchangeInfo diff UM + spot, lifecycle state | built in P4 (this branch) | the exact birth/death minute of every market, shortability (margin flag), filters; the missing timestamps of H2/H3 | **COLLECT** |
| 6 | first tradable market state | niche: first book, first trade, first mark/index/OI/funding of a new market | seconds to minutes | triggered capture armed at announcement (median 1.7 h before launch) | built in P4; launch-day Vision covers 166-174/174 for history | turns H2 from "return vs BTC" into overshoot vs index with crowding state | **COLLECT** |
| 7 | L2 microstructure around launch / delisting | execution + niche (thin books, capacity) | 100 ms streams, 1 000-level REST every 5 s | depth20@100ms (public/ws) + REST depth + bookTicker | built in P4; history: Vision bookDepth 1-min (166/174, 41/46) | measured cost and capacity for H2 and the H3 seal; slippage model | **COLLECT** |
| 8 | account execution / fills | execution (our own reality) | n/a | read-only key: commissionRate, fills, leverage brackets, margin rates | scripts exist (P1), no key configured | replaces declared 4 + 4 / 8 + 6 bps by actual; decides the VIP0 wall precisely | **COLLECT** (needs a read-only key) |
| 9 | fee / rebate tier | cost | n/a | official schedules (P1.1 manifest, final) | done | at VIP0 nothing reopens; institutional maker tiers = market making (P1 verdict) | **IGNORE** until volume tiers change |
| 10 | private order flow | information | ms | not available | impossible for this repo | the only true information edge; not ours | **IGNORE** |

## Decisions

- **IGNORE**: public OHLCV / funding-only / technical features as edge sources; the public liquidation message as a
  direct signal; fee tiers (no change at VIP0); private order flow (not accessible).
- **COLLECT**: exchange status transitions (5); first tradable market state (6); L2 + trades around launches and
  delistings (7); execution feasibility data (8); announcements with body text (4); funding and liquidation bursts
  only as state / trigger (2, 3).
- **FORWARD_ONLY**: H3 delisting pressure (sealed 2026-09-11 → 2028-09-11; the tape now supplies the depth and
  borrow status at announcement that the seal requires).
- **RESEARCH_ONLY**: H2 listing fade until `market_state_tape` has recorded real launches with first book, index,
  OI and funding; no re-test on the 174 events.

## What this changes

The three looks failed for one reason: they saw the event (candle) but not the state (book, reference, crowding,
shortability). Sources 5-7 are the only ones on the list that are both observable by us and not archived by
anyone else in this form. That is where an edge can be born; it is not an edge yet.
