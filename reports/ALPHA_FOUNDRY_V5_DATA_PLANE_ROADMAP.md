# Alpha Foundry V5 — Data Plane Roadmap

## Evidence basis

DEV 6h readiness on the Market Physics 100ms state tape exposed 186 columns / 648003 rows. Only A1, A2 and A6 are currently READY. A3-A5, A7-A16 are blocked by explicit missing modalities.

This document translates those blockers into the minimum number of reusable data planes. The objective is not to maximize feature count; it is to maximize the number of falsifiable, economically distinct mechanisms per new causal data plane.

## Priority 0 — PIT provenance plane

Current state tape has clean structural chronology but does not carry explicit `*_available_ts_ns` / `*_receive_ts_ns` columns into the research frame. Therefore its research readiness is `STRUCTURAL_ONLY`, not full PIT proof.

Required:
- propagate per-source receive/availability clocks into every joined research tensor;
- preserve causal source watermark per row;
- record source window / collector run id / dataset manifest;
- fail closed if a joined feature cannot prove `available_ts_ns <= asof_ns`.

This does not create alpha; it makes every later alpha admissible.

## Priority 1 — Event / Trade Microstructure Plane

Unlock target labs: A3, A4, A5.

Existing raw inputs already collected by Market Physics:
- `raw/trades/venue=*/symbol=*/date=*/events.jsonl`
- `raw/book_events/venue=*/symbol=*/date=*/events.jsonl`

Required causal features:
- signed/gross trade notional by 100ms, 500ms, 2s, 10s, 60s;
- CVD, flow acceleration, flow jerk;
- trade count / inter-arrival CV / burst intensity;
- individual-vs-aggregate modality fraction;
- add/modify/remove/cancel intensities by side;
- removal-vs-cancel distinction retained;
- depletion/replenishment velocity;
- absorption = signed flow relative to realized price impact;
- queue-pressure and OFI deltas;
- event-count windows last 10/50/250 events in parallel with clock windows.

No new exchange source is required to start this plane. It should first be built from the already captured 6h DEV data, then independently confirmed on a later frozen window.

## Priority 2 — Derivatives / Leverage Plane

Unlock target labs: A7, A8, A9, A10.

Existing raw inputs already collected by Market Physics:
- `raw/derivatives/venue=*/symbol=*/date=*/events.jsonl`
- derivative kinds: open_interest, funding, mark, index, premium, liquidation.

Required causal features:
- per-venue OI level / delta / acceleration;
- funding level / delta / cross-venue dispersion;
- premium and mark-index basis in bps;
- basis velocity / acceleration;
- long/short liquidation notional and intensity;
- liquidation/depth and liquidation/OI ratios;
- cross-venue derivative state dispersion;
- time-to-funding / funding-clock features derived from venue contract schedule;
- four leverage topology states from joint price/OI movement, conditioned on flow/funding/basis.

Important: never sum raw OI across venues unless unit normalization is proven. Use per-venue changes and robust cross-venue summaries.

## Priority 3 — Cross-Asset Causal Plane

Unlock target labs: A12, A13.

No new external provider is required for the first version.

Required:
- synchronized BTC/ETH/SOL innovations;
- rolling beta estimated only on past data;
- residual returns by symbol;
- leader/follower lag matrix;
- residual relative-value spreads;
- later expand to a larger liquid universe after the methodology is proven.

This plane should be derived only after the PIT provenance plane is present so cross-asset joins cannot introduce timestamp leakage.

## Priority 4 — Hyperliquid Identity / Wallet Plane

Unlock target lab: A11.

Required:
- public wallet identity when available;
- signed wallet flow;
- size and position context;
- 1s/5s/30s/5m markouts;
- rolling wallet skill with strict past-only estimation;
- persistence and regime-conditioned markout;
- informed-flow aggregate excluding current trade from its own skill estimate.

Wallet ranking must be nested inside the research split. Global wallet ranking before the test window is forbidden.

## Priority 5 — Execution Evidence Plane

Unlock target lab: A16 and monetize all other labs.

Required:
- decision/send/ack/fill clocks;
- intended vs actual price;
- maker/taker flag;
- requested and filled quantity;
- queue-ahead estimate with confidence label;
- 100ms/1s/5s/30s post-fill markouts;
- missed-fill opportunity cost;
- venue-specific fee/rebate schedules;
- realized latency distributions.

Without L3 data, passive fill inference remains explicitly `L2_CONSERVATIVE`.

## Priority 6 — Options Surface Plane

Unlock target lab: A14.

Required:
- IV level, 25d risk reversal, butterfly/skew;
- term structure;
- option OI / volume;
- delta/gamma exposure proxies;
- receive/availability clocks for every option observation.

## Priority 7 — On-chain / Exchange Flow Plane

Unlock target lab: A15.

Required:
- exchange deposits / withdrawals / netflows;
- stablecoin issuance/redemption and venue inflows;
- large transfer / whale-flow features;
- source publication and chain-confirmation availability clocks.

## Expected unlock progression

Initial: A1, A2, A6 = 3 labs.

After Event/Trade Plane: potentially A1-A6 = 6 labs.

After Derivatives Plane: potentially A1-A10 except any lab failing scientific/economic validation = up to 10 data-ready labs.

After Cross-Asset Plane: A12/A13 become data-ready.

Wallet, execution, options and on-chain remain orthogonal expansion planes rather than prerequisites for the first broad discovery tournament.

## Hard rule

`READY` means data prerequisites are causally present. It never means the mechanism has alpha. Each lab still must pass the V5 lifecycle:

`DATA READY -> DEV DISCOVERY -> INDEPENDENT CONFIRMATION -> EXECUTION ECONOMICS -> PAPER LIVE -> PORTFOLIO ADMISSION`.
