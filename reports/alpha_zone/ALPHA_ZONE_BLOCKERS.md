# ALPHA ZONE BLOCKERS (2026-09-12T14:59:18 UTC)

## free_blocker

- **capacity at 20 bps not observable for pre-2026 launches (archive generation)** — affects OTHER_VENUE_FIRST, MEXC_TO_BINANCE; 115 events; route: structural for history; the P4 live tape records 20 bps and tick L2 for every future launch; a preregistration may accept 1 % capacity as UNKNOWN-but-fills; cost: free; cleared: no
- **spread and slippage in the cost chains are still declared numbers** — affects OTHER_VENUE_FIRST, MEXC_TO_BINANCE; 160 events; route: the depth features now carry an effective-spread proxy and slippage bounds per window; a preregistration must name which window it uses; cost: free; cleared: no
- **venue precedence unknown** — affects OTHER_VENUE_FIRST; 31 events; route: Gate first-candle per pair; OKX/Bybit announcement archives via the P7 body archiver; cost: free; cleared: no
- **announced opening time and first traded bar disagree by more than 15 min** — affects OTHER_VENUE_FIRST, MEXC_TO_BINANCE; 8 events; route: human decision per event on which timestamp is the event; excluded by rule until then; cost: free; cleared: no

## credential_blocker

- **actual account fees unknown (no read-only API key)** — affects OTHER_VENUE_FIRST, MEXC_TO_BINANCE, H3_DELISTING, TRUE_FIRST_LISTING; 153 events; route: set BINANCE_READONLY_API_KEY / _SECRET to a key with no trading, withdrawal or transfer permission; re-run P8 --collect; cost: free; cleared: no

## provider_blocker

- **no free depth or index reference on the launch day** — affects OTHER_VENUE_FIRST; 7 events; route: H2_PROVIDER_REQUEST_WINDOWS.csv, P0 rows only (targeted windows, never a subscription); cost: paid, small; cleared: no

## forward_only_blocker

- **too few true first listings in history** — affects TRUE_FIRST_LISTING; 6 events; route: P4 market_state_tape captures certified births live; no backfill can create more; cost: time; cleared: no
- **H3 seal counts only announcements after 2026-09-11** — affects H3_DELISTING; 0 events; route: wait; ~18-20 months at the 2025 rate; cost: time; cleared: no

## conceptual_blocker

- **MEXC pre-Binance volume may be wash-traded; pump and volume features inherit it** — affects MEXC_TO_BINANCE; 113 events; route: compare MEXC volume to OKX/Bybit-first events; treat volume features as suspect until then; cost: free; cleared: no
- **the public liquidation message arrives after the move** — affects FORCED_FLOW_PUBLIC; 368 events; route: none: structural; the family is closed as a direct signal; cost: none; cleared: no

## Order of clearing

1. credential_blocker (one key, no permission) — unlocks execution_ready for every zone at once.
2. free_blockers on the closest zone (spread/slippage window named in the prereg; precedence for the Gate-only assets; the 8 timestamp disagreements decided by a human).
3. provider_blocker only for the 7 windows, only if a preregistration needs them.
4. forward_only_blockers are cleared by time, not by work.

