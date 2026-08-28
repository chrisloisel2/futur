# Alpha Foundry V5 — Data Plane Unlock Plan

## Empirical starting point

The old 6h Market Physics DEV tape contains 648,003 rows and 186 columns. The first fail-closed readiness audit marks only A1, A2 and A6 as technically READY. A3-A5, A7-A16 are BLOCKED by explicit missing modalities or derived planes.

This document does not change any discovery gate and does not authorize using the Phase 5.2 forward window for V5 discovery.

## Wave D1 — Event Microstructure

Source: existing append-only `raw/book_events` + `raw/trades`.

Materialize causally:

- deep-book depletion/replenishment by side
- remove/add counts and event intensity
- queue pressure
- signed/gross trade notional and flow imbalance
- CVD
- flow acceleration/jerk
- trade rate and interarrival CV
- price impact and bounded absorption
- individual vs aggregate trade modality
- explicit book/trade availability timestamps

Target labs: A3, A4, A5.

A lab is not READY merely because zero-filled event columns exist. Minimum active-row gates are enforced.

## Wave D2 — Derivatives State

Source: existing append-only `raw/derivatives`.

Materialize causally and per venue:

- open interest and OI changes
- funding
- mark and index
- mark-index premium/basis proxy
- liquidation long/short/total/imbalance windows
- cross-venue basis dispersion
- explicit availability timestamps

Target labs: A7, A8.

A9 is deliberately NOT unlocked by mark-index premium. It requires an executable perp-spot basis plane. A10 remains blocked until a canonical next-funding timestamp/funding clock is captured.

## Wave D3 — Hyperliquid Wallet Intelligence

Source: existing normalized Hyperliquid trades with public buyer/seller identities when present.

Rules:

- score only the aggressor wallet
- freeze the wallet score at trade arrival
- update historical score only after the markout horizon matures
- shrink low-history wallets toward zero
- expose scored-flow coverage
- never treat identity presence alone as a usable wallet alpha

Target lab: A11.

## Wave D4 — Cross Asset

Source: base tape fair values.

Materialize causally:

- past market return
- lagged rolling beta
- factor-neutral residual
- leader innovation
- lagged horizon returns/innovations

This plane is useful diagnostically on BTC/ETH/SOL but A12/A13 remain structurally BLOCKED until the universe contains at least 8/12 symbols respectively.

## Still-blocked planes

- A9: executable spot/perp quotes and `perp_spot_basis_bps`
- A10: canonical funding clock / next funding timestamp
- A12/A13: materially wider liquid universe
- A14: options surface data with IV/skew/OI/gamma or equivalent positioning state
- A15: PIT on-chain/exchange/stablecoin flow plane
- A16: actual order/execution traces with queue-ahead/fill/markout evidence

## Scientific boundary

Data-plane readiness only means an economic hypothesis can be tested without fabricating its inputs. It is not alpha evidence. Discovery, independent confirmation, execution economics, paper-live and portfolio admission remain separate locked stages.
